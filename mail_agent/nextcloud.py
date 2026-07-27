from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .command import CommandResult, CommandRunner
from .config import Config
from .models import OperationResult
from .utils import atomic_write_bytes, extract_json_object, normalize_address


class NextcloudSkillError(RuntimeError):
    """Raised when the installed OpenClaw Nextcloud skill cannot be used."""


class NextcloudSkillClient:
    """Restricted bridge to the ClawHub ``openclaw-nextcloud`` skill.

    The mail agent only invokes read operations for calendars/address books/contacts
    and the single non-destructive operation needed by this project: creating a new
    calendar event. It never invokes delete, edit, share, file upload, Deck, or
    Notes commands. Invoice files use the separate restricted WebDAV client.
    """

    def __init__(self, config: Config, runner: CommandRunner) -> None:
        self.config = config
        self.runner = runner
        self.log = logging.getLogger(__name__)
        self._contact_emails: set[str] | None = None
        self._contact_cache_source = "not-loaded"
        self._last_contact_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.config.nextcloud.enabled)

    @property
    def workspace_root(self) -> Path:
        # Use the workspace containing this package, not the location of an
        # optional alternate config file. This prevents a test/custom config in
        # /tmp from making OpenClaw install or discover skills in the wrong tree.
        return Path(__file__).resolve().parents[1]

    @property
    def script_path(self) -> Path:
        configured_root = self.config.nextcloud.skill_dir.resolve()
        configured = configured_root / "scripts" / "nextcloud.js"
        if configured.exists():
            return configured

        # An explicitly configured non-default directory is authoritative. Falling
        # back to another workspace copy would hide deployment mistakes and made
        # tests/installations silently use stale third-party code. Discovery is
        # reserved for the default workspace location, where OpenClaw may choose an
        # owner-prefixed directory name.
        default_root = (self.workspace_root / "skills" / "openclaw-nextcloud").resolve()
        if configured_root != default_root:
            return configured

        root = self.workspace_root / "skills"
        candidates = sorted(root.glob("*/scripts/nextcloud.js")) if root.exists() else []
        for candidate in candidates:
            if "nextcloud" in candidate.parent.parent.name.casefold():
                return candidate
        return configured

    def credentials(self) -> tuple[str, str, str]:
        cfg = self.config.nextcloud
        return (
            os.environ.get(cfg.base_url_env, "").strip().rstrip("/"),
            os.environ.get(cfg.username_env, "").strip(),
            os.environ.get(cfg.token_env, "").strip(),
        )

    def missing_environment(self) -> list[str]:
        cfg = self.config.nextcloud
        return [
            name
            for name in (cfg.base_url_env, cfg.username_env, cfg.token_env)
            if not os.environ.get(name, "").strip()
        ]

    def node_health(self) -> tuple[bool, str]:
        node = shutil.which("node")
        if not node:
            return False, "Node.js wurde nicht gefunden (benoetigt wird Node.js 20 oder neuer)"
        result = self.runner.run([node, "--version"], timeout=5)
        version = result.stdout.strip() or result.stderr.strip()
        if not result.ok:
            return False, version or "Node.js konnte nicht gestartet werden"
        try:
            major = int(version.lstrip("v").split(".", 1)[0])
        except (TypeError, ValueError):
            return False, f"Node.js-Version konnte nicht gelesen werden: {version!r}"
        if major < 20:
            return False, f"Node.js {version} ist zu alt; benoetigt wird Version 20 oder neuer"
        return True, version

    @staticmethod
    def _verification_decision(payload: Any) -> tuple[bool, str]:
        if not isinstance(payload, dict):
            return False, "Verifizierungsantwort ist kein JSON-Objekt"

        def first_value(node: Any, keys: set[str]) -> Any:
            if isinstance(node, dict):
                for key, value in node.items():
                    if str(key).casefold() in keys:
                        return value
                for value in node.values():
                    found = first_value(value, keys)
                    if found is not None:
                        return found
            elif isinstance(node, list):
                for value in node:
                    found = first_value(value, keys)
                    if found is not None:
                        return found
            return None

        ok_value = first_value(payload, {"ok"})
        decision_value = first_value(payload, {"decision", "verdict"})
        decision = str(decision_value or "").strip().casefold()
        if ok_value is False or decision in {"fail", "failed", "block", "blocked", "reject", "rejected", "malicious"}:
            return False, f"ClawHub-Verifizierung abgelehnt (decision={decision or 'unbekannt'})"
        if decision in {"warn", "warning", "review", "risky", "risk", "pending"}:
            return False, (
                f"ClawHub verlangt eine manuelle Sicherheitspruefung (decision={decision}). "
                "Pruefe zuerst 'openclaw skills verify ... --card' und installiere nur nach eigener Freigabe."
            )
        if ok_value is True or decision in {"pass", "passed", "allow", "allowed", "trusted", "safe", "ok"}:
            return True, f"ClawHub-Verifizierung bestanden (decision={decision or 'ok'})"
        # The official verify command exits non-zero for a failed decision. If it
        # exited successfully but uses a newer envelope without a known decision,
        # preserve the raw response and require an explicit human review instead of
        # silently installing executable third-party code.
        return False, "Verifizierungsantwort enthaelt keine eindeutig positive Trust-Entscheidung"

    def verify_skill(self, *, allow_review: bool = False) -> OperationResult:
        openclaw = shutil.which("openclaw")
        if not openclaw:
            return OperationResult(
                False,
                "nextcloud-skill-verifier-missing",
                "'openclaw' wurde im PATH nicht gefunden; der Community-Skill wird nicht automatisch installiert.",
            )

        # The OpenClaw CLI prints the clawhub.skill.verify.v1 JSON envelope by
        # default and exits non-zero for a failed registry decision.
        result = self.runner.run(
            [openclaw, "skills", "verify", self.config.nextcloud.skill_package],
            timeout=90,
            cwd=self.workspace_root,
        )
        if not result.ok:
            return OperationResult(False, "nextcloud-skill-verification-failed", result.combined)
        try:
            payload = extract_json_object(result.stdout or result.combined)
        except (ValueError, json.JSONDecodeError):
            return OperationResult(
                False,
                "nextcloud-skill-verification-invalid",
                "OpenClaw lieferte bei der Skill-Verifizierung kein auswertbares JSON: "
                + (result.stdout or result.combined)[:500],
            )
        trusted, detail = self._verification_decision(payload)
        review_required = (
            not trusted
            and "manuelle Sicherheitspruefung" in detail
        )
        if review_required and allow_review:
            return OperationResult(
                True,
                "nextcloud-skill-review-approved",
                detail + " Die manuelle Freigabe wurde fuer diesen Installationsaufruf ausdruecklich bestaetigt.",
            )
        return OperationResult(
            trusted,
            "nextcloud-skill-verified" if trusted else (
                "nextcloud-skill-review-required" if review_required else "nextcloud-skill-verification-failed"
            ),
            detail,
        )

    def skill_card(self) -> OperationResult:
        openclaw = shutil.which("openclaw")
        if not openclaw:
            return OperationResult(False, "nextcloud-skill-verifier-missing", "'openclaw' wurde im PATH nicht gefunden")
        result = self.runner.run(
            [openclaw, "skills", "verify", self.config.nextcloud.skill_package, "--card"],
            timeout=90,
            cwd=self.workspace_root,
        )
        return OperationResult(
            result.ok,
            "nextcloud-skill-card" if result.ok else "nextcloud-skill-card-failed",
            result.stdout.strip() or result.stderr.strip(),
        )

    def install_skill(self, *, allow_review: bool = False) -> OperationResult:
        # Re-check the registry trust envelope even when a local copy already
        # exists. A previously installed version can later be blocked or require
        # review, and executable third-party code must not silently bypass that
        # decision merely because its script is present on disk.
        verification = self.verify_skill(allow_review=allow_review)
        if not verification.ok:
            return verification
        if self.script_path.exists():
            return OperationResult(
                True,
                "nextcloud-skill-installed",
                f"Skill vorhanden und aktuell verifiziert: {self.script_path}",
            )
        openclaw = shutil.which("openclaw")
        if not openclaw:
            return OperationResult(False, "nextcloud-skill-installer-missing", "'openclaw' wurde im PATH nicht gefunden")
        install_command = [
            openclaw,
            "skills",
            "install",
            self.config.nextcloud.skill_package,
        ]
        if allow_review:
            # OpenClaw requires its own explicit non-interactive acknowledgement
            # after the caller has reviewed a risky community release. Our
            # ``--allow-review`` flag is the human-facing guard; this is the
            # corresponding official OpenClaw CLI flag.
            install_command.append("--acknowledge-clawhub-risk")
        result = self.runner.run(
            install_command,
            timeout=240,
            cwd=self.workspace_root,
        )
        if not result.ok:
            return OperationResult(False, "nextcloud-skill-install-failed", result.combined)
        if not self.script_path.exists():
            return OperationResult(
                False,
                "nextcloud-skill-install-unverified",
                (result.combined + "\nInstallationskommando war erfolgreich, aber scripts/nextcloud.js wurde nicht gefunden.").strip(),
            )
        check = self.runner.run([openclaw, "skills", "check"], timeout=60, cwd=self.workspace_root)
        detail = str(self.script_path)
        if not check.ok:
            # ``skills check`` inspects every visible workspace skill. An
            # unrelated broken skill must not turn an otherwise verified and
            # successfully installed Nextcloud bridge into a false failure.
            detail += "; Hinweis: 'openclaw skills check' meldete zusaetzliche Workspace-Probleme: " + check.combined[:500]
        return OperationResult(True, "nextcloud-skill-installed", detail)

    def _run(self, arguments: Iterable[str], *, timeout: int = 45) -> Any:
        if not self.enabled:
            raise NextcloudSkillError("Nextcloud ist in mail_agent/config.toml deaktiviert")
        missing = self.missing_environment()
        if missing:
            raise NextcloudSkillError("Fehlende Umgebungsvariablen: " + ", ".join(missing))
        node_ok, node_detail = self.node_health()
        if not node_ok:
            raise NextcloudSkillError(node_detail)
        script = self.script_path
        if not script.exists():
            raise NextcloudSkillError(
                f"OpenClaw-Nextcloud-Skill fehlt: {script}. "
                f"Installieren mit: openclaw skills install {self.config.nextcloud.skill_package}"
            )
        node = shutil.which("node") or "node"
        result = self.runner.run(
            [node, str(script), *[str(item) for item in arguments]],
            timeout=timeout,
            cwd=self.workspace_root,
        )
        return self._decode_result(result)

    @staticmethod
    def _decode_result(result: CommandResult) -> Any:
        if not result.ok:
            raise NextcloudSkillError(result.combined or f"Nextcloud-Skill Exit-Code {result.returncode}")
        raw = result.stdout.strip()
        if not raw:
            raise NextcloudSkillError("Nextcloud-Skill lieferte keine Ausgabe")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NextcloudSkillError(f"Nextcloud-Skill lieferte ungueltiges JSON: {raw[:500]}") from exc
        if not isinstance(payload, dict):
            raise NextcloudSkillError("Nextcloud-Skill lieferte kein JSON-Objekt")
        if str(payload.get("status") or "").casefold() != "success":
            raise NextcloudSkillError(str(payload.get("message") or payload.get("error") or "Nextcloud-Aufruf fehlgeschlagen"))
        return payload.get("data")

    def list_calendars(self) -> list[dict[str, Any]]:
        data = self._run(["calendars", "list", "--type", "events"])
        return [dict(item) for item in data] if isinstance(data, list) else []

    def list_addressbooks(self) -> list[dict[str, Any]]:
        data = self._run(["addressbooks", "list"])
        return [dict(item) for item in data] if isinstance(data, list) else []

    def list_contacts(self) -> list[dict[str, Any]]:
        args = ["contacts", "list"]
        if self.config.nextcloud.addressbook.strip():
            args += ["--addressbook", self.config.nextcloud.addressbook.strip()]
        data = self._run(args, timeout=90)
        return [dict(item) for item in data] if isinstance(data, list) else []

    def search_contacts(self, query: str) -> list[dict[str, Any]]:
        args = ["contacts", "search", "--query", query]
        if self.config.nextcloud.addressbook.strip():
            args += ["--addressbook", self.config.nextcloud.addressbook.strip()]
        data = self._run(args)
        return [dict(item) for item in data] if isinstance(data, list) else []

    @staticmethod
    def _iso(value: datetime | date) -> str:
        return value.isoformat()

    def create_event(self, normalized_event: Any) -> OperationResult:
        event = normalized_event.event
        title = str(event.title or "Termin").strip()[:300] or "Termin"
        description = str(event.notes or "").strip()[:4000]
        location = str(event.location or "").strip()[:500]
        args = [
            "calendar",
            "create",
            "--summary",
            title,
            "--start",
            self._iso(normalized_event.start),
            "--end",
            self._iso(normalized_event.end),
        ]
        if self.config.nextcloud.calendar.strip():
            args += ["--calendar", self.config.nextcloud.calendar.strip()]
        if description:
            args += ["--description", description]
        if location:
            args += ["--location", location]
        try:
            data = self._run(args, timeout=60)
        except NextcloudSkillError as exc:
            return OperationResult(False, "nextcloud-calendar-failed", str(exc))
        detail = "Termin ueber OpenClaw-Nextcloud-Skill angelegt"
        if isinstance(data, dict):
            uid = data.get("uid") or data.get("id")
            if uid:
                detail += f" (UID {uid})"
        return OperationResult(True, "created", detail)

    @staticmethod
    def contact_emails(contact: dict[str, Any]) -> set[str]:
        """Extract normalized addresses from common CardDAV/skill JSON shapes."""

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
                    walk(item, email_context=email_context or "email" in key_text or key_text in {"mail", "value"})

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
            # Deliberately cache email addresses only. Contact names, phone numbers,
            # notes, and organisations are not copied into the mail-agent state.
            "emails": sorted(emails),
        }
        atomic_write_bytes(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
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
            emails: set[str] = set()
            for contact in contacts:
                emails.update(self.contact_emails(contact))
            self._write_contact_cache(emails)
            self._contact_emails = emails
            self._contact_cache_source = "nextcloud"
            self._last_contact_error = ""
            return True, f"{len(emails)} Kontaktadressen ueber CardDAV/Nextcloud geladen"
        except NextcloudSkillError as exc:
            self._last_contact_error = str(exc)
            if cached:
                self._contact_emails = cached[0]
                self._contact_cache_source = "stale-cache"
                return True, f"Nextcloud nicht erreichbar; verwende alten Cache mit {len(cached[0])} Adressen: {exc}"
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
        return OperationResult(True, "nextcloud-cache-cleared", str(self.config.nextcloud.contact_cache_file))

    @staticmethod
    def _resource_aliases(item: dict[str, Any], *, kind: str) -> set[str]:
        keys = (
            ("displayName", "name", "calendar", "href", "url")
            if kind == "calendar"
            else ("displayName", "name", "addressBook", "addressbook", "href", "url")
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
    def _resource_selected(cls, items: list[dict[str, Any]], selected: str, *, kind: str) -> bool:
        wanted = (selected or "").strip().casefold()
        if not wanted:
            return bool(items)
        wanted_slug = wanted.rstrip("/").rsplit("/", 1)[-1]
        return any(
            wanted in cls._resource_aliases(item, kind=kind)
            or wanted_slug in cls._resource_aliases(item, kind=kind)
            for item in items
        )

    def health(self, *, live: bool = True) -> dict[str, Any]:
        base_url, username, token = self.credentials()
        node_ok, node_detail = self.node_health()
        result: dict[str, Any] = {
            "ok": False,
            "enabled": self.enabled,
            "skill_package": self.config.nextcloud.skill_package,
            "skill_path": str(self.script_path),
            "skill_installed": self.script_path.exists(),
            "node_ok": node_ok,
            "node": node_detail,
            "environment_ok": bool(base_url and username and token),
            "missing_environment": self.missing_environment(),
            "base_url": base_url,
            "user": username,
            "calendar": self.config.nextcloud.calendar,
            "addressbook": self.config.nextcloud.addressbook,
            "contacts_enabled": self.config.nextcloud.contacts_enabled,
            "contact_cache": str(self.config.nextcloud.contact_cache_file),
        }
        if not self.enabled:
            result["detail"] = "Nextcloud ist in mail_agent/config.toml deaktiviert"
            return result
        if not self.script_path.exists():
            result["detail"] = (
                "OpenClaw-Nextcloud-Skill fehlt. Installieren mit: "
                f"openclaw skills install {self.config.nextcloud.skill_package}"
            )
            return result
        if not node_ok:
            result["detail"] = node_detail
            return result
        if not result["environment_ok"]:
            result["detail"] = "Fehlende Umgebungsvariablen: " + ", ".join(result["missing_environment"])
            return result
        if not live:
            result["ok"] = True
            result["detail"] = "Lokale Nextcloud-Konfiguration vollstaendig; Live-Test nicht ausgefuehrt"
            return result
        try:
            calendars = self.list_calendars()
            addressbooks = self.list_addressbooks()
            result["calendars"] = [
                str(item.get("displayName") or item.get("name") or item.get("calendar") or item.get("href") or "")
                for item in calendars
            ]
            result["addressbooks"] = [
                str(item.get("displayName") or item.get("name") or item.get("addressBook") or item.get("href") or "")
                for item in addressbooks
            ]
            contacts_ok, contacts_detail = self.refresh_contact_cache(force=False)
            result["contacts_ok"] = contacts_ok
            result["contacts_detail"] = contacts_detail
            calendar_found = self._resource_selected(
                calendars,
                self.config.nextcloud.calendar,
                kind="calendar",
            )
            addressbook_found = (
                True
                if not self.config.nextcloud.contacts_enabled
                else self._resource_selected(
                    addressbooks,
                    self.config.nextcloud.addressbook,
                    kind="addressbook",
                )
            )
            result["selected_calendar_found"] = calendar_found
            result["selected_addressbook_found"] = addressbook_found
            result["ok"] = bool(calendar_found and addressbook_found and (contacts_ok or not self.config.nextcloud.contacts_enabled))
            if result["ok"]:
                result["detail"] = "Nextcloud-Kalender und CardDAV-Verbindung sind erreichbar"
            else:
                missing_resources: list[str] = []
                if not calendar_found:
                    missing_resources.append(
                        "Kalender '" + (self.config.nextcloud.calendar or "<nicht ausgewaehlt>") + "'"
                    )
                if not addressbook_found:
                    missing_resources.append(
                        "Adressbuch '" + (self.config.nextcloud.addressbook or "<nicht ausgewaehlt>") + "'"
                    )
                if not contacts_ok and self.config.nextcloud.contacts_enabled:
                    missing_resources.append("CardDAV-Kontaktabgleich")
                result["detail"] = "Nicht bereit: " + ", ".join(missing_resources)
            return result
        except NextcloudSkillError as exc:
            result["detail"] = str(exc)
            return result
