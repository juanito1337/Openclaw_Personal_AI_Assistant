from __future__ import annotations

from typing import Any


def add_commands(sub: Any) -> None:
    invoices = sub.add_parser("invoices", help="Rechnungs-OCR, Metadaten und Jahresregister")
    invoices_sub = invoices.add_subparsers(dest="invoices_command", required=True)
    invoices_sub.add_parser("status", help="OCR-Werkzeuge, Register und Zaehler anzeigen")
    invoices_sub.add_parser(
        "audit",
        help="Rechnungsbestand und Pruefbacklog ausschliesslich aggregiert auswerten",
    )
    invoices_list = invoices_sub.add_parser("list", help="Rechnungsmetadaten anzeigen")
    invoices_list.add_argument("--year", type=int, default=0)
    invoices_list.add_argument(
        "--status", default="", choices=("", "confirmed", "confirmed-manual", "review", "error")
    )
    invoices_list.add_argument("--limit", type=int, default=100)
    invoices_review = invoices_sub.add_parser("review", help="Unsichere Rechnungsmetadaten anzeigen")
    invoices_review.add_argument("--limit", type=int, default=100)
    invoices_export = invoices_sub.add_parser(
        "export",
        help="Jahres-CSV schreibfrei vorschauen oder explizit in Nextcloud aktualisieren",
    )
    invoices_export.add_argument("--year", type=int, required=True)
    invoices_export.add_argument(
        "--nextcloud",
        action="store_true",
        help="Kompatibilitaetsoption ohne eigene Schreibfreigabe",
    )
    invoices_export.add_argument(
        "--filename", default="", help="Nur Rechnungen_YYYY.csv ist erlaubt"
    )
    invoices_export_effect = invoices_export.add_mutually_exclusive_group()
    invoices_export_effect.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur im Speicher rendern; weder SQLite noch Nextcloud aendern",
    )
    invoices_export_effect.add_argument(
        "--yes",
        action="store_true",
        help="Verwaltetes Nextcloud-Jahresregister ausdruecklich bedingt ersetzen",
    )
    invoices_backfill = invoices_sub.add_parser(
        "backfill", help="Bereits archivierte Rechnungen eines Jahres neu auswerten"
    )
    invoices_backfill.add_argument("--year", type=int, required=True)
    invoices_backfill.add_argument("--limit", type=int, default=500)
    invoices_backfill_effect = invoices_backfill.add_mutually_exclusive_group()
    invoices_backfill_effect.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur lesen und auswerten; weder SQLite noch Nextcloud aendern",
    )
    invoices_backfill_effect.add_argument(
        "--yes",
        action="store_true",
        help="SQLite und verwaltetes Nextcloud-Jahresregister ausdruecklich aktualisieren",
    )
    invoices_reprocess = invoices_sub.add_parser(
        "reprocess",
        help="Review- oder unklassifizierte Rechnungen schreibfrei neu bewerten",
    )
    invoices_reprocess.add_argument(
        "--status", required=True, choices=("review", "unclassified")
    )
    invoices_reprocess.add_argument("--source-year", type=int, required=True)
    invoices_reprocess.add_argument("--limit", type=int, default=100)
    invoices_reprocess.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Nur PDF lesen, scannen und Alt/Neu-Vorschlag ausgeben; nichts speichern",
    )
    invoices_reprocess_apply = invoices_sub.add_parser(
        "reprocess-apply",
        help="Genau einen unveraenderten Reprocessing-Vorschlag explizit uebernehmen",
    )
    invoices_reprocess_apply.add_argument("--hash", required=True, dest="attachment_hash")
    invoices_reprocess_apply.add_argument(
        "--expected-preview-sha256",
        required=True,
        dest="expected_preview_sha256",
    )
    invoices_reprocess_apply.add_argument("--yes", action="store_true")
    invoices_correct = invoices_sub.add_parser(
        "correct", help="Rechnungsmetadaten nach Nutzerauftrag korrigieren"
    )
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
    tools_list = tools_sub.add_parser("list")
    tools_list.add_argument(
        "--catalog",
        action="store_true",
        help="Statischen Toolkatalog ohne Konfiguration oder Live-Rechte anzeigen",
    )

    mail = sub.add_parser("mail", help="Mail-Werkzeug des Personal Assistants")
    mail_sub = mail.add_subparsers(dest="mail_command", required=True)
    mail_sub.add_parser("status")
    mail_sub.add_parser("doctor")
    mail_sub.add_parser("guide")
    mail_review = mail_sub.add_parser(
        "review", help="Review-Gruende und Einzelfaelle read-only untersuchen"
    )
    review_sub = mail_review.add_subparsers(dest="review_command", required=True)
    review_status = review_sub.add_parser("status", help="Review-Gruende aggregiert anzeigen")
    review_status.add_argument("--days", type=int, default=7)
    review_list = review_sub.add_parser("list", help="Review-Metadaten nach Grund anzeigen")
    review_list.add_argument("--reason", required=True)
    review_list.add_argument("--limit", type=int, default=50)
    review_suggest = review_sub.add_parser(
        "suggest", help="Genau eine Mail read-only neu einschaetzen"
    )
    review_suggest.add_argument("--folder", required=True)
    review_suggest.add_argument("--message-id", required=True)
    review_suggest.add_argument("--expected-subject", required=True)
    review_correct = review_sub.add_parser(
        "correct", help="Genau eine Review-Mail nach ausdruecklicher Freigabe korrigieren"
    )
    review_correct.add_argument("--source", required=True)
    review_correct.add_argument("--message-id", required=True)
    review_correct.add_argument("--expected-subject", required=True)
    review_correct.add_argument("--verdict", required=True, choices=("relevant", "routine", "spam"))
    review_correct.add_argument("--label", default="")
    review_correct.add_argument("--yes", action="store_true")
    mail_folders = mail_sub.add_parser(
        "folders", help="Konfigurierte Mailordner planen oder explizit anlegen"
    )
    folders_sub = mail_folders.add_subparsers(dest="folders_command", required=True)
    folders_sub.add_parser("plan", help="Fehlende konfigurierte Ordner read-only anzeigen")
    folders_apply = folders_sub.add_parser("apply", help="Fehlende konfigurierte Ordner anlegen")
    folders_apply.add_argument("--yes", action="store_true")
    folders_activate = folders_sub.add_parser(
        "activate-relevant",
        help="Genau einen Relevant-Zielordner konfigurieren und explizit anlegen",
    )
    folders_activate.add_argument("--relevant", required=True)
    folders_activate.add_argument("--yes", action="store_true")
    mail_index = mail_sub.add_parser(
        "index", help="Vollkonto-Suchprojektion read-only planen oder lokal aufbauen"
    )
    index_sub = mail_index.add_subparsers(dest="index_command", required=True)
    index_sub.add_parser("plan", help="Ordner und Connectorfaehigkeiten read-only inventarisieren")
    index_backfill = index_sub.add_parser(
        "backfill", help="Begrenzten lokalen Backfill nach expliziter Freigabe ausfuehren"
    )
    index_backfill.add_argument("--page-size", type=int, default=50)
    index_backfill.add_argument("--max-pages", type=int, default=200)
    index_backfill.add_argument("--max-messages", type=int, default=10000)
    index_backfill.add_argument("--max-bytes", type=int, default=1000000000)
    index_backfill.add_argument("--max-message-bytes", type=int, default=100000000)
    index_backfill.add_argument("--max-runtime", type=float, default=3600.0)
    index_backfill.add_argument("--request-interval", type=float, default=0.2)
    index_backfill.add_argument("--yes", action="store_true")
    index_reconcile = index_sub.add_parser(
        "reconcile", help="Autoritative inkrementelle Mailprojektion lokal abgleichen"
    )
    index_reconcile.add_argument("--max-folders", type=int, default=500)
    index_reconcile.add_argument("--max-messages", type=int, default=100000)
    index_reconcile.add_argument("--max-bytes", type=int, default=2000000000)
    index_reconcile.add_argument("--max-message-bytes", type=int, default=100000000)
    index_reconcile.add_argument("--max-runtime", type=float, default=3600.0)
    index_reconcile.add_argument("--request-interval", type=float, default=0.2)
    index_reconcile.add_argument("--retention-generations", type=int, default=2)
    index_reconcile.add_argument("--yes", action="store_true")
    mail_dry = mail_sub.add_parser("dry-run")
    mail_dry.add_argument("--limit", type=int, default=20)
    mail_run = mail_sub.add_parser("run")
    mail_run.add_argument("--limit", type=int, default=20)
    mail_run.add_argument("--drain", action="store_true")
    mail_run.add_argument("--batch-size", type=int, default=20)
    mail_run.add_argument("--max-messages", type=int, default=500)
    mail_run.add_argument("--max-runtime", type=int, default=2700)
    mail_run.add_argument("--max-batches", type=int, default=100)
    mail_orders = mail_sub.add_parser(
        "orders-import", help="Bestehende Mail-Snapshots auf Bestellungen pruefen"
    )
    mail_orders.add_argument("--limit", type=int, default=500)
    mail_orders.add_argument("--dry-run", action="store_true")
    mail_sub.add_parser(
        "move-status", help="Berechtigungen und gesperrte Zielordner des Mail-Verschiebewerkzeugs pruefen"
    )
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
    mail_draft_reply = mail_sub.add_parser(
        "reply-draft", help="Antwortentwurf fuer eine ausgewaehlte Mail anlegen"
    )
    mail_draft_reply.add_argument("--folder", required=True)
    mail_draft_reply.add_argument("--message-id", required=True)
    mail_draft_reply.add_argument("--expected-subject", default="")
    mail_draft_reply.add_argument("--body", required=True)
    mail_send_reply = mail_sub.add_parser(
        "reply-send", help="Einen zuvor angezeigten Antwortentwurf versenden"
    )
    mail_send_reply.add_argument("--draft-id", required=True)
    mail_send_reply.add_argument("--yes", action="store_true")
    mail_compose_draft = mail_sub.add_parser("compose-draft", help="Entwurf fuer eine neue Mail anlegen")
    mail_compose_draft.add_argument("--to", required=True)
    mail_compose_draft.add_argument("--subject", required=True)
    mail_compose_draft.add_argument("--body", required=True)
    mail_compose_send = mail_sub.add_parser(
        "compose-send", help="Einen zuvor angezeigten neuen Mailentwurf versenden"
    )
    mail_compose_send.add_argument("--draft-id", required=True)
    mail_compose_send.add_argument("--yes", action="store_true")
    mail_move = mail_sub.add_parser(
        "move", help="Eine eindeutig identifizierte Mail kontrolliert verschieben"
    )
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
    learning_not_spam = learning_sub.add_parser(
        "not-spam", help="Nicht-Spam-Gegenbelege und ihren Ursprung anzeigen"
    )
    learning_not_spam.add_argument("--limit", type=int, default=100)
    learning_mixed = learning_sub.add_parser(
        "mixed-senders", help="Absender mit verschiedenen Mailtypen anzeigen"
    )
    learning_mixed.add_argument("--limit", type=int, default=100)
    learning_conflicts = learning_sub.add_parser(
        "conflicts", help="Widerspruechliche Musterkorrekturen anzeigen"
    )
    learning_conflicts.add_argument("--limit", type=int, default=100)
    learning_conflicts.add_argument("--id", default="", help="Optional genau eine conflict_id anzeigen")
    learning_forget = learning_sub.add_parser(
        "forget-feedback", help="Genau einen Korrekturbeleg nach expliziter Freigabe entfernen"
    )
    learning_forget.add_argument("--id", type=int, required=True, help="Exakte ID aus conflicts/feedback")
    learning_forget.add_argument("--yes", action="store_true")
    learning_evaluate = learning_sub.add_parser(
        "evaluate", help="Lernqualitaet und Basisvergleich aggregiert auswerten"
    )
    learning_evaluate.add_argument("--limit", type=int, default=5000)
    learning_export = learning_sub.add_parser(
        "dataset-export", help="Pseudonymisierten Lern-Datensatz lokal exportieren"
    )
    learning_export.add_argument("--output", default="mail_agent/data/learning_dataset.json")
    learning_export.add_argument("--limit", type=int, default=5000)
    learning_sub.add_parser("folder-list", help="Dynamische Korrektur-Unterordner anzeigen")
    learning_create = learning_sub.add_parser(
        "folder-create", help="Korrektur-Unterordner nach Nutzerauftrag anlegen"
    )
    learning_create.add_argument(
        "--parent", required=True, choices=("routine", "important", "spam", "not-spam")
    )
    learning_create.add_argument("--name", required=True)
    learning_create.add_argument("--label", default="")
    learning_create.add_argument("--yes", action="store_true")
    learning_disable = learning_sub.add_parser(
        "folder-disable", help="Lernzuordnung deaktivieren; IMAP-Ordner behalten"
    )
    learning_disable.add_argument("--folder", required=True)
    learning_disable.add_argument("--yes", action="store_true")
