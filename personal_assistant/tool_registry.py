from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .tool_settings import ToolSettings


@dataclass(frozen=True, slots=True)
class AgentTool:
    id: str
    description: str
    command: str
    mode: str
    writes_external_data: bool = False
    approval: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_tool_registry(settings: ToolSettings) -> list[AgentTool]:
    tools = [
        AgentTool(
            "assistant.status",
            "Core, Ressourcen, Policies und Connector-Status anzeigen",
            './scripts/assistant.sh status',
            "read",
        ),
        AgentTool(
            "assistant.version",
            "Installierte OpenClaw-Version und Konsistenz aus der verbindlichen Release-Datei pruefen",
            './scripts/assistant.sh version --verify',
            "read",
        ),
        AgentTool(
            "assistant.version.history",
            "Aenderungen der installierten und vorherigen Updates maschinenlesbar anzeigen",
            './scripts/assistant.sh version --verify --history --limit 10',
            "read",
        ),
        AgentTool(
            "assistant.version.since",
            "Alle bekannten Aenderungen seit einer genannten Version anzeigen",
            './scripts/assistant.sh version --verify --history --since "<Version>" --limit 20',
            "read",
        ),
        AgentTool(
            "assistant.search",
            "E-Mails, Nextcloud-Dateien, Kontakte und Kalender lokal durchsuchen",
            './scripts/assistant.sh search "<Suchbegriff>"',
            "read",
        ),
        AgentTool(
            "assistant.monitor.status",
            "Evidenzbasierten Gesundheitswert, Teilwerte und konkrete Probleme anzeigen",
            './scripts/assistant.sh monitor status --days 7 --live',
            "read",
        ),
        AgentTool(
            "assistant.jobs.status",
            "Soll-/Ist-Zustand der freigegebenen Hintergrundjobs anzeigen",
            './scripts/assistant.sh jobs status --target all',
            "read",
        ),
        AgentTool(
            "assistant.jobs.check",
            "Hintergrundjobs pruefen, Alerts aktualisieren und die eng begrenzte Mail-Dry-Run-Sperre automatisch beheben",
            './scripts/assistant.sh jobs check --target all --deep',
            "local-write",
            False,
            "job-monitoring-and-safe-mail-recovery",
        ),
        AgentTool(
            "assistant.jobs.alerts",
            "Aktive Job-Ausfaelle und den letzten beobachteten Zustand anzeigen",
            './scripts/assistant.sh jobs alerts',
            "read",
        ),
        AgentTool(
            "assistant.jobs.on",
            "Standardfunktionen nach ausdruecklichem Nutzerauftrag sicher einschalten",
            './scripts/assistant.sh jobs on standard',
            "local-write",
            False,
            "explicit-user-start",
        ),
        AgentTool(
            "assistant.jobs.restart",
            "Standardfunktionen nach ausdruecklichem Nutzerauftrag reparieren und neu starten",
            './scripts/assistant.sh jobs restart standard',
            "local-write",
            False,
            "explicit-user-restart",
        ),
        AgentTool(
            "assistant.jobs.off",
            "Produktive Standardjobs nach ausdruecklichem Nutzerauftrag ausschalten",
            './scripts/assistant.sh jobs off standard',
            "local-write",
            False,
            "explicit-user-stop",
        ),
        AgentTool(
            "assistant.ollama.status",
            "Ollama-Prioritaetskoordinator, Queue und Upstream-Zustand anzeigen",
            './scripts/assistant.sh ollama status',
            "read",
        ),
        AgentTool(
            "assistant.ollama.check",
            "Ollama-Upstream ueber den lokalen Prioritaetskoordinator live pruefen",
            './scripts/assistant.sh ollama check',
            "read",
        ),
        AgentTool(
            "assistant.ollama.queue",
            "Aktive und wartende Modellauftraege mit Prioritaeten anzeigen",
            './scripts/assistant.sh ollama queue',
            "read",
        ),
        AgentTool(
            "assistant.ollama.start",
            "Ollama-Prioritaetskoordinator nach ausdruecklichem Nutzerauftrag starten",
            './scripts/assistant.sh ollama start',
            "local-write",
            False,
            "explicit-user-start",
        ),
        AgentTool(
            "assistant.ollama.restart",
            "Ollama-Prioritaetskoordinator nach ausdruecklichem Nutzerauftrag neu starten und verifizieren",
            './scripts/assistant.sh ollama restart',
            "local-write",
            False,
            "explicit-user-restart",
        ),
        AgentTool(
            "assistant.performance.mail",
            "Privacy-sichere Laufzeit-, Queue- und Ollama-Metriken des Mail-Interfaces auswerten",
            './scripts/assistant.sh performance mail --limit 20',
            "read",
        ),
        AgentTool(
            "assistant.monitor.record",
            "Monitoring-Snapshot lokal fuer Trendanalyse speichern",
            './scripts/assistant.sh monitor record --days 7 --live',
            "local-write",
            False,
            "monitoring-local-only",
        ),
        AgentTool(
            "assistant.monitor.history",
            "Gesundheitswert und Trend der letzten 30 Tage anzeigen",
            './scripts/assistant.sh monitor history --days 30',
            "read",
        ),
        AgentTool(
            "security.antivirus.doctor",
            "ClamAV-Dienst, Signaturstand und einen lokalen Testscan pruefen",
            './scripts/assistant.sh security antivirus doctor',
            "read",
        ),
        AgentTool(
            "security.antivirus.self-test",
            "Harmlosen EICAR-Test ausfuehren und die Malware-Erkennung nachweisen",
            './scripts/assistant.sh security antivirus self-test',
            "read",
        ),
        AgentTool(
            "security.antivirus.scan",
            "Datei aus der kontrollierten Workspace-Outbox vor weiterer Verwendung auf Schadsoftware pruefen",
            './scripts/assistant.sh security antivirus scan --file "personal_assistant/data/workspace_outbox/<Datei>"',
            "read",
            False,
            "host-antivirus-read-only",
        ),
        AgentTool(
            "nextcloud.list",
            "Dateien innerhalb der erlaubten Nextcloud-Wurzeln auflisten",
            './scripts/assistant.sh nextcloud list --path "Assistent"',
            "read",
        ),
        AgentTool(
            "nextcloud.sync",
            "Nextcloud-Inhalte inkrementell in den lokalen Index synchronisieren",
            './scripts/assistant.sh nextcloud sync',
            "read",
        ),
        AgentTool(
            "mail.status",
            "Status des Mail-Werkzeugs anzeigen",
            './scripts/assistant.sh mail status',
            "read",
        ),
        AgentTool(
            "mail.doctor",
            "Mail-Werkzeug und Integrationen diagnostizieren",
            './scripts/assistant.sh mail doctor',
            "read",
        ),
        AgentTool(
            "mail.learning.status",
            "Korrekturlernen, gemischte Absender, Musterkonflikte und Lernordner anzeigen",
            './scripts/assistant.sh mail learning status',
            "read",
        ),
        AgentTool(
            "mail.learning.feedback",
            "Letzte Nutzerkorrekturen mit Muster, Typ-Label und Match-Metadaten ohne Mailtext anzeigen",
            './scripts/assistant.sh mail learning feedback --limit 50',
            "read",
        ),
        AgentTool(
            "mail.learning.not-spam",
            "Nicht-Spam-Gegenbelege mit Ursprung INBOX-Restore oder Korrekturordner ohne Mailinhalte anzeigen",
            './scripts/assistant.sh mail learning not-spam --limit 100',
            "read",
        ),
        AgentTool(
            "mail.learning.mixed-senders",
            "Absender anzeigen, die nach Nutzerfeedback verschiedene Mailtypen senden",
            './scripts/assistant.sh mail learning mixed-senders --limit 100',
            "read",
        ),
        AgentTool(
            "mail.learning.conflicts",
            "Widerspruechliche Korrekturen mit stabiler conflict_id und betroffenen Feedback-IDs anzeigen",
            './scripts/assistant.sh mail learning conflicts --limit 100',
            "read",
        ),
        AgentTool(
            "mail.learning.evaluate",
            "Lernqualitaet chronologisch gegen die alte Absenderlogik und gespeicherte Klassifikationen auswerten",
            './scripts/assistant.sh mail learning evaluate --limit 5000',
            "read",
        ),
        AgentTool(
            "mail.learning.dataset-export",
            "Pseudonymisierten Lern-Datensatz ohne Mailtexte, Betreffe, Adressen oder Message-IDs lokal exportieren",
            './scripts/assistant.sh mail learning dataset-export --output "mail_agent/data/learning_dataset.json" --limit 5000',
            "local-write",
            False,
            "learning-dataset-local-only",
        ),
        AgentTool(
            "mail.learning.folder-list",
            "Kontrollierte dynamische Korrektur-Unterordner anzeigen",
            './scripts/assistant.sh mail learning folder-list',
            "read",
        ),
        AgentTool(
            "mail.learning.folder-create",
            "Korrektur-Unterordner nach ausdruecklichem Nutzerauftrag anlegen und registrieren",
            './scripts/assistant.sh mail learning folder-create --parent "<routine|important|spam|not-spam>" --name "<Name>" --label "<Typ>" --yes',
            "write",
            True,
            "explicit-user-create-correction-folder",
        ),
        AgentTool(
            "mail.learning.folder-disable",
            "Lernzuordnung eines dynamischen Korrekturordners nach Nutzerauftrag deaktivieren, ohne den IMAP-Ordner zu loeschen",
            './scripts/assistant.sh mail learning folder-disable --folder "<Ordner>" --yes',
            "local-write",
            False,
            "explicit-user-disable-learning-folder",
        ),
        AgentTool(
            "mail.sources.configure",
            "Primaer- und Provider-Spam-/Quarantaeneordner kontrolliert konfigurieren",
            './scripts/assistant.sh setup mail-sources --primary "INBOX" --quarantine-folder "Spamverdacht"',
            "local-write",
            False,
            "safe-settings",
        ),
        AgentTool(
            "mail.dry-run",
            "Mail-Pipeline ohne externe Aenderungen pruefen",
            './scripts/assistant.sh mail dry-run --limit 20',
            "read",
        ),
        AgentTool(
            "mail.run",
            "Mail-Pipeline produktiv ausfuehren",
            './scripts/assistant.sh mail run --limit 20',
            "write",
            True,
            "configured-policy",
        ),
        AgentTool(
            "mail.spam-review",
            "Provider-Spamordner als Quarantaene pruefen und klare Fehlklassifizierungen retten",
            './scripts/assistant.sh mail spam-review --limit 20',
            "write",
            True,
            "quarantine-rescue-policy",
        ),
    ]

    if settings.mail.move.enabled:
        tools.extend([
            AgentTool(
                "mail.move-status",
                "Berechtigungen, vorhandene Ordner und gesperrte Ziele des Mail-Verschiebewerkzeugs pruefen",
                './scripts/assistant.sh mail move-status',
                "read",
            ),
            AgentTool(
                "mail.list",
                "Mail-Metadaten eines vorhandenen Ordners lesen, um eine Mail eindeutig auszuwaehlen",
                './scripts/assistant.sh mail list --folder "<Ordner>" --limit 50',
                "read",
            ),
            AgentTool(
                "mail.search",
                "Mail-Metadaten ordneruebergreifend einschliesslich Review-Ordnern durchsuchen",
                './scripts/assistant.sh mail search --query "<Suchbegriff>" --limit 50',
                "read",
            ),
            AgentTool(
                "mail.read",
                "Eine per Ordner und Mail-ID eindeutig ausgewaehlte Mail read-only mit Inhalt lesen",
                './scripts/assistant.sh mail read --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>"',
                "read",
            ),
            AgentTool(
                "mail.reply-draft",
                "Antwortentwurf zu einer eindeutig ausgewaehlten Mail anlegen und vollstaendig anzeigen",
                './scripts/assistant.sh mail reply-draft --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>" --body "<Entwurf>"',
                "local-write",
                False,
                "draft-only-no-send",
            ),
            AgentTool(
                "mail.reply-send",
                "Nur einen zuvor angezeigten Antwortentwurf nach ausdruecklicher Nutzerfreigabe versenden",
                './scripts/assistant.sh mail reply-send --draft-id "<Entwurfs-ID>" --yes',
                "write",
                True,
                "explicit-user-approved-presented-draft",
            ),
            AgentTool(
                "mail.move",
                "Eine eindeutig per Mail-ID ausgewaehlte Mail zwischen vorhandenen, nicht-destruktiven Ordnern verschieben",
                './scripts/assistant.sh mail move --source "<Quelle>" --destination "<Ziel>" --message-id "<ID>" --expected-subject "<Betreff>"',
                "write",
                True,
                "configured-mail-organize-single-message",
            ),
        ])

    tools.extend([
        AgentTool(
            "nextcloud.contacts.discover",
            "Erreichbare Nextcloud-CardDAV-Adressbuecher read-only mit Serverrechten auflisten",
            './scripts/assistant.sh contacts discover',
            "read",
        ),
        AgentTool(
            "nextcloud.contacts.configure",
            "Ein zuvor entdecktes Adressbuch nach ausdruecklicher Nutzerwahl konfigurieren; Aktualisierungsrecht nur mit --allow-update",
            './scripts/assistant.sh contacts configure --resource "<resource_id>" --allow-update --yes',
            "local-write",
            False,
            "explicit-user-addressbook-selection",
        ),
        AgentTool(
            "nextcloud.calendar.discover",
            "Erreichbare Nextcloud-Kalender read-only mit VEVENT-Unterstuetzung und Serverrechten auflisten",
            './scripts/assistant.sh calendar discover',
            "read",
        ),
        AgentTool(
            "nextcloud.calendar.configure",
            "Einen zuvor entdeckten Kalender nach ausdruecklicher Nutzerwahl konfigurieren; Aktualisierungsrecht nur mit --allow-update",
            './scripts/assistant.sh calendar configure --resource "<resource_id>" --allow-update --yes',
            "local-write",
            False,
            "explicit-user-calendar-selection",
        ),
        AgentTool(
            "nextcloud.tasks.discover",
            "Erreichbare Nextcloud-Aufgabenlisten read-only mit VTODO-Unterstuetzung und Serverrechten auflisten",
            './scripts/assistant.sh tasks discover',
            "read",
        ),
        AgentTool(
            "nextcloud.tasks.configure",
            "Eine zuvor entdeckte Aufgabenliste nach ausdruecklicher Nutzerwahl konfigurieren; Aktualisierungsrecht nur mit --allow-update",
            './scripts/assistant.sh tasks configure --resource "<resource_id>" --allow-update --yes',
            "local-write",
            False,
            "explicit-user-task-list-selection",
        ),
    ])

    workspace = settings.nextcloud.workspace
    if workspace.enabled:
        root = workspace.root
        if workspace.allow_mkdir:
            tools.append(
                AgentTool(
                    "nextcloud.workspace.mkdir",
                    "Ordner innerhalb des Nextcloud-Arbeitsbereichs idempotent anlegen",
                    f'./scripts/assistant.sh nextcloud mkdir --path "{root}/<Ordner>"',
                    "write",
                    True,
                    "workspace-create",
                )
            )
        if workspace.allow_write_text:
            tools.append(
                AgentTool(
                    "nextcloud.workspace.write-text",
                    "Neue UTF-8-Textdatei create-only aus stdin im Nextcloud-Arbeitsbereich anlegen",
                    f'printf "%s" "<Inhalt>" | ./scripts/assistant.sh nextcloud write-text --path "{root}/<Datei>.md"',
                    "write",
                    True,
                    "workspace-create-only",
                )
            )
        if workspace.allow_upload:
            tools.append(
                AgentTool(
                    "nextcloud.workspace.upload",
                    "Virengepruefte neue Datei create-only aus der kontrollierten Workspace-Outbox nach Nextcloud hochladen",
                    f'./scripts/assistant.sh nextcloud upload --local "personal_assistant/data/workspace_outbox/<Datei>" --path "{root}/<Ziel>"',
                    "write",
                    True,
                    "workspace-create-only",
                )
            )
        if workspace.allow_move:
            tools.append(
                AgentTool(
                    "nextcloud.workspace.move",
                    "Datei oder Ordner innerhalb des Arbeitsbereichs ohne Ueberschreiben verschieben oder umbenennen",
                    f'./scripts/assistant.sh nextcloud move --source "{root}/<Quelle>" --destination "{root}/<Ziel>"',
                    "write",
                    True,
                    "workspace-organize-no-overwrite",
                )
            )
        tools.append(
            AgentTool(
                "nextcloud.workspace.configure",
                "Nextcloud-Arbeitsbereich und Rechte konfigurieren",
                f'./scripts/assistant.sh setup workspace --root "{root}" --approve-permissions',
                "local-write",
                False,
                "explicit-user-permission",
            )
        )

    direct_calendar = settings.nextcloud.calendar
    if direct_calendar.enabled:
        tools.append(
            AgentTool(
                "nextcloud.calendar.status",
                "Konfiguration sowie Lese-, Anlege- und Aktualisierungsrechte des direkten Nextcloud-Kalenderwerkzeugs pruefen",
                './scripts/assistant.sh calendar status',
                "read",
            )
        )
        if direct_calendar.allow_list:
            tools.extend([
                AgentTool(
                    "nextcloud.calendar.list",
                    "Termine aus dem freigegebenen Nextcloud-Kalender mit UID lesen",
                    './scripts/assistant.sh calendar list --limit 100',
                    "read",
                ),
                AgentTool(
                    "nextcloud.calendar.search",
                    "Termine nach Titel, Ort, Beschreibung oder UID suchen",
                    './scripts/assistant.sh calendar search --query "<Suchbegriff>" --limit 50',
                    "read",
                ),
            ])
        if direct_calendar.allow_update:
            tools.append(
                AgentTool(
                    "nextcloud.calendar.update",
                    "Einen zuvor eindeutig per UID ausgewaehlten Termin nach ausdruecklichem Nutzerauftrag ETag-geschuetzt aktualisieren; Serien nur mit gesonderter Freigabe",
                    (
                        './scripts/assistant.sh calendar update --uid "<UID>" '
                        '--expected-title "<aktueller Titel>" --start "<ISO-8601>" --yes'
                    ),
                    "write",
                    True,
                    "explicit-user-calendar-update-etag-guarded",
                )
            )
        if direct_calendar.allow_create:
            tools.append(
                AgentTool(
                    "nextcloud.calendar.create",
                    "Neuen Termin direkt in den freigegebenen Nextcloud-Kalender eintragen; calendar create akzeptiert kein --yes",
                    (
                        './scripts/assistant.sh calendar create --title "<Titel>" '
                        '--start "<ISO-8601>" --end "<ISO-8601>" '
                        '--location "<Ort>" --description "<Beschreibung>"'
                    ),
                    "write",
                    True,
                    "configured-calendar-create-only",
                )
            )

    direct_tasks = settings.nextcloud.tasks
    if direct_tasks.enabled:
        tools.append(
            AgentTool(
                "nextcloud.tasks.status",
                "Konfiguration sowie Lese-, Anlege- und Aktualisierungsrechte des Nextcloud-Aufgabenwerkzeugs pruefen",
                './scripts/assistant.sh tasks status',
                "read",
            )
        )
        if direct_tasks.allow_list:
            tools.append(
                AgentTool(
                    "nextcloud.tasks.list",
                    "Aufgaben aus der freigegebenen Nextcloud-Aufgabenliste mit UID lesen",
                    './scripts/assistant.sh tasks list --include-completed --limit 100',
                    "read",
                )
            )
        if direct_tasks.allow_update:
            tools.append(
                AgentTool(
                    "nextcloud.tasks.update",
                    "Eine zuvor eindeutig per UID ausgewaehlte Aufgabe nach ausdruecklichem Nutzerauftrag ETag-geschuetzt aktualisieren oder abschliessen; Serien nur mit gesonderter Freigabe",
                    (
                        './scripts/assistant.sh tasks update --uid "<UID>" '
                        '--expected-title "<aktueller Titel>" --due "<YYYY-MM-DD oder ISO-8601>" --yes'
                    ),
                    "write",
                    True,
                    "explicit-user-task-update-etag-guarded",
                )
            )
        if direct_tasks.allow_create:
            tools.append(
                AgentTool(
                    "nextcloud.tasks.create",
                    "Neue Aufgabe in der freigegebenen Nextcloud-Aufgabenliste anlegen",
                    (
                        './scripts/assistant.sh tasks create --title "<Titel>" '
                        '--due "<YYYY-MM-DD oder ISO-8601>" --priority <0-9> '
                        '--description "<Beschreibung>"'
                    ),
                    "write",
                    True,
                    "configured-tasks-create-only",
                )
            )

    direct_contacts = settings.nextcloud.contacts
    if direct_contacts.enabled:
        tools.append(
            AgentTool(
                "nextcloud.contacts.status",
                "Konfiguration sowie Lese-, Anlege- und Aktualisierungsrechte des CardDAV-Kontaktwerkzeugs pruefen",
                './scripts/assistant.sh contacts status',
                "read",
            )
        )
        if direct_contacts.allow_list:
            tools.extend([
                AgentTool(
                    "nextcloud.contacts.list",
                    "Kontakte aus dem freigegebenen CardDAV-Adressbuch lesen",
                    './scripts/assistant.sh contacts list --limit 100',
                    "read",
                ),
                AgentTool(
                    "nextcloud.contacts.search",
                    "Kontakte nach Name, E-Mail, Telefon oder Organisation suchen",
                    './scripts/assistant.sh contacts search --query "<Suchbegriff>" --limit 50',
                    "read",
                ),
            ])
        if direct_contacts.allow_update:
            tools.append(
                AgentTool(
                    "nextcloud.contacts.update",
                    "Einen zuvor eindeutig gesuchten Kontakt per UID nach ausdruecklichem Nutzerauftrag ETag-geschuetzt aktualisieren; kein Loeschen oder Merge",
                    './scripts/assistant.sh contacts update --uid "<UID>" --expected-name "<aktueller Name>" --phone "<neue Telefonnummer>" --yes',
                    "write",
                    True,
                    "explicit-user-contact-update-etag-guarded",
                )
            )
        if direct_contacts.allow_create:
            tools.extend([
                AgentTool(
                    "nextcloud.contacts.create",
                    "Neuen Kontakt nach ausdruecklichem Nutzerauftrag create-only anlegen; bestehende Kontakte werden nicht veraendert",
                    './scripts/assistant.sh contacts create --name "<Name>" --email "<E-Mail>" --phone "<Telefon>" --organization "<Firma>" --yes',
                    "write",
                    True,
                    "explicit-user-contact-create-only",
                ),
                AgentTool(
                    "nextcloud.contacts.from-mail-preview",
                    "Absender und konservativ erkannte Signaturdaten einer eindeutig ausgewaehlten Mail als Kontaktvorschlag pruefen",
                    './scripts/assistant.sh contacts from-mail --folder "<Ordner>" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --dry-run',
                    "read",
                ),
                AgentTool(
                    "nextcloud.contacts.from-mail-create",
                    "Kontakt aus einer eindeutig ausgewaehlten Mail nach Vorschau und ausdruecklichem Nutzerauftrag create-only anlegen",
                    './scripts/assistant.sh contacts from-mail --folder "<Ordner>" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --yes',
                    "write",
                    True,
                    "explicit-user-contact-from-mail-create-only",
                ),
            ])

    deck_orders = settings.nextcloud.deck_orders
    if deck_orders.enabled:
        tools.extend([
            AgentTool(
                "nextcloud.deck.orders.status",
                "Nextcloud Deck-Bestellboard, Rechte und lokale Bestelldatenbank pruefen",
                './scripts/assistant.sh orders status',
                "read",
            ),
            AgentTool(
                "nextcloud.deck.orders.list",
                "Laufende Bestellungen mit Status, Liefertermin, Tracking und Retouren ausgeben",
                './scripts/assistant.sh orders list --limit 100',
                "read",
            ),
            AgentTool(
                "nextcloud.deck.discover",
                "Verfuegbare Nextcloud Deck-Boards read-only auflisten",
                './scripts/assistant.sh deck discover',
                "read",
            ),
            AgentTool(
                "mail.orders.import",
                "Bereits lokal indexierte Mails kontrolliert auf laufende Bestellungen nachklassifizieren",
                './scripts/assistant.sh mail orders-import --limit 500 --dry-run',
                "read",
            ),
            AgentTool(
                "nextcloud.deck.orders.sync",
                "Ausstehende Aktualisierungen agentenverwalteter Bestellkarten nach Nextcloud Deck synchronisieren",
                './scripts/assistant.sh orders sync --limit 500',
                "write",
                True,
                "managed-order-cards-only",
            ),
            AgentTool(
                "nextcloud.deck.orders.due-date-preview",
                "Agentenverwaltete Bestellkarten ohne Faelligkeitsdatum read-only pruefen und plausible Quellen anzeigen",
                './scripts/assistant.sh orders due-date-backfill --limit 500 --dry-run',
                "read",
            ),
            AgentTool(
                "nextcloud.deck.orders.due-date-backfill",
                "Fehlende Faelligkeitsdaten agentenverwalteter Bestellkarten nach ausdruecklichem Auftrag ergaenzen",
                './scripts/assistant.sh orders due-date-backfill --limit 500 --yes',
                "write",
                True,
                "managed-order-cards-missing-due-only",
            ),
        ])

    if settings.mail.invoices.enabled:
        tools.extend([
            AgentTool(
                "mail.invoice-archive",
                "Sicher erkannte und virengepruefte Rechnungs-PDFs create-only ueber ActionPlan in Nextcloud archivieren",
                './scripts/assistant.sh mail run --limit 20',
                "write",
                True,
                "automatic-create-only",
            ),
            AgentTool(
                "assistant.invoices.status",
                "Rechnungs-OCR, Abhaengigkeiten, Jahresregister und offene Prueffaelle anzeigen",
                './scripts/assistant.sh invoices status',
                "read",
            ),
            AgentTool(
                "assistant.invoices.list",
                "Erkannte Rechnungsmetadaten eines Jahres ohne PDF-Inhalt anzeigen",
                './scripts/assistant.sh invoices list --year <YYYY> --limit 100',
                "read",
            ),
            AgentTool(
                "assistant.invoices.review",
                "Unsichere Rechnungsdaten mit Hash und Erkennungsquelle zur Korrektur auflisten",
                './scripts/assistant.sh invoices review --limit 100',
                "read",
            ),
            AgentTool(
                "assistant.invoices.export",
                "Jahresregister als semikolongetrennte UTF-8-CSV lokal erzeugen",
                './scripts/assistant.sh invoices export --year <YYYY>',
                "local-write",
                False,
                "managed-invoice-register",
            ),
            AgentTool(
                "assistant.invoices.export-nextcloud",
                "Jahresregister nach ausdruecklichem Nutzerauftrag create-only nach Nextcloud exportieren",
                './scripts/assistant.sh invoices export --year <YYYY> --nextcloud --yes',
                "write",
                True,
                "explicit-user-export-create-only",
            ),
            AgentTool(
                "assistant.invoices.backfill-preview",
                "Bereits archivierte Rechnungs-PDFs eines Jahres read-only erneut mit Text/OCR auswerten, ohne die Datenbank zu aendern",
                './scripts/assistant.sh invoices backfill --year <YYYY> --limit 500 --dry-run',
                "read",
            ),
            AgentTool(
                "assistant.invoices.backfill",
                "Bereits archivierte Rechnungs-PDFs eines Jahres nach ausdruecklichem Nutzerauftrag neu auswerten und ins Jahresregister uebernehmen",
                './scripts/assistant.sh invoices backfill --year <YYYY> --limit 500 --yes',
                "local-write",
                False,
                "explicit-user-invoice-backfill",
            ),
            AgentTool(
                "assistant.invoices.correct",
                "Unsichere Rechnungsmetadaten anhand des PDF-Hashs nach ausdruecklichem Nutzerauftrag korrigieren",
                './scripts/assistant.sh invoices correct --hash <SHA256> --date <YYYY-MM-DD> --number "<Nr>" --supplier "<Steller>" --category "<Kategorie>" --gross "<Betrag>" --yes',
                "local-write",
                False,
                "explicit-user-correction",
            ),
        ])
    if settings.mail.calendar_mail.enabled:
        tools.append(
            AgentTool(
                "mail.calendar-command",
                "Termin aus einer autorisierten Befehlsmail in den konfigurierten Nextcloud-Kalender eintragen",
                f'Subject: {settings.mail.calendar_mail.subject_prefix} <Terminbeschreibung>',
                "write",
                True,
                "trusted-owner-command",
            )
        )
    return tools
