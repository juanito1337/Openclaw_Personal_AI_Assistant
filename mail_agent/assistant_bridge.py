from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from personal_assistant.bootstrap import create_personal_assistant
from personal_assistant.config import load_config as load_assistant_config
from personal_assistant.service import PersonalAssistant

from .models import OperationResult, ParsedMessage
from .utils import atomic_write_bytes


class PersonalAssistantActionBridge:
    """Narrow bridge from the stable mail pipeline into ActionPlan/Outbox.

    The mail agent never receives a generic PersonalAssistant handle. It can only
    request the two explicitly supported operations below. Policy, resource
    permissions, idempotency and audit remain enforced by the assistant core.
    """

    def __init__(self, *, dry_run: bool = False, config_path: Path | None = None) -> None:
        self.dry_run = dry_run
        self.config_path = Path(config_path).expanduser().resolve() if config_path is not None else None

    def _open(self) -> PersonalAssistant:
        # With no explicit path, load_config honors PERSONAL_ASSISTANT_CONFIG and
        # otherwise falls back to the normal workspace configuration.
        return create_personal_assistant(load_assistant_config(self.config_path))

    def health(self, *, resource_id: str = "nextcloud-files-main") -> OperationResult:
        assistant = self._open()
        try:
            resource = assistant.registry.get(resource_id)
            if not resource.enabled:
                return OperationResult(False, "resource-disabled", f"Ressource {resource_id} ist deaktiviert")
            if "create" not in resource.permissions:
                return OperationResult(False, "resource-create-denied", f"Ressource {resource_id} besitzt kein create-Recht")
            health = assistant.nextcloud_discovery.root_health()
            return OperationResult(bool(health.get("ok")), "ok" if health.get("ok") else "nextcloud-unavailable", str(health))
        except Exception as exc:
            return OperationResult(False, "assistant-bridge-unavailable", str(exc))
        finally:
            assistant.close()

    def archive_invoice(
        self,
        *,
        message: ParsedMessage,
        attachment_hash: str,
        data: bytes,
        remote_path: str,
        content_type: str = "application/pdf",
        resource_id: str = "nextcloud-files-main",
    ) -> OperationResult:
        key = f"invoice-upload:{attachment_hash}:{remote_path}"
        if self.dry_run:
            return OperationResult(
                True,
                "would-archive-invoice",
                "Wuerde Rechnungs-PDF ueber ActionPlan create-only in Nextcloud archivieren",
                destination=resource_id,
                path=remote_path,
            )

        assistant = self._open()
        payload_path: Path | None = None
        completed = False
        try:
            payload_dir = assistant.config.runtime.database.parent / "action_payloads" / "invoices"
            payload_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(payload_dir, 0o700)
            payload_path = payload_dir / f"{attachment_hash}.pdf"
            if not payload_path.exists():
                atomic_write_bytes(payload_path, data)
                os.chmod(payload_path, 0o600)

            plan = assistant.actions.plan(
                "files.create",
                resource_id,
                {
                    "local_path": str(payload_path),
                    "path": remote_path,
                    "content_type": content_type,
                    "overwrite": False,
                    "source_message": message.stable_key,
                    "attachment_sha256": attachment_hash,
                },
                idempotency_key=key,
            )
            if plan.status == "completed":
                return OperationResult(
                    True,
                    "invoice-duplicate",
                    "Rechnungs-PDF war bereits ueber ActionPlan archiviert",
                    destination=resource_id,
                    path=remote_path,
                )
            if plan.status != "approved":
                return OperationResult(
                    False,
                    "invoice-action-not-approved",
                    f"Rechnungs-ActionPlan ist nicht ausfuehrbar: {plan.status}",
                    destination=resource_id,
                    path=remote_path,
                )
            result = assistant.actions.execute(plan.id)
            if result.status != "completed":
                return OperationResult(
                    False,
                    "invoice-action-failed",
                    result.error or f"Rechnungs-ActionPlan endete mit {result.status}",
                    destination=resource_id,
                    path=remote_path,
                )
            completed = True
            return OperationResult(
                True,
                "invoice-archived",
                "Rechnungs-PDF ueber ActionPlan in Nextcloud archiviert",
                destination=resource_id,
                path=remote_path,
            )
        except Exception as exc:
            return OperationResult(False, "invoice-action-failed", str(exc), path=remote_path)
        finally:
            try:
                if completed and payload_path and payload_path.exists():
                    payload_path.unlink()
            except OSError:
                pass
            assistant.close()


    def sync_invoice_register(
        self,
        *,
        data: bytes,
        year: int,
        remote_path: str,
        resource_id: str = "nextcloud-files-main",
    ) -> OperationResult:
        digest = hashlib.sha256(data).hexdigest()
        key = f"invoice-register-sync:{year}:{digest}:{remote_path}"
        if self.dry_run:
            return OperationResult(
                True,
                "would-sync-invoice-register",
                "Wuerde das Jahresregister im Nextcloud-Jahresordner aktualisieren",
                destination=resource_id,
                path=remote_path,
            )
        assistant = self._open()
        payload_path: Path | None = None
        try:
            payload_dir = assistant.config.runtime.database.parent / "action_payloads" / "invoice-register"
            payload_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(payload_dir, 0o700)
            payload_path = payload_dir / f"Rechnungen_{int(year):04d}_{digest[:12]}.csv"
            if not payload_path.exists():
                atomic_write_bytes(payload_path, data)
                os.chmod(payload_path, 0o600)
            plan = assistant.actions.plan(
                "files.create",
                resource_id,
                {
                    "local_path": str(payload_path),
                    "path": remote_path,
                    "content_type": "text/csv; charset=utf-8",
                    "overwrite": True,
                    "sha256": digest,
                    "managed_invoice_register": True,
                    "year": int(year),
                },
                idempotency_key=key,
            )
            if plan.status == "failed":
                plan = assistant.actions.retry_managed_invoice_register(plan.id)
            if plan.status not in {"approved", "completed"}:
                return OperationResult(False, "invoice-register-not-approved", f"ActionPlan ist nicht ausfuehrbar: {plan.status}", destination=resource_id, path=remote_path)
            result, duplicate = assistant.actions.execute_workspace(plan.id)
            if result.status != "completed":
                return OperationResult(False, "invoice-register-sync-failed", result.error or f"ActionPlan endete mit {result.status}", destination=resource_id, path=remote_path)
            status = "invoice-register-unchanged" if duplicate else "invoice-register-synced"
            detail = "Jahresregister war bereits aktuell" if duplicate else "Jahresregister im Nextcloud-Jahresordner aktualisiert"
            return OperationResult(True, status, detail, destination=resource_id, path=remote_path)
        except Exception as exc:
            return OperationResult(False, "invoice-register-sync-failed", str(exc), path=remote_path)
        finally:
            try:
                if payload_path and payload_path.exists():
                    payload_path.unlink()
            except OSError:
                pass
            assistant.close()

    # Compatibility alias for older callers. R26 always updates the fixed
    # yearly Nextcloud register instead of creating timestamped exports.
    export_invoice_register = sync_invoice_register

    def read_invoice_pdf(
        self,
        *,
        remote_path: str,
        allowed_folder: str,
        resource_id: str = "nextcloud-files-main",
    ) -> bytes:
        if self.dry_run:
            raise RuntimeError("PDF-Download ist im Bridge-Dry-Run nicht verfuegbar")
        assistant = self._open()
        try:
            resource = assistant.registry.get(resource_id)
            if not resource.enabled:
                raise PermissionError(f"Ressource {resource_id} ist deaktiviert")
            if "read" not in resource.permissions:
                raise PermissionError(f"Ressource {resource_id} besitzt kein read-Recht")
            clean_path = assistant.nextcloud_files.clean_path(remote_path)
            clean_folder = assistant.nextcloud_files.clean_path(allowed_folder)
            if clean_path != clean_folder and not clean_path.startswith(clean_folder.rstrip("/") + "/"):
                raise PermissionError("Rechnungs-Backfill darf nur innerhalb des konfigurierten Rechnungsordners lesen")
            data = assistant.nextcloud_files.download(clean_path)
            if not data.startswith(b"%PDF-"):
                raise ValueError("Archivdatei ist kein gueltiges PDF")
            return data
        finally:
            assistant.close()

    def process_order_event(
        self, *, message: ParsedMessage, order_data: dict[str, Any], source_category: str
    ) -> OperationResult:
        if self.dry_run:
            assistant = self._open()
            try:
                result = assistant.orders_process_event(
                    order_data, stable_key=message.stable_key, subject=message.subject,
                    sender=message.sender_addr, received_at=message.received_at or message.date,
                    source_category=source_category, dry_run=True,
                )
                return OperationResult(True, str(result.get("status") or "would-process-order"), str(result))
            finally:
                assistant.close()
        assistant = self._open()
        try:
            result = assistant.orders_process_event(
                order_data, stable_key=message.stable_key, subject=message.subject,
                sender=message.sender_addr, received_at=message.received_at or message.date,
                source_category=source_category, dry_run=False,
            )
            return OperationResult(bool(result.get("ok")), str(result.get("status") or "order-processed"), str(result), destination="nextcloud-deck-orders")
        except Exception as exc:
            return OperationResult(False, "order-tool-failed", str(exc), destination="nextcloud-deck-orders")
        finally:
            assistant.close()

    def create_calendar_event(
        self,
        *,
        message: ParsedMessage,
        resource_id: str,
        ics: str,
        uid: str,
        fingerprint: str,
        sender: str,
    ) -> OperationResult:
        key = f"trusted-mail-event:{resource_id}:{fingerprint}"
        if self.dry_run:
            return OperationResult(
                True,
                "would-create-command-event",
                f"Wuerde Termin aus autorisierter Befehlsmail in {resource_id} anlegen",
                destination=resource_id,
            )

        assistant = self._open()
        try:
            plan = assistant.actions.plan(
                "calendar.create",
                resource_id,
                {
                    "ics": ics,
                    "uid": uid,
                    "source_message": message.stable_key,
                    "command_sender": sender,
                },
                idempotency_key=key,
            )
            if plan.status == "completed":
                return OperationResult(
                    True,
                    "duplicate",
                    "Termin aus Befehlsmail war bereits eingetragen",
                    destination=resource_id,
                )
            if plan.status == "proposed":
                plan = assistant.actions.approve_trusted_command(
                    plan.id,
                    actor=f"trusted-mail:{sender}",
                    evidence={
                        "source_message": message.stable_key,
                        "subject_hash": hashlib.sha256(message.subject.encode("utf-8", errors="replace")).hexdigest(),
                    },
                )
            if plan.status != "approved":
                return OperationResult(
                    False,
                    "calendar-command-not-approved",
                    f"Termin-ActionPlan ist nicht ausfuehrbar: {plan.status}",
                    destination=resource_id,
                )
            result = assistant.actions.execute(plan.id)
            if result.status != "completed":
                return OperationResult(
                    False,
                    "calendar-command-failed",
                    result.error or f"Termin-ActionPlan endete mit {result.status}",
                    destination=resource_id,
                )
            return OperationResult(
                True,
                "created",
                "Termin aus autorisierter Befehlsmail in Nextcloud eingetragen",
                destination=resource_id,
            )
        except Exception as exc:
            return OperationResult(False, "calendar-command-failed", str(exc), destination=resource_id)
        finally:
            assistant.close()
