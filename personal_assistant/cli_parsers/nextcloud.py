from __future__ import annotations

from typing import Any


def add_commands(sub: Any) -> None:
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
    nc_move = nc_sub.add_parser(
        "move", help="Datei oder Ordner innerhalb des Arbeitsbereichs ohne Ueberschreiben verschieben"
    )
    nc_move.add_argument("--source", required=True)
    nc_move.add_argument("--destination", required=True)

    calendar = sub.add_parser("calendar", help="Direktes Nextcloud-Kalenderwerkzeug")
    calendar_sub = calendar.add_subparsers(dest="calendar_command", required=True)
    calendar_sub.add_parser("discover", help="Erreichbare VEVENT-Kalender read-only auflisten")
    calendar_sub.add_parser(
        "status", help="Konfiguration sowie Lese-, Anlege- und Aktualisierungsrechte pruefen"
    )
    calendar_configure = calendar_sub.add_parser(
        "configure", help="Entdeckten Kalender nach ausdruecklicher Auswahl konfigurieren"
    )
    calendar_configure.add_argument("--resource", required=True, help="resource_id aus calendar discover")
    calendar_configure.add_argument("--timezone", default="Europe/Berlin")
    calendar_configure.add_argument("--default-duration-minutes", type=int, default=60)
    calendar_configure.add_argument("--max-duration-hours", type=int, default=168)
    calendar_configure.add_argument("--max-future-days", type=int, default=730)
    calendar_configure.add_argument(
        "--allow-update", action="store_true", help="Bestehende Termine ETag-geschuetzt aktualisieren"
    )
    calendar_configure.add_argument(
        "--yes", action="store_true", help="Ausdrueckliche Nutzerfreigabe bestaetigen"
    )
    calendar_create = calendar_sub.add_parser(
        "create", help="Neuen Termin anlegen, ohne bestehende Termine zu ersetzen"
    )
    calendar_create.add_argument("--title", required=True)
    calendar_create.add_argument(
        "--start", required=True, help="ISO-8601; ohne Offset gilt die konfigurierte Zeitzone"
    )
    calendar_create.add_argument("--end", default="", help="ISO-8601; alternativ --duration-minutes")
    calendar_create.add_argument("--duration-minutes", type=int, default=None)
    calendar_create.add_argument("--location", default="")
    calendar_create.add_argument("--description", default="")
    calendar_create.add_argument(
        "--uid", default="", help="Optional; normalerweise automatisch und idempotent"
    )
    calendar_list = calendar_sub.add_parser("list", help="Termine im konfigurierten Zeitraum lesen")
    calendar_list.add_argument("--limit", type=int, default=100)
    calendar_search = calendar_sub.add_parser(
        "search", help="Termine nach Titel, Ort, Beschreibung oder UID suchen"
    )
    calendar_search.add_argument("--query", required=True)
    calendar_search.add_argument("--limit", type=int, default=50)
    calendar_update = calendar_sub.add_parser(
        "update", help="Einen eindeutig per UID ausgewaehlten Termin aktualisieren"
    )
    calendar_update.add_argument("--uid", required=True, help="UID aus calendar list/search")
    calendar_update.add_argument("--title", default=None)
    calendar_update.add_argument(
        "--start", default=None, help="ISO-8601; ohne Offset gilt die konfigurierte Zeitzone"
    )
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
    tasks_configure = tasks_sub.add_parser(
        "configure", help="Entdeckte Aufgabenliste nach ausdruecklicher Auswahl konfigurieren"
    )
    tasks_configure.add_argument("--resource", required=True, help="resource_id aus tasks discover")
    tasks_configure.add_argument("--timezone", default="Europe/Berlin")
    tasks_configure.add_argument("--max-future-days", type=int, default=3650)
    tasks_configure.add_argument(
        "--read-only", action="store_true", help="Nur Lesen erlauben, keine neuen Aufgaben anlegen"
    )
    tasks_configure.add_argument(
        "--create-only", action="store_true", help="Nur Anlegen erlauben, keine Aufgabenliste lesen"
    )
    tasks_configure.add_argument(
        "--allow-update", action="store_true", help="Bestehende Aufgaben ETag-geschuetzt aktualisieren"
    )
    tasks_configure.add_argument(
        "--yes", action="store_true", help="Ausdrueckliche Nutzerfreigabe bestaetigen"
    )
    tasks_status = tasks_sub.add_parser(
        "status", help="Konfiguration, Rechte und VTODO-Unterstuetzung pruefen"
    )
    tasks_status.add_argument("--no-live", action="store_true")
    tasks_list = tasks_sub.add_parser("list", help="Aufgaben lesen")
    tasks_list.add_argument("--include-completed", action="store_true")
    tasks_list.add_argument("--limit", type=int, default=100)
    tasks_create = tasks_sub.add_parser(
        "create", help="Neue Aufgabe anlegen, ohne bestehende Aufgaben zu ersetzen"
    )
    tasks_create.add_argument("--title", required=True)
    tasks_create.add_argument("--due", default="", help="YYYY-MM-DD oder ISO-8601")
    tasks_create.add_argument("--start", default="", help="YYYY-MM-DD oder ISO-8601")
    tasks_create.add_argument("--description", default="")
    tasks_create.add_argument("--priority", type=int, default=0)
    tasks_create.add_argument("--category", action="append", default=[])
    tasks_create.add_argument("--uid", default="", help="Optional; normalerweise automatisch und idempotent")
    tasks_update = tasks_sub.add_parser(
        "update", help="Eine eindeutig per UID ausgewaehlte Aufgabe aktualisieren"
    )
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
    tasks_update.add_argument(
        "--status", choices=("NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"), default=None
    )
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
    contacts_search = contacts_sub.add_parser(
        "search", help="Kontakte nach Name, Mail, Telefon oder Firma suchen"
    )
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
    contacts_update.add_argument(
        "--email", action="append", default=None, help="Ersetzt alle E-Mail-Adressen"
    )
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
    contacts_mail.add_argument(
        "--message-id", required=True, help="Mail-ID aus mail list, nicht Message-ID-Header"
    )
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
    orders_due = orders_sub.add_parser(
        "due-date-backfill", help="Fehlende Deck-Faelligkeitsdaten kontrolliert ergaenzen"
    )
    orders_due.add_argument("--limit", type=int, default=500)
    orders_due.add_argument("--dry-run", action="store_true")
    orders_due.add_argument("--yes", action="store_true")
