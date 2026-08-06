from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import now_utc_iso

_CATEGORY_VERDICTS = {"spam", "routine", "relevant"}


def _classification_category(value: Any) -> str | None:
    category = str(value or "").strip().casefold()
    if category == "appointment":
        return "relevant"
    return category if category in _CATEGORY_VERDICTS else None


@dataclass(slots=True)
class PredictorMetrics:
    samples: int = 0
    predictions: int = 0
    correct: int = 0
    wrong: int = 0
    abstentions: int = 0
    relevant_missed: int = 0
    spam_forward_risk: int = 0
    confusion: dict[str, Counter[str]] = field(
        default_factory=lambda: {category: Counter() for category in sorted(_CATEGORY_VERDICTS)}
    )
    actual_counts: Counter[str] = field(default_factory=Counter)
    predicted_counts: Counter[str] = field(default_factory=Counter)

    def observe(self, actual: str, predicted: str | None) -> None:
        self.samples += 1
        self.actual_counts[actual] += 1
        if predicted not in _CATEGORY_VERDICTS:
            self.abstentions += 1
            self.confusion[actual]["abstain"] += 1
            return
        self.predictions += 1
        self.predicted_counts[predicted] += 1
        self.confusion[actual][predicted] += 1
        if predicted == actual:
            self.correct += 1
        else:
            self.wrong += 1
            if actual == "relevant" and predicted in {"spam", "routine"}:
                self.relevant_missed += 1
            if actual == "spam" and predicted == "relevant":
                self.spam_forward_risk += 1

    def to_dict(self) -> dict[str, Any]:
        coverage = self.predictions / self.samples if self.samples else 0.0
        accuracy = self.correct / self.predictions if self.predictions else 0.0
        by_actual: dict[str, Any] = {}
        matrix: dict[str, dict[str, int]] = {}
        for actual in sorted(_CATEGORY_VERDICTS):
            counts = self.confusion[actual]
            samples = self.actual_counts[actual]
            predictions = samples - counts.get("abstain", 0)
            correct = counts.get(actual, 0)
            wrong = predictions - correct
            by_actual[actual] = {
                "samples": samples,
                "predictions": predictions,
                "abstentions": counts.get("abstain", 0),
                "correct": correct,
                "wrong": wrong,
                "accuracy_percent": round((correct / predictions * 100.0) if predictions else 0.0, 2),
            }
            matrix[actual] = {
                predicted: int(counts.get(predicted, 0))
                for predicted in ("spam", "routine", "relevant", "abstain")
            }
        return {
            "samples": self.samples,
            "predictions": self.predictions,
            "abstentions": self.abstentions,
            "coverage": round(coverage, 4),
            "coverage_percent": round(coverage * 100.0, 2),
            "correct": self.correct,
            "wrong": self.wrong,
            "accuracy": round(accuracy, 4),
            "accuracy_percent": round(accuracy * 100.0, 2),
            "relevant_missed": self.relevant_missed,
            "spam_forward_risk": self.spam_forward_risk,
            "by_actual_category": by_actual,
            "confusion_matrix": matrix,
            "predicted_distribution": dict(sorted(self.predicted_counts.items())),
        }


class LearningQualityAnalyzer:
    """Chronological, privacy-preserving evaluation of explicit corrections.

    Old feedback is never allowed to test itself. Routine and spam pattern
    predictions require two older, mutually consistent corrections, while one
    older relevant correction may protect a later important message. Original
    automated decisions are evaluated only when an immutable snapshot was stored
    at correction time; legacy rows deliberately abstain.
    """

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def _rows(self, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100000))
        rows = self.storage.connection.execute(
            """
            SELECT f.id, f.stable_key, f.verdict, lower(COALESCE(f.sender_addr, '')) AS sender_addr,
                   lower(COALESCE(f.sender_domain, '')) AS sender_domain,
                   COALESCE(NULLIF(f.subject_pattern, ''), f.subject_signature, '') AS subject_pattern,
                   COALESCE(f.label, '') AS label, COALESCE(f.feature_json, '') AS feature_json,
                   f.created_at, f.original_category, f.original_confidence,
                   COALESCE(f.original_source, '') AS original_source,
                   COALESCE(f.original_rule_decision, '') AS original_rule_decision,
                   COALESCE(f.original_snapshot_valid, 0) AS original_snapshot_valid
            FROM feedback AS f
            WHERE f.id IN (SELECT id FROM feedback ORDER BY id DESC LIMIT ?)
            ORDER BY f.id ASC
            """,
            (safe_limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _consistent_prediction(counts: Counter[str], *, minimum: int) -> str | None:
        category_counts = Counter({key: value for key, value in counts.items() if key in _CATEGORY_VERDICTS})
        if sum(category_counts.values()) < minimum or len(category_counts) != 1:
            return None
        return next(iter(category_counts))

    @staticmethod
    def _safe_pattern_prediction(counts: Counter[str]) -> str | None:
        category_counts = Counter({key: value for key, value in counts.items() if key in _CATEGORY_VERDICTS})
        if len(category_counts) != 1:
            return None
        verdict, count = next(iter(category_counts.items()))
        minimum = 1 if verdict == "relevant" else 2
        return verdict if count >= minimum else None

    def report(self, *, limit: int = 5000) -> dict[str, Any]:
        rows = self._rows(limit)
        category_rows = [row for row in rows if str(row.get("verdict") or "") in _CATEGORY_VERDICTS]

        sender_history: dict[str, Counter[str]] = defaultdict(Counter)
        pattern_history: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        sender_only = PredictorMetrics()
        pattern = PredictorMetrics()
        stored_decision = PredictorMetrics()

        verdict_distribution: Counter[str] = Counter()
        label_distribution: Counter[str] = Counter()
        original_source_distribution: Counter[str] = Counter()
        pattern_counts: Counter[tuple[str, str]] = Counter()
        usable_rows = 0
        feature_rows = 0
        original_snapshot_rows = 0

        for row in rows:
            verdict = str(row.get("verdict") or "")
            verdict_distribution[verdict] += 1
            label = str(row.get("label") or "").strip()
            if label:
                label_distribution[label] += 1
            if str(row.get("feature_json") or "").strip():
                feature_rows += 1
            if verdict not in _CATEGORY_VERDICTS:
                continue

            sender = str(row.get("sender_addr") or "")
            subject_pattern = str(row.get("subject_pattern") or "")
            if sender and subject_pattern:
                usable_rows += 1
                pattern_counts[(sender, subject_pattern)] += 1

            baseline_prediction = self._consistent_prediction(sender_history[sender], minimum=2) if sender else None
            pattern_prediction = (
                self._safe_pattern_prediction(pattern_history[(sender, subject_pattern)])
                if sender and subject_pattern else None
            )
            sender_only.observe(verdict, baseline_prediction)
            pattern.observe(verdict, pattern_prediction)

            if int(row.get("original_snapshot_valid") or 0) == 1:
                original_snapshot_rows += 1
                source = str(row.get("original_source") or "unknown") or "unknown"
                original_source_distribution[source] += 1
                stored_decision.observe(verdict, _classification_category(row.get("original_category")))

            if sender:
                sender_history[sender][verdict] += 1
            if sender and subject_pattern:
                pattern_history[(sender, subject_pattern)][verdict] += 1

        repeated_patterns = sum(1 for count in pattern_counts.values() if count >= 2)
        singleton_patterns = sum(1 for count in pattern_counts.values() if count == 1)
        mixed_count = len(self.storage.mixed_senders(limit=100000))
        conflicts = self.storage.pattern_conflicts(limit=100000)

        baseline_data = sender_only.to_dict()
        pattern_data = pattern.to_dict()
        stored_data = stored_decision.to_dict()
        stored_data["available"] = original_snapshot_rows > 0
        stored_data["legacy_rows_without_snapshot"] = len(category_rows) - original_snapshot_rows
        stored_data["source_distribution"] = dict(sorted(original_source_distribution.items()))
        comparison = {
            "accuracy_delta_percentage_points": round(
                pattern_data["accuracy_percent"] - baseline_data["accuracy_percent"], 2
            ),
            "coverage_delta_percentage_points": round(
                pattern_data["coverage_percent"] - baseline_data["coverage_percent"], 2
            ),
            "relevant_missed_delta": pattern_data["relevant_missed"] - baseline_data["relevant_missed"],
            "spam_forward_risk_delta": pattern_data["spam_forward_risk"] - baseline_data["spam_forward_risk"],
        }

        recommendations: list[str] = []
        if len(category_rows) < 50:
            recommendations.append(
                "Die Datenbasis ist noch klein; mindestens 50 bis 100 konsistente Kategorie-Korrekturen sammeln."
            )
        if mixed_count:
            recommendations.append(
                "Gemischte Absender vorhanden: keine pauschalen Absenderregeln setzen; Muster und Typ-Labels priorisieren."
            )
        if conflicts:
            recommendations.append(
                "Widerspruechliche Muster mit 'mail learning conflicts' anhand ihrer conflict_id pruefen."
            )
        if singleton_patterns > repeated_patterns:
            recommendations.append(
                "Viele Muster haben erst ein Beispiel; Routine und Spam werden deshalb erst ab zwei konsistenten Treffern erzwungen."
            )
        if original_snapshot_rows == 0:
            recommendations.append(
                "Historische Modellqualitaet ist nicht belastbar messbar; unveraenderliche Originalentscheidungen werden erst ab R22.2 gespeichert."
            )
        if not recommendations:
            recommendations.append("Keine offensichtlichen Datenqualitaetsprobleme erkannt; weiter beobachten.")

        return {
            "ok": True,
            "generated_at": now_utc_iso(),
            "privacy": {
                "mail_bodies_read": False,
                "attachments_read": False,
                "report_contains_sender_addresses": False,
                "report_contains_subjects": False,
            },
            "data_quality": {
                "feedback_rows": len(rows),
                "category_feedback_rows": len(category_rows),
                "not_spam_rows": int(verdict_distribution.get("not_spam", 0)),
                "usable_sender_pattern_rows": usable_rows,
                "rows_with_feature_metadata": feature_rows,
                "labeled_rows": sum(label_distribution.values()),
                "rows_with_immutable_original_decision": original_snapshot_rows,
                "legacy_rows_without_original_decision": len(category_rows) - original_snapshot_rows,
                "verdict_distribution": dict(sorted(verdict_distribution.items())),
                "label_distribution": dict(sorted(label_distribution.items())),
                "unique_sender_patterns": len(pattern_counts),
                "repeated_sender_patterns": repeated_patterns,
                "singleton_sender_patterns": singleton_patterns,
                "mixed_senders": mixed_count,
                "conflicting_sender_patterns": len(conflicts),
            },
            "evaluation": {
                "method": "chronological-walk-forward",
                "self_test_leakage": False,
                "sender_only_baseline": baseline_data,
                "pattern_learning": pattern_data,
                "stored_original_decision": stored_data,
                "notes": [
                    "Sender-only predicts only after two older, mutually consistent sender corrections.",
                    "Pattern learning requires two older consistent routine/spam corrections; one older relevant correction may protect important mail.",
                    "Original decisions are measured only from immutable snapshots captured before a user correction; legacy rows abstain.",
                ],
            },
            "comparison": comparison,
            "recommendations": recommendations,
        }

    @staticmethod
    def _pseudonym(key: bytes, value: Any) -> str:
        text = str(value or "").strip().casefold().encode("utf-8", errors="replace")
        return hmac.new(key, text, hashlib.sha256).hexdigest()[:24] if text else ""

    def export_dataset(self, output: Path, *, limit: int = 5000) -> Path:
        rows = self._rows(limit)
        key = secrets.token_bytes(32)
        records: list[dict[str, Any]] = []
        for row in rows:
            try:
                features = json.loads(str(row.get("feature_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                features = {}
            if not isinstance(features, dict):
                features = {}
            created = str(row.get("created_at") or "")
            snapshot_valid = int(row.get("original_snapshot_valid") or 0) == 1
            records.append({
                "feedback_id": int(row.get("id") or 0),
                "message_key": self._pseudonym(key, row.get("stable_key")),
                "sender": self._pseudonym(key, row.get("sender_addr")),
                "sender_domain": self._pseudonym(key, row.get("sender_domain")),
                "subject_pattern": self._pseudonym(key, row.get("subject_pattern")),
                "verdict": str(row.get("verdict") or ""),
                "label": str(row.get("label") or "")[:80],
                "features": features,
                "original_decision_available": snapshot_valid,
                "original_category": _classification_category(row.get("original_category")) if snapshot_valid else None,
                "original_source": str(row.get("original_source") or "")[:80] if snapshot_valid else "",
                "original_confidence": row.get("original_confidence") if snapshot_valid else None,
                "created_date": created[:10],
            })

        payload = {
            "schema_version": 2,
            "created_at": now_utc_iso(),
            "privacy": {
                "pseudonymization": "per-export keyed HMAC; key is not stored",
                "contains_mail_bodies": False,
                "contains_raw_subjects": False,
                "contains_email_addresses": False,
                "contains_message_ids": False,
                "contains_original_reasons": False,
                "cross_export_linkability": False,
            },
            "records": records,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(output)
        output.chmod(0o600)
        return output
