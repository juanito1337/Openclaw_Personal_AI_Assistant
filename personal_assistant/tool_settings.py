from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import WORKSPACE_ROOT

DEFAULT_TOOL_SETTINGS = WORKSPACE_ROOT / "personal_assistant/tools.toml"
DEFAULT_RELEASE_TOOL_SETTINGS = Path(__file__).with_name("tool_defaults.toml")
DEFAULT_WORKSPACE_OUTBOX = WORKSPACE_ROOT / "personal_assistant/data/workspace_outbox"
DEFAULT_ANTIVIRUS_TEMP = WORKSPACE_ROOT / "personal_assistant/data/antivirus_tmp"
DEFAULT_ORDERS_DB = WORKSPACE_ROOT / "personal_assistant/data/orders.sqlite3"
DEFAULT_PORTFOLIO_DB = WORKSPACE_ROOT / "personal_assistant/data/portfolio.sqlite3"
DEFAULT_PORTFOLIO_INBOX = WORKSPACE_ROOT / "personal_assistant/data/portfolio_inbox"
DEFAULT_PORTFOLIO_NEXTCLOUD_FOLDER = "Assistent/Finanzen/Portfolio"


@dataclass(slots=True)
class InvoiceToolSettings:
    enabled: bool = False
    resource_id: str = "nextcloud-files-main"
    folder: str = "Assistent/Rechnungen"
    organize_by_year_month: bool = True


@dataclass(slots=True)
class CalendarMailToolSettings:
    enabled: bool = False
    subject_prefix: str = "[ASSISTENT TERMIN]"
    sender_addresses: tuple[str, ...] = ()
    calendar_resource_id: str = ""


@dataclass(slots=True)
class MailMoveToolSettings:
    enabled: bool = False
    resource_id: str = "mail-agent"
    max_batch: int = 1
    denied_destinations: tuple[str, ...] = (
        "trash", "papierkorb", "deleted", "deleted messages", "gelöscht",
        "junk", "spam", "spamverdacht", "agent/virusverdacht",
    )
    denied_sources: tuple[str, ...] = (
        "agent/pruefen", "agent/termin-pruefen", "agent/virusverdacht",
    )


@dataclass(slots=True)
class MailToolSettings:
    enabled: bool = True
    invoices: InvoiceToolSettings = field(default_factory=InvoiceToolSettings)
    calendar_mail: CalendarMailToolSettings = field(default_factory=CalendarMailToolSettings)
    move: MailMoveToolSettings = field(default_factory=MailMoveToolSettings)


@dataclass(slots=True)
class NextcloudWorkspaceToolSettings:
    enabled: bool = True
    resource_id: str = "nextcloud-files-main"
    root: str = "Assistent"
    outbox: Path = DEFAULT_WORKSPACE_OUTBOX
    allow_mkdir: bool = True
    allow_upload: bool = True
    allow_write_text: bool = True
    allow_move: bool = True


@dataclass(slots=True)
class DirectCalendarToolSettings:
    enabled: bool = False
    resource_id: str = ""
    allow_create: bool = True
    allow_list: bool = True
    allow_update: bool = False
    timezone: str = "Europe/Berlin"
    default_duration_minutes: int = 60
    max_duration_hours: int = 168
    max_future_days: int = 730


@dataclass(slots=True)
class DirectTasksToolSettings:
    enabled: bool = False
    resource_id: str = ""
    allow_create: bool = True
    allow_list: bool = True
    allow_update: bool = False
    timezone: str = "Europe/Berlin"
    max_future_days: int = 3650


@dataclass(slots=True)
class DirectContactsToolSettings:
    enabled: bool = False
    resource_id: str = ""
    allow_list: bool = True
    allow_create: bool = False
    allow_update: bool = False
    max_results: int = 500


@dataclass(slots=True)
class DeckOrdersToolSettings:
    enabled: bool = False
    resource_id: str = "nextcloud-deck-orders"
    board_id: int = 0
    board_title: str = "Bestellungen"
    allow_read: bool = True
    allow_create: bool = True
    allow_update: bool = True
    allow_move: bool = True
    auto_process_mail: bool = True
    min_confidence: float = 0.82
    database: Path = DEFAULT_ORDERS_DB


@dataclass(slots=True)
class NextcloudToolSettings:
    workspace: NextcloudWorkspaceToolSettings = field(default_factory=NextcloudWorkspaceToolSettings)
    calendar: DirectCalendarToolSettings = field(default_factory=DirectCalendarToolSettings)
    tasks: DirectTasksToolSettings = field(default_factory=DirectTasksToolSettings)
    contacts: DirectContactsToolSettings = field(default_factory=DirectContactsToolSettings)
    deck_orders: DeckOrdersToolSettings = field(default_factory=DeckOrdersToolSettings)


@dataclass(slots=True)
class AntivirusToolSettings:
    enabled: bool = True
    binary: str = "clamdscan"
    fallback_binary: str = "clamscan"
    allow_standalone_fallback: bool = True
    daemon_service: str = "clamav-daemon.service"
    freshclam_service: str = "clamav-freshclam.service"
    fail_closed: bool = True
    scan_raw_mail: bool = True
    scan_attachments: bool = True
    cache_hours: int = 24
    max_scan_bytes: int = 100_000_000
    timeout_seconds: int = 120
    temp_dir: Path = DEFAULT_ANTIVIRUS_TEMP


@dataclass(slots=True)
class SecurityToolSettings:
    antivirus: AntivirusToolSettings = field(default_factory=AntivirusToolSettings)


@dataclass(slots=True)
class PortfolioToolSettings:
    enabled: bool = False
    database: Path = DEFAULT_PORTFOLIO_DB
    import_root: Path = DEFAULT_PORTFOLIO_INBOX
    nextcloud_folder: str = DEFAULT_PORTFOLIO_NEXTCLOUD_FOLDER
    provider: str = "disabled"
    api_key_env: str = "PORTFOLIO_EODHD_API_KEY"
    interval_minutes: int = 15
    stale_warning_minutes: int = 45
    stale_critical_minutes: int = 90
    request_timeout_seconds: int = 20
    max_symbols: int = 100
    timezone: str = "Europe/Berlin"
    market_open: str = "08:00"
    market_close: str = "22:00"


@dataclass(slots=True)
class ToolSettings:
    path: Path
    mail: MailToolSettings = field(default_factory=MailToolSettings)
    nextcloud: NextcloudToolSettings = field(default_factory=NextcloudToolSettings)
    security: SecurityToolSettings = field(default_factory=SecurityToolSettings)
    portfolio: PortfolioToolSettings = field(default_factory=PortfolioToolSettings)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return data if isinstance(data, dict) else {}


def _merge_settings(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(defaults)
    for key, value in overrides.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_settings(existing, value)
        else:
            merged[key] = value
    return merged


def clean_remote_path(value: str, *, field_name: str = "remote path") -> str:
    value = str(value or "").replace("\\", "/").strip().strip("/")
    if not value or value == ".." or value.startswith("../") or "/../" in f"/{value}/":
        raise ValueError(f"tools.toml: ungueltiger {field_name}")
    return value


def _clean_outbox(value: str | Path) -> Path:
    path = Path(value or DEFAULT_WORKSPACE_OUTBOX).expanduser().resolve()
    workspace = WORKSPACE_ROOT.resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("tools.toml: nextcloud.workspace.outbox muss im Workspace liegen") from exc
    return path


def _clean_workspace_path(value: str | Path, *, field_name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    path = path.resolve()
    try:
        path.relative_to(WORKSPACE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"tools.toml: {field_name} muss im Workspace liegen") from exc
    return path


def load_tool_settings(
    path: str | Path | None = None,
    *,
    defaults_path: str | Path | None = None,
) -> ToolSettings:
    configured = path or os.environ.get("OPENCLAW_TOOLS_CONFIG") or DEFAULT_TOOL_SETTINGS
    config_path = Path(configured).expanduser().resolve()
    configured_defaults = (
        defaults_path
        or os.environ.get("OPENCLAW_TOOL_DEFAULTS_CONFIG")
        or DEFAULT_RELEASE_TOOL_SETTINGS
    )
    release_path = Path(configured_defaults).expanduser().resolve()
    release_data = _read_toml(release_path)
    override_data = _read_toml(config_path)
    data = _merge_settings(release_data, override_data)

    mail_data = _section(data, "mail")
    invoice_data = _section(mail_data, "invoices")
    calendar_data = _section(mail_data, "calendar_mail")
    move_data = _section(mail_data, "move")
    release_move_data = _section(_section(release_data, "mail"), "move")
    override_move_data = _section(_section(override_data, "mail"), "move")
    nextcloud_data = _section(data, "nextcloud")
    workspace_data = _section(nextcloud_data, "workspace")
    direct_calendar_data = _section(nextcloud_data, "calendar")
    direct_tasks_data = _section(nextcloud_data, "tasks")
    direct_contacts_data = _section(nextcloud_data, "contacts")
    deck_orders_data = _section(nextcloud_data, "deck_orders")
    security_data = _section(data, "security")
    antivirus_data = _section(security_data, "antivirus")
    portfolio_data = _section(data, "portfolio")

    invoices = InvoiceToolSettings(
        enabled=bool(invoice_data.get("enabled", False)),
        resource_id=str(invoice_data.get("resource_id") or "nextcloud-files-main").strip(),
        folder=clean_remote_path(
            str(invoice_data.get("folder") or "Assistent/Rechnungen"),
            field_name="Rechnungsordner",
        ),
        organize_by_year_month=bool(invoice_data.get("organize_by_year_month", True)),
    )
    senders = tuple(
        str(value).strip().casefold()
        for value in calendar_data.get("sender_addresses", [])
        if str(value).strip()
    )
    calendar_mail = CalendarMailToolSettings(
        enabled=bool(calendar_data.get("enabled", False)),
        subject_prefix=str(calendar_data.get("subject_prefix") or "[ASSISTENT TERMIN]").strip(),
        sender_addresses=senders,
        calendar_resource_id=str(calendar_data.get("calendar_resource_id") or "").strip(),
    )
    release_denied_destinations = tuple(
        str(value).strip().casefold()
        for value in release_move_data.get(
            "denied_destinations", MailMoveToolSettings().denied_destinations
        )
        if str(value).strip()
    )
    override_denied_destinations = tuple(
        str(value).strip().casefold()
        for value in override_move_data.get("denied_destinations", [])
        if str(value).strip()
    )
    denied_destinations = tuple(dict.fromkeys(
        (*release_denied_destinations, *override_denied_destinations)
    ))
    release_denied_sources = tuple(
        str(value).strip().casefold()
        for value in release_move_data.get("denied_sources", MailMoveToolSettings().denied_sources)
        if str(value).strip()
    )
    override_denied_sources = tuple(
        str(value).strip().casefold()
        for value in override_move_data.get("denied_sources", [])
        if str(value).strip()
    )
    denied_sources = tuple(dict.fromkeys((*release_denied_sources, *override_denied_sources)))
    mail_move = MailMoveToolSettings(
        enabled=bool(move_data.get("enabled", False)),
        resource_id=str(move_data.get("resource_id") or "mail-agent").strip(),
        max_batch=max(1, min(int(move_data.get("max_batch", 1)), 20)),
        denied_destinations=denied_destinations or MailMoveToolSettings().denied_destinations,
        denied_sources=denied_sources or MailMoveToolSettings().denied_sources,
    )
    direct_calendar = DirectCalendarToolSettings(
        enabled=bool(direct_calendar_data.get("enabled", False)),
        resource_id=str(direct_calendar_data.get("resource_id") or "").strip(),
        allow_create=bool(direct_calendar_data.get("allow_create", True)),
        allow_list=bool(direct_calendar_data.get("allow_list", True)),
        allow_update=bool(direct_calendar_data.get("allow_update", False)),
        timezone=str(direct_calendar_data.get("timezone") or "Europe/Berlin").strip(),
        default_duration_minutes=max(5, min(int(direct_calendar_data.get("default_duration_minutes", 60)), 1440)),
        max_duration_hours=max(1, min(int(direct_calendar_data.get("max_duration_hours", 168)), 24 * 31)),
        max_future_days=max(1, min(int(direct_calendar_data.get("max_future_days", 730)), 3650)),
    )
    direct_tasks = DirectTasksToolSettings(
        enabled=bool(direct_tasks_data.get("enabled", False)),
        resource_id=str(direct_tasks_data.get("resource_id") or "").strip(),
        allow_create=bool(direct_tasks_data.get("allow_create", True)),
        allow_list=bool(direct_tasks_data.get("allow_list", True)),
        allow_update=bool(direct_tasks_data.get("allow_update", False)),
        timezone=str(direct_tasks_data.get("timezone") or "Europe/Berlin").strip(),
        max_future_days=max(1, min(int(direct_tasks_data.get("max_future_days", 3650)), 3650)),
    )
    direct_contacts = DirectContactsToolSettings(
        enabled=bool(direct_contacts_data.get("enabled", False)),
        resource_id=str(direct_contacts_data.get("resource_id") or "").strip(),
        allow_list=bool(direct_contacts_data.get("allow_list", True)),
        allow_create=bool(direct_contacts_data.get("allow_create", False)),
        allow_update=bool(direct_contacts_data.get("allow_update", False)),
        max_results=max(1, min(int(direct_contacts_data.get("max_results", 500)), 5000)),
    )
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(direct_calendar.timezone)
        ZoneInfo(direct_tasks.timezone)
    except Exception as exc:
        raise ValueError("tools.toml: ungueltige IANA-Zeitzone") from exc

    deck_orders = DeckOrdersToolSettings(
        enabled=bool(deck_orders_data.get("enabled", False)),
        resource_id=str(deck_orders_data.get("resource_id") or "nextcloud-deck-orders").strip(),
        board_id=max(0, int(deck_orders_data.get("board_id", 0))),
        board_title=str(deck_orders_data.get("board_title") or "Bestellungen").strip()[:100],
        allow_read=bool(deck_orders_data.get("allow_read", True)),
        allow_create=bool(deck_orders_data.get("allow_create", True)),
        allow_update=bool(deck_orders_data.get("allow_update", True)),
        allow_move=bool(deck_orders_data.get("allow_move", True)),
        auto_process_mail=bool(deck_orders_data.get("auto_process_mail", True)),
        min_confidence=max(0.50, min(float(deck_orders_data.get("min_confidence", 0.82)), 1.0)),
        database=_clean_outbox(deck_orders_data.get("database") or DEFAULT_ORDERS_DB),
    )

    antivirus = AntivirusToolSettings(
        enabled=bool(antivirus_data.get("enabled", True)),
        binary=str(antivirus_data.get("binary") or "clamdscan").strip(),
        fallback_binary=str(antivirus_data.get("fallback_binary") or "clamscan").strip(),
        allow_standalone_fallback=bool(antivirus_data.get("allow_standalone_fallback", True)),
        daemon_service=str(antivirus_data.get("daemon_service") or "clamav-daemon.service").strip(),
        freshclam_service=str(antivirus_data.get("freshclam_service") or "clamav-freshclam.service").strip(),
        fail_closed=bool(antivirus_data.get("fail_closed", True)),
        scan_raw_mail=bool(antivirus_data.get("scan_raw_mail", True)),
        scan_attachments=bool(antivirus_data.get("scan_attachments", True)),
        cache_hours=max(0, min(int(antivirus_data.get("cache_hours", 24)), 720)),
        max_scan_bytes=max(1024, min(int(antivirus_data.get("max_scan_bytes", 100_000_000)), 1_000_000_000)),
        timeout_seconds=max(5, min(int(antivirus_data.get("timeout_seconds", 120)), 1800)),
        temp_dir=_clean_outbox(antivirus_data.get("temp_dir") or DEFAULT_ANTIVIRUS_TEMP),
    )
    interval_minutes = int(portfolio_data.get("interval_minutes", 15))
    if interval_minutes not in {15, 30, 60, 90, 120}:
        raise ValueError(
            "tools.toml: portfolio.interval_minutes muss 15, 30, 60, 90 oder 120 sein"
        )
    provider = str(portfolio_data.get("provider") or "disabled").strip().casefold()
    if provider == "twelve-data":
        # Safe migration from releases before the EODHD switch: do not silently
        # send market-data requests with a different provider or credential.
        provider = "disabled"
    if provider not in {"disabled", "eodhd"}:
        raise ValueError("tools.toml: portfolio.provider muss disabled oder eodhd sein")
    api_key_env = str(
        portfolio_data.get("api_key_env") or "PORTFOLIO_EODHD_API_KEY"
    ).strip()
    if api_key_env == "PORTFOLIO_MARKET_DATA_API_KEY":
        api_key_env = "PORTFOLIO_EODHD_API_KEY"
    if not api_key_env or not api_key_env.replace("_", "").isalnum() or api_key_env[0].isdigit():
        raise ValueError("tools.toml: portfolio.api_key_env ist kein gueltiger Variablenname")
    portfolio = PortfolioToolSettings(
        enabled=bool(portfolio_data.get("enabled", False)),
        database=_clean_workspace_path(
            portfolio_data.get("database") or DEFAULT_PORTFOLIO_DB,
            field_name="portfolio.database",
        ),
        import_root=_clean_workspace_path(
            portfolio_data.get("import_root") or DEFAULT_PORTFOLIO_INBOX,
            field_name="portfolio.import_root",
        ),
        nextcloud_folder=clean_remote_path(
            portfolio_data.get("nextcloud_folder") or DEFAULT_PORTFOLIO_NEXTCLOUD_FOLDER,
            field_name="portfolio.nextcloud_folder",
        ),
        provider=provider,
        api_key_env=api_key_env,
        interval_minutes=interval_minutes,
        stale_warning_minutes=max(
            interval_minutes,
            min(int(portfolio_data.get("stale_warning_minutes", 45)), 1440),
        ),
        stale_critical_minutes=max(
            interval_minutes,
            min(int(portfolio_data.get("stale_critical_minutes", 90)), 2880),
        ),
        request_timeout_seconds=max(
            5, min(int(portfolio_data.get("request_timeout_seconds", 20)), 120)
        ),
        max_symbols=max(1, min(int(portfolio_data.get("max_symbols", 100)), 500)),
        timezone=str(portfolio_data.get("timezone") or "Europe/Berlin").strip(),
        market_open=str(portfolio_data.get("market_open") or "08:00").strip(),
        market_close=str(portfolio_data.get("market_close") or "22:00").strip(),
    )
    if portfolio.stale_critical_minutes < portfolio.stale_warning_minutes:
        raise ValueError(
            "tools.toml: portfolio.stale_critical_minutes muss mindestens stale_warning_minutes sein"
        )
    if not portfolio.nextcloud_folder or not (
        portfolio.nextcloud_folder == "Assistent"
        or portfolio.nextcloud_folder.startswith("Assistent/")
    ):
        raise ValueError("tools.toml: portfolio.nextcloud_folder muss unter Assistent/ liegen")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(portfolio.timezone)
    except Exception as exc:
        raise ValueError("tools.toml: ungueltige Portfolio-Zeitzone") from exc
    for clock_name, clock_value in (
        ("portfolio.market_open", portfolio.market_open),
        ("portfolio.market_close", portfolio.market_close),
    ):
        try:
            hour, minute = (int(value) for value in clock_value.split(":", 1))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError(f"tools.toml: {clock_name} muss HH:MM sein") from exc
    workspace = NextcloudWorkspaceToolSettings(
        enabled=bool(workspace_data.get("enabled", True)),
        resource_id=str(workspace_data.get("resource_id") or "nextcloud-files-main").strip(),
        root=clean_remote_path(
            str(workspace_data.get("root") or "Assistent"),
            field_name="Workspace-Wurzel",
        ),
        outbox=_clean_outbox(workspace_data.get("outbox") or DEFAULT_WORKSPACE_OUTBOX),
        allow_mkdir=bool(workspace_data.get("allow_mkdir", True)),
        allow_upload=bool(workspace_data.get("allow_upload", True)),
        allow_write_text=bool(workspace_data.get("allow_write_text", True)),
        allow_move=bool(workspace_data.get("allow_move", True)),
    )

    # Container layout v3 injects role-mounted data roots after the instance
    # configuration was parsed.  These roots are administrator-owned mount
    # points, so they intentionally do not pass through the legacy
    # workspace-only validators above.
    if orders_root := os.environ.get("OPENCLAW_ORDERS_DATA_DIR"):
        deck_orders.database = Path(orders_root).expanduser().resolve() / "orders.sqlite3"
    if security_root := os.environ.get("OPENCLAW_SECURITY_DATA_DIR"):
        antivirus.temp_dir = Path(security_root).expanduser().resolve() / "tmp"
    if portfolio_root := os.environ.get("OPENCLAW_PORTFOLIO_DATA_DIR"):
        root = Path(portfolio_root).expanduser().resolve()
        portfolio.database = root / "portfolio.sqlite3"
        portfolio.import_root = root / "inbox"
    if core_root := os.environ.get("OPENCLAW_CORE_DATA_DIR"):
        workspace.outbox = Path(core_root).expanduser().resolve() / "workspace_outbox"

    if calendar_mail.enabled:
        if not calendar_mail.sender_addresses:
            raise ValueError("tools.toml: mail.calendar_mail.sender_addresses ist leer")
        if not calendar_mail.calendar_resource_id:
            raise ValueError("tools.toml: mail.calendar_mail.calendar_resource_id ist leer")
        if not calendar_mail.subject_prefix:
            raise ValueError("tools.toml: mail.calendar_mail.subject_prefix ist leer")
    if invoices.enabled and not invoices.resource_id:
        raise ValueError("tools.toml: mail.invoices.resource_id ist leer")
    if mail_move.enabled and not mail_move.resource_id:
        raise ValueError("tools.toml: mail.move.resource_id ist leer")
    if workspace.enabled and not workspace.resource_id:
        raise ValueError("tools.toml: nextcloud.workspace.resource_id ist leer")
    if direct_calendar.enabled and not direct_calendar.resource_id:
        raise ValueError("tools.toml: nextcloud.calendar.resource_id ist leer")
    if direct_calendar.enabled and not (direct_calendar.allow_create or direct_calendar.allow_list or direct_calendar.allow_update):
        raise ValueError("tools.toml: direktes Kalenderwerkzeug hat keine erlaubte Funktion")
    if direct_calendar.allow_update and not direct_calendar.allow_list:
        raise ValueError("tools.toml: Kalender-Aktualisierung benoetigt Leserechte")
    if direct_tasks.enabled and not direct_tasks.resource_id:
        raise ValueError("tools.toml: nextcloud.tasks.resource_id ist leer")
    if direct_tasks.enabled and not (direct_tasks.allow_create or direct_tasks.allow_list or direct_tasks.allow_update):
        raise ValueError("tools.toml: direktes Aufgabenwerkzeug hat keine erlaubte Funktion")
    if direct_tasks.allow_update and not direct_tasks.allow_list:
        raise ValueError("tools.toml: Aufgaben-Aktualisierung benoetigt Leserechte")
    if direct_contacts.enabled and not direct_contacts.resource_id:
        raise ValueError("tools.toml: direktes Kontaktwerkzeug benoetigt resource_id")
    if direct_contacts.enabled and not (direct_contacts.allow_create or direct_contacts.allow_list):
        raise ValueError("tools.toml: direktes Kontaktwerkzeug hat keine erlaubte Funktion")
    if deck_orders.enabled and (not deck_orders.resource_id or deck_orders.board_id <= 0):
        raise ValueError("tools.toml: Deck-Bestellwerkzeug benoetigt resource_id und board_id")
    if deck_orders.enabled and not (deck_orders.allow_read or deck_orders.allow_create or deck_orders.allow_update or deck_orders.allow_move):
        raise ValueError("tools.toml: Deck-Bestellwerkzeug hat keine erlaubte Funktion")
    if antivirus.enabled and not antivirus.binary and not antivirus.fallback_binary:
        raise ValueError("tools.toml: security.antivirus benoetigt mindestens einen Scanner")

    return ToolSettings(
        path=config_path,
        mail=MailToolSettings(
            enabled=bool(mail_data.get("enabled", True)),
            invoices=invoices,
            calendar_mail=calendar_mail,
            move=mail_move,
        ),
        nextcloud=NextcloudToolSettings(
            workspace=workspace,
            calendar=direct_calendar,
            tasks=direct_tasks,
            contacts=direct_contacts,
            deck_orders=deck_orders,
        ),
        security=SecurityToolSettings(antivirus=antivirus),
        portfolio=portfolio,
    )
