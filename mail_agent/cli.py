from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, replace
from datetime import datetime
from email.utils import parsedate_to_datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from personal_assistant.antivirus import HostAntivirus
from personal_assistant.tool_settings import load_tool_settings

from .app import MailAgent
from .assistant_bridge import PersonalAssistantActionBridge
from .config import Config, load_config
from .envfile import default_env_file, load_env_file
from .invoice_extract import InvoiceExtractor, amount_to_cents
from .invoice_register import InvoiceRegister
from .invoice_reprocess import ReadOnlyInvoicePdfReader, run_reprocess_preview
from .learning import LearningFolderRegistry
from .learning_quality import LearningQualityAnalyzer
from .lock import ProcessLock, ProcessLockError, inspect_process_lock
from .models import ParsedMessage
from .nextcloud import NextcloudSkillClient, NextcloudSkillError
from .nextcloud_setup import interactive_nextcloud_setup
from .setup_assistant import (
    build_guide,
    configuration_fingerprint,
    extended_help,
    interactive_configure,
    invalidate_dry_run,
    productive_run_blockers,
    read_setup_state,
    record_dry_run,
    update_toml_values,
)
from .storage import Storage
from .telemetry import read_recent_performance, summarize_performance
from .training import TrainingManager
from .utils import normalize_address


def _configure_logging(log_file: Path, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokaler Mail-Chief-of-Staff fuer Himalaya, Ollama und Nextcloud")
    parser.add_argument("--config", help="Pfad zur config.toml")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="IMAP-Ordner und lokale Datenbank vorbereiten")
    setup.add_argument("--dry-run", action="store_true")

    run = sub.add_parser("run", help="Feedback verarbeiten und INBOX triagieren")
    run.add_argument(
        "--limit", type=int, default=20,
        help="Globales Nachrichtenlimit fuer einen einzelnen Lauf (ohne --drain)",
    )
    run.add_argument(
        "--drain", action="store_true",
        help="In sicheren Batches weiterarbeiten, bis die INBOX leer ist oder eine Schutzgrenze greift",
    )
    run.add_argument("--batch-size", type=int, default=20, help="Batchgroesse im Drain-Modus")
    run.add_argument("--max-messages", type=int, default=500, help="Sicherheitsgrenze pro Drain-Start")
    run.add_argument("--max-runtime", type=int, default=2400, help="Harte maximale Drain-Laufzeit in Sekunden")
    run.add_argument("--shutdown-reserve", type=int, default=180, help="Reserve fuer kontrollierten Abschluss vor dem harten Laufzeitlimit")
    run.add_argument("--max-batches", type=int, default=100, help="Maximale Anzahl Drain-Batches")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-digest", action="store_true")
    run.add_argument(
        "--force",
        action="store_true",
        help=("Produktive Sicherheitspruefung nur interaktiv umgehen; benoetigt "
              "MAIL_AGENT_ALLOW_FORCE=YES und die Eingabe FORCE"),
    )

    spam_review = sub.add_parser("spam-review", help="Provider-Spamordner kontrolliert auf Fehlklassifizierungen pruefen")
    spam_review.add_argument("--limit", type=int, default=20)
    spam_review.add_argument("--dry-run", action="store_true")

    orders_import = sub.add_parser("orders-import", help="Bereits indexierte Mails erneut auf laufende Bestellungen pruefen")
    orders_import.add_argument("--limit", type=int, default=500)
    orders_import.add_argument("--dry-run", action="store_true")

    digest = sub.add_parser("digest", help="Tagesuebersicht jetzt senden")
    digest.add_argument("--dry-run", action="store_true")

    sub.add_parser("doctor", help="Himalaya, Ollama, Ordner, Kalender und Nextcloud pruefen")
    sub.add_parser("status", help="Lokalen Verarbeitungsstatus anzeigen")
    review = sub.add_parser("review", help="Review-Gruende und Einzelfaelle read-only untersuchen")
    review_sub = review.add_subparsers(dest="review_command", required=True)
    review_status = review_sub.add_parser("status", help="Review-Gruende aggregiert anzeigen")
    review_status.add_argument("--days", type=int, default=7)
    review_list = review_sub.add_parser("list", help="Review-Metadaten nach typisiertem Grund anzeigen")
    review_list.add_argument("--reason", required=True)
    review_list.add_argument("--limit", type=int, default=50)
    review_suggest = review_sub.add_parser(
        "suggest", help="Genau eine Mail read-only und evidenzbasiert neu einschaetzen"
    )
    review_suggest.add_argument("--folder", required=True)
    review_suggest.add_argument("--message-id", required=True)
    review_suggest.add_argument("--expected-subject", required=True)
    folders = sub.add_parser("folders", help="Konfigurierte Mailordner planen oder explizit anlegen")
    folders_sub = folders.add_subparsers(dest="folders_command", required=True)
    folders_sub.add_parser("plan", help="Fehlende konfigurierte Ordner read-only anzeigen")
    folders_apply = folders_sub.add_parser("apply", help="Fehlende konfigurierte Ordner anlegen")
    folders_apply.add_argument("--yes", action="store_true")
    folders_activate = folders_sub.add_parser(
        "activate-relevant",
        help="Genau einen Relevant-Zielordner konfigurieren und explizit anlegen",
    )
    folders_activate.add_argument("--relevant", required=True)
    folders_activate.add_argument("--yes", action="store_true")
    performance = sub.add_parser("performance", help="Privacy-sichere Laufzeitmessungen anzeigen")
    performance.add_argument("--limit", type=int, default=20, help="Anzahl der letzten Laeufe (1-500)")
    performance.add_argument("--raw", action="store_true", help="Unverdichtete Telemetrie-Datensaetze anzeigen")

    invoices = sub.add_parser("invoices", help="Rechnungsmetadaten, OCR und Jahresregister verwalten")
    inv_sub = invoices.add_subparsers(dest="invoices_command", required=True)
    inv_sub.add_parser("status", help="OCR-Werkzeuge, Register und Rechnungszaehler anzeigen")
    inv_list = inv_sub.add_parser("list", help="Rechnungsregistereintraege ohne PDF-Inhalt anzeigen")
    inv_list.add_argument("--year", type=int, default=0)
    inv_list.add_argument("--status", default="", choices=("", "confirmed", "confirmed-manual", "review", "error"))
    inv_list.add_argument("--limit", type=int, default=100)
    inv_review = inv_sub.add_parser("review", help="Unsichere Rechnungsmetadaten anzeigen")
    inv_review.add_argument("--limit", type=int, default=100)
    inv_export = inv_sub.add_parser(
        "export",
        help="Jahres-CSV schreibfrei vorschauen oder explizit im Nextcloud-Jahresordner aktualisieren",
    )
    inv_export.add_argument("--year", type=int, required=True)
    inv_export.add_argument("--nextcloud", action="store_true", help="Kompatibilitaetsoption; R26 speichert immer nur in Nextcloud")
    inv_export.add_argument("--filename", default="", help="Nur der feste Name Rechnungen_YYYY.csv ist erlaubt")
    inv_export_effect = inv_export.add_mutually_exclusive_group()
    inv_export_effect.add_argument(
        "--dry-run",
        action="store_true",
        help="CSV nur im Speicher rendern; weder SQLite noch Nextcloud aendern",
    )
    inv_export_effect.add_argument(
        "--yes",
        action="store_true",
        help="Verwaltetes Nextcloud-Jahresregister ausdruecklich bedingt ersetzen",
    )
    inv_backfill = inv_sub.add_parser(
        "backfill",
        help="Archivierte Rechnungs-PDFs vorschauen oder explizit in SQLite und Jahresregister uebernehmen",
    )
    inv_backfill.add_argument("--year", type=int, required=True)
    inv_backfill.add_argument("--limit", type=int, default=500)
    inv_backfill_effect = inv_backfill.add_mutually_exclusive_group()
    inv_backfill_effect.add_argument(
        "--dry-run",
        action="store_true",
        help="PDFs nur lesen und auswerten; weder SQLite noch Nextcloud aendern",
    )
    inv_backfill_effect.add_argument(
        "--yes",
        action="store_true",
        help="Extraktion ausdruecklich in SQLite speichern und Jahresregister bedingt ersetzen",
    )
    inv_reprocess = inv_sub.add_parser(
        "reprocess",
        help="Review- oder unklassifizierte Rechnungen schreibfrei neu bewerten",
    )
    inv_reprocess.add_argument("--status", required=True, choices=("review", "unclassified"))
    inv_reprocess.add_argument("--source-year", type=int, required=True)
    inv_reprocess.add_argument("--limit", type=int, default=100)
    inv_reprocess.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Nur PDF lesen, scannen und Alt/Neu-Vorschlag ausgeben; nichts speichern",
    )
    inv_correct = inv_sub.add_parser("correct", help="Unsichere Rechnungsmetadaten nach ausdruecklichem Auftrag korrigieren")
    inv_correct.add_argument("--hash", required=True, dest="attachment_hash")
    inv_correct.add_argument("--date", required=True, dest="invoice_date")
    inv_correct.add_argument("--number", required=True, dest="invoice_number")
    inv_correct.add_argument("--supplier", required=True)
    inv_correct.add_argument("--category", required=True)
    inv_correct.add_argument("--gross", required=True)
    inv_correct.add_argument("--net", default="")
    inv_correct.add_argument("--tax", default="")
    inv_correct.add_argument("--currency", default="EUR")
    inv_correct.add_argument("--due-date", default="")
    inv_correct.add_argument("--yes", action="store_true")
    sub.add_parser("test-config", help="Konfiguration laden und Pfade ausgeben")
    sub.add_parser("guide", help="Zustand pruefen und konkrete naechste Schritte anzeigen")
    sub.add_parser("production-check", help="Maschinenlesbare Produktionsfreigabe fuer Supervisor pruefen")
    sub.add_parser("lock-status", help="Echte Prozesssperre des Mail-Interfaces maschinenlesbar pruefen")
    sub.add_parser("configure", help="Interaktiver Konfigurationsassistent")
    sub.add_parser("onboard", help="Gefuehrtes Erst-Setup fuer Grundkonfiguration, Nextcloud und Mailordner")
    help_parser = sub.add_parser("help", help="Ausfuehrliche thematische Hilfe anzeigen")
    help_parser.add_argument("topic", nargs="?", default="overview", help="Hilfethema, z. B. training oder nextcloud")

    nextcloud = sub.add_parser(
        "nextcloud",
        help="Native Nextcloud-CalDAV/CardDAV-Bruecke verwalten",
    )
    nc_sub = nextcloud.add_subparsers(dest="nextcloud_command", required=True)
    nc_sub.add_parser(
        "setup",
        help="App-Passwort, Kalender und Adressbuch interaktiv einrichten",
    )
    nc_sub.add_parser("doctor", help="Nextcloud-Verbindung live pruefen")
    nc_sub.add_parser("status", help="Alias fuer 'nextcloud doctor'")
    nc_sub.add_parser(
        "verify-skill",
        help="Kompatibilitaetsalias: native Release-Bruecke pruefen",
    )
    nc_sub.add_parser(
        "skill-card",
        help="Kompatibilitaetsalias: eingeschraenkten nativen Vertrag anzeigen",
    )
    nc_install = nc_sub.add_parser(
        "install-skill",
        help="Kompatibilitaetsalias; es wird kein Drittcode installiert",
    )
    nc_install.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    nc_install.add_argument(
        "--allow-review",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    nc_sub.add_parser("calendars", help="Verfuegbare Nextcloud-Kalender auflisten")
    nc_sub.add_parser("addressbooks", help="Verfuegbare Nextcloud-Adressbuecher auflisten")
    nc_contacts = nc_sub.add_parser("contacts", help="Kontakte anhand eines Suchbegriffs suchen")
    nc_contacts.add_argument("--query", required=True, help="Name oder E-Mail-Adresse")
    nc_sub.add_parser("sync-contacts", help="Lokalen CardDAV-E-Mail-Cache aktualisieren")
    nc_sub.add_parser("clear-contact-cache", help="Lokalen CardDAV-E-Mail-Cache loeschen")
    nc_disable = nc_sub.add_parser(
        "disable",
        help="Nextcloud-Bruecke deaktivieren; Skill und Secrets bleiben fuer eine spaetere Reaktivierung erhalten",
    )
    nc_disable.add_argument("--yes", action="store_true", help="Deaktivierung ausdruecklich bestaetigen")

    training = sub.add_parser("training", help="Regeln und Korrekturfeedback nachvollziehbar verwalten")
    tr_sub = training.add_subparsers(dest="training_command", required=True)
    tr_sub.add_parser("status", help="Regel- und Feedbackzaehler anzeigen")
    tr_sub.add_parser("rules", help="Aktuelle harte Regeln anzeigen")
    feedback = tr_sub.add_parser("feedback", help="Letzte Nutzerkorrekturen ohne Mailtext anzeigen")
    feedback.add_argument("--limit", type=int, default=50)
    not_spam = tr_sub.add_parser("not-spam", help="Nicht-Spam-Gegenbelege und ihren Ursprung ohne Mailinhalte anzeigen")
    not_spam.add_argument("--limit", type=int, default=100)
    mixed = tr_sub.add_parser("mixed-senders", help="Absender mit unterschiedlichen korrigierten Mailtypen anzeigen")
    mixed.add_argument("--limit", type=int, default=100)
    conflicts = tr_sub.add_parser("conflicts", help="Widerspruechliche Korrekturen fuer dasselbe Betreffmuster anzeigen")
    conflicts.add_argument("--limit", type=int, default=100)
    conflicts.add_argument("--id", default="", help="Optional genau eine conflict_id anzeigen")
    evaluate = tr_sub.add_parser("evaluate", help="Lernqualitaet mit chronologischem Basisvergleich auswerten")
    evaluate.add_argument("--limit", type=int, default=5000)
    dataset_export = tr_sub.add_parser("dataset-export", help="Pseudonymisierten Lern-Datensatz ohne Mailtexte exportieren")
    dataset_export.add_argument("--output", default="mail_agent/data/learning_dataset.json")
    dataset_export.add_argument("--limit", type=int, default=5000)
    tr_sub.add_parser("folder-list", help="Dynamische Korrektur-Unterordner anzeigen")
    folder_create = tr_sub.add_parser("folder-create", help="Kontrollierten Korrektur-Unterordner anlegen")
    folder_create.add_argument("--parent", required=True, choices=("routine", "important", "spam", "not-spam"))
    folder_create.add_argument("--name", required=True)
    folder_create.add_argument("--label", default="")
    folder_create.add_argument("--yes", action="store_true", help="IMAP-Ordnererstellung ausdruecklich bestaetigen")
    folder_disable = tr_sub.add_parser("folder-disable", help="Lernzuordnung deaktivieren; IMAP-Ordner bleibt bestehen")
    folder_disable.add_argument("--folder", required=True)
    folder_disable.add_argument("--yes", action="store_true")
    export = tr_sub.add_parser("export", help="Regeln und Korrekturmetadaten sicher exportieren")
    export.add_argument("--output", default="mail_agent/data/training_export.json")
    for name, description in (
        ("rule-add", "Harte Regel mit Sicherung hinzufuegen"),
        ("rule-remove", "Harte Regel mit Sicherung entfernen"),
    ):
        rule = tr_sub.add_parser(name, help=description)
        rule.add_argument("category", choices=("spam", "important", "routine"))
        rule.add_argument("kind", help="address, domain, sender-name oder subject-phrase")
        rule.add_argument("value", help="Regelwert")
    forget = tr_sub.add_parser("forget-sender", help="Gespeichertes Korrekturfeedback eines Absenders entfernen")
    forget.add_argument("email", help="Exakte Absenderadresse")
    forget.add_argument("--yes", action="store_true", help="Loeschung des Feedbacks ausdruecklich bestaetigen")
    forget_feedback = tr_sub.add_parser(
        "forget-feedback",
        help="Genau einen Korrekturdatensatz anhand seiner ID entfernen",
    )
    forget_feedback.add_argument("id", type=int, help="ID aus 'training feedback'")
    forget_feedback.add_argument("--yes", action="store_true", help="Loeschung ausdruecklich bestaetigen")
    return parser


def _productive_checks_with_folder_self_heal(agent: MailAgent) -> dict[str, object]:
    """Run the normal doctor and repair only missing configured Agent folders.

    This is deliberately narrow: no configuration, credentials, permissions or
    safety gates are changed. A failed repair remains a normal productive blocker.
    """

    checks = agent.doctor()
    folders = checks.get("folders")
    missing = list(folders.get("missing") or []) if isinstance(folders, dict) else []
    if not missing:
        return checks
    config = getattr(agent, "config", None)
    configured_folders = getattr(config, "folders", None)
    relevant = str(getattr(configured_folders, "relevant", "") or "").strip()
    if relevant and relevant.casefold() in {str(item).casefold() for item in missing}:
        logging.getLogger(__name__).warning(
            "Neuer Relevant-Ordner fehlt; automatische Erstellung bleibt bis zur expliziten "
            "Freigabe mit 'mail folders apply --yes' blockiert."
        )
        if isinstance(folders, dict):
            folders["explicit_activation_required"] = True
            folders["activation_command"] = (
                "./scripts/assistant.sh mail folders plan; danach nach Freigabe "
                "./scripts/assistant.sh mail folders apply --yes"
            )
        return checks

    logging.getLogger(__name__).warning(
        "Fehlende Agent-Mailordner erkannt; fuehre sicheres Setup aus: %s",
        ", ".join(missing),
    )
    results = agent.setup()
    repair_report = {
        "event": "mail-folder-self-heal",
        "missing_before": missing,
        "results": [asdict(result) for result in results],
    }
    print(json.dumps(repair_report, ensure_ascii=False), file=sys.stderr)
    return agent.doctor()


def _activate_relevant_folder(agent: MailAgent, requested_folder: str) -> dict[str, object]:
    """Configure and create exactly one relevant-mail target after approval.

    The caller must hold the productive mail lock.  A different existing target
    is a configuration conflict, and a failed or uncertain IMAP create restores
    the previous local configuration.  A remotely created folder is deliberately
    never deleted during compensation.
    """

    target = str(requested_folder or "").strip()
    if not target or "\r" in target or "\n" in target:
        raise ValueError("Relevant-Ordner darf nicht leer sein oder Zeilenumbrueche enthalten")
    if target.startswith("/") or target.endswith("/") or "//" in target:
        raise ValueError("Relevant-Ordner muss ein normalisierter relativer IMAP-Pfad sein")
    review_root = str(agent.config.folders.review or "").strip().split("/", 1)[0]
    if not review_root or not target.casefold().startswith((review_root + "/").casefold()):
        raise ValueError(
            "Relevant-Ordner muss unter demselben Agent-Wurzelordner wie folders.review liegen"
        )

    current = str(agent.config.folders.relevant or "").strip()
    if current and current.casefold() != target.casefold():
        raise RuntimeError(
            f"Konfigurationskonflikt: folders.relevant ist bereits auf {current!r} gesetzt"
        )

    backup: Path | None = None
    configuration_changed = not current
    if configuration_changed:
        backup = update_toml_values(agent.config.path, {("folders", "relevant"): target})
        try:
            agent.config = load_config(agent.config.path)
        except Exception:
            shutil.copy2(backup, agent.config.path)
            agent.config = load_config(agent.config.path)
            raise

    results = agent.himalaya.ensure_folders([target])
    created = [item.destination for item in results if item.ok and item.status == "created"]
    failed = [item for item in results if not item.ok]
    folders, folder_error = agent.himalaya.list_folders()
    target_present = target.casefold() in {item.casefold() for item in folders}
    ok = not failed and not folder_error and target_present
    if not ok and backup is not None:
        shutil.copy2(backup, agent.config.path)
        agent.config = load_config(agent.config.path)

    payload: dict[str, object] = {
        "ok": ok,
        "target": target,
        "configuration_changed": configuration_changed,
        "configuration_restored": bool(not ok and backup is not None),
        "backup": str(backup) if backup is not None else "",
        "results": [asdict(item) for item in results],
        "target_present": target_present,
        "folder_error": folder_error,
        "moves_performed": 0,
        "external_change_may_persist_after_rollback": bool(created),
    }
    if not ok:
        payload["error"] = (
            folder_error
            or "; ".join(item.detail or item.status for item in failed)
            or "Relevant-Ordner konnte nach der Erstellung nicht verifiziert werden"
        )
    return payload


def _print_config(config: Config) -> None:
    print(json.dumps({
        "config": str(config.path),
        "environment_file": str(default_env_file()),
        "database": str(config.runtime.database),
        "rules": str(config.runtime.rules_file),
        "model": config.ollama.model,
        "batch_classification": {
            "enabled": config.ollama.batch_enabled,
            "batch_size": config.ollama.batch_size,
            "prefetch": config.ollama.batch_prefetch,
            "batch_timeout_seconds": config.ollama.batch_timeout_seconds,
            "single_mail_uses_single_request": True,
            "batch_max_body_chars": config.ollama.batch_max_body_chars,
            "batch_max_total_chars": config.ollama.batch_max_total_chars,
            "think": config.ollama.think,
            "num_ctx_override": config.ollama.num_ctx,
            "fallback_to_smaller_groups": config.ollama.batch_fallback_to_smaller_groups,
        },
        "forward_to": config.mailbox.forward_to,
        "calendar_backend": config.calendar.backend,
        "calendar_requires_trusted_sender": config.calendar.require_trusted_sender,
        "calendar_trust_feedback_count": config.calendar.trust_feedback_count,
        "calendar_approval_required": config.calendar.approval_required,
        "calendar_approval_recipient": config.calendar.approval_recipient or config.mailbox.forward_to,
        "calendar_approval_reply_from": config.calendar.approval_reply_from or config.mailbox.forward_to,
        "calendar_require_future": config.calendar.require_future,
        "invoice_archive": {
            "enabled": config.invoices.enabled,
            "require_routine": config.invoices.require_routine,
            "min_confidence": config.invoices.min_confidence,
            "nextcloud_folder": config.invoices.nextcloud_folder,
            "organize_by_year_month": config.invoices.organize_by_year_month,
        },
        "nextcloud_enabled": config.nextcloud.enabled,
        "nextcloud_backend": "native-caldav-carddav",
        "nextcloud_calendar": config.nextcloud.calendar,
        "nextcloud_addressbook": config.nextcloud.addressbook,
        "nextcloud_contacts_prevent_spam": config.nextcloud.contacts_prevent_spam,
        "nextcloud_contacts_trusted_for_calendar": config.nextcloud.trust_contacts_for_calendar,
    }, indent=2, ensure_ascii=False))


def _safe_contact_view(item: dict[str, Any]) -> dict[str, Any]:
    """Return only fields useful for mail identity checks, not notes/phones/addresses."""

    name = ""
    for key in ("displayName", "fullName", "name", "fn"):
        value = str(item.get(key) or "").strip()
        if value:
            name = value
            break
    emails = sorted(NextcloudSkillClient.contact_emails(item))
    return {"name": name, "emails": emails}


def _confirm(prompt: str, *, explicit_yes: bool) -> bool:
    if explicit_yes:
        return True
    if not sys.stdin.isatty():
        return False
    answer = input(prompt + " [j/N]: ").strip().casefold()
    return answer in {"j", "ja", "y", "yes"}


def _handle_nextcloud(args: argparse.Namespace, config: Config) -> int:
    if args.nextcloud_command == "setup":
        if not sys.stdin.isatty():
            print("Der Nextcloud-Setup-Assistent benoetigt ein interaktives Terminal.", file=sys.stderr)
            return 2
        agent = MailAgent(config, dry_run=True)
        try:
            try:
                updated, _ = interactive_nextcloud_setup(config, agent.nextcloud)
            except (EOFError, KeyboardInterrupt):
                print("\nNextcloud-Setup abgebrochen; bestehende Werte bleiben erhalten.", file=sys.stderr)
                return 130
            except Exception as exc:
                print(f"Nextcloud-Setup fehlgeschlagen: {exc}", file=sys.stderr)
                print("Hilfe: ./scripts/mail-agent.sh help nextcloud", file=sys.stderr)
                return 2
        finally:
            agent.close()
        updated_agent = MailAgent(updated, dry_run=True)
        try:
            health = updated_agent.nextcloud.health(live=True)
            files_health = updated_agent.assistant_bridge.health()
            result = {
                **health,
                "files": {"ok": files_health.ok, "detail": files_health.detail},
                "invoice_archive_enabled": updated.invoices.enabled,
                "invoice_folder": updated.invoices.nextcloud_folder,
            }
            if updated.invoices.enabled:
                result["ok"] = bool(health.get("ok")) and files_health.ok
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("ok") else 1
        finally:
            updated_agent.close()

    agent = MailAgent(config, dry_run=True)
    try:
        client = agent.nextcloud
        command = args.nextcloud_command
        if command in {"doctor", "status"}:
            result = client.health(live=True)
            files_health = agent.assistant_bridge.health()
            result["files"] = {
                "ok": files_health.ok,
                "enabled": config.invoices.enabled,
                "folder": config.invoices.nextcloud_folder,
                "detail": files_health.detail,
            }
            if config.invoices.enabled:
                result["ok"] = bool(result.get("ok")) and files_health.ok
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("ok") else 1
        if command == "verify-skill":
            result = client.verify_skill()
            print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
            return 0 if result.ok else 1
        if command == "skill-card":
            result = client.skill_card()
            print(result.detail)
            return 0 if result.ok else 1
        if command == "install-skill":
            result = client.install_skill(allow_review=args.allow_review)
            print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
            return 0 if result.ok else 1
        if command == "disable":
            if not _confirm(
                "Nextcloud fuer den Mail-Agenten deaktivieren und Kalender auf sichere ICS-Warteschlange stellen",
                explicit_yes=args.yes,
            ):
                print("Keine Aenderung vorgenommen.", file=sys.stderr)
                return 2
            changes: dict[tuple[str, str], object] = {
                ("nextcloud", "enabled"): False,
                ("invoices", "enabled"): False,
            }
            if config.calendar.backend == "nextcloud_skill":
                changes[("calendar", "backend")] = "queue"
            backup = update_toml_values(config.path, changes)
            invalidate_dry_run(config, "Nextcloud-Konfiguration wurde deaktiviert")
            cache_result = client.clear_contact_cache()
            print(json.dumps({
                "ok": True,
                "config_backup": str(backup),
                "calendar_backend": "queue" if config.calendar.backend == "nextcloud_skill" else config.calendar.backend,
                "contact_cache": asdict(cache_result),
                "note": (
                    "Die native Release-Bruecke bleibt unveraendert; "
                    "~/.config/mail-agent.env wurde nicht geloescht."
                ),
            }, indent=2, ensure_ascii=False))
            return 0
        try:
            if command == "calendars":
                print(json.dumps(client.list_calendars(), indent=2, ensure_ascii=False))
            elif command == "addressbooks":
                print(json.dumps(client.list_addressbooks(), indent=2, ensure_ascii=False))
            elif command == "contacts":
                contacts = [_safe_contact_view(item) for item in client.search_contacts(args.query)]
                print(json.dumps(contacts, indent=2, ensure_ascii=False))
            elif command == "sync-contacts":
                ok, detail = client.refresh_contact_cache(force=True)
                print(json.dumps({"ok": ok, "detail": detail}, indent=2, ensure_ascii=False))
                return 0 if ok else 1
            elif command == "clear-contact-cache":
                result = client.clear_contact_cache()
                print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
                return 0 if result.ok else 1
            else:
                return 2
            return 0
        except NextcloudSkillError as exc:
            print(json.dumps({"ok": False, "detail": str(exc)}, indent=2, ensure_ascii=False))
            return 1
    finally:
        agent.close()


def _handle_training(args: argparse.Namespace, config: Config) -> int:
    agent = MailAgent(config, dry_run=True)
    try:
        manager = TrainingManager(config.runtime.rules_file, agent.storage)
        learning = LearningFolderRegistry(config)
        command = args.training_command
        if command == "status":
            payload = manager.status()
            payload["learning_folders"] = [item.to_dict() for item in learning.list()]
            payload["mixed_senders"] = len(agent.storage.mixed_senders(limit=10000))
            payload["pattern_conflicts"] = len(agent.storage.pattern_conflicts(limit=10000))
            payload["not_spam_feedback"] = agent.storage.not_spam_feedback_summary()
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if command == "rules":
            print(json.dumps(manager.rules(), indent=2, ensure_ascii=False))
            return 0
        if command == "feedback":
            print(json.dumps(agent.storage.list_feedback(limit=args.limit), indent=2, ensure_ascii=False))
            return 0
        if command == "not-spam":
            print(json.dumps({
                "ok": True,
                "summary": agent.storage.not_spam_feedback_summary(),
                "records": agent.storage.list_not_spam_feedback(limit=args.limit),
            }, indent=2, ensure_ascii=False))
            return 0
        if command == "mixed-senders":
            print(json.dumps(agent.storage.mixed_senders(limit=args.limit), indent=2, ensure_ascii=False))
            return 0
        if command == "conflicts":
            result = agent.storage.pattern_conflicts(limit=args.limit, conflict_id=args.id)
            print(json.dumps({"ok": True, "conflict_id": args.id or "", "conflicts": result}, indent=2, ensure_ascii=False))
            return 0 if result or not args.id else 1
        if command == "evaluate":
            report = LearningQualityAnalyzer(agent.storage).report(limit=args.limit)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        if command == "dataset-export":
            output = Path(args.output).expanduser()
            output = (Path.cwd() / output).resolve() if not output.is_absolute() else output.resolve()
            allowed_root = config.runtime.database.parent.resolve()
            try:
                output.relative_to(allowed_root)
            except ValueError:
                print(
                    f"Dataset-Export ist nur innerhalb von {allowed_root} erlaubt.",
                    file=sys.stderr,
                )
                return 2
            exported = LearningQualityAnalyzer(agent.storage).export_dataset(output, limit=args.limit)
            print(json.dumps({
                "ok": True,
                "path": str(exported),
                "records": len(json.loads(exported.read_text(encoding="utf-8")).get("records", [])),
                "mode": oct(exported.stat().st_mode & 0o777),
                "contains_mail_bodies": False,
                "contains_email_addresses": False,
            }, indent=2, ensure_ascii=False))
            return 0
        if command == "folder-list":
            print(json.dumps({
                "ok": True,
                "registry": str(learning.path),
                "folders": [item.to_dict() for item in learning.list()],
            }, indent=2, ensure_ascii=False))
            return 0
        if command == "folder-create":
            if not args.yes:
                print("Ordnererstellung erfordert --yes nach ausdruecklichem Nutzerauftrag.", file=sys.stderr)
                return 2
            item = learning.create(parent=args.parent, name=args.name, label=args.label)
            agent.himalaya.dry_run = False
            results = agent.himalaya.ensure_folders([item.folder])
            ok = bool(results) and all(result.ok for result in results)
            if not ok:
                learning.disable(item.folder)
            if ok:
                invalidate_dry_run(config, f"Dynamischer Korrekturordner angelegt: {item.folder}")
            print(json.dumps({
                "ok": ok,
                "folder": item.to_dict(),
                "imap": [asdict(result) for result in results],
                "new_dry_run_required": ok,
            }, indent=2, ensure_ascii=False))
            return 0 if ok else 1
        if command == "folder-disable":
            if not args.yes:
                print("Deaktivierung erfordert --yes nach ausdruecklichem Nutzerauftrag.", file=sys.stderr)
                return 2
            changed = learning.disable(args.folder)
            if changed:
                invalidate_dry_run(config, f"Dynamischer Korrekturordner deaktiviert: {args.folder}")
            print(json.dumps({
                "ok": changed,
                "folder": args.folder,
                "imap_folder_deleted": False,
                "new_dry_run_required": changed,
            }, indent=2, ensure_ascii=False))
            return 0 if changed else 1
        if command == "export":
            output = manager.export(Path(args.output))
            print(json.dumps({"ok": True, "path": str(output)}, indent=2, ensure_ascii=False))
            return 0
        if command in {"rule-add", "rule-remove"}:
            change = (
                manager.add_rule(args.category, args.kind, args.value)
                if command == "rule-add"
                else manager.remove_rule(args.category, args.kind, args.value)
            )
            print(json.dumps(asdict(change), indent=2, ensure_ascii=False, default=str))
            if change.changed:
                invalidate_dry_run(
                    config,
                    f"Trainingsregel geaendert: {change.category}.{change.kind}={change.value}",
                )
                print(
                    "Regeln wurden geaendert. Vor dem naechsten produktiven Lauf ist ein neuer Dry-Run erforderlich.",
                    file=sys.stderr,
                )
            return 0
        if command == "forget-sender":
            sender = normalize_address(args.email)
            if not sender or "@" not in sender:
                print("Ungueltige E-Mail-Adresse.", file=sys.stderr)
                return 2
            if not _confirm(
                f"Alle gespeicherten Korrekturdatensaetze fuer {sender} entfernen",
                explicit_yes=args.yes,
            ):
                print("Keine Aenderung vorgenommen.", file=sys.stderr)
                return 2
            deleted = agent.storage.delete_feedback_for_sender(sender)
            if deleted:
                invalidate_dry_run(config, f"Korrekturfeedback fuer {sender} entfernt")
            print(json.dumps({
                "ok": True,
                "sender": sender,
                "deleted_feedback_rows": deleted,
                "new_dry_run_required": bool(deleted),
            }, indent=2, ensure_ascii=False))
            return 0
        if command == "forget-feedback":
            if args.id <= 0:
                print("Feedback-ID muss groesser als 0 sein.", file=sys.stderr)
                return 2
            if not _confirm(
                f"Korrekturdatensatz mit ID {args.id} entfernen",
                explicit_yes=args.yes,
            ):
                print("Keine Aenderung vorgenommen.", file=sys.stderr)
                return 2
            deleted = agent.storage.delete_feedback_by_id(args.id)
            if deleted:
                invalidate_dry_run(config, f"Korrekturfeedback-ID {args.id} entfernt")
            print(json.dumps({
                "ok": bool(deleted),
                "feedback_id": args.id,
                "deleted_feedback_rows": deleted,
                "new_dry_run_required": bool(deleted),
            }, indent=2, ensure_ascii=False))
            return 0 if deleted else 1
        return 2
    except (OSError, ValueError) as exc:
        print(f"Trainingseingriff fehlgeschlagen: {exc}", file=sys.stderr)
        return 2
    finally:
        agent.close()


def _handle_onboard(config: Config) -> int:
    if not sys.stdin.isatty():
        print("Der Onboarding-Assistent benoetigt ein interaktives Terminal.", file=sys.stderr)
        return 2

    print("MAIL-AGENT ONBOARDING")
    print("=====================")
    print(
        "Der Assistent aktiviert weder den systemd-Timer noch einen produktiven Mail-Lauf. "
        "Am Ende bleibt ein Dry-Run erforderlich.\n"
    )
    try:
        config, _ = interactive_configure(config)
    except (EOFError, KeyboardInterrupt):
        print("\nOnboarding abgebrochen; bereits bestaetigte Aenderungen bleiben erhalten.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Grundkonfiguration fehlgeschlagen: {exc}", file=sys.stderr)
        return 2

    if _confirm("Nextcloud CalDAV/CardDAV jetzt einrichten", explicit_yes=False):
        agent = MailAgent(config, dry_run=True)
        try:
            config, _ = interactive_nextcloud_setup(config, agent.nextcloud)
        except (EOFError, KeyboardInterrupt):
            print("\nNextcloud-Einrichtung uebersprungen/abgebrochen.", file=sys.stderr)
        except Exception as exc:
            print(f"Nextcloud-Einrichtung noch nicht abgeschlossen: {exc}", file=sys.stderr)
            print("Spaeter fortsetzen mit: ./scripts/mail-agent.sh nextcloud setup", file=sys.stderr)
        finally:
            agent.close()

    agent = MailAgent(config, dry_run=True)
    try:
        checks = agent.doctor()
        folders = checks.get("folders", {})
        missing = folders.get("missing", []) if isinstance(folders, dict) else []
    finally:
        agent.close()

    if missing and _confirm("Fehlende Agent-Mailordner jetzt anlegen", explicit_yes=False):
        setup_agent = MailAgent(config, dry_run=False)
        try:
            results = setup_agent.setup()
            print(json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False))
        finally:
            setup_agent.close()

    final_agent = MailAgent(config, dry_run=True)
    try:
        print("\n" + build_guide(config, final_agent.doctor()))
    finally:
        final_agent.close()
    return 0


def _invoice_row(row: object) -> dict[str, object]:
    item = dict(row)  # sqlite3.Row
    raw = str(item.pop("extraction_json", "") or "")
    if raw:
        try:
            metadata = json.loads(raw)
        except json.JSONDecodeError:
            metadata = {}
        if isinstance(metadata, dict):
            item["issues"] = metadata.get("issues") or []
            item["evidence"] = {
                name: (metadata.get(name) or {}).get("evidence", "")
                for name in ("invoice_date", "invoice_number", "supplier", "gross_amount")
                if isinstance(metadata.get(name), dict)
            }
    return item


def _backfill_year(row: object) -> int | None:
    item = dict(row)
    for value in (item.get("invoice_date"), item.get("received_date"), item.get("created_at")):
        raw = str(value or "").strip()
        if len(raw) >= 4 and raw[:4].isdigit():
            return int(raw[:4])
    remote = str(item.get("nextcloud_path") or "")
    match = __import__("re").search(r"(?:^|/)(20\d{2})(?:/|$)", remote)
    if match:
        return int(match.group(1))
    raw_received = str(item.get("received_at") or "")
    try:
        parsed = parsedate_to_datetime(raw_received)
        return parsed.year if parsed else None
    except (TypeError, ValueError, OverflowError):
        return None


def _row_message(row: object) -> ParsedMessage:
    item = dict(row)
    return ParsedMessage(
        stable_key=str(item.get("stable_key") or ""),
        mailbox_id=str(item.get("mailbox_id") or ""),
        source_folder=str(item.get("last_folder") or ""),
        raw=b"",
        message_id=str(item.get("message_id") or ""),
        subject=str(item.get("subject") or ""),
        sender_name=str(item.get("sender_name") or ""),
        sender_addr=str(item.get("sender_addr") or ""),
        date=str(item.get("received_at") or item.get("received_date") or item.get("created_at") or ""),
        received_at=str(item.get("received_at") or item.get("received_date") or item.get("created_at") or ""),
    )


def _validate_iso_date(value: str, *, optional: bool = False) -> str:
    raw = str(value or "").strip()
    if optional and not raw:
        return ""
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("Datum muss YYYY-MM-DD entsprechen") from exc


def _sync_invoice_register(
    register: InvoiceRegister,
    *,
    year: int,
    invoice_tool: object,
    dry_run: bool = False,
) -> dict[str, object]:
    # The caller supplies one of multiple settings dataclasses behind this narrow dynamic boundary.
    folder = str(getattr(invoice_tool, "folder"))  # noqa: B009
    resource_id = str(getattr(invoice_tool, "resource_id"))  # noqa: B009
    rendered = register.render(year, invoice_folder=folder)
    bridge = PersonalAssistantActionBridge(dry_run=dry_run)
    result = bridge.sync_invoice_register(
        data=rendered.data,
        year=year,
        remote_path=rendered.path,
        resource_id=resource_id,
    )
    payload = rendered.to_dict()
    payload.update({
        "ok": result.ok,
        "status": result.status,
        "detail": result.detail,
        "path": result.path or rendered.path,
        "destination": result.destination or resource_id,
        "storage": "nextcloud-only",
    })
    return payload


def _handle_invoice_reprocess(args: argparse.Namespace, config: Config) -> int:
    if not bool(getattr(args, "dry_run", False)):
        raise PermissionError("Reprocessing-Vorschau benoetigt zwingend --dry-run")
    tools = load_tool_settings()
    invoice_tool = tools.mail.invoices
    if not invoice_tool.enabled:
        raise PermissionError("Zentrales Rechnungswerkzeug ist in tools.toml deaktiviert")
    reader = ReadOnlyInvoicePdfReader()
    extractor = InvoiceExtractor(config.invoices)
    with tempfile.TemporaryDirectory(prefix="openclaw-invoice-preview-") as temporary:
        temp_root = Path(temporary)
        antivirus_settings = replace(
            tools.security.antivirus,
            temp_dir=temp_root / "scan",
        )
        antivirus = HostAntivirus(
            antivirus_settings,
            database=temp_root / "antivirus.sqlite3",
        )

        def read_pdf(remote_path: str) -> bytes:
            data = reader.read(
                remote_path,
                allowed_folder=invoice_tool.folder,
                resource_id=invoice_tool.resource_id,
            )
            if len(data) > config.invoices.max_pdf_bytes:
                raise ValueError("PDF ueberschreitet die konfigurierte Maximalgroesse")
            return data

        def scan_pdf(data: bytes, name: str) -> str:
            scan = antivirus.scan_bytes(
                data,
                name=name,
                source_type="invoice-reprocess-preview",
                use_cache=False,
            )
            if not scan.clean:
                raise RuntimeError("antivirus-gate-blocked")
            return scan.scanner_identity

        try:
            payload = run_reprocess_preview(
                config.runtime.database,
                status=str(args.status),
                source_year=int(args.source_year),
                limit=int(args.limit),
                extractor=extractor,
                read_pdf=read_pdf,
                scan_pdf=scan_pdf,
            )
        finally:
            antivirus.close()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


def _handle_invoices(args: argparse.Namespace, config: Config) -> int:
    if args.invoices_command == "reprocess":
        return _handle_invoice_reprocess(args, config)
    storage = Storage(config.runtime.database)
    try:
        register = InvoiceRegister(storage, config.invoices)
        extractor = InvoiceExtractor(config.invoices)
        command = args.invoices_command
        if command == "status":
            tools = load_tool_settings()
            invoice_tool = tools.mail.invoices
            years = register.status(invoice_folder=invoice_tool.folder)
            ocr = extractor.doctor()
            payload = {
                "ok": bool(ocr.get("ok")),
                "ocr": ocr,
                "register": years,
                "counts": {
                    "all": len(storage.list_invoices(limit=5000)),
                    "review": len(storage.list_invoices(extraction_status="review", limit=5000)),
                    "confirmed": len(storage.list_invoices(extraction_status="confirmed", limit=5000))
                    + len(storage.list_invoices(extraction_status="confirmed-manual", limit=5000)),
                },
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0 if payload["ok"] else 1
        if command in {"list", "review"}:
            year = int(getattr(args, "year", 0) or 0) or None
            status = "review" if command == "review" else str(getattr(args, "status", "") or "")
            rows = storage.list_invoices(year=year, extraction_status=status, limit=max(1, args.limit))
            print(json.dumps({"ok": True, "count": len(rows), "records": [_invoice_row(row) for row in rows]}, indent=2, ensure_ascii=False, default=str))
            return 0
        if command == "export":
            if not 2000 <= int(args.year) <= 2100:
                raise ValueError("Jahr muss zwischen 2000 und 2100 liegen")
            if not args.dry_run and not args.yes:
                raise PermissionError(
                    "Export benoetigt --dry-run fuer die schreibfreie Vorschau oder --yes fuer "
                    "die Aktualisierung des Nextcloud-Jahresregisters"
                )
            expected_filename = f"Rechnungen_{int(args.year):04d}.csv"
            if args.filename and str(args.filename).strip() != expected_filename:
                raise ValueError(f"R26 erlaubt nur den festen Dateinamen {expected_filename}")
            tools = load_tool_settings()
            invoice_tool = tools.mail.invoices
            if not invoice_tool.enabled:
                raise PermissionError("Zentrales Rechnungswerkzeug ist in tools.toml deaktiviert")
            payload = _sync_invoice_register(
                register,
                year=args.year,
                invoice_tool=invoice_tool,
                dry_run=bool(args.dry_run),
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0 if payload.get("ok") else 1
        if command == "backfill":
            year = int(args.year)
            if not 2000 <= year <= 2100:
                raise ValueError("Jahr muss zwischen 2000 und 2100 liegen")
            if not args.dry_run and not args.yes:
                raise PermissionError("Produktiver Backfill benoetigt --yes nach ausdruecklichem Nutzerauftrag")
            tools = load_tool_settings()
            invoice_tool = tools.mail.invoices
            if not invoice_tool.enabled:
                raise PermissionError("Zentrales Rechnungswerkzeug ist in tools.toml deaktiviert")
            candidates = [
                row for row in storage.list_invoice_backfill_candidates(limit=max(1, args.limit))
                if _backfill_year(row) == year
            ]
            bridge = PersonalAssistantActionBridge(dry_run=False)
            antivirus = HostAntivirus(tools.security.antivirus)
            processed: list[dict[str, object]] = []
            errors: list[dict[str, str]] = []
            touched_years: set[int] = set()
            try:
                for row in candidates:
                    item = dict(row)
                    attachment_hash = str(item.get("attachment_hash") or "")
                    remote_path = str(item.get("nextcloud_path") or "")
                    try:
                        data = bridge.read_invoice_pdf(
                            remote_path=remote_path,
                            allowed_folder=invoice_tool.folder,
                            resource_id=invoice_tool.resource_id,
                        )
                        if len(data) > config.invoices.max_pdf_bytes:
                            raise ValueError("PDF ueberschreitet die konfigurierte Maximalgroesse")
                        scan = antivirus.scan_bytes(
                            data,
                            name=str(item.get("original_filename") or Path(remote_path).name or "invoice.pdf"),
                            source_type="invoice-backfill",
                        )
                        if scan.infected:
                            raise RuntimeError("Virenscanner meldet Schadsoftware: " + (scan.signature or scan.detail))
                        if scan.error and tools.security.antivirus.fail_closed:
                            raise RuntimeError("Virenscan fehlgeschlagen: " + (scan.detail or scan.status))
                        message = _row_message(row)
                        metadata = extractor.extract(
                            data,
                            message,
                            filename=str(
                                item.get("original_filename")
                                or Path(remote_path).name
                                or "invoice.pdf"
                            ),
                            scanner_identity=str(getattr(scan, "scanner_identity", "") or ""),
                        )
                        target_year = int(metadata.invoice_date.value[:4]) if metadata.invoice_date.value else year
                        processed.append({
                            "attachment_hash": attachment_hash,
                            "path": remote_path,
                            "status": metadata.status,
                            "invoice_date": metadata.invoice_date.value,
                            "invoice_number": metadata.invoice_number.value,
                            "supplier": metadata.supplier.value,
                            "gross_amount": metadata.gross_amount.value,
                            "method": metadata.method,
                            "confidence": metadata.confidence,
                            "issues": metadata.issues,
                            "would_update": bool(args.dry_run),
                        })
                        if not args.dry_run:
                            received = message.date
                            try:
                                parsed_received = parsedate_to_datetime(received)
                                received = parsed_received.date().isoformat() if parsed_received else ""
                            except (TypeError, ValueError, OverflowError):
                                received = str(item.get("received_date") or "")[:10]
                            storage.update_invoice_extraction(
                                attachment_hash,
                                invoice_date=metadata.invoice_date.value,
                                received_date=received,
                                invoice_number=metadata.invoice_number.value,
                                supplier=metadata.supplier.value,
                                category=metadata.category.value or "Ungeklärt",
                                gross_amount_cents=amount_to_cents(metadata.gross_amount.value),
                                net_amount_cents=amount_to_cents(metadata.net_amount.value),
                                tax_amount_cents=amount_to_cents(metadata.tax_amount.value),
                                currency=metadata.currency.value or "EUR",
                                due_date=metadata.due_date.value,
                                extraction_status=metadata.status,
                                extraction_confidence=metadata.confidence,
                                extraction_method="backfill-" + metadata.method,
                                extraction_json=metadata.to_json(),
                                register_year=target_year,
                            )
                            touched_years.add(target_year)
                    except Exception as exc:
                        errors.append({"attachment_hash": attachment_hash, "path": remote_path, "error": str(exc)})
            finally:
                antivirus.close()
            registers = []
            if not args.dry_run:
                for touched in sorted(touched_years or {year}):
                    registers.append(_sync_invoice_register(register, year=touched, invoice_tool=invoice_tool))
            payload = {
                "ok": not errors,
                "dry_run": bool(args.dry_run),
                "year": year,
                "candidates": len(candidates),
                "processed": processed,
                "errors": errors,
                "registers": registers,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0 if payload["ok"] else 1
        if command == "correct":
            if not args.yes:
                raise PermissionError("Metadatenkorrektur benoetigt --yes nach ausdruecklichem Nutzerauftrag")
            tools = load_tool_settings()
            invoice_tool = tools.mail.invoices
            if not invoice_tool.enabled:
                raise PermissionError("Zentrales Rechnungswerkzeug ist in tools.toml deaktiviert")
            invoice_date = _validate_iso_date(args.invoice_date)
            due_date = _validate_iso_date(args.due_date, optional=True)
            gross = amount_to_cents(args.gross)
            net = amount_to_cents(args.net) if args.net else None
            tax = amount_to_cents(args.tax) if args.tax else None
            if gross is None:
                raise ValueError("--gross ist kein gueltiger Betrag")
            old_year, new_year = storage.correct_invoice_metadata(
                args.attachment_hash, invoice_date=invoice_date, invoice_number=args.invoice_number.strip(),
                supplier=args.supplier.strip(), category=args.category.strip(), gross_amount_cents=gross,
                net_amount_cents=net, tax_amount_cents=tax, currency=args.currency.strip().upper(),
                due_date=due_date,
            )
            regenerated = []
            for year in sorted({v for v in (old_year, new_year) if v is not None}):
                regenerated.append(_sync_invoice_register(register, year=year, invoice_tool=invoice_tool))
            if not all(bool(item.get("ok")) for item in regenerated):
                print(json.dumps({"ok": False, "attachment_hash": args.attachment_hash, "registers": regenerated}, indent=2, ensure_ascii=False))
                return 1
            print(json.dumps({"ok": True, "attachment_hash": args.attachment_hash, "registers": regenerated}, indent=2, ensure_ascii=False))
            return 0
        raise ValueError(f"Unbekannter Rechnungsbefehl: {command}")
    finally:
        storage.close()


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)

    # Help must remain available even when config.toml or the secrets file is
    # broken. If config.toml is valid, append the actual paths and current values.
    if args.command == "help":
        help_config: Config | None = None
        warning = ""
        try:
            help_config = load_config(args.config)
        except Exception as exc:
            warning = f"\nHinweis: config.toml konnte nicht geladen werden: {exc}\n"
        print(extended_help(args.topic, help_config), end="")
        if warning:
            print(warning, file=sys.stderr, end="")
        return 0

    try:
        central_secrets = Path("~/.config/personal-assistant/secrets.env").expanduser().resolve()
        load_env_file(central_secrets)
        load_env_file()
    except ValueError as exc:
        print(f"Fehler in {default_env_file()}: {exc}", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        print("Hilfe: ./scripts/mail-agent.sh help config", file=sys.stderr)
        return 2

    _configure_logging(config.runtime.log_file, args.verbose)

    if args.command == "run" and getattr(args, "force", False):
        if os.environ.get("MAIL_AGENT_ALLOW_FORCE") != "YES" or not sys.stdin.isatty():
            print(
                "--force ist nur interaktiv mit MAIL_AGENT_ALLOW_FORCE=YES erlaubt; "
                "Automationen duerfen die Produktionssperre nicht umgehen.",
                file=sys.stderr,
            )
            return 4
        confirmation = input("Sicherheitspruefung wirklich umgehen? Tippe exakt FORCE: ").strip()
        if confirmation != "FORCE":
            print("Abgebrochen; Sicherheitspruefung bleibt aktiv.", file=sys.stderr)
            return 4

    if args.command == "onboard":
        return _handle_onboard(config)

    if args.command == "nextcloud":
        return _handle_nextcloud(args, config)
    if args.command == "training":
        return _handle_training(args, config)

    if args.command == "configure":
        if not sys.stdin.isatty():
            print("Der Konfigurationsassistent benoetigt ein interaktives Terminal.", file=sys.stderr)
            return 2
        try:
            config, _ = interactive_configure(config)
        except (EOFError, KeyboardInterrupt):
            print("\nKonfiguration abgebrochen; bestehende Werte bleiben erhalten.", file=sys.stderr)
            return 130
        except Exception as exc:
            print(f"Konfiguration fehlgeschlagen: {exc}", file=sys.stderr)
            return 2

    if args.command == "test-config":
        _print_config(config)
        return 0

    if args.command == "performance":
        path = config.runtime.database.parent / "performance.jsonl"
        records = read_recent_performance(path, limit=max(1, min(args.limit, 500)))
        result: object = records if args.raw else summarize_performance(records)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "invoices":
        try:
            return _handle_invoices(args, config)
        except (ValueError, KeyError, PermissionError, FileNotFoundError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except Exception as exc:
            logging.getLogger(__name__).exception("Rechnungsbefehl fehlgeschlagen")
            print(f"Fehler: {exc}", file=sys.stderr)
            return 1

    if args.command == "lock-status":
        result = inspect_process_lock(config.runtime.lock_file)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            return 1
        return 3 if result.get("locked") else 0

    dry_run = bool(getattr(args, "dry_run", False))
    agent = MailAgent(config, dry_run=dry_run)
    try:
        if args.command == "guide":
            print(build_guide(config, agent.doctor()))
            return 0

        if args.command == "production-check":
            checks = agent.doctor()
            blockers = productive_run_blockers(config, checks)
            state = read_setup_state(config)
            current_fingerprint = configuration_fingerprint(config)
            required = ("himalaya", "folders", "mail_sources", "ollama", "database", "config")
            required_ok = all(
                isinstance(checks.get(name), dict) and bool(checks[name].get("ok"))
                for name in required
            )
            dry_run_gate_blockers = {
                "Noch kein erfolgreicher Dry-Run protokolliert",
                "Konfiguration oder Regeln wurden seit dem letzten Dry-Run geaendert",
            }
            auto_recoverable = bool(
                blockers
                and required_ok
                and all(blocker in dry_run_gate_blockers for blocker in blockers)
            )
            result = {
                "ok": not blockers,
                "blockers": blockers,
                "auto_recoverable": auto_recoverable,
                "gate": {
                    "last_dry_run_at": state.get("last_dry_run_at", ""),
                    "last_dry_run_ok": bool(state.get("last_dry_run_ok")),
                    "stored_fingerprint": str(state.get("config_fingerprint") or ""),
                    "current_fingerprint": current_fingerprint,
                    "fingerprint_matches": state.get("config_fingerprint") == current_fingerprint,
                },
                "required_checks_ok": required_ok,
            }
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["ok"] else 4

        if args.command == "configure":
            checks = agent.doctor()
            print("\n" + build_guide(config, checks))
            return 0

        if args.command == "orders-import":
            try:
                with ProcessLock(config.runtime.lock_file):
                    result = agent.import_order_snapshots(limit=max(1, args.limit))
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                    return 0 if result.get("ok") else 1
            except ProcessLockError as exc:
                print(str(exc), file=sys.stderr)
                return 3

        if args.command == "spam-review":
            if not dry_run:
                checks = _productive_checks_with_folder_self_heal(agent)
                blockers = productive_run_blockers(config, checks)
                if blockers:
                    print("PRODUKTIVER SPAM-REVIEW BLOCKIERT", file=sys.stderr)
                    for blocker in blockers:
                        print(f"- {blocker}", file=sys.stderr)
                    return 4
            try:
                with ProcessLock(config.runtime.lock_file):
                    summary = agent.review_quarantine(limit=max(1, args.limit))
                    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
                    if dry_run:
                        record_dry_run(
                            config,
                            processed=summary.processed,
                            errors=summary.errors,
                            limit=max(1, args.limit),
                        )
                    return 0 if not summary.errors else 1
            except ProcessLockError as exc:
                print(str(exc), file=sys.stderr)
                return 3

        if args.command in {"setup", "run", "digest"}:
            if args.command == "run" and not dry_run and not args.force:
                checks = _productive_checks_with_folder_self_heal(agent)
                blockers = productive_run_blockers(config, checks)
                if blockers:
                    print("PRODUKTIVER LAUF BLOCKIERT", file=sys.stderr)
                    print("==========================", file=sys.stderr)
                    for blocker in blockers:
                        print(f"- {blocker}", file=sys.stderr)
                    print("\nNaechster Schritt:", file=sys.stderr)
                    print("  ./scripts/mail-agent.sh guide", file=sys.stderr)
                    print("\nNur bei bewusster manueller Entscheidung: run ... --force", file=sys.stderr)
                    return 4
            try:
                with ProcessLock(config.runtime.lock_file):
                    if args.command == "setup":
                        results = agent.setup()
                        print(json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False))
                        return 0 if all(result.ok for result in results) else 1
                    if args.command == "run":
                        if args.drain:
                            values = {
                                "batch-size": args.batch_size,
                                "max-messages": args.max_messages,
                                "max-runtime": args.max_runtime,
                                "shutdown-reserve": args.shutdown_reserve,
                                "max-batches": args.max_batches,
                            }
                            invalid = [name for name, value in values.items() if value <= 0]
                            if invalid:
                                print(
                                    "Drain-Werte muessen groesser als 0 sein: " + ", ".join(invalid),
                                    file=sys.stderr,
                                )
                                return 2
                            if args.max_messages < args.batch_size:
                                print("--max-messages darf nicht kleiner als --batch-size sein.", file=sys.stderr)
                                return 2
                            if args.shutdown_reserve >= args.max_runtime:
                                print("--shutdown-reserve muss kleiner als --max-runtime sein.", file=sys.stderr)
                                return 2
                            summary = agent.drain(
                                batch_size=args.batch_size,
                                max_messages=args.max_messages,
                                max_runtime_seconds=args.max_runtime,
                                shutdown_reserve_seconds=args.shutdown_reserve,
                                max_batches=args.max_batches,
                                include_digest=not args.no_digest,
                            )
                            recorded_limit = args.batch_size
                        else:
                            limit = max(1, args.limit)
                            summary = agent.run(limit=limit, include_digest=not args.no_digest)
                            recorded_limit = limit
                        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
                        if dry_run:
                            record_dry_run(
                                config,
                                processed=summary.processed,
                                errors=summary.errors,
                                limit=recorded_limit,
                            )
                        return 0 if not summary.errors else 1
                    result = agent.digest.send_if_due(force=True)
                    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
                    return 0 if result.ok else 1
            except ProcessLockError as exc:
                print(str(exc), file=sys.stderr)
                print("Status und genaue Schritte: ./scripts/mail-agent.sh guide", file=sys.stderr)
                return 3
        if args.command == "doctor":
            checks = agent.doctor()
            print(json.dumps(checks, indent=2, ensure_ascii=False))
            required = ("himalaya", "folders", "mail_sources", "forwarding", "ollama", "database", "config")
            return 0 if all(bool(checks[name].get("ok")) for name in required) else 1
        if args.command == "status":
            print(json.dumps(agent.status(), indent=2, ensure_ascii=False))
            return 0
        if args.command == "review":
            try:
                if args.review_command == "status":
                    result = agent.review.status(days=args.days)
                elif args.review_command == "list":
                    result = agent.review.list(args.reason, limit=args.limit)
                elif args.review_command == "suggest":
                    if not agent.tool_settings.mail.move.enabled:
                        raise PermissionError("Direktes Mail-Lesewerkzeug ist deaktiviert")
                    result = agent.review.suggest(
                        args.folder,
                        args.message_id,
                        args.expected_subject,
                    )
                else:
                    raise ValueError(f"Unbekannter Review-Befehl: {args.review_command}")
            except (ValueError, KeyError, PermissionError, RuntimeError) as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
                return 2
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("ok") else 1
        if args.command == "folders":
            plan = agent.folder_plan()
            if args.folders_command == "plan":
                print(json.dumps(plan, indent=2, ensure_ascii=False))
                return 0 if plan.get("ok") else 1
            if args.folders_command == "activate-relevant":
                if not args.yes:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "error": "Explizite Freigabe mit --yes fehlt",
                                "requested_relevant_folder": args.relevant,
                            },
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                    return 2
                try:
                    with ProcessLock(config.runtime.lock_file):
                        payload = _activate_relevant_folder(agent, args.relevant)
                        if payload.get("ok"):
                            invalidate_dry_run(
                                agent.config,
                                f"Relevant-Ordner aktiviert: {payload['target']}",
                            )
                except (ProcessLockError, ValueError, RuntimeError, OSError) as exc:
                    print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
                    return 2
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0 if payload.get("ok") else 1
            if not args.yes:
                print(
                    json.dumps(
                        {"ok": False, "error": "Explizite Freigabe mit --yes fehlt", "plan": plan},
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return 2
            if plan.get("activation_required"):
                print(json.dumps(plan, indent=2, ensure_ascii=False))
                return 2
            results = agent.setup()
            payload = {
                "ok": all(item.ok for item in results),
                "results": [asdict(item) for item in results],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0 if payload["ok"] else 1
    finally:
        agent.close()
    return 0
