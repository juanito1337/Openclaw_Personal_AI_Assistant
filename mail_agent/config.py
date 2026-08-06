from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WORKSPACE_ROOT = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1]).expanduser().resolve()
DEFAULT_CONFIG_PATH = WORKSPACE_ROOT / "mail_agent/config.toml"


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _resolve_path(value: str | Path, base: Path = WORKSPACE_ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


@dataclass(slots=True)
class MailboxConfig:
    himalaya_binary: str = "himalaya"
    account: str = ""
    source_folder: str = "INBOX"
    quarantine_folders: list[str] = field(default_factory=lambda: ["Spam"])
    quarantine_max_per_run: int = 10
    quarantine_rescue_only: bool = True
    from_header: str = "Mail Agent <mail-agent@example.invalid>"
    forward_to: str = "important@example.invalid"
    page_size: int = 100

    def all_source_folders(self) -> list[str]:
        values = [self.source_folder, *self.quarantine_folders]
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))


@dataclass(slots=True)
class FolderConfig:
    spam: str = "Agent/Spam"
    routine: str = "Agent/Routine"
    forwarded: str = "Agent/Weitergeleitet"
    review: str = "Agent/Pruefen"
    feedback_not_spam: str = "Agent/Korrektur-Kein-Spam"
    feedback_unimportant: str = "Agent/Korrektur-Unwichtig"
    feedback_important: str = "Agent/Korrektur-Wichtig"
    feedback_spam: str = "Agent/Korrektur-Spam"
    appointment_review: str = "Agent/Termin-Pruefen"
    error: str = "Agent/Fehler"
    malware: str = "Agent/Virusverdacht"

    def all(self) -> list[str]:
        return list(dict.fromkeys([
            self.spam,
            self.routine,
            self.forwarded,
            self.review,
            self.feedback_not_spam,
            self.feedback_unimportant,
            self.feedback_important,
            self.feedback_spam,
            self.appointment_review,
            self.error,
            self.malware,
        ]))


@dataclass(slots=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "gemma4:31b"
    timeout_seconds: int = 600
    queue_timeout_seconds: int = 600
    request_timeout_margin_seconds: int = 30
    max_body_chars: int = 6000
    temperature: float = 0.1

    # Mehrere ungeklärte Mails werden in einer strukturierten Modellanfrage
    # klassifiziert. Eine einzelne neue Mail bleibt automatisch ein Einzelaufruf.
    batch_enabled: bool = True
    batch_size: int = 3
    batch_prefetch: int = 9
    batch_timeout_seconds: int = 300
    batch_retry_timeout_seconds: int = 300
    batch_timeout_split_once: bool = True
    batch_max_split_depth: int = 2
    batch_max_body_chars: int = 4000
    batch_max_total_chars: int = 18000
    batch_fallback_to_smaller_groups: bool = True

    # R19: Die konfigurierte batch_size bleibt die Obergrenze. Adaptive Batches
    # verkleinern nur komplexe oder besonders grosse Gruppen; einfache Mails
    # duerfen weiterhin die volle Batchgroesse nutzen.
    batch_adaptive_enabled: bool = True
    batch_adaptive_target_chars: int = 14000
    batch_adaptive_heavy_body_chars: int = 3500
    batch_adaptive_max_attachments: int = 2

    # R25: unresolved model groups may use both Ollama slots. Background burst is
    # admitted only while no foreground request is active or waiting.
    parallel_requests: int = 1
    background_burst: bool = False

    # Diese Optionen gelten nur für die nativen /api/chat-Aufrufe des Mail-Agenten.
    num_ctx: int = 16_384
    num_predict: int = 512
    single_retry_num_predict: int = 1024
    batch_num_predict: int = 2048
    keep_alive: str = "1h"
    think: bool = False


@dataclass(slots=True)
class ThresholdConfig:
    spam: float = 0.95
    relevant: float = 0.90
    routine: float = 0.90
    calendar: float = 0.95
    min_forward_importance: int = 7


@dataclass(slots=True)
class ForwardingConfig:
    enabled: bool = True
    attach_original_eml: bool = True
    # Kept only so existing 3.2/3.3 config.toml files remain loadable.
    # Since 3.3.1, original messages are sent as ZIP on the first attempt.
    retry_as_zip_on_rejection: bool = True
    subject_prefix: str = "[WICHTIG {importance}/10]"
    payload_dir: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/forward_payloads")


@dataclass(slots=True)
class CalendarConfig:
    enabled: bool = True
    auto_create: bool = True
    require_trusted_sender: bool = True
    trust_feedback_count: int = 2
    approval_required: bool = True
    approval_recipient: str = ""
    approval_reply_from: str = ""
    approval_expiry_days: int = 14
    approval_subject_prefix: str = "[MAIL-AGENT TERMIN]"
    require_future: bool = True
    send_result_mail: bool = True
    backend: str = "auto"
    timezone: str = "Europe/Berlin"
    command: str = ""
    pending_dir: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/calendar_pending")
    created_dir: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/calendar_created")
    caldav_url_env: str = "MAIL_AGENT_CALDAV_URL"
    caldav_username_env: str = "MAIL_AGENT_CALDAV_USERNAME"
    caldav_password_env: str = "MAIL_AGENT_CALDAV_PASSWORD"


@dataclass(slots=True)
class NextcloudConfig:
    enabled: bool = False
    skill_package: str = "@keithvassallomt/openclaw-nextcloud"
    skill_dir: Path = field(default_factory=lambda: WORKSPACE_ROOT / "skills/openclaw-nextcloud")
    base_url_env: str = "NEXTCLOUD_URL"
    username_env: str = "NEXTCLOUD_USER"
    token_env: str = "NEXTCLOUD_TOKEN"
    calendar: str = ""
    addressbook: str = ""
    contacts_enabled: bool = True
    contacts_prevent_spam: bool = True
    trust_contacts_for_calendar: bool = False
    contact_importance_boost: int = 1
    contact_cache_ttl_seconds: int = 3600
    contact_cache_file: Path = field(
        default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/nextcloud_contacts_cache.json"
    )


@dataclass(slots=True)
class InvoiceConfig:
    enabled: bool = False
    require_routine: bool = True
    min_confidence: float = 0.90
    nextcloud_folder: str = "Mail-Agent/Rechnungen"
    organize_by_year_month: bool = True
    max_pdf_bytes: int = 25_000_000
    upload_timeout_seconds: int = 120
    metadata_enabled: bool = True
    metadata_min_confidence: float = 0.82
    text_timeout_seconds: int = 45
    min_text_quality: float = 0.35
    ocr_enabled: bool = True
    ocr_languages: str = "deu+eng"
    ocr_max_pages: int = 2
    ocr_dpi: int = 300
    ocr_page_segmentation: int = 6
    ocr_timeout_seconds: int = 180
    review_subfolder: str = "Pruefen"
    register_enabled: bool = True
    register_dir: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/invoice_register")
    register_delimiter: str = ";"


@dataclass(slots=True)
class DigestConfig:
    enabled: bool = False
    hour_local: int = 18
    min_items: int = 1
    subject: str = "Mail-Tagesuebersicht {date}"


@dataclass(slots=True)
class NotificationConfig:
    signal_enabled: bool = False
    signal_script: Path = field(default_factory=lambda: WORKSPACE_ROOT / "scripts/signal-send.sh")
    signal_recipient: str = ""


@dataclass(slots=True)
class RuntimeConfig:
    database: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/mail_agent.sqlite3")
    rules_file: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/rules.toml")
    learning_folders_file: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/learning_folders.json")
    log_file: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/mail_agent.log")
    lock_file: Path = field(default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/mail_agent.lock")
    command_timeout_seconds: int = 90


@dataclass(slots=True)
class Config:
    mailbox: MailboxConfig
    folders: FolderConfig
    ollama: OllamaConfig
    thresholds: ThresholdConfig
    forwarding: ForwardingConfig
    calendar: CalendarConfig
    nextcloud: NextcloudConfig
    invoices: InvoiceConfig
    digest: DigestConfig
    notifications: NotificationConfig
    runtime: RuntimeConfig
    path: Path

    def ensure_local_dirs(self) -> None:
        for path in [
            self.forwarding.payload_dir,
            self.calendar.pending_dir,
            self.calendar.created_dir,
            self.nextcloud.contact_cache_file.parent,
            self.runtime.database.parent,
            self.runtime.log_file.parent,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def _validate_config(config: Config) -> None:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    def safe_text(value: object, label: str) -> str:
        text = str(value or "").strip()
        require(bool(text), f"{label} darf nicht leer sein")
        require("\r" not in text and "\n" not in text, f"{label} darf keine Zeilenumbrueche enthalten")
        return text

    safe_text(config.mailbox.himalaya_binary, "mailbox.himalaya_binary")
    source_folder = safe_text(config.mailbox.source_folder, "mailbox.source_folder")
    quarantine_folders = [
        safe_text(folder, f"mailbox.quarantine_folders[{index}]")
        for index, folder in enumerate(config.mailbox.quarantine_folders)
    ]
    quarantine_folded = [folder.casefold() for folder in quarantine_folders]
    require(len(quarantine_folded) == len(set(quarantine_folded)),
            "mailbox.quarantine_folders darf keine doppelten Ordner enthalten")
    require(source_folder.casefold() not in set(quarantine_folded),
            "mailbox.source_folder darf nicht zugleich Quarantaeneordner sein")
    require(
        isinstance(config.mailbox.quarantine_max_per_run, int)
        and 0 <= config.mailbox.quarantine_max_per_run <= 500,
        "mailbox.quarantine_max_per_run muss zwischen 0 und 500 liegen",
    )
    require(isinstance(config.mailbox.quarantine_rescue_only, bool),
            "mailbox.quarantine_rescue_only muss true oder false sein")
    from_header = safe_text(config.mailbox.from_header, "mailbox.from_header")
    forward_to = safe_text(config.mailbox.forward_to, "mailbox.forward_to")
    require("@" in parseaddr(from_header)[1], "mailbox.from_header enthaelt keine gueltige Absenderadresse")
    require("@" in parseaddr(forward_to)[1], "mailbox.forward_to enthaelt keine gueltige Zieladresse")
    require(isinstance(config.mailbox.page_size, int) and 1 <= config.mailbox.page_size <= 1000,
            "mailbox.page_size muss zwischen 1 und 1000 liegen")

    folder_values = []
    for field_name, folder in (
        ("spam", config.folders.spam),
        ("routine", config.folders.routine),
        ("forwarded", config.folders.forwarded),
        ("review", config.folders.review),
        ("feedback_not_spam", config.folders.feedback_not_spam),
        ("feedback_unimportant", config.folders.feedback_unimportant),
        ("feedback_important", config.folders.feedback_important),
        ("feedback_spam", config.folders.feedback_spam),
        ("appointment_review", config.folders.appointment_review),
        ("error", config.folders.error),
    ):
        folder_values.append(safe_text(folder, f"folders.{field_name}"))
    folded = [item.casefold() for item in folder_values]
    require(len(folded) == len(set(folded)), "Alle Agent-Ordner muessen unterschiedliche Namen haben")
    require(source_folder.casefold() not in set(folded), "mailbox.source_folder darf kein Agent-Zielordner sein")
    overlap = set(quarantine_folded) & set(folded)
    require(not overlap, "mailbox.quarantine_folders duerfen keine Agent-Zielordner sein: " + ", ".join(sorted(overlap)))

    base_url = safe_text(config.ollama.base_url, "ollama.base_url")
    parsed_url = urlparse(base_url)
    require(parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc),
            "ollama.base_url muss eine vollstaendige HTTP(S)-URL sein")
    safe_text(config.ollama.model, "ollama.model")
    require(isinstance(config.ollama.timeout_seconds, int) and config.ollama.timeout_seconds > 0,
            "ollama.timeout_seconds muss groesser als 0 sein")
    require(isinstance(config.ollama.queue_timeout_seconds, int) and config.ollama.queue_timeout_seconds > 0,
            "ollama.queue_timeout_seconds muss groesser als 0 sein")
    require(isinstance(config.ollama.parallel_requests, int) and 1 <= config.ollama.parallel_requests <= 2,
            "ollama.parallel_requests muss 1 oder 2 sein")
    require(isinstance(config.ollama.background_burst, bool),
            "ollama.background_burst muss true oder false sein")
    require(isinstance(config.ollama.request_timeout_margin_seconds, int) and config.ollama.request_timeout_margin_seconds >= 5,
            "ollama.request_timeout_margin_seconds muss mindestens 5 sein")
    require(isinstance(config.ollama.max_body_chars, int) and config.ollama.max_body_chars >= 1000,
            "ollama.max_body_chars muss mindestens 1000 sein")
    require(isinstance(config.ollama.batch_enabled, bool),
            "ollama.batch_enabled muss true oder false sein")
    require(isinstance(config.ollama.batch_size, int) and 1 <= config.ollama.batch_size <= 10,
            "ollama.batch_size muss zwischen 1 und 10 liegen")
    require(
        isinstance(config.ollama.batch_prefetch, int)
        and config.ollama.batch_size <= config.ollama.batch_prefetch <= 100,
        "ollama.batch_prefetch muss mindestens batch_size und hoechstens 100 sein",
    )
    require(
        isinstance(config.ollama.batch_timeout_seconds, int)
        and config.ollama.batch_timeout_seconds > 0,
        "ollama.batch_timeout_seconds muss groesser als 0 sein",
    )
    require(
        isinstance(config.ollama.batch_retry_timeout_seconds, int)
        and config.ollama.batch_retry_timeout_seconds > 0
        and config.ollama.batch_retry_timeout_seconds <= config.ollama.batch_timeout_seconds,
        "ollama.batch_retry_timeout_seconds muss groesser als 0 und hoechstens batch_timeout_seconds sein",
    )
    require(isinstance(config.ollama.batch_timeout_split_once, bool),
            "ollama.batch_timeout_split_once muss true oder false sein")
    require(isinstance(config.ollama.batch_max_split_depth, int) and 0 <= config.ollama.batch_max_split_depth <= 3,
            "ollama.batch_max_split_depth muss zwischen 0 und 3 liegen")
    require(
        isinstance(config.ollama.batch_max_body_chars, int)
        and 500 <= config.ollama.batch_max_body_chars <= config.ollama.max_body_chars,
        "ollama.batch_max_body_chars muss zwischen 500 und max_body_chars liegen",
    )
    require(
        isinstance(config.ollama.batch_max_total_chars, int)
        and config.ollama.batch_max_total_chars >= 8000,
        "ollama.batch_max_total_chars muss mindestens 8000 sein",
    )
    require(isinstance(config.ollama.batch_fallback_to_smaller_groups, bool),
            "ollama.batch_fallback_to_smaller_groups muss true oder false sein")
    require(isinstance(config.ollama.batch_adaptive_enabled, bool),
            "ollama.batch_adaptive_enabled muss true oder false sein")
    require(
        isinstance(config.ollama.batch_adaptive_target_chars, int)
        and 8000 <= config.ollama.batch_adaptive_target_chars <= config.ollama.batch_max_total_chars,
        "ollama.batch_adaptive_target_chars muss zwischen 8000 und batch_max_total_chars liegen",
    )
    require(
        isinstance(config.ollama.batch_adaptive_heavy_body_chars, int)
        and 500 <= config.ollama.batch_adaptive_heavy_body_chars <= config.ollama.max_body_chars,
        "ollama.batch_adaptive_heavy_body_chars muss zwischen 500 und max_body_chars liegen",
    )
    require(
        isinstance(config.ollama.batch_adaptive_max_attachments, int)
        and 0 <= config.ollama.batch_adaptive_max_attachments <= 50,
        "ollama.batch_adaptive_max_attachments muss zwischen 0 und 50 liegen",
    )
    require(isinstance(config.ollama.num_ctx, int) and config.ollama.num_ctx >= 0,
            "ollama.num_ctx darf nicht negativ sein")
    require(isinstance(config.ollama.num_predict, int) and config.ollama.num_predict >= 64,
            "ollama.num_predict muss mindestens 64 sein")
    require(
        isinstance(config.ollama.single_retry_num_predict, int)
        and config.ollama.single_retry_num_predict >= config.ollama.num_predict
        and config.ollama.single_retry_num_predict <= 4096,
        "ollama.single_retry_num_predict muss mindestens num_predict und hoechstens 4096 sein",
    )
    require(isinstance(config.ollama.batch_num_predict, int) and config.ollama.batch_num_predict >= 256,
            "ollama.batch_num_predict muss mindestens 256 sein")
    keep_alive = safe_text(config.ollama.keep_alive, "ollama.keep_alive")
    require(len(keep_alive) <= 32, "ollama.keep_alive ist zu lang")
    require(isinstance(config.ollama.think, bool), "ollama.think muss true oder false sein")
    try:
        temperature = float(config.ollama.temperature)
        require(0.0 <= temperature <= 2.0, "ollama.temperature muss zwischen 0 und 2 liegen")
    except (TypeError, ValueError):
        errors.append("ollama.temperature muss eine Zahl sein")

    for name, value in (
        ("spam", config.thresholds.spam),
        ("relevant", config.thresholds.relevant),
        ("routine", config.thresholds.routine),
        ("calendar", config.thresholds.calendar),
    ):
        try:
            number = float(value)
            require(0.0 <= number <= 1.0, f"thresholds.{name} muss zwischen 0 und 1 liegen")
        except (TypeError, ValueError):
            errors.append(f"thresholds.{name} muss eine Zahl sein")
    require(
        isinstance(config.thresholds.min_forward_importance, int)
        and 1 <= config.thresholds.min_forward_importance <= 10,
        "thresholds.min_forward_importance muss zwischen 1 und 10 liegen",
    )
    try:
        config.forwarding.subject_prefix.format(importance=10)
    except (KeyError, ValueError) as exc:
        errors.append(f"forwarding.subject_prefix ist ungueltig: {exc}")

    backend = str(config.calendar.backend or "").strip().lower()
    require(backend in {"auto", "caldav", "nextcloud_skill", "command", "khal", "queue"},
            "calendar.backend muss auto, caldav, nextcloud_skill, command, khal oder queue sein")
    require(isinstance(config.calendar.trust_feedback_count, int) and config.calendar.trust_feedback_count >= 1,
            "calendar.trust_feedback_count muss mindestens 1 sein")
    try:
        ZoneInfo(config.calendar.timezone)
    except (ZoneInfoNotFoundError, TypeError):
        errors.append(f"calendar.timezone ist unbekannt: {config.calendar.timezone!r}")
    if backend == "command":
        require("{ics_path}" in config.calendar.command,
                "calendar.command muss beim Backend command den Platzhalter {ics_path} enthalten")
    require(isinstance(config.calendar.approval_required, bool),
            "calendar.approval_required muss true oder false sein")
    require(isinstance(config.calendar.require_future, bool),
            "calendar.require_future muss true oder false sein")
    require(isinstance(config.calendar.send_result_mail, bool),
            "calendar.send_result_mail muss true oder false sein")
    require(isinstance(config.calendar.approval_expiry_days, int) and 1 <= config.calendar.approval_expiry_days <= 90,
            "calendar.approval_expiry_days muss zwischen 1 und 90 liegen")
    safe_text(config.calendar.approval_subject_prefix, "calendar.approval_subject_prefix")
    for label, value in (("approval_recipient", config.calendar.approval_recipient),
                         ("approval_reply_from", config.calendar.approval_reply_from)):
        if str(value or "").strip():
            require("@" in parseaddr(str(value))[1], f"calendar.{label} enthaelt keine gueltige Mailadresse")

    safe_text(config.nextcloud.skill_package, "nextcloud.skill_package")
    for field_name, value in (
        ("base_url_env", config.nextcloud.base_url_env),
        ("username_env", config.nextcloud.username_env),
        ("token_env", config.nextcloud.token_env),
    ):
        env_name = safe_text(value, f"nextcloud.{field_name}")
        require(env_name.replace("_", "").isalnum() and env_name.upper() == env_name,
                f"nextcloud.{field_name} muss ein gueltiger grossgeschriebener Umgebungsvariablenname sein")
    require(
        isinstance(config.nextcloud.contact_importance_boost, int)
        and 0 <= config.nextcloud.contact_importance_boost <= 3,
        "nextcloud.contact_importance_boost muss zwischen 0 und 3 liegen",
    )
    require(
        isinstance(config.nextcloud.contact_cache_ttl_seconds, int)
        and config.nextcloud.contact_cache_ttl_seconds >= 60,
        "nextcloud.contact_cache_ttl_seconds muss mindestens 60 sein",
    )

    require(isinstance(config.invoices.enabled, bool), "invoices.enabled muss true oder false sein")
    require(isinstance(config.invoices.require_routine, bool), "invoices.require_routine muss true oder false sein")
    try:
        invoice_confidence = float(config.invoices.min_confidence)
        require(0.5 <= invoice_confidence <= 1.0, "invoices.min_confidence muss zwischen 0.5 und 1.0 liegen")
    except (TypeError, ValueError):
        errors.append("invoices.min_confidence muss eine Zahl sein")
    invoice_folder = safe_text(config.invoices.nextcloud_folder, "invoices.nextcloud_folder")
    require(".." not in invoice_folder.replace("\\", "/").split("/"),
            "invoices.nextcloud_folder darf kein '..' enthalten")
    require(isinstance(config.invoices.max_pdf_bytes, int) and 1024 <= config.invoices.max_pdf_bytes <= 250_000_000,
            "invoices.max_pdf_bytes muss zwischen 1024 und 250000000 liegen")
    require(isinstance(config.invoices.upload_timeout_seconds, int) and config.invoices.upload_timeout_seconds > 0,
            "invoices.upload_timeout_seconds muss groesser als 0 sein")
    require(isinstance(config.invoices.metadata_enabled, bool), "invoices.metadata_enabled muss true oder false sein")
    require(0.5 <= float(config.invoices.metadata_min_confidence) <= 1.0,
            "invoices.metadata_min_confidence muss zwischen 0.5 und 1.0 liegen")
    require(isinstance(config.invoices.text_timeout_seconds, int) and config.invoices.text_timeout_seconds > 0,
            "invoices.text_timeout_seconds muss groesser als 0 sein")
    require(0.0 <= float(config.invoices.min_text_quality) <= 1.0,
            "invoices.min_text_quality muss zwischen 0 und 1 liegen")
    require(isinstance(config.invoices.ocr_enabled, bool), "invoices.ocr_enabled muss true oder false sein")
    safe_text(config.invoices.ocr_languages, "invoices.ocr_languages")
    require(isinstance(config.invoices.ocr_max_pages, int) and 1 <= config.invoices.ocr_max_pages <= 20,
            "invoices.ocr_max_pages muss zwischen 1 und 20 liegen")
    require(isinstance(config.invoices.ocr_dpi, int) and 150 <= config.invoices.ocr_dpi <= 600,
            "invoices.ocr_dpi muss zwischen 150 und 600 liegen")
    require(isinstance(config.invoices.ocr_timeout_seconds, int) and config.invoices.ocr_timeout_seconds > 0,
            "invoices.ocr_timeout_seconds muss groesser als 0 sein")
    safe_text(config.invoices.review_subfolder, "invoices.review_subfolder")
    require(isinstance(config.invoices.register_enabled, bool), "invoices.register_enabled muss true oder false sein")
    require(str(config.invoices.register_delimiter) == ";", "invoices.register_delimiter muss in R26 ein Semikolon sein")

    require(isinstance(config.digest.hour_local, int) and 0 <= config.digest.hour_local <= 23,
            "digest.hour_local muss zwischen 0 und 23 liegen")
    require(isinstance(config.digest.min_items, int) and config.digest.min_items >= 0,
            "digest.min_items darf nicht negativ sein")
    require(isinstance(config.runtime.command_timeout_seconds, int) and config.runtime.command_timeout_seconds > 0,
            "runtime.command_timeout_seconds muss groesser als 0 sein")
    if config.notifications.signal_enabled:
        require(bool(str(config.notifications.signal_recipient or "").strip()),
                "notifications.signal_recipient fehlt, obwohl Signal aktiviert ist")

    if errors:
        raise ValueError("Ungueltige Mail-Agent-Konfiguration:\n- " + "\n- ".join(errors))


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path or os.environ.get("MAIL_AGENT_CONFIG", DEFAULT_CONFIG_PATH)).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Mail-Agent-Konfiguration fehlt: {config_path}. Zuerst ./scripts/bootstrap-local.sh ausfuehren")
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    mailbox = MailboxConfig(**_section(data, "mailbox"))
    folders = FolderConfig(**_section(data, "folders"))
    ollama = OllamaConfig(**_section(data, "ollama"))
    thresholds = ThresholdConfig(**_section(data, "thresholds"))

    forwarding_data = _section(data, "forwarding").copy()
    if "payload_dir" in forwarding_data:
        forwarding_data["payload_dir"] = _resolve_path(forwarding_data["payload_dir"])
    forwarding = ForwardingConfig(**forwarding_data)

    calendar_data = _section(data, "calendar").copy()
    for key in ("pending_dir", "created_dir"):
        if key in calendar_data:
            calendar_data[key] = _resolve_path(calendar_data[key])
    calendar = CalendarConfig(**calendar_data)

    nextcloud_data = _section(data, "nextcloud").copy()
    for key in ("skill_dir", "contact_cache_file"):
        if key in nextcloud_data:
            nextcloud_data[key] = _resolve_path(nextcloud_data[key])
    nextcloud = NextcloudConfig(**nextcloud_data)

    invoice_data = _section(data, "invoices").copy()
    if "register_dir" in invoice_data:
        invoice_data["register_dir"] = _resolve_path(invoice_data["register_dir"])
    invoices = InvoiceConfig(**invoice_data)
    digest = DigestConfig(**_section(data, "digest"))

    notification_data = _section(data, "notifications").copy()
    if "signal_script" in notification_data:
        notification_data["signal_script"] = _resolve_path(notification_data["signal_script"])
    notifications = NotificationConfig(**notification_data)

    runtime_data = _section(data, "runtime").copy()
    for key in ("database", "rules_file", "learning_folders_file", "log_file", "lock_file"):
        if key in runtime_data:
            runtime_data[key] = _resolve_path(runtime_data[key])
    runtime = RuntimeConfig(**runtime_data)
    mail_data = os.environ.get("OPENCLAW_MAIL_DATA_DIR", "").strip()
    if mail_data:
        data_root = Path(mail_data).expanduser().resolve()
        forwarding.payload_dir = data_root / "forward_payloads"
        calendar.pending_dir = data_root / "calendar_pending"
        calendar.created_dir = data_root / "calendar_created"
        nextcloud.contact_cache_file = data_root / "nextcloud_contacts_cache.json"
        invoices.register_dir = data_root / "invoice_register"
        runtime.database = data_root / "mail_agent.sqlite3"
        runtime.log_file = data_root / "mail_agent.log"
        runtime.lock_file = data_root / "mail_agent.lock"
        runtime.learning_folders_file = data_root / "learning_folders.json"

    config = Config(
        mailbox=mailbox,
        folders=folders,
        ollama=ollama,
        thresholds=thresholds,
        forwarding=forwarding,
        calendar=calendar,
        nextcloud=nextcloud,
        invoices=invoices,
        digest=digest,
        notifications=notifications,
        runtime=runtime,
        path=config_path,
    )
    _validate_config(config)
    config.ensure_local_dirs()
    return config
