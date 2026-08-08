from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from personal_assistant.config import AssistantConfig
from personal_assistant.connectors.nextcloud.calendar import NextcloudCalendar
from personal_assistant.connectors.nextcloud.client import (
    NextcloudClient,
    NextcloudError,
)
from personal_assistant.connectors.nextcloud.contacts import NextcloudContacts
from personal_assistant.connectors.nextcloud.discovery import (
    DiscoveredCollection,
    NextcloudDiscovery,
)

from .command import CommandRunner
from .config import Config
from .models import OperationResult
from .utils import atomic_write_bytes, normalize_address


class NextcloudSkillError(RuntimeError):
    """Compatibility error for the restricted native Nextcloud bridge."""


class NextcloudSkillClient:
    """Narrow CalDAV/CardDAV bridge backed by release-owned Python code.

    The historical implementation executed a broad workspace-installed community
    skill. Container releases deliberately do not execute mutable workspace code.
    This compatibility class therefore keeps the established mail-agent interface
    while delegating only discovery, contact reads and create-only calendar writes
    to the audited native connectors in ``personal_assistant.connectors``.
    """

    def __init__(
        self,
        config: Config,
        runner: CommandRunner,
        *,
        calendar_resource_id: str = "",
    ) -> None:
        self.config = config
        self.runner = runner
        self.log = logging.getLogger(__name__)
        self.calendar_resource_id = str(calendar_resource_id or "").strip()
        native_config = AssistantConfig()
        native_config.nextcloud.enabled = config.nextcloud.enabled
        native_config.nextcloud.base_url_env = config.nextcloud.base_url_env
        native_config.nextcloud.username_env = config.nextcloud.username_env
        native_config.nextcloud.token_env = config.nextcloud.token_env
        native_config.nextcloud.request_timeout_seconds = max(
            5, min(config.runtime.command_timeout_seconds, 300)
        )
        self.native_config = native_config
        self.client = NextcloudClient(native_config)
        self.discovery = NextcloudDiscovery(self.client)
        self.calendar = NextcloudCalendar(native_config, self.client)
        self.contacts = NextcloudContacts(native_config, self.client)
        self._contact_emails: set[str] | None = None
        self._contact_cache_source = "not-loaded"
        self._last_contact_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.config.nextcloud.enabled)

    @property
    def available(self) -> bool:
        return True

    @property
    def script_path(self) -> Path:
        """Compatibility path used by older backend-selection code and status UI."""
        return Path(__file__).resolve().parents[1] / "personal_assistant/connectors/nextcloud/client.py"

    @property
    def workspace_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def credentials(self) -> tuple[str, str, str]:
        return self.client.credentials()

    def missing_environment(self) -> list[str]:
        return self.client.missing_environment()

    def node_health(self) -> tuple[bool, str]:
        return True, "nicht erforderlich (native Python-Bruecke)"

    def verify_skill(self, *, allow_review: bool = False) -> OperationResult:
        del allow_review
        return OperationResult(
            True,
            "nextcloud-native-verified",
            "Native CalDAV/CardDAV-Bruecke ist Bestandteil des verifizierten Releases; "
            "kein Community-Skill wird ausgefuehrt.",
        )

    def skill_card(self) -> OperationResult:
        return OperationResult(
            True,
            "nextcloud-native-contract",
            (
                "Native Release-Bruecke: Kalender/Adressbuecher entdecken, Kontakte lesen "
                "und neue Kalenderobjekte mit If-None-Match anlegen. Keine Delete-, Share- "
                "oder freie WebDAV-Schnittstelle."
            ),
        )

    def install_skill(self, *, allow_review: bool = False) -> OperationResult:
        del allow_review
        return OperationResult(
            True,
            "nextcloud-native-present",
            "Keine Installation erforderlich; die eingeschraenkte Bruecke kommt aus dem "
            "unveraenderlichen Release-Image.",
        )

    @staticmethod
    def _collection_dict(item: DiscoveredCollection, *, kind: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "displayName": item.name,
            "name": item.name,
            "href": item.href,
            "resource_id": item.resource_id,
            "can_read": item.can_read,
            "can_create": item.can_create,
            "can_update": item.can_update,
        }
        result["calendar" if kind == "calendar" else "addressBook"] = item.name
        if item.components:
            result["components"] = list(item.components)
        return result

    def list_calendars(self) -> list[dict[str, Any]]:
        try:
            return [
                self._collection_dict(item, kind="calendar")
                for item in self.discovery.calendars()
            ]
        except (NextcloudError, OSError, ValueError) as exc:
            raise NextcloudSkillError(str(exc)) from exc

    def list_addressbooks(self) -> list[dict[str, Any]]:
        try:
            return [
                self._collection_dict(item, kind="addressbook")
                for item in self.discovery.addressbooks()
            ]
        except (NextcloudError, OSError, ValueError) as exc:
            raise NextcloudSkillError(str(exc)) from exc

    @staticmethod
    def _collection_aliases(item: DiscoveredCollection) -> set[str]:
        aliases = {
            item.name.casefold(),
            item.href.casefold(),
            item.href.rstrip("/").rsplit("/", 1)[-1].casefold(),
            item.resource_id.casefold(),
        }
        return {value for value in aliases if value}

    @classmethod
    def _select_collection(
        cls,
        items: list[DiscoveredCollection],
        selected: str,
        *,
        label: str,
        require_selection: bool,
    ) -> list[DiscoveredCollection]:
        wanted = str(selected or "").strip().casefold()
        if not wanted:
            if require_selection:
                if len(items) == 1:
                    return items
                raise NextcloudSkillError(
                    f"{label} ist nicht eindeutig konfiguriert ({len(items)} Treffer); "
                    "eine stabile Ressourcen-ID oder ein exakter Name ist erforderlich"
                )
            return items
        wanted_slug = wanted.rstrip("/").rsplit("/", 1)[-1]
        matches = [
            item
            for item in items
            if wanted in cls._collection_aliases(item)
            or wanted_slug in cls._collection_aliases(item)
        ]
        if len(matches) != 1:
            raise NextcloudSkillError(
                f"{label} {selected!r} wurde nicht eindeutig gefunden ({len(matches)} Treffer)"
            )
        return matches

    def _addressbook_collections(self) -> list[DiscoveredCollection]:
        try:
            books = self.discovery.addressbooks()
        except (NextcloudError, OSError, ValueError) as exc:
            raise NextcloudSkillError(str(exc)) from exc
        return self._select_collection(
            books,
            self.config.nextcloud.addressbook,
            label="Adressbuch",
            require_selection=False,
        )

    def list_contacts(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        try:
            for addressbook in self._addressbook_collections():
                for contact in self.contacts.list_contacts(addressbook):
                    result.append(
                        {
                            "uid": contact.uid,
                            "displayName": contact.name,
                            "fullName": contact.name,
                            "emails": list(contact.emails),
                            "addressBook": addressbook.name,
                            "href": contact.href,
                        }
                    )
        except (NextcloudError, OSError, ValueError) as exc:
            raise NextcloudSkillError(str(exc)) from exc
        return result

    def search_contacts(self, query: str) -> list[dict[str, Any]]:
        wanted = str(query or "").strip().casefold()
        contacts = self.list_contacts()
        if not wanted:
            return contacts
        return [
            item
            for item in contacts
            if wanted in str(item.get("displayName") or "").casefold()
            or any(wanted in str(value).casefold() for value in item.get("emails", []))
        ]

    def create_event(self, normalized_event: Any) -> OperationResult:
        selector = self.config.nextcloud.calendar.strip() or self.calendar_resource_id
        try:
            calendars = self._select_collection(
                self.discovery.calendars(),
                selector,
                label="Kalender",
                require_selection=True,
            )
            calendar = calendars[0]
            if not calendar.can_create:
                return OperationResult(
                    False,
                    "nextcloud-calendar-permission-denied",
                    f"Kalender {calendar.name!r} meldet kein create/bind-Recht",
                )
            href = self.calendar.create_event(
                calendar,
                str(normalized_event.ics),
                str(normalized_event.uid),
            )
            return OperationResult(
                True,
                "created",
                f"Termin create-only ueber native CalDAV-Bruecke angelegt ({href})",
            )
        except NextcloudError as exc:
            if "existiert bereits" in str(exc):
                return OperationResult(True, "duplicate", str(exc))
            return OperationResult(False, "nextcloud-calendar-failed", str(exc))
        except (NextcloudSkillError, OSError, ValueError) as exc:
            return OperationResult(False, "nextcloud-calendar-failed", str(exc))

    @staticmethod
    def contact_emails(contact: dict[str, Any]) -> set[str]:
        found: set[str] = set()

        def walk(value: Any, *, email_context: bool = False) -> None:
            if isinstance(value, str):
                if email_context or "@" in value:
                    normalized = normalize_address(value)
                    if "@" in normalized and " " not in normalized:
                        found.add(normalized)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item, email_context=email_context)
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    key_text = str(key).casefold()
                    walk(
                        item,
                        email_context=email_context
                        or "email" in key_text
                        or key_text in {"mail", "value"},
                    )

        for key, value in contact.items():
            key_text = str(key).casefold()
            if "email" in key_text or key_text == "mail":
                walk(value, email_context=True)
        return found

    def _read_contact_cache(self) -> tuple[set[str], float] | None:
        path = self.config.nextcloud.contact_cache_file
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            refreshed_at = float(payload.get("refreshed_at", 0))
            emails = {
                normalize_address(str(item))
                for item in payload.get("emails", [])
                if normalize_address(str(item))
            }
            return emails, refreshed_at
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _write_contact_cache(self, emails: set[str]) -> None:
        path = self.config.nextcloud.contact_cache_file
        payload = {
            "refreshed_at": time.time(),
            "email_count": len(emails),
            "emails": sorted(emails),
        }
        atomic_write_bytes(
            path,
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        os.chmod(path, 0o600)

    def refresh_contact_cache(self, *, force: bool = False) -> tuple[bool, str]:
        if not self.enabled or not self.config.nextcloud.contacts_enabled:
            self._contact_emails = set()
            self._contact_cache_source = "disabled"
            return False, "Nextcloud-Kontaktabgleich ist deaktiviert"

        cached = self._read_contact_cache()
        if not force and cached:
            emails, refreshed_at = cached
            age = max(0, time.time() - refreshed_at)
            if age <= self.config.nextcloud.contact_cache_ttl_seconds:
                self._contact_emails = emails
                self._contact_cache_source = "fresh-cache"
                return True, f"{len(emails)} Kontaktadressen aus Cache ({int(age)} Sekunden alt)"

        try:
            contacts = self.list_contacts()
            refreshed_emails: set[str] = set()
            for contact in contacts:
                refreshed_emails.update(self.contact_emails(contact))
            self._write_contact_cache(refreshed_emails)
            self._contact_emails = refreshed_emails
            self._contact_cache_source = "nextcloud"
            self._last_contact_error = ""
            return True, (
                f"{len(refreshed_emails)} Kontaktadressen ueber native CardDAV-Bruecke geladen"
            )
        except NextcloudSkillError as exc:
            self._last_contact_error = str(exc)
            if cached:
                self._contact_emails = cached[0]
                self._contact_cache_source = "stale-cache"
                return True, (
                    f"Nextcloud nicht erreichbar; verwende alten Cache mit "
                    f"{len(cached[0])} Adressen: {exc}"
                )
            self._contact_emails = set()
            self._contact_cache_source = "error"
            return False, str(exc)

    def is_known_contact(self, email: str) -> bool:
        normalized = normalize_address(email)
        if not normalized:
            return False
        if self._contact_emails is None:
            self.refresh_contact_cache(force=False)
        return normalized in (self._contact_emails or set())

    def clear_contact_cache(self) -> OperationResult:
        self._contact_emails = None
        try:
            self.config.nextcloud.contact_cache_file.unlink(missing_ok=True)
        except OSError as exc:
            return OperationResult(False, "nextcloud-cache-clear-failed", str(exc))
        return OperationResult(
            True,
            "nextcloud-cache-cleared",
            str(self.config.nextcloud.contact_cache_file),
        )

    @staticmethod
    def _resource_aliases(item: dict[str, Any], *, kind: str) -> set[str]:
        keys = (
            ("displayName", "name", "calendar", "href", "url", "resource_id")
            if kind == "calendar"
            else (
                "displayName",
                "name",
                "addressBook",
                "addressbook",
                "href",
                "url",
                "resource_id",
            )
        )
        aliases: set[str] = set()
        for key in keys:
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            aliases.add(value.casefold())
            aliases.add(value.rstrip("/").rsplit("/", 1)[-1].casefold())
        return aliases

    @classmethod
    def _resource_selected(
        cls,
        items: list[dict[str, Any]],
        selected: str,
        *,
        kind: str,
    ) -> bool:
        wanted = (selected or "").strip().casefold()
        if not wanted:
            return len(items) == 1
        wanted_slug = wanted.rstrip("/").rsplit("/", 1)[-1]
        matches = [
            item
            for item in items
            if wanted in cls._resource_aliases(item, kind=kind)
            or wanted_slug in cls._resource_aliases(item, kind=kind)
        ]
        return len(matches) == 1

    def health(self, *, live: bool = True) -> dict[str, Any]:
        base_url, username, token = self.credentials()
        calendar_selector = self.config.nextcloud.calendar.strip() or self.calendar_resource_id
        result: dict[str, Any] = {
            "ok": False,
            "enabled": self.enabled,
            "backend": "native-caldav-carddav",
            "connector_path": str(self.script_path),
            "connector_available": self.available,
            "environment_ok": bool(base_url and username and token),
            "missing_environment": self.missing_environment(),
            "base_url": base_url,
            "user": username,
            "calendar": calendar_selector,
            "addressbook": self.config.nextcloud.addressbook,
            "contacts_enabled": self.config.nextcloud.contacts_enabled,
            "contact_cache": str(self.config.nextcloud.contact_cache_file),
        }
        if not self.enabled:
            result["detail"] = "Nextcloud ist in mail_agent/config.toml deaktiviert"
            return result
        if not result["environment_ok"]:
            result["detail"] = "Fehlende Umgebungsvariablen: " + ", ".join(
                result["missing_environment"]
            )
            return result
        if not live:
            result["ok"] = True
            result["detail"] = (
                "Native Nextcloud-Konfiguration vollstaendig; Live-Test nicht ausgefuehrt"
            )
            return result
        try:
            calendars = self.list_calendars()
            addressbooks = self.list_addressbooks()
            result["calendars"] = [str(item.get("displayName") or "") for item in calendars]
            result["addressbooks"] = [
                str(item.get("displayName") or "") for item in addressbooks
            ]
            contacts_ok, contacts_detail = self.refresh_contact_cache(force=False)
            result["contacts_ok"] = contacts_ok
            result["contacts_detail"] = contacts_detail
            calendar_found = self._resource_selected(
                calendars,
                calendar_selector,
                kind="calendar",
            )
            calendar_create_allowed = bool(
                calendar_found
                and next(
                    (
                        item.get("can_create")
                        for item in calendars
                        if self._resource_selected(
                            [item],
                            calendar_selector,
                            kind="calendar",
                        )
                    ),
                    False,
                )
            )
            addressbook_found = (
                True
                if not self.config.nextcloud.contacts_enabled
                else bool(addressbooks)
                if not self.config.nextcloud.addressbook.strip()
                else self._resource_selected(
                    addressbooks,
                    self.config.nextcloud.addressbook,
                    kind="addressbook",
                )
            )
            result["selected_calendar_found"] = calendar_found
            result["selected_calendar_create_allowed"] = calendar_create_allowed
            result["selected_addressbook_found"] = addressbook_found
            result["ok"] = bool(
                calendar_found
                and calendar_create_allowed
                and addressbook_found
                and (contacts_ok or not self.config.nextcloud.contacts_enabled)
            )
            if result["ok"]:
                result["detail"] = (
                    "Native Nextcloud-Kalender- und CardDAV-Verbindung sind erreichbar"
                )
            else:
                missing_resources: list[str] = []
                if not calendar_found:
                    missing_resources.append(
                        "Kalender '" + (calendar_selector or "<nicht eindeutig>") + "'"
                    )
                elif not calendar_create_allowed:
                    missing_resources.append("Create-Recht im ausgewaehlten Kalender")
                if not addressbook_found:
                    missing_resources.append(
                        "Adressbuch '"
                        + (self.config.nextcloud.addressbook or "<nicht gefunden>")
                        + "'"
                    )
                if not contacts_ok and self.config.nextcloud.contacts_enabled:
                    missing_resources.append("CardDAV-Kontaktabgleich")
                result["detail"] = "Nicht bereit: " + ", ".join(missing_resources)
            return result
        except NextcloudSkillError as exc:
            result["detail"] = str(exc)
            return result
