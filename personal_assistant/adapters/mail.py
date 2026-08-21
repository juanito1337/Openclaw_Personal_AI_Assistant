from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import asdict
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from mail_agent.command import CommandRunner
from mail_agent.config import load_config as load_mail_config
from mail_agent.himalaya import HimalayaClient
from mail_agent.learning import LearningFolderRegistry
from mail_agent.models import Envelope, ParsedMessage
from mail_agent.parser import parse_eml
from mail_agent.utils import clean_single_line

from ..policy import PolicyEngine
from ..registry import ResourceRegistry
from ..storage import AssistantStorage
from ..tool_settings import MailMoveToolSettings


class MailMoveService:
    SERVER_METADATA_FALLBACK_LIMIT = 100
    QUERY_CONNECTORS = frozenset({"and", "or", "und", "oder"})

    def __init__(
        self,
        settings: MailMoveToolSettings,
        registry: ResourceRegistry,
        policy: PolicyEngine,
        storage: AssistantStorage,
        client: HimalayaClient | None = None,
    ) -> None:
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
        ok = bool(
            self.settings.enabled
            and not error
            and "read" in resource.permissions
            and "move" in resource.permissions
        )
        return {
            "ok": ok,
            "enabled": self.settings.enabled,
            "resource_id": self.settings.resource_id,
            "resource_permissions": list(resource.permissions),
            "folders": folders,
            "folder_error": error,
            "max_batch": self.settings.max_batch,
            "denied_destinations": list(self.settings.denied_destinations),
            "denied_sources": list(self.settings.denied_sources),
            "compose_allowed": self.settings.enabled and "forward" in resource.permissions,
            "delete_allowed": False,
            "expunge_allowed": False,
            "folder_changes_allowed": False,
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
        return {
            "ok": True,
            "folder": resolved,
            "count": len(envelopes),
            "messages": [asdict(item) for item in envelopes],
        }

    def search_messages(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        """Search every readable IMAP folder, including review folders."""
        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Lesewerkzeug ist deaktiviert")
        clean_query = " ".join(str(query or "").split())
        if not clean_query:
            raise ValueError("Suchbegriff darf nicht leer sein")
        terms: list[str] = []
        seen_terms: set[str] = set()
        for term in re.findall(r"[\w@.+-]+", clean_query, flags=re.UNICODE):
            folded = term.casefold()
            if (
                folded
                and any(character.isalnum() for character in folded)
                and folded not in self.QUERY_CONNECTORS
                and folded not in seen_terms
            ):
                terms.append(term)
                seen_terms.add(folded)
        if not terms:
            raise ValueError("Suchbegriff enthaelt keine durchsuchbaren Zeichen")
        if len(terms) > 12:
            raise ValueError("Mail-Suche akzeptiert hoechstens 12 eindeutige Suchwoerter")
        decision = self.policy.decide(self.settings.resource_id, "mail.read", {"query": query})
        if not decision.allowed:
            raise PermissionError(decision.reason)
        client = self._client()
        contract_method = getattr(client, "search_contract", None)
        contract_value = contract_method() if callable(contract_method) else {}
        raw_contract = contract_value if isinstance(contract_value, dict) else {}
        contract = {
            "provider": str(raw_contract.get("provider") or "undeclared-connector"),
            "authoritative": raw_contract.get("authoritative") is True,
            "body_search_verified": raw_contract.get("body_search_verified") is True,
            "metadata_fallback": raw_contract.get("metadata_fallback") is True,
            "reason": str(
                raw_contract.get("reason")
                or "connector-did-not-declare-authoritative-search"
            ),
        }
        folders, error = client.list_folders()
        if error:
            raise RuntimeError(error)
        if not folders:
            raise RuntimeError("Mail-Suche hat keine lesbaren IMAP-Ordner gefunden")
        matches: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        metadata_errors: list[dict[str, str]] = []
        limited_folders: list[str] = []
        per_folder = max(1, min(200, int(limit)))
        for folder in folders:
            envelopes, error = client.search_envelopes(folder, terms, limit=per_folder)
            if error:
                errors.append(
                    {"folder": folder, "stage": "provider-query", "error": error}
                )
                continue
            if len(envelopes) >= per_folder:
                limited_folders.append(folder)
            for envelope in envelopes:
                item = asdict(envelope)
                item["folder"] = folder
                item["match_source"] = "server-query"
                item["match_fields"] = ["provider-query"]
                item["body_match_verified"] = bool(contract["body_search_verified"])
                matches.append(item)

        metadata_scanned_folders = 0
        metadata_scan_limit = max(per_folder, self.SERVER_METADATA_FALLBACK_LIMIT)
        if not matches and contract["metadata_fallback"]:
            folded_terms = [term.casefold() for term in terms]
            for folder in folders:
                envelopes, metadata_error = client.list_envelopes(
                    folder,
                    limit=metadata_scan_limit,
                )
                if metadata_error:
                    metadata_errors.append(
                        {
                            "folder": folder,
                            "stage": "metadata-fallback",
                            "error": metadata_error,
                        }
                    )
                    continue
                metadata_scanned_folders += 1
                if len(envelopes) >= metadata_scan_limit:
                    limited_folders.append(folder)
                for envelope in envelopes:
                    fields = {
                        "sender-name": envelope.sender_name.casefold(),
                        "sender-address": envelope.sender_addr.casefold(),
                        "subject": envelope.subject.casefold(),
                    }
                    searchable = "\n".join(fields.values())
                    if not all(term in searchable for term in folded_terms):
                        continue
                    item = asdict(envelope)
                    item["folder"] = folder
                    item["match_source"] = "bounded-envelope-metadata"
                    item["match_fields"] = sorted(
                        field
                        for field, value in fields.items()
                        if any(term in value for term in folded_terms)
                    )
                    item["body_match_verified"] = False
                    matches.append(item)

        if folders and len(errors) == len(folders) and metadata_scanned_folders == 0:
            raise RuntimeError(
                "Mail-Suche ist in allen Ordnern fehlgeschlagen: "
                + "; ".join(f"{item['folder']}: {item['error']}" for item in errors)
            )
        matches.sort(
            key=lambda item: str(item.get("received_at") or item.get("date") or ""),
            reverse=True,
        )
        selected = matches[:per_folder]
        limitations: list[str] = []
        if not contract["authoritative"]:
            limitations.append("server-query-not-authoritative")
        if not contract["body_search_verified"]:
            limitations.append("body-search-not-verified")
        if metadata_scanned_folders:
            limitations.append("bounded-envelope-metadata-only")
        combined_errors = [*errors, *metadata_errors]
        failed_folders = {item["folder"] for item in combined_errors}
        truncated = bool(limited_folders or len(matches) > per_folder)
        complete = bool(contract["authoritative"] and not combined_errors and not truncated)
        return {
            "ok": True,
            "complete": complete,
            "query": query,
            "query_terms": terms,
            "count": len(selected),
            "messages": selected,
            "result_limit": per_folder,
            "results_may_be_truncated": truncated,
            "limited_folders": sorted(set(limited_folders), key=str.casefold),
            "folder_errors": combined_errors,
            "filter_limitations": limitations,
            "search_scope": {
                "provider": contract["provider"],
                "server_query_authoritative": contract["authoritative"],
                "body_search_verified": contract["body_search_verified"],
                "metadata_fields": ["sender-name", "sender-address", "subject"],
                "reason": contract["reason"],
            },
            "metadata_fallback": {
                "used": bool(metadata_scanned_folders),
                "bounded": True,
                "per_folder_limit": metadata_scan_limit,
                "scanned_folders": metadata_scanned_folders,
                "failed_folders": len(metadata_errors),
                "match_count": sum(
                    item.get("match_source") == "bounded-envelope-metadata"
                    for item in selected
                ),
            },
            "total_folders": len(folders),
            "searched_folders": len(folders) - len(errors),
            "failed_folders": len(failed_folders),
        }

    @staticmethod
    def _locator_terms(subject: str) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for term in re.findall(r"[\w@.+-]+", str(subject or ""), flags=re.UNICODE):
            folded = term.casefold()
            if folded and folded not in seen:
                terms.append(term)
                seen.add(folded)
            if len(terms) >= 12:
                break
        return terms

    @staticmethod
    def _same_envelope(envelope: Envelope, candidate: dict[str, Any]) -> bool:
        subject = str(candidate.get("title") or candidate.get("subject") or "").strip()
        sender_value = candidate.get("sender")
        sender: dict[str, Any] = sender_value if isinstance(sender_value, dict) else {}
        sender_address = str(sender.get("address") or candidate.get("sender_addr") or "").casefold()
        if subject and envelope.subject.strip() != subject:
            return False
        return not (
            sender_address and envelope.sender_addr.strip().casefold() != sender_address
        )

    def resolve_live_locators(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Revalidate only candidate locations and resolve a moved hit conservatively."""

        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Lesewerkzeug ist deaktiviert")
        decision = self.policy.decide(self.settings.resource_id, "mail.read", {"live_locator": True})
        if not decision.allowed:
            raise PermissionError(decision.reason)
        client = self._client()
        folders, error = client.list_folders()
        calls = {"list_folders": 1, "list_envelopes": 0, "search_envelopes": 0}
        if error:
            return {
                "ok": False,
                "complete": False,
                "results": [],
                "folder_errors": [{"folder": "*", "error": error}],
                "backend_calls": calls,
            }
        folder_map = self._folder_map(folders)
        envelope_cache: dict[str, tuple[list[Envelope], str]] = {}
        folder_errors: list[dict[str, str]] = []

        def envelopes(folder: str) -> list[Envelope]:
            resolved = folder_map.get(folder.strip().casefold())
            if not resolved:
                return []
            if resolved not in envelope_cache:
                envelope_cache[resolved] = client.list_envelopes(resolved, limit=200)
                calls["list_envelopes"] += 1
                if envelope_cache[resolved][1]:
                    folder_errors.append(
                        {"folder": resolved, "error": envelope_cache[resolved][1]}
                    )
            return envelope_cache[resolved][0] if not envelope_cache[resolved][1] else []

        resolved_results: list[dict[str, Any]] = []
        for candidate in candidates:
            locators = [dict(item) for item in candidate.get("locators") or []]
            live: list[dict[str, Any]] = []
            for locator in locators:
                locator["live_state"] = "stale"
                if not locator.get("current_in_index") or not locator.get("mailbox_id"):
                    continue
                folder = str(locator.get("folder") or "")
                match = next(
                    (
                        item
                        for item in envelopes(folder)
                        if str(item.mailbox_id) == str(locator.get("mailbox_id"))
                        and self._same_envelope(item, candidate)
                    ),
                    None,
                )
                if match is not None:
                    locator["live_state"] = "validated"
                    locator["stale"] = False
                    live.append(locator)

            state = "validated"
            selected: dict[str, Any] | None = None
            if live:
                selected = sorted(
                    live,
                    key=lambda item: (
                        bool(item.get("quarantine")),
                        str(item.get("folder") or "").casefold(),
                        str(item.get("mailbox_id") or ""),
                        str(item.get("occurrence_id") or ""),
                    ),
                )[0]
                selected = {**selected, "selected": True, "selection": "deterministic-live"}
            else:
                matches: list[dict[str, Any]] = []
                terms = self._locator_terms(
                    str(candidate.get("title") or candidate.get("subject") or "")
                )
                if terms:
                    for folder in sorted(folders, key=str.casefold):
                        rows, search_error = client.search_envelopes(folder, terms, limit=200)
                        calls["search_envelopes"] += 1
                        if search_error:
                            folder_errors.append({"folder": folder, "error": search_error})
                            continue
                        for envelope in rows:
                            if not self._same_envelope(envelope, candidate):
                                continue
                            matches.append(
                                {
                                    "occurrence_id": "",
                                    "locator_id": "",
                                    "resource_id": self.settings.resource_id,
                                    "folder_id": "",
                                    "folder": folder,
                                    "mailbox_id": str(envelope.mailbox_id),
                                    "uidvalidity": "",
                                    "uid": "",
                                    "observed_at": "",
                                    "current_in_index": False,
                                    "stale": False,
                                    "quarantine": False,
                                    "source_status": "live-server",
                                    "source_generation": "",
                                    "conflict_code": "",
                                    "live_state": "resolved-after-move",
                                    "selected": True,
                                    "selection": "unique-live-reresolution",
                                }
                            )
                unique = {
                    (str(item["folder"]), str(item["mailbox_id"])): item for item in matches
                }
                if len(unique) == 1:
                    selected = next(iter(unique.values()))
                    locators.append(dict(selected))
                    state = "resolved-after-move"
                elif len(unique) > 1:
                    state = "conflict"
                else:
                    state = "missing"
            resolved_results.append(
                {
                    "content_id": str(candidate.get("content_id") or ""),
                    "state": state,
                    "live_locator": selected,
                    "locators": locators,
                    "complete": selected is not None,
                }
            )
        return {
            "ok": all(bool(item["complete"]) for item in resolved_results) and not folder_errors,
            "complete": all(bool(item["complete"]) for item in resolved_results) and not folder_errors,
            "results": resolved_results,
            "folder_errors": folder_errors,
            "backend_calls": calls,
        }

    def read_message(
        self,
        folder: str,
        message_id: str,
        *,
        expected_subject: str = "",
    ) -> ParsedMessage:
        """Read exactly one selected mail without exposing its body in the CLI output."""
        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Lesewerkzeug ist deaktiviert")
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
        if envelope is None and expected_subject:
            terms = self._locator_terms(expected_subject)
            searched, search_error = client.search_envelopes(resolved, terms, limit=200)
            if search_error:
                raise RuntimeError(search_error)
            envelope = next(
                (
                    item
                    for item in searched
                    if str(item.mailbox_id) == str(message_id)
                    and item.subject.strip() == expected_subject.strip()
                ),
                None,
            )
        if envelope is not None and expected_subject and envelope.subject.strip() != expected_subject.strip():
            raise PermissionError("Betreff stimmt nicht mit der erwarteten Mail ueberein")
        if envelope is None:
            raise RuntimeError(
                "mail-locator-conflict: Ordner, Mailbox-ID und erwarteter Betreff "
                "sind auf dem Server nicht mehr gemeinsam aktuell"
            )
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
        reply_body = str(body or "").strip().replace("<#", "< #")
        if not reply_body:
            raise ValueError("Antwortentwurf darf nicht leer sein")
        subject = message.subject.strip()
        if not subject.casefold().startswith("re:"):
            subject = f"Re: {subject}"
        subject = clean_single_line(subject, 900)
        payload = {
            "draft_kind": "reply",
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
        material = "\0".join(
            (message.source_folder.casefold(), message.mailbox_id, recipient, subject, reply_body)
        )
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
            "ok": True,
            "draft_id": plan.id,
            "status": plan.status,
            "to": recipient,
            "subject": subject,
            "body": reply_body,
            "requires_explicit_approval": True,
        }

    @staticmethod
    def _recipient(value: str) -> str:
        raw = str(value or "").strip()
        parsed = parseaddr(raw)[1].strip().casefold()
        if (
            not parsed
            or "@" not in parsed
            or any(char in raw for char in "\r\n")
            or any(char in parsed for char in "\r\n")
        ):
            raise ValueError("Empfaengeradresse ist nicht versandfaehig")
        return parsed

    def draft_message(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        """Store a complete new-message draft without sending anything."""
        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Werkzeug ist deaktiviert")
        normalized_recipient = self._recipient(recipient)
        normalized_subject = clean_single_line(str(subject or "").strip(), 900)
        message_body = str(body or "").strip().replace("<#", "< #")
        if not normalized_subject:
            raise ValueError("Betreff des Mailentwurfs darf nicht leer sein")
        if not message_body:
            raise ValueError("Mailentwurf darf nicht leer sein")
        payload = {
            "draft_kind": "compose",
            "recipient": normalized_recipient,
            "subject": normalized_subject,
            "body": message_body,
        }
        decision = self.policy.decide(self.settings.resource_id, "mail.send", payload)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        material = "\0".join((normalized_recipient, normalized_subject, message_body))
        plan = self.storage.create_action(
            idempotency_key="mail-compose:" + hashlib.sha256(material.encode("utf-8")).hexdigest(),
            action_type="mail.send",
            resource_id=self.settings.resource_id,
            payload=payload,
            requires_approval=True,
        )
        self.storage.audit(
            "mail.compose.drafted",
            {"id": plan.id, "recipient": normalized_recipient, "subject": normalized_subject},
            resource_id=plan.resource_id,
        )
        return {
            "ok": True,
            "draft_id": plan.id,
            "status": plan.status,
            "to": normalized_recipient,
            "subject": normalized_subject,
            "body": message_body,
            "requires_explicit_approval": True,
        }

    def send_reply(self, draft_id: str, *, approved: bool = False) -> dict[str, Any]:
        return self._send_draft(draft_id, approved=approved, expected_kind="reply")

    def send_message(self, draft_id: str, *, approved: bool = False) -> dict[str, Any]:
        return self._send_draft(draft_id, approved=approved, expected_kind="compose")

    def _send_draft(
        self,
        draft_id: str,
        *,
        approved: bool,
        expected_kind: str,
    ) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Versand erfordert die ausdrueckliche Freigabe --yes")
        plan = self.storage.get_action(str(draft_id))
        if plan.action_type != "mail.send" or plan.resource_id != self.settings.resource_id:
            raise PermissionError("ActionPlan ist kein freigabefaehiger Mailentwurf")
        payload = plan.payload
        actual_kind = str(
            payload.get("draft_kind")
            or ("reply" if payload.get("source_message_id") or payload.get("mailbox_id") else "compose")
        )
        if actual_kind != expected_kind:
            raise PermissionError(f"Mailentwurf hat den falschen Typ: {actual_kind}")
        if plan.status == "completed":
            return {"ok": True, "duplicate": True, "draft_id": plan.id, "status": plan.status}
        if plan.status != "proposed" or not plan.requires_approval:
            raise PermissionError(f"Mailentwurf ist nicht freigabefaehig: {plan.status}")
        decision = self.policy.decide(self.settings.resource_id, "mail.send", payload)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        approved_plan = self.storage.update_action(plan.id, "approved")
        event_prefix = "mail.reply" if actual_kind == "reply" else "mail.compose"
        self.storage.audit(
            f"{event_prefix}.approved", {"id": plan.id}, resource_id=plan.resource_id, actor="user"
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
        template = "\n".join(headers) + "\n\n" + str(payload["body"]) + "\n"
        result = client.send_template(template)
        if not result.ok:
            failed = self.storage.update_action(approved_plan.id, "failed", result.detail)
            self.storage.audit(
                f"{event_prefix}.failed",
                {"id": plan.id, "status": result.status, "detail": result.detail},
                resource_id=plan.resource_id,
            )
            return {"ok": False, "draft_id": plan.id, "status": failed.status, "detail": result.detail}
        completed = self.storage.update_action(approved_plan.id, "completed")
        self.storage.audit(
            f"{event_prefix}.sent",
            {"id": plan.id, "recipient": payload["recipient"], "subject": payload["subject"]},
            resource_id=plan.resource_id,
            actor=f"user-approved-mail-{actual_kind}",
        )
        return {"ok": True, "duplicate": False, "draft_id": plan.id, "status": completed.status}

    def move(
        self,
        *,
        source: str,
        destination: str,
        message_id: str,
        expected_subject: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
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
            raise PermissionError(
                f"Quellordner ist fuer direkte Agentenverschiebungen gesperrt: {source_real}"
            )
        if destination_real.strip().casefold() in set(self.settings.denied_destinations):
            raise PermissionError(
                f"Zielordner ist fuer direkte Agentenverschiebungen gesperrt: {destination_real}"
            )
        decision = self.policy.decide(
            self.settings.resource_id,
            "mail.move",
            {
                "source": source_real,
                "destination": destination_real,
                "message_id": str(message_id),
                "direct_mail_move_tool": True,
            },
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        envelopes, error = client.list_envelopes(source_real, limit=200)
        if error:
            raise RuntimeError(error)
        envelope = next((item for item in envelopes if str(item.mailbox_id) == str(message_id)), None)
        if envelope is None:
            key = self._key(source_real, destination_real, message_id, expected_subject)
            rows = [
                p
                for p in self.storage.list_actions(limit=200)
                if p.idempotency_key == key and p.status == "completed"
            ]
            if rows:
                return {
                    "ok": True,
                    "duplicate": True,
                    "source": source_real,
                    "destination": destination_real,
                    "message_id": str(message_id),
                    "action": asdict(rows[0]),
                    "detail": "Bereits erfolgreich verschoben",
                }
            raise ValueError("Mail-ID wurde im angegebenen Quellordner nicht gefunden")
        if expected_subject and envelope.subject.strip() != expected_subject.strip():
            raise PermissionError("Betreff stimmt nicht mit der erwarteten Mail ueberein")
        payload = {
            "source": source_real,
            "destination": destination_real,
            "message_id": str(message_id),
            "subject": envelope.subject,
            "sender": envelope.sender_addr,
            "date": envelope.date,
            "direct_mail_move_tool": True,
        }
        key = self._key(source_real, destination_real, message_id, envelope.subject)
        plan = self.storage.create_action(
            idempotency_key=key,
            action_type="mail.move",
            resource_id=self.settings.resource_id,
            payload=payload,
            requires_approval=decision.requires_approval,
        )
        self.storage.audit(
            "action.planned",
            {"id": plan.id, "type": "mail.move", "status": plan.status},
            resource_id=plan.resource_id,
        )
        if plan.status == "completed":
            return {
                "ok": True,
                "duplicate": True,
                "source": source_real,
                "destination": destination_real,
                "message_id": str(message_id),
                "action": asdict(plan),
            }
        if plan.status == "proposed":
            plan = self.storage.update_action(plan.id, "approved")
            self.storage.audit(
                "action.approved_configured_mail_move_tool",
                {"id": plan.id},
                resource_id=plan.resource_id,
                actor="agent-mail-move-tool",
            )
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "duplicate": False,
                "source": source_real,
                "destination": destination_real,
                "message": asdict(envelope),
                "action": asdict(plan),
            }
        result = client.move_message(source_real, destination_real, str(message_id))
        if not result.ok:
            failed = self.storage.update_action(plan.id, "failed", result.detail)
            self.storage.audit(
                "mail.move.failed", {"id": plan.id, "detail": result.detail}, resource_id=plan.resource_id
            )
            return {"ok": False, "duplicate": False, "detail": result.detail, "action": asdict(failed)}
        completed = self.storage.update_action(plan.id, "completed", "")
        self.storage.audit(
            "mail.move.completed", payload, resource_id=plan.resource_id, actor="agent-mail-move-tool"
        )
        return {
            "ok": True,
            "duplicate": False,
            "source": source_real,
            "destination": destination_real,
            "message": asdict(envelope),
            "action": asdict(completed),
        }

    def review_correct(
        self,
        *,
        source: str,
        message_id: str,
        expected_subject: str,
        verdict: str,
        label: str = "",
        approved: bool = False,
    ) -> dict[str, Any]:
        """Move exactly one review mail to an allowlisted correction folder."""

        if not approved:
            raise PermissionError("Review-Korrektur erfordert die ausdrueckliche Freigabe --yes")
        if not self.settings.enabled:
            raise PermissionError("Direktes Mail-Werkzeug ist deaktiviert")
        selected_id = str(message_id or "").strip()
        selected_subject = str(expected_subject or "").strip()
        selected_verdict = str(verdict or "").strip().casefold()
        if not selected_id or not selected_subject:
            raise ValueError("Mailbox-ID und erwarteter Betreff sind erforderlich")
        destination_fields = {
            "relevant": "feedback_important",
            "routine": "feedback_unimportant",
            "spam": "feedback_spam",
        }
        if selected_verdict not in destination_fields:
            raise ValueError("Urteil muss relevant, routine oder spam sein")

        client = self._client()
        config = getattr(client, "config", None)
        if config is None or not hasattr(config, "folders"):
            config = load_mail_config()
        configured_source = str(config.folders.review).strip()
        if source.strip().casefold() != configured_source.casefold():
            raise PermissionError(
                "Review-Korrektur ist nur aus dem konfigurierten allgemeinen Review-Ordner erlaubt"
            )
        destination = str(
            getattr(config.folders, destination_fields[selected_verdict])
        ).strip()
        selected_label = str(label or "").strip().casefold()
        if selected_label:
            matches = [
                item
                for item in LearningFolderRegistry(config).list(active_only=True)
                if item.verdict == selected_verdict and item.label.casefold() == selected_label
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Label ist fuer dieses Urteil nicht eindeutig als aktiver Korrekturordner registriert"
                )
            destination = matches[0].folder

        folders, error = client.list_folders()
        if error:
            raise RuntimeError(error)
        folder_map = self._folder_map(folders)
        source_real = folder_map.get(configured_source.casefold())
        destination_real = folder_map.get(destination.casefold())
        if not source_real:
            raise ValueError(f"Review-Quellordner nicht gefunden: {configured_source}")
        if not destination_real:
            raise ValueError(f"Korrektur-Zielordner nicht gefunden: {destination}")

        decision = self.policy.decide(
            self.settings.resource_id,
            "mail.move",
            {
                "source": source_real,
                "destination": destination_real,
                "message_id": selected_id,
                "review_correction": True,
                "verdict": selected_verdict,
            },
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        key = self._review_correction_key(
            source_real,
            destination_real,
            selected_id,
            selected_subject,
            selected_verdict,
        )
        envelopes, error = client.list_envelopes(source_real, limit=200)
        if error:
            raise RuntimeError(error)
        envelope = next((item for item in envelopes if str(item.mailbox_id) == selected_id), None)
        if envelope is None:
            previous = next(
                (
                    item
                    for item in self.storage.list_actions(limit=200)
                    if item.idempotency_key == key
                ),
                None,
            )
            if previous is not None and previous.status == "completed":
                return {
                    "ok": True,
                    "duplicate": True,
                    "source": source_real,
                    "destination": destination_real,
                    "message_id": selected_id,
                    "verdict": selected_verdict,
                    "action": asdict(previous),
                }
            if previous is not None and previous.status == "failed":
                return {
                    "ok": False,
                    "duplicate": True,
                    "retry_blocked": True,
                    "detail": previous.error or "Vorheriger Move ist fehlgeschlagen oder unklar",
                    "action": asdict(previous),
                }
            raise ValueError("Mail-ID wurde im angegebenen Review-Ordner nicht gefunden")
        if envelope.subject.strip() != selected_subject:
            raise PermissionError("Betreff stimmt nicht mit der erwarteten Mail ueberein")

        payload = {
            "source": source_real,
            "destination": destination_real,
            "message_id": selected_id,
            "subject": envelope.subject,
            "verdict": selected_verdict,
            "label": selected_label,
            "review_correction": True,
        }
        plan = self.storage.create_action(
            idempotency_key=key,
            action_type="mail.review.correct",
            resource_id=self.settings.resource_id,
            payload=payload,
            requires_approval=True,
        )
        if plan.status == "completed":
            return {
                "ok": True,
                "duplicate": True,
                **payload,
                "action": asdict(plan),
            }
        if plan.status == "failed":
            return {
                "ok": False,
                "duplicate": True,
                "retry_blocked": True,
                "detail": plan.error,
                "action": asdict(plan),
            }
        if plan.status != "proposed" or not plan.requires_approval:
            raise PermissionError(f"Review-Korrektur ist nicht freigabefaehig: {plan.status}")
        plan = self.storage.update_action(plan.id, "approved")
        self.storage.audit(
            "mail.review.correct.approved",
            {"id": plan.id, "verdict": selected_verdict},
            resource_id=plan.resource_id,
            actor="user",
        )
        result = client.move_message(source_real, destination_real, selected_id)
        if not result.ok:
            failed = self.storage.update_action(plan.id, "failed", result.detail or result.status)
            self.storage.audit(
                "mail.review.correct.failed",
                {"id": plan.id, "status": result.status},
                resource_id=plan.resource_id,
            )
            return {
                "ok": False,
                "duplicate": False,
                "retry_blocked": True,
                "uncertain": result.status in {"delivery-uncertain", "move-uncertain"},
                "detail": result.detail or result.status,
                "action": asdict(failed),
            }
        completed = self.storage.update_action(plan.id, "completed")
        self.storage.audit(
            "mail.review.correct.completed",
            payload,
            resource_id=plan.resource_id,
            actor="user",
        )
        return {
            "ok": True,
            "duplicate": False,
            **payload,
            "action": asdict(completed),
            "feedback_recorded": False,
            "feedback_contract": "next-mail-worker-run-from-correction-folder",
        }

    @staticmethod
    def _key(source: str, destination: str, message_id: str, subject: str) -> str:
        material = "\0".join((source.casefold(), destination.casefold(), str(message_id), subject.strip()))
        return "mail-move:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _review_correction_key(
        source: str,
        destination: str,
        message_id: str,
        subject: str,
        verdict: str,
    ) -> str:
        material = "\0".join(
            (
                source.casefold(),
                destination.casefold(),
                str(message_id),
                subject.strip(),
                verdict,
            )
        )
        return "mail-review-correct:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
