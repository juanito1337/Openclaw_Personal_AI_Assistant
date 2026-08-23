from __future__ import annotations

from typing import Any

from personal_assistant.work_scheduler import VALID_TOPICS


def add_commands(sub: Any) -> None:
    sub.add_parser("doctor", help="Core, Index, Policies und Nextcloud pruefen")
    sub.add_parser("status", help="Kompakten Status anzeigen")
    capabilities = sub.add_parser("capabilities", help="Maschinenlesbare Rechte und Grenzen anzeigen")
    capabilities.add_argument(
        "--schema",
        action="store_true",
        help="Konfigurationsfreies Schema der Live-Capabilities anzeigen",
    )

    version = sub.add_parser("version", help="Installierte Version, Konsistenz und Updatehistorie anzeigen")
    version.add_argument(
        "--verify",
        action="store_true",
        help="Manifest, AGENTS.md, README und CHANGELOG gegeneinander pruefen",
    )
    version.add_argument("--history", action="store_true", help="Releasehistorie mit ausgeben")
    version.add_argument(
        "--since", default="", help="Nur Aenderungen nach dieser Version anzeigen, z. B. 3.4.0-r18"
    )
    version.add_argument("--limit", type=int, default=10, help="Maximale Anzahl Historieneintraege")

    monitor = sub.add_parser("monitor", help="Leistung, Zuverlaessigkeit und Datenfrische bewerten")
    monitor_sub = monitor.add_subparsers(dest="monitor_command", required=True)
    monitor_status = monitor_sub.add_parser(
        "status", help="Aktuellen evidenzbasierten Gesundheitswert anzeigen"
    )
    monitor_status.add_argument("--days", type=int, default=7)
    monitor_status.add_argument(
        "--live", action="store_true", help="Nextcloud und lokale Dienste live pruefen"
    )
    monitor_record = monitor_sub.add_parser("record", help="Monitoring-Snapshot lokal speichern")
    monitor_record.add_argument("--days", type=int, default=7)
    monitor_record.add_argument("--live", action="store_true")
    monitor_history = monitor_sub.add_parser("history", help="Gespeicherte Entwicklung anzeigen")
    monitor_history.add_argument("--days", type=int, default=30)
    monitor_history.add_argument("--limit", type=int, default=100)

    scheduler = sub.add_parser("scheduler", help="Adaptive Hintergrund-Queue und Themenfokus verwalten")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    scheduler_status = scheduler_sub.add_parser(
        "status", help="Aktive, wartende und letzte Aufgaben anzeigen"
    )
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
    jobs_status = jobs_sub.add_parser(
        "status", help="Soll- und Ist-Zustand aller freigegebenen Jobs anzeigen"
    )
    jobs_status.add_argument(
        "--target",
        choices=("standard", "all", "supervisor", "mail", "mail-index", "sync", "portfolio", "monitor"),
        default="all",
    )
    jobs_status.add_argument("--deep", action="store_true", help="Zusaetzliche Tool-Health-Checks ausfuehren")
    jobs_check = jobs_sub.add_parser(
        "check", help="Jobs pruefen und Zustandswechsel als lokale Alerts speichern"
    )
    jobs_check.add_argument(
        "--target",
        choices=("standard", "all", "supervisor", "mail", "mail-index", "sync", "portfolio", "monitor"),
        default="all",
    )
    jobs_check.add_argument("--deep", action="store_true", help="Zusaetzliche Tool-Health-Checks ausfuehren")
    jobs_sub.add_parser("alerts", help="Aktive Job-Alerts und letzten beobachteten Zustand anzeigen")
    for command_name, help_text in (
        ("on", "Standardjobs einschalten und sicher hochfahren"),
        ("restart", "Standardjobs reparieren und neu starten"),
        ("off", "Produktive Jobs bewusst ausschalten"),
    ):
        job_action = jobs_sub.add_parser(command_name, help=help_text)
        job_action.add_argument(
            "target",
            nargs="?",
            choices=("standard", "all", "supervisor", "mail", "mail-index", "sync", "portfolio", "monitor"),
            default="standard",
        )
        if command_name in {"on", "restart"}:
            job_action.add_argument(
                "--no-run-now",
                action="store_true",
                help="Timer aktivieren, aber keinen sofortigen Joblauf starten",
            )

    ollama = sub.add_parser("ollama", help="Ollama-Prioritaetskoordinator pruefen und kontrolliert starten")
    ollama_sub = ollama.add_subparsers(dest="ollama_command", required=True)
    ollama_sub.add_parser("status", help="Proxy, Queue und Upstream-Zustand anzeigen")
    ollama_sub.add_parser("check", help="Ollama-Upstream ueber den Proxy live pruefen")
    ollama_sub.add_parser("queue", help="Aktive und wartende Modellauftraege kompakt anzeigen")
    ollama_sub.add_parser("start", help="Prioritaetskoordinator nach ausdruecklichem Auftrag starten")
    ollama_sub.add_parser("restart", help="Prioritaetskoordinator nach ausdruecklichem Auftrag neu starten")

    performance = sub.add_parser("performance", help="Privacy-sichere Performance-Telemetrie auswerten")
    performance_sub = performance.add_subparsers(dest="performance_command", required=True)
    performance_mail = performance_sub.add_parser(
        "mail", help="Laufzeiten des automatischen Mail-Interfaces anzeigen"
    )
    performance_mail.add_argument("--limit", type=int, default=20)
    performance_mail.add_argument("--raw", action="store_true")
