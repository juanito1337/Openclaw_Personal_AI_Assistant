from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import WORKSPACE_ROOT

DEFAULT_SCHEDULER_DB = WORKSPACE_ROOT / "personal_assistant/data/work_scheduler.sqlite3"
VALID_TOPICS = ("mail", "portfolio", "knowledge", "planning", "operations")
TERMINAL_STATES = ("completed", "degraded", "failed", "cancelled", "interrupted")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(max(0.0, float(value)) for value in values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 2)


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    job: str
    topic: str
    base_priority: int
    deadline_seconds: int
    max_runtime_seconds: int
    description: str


TASK_POLICIES: dict[str, TaskPolicy] = {
    "mail": TaskPolicy(
        job="mail",
        topic="mail",
        base_priority=60,
        deadline_seconds=30 * 60,
        max_runtime_seconds=50 * 60,
        description="Automatische Mailverarbeitung",
    ),
    "portfolio": TaskPolicy(
        job="portfolio",
        topic="portfolio",
        base_priority=70,
        deadline_seconds=30 * 60,
        max_runtime_seconds=15 * 60,
        description="Depot- und Watchlist-Kursversorgung",
    ),
    "sync": TaskPolicy(
        job="sync",
        topic="knowledge",
        base_priority=40,
        deadline_seconds=60 * 60,
        max_runtime_seconds=30 * 60,
        description="Wissensindex-Synchronisation",
    ),
    "monitor": TaskPolicy(
        job="monitor",
        topic="operations",
        base_priority=50,
        deadline_seconds=2 * 60 * 60,
        max_runtime_seconds=20 * 60,
        description="Technischer Performance-Snapshot",
    ),
}


@dataclass(frozen=True, slots=True)
class ClaimResult:
    granted: bool
    ticket_id: str
    reason: str
    position: int | None = None
    score: float | None = None
    lease_token: str = ""
    lease_expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdaptiveWorkScheduler:
    """Persistent non-preemptive scheduler for allowlisted background tasks.

    The scheduler coordinates complete jobs. It deliberately does not replace the
    Ollama request-level priority proxy and never executes commands itself.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        lease_seconds: int | None = None,
        arbitration_seconds: int | None = None,
        starvation_seconds: int | None = None,
    ) -> None:
        default = DEFAULT_SCHEDULER_DB
        if root := os.environ.get("OPENCLAW_COORDINATION_DATA_DIR"):
            default = Path(root).expanduser().resolve() / "work_scheduler.sqlite3"
        self.path = Path(path or default).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now or _utc_now
        self.lease_seconds = max(
            30,
            int(lease_seconds or os.environ.get("SCHEDULER_LEASE_SECONDS", "90")),
        )
        self.arbitration_seconds = max(
            0,
            int(arbitration_seconds if arbitration_seconds is not None else os.environ.get("SCHEDULER_ARBITRATION_SECONDS", "2")),
        )
        self.starvation_seconds = max(
            60,
            int(starvation_seconds or os.environ.get("SCHEDULER_STARVATION_SECONDS", "1800")),
        )
        self.connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_queue (
                id TEXT PRIMARY KEY,
                job TEXT NOT NULL,
                topic TEXT NOT NULL,
                description TEXT NOT NULL,
                base_priority INTEGER NOT NULL,
                status TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                not_before TEXT NOT NULL,
                deadline_at TEXT NOT NULL,
                owner TEXT NOT NULL,
                lease_token TEXT NOT NULL DEFAULT '',
                lease_expires_at TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                wait_ms REAL NOT NULL DEFAULT 0,
                duration_ms REAL NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '',
                exit_code INTEGER,
                error_code TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_task_queue_one_live_job
                ON task_queue(job) WHERE status IN ('pending','running');
            CREATE INDEX IF NOT EXISTS idx_task_queue_status_time
                ON task_queue(status, queued_at);
            CREATE INDEX IF NOT EXISTS idx_task_queue_finished
                ON task_queue(finished_at);

            CREATE TABLE IF NOT EXISTS activity (
                topic TEXT PRIMARY KEY,
                last_seen_at TEXT NOT NULL,
                boost_started_at TEXT NOT NULL,
                boost_until TEXT NOT NULL,
                signal_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT ''
            );
            """
        )

    @staticmethod
    def policy(job: str) -> TaskPolicy:
        clean = str(job or "").strip().casefold()
        try:
            return TASK_POLICIES[clean]
        except KeyError as exc:
            raise ValueError(f"Nicht freigegebener Scheduler-Job: {job}") from exc

    @staticmethod
    def validate_topic(topic: str) -> str:
        clean = str(topic or "").strip().casefold()
        if clean not in VALID_TOPICS:
            raise ValueError(f"Unbekanntes Scheduler-Thema: {topic}")
        return clean

    def _begin(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self.connection.execute("COMMIT")

    def _rollback(self) -> None:
        self.connection.execute("ROLLBACK")

    def record_activity(
        self,
        topic: str,
        *,
        source: str = "interactive-cli",
        boost_minutes: int = 30,
    ) -> dict[str, Any]:
        clean_topic = self.validate_topic(topic)
        now = self.now().astimezone(UTC)
        minutes = max(1, min(int(boost_minutes), 180))
        until = now + timedelta(minutes=minutes)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO activity(topic,last_seen_at,boost_started_at,boost_until,signal_count,source)
                VALUES(?,?,?,?,1,?)
                ON CONFLICT(topic) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    boost_started_at=excluded.boost_started_at,
                    boost_until=CASE
                        WHEN activity.boost_until > excluded.boost_until THEN activity.boost_until
                        ELSE excluded.boost_until
                    END,
                    signal_count=activity.signal_count+1,
                    source=excluded.source
                """,
                (clean_topic, _iso(now), _iso(now), _iso(until), str(source or "")[:80]),
            )
        return {
            "ok": True,
            "topic": clean_topic,
            "source": str(source or "")[:80],
            "boost_until": _iso(until),
            "permissions_changed": False,
        }

    def enqueue(
        self,
        job: str,
        *,
        owner: str,
        metadata: dict[str, Any] | None = None,
        arbitration_seconds: int | None = None,
    ) -> str:
        policy = self.policy(job)
        now = self.now().astimezone(UTC)
        delay = self.arbitration_seconds if arbitration_seconds is None else max(0, int(arbitration_seconds))
        not_before = now + timedelta(seconds=delay)
        deadline = now + timedelta(seconds=policy.deadline_seconds)
        clean_owner = str(owner or "unknown")[:160]
        encoded_metadata = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)[:4000]
        self._begin()
        try:
            self._recover_expired_locked(now)
            existing = self.connection.execute(
                """
                SELECT id FROM task_queue
                WHERE job=? AND status IN ('pending','running')
                ORDER BY queued_at LIMIT 1
                """,
                (policy.job,),
            ).fetchone()
            if existing is not None:
                self._commit()
                return str(existing["id"])
            ticket_id = uuid.uuid4().hex
            self.connection.execute(
                """
                INSERT INTO task_queue(
                    id,job,topic,description,base_priority,status,queued_at,
                    not_before,deadline_at,owner,updated_at,metadata_json
                ) VALUES(?,?,?,?,?,'pending',?,?,?,?,?,?)
                """,
                (
                    ticket_id,
                    policy.job,
                    policy.topic,
                    policy.description,
                    policy.base_priority,
                    _iso(now),
                    _iso(not_before),
                    _iso(deadline),
                    clean_owner,
                    _iso(now),
                    encoded_metadata,
                ),
            )
            self._commit()
            return ticket_id
        except Exception:
            self._rollback()
            raise

    def _activity_locked(self) -> dict[str, sqlite3.Row]:
        rows = self.connection.execute("SELECT * FROM activity").fetchall()
        return {str(row["topic"]): row for row in rows}

    def _score(
        self,
        row: sqlite3.Row,
        *,
        now: datetime,
        activity: dict[str, sqlite3.Row],
    ) -> float:
        queued = _parse_time(row["queued_at"]) or now
        deadline = _parse_time(row["deadline_at"]) or now
        waited = max(0.0, (now - queued).total_seconds())
        age_points = min(60.0, waited / 60.0)
        starvation = 200.0 if waited >= self.starvation_seconds else 0.0

        deadline_window = max(1.0, (deadline - queued).total_seconds())
        deadline_left = (deadline - now).total_seconds()
        if deadline_left <= 0:
            deadline_points = 120.0 + min(60.0, abs(deadline_left) / 60.0)
        else:
            deadline_points = max(0.0, 50.0 * (1.0 - deadline_left / deadline_window))

        activity_points = 0.0
        signal = activity.get(str(row["topic"]))
        if signal is not None:
            start = _parse_time(signal["boost_started_at"])
            until = _parse_time(signal["boost_until"])
            if start is not None and until is not None and start <= now < until:
                window = max(1.0, (until - start).total_seconds())
                remaining = max(0.0, (until - now).total_seconds())
                activity_points = 40.0 * remaining / window

        return round(
            float(row["base_priority"]) + age_points + starvation + deadline_points + activity_points,
            3,
        )

    def _pending_ranked_locked(
        self,
        now: datetime,
        *,
        include_future: bool = False,
    ) -> list[tuple[sqlite3.Row, float]]:
        rows = self.connection.execute(
            "SELECT * FROM task_queue WHERE status='pending' ORDER BY queued_at,id"
        ).fetchall()
        activity = self._activity_locked()
        ranked = [
            (row, self._score(row, now=now, activity=activity))
            for row in rows
            if include_future or (_parse_time(row["not_before"]) or now) <= now
        ]
        ranked.sort(key=lambda item: (-item[1], str(item[0]["queued_at"]), str(item[0]["id"])))
        return ranked

    def _recover_expired_locked(self, now: datetime) -> int:
        rows = self.connection.execute(
            "SELECT id,lease_expires_at,attempts FROM task_queue WHERE status='running'"
        ).fetchall()
        recovered = 0
        for row in rows:
            expires = _parse_time(row["lease_expires_at"])
            if expires is None or expires > now:
                continue
            self.connection.execute(
                """
                UPDATE task_queue
                SET status='pending', lease_token='', lease_expires_at='',
                    started_at='', updated_at=?, attempts=?,
                    error_code='lease-expired',
                    detail='Abgelaufene Scheduler-Lease wurde sicher neu eingereiht'
                WHERE id=? AND status='running'
                """,
                (_iso(now), int(row["attempts"] or 0), str(row["id"])),
            )
            recovered += 1
        return recovered

    def claim(self, ticket_id: str, *, owner: str) -> ClaimResult:
        now = self.now().astimezone(UTC)
        clean_ticket = str(ticket_id or "")
        clean_owner = str(owner or "unknown")[:160]
        self._begin()
        try:
            self._recover_expired_locked(now)
            row = self.connection.execute(
                "SELECT * FROM task_queue WHERE id=?",
                (clean_ticket,),
            ).fetchone()
            if row is None:
                self._commit()
                return ClaimResult(False, clean_ticket, "missing")
            if str(row["status"]) == "running" and str(row["owner"]) == clean_owner:
                self._commit()
                return ClaimResult(
                    True,
                    clean_ticket,
                    "already-owned",
                    lease_token=str(row["lease_token"]),
                    lease_expires_at=str(row["lease_expires_at"]),
                )
            if str(row["status"]) != "pending":
                self._commit()
                return ClaimResult(False, clean_ticket, f"state-{row['status']}")

            active = self.connection.execute(
                """
                SELECT id FROM task_queue
                WHERE status='running' AND lease_expires_at > ?
                LIMIT 1
                """,
                (_iso(now),),
            ).fetchone()
            ranked = self._pending_ranked_locked(now)
            ids = [str(item[0]["id"]) for item in ranked]
            position = ids.index(clean_ticket) + 1 if clean_ticket in ids else None
            score = next((value for candidate, value in ranked if str(candidate["id"]) == clean_ticket), None)
            if active is not None:
                self._commit()
                return ClaimResult(False, clean_ticket, "busy", position=position, score=score)
            if not ranked:
                self._commit()
                return ClaimResult(False, clean_ticket, "arbitration", position=position, score=score)
            selected, selected_score = ranked[0]
            if str(selected["id"]) != clean_ticket:
                self._commit()
                return ClaimResult(False, clean_ticket, "higher-priority", position=position, score=score)

            token = uuid.uuid4().hex
            expires = now + timedelta(seconds=self.lease_seconds)
            queued = _parse_time(selected["queued_at"]) or now
            updated = self.connection.execute(
                """
                UPDATE task_queue
                SET status='running',owner=?,lease_token=?,lease_expires_at=?,
                    started_at=?,updated_at=?,attempts=attempts+1,
                    wait_ms=?,error_code='',detail=''
                WHERE id=? AND status='pending'
                """,
                (
                    clean_owner,
                    token,
                    _iso(expires),
                    _iso(now),
                    _iso(now),
                    max(0.0, (now - queued).total_seconds() * 1000.0),
                    clean_ticket,
                ),
            )
            if updated.rowcount != 1:
                self._commit()
                return ClaimResult(False, clean_ticket, "lost-race", position=position, score=score)
            self._commit()
            return ClaimResult(
                True,
                clean_ticket,
                "granted",
                position=1,
                score=selected_score,
                lease_token=token,
                lease_expires_at=_iso(expires),
            )
        except Exception:
            self._rollback()
            raise

    def renew(self, lease_token: str, *, owner: str) -> bool:
        now = self.now().astimezone(UTC)
        expires = now + timedelta(seconds=self.lease_seconds)
        with self.connection:
            updated = self.connection.execute(
                """
                UPDATE task_queue
                SET lease_expires_at=?,updated_at=?
                WHERE status='running' AND lease_token=? AND owner=?
                """,
                (_iso(expires), _iso(now), str(lease_token or ""), str(owner or "")[:160]),
            )
        return updated.rowcount == 1

    def finish(
        self,
        lease_token: str,
        *,
        owner: str,
        result: str,
        exit_code: int | None = None,
        error_code: str = "",
        detail: str = "",
    ) -> bool:
        clean_result = str(result or "").strip().casefold()
        if clean_result not in TERMINAL_STATES:
            raise ValueError(f"Unbekanntes Scheduler-Ergebnis: {result}")
        now = self.now().astimezone(UTC)
        row = self.connection.execute(
            """
            SELECT id,started_at FROM task_queue
            WHERE status='running' AND lease_token=? AND owner=?
            """,
            (str(lease_token or ""), str(owner or "")[:160]),
        ).fetchone()
        if row is None:
            return False
        started = _parse_time(row["started_at"]) or now
        with self.connection:
            updated = self.connection.execute(
                """
                UPDATE task_queue
                SET status=?,result=?,exit_code=?,error_code=?,detail=?,
                    finished_at=?,updated_at=?,duration_ms=?,
                    lease_token='',lease_expires_at=''
                WHERE id=? AND status='running' AND lease_token=?
                """,
                (
                    clean_result,
                    clean_result,
                    exit_code,
                    str(error_code or "")[:120],
                    str(detail or "")[:2000],
                    _iso(now),
                    _iso(now),
                    max(0.0, (now - started).total_seconds() * 1000.0),
                    str(row["id"]),
                    str(lease_token or ""),
                ),
            )
        return updated.rowcount == 1

    def cancel_pending(self, ticket_id: str, *, detail: str = "") -> bool:
        now = self.now().astimezone(UTC)
        with self.connection:
            updated = self.connection.execute(
                """
                UPDATE task_queue
                SET status='cancelled',result='cancelled',detail=?,
                    finished_at=?,updated_at=?
                WHERE id=? AND status='pending'
                """,
                (str(detail or "")[:2000], _iso(now), _iso(now), str(ticket_id or "")),
            )
        return updated.rowcount == 1

    def _row_payload(
        self,
        row: sqlite3.Row,
        *,
        now: datetime,
        activity: dict[str, sqlite3.Row],
    ) -> dict[str, Any]:
        queued = _parse_time(row["queued_at"]) or now
        deadline = _parse_time(row["deadline_at"])
        started = _parse_time(row["started_at"])
        return {
            "id": str(row["id"]),
            "job": str(row["job"]),
            "topic": str(row["topic"]),
            "description": str(row["description"]),
            "status": str(row["status"]),
            "score": self._score(row, now=now, activity=activity),
            "queued_at": str(row["queued_at"]),
            "wait_seconds": round(max(0.0, (now - queued).total_seconds()), 2),
            "deadline_at": str(row["deadline_at"]),
            "deadline_missed": bool(deadline and deadline < now and str(row["status"]) in {"pending", "running"}),
            "owner": str(row["owner"]),
            "lease_expires_at": str(row["lease_expires_at"]),
            "started_at": str(row["started_at"]),
            "finished_at": str(row["finished_at"]),
            "running_seconds": round(max(0.0, (now - started).total_seconds()), 2) if started else None,
            "attempts": int(row["attempts"] or 0),
            "wait_ms": round(float(row["wait_ms"] or 0.0), 2),
            "duration_ms": round(float(row["duration_ms"] or 0.0), 2),
            "result": str(row["result"]),
            "exit_code": row["exit_code"],
            "error_code": str(row["error_code"]),
            "detail": str(row["detail"]),
        }

    def snapshot(self, *, recent_limit: int = 20) -> dict[str, Any]:
        now = self.now().astimezone(UTC)
        activity = self._activity_locked()
        active_rows = self.connection.execute(
            "SELECT * FROM task_queue WHERE status='running' ORDER BY started_at"
        ).fetchall()
        pending_rows = self.connection.execute(
            "SELECT * FROM task_queue WHERE status='pending' ORDER BY queued_at"
        ).fetchall()
        pending = [self._row_payload(row, now=now, activity=activity) for row in pending_rows]
        pending.sort(key=lambda item: (-float(item["score"]), str(item["queued_at"]), str(item["id"])))
        for index, item in enumerate(pending, start=1):
            item["position"] = index
        recent_rows = self.connection.execute(
            """
            SELECT * FROM task_queue
            WHERE status IN ('completed','degraded','failed','cancelled','interrupted')
            ORDER BY finished_at DESC LIMIT ?
            """,
            (max(1, min(int(recent_limit), 500)),),
        ).fetchall()
        activity_payload = []
        for topic, row in sorted(activity.items()):
            until = _parse_time(row["boost_until"])
            activity_payload.append({
                "topic": topic,
                "last_seen_at": str(row["last_seen_at"]),
                "boost_until": str(row["boost_until"]),
                "active": bool(until and until > now),
                "signal_count": int(row["signal_count"] or 0),
                "source": str(row["source"]),
            })
        result_counts = {
            str(row["result"]): int(row["count"])
            for row in self.connection.execute(
                """
                SELECT result,COUNT(*) count FROM task_queue
                WHERE finished_at >= ? AND result != ''
                GROUP BY result
                """,
                (_iso(now - timedelta(days=7)),),
            ).fetchall()
        }
        wait_row = self.connection.execute(
            """
            SELECT AVG(wait_ms) average_wait_ms,MAX(wait_ms) max_wait_ms,
                   AVG(duration_ms) average_duration_ms,MAX(duration_ms) max_duration_ms
            FROM task_queue WHERE finished_at >= ?
            """,
            (_iso(now - timedelta(days=7)),),
        ).fetchone()
        timing_rows = self.connection.execute(
            """
            SELECT wait_ms,duration_ms FROM task_queue
            WHERE finished_at >= ?
            """,
            (_iso(now - timedelta(days=7)),),
        ).fetchall()
        wait_values = [float(row["wait_ms"] or 0.0) for row in timing_rows]
        duration_values = [float(row["duration_ms"] or 0.0) for row in timing_rows]
        run_count = sum(result_counts.values())
        successful = int(result_counts.get("completed", 0))
        by_job: dict[str, dict[str, Any]] = {
            name: {
                "runs": 0,
                "results": {},
                "success_rate": None,
                "average_wait_ms": 0.0,
                "average_duration_ms": 0.0,
                "last_finished_at": "",
                "last_success_at": "",
            }
            for name in sorted(TASK_POLICIES)
        }
        for row in self.connection.execute(
            """
            SELECT job,COUNT(*) runs,AVG(wait_ms) average_wait_ms,
                   AVG(duration_ms) average_duration_ms,
                   MAX(finished_at) last_finished_at,
                   MAX(CASE WHEN result='completed' THEN finished_at ELSE '' END) last_success_at
            FROM task_queue WHERE finished_at >= ?
            GROUP BY job
            """,
            (_iso(now - timedelta(days=7)),),
        ).fetchall():
            name = str(row["job"])
            if name not in by_job:
                continue
            by_job[name].update({
                "runs": int(row["runs"] or 0),
                "average_wait_ms": round(float(row["average_wait_ms"] or 0.0), 2),
                "average_duration_ms": round(float(row["average_duration_ms"] or 0.0), 2),
                "last_finished_at": str(row["last_finished_at"] or ""),
                "last_success_at": str(row["last_success_at"] or ""),
            })
        for row in self.connection.execute(
            """
            SELECT job,result,COUNT(*) count FROM task_queue
            WHERE finished_at >= ? AND result != ''
            GROUP BY job,result
            """,
            (_iso(now - timedelta(days=7)),),
        ).fetchall():
            name = str(row["job"])
            if name in by_job:
                by_job[name]["results"][str(row["result"])] = int(row["count"])
        for item in by_job.values():
            item["success_rate"] = (
                round(int(item["results"].get("completed", 0)) / int(item["runs"]), 4)
                if item["runs"]
                else None
            )
        return {
            "ok": True,
            "generated_at": _iso(now),
            "database": str(self.path),
            "active": [self._row_payload(row, now=now, activity=activity) for row in active_rows],
            "pending": pending,
            "recent": [self._row_payload(row, now=now, activity=activity) for row in recent_rows],
            "activity": activity_payload,
            "limits": {
                "concurrency": 1,
                "lease_seconds": self.lease_seconds,
                "arbitration_seconds": self.arbitration_seconds,
                "starvation_seconds": self.starvation_seconds,
                "non_preemptive": True,
            },
            "seven_day": {
                "runs": run_count,
                "results": result_counts,
                "success_rate": round(successful / run_count, 4) if run_count else None,
                "average_wait_ms": round(float(wait_row["average_wait_ms"] or 0.0), 2) if wait_row else 0.0,
                "max_wait_ms": round(float(wait_row["max_wait_ms"] or 0.0), 2) if wait_row else 0.0,
                "p50_wait_ms": _percentile(wait_values, 0.50),
                "p95_wait_ms": _percentile(wait_values, 0.95),
                "average_duration_ms": round(float(wait_row["average_duration_ms"] or 0.0), 2) if wait_row else 0.0,
                "max_duration_ms": round(float(wait_row["max_duration_ms"] or 0.0), 2) if wait_row else 0.0,
                "p50_duration_ms": _percentile(duration_values, 0.50),
                "p95_duration_ms": _percentile(duration_values, 0.95),
                "by_job": by_job,
            },
        }

    def health(self) -> dict[str, Any]:
        now = self.now().astimezone(UTC)
        try:
            integrity_row = self.connection.execute("PRAGMA integrity_check").fetchone()
            integrity = str(integrity_row[0] if integrity_row else "unknown")
        except sqlite3.Error as exc:
            return {
                "enabled": True,
                "ok": False,
                "state": "failed",
                "database": str(self.path),
                "error": str(exc)[:500],
            }
        snapshot = self.snapshot(recent_limit=10)
        stale_leases = [
            item for item in snapshot["active"]
            if (_parse_time(item["lease_expires_at"]) or now) <= now
        ]
        deadline_misses = [
            item for item in [*snapshot["active"], *snapshot["pending"]]
            if item["deadline_missed"]
        ]
        failed_recent = [
            item for item in snapshot["recent"]
            if item["result"] in {"failed", "interrupted"}
        ]
        ok = integrity == "ok" and not stale_leases and not deadline_misses
        state = "healthy" if ok else ("failed" if integrity != "ok" or stale_leases else "degraded")
        return {
            "enabled": True,
            "ok": ok,
            "state": state,
            "database": str(self.path),
            "integrity": integrity,
            "active": len(snapshot["active"]),
            "pending": len(snapshot["pending"]),
            "stale_leases": len(stale_leases),
            "deadline_misses": len(deadline_misses),
            "failed_recent": len(failed_recent),
            "oldest_pending_seconds": max(
                (float(item["wait_seconds"]) for item in snapshot["pending"]),
                default=0.0,
            ),
            "seven_day": snapshot["seven_day"],
        }

    def doctor(self) -> dict[str, Any]:
        health = self.health()
        health["policies"] = [asdict(TASK_POLICIES[name]) for name in sorted(TASK_POLICIES)]
        health["topics"] = list(VALID_TOPICS)
        return health

    def prune(self, *, keep_days: int = 180) -> int:
        cutoff = _iso(self.now().astimezone(UTC) - timedelta(days=max(7, int(keep_days))))
        with self.connection:
            cursor = self.connection.execute(
                """
                DELETE FROM task_queue
                WHERE status IN ('completed','degraded','failed','cancelled','interrupted')
                  AND finished_at != '' AND finished_at < ?
                """,
                (cutoff,),
            )
        return int(cursor.rowcount or 0)
