from __future__ import annotations

from typing import Any


def add_commands(sub: Any) -> None:
    setup = sub.add_parser("setup", help="Zentrale Einrichtung")
    setup_sub = setup.add_subparsers(dest="setup_command", required=True)
    setup_sub.add_parser("init", help="Lokale Konfigurationsdateien anlegen")
    nc_setup = setup_sub.add_parser("nextcloud", help="Nextcloud-Zugang zentral einrichten")
    nc_setup.add_argument("--url")
    nc_setup.add_argument("--username")
    nc_setup.add_argument("--token")
    nc_setup.add_argument("--non-interactive", action="store_true")
    nc_setup.add_argument(
        "--use-existing",
        action="store_true",
        help="Vorhandene zentrale/legacy NEXTCLOUD_* Variablen aktivieren",
    )
    tools_setup = setup_sub.add_parser(
        "tools", help="Mail-, Rechnungs- und Kalenderwerkzeuge zentral einrichten"
    )
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
    sources_setup = setup_sub.add_parser(
        "mail-sources", help="Primaer- und Spam-/Quarantaeneordner konfigurieren"
    )
    sources_setup.add_argument("--primary", default="INBOX")
    sources_setup.add_argument("--quarantine-folder", action="append", default=[])
    sources_setup.add_argument("--max-per-run", type=int, default=10)
    sources_setup.add_argument(
        "--full-triage",
        action="store_true",
        help="Auch gewoehnliche Routine-/Spam-Mails aus dem Providerordner normal routen",
    )
    workspace_setup = setup_sub.add_parser(
        "workspace", help="Nextcloud-Arbeitsbereich und kontrollierte Schreibwerkzeuge einrichten"
    )
    workspace_setup.add_argument("--resource", default="nextcloud-files-main")
    workspace_setup.add_argument("--root", default="Assistent")
    workspace_setup.add_argument("--outbox", default="personal_assistant/data/workspace_outbox")
    workspace_setup.add_argument("--disable-mkdir", action="store_true")
    workspace_setup.add_argument("--disable-upload", action="store_true")
    workspace_setup.add_argument("--disable-write-text", action="store_true")
    workspace_setup.add_argument("--disable-move", action="store_true")
    workspace_setup.add_argument("--approve-permissions", action="store_true")
    calendar_setup = setup_sub.add_parser(
        "calendar",
        help=(
            "Direktes Nextcloud-Kalenderwerkzeug fuer Lesen, Anlegen und optionales Aktualisieren einrichten"
        ),
    )
    calendar_setup.add_argument("--resource", default="")
    calendar_setup.add_argument("--timezone", default="Europe/Berlin")
    calendar_setup.add_argument("--default-duration-minutes", type=int, default=60)
    calendar_setup.add_argument("--max-duration-hours", type=int, default=168)
    calendar_setup.add_argument("--max-future-days", type=int, default=730)
    calendar_setup.add_argument("--approve-permissions", action="store_true")
    tasks_setup = setup_sub.add_parser(
        "tasks",
        help=(
            "Direktes Nextcloud-Aufgabenwerkzeug fuer Lesen, Anlegen und optionales Aktualisieren einrichten"
        ),
    )
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
    portfolio_setup.add_argument("--provider", default="eodhd", choices=("eodhd",))
    portfolio_setup.add_argument("--interval-minutes", type=int, default=15, choices=(15, 30, 60, 90, 120))
    portfolio_setup.add_argument("--stale-warning-minutes", type=int)
    portfolio_setup.add_argument("--stale-critical-minutes", type=int)
    portfolio_setup.add_argument("--disable", action="store_true")
    portfolio_setup.add_argument("--approve-permissions", action="store_true")
