from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .learning import message_feature_metadata
from .models import Classification, ParsedMessage
from .review import REVIEW_REASON_VALUES, ReviewReason, parse_review_reason
from .utils import (
    SUBJECT_PATTERN_VERSION_CURRENT,
    SUBJECT_PATTERN_VERSION_LEGACY,
    SUBJECT_PATTERN_VERSIONS,
    normalize_subject,
    normalize_subject_pattern,
    now_utc_iso,
    subject_patterns,
)

SCHEMA_VERSION = 4


FINAL_STATUSES = {
    "spam",
    "routine",
    "forwarded",
    "relevant",
    "review",
    "appointment-review",
    "error",
    "delivery-uncertain",
    "quarantine-reviewed",
}


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._pattern_gate_cache: tuple[tuple[int, int], dict[str, Any]] | None = None
        try:
            self._migrate()
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def pattern_activation_status(self) -> dict[str, Any]:
        """Return the cached v1/v2 safety gate for deterministic runtime matching."""
        state = self.connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS newest FROM feedback"
        ).fetchone()
        cache_key = (int(state["count"] or 0), int(state["newest"] or 0))
        if self._pattern_gate_cache and self._pattern_gate_cache[0] == cache_key:
            return dict(self._pattern_gate_cache[1])
        # Local import avoids a module cycle; the analyzer never calls this method.
        from .learning_quality import LearningQualityAnalyzer

        report = LearningQualityAnalyzer(self).report(limit=100000)
        gate = dict(report["evaluation"]["subject_pattern_versions"]["activation_gate"])
        self._pattern_gate_cache = (cache_key, gate)
        return dict(gate)

    def _migrate(self) -> None:
        current_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Datenbankschema {current_version} ist neuer als dieser Agent ({SCHEMA_VERSION})"
            )
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                stable_key TEXT PRIMARY KEY,
                message_id TEXT,
                mailbox_id TEXT,
                last_folder TEXT,
                sender_addr TEXT,
                sender_name TEXT,
                sender_domain TEXT,
                subject TEXT,
                subject_signature TEXT,
                received_at TEXT,
                category TEXT,
                confidence REAL,
                importance INTEGER,
                forward_flag INTEGER,
                reason TEXT,
                summary TEXT,
                expected_action TEXT,
                status TEXT,
                destination_folder TEXT,
                classification_json TEXT,
                review_reason TEXT,
                review_category TEXT,
                review_confidence REAL,
                review_source TEXT,
                review_threshold REAL,
                review_captured_at TEXT,
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                forwarded_at TEXT,
                last_error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_key TEXT NOT NULL,
                verdict TEXT NOT NULL,
                sender_addr TEXT,
                sender_domain TEXT,
                subject TEXT,
                subject_signature TEXT,
                subject_pattern TEXT,
                pattern_version INTEGER NOT NULL DEFAULT 1,
                source_folder TEXT,
                correction_folder TEXT,
                label TEXT,
                feature_json TEXT,
                original_category TEXT,
                original_confidence REAL,
                original_reason TEXT,
                original_source TEXT,
                original_rule_decision TEXT,
                original_classification_json TEXT,
                original_captured_at TEXT,
                original_snapshot_valid INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                metadata_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_sender ON feedback(sender_addr);
            CREATE INDEX IF NOT EXISTS idx_feedback_domain ON feedback(sender_domain);
            CREATE INDEX IF NOT EXISTS idx_feedback_subject_signature ON feedback(subject_signature);
            DROP INDEX IF EXISTS idx_feedback_unique;
            DELETE FROM feedback
            WHERE id NOT IN (
                SELECT MAX(id) FROM feedback GROUP BY stable_key
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_stable_key ON feedback(stable_key);

            CREATE TABLE IF NOT EXISTS events (
                event_key TEXT PRIMARY KEY,
                stable_key TEXT NOT NULL,
                uid TEXT,
                fingerprint TEXT,
                title TEXT,
                starts_at TEXT,
                ends_at TEXT,
                status TEXT,
                backend TEXT,
                path TEXT,
                error TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_stable_key ON events(stable_key);

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_key TEXT NOT NULL,
                attachment_hash TEXT NOT NULL UNIQUE,
                original_filename TEXT,
                nextcloud_path TEXT,
                size_bytes INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                invoice_date TEXT,
                received_date TEXT,
                invoice_number TEXT,
                supplier TEXT,
                category TEXT,
                gross_amount_cents INTEGER,
                net_amount_cents INTEGER,
                tax_amount_cents INTEGER,
                currency TEXT,
                due_date TEXT,
                extraction_status TEXT,
                extraction_confidence REAL,
                extraction_method TEXT,
                extraction_json TEXT,
                register_year INTEGER,
                register_updated_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_invoices_stable_key ON invoices(stable_key);

            CREATE TABLE IF NOT EXISTS invoice_reprocess_audit (
                operation_id TEXT PRIMARY KEY,
                invoice_id INTEGER NOT NULL,
                attachment_hash TEXT NOT NULL,
                preview_sha256 TEXT NOT NULL,
                old_state_sha256 TEXT NOT NULL,
                new_state_sha256 TEXT NOT NULL,
                proposal_sha256 TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                approval_label TEXT NOT NULL,
                source_status TEXT NOT NULL,
                proposed_status TEXT NOT NULL,
                old_register_year INTEGER,
                new_register_year INTEGER NOT NULL,
                register_years_json TEXT NOT NULL,
                completed_years_json TEXT NOT NULL,
                result_status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                claim_token TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(invoice_id) REFERENCES invoices(id),
                UNIQUE(attachment_hash, preview_sha256)
            );
            CREATE INDEX IF NOT EXISTS idx_invoice_reprocess_audit_invoice
                ON invoice_reprocess_audit(invoice_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_invoice_reprocess_audit_status
                ON invoice_reprocess_audit(result_status, updated_at);

            CREATE TABLE IF NOT EXISTS calendar_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                token_hint TEXT NOT NULL,
                event_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                stable_key TEXT NOT NULL,
                source_subject TEXT,
                event_json TEXT NOT NULL,
                ics_path TEXT,
                requester_email TEXT,
                status TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                responded_at TEXT,
                response_stable_key TEXT,
                backend TEXT,
                created_path TEXT,
                error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_calendar_approvals_event ON calendar_approvals(event_key, fingerprint);
            CREATE INDEX IF NOT EXISTS idx_calendar_approvals_status ON calendar_approvals(status);

            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_key TEXT,
                action TEXT NOT NULL,
                source_folder TEXT,
                destination_folder TEXT,
                ok INTEGER NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS digests (
                day TEXT PRIMARY KEY,
                sent_at TEXT,
                status TEXT NOT NULL,
                detail TEXT
            );
            """
        )
        feedback_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(feedback)").fetchall()
        }
        for column, declaration in (
            ("subject_pattern", "TEXT"),
            ("pattern_version", "INTEGER NOT NULL DEFAULT 1"),
            ("correction_folder", "TEXT"),
            ("label", "TEXT"),
            ("feature_json", "TEXT"),
            ("original_category", "TEXT"),
            ("original_confidence", "REAL"),
            ("original_reason", "TEXT"),
            ("original_source", "TEXT"),
            ("original_rule_decision", "TEXT"),
            ("original_classification_json", "TEXT"),
            ("original_captured_at", "TEXT"),
            ("original_snapshot_valid", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in feedback_columns:
                self.connection.execute(f"ALTER TABLE feedback ADD COLUMN {column} {declaration}")
        self.connection.execute(
            "UPDATE feedback SET subject_pattern = subject_signature "
            "WHERE COALESCE(subject_pattern, '') = ''"
        )
        # Existing rows were produced by the frozen v1 implementation. Never
        # recompute them with current code or silently change their semantics.
        self.connection.execute(
            "UPDATE feedback SET pattern_version = ? WHERE pattern_version IS NULL",
            (SUBJECT_PATTERN_VERSION_LEGACY,),
        )
        invalid_pattern_versions = [
            int(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT pattern_version FROM feedback"
            ).fetchall()
            if int(row[0]) not in SUBJECT_PATTERN_VERSIONS
        ]
        if invalid_pattern_versions:
            values = ", ".join(str(value) for value in sorted(invalid_pattern_versions))
            raise RuntimeError(f"Datenbank enthaelt unbekannte Betreffmusterversionen: {values}")
        self.connection.execute(
            "UPDATE feedback SET correction_folder = source_folder "
            "WHERE COALESCE(correction_folder, '') = ''"
        )
        message_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(messages)").fetchall()
        }
        for column, declaration in (
            ("review_reason", "TEXT"),
            ("review_category", "TEXT"),
            ("review_confidence", "REAL"),
            ("review_source", "TEXT"),
            ("review_threshold", "REAL"),
            ("review_captured_at", "TEXT"),
        ):
            if column not in message_columns:
                self.connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {declaration}")
        # Only facts that are unambiguous in the legacy row are backfilled.  A
        # generic review status may also have been caused by an invoice or another
        # safety gate, so it deliberately remains unknown.
        self.connection.execute(
            """
            UPDATE messages
            SET review_reason = CASE
                    WHEN status = 'appointment-review' THEN ?
                    WHEN status = 'review' AND category = 'uncertain' THEN ?
                    WHEN status = 'review' THEN ?
                    ELSE review_reason
                END,
                review_category = CASE
                    WHEN status IN ('review', 'appointment-review')
                    THEN COALESCE(review_category, category)
                    ELSE review_category
                END,
                review_confidence = CASE
                    WHEN status IN ('review', 'appointment-review')
                    THEN COALESCE(review_confidence, confidence)
                    ELSE review_confidence
                END
            WHERE COALESCE(review_reason, '') = ''
              AND status IN ('review', 'appointment-review')
            """,
            (
                ReviewReason.APPOINTMENT_REVIEW.value,
                ReviewReason.CLASSIFICATION_UNCERTAIN.value,
                ReviewReason.UNKNOWN_LEGACY.value,
            ),
        )
        invalid_review_reasons = [
            str(row[0])
            for row in self.connection.execute(
                """
                SELECT DISTINCT review_reason FROM messages
                WHERE COALESCE(review_reason, '') != ''
                """
            ).fetchall()
            if str(row[0]) not in REVIEW_REASON_VALUES
        ]
        if invalid_review_reasons:
            values = ", ".join(sorted(invalid_review_reasons))
            raise RuntimeError(f"Datenbank enthaelt unbekannte Review-Gruende: {values}")
        invoice_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(invoices)").fetchall()
        }
        for column, declaration in (
            ("invoice_date", "TEXT"),
            ("received_date", "TEXT"),
            ("invoice_number", "TEXT"),
            ("supplier", "TEXT"),
            ("category", "TEXT"),
            ("gross_amount_cents", "INTEGER"),
            ("net_amount_cents", "INTEGER"),
            ("tax_amount_cents", "INTEGER"),
            ("currency", "TEXT"),
            ("due_date", "TEXT"),
            ("extraction_status", "TEXT"),
            ("extraction_confidence", "REAL"),
            ("extraction_method", "TEXT"),
            ("extraction_json", "TEXT"),
            ("register_year", "INTEGER"),
            ("register_updated_at", "TEXT"),
        ):
            if column not in invoice_columns:
                self.connection.execute(f"ALTER TABLE invoices ADD COLUMN {column} {declaration}")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_register_year ON invoices(register_year, extraction_status)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_subject_pattern ON feedback(subject_pattern)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_pattern_version "
            "ON feedback(pattern_version, subject_pattern)"
        )
        self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.connection.commit()

    def get_message(self, stable_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM messages WHERE stable_key = ?", (stable_key,)
        ).fetchone()

    def is_final(self, stable_key: str) -> bool:
        row = self.get_message(stable_key)
        return bool(row and row["status"] in FINAL_STATUSES)

    def upsert_message(self, message: ParsedMessage, classification: Classification | None = None, status: str = "seen") -> None:
        timestamp = now_utc_iso()
        classification_json = json.dumps(classification.to_dict(), ensure_ascii=False) if classification else None
        values = {
            "stable_key": message.stable_key,
            "message_id": message.message_id,
            "mailbox_id": message.mailbox_id,
            "last_folder": message.source_folder,
            "sender_addr": message.sender_addr,
            "sender_name": message.sender_name,
            "sender_domain": message.sender_domain,
            "subject": message.subject,
            "subject_signature": normalize_subject(message.subject),
            "received_at": message.received_at or message.date,
            "category": classification.category if classification else None,
            "confidence": classification.confidence if classification else None,
            "importance": classification.importance if classification else None,
            "forward_flag": int(classification.forward) if classification else None,
            "reason": classification.reason if classification else None,
            "summary": classification.summary if classification else None,
            "expected_action": classification.expected_action if classification else None,
            "status": status,
            "classification_json": classification_json,
            "first_seen_at": timestamp,
            "updated_at": timestamp,
        }
        self.connection.execute(
            """
            INSERT INTO messages (
                stable_key, message_id, mailbox_id, last_folder, sender_addr, sender_name,
                sender_domain, subject, subject_signature, received_at, category, confidence,
                importance, forward_flag, reason, summary, expected_action, status,
                classification_json, first_seen_at, updated_at
            ) VALUES (
                :stable_key, :message_id, :mailbox_id, :last_folder, :sender_addr, :sender_name,
                :sender_domain, :subject, :subject_signature, :received_at, :category, :confidence,
                :importance, :forward_flag, :reason, :summary, :expected_action, :status,
                :classification_json, :first_seen_at, :updated_at
            )
            ON CONFLICT(stable_key) DO UPDATE SET
                message_id=excluded.message_id,
                mailbox_id=excluded.mailbox_id,
                last_folder=excluded.last_folder,
                sender_addr=excluded.sender_addr,
                sender_name=excluded.sender_name,
                sender_domain=excluded.sender_domain,
                subject=excluded.subject,
                subject_signature=excluded.subject_signature,
                received_at=excluded.received_at,
                category=COALESCE(excluded.category, messages.category),
                confidence=COALESCE(excluded.confidence, messages.confidence),
                importance=COALESCE(excluded.importance, messages.importance),
                forward_flag=COALESCE(excluded.forward_flag, messages.forward_flag),
                reason=COALESCE(excluded.reason, messages.reason),
                summary=COALESCE(excluded.summary, messages.summary),
                expected_action=COALESCE(excluded.expected_action, messages.expected_action),
                status=excluded.status,
                classification_json=COALESCE(excluded.classification_json, messages.classification_json),
                updated_at=excluded.updated_at
            """,
            values,
        )
        self.connection.commit()

    def update_status(
        self,
        stable_key: str,
        status: str,
        *,
        destination: str = "",
        error: str = "",
        forwarded: bool = False,
        increment_retry: bool = False,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, now_utc_iso()]
        if destination:
            fields.append("destination_folder = ?")
            values.append(destination)
        if error:
            fields.append("last_error = ?")
            values.append(error[:4000])
        else:
            fields.append("last_error = NULL")
        if forwarded:
            fields.append("forwarded_at = COALESCE(forwarded_at, ?)")
            values.append(now_utc_iso())
        if increment_retry:
            fields.append("retry_count = retry_count + 1")
        values.append(stable_key)
        self.connection.execute(f"UPDATE messages SET {', '.join(fields)} WHERE stable_key = ?", values)
        self.connection.commit()

    def record_review(
        self,
        stable_key: str,
        reason: str | ReviewReason,
        classification: Classification,
        *,
        threshold: float | None = None,
    ) -> None:
        """Capture the first technical review decision without mail content."""

        parsed_reason = parse_review_reason(reason)
        if classification.category not in {"spam", "routine", "relevant", "appointment", "uncertain"}:
            raise ValueError(f"Ungueltige Review-Kategorie: {classification.category}")
        confidence = float(classification.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Review-Konfidenz muss zwischen 0 und 1 liegen")
        if threshold is not None and not 0.0 <= float(threshold) <= 1.0:
            raise ValueError("Review-Schwelle muss zwischen 0 und 1 liegen")
        source = str(classification.source or "").strip()[:160]
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE messages
                SET review_reason = COALESCE(review_reason, ?),
                    review_category = COALESCE(review_category, ?),
                    review_confidence = COALESCE(review_confidence, ?),
                    review_source = COALESCE(review_source, ?),
                    review_threshold = COALESCE(review_threshold, ?),
                    review_captured_at = COALESCE(review_captured_at, ?)
                WHERE stable_key = ?
                """,
                (
                    parsed_reason.value,
                    classification.category,
                    confidence,
                    source,
                    float(threshold) if threshold is not None else None,
                    now_utc_iso(),
                    stable_key,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Mailzustand nicht gefunden: {stable_key}")

    @staticmethod
    def _review_confidence_band(value: float | int | None) -> str:
        if value is None:
            return "unknown"
        confidence = float(value)
        if confidence < 0.70:
            return "0.00-0.69"
        if confidence < 0.90:
            return "0.70-0.89"
        if confidence < 0.95:
            return "0.90-0.94"
        return "0.95-1.00"

    def review_status(self, *, days: int = 7) -> dict[str, Any]:
        """Return content-free aggregates for typed review decisions."""

        bounded_days = max(1, min(int(days), 3650))
        cutoff = (datetime.now(UTC) - timedelta(days=bounded_days)).isoformat(timespec="seconds")
        rows = self.connection.execute(
            """
            SELECT review_reason, review_source, review_confidence,
                   review_category, review_threshold
            FROM messages
            WHERE review_reason IS NOT NULL
              AND review_reason != ''
              AND COALESCE(review_captured_at, updated_at) >= ?
            """,
            (cutoff,),
        ).fetchall()
        reasons: dict[str, int] = {}
        sources: dict[str, int] = {}
        confidence_bands: dict[str, int] = {}
        categories: dict[str, int] = {}
        missing_snapshot = 0
        for row in rows:
            reason = parse_review_reason(str(row["review_reason"])).value
            reasons[reason] = reasons.get(reason, 0) + 1
            source = str(row["review_source"] or "unknown")
            sources[source] = sources.get(source, 0) + 1
            category = str(row["review_category"] or "unknown")
            categories[category] = categories.get(category, 0) + 1
            band = self._review_confidence_band(row["review_confidence"])
            confidence_bands[band] = confidence_bands.get(band, 0) + 1
            if row["review_confidence"] is None or not row["review_source"]:
                missing_snapshot += 1
        return {
            "ok": True,
            "read_only": True,
            "days": bounded_days,
            "cutoff": cutoff,
            "count": len(rows),
            "reasons": dict(sorted(reasons.items())),
            "sources": dict(sorted(sources.items())),
            "categories": dict(sorted(categories.items())),
            "confidence_bands": dict(sorted(confidence_bands.items())),
            "data_quality": {
                "typed_reasons": len(rows),
                "unknown_legacy": reasons.get(ReviewReason.UNKNOWN_LEGACY.value, 0),
                "missing_original_snapshot": missing_snapshot,
            },
            "complete": True,
            "folder_errors": [],
            "results_may_be_truncated": False,
        }

    def review_items(self, reason: str | ReviewReason, *, limit: int = 50) -> dict[str, Any]:
        """List bounded review metadata without bodies, attachments or free-text reasons."""

        parsed_reason = parse_review_reason(reason)
        bounded_limit = max(1, min(int(limit), 200))
        total = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM messages WHERE review_reason = ?",
                (parsed_reason.value,),
            ).fetchone()[0]
        )
        rows = self.connection.execute(
            """
            SELECT mailbox_id, last_folder, subject, sender_name, sender_addr,
                   received_at, review_reason, review_category, review_confidence,
                   review_source, review_threshold, review_captured_at
            FROM messages
            WHERE review_reason = ?
            ORDER BY COALESCE(review_captured_at, updated_at) DESC, stable_key DESC
            LIMIT ?
            """,
            (parsed_reason.value, bounded_limit),
        ).fetchall()
        return {
            "ok": True,
            "read_only": True,
            "reason": parsed_reason.value,
            "count": len(rows),
            "total": total,
            "messages": [dict(row) for row in rows],
            "complete": True,
            "folder_errors": [],
            "results_may_be_truncated": total > len(rows),
        }

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _original_feedback_snapshot(self, stable_key: str) -> dict[str, Any]:
        existing_feedback = self.connection.execute(
            """
            SELECT original_category, original_confidence, original_reason, original_source,
                   original_rule_decision, original_classification_json, original_captured_at,
                   COALESCE(original_snapshot_valid, 0) AS original_snapshot_valid
            FROM feedback WHERE stable_key = ?
            """,
            (stable_key,),
        ).fetchone()
        if existing_feedback and int(existing_feedback["original_snapshot_valid"] or 0) == 1:
            return dict(existing_feedback)

        message_row = self.get_message(stable_key)
        if not message_row:
            return {"original_snapshot_valid": 0}
        classification_data = self._json_dict(message_row["classification_json"])
        source = str(classification_data.get("source") or "").strip()
        category = str(classification_data.get("category") or message_row["category"] or "").strip()
        # A previous user correction is not an immutable pre-correction decision.
        # Legacy rows where that distinction cannot be proven remain unavailable.
        if not category or source.casefold().startswith("feedback"):
            return {"original_snapshot_valid": 0}
        confidence_value = classification_data.get("confidence", message_row["confidence"])
        try:
            confidence = float(confidence_value) if confidence_value is not None else None
        except (TypeError, ValueError):
            confidence = None
        reason = str(classification_data.get("reason") or message_row["reason"] or "")[:1000]
        rule_decision = ""
        if "rule" in source.casefold():
            rule_decision = json.dumps(
                {"category": category, "source": source, "reason": reason},
                ensure_ascii=False,
            )
        return {
            "original_category": category,
            "original_confidence": confidence,
            "original_reason": reason,
            "original_source": source,
            "original_rule_decision": rule_decision,
            "original_classification_json": json.dumps(classification_data, ensure_ascii=False) if classification_data else "",
            "original_captured_at": now_utc_iso(),
            "original_snapshot_valid": 1,
        }

    def record_feedback(
        self,
        message: ParsedMessage,
        verdict: str,
        source_folder: str,
        metadata: dict[str, Any] | None = None,
        *,
        label: str = "",
    ) -> None:
        # A correction is the current truth for this exact message. The original
        # automated decision is captured once and then preserved across reversals.
        pattern_version = SUBJECT_PATTERN_VERSION_CURRENT
        subject_pattern = normalize_subject_pattern(message.subject, version=pattern_version)
        features = message_feature_metadata(message)
        original = self._original_feedback_snapshot(message.stable_key)
        with self.connection:
            self.connection.execute("DELETE FROM feedback WHERE stable_key = ?", (message.stable_key,))
            self.connection.execute(
                """
                INSERT INTO feedback (
                    stable_key, verdict, sender_addr, sender_domain, subject,
                    subject_signature, subject_pattern, pattern_version,
                    source_folder, correction_folder,
                    label, feature_json, original_category, original_confidence,
                    original_reason, original_source, original_rule_decision,
                    original_classification_json, original_captured_at, original_snapshot_valid,
                    created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.stable_key,
                    verdict,
                    (message.sender_addr or "").strip().lower(),
                    (message.sender_domain or "").strip().lower(),
                    message.subject,
                    normalize_subject(message.subject),
                    subject_pattern,
                    pattern_version,
                    source_folder,
                    source_folder,
                    (label or "").strip().casefold()[:80],
                    json.dumps(features, ensure_ascii=False),
                    original.get("original_category"),
                    original.get("original_confidence"),
                    original.get("original_reason"),
                    original.get("original_source"),
                    original.get("original_rule_decision"),
                    original.get("original_classification_json"),
                    original.get("original_captured_at"),
                    int(original.get("original_snapshot_valid") or 0),
                    now_utc_iso(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def sender_feedback_profile(self, sender_addr: str) -> dict[str, Any]:
        sender = (sender_addr or "").strip().lower()
        if not sender:
            return {"sender": "", "counts": {}, "category_counts": {}, "mixed": False, "total": 0}
        rows = self.connection.execute(
            "SELECT verdict, COUNT(*) AS count FROM feedback "
            "WHERE lower(sender_addr) = ? GROUP BY verdict ORDER BY verdict",
            (sender,),
        ).fetchall()
        counts = {str(row["verdict"]): int(row["count"]) for row in rows}
        category_counts = {
            verdict: count for verdict, count in counts.items()
            if verdict in {"spam", "routine", "relevant"}
        }
        return {
            "sender": sender,
            "counts": counts,
            "category_counts": category_counts,
            "mixed": len(category_counts) > 1,
            "total": sum(counts.values()),
        }

    @staticmethod
    def _feedback_origin(source_folder: str, metadata: dict[str, Any] | None = None) -> str:
        explicit = str((metadata or {}).get("origin") or "").strip().casefold()
        if explicit:
            return explicit
        folded = (source_folder or "").strip().casefold()
        if folded == "inbox-restore":
            return "inbox-restore"
        if "korrektur-kein-spam" in folded:
            return "not-spam-correction-folder"
        if folded:
            return "correction-folder"
        return "unknown"

    def pattern_feedback_decision(self, message: ParsedMessage) -> dict[str, Any]:
        sender = (message.sender_addr or "").strip().lower()
        patterns = subject_patterns(message.subject)
        pattern = patterns[SUBJECT_PATTERN_VERSION_CURRENT]
        activation = self.pattern_activation_status()
        current_enabled = int(bool(activation.get("allowed")))
        if not sender or not pattern:
            return {
                "verdict": None,
                "count": 0,
                "conflict": False,
                "pattern": pattern,
                "pattern_version": SUBJECT_PATTERN_VERSION_CURRENT,
                "pattern_activation": activation,
                "prevent_spam": False,
                "counts": {},
                "not_spam": {"count": 0, "origin": "", "source_folder": ""},
            }
        rows = self.connection.execute(
            """
            SELECT verdict, COUNT(*) AS count, MAX(id) AS newest
            FROM feedback
            WHERE lower(sender_addr) = ?
              AND (? = 1 OR pattern_version != ?)
              AND ((pattern_version = ? AND COALESCE(NULLIF(subject_pattern, ''), subject_signature) = ?)
                OR (pattern_version = ? AND COALESCE(NULLIF(subject_pattern, ''), subject_signature) = ?))
              AND verdict IN ('spam', 'routine', 'relevant', 'not_spam')
            GROUP BY verdict
            ORDER BY newest DESC
            """,
            (
                sender,
                current_enabled,
                SUBJECT_PATTERN_VERSION_CURRENT,
                SUBJECT_PATTERN_VERSION_LEGACY,
                patterns[SUBJECT_PATTERN_VERSION_LEGACY],
                SUBJECT_PATTERN_VERSION_CURRENT,
                patterns[SUBJECT_PATTERN_VERSION_CURRENT],
            ),
        ).fetchall()
        counts = {str(row["verdict"]): int(row["count"]) for row in rows}
        not_spam_count = int(counts.get("not_spam", 0))
        not_spam_row = None
        if not_spam_count:
            not_spam_row = self.connection.execute(
                """
                SELECT id, source_folder, correction_folder, metadata_json, created_at
                FROM feedback
                WHERE lower(sender_addr) = ?
                  AND (? = 1 OR pattern_version != ?)
                  AND ((pattern_version = ? AND COALESCE(NULLIF(subject_pattern, ''), subject_signature) = ?)
                    OR (pattern_version = ? AND COALESCE(NULLIF(subject_pattern, ''), subject_signature) = ?))
                  AND verdict = 'not_spam'
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    sender,
                    current_enabled,
                    SUBJECT_PATTERN_VERSION_CURRENT,
                    SUBJECT_PATTERN_VERSION_LEGACY,
                    patterns[SUBJECT_PATTERN_VERSION_LEGACY],
                    SUBJECT_PATTERN_VERSION_CURRENT,
                    patterns[SUBJECT_PATTERN_VERSION_CURRENT],
                ),
            ).fetchone()
        metadata = self._json_dict(not_spam_row["metadata_json"]) if not_spam_row else {}
        not_spam = {
            "count": not_spam_count,
            "origin": self._feedback_origin(str(not_spam_row["source_folder"] or ""), metadata) if not_spam_row else "",
            "source_folder": str(not_spam_row["source_folder"] or "") if not_spam_row else "",
            "correction_folder": str(not_spam_row["correction_folder"] or "") if not_spam_row else "",
            "feedback_id": int(not_spam_row["id"]) if not_spam_row else None,
            "created_at": str(not_spam_row["created_at"] or "") if not_spam_row else "",
        }
        category_counts = {k: v for k, v in counts.items() if k in {"spam", "routine", "relevant"}}
        result = {
            "verdict": None,
            "count": 0,
            "conflict": False,
            "pattern": pattern,
            "pattern_version": SUBJECT_PATTERN_VERSION_CURRENT,
            "pattern_activation": activation,
            "counts": counts,
            "prevent_spam": bool(not_spam_count),
            "not_spam": not_spam,
            "countered_verdict": "",
        }
        if len(category_counts) > 1:
            result["count"] = sum(category_counts.values())
            result["conflict"] = True
            return result
        if len(category_counts) == 1:
            verdict = next(iter(category_counts))
            # not_spam is deliberately a negative assertion only: it blocks spam,
            # but must not turn a known routine or relevant pattern into another class.
            if verdict == "spam" and not_spam_count:
                result["verdict"] = "not_spam"
                result["count"] = not_spam_count
                result["countered_verdict"] = "spam"
                return result
            result["verdict"] = verdict
            result["count"] = category_counts[verdict]
            return result
        if not_spam_count:
            result["verdict"] = "not_spam"
            result["count"] = not_spam_count
        return result

    def find_feedback(self, message: ParsedMessage, limit: int = 12) -> list[dict[str, Any]]:
        patterns = subject_patterns(message.subject)
        activation = self.pattern_activation_status()
        current_enabled = int(bool(activation.get("allowed")))
        sender = (message.sender_addr or "").strip().lower()
        domain = (message.sender_domain or "").strip().lower()
        current_features = message_feature_metadata(message)
        rows = self.connection.execute(
            """
            SELECT id, verdict, sender_addr, sender_domain, subject, subject_signature,
                   COALESCE(NULLIF(subject_pattern, ''), subject_signature) AS subject_pattern,
                   pattern_version,
                   source_folder, correction_folder, label, feature_json,
                   original_category, original_confidence, original_source, original_captured_at,
                   COALESCE(original_snapshot_valid, 0) AS original_snapshot_valid, created_at
            FROM feedback
            WHERE (? = 1 OR pattern_version != ?)
              AND ((sender_addr != '' AND lower(sender_addr) = ?)
               OR (sender_domain != '' AND lower(sender_domain) = ?)
               OR (COALESCE(NULLIF(subject_pattern, ''), subject_signature) != '' AND (
                    (pattern_version = ? AND COALESCE(NULLIF(subject_pattern, ''), subject_signature) = ?)
                 OR (pattern_version = ? AND COALESCE(NULLIF(subject_pattern, ''), subject_signature) = ?))))
            ORDER BY id DESC
            LIMIT 250
            """,
            (
                current_enabled,
                SUBJECT_PATTERN_VERSION_CURRENT,
                sender,
                domain,
                SUBJECT_PATTERN_VERSION_LEGACY,
                patterns[SUBJECT_PATTERN_VERSION_LEGACY],
                SUBJECT_PATTERN_VERSION_CURRENT,
                patterns[SUBJECT_PATTERN_VERSION_CURRENT],
            ),
        ).fetchall()
        profile = self.sender_feedback_profile(sender)
        ranked: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item_sender = str(item.get("sender_addr") or "").lower()
            item_domain = str(item.get("sender_domain") or "").lower()
            item_pattern = str(item.get("subject_pattern") or "")
            item_pattern_version = int(
                item.get("pattern_version") or SUBJECT_PATTERN_VERSION_LEGACY
            )
            score = 0
            reasons: list[str] = []
            if sender and item_sender == sender:
                score += 20
                reasons.append("same-sender")
            if domain and item_domain == domain:
                score += 10
                reasons.append("same-domain")
            if item_pattern and item_pattern == patterns.get(item_pattern_version, ""):
                score += 70
                reasons.append("same-subject-pattern")
            old_features = self._json_dict(item.get("feature_json"))
            if old_features and old_features.get("attachment_types") == current_features.get("attachment_types"):
                score += 8
                reasons.append("same-attachment-types")
            if old_features and bool(old_features.get("calendar_invite")) == bool(current_features.get("calendar_invite")):
                score += 4
            item["match_score"] = score
            item["match_reasons"] = reasons
            item["features"] = old_features
            item["sender_mixed"] = bool(profile.get("mixed")) and item_sender == sender
            ranked.append(item)
        ranked.sort(key=lambda item: (int(item.get("match_score") or 0), int(item.get("id") or 0)), reverse=True)
        return ranked[: max(1, min(int(limit), 100))]

    def feedback_count_for_sender(self, sender_addr: str, verdict: str) -> int:
        sender = (sender_addr or "").strip().lower()
        if not sender:
            return 0
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM feedback WHERE lower(sender_addr) = ? AND verdict = ?",
            (sender, verdict),
        ).fetchone()
        return int(row["count"] if row else 0)

    def exact_feedback_verdict(self, message: ParsedMessage) -> str | None:
        decision = self.pattern_feedback_decision(message)
        return str(decision["verdict"]) if decision.get("verdict") else None

    def feedback_summary(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT verdict, COUNT(*) AS count FROM feedback GROUP BY verdict ORDER BY verdict"
        ).fetchall()
        return {str(row["verdict"]): int(row["count"]) for row in rows}

    def not_spam_feedback_summary(self) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT source_folder, metadata_json, COUNT(*) AS count
            FROM feedback
            WHERE verdict = 'not_spam'
            GROUP BY source_folder, metadata_json
            """
        ).fetchall()
        origins: dict[str, int] = {}
        total = 0
        for row in rows:
            count = int(row["count"] or 0)
            metadata = self._json_dict(row["metadata_json"])
            origin = self._feedback_origin(str(row["source_folder"] or ""), metadata)
            origins[origin] = origins.get(origin, 0) + count
            total += count
        return {"total": total, "origins": dict(sorted(origins.items()))}

    def list_not_spam_feedback(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 10000))
        rows = self.connection.execute(
            """
            SELECT id, source_folder, correction_folder, label, metadata_json,
                   original_category, original_source,
                   COALESCE(original_snapshot_valid, 0) AS original_snapshot_valid,
                   created_at
            FROM feedback
            WHERE verdict = 'not_spam'
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            metadata = self._json_dict(row["metadata_json"])
            result.append({
                "feedback_id": int(row["id"]),
                "origin": self._feedback_origin(str(row["source_folder"] or ""), metadata),
                "source_folder": str(row["source_folder"] or ""),
                "correction_folder": str(row["correction_folder"] or ""),
                "label": str(row["label"] or ""),
                "created_at": str(row["created_at"] or ""),
                "original_category": str(row["original_category"] or ""),
                "original_source": str(row["original_source"] or ""),
                "original_snapshot_valid": bool(row["original_snapshot_valid"]),
                "previous_status": str(metadata.get("previous_status") or ""),
            })
        return result

    def list_feedback(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 10000))
        rows = self.connection.execute(
            """
            SELECT id, stable_key, verdict, sender_addr, sender_domain, subject,
                   subject_signature, COALESCE(NULLIF(subject_pattern, ''), subject_signature) AS subject_pattern,
                   pattern_version,
                   source_folder, correction_folder, label, feature_json,
                   original_category, original_confidence, original_source, original_captured_at,
                   COALESCE(original_snapshot_valid, 0) AS original_snapshot_valid, created_at
            FROM feedback
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["features"] = self._json_dict(item.pop("feature_json", ""))
            result.append(item)
        return result

    def mixed_senders(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 10000))
        rows = self.connection.execute(
            """
            SELECT lower(sender_addr) AS sender_addr, COUNT(*) AS total,
                   COUNT(DISTINCT CASE WHEN verdict IN ('spam','routine','relevant') THEN verdict END) AS verdicts
            FROM feedback
            WHERE COALESCE(sender_addr, '') != ''
            GROUP BY lower(sender_addr)
            HAVING verdicts > 1
            ORDER BY total DESC, sender_addr
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        result = []
        for row in rows:
            profile = self.sender_feedback_profile(str(row["sender_addr"]))
            result.append(profile)
        return result

    @staticmethod
    def _conflict_id(sender: str, pattern: str) -> str:
        import hashlib
        payload = (sender.casefold() + "\0" + pattern.casefold()).encode("utf-8", errors="replace")
        return hashlib.sha256(payload).hexdigest()[:16]

    def pattern_conflicts(self, limit: int = 100, conflict_id: str = "") -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 10000))
        rows = self.connection.execute(
            """
            SELECT lower(sender_addr) AS sender_addr,
                   COALESCE(NULLIF(subject_pattern, ''), subject_signature) AS subject_pattern,
                   pattern_version,
                   COUNT(*) AS total, COUNT(DISTINCT verdict) AS verdict_count,
                   GROUP_CONCAT(DISTINCT verdict) AS verdicts,
                   GROUP_CONCAT(id) AS feedback_ids
            FROM feedback
            WHERE verdict IN ('spam','routine','relevant')
              AND COALESCE(sender_addr, '') != ''
              AND COALESCE(NULLIF(subject_pattern, ''), subject_signature) != ''
            GROUP BY lower(sender_addr), pattern_version,
                     COALESCE(NULLIF(subject_pattern, ''), subject_signature)
            HAVING verdict_count > 1
            ORDER BY total DESC, sender_addr, subject_pattern
            LIMIT ?
            """,
            (10000 if conflict_id else safe_limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        wanted = (conflict_id or "").strip().casefold()
        for row in rows:
            item = dict(row)
            item["conflict_id"] = self._conflict_id(
                str(item.get("sender_addr") or ""),
                f"v{int(item.get('pattern_version') or SUBJECT_PATTERN_VERSION_LEGACY)}:"
                + str(item.get("subject_pattern") or ""),
            )
            item["feedback_ids"] = [
                int(value) for value in str(item.get("feedback_ids") or "").split(",") if value.isdigit()
            ]
            if wanted and item["conflict_id"].casefold() != wanted:
                continue
            result.append(item)
            if len(result) >= safe_limit:
                break
        return result

    def delete_feedback_for_sender(self, sender_addr: str) -> int:
        sender = (sender_addr or "").strip().lower()
        if not sender:
            return 0
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM feedback WHERE lower(sender_addr) = ?",
                (sender,),
            )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def delete_feedback_by_id(self, feedback_id: int) -> int:
        if int(feedback_id) <= 0:
            return 0
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM feedback WHERE id = ?",
                (int(feedback_id),),
            )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def record_action(
        self,
        stable_key: str,
        action: str,
        source_folder: str,
        destination_folder: str,
        ok: bool,
        detail: str = "",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO actions (stable_key, action, source_folder, destination_folder, ok, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (stable_key, action, source_folder, destination_folder, int(ok), detail[:4000], now_utc_iso()),
        )
        self.connection.commit()

    def get_event(self, event_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM events WHERE event_key = ?", (event_key,)
        ).fetchone()

    def record_event(
        self,
        event_key: str,
        stable_key: str,
        *,
        uid: str,
        fingerprint: str,
        title: str,
        starts_at: str,
        ends_at: str,
        status: str,
        backend: str,
        path: str = "",
        error: str = "",
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO events (
                event_key, stable_key, uid, fingerprint, title, starts_at, ends_at,
                status, backend, path, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_key,
                stable_key,
                uid,
                fingerprint,
                title,
                starts_at,
                ends_at,
                status,
                backend,
                path,
                error[:4000],
                now_utc_iso(),
            ),
        )
        self.connection.commit()

    def get_invoice(self, attachment_hash: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM invoices WHERE attachment_hash = ?", (attachment_hash,)
        ).fetchone()

    def record_invoice(
        self,
        *,
        stable_key: str,
        attachment_hash: str,
        original_filename: str,
        nextcloud_path: str,
        size_bytes: int,
        status: str,
        error: str = "",
        invoice_date: str = "",
        received_date: str = "",
        invoice_number: str = "",
        supplier: str = "",
        category: str = "",
        gross_amount_cents: int | None = None,
        net_amount_cents: int | None = None,
        tax_amount_cents: int | None = None,
        currency: str = "EUR",
        due_date: str = "",
        extraction_status: str = "",
        extraction_confidence: float = 0.0,
        extraction_method: str = "",
        extraction_json: str = "",
        register_year: int | None = None,
    ) -> None:
        timestamp = now_utc_iso()
        self.connection.execute(
            """
            INSERT INTO invoices (
                stable_key, attachment_hash, original_filename, nextcloud_path,
                size_bytes, status, error, invoice_date, received_date, invoice_number,
                supplier, category, gross_amount_cents, net_amount_cents, tax_amount_cents,
                currency, due_date, extraction_status, extraction_confidence, extraction_method,
                extraction_json, register_year, register_updated_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(attachment_hash) DO UPDATE SET
                stable_key=excluded.stable_key,
                original_filename=excluded.original_filename,
                nextcloud_path=excluded.nextcloud_path,
                size_bytes=excluded.size_bytes,
                status=excluded.status,
                error=excluded.error,
                invoice_date=excluded.invoice_date,
                received_date=excluded.received_date,
                invoice_number=excluded.invoice_number,
                supplier=excluded.supplier,
                category=excluded.category,
                gross_amount_cents=excluded.gross_amount_cents,
                net_amount_cents=excluded.net_amount_cents,
                tax_amount_cents=excluded.tax_amount_cents,
                currency=excluded.currency,
                due_date=excluded.due_date,
                extraction_status=excluded.extraction_status,
                extraction_confidence=excluded.extraction_confidence,
                extraction_method=excluded.extraction_method,
                extraction_json=excluded.extraction_json,
                register_year=excluded.register_year,
                register_updated_at=excluded.register_updated_at,
                updated_at=excluded.updated_at
            """,
            (
                stable_key, attachment_hash, original_filename, nextcloud_path,
                int(size_bytes), status, error[:4000], invoice_date, received_date, invoice_number,
                supplier, category, gross_amount_cents, net_amount_cents, tax_amount_cents,
                currency, due_date, extraction_status, float(extraction_confidence), extraction_method,
                extraction_json[:20000], register_year, timestamp, timestamp, timestamp,
            ),
        )
        self.connection.commit()


    def list_invoice_register_rows(self, *, year: int, limit: int = 100000) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT i.*, substr(COALESCE(m.received_at, ''), 1, 10) AS message_received_date
            FROM invoices i
            LEFT JOIN messages m ON m.stable_key = i.stable_key
            WHERE i.register_year = ? AND i.status IN ('uploaded', 'duplicate')
            ORDER BY COALESCE(i.invoice_date, i.received_date, i.created_at), i.id
            LIMIT ?
            """,
            (int(year), max(1, int(limit))),
        ).fetchall()

    def invoice_register_years(self) -> list[int]:
        rows = self.connection.execute(
            "SELECT DISTINCT register_year FROM invoices WHERE register_year IS NOT NULL ORDER BY register_year"
        ).fetchall()
        return [int(row[0]) for row in rows]

    def count_invoice_register_rows(self, year: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM invoices WHERE register_year = ? AND status IN ('uploaded', 'duplicate')",
            (int(year),),
        ).fetchone()
        return int(row[0] if row else 0)

    def list_invoices(self, *, year: int | None = None, extraction_status: str = '', limit: int = 100) -> list[sqlite3.Row]:
        clauses = ["1=1"]
        values: list[object] = []
        if year is not None:
            clauses.append("register_year = ?")
            values.append(int(year))
        if extraction_status:
            clauses.append("extraction_status = ?")
            values.append(extraction_status)
        values.append(max(1, min(int(limit), 5000)))
        return self.connection.execute(
            "SELECT * FROM invoices WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT ?",
            values,
        ).fetchall()

    def list_invoice_backfill_candidates(self, *, limit: int = 5000) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT i.*, m.message_id, m.mailbox_id, m.last_folder, m.sender_addr,
                   m.sender_name, m.subject, m.received_at
            FROM invoices i
            LEFT JOIN messages m ON m.stable_key = i.stable_key
            WHERE i.status IN ('uploaded', 'duplicate')
              AND COALESCE(i.extraction_status, '') = ''
              AND COALESCE(i.nextcloud_path, '') != ''
            ORDER BY i.created_at, i.id
            LIMIT ?
            """,
            (max(1, min(int(limit), 20000)),),
        ).fetchall()

    def update_invoice_extraction(
        self,
        attachment_hash: str,
        *,
        invoice_date: str = "",
        received_date: str = "",
        invoice_number: str = "",
        supplier: str = "",
        category: str = "",
        gross_amount_cents: int | None = None,
        net_amount_cents: int | None = None,
        tax_amount_cents: int | None = None,
        currency: str = "EUR",
        due_date: str = "",
        extraction_status: str = "review",
        extraction_confidence: float = 0.0,
        extraction_method: str = "",
        extraction_json: str = "",
        register_year: int | None = None,
    ) -> None:
        if self.get_invoice(attachment_hash) is None:
            raise KeyError(f"Unbekannte Rechnungsdatei: {attachment_hash}")
        timestamp = now_utc_iso()
        self.connection.execute(
            """
            UPDATE invoices SET invoice_date=?, received_date=?, invoice_number=?, supplier=?, category=?,
                gross_amount_cents=?, net_amount_cents=?, tax_amount_cents=?, currency=?, due_date=?,
                extraction_status=?, extraction_confidence=?, extraction_method=?, extraction_json=?,
                register_year=?, register_updated_at=?, updated_at=?
            WHERE attachment_hash=?
            """,
            (
                invoice_date, received_date, invoice_number, supplier, category,
                gross_amount_cents, net_amount_cents, tax_amount_cents, currency, due_date,
                extraction_status, float(extraction_confidence), extraction_method,
                extraction_json[:20000], register_year, timestamp, timestamp, attachment_hash,
            ),
        )
        self.connection.commit()

    def correct_invoice_metadata(
        self,
        attachment_hash: str,
        *,
        invoice_date: str,
        invoice_number: str,
        supplier: str,
        category: str,
        gross_amount_cents: int | None,
        net_amount_cents: int | None,
        tax_amount_cents: int | None,
        currency: str,
        due_date: str,
    ) -> tuple[int | None, int]:
        current = self.get_invoice(attachment_hash)
        if current is None:
            raise KeyError(f"Unbekannte Rechnungsdatei: {attachment_hash}")
        old_year = int(current["register_year"]) if current["register_year"] is not None else None
        new_year = int(invoice_date[:4]) if invoice_date else int(current["register_year"] or datetime.now().year)
        timestamp = now_utc_iso()
        self.connection.execute(
            """
            UPDATE invoices SET invoice_date=?, invoice_number=?, supplier=?, category=?,
                gross_amount_cents=?, net_amount_cents=?, tax_amount_cents=?, currency=?, due_date=?,
                extraction_status='confirmed-manual', extraction_confidence=1.0,
                extraction_method='manual-correction', register_year=?, register_updated_at=?, updated_at=?
            WHERE attachment_hash=?
            """,
            (invoice_date, invoice_number, supplier, category, gross_amount_cents, net_amount_cents,
             tax_amount_cents, currency, due_date, new_year, timestamp, timestamp, attachment_hash),
        )
        self.connection.commit()
        return old_year, new_year

    def create_calendar_approval(
        self,
        *,
        token_hash: str,
        token_hint: str,
        event_key: str,
        fingerprint: str,
        stable_key: str,
        source_subject: str,
        event_json: str,
        ics_path: str,
        requester_email: str,
        status: str,
        expires_at: str,
    ) -> int:
        timestamp = now_utc_iso()
        cursor = self.connection.execute(
            """
            INSERT INTO calendar_approvals (
                token_hash, token_hint, event_key, fingerprint, stable_key, source_subject,
                event_json, ics_path, requester_email, status, expires_at, requested_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_hash, token_hint, event_key, fingerprint, stable_key, source_subject,
                event_json, ics_path, requester_email, status, expires_at, timestamp, timestamp,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_calendar_approval(self, token_hash: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM calendar_approvals WHERE token_hash = ?", (token_hash,)
        ).fetchone()

    def pending_calendar_approval(self, event_key: str, fingerprint: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM calendar_approvals
            WHERE event_key = ? AND fingerprint = ?
              AND status IN ('sending', 'pending', 'approved-creating', 'create-error')
            ORDER BY id DESC LIMIT 1
            """,
            (event_key, fingerprint),
        ).fetchone()

    def update_calendar_approval(
        self,
        approval_id: int,
        status: str,
        *,
        response_stable_key: str = "",
        backend: str = "",
        created_path: str = "",
        error: str = "",
        responded: bool = False,
    ) -> None:
        fields = ["status = ?", "updated_at = ?", "error = ?"]
        values: list[Any] = [status, now_utc_iso(), error[:4000]]
        if response_stable_key:
            fields.append("response_stable_key = ?")
            values.append(response_stable_key)
        if backend:
            fields.append("backend = ?")
            values.append(backend)
        if created_path:
            fields.append("created_path = ?")
            values.append(created_path)
        if responded:
            fields.append("responded_at = ?")
            values.append(now_utc_iso())
        values.append(int(approval_id))
        self.connection.execute(
            f"UPDATE calendar_approvals SET {', '.join(fields)} WHERE id = ?", values
        )
        self.connection.commit()

    def digest_rows(self, start_utc: str, end_utc: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT stable_key, sender_name, sender_addr, subject, category, confidence,
                   importance, reason, summary, expected_action, status, destination_folder,
                   updated_at, last_error
            FROM messages
            WHERE updated_at >= ? AND updated_at < ?
            ORDER BY importance DESC, updated_at ASC
            """,
            (start_utc, end_utc),
        ).fetchall()
        return [dict(row) for row in rows]

    def digest_events(self, start_utc: str, end_utc: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT event_key, uid, title, starts_at, ends_at, status, backend, path, error, created_at
            FROM events
            WHERE created_at >= ? AND created_at < ?
            ORDER BY starts_at ASC, created_at ASC
            """,
            (start_utc, end_utc),
        ).fetchall()
        return [dict(row) for row in rows]

    def digest_sent(self, day: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM digests WHERE day = ? AND status = 'sent'", (day,)
        ).fetchone()
        return bool(row)

    def mark_digest(self, day: str, status: str, detail: str = "") -> None:
        self.connection.execute(
            """
            INSERT INTO digests(day, sent_at, status, detail)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET sent_at=excluded.sent_at, status=excluded.status, detail=excluded.detail
            """,
            (day, now_utc_iso(), status, detail[:4000]),
        )
        self.connection.commit()

    def invoice_summary(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM invoices GROUP BY status ORDER BY status"
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def approval_summary(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM calendar_approvals GROUP BY status ORDER BY status"
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def status_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM messages GROUP BY status ORDER BY status"
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def recent_errors(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT subject, sender_addr, status, last_error, updated_at
            FROM messages WHERE last_error IS NOT NULL AND last_error != ''
            ORDER BY updated_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
