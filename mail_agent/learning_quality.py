from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import (
    SUBJECT_PATTERN_VERSION_CURRENT,
    SUBJECT_PATTERN_VERSION_LEGACY,
    normalize_subject_pattern,
    now_utc_iso,
)

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
            false_positive = sum(
                self.confusion[other].get(actual, 0)
                for other in sorted(_CATEGORY_VERDICTS)
                if other != actual
            )
            false_negative = sum(
                counts.get(predicted, 0)
                for predicted in sorted(_CATEGORY_VERDICTS)
                if predicted != actual
            )
            precision_denominator = correct + false_positive
            recall_denominator = correct + false_negative
            by_actual[actual] = {
                "samples": samples,
                "predictions": predictions,
                "abstentions": counts.get("abstain", 0),
                "correct": correct,
                "wrong": wrong,
                "accuracy_percent": round((correct / predictions * 100.0) if predictions else 0.0, 2),
                "false_positive": false_positive,
                "false_negative": false_negative,
                "precision": round(correct / precision_denominator, 4) if precision_denominator else 0.0,
                "recall": round(correct / recall_denominator, 4) if recall_denominator else 0.0,
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
            "false_positive_total": sum(
                sum(
                    self.confusion[actual].get(predicted, 0)
                    for actual in sorted(_CATEGORY_VERDICTS)
                    if actual != predicted
                )
                for predicted in sorted(_CATEGORY_VERDICTS)
            ),
            "false_negative_total": self.wrong,
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
                   COALESCE(f.subject, '') AS subject,
                   COALESCE(NULLIF(f.subject_pattern, ''), f.subject_signature, '') AS subject_pattern,
                   COALESCE(f.pattern_version, 1) AS pattern_version,
                   COALESCE(f.label, '') AS label, COALESCE(f.feature_json, '') AS feature_json,
                   f.created_at, f.original_category, f.original_confidence,
                   COALESCE(f.original_source, '') AS original_source,
                   COALESCE(f.original_rule_decision, '') AS original_rule_decision,
                   COALESCE(f.original_snapshot_valid, 0) AS original_snapshot_valid,
                   f.decision_snapshot_id,
                   d.decided_at, COALESCE(d.source_type, '') AS decision_source_type,
                   COALESCE(d.feature_json, '') AS decision_feature_json,
                   COALESCE(d.rule_snapshot_json, '') AS rule_snapshot_json,
                   COALESCE(d.model_snapshot_json, '') AS model_snapshot_json,
                   COALESCE(d.combined_snapshot_json, '') AS combined_snapshot_json,
                   COALESCE(d.sender_group_sha256, '') AS sender_group_sha256,
                   COALESCE(d.thread_group_sha256, '') AS thread_group_sha256
            FROM feedback AS f
            LEFT JOIN decision_snapshots AS d ON d.id = f.decision_snapshot_id
            WHERE f.id IN (SELECT id FROM feedback ORDER BY id DESC LIMIT ?)
            ORDER BY f.id ASC
            """,
            (safe_limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _snapshot_metrics(cls, rows: list[dict[str, Any]], field: str) -> PredictorMetrics:
        metrics = PredictorMetrics()
        for row in rows:
            actual = str(row.get("verdict") or "")
            if actual not in _CATEGORY_VERDICTS or row.get("decision_snapshot_id") is None:
                continue
            component = cls._json_dict(row.get(field))
            metrics.observe(actual, _classification_category(component.get("category")))
        return metrics

    @classmethod
    def _grouped_temporal_holdout(cls, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a time-ordered holdout without sender or thread leakage."""

        eligible = [
            row for row in rows
            if str(row.get("verdict") or "") in _CATEGORY_VERDICTS
            and row.get("decision_snapshot_id") is not None
        ]
        eligible.sort(key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)))
        if len(eligible) < 2:
            empty = PredictorMetrics().to_dict()
            return {
                "method": "chronological-70-30-purged-by-sender-and-thread",
                "train_samples": 0,
                "eval_samples": 0,
                "purged_samples": len(eligible),
                "sender_overlap": 0,
                "thread_overlap": 0,
                "predictors": {name: empty for name in ("sender", "pattern", "rule", "model", "combined")},
            }

        cutoff = max(1, min(len(eligible) - 1, int(len(eligible) * 0.7)))
        before = eligible[:cutoff]
        after = eligible[cutoff:]
        before_senders = {str(row.get("sender_group_sha256") or "") for row in before}
        after_senders = {str(row.get("sender_group_sha256") or "") for row in after}
        before_threads = {str(row.get("thread_group_sha256") or "") for row in before}
        after_threads = {str(row.get("thread_group_sha256") or "") for row in after}
        overlapping_senders = (before_senders & after_senders) - {""}
        overlapping_threads = (before_threads & after_threads) - {""}

        def clean(partition: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                row for row in partition
                if str(row.get("sender_group_sha256") or "") not in overlapping_senders
                and str(row.get("thread_group_sha256") or "") not in overlapping_threads
            ]

        train = clean(before)
        evaluation = clean(after)
        sender_history: dict[str, Counter[str]] = defaultdict(Counter)
        pattern_history: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        for row in train:
            actual = str(row.get("verdict") or "")
            sender = str(row.get("sender_group_sha256") or "")
            pattern = normalize_subject_pattern(
                str(row.get("subject") or ""), version=SUBJECT_PATTERN_VERSION_CURRENT
            )
            if sender:
                sender_history[sender][actual] += 1
            if sender and pattern:
                pattern_history[(sender, pattern)][actual] += 1

        sender_metrics = PredictorMetrics()
        pattern_metrics = PredictorMetrics()
        for row in evaluation:
            actual = str(row.get("verdict") or "")
            sender = str(row.get("sender_group_sha256") or "")
            pattern = normalize_subject_pattern(
                str(row.get("subject") or ""), version=SUBJECT_PATTERN_VERSION_CURRENT
            )
            sender_metrics.observe(
                actual,
                cls._consistent_prediction(sender_history[sender], minimum=2) if sender else None,
            )
            pattern_metrics.observe(
                actual,
                cls._safe_pattern_prediction(pattern_history[(sender, pattern)])
                if sender and pattern else None,
            )

        return {
            "method": "chronological-70-30-purged-by-sender-and-thread",
            "train_samples": len(train),
            "eval_samples": len(evaluation),
            "purged_samples": len(eligible) - len(train) - len(evaluation),
            "sender_overlap": 0,
            "thread_overlap": 0,
            "cutoff_feedback_id": int(eligible[cutoff]["id"]),
            "predictors": {
                "sender": sender_metrics.to_dict(),
                "pattern": pattern_metrics.to_dict(),
                "rule": cls._snapshot_metrics(evaluation, "rule_snapshot_json").to_dict(),
                "model": cls._snapshot_metrics(evaluation, "model_snapshot_json").to_dict(),
                "combined": cls._snapshot_metrics(evaluation, "combined_snapshot_json").to_dict(),
            },
        }

    @classmethod
    def _impact_review(cls, rows: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> dict[str, Any]:
        cases: list[dict[str, Any]] = []
        for row in rows:
            if row.get("decision_snapshot_id") is None:
                continue
            actual = str(row.get("verdict") or "")
            combined = cls._json_dict(row.get("combined_snapshot_json"))
            predicted = _classification_category(combined.get("category"))
            risk = ""
            priority = 0
            if actual == "relevant" and predicted in {"spam", "routine"}:
                risk, priority = "relevant-not-forwarded", 100
            elif actual == "spam" and predicted == "relevant":
                risk, priority = "spam-forward-risk", 100
            if risk:
                cases.append({
                    "feedback_id": int(row.get("id") or 0),
                    "decision_snapshot_id": int(row["decision_snapshot_id"]),
                    "risk": risk,
                    "priority": priority,
                    "source_type": str(row.get("decision_source_type") or "unknown"),
                })
        for conflict in conflicts:
            cases.append({
                "conflict_id": str(conflict.get("conflict_id") or ""),
                "risk": "conflicting-pattern",
                "priority": 80,
                "feedback_count": int(conflict.get("total") or 0),
            })
        cases.sort(
            key=lambda item: (
                -int(item.get("priority") or 0),
                int(item.get("feedback_id") or 0),
                str(item.get("conflict_id") or ""),
            )
        )
        counts = Counter(str(item["risk"]) for item in cases)
        return {
            "content_free": True,
            "automatic_activation": False,
            "count": len(cases),
            "by_risk": dict(sorted(counts.items())),
            "cases": cases[:100],
            "results_may_be_truncated": len(cases) > 100,
        }

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

    @classmethod
    def _pattern_metrics(cls, rows: list[dict[str, Any]], *, version: int) -> PredictorMetrics:
        """Evaluate one normalizer chronologically without testing a row on itself."""
        history: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        metrics = PredictorMetrics()
        for row in rows:
            verdict = str(row.get("verdict") or "")
            if verdict not in _CATEGORY_VERDICTS:
                continue
            sender = str(row.get("sender_addr") or "")
            pattern = normalize_subject_pattern(str(row.get("subject") or ""), version=version)
            prediction = (
                cls._safe_pattern_prediction(history[(sender, pattern)])
                if sender and pattern
                else None
            )
            metrics.observe(verdict, prediction)
            if sender and pattern:
                history[(sender, pattern)][verdict] += 1
        return metrics

    def report(self, *, limit: int = 5000) -> dict[str, Any]:
        rows = self._rows(limit)
        category_rows = [row for row in rows if str(row.get("verdict") or "") in _CATEGORY_VERDICTS]

        sender_history: dict[str, Counter[str]] = defaultdict(Counter)
        sender_only = PredictorMetrics()
        stored_decision = PredictorMetrics()

        verdict_distribution: Counter[str] = Counter()
        label_distribution: Counter[str] = Counter()
        original_source_distribution: Counter[str] = Counter()
        decision_source_distribution: Counter[str] = Counter()
        pattern_counts: Counter[tuple[int, str, str]] = Counter()
        usable_rows = 0
        feature_rows = 0
        original_snapshot_rows = 0
        decision_feature_rows = 0

        for row in rows:
            verdict = str(row.get("verdict") or "")
            verdict_distribution[verdict] += 1
            label = str(row.get("label") or "").strip()
            if label:
                label_distribution[label] += 1
            if str(row.get("feature_json") or "").strip():
                feature_rows += 1
            if self._json_dict(row.get("decision_feature_json")):
                decision_feature_rows += 1
            if verdict not in _CATEGORY_VERDICTS:
                continue

            sender = str(row.get("sender_addr") or "")
            subject_pattern = str(row.get("subject_pattern") or "")
            if sender and subject_pattern:
                usable_rows += 1
                pattern_counts[(int(row.get("pattern_version") or 1), sender, subject_pattern)] += 1

            baseline_prediction = self._consistent_prediction(sender_history[sender], minimum=2) if sender else None
            sender_only.observe(verdict, baseline_prediction)

            if row.get("decision_snapshot_id") is not None:
                original_snapshot_rows += 1
                source = str(row.get("decision_source_type") or "unknown") or "unknown"
                original_source_distribution[source] += 1
                decision_source_distribution[source] += 1
                combined = self._json_dict(row.get("combined_snapshot_json"))
                stored_decision.observe(verdict, _classification_category(combined.get("category")))

            if sender:
                sender_history[sender][verdict] += 1
        legacy_pattern = self._pattern_metrics(
            category_rows, version=SUBJECT_PATTERN_VERSION_LEGACY
        ).to_dict()
        current_pattern = self._pattern_metrics(
            category_rows, version=SUBJECT_PATTERN_VERSION_CURRENT
        ).to_dict()

        repeated_patterns = sum(1 for count in pattern_counts.values() if count >= 2)
        singleton_patterns = sum(1 for count in pattern_counts.values() if count == 1)
        mixed_count = len(self.storage.mixed_senders(limit=100000))
        conflicts = self.storage.pattern_conflicts(limit=100000)
        rule_metrics = self._snapshot_metrics(category_rows, "rule_snapshot_json").to_dict()
        model_metrics = self._snapshot_metrics(category_rows, "model_snapshot_json").to_dict()
        combined_metrics = self._snapshot_metrics(category_rows, "combined_snapshot_json").to_dict()
        grouped_holdout = self._grouped_temporal_holdout(category_rows)
        impact_review = self._impact_review(category_rows, conflicts)

        baseline_data = sender_only.to_dict()
        pattern_data = current_pattern
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
        pattern_version_comparison = {
            "baseline_version": SUBJECT_PATTERN_VERSION_LEGACY,
            "candidate_version": SUBJECT_PATTERN_VERSION_CURRENT,
            "sample": len(category_rows),
            "version_1": legacy_pattern,
            "version_2": current_pattern,
            "coverage_delta_percentage_points": round(
                current_pattern["coverage_percent"] - legacy_pattern["coverage_percent"], 2
            ),
            "accuracy_delta_percentage_points": round(
                current_pattern["accuracy_percent"] - legacy_pattern["accuracy_percent"], 2
            ),
            "relevant_missed_delta": (
                current_pattern["relevant_missed"] - legacy_pattern["relevant_missed"]
            ),
            "spam_forward_risk_delta": (
                current_pattern["spam_forward_risk"] - legacy_pattern["spam_forward_risk"]
            ),
        }
        activation_allowed = bool(
            current_pattern["relevant_missed"] <= legacy_pattern["relevant_missed"]
            and current_pattern["spam_forward_risk"] <= legacy_pattern["spam_forward_risk"]
        )
        activation_gate = {
            "allowed": activation_allowed,
            "candidate_version": SUBJECT_PATTERN_VERSION_CURRENT,
            "baseline_version": SUBJECT_PATTERN_VERSION_LEGACY,
            "reason": (
                "candidate-does-not-worsen-safety-errors"
                if activation_allowed
                else "candidate-worsens-safety-errors"
            ),
            "relevant_missed_not_worse": (
                current_pattern["relevant_missed"] <= legacy_pattern["relevant_missed"]
            ),
            "spam_forward_risk_not_worse": (
                current_pattern["spam_forward_risk"] <= legacy_pattern["spam_forward_risk"]
            ),
            "minimum_evidence": {"relevant": 1, "routine": 2, "spam": 2},
            "conflict_free_required": True,
            "automatic_activation": False,
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
                "rows_with_decision_feature_snapshot": decision_feature_rows,
                "labeled_rows": sum(label_distribution.values()),
                "rows_with_immutable_original_decision": original_snapshot_rows,
                "legacy_rows_without_original_decision": len(category_rows) - original_snapshot_rows,
                "decision_source_distribution": dict(sorted(decision_source_distribution.items())),
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
                "rule_decision": rule_metrics,
                "model_decision": model_metrics,
                "combined_decision": combined_metrics,
                "grouped_temporal_holdout": grouped_holdout,
                "subject_pattern_versions": {
                    "method": "chronological-walk-forward",
                    "self_test_leakage": False,
                    "comparison": pattern_version_comparison,
                    "activation_gate": activation_gate,
                },
                "stored_original_decision": stored_data,
                "notes": [
                    "Sender-only predicts only after two older, mutually consistent sender corrections.",
                    "Pattern learning requires two older consistent routine/spam corrections; one older relevant correction may protect important mail.",
                    (
                        "Version 1 and version 2 are recomputed independently from raw stored "
                        "subjects; persisted legacy patterns are never rewritten."
                    ),
                    "Original decisions are measured only from immutable snapshots captured before a user correction; legacy rows abstain.",
                    "The holdout purges every sender or thread that crosses the chronological split.",
                    "Learning suggestions and quality findings never activate a rule automatically.",
                ],
            },
            "comparison": comparison,
            "review_priority": impact_review,
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
            snapshot_valid = row.get("decision_snapshot_id") is not None
            records.append({
                "feedback_id": int(row.get("id") or 0),
                "message_key": self._pseudonym(key, row.get("stable_key")),
                "sender": self._pseudonym(key, row.get("sender_addr")),
                "sender_domain": self._pseudonym(key, row.get("sender_domain")),
                "subject_pattern": self._pseudonym(key, row.get("subject_pattern")),
                "pattern_version": int(row.get("pattern_version") or 1),
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
            "schema_version": 4,
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
