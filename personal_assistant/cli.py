from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
import os
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, DEFAULT_SECRETS, load_config
from .env import load_env
from .models import Resource
from .release import release_report
from .mail_source_setup import configure_mail_sources
from .job_control import JobController
from .service import PersonalAssistant
from .setup import configure_nextcloud, initialize_local_files
from .tool_setup import configure_calendar_tools, configure_deck_orders_tools, configure_mail_move_tools, configure_mail_tools, configure_portfolio_tools, configure_tasks_tools, configure_workspace_tools
from .work_scheduler import AdaptiveWorkScheduler, VALID_TOPICS


def _logging(path: Path, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not root.handlers:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=3_000_000, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Lokale Personal-Assistant-Plattform")
    root.add_argument("--config", default=str(DEFAULT_CONFIG))
    root.add_argument("--verbose", action="store_true")
    sub = root.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Zentrale Einrichtung")
    setup_sub = setup.add_subparsers(dest="setup_command", required=True)
    setup_sub.add_parser("init", help="Lokale Konfigurationsdateien anlegen")
    nc_setup = setup_sub.add_parser("nextcloud", help="Nextcloud-Zugang zentral einrichten")
    nc_setup.add_argument("--url")
    nc_setup.add_argument("--username")
    nc_setup.add_argument("--token")
    nc_setup.add_argument("--non-interactive", action="store_true")
    nc_setup.add_argument("--use-existing", action="store_true", help="Vorhandene zentrale/legacy NEXTCLOUD_* Variablen aktivieren")
    tools_setup = setup_sub.add_parser("tools", help="Mail-, Rechnungs- und Kalenderwerkzeuge zentral einrichten")
    tools_setup.add_argument("--owner-email", default="")
    tools_setup.add_argument("--calendar-resource", default="")
    tools_setup.add_argument("--invoice-folder", default="Assistent/Rechnungen")
    tools_setup.add_argument("--disable-invoices", action="store_true")
    tools_setup.add_argument("--disable-calendar-mail", action="store_true")
    tools_setup.add_argument("--approve-permissions", action="store_true")
    move_setup = setup_sub.add_parser(
        "mail-move",
        help="Direktes Lesen, Antworten, Verfassen und Verschieben einzelner Mails freigeben",
    )
    move_setup.add_argument("--max-batch", type=int, default=1)
    move_setup.add_argument("--disable", action="store_true")
    move_setup.add_argument("--approve-permissions", action="store_true")
    sources_setup = setup_sub.add_parser("mail-sources", help="Primaer- und Spam-/Quarantaeneordner konfigurieren")
    sources_setup.add_argument("--primary", default="INBOX")
    sources_setup.add_argument("--quarantine-folder", action="append", default=[])
    sources_setup.add_argument("--max-per-run", type=int, default=10)
    sources_setup.add_argument("--full-triage", action="store_true", help="Auch gewoehnliche Routine-/Spam-Mails aus dem Providerordner normal routen")
    workspace_setup = setup_sub.add_parser("workspace", help="Nextcloud-Arbeitsbereich und kontrollierte Schreibwerkzeuge einrichten")
    workspace_setup.add_argument("--resource", default="nextcloud-files-main")
    workspace_setup.add_argument("--root", default="Assistent")
    workspace_setup.add_argument("--outbox", default="personal_assistant/data/workspace_outbox")
    workspace_setup.add_argument("--disable-mkdir", action="store_true")
    workspace_setup.add_argument("--disable-upload", action="store_true")
    workspace_setup.add_argument("--disable-write-text", action="store_true")
    workspace_setup.add_argument("--disable-move", action="store_true")
    workspace_setup.add_argument("--approve-permissions", action="store_true")
    calendar_setup = setup_sub.add_parser("calendar", help="Direktes Nextcloud-Kalenderwerkzeug fuer Lesen, Anlegen und optionales Aktualisieren einrichten")
    calendar_setup.add_argument("--resource", default="")
    calendar_setup.add_argument("--timezone", default="Europe/Berlin")
    calendar_setup.add_argument("--default-duration-minutes", type=int, default=60)
    calendar_setup.add_argument("--max-duration-hours", type=int, default=168)
    calendar_setup.add_argument("--max-future-days", type=int, default=730)
    calendar_setup.add_argument("--approve-permissions", action="store_true")
    tasks_setup = setup_sub.add_parser("tasks", help="Direktes Nextcloud-Aufgabenwerkzeug fuer Lesen, Anlegen und optionales Aktualisieren einrichten")
    tasks_setup.add_argument("--resource", default="")
    tasks_setup.add_argument("--timezone", default="Europe/Berlin")
    tasks_setup.add_argument("--max-future-days", type=int, default=3650)
    tasks_setup.add_argument("--disable-create", action="store_true")
    tasks_setup.add_argument("--disable-list", action="store_true")
    tasks_setup.add_argument("--approve-permissions", action="store_true")
    deck_setup = setup_sub.add_parser("deck-orders", help="Nextcloud Deck-Bestellmonitor einrichten")
    deck_setup.add_argument("--board-id", type=int, default=0)
    deck_setup.add_argument("--board-title", default="Bestellungen")
    deck_setup.add_argument("--create-board", action="store_true")
    deck_setup.add_argument("--min-confidence", type=float, default=0.82)
    deck_setup.add_argument("--disable-auto-mail", action="store_true")
    deck_setup.add_argument("--approve-permissions", action="store_true")
    portfolio_setup = setup_sub.add_parser(
        "portfolio", help="Lokalen Portfolio-Monitor und Marktdatenadapter einrichten"
    )
    portfolio_setup.add_argument("--provider", default="twelve-data", choices=("twelve-data",))
    portfolio_setup.add_argument("--interval-minutes", type=int, default=30, choices=(15, 30))
    portfolio_setup.add_argument("--stale-warning-minutes", type=int, default=45)
    portfolio_setup.add_argument("--stale-critical-minutes", type=int, default=90)
    portfolio_setup.add_argument("--disable", action="store_true")
    portfolio_setup.add_argument("--approve-permissions", action="store_true")

    sub.add_parser("doctor", help="Core, Index, Policies und Nextcloud pruefen")
    sub.add_parser("status", help="Kompakten Status anzeigen")
    sub.add_parser("capabilities", help="Maschinenlesbare Rechte und Grenzen anzeigen")

    version = sub.add_parser("version", help="Installierte Version, Konsistenz und Updatehistorie anzeigen")
    version.add_argument("--verify", action="store_true", help="Manifest, AGENTS.md, README und CHANGELOG gegeneinander pruefen")
    version.add_argument("--history", action="store_true", help="Releasehistorie mit ausgeben")
    version.add_argument("--since", default="", help="Nur Aenderungen nach dieser Version anzeigen, z. B. 3.4.0-r18")
    version.add_argument("--limit", type=int, default=10, help="Maximale Anzahl Historieneintraege")

    monitor = sub.add_parser("monitor", help="Leistung, Zuverlaessigkeit und Datenfrische bewerten")
    monitor_sub = monitor.add_subparsers(dest="monitor_command", required=True)
    monitor_status = monitor_sub.add_parser("status", help="Aktuellen evidenzbasierten Gesundheitswert anzeigen")
    monitor_status.add_argument("--days", type=int, default=7)
    monitor_status.add_argument("--live", action="store_true", help="Nextcloud und lokale Dienste live pruefen")
    monitor_record = monitor_sub.add_parser("record", help="Monitoring-Snapshot lokal speichern")
    monitor_record.add_argument("--days", type=int, default=7)
    monitor_record.add_argument("--live", action="store_true")
    monitor_history = monitor_sub.add_parser("history", help="Gespeicherte Entwicklung anzeigen")
    monitor_history.add_argument("--days", type=int, default=30)
    monitor_history.add_argument("--limit", type=int, default=100)

    scheduler = sub.add_parser("scheduler", help="Adaptive Hintergrund-Queue und Themenfokus verwalten")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    scheduler_status = scheduler_sub.add_parser("status", help="Aktive, wartende und letzte Aufgaben anzeigen")
    scheduler_status.add_argument("--limit", type=int, default=20)
    scheduler_sub.add_parser("doctor", help="Scheduler-Datenbank, Leases und Fristen pruefen")
    scheduler_sub.add_parser("activity", help="Aktuelle zeitlich begrenzte Themenprioritaeten anzeigen")
    scheduler_focus = scheduler_sub.add_parser(
        "focus", help="Aktuelles Nutzerthema lokal und zeitlich begrenzt priorisieren"
    )
    scheduler_focus.add_argument("--topic", required=True, choices=VALID_TOPICS)
    scheduler_focus.add_argument("--minutes", type=int, default=30)
    scheduler_focus.add_argument("--source", default="agent-chat")

    jobs = sub.add_parser("jobs", help="Hintergrundjobs ueberwachen und kontrolliert ein-/ausschalten")
    jobs_sub = jobs.add_subparsers(dest="jobs_command", required=True)
    jobs_status = jobs_sub.add_parser("status", help="Soll- und Ist-Zustand aller freigegebenen Jobs anzeigen")
    jobs_status.add_argument("--target", choices=("standard", "all", "supervisor", "mail", "sync", "portfolio", "monitor"), default="all")
    jobs_status.add_argument("--deep", action="store_true", help="Zusaetzliche Tool-Health-Checks ausfuehren")
    jobs_check = jobs_sub.add_parser("check", help="Jobs pruefen und Zustandswechsel als lokale Alerts speichern")
    jobs_check.add_argument("--target", choices=("standard", "all", "supervisor", "mail", "sync", "portfolio", "monitor"), default="all")
    jobs_check.add_argument("--deep", action="store_true", help="Zusaetzliche Tool-Health-Checks ausfuehren")
    jobs_sub.add_parser("alerts", help="Aktive Job-Alerts und letzten beobachteten Zustand anzeigen")
    for command_name, help_text in (
        ("on", "Standardjobs einschalten und sicher hochfahren"),
        ("restart", "Standardjobs reparieren und neu starten"),
        ("off", "Produktive Jobs bewusst ausschalten"),
    ):
        job_action = jobs_sub.add_parser(command_name, help=help_text)
        job_action.add_argument("target", nargs="?", choices=("standard", "all", "supervisor", "mail", "sync", "portfolio", "monitor"), default="standard")
        if command_name in {"on", "restart"}:
            job_action.add_argument("--no-run-now", action="store_true", help="Timer aktivieren, aber keinen sofortigen Joblauf starten")

    ollama = sub.add_parser("ollama", help="Ollama-Prioritaetskoordinator pruefen und kontrolliert starten")
    ollama_sub = ollama.add_subparsers(dest="ollama_command", required=True)
    ollama_sub.add_parser("status", help="Proxy, Queue und Upstream-Zustand anzeigen")
    ollama_sub.add_parser("check", help="Ollama-Upstream ueber den Proxy live pruefen")
    ollama_sub.add_parser("queue", help="Aktive und wartende Modellauftraege kompakt anzeigen")
    ollama_sub.add_parser("start", help="Prioritaetskoordinator nach ausdruecklichem Auftrag starten")
    ollama_sub.add_parser("restart", help="Prioritaetskoordinator nach ausdruecklichem Auftrag neu starten")

    performance = sub.add_parser("performance", help="Privacy-sichere Performance-Telemetrie auswerten")
    performance_sub = performance.add_subparsers(dest="performance_command", required=True)
    performance_mail = performance_sub.add_parser("mail", help="Laufzeiten des automatischen Mail-Interfaces anzeigen")
    performance_mail.add_argument("--limit", type=int, default=20)
    performance_mail.add_argument("--raw", action="store_true")

    portfolio = sub.add_parser(
        "portfolio", help="Depot, Watchlist, Marktdaten und regelbasierte Analysen"
    )
    portfolio_sub = portfolio.add_subparsers(dest="portfolio_command", required=True)
    portfolio_sub.add_parser("status", help="Konfiguration, Datenfrische und Abdeckung anzeigen")
    portfolio_sub.add_parser("doctor", help="Portfolio-Datenbank und Pflichtkurse pruefen")
    portfolio_import = portfolio_sub.add_parser(
        "import-pp", help="Portfolio-Performance-XML aus dem kontrollierten Importordner einlesen"
    )
    portfolio_import.add_argument("--file", required=True)
    portfolio_import.add_argument("--dry-run", action="store_true")
    portfolio_import.add_argument("--yes", action="store_true")
    portfolio_csv = portfolio_sub.add_parser(
        "import-csv", help="Strikten DKB-CSV-Depotsnapshot lokal oder aus Nextcloud einlesen"
    )
    portfolio_csv_source = portfolio_csv.add_mutually_exclusive_group(required=True)
    portfolio_csv_source.add_argument("--file")
    portfolio_csv_source.add_argument("--nextcloud-path")
    portfolio_csv.add_argument("--dry-run", action="store_true")
    portfolio_csv.add_argument("--yes", action="store_true")
    portfolio_sub.add_parser("holdings", help="Letzten importierten Depotbestand anzeigen")
    watchlist = portfolio_sub.add_parser("watchlist", help="Watchlist verwalten")
    watchlist_sub = watchlist.add_subparsers(dest="watchlist_command", required=True)
    watchlist_sub.add_parser("list")
    watchlist_add = watchlist_sub.add_parser(
        "add", help="Exakte ISIN/Symbol/MIC-Zuordnung bestaetigen und aufnehmen"
    )
    watchlist_add.add_argument("--isin", required=True)
    watchlist_add.add_argument("--name", required=True)
    watchlist_add.add_argument("--symbol", required=True)
    watchlist_add.add_argument("--mic", required=True)
    watchlist_add.add_argument("--currency", required=True)
    watchlist_add.add_argument("--yes", action="store_true")
    watchlist_disable = watchlist_sub.add_parser("disable")
    watchlist_disable.add_argument("--isin", required=True)
    watchlist_disable.add_argument("--yes", action="store_true")
    quotes = portfolio_sub.add_parser("quotes", help="Kursversorgung pruefen oder aktualisieren")
    quotes_sub = quotes.add_subparsers(dest="quotes_command", required=True)
    quotes_sub.add_parser("status")
    quotes_refresh = quotes_sub.add_parser("refresh")
    quotes_refresh.add_argument("--force", action="store_true")
    portfolio_analyze = portfolio_sub.add_parser(
        "analyze", help="Zeitreihe und deterministische Trendindikatoren berechnen"
    )
    portfolio_analyze.add_argument("--isin", required=True)
    portfolio_analyze.add_argument("--limit", type=int, default=500)
    portfolio_alerts = portfolio_sub.add_parser("alerts", help="Kursmarken verwalten")
    portfolio_alerts_sub = portfolio_alerts.add_subparsers(
        dest="portfolio_alerts_command", required=True
    )
    portfolio_alerts_sub.add_parser("list")
    portfolio_alert_add = portfolio_alerts_sub.add_parser("add")
    portfolio_alert_add.add_argument("--isin", required=True)
    portfolio_alert_add.add_argument("--direction", required=True, choices=("above", "below"))
    portfolio_alert_add.add_argument("--threshold", required=True)
    portfolio_alert_add.add_argument("--currency", required=True)
    portfolio_alert_add.add_argument("--hysteresis-bps", type=int, default=25)
    portfolio_alert_add.add_argument("--cooldown-minutes", type=int, default=60)
    portfolio_alert_add.add_argument("--yes", action="store_true")
    portfolio_alert_disable = portfolio_alerts_sub.add_parser("disable")
    portfolio_alert_disable.add_argument("--id", required=True)
    portfolio_alert_disable.add_argument("--yes", action="store_true")
    portfolio_sub.add_parser(
        "performance", help="Signalqualitaet getrennt von der technischen Gesundheit anzeigen"
    )

    invoices = sub.add_parser("invoices", help="Rechnungs-OCR, Metadaten und Jahresregister")
    invoices_sub = invoices.add_subparsers(dest="invoices_command", required=True)
    invoices_sub.add_parser("status", help="OCR-Werkzeuge, Register und Zaehler anzeigen")
    invoices_list = invoices_sub.add_parser("list", help="Rechnungsmetadaten anzeigen")
    invoices_list.add_argument("--year", type=int, default=0)
    invoices_list.add_argument("--status", default="", choices=("", "confirmed", "confirmed-manual", "review", "error"))
    invoices_list.add_argument("--limit", type=int, default=100)
    invoices_review = invoices_sub.add_parser("review", help="Unsichere Rechnungsmetadaten anzeigen")
    invoices_review.add_argument("--limit", type=int, default=100)
    invoices_export = invoices_sub.add_parser("export", help="Jahres-CSV erzeugen und optional nach Nextcloud exportieren")
    invoices_export.add_argument("--year", type=int, required=True)
    invoices_export.add_argument("--nextcloud", action="store_true")
    invoices_export.add_argument("--filename", default="")
    invoices_export.add_argument("--yes", action="store_true")
    invoices_backfill = invoices_sub.add_parser("backfill", help="Bereits archivierte Rechnungen eines Jahres neu auswerten")
    invoices_backfill.add_argument("--year", type=int, required=True)
    invoices_backfill.add_argument("--limit", type=int, default=500)
    invoices_backfill.add_argument("--dry-run", action="store_true")
    invoices_backfill.add_argument("--yes", action="store_true")
    invoices_correct = invoices_sub.add_parser("correct", help="Rechnungsmetadaten nach Nutzerauftrag korrigieren")
    invoices_correct.add_argument("--hash", required=True, dest="attachment_hash")
    invoices_correct.add_argument("--date", required=True, dest="invoice_date")
    invoices_correct.add_argument("--number", required=True, dest="invoice_number")
    invoices_correct.add_argument("--supplier", required=True)
    invoices_correct.add_argument("--category", required=True)
    invoices_correct.add_argument("--gross", required=True)
    invoices_correct.add_argument("--net", default="")
    invoices_correct.add_argument("--tax", default="")
    invoices_correct.add_argument("--currency", default="EUR")
    invoices_correct.add_argument("--due-date", default="")
    invoices_correct.add_argument("--yes", action="store_true")

    security = sub.add_parser("security", help="Sicherheitswerkzeuge des Personal Assistants")
    security_sub = security.add_subparsers(dest="security_command", required=True)
    antivirus = security_sub.add_parser("antivirus", help="Host-Virenscanner")
    antivirus_sub = antivirus.add_subparsers(dest="antivirus_command", required=True)
    av_doctor = antivirus_sub.add_parser("doctor", help="ClamAV-Dienst, Signaturen und Testscan pruefen")
    av_doctor.add_argument("--no-live-scan", action="store_true")
    antivirus_sub.add_parser("self-test", help="Harmlosen EICAR-Erkennungstest ausfuehren")
    av_scan = antivirus_sub.add_parser("scan", help="Datei aus der kontrollierten Outbox pruefen")
    av_scan.add_argument("--file", required=True)
    av_scan.add_argument("--no-cache", action="store_true")

    tools = sub.add_parser("tools", help="Fuer den Agenten freigegebene Werkzeuge anzeigen")
    tools_sub = tools.add_subparsers(dest="tools_command", required=True)
    tools_sub.add_parser("list")

    mail = sub.add_parser("mail", help="Mail-Werkzeug des Personal Assistants")
    mail_sub = mail.add_subparsers(dest="mail_command", required=True)
    mail_sub.add_parser("status")
    mail_sub.add_parser("doctor")
    mail_sub.add_parser("guide")
    mail_dry = mail_sub.add_parser("dry-run")
    mail_dry.add_argument("--limit", type=int, default=20)
    mail_run = mail_sub.add_parser("run")
    mail_run.add_argument("--limit", type=int, default=20)
    mail_run.add_argument("--drain", action="store_true")
    mail_run.add_argument("--batch-size", type=int, default=20)
    mail_run.add_argument("--max-messages", type=int, default=500)
    mail_run.add_argument("--max-runtime", type=int, default=2700)
    mail_run.add_argument("--max-batches", type=int, default=100)
    mail_orders = mail_sub.add_parser("orders-import", help="Bestehende Mail-Snapshots auf Bestellungen pruefen")
    mail_orders.add_argument("--limit", type=int, default=500)
    mail_orders.add_argument("--dry-run", action="store_true")
    mail_sub.add_parser("move-status", help="Berechtigungen und gesperrte Zielordner des Mail-Verschiebewerkzeugs pruefen")
    mail_list = mail_sub.add_parser("list", help="Mail-Metadaten eines vorhandenen Ordners auflisten")
    mail_list.add_argument("--folder", required=True)
    mail_list.add_argument("--limit", type=int, default=50)
    mail_search = mail_sub.add_parser(
        "search",
        help="Mails ordneruebergreifend serverseitig in Absender, Betreff und Text durchsuchen",
    )
    mail_search.add_argument("--query", required=True)
    mail_search.add_argument("--limit", type=int, default=50)
    mail_read = mail_sub.add_parser("read", help="Eine eindeutig identifizierte Mail read-only lesen")
    mail_read.add_argument("--folder", required=True)
    mail_read.add_argument("--message-id", required=True)
    mail_read.add_argument("--expected-subject", default="")
    mail_draft_reply = mail_sub.add_parser("reply-draft", help="Antwortentwurf fuer eine ausgewaehlte Mail anlegen")
    mail_draft_reply.add_argument("--folder", required=True)
    mail_draft_reply.add_argument("--message-id", required=True)
    mail_draft_reply.add_argument("--expected-subject", default="")
    mail_draft_reply.add_argument("--body", required=True)
    mail_send_reply = mail_sub.add_parser("reply-send", help="Einen zuvor angezeigten Antwortentwurf versenden")
    mail_send_reply.add_argument("--draft-id", required=True)
    mail_send_reply.add_argument("--yes", action="store_true")
    mail_compose_draft = mail_sub.add_parser("compose-draft", help="Entwurf fuer eine neue Mail anlegen")
    mail_compose_draft.add_argument("--to", required=True)
    mail_compose_draft.add_argument("--subject", required=True)
    mail_compose_draft.add_argument("--body", required=True)
    mail_compose_send = mail_sub.add_parser("compose-send", help="Einen zuvor angezeigten neuen Mailentwurf versenden")
    mail_compose_send.add_argument("--draft-id", required=True)
    mail_compose_send.add_argument("--yes", action="store_true")
    mail_move = mail_sub.add_parser("move", help="Eine eindeutig identifizierte Mail kontrolliert verschieben")
    mail_move.add_argument("--source", required=True)
    mail_move.add_argument("--destination", required=True)
    mail_move.add_argument("--message-id", required=True)
    mail_move.add_argument("--expected-subject", default="")
    mail_move.add_argument("--dry-run", action="store_true")
    mail_spam = mail_sub.add_parser("spam-review")
    mail_spam.add_argument("--limit", type=int, default=20)
    mail_spam.add_argument("--dry-run", action="store_true")
    mail_learning = mail_sub.add_parser("learning", help="Korrekturlernen, Muster und Lernordner verwalten")
    learning_sub = mail_learning.add_subparsers(dest="learning_command", required=True)
    learning_sub.add_parser("status", help="Lernstatus, gemischte Absender und Konflikte anzeigen")
    learning_feedback = learning_sub.add_parser("feedback", help="Letzte Korrekturen ohne Mailtext anzeigen")
    learning_feedback.add_argument("--limit", type=int, default=50)
    learning_not_spam = learning_sub.add_parser("not-spam", help="Nicht-Spam-Gegenbelege und ihren Ursprung anzeigen")
    learning_not_spam.add_argument("--limit", type=int, default=100)
    learning_mixed = learning_sub.add_parser("mixed-senders", help="Absender mit verschiedenen Mailtypen anzeigen")
    learning_mixed.add_argument("--limit", type=int, default=100)
    learning_conflicts = learning_sub.add_parser("conflicts", help="Widerspruechliche Musterkorrekturen anzeigen")
    learning_conflicts.add_argument("--limit", type=int, default=100)
    learning_conflicts.add_argument("--id", default="", help="Optional genau eine conflict_id anzeigen")
    learning_evaluate = learning_sub.add_parser("evaluate", help="Lernqualitaet und Basisvergleich aggregiert auswerten")
    learning_evaluate.add_argument("--limit", type=int, default=5000)
    learning_export = learning_sub.add_parser("dataset-export", help="Pseudonymisierten Lern-Datensatz lokal exportieren")
    learning_export.add_argument("--output", default="mail_agent/data/learning_dataset.json")
    learning_export.add_argument("--limit", type=int, default=5000)
    learning_sub.add_parser("folder-list", help="Dynamische Korrektur-Unterordner anzeigen")
    learning_create = learning_sub.add_parser("folder-create", help="Korrektur-Unterordner nach Nutzerauftrag anlegen")
    learning_create.add_argument("--parent", required=True, choices=("routine", "important", "spam", "not-spam"))
    learning_create.add_argument("--name", required=True)
    learning_create.add_argument("--label", default="")
    learning_create.add_argument("--yes", action="store_true")
    learning_disable = learning_sub.add_parser("folder-disable", help="Lernzuordnung deaktivieren; IMAP-Ordner behalten")
    learning_disable.add_argument("--folder", required=True)
    learning_disable.add_argument("--yes", action="store_true")

    nextcloud = sub.add_parser("nextcloud", help="Nextcloud-Verbindung und Discovery")
    nc_sub = nextcloud.add_subparsers(dest="nextcloud_command", required=True)
    nc_sub.add_parser("doctor")
    discover = nc_sub.add_parser("discover")
    discover.add_argument("--no-persist", action="store_true")
    nc_sub.add_parser("sync")
    nc_list = nc_sub.add_parser("list", help="Erlaubte Nextcloud-Dateien read-only auflisten")
    nc_list.add_argument("--path", default="Assistent")
    nc_list.add_argument("--max-depth", type=int, default=3)
    nc_mkdir = nc_sub.add_parser("mkdir", help="Ordner innerhalb des erlaubten Arbeitsbereichs anlegen")
    nc_mkdir.add_argument("--path", required=True)
    nc_upload = nc_sub.add_parser("upload", help="Datei aus der kontrollierten Outbox create-only hochladen")
    nc_upload.add_argument("--local", required=True)
    nc_upload.add_argument("--path", required=True)
    nc_upload.add_argument("--content-type", default="")
    nc_text = nc_sub.add_parser("write-text", help="Neue UTF-8-Textdatei create-only aus stdin anlegen")
    nc_text.add_argument("--path", required=True)
    nc_text.add_argument("--content-type", default="text/plain; charset=utf-8")
    nc_text.add_argument("--text", default=None, help="Optionaler Text; ohne diese Option wird stdin gelesen")
    nc_move = nc_sub.add_parser("move", help="Datei oder Ordner innerhalb des Arbeitsbereichs ohne Ueberschreiben verschieben")
    nc_move.add_argument("--source", required=True)
    nc_move.add_argument("--destination", required=True)

    calendar = sub.add_parser("calendar", help="Direktes Nextcloud-Kalenderwerkzeug")
    calendar_sub = calendar.add_subparsers(dest="calendar_command", required=True)
    calendar_sub.add_parser("discover", help="Erreichbare VEVENT-Kalender read-only auflisten")
    calendar_sub.add_parser("status", help="Konfiguration sowie Lese-, Anlege- und Aktualisierungsrechte pruefen")
    calendar_configure = calendar_sub.add_parser("configure", help="Entdeckten Kalender nach ausdruecklicher Auswahl konfigurieren")
    calendar_configure.add_argument("--resource", required=True, help="resource_id aus calendar discover")
    calendar_configure.add_argument("--timezone", default="Europe/Berlin")
    calendar_configure.add_argument("--default-duration-minutes", type=int, default=60)
    calendar_configure.add_argument("--max-duration-hours", type=int, default=168)
    calendar_configure.add_argument("--max-future-days", type=int, default=730)
    calendar_configure.add_argument("--allow-update", action="store_true", help="Bestehende Termine ETag-geschuetzt aktualisieren")
    calendar_configure.add_argument("--yes", action="store_true", help="Ausdrueckliche Nutzerfreigabe bestaetigen")
    calendar_create = calendar_sub.add_parser("create", help="Neuen Termin anlegen, ohne bestehende Termine zu ersetzen")
    calendar_create.add_argument("--title", required=True)
    calendar_create.add_argument("--start", required=True, help="ISO-8601; ohne Offset gilt die konfigurierte Zeitzone")
    calendar_create.add_argument("--end", default="", help="ISO-8601; alternativ --duration-minutes")
    calendar_create.add_argument("--duration-minutes", type=int, default=None)
    calendar_create.add_argument("--location", default="")
    calendar_create.add_argument("--description", default="")
    calendar_create.add_argument("--uid", default="", help="Optional; normalerweise automatisch und idempotent")
    calendar_list = calendar_sub.add_parser("list", help="Termine im konfigurierten Zeitraum lesen")
    calendar_list.add_argument("--limit", type=int, default=100)
    calendar_search = calendar_sub.add_parser("search", help="Termine nach Titel, Ort, Beschreibung oder UID suchen")
    calendar_search.add_argument("--query", required=True)
    calendar_search.add_argument("--limit", type=int, default=50)
    calendar_update = calendar_sub.add_parser("update", help="Einen eindeutig per UID ausgewaehlten Termin aktualisieren")
    calendar_update.add_argument("--uid", required=True, help="UID aus calendar list/search")
    calendar_update.add_argument("--title", default=None)
    calendar_update.add_argument("--start", default=None, help="ISO-8601; ohne Offset gilt die konfigurierte Zeitzone")
    calendar_update.add_argument("--end", default=None, help="ISO-8601; alternativ --duration-minutes")
    calendar_update.add_argument("--duration-minutes", type=int, default=None)
    calendar_update.add_argument("--location", default=None)
    calendar_update.add_argument("--clear-location", action="store_true")
    calendar_update.add_argument("--description", default=None)
    calendar_update.add_argument("--clear-description", action="store_true")
    calendar_update.add_argument("--expected-title", default="")
    calendar_update.add_argument("--expected-start", default="")
    calendar_update.add_argument("--allow-recurring-series", action="store_true")
    calendar_update.add_argument("--yes", action="store_true")

    tasks = sub.add_parser("tasks", help="Direktes Nextcloud-Aufgabenwerkzeug")
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    tasks_sub.add_parser("discover", help="Erreichbare VTODO-Aufgabenlisten read-only auflisten")
    tasks_configure = tasks_sub.add_parser("configure", help="Entdeckte Aufgabenliste nach ausdruecklicher Auswahl konfigurieren")
    tasks_configure.add_argument("--resource", required=True, help="resource_id aus tasks discover")
    tasks_configure.add_argument("--timezone", default="Europe/Berlin")
    tasks_configure.add_argument("--max-future-days", type=int, default=3650)
    tasks_configure.add_argument("--read-only", action="store_true", help="Nur Lesen erlauben, keine neuen Aufgaben anlegen")
    tasks_configure.add_argument("--create-only", action="store_true", help="Nur Anlegen erlauben, keine Aufgabenliste lesen")
    tasks_configure.add_argument("--allow-update", action="store_true", help="Bestehende Aufgaben ETag-geschuetzt aktualisieren")
    tasks_configure.add_argument("--yes", action="store_true", help="Ausdrueckliche Nutzerfreigabe bestaetigen")
    tasks_status = tasks_sub.add_parser("status", help="Konfiguration, Rechte und VTODO-Unterstuetzung pruefen")
    tasks_status.add_argument("--no-live", action="store_true")
    tasks_list = tasks_sub.add_parser("list", help="Aufgaben lesen")
    tasks_list.add_argument("--include-completed", action="store_true")
    tasks_list.add_argument("--limit", type=int, default=100)
    tasks_create = tasks_sub.add_parser("create", help="Neue Aufgabe anlegen, ohne bestehende Aufgaben zu ersetzen")
    tasks_create.add_argument("--title", required=True)
    tasks_create.add_argument("--due", default="", help="YYYY-MM-DD oder ISO-8601")
    tasks_create.add_argument("--start", default="", help="YYYY-MM-DD oder ISO-8601")
    tasks_create.add_argument("--description", default="")
    tasks_create.add_argument("--priority", type=int, default=0)
    tasks_create.add_argument("--category", action="append", default=[])
    tasks_create.add_argument("--uid", default="", help="Optional; normalerweise automatisch und idempotent")
    tasks_update = tasks_sub.add_parser("update", help="Eine eindeutig per UID ausgewaehlte Aufgabe aktualisieren")
    tasks_update.add_argument("--uid", required=True, help="UID aus tasks list")
    tasks_update.add_argument("--title", default=None)
    tasks_update.add_argument("--due", default=None, help="YYYY-MM-DD oder ISO-8601")
    tasks_update.add_argument("--clear-due", action="store_true")
    tasks_update.add_argument("--start", default=None, help="YYYY-MM-DD oder ISO-8601")
    tasks_update.add_argument("--clear-start", action="store_true")
    tasks_update.add_argument("--description", default=None)
    tasks_update.add_argument("--clear-description", action="store_true")
    tasks_update.add_argument("--priority", type=int, default=None)
    tasks_update.add_argument("--category", action="append", default=None)
    tasks_update.add_argument("--clear-categories", action="store_true")
    tasks_update.add_argument("--status", choices=("NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"), default=None)
    tasks_update.add_argument("--percent-complete", type=int, default=None)
    tasks_update.add_argument("--expected-title", default="")
    tasks_update.add_argument("--expected-due", default="")
    tasks_update.add_argument("--allow-recurring", action="store_true")
    tasks_update.add_argument("--yes", action="store_true")

    contacts = sub.add_parser("contacts", help="Direktes Nextcloud-CardDAV-Kontaktwerkzeug")
    contacts_sub = contacts.add_subparsers(dest="contacts_command", required=True)
    contacts_sub.add_parser("discover", help="Erreichbare CardDAV-Adressbuecher read-only auflisten")
    contacts_configure = contacts_sub.add_parser(
        "configure",
        help="Entdecktes Adressbuch nach ausdruecklicher Auswahl konfigurieren",
    )
    contacts_configure.add_argument("--resource", required=True, help="resource_id aus contacts discover")
    contacts_configure.add_argument("--max-results", type=int, default=500)
    contacts_configure.add_argument("--read-only", action="store_true")
    contacts_configure.add_argument("--create-only", action="store_true")
    contacts_configure.add_argument(
        "--allow-update",
        action="store_true",
        help="Bestehende Kontakte nach explizitem Auftrag ETag-geschuetzt aktualisieren",
    )
    contacts_configure.add_argument("--yes", action="store_true")
    contacts_status = contacts_sub.add_parser("status", help="Konfiguration und CardDAV-Rechte pruefen")
    contacts_status.add_argument("--no-live", action="store_true")
    contacts_list = contacts_sub.add_parser("list", help="Kontakte aus dem ausgewaehlten Adressbuch lesen")
    contacts_list.add_argument("--limit", type=int, default=100)
    contacts_search = contacts_sub.add_parser("search", help="Kontakte nach Name, Mail, Telefon oder Firma suchen")
    contacts_search.add_argument("--query", required=True)
    contacts_search.add_argument("--limit", type=int, default=50)
    contacts_create = contacts_sub.add_parser("create", help="Neuen Kontakt create-only anlegen")
    contacts_create.add_argument("--name", required=True)
    contacts_create.add_argument("--email", action="append", required=True)
    contacts_create.add_argument("--phone", action="append", default=[])
    contacts_create.add_argument("--organization", default="")
    contacts_create.add_argument("--note", default="")
    contacts_create.add_argument("--allow-name-collision", action="store_true")
    contacts_create.add_argument("--yes", action="store_true")
    contacts_update = contacts_sub.add_parser(
        "update",
        help="Einen zuvor eindeutig per UID ausgewaehlten Kontakt aktualisieren",
    )
    contacts_update.add_argument("--uid", required=True, help="UID aus contacts search/list")
    contacts_update.add_argument("--name", default=None)
    contacts_update.add_argument("--email", action="append", default=None, help="Ersetzt alle E-Mail-Adressen")
    contacts_update.add_argument("--clear-emails", action="store_true")
    contacts_update.add_argument("--phone", action="append", default=None, help="Ersetzt alle Telefonnummern")
    contacts_update.add_argument("--clear-phones", action="store_true")
    contacts_update.add_argument("--organization", default=None)
    contacts_update.add_argument("--clear-organization", action="store_true")
    contacts_update.add_argument("--note", default=None)
    contacts_update.add_argument("--clear-note", action="store_true")
    contacts_update.add_argument("--expected-name", default="")
    contacts_update.add_argument("--expected-email", default="")
    contacts_update.add_argument("--allow-name-collision", action="store_true")
    contacts_update.add_argument("--yes", action="store_true")
    contacts_mail = contacts_sub.add_parser(
        "from-mail",
        help="Absender einer eindeutig ausgewaehlten Mail als Kontakt vorschlagen oder anlegen",
    )
    contacts_mail.add_argument("--folder", required=True)
    contacts_mail.add_argument("--message-id", required=True, help="Mail-ID aus mail list, nicht Message-ID-Header")
    contacts_mail.add_argument("--expected-subject", default="")
    contacts_mail.add_argument("--name", default="", help="Erkannten Namen gezielt ueberschreiben")
    contacts_mail.add_argument("--organization", default="")
    contacts_mail.add_argument("--phone", action="append", default=[])
    contacts_mail.add_argument("--note", default="")
    contacts_mail.add_argument("--allow-name-collision", action="store_true")
    contacts_mail.add_argument("--dry-run", action="store_true")
    contacts_mail.add_argument("--yes", action="store_true")

    deck = sub.add_parser("deck", help="Nextcloud Deck-Verbindung und Bestellboard")
    deck_sub = deck.add_subparsers(dest="deck_command", required=True)
    deck_sub.add_parser("discover", help="Verfuegbare Deck-Boards lesen")
    deck_status = deck_sub.add_parser("status", help="Bestellboard und Rechte pruefen")
    deck_status.add_argument("--no-live", action="store_true")

    orders = sub.add_parser("orders", help="Laufende Bestellungen aus Mail und Nextcloud Deck")
    orders_sub = orders.add_subparsers(dest="orders_command", required=True)
    orders_status = orders_sub.add_parser("status", help="Bestellmonitor pruefen")
    orders_status.add_argument("--no-live", action="store_true")
    orders_list = orders_sub.add_parser("list", help="Bestellungen aus der lokalen Wahrheitsschicht anzeigen")
    orders_list.add_argument("--status", default="")
    orders_list.add_argument("--limit", type=int, default=100)
    orders_sync = orders_sub.add_parser("sync", help="Ausstehende Deck-Aktualisierungen wiederholen")
    orders_sync.add_argument("--limit", type=int, default=500)
    orders_due = orders_sub.add_parser("due-date-backfill", help="Fehlende Deck-Faelligkeitsdaten kontrolliert ergaenzen")
    orders_due.add_argument("--limit", type=int, default=500)
    orders_due.add_argument("--dry-run", action="store_true")
    orders_due.add_argument("--yes", action="store_true")

    resources = sub.add_parser("resources", help="Dynamische Ressourcen verwalten")
    res_sub = resources.add_subparsers(dest="resources_command", required=True)
    res_list = res_sub.add_parser("list")
    res_list.add_argument("--kind", default="")
    res_add = res_sub.add_parser("add")
    res_add.add_argument("--id", required=True)
    res_add.add_argument("--kind", required=True)
    res_add.add_argument("--connector", required=True)
    res_add.add_argument("--remote-id", default="")
    res_add.add_argument("--permissions", default="read")
    res_add.add_argument("--disabled", action="store_true")
    res_add.add_argument("--approve-permissions", action="store_true", help="Interaktive Freigabe fuer neue/erweiterte Schreibrechte")

    index = sub.add_parser("index", help="Lokalen Wissensindex aktualisieren")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    index_sub.add_parser("mail")
    index_sub.add_parser("all")

    search = sub.add_parser("search", help="Schnelle lokale Hybrid-Grundsuche")
    search.add_argument("query")
    search.add_argument("--limit", type=int)
    search.add_argument("--source-type", default="")
    search.add_argument("--resource", default="")

    actions = sub.add_parser("actions", help="ActionPlan/Outbox verwalten")
    act_sub = actions.add_subparsers(dest="actions_command", required=True)
    act_list = act_sub.add_parser("list")
    act_list.add_argument("--status", default="")
    act_list.add_argument("--limit", type=int, default=100)
    upload = act_sub.add_parser("plan-upload")
    upload.add_argument("local_path")
    upload.add_argument("remote_path")
    upload.add_argument("--resource", default="nextcloud-files-main")
    upload.add_argument("--content-type", default="application/octet-stream")
    event = act_sub.add_parser("plan-event")
    event.add_argument("ics_file")
    event.add_argument("--uid", required=True)
    event.add_argument("--resource", required=True)
    task = act_sub.add_parser("plan-task")
    task.add_argument("ics_file")
    task.add_argument("--uid", required=True)
    task.add_argument("--resource", required=True)
    approve = act_sub.add_parser("approve")
    approve.add_argument("action_id")
    execute = act_sub.add_parser("execute")
    execute.add_argument("action_id")

    settings = sub.add_parser("settings", help="Kontrollierte, sichere Settings")
    set_sub = settings.add_subparsers(dest="settings_command", required=True)
    set_sub.add_parser("list")
    set_value = set_sub.add_parser("set")
    set_value.add_argument("key")
    set_value.add_argument("value")

    return root


def _load_secrets(config_path: Path | None = None) -> None:
    # Central file wins. The legacy file remains a compatibility fallback for the
    # existing mail agent during migration.
    load_env(DEFAULT_SECRETS)
    load_env(Path("~/.config/mail-agent.env").expanduser())



def _run_mail_tool(args: argparse.Namespace) -> int:
    command = [sys.executable, "-m", "mail_agent"]
    if args.mail_command in {"status", "doctor", "guide"}:
        command.append(args.mail_command)
    elif args.mail_command == "dry-run":
        command += ["run", "--dry-run", "--no-digest", "--limit", str(max(1, args.limit))]
    elif args.mail_command == "run":
        command += ["run", "--no-digest", "--limit", str(max(1, args.limit))]
        if args.drain:
            command += [
                "--drain",
                "--batch-size", str(max(1, args.batch_size)),
                "--max-messages", str(max(1, args.max_messages)),
                "--max-runtime", str(max(1, args.max_runtime)),
                "--max-batches", str(max(1, args.max_batches)),
            ]
    elif args.mail_command == "orders-import":
        command += ["orders-import", "--limit", str(max(1, args.limit))]
        if args.dry_run:
            command.append("--dry-run")
    elif args.mail_command == "spam-review":
        command += ["spam-review", "--limit", str(max(1, args.limit))]
        if args.dry_run:
            command.append("--dry-run")
    elif args.mail_command == "learning":
        command += ["training", args.learning_command]
        if args.learning_command in {"feedback", "not-spam", "mixed-senders", "conflicts", "evaluate"}:
            command += ["--limit", str(max(1, args.limit))]
            if args.learning_command == "conflicts" and args.id:
                command += ["--id", args.id]
        elif args.learning_command == "dataset-export":
            command += ["--output", args.output, "--limit", str(max(1, args.limit))]
        elif args.learning_command == "folder-create":
            command += ["--parent", args.parent, "--name", args.name]
            if args.label:
                command += ["--label", args.label]
            if args.yes:
                command.append("--yes")
        elif args.learning_command == "folder-disable":
            command += ["--folder", args.folder]
            if args.yes:
                command.append("--yes")
    else:
        raise ValueError(f"Unbekanntes Mail-Werkzeug: {args.mail_command}")
    environment = os.environ.copy()
    environment["OPENCLAW_OLLAMA_PRIORITY"] = "interactive"
    environment["OPENCLAW_OLLAMA_SOURCE"] = "openclaw-mail-tool"
    return subprocess.run(command, check=False, env=environment).returncode


def _run_invoice_tool(args: argparse.Namespace) -> int:
    command = [sys.executable, "-m", "mail_agent", "invoices", args.invoices_command]
    if args.invoices_command == "list":
        if args.year:
            command += ["--year", str(args.year)]
        if args.status:
            command += ["--status", args.status]
        command += ["--limit", str(max(1, args.limit))]
    elif args.invoices_command == "review":
        command += ["--limit", str(max(1, args.limit))]
    elif args.invoices_command == "export":
        command += ["--year", str(args.year)]
        if args.nextcloud:
            command.append("--nextcloud")
        if args.filename:
            command += ["--filename", args.filename]
        if args.yes:
            command.append("--yes")
    elif args.invoices_command == "backfill":
        command += ["--year", str(args.year), "--limit", str(max(1, args.limit))]
        if args.dry_run:
            command.append("--dry-run")
        if args.yes:
            command.append("--yes")
    elif args.invoices_command == "correct":
        command += [
            "--hash", args.attachment_hash, "--date", args.invoice_date,
            "--number", args.invoice_number, "--supplier", args.supplier,
            "--category", args.category, "--gross", args.gross,
            "--currency", args.currency,
        ]
        if args.net:
            command += ["--net", args.net]
        if args.tax:
            command += ["--tax", args.tax]
        if args.due_date:
            command += ["--due-date", args.due_date]
        if args.yes:
            command.append("--yes")
    environment = os.environ.copy()
    environment["OPENCLAW_OLLAMA_PRIORITY"] = "interactive"
    environment["OPENCLAW_OLLAMA_SOURCE"] = "openclaw-invoice-tool"
    return subprocess.run(command, check=False, env=environment).returncode


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _run_json_command(command: list[str], *, timeout: int = 60) -> tuple[int, dict[str, Any]]:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, {"ok": False, "error": "Zeitlimit ueberschritten", "command": command[0]}
    except OSError as exc:
        return 127, {"ok": False, "error": str(exc), "command": command[0]}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    if not isinstance(payload, dict):
        payload = {"ok": completed.returncode == 0, "result": payload}
    payload.setdefault("returncode", completed.returncode)
    if completed.stderr.strip() and "stderr" not in payload:
        payload["stderr"] = completed.stderr.strip()[-2000:]
    return completed.returncode, payload


def _handle_ollama(args: argparse.Namespace) -> int:
    workspace = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1]).expanduser().resolve()
    script = str(workspace / "scripts/ollama-priority-proxy.sh")
    if args.ollama_command == "status":
        code, payload = _run_json_command([script, "status"], timeout=30)
    elif args.ollama_command == "check":
        code, payload = _run_json_command([script, "check-upstream"], timeout=30)
    elif args.ollama_command == "queue":
        code, status = _run_json_command([script, "status"], timeout=30)
        payload = {
            "ok": bool(status.get("ok")),
            "queue": status.get("queue") or {},
            "stats": status.get("stats") or {},
            "detail": status.get("detail", ""),
            "returncode": code,
        }
    elif args.ollama_command in {"start", "restart"}:
        action = args.ollama_command
        service = "ollama-priority-proxy.service"
        control = subprocess.run(
            ["systemctl", "--user", action, service],
            check=False, capture_output=True, text=True, timeout=60,
        )
        code, status = _run_json_command([script, "status"], timeout=30) if control.returncode == 0 else (control.returncode, {})
        upstream_code, upstream = _run_json_command([script, "check-upstream"], timeout=30) if control.returncode == 0 else (control.returncode, {})
        payload = {
            "ok": control.returncode == 0 and code == 0 and upstream_code == 0 and bool(status.get("ok")) and bool(upstream.get("ok")),
            "operation": action,
            "service": service,
            "control": {
                "returncode": control.returncode,
                "detail": (control.stderr.strip() or control.stdout.strip())[-2000:],
            },
            "status": status,
            "upstream": upstream,
        }
        code = 0 if payload["ok"] else 1
    else:
        payload = {"ok": False, "error": f"Unbekannter Ollama-Befehl: {args.ollama_command}"}
        code = 2
    _print(payload)
    return 0 if code == 0 and payload.get("ok") else 1


def _handle_performance(args: argparse.Namespace) -> int:
    if args.performance_command != "mail":
        _print({"ok": False, "error": f"Unbekannter Performance-Befehl: {args.performance_command}"})
        return 2
    command = [sys.executable, "-m", "mail_agent", "performance", "--limit", str(max(1, min(args.limit, 500)))]
    if args.raw:
        command.append("--raw")
    return subprocess.run(command, check=False).returncode


def _handle_version(args: argparse.Namespace) -> int:
    payload = release_report(
        verify=bool(args.verify),
        include_history=bool(args.history),
        since=str(args.since or ""),
        limit=max(1, min(int(args.limit), 100)),
    )
    _print(payload)
    return 0 if payload.get("ok") else 1


def _handle_jobs(args: argparse.Namespace) -> int:
    controller = JobController()
    try:
        if args.jobs_command == "status":
            result = controller.status(target=args.target, deep=args.deep, record=False)
        elif args.jobs_command == "check":
            result = controller.check(target=args.target, deep=args.deep)
        elif args.jobs_command == "alerts":
            result = controller.alerts()
        elif args.jobs_command == "on":
            result = controller.on(target=args.target, restart=False, run_now=not args.no_run_now)
        elif args.jobs_command == "restart":
            result = controller.on(target=args.target, restart=True, run_now=not args.no_run_now)
        elif args.jobs_command == "off":
            result = controller.off(target=args.target)
        else:
            raise ValueError(f"Unbekannter Jobs-Befehl: {args.jobs_command}")
        _print(result)
        return 0 if result.get("ok") else 1
    except (OSError, ValueError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1


def _handle_scheduler(args: argparse.Namespace) -> int:
    scheduler = AdaptiveWorkScheduler()
    try:
        if args.scheduler_command == "status":
            result = scheduler.snapshot(recent_limit=max(1, min(int(args.limit), 500)))
        elif args.scheduler_command == "doctor":
            result = scheduler.doctor()
        elif args.scheduler_command == "activity":
            snapshot = scheduler.snapshot(recent_limit=1)
            result = {
                "ok": True,
                "generated_at": snapshot["generated_at"],
                "activity": snapshot["activity"],
            }
        elif args.scheduler_command == "focus":
            result = scheduler.record_activity(
                args.topic,
                source=args.source,
                boost_minutes=args.minutes,
            )
        else:
            raise ValueError(f"Unbekannter Scheduler-Befehl: {args.scheduler_command}")
        _print(result)
        return 0 if result.get("ok") else 1
    except (OSError, sqlite3.Error, ValueError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    finally:
        scheduler.close()


def _interactive_topic(args: argparse.Namespace) -> str:
    mapping = {
        "mail": "mail",
        "invoices": "mail",
        "orders": "mail",
        "portfolio": "portfolio",
        "nextcloud": "knowledge",
        "search": "knowledge",
        "index": "knowledge",
        "calendar": "planning",
        "tasks": "planning",
        "contacts": "planning",
    }
    return mapping.get(str(args.command or ""), "")


def _record_interactive_activity(args: argparse.Namespace) -> None:
    if os.environ.get("OPENCLAW_SCHEDULER_SOURCE", "").strip().casefold() in {
        "background-worker",
        "supervisor",
    }:
        return
    topic = _interactive_topic(args)
    if not topic:
        return
    scheduler = AdaptiveWorkScheduler()
    try:
        scheduler.record_activity(topic, source="interactive-cli", boost_minutes=30)
    finally:
        scheduler.close()


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = parser().parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()

    # Job recovery must remain available even when the assistant configuration is
    # damaged. Deep tool checks will still report the configuration error.
    if args.command == "version":
        return _handle_version(args)

    if args.command == "jobs":
        return _handle_jobs(args)

    if args.command == "scheduler":
        return _handle_scheduler(args)

    if args.command == "ollama":
        return _handle_ollama(args)

    if args.command == "performance":
        return _handle_performance(args)

    if args.command == "setup" and args.setup_command == "init":
        _print({"created": initialize_local_files(), "config": str(DEFAULT_CONFIG)})
        return 0

    if not config_path.exists():
        print("Personal-Assistant-Konfiguration fehlt. Zuerst: ./scripts/assistant.sh setup init", file=sys.stderr)
        return 2
    try:
        _load_secrets(config_path)
        config = load_config(config_path)
    except Exception as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    _logging(config.runtime.log_file, args.verbose)

    try:
        _record_interactive_activity(args)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Scheduler-Warnung: Aktivitaet konnte nicht gespeichert werden: {exc}", file=sys.stderr)

    if args.command == "invoices":
        return _run_invoice_tool(args)

    if args.command == "setup" and args.setup_command == "tools":
        try:
            result = configure_mail_tools(
                owner_email=args.owner_email,
                calendar_resource_id=args.calendar_resource,
                invoice_folder=args.invoice_folder,
                enable_invoices=not args.disable_invoices,
                enable_calendar_mail=not args.disable_calendar_mail,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Tool-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "mail-move":
        try:
            result = configure_mail_move_tools(
                enable=not args.disable, max_batch=args.max_batch,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Direktes Mail-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "portfolio":
        try:
            result = configure_portfolio_tools(
                enable=not args.disable,
                provider=args.provider,
                interval_minutes=args.interval_minutes,
                stale_warning_minutes=args.stale_warning_minutes,
                stale_critical_minutes=args.stale_critical_minutes,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Portfolio-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "mail-sources":
        try:
            result = configure_mail_sources(
                primary=args.primary,
                quarantine_folders=tuple(args.quarantine_folder or ["Spam"]),
                max_per_run=args.max_per_run,
                rescue_only=not args.full_triage,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Mailquellen-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "workspace":
        try:
            outbox = Path(args.outbox).expanduser()
            if not outbox.is_absolute():
                outbox = (config.path.parents[1] / outbox).resolve()
            result = configure_workspace_tools(
                resource_id=args.resource,
                root=args.root,
                outbox=outbox,
                allow_mkdir=not args.disable_mkdir,
                allow_upload=not args.disable_upload,
                allow_write_text=not args.disable_write_text,
                allow_move=not args.disable_move,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Workspace-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "calendar":
        try:
            result = configure_calendar_tools(
                resource_id=args.resource,
                timezone=args.timezone,
                default_duration_minutes=args.default_duration_minutes,
                max_duration_hours=args.max_duration_hours,
                max_future_days=args.max_future_days,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Kalender-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "tasks":
        try:
            result = configure_tasks_tools(
                resource_id=args.resource,
                timezone=args.timezone,
                allow_create=not args.disable_create,
                allow_list=not args.disable_list,
                max_future_days=args.max_future_days,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Aufgaben-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    direct_mail_commands = {
        "move-status", "list", "search", "read", "reply-draft", "reply-send",
        "compose-draft", "compose-send", "move",
    }
    if args.command == "mail" and args.mail_command not in direct_mail_commands:
        return _run_mail_tool(args)

    if args.command == "setup" and args.setup_command == "nextcloud":
        try:
            result = configure_nextcloud(
                config,
                url=args.url or "",
                username=args.username or "",
                token=args.token or "",
                interactive=not args.non_interactive and not args.use_existing,
                use_existing=args.use_existing,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Nextcloud-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    assistant = PersonalAssistant(config)
    try:
        if args.command == "mail" and args.mail_command == "move-status":
            result = assistant.mail_move_status()
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "list":
            result = assistant.mail_list_messages(args.folder, limit=args.limit)
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "search":
            result = assistant.mail_search_messages(args.query, limit=args.limit)
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "read":
            result = assistant.mail_read_message(
                args.folder, args.message_id, expected_subject=args.expected_subject,
            )
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "reply-draft":
            result = assistant.mail_draft_reply(
                args.folder, args.message_id, args.body, expected_subject=args.expected_subject,
            )
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "reply-send":
            result = assistant.mail_send_reply(args.draft_id, approved=args.yes)
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "compose-draft":
            result = assistant.mail_draft_message(args.to, args.subject, args.body)
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "compose-send":
            result = assistant.mail_send_message(args.draft_id, approved=args.yes)
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "mail" and args.mail_command == "move":
            result = assistant.mail_move_message(
                source=args.source, destination=args.destination, message_id=args.message_id,
                expected_subject=args.expected_subject, dry_run=args.dry_run,
            )
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "setup" and args.setup_command == "deck-orders":
            if not args.approve_permissions or not sys.stdin.isatty():
                raise PermissionError("Deck-Setup mit Schreibrechten erfordert --approve-permissions in einem interaktiven Terminal")
            confirmation = input("Deck-Board und fehlende Spalten anlegen/verwenden? Tippe exakt APPROVE: ").strip()
            if confirmation != "APPROVE":
                raise PermissionError("Deck-Setup abgebrochen")
            prepared = assistant.deck_prepare_orders_board(
                board_id=args.board_id, board_title=args.board_title, create_board=args.create_board
            )
            configured = configure_deck_orders_tools(
                board_id=int(prepared["board_id"]), board_title=str(prepared["board_title"]),
                auto_process_mail=not args.disable_auto_mail, min_confidence=args.min_confidence,
                approve_permissions=True,
            )
            _print({"ok": True, "prepared": prepared, "configured": configured})
            return 0
        if args.command == "doctor":
            result = assistant.doctor(live=True)
            result["release"] = release_report(verify=True)
            _print(result)
            return 0 if (
                result["database"]["ok"]
                and result["resources"]["ok"]
                and result["scheduler"]["ok"]
                and result["release"].get("ok")
            ) else 1
        if args.command == "status":
            _print({
                "release": release_report(verify=True),
                "doctor": assistant.doctor(live=False),
                "resources": len(assistant.registry.resources),
                "actions": {status: len(assistant.storage.list_actions(status=status, limit=10000)) for status in ("proposed", "approved", "failed")},
            })
            return 0
        if args.command == "capabilities":
            _print(assistant.capabilities())
            return 0
        if args.command == "tools" and args.tools_command == "list":
            _print(assistant.tools())
            return 0
        if args.command == "deck":
            if args.deck_command == "discover":
                _print(assistant.deck_discover())
                return 0
            if args.deck_command == "status":
                result = assistant.deck_orders_status(live=not args.no_live)
                _print(result)
                return 0 if result.get("ok") else 1
        if args.command == "orders":
            if args.orders_command == "status":
                result = assistant.deck_orders_status(live=not args.no_live)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.orders_command == "list":
                _print(assistant.orders_list(status=args.status, limit=args.limit))
                return 0
            if args.orders_command == "sync":
                result = assistant.orders_sync(limit=args.limit)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.orders_command == "due-date-backfill":
                if not args.dry_run and not args.yes:
                    raise PermissionError("Produktiver Due-Date-Backfill benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                result = assistant.orders_due_date_backfill(limit=args.limit, dry_run=bool(args.dry_run))
                _print(result)
                return 0 if result.get("ok") else 1
        if args.command == "calendar":
            if args.calendar_command == "discover":
                result = assistant.calendar_discover()
                _print(result)
                return 0 if result.get("ok") else 1
            if args.calendar_command == "status":
                result = assistant.direct_calendar_status()
                _print(result)
                return 0 if result.get("ok") else 1
            if args.calendar_command == "configure":
                if not args.yes:
                    raise PermissionError("Kalenderauswahl benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                result = assistant.calendar_configure(
                    resource_id=args.resource,
                    timezone_name=args.timezone,
                    default_duration_minutes=args.default_duration_minutes,
                    max_duration_hours=args.max_duration_hours,
                    max_future_days=args.max_future_days,
                    allow_update=bool(args.allow_update),
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.calendar_command == "create":
                result = assistant.calendar_create(
                    title=args.title,
                    start=args.start,
                    end=args.end,
                    duration_minutes=args.duration_minutes,
                    location=args.location,
                    description=args.description,
                    uid=args.uid,
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.calendar_command == "list":
                result = assistant.calendar_list(limit=args.limit)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.calendar_command == "search":
                result = assistant.calendar_search(args.query, limit=args.limit)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.calendar_command == "update":
                if not args.yes:
                    raise PermissionError("Kalender-Aktualisierung benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                result = assistant.calendar_update(
                    uid=args.uid,
                    title=args.title,
                    start=args.start,
                    end=args.end,
                    duration_minutes=args.duration_minutes,
                    location=args.location,
                    clear_location=bool(args.clear_location),
                    description=args.description,
                    clear_description=bool(args.clear_description),
                    expected_title=args.expected_title,
                    expected_start=args.expected_start,
                    allow_recurring_series=bool(args.allow_recurring_series),
                )
                _print(result)
                return 0 if result.get("ok") else 1
        if args.command == "tasks":
            if args.tasks_command == "discover":
                result = assistant.tasks_discover()
                _print(result)
                return 0 if result.get("ok") else 1
            if args.tasks_command == "configure":
                if not args.yes:
                    raise PermissionError("Aufgabenlistenauswahl benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                if args.read_only and args.create_only:
                    raise ValueError("--read-only und --create-only koennen nicht gemeinsam verwendet werden")
                if args.create_only and args.allow_update:
                    raise ValueError("--allow-update benoetigt Leserechte und kann nicht mit --create-only verwendet werden")
                result = assistant.tasks_configure(
                    resource_id=args.resource,
                    timezone_name=args.timezone,
                    allow_create=not args.read_only,
                    allow_list=not args.create_only,
                    max_future_days=args.max_future_days,
                    allow_update=bool(args.allow_update),
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.tasks_command == "status":
                result = assistant.direct_tasks_status(live=not args.no_live)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.tasks_command == "list":
                result = assistant.tasks_list(
                    include_completed=args.include_completed,
                    limit=args.limit,
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.tasks_command == "create":
                result = assistant.task_create(
                    title=args.title,
                    due=args.due,
                    start=args.start,
                    description=args.description,
                    priority=args.priority,
                    categories=tuple(args.category or []),
                    uid=args.uid,
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.tasks_command == "update":
                if not args.yes:
                    raise PermissionError("Aufgaben-Aktualisierung benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                result = assistant.task_update(
                    uid=args.uid,
                    title=args.title,
                    due=args.due,
                    clear_due=bool(args.clear_due),
                    start=args.start,
                    clear_start=bool(args.clear_start),
                    description=args.description,
                    clear_description=bool(args.clear_description),
                    priority=args.priority,
                    categories=tuple(args.category) if args.category is not None else None,
                    clear_categories=bool(args.clear_categories),
                    status=args.status,
                    percent_complete=args.percent_complete,
                    expected_title=args.expected_title,
                    expected_due=args.expected_due,
                    allow_recurring=bool(args.allow_recurring),
                )
                _print(result)
                return 0 if result.get("ok") else 1
        if args.command == "contacts":
            if args.contacts_command == "discover":
                result = assistant.contacts_discover()
                _print(result)
                return 0 if result.get("ok") else 1
            if args.contacts_command == "configure":
                if not args.yes:
                    raise PermissionError("Adressbuchauswahl benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                if args.read_only and args.create_only:
                    raise ValueError("--read-only und --create-only koennen nicht gemeinsam verwendet werden")
                if args.create_only and args.allow_update:
                    raise ValueError("--allow-update benoetigt Leserechte und kann nicht mit --create-only verwendet werden")
                result = assistant.contacts_configure(
                    resource_id=args.resource,
                    allow_create=not args.read_only,
                    allow_list=not args.create_only,
                    allow_update=bool(args.allow_update),
                    max_results=args.max_results,
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.contacts_command == "status":
                result = assistant.direct_contacts_status(live=not args.no_live)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.contacts_command == "list":
                result = assistant.contacts_list(limit=args.limit)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.contacts_command == "search":
                result = assistant.contacts_search(args.query, limit=args.limit)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.contacts_command == "create":
                if not args.yes:
                    raise PermissionError("Kontaktanlage benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                result = assistant.contact_create(
                    name=args.name,
                    emails=tuple(args.email or []),
                    phones=tuple(args.phone or []),
                    organization=args.organization,
                    note=args.note,
                    allow_name_collision=bool(args.allow_name_collision),
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.contacts_command == "update":
                if not args.yes:
                    raise PermissionError("Kontakt-Aktualisierung benoetigt --yes nach ausdruecklichem Nutzerauftrag")
                if args.clear_emails and args.email is not None:
                    raise ValueError("--clear-emails und --email koennen nicht gemeinsam verwendet werden")
                if args.clear_phones and args.phone is not None:
                    raise ValueError("--clear-phones und --phone koennen nicht gemeinsam verwendet werden")
                if args.clear_organization and args.organization is not None:
                    raise ValueError("--clear-organization und --organization koennen nicht gemeinsam verwendet werden")
                if args.clear_note and args.note is not None:
                    raise ValueError("--clear-note und --note koennen nicht gemeinsam verwendet werden")
                emails = () if args.clear_emails else (tuple(args.email) if args.email is not None else None)
                phones = () if args.clear_phones else (tuple(args.phone) if args.phone is not None else None)
                organization = "" if args.clear_organization else args.organization
                note = "" if args.clear_note else args.note
                result = assistant.contact_update(
                    uid=args.uid,
                    name=args.name,
                    emails=emails,
                    phones=phones,
                    organization=organization,
                    note=note,
                    expected_name=args.expected_name,
                    expected_email=args.expected_email,
                    allow_name_collision=bool(args.allow_name_collision),
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.contacts_command == "from-mail":
                if args.dry_run and args.yes:
                    raise ValueError("--dry-run und --yes koennen nicht gemeinsam verwendet werden")
                if not args.dry_run and not args.yes:
                    raise PermissionError("Mail-Kontaktvorschlag benoetigt --dry-run oder --yes")
                result = assistant.contact_from_mail(
                    folder=args.folder,
                    message_id=args.message_id,
                    expected_subject=args.expected_subject,
                    dry_run=bool(args.dry_run),
                    name=args.name,
                    organization=args.organization,
                    phones=tuple(args.phone or []),
                    note=args.note,
                    allow_name_collision=bool(args.allow_name_collision),
                )
                _print(result)
                return 0 if result.get("ok") else 1
        if args.command == "security" and args.security_command == "antivirus":
            if args.antivirus_command == "doctor":
                result = assistant.antivirus_doctor(live_scan=not args.no_live_scan)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.antivirus_command == "self-test":
                result = assistant.antivirus_self_test()
                _print(result)
                return 0 if result.get("ok") else 1
            if args.antivirus_command == "scan":
                result = assistant.antivirus_scan_path(args.file, use_cache=not args.no_cache)
                _print(result)
                return 0 if result.get("status") == "clean" else 1
        if args.command == "monitor":
            if args.monitor_command == "status":
                _print(assistant.monitor.report(days=args.days, live=args.live))
                return 0
            if args.monitor_command == "record":
                _print(assistant.monitor.record(days=args.days, live=args.live))
                return 0
            if args.monitor_command == "history":
                _print(assistant.monitor.history(days=args.days, limit=args.limit))
                return 0
        if args.command == "portfolio":
            if args.portfolio_command == "status":
                result = assistant.portfolio.status()
                _print(result)
                return 0
            if args.portfolio_command == "doctor":
                result = assistant.portfolio.doctor()
                _print(result)
                return 0 if result.get("ok") else 1
            if args.portfolio_command == "import-pp":
                if args.dry_run and args.yes:
                    raise ValueError("--dry-run und --yes koennen nicht gemeinsam verwendet werden")
                if not args.dry_run and not args.yes:
                    raise PermissionError("Portfolio-Import benoetigt --dry-run oder --yes")
                result = assistant.portfolio.import_pp(args.file, dry_run=not args.yes)
                _print(result)
                return 0
            if args.portfolio_command == "import-csv":
                if args.dry_run and args.yes:
                    raise ValueError("--dry-run und --yes koennen nicht gemeinsam verwendet werden")
                if not args.dry_run and not args.yes:
                    raise PermissionError("Portfolio-CSV-Import benoetigt --dry-run oder --yes")
                result = assistant.portfolio_import_csv(
                    local_file=args.file or "",
                    nextcloud_path=args.nextcloud_path or "",
                    dry_run=not args.yes,
                )
                _print(result)
                return 0
            if args.portfolio_command == "holdings":
                _print(assistant.portfolio.holdings())
                return 0
            if args.portfolio_command == "watchlist":
                if args.watchlist_command == "list":
                    _print(assistant.portfolio.watchlist())
                    return 0
                if not args.yes:
                    raise PermissionError("Watchlist-Aenderung benoetigt --yes")
                if args.watchlist_command == "add":
                    _print(
                        assistant.portfolio.watchlist_add(
                            isin=args.isin, name=args.name, symbol=args.symbol,
                            mic=args.mic, currency=args.currency,
                        )
                    )
                    return 0
                if args.watchlist_command == "disable":
                    _print(assistant.portfolio.watchlist_disable(args.isin))
                    return 0
            if args.portfolio_command == "quotes":
                if args.quotes_command == "status":
                    _print(assistant.portfolio.health())
                    return 0
                if args.quotes_command == "refresh":
                    result = assistant.portfolio.refresh_quotes(force=bool(args.force))
                    _print(result)
                    if result.get("status") == "degraded":
                        return 1
                    return 0 if result.get("ok") else 2
            if args.portfolio_command == "analyze":
                result = assistant.portfolio.analyze(args.isin, limit=args.limit)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.portfolio_command == "alerts":
                if args.portfolio_alerts_command == "list":
                    _print(assistant.portfolio.alerts())
                    return 0
                if not args.yes:
                    raise PermissionError("Kursalarm-Aenderung benoetigt --yes")
                if args.portfolio_alerts_command == "add":
                    _print(
                        assistant.portfolio.alert_add(
                            isin=args.isin, direction=args.direction,
                            threshold=Decimal(args.threshold), currency=args.currency,
                            hysteresis_bps=args.hysteresis_bps,
                            cooldown_minutes=args.cooldown_minutes,
                        )
                    )
                    return 0
                if args.portfolio_alerts_command == "disable":
                    _print(assistant.portfolio.alert_disable(args.id))
                    return 0
            if args.portfolio_command == "performance":
                _print(assistant.portfolio.signal_performance())
                return 0
        if args.command == "nextcloud":
            if args.nextcloud_command == "doctor":
                _print(assistant.nextcloud_discovery.root_health())
                return 0
            if args.nextcloud_command == "discover":
                _print(assistant.discover_nextcloud(persist=not args.no_persist))
                return 0
            if args.nextcloud_command == "sync":
                _print(assistant.sync_nextcloud())
                return 0
            if args.nextcloud_command == "list":
                _print(assistant.list_nextcloud_files(args.path, max_depth=args.max_depth))
                return 0
            if args.nextcloud_command == "mkdir":
                _print(assistant.workspace_mkdir(args.path))
                return 0
            if args.nextcloud_command == "upload":
                _print(assistant.workspace_upload(args.local, args.path, content_type=args.content_type))
                return 0
            if args.nextcloud_command == "write-text":
                if args.text is None:
                    if sys.stdin.isatty():
                        raise ValueError("Text fehlt: --text verwenden oder Inhalt ueber stdin uebergeben")
                    text = sys.stdin.read()
                else:
                    text = args.text
                _print(assistant.workspace_write_text(args.path, text, content_type=args.content_type))
                return 0
            if args.nextcloud_command == "move":
                _print(assistant.workspace_move(args.source, args.destination))
                return 0
        if args.command == "resources":
            if args.resources_command == "list":
                _print([asdict(item) for item in assistant.registry.list(kind=args.kind)])
                return 0
            if args.resources_command == "add":
                permissions = tuple(value.strip() for value in args.permissions.split(",") if value.strip())
                existing = assistant.registry.resources.get(args.id)
                existing_permissions = set(existing.permissions) if existing else set()
                expanded = set(permissions) - existing_permissions - {"read"}
                if expanded:
                    if not args.approve_permissions or not sys.stdin.isatty():
                        raise PermissionError(
                            "Neue oder erweiterte Schreibrechte benoetigen --approve-permissions in einem interaktiven Terminal"
                        )
                    confirmation = input(
                        "Berechtigungen erweitern (" + ", ".join(sorted(expanded)) + ")? Tippe exakt APPROVE: "
                    ).strip()
                    if confirmation != "APPROVE":
                        raise PermissionError("Berechtigungserweiterung abgebrochen")
                resource = Resource(
                    id=args.id,
                    kind=args.kind,
                    connector=args.connector,
                    enabled=not args.disabled,
                    remote_id=args.remote_id,
                    permissions=permissions,
                )
                backup = assistant.registry.upsert(resource)
                assistant.storage.audit("resource.upsert", asdict(resource), resource_id=resource.id, actor="user")
                _print({"resource": asdict(resource), "backup": str(backup or "")})
                return 0
        if args.command == "index":
            if args.index_command == "mail":
                _print(assistant.sync_mail())
                return 0
            if args.index_command == "all":
                _print(assistant.sync_all())
                return 0
        if args.command == "search":
            limit = args.limit or config.search.default_limit
            results = assistant.storage.search(
                args.query,
                limit=limit,
                source_type=args.source_type,
                resource_id=args.resource,
            )
            _print([asdict(item) for item in results])
            return 0
        if args.command == "actions":
            if args.actions_command == "list":
                _print([asdict(item) for item in assistant.storage.list_actions(args.status, args.limit)])
                return 0
            if args.actions_command == "plan-upload":
                local_path = Path(args.local_path).expanduser().resolve()
                if not local_path.is_file():
                    raise FileNotFoundError(local_path)
                scan = assistant.antivirus.scan_path(local_path, source_type="action-plan-upload")
                if not scan.clean:
                    raise PermissionError(
                        "Upload-ActionPlan durch Virenscanner blockiert: "
                        + (scan.signature or scan.detail or scan.status)
                    )
                plan = assistant.actions.plan(
                    "files.create",
                    args.resource,
                    {"local_path": str(local_path), "path": args.remote_path, "content_type": args.content_type, "overwrite": False},
                )
                _print(asdict(plan))
                return 0
            if args.actions_command in {"plan-event", "plan-task"}:
                path = Path(args.ics_file).expanduser().resolve()
                ics = path.read_text(encoding="utf-8")
                action_type = "calendar.create" if args.actions_command == "plan-event" else "tasks.create"
                plan = assistant.actions.plan(action_type, args.resource, {"ics": ics, "uid": args.uid})
                _print(asdict(plan))
                return 0
            if args.actions_command == "approve":
                if not sys.stdin.isatty():
                    raise PermissionError("ActionPlan-Freigaben sind derzeit nur interaktiv erlaubt")
                confirmation = input("ActionPlan freigeben? Tippe exakt APPROVE: ").strip()
                if confirmation != "APPROVE":
                    raise PermissionError("Freigabe abgebrochen")
                _print(asdict(assistant.actions.approve(args.action_id)))
                return 0
            if args.actions_command == "execute":
                result = assistant.actions.execute(args.action_id)
                _print(asdict(result))
                return 0 if result.status == "completed" else 1
        if args.command == "settings":
            if args.settings_command == "list":
                _print(assistant.settings.list_safe())
                return 0
            if args.settings_command == "set":
                backup = assistant.settings.set_safe(args.key, args.value, actor="user")
                _print({"ok": True, "backup": str(backup), "key": args.key, "value": args.value})
                return 0
    except (ValueError, KeyError, PermissionError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        logging.getLogger(__name__).exception("Assistant command failed")
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    finally:
        assistant.close()
    return 0
