from __future__ import annotations

from typing import Any


def add_commands(sub: Any) -> None:
    portfolio = sub.add_parser("portfolio", help="Depot, Watchlist, Marktdaten und regelbasierte Analysen")
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
    portfolio_sub.add_parser(
        "valuation",
        help="Aktuellen Depotwert und Gewinn mit EODHD-Kursen und EODHD-FX berechnen",
    )
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
    quotes_get = quotes_sub.add_parser(
        "get", help="Letzten gespeicherten Kurs fuer eine exakte ISIN anzeigen"
    )
    quotes_get.add_argument("--isin", required=True)
    quotes_refresh = quotes_sub.add_parser("refresh")
    quotes_refresh.add_argument("--force", action="store_true")
    portfolio_analyze = portfolio_sub.add_parser(
        "analyze", help="Zeitreihe und deterministische Trendindikatoren berechnen"
    )
    portfolio_analyze.add_argument("--isin", required=True)
    portfolio_analyze.add_argument("--limit", type=int, default=500)
    portfolio_alerts = portfolio_sub.add_parser("alerts", help="Kursmarken verwalten")
    portfolio_alerts_sub = portfolio_alerts.add_subparsers(dest="portfolio_alerts_command", required=True)
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
