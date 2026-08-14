from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .classifier import OllamaClassifier
from .himalaya import HimalayaClient
from .parser import parse_eml
from .rules import RuleEngine
from .storage import Storage


class ReviewService:
    """Read-only review diagnostics with exact IMAP identity guards."""

    def __init__(
        self,
        storage: Storage,
        rules: RuleEngine,
        classifier: OllamaClassifier,
        client: HimalayaClient,
    ) -> None:
        self.storage = storage
        self.rules = rules
        self.classifier = classifier
        self.client = client

    def status(self, *, days: int = 7) -> dict[str, Any]:
        return self.storage.review_status(days=days)

    def list(self, reason: str, *, limit: int = 50) -> dict[str, Any]:
        return self.storage.review_items(reason, limit=limit)

    def suggest(self, folder: str, message_id: str, expected_subject: str) -> dict[str, Any]:
        selected_folder = str(folder or "").strip()
        selected_id = str(message_id or "").strip()
        selected_subject = str(expected_subject or "").strip()
        if not selected_folder or not selected_id or not selected_subject:
            raise ValueError("Ordner, Mailbox-ID und erwarteter Betreff sind erforderlich")

        folders, folder_error = self.client.list_folders()
        if folder_error:
            raise RuntimeError(folder_error)
        folder_map = {item.strip().casefold(): item.strip() for item in folders if item.strip()}
        resolved_folder = folder_map.get(selected_folder.casefold())
        if not resolved_folder:
            raise ValueError(f"Mailordner nicht gefunden: {selected_folder}")

        envelopes, list_error = self.client.list_envelopes(resolved_folder, limit=200)
        if list_error:
            raise RuntimeError(list_error)
        envelope = next(
            (item for item in envelopes if str(item.mailbox_id) == selected_id),
            None,
        )
        if envelope is None:
            raise ValueError("Mailbox-ID ist im angegebenen Ordner nicht vorhanden")
        if envelope.subject.strip() != selected_subject:
            raise PermissionError("Betreff stimmt nicht mit der erwarteten Mail ueberein")

        with tempfile.TemporaryDirectory(prefix="openclaw-mail-review-") as temp_dir:
            os.chmod(temp_dir, 0o700)
            destination = Path(temp_dir) / "message.eml"
            exported = self.client.export_message(resolved_folder, selected_id, destination)
            if not exported.ok or not destination.is_file():
                raise RuntimeError(exported.detail or "Mail konnte nicht exportiert werden")
            os.chmod(destination, 0o600)
            message = parse_eml(destination.read_bytes(), envelope, resolved_folder)
        if message.subject.strip() != selected_subject:
            raise PermissionError("Exportierter Betreff stimmt nicht mit der erwarteten Mail ueberein")

        original = self.storage.get_message(message.stable_key)
        context = self.rules.evaluate(message)
        current = self.classifier.classify(message)
        fallback = current.source in {"fallback", "important-fallback"}
        evidence: dict[str, Any] = {
            "rule_forced": asdict(context.forced) if context.forced is not None else None,
            "rule_notes": list(context.notes or []),
            "prevent_spam": context.prevent_spam,
            "important_sender": context.important_sender,
            "known_contact": context.known_contact,
            "classification_source": current.source,
            "classification_reason": current.reason,
        }
        original_decision = None
        if original is not None:
            original_decision = {
                "category": original["review_category"] or original["category"],
                "confidence": (
                    original["review_confidence"]
                    if original["review_confidence"] is not None
                    else original["confidence"]
                ),
                "source": original["review_source"],
                "review_reason": original["review_reason"],
            }
        return {
            "ok": not fallback,
            "status": "abstain" if fallback else "suggestion",
            "read_only": True,
            "stored": False,
            "moved": False,
            "sent": False,
            "identity": {
                "folder": resolved_folder,
                "mailbox_id": selected_id,
                "subject": selected_subject,
            },
            "original_decision": original_decision,
            "current_decision": {
                "category": current.category,
                "confidence": current.confidence,
                "importance": current.importance,
                "forward": current.forward,
                "source": current.source,
                "reason": current.reason,
            },
            "evidence": evidence,
            "uncertainty": {
                "abstained": fallback or current.category == "uncertain",
                "model_failure": fallback,
                "conflict_or_mixed_sender": any(
                    marker in note.casefold()
                    for note in evidence["rule_notes"]
                    for marker in ("widerspruech", "gemischter absender")
                ),
            },
            "complete": True,
            "folder_errors": [],
            "results_may_be_truncated": False,
            "approval_required": True,
            "next_step": "request-explicit-review-correction",
            "next_tool": "mail.review.correct",
            "correction_available": True,
        }
