from __future__ import annotations

import os
import tomllib
from dataclasses import asdict
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from .config import WORKSPACE_ROOT
from .models import Resource
from .registry import ResourceRegistry
from .tool_settings import (
    DEFAULT_TOOL_SETTINGS,
    DEFAULT_WORKSPACE_OUTBOX,
    CalendarMailToolSettings,
    DirectCalendarToolSettings,
    DirectContactsToolSettings,
    DirectTasksToolSettings,
    DeckOrdersToolSettings,
    InvoiceToolSettings,
    MailMoveToolSettings,
    MailToolSettings,
    NextcloudToolSettings,
    NextcloudWorkspaceToolSettings,
    PortfolioToolSettings,
    ToolSettings,
    clean_remote_path,
    load_tool_settings,
)


def _toml_string(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _write_tools(path: Path, settings: ToolSettings) -> Path | None:
    backup: Path | None = None
    if path.exists():
        old = path.read_bytes()
    else:
        old = b""

    mail = settings.mail
    invoices = mail.invoices
    calendar = mail.calendar_mail
    mail_move = mail.move
    workspace = settings.nextcloud.workspace
    direct_calendar = settings.nextcloud.calendar
    direct_tasks = settings.nextcloud.tasks
    direct_contacts = settings.nextcloud.contacts
    deck_orders = settings.nextcloud.deck_orders
    antivirus = settings.security.antivirus
    portfolio = settings.portfolio
    lines = [
        "# Central tool configuration for the Personal Assistant.",
        "# Secrets stay in ~/.config/personal-assistant/secrets.env.",
        "",
        "[mail]",
        f"enabled = {'true' if mail.enabled else 'false'}",
        "",
        "[mail.invoices]",
        f"enabled = {'true' if invoices.enabled else 'false'}",
        f"resource_id = {_toml_string(invoices.resource_id)}",
        f"folder = {_toml_string(invoices.folder)}",
        f"organize_by_year_month = {'true' if invoices.organize_by_year_month else 'false'}",
        "",
        "[mail.calendar_mail]",
        f"enabled = {'true' if calendar.enabled else 'false'}",
        f"subject_prefix = {_toml_string(calendar.subject_prefix)}",
        "sender_addresses = [" + ", ".join(_toml_string(v) for v in calendar.sender_addresses) + "]",
        f"calendar_resource_id = {_toml_string(calendar.calendar_resource_id)}",
        "",
        "[mail.move]",
        f"enabled = {'true' if mail_move.enabled else 'false'}",
        f"resource_id = {_toml_string(mail_move.resource_id)}",
        f"max_batch = {mail_move.max_batch}",
        "denied_destinations = [" + ", ".join(_toml_string(v) for v in mail_move.denied_destinations) + "]",
        "denied_sources = [" + ", ".join(_toml_string(v) for v in mail_move.denied_sources) + "]",
        "",
        "[nextcloud.workspace]",
        f"enabled = {'true' if workspace.enabled else 'false'}",
        f"resource_id = {_toml_string(workspace.resource_id)}",
        f"root = {_toml_string(workspace.root)}",
        f"outbox = {_toml_string(str(workspace.outbox))}",
        f"allow_mkdir = {'true' if workspace.allow_mkdir else 'false'}",
        f"allow_upload = {'true' if workspace.allow_upload else 'false'}",
        f"allow_write_text = {'true' if workspace.allow_write_text else 'false'}",
        f"allow_move = {'true' if workspace.allow_move else 'false'}",
        "",
        "[nextcloud.calendar]",
        f"enabled = {'true' if direct_calendar.enabled else 'false'}",
        f"resource_id = {_toml_string(direct_calendar.resource_id)}",
        f"allow_create = {'true' if direct_calendar.allow_create else 'false'}",
        f"allow_list = {'true' if direct_calendar.allow_list else 'false'}",
        f"allow_update = {'true' if direct_calendar.allow_update else 'false'}",
        f"timezone = {_toml_string(direct_calendar.timezone)}",
        f"default_duration_minutes = {direct_calendar.default_duration_minutes}",
        f"max_duration_hours = {direct_calendar.max_duration_hours}",
        f"max_future_days = {direct_calendar.max_future_days}",
        "",
        "[nextcloud.tasks]",
        f"enabled = {'true' if direct_tasks.enabled else 'false'}",
        f"resource_id = {_toml_string(direct_tasks.resource_id)}",
        f"allow_create = {'true' if direct_tasks.allow_create else 'false'}",
        f"allow_list = {'true' if direct_tasks.allow_list else 'false'}",
        f"allow_update = {'true' if direct_tasks.allow_update else 'false'}",
        f"timezone = {_toml_string(direct_tasks.timezone)}",
        f"max_future_days = {direct_tasks.max_future_days}",
        "",
        "[nextcloud.contacts]",
        f"enabled = {'true' if direct_contacts.enabled else 'false'}",
        f"resource_id = {_toml_string(direct_contacts.resource_id)}",
        f"allow_list = {'true' if direct_contacts.allow_list else 'false'}",
        f"allow_create = {'true' if direct_contacts.allow_create else 'false'}",
        f"allow_update = {'true' if direct_contacts.allow_update else 'false'}",
        f"max_results = {direct_contacts.max_results}",
        "",
        "[nextcloud.deck_orders]",
        f"enabled = {'true' if deck_orders.enabled else 'false'}",
        f"resource_id = {_toml_string(deck_orders.resource_id)}",
        f"board_id = {deck_orders.board_id}",
        f"board_title = {_toml_string(deck_orders.board_title)}",
        f"allow_read = {'true' if deck_orders.allow_read else 'false'}",
        f"allow_create = {'true' if deck_orders.allow_create else 'false'}",
        f"allow_update = {'true' if deck_orders.allow_update else 'false'}",
        f"allow_move = {'true' if deck_orders.allow_move else 'false'}",
        f"auto_process_mail = {'true' if deck_orders.auto_process_mail else 'false'}",
        f"min_confidence = {deck_orders.min_confidence:.3f}",
        f"database = {_toml_string(str(deck_orders.database))}",
        "",
        "[security.antivirus]",
        f"enabled = {'true' if antivirus.enabled else 'false'}",
        f"binary = {_toml_string(antivirus.binary)}",
        f"fallback_binary = {_toml_string(antivirus.fallback_binary)}",
        f"allow_standalone_fallback = {'true' if antivirus.allow_standalone_fallback else 'false'}",
        f"daemon_service = {_toml_string(antivirus.daemon_service)}",
        f"freshclam_service = {_toml_string(antivirus.freshclam_service)}",
        f"fail_closed = {'true' if antivirus.fail_closed else 'false'}",
        f"scan_raw_mail = {'true' if antivirus.scan_raw_mail else 'false'}",
        f"scan_attachments = {'true' if antivirus.scan_attachments else 'false'}",
        f"cache_hours = {antivirus.cache_hours}",
        f"max_scan_bytes = {antivirus.max_scan_bytes}",
        f"timeout_seconds = {antivirus.timeout_seconds}",
        f"temp_dir = {_toml_string(str(antivirus.temp_dir))}",
        "",
        "[portfolio]",
        f"enabled = {'true' if portfolio.enabled else 'false'}",
        f"database = {_toml_string(str(portfolio.database))}",
        f"import_root = {_toml_string(str(portfolio.import_root))}",
        f"nextcloud_folder = {_toml_string(portfolio.nextcloud_folder)}",
        f"provider = {_toml_string(portfolio.provider)}",
        f"api_key_env = {_toml_string(portfolio.api_key_env)}",
        f"interval_minutes = {portfolio.interval_minutes}",
        f"stale_warning_minutes = {portfolio.stale_warning_minutes}",
        f"stale_critical_minutes = {portfolio.stale_critical_minutes}",
        f"request_timeout_seconds = {portfolio.request_timeout_seconds}",
        f"max_symbols = {portfolio.max_symbols}",
        f"timezone = {_toml_string(portfolio.timezone)}",
        f"market_open = {_toml_string(portfolio.market_open)}",
        f"market_close = {_toml_string(portfolio.market_close)}",
        "",
    ]
    rendered = "\n".join(lines).encode("utf-8")
    if old == rendered:
        return None
    if old:
        backup = path.with_name(path.name + ".backup")
        counter = 1
        while backup.exists():
            backup = path.with_name(path.name + f".backup-{counter}")
            counter += 1
        backup.write_bytes(old)
        os.chmod(backup, 0o600)
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(rendered)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return backup


def _mail_defaults() -> tuple[str, str, str]:
    path = WORKSPACE_ROOT / "mail_agent/config.toml"
    if not path.exists():
        return "", "", ""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    mailbox = data.get("mailbox", {}) if isinstance(data.get("mailbox", {}), dict) else {}
    calendar = data.get("calendar", {}) if isinstance(data.get("calendar", {}), dict) else {}
    owner = str(calendar.get("approval_reply_from") or mailbox.get("forward_to") or "").strip()
    owner = parseaddr(owner)[1].strip().casefold()
    old_senders = calendar.get("command_sender_addresses", [])
    if not owner and isinstance(old_senders, list):
        for value in old_senders:
            candidate = parseaddr(str(value))[1].strip().casefold()
            if candidate:
                owner = candidate
                break
    old_resource = str(calendar.get("command_calendar_resource_id") or "").strip()
    old_prefix = str(calendar.get("command_subject_prefix") or "[ASSISTENT TERMIN]").strip()
    return owner, old_resource, old_prefix


def _resource_supports_component(resource: Resource, component: str) -> bool:
    components = {
        str(value).upper()
        for value in resource.metadata.get("components", [])
        if str(value).strip()
    }
    # Legacy resources have no component metadata. Preserve compatibility for
    # manual setup, while live discovery/configure always supplies it.
    return not components or component.upper() in components


def _choose_calendar(registry: ResourceRegistry, *, component: str = "VEVENT") -> str:
    candidates = [
        item for item in registry.list(kind="calendar")
        if item.enabled and _resource_supports_component(item, component)
    ]
    for item in candidates:
        name = str(item.metadata.get("name") or "").casefold()
        if name == "personal":
            return item.id
    for item in candidates:
        name = str(item.metadata.get("name") or "").casefold()
        if "birthday" not in name and "geburtstag" not in name:
            return item.id
    return candidates[0].id if candidates else ""


def _updated_settings(
    existing: ToolSettings,
    *,
    mail: MailToolSettings | None = None,
    workspace: NextcloudWorkspaceToolSettings | None = None,
    direct_calendar: DirectCalendarToolSettings | None = None,
    direct_tasks: DirectTasksToolSettings | None = None,
    direct_contacts: DirectContactsToolSettings | None = None,
    deck_orders: DeckOrdersToolSettings | None = None,
    portfolio: PortfolioToolSettings | None = None,
) -> ToolSettings:
    return ToolSettings(
        path=existing.path,
        mail=mail or existing.mail,
        nextcloud=NextcloudToolSettings(
            workspace=workspace or existing.nextcloud.workspace,
            calendar=direct_calendar or existing.nextcloud.calendar,
            tasks=direct_tasks or existing.nextcloud.tasks,
            contacts=direct_contacts or existing.nextcloud.contacts,
            deck_orders=deck_orders or existing.nextcloud.deck_orders,
        ),
        security=existing.security,
        portfolio=portfolio or existing.portfolio,
    )


def configure_portfolio_tools(
    *,
    enable: bool = True,
    provider: str = "eodhd",
    interval_minutes: int = 15,
    stale_warning_minutes: int = 45,
    stale_critical_minutes: int = 90,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    existing = load_tool_settings(path)
    if enable and not approve_permissions:
        raise PermissionError(
            "Portfolio-Marktdatenzugriff benoetigt --approve-permissions"
        )
    provider = provider.strip().casefold()
    if provider not in {"disabled", "eodhd"}:
        raise ValueError("Portfolio-Anbieter muss disabled oder eodhd sein")
    if interval_minutes not in {15, 30}:
        raise ValueError("Portfolio-Intervall muss 15 oder 30 Minuten sein")
    warning = max(interval_minutes, min(int(stale_warning_minutes), 1440))
    critical = max(interval_minutes, min(int(stale_critical_minutes), 2880))
    if critical < warning:
        raise ValueError("Kritische Veraltung muss mindestens der Warnschwelle entsprechen")
    portfolio = PortfolioToolSettings(
        enabled=bool(enable),
        database=existing.portfolio.database,
        import_root=existing.portfolio.import_root,
        nextcloud_folder=existing.portfolio.nextcloud_folder,
        provider=provider if enable else "disabled",
        api_key_env="PORTFOLIO_EODHD_API_KEY",
        interval_minutes=interval_minutes,
        stale_warning_minutes=warning,
        stale_critical_minutes=critical,
        request_timeout_seconds=existing.portfolio.request_timeout_seconds,
        max_symbols=existing.portfolio.max_symbols,
        timezone=existing.portfolio.timezone,
        market_open=existing.portfolio.market_open,
        market_close=existing.portfolio.market_close,
    )
    if enable:
        portfolio.import_root.mkdir(parents=True, exist_ok=True)
        os.chmod(portfolio.import_root, 0o700)
    settings = _updated_settings(existing, portfolio=portfolio)
    backup = _write_tools(Path(path), settings)
    configured = load_tool_settings(path)
    return {
        "ok": True,
        "tools_file": str(configured.path),
        "backup": str(backup or ""),
        "portfolio": asdict(configured.portfolio),
        "api_key_env": configured.portfolio.api_key_env,
        "secret_stored": False,
        "job_enabled": False,
        "detail": (
            "API-Schluessel separat im Host-Secrets-Verzeichnis setzen; "
            "Portfolio-Job bleibt bis 'jobs on portfolio' aus."
        ),
    }


def configure_mail_tools(
    *,
    owner_email: str = "",
    calendar_resource_id: str = "",
    invoice_folder: str = "Assistent/Rechnungen",
    enable_invoices: bool = True,
    enable_calendar_mail: bool = True,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    existing = load_tool_settings(path)
    registry_path = WORKSPACE_ROOT / "personal_assistant/resources.toml"
    registry = ResourceRegistry(registry_path)
    inferred_owner, old_resource, old_prefix = _mail_defaults()
    existing_owner = existing.mail.calendar_mail.sender_addresses[0] if existing.mail.calendar_mail.sender_addresses else ""
    owner = parseaddr(owner_email or inferred_owner or existing_owner)[1].strip().casefold()
    resource_id = (
        calendar_resource_id
        or old_resource
        or existing.mail.calendar_mail.calendar_resource_id
        or _choose_calendar(registry, component="VEVENT")
    )
    warnings: list[str] = []

    calendar_enabled = bool(enable_calendar_mail)
    if calendar_enabled and not owner:
        calendar_enabled = False
        warnings.append("Kalender-Befehlsmail blieb deaktiviert: keine Eigentuermer-Mailadresse ermittelbar")
    if calendar_enabled and not resource_id:
        calendar_enabled = False
        warnings.append("Kalender-Befehlsmail blieb deaktiviert: kein Kalender gefunden")

    resource_backup = ""
    if calendar_enabled:
        resource = registry.get(resource_id)
        if "create" not in resource.permissions:
            if not approve_permissions:
                calendar_enabled = False
                warnings.append("Kalender-Befehlsmail blieb deaktiviert: create-Recht wurde nicht freigegeben")
            else:
                updated = Resource(
                    id=resource.id,
                    kind=resource.kind,
                    connector=resource.connector,
                    enabled=resource.enabled,
                    remote_id=resource.remote_id,
                    permissions=tuple(dict.fromkeys((*resource.permissions, "create"))),
                    metadata=resource.metadata,
                )
                backup = registry.upsert(updated)
                resource_backup = str(backup or "")

    invoices = InvoiceToolSettings(
        enabled=bool(enable_invoices),
        resource_id=existing.mail.invoices.resource_id or "nextcloud-files-main",
        folder=clean_remote_path(invoice_folder or "Assistent/Rechnungen", field_name="Rechnungsordner"),
        organize_by_year_month=existing.mail.invoices.organize_by_year_month,
    )
    calendar = CalendarMailToolSettings(
        enabled=calendar_enabled,
        subject_prefix=existing.mail.calendar_mail.subject_prefix or old_prefix or "[ASSISTENT TERMIN]",
        sender_addresses=(owner,) if owner else (),
        calendar_resource_id=resource_id,
    )
    mail = MailToolSettings(enabled=True, invoices=invoices, calendar_mail=calendar, move=existing.mail.move)
    configured_settings = _updated_settings(existing, mail=mail)
    backup = _write_tools(Path(path), configured_settings)
    configured = load_tool_settings(path)
    return {
        "ok": True,
        "tools_file": str(configured.path),
        "backup": str(backup or ""),
        "resource_backup": resource_backup,
        "mail": asdict(configured.mail),
        "workspace": asdict(configured.nextcloud.workspace),
        "warnings": warnings,
    }


def configure_mail_move_tools(
    *,
    enable: bool = True,
    max_batch: int = 1,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    existing = load_tool_settings(path)
    registry = ResourceRegistry(WORKSPACE_ROOT / "personal_assistant/resources.toml")
    resource_id = existing.mail.move.resource_id or "mail-agent"
    resource = registry.get(resource_id)
    if resource.connector not in {"local", "mail-agent"} and resource.kind not in {"tool", "mail"}:
        raise ValueError("Mail-Ressource ist nicht fuer das lokale Mail-Werkzeug vorgesehen")
    required = {"read"}
    if enable:
        required.update({"forward", "move"})
    missing = required - set(resource.permissions)
    if missing and not approve_permissions:
        raise PermissionError(
            "Direkte Mail-Rechte muessen explizit freigegeben werden: " + ", ".join(sorted(missing))
        )
    updated = Resource(
        id=resource.id, kind=resource.kind, connector=resource.connector, enabled=resource.enabled,
        remote_id=resource.remote_id,
        permissions=tuple(dict.fromkeys((*resource.permissions, *sorted(required)))),
        metadata=resource.metadata,
    )
    resource_backup = registry.upsert(updated) if updated != resource else None
    move = MailMoveToolSettings(
        enabled=bool(enable), resource_id=resource_id, max_batch=max(1, min(int(max_batch), 20)),
        denied_destinations=existing.mail.move.denied_destinations,
        denied_sources=existing.mail.move.denied_sources,
    )
    mail = MailToolSettings(
        enabled=existing.mail.enabled, invoices=existing.mail.invoices,
        calendar_mail=existing.mail.calendar_mail, move=move,
    )
    settings = _updated_settings(existing, mail=mail)
    backup = _write_tools(Path(path), settings)
    configured = load_tool_settings(path)
    return {
        "ok": True, "tools_file": str(configured.path), "backup": str(backup or ""),
        "resource_backup": str(resource_backup or ""), "mail_move": asdict(configured.mail.move),
        "permissions": list(updated.permissions),
        "delete_allowed": False, "expunge_allowed": False, "folder_changes_allowed": False,
    }


def configure_calendar_tools(
    *,
    resource_id: str = "",
    timezone: str = "Europe/Berlin",
    allow_create: bool = True,
    allow_list: bool = True,
    allow_update: bool = False,
    default_duration_minutes: int = 60,
    max_duration_hours: int = 168,
    max_future_days: int = 730,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    if allow_update and not allow_list:
        raise ValueError("Kalender-Aktualisierung benoetigt Leserechte")
    existing = load_tool_settings(path)
    registry = ResourceRegistry(WORKSPACE_ROOT / "personal_assistant/resources.toml")
    resource_id = (
        resource_id
        or existing.nextcloud.calendar.resource_id
        or existing.mail.calendar_mail.calendar_resource_id
        or _choose_calendar(registry, component="VEVENT")
    )
    if not resource_id:
        raise ValueError("Kein Nextcloud-Kalender gefunden")
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise ValueError(f"Ungueltige IANA-Zeitzone: {timezone}") from exc

    resource = registry.get(resource_id)
    if resource.connector != "nextcloud" or resource.kind != "calendar":
        raise ValueError("Kalender-Ressource muss eine Nextcloud calendar Ressource sein")
    if not _resource_supports_component(resource, "VEVENT"):
        raise ValueError("Kalender-Ressource bewirbt keine VEVENT-Unterstuetzung")
    required: set[str] = set()
    if allow_list:
        required.add("read")
    if allow_create:
        required.add("create")
    if allow_update:
        required.add("update")
    missing = required - set(resource.permissions)
    if missing and not approve_permissions:
        raise PermissionError(
            "Kalenderrechte muessen explizit freigegeben werden: " + ", ".join(sorted(missing))
        )
    updated = Resource(
        id=resource.id,
        kind=resource.kind,
        connector=resource.connector,
        enabled=resource.enabled,
        remote_id=resource.remote_id,
        permissions=tuple(dict.fromkeys((*resource.permissions, *sorted(required)))),
        metadata=resource.metadata,
    )
    resource_backup = None
    if updated != resource:
        resource_backup = registry.upsert(updated)

    direct = DirectCalendarToolSettings(
        enabled=True,
        resource_id=resource_id,
        allow_create=allow_create,
        allow_list=allow_list,
        allow_update=allow_update,
        timezone=timezone,
        default_duration_minutes=max(5, min(int(default_duration_minutes), 1440)),
        max_duration_hours=max(1, min(int(max_duration_hours), 24 * 31)),
        max_future_days=max(1, min(int(max_future_days), 3650)),
    )
    settings = _updated_settings(existing, direct_calendar=direct)
    backup = _write_tools(Path(path), settings)
    configured = load_tool_settings(path)
    return {
        "ok": True,
        "tools_file": str(configured.path),
        "backup": str(backup or ""),
        "resource_backup": str(resource_backup or ""),
        "calendar": asdict(configured.nextcloud.calendar),
        "permissions": list(updated.permissions),
        "update_allowed": bool(allow_update),
        "delete_allowed": False,
    }


def configure_tasks_tools(
    *,
    resource_id: str = "",
    timezone: str = "Europe/Berlin",
    allow_create: bool = True,
    allow_list: bool = True,
    allow_update: bool = False,
    max_future_days: int = 3650,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    if allow_update and not allow_list:
        raise ValueError("Aufgaben-Aktualisierung benoetigt Leserechte")
    existing = load_tool_settings(path)
    registry = ResourceRegistry(WORKSPACE_ROOT / "personal_assistant/resources.toml")
    resource_id = (
        resource_id
        or existing.nextcloud.tasks.resource_id
        or existing.nextcloud.calendar.resource_id
        or existing.mail.calendar_mail.calendar_resource_id
        or _choose_calendar(registry, component="VTODO")
    )
    if not resource_id:
        raise ValueError("Keine Nextcloud-Aufgabenliste bzw. kein CalDAV-Kalender gefunden")
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise ValueError(f"Ungueltige IANA-Zeitzone: {timezone}") from exc

    resource = registry.get(resource_id)
    if resource.connector != "nextcloud" or resource.kind != "calendar":
        raise ValueError("Aufgaben-Ressource muss eine Nextcloud calendar Ressource sein")
    if not _resource_supports_component(resource, "VTODO"):
        raise ValueError("Aufgaben-Ressource bewirbt keine VTODO-Unterstuetzung")
    required: set[str] = set()
    if allow_list:
        required.add("read")
    if allow_create:
        required.add("create")
    if allow_update:
        required.add("update")
    missing = required - set(resource.permissions)
    if missing and not approve_permissions:
        raise PermissionError(
            "Aufgabenrechte muessen explizit freigegeben werden: " + ", ".join(sorted(missing))
        )
    updated = Resource(
        id=resource.id,
        kind=resource.kind,
        connector=resource.connector,
        enabled=resource.enabled,
        remote_id=resource.remote_id,
        permissions=tuple(dict.fromkeys((*resource.permissions, *sorted(required)))),
        metadata=resource.metadata,
    )
    resource_backup = None
    if updated != resource:
        resource_backup = registry.upsert(updated)

    direct = DirectTasksToolSettings(
        enabled=True,
        resource_id=resource_id,
        allow_create=allow_create,
        allow_list=allow_list,
        allow_update=allow_update,
        timezone=timezone,
        max_future_days=max(1, min(int(max_future_days), 3650)),
    )
    settings = _updated_settings(existing, direct_tasks=direct)
    backup = _write_tools(Path(path), settings)
    configured = load_tool_settings(path)
    return {
        "ok": True,
        "tools_file": str(configured.path),
        "backup": str(backup or ""),
        "resource_backup": str(resource_backup or ""),
        "tasks": asdict(configured.nextcloud.tasks),
        "permissions": list(updated.permissions),
        "delete_allowed": False,
        "overwrite_allowed": False,
        "update_allowed": bool(allow_update),
    }


def configure_contacts_tools(
    *,
    resource_id: str,
    allow_create: bool = True,
    allow_list: bool = True,
    allow_update: bool = False,
    max_results: int = 500,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    if allow_update and not allow_list:
        raise ValueError("Kontakt-Aktualisierung benoetigt Leserechte")
    existing = load_tool_settings(path)
    registry = ResourceRegistry(WORKSPACE_ROOT / "personal_assistant/resources.toml")
    resource = registry.get(resource_id)
    if resource.connector != "nextcloud" or resource.kind != "addressbook":
        raise ValueError("Kontakt-Ressource muss ein Nextcloud-Adressbuch sein")
    required: set[str] = set()
    if allow_list:
        required.add("read")
    if allow_create:
        required.add("create")
    if allow_update:
        required.add("update")
    missing = required - set(resource.permissions)
    if missing and not approve_permissions:
        raise PermissionError(
            "Kontaktrechte muessen explizit freigegeben werden: " + ", ".join(sorted(missing))
        )
    updated = Resource(
        id=resource.id,
        kind=resource.kind,
        connector=resource.connector,
        enabled=resource.enabled,
        remote_id=resource.remote_id,
        permissions=tuple(dict.fromkeys((*resource.permissions, *sorted(required)))),
        metadata=resource.metadata,
    )
    resource_backup = registry.upsert(updated) if updated != resource else None
    contacts = DirectContactsToolSettings(
        enabled=True,
        resource_id=resource_id,
        allow_list=allow_list,
        allow_create=allow_create,
        allow_update=allow_update,
        max_results=max(1, min(int(max_results), 5000)),
    )
    settings = _updated_settings(existing, direct_contacts=contacts)
    backup = _write_tools(Path(path), settings)
    configured = load_tool_settings(path)
    return {
        "ok": True,
        "tools_file": str(configured.path),
        "backup": str(backup or ""),
        "resource_backup": str(resource_backup or ""),
        "contacts": asdict(configured.nextcloud.contacts),
        "permissions": list(updated.permissions),
        "update_allowed": bool(allow_update),
        "delete_allowed": False,
        "create_only": bool(allow_create and not allow_update),
    }


def configure_workspace_tools(
    *,
    resource_id: str = "nextcloud-files-main",
    root: str = "Assistent",
    outbox: str | Path = DEFAULT_WORKSPACE_OUTBOX,
    allow_mkdir: bool = True,
    allow_upload: bool = True,
    allow_write_text: bool = True,
    allow_move: bool = True,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    existing = load_tool_settings(path)
    registry = ResourceRegistry(WORKSPACE_ROOT / "personal_assistant/resources.toml")
    root = clean_remote_path(root, field_name="Workspace-Wurzel")
    outbox_path = Path(outbox).expanduser().resolve()
    try:
        outbox_path.relative_to(WORKSPACE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Workspace-Outbox muss innerhalb des OpenClaw-Workspace liegen") from exc

    resource = registry.get(resource_id)
    if resource.connector != "nextcloud" or resource.kind != "file-root":
        raise ValueError("Workspace-Ressource muss eine Nextcloud file-root Ressource sein")
    required = {"read"}
    if allow_mkdir or allow_upload or allow_write_text:
        required.add("create")
    if allow_move:
        required.add("move")
    missing = required - set(resource.permissions)
    if missing and not approve_permissions:
        raise PermissionError(
            "Workspace-Rechte muessen explizit freigegeben werden: " + ", ".join(sorted(missing))
        )

    roots = [str(value).strip("/") for value in resource.metadata.get("allowed_roots", []) if str(value).strip("/")]
    if root not in roots:
        roots.append(root)
    updated = Resource(
        id=resource.id,
        kind=resource.kind,
        connector=resource.connector,
        enabled=resource.enabled,
        remote_id=resource.remote_id,
        permissions=tuple(dict.fromkeys((*resource.permissions, *sorted(required)))),
        metadata={**resource.metadata, "allowed_roots": roots},
    )
    resource_backup = None
    if updated != resource:
        resource_backup = registry.upsert(updated)

    workspace = NextcloudWorkspaceToolSettings(
        enabled=True,
        resource_id=resource_id,
        root=root,
        outbox=outbox_path,
        allow_mkdir=allow_mkdir,
        allow_upload=allow_upload,
        allow_write_text=allow_write_text,
        allow_move=allow_move,
    )
    outbox_path.mkdir(parents=True, exist_ok=True)
    os.chmod(outbox_path, 0o700)
    settings = _updated_settings(existing, workspace=workspace)
    backup = _write_tools(Path(path), settings)
    configured = load_tool_settings(path)
    return {
        "ok": True,
        "tools_file": str(configured.path),
        "backup": str(backup or ""),
        "resource_backup": str(resource_backup or ""),
        "workspace": asdict(configured.nextcloud.workspace),
        "permissions": list(updated.permissions),
        "allowed_roots": roots,
    }


def configure_deck_orders_tools(
    *,
    board_id: int,
    board_title: str = "Bestellungen",
    allow_read: bool = True,
    allow_create: bool = True,
    allow_update: bool = True,
    allow_move: bool = True,
    auto_process_mail: bool = True,
    min_confidence: float = 0.82,
    approve_permissions: bool = False,
    path: Path = DEFAULT_TOOL_SETTINGS,
) -> dict[str, Any]:
    if int(board_id) <= 0:
        raise ValueError("Eine positive Deck board_id ist erforderlich")
    existing = load_tool_settings(path)
    registry = ResourceRegistry(WORKSPACE_ROOT / "personal_assistant/resources.toml")
    resource_id = "nextcloud-deck-orders"
    required = set()
    if allow_read: required.add("read")
    if allow_create: required.add("create")
    if allow_update: required.add("update")
    if allow_move: required.add("move")
    if required - {"read"} and not approve_permissions:
        raise PermissionError("Deck-Schreibrechte benoetigen --approve-permissions")
    old = registry.resources.get(resource_id)
    resource = Resource(
        id=resource_id, kind="deck-board", connector="nextcloud", enabled=True,
        remote_id=str(int(board_id)), permissions=tuple(sorted(required)),
        metadata={"name": board_title[:100], "managed_by": "personal-assistant", "board_id": int(board_id)},
    )
    resource_backup = registry.upsert(resource) if old != resource else None
    deck = DeckOrdersToolSettings(
        enabled=True, resource_id=resource_id, board_id=int(board_id), board_title=board_title[:100],
        allow_read=allow_read, allow_create=allow_create, allow_update=allow_update, allow_move=allow_move,
        auto_process_mail=auto_process_mail, min_confidence=max(0.5, min(float(min_confidence), 1.0)),
        database=existing.nextcloud.deck_orders.database,
    )
    settings = _updated_settings(existing, deck_orders=deck)
    backup = _write_tools(Path(path), settings)
    configured = load_tool_settings(path)
    return {
        "ok": True, "tools_file": str(configured.path), "backup": str(backup or ""),
        "resource_backup": str(resource_backup or ""), "deck_orders": asdict(configured.nextcloud.deck_orders),
        "permissions": list(resource.permissions), "managed_only": True, "delete_allowed": False,
    }
