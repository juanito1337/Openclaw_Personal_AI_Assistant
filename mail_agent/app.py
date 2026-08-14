from __future__ import annotations

import json
import logging
import tempfile
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personal_assistant.antivirus import AntivirusResult, HostAntivirus
from personal_assistant.tool_settings import load_tool_settings

from .assistant_bridge import PersonalAssistantActionBridge
from .attachments import extract_all_attachments
from .calendar import CalendarManager
from .classifier import OllamaClassifier, RuntimeBudgetExceeded
from .command import CommandRunner
from .config import Config
from .digest import DigestManager
from .forwarding import Forwarder
from .himalaya import HimalayaClient
from .invoices import InvoiceManager
from .learning import LearningFolderRegistry
from .models import Classification, Envelope, OperationResult, ParsedMessage
from .nextcloud import NextcloudSkillClient
from .notifier import Notifier
from .parser import parse_eml
from .review_service import ReviewService
from .rules import RuleEngine
from .search_snapshot import SearchSnapshotWriter
from .storage import Storage
from .telemetry import PerformanceTelemetry


@dataclass(slots=True)
class RunSummary:
    processed: int = 0
    skipped: int = 0
    actions: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    classifier: dict[str, object] = field(default_factory=dict)
    performance: dict[str, object] = field(default_factory=dict)
    drain: dict[str, object] = field(default_factory=dict)

    def add(self, message: ParsedMessage | None, result: OperationResult, category: str = "") -> None:
        self.actions.append({
            "message": message.stable_key if message else "",
            "subject": message.subject if message else "",
            "category": category,
            "status": result.status,
            "ok": result.ok,
            "detail": result.detail,
            "destination": result.destination,
            "path": result.path,
        })
        if not result.ok:
            self.errors.append(result.detail or result.status)

    def merge(self, other: RunSummary) -> None:
        self.processed += other.processed
        self.skipped += other.skipped
        self.actions.extend(other.actions)
        self.errors.extend(other.errors)

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "processed": self.processed,
            "skipped": self.skipped,
            "actions": self.actions,
            "errors": self.errors,
            "classifier": self.classifier,
            "performance": self.performance,
        }
        if self.drain:
            data["drain"] = self.drain
        return data


class MailAgent:
    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.log = logging.getLogger(__name__)
        self.telemetry = PerformanceTelemetry.for_database(config.runtime.database)
        self.runner = CommandRunner(
            config.runtime.command_timeout_seconds,
            observer=self.telemetry.observe_command,
        )
        self.storage = Storage(config.runtime.database)
        self.learning_folders = LearningFolderRegistry(config)
        self.search_snapshots = SearchSnapshotWriter(
            config.runtime.database.parent / "search_documents",
            enabled=not dry_run,
        )
        self.himalaya = HimalayaClient(config, self.runner, dry_run=dry_run)
        self.tool_settings = load_tool_settings()
        self.nextcloud = NextcloudSkillClient(
            config,
            self.runner,
            calendar_resource_id=self.tool_settings.mail.calendar_mail.calendar_resource_id,
        )
        self.rules = RuleEngine(
            config.runtime.rules_file,
            self.storage,
            contact_lookup=(
                self.nextcloud.is_known_contact
                if config.nextcloud.enabled and config.nextcloud.contacts_enabled
                else None
            ),
            contacts_prevent_spam=config.nextcloud.contacts_prevent_spam,
            trust_contacts_for_calendar=config.nextcloud.trust_contacts_for_calendar,
            contact_importance_boost=config.nextcloud.contact_importance_boost,
        )
        self.classifier = OllamaClassifier(
            config, self.storage, self.rules, telemetry=self.telemetry
        )
        self.review = ReviewService(self.storage, self.rules, self.classifier, self.himalaya)
        self.forwarder = Forwarder(config, self.himalaya)
        self.antivirus = HostAntivirus(self.tool_settings.security.antivirus)
        self.assistant_bridge = PersonalAssistantActionBridge(dry_run=dry_run)
        self.invoices = InvoiceManager(
            config,
            self.storage,
            self.assistant_bridge,
            self.tool_settings.mail.invoices,
            antivirus=self.antivirus,
            dry_run=dry_run,
        )
        self.calendar = CalendarManager(
            config,
            self.storage,
            self.runner,
            dry_run=dry_run,
            nextcloud=self.nextcloud,
            send_mail=self.forwarder.send_plain_to,
            assistant_bridge=self.assistant_bridge,
            command_settings=self.tool_settings.mail.calendar_mail,
        )
        self.notifier = Notifier(config, self.runner, dry_run=dry_run)
        self.digest = DigestManager(config, self.storage, self.forwarder, dry_run=dry_run)

    def close(self) -> None:
        self.antivirus.close()
        self.storage.close()

    def _start_telemetry(self, operation: str) -> None:
        telemetry = getattr(self, "telemetry", None)
        if telemetry is None:
            try:
                telemetry = PerformanceTelemetry.for_database(self.config.runtime.database)
            except Exception:
                return
            self.telemetry = telemetry
            runner = getattr(self, "runner", None)
            if runner is not None and getattr(runner, "observer", None) is None:
                runner.observer = telemetry.observe_command
            classifier = getattr(self, "classifier", None)
            if classifier is not None and getattr(classifier, "telemetry", None) is None:
                classifier.telemetry = telemetry
        telemetry.reset(operation)

    def _phase(self, name: str):
        telemetry = getattr(self, "telemetry", None)
        return telemetry.phase(name) if telemetry is not None else nullcontext()

    def _finish_telemetry(self, summary: RunSummary) -> None:
        telemetry = getattr(self, "telemetry", None)
        if telemetry is None:
            return
        summary.performance = telemetry.finish(
            processed=summary.processed,
            skipped=summary.skipped,
            errors=summary.errors,
            classifier=summary.classifier,
            drain=summary.drain,
        )

    def _required_folders(self) -> list[str]:
        return list(dict.fromkeys([
            *self.config.folders.all(),
            *self.learning_folders.active_folders(),
        ]))

    def setup(self) -> list[OperationResult]:
        return self.himalaya.ensure_folders(self._required_folders())

    def _prepare_run(self, summary: RunSummary) -> bool:
        folders, folder_error = self.himalaya.list_folders()
        if folder_error:
            summary.errors.append(f"Ordner-Preflight fehlgeschlagen: {folder_error}")
            return False
        existing = {folder.casefold() for folder in folders}
        required_folders = self._required_folders()
        if not self.tool_settings.security.antivirus.enabled:
            required_folders = [
                item for item in required_folders
                if item.casefold() != self.config.folders.malware.casefold()
            ]
        missing = [folder for folder in required_folders if folder.casefold() not in existing]
        if missing:
            summary.errors.append(
                "Erforderliche Agent-Ordner fehlen: " + ", ".join(missing) + ". Zuerst 'scripts/mail-agent.sh setup' ausfuehren."
            )
            return False
        if self.config.mailbox.source_folder.casefold() not in existing:
            summary.errors.append(
                f"Primaerer Quellordner fehlt: {self.config.mailbox.source_folder}"
            )
            return False
        missing_quarantine = [
            folder for folder in self.config.mailbox.quarantine_folders
            if folder.casefold() not in existing
        ]
        if missing_quarantine:
            detail = "Konfigurierte Spam-/Quarantaeneordner fehlen: " + ", ".join(missing_quarantine)
            self.log.warning(detail)
            summary.actions.append({
                "message": "",
                "subject": "",
                "category": "system",
                "status": "quarantine-folder-missing",
                "ok": True,
                "detail": detail,
                "destination": "",
                "path": "",
            })
        if self.config.nextcloud.enabled and self.config.nextcloud.contacts_enabled:
            contacts_ok, contacts_detail = self.nextcloud.refresh_contact_cache(force=False)
            if not contacts_ok:
                self.log.warning("Nextcloud-Kontaktabgleich nicht verfuegbar: %s", contacts_detail)
        return True

    def _process_batch(self, limit: int) -> RunSummary:
        summary = RunSummary()
        self._process_feedback(summary, limit=limit)
        if summary.errors:
            return summary

        remaining = max(0, limit - summary.processed)
        available_quarantine = self._available_quarantine_folders()
        if remaining and available_quarantine and self.config.mailbox.quarantine_max_per_run > 0:
            quarantine_budget = min(
                self.config.mailbox.quarantine_max_per_run,
                max(1, remaining // 5),
            )
            for folder in available_quarantine:
                if quarantine_budget <= 0:
                    break
                before = summary.processed
                self._process_quarantine_folder(folder, summary, quarantine_budget)
                quarantine_budget -= max(0, summary.processed - before)

        remaining = max(0, limit - summary.processed)
        if remaining:
            self._process_folder(self.config.mailbox.source_folder, summary, remaining)
        return summary

    def _available_quarantine_folders(self) -> list[str]:
        folders, error = self.himalaya.list_folders()
        if error:
            self.log.warning("Quarantaeneordner konnten nicht ermittelt werden: %s", error)
            return []
        by_folded = {folder.casefold(): folder for folder in folders}
        return [
            by_folded[configured.casefold()]
            for configured in self.config.mailbox.quarantine_folders
            if configured.casefold() in by_folded
        ]

    def _append_digest(self, summary: RunSummary) -> None:
        digest_result = self.digest.send_if_due(force=False)
        if digest_result.status not in {"digest-not-due", "digest-already-sent", "digest-empty", "digest-disabled"}:
            summary.add(None, digest_result, "digest")

    def run(self, limit: int = 20, include_digest: bool = True) -> RunSummary:
        summary = RunSummary()
        self._start_telemetry("run-dry" if self.dry_run else "run")
        self.classifier.reset_metrics()
        try:
            with self._phase("preflight"):
                prepared = self._prepare_run(summary)
            if not prepared:
                return summary
            with self._phase("mail_processing"):
                summary.merge(self._process_batch(limit=max(1, limit)))
            if summary.errors:
                return summary
            if include_digest:
                with self._phase("digest"):
                    self._append_digest(summary)
            return summary
        finally:
            summary.classifier = self.classifier.metrics_snapshot()
            self._finish_telemetry(summary)

    def review_quarantine(self, limit: int = 20) -> RunSummary:
        summary = RunSummary()
        self._start_telemetry("spam-review-dry" if self.dry_run else "spam-review")
        self.classifier.reset_metrics()
        try:
            with self._phase("preflight"):
                prepared = self._prepare_run(summary)
            if not prepared:
                return summary
            remaining = max(1, limit)
            folders = self._available_quarantine_folders()
            if not folders:
                summary.actions.append({
                    "message": "",
                    "subject": "",
                    "category": "system",
                    "status": "quarantine-empty-or-unavailable",
                    "ok": True,
                    "detail": "Kein konfigurierter Spam-/Quarantaeneordner ist verfuegbar",
                    "destination": "",
                    "path": "",
                })
                return summary
            for folder in folders:
                if remaining <= 0:
                    break
                before = summary.processed
                self._process_quarantine_folder(folder, summary, remaining)
                remaining -= max(0, summary.processed - before)
            return summary
        finally:
            summary.classifier = self.classifier.metrics_snapshot()
            self._finish_telemetry(summary)

    def drain(
        self,
        *,
        batch_size: int = 20,
        max_messages: int = 500,
        max_runtime_seconds: int = 2400,
        shutdown_reserve_seconds: int = 180,
        max_batches: int = 100,
        include_digest: bool = True,
    ) -> RunSummary:
        """Process work in bounded batches until the inbox is empty.

        Dry-runs intentionally execute one batch only because no messages are
        moved and repeating the same inbox page would not be meaningful.
        """
        batch_size = max(1, batch_size)
        max_messages = max(batch_size, max_messages)
        max_runtime_seconds = max(1, max_runtime_seconds)
        shutdown_reserve_seconds = max(30, shutdown_reserve_seconds)
        if shutdown_reserve_seconds >= max_runtime_seconds:
            shutdown_reserve_seconds = max(1, max_runtime_seconds // 4)
        max_batches = max(1, max_batches)

        summary = RunSummary()
        self._start_telemetry("drain-dry" if self.dry_run else "drain")
        started = time.monotonic()
        batches = 0
        stop_reason = ""
        inbox_remaining: bool | None = None
        self.classifier.reset_metrics()
        work_deadline = started + max_runtime_seconds - shutdown_reserve_seconds
        deadline_setter = getattr(self.classifier, "set_runtime_deadline", None)
        if callable(deadline_setter):
            deadline_setter(work_deadline)
        try:
            with self._phase("preflight"):
                prepared = self._prepare_run(summary)
            if not prepared:
                stop_reason = "preflight-error"
                return summary

            while True:
                elapsed = time.monotonic() - started
                if summary.processed >= max_messages:
                    stop_reason = "max-messages"
                    break
                if batches >= max_batches:
                    stop_reason = "max-batches"
                    break
                remaining_work = work_deadline - time.monotonic()
                if remaining_work <= 0:
                    stop_reason = "runtime-reserve"
                    break

                current_limit = min(batch_size, max_messages - summary.processed)
                try:
                    batch = self._process_batch(limit=current_limit)
                except RuntimeBudgetExceeded:
                    stop_reason = "runtime-reserve"
                    break
                batches += 1
                summary.merge(batch)
                telemetry = getattr(self, "telemetry", None)
                progress_updater = getattr(telemetry, "update_progress", None)
                if callable(progress_updater):
                    progress_updater(
                        processed=summary.processed,
                        skipped=summary.skipped,
                        errors=summary.errors,
                        classifier=self.classifier.metrics_snapshot(),
                        phase="batch-complete",
                    )

                if batch.errors:
                    stop_reason = "batch-error"
                    break

                if self.dry_run:
                    envelopes, error = self.himalaya.list_envelopes(
                        self.config.mailbox.source_folder, limit=1
                    )
                    if error:
                        summary.errors.append(f"INBOX-Status konnte nicht gelesen werden: {error}")
                        stop_reason = "status-error"
                    else:
                        inbox_remaining = bool(envelopes)
                        stop_reason = "dry-run-single-batch"
                    break

                envelopes, error = self.himalaya.list_envelopes(
                    self.config.mailbox.source_folder, limit=1
                )
                if error:
                    summary.errors.append(f"INBOX-Status konnte nicht gelesen werden: {error}")
                    stop_reason = "status-error"
                    break
                inbox_remaining = bool(envelopes)

                if batch.processed == 0:
                    if inbox_remaining:
                        summary.errors.append(
                            "Drain-Modus hat keinen Fortschritt erzielt, obwohl noch Mails in der INBOX liegen."
                        )
                        stop_reason = "no-progress"
                    else:
                        stop_reason = "queue-empty"
                    break

                # If fewer than one full batch was consumed and the inbox is now
                # empty, all correction folders were also exhausted in this cycle.
                if not inbox_remaining and batch.processed < current_limit:
                    stop_reason = "queue-empty"
                    break

            if include_digest and not summary.errors and stop_reason != "runtime-reserve":
                self._append_digest(summary)
            return summary
        finally:
            elapsed = time.monotonic() - started
            if not stop_reason:
                stop_reason = "completed"
            summary.classifier = self.classifier.metrics_snapshot()
            summary.drain = {
                "enabled": True,
                "batch_size": batch_size,
                "batches": batches,
                "max_messages": max_messages,
                "max_runtime_seconds": max_runtime_seconds,
                "shutdown_reserve_seconds": shutdown_reserve_seconds,
                "work_budget_seconds": max_runtime_seconds - shutdown_reserve_seconds,
                "max_batches": max_batches,
                "elapsed_seconds": round(elapsed, 3),
                "stop_reason": stop_reason,
                "inbox_remaining": inbox_remaining,
            }
            deadline_clearer = getattr(self.classifier, "clear_runtime_deadline", None)
            if callable(deadline_clearer):
                deadline_clearer()
            self._finish_telemetry(summary)

    def _process_feedback(self, summary: RunSummary, limit: int) -> None:
        learning_registry = getattr(self, "learning_folders", None)
        dynamic_mappings = learning_registry.feedback_mappings() if learning_registry is not None else []
        mappings = [
            (self.config.folders.feedback_spam, "spam", ""),
            (self.config.folders.feedback_unimportant, "routine", ""),
            (self.config.folders.feedback_important, "relevant", ""),
            (self.config.folders.feedback_not_spam, "not_spam", ""),
            *dynamic_mappings,
        ]
        for folder, verdict, label in mappings:
            remaining = max(0, limit - summary.processed)
            if remaining == 0:
                break
            with self._phase("mail_list"):
                envelopes, error = self.himalaya.list_envelopes(folder, limit=remaining)
            if error:
                detail = f"Feedback-Ordner {folder} konnte nicht gelesen werden: {error}"
                self.log.error(detail)
                summary.errors.append(detail)
                return
            for envelope in envelopes:
                if summary.processed >= limit:
                    break
                message = self._load_message(folder, envelope, summary)
                if not message:
                    continue
                existing = self.storage.get_message(message.stable_key)
                if existing and existing["status"] == "forwarding" and not existing["forwarded_at"]:
                    result = self._move(
                        message, folder, self.config.folders.error, "error",
                        "Vorheriger Versand wurde unterbrochen; zur Vermeidung einer Doppelweiterleitung ist eine manuelle Pruefung erforderlich.",
                    )
                    summary.processed += 1
                    summary.add(message, result, str(existing["category"] or ""))
                    continue
                # A message in a correction folder is an explicit user decision.
                # It must override an older failed move instead of retrying the old
                # destination first.
                if verdict == "spam":
                    classification = Classification("spam", 1.0, 1, False, "Vom Nutzer als Spam markiert", source="feedback")
                elif verdict == "routine":
                    classification = Classification("routine", 1.0, 2, False, "Vom Nutzer als unwichtig/Routine markiert", source="feedback")
                elif verdict == "relevant":
                    if message.calendar_invites:
                        classification = Classification(
                            "appointment", 1.0, 9, True, "Vom Nutzer als wichtige Termineinladung markiert",
                            summary=f"Manuell als wichtig markierte Termineinladung: {message.subject}",
                            expected_action="Termin und Originalmail pruefen.",
                            source="feedback",
                        )
                    else:
                        candidate = self.classifier.classify(message, force_not_spam=True)
                        if candidate.category == "appointment":
                            classification = Classification(
                                "appointment", 1.0, max(candidate.importance, 9), True,
                                "Vom Nutzer als wichtig markiert; Terminangaben wurden erneut extrahiert. " + candidate.reason,
                                summary=candidate.summary or f"Manuell als wichtig markierte Mail: {message.subject}",
                                expected_action=candidate.expected_action or "Termin und Originalmail pruefen.",
                                calendar_event=candidate.calendar_event,
                                source="feedback+" + candidate.source,
                            )
                        else:
                            classification = Classification(
                                "relevant", 1.0, 9, True, "Vom Nutzer als wichtig markiert",
                                summary=f"Manuell als wichtig markierte Mail: {message.subject}",
                                expected_action="Mail lesen und bearbeiten.",
                                source="feedback",
                            )
                else:
                    if not self.dry_run:
                        self.storage.record_feedback(
                            message,
                            verdict,
                            folder,
                            metadata={"origin": "not-spam-correction-folder"},
                            label=label,
                        )
                    classification = self.classifier.classify(message, force_not_spam=True)

                if verdict != "not_spam" and not self.dry_run:
                    self.storage.record_feedback(message, verdict, folder, label=label)
                result = self._route(message, classification, folder, force=verdict != "not_spam")
                summary.processed += 1
                summary.add(message, result, classification.category)

    def _process_folder(self, folder: str, summary: RunSummary, limit: int) -> None:
        with self._phase("mail_list"):
            envelopes, error = self.himalaya.list_envelopes(folder, limit=limit)
        if error:
            summary.errors.append(error)
            return

        pending: list[ParsedMessage] = []
        prefetch = (
            self.config.ollama.batch_prefetch
            if self.config.ollama.batch_enabled
            else 1
        )

        for envelope in envelopes:
            message = self._load_message(folder, envelope, summary)
            if not message:
                continue
            command_result = self._handle_calendar_command_mail(message, folder)
            if command_result is not None:
                summary.processed += 1
                summary.add(message, command_result, "appointment")
                continue
            approval_result = self._handle_calendar_approval_reply(message, folder)
            if approval_result is not None:
                summary.processed += 1
                summary.add(message, approval_result, "routine")
                continue
            existing = self.storage.get_message(message.stable_key)
            if existing and existing["status"] == "forwarding" and not existing["forwarded_at"]:
                detail = "Vorheriger Versand wurde unterbrochen; zur Vermeidung einer Doppelweiterleitung ist eine manuelle Pruefung erforderlich."
                result = self._move(message, folder, self.config.folders.error, "error", detail)
                summary.processed += 1
                summary.add(message, result, str(existing["category"] or ""))
                continue
            retry_result = self._retry_pending_move(message, folder, existing)
            if retry_result is not None:
                summary.processed += 1
                summary.add(message, retry_result, str(existing["category"] or "") if existing else "")
                continue
            if (
                existing
                and existing["status"] in {"spam", "quarantine-reviewed"}
                and folder.casefold() == self.config.mailbox.source_folder.casefold()
            ):
                # Restoring a message from either the agent spam folder or the
                # provider quarantine into the primary inbox is explicit not-spam
                # feedback, even without the dedicated correction folder.
                if not self.dry_run:
                    self.storage.record_feedback(
                        message,
                        "not_spam",
                        "INBOX-Restore",
                        metadata={
                            "origin": "inbox-restore",
                            "previous_status": str(existing["status"] or ""),
                            "source_folder": folder,
                        },
                    )
                classification = self.classifier.classify(message, force_not_spam=True)
                result = self._route(message, classification, folder)
                summary.processed += 1
                summary.add(message, result, classification.category)
                continue
            if existing and self.storage.is_final(message.stable_key):
                summary.skipped += 1
                continue

            pending.append(message)
            if len(pending) >= prefetch:
                self._classify_and_route_batch(folder, pending, summary)
                pending = []

        if pending:
            self._classify_and_route_batch(folder, pending, summary)

    def _process_quarantine_folder(self, folder: str, summary: RunSummary, limit: int) -> None:
        with self._phase("mail_list"):
            envelopes, error = self.himalaya.list_envelopes(folder, limit=limit)
        if error:
            detail = f"Spam-/Quarantaeneordner {folder} konnte nicht gelesen werden: {error}"
            self.log.warning(detail)
            summary.actions.append({
                "message": "",
                "subject": "",
                "category": "system",
                "status": "quarantine-read-failed",
                "ok": True,
                "detail": detail,
                "destination": folder,
                "path": "",
            })
            return

        pending: list[ParsedMessage] = []
        prefetch = self.config.ollama.batch_prefetch if self.config.ollama.batch_enabled else 1
        for envelope in envelopes:
            message = self._load_message(folder, envelope, summary)
            if not message:
                continue

            command_result = self._handle_calendar_command_mail(message, folder)
            if command_result is not None:
                summary.processed += 1
                summary.add(message, command_result, "appointment")
                continue

            approval_result = self._handle_calendar_approval_reply(message, folder)
            if approval_result is not None:
                summary.processed += 1
                summary.add(message, approval_result, "routine")
                continue

            existing = self.storage.get_message(message.stable_key)
            retry_result = self._retry_pending_move(message, folder, existing)
            if retry_result is not None:
                summary.processed += 1
                summary.add(message, retry_result, str(existing["category"] or "") if existing else "")
                continue
            if existing and self.storage.is_final(message.stable_key):
                summary.skipped += 1
                continue

            pending.append(message)
            if len(pending) >= prefetch:
                self._classify_and_route_quarantine_batch(folder, pending, summary)
                pending = []

        if pending:
            self._classify_and_route_quarantine_batch(folder, pending, summary)

    def _classify_and_route_quarantine_batch(
        self,
        folder: str,
        messages: list[ParsedMessage],
        summary: RunSummary,
    ) -> None:
        with self._phase("classification"):
            classifications = self.classifier.classify_many(messages)
        if len(classifications) != len(messages):
            summary.errors.append(
                f"Interner Quarantaene-Batchfehler: {len(messages)} Mails, aber {len(classifications)} Klassifizierungen"
            )
            return
        with self._phase("routing"):
            for message, classification in zip(messages, classifications, strict=True):
                result = self._route_quarantine(message, classification, folder)
                summary.processed += 1
                summary.add(message, result, classification.category)

    def _route_quarantine(
        self, message: ParsedMessage, classification: Classification, source_folder: str
    ) -> OperationResult:
        # The provider spam folder is a quarantine source, not a second inbox.
        # Only clear rescue cases are moved out. Obvious spam and ordinary routine
        # mail stay in place and are marked final locally to avoid repeated model use.
        if classification.category in {"relevant", "appointment", "uncertain"}:
            return self._route(message, classification, source_folder)

        if classification.category == "routine":
            invoice_result = self.invoices.process(message, classification)
            if invoice_result.status == "invoice-review-required":
                if self.dry_run:
                    return OperationResult(
                        True,
                        "dry-run-quarantine-rescue",
                        "Wuerde unklare Rechnungs-Mail aus dem Spamordner zur Pruefung retten: " + invoice_result.detail,
                        destination=self.config.folders.review,
                        path=invoice_result.path,
                    )
                self.storage.upsert_message(message, classification, status="classified")
                return self._move(
                    message, source_folder, self.config.folders.review, "review", invoice_result.detail
                )
            if not invoice_result.ok:
                if self.dry_run:
                    return OperationResult(
                        True,
                        "dry-run-quarantine-error",
                        "Rechnungsarchivierung wuerde fehlschlagen: " + (invoice_result.detail or invoice_result.status),
                        destination=self.config.folders.error,
                    )
                self.storage.upsert_message(message, classification, status="classified")
                return self._move(
                    message, source_folder, self.config.folders.error, "error", invoice_result.detail
                )
            if invoice_result.status in {"invoice-archived-review-required", "invoice-duplicate-review-required"}:
                if self.dry_run:
                    return OperationResult(True, "dry-run-quarantine-invoice-review", invoice_result.detail, destination=self.config.folders.review, path=invoice_result.path)
                self.storage.upsert_message(message, classification, status="classified")
                return self._move(message, source_folder, self.config.folders.review, "review", invoice_result.detail)
            if invoice_result.status in {
                "invoice-archived", "invoice-duplicate", "would-archive-invoice",
                "invoice-archived-metadata-review", "invoice-duplicate-metadata-review",
            }:
                if self.dry_run:
                    return OperationResult(
                        True,
                        "dry-run-quarantine-invoice",
                        invoice_result.detail,
                        destination=self.config.folders.routine,
                        path=invoice_result.path,
                    )
                self.storage.upsert_message(message, classification, status="classified")
                return self._move(
                    message, source_folder, self.config.folders.routine, "routine", invoice_result.detail
                )

        if not self.config.mailbox.quarantine_rescue_only:
            return self._route(message, classification, source_folder)

        detail = (
            f"Im Provider-Spamordner geprueft und dort belassen; Kategorie {classification.category}, "
            f"Sicherheit {classification.confidence:.2f}"
        )
        if self.dry_run:
            return OperationResult(
                True,
                "dry-run-quarantine-keep",
                detail,
                destination=source_folder,
            )
        self.storage.upsert_message(message, classification, status="quarantine-reviewed")
        self.storage.record_action(
            message.stable_key,
            "quarantine-review",
            source_folder,
            source_folder,
            True,
            detail,
        )
        return OperationResult(
            True,
            "quarantine-kept",
            detail,
            destination=source_folder,
        )

    def _classify_and_route_batch(
        self,
        folder: str,
        messages: list[ParsedMessage],
        summary: RunSummary,
    ) -> None:
        with self._phase("classification"):
            classifications = self.classifier.classify_many(messages)
        if len(classifications) != len(messages):
            summary.errors.append(
                f"Interner Batchfehler: {len(messages)} Mails, aber {len(classifications)} Klassifizierungen"
            )
            return
        with self._phase("routing"):
            for message, classification in zip(messages, classifications, strict=True):
                result = self._route(message, classification, folder)
                summary.processed += 1
                summary.add(message, result, classification.category)

    def _block_antivirus_result(
        self,
        folder: str,
        envelope: Envelope,
        summary: RunSummary,
        scan: AntivirusResult,
        *,
        message: ParsedMessage | None = None,
    ) -> bool:
        settings = self.tool_settings.security.antivirus
        if scan.clean or scan.status == "disabled":
            return False
        if scan.error and not settings.fail_closed:
            self.log.warning("Virenscan fehlgeschlagen, fail_closed=false: %s", scan.detail)
            return False

        infected = scan.infected
        destination = self.config.folders.malware if infected else self.config.folders.error
        final_status = "malware-detected" if infected else "antivirus-error"
        signature = scan.signature or scan.detail or scan.status
        detail = (
            f"Virenscanner blockierte {scan.source_type} {scan.name}: {signature}"
            if infected
            else f"Virenscan nicht eindeutig erfolgreich; Mail bleibt fail-closed gesperrt: {signature}"
        )
        stable_key = message.stable_key if message else f"mailbox:{folder}/{envelope.mailbox_id}"
        if self.dry_run:
            result = OperationResult(True, "would-" + final_status, detail, destination=destination)
        else:
            moved = self.himalaya.move_message(folder, destination, envelope.mailbox_id)
            self.storage.record_action(stable_key, "antivirus", folder, destination, moved.ok, detail)
            if message is not None:
                self.storage.upsert_message(message, status=final_status)
                self.storage.update_status(
                    message.stable_key,
                    final_status if moved.ok else "move-failed",
                    destination=destination,
                    error=detail,
                    increment_retry=not moved.ok,
                )
            result = OperationResult(
                moved.ok,
                final_status if moved.ok else "antivirus-move-failed",
                detail if moved.ok else detail + " | Verschieben fehlgeschlagen: " + moved.detail,
                destination=destination,
            )
        summary.processed += 1
        summary.add(message, result, "security")
        if scan.error:
            summary.errors.append(detail)
        return True

    def _load_message(self, folder: str, envelope: Envelope, summary: RunSummary) -> ParsedMessage | None:
        with tempfile.TemporaryDirectory(prefix="mail-agent-") as temp_dir:
            path = Path(temp_dir) / "message.eml"
            with self._phase("mail_export"):
                exported = self.himalaya.export_message(folder, envelope.mailbox_id, path)
            if not exported.ok:
                if self.himalaya.is_missing_message_error(exported.detail):
                    summary.skipped += 1
                    detail = (
                        "Nachricht war beim Export nicht mehr im Ordner vorhanden; "
                        "sie wurde sicher uebersprungen und es wurde keine Aktion ausgefuehrt."
                    )
                    self.log.warning(
                        "Nachricht %s/%s ist zwischen Auflistung und Export verschwunden",
                        folder, envelope.mailbox_id,
                    )
                    summary.actions.append({
                        "message": f"mailbox:{folder}/{envelope.mailbox_id}",
                        "subject": envelope.subject,
                        "category": "",
                        "status": "skipped-missing",
                        "ok": True,
                        "detail": detail,
                        "destination": "",
                        "path": "",
                    })
                    return None
                summary.errors.append(f"Export {folder}/{envelope.mailbox_id}: {exported.detail}")
                return None
            try:
                with self._phase("mail_read_raw"):
                    raw = path.read_bytes()
                av_settings = self.tool_settings.security.antivirus
                if av_settings.enabled and av_settings.scan_raw_mail:
                    with self._phase("antivirus_raw"):
                        scan = self.antivirus.scan_bytes(
                            raw,
                            name=f"{envelope.mailbox_id}.eml",
                            source_type="raw-mail",
                        )
                    telemetry = getattr(self, "telemetry", None)
                    if telemetry is not None:
                        telemetry.record_antivirus(
                            duration_ms=scan.duration_ms,
                            status=scan.status,
                            source_type="raw-mail-scanner",
                        )
                    if self._block_antivirus_result(folder, envelope, summary, scan):
                        return None
                with self._phase("mail_parse"):
                    message = parse_eml(raw, envelope, folder)
                if av_settings.enabled and av_settings.scan_attachments:
                    with self._phase("attachment_extract"):
                        attachments = extract_all_attachments(message)
                    for attachment in attachments:
                        with self._phase("antivirus_attachment"):
                            scan = self.antivirus.scan_bytes(
                                attachment.data,
                                name=attachment.filename,
                                source_type="mail-attachment",
                            )
                        telemetry = getattr(self, "telemetry", None)
                        if telemetry is not None:
                            telemetry.record_antivirus(
                                duration_ms=scan.duration_ms,
                                status=scan.status,
                                source_type="attachment-scanner",
                            )
                        if self._block_antivirus_result(
                            folder, envelope, summary, scan, message=message
                        ):
                            return None
                with self._phase("snapshot_write"):
                    self.search_snapshots.write(message)
                return message
            except Exception as exc:
                summary.errors.append(f"Parse {folder}/{envelope.mailbox_id}: {exc}")
                return None

    def _process_order_event(self, message: ParsedMessage, classification: Classification) -> OperationResult:
        signal = classification.order
        if classification.category not in {"routine", "relevant"}:
            return OperationResult(True, "order-category-blocked")
        if not signal or not signal.is_order_event:
            return OperationResult(True, "order-not-detected")
        result = self.assistant_bridge.process_order_event(
            message=message, order_data=asdict(signal), source_category=classification.category
        )
        if not result.ok:
            self.log.error("Bestellmonitor fuer %s fehlgeschlagen: %s", message.stable_key, result.detail)
            self.notifier.critical(f"Bestellmonitor-Fehler: {message.subject} — {result.detail[:500]}")
        return result

    def _route(self, message: ParsedMessage, classification: Classification, source_folder: str, force: bool = False) -> OperationResult:
        if self.dry_run:
            return self._dry_route(message, classification)
        self.storage.upsert_message(message, classification, status="classified")
        self._process_order_event(message, classification)
        thresholds = self.config.thresholds

        if classification.category == "spam":
            destination = self.config.folders.spam if force or classification.confidence >= thresholds.spam else self.config.folders.review
            return self._move(message, source_folder, destination, "spam" if destination == self.config.folders.spam else "review")

        if classification.category == "routine":
            destination = self.config.folders.routine if force or classification.confidence >= thresholds.routine else self.config.folders.review
            if destination == self.config.folders.review:
                return self._move(message, source_folder, destination, "review")
            invoice_result = self.invoices.process(message, classification)
            if invoice_result.status == "invoice-review-required":
                return self._move(
                    message, source_folder, self.config.folders.review, "review", invoice_result.detail
                )
            if not invoice_result.ok:
                detail = "Rechnungsarchivierung fehlgeschlagen: " + (invoice_result.detail or invoice_result.status)
                return self._move(message, source_folder, self.config.folders.error, "error", detail)
            if invoice_result.status in {"invoice-archived-review-required", "invoice-duplicate-review-required"}:
                return self._move(message, source_folder, self.config.folders.review, "review", invoice_result.detail)
            invoice_detail = (
                invoice_result.detail
                if invoice_result.status in {
                    "invoice-archived", "invoice-duplicate", "would-archive-invoice",
                    "invoice-archived-metadata-review", "invoice-duplicate-metadata-review",
                }
                else ""
            )
            return self._move(message, source_folder, destination, "routine", invoice_detail)

        if classification.category == "appointment":
            trusted_sender = self.rules.is_trusted_sender(
                message, self.config.calendar.trust_feedback_count
            )
            calendar_result = self.calendar.process(
                message, classification, trusted_sender=trusted_sender
            )
            can_forward = (
                not self.config.calendar.approval_required
                and classification.forward
                and (force or classification.confidence >= thresholds.relevant)
                and classification.importance >= thresholds.min_forward_importance
            )
            if not calendar_result.ok:
                detail = f"Terminverarbeitung fehlgeschlagen: {calendar_result.detail or calendar_result.status}"
                return self._move(message, source_folder, self.config.folders.error, "error", detail)
            forward_result = self._forward_once(message, classification) if can_forward else OperationResult(True, "not-forwarded")
            if not forward_result.ok:
                detail = f"Weiterleitung fehlgeschlagen: {forward_result.detail}"
                self.notifier.critical(f"Mail-Agent Fehler: {message.subject} — {detail[:500]}")
                failure_status = (
                    "delivery-uncertain"
                    if forward_result.status == "delivery-uncertain"
                    else "error"
                )
                return self._move(
                    message, source_folder, self.config.folders.error, failure_status, detail
                )
            if calendar_result.status in {"created", "duplicate"}:
                destination = self.config.folders.forwarded if can_forward else self.config.folders.routine
                status = "forwarded" if can_forward else "routine"
            else:
                destination = self.config.folders.appointment_review
                status = "appointment-review"
            detail = calendar_result.detail
            return self._move(message, source_folder, destination, status, detail, forwarded=can_forward)

        if classification.category == "relevant":
            can_forward = (
                classification.forward
                and (force or classification.confidence >= thresholds.relevant)
                and classification.importance >= thresholds.min_forward_importance
            )
            if not can_forward:
                return self._move(message, source_folder, self.config.folders.review, "review")
            forward_result = self._forward_once(message, classification)
            if not forward_result.ok:
                detail = f"Weiterleitung fehlgeschlagen: {forward_result.detail}"
                self.notifier.critical(f"Mail-Agent Fehler: {message.subject} — {detail[:500]}")
                failure_status = (
                    "delivery-uncertain"
                    if forward_result.status == "delivery-uncertain"
                    else "error"
                )
                return self._move(
                    message, source_folder, self.config.folders.error, failure_status, detail
                )
            return self._move(
                message,
                source_folder,
                self.config.folders.forwarded,
                "forwarded",
                forwarded=True,
            )

        return self._move(message, source_folder, self.config.folders.review, "review")

    def _handle_calendar_command_mail(
        self, message: ParsedMessage, source_folder: str
    ) -> OperationResult | None:
        settings = self.tool_settings.mail.calendar_mail
        prefix = settings.subject_prefix.strip()
        if not settings.enabled or not prefix or not message.subject.casefold().startswith(prefix.casefold()):
            return None
        classification = self.classifier.classify(message, force_not_spam=True)
        result = self.calendar.process_command_mail(message, classification)
        if result is None:
            return None
        if self.dry_run:
            destination = self.config.folders.routine if result.ok else self.config.folders.review
            return OperationResult(
                True,
                "dry-run-calendar-command",
                result.detail or result.status,
                destination=destination,
                path=result.path,
            )
        self.storage.upsert_message(message, classification, status="calendar-command")
        review_statuses = {
            "calendar-command-sender-rejected",
            "calendar-command-no-event",
            "calendar-command-invalid-event",
        }
        if result.status in review_statuses:
            return self._move(message, source_folder, self.config.folders.review, "review", result.detail)
        if not result.ok:
            return self._move(message, source_folder, self.config.folders.error, "error", result.detail)
        return self._move(message, source_folder, self.config.folders.routine, "routine", result.detail)

    def _handle_calendar_approval_reply(
        self, message: ParsedMessage, source_folder: str
    ) -> OperationResult | None:
        result = self.calendar.handle_approval_reply(message)
        if result is None:
            return None
        classification = Classification(
            "routine",
            1.0,
            2,
            False,
            "Antwort auf eine vom Mail-Agenten erzeugte Terminfreigabe",
            summary=result.detail,
            source="calendar-approval",
        )
        if self.dry_run:
            destination = self.config.folders.routine if result.ok else self.config.folders.review
            return OperationResult(
                True,
                "dry-run",
                f"Wuerde Terminfreigabe behandeln: {result.status} - {result.detail}",
                destination=destination,
                path=result.path,
            )
        self.storage.upsert_message(message, classification, status="calendar-approval-reply")
        if result.ok:
            return self._move(
                message, source_folder, self.config.folders.routine, "routine", result.detail
            )
        destination = (
            self.config.folders.error
            if result.status in {"approval-create-failed", "approval-event-invalid"}
            else self.config.folders.review
        )
        status = "error" if destination == self.config.folders.error else "review"
        return self._move(message, source_folder, destination, status, result.detail)

    def _forward_once(self, message: ParsedMessage, classification: Classification) -> OperationResult:
        existing = self.storage.get_message(message.stable_key)
        if existing and existing["forwarded_at"]:
            return OperationResult(True, "already-forwarded")
        self.storage.update_status(message.stable_key, "forwarding")
        result = self.forwarder.forward(message, classification)
        self.storage.record_action(message.stable_key, "forward", message.source_folder, self.config.mailbox.forward_to, result.ok, result.detail)
        if result.ok:
            self.storage.update_status(message.stable_key, "forwarded-awaiting-move", forwarded=True)
        elif result.status == "delivery-uncertain":
            # SMTP may already have accepted the message. Never schedule an automatic
            # retry for this state, otherwise the recipient may receive duplicates.
            self.storage.update_status(
                message.stable_key, "delivery-uncertain", error=result.detail
            )
        else:
            self.storage.update_status(
                message.stable_key, "forward-failed", error=result.detail, increment_retry=True
            )
        return result

    def _move(
        self,
        message: ParsedMessage,
        source: str,
        destination: str,
        final_status: str,
        detail: str = "",
        forwarded: bool = False,
    ) -> OperationResult:
        with self._phase("mail_move"):
            result = self.himalaya.move_message(source, destination, message.mailbox_id)
        with self._phase("database_write"):
            self.storage.record_action(message.stable_key, "move", source, destination, result.ok, result.detail)
        if result.ok:
            with self._phase("database_write"):
                self.storage.update_status(
                    message.stable_key,
                    final_status,
                    destination=destination,
                    forwarded=forwarded,
                    error=detail if final_status == "error" else "",
                )
            result.status = final_status
            result.detail = detail or result.detail
        else:
            combined_error = result.detail
            if detail:
                combined_error = f"{detail} | Verschieben fehlgeschlagen: {result.detail}"
            with self._phase("database_write"):
                self.storage.update_status(
                    message.stable_key,
                    "move-failed",
                    destination=destination,
                    error=combined_error,
                    forwarded=forwarded,
                    increment_retry=True,
                )
        return result

    def _status_for_destination(self, destination: str) -> str:
        mapping = {
            self.config.folders.spam: "spam",
            self.config.folders.routine: "routine",
            self.config.folders.forwarded: "forwarded",
            self.config.folders.review: "review",
            self.config.folders.appointment_review: "appointment-review",
            self.config.folders.error: "error",
        }
        return mapping.get(destination, "review")

    def _retry_pending_move(self, message: ParsedMessage, source: str, existing: Any) -> OperationResult | None:
        if not existing:
            return None
        row = existing
        if row["status"] != "move-failed" or not row["destination_folder"]:
            return None
        destination = str(row["destination_folder"])
        final_status = self._status_for_destination(destination)
        detail = str(row["last_error"] or "") if final_status == "error" else ""
        return self._move(
            message,
            source,
            destination,
            final_status,
            detail,
            forwarded=bool(row["forwarded_at"]),
        )

    def _dry_route(self, message: ParsedMessage, classification: Classification) -> OperationResult:
        thresholds = self.config.thresholds
        detail = f"Wuerde als {classification.category} behandeln"
        order_result = self._process_order_event(message, classification)
        if order_result.status == "would-process-order":
            detail += "; Bestellereignis wuerde in Deck verarbeitet"
        path = ""
        if classification.category == "spam":
            dest = self.config.folders.spam if classification.confidence >= thresholds.spam else self.config.folders.review
        elif classification.category == "routine":
            dest = self.config.folders.routine if classification.confidence >= thresholds.routine else self.config.folders.review
            if dest == self.config.folders.routine:
                invoice_result = self.invoices.process(message, classification)
                if invoice_result.status == "invoice-review-required":
                    dest = self.config.folders.review
                    detail += "; Rechnungs-PDF braucht Pruefung: " + invoice_result.detail
                elif not invoice_result.ok:
                    dest = self.config.folders.error
                    detail += "; Rechnungsarchivierung wuerde fehlschlagen: " + (invoice_result.detail or invoice_result.status)
                elif invoice_result.status in {
                    "would-archive-invoice", "invoice-archived-review-required",
                    "invoice-duplicate-review-required", "invoice-archived-metadata-review",
                    "invoice-duplicate-metadata-review",
                }:
                    detail += "; " + invoice_result.detail
                    path = invoice_result.path
                    if invoice_result.status in {"invoice-archived-review-required", "invoice-duplicate-review-required"}:
                        dest = self.config.folders.review
        elif classification.category == "relevant":
            dest = self.config.folders.forwarded if (
                classification.forward
                and classification.confidence >= thresholds.relevant
                and classification.importance >= thresholds.min_forward_importance
            ) else self.config.folders.review
        elif classification.category == "appointment":
            trusted_sender = self.rules.is_trusted_sender(
                message, self.config.calendar.trust_feedback_count
            )
            calendar_result = self.calendar.process(
                message, classification, trusted_sender=trusted_sender
            )
            dest = self.config.folders.appointment_review if calendar_result.ok else self.config.folders.error
            detail += "; " + (calendar_result.detail or calendar_result.status)
            path = calendar_result.path
        else:
            dest = self.config.folders.review
        return OperationResult(True, "dry-run", detail, destination=dest, path=path)

    def import_order_snapshots(self, *, limit: int = 500) -> dict[str, Any]:
        root = self.config.runtime.database.parent / "search_documents"
        paths = sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:max(1, limit)]
        messages: list[ParsedMessage] = []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                attachments = []
                from .models import AttachmentInfo
                for item in meta.get("attachments", []) if isinstance(meta.get("attachments"), list) else []:
                    if isinstance(item, dict):
                        attachments.append(AttachmentInfo(str(item.get("filename") or ""), str(item.get("content_type") or "application/octet-stream"), int(item.get("size") or 0)))
                messages.append(ParsedMessage(
                    stable_key=str(data.get("stable_key") or path.stem), mailbox_id="snapshot",
                    source_folder=str(meta.get("source_folder") or "snapshot"), raw=b"",
                    message_id=str(data.get("message_id") or ""), subject=str(data.get("subject") or ""),
                    sender_name=str(data.get("sender_name") or ""), sender_addr=str(data.get("sender_addr") or ""),
                    date=str(meta.get("date") or ""), received_at=str(meta.get("received_at") or meta.get("date") or ""),
                    body_text=str(data.get("body_text") or ""), attachments=attachments,
                ))
            except Exception as exc:
                self.log.warning("Bestell-Backfill ueberspringt %s: %s", path, exc)
        processed = 0
        detected = 0
        results: list[dict[str, Any]] = []
        batch = max(1, self.config.ollama.batch_prefetch if self.config.ollama.batch_enabled else 1)
        for offset in range(0, len(messages), batch):
            group = messages[offset:offset + batch]
            classifications = self.classifier.classify_many(group)
            for message, classification in zip(group, classifications, strict=True):
                processed += 1
                if classification.order and classification.order.is_order_event:
                    detected += 1
                    result = self._process_order_event(message, classification)
                    results.append({"stable_key": message.stable_key, "subject": message.subject, "status": result.status, "ok": result.ok})
        return {"ok": all(item["ok"] for item in results), "processed": processed, "detected": detected, "results": results}

    def doctor(self) -> dict[str, object]:
        import shutil

        checks: dict[str, object] = {}
        binary = shutil.which(self.config.mailbox.himalaya_binary)
        checks["himalaya"] = {"ok": bool(binary), "path": binary or "nicht gefunden"}
        if binary:
            folders, error = self.himalaya.list_folders()
            existing = {folder.casefold() for folder in folders}
            missing = [folder for folder in self._required_folders() if folder.casefold() not in existing]
            checks["folders"] = {"ok": not error and not missing, "count": len(folders), "error": error, "missing": missing}
            missing_sources = [
                folder for folder in self.config.mailbox.all_source_folders()
                if folder.casefold() not in existing
            ]
            checks["mail_sources"] = {
                "ok": not error and not missing_sources,
                "primary": self.config.mailbox.source_folder,
                "quarantine": list(self.config.mailbox.quarantine_folders),
                "quarantine_rescue_only": self.config.mailbox.quarantine_rescue_only,
                "quarantine_max_per_run": self.config.mailbox.quarantine_max_per_run,
                "missing": missing_sources,
            }
            forwarding_ok, forwarding_detail = self.himalaya.forwarding_safety()
            checks["forwarding"] = {
                "ok": bool(not self.config.forwarding.enabled or forwarding_ok),
                "enabled": self.config.forwarding.enabled,
                "detail": forwarding_detail,
            }
        else:
            checks["folders"] = {"ok": False, "error": "Himalaya fehlt"}
            checks["mail_sources"] = {
                "ok": False,
                "primary": self.config.mailbox.source_folder,
                "quarantine": list(self.config.mailbox.quarantine_folders),
                "missing": self.config.mailbox.all_source_folders(),
                "error": "Himalaya fehlt",
            }
            checks["forwarding"] = {"ok": False, "detail": "Himalaya fehlt"}
        ollama_ok, ollama_detail = self.classifier.health()
        checks["ollama"] = {"ok": ollama_ok, "detail": ollama_detail}
        nextcloud_health = self.nextcloud.health(live=self.config.nextcloud.enabled)
        checks["nextcloud"] = nextcloud_health
        checks["antivirus"] = self.antivirus.doctor(live_scan=True)

        invoice_enabled = self.tool_settings.mail.invoices.enabled
        invoice_health = self.assistant_bridge.health(resource_id=self.tool_settings.mail.invoices.resource_id)
        checks["invoices"] = {
            "ok": bool(not invoice_enabled or invoice_health.ok),
            "enabled": invoice_enabled,
            "folder": self.tool_settings.mail.invoices.folder,
            "resource_id": self.tool_settings.mail.invoices.resource_id,
            "detail": invoice_health.detail if invoice_enabled else "Rechnungsarchivierung ist deaktiviert",
        }
        try:
            assistant = self.assistant_bridge._open()
            try:
                checks["deck_orders"] = assistant.deck_orders_status(live=True)
            finally:
                assistant.close()
        except Exception as exc:
            checks["deck_orders"] = {"ok": False, "detail": str(exc)}

        checks["calendar_command_mail"] = {
            "ok": bool(
                not self.tool_settings.mail.calendar_mail.enabled
                or (
                    self.tool_settings.mail.calendar_mail.sender_addresses
                    and self.tool_settings.mail.calendar_mail.calendar_resource_id
                )
            ),
            "enabled": self.tool_settings.mail.calendar_mail.enabled,
            "subject_prefix": self.tool_settings.mail.calendar_mail.subject_prefix,
            "calendar_resource_id": self.tool_settings.mail.calendar_mail.calendar_resource_id,
            "allowed_senders": list(self.tool_settings.mail.calendar_mail.sender_addresses),
        }
        calendar_ok, backend, calendar_detail = self.calendar.health(
            nextcloud_health=nextcloud_health if self.config.nextcloud.enabled else None
        )
        checks["calendar"] = {"ok": calendar_ok, "backend": backend, "detail": calendar_detail}
        learning_items = [item.to_dict() for item in self.learning_folders.list()]
        checks["learning"] = {
            "ok": True,
            "registry": str(self.learning_folders.path),
            "active_folders": [item["folder"] for item in learning_items if item.get("active")],
            "mixed_senders": len(self.storage.mixed_senders(limit=10000)),
            "pattern_conflicts": len(self.storage.pattern_conflicts(limit=10000)),
        }
        checks["database"] = {"ok": self.config.runtime.database.exists(), "path": str(self.config.runtime.database)}
        checks["config"] = {"ok": True, "path": str(self.config.path)}
        return checks

    def status(self) -> dict[str, object]:
        return {
            "counts": self.storage.status_counts(),
            "recent_errors": self.storage.recent_errors(),
            "feedback": self.storage.feedback_summary(),
            "learning": {
                "folders": [item.to_dict() for item in self.learning_folders.list(active_only=True)],
                "mixed_senders": len(self.storage.mixed_senders(limit=10000)),
                "pattern_conflicts": len(self.storage.pattern_conflicts(limit=10000)),
            },
            "nextcloud": self.nextcloud.health(live=False),
            "invoices": self.storage.invoice_summary(),
            "calendar_approvals": self.storage.approval_summary(),
            "database": str(self.config.runtime.database),
        }
