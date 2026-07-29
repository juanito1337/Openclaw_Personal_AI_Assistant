from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import asdict
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from mail_agent.command import CommandRunner
from mail_agent.config import load_config as load_mail_config
from mail_agent.himalaya import HimalayaClient
from mail_agent.models import Envelope, ParsedMessage
from mail_agent.parser import parse_eml
from mail_agent.utils import clean_single_line

from .policy import PolicyEngine
from .registry import ResourceRegistry
from .storage import AssistantStorage
from .tool_settings import MailMoveToolSettings


class MailMoveService:
    def __init__(self, settings: MailMoveToolSettings, registry: ResourceRegistry, policy: PolicyEngine,
                 storage: AssistantStorage, client: HimalayaClient | None = None) -> None:
        self.settings = settings
        self.registry = registry
        self.policy = policy
        self.storage = storage
        self._client_override = client

    def _client(self) -> HimalayaClient:
        if self._client_override is not None:
            return self._client_override
        config = load_mail_config()
        return HimalayaClient(config, CommandRunner(), dry_run=False)

    @staticmethod
    def _folder_map(folders: list[str]) -> dict[str, str]:
        return {name.strip().casefold(): name.strip() for name in folders if name.strip()}

    def _resource(self):
        return self.registry.get(self.settings.resource_id)

    def status(self) -> dict[str, Any]:
        resource = self._resource()
        try:
            folders, error = self._client().list_folders()
        except Exception as exc:
            folders, error = [], str(exc)
        ok = bool(self.settings.enabled and not error and "read" in resource.permissions and "move" in resource.permissions)
        return {
            "ok": ok, "enabled": self.settings.enabled, "resource_id": self.settings.resource_id,
            "resource_permissions": list(resource.permissions), "folders": folders, "folder_error": error,
            "max_batch": self.settings.max_batch, "denied_destinations": list(self.settings.denied_destinations),
            "denied_sources": list(self.settings.denied_sources),
            "delete_allowed": False, "expunge_allowed": False, "folder_changes_allowed": False,
        }

    def list_messages(self, folder: str, *, limit: int = 50) -> dict[str, Any]:
        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Verschiebewerkzeug ist deaktiviert")
        decision = self.policy.decide(self.settings.resource_id, "mail.read", {"folder": folder})
        if not decision.allowed:
            raise PermissionError(decision.reason)
        folders, error = self._client().list_folders()
        if error:
            raise RuntimeError(error)
        fmap = self._folder_map(folders)
        resolved = fmap.get(folder.strip().casefold())
        if not resolved:
            raise ValueError(f"Mailordner nicht gefunden: {folder}")
        envelopes, error = self._client().list_envelopes(resolved, limit=max(1, min(int(limit), 200)))
        if error:
            raise RuntimeError(error)
        return {"ok": True, "folder": resolved, "count": len(envelopes), "messages": [asdict(item) for item in envelopes]}

    def search_messages(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        """Search envelope metadata in every readable IMAP folder, including review folders."""
        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Lesewerkzeug ist deaktiviert")
        needle = str(query or "").strip().casefold()
        if not needle:
            raise ValueError("Suchbegriff darf nicht leer sein")
        decision = self.policy.decide(self.settings.resource_id, "mail.read", {"query": query})
        if not decision.allowed:
            raise PermissionError(decision.reason)
        client = self._client()
        folders, error = client.list_folders()
        if error:
            raise RuntimeError(error)
        matches: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        per_folder = max(1, min(200, int(limit)))
        for folder in folders:
            envelopes, error = client.list_envelopes(folder, limit=per_folder)
            if error:
                errors.append({"folder": folder, "error": error})
                continue
            for envelope in envelopes:
                haystack = "\n".join((
                    envelope.subject, envelope.sender_name, envelope.sender_addr,
                    envelope.date, envelope.received_at,
                )).casefold()
                if needle in haystack:
                    item = asdict(envelope)
                    item["folder"] = folder
                    matches.append(item)
        matches.sort(key=lambda item: str(item.get("received_at") or item.get("date") or ""), reverse=True)
        return {
            "ok": True, "query": query, "count": min(len(matches), per_folder),
            "messages": matches[:per_folder], "folder_errors": errors,
        }

    def read_message(
        self,
        folder: str,
        message_id: str,
        *,
        expected_subject: str = "",
    ) -> ParsedMessage:
        """Read exactly one selected mail without exposing its body in the CLI output."""
        decision = self.policy.decide(
            self.settings.resource_id,
            "mail.read",
            {"folder": folder, "message_id": str(message_id)},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        client = self._client()
        folders, error = client.list_folders()
        if error:
            raise RuntimeError(error)
        fmap = self._folder_map(folders)
        resolved = fmap.get(folder.strip().casefold())
        if not resolved:
            raise ValueError(f"Mailordner nicht gefunden: {folder}")
        envelopes, error = client.list_envelopes(resolved, limit=200)
        if error:
            raise RuntimeError(error)
        envelope = next((item for item in envelopes if str(item.mailbox_id) == str(message_id)), None)
        if envelope is not None and expected_subject and envelope.subject.strip() != expected_subject.strip():
            raise PermissionError("Betreff stimmt nicht mit der erwarteten Mail ueberein")
        if envelope is None:
            envelope = Envelope(mailbox_id=str(message_id))
        with tempfile.TemporaryDirectory(prefix="openclaw-contact-mail-") as folder_path:
            os.chmod(folder_path, 0o700)
            destination = Path(folder_path) / "message.eml"
            result = client.export_message(resolved, str(message_id), destination)
            if not result.ok or not destination.is_file():
                raise RuntimeError(result.detail or "Mail konnte nicht exportiert werden")
            os.chmod(destination, 0o600)
            raw = destination.read_bytes()
        parsed = parse_eml(raw, envelope, resolved)
        if expected_subject and parsed.subject.strip() != expected_subject.strip():
            raise PermissionError("Betreff stimmt nicht mit der erwarteten Mail ueberein")
        return parsed

    def read(self, folder: str, message_id: str, *, expected_subject: str = "") -> dict[str, Any]:
        message = self.read_message(folder, message_id, expected_subject=expected_subject)
        return {
            "ok": True,
            "message": {
                "mailbox_id": message.mailbox_id,
                "folder": message.source_folder,
                "message_id": message.message_id,
                "subject": message.subject,
                "sender_name": message.sender_name,
                "sender_addr": message.sender_addr,
                "recipients": list(message.recipients),
                "date": message.date,
                "received_at": message.received_at,
                "body_text": message.body_text,
                "attachments": [asdict(item) for item in message.attachments],
            },
            "read_only": True,
        }

    def draft_reply(
        self, folder: str, message_id: str, body: str, *, expected_subject: str = ""
    ) -> dict[str, Any]:
        message = self.read_message(folder, message_id, expected_subject=expected_subject)
        recipient = parseaddr(message.sender_addr)[1].strip().casefold()
        if not recipient or "@" not in recipient or any(char in recipient for char in "\r\n"):
            raise ValueError("Absenderadresse der ausgewaehlten Mail ist nicht versandfaehig")
        reply_body = str(body or "").strip()
        if not reply_body:
            raise ValueError("Antwortentwurf darf nicht leer sein")
        subject = message.subject.strip()
        if not subject.casefold().startswith("re:"):
            subject = f"Re: {subject}"
        subject = clean_single_line(subject, 900)
        payload = {
            "folder": message.source_folder,
            "mailbox_id": message.mailbox_id,
            "source_message_id": message.message_id,
            "expected_subject": message.subject,
            "recipient": recipient,
            "subject": subject,
            "body": reply_body,
        }
        decision = self.policy.decide(self.settings.resource_id, "mail.send", payload)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        material = "\0".join((
            message.source_folder.casefold(), message.mailbox_id, recipient, subject, reply_body
        ))
        plan = self.storage.create_action(
            idempotency_key="mail-reply:" + hashlib.sha256(material.encode("utf-8")).hexdigest(),
            action_type="mail.send",
            resource_id=self.settings.resource_id,
            payload=payload,
            requires_approval=True,
        )
        self.storage.audit(
            "mail.reply.drafted",
            {"id": plan.id, "folder": message.source_folder, "mailbox_id": message.mailbox_id},
            resource_id=plan.resource_id,
        )
        return {
            "ok": True, "draft_id": plan.id, "status": plan.status,
            "to": recipient, "subject": subject, "body": reply_body,
            "requires_explicit_approval": True,
        }

    def send_reply(self, draft_id: str, *, approved: bool = False) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Versand erfordert die ausdrueckliche Freigabe --yes")
        plan = self.storage.get_action(str(draft_id))
        if plan.action_type != "mail.send" or plan.resource_id != self.settings.resource_id:
            raise PermissionError("ActionPlan ist kein Mail-Antwortentwurf")
        if plan.status == "completed":
            return {"ok": True, "duplicate": True, "draft_id": plan.id, "status": plan.status}
        if plan.status != "proposed" or not plan.requires_approval:
            raise PermissionError(f"Mail-Antwortentwurf ist nicht freigabefaehig: {plan.status}")
        payload = plan.payload
        decision = self.policy.decide(self.settings.resource_id, "mail.send", payload)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        approved_plan = self.storage.update_action(plan.id, "approved")
        self.storage.audit(
            "mail.reply.approved", {"id": plan.id}, resource_id=plan.resource_id, actor="user"
        )
        client = self._client()
        config = getattr(client, "config", None) or load_mail_config()
        headers = [
            f"From: {config.mailbox.from_header}",
            f"To: {clean_single_line(str(payload['recipient']), 500)}",
            f"Subject: {clean_single_line(str(payload['subject']), 900)}",
        ]
        source_message_id = clean_single_line(str(payload.get("source_message_id") or ""), 500)
        if source_message_id:
            headers.extend([f"In-Reply-To: {source_message_id}", f"References: {source_message_id}"])
        template = "\n".join(headers) + "\n\n" + str(payload["body"]).replace("<#", "< #") + "\n"
        result = client.send_template(template)
        if not result.ok:
            failed = self.storage.update_action(approved_plan.id, "failed", result.detail)
            self.storage.audit(
                "mail.reply.failed", {"id": plan.id, "status": result.status, "detail": result.detail},
                resource_id=plan.resource_id,
            )
            return {"ok": False, "draft_id": plan.id, "status": failed.status, "detail": result.detail}
        completed = self.storage.update_action(approved_plan.id, "completed")
        self.storage.audit(
            "mail.reply.sent",
            {"id": plan.id, "recipient": payload["recipient"], "subject": payload["subject"]},
            resource_id=plan.resource_id,
            actor="user-approved-mail-reply",
        )
        return {"ok": True, "duplicate": False, "draft_id": plan.id, "status": completed.status}

    def move(self, *, source: str, destination: str, message_id: str, expected_subject: str = "", dry_run: bool = False) -> dict[str, Any]:
        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Verschiebewerkzeug ist deaktiviert")
        client = self._client()
        folders, error = client.list_folders()
        if error:
            raise RuntimeError(error)
        fmap = self._folder_map(folders)
        source_real = fmap.get(source.strip().casefold())
        destination_real = fmap.get(destination.strip().casefold())
        if not source_real:
            raise ValueError(f"Quellordner nicht gefunden: {source}")
        if not destination_real:
            raise ValueError(f"Zielordner nicht gefunden: {destination}")
        if source_real.casefold() == destination_real.casefold():
            raise ValueError("Quelle und Ziel sind identisch")
        denied_sources = set(self.settings.denied_sources)
        configured_folders = getattr(getattr(client, "config", None), "folders", None)
        if configured_folders is not None:
            denied_sources.update(
                str(value).strip().casefold()
                for value in (
                    getattr(configured_folders, "review", ""),
                    getattr(configured_folders, "appointment_review", ""),
                    getattr(configured_folders, "malware", ""),
                )
                if str(value).strip()
            )
        if source_real.strip().casefold() in denied_sources:
            raise PermissionError(f"Quellordner ist fuer direkte Agentenverschiebungen gesperrt: {source_real}")
        if destination_real.strip().casefold() in set(self.settings.denied_destinations):
            raise PermissionError(f"Zielordner ist fuer direkte Agentenverschiebungen gesperrt: {destination_real}")
        decision = self.policy.decide(self.settings.resource_id, "mail.move", {
            "source": source_real, "destination": destination_real, "message_id": str(message_id),
            "direct_mail_move_tool": True,
        })
        if not decision.allowed:
            raise PermissionError(decision.reason)
        envelopes, error = client.list_envelopes(source_real, limit=200)
        if error:
            raise RuntimeError(error)
        envelope = next((item for item in envelopes if str(item.mailbox_id) == str(message_id)), None)
        if envelope is None:
            key = self._key(source_real, destination_real, message_id, expected_subject)
            rows = [p for p in self.storage.list_actions(limit=200) if p.idempotency_key == key and p.status == "completed"]
            if rows:
                return {"ok": True, "duplicate": True, "source": source_real, "destination": destination_real,
                        "message_id": str(message_id), "action": asdict(rows[0]), "detail": "Bereits erfolgreich verschoben"}
            raise ValueError("Mail-ID wurde im angegebenen Quellordner nicht gefunden")
        if expected_subject and envelope.subject.strip() != expected_subject.strip():
            raise PermissionError("Betreff stimmt nicht mit der erwarteten Mail ueberein")
        payload = {
            "source": source_real, "destination": destination_real, "message_id": str(message_id),
            "subject": envelope.subject, "sender": envelope.sender_addr, "date": envelope.date,
            "direct_mail_move_tool": True,
        }
        key = self._key(source_real, destination_real, message_id, envelope.subject)
        plan = self.storage.create_action(
            idempotency_key=key, action_type="mail.move", resource_id=self.settings.resource_id,
            payload=payload, requires_approval=decision.requires_approval,
        )
        self.storage.audit("action.planned", {"id": plan.id, "type": "mail.move", "status": plan.status}, resource_id=plan.resource_id)
        if plan.status == "completed":
            return {"ok": True, "duplicate": True, "source": source_real, "destination": destination_real,
                    "message_id": str(message_id), "action": asdict(plan)}
        if plan.status == "proposed":
            plan = self.storage.update_action(plan.id, "approved")
            self.storage.audit("action.approved_configured_mail_move_tool", {"id": plan.id}, resource_id=plan.resource_id, actor="agent-mail-move-tool")
        if dry_run:
            return {"ok": True, "dry_run": True, "duplicate": False, "source": source_real,
                    "destination": destination_real, "message": asdict(envelope), "action": asdict(plan)}
        result = client.move_message(source_real, destination_real, str(message_id))
        if not result.ok:
            failed = self.storage.update_action(plan.id, "failed", result.detail)
            self.storage.audit("mail.move.failed", {"id": plan.id, "detail": result.detail}, resource_id=plan.resource_id)
            return {"ok": False, "duplicate": False, "detail": result.detail, "action": asdict(failed)}
        completed = self.storage.update_action(plan.id, "completed", "")
        self.storage.audit("mail.move.completed", payload, resource_id=plan.resource_id, actor="agent-mail-move-tool")
        return {"ok": True, "duplicate": False, "source": source_real, "destination": destination_real,
                "message": asdict(envelope), "action": asdict(completed)}

    @staticmethod
    def _key(source: str, destination: str, message_id: str, subject: str) -> str:
        material = "\0".join((source.casefold(), destination.casefold(), str(message_id), subject.strip()))
        return "mail-move:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
