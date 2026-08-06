from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import WORKSPACE_ROOT, AssistantConfig
from .contracts.time import now_utc_iso
from .registry import ResourceRegistry
from .storage import AssistantStorage

DEFAULT_MONITOR_DB = WORKSPACE_ROOT / "personal_assistant/data/monitoring.sqlite3"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _age_hours(value: object, *, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def _ratio(part: int | float, total: int | float) -> float:
    return float(part) / float(total) if total else 0.0


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _round(value: float) -> float:
    return round(float(value), 2)


@dataclass(slots=True)
class ComponentScore:
    id: str
    label: str
    score: float
    maximum: float
    status: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "score": _round(self.score),
            "maximum": _round(self.maximum),
            "percent": _round(100.0 * self.score / self.maximum if self.maximum else 0.0),
            "status": self.status,
            "evidence": self.evidence,
        }


class MonitoringStore:
    def __init__(self, path: Path = DEFAULT_MONITOR_DB) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                window_days INTEGER NOT NULL,
                live INTEGER NOT NULL,
                score REAL NOT NULL,
                rating TEXT NOT NULL,
                confidence TEXT NOT NULL,
                report_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_monitor_snapshots_time ON snapshots(recorded_at);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def record(self, report: dict[str, Any], *, days: int, live: bool) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO snapshots(recorded_at,window_days,live,score,rating,confidence,report_json)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                str(report["generated_at"]),
                int(days),
                int(live),
                float(report["overall_score"]),
                str(report["rating"]),
                str(report["confidence"]),
                json.dumps(report, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def history(self, *, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        since = (_utc_now() - timedelta(days=max(1, days))).isoformat()
        rows = self.connection.execute(
            """
            SELECT id,recorded_at,window_days,live,score,rating,confidence
            FROM snapshots
            WHERE recorded_at >= ?
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            (since, max(1, min(int(limit), 1000))),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT report_json FROM snapshots ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(str(row["report_json"]))
        except json.JSONDecodeError:
            return None

    def prune(self, *, keep_days: int = 180) -> int:
        cutoff = (_utc_now() - timedelta(days=max(7, keep_days))).isoformat()
        cursor = self.connection.execute("DELETE FROM snapshots WHERE recorded_at < ?", (cutoff,))
        self.connection.commit()
        return int(cursor.rowcount or 0)


class PerformanceMonitor:
    """Produces an evidence-based technical health report.

    The score is deliberately operational. It does not claim to measure whether
    every classification or generated answer is semantically correct. Quality is
    estimated only from review, uncertainty and feedback signals available in the
    local databases.
    """

    def __init__(
        self,
        config: AssistantConfig,
        storage: AssistantStorage,
        registry: ResourceRegistry,
        *,
        live_health: Callable[[], dict[str, Any]] | None = None,
        mail_database: Path | None = None,
        monitor_database: Path | None = None,
        antivirus_health: Callable[..., dict[str, Any]] | None = None,
        antivirus_summary: Callable[..., dict[str, Any]] | None = None,
        portfolio_health: Callable[[], dict[str, Any]] | None = None,
        scheduler_health: Callable[[], dict[str, Any]] | None = None,
        jobs_health: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.registry = registry
        self.live_health = live_health
        self.mail_database = (mail_database or self._mail_database()).expanduser().resolve()
        default_monitor = DEFAULT_MONITOR_DB
        if root := os.environ.get("OPENCLAW_MONITORING_DATA_DIR"):
            default_monitor = Path(root).expanduser().resolve() / "monitoring.sqlite3"
        self.monitor_database = Path(monitor_database or default_monitor).expanduser().resolve()
        self.antivirus_health = antivirus_health
        self.antivirus_summary = antivirus_summary
        self.portfolio_health = portfolio_health
        self.scheduler_health = scheduler_health
        self.jobs_health = jobs_health
        self._store: MonitoringStore | None = None

    def _monitor_store(self) -> MonitoringStore:
        if self._store is None:
            self._store = MonitoringStore(self.monitor_database)
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()

    @staticmethod
    def _mail_database() -> Path:
        try:
            from mail_agent.config import load_config as load_mail_config

            return load_mail_config().runtime.database
        except Exception:
            if root := os.environ.get("OPENCLAW_MAIL_DATA_DIR"):
                return Path(root).expanduser().resolve() / "mail_agent.sqlite3"
            return WORKSPACE_ROOT / "mail_agent/data/mail_agent.sqlite3"

    @staticmethod
    def _query_one(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        try:
            return connection.execute(sql, params).fetchone()
        except sqlite3.Error:
            return None

    @staticmethod
    def _query_all(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        try:
            return connection.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []

    @staticmethod
    def _integrity(path: Path) -> str:
        if not path.exists():
            return "missing"
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = connection.execute("PRAGMA integrity_check").fetchone()
                return str(row[0] if row else "unknown")
            finally:
                connection.close()
        except sqlite3.Error as exc:
            return f"error: {exc}"

    @staticmethod
    def _systemd_unit(unit: str) -> dict[str, Any]:
        if shutil.which("systemctl") is None:
            return {"available": False, "unit": unit}
        command = [
            "systemctl", "--user", "show", unit, "--no-pager",
            "--property=LoadState,ActiveState,SubState,UnitFileState,Result",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "unit": unit, "error": str(exc)}
        values: dict[str, Any] = {"available": completed.returncode == 0, "unit": unit}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        if completed.stderr.strip():
            values["error"] = completed.stderr.strip()[:500]
        return values

    def _mail_metrics(self, since: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "database": str(self.mail_database),
            "integrity": self._integrity(self.mail_database),
            "recent_messages": 0,
            "message_errors": 0,
            "review_or_uncertain": 0,
            "average_confidence": None,
            "actions": 0,
            "action_failures": 0,
            "feedback": 0,
            "invoices": {},
            "events": {},
        }
        if not self.mail_database.exists():
            return result
        connection = sqlite3.connect(f"file:{self.mail_database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = self._query_one(
                connection,
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status IN ('error','delivery-uncertain') OR COALESCE(last_error,'') != '' THEN 1 ELSE 0 END) AS errors,
                       SUM(CASE WHEN status IN ('review','appointment-review') OR category='uncertain' THEN 1 ELSE 0 END) AS review_count,
                       AVG(CASE WHEN confidence IS NOT NULL THEN confidence END) AS avg_confidence
                FROM messages WHERE updated_at >= ?
                """,
                (since,),
            )
            if row:
                result["recent_messages"] = int(row["total"] or 0)
                result["message_errors"] = int(row["errors"] or 0)
                result["review_or_uncertain"] = int(row["review_count"] or 0)
                result["average_confidence"] = _round(float(row["avg_confidence"])) if row["avg_confidence"] is not None else None
            row = self._query_one(
                connection,
                "SELECT COUNT(*) total, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failures FROM actions WHERE created_at >= ?",
                (since,),
            )
            if row:
                result["actions"] = int(row["total"] or 0)
                result["action_failures"] = int(row["failures"] or 0)
            row = self._query_one(connection, "SELECT COUNT(*) total FROM feedback WHERE created_at >= ?", (since,))
            result["feedback"] = int(row["total"] or 0) if row else 0
            for table, key in (("invoices", "invoices"), ("events", "events")):
                rows = self._query_all(
                    connection,
                    f"SELECT status, COUNT(*) count FROM {table} WHERE created_at >= ? GROUP BY status",
                    (since,),
                )
                result[key] = {str(item["status"]): int(item["count"]) for item in rows}
        finally:
            connection.close()
        return result

    def _assistant_metrics(self, since: str, *, now: datetime) -> dict[str, Any]:
        core_connection = self.storage.connection
        knowledge_connection = self.storage.knowledge_connection
        document_rows = self._query_all(
            knowledge_connection,
            "SELECT source_type, COUNT(*) count FROM documents GROUP BY source_type",
        )
        action_rows = self._query_all(
            core_connection,
            "SELECT status, COUNT(*) count FROM action_plans WHERE updated_at >= ? GROUP BY status",
            (since,),
        )
        stale_row = self._query_one(
            core_connection,
            """
            SELECT COUNT(*) count FROM action_plans
            WHERE status IN ('proposed','approved','executing') AND updated_at < ?
            """,
            ((now - timedelta(hours=24)).isoformat(),),
        )
        sync_rows = self._query_all(
            knowledge_connection,
            "SELECT resource_id,scope,synced_at,status,detail FROM sync_state ORDER BY resource_id,scope",
        )
        latest_row = self._query_one(
            knowledge_connection,
            "SELECT MAX(indexed_at) latest, COUNT(*) total FROM documents",
        )
        chunk_row = self._query_one(
            knowledge_connection, "SELECT COUNT(*) count FROM chunks"
        )

        started = time.perf_counter()
        try:
            knowledge_connection.execute("SELECT COUNT(*) FROM documents").fetchone()
            if self.storage.fts_enabled:
                knowledge_connection.execute(
                    "SELECT rowid FROM knowledge_fts LIMIT 1"
                ).fetchone()
            query_ms = (time.perf_counter() - started) * 1000.0
        except sqlite3.Error:
            query_ms = -1.0

        return {
            "integrity": self.storage.integrity(),
            "resource_count": len(self.registry.resources),
            "duplicate_resource_ids": sorted(set(self.registry.duplicate_ids)),
            "fts5": self.storage.fts_enabled,
            "documents": {str(row["source_type"]): int(row["count"]) for row in document_rows},
            "document_count": int(latest_row["total"] or 0) if latest_row else 0,
            "chunk_count": int(chunk_row["count"] or 0) if chunk_row else 0,
            "latest_indexed_at": str(latest_row["latest"] or "") if latest_row else "",
            "latest_index_age_hours": _age_hours(latest_row["latest"] if latest_row else "", now=now),
            "local_query_ms": _round(query_ms),
            "action_plans": {str(row["status"]): int(row["count"]) for row in action_rows},
            "stale_action_plans": int(stale_row["count"] or 0) if stale_row else 0,
            "sync_state": [
                {
                    "resource_id": str(row["resource_id"]),
                    "scope": str(row["scope"]),
                    "synced_at": str(row["synced_at"] or ""),
                    "age_hours": _round(_age_hours(row["synced_at"], now=now) or 0.0) if row["synced_at"] else None,
                    "status": str(row["status"]),
                    "detail": str(row["detail"] or "")[:500],
                }
                for row in sync_rows
            ],
        }

    @staticmethod
    def _host_metrics(workspace: Path) -> dict[str, Any]:
        usage = shutil.disk_usage(workspace)
        memory: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                number = value.strip().split()[0]
                if number.isdigit():
                    memory[key] = int(number) * 1024
        except OSError:
            pass
        try:
            load = os.getloadavg()
        except OSError:
            load = ()
        return {
            "disk_total_bytes": usage.total,
            "disk_free_bytes": usage.free,
            "disk_free_percent": _round(100.0 * usage.free / usage.total if usage.total else 0.0),
            "memory_total_bytes": memory.get("MemTotal"),
            "memory_available_bytes": memory.get("MemAvailable"),
            "memory_available_percent": _round(
                100.0 * memory.get("MemAvailable", 0) / memory.get("MemTotal", 1)
            ) if memory.get("MemTotal") else None,
            "load_average": [_round(value) for value in load],
        }

    def _live_nextcloud(self) -> dict[str, Any]:
        if self.live_health is None:
            return {"checked": False, "ok": None, "latency_ms": None}
        started = time.perf_counter()
        try:
            result = self.live_health()
            latency = (time.perf_counter() - started) * 1000.0
            return {"checked": True, "ok": bool(result.get("ok")), "latency_ms": _round(latency), "detail": result}
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000.0
            return {"checked": True, "ok": False, "latency_ms": _round(latency), "error": str(exc)}

    def _antivirus_metrics(self, *, live: bool, days: int) -> dict[str, Any]:
        result: dict[str, Any] = {"checked": False, "ok": None, "summary": {}}
        if self.antivirus_summary is not None:
            try:
                result["summary"] = self.antivirus_summary(days=days)
            except Exception as exc:
                result["summary_error"] = str(exc)
        if live and self.antivirus_health is not None:
            try:
                health = self.antivirus_health(live_scan=True)
                result.update({"checked": True, "ok": bool(health.get("ok")), "health": health})
            except Exception as exc:
                result.update({"checked": True, "ok": False, "error": str(exc)})
        return result

    def _portfolio_metrics(self) -> dict[str, Any]:
        if self.portfolio_health is None:
            return {"enabled": False, "ok": True, "state": "unavailable", "coverage": None}
        try:
            return self.portfolio_health()
        except Exception as exc:
            return {
                "enabled": True,
                "ok": False,
                "state": "failed",
                "coverage": 0.0,
                "error": str(exc)[:500],
            }

    def _scheduler_metrics(self) -> dict[str, Any]:
        if self.scheduler_health is None:
            return {"enabled": False, "ok": True, "state": "unavailable"}
        try:
            return self.scheduler_health()
        except Exception as exc:
            return {
                "enabled": True,
                "ok": False,
                "state": "failed",
                "error": str(exc)[:500],
            }

    def _jobs_metrics(self) -> dict[str, Any]:
        if self.jobs_health is None:
            return {"checked": False, "ok": None, "jobs": []}
        try:
            result = self.jobs_health()
            return {"checked": True, **result}
        except Exception as exc:
            return {"checked": True, "ok": False, "jobs": [], "error": str(exc)[:500]}

    @staticmethod
    def _status(percent: float) -> str:
        if percent >= 90:
            return "excellent"
        if percent >= 75:
            return "good"
        if percent >= 60:
            return "degraded"
        if percent >= 40:
            return "poor"
        return "critical"

    @staticmethod
    def _rating(score: float) -> str:
        if score >= 90:
            return "sehr gut"
        if score >= 75:
            return "gut"
        if score >= 60:
            return "eingeschraenkt"
        if score >= 40:
            return "schlecht"
        return "kritisch"

    def report(self, *, days: int = 7, live: bool = False) -> dict[str, Any]:
        days = max(1, min(int(days), 90))
        now = _utc_now()
        since_dt = now - timedelta(days=days)
        since = since_dt.isoformat()
        assistant = self._assistant_metrics(since, now=now)
        mail = self._mail_metrics(since)
        host = self._host_metrics(WORKSPACE_ROOT)
        nextcloud_live = self._live_nextcloud() if live else {"checked": False, "ok": None, "latency_ms": None}
        antivirus = self._antivirus_metrics(live=live, days=days)
        portfolio = self._portfolio_metrics()
        scheduler = self._scheduler_metrics()
        jobs = self._jobs_metrics()
        services = {
            unit: self._systemd_unit(unit)
            for unit in (
                "mail-agent.timer",
                "personal-assistant-sync.timer",
                "personal-assistant-supervisor.timer",
                "personal-assistant-monitor.timer",
                "personal-assistant-portfolio.timer",
            )
        }

        components: list[ComponentScore] = []
        recommendations: list[str] = []
        coverage = 0

        # Core: 15
        core = 0.0
        core += 7.0 if assistant["integrity"] == "ok" else 0.0
        core += 3.0 if mail["integrity"] == "ok" else 0.0
        core += 2.0 if not assistant["duplicate_resource_ids"] else 0.0
        core += 2.0 if assistant["fts5"] else 0.0
        core += 1.0 if assistant["resource_count"] > 0 else 0.0
        components.append(ComponentScore("core", "Core und Datenbanken", core, 15.0, self._status(core / 15.0 * 100), {
            "assistant_integrity": assistant["integrity"],
            "mail_integrity": mail["integrity"],
            "duplicate_resource_ids": assistant["duplicate_resource_ids"],
            "fts5": assistant["fts5"],
        }))
        if core < 15:
            recommendations.append("Core-/Datenbankfehler zuerst mit doctor und Backup pruefen.")

        # Mail reliability: 20
        recent = int(mail["recent_messages"])
        mail_score = 14.0 if recent == 0 else 20.0
        if recent:
            coverage += 1
            error_ratio = _ratio(mail["message_errors"], recent)
            mail_score -= min(10.0, error_ratio * 100.0)
        else:
            error_ratio = 0.0
            recommendations.append("Im Auswertungsfenster fehlen neue Maildaten; Aussage zur Mailzuverlaessigkeit ist eingeschraenkt.")
        action_total = int(mail["actions"])
        action_failure_ratio = _ratio(mail["action_failures"], action_total)
        if action_total:
            coverage += 1
            mail_score -= min(5.0, action_failure_ratio * 20.0)
        bad_invoice = sum(count for status, count in mail["invoices"].items() if status not in {"uploaded", "completed", "archived", "duplicate", "dry-run"})
        bad_events = sum(count for status, count in mail["events"].items() if status in {"error", "failed", "delivery-uncertain"})
        mail_score -= min(3.0, float(bad_invoice + bad_events))
        mail_score = _clamp(mail_score, 0.0, 20.0)
        components.append(ComponentScore("mail_reliability", "Mail-Zuverlaessigkeit", mail_score, 20.0, self._status(mail_score / 20.0 * 100), {
            "recent_messages": recent,
            "message_errors": mail["message_errors"],
            "error_rate": _round(error_ratio),
            "actions": action_total,
            "action_failures": mail["action_failures"],
            "action_failure_rate": _round(action_failure_ratio),
            "invoices": mail["invoices"],
            "events": mail["events"],
        }))
        if error_ratio > 0.05 or action_failure_ratio > 0.05:
            recommendations.append("Mailfehlerquote ist erhoeht; recent_errors und fehlgeschlagene Aktionen pruefen.")

        # Classification quality: 15, explicitly indirect
        quality = 9.0 if recent == 0 else 15.0
        review_ratio = _ratio(mail["review_or_uncertain"], recent)
        if recent:
            quality -= min(9.0, review_ratio * 15.0)
            average_confidence = mail["average_confidence"]
            if average_confidence is not None:
                confidence_value = float(average_confidence)
                if confidence_value < 0.55:
                    quality -= 3.0
                elif confidence_value < 0.7:
                    quality -= 1.5
        quality = _clamp(quality, 0.0, 15.0)
        components.append(ComponentScore("classification_quality", "Klassifizierungsqualitaet (indirekt)", quality, 15.0, self._status(quality / 15.0 * 100), {
            "recent_messages": recent,
            "review_or_uncertain": mail["review_or_uncertain"],
            "review_rate": _round(review_ratio),
            "average_confidence": mail["average_confidence"],
            "feedback_events": mail["feedback"],
            "limitation": "Echte Praezision/Recall erfordert bestaetigte Benutzer-Labels.",
        }))
        if review_ratio > 0.30:
            recommendations.append("Viele Mails landen in Pruefung/unsicher; Korrekturordner und Trainingsregeln auswerten.")

        # Nextcloud: 15
        sync_rows = assistant["sync_state"]
        sync_ok = [row for row in sync_rows if row["status"] == "ok"]
        sync_bad = [row for row in sync_rows if row["status"] != "ok"]
        nextcloud_score = 4.0 if not sync_rows else 11.0
        if sync_rows:
            coverage += 1
            nextcloud_score -= min(6.0, 2.0 * len(sync_bad))
            stale = [row for row in sync_rows if row["age_hours"] is not None and float(row["age_hours"]) > 24.0]
            nextcloud_score -= min(4.0, float(len(stale)))
        else:
            stale = []
            recommendations.append("Noch keine Sync-Historie vorhanden; nextcloud sync ausfuehren.")
        if live:
            coverage += 1
            nextcloud_score += 4.0 if nextcloud_live.get("ok") else 0.0
        else:
            nextcloud_score += 2.0
        nextcloud_score = _clamp(nextcloud_score, 0.0, 15.0)
        components.append(ComponentScore("nextcloud", "Nextcloud und Datenfrische", nextcloud_score, 15.0, self._status(nextcloud_score / 15.0 * 100), {
            "sync_scopes": len(sync_rows),
            "sync_ok": len(sync_ok),
            "sync_failed_or_partial": len(sync_bad),
            "stale_over_24h": len(stale),
            "live": nextcloud_live,
        }))
        if sync_bad or stale:
            recommendations.append("Nextcloud-Sync ist fehlerhaft oder veraltet; Sync-Status und Journal pruefen.")

        # Action execution: 15
        plans = assistant["action_plans"]
        completed = int(plans.get("completed", 0))
        failed = int(plans.get("failed", 0))
        total_plans = sum(int(value) for value in plans.values())
        action_score = 10.0 if total_plans == 0 else 15.0
        if total_plans:
            coverage += 1
            failure_ratio = _ratio(failed, total_plans)
            action_score -= min(10.0, failure_ratio * 25.0)
        else:
            failure_ratio = 0.0
        stale_plans = int(assistant["stale_action_plans"])
        action_score -= min(5.0, stale_plans * 1.5)
        action_score = _clamp(action_score, 0.0, 15.0)
        components.append(ComponentScore("actions", "ActionPlan-Ausfuehrung", action_score, 15.0, self._status(action_score / 15.0 * 100), {
            "statuses": plans,
            "completed": completed,
            "failed": failed,
            "failure_rate": _round(failure_ratio),
            "stale_over_24h": stale_plans,
        }))
        if failed or stale_plans:
            recommendations.append("Fehlgeschlagene oder alte ActionPlans mit actions list pruefen.")

        # Knowledge: 10
        knowledge = 0.0
        knowledge += 3.0 if assistant["document_count"] > 0 else 0.0
        knowledge += 2.0 if assistant["chunk_count"] > 0 else 0.0
        age = assistant["latest_index_age_hours"]
        if age is None:
            freshness_points = 0.0
        elif age <= 6:
            freshness_points = 3.0
        elif age <= 24:
            freshness_points = 2.0
        elif age <= 72:
            freshness_points = 1.0
        else:
            freshness_points = 0.0
        knowledge += freshness_points
        query_ms = float(assistant["local_query_ms"])
        if 0 <= query_ms <= 50:
            knowledge += 2.0
        elif query_ms <= 250:
            knowledge += 1.0
        components.append(ComponentScore("knowledge", "Wissensindex und Suche", knowledge, 10.0, self._status(knowledge / 10.0 * 100), {
            "documents": assistant["document_count"],
            "documents_by_type": assistant["documents"],
            "chunks": assistant["chunk_count"],
            "latest_index_age_hours": age,
            "local_query_ms": query_ms,
            "semantic_provider": self.config.search.semantic_provider,
        }))
        if knowledge < 7:
            recommendations.append("Wissensindex ist leer, alt oder langsam; Index- und Sync-Lauf pruefen.")

        # Runtime/host: 5 (intern weiter auf einer 10-Punkte-Rohskala berechnet)
        runtime = 0.0
        free_percent = float(host["disk_free_percent"])
        runtime += 4.0 if free_percent >= 15 else (2.0 if free_percent >= 8 else 0.0)
        memory_percent = host.get("memory_available_percent")
        runtime += 2.0 if memory_percent is None or float(memory_percent) >= 15 else (1.0 if float(memory_percent) >= 8 else 0.0)
        desired_jobs = [
            value for value in jobs.get("jobs", [])
            if value.get("desired") == "on"
        ]
        healthy_jobs = [value for value in desired_jobs if value.get("ok")]
        loaded_units = [value for value in services.values() if value.get("available")]
        active_units = [value for value in loaded_units if value.get("ActiveState") == "active"]
        if jobs.get("checked") and desired_jobs:
            coverage += 1
            runtime += 4.0 * len(healthy_jobs) / len(desired_jobs)
        elif loaded_units:
            coverage += 1
            runtime += 4.0 * len(active_units) / len(loaded_units)
        else:
            runtime += 2.0
        if scheduler.get("enabled") and not scheduler.get("ok"):
            runtime = max(0.0, runtime - 2.0)
            recommendations.append(
                "Adaptive Aufgabensteuerung ist eingeschraenkt; scheduler doctor und jobs alerts pruefen."
            )
        if live and antivirus.get("checked") and not antivirus.get("ok"):
            runtime = max(0.0, runtime - 4.0)
            recommendations.append("Host-Virenscanner ist nicht einsatzbereit; ClamAV-Dienst und Signaturupdate pruefen. Mail-Anhaenge bleiben fail-closed gesperrt.")
        runtime = runtime / 2.0
        components.append(ComponentScore("runtime", "Dienste, Sicherheit und Hostressourcen", runtime, 5.0, self._status(runtime / 5.0 * 100), {
            "disk_free_percent": free_percent,
            "memory_available_percent": memory_percent,
            "services": services,
            "jobs": jobs,
            "scheduler": scheduler,
            "antivirus": antivirus,
            "load_average": host["load_average"],
        }))
        if free_percent < 15:
            recommendations.append("Freier Speicher wird knapp; Logs, Backups und Datenbanken pruefen.")

        # Portfolio market-data pipeline: 5. Disabled is neutral, not falsely "healthy".
        portfolio_enabled = bool(portfolio.get("enabled"))
        portfolio_state = str(portfolio.get("state") or "unknown")
        if not portfolio_enabled:
            portfolio_score = 5.0
        elif portfolio_state == "healthy":
            portfolio_score = 5.0
            coverage += 1
        elif portfolio_state == "degraded":
            portfolio_score = 2.5
            coverage += 1
        else:
            portfolio_score = 0.0
            coverage += 1
        components.append(ComponentScore(
            "portfolio_market_data",
            "Depot- und Marktdatenversorgung",
            portfolio_score,
            5.0,
            "disabled" if not portfolio_enabled else self._status(portfolio_score / 5.0 * 100),
            portfolio,
        ))
        if portfolio_enabled and portfolio_state != "healthy":
            recommendations.append(
                "Portfolio-Kursversorgung ist unvollstaendig oder veraltet; "
                "portfolio doctor und jobs alerts pruefen. Frische Trendanalysen bleiben gesperrt."
            )

        score = _round(sum(component.score for component in components))
        if coverage >= 5:
            confidence = "hoch"
        elif coverage >= 3:
            confidence = "mittel"
        else:
            confidence = "niedrig"

        return {
            "score_schema": 3,
            "generated_at": now_utc_iso(),
            "window": {"days": days, "since": since, "until": now.isoformat()},
            "overall_score": score,
            "rating": self._rating(score),
            "confidence": confidence,
            "interpretation": (
                "Technischer Betriebs- und Qualitaetsindikator. Er misst Verfuegbarkeit, Fehler, "
                "Datenfrische und indirekte Klassifizierungssignale; er beweist nicht die inhaltliche "
                "Richtigkeit jeder Entscheidung."
            ),
            "components": [component.to_dict() for component in components],
            "metrics": {
                "assistant": assistant,
                "mail": mail,
                "host": host,
                "services": services,
                "jobs": jobs,
                "scheduler": scheduler,
                "nextcloud_live": nextcloud_live,
                "antivirus": antivirus,
                "portfolio": portfolio,
            },
            "recommendations": list(dict.fromkeys(recommendations)),
        }

    def record(self, *, days: int = 7, live: bool = False) -> dict[str, Any]:
        report = self.report(days=days, live=live)
        store = self._monitor_store()
        snapshot_id = store.record(report, days=days, live=live)
        store.prune(keep_days=180)
        return {"ok": True, "snapshot_id": snapshot_id, "report": report}

    def history(self, *, days: int = 30, limit: int = 100) -> dict[str, Any]:
        if not self.monitor_database.exists():
            return {
                "snapshots": [],
                "trend": "insufficient_data",
                "score_delta": None,
                "database": str(self.monitor_database),
            }
        store = self._monitor_store()
        rows = store.history(days=days, limit=limit)
        trend = "insufficient_data"
        delta = None
        if len(rows) >= 2:
            newest = float(rows[0]["score"])
            oldest = float(rows[-1]["score"])
            delta = _round(newest - oldest)
            trend = "improving" if delta > 2 else ("declining" if delta < -2 else "stable")
        return {"snapshots": rows, "trend": trend, "score_delta": delta, "database": str(store.path)}
