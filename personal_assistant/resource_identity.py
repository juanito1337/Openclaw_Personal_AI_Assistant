from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .models import Resource
from .registry import ResourceRegistry
from .tool_settings import ToolSettings, load_tool_settings


@dataclass(slots=True, frozen=True)
class ResourceReference:
    domain: str
    setting: str
    enabled: bool
    resource_id: str
    kind: str
    component: str
    required_permissions: tuple[str, ...]
    business_path: str = ""


class ResourceIdentityError(RuntimeError):
    def __init__(self, code: str, detail: str, *, resource_id: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.resource_id = resource_id

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "detail": self.detail,
            "resource_id": self.resource_id,
        }


def _required(enabled: bool, *permissions: tuple[bool, str]) -> tuple[str, ...]:
    if not enabled:
        return ()
    return tuple(permission for selected, permission in permissions if selected)


def configured_references(settings: ToolSettings) -> tuple[ResourceReference, ...]:
    workspace = settings.nextcloud.workspace
    calendar = settings.nextcloud.calendar
    tasks = settings.nextcloud.tasks
    contacts = settings.nextcloud.contacts
    invoices = settings.mail.invoices
    mail = settings.mail.move
    portfolio = settings.portfolio
    return (
        ResourceReference(
            "calendar",
            "nextcloud.calendar.resource_id",
            calendar.enabled,
            calendar.resource_id,
            "calendar",
            "VEVENT",
            _required(
                calendar.enabled,
                (calendar.allow_list, "read"),
                (calendar.allow_create, "create"),
                (calendar.allow_update, "update"),
            ),
        ),
        ResourceReference(
            "mail-calendar",
            "mail.calendar_mail.calendar_resource_id",
            settings.mail.calendar_mail.enabled,
            settings.mail.calendar_mail.calendar_resource_id,
            "calendar",
            "VEVENT",
            ("create",) if settings.mail.calendar_mail.enabled else (),
        ),
        ResourceReference(
            "tasks",
            "nextcloud.tasks.resource_id",
            tasks.enabled,
            tasks.resource_id,
            "calendar",
            "VTODO",
            _required(
                tasks.enabled,
                (tasks.allow_list, "read"),
                (tasks.allow_create, "create"),
                (tasks.allow_update, "update"),
            ),
        ),
        ResourceReference(
            "contacts",
            "nextcloud.contacts.resource_id",
            contacts.enabled,
            contacts.resource_id,
            "addressbook",
            "VCARD",
            _required(
                contacts.enabled,
                (contacts.allow_list, "read"),
                (contacts.allow_create, "create"),
                (contacts.allow_update, "update"),
            ),
        ),
        ResourceReference(
            "nextcloud-files",
            "nextcloud.workspace.resource_id",
            workspace.enabled,
            workspace.resource_id,
            "file-root",
            "DAV-COLLECTION",
            _required(
                workspace.enabled,
                (True, "read"),
                (
                    workspace.allow_mkdir or workspace.allow_upload or workspace.allow_write_text,
                    "create",
                ),
                (workspace.allow_move, "move"),
            ),
            workspace.root,
        ),
        ResourceReference(
            "invoices",
            "mail.invoices.resource_id",
            invoices.enabled,
            invoices.resource_id,
            "file-root",
            "DAV-COLLECTION",
            ("read", "create") if invoices.enabled else (),
            invoices.folder,
        ),
        ResourceReference(
            "mail-folders",
            "mail.move.resource_id",
            mail.enabled,
            mail.resource_id,
            "email-service",
            "IMAP-MAILBOXES",
            ("read", "move") if mail.enabled else (),
        ),
        ResourceReference(
            "portfolio-files",
            "nextcloud.workspace.resource_id",
            portfolio.enabled and workspace.enabled,
            workspace.resource_id,
            "file-root",
            "DAV-COLLECTION",
            ("read",) if portfolio.enabled and workspace.enabled else (),
            portfolio.nextcloud_folder,
        ),
    )


def resolve_reference(registry: ResourceRegistry, reference: ResourceReference) -> Resource:
    resource_id = reference.resource_id.strip()
    if not resource_id:
        raise ResourceIdentityError(
            "resource-id-missing",
            f"{reference.setting} ist leer",
        )
    if resource_id in getattr(registry, "duplicate_ids", []):
        raise ResourceIdentityError(
            "resource-id-duplicate",
            f"Ressourcen-ID {resource_id!r} ist mehrfach registriert",
            resource_id=resource_id,
        )
    try:
        resource = registry.get(resource_id)
    except KeyError:
        raise ResourceIdentityError(
            "resource-id-stale",
            f"Ressourcen-ID {resource_id!r} ist nicht registriert",
            resource_id=resource_id,
        ) from None
    if not resource.enabled:
        raise ResourceIdentityError(
            "resource-disabled",
            f"Ressource {resource_id!r} ist deaktiviert",
            resource_id=resource_id,
        )
    if resource.kind != reference.kind:
        raise ResourceIdentityError(
            "resource-kind-mismatch",
            f"Ressource {resource_id!r} hat Typ {resource.kind!r} statt {reference.kind!r}",
            resource_id=resource_id,
        )
    components = {
        str(value).upper() for value in resource.metadata.get("components", []) if str(value).strip()
    }
    if reference.component in {"VEVENT", "VTODO"} and components and reference.component not in components:
        raise ResourceIdentityError(
            "resource-component-missing",
            f"Ressource {resource_id!r} unterstuetzt {reference.component} nicht",
            resource_id=resource_id,
        )
    missing = sorted(set(reference.required_permissions) - set(resource.permissions))
    if missing:
        raise ResourceIdentityError(
            "resource-permission-missing",
            f"Ressource {resource_id!r} hat keine bestaetigten Rechte: {', '.join(missing)}",
            resource_id=resource_id,
        )
    return resource


def select_exact_discovered(
    values: Iterable[Any],
    resource_id: str,
    *,
    label: str,
) -> Any:
    selected = [item for item in values if str(getattr(item, "resource_id", "")) == resource_id]
    if not resource_id:
        raise ResourceIdentityError("resource-id-missing", f"{label}: Ressourcen-ID fehlt")
    if not selected:
        raise ResourceIdentityError(
            "resource-id-stale",
            f"{label} {resource_id!r} wurde nicht gefunden",
            resource_id=resource_id,
        )
    if len(selected) > 1:
        raise ResourceIdentityError(
            "resource-discovery-ambiguous",
            f"{label} {resource_id!r} ist mehrdeutig ({len(selected)} Treffer)",
            resource_id=resource_id,
        )
    return selected[0]


def inventory_report(
    settings: ToolSettings,
    registry: ResourceRegistry,
    *,
    missing_credentials: Iterable[str] = (),
) -> dict[str, Any]:
    credentials = sorted({str(value) for value in missing_credentials if str(value)})
    items: list[dict[str, Any]] = []
    for reference in configured_references(settings):
        payload: dict[str, Any] = {
            **asdict(reference),
            "ok": True,
            "state": "disabled" if not reference.enabled else "resolved",
            "error": None,
            "business_name": "",
            "remote_identifier": "",
            "resource_permissions": [],
        }
        if (
            reference.enabled
            and credentials
            and reference.kind
            in {
                "calendar",
                "addressbook",
                "file-root",
            }
        ):
            payload["ok"] = False
            payload["state"] = "credentials-missing"
            payload["error"] = {
                "code": "resource-credentials-missing",
                "detail": "Fehlende Umgebungsvariablen: " + ", ".join(credentials),
                "resource_id": reference.resource_id,
            }
        elif reference.enabled:
            try:
                resource = resolve_reference(registry, reference)
            except ResourceIdentityError as exc:
                payload["ok"] = False
                payload["state"] = exc.code
                payload["error"] = exc.to_dict()
            else:
                payload["business_name"] = str(resource.metadata.get("name") or "")
                payload["remote_identifier"] = resource.remote_id
                payload["resource_permissions"] = list(resource.permissions)
        items.append(payload)

    direct = next(item for item in items if item["domain"] == "calendar")
    mail = next(item for item in items if item["domain"] == "mail-calendar")
    drift = bool(direct["enabled"] and mail["enabled"] and direct["resource_id"] != mail["resource_id"])
    if drift:
        mail["ok"] = False
        mail["state"] = "resource-configuration-drift"
        mail["error"] = {
            "code": "resource-configuration-drift",
            "detail": (
                "Mail-Kalender und direktes Kalenderwerkzeug referenzieren unterschiedliche Ressourcen-IDs"
            ),
            "resource_id": str(mail["resource_id"]),
        }
    return {
        "ok": all(bool(item["ok"]) for item in items),
        "read_only": True,
        "credentials": {
            "ok": not credentials,
            "missing": credentials,
            "error_code": "resource-credentials-missing" if credentials else "",
        },
        "calendar_identity": {
            "ok": not drift,
            "direct_resource_id": direct["resource_id"],
            "mail_resource_id": mail["resource_id"],
            "same_resource": not drift,
            "error_code": "resource-configuration-drift" if drift else "",
        },
        "items": items,
    }


def _canonical_digest(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CalendarIdentityMigration:
    def __init__(self, settings_path: Path, registry: ResourceRegistry) -> None:
        self.settings_path = settings_path.expanduser().resolve()
        self.registry = registry

    def preview(self) -> dict[str, Any]:
        settings = load_tool_settings(self.settings_path)
        direct = settings.nextcloud.calendar
        mail = settings.mail.calendar_mail
        if direct.enabled:
            reference = next(item for item in configured_references(settings) if item.domain == "calendar")
            resource = resolve_reference(self.registry, reference)
            target = direct.resource_id
            action = "synchronize-mail-calendar-reference"
            target_permissions = list(resource.permissions)
        elif not mail.enabled:
            target = ""
            action = "remove-disabled-legacy-mail-calendar-reference"
            target_permissions = []
        else:
            raise ResourceIdentityError(
                "resource-migration-target-missing",
                "Aktiver Mail-Kalender kann ohne aktive direkte VEVENT-Ressource nicht migriert werden",
                resource_id=mail.calendar_resource_id,
            )
        changed = mail.calendar_resource_id != target
        basis = {
            "schema": 1,
            "operation": "calendar-resource-identity-migration",
            "settings_path": str(self.settings_path),
            "registry_path": str(self.registry.path.resolve()),
            "action": action,
            "changed": changed,
            "from_resource_id": mail.calendar_resource_id,
            "to_resource_id": target,
            "resource_permissions_before": target_permissions,
            "resource_permissions_after": target_permissions,
            "external_writes": False,
            "permission_expansion": False,
        }
        return {
            "ok": True,
            "read_only": True,
            **basis,
            "preview_sha256": _canonical_digest(basis),
            "approval_required": bool(changed),
            "approval": "explicit-user-calendar-resource-identity-migration",
        }

    def apply(self, *, expected_preview_sha256: str, approved: bool) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Kalender-Ressourcenmigration benoetigt --yes")
        preview = self.preview()
        if preview["preview_sha256"] != expected_preview_sha256:
            raise ResourceIdentityError(
                "resource-migration-preview-stale",
                "Konfiguration oder Registry haben sich seit der Vorschau geaendert",
            )
        if not preview["changed"]:
            return {**preview, "applied": False, "backup": "", "idempotent": True}

        from .tool_setup import _updated_settings, _write_tools

        settings = load_tool_settings(self.settings_path)
        mail_settings = replace(
            settings.mail,
            calendar_mail=replace(
                settings.mail.calendar_mail,
                calendar_resource_id=str(preview["to_resource_id"]),
            ),
        )
        updated = _updated_settings(settings, mail=mail_settings)
        backup = _write_tools(self.settings_path, updated)
        try:
            validated = load_tool_settings(self.settings_path)
            report = inventory_report(validated, self.registry)
            calendar_identity = report["calendar_identity"]
            if not calendar_identity["ok"]:
                raise ResourceIdentityError(
                    "resource-migration-validation-failed",
                    "Publizierte Kalenderidentitaet ist weiterhin inkonsistent",
                )
        except Exception:
            if backup and backup.exists():
                self._restore_file(backup)
            raise
        return {
            **preview,
            "applied": True,
            "backup": str(backup or ""),
            "atomic_publish": True,
            "validated": True,
        }

    def rollback(self, backup: Path, *, approved: bool) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Rollback der Kalender-Ressourcenmigration benoetigt --yes")
        candidate = backup.expanduser().resolve()
        if candidate.parent != self.settings_path.parent or not candidate.name.startswith(
            self.settings_path.name + ".backup"
        ):
            raise ResourceIdentityError(
                "resource-migration-backup-invalid",
                "Rollback akzeptiert nur ein Backup der konfigurierten tools.toml",
            )
        if not candidate.is_file():
            raise ResourceIdentityError(
                "resource-migration-backup-missing",
                "Angegebenes Konfigurationsbackup fehlt",
            )
        load_tool_settings(candidate)
        replaced_backup = self._restore_file(candidate)
        load_tool_settings(self.settings_path)
        return {
            "ok": True,
            "rolled_back": True,
            "restored_from": str(candidate),
            "replaced_configuration_backup": str(replaced_backup),
            "external_writes": False,
            "permission_expansion": False,
        }

    def _restore_file(self, source: Path) -> Path:
        current_backup = self.settings_path.with_name(self.settings_path.name + ".pre-rollback")
        counter = 1
        while current_backup.exists():
            current_backup = self.settings_path.with_name(
                self.settings_path.name + f".pre-rollback-{counter}"
            )
            counter += 1
        if self.settings_path.exists():
            shutil.copy2(self.settings_path, current_backup)
            os.chmod(current_backup, 0o600)
        temp = self.settings_path.with_name(self.settings_path.name + ".rollback.tmp")
        shutil.copy2(source, temp)
        os.chmod(temp, 0o600)
        os.replace(temp, self.settings_path)
        return current_backup


__all__ = [
    "CalendarIdentityMigration",
    "ResourceIdentityError",
    "ResourceReference",
    "configured_references",
    "inventory_report",
    "resolve_reference",
    "select_exact_discovered",
]
