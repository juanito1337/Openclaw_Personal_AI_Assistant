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
    mapping = portfolio_sub.add_parser(
        "mapping", help="EODHD-Kandidaten durch Ollama begrenzt auswaehlen lassen"
    )
    mapping_sub = mapping.add_subparsers(dest="mapping_command", required=True)
    mapping_suggest = mapping_sub.add_parser(
        "suggest", help="Schreibgeschuetzten Mappingvorschlag fuer ISIN oder Wertpapiername erzeugen"
    )
    mapping_source = mapping_suggest.add_mutually_exclusive_group(required=True)
    mapping_source.add_argument("--isin")
    mapping_source.add_argument("--query")
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
    research = portfolio_sub.add_parser(
        "research", help="EODHD-Research, Aktiensuche und erklaerbares Ranking"
    )
    research_sub = research.add_subparsers(dest="research_command", required=True)
    research_sub.add_parser("status", help="Research-Konfiguration und letzten Lauf anzeigen")
    research_sub.add_parser("models", help="Versionierte Analysemodelle und Gewichte anzeigen")
    research_history = research_sub.add_parser("history", help="Research-Laeufe anzeigen")
    research_history.add_argument("--limit", type=int, default=20)
    research_screen = research_sub.add_parser(
        "screen", help="Neue Aktien mit EODHD suchen und deterministisch bewerten"
    )
    research_screen.add_argument(
        "--strategy",
        choices=("auto", "balanced", "quality-value", "quality-growth", "dividend-quality"),
        default="auto",
    )
    research_screen.add_argument("--exchange", default="")
    research_screen.add_argument("--sector", default="")
    research_screen.add_argument("--limit", type=int, default=5)
    research_analyze = research_sub.add_parser(
        "analyze", help="Eine exakte ISIN mit EODHD-Fundamental- und EOD-Daten analysieren"
    )
    research_analyze.add_argument("--isin", required=True)
    research_analyze.add_argument(
        "--strategy",
        choices=("auto", "balanced", "quality-value", "quality-growth", "dividend-quality"),
        default="auto",
    )
    philosophy = portfolio_sub.add_parser(
        "philosophy", help="Versioniertes Investmentprofil und belegtes Feedback verwalten"
    )
    philosophy_sub = philosophy.add_subparsers(dest="philosophy_command", required=True)
    philosophy_sub.add_parser("show", help="Aktuell bestaetigtes Investmentprofil anzeigen")
    philosophy_sub.add_parser("review", help="Profiltreue und Konzentrationsregeln pruefen")
    philosophy_history = philosophy_sub.add_parser("history", help="Profilversionen anzeigen")
    philosophy_history.add_argument("--limit", type=int, default=20)
    philosophy_set = philosophy_sub.add_parser(
        "set", help="Vollstaendige neue Investmentprofil-Version bestaetigen"
    )
    philosophy_set.add_argument(
        "--risk-tolerance", required=True, choices=("conservative", "balanced", "growth")
    )
    philosophy_set.add_argument("--horizon-years", required=True, type=int)
    philosophy_set.add_argument(
        "--strategy",
        required=True,
        choices=("balanced", "quality-value", "quality-growth", "dividend-quality"),
    )
    philosophy_set.add_argument("--max-position-pct", required=True)
    philosophy_set.add_argument("--max-sector-pct", required=True)
    philosophy_set.add_argument("--preferred-sectors", default="")
    philosophy_set.add_argument("--excluded-sectors", default="")
    philosophy_set.add_argument("--notes", default="")
    philosophy_set.add_argument("--yes", action="store_true")
    philosophy_feedback = philosophy_sub.add_parser(
        "feedback", help="Begruendete Rueckmeldung zu einem gespeicherten Research-Kandidaten"
    )
    philosophy_feedback.add_argument("--candidate-id", required=True)
    philosophy_feedback.add_argument(
        "--decision", required=True, choices=("interested", "rejected", "watch", "bought", "sold")
    )
    philosophy_feedback.add_argument("--reason", required=True)
    philosophy_feedback.add_argument("--yes", action="store_true")
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
