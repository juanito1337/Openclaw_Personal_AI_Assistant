from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .connectors.nextcloud.calendar import NextcloudCalendar
from .connectors.nextcloud.client import NextcloudClient
from .connectors.nextcloud.contacts import NextcloudContacts
from .connectors.nextcloud.discovery import DiscoveredCollection
from .connectors.nextcloud.files import NextcloudFiles
from .connectors.nextcloud.tasks import NextcloudTasks
from .models import ActionPlan
from .policy import PolicyEngine
from .registry import ResourceRegistry
from .storage import AssistantStorage


class ActionService:
    def __init__(
        self,
        storage: AssistantStorage,
        registry: ResourceRegistry,
        policy: PolicyEngine,
        nextcloud_client: NextcloudClient,
        nextcloud_files: NextcloudFiles,
        nextcloud_calendar: NextcloudCalendar,
        nextcloud_tasks: NextcloudTasks,
        nextcloud_contacts: NextcloudContacts | None = None,
    ) -> None:
        self.storage = storage
        self.registry = registry
        self.policy = policy
        self.client = nextcloud_client
        self.files = nextcloud_files
        self.calendar = nextcloud_calendar
        self.tasks = nextcloud_tasks
        self.contacts = nextcloud_contacts

    def plan(self, action_type: str, resource_id: str, payload: dict[str, Any], idempotency_key: str = "") -> ActionPlan:
        decision = self.policy.decide(resource_id, action_type, payload)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        key = idempotency_key or self._default_key(action_type, resource_id, payload)
        plan = self.storage.create_action(
            idempotency_key=key,
            action_type=action_type,
            resource_id=resource_id,
            payload=payload,
            requires_approval=decision.requires_approval,
        )
        self.storage.audit("action.planned", {"id": plan.id, "type": action_type, "status": plan.status}, resource_id=resource_id)
        return plan

    def approve(self, action_id: str) -> ActionPlan:
        plan = self.storage.get_action(action_id)
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit("action.approved", {"id": action_id}, resource_id=plan.resource_id, actor="user")
        return updated

    def retry_managed_invoice_register(self, action_id: str) -> ActionPlan:
        """Retry only a failed, policy-approved managed yearly invoice register.

        This avoids treating a transient WebDAV failure as a permanent block for
        the same deterministic CSV content. It does not approve arbitrary user
        writes and keeps the narrow path/schema policy in force.
        """
        plan = self.storage.get_action(action_id)
        if plan.action_type != "files.create" or not bool(plan.payload.get("managed_invoice_register")):
            raise PermissionError("Automatischer Retry ist nur fuer das verwaltete Rechnungsregister erlaubt")
        if plan.status != "failed":
            return plan
        decision = self.policy.decide(plan.resource_id, plan.action_type, plan.payload)
        if not decision.allowed or decision.requires_approval:
            raise PermissionError(decision.reason or "Rechnungsregister-Retry ist nicht freigegeben")
        updated = self.storage.update_action(action_id, "approved", "")
        self.storage.audit(
            "action.retry_managed_invoice_register",
            {"id": action_id, "path": str(plan.payload.get("path") or "")},
            resource_id=plan.resource_id,
            actor="mail-agent",
        )
        return updated

    def approve_trusted_command(self, action_id: str, *, actor: str, evidence: dict[str, Any]) -> ActionPlan:
        """Narrow approval path for an already validated owner command mail."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "calendar.create":
            raise PermissionError("Trusted-command approval is limited to calendar.create")
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit(
            "action.approved_trusted_command",
            {"id": action_id, "evidence": evidence},
            resource_id=plan.resource_id,
            actor=actor,
        )
        return updated

    def approve_configured_calendar_tool(self, action_id: str, *, evidence: dict[str, Any]) -> ActionPlan:
        """Approve only a calendar.create plan enabled by the configured direct tool.

        This does not weaken the global approval policy. The narrow tool setting,
        resource permission and policy decision have already been checked before
        this method is called, and the approval is separately audited.
        """
        plan = self.storage.get_action(action_id)
        if plan.action_type != "calendar.create":
            raise PermissionError("Configured calendar-tool approval is limited to calendar.create")
        if not bool(plan.payload.get("direct_calendar_tool")):
            raise PermissionError("ActionPlan stammt nicht aus dem direkten Kalenderwerkzeug")
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit(
            "action.approved_configured_calendar_tool",
            {"id": action_id, "evidence": evidence},
            resource_id=plan.resource_id,
            actor="agent-calendar-tool",
        )
        return updated

    def approve_configured_calendar_update(self, action_id: str, *, evidence: dict[str, Any]) -> ActionPlan:
        """Approve one explicit, ETag-guarded CalDAV VEVENT update."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "calendar.update":
            raise PermissionError("Configured calendar-update approval is limited to calendar.update")
        if not bool(plan.payload.get("direct_calendar_tool")):
            raise PermissionError("ActionPlan stammt nicht aus dem direkten Kalenderwerkzeug")
        if not bool(plan.payload.get("optimistic_concurrency")):
            raise PermissionError("Kalender-Update besitzt keinen ETag-Konfliktschutz")
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit(
            "action.approved_configured_calendar_update",
            {"id": action_id, "evidence": evidence},
            resource_id=plan.resource_id,
            actor="agent-calendar-tool",
        )
        return updated

    def approve_configured_tasks_tool(self, action_id: str, *, evidence: dict[str, Any]) -> ActionPlan:
        """Approve only a tasks.create plan enabled by the configured direct tool."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "tasks.create":
            raise PermissionError("Configured tasks-tool approval is limited to tasks.create")
        if not bool(plan.payload.get("direct_tasks_tool")):
            raise PermissionError("ActionPlan stammt nicht aus dem direkten Aufgabenwerkzeug")
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit(
            "action.approved_configured_tasks_tool",
            {"id": action_id, "evidence": evidence},
            resource_id=plan.resource_id,
            actor="agent-tasks-tool",
        )
        return updated

    def approve_configured_tasks_update(self, action_id: str, *, evidence: dict[str, Any]) -> ActionPlan:
        """Approve one explicit, ETag-guarded CalDAV VTODO update."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "tasks.update":
            raise PermissionError("Configured tasks-update approval is limited to tasks.update")
        if not bool(plan.payload.get("direct_tasks_tool")):
            raise PermissionError("ActionPlan stammt nicht aus dem direkten Aufgabenwerkzeug")
        if not bool(plan.payload.get("optimistic_concurrency")):
            raise PermissionError("Aufgaben-Update besitzt keinen ETag-Konfliktschutz")
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit(
            "action.approved_configured_tasks_update",
            {"id": action_id, "evidence": evidence},
            resource_id=plan.resource_id,
            actor="agent-tasks-tool",
        )
        return updated

    def approve_configured_contacts_tool(self, action_id: str, *, evidence: dict[str, Any]) -> ActionPlan:
        """Approve only create-only CardDAV contacts from the configured tool."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "contacts.create":
            raise PermissionError("Configured contacts-tool approval is limited to contacts.create")
        if not bool(plan.payload.get("direct_contacts_tool")):
            raise PermissionError("ActionPlan stammt nicht aus dem direkten Kontaktwerkzeug")
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit(
            "action.approved_configured_contacts_tool",
            {"id": action_id, "evidence": evidence},
            resource_id=plan.resource_id,
            actor="agent-contacts-tool",
        )
        return updated

    def approve_configured_contacts_update(self, action_id: str, *, evidence: dict[str, Any]) -> ActionPlan:
        """Approve one explicit, ETag-guarded CardDAV contact update."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "contacts.update":
            raise PermissionError("Configured contacts-update approval is limited to contacts.update")
        if not bool(plan.payload.get("direct_contacts_tool")):
            raise PermissionError("ActionPlan stammt nicht aus dem direkten Kontaktwerkzeug")
        if not bool(plan.payload.get("optimistic_concurrency")):
            raise PermissionError("Kontakt-Update besitzt keinen ETag-Konfliktschutz")
        if plan.status not in {"proposed", "failed"}:
            return plan
        updated = self.storage.update_action(action_id, "approved")
        self.storage.audit(
            "action.approved_configured_contacts_update",
            {"id": action_id, "evidence": evidence},
            resource_id=plan.resource_id,
            actor="agent-contacts-tool",
        )
        return updated

    def execute_contact_create(self, action_id: str) -> tuple[ActionPlan, bool]:
        """Execute contacts.create and verify completed plans against CardDAV."""
        if self.contacts is None:
            raise RuntimeError("CardDAV-Kontaktexecutor ist nicht konfiguriert")
        plan = self.storage.get_action(action_id)
        if plan.action_type != "contacts.create":
            raise ValueError("execute_contact_create erwartet contacts.create")
        resource = self.registry.get(plan.resource_id)
        collection = DiscoveredCollection(
            kind=resource.kind,
            href=str(resource.metadata.get("href") or resource.remote_id),
            name=str(resource.metadata.get("name") or resource.id),
            resource_id=resource.id,
        )
        uid = str(plan.payload["uid"])
        if plan.status == "completed":
            if self.contacts.contact_exists(collection, uid):
                self.storage.audit(
                    "action.duplicate_verified",
                    {"id": plan.id, "type": plan.action_type, "detail": "Kontakt vorhanden"},
                    resource_id=plan.resource_id,
                )
                return plan, True
            self.storage.audit(
                "action.completed_stale",
                {"id": plan.id, "type": plan.action_type, "detail": "Kontakt fehlt extern"},
                resource_id=plan.resource_id,
            )
            plan = self.storage.update_action(plan.id, "approved", "")
        result = self.execute(plan.id)
        return result, False

    def execute_contact_update(self, action_id: str) -> tuple[ActionPlan, bool]:
        """Execute contacts.update and detect an already-applied retry safely."""
        if self.contacts is None:
            raise RuntimeError("CardDAV-Kontaktexecutor ist nicht konfiguriert")
        plan = self.storage.get_action(action_id)
        if plan.action_type != "contacts.update":
            raise ValueError("execute_contact_update erwartet contacts.update")
        if plan.status == "completed":
            current = self.contacts.read_contact(
                str(plan.payload["href"]),
                fallback_uid=str(plan.payload["uid"]),
            )
            if self._contact_changes_match(current, dict(plan.payload.get("changes") or {})):
                self.storage.audit(
                    "action.duplicate_verified",
                    {"id": plan.id, "type": plan.action_type, "detail": "Kontakt-Aenderung bereits vorhanden"},
                    resource_id=plan.resource_id,
                )
                return plan, True
            failed = self.storage.update_action(
                plan.id,
                "failed",
                "Abgeschlossenes Kontakt-Update stimmt extern nicht mehr ueberein; erneute Auswahl erforderlich",
            )
            return failed, False
        result = self.execute(plan.id)
        return result, False

    @staticmethod
    def _contact_changes_match(contact, changes: dict[str, Any]) -> bool:
        if "name" in changes and contact.name != str(changes["name"]):
            return False
        if "emails" in changes and {value.casefold() for value in contact.emails} != {
            str(value).casefold() for value in changes["emails"]
        }:
            return False
        if "phones" in changes and set(contact.phones) != {str(value) for value in changes["phones"]}:
            return False
        if "organization" in changes and contact.organization != str(changes["organization"]):
            return False
        if "note" in changes and contact.note != str(changes["note"]):
            return False
        return True

    def execute_calendar_update(self, action_id: str) -> tuple[ActionPlan, bool]:
        """Execute calendar.update and verify completed retries by content hash."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "calendar.update":
            raise ValueError("execute_calendar_update erwartet calendar.update")
        if plan.status == "completed":
            current = self.calendar.read_event(
                str(plan.payload["href"]), fallback_uid=str(plan.payload["uid"])
            )
            actual = hashlib.sha256(current.raw_ics.encode("utf-8")).hexdigest()
            if actual == str(plan.payload.get("expected_sha256") or ""):
                self.storage.audit(
                    "action.duplicate_verified",
                    {"id": plan.id, "type": plan.action_type, "detail": "Kalender-Aenderung bereits vorhanden"},
                    resource_id=plan.resource_id,
                )
                return plan, True
            failed = self.storage.update_action(
                plan.id, "failed",
                "Abgeschlossenes Kalender-Update stimmt extern nicht mehr ueberein; erneute Auswahl erforderlich",
            )
            return failed, False
        return self.execute(plan.id), False

    def execute_task_update(self, action_id: str) -> tuple[ActionPlan, bool]:
        """Execute tasks.update and verify completed retries by content hash."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "tasks.update":
            raise ValueError("execute_task_update erwartet tasks.update")
        if plan.status == "completed":
            current = self.tasks.read_task(
                str(plan.payload["href"]), fallback_uid=str(plan.payload["uid"])
            )
            actual = hashlib.sha256(current.raw_ics.encode("utf-8")).hexdigest()
            if actual == str(plan.payload.get("expected_sha256") or ""):
                self.storage.audit(
                    "action.duplicate_verified",
                    {"id": plan.id, "type": plan.action_type, "detail": "Aufgaben-Aenderung bereits vorhanden"},
                    resource_id=plan.resource_id,
                )
                return plan, True
            failed = self.storage.update_action(
                plan.id, "failed",
                "Abgeschlossenes Aufgaben-Update stimmt extern nicht mehr ueberein; erneute Auswahl erforderlich",
            )
            return failed, False
        return self.execute(plan.id), False

    def execute_task_create(self, action_id: str) -> tuple[ActionPlan, bool]:
        """Execute tasks.create and verify completed plans against Nextcloud."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "tasks.create":
            raise ValueError("execute_task_create erwartet tasks.create")
        resource = self.registry.get(plan.resource_id)
        collection = DiscoveredCollection(
            kind=resource.kind,
            href=str(resource.metadata.get("href") or resource.remote_id),
            name=str(resource.metadata.get("name") or resource.id),
            resource_id=resource.id,
        )
        uid = str(plan.payload["uid"])
        if plan.status == "completed":
            if self.tasks.task_exists(collection, uid):
                self.storage.audit(
                    "action.duplicate_verified",
                    {"id": plan.id, "type": plan.action_type, "detail": "Aufgabe vorhanden"},
                    resource_id=plan.resource_id,
                )
                return plan, True
            self.storage.audit(
                "action.completed_stale",
                {"id": plan.id, "type": plan.action_type, "detail": "Aufgabe fehlt extern"},
                resource_id=plan.resource_id,
            )
            plan = self.storage.update_action(plan.id, "approved", "")
        result = self.execute(plan.id)
        return result, False

    def execute_calendar_create(self, action_id: str) -> tuple[ActionPlan, bool]:
        """Execute calendar.create and verify completed plans against Nextcloud."""
        plan = self.storage.get_action(action_id)
        if plan.action_type != "calendar.create":
            raise ValueError("execute_calendar_create erwartet calendar.create")
        resource = self.registry.get(plan.resource_id)
        collection = DiscoveredCollection(
            kind=resource.kind,
            href=str(resource.metadata.get("href") or resource.remote_id),
            name=str(resource.metadata.get("name") or resource.id),
            resource_id=resource.id,
        )
        uid = str(plan.payload["uid"])
        if plan.status == "completed":
            if self.calendar.event_exists(collection, uid):
                self.storage.audit(
                    "action.duplicate_verified",
                    {"id": plan.id, "type": plan.action_type, "detail": "Kalendereintrag vorhanden"},
                    resource_id=plan.resource_id,
                )
                return plan, True
            self.storage.audit(
                "action.completed_stale",
                {"id": plan.id, "type": plan.action_type, "detail": "Kalendereintrag fehlt extern"},
                resource_id=plan.resource_id,
            )
            plan = self.storage.update_action(plan.id, "approved", "")
        result = self.execute(plan.id)
        return result, False

    def execute_workspace(self, action_id: str) -> tuple[ActionPlan, bool]:
        """Execute a workspace action and reconcile stale completed records.

        The database is not treated as the sole source of truth for external
        writes. A completed action is first verified against Nextcloud. Missing
        create-only targets are safely recreated; conflicting targets are never
        overwritten. The boolean return value indicates a verified duplicate.
        """
        plan = self.storage.get_action(action_id)
        if plan.status == "completed":
            verified, retryable, detail = self._verify_workspace_postcondition(plan)
            if verified:
                self.storage.audit(
                    "action.duplicate_verified",
                    {"id": plan.id, "type": plan.action_type, "detail": detail},
                    resource_id=plan.resource_id,
                )
                return plan, True
            if not retryable:
                failed = self.storage.update_action(plan.id, "failed", detail)
                self.storage.audit(
                    "action.reconciliation_conflict",
                    {"id": plan.id, "type": plan.action_type, "detail": detail},
                    resource_id=plan.resource_id,
                )
                return failed, False
            self.storage.audit(
                "action.completed_stale",
                {"id": plan.id, "type": plan.action_type, "detail": detail},
                resource_id=plan.resource_id,
            )
            plan = self.storage.update_action(plan.id, "approved", "")
        result = self.execute(plan.id)
        return result, False

    def _verify_workspace_postcondition(self, plan: ActionPlan) -> tuple[bool, bool, str]:
        if plan.action_type == "files.mkdir":
            path = str(plan.payload["path"])
            if self.files.exists(path):
                return True, False, f"Ordner vorhanden: {path}"
            return False, True, f"Abgeschlossener Ordner fehlt in Nextcloud: {path}"

        if plan.action_type == "files.create":
            path = str(plan.payload["path"])
            if not self.files.exists(path):
                return False, True, f"Abgeschlossene Datei fehlt in Nextcloud: {path}"
            expected = str(plan.payload.get("sha256") or "")
            if not expected:
                return True, False, f"Datei vorhanden: {path}"
            actual = hashlib.sha256(self.files.download(path)).hexdigest()
            if actual == expected:
                return True, False, f"Datei und SHA-256 bestaetigt: {path}"
            return (
                False,
                False,
                f"Zieldatei existiert mit anderem Inhalt; Ueberschreiben verboten: {path}",
            )

        if plan.action_type == "files.move":
            source = str(plan.payload["source"])
            destination = str(plan.payload["destination"])
            source_exists = self.files.exists(source)
            destination_exists = self.files.exists(destination)
            if destination_exists and not source_exists:
                return True, False, f"Verschieben bestaetigt: {destination}"
            if source_exists and not destination_exists:
                return False, True, f"Abgeschlossenes Verschieben ist extern nicht erfolgt: {source}"
            if source_exists and destination_exists:
                return False, False, "Quelle und Ziel existieren; automatisches Ueberschreiben verboten"
            return False, False, "Weder Quelle noch Ziel existieren; Aktion kann nicht sicher wiederholt werden"

        return True, False, "Keine externe Workspace-Nachbedingung erforderlich"

    def execute(self, action_id: str) -> ActionPlan:
        plan = self.storage.get_action(action_id)
        if plan.status == "completed":
            return plan
        if plan.status != "approved":
            raise PermissionError(f"ActionPlan ist nicht freigegeben: {plan.status}")
        decision = self.policy.decide(plan.resource_id, plan.action_type, plan.payload)
        if not decision.allowed:
            return self.storage.update_action(action_id, "failed", decision.reason)
        self.storage.update_action(action_id, "executing")
        try:
            result = self._execute(plan)
            updated = self.storage.update_action(action_id, "completed")
            self.storage.audit("action.completed", {"id": action_id, "result": result}, resource_id=plan.resource_id)
            return updated
        except Exception as exc:
            updated = self.storage.update_action(action_id, "failed", str(exc))
            self.storage.audit("action.failed", {"id": action_id, "error": str(exc)}, resource_id=plan.resource_id)
            return updated

    def _execute(self, plan: ActionPlan) -> str:
        resource = self.registry.get(plan.resource_id)
        if resource.connector != "nextcloud":
            raise ValueError(f"Noch kein Executor fuer Connector {resource.connector}")
        if plan.action_type == "files.create":
            path = str(plan.payload["path"])
            local_path = Path(str(plan.payload["local_path"])).expanduser().resolve()
            data = local_path.read_bytes()
            parent = str(Path(path).parent).replace("\\", "/")
            if parent not in {"", "."}:
                self.files.ensure_folder(parent)
            content_type = str(plan.payload.get("content_type") or "application/octet-stream")
            if bool(plan.payload.get("managed_invoice_register")):
                self.files.replace_managed_invoice_register(
                    path,
                    data,
                    content_type=content_type,
                    expected_sha256=str(plan.payload.get("sha256") or ""),
                )
            else:
                self.files.upload_new(path, data, content_type)
            return path
        if plan.action_type == "files.mkdir":
            path = str(plan.payload["path"])
            self.files.ensure_folder(path)
            return path
        if plan.action_type == "files.move":
            source = str(plan.payload["source"])
            destination = str(plan.payload["destination"])
            self.files.move_new(source, destination)
            return destination
        collection = DiscoveredCollection(
            kind=resource.kind,
            href=str(resource.metadata.get("href") or resource.remote_id),
            name=str(resource.metadata.get("name") or resource.id),
            resource_id=resource.id,
        )
        if plan.action_type == "calendar.create":
            return self.calendar.create_event(collection, str(plan.payload["ics"]), str(plan.payload["uid"]))
        if plan.action_type == "calendar.update":
            updated = self.calendar.update_event(
                collection,
                href=str(plan.payload["href"]),
                uid=str(plan.payload["uid"]),
                ics=str(plan.payload["ics"]),
                etag=str(plan.payload.get("etag") or ""),
            )
            return updated.href
        if plan.action_type == "tasks.create":
            return self.tasks.create_task(collection, str(plan.payload["ics"]), str(plan.payload["uid"]))
        if plan.action_type == "tasks.update":
            updated = self.tasks.update_task(
                collection,
                href=str(plan.payload["href"]),
                uid=str(plan.payload["uid"]),
                ics=str(plan.payload["ics"]),
                etag=str(plan.payload.get("etag") or ""),
            )
            return updated.href
        if plan.action_type == "contacts.create":
            if self.contacts is None:
                raise RuntimeError("CardDAV-Kontaktexecutor ist nicht konfiguriert")
            return self.contacts.create_contact(
                collection,
                str(plan.payload["vcard"]),
                str(plan.payload["uid"]),
            )
        if plan.action_type == "contacts.update":
            if self.contacts is None:
                raise RuntimeError("CardDAV-Kontaktexecutor ist nicht konfiguriert")
            updated = self.contacts.update_contact(
                collection,
                href=str(plan.payload["href"]),
                uid=str(plan.payload["uid"]),
                vcard=str(plan.payload["vcard"]),
                etag=str(plan.payload.get("etag") or ""),
            )
            return updated.href
        raise ValueError(f"Nicht unterstuetzter Aktionstyp: {plan.action_type}")

    @staticmethod
    def _default_key(action_type: str, resource_id: str, payload: dict[str, Any]) -> str:
        raw = repr(sorted(payload.items())).encode("utf-8")
        return f"{action_type}:{resource_id}:{hashlib.sha256(raw).hexdigest()}"
