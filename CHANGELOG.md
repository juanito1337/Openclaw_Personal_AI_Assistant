# Changelog

## 3.4.0-r27.0 – Containerbetrieb mit verifiziertem Backup, Produkttest und Rollback

- Der komplette Agent laeuft aus einem unveraenderlichen Docker-Image auf Basis des offiziellen OpenClaw-Images; Code und Abhaengigkeiten werden von Konfiguration, Geheimnissen und persistenten Laufzeitdaten getrennt.
- Der bestehende Live-Zustand kann einmalig nach /srv/openclaw migriert werden; alte systemd-Writer werden dabei gestoppt, waehrend der urspruengliche Live-Ordner als zusaetzliche Rueckfallebene erhalten bleibt.
- Vor jedem Imagewechsel werden alle Writer gestoppt, SQLite-Datenbanken geprueft, State, Konfiguration und Secrets archiviert, die Pruefsumme validiert und eine testweise Wiederherstellung ausgefuehrt.
- Eine neue Version startet zuerst nur Gateway und Ollama-Prioritaetsproxy, fuehrt danach einen begrenzten Dry-Run und optional einen schreibenden Produktivlauf aus und aktiviert die Hintergrundworker erst nach erfolgreichem Smoke-Test.
- Bei einem Fehler stellt rollback.sh den vorherigen lokalen Datenstand und das vorherige Image automatisch wieder her; fuer externe IMAP-, Nextcloud-, CardDAV- und CalDAV-Aenderungen sind verpflichtende Backup- und Restore-Hooks vorgesehen.
- Mail-, Sync- und Supervisor-Zeitplaene laufen ohne systemd als getrennte Container-Worker und respektieren weiterhin den im Agenten gespeicherten ON/OFF-Sollzustand.
- OPENCLAW_WORKSPACE wird nun in allen relevanten Pfaden respektiert, damit der persistente Workspace ausserhalb des Images liegt und derselbe Quellstand lokal, in Tests und im Container reproduzierbar verwendet wird.
- Eine GitHub-Actions-Pipeline testet den Quellstand und veroeffentlicht getaggte Images mit Commit- und Release-Tag in die private GitHub Container Registry.

## 3.4.0-r26.4 – Agent erkennt Kalender-, Aufgaben- und Kontaktwerkzeuge korrekt

- Der installierte `personal-assistant`-Skill wurde auf den aktuellen Funktionsstand gebracht und beschreibt Kalender, Aufgaben und Kontakte nicht mehr faelschlich als ausschliesslich create-only.
- Bei Fragen nach anstehenden Terminen oder offenen Aufgaben muss der Agent zuerst `calendar status/list` beziehungsweise `tasks status/list` verwenden, statt die Funktion ohne Pruefung zu verneinen.
- Widerspruechliche spaetere Abschnitte in `AGENTS.md` wurden entfernt; die Betriebsanweisung stimmt nun mit R26.3-Backend, CLI und Tool-Registry ueberein.
- Kalender- und Aufgaben-Konfigurationswerkzeuge zeigen fuer den ausdruecklich gewuenschten Schreibzugriff konsistent `--allow-update --yes`.
- CLI-Hilfetexte unterscheiden klar zwischen neuem Anlegen und ETag-geschuetztem Aktualisieren bestehender Objekte.
- Ein Regressionstest verhindert kuenftig veraltete Skill-Versionen, create-only-Falschbehauptungen und fehlende List-/Update-Werkzeuge.
- Nach der Installation muss der OpenClaw-Gateway-Prozess neu gestartet oder eine neue Agentensitzung begonnen werden, damit der aktualisierte Skillkontext geladen wird.

## 3.4.0-r26.3 – ETag-geschuetztes Bearbeiten von Kalenderterminen und Aufgaben

- `calendar list` und `calendar search` liefern bestehende VEVENTs mit exakter UID; `calendar update` bearbeitet nur einen eindeutig ausgewaehlten Eintrag.
- `tasks update` aendert eine bestehende VTODO-Aufgabe oder setzt sie kontrolliert auf `COMPLETED`; Titel, Start, Faelligkeit, Beschreibung, Prioritaet, Kategorien, Status und Fortschritt sind einzeln aktualisierbar.
- Kalender- und Aufgabenupdates muessen bei der jeweiligen Konfiguration mit `--allow-update --yes` bewusst aktiviert werden und benoetigen live bestaetigte CalDAV-Update-Rechte.
- Jeder PUT verwendet `If-Match` mit der unmittelbar zuvor gelesenen ETag. Parallele Aenderungen fuehren zu einem sichtbaren Konflikt statt zu stillem Ueberschreiben.
- Teilaktualisierungen erhalten UID, Teilnehmer, Alarme, Zeitzonen, Wiederholungsregeln, Ausnahmen und unbekannte iCalendar-Eigenschaften.
- Wiederkehrende Termine und Aufgaben sind standardmaessig gesperrt und benoetigen eine gesonderte ausdrueckliche Serienfreigabe.
- Jede Aenderung laeuft als auditierter ActionPlan mit exakter UID sowie optionalen Erwartungswerten. Loeschen, Massenbearbeitung und Verschieben zwischen Sammlungen bleiben verboten.

## 3.4.0-r26.2 – ETag-geschuetztes Bearbeiten von CardDAV-Kontakten

- `contacts update` bearbeitet genau einen zuvor per `contacts search` oder `contacts list` gefundenen Kontakt anhand seiner exakten UID.
- Schreibzugriffe muessen bei der Adressbuchkonfiguration mit `--allow-update --yes` bewusst aktiviert werden und benoetigen live bestaetigte CardDAV-Update-Rechte.
- Der aktuelle Kontakt wird unmittelbar vor der Aenderung gelesen; der PUT verwendet `If-Match` mit der aktuellen ETag und bricht bei einer parallelen Aenderung mit Konflikt ab.
- Teilaktualisierungen ersetzen nur explizit genannte Felder. UID, Anschrift, Geburtstag, Foto und unbekannte vCard-Erweiterungen bleiben erhalten.
- Wiederholte `--email`- oder `--phone`-Optionen ersetzen die jeweilige komplette Liste; `--clear-*` leert das Feld bewusst.
- E-Mail-Dubletten, unerwartet geaenderte Ausgangsdaten und unbeabsichtigte Namenskollisionen werden vor dem Schreibzugriff blockiert.
- Jede Kontaktaktualisierung ist ein auditierter, freigabepflichtiger ActionPlan. Kontakt-Loeschen, Merge und automatische Massenupdates bleiben verboten.

## 3.4.0-r26.1 – Robuste Ollama-JSON-Antworten und konservative Batch-Laufzeiten

- `done_reason = length` wird als abgeschnittene Modellantwort erkannt und getrennt von normal fehlerhaftem JSON behandelt.
- Einzelantworten erhalten genau einen Schema-Retry mit 1024 statt 512 Ausgabetokens.
- Abgeschnittene Batches werden in kleinere Gruppen geteilt; derselbe grosse Batch wird nicht nochmals mit `format=json` generiert.
- Standardmaessig werden hoechstens drei Mails pro Batch und nur eine Mail-Modellgruppe gleichzeitig verarbeitet.
- Split-Retries erhalten 300 Sekunden und koennen bis zu sicheren Einzelanfragen heruntergeteilt werden.
- Der Hintergrund-Burst des Mail-Agenten ist deaktiviert; der zweite Prioritaetsslot bleibt fuer interaktive Agentenanfragen verfuegbar.
- Die R26-Rechnungsablage und das Nextcloud-Jahresregister bleiben unveraendert enthalten.

## 3.4.0-r26 – Zuverlaessige Rechnungsablage und Nextcloud-Jahresregister

- Native PDF-Textschichten haben Vorrang; OCR wird nur noch als Fallback bei unbrauchbarem Text oder unsicherem Rechnungsdatum ausgefuehrt.
- Ein sicher erkanntes Rechnungsdatum bestimmt die Nextcloud-Ablage unter Jahr und Monat unabhaengig von fehlenden Zusatzdaten.
- Nur ein unsicheres Rechnungsdatum fuehrt nach `Pruefen`; unvollstaendige Betrags-, Firmen-, Nummern- oder Kategoriedaten bleiben als CSV-Pruefstatus im normalen Jahresordner.
- Pro Jahr existiert ausschliesslich `<Rechnungsordner>/<YYYY>/Rechnungen_<YYYY>.csv` in Nextcloud; eine produktive lokale Registerkopie wird nicht mehr erzeugt.
- Jede neue, korrigierte oder erneut erkannte Dublettenrechnung synchronisiert das Jahresregister. Fehler werden nicht still uebersprungen.
- Die kontrollierte CSV-Ersetzung prueft Pfad, Jahr, Schema und SHA-256 und verwendet ETag-Vorbedingungen gegen parallelen Datenverlust. Alle anderen Ueberschreibverbote bleiben bestehen.
- Die R26-Konfigurationsmigration aktiviert Metadaten und Jahresregister, erzwingt den Semikolon-Standard und entfernt den veralteten lokalen `register_dir`-Eintrag atomar.
- Installer und Rollback sichern den bisherigen Stand und entfernen keine produktiven Rechnungsdaten oder PDFs.

## 3.4.0-r25 – Prioritaetsgesteuerter Zwei-Slot-Betrieb fuer Gemma

- Zwei echte parallele Modellslots im lokalen Ollama-Prioritaetsproxy.
- Automatische Mailarbeit nutzt normalerweise hoechstens einen Slot; der zweite Hintergrundslot ist nur im Aufholbetrieb und ohne Vordergrundverkehr zulaessig.
- Interaktive und normale Agentenanfragen werden vor neu beginnender Hintergrundarbeit eingeplant.
- Unabhaengige Mailgruppen koennen parallel klassifiziert werden; Ergebnisreihenfolge und sichere Fallbacks bleiben stabil.
- Standard-Mailkontext 16384 Tokens, `keep_alive = "1h"`, Einzel-Timeout 600 Sekunden und Batch-Timeout 300 Sekunden.
- Atomare, idempotente R25-Konfigurationsmigration mit vollstaendigem Rollback.
- Telemetrie zeigt aktive Slots, maximale Slotzahl und beobachtete Parallelitaet.

## 3.4.0-r24 – Begrenzte Ollama-Laufzeiten und belastbare Mail-Telemetrie

- Queue-Wartezeit und Modelllaufzeit werden getrennt begrenzt und ausgewertet.
- Ein Timeout fuehrt hoechstens zu einem begrenzten Split-Versuch; rekursive Langzeit-Retries sind ausgeschlossen.
- Der automatische Mail-Drain besitzt 40 Minuten Gesamtbudget mit drei Minuten Sicherheitsreserve und beendet sich kontrolliert vor `TimeoutStartSec=50min`.
- Fortschritt, laufende Modellversuche und Abbruchgrund werden bereits waehrend des Laufs datensparsam gesichert.
- Lebende Inflight-Laeufe werden nicht mehr als unterbrochen gewertet oder von Parallelstarts ueberschrieben.
- Auswertungen deduplizieren alte Mehrfacheintraege derselben `run_id`.
- Der Prioritaetsproxy meldet `queue_timeout` und `upstream_timeout` maschinenlesbar und begrenzt Upstream-Aufrufe standardmaessig.

## 3.4.0-r23.4 - CardDAV-Kontakte lesen und create-only aus Mails anlegen

- `contacts discover` listet erreichbare CardDAV-Adressbuecher read-only mit stabiler `resource_id` und Live-Rechten.
- Ein Adressbuch wird nur nach ausdruecklicher Auswahl konfiguriert; Kontakte koennen live aufgelistet und durchsucht werden.
- `contacts create` legt neue vCards mit `If-None-Match: *` create-only an; Update und Loeschen bleiben gesperrt.
- `contacts from-mail --dry-run` liest genau eine ausgewaehlte Mail, scannt sie lokal und erzeugt einen konservativen Kontaktvorschlag aus Absender und Signatur.
- Exakte E-Mail-Dubletten werden nicht erneut angelegt, Namenskollisionen erfordern Freigabe und no-reply-Absender werden blockiert.

## 3.4.0-r23.3 - Garantierte plausible Deck-Faelligkeitsdaten

- Jede aus einer Mail erzeugte agentenverwaltete Bestellkarte erhaelt ein nichtleeres `dueDate`.
- Prioritaet: Retourenfrist, erwartete Lieferung/Zustellung, Bestelldatum, Mail-Eingangsdatum und erst zuletzt das lokale Verarbeitungsdatum.
- Datumsquelle und Konfidenz werden in der lokalen Bestelldatenbank sowie im verwalteten Kartenbereich dokumentiert.
- Bereits vorhandene plausible Deck-Daten bleiben bei spaeteren Mailereignissen unveraendert.
- Neue Vorschau und kontrollierter Backfill ergaenzen nur fehlende Daten agentenverwalteter Karten.

## 3.4.0-r23.2 - Deck-Datum aus dem echten Mail-Eingang

- Routine-Mail-Karten im Bestell-Deck verwenden den Eingangszeitpunkt der ersten Quellmail als Deck-Datum.
- Mail-Header-Datum und serverseitiger Eingangszeitpunkt werden getrennt behandelt.
- Spaetere Statusmails veraendern das urspruengliche Deck-Datum nicht.
- Bestehende Bestelldatenbanken werden additiv migriert.

## 3.4.0-r23.1 – Produktionsinstaller ohne pytest-Abhängigkeit

- Produktive Installation benötigt kein pytest mehr.
- Standardbibliotheks- und Laufzeit-Smoke-Checks ersetzen die verpflichtende Entwickler-Testsuite.
- Optionale Volltests bleiben über `OPENCLAW_INSTALL_RUN_TESTS=1` verfügbar.
- Kumulativ installierbar von R22.4 und R23.

## 3.4.0-r23 – Sichere Rechnungs-OCR und fortlaufendes Jahresregister

- PDF-Textschicht zuerst; Tesseract-OCR nur bei niedriger Textqualitaet, fehlenden Pflichtfeldern oder geringer Konfidenz.
- Rechnungsdatum wird nur aus expliziten Rechnungsdatumsfeldern bestaetigt; Leistungs-, Liefer-, Bestell- und Faelligkeitsdatum werden ausgeschlossen.
- Text/OCR-Widersprueche bei Datum, Nummer oder Bruttobetrag erzwingen `Pruefen` statt stiller Uebernahme.
- Atomisches Jahresregister als UTF-8-BOM/CSV mit Semikolon, Dezimalkomma, Rechnungsnummer, Rechnungssteller, Kategorie, Netto, USt und Brutto.
- Kontrollierter Nextcloud-Backfill fuer bestehende Archive mit Read-Scope, Virenscan, Dry-Run und ausdruecklichem `--yes`.
- Registrierte Agentenwerkzeuge fuer Status, Liste, Prueffaelle, Korrektur, lokalen Export, create-only Nextcloud-Export und Backfill.

## 3.4.0-r22.4 – CalDAV-Discovery fuer Kalender und Aufgabenlisten

- `calendar discover` listet erreichbare VEVENT-Kalender read-only mit stabiler `resource_id`, Komponenten und Serverrechten.
- `tasks discover` listet erreichbare VTODO-Aufgabenlisten read-only; reine Aufgabenlisten und kombinierte Kalender werden korrekt unterschieden.
- `calendar configure --resource ... --yes` und `tasks configure --resource ... --yes` konfigurieren nur eine zuvor live entdeckte Ressource.
- Vor der Konfiguration werden VEVENT/VTODO sowie Lese- und Anlegerechte des Servers geprueft.
- Discovery veraendert weder `tools.toml` noch `resources.toml`; die Auswahl bleibt eine ausdrueckliche Nutzerentscheidung.

## 3.4.0-r22.3 – Robustes Nicht-Spam-Gegenlernen und Herkunftsnachweis

- INBOX-Restore wird mit Herkunft und vorherigem Status als explizites Nicht-Spam-Feedback gespeichert.
- Nicht-Spam wirkt nur auf dasselbe Absender-/Betreffmuster, nicht pauschal auf alle Mails eines Absenders.
- Spam-Gegenbelege blockieren erneute Spamzuordnung, ohne Routine- oder Wichtig-Muster zu ueberschreiben.
- Neues read-only Werkzeug `mail learning not-spam` zeigt Herkunft und Feedback-ID ohne Mailinhalte.
- Der Spam-Restore-Regressionsfall laeuft mit explizit sauberem Antivirus-Test reproduzierbar durch.

## 3.4.0-r22.2 – Sichere Musterentscheidungen und belastbare Originalklassifikation

- Routine/Spam erst nach zwei konsistenten Mustertreffern.
- Relevante Muster werden bereits nach einem eindeutigen Treffer schuetzend beruecksichtigt.
- Unveraenderlicher Originalentscheidungs-Snapshot vor Nutzerkorrekturen.
- Keine scheinbare 100-Prozent-Modellgenauigkeit aus korrigierten Altzeilen.
- Kategorienmetriken, Konfusionsmatrix und stabile Konflikt-IDs.

## 3.4.0-r22.1 – Robuste Migration bestehender Lernhistorie

- Fehlende Lernspalten werden vor dem Index `idx_feedback_subject_pattern` angelegt.
- Vorhandene `feedback`-Datensaetze bleiben erhalten; Betreffmuster und Korrekturordner werden aus den bisherigen Feldern rueckbefuellt.
- Der Installer erstellt ein konsistentes SQLite-Backup und prueft die Migration zuerst auf einer Datenbankkopie.
- `PRAGMA quick_check`, Spalten- und Indexpruefung muessen erfolgreich sein, bevor Dienste neu gestartet werden.
- Neuer Regressionstest bildet eine produktive R20.2/R22-Altdatenbank mit `PRAGMA user_version=1` nach.

## 3.4.0-r22 – Lernqualitaet, Konfliktanalyse und pseudonymisierte Evaluation

- Chronologische Walk-forward-Evaluation verhindert, dass eine Korrektur sich selbst als Treffer bewertet.
- Die neue Musterlogik wird gegen die alte pauschale Absenderlogik und gespeicherte Originalklassifikationen verglichen.
- Gefaehrliche Fehler wie relevante Mail als Routine/Spam und Spam als relevant werden separat gezaehlt.
- Ein aggregierter Qualitaetsbericht zeigt Datenbasis, Abdeckung, Genauigkeit, gemischte Absender und Musterkonflikte ohne Mailinhalte.
- Optionaler lokaler Datensatzexport pseudonymisiert Absender, Domain, Betreffmuster und Message-Key per nicht gespeichertem Export-Schluessel.
- Der Export enthaelt keine Mailtexte, Rohbetreffe, E-Mail-Adressen oder Message-IDs und wird mit Dateimodus 0600 geschrieben.


## 3.4.0-r21 – Musterbasiertes Mail-Lernen und dynamische Korrekturordner

- Nutzerkorrekturen wirken auf Absender plus normalisiertes Betreffmuster statt pauschal auf den gesamten Absender.
- Absender mit verschiedenen korrigierten Mailtypen werden als gemischt erkannt und nicht durch eine Absenderregel erzwungen.
- Aehnliche Korrekturen werden mit Typ-Label und privacy-sicheren Merkmalen als nachvollziehbare Modellbeispiele bereitgestellt.
- Dynamische Korrektur-Unterordner koennen nur nach ausdruecklichem Nutzerauftrag angelegt oder als Lernquelle deaktiviert werden.
- Neue registrierte Agentenwerkzeuge zeigen Feedback, gemischte Absender, Musterkonflikte und Lernordner an.
- Es findet kein Modell-Fine-Tuning statt; alle Lernentscheidungen bleiben sofort nachvollziehbar und rueckgaengig.


## 3.4.0-r20.2 – Verbindliche Versions- und Updatekenntnis

- `RELEASE.json` ist die einzige maschinenlesbare Laufzeitquelle fuer die installierte Version.
- Neue registrierte Befehle `assistant.sh version --verify`, `--history` und `--since`.
- `assistant status` und `assistant doctor` liefern die verifizierte Release-Identitaet.
- AGENTS.md verpflichtet den Agenten, Versionsangaben niemals aus Erinnerung oder Paketnamen abzuleiten.
- Installer erkennt den vorherigen Stand, setzt Installationszeit/ID erst nach erfolgreichem Dateiaustausch und sendet ein OpenClaw-Updateereignis.
- README, AGENTS.md und CHANGELOG werden gegen die aktuelle Release-Version geprueft.

## 3.4.0-r20.1 – Stabiler Wiederanlauf und vollstaendige Agentensteuerung

- Mail-Timer und Mail-Interface werden vor dem sicheren Wiederherstellungs-Dry-Run geordnet gestoppt.
- Echte Prozesssperre wird geprueft; temporaere Lock-Konflikte erhalten keinen 30-Minuten-Cooldown.
- Ollama- und Performancebefehle sind als stabile `assistant.sh`-Werkzeuge registriert.
- Installer stellt Timer wieder her, ohne Supervisor und Mail-Interface gleichzeitig zu starten.

## 3.4.0-r20 – Zentrale Ollama-Priorisierung

- Lokaler, nur an Loopback gebundener Ollama-Prioritaetsproxy fuer alle Agentenfunktionen.
- Direkte OpenClaw-Anfragen und explizite Mail-Tool-Aufrufe erhalten Vorrang vor automatischer Mailklassifikation.
- Single-Flight-Ausfuehrung schuetzt das grosse lokale Modell vor konkurrierenden Kontexten und VRAM-Ueberlastung.
- Nicht-praemptiv: laufende Modellantworten werden nicht abgebrochen; nur neue Aufrufe werden geordnet.
- Starvation-Schutz, Queue-Limits, Zeitgrenzen, Streaming-Durchleitung und defensive Fail-open-Logik.
- Supervisor ueberwacht Proxy und Ollama-Upstream; systemd startet den Proxy nach Fehlern neu.
- R18-Telemetrie erfasst nun zusaetzlich die Wartezeit in der Ollama-Warteschlange.
- Technische Unit- und Skriptnamen bleiben aus Kompatibilitaetsgruenden unveraendert; fachlich ist `mail-agent.service` das Mail-Interface des OpenClaw-Agenten.

## 3.4.0-r17 – Automatische sichere Mail-Freigabe

- Maschinenlesbarer `mail-agent production-check` fuer die produktive Sicherheitsfreigabe.
- Supervisor erkennt Exit-Status 4 durch fehlenden oder veralteten Dry-Run-Fingerprint.
- Automatischer, auf fuenf Mails begrenzter Dry-Run nur wenn alle Pflicht-Health-Checks erfolgreich sind.
- JSON-Ergebnis, Fehlerfreiheit und Production-Gate werden vor dem normalen Dienststart erneut geprueft.
- Kein `--force`; keine automatische Aenderung von Zugangsdaten, Regeln, Berechtigungen oder Virenschutz.
- 30-Minuten-Cooldown nach fehlgeschlagenem Auto-Dry-Run gegen Wiederholungsschleifen.
- OpenClaw-Ereignis meldet sowohl erfolgreiche als auch fehlgeschlagene automatische Wiederherstellung.

## 3.4.0-r16 – Job-Supervisor und Selbstheilung

- Persistenter Sollzustand fuer Hintergrundjobs mit `ON`, bewusstem `OFF` und unerwartetem Fehlerzustand.
- Registrierte Befehle `jobs status/check/alerts/on/restart/off`.
- Fuenf-Minuten-Supervisor fuer systemd-Timer und fehlgeschlagene Dienste.
- Sofortiges OpenClaw-Systemereignis bei neuen oder behobenen Betriebsalerts, ohne wiederholten Alarm fuer unveraenderte Fehler.
- Heartbeat-Vertrag fuer verbindliche Fehlerdiagnose und Meldung statt unbelegter Arbeitsbehauptungen.
- Produktive Mail-Preflight-Reparatur fuer ausschliesslich fehlende konfigurierte `Agent/...`-Ordner.
- Expliziter Standard-ON-Schalter installiert fehlende paketierte User-Units und startet Mailautomatik sicher.

## 3.4.0-r14 – Nextcloud Deck Bestellmonitor

- Offizieller Nextcloud-Deck-REST-Connector fuer Boards, Spalten und Karten.
- Lokale Bestelldatenbank mit Ereignishistorie, Deduplizierung und Sync-Retry.
- Mailklassifizierung extrahiert Bestellbestaetigung, Versand, Tracking, Zustellung, Retoure, Erstattung und Storno.
- Automatische Aktualisierung ausschliesslich agentenverwalteter Deck-Karten.
- Historischer Backfill aus lokalen Mail-Snapshots mit Dry-Run.
- Neue Agentenwerkzeuge `deck discover`, `orders status/list/sync` und `mail orders-import`.
- Keine Loeschungen, keine Board-Freigaben und keine Aenderung manueller Karten.

## 3.4.0-r12

- Added registered direct `calendar status` and `calendar create` agent tools.
- Added create-only event generation with ISO-8601 validation and Europe/Berlin defaults.
- Added narrow configured-tool approval without weakening the global calendar policy.
- Added deterministic UID/idempotency and remote CalDAV verification before duplicate claims.
- Kept event update, overwrite and delete hard-disabled.
- Added direct calendar setup, Doctor evidence, documentation and regression tests.
- Fixed central tool rewriting so ClamAV configuration is preserved during later setup changes.

## 3.4.0-r7

- added provider spam/quarantine folders as first-class mail sources
- made every normal mail run reserve a bounded share for quarantine review
- added explicit `assistant.sh mail spam-review` and central source setup
- kept obvious spam and ordinary routine mail in provider quarantine
- rescued relevant, appointment, uncertain and unambiguous invoice messages
- treated manual restore from provider spam to INBOX as not-spam feedback
- added source-folder diagnostics, productive-run gating and regression tests

## 3.4.0-r6

- defined the Personal Assistant as the only agent and the mail pipeline as a registered tool/subsystem
- added a machine-readable tool registry and `assistant.sh tools list`
- exposed mail operations through `assistant.sh mail ...`
- added restricted Nextcloud file listing without a local filesystem mount
- routed invoice PDF uploads through ActionPlan, policy, idempotency and audit
- added create-only invoice archive under `Assistent/Rechnungen/YYYY/MM`
- added trusted owner calendar-command mails with sender allowlist and exact subject prefix
- preserved explicit calendar create permission during later Nextcloud discovery
- added central `personal_assistant/tools.toml` configuration and setup command
- added regression tests for tool visibility, invoice ActionPlans, calendar command approval and discovery permissions

## 3.4.0

- introduced Personal Assistant core while retaining the stable mail agent
- added central secrets file and compatibility loading of legacy mail secrets
- added dynamic Resource Registry and machine-readable capability manifest
- added policy engine with hard-denied and approval-required actions
- added ActionPlan/outbox storage, idempotency, approval, execution, and audit
- added direct restricted Nextcloud WebDAV, CardDAV, CalDAV, and VTODO providers
- added Nextcloud discovery and resource persistence
- added incremental SQLite FTS5 knowledge index
- added mail search snapshots for newly processed messages
- added indexing for mail metadata, Nextcloud files, contacts, and calendar events
- added DOCX/XLSX/text extraction and optional local `pdftotext` extraction
- added central setup, resources, search, settings, actions, and diagnostics CLI
- added independent optional knowledge-sync systemd timer
- retained all 3.3.1 mail safety and delivery-uncertain protections

## 3.4.0-r8 - Nextcloud workspace tools

- Treats `Assistent/` as the durable Personal-Assistant workspace.
- Adds registered `nextcloud mkdir`, `write-text`, `upload` and `move` tools.
- Restricts uploads to `personal_assistant/data/workspace_outbox/`.
- Adds `files.mkdir` and `files.move` ActionPlan executors and policy checks.
- Adds an explicit `organize` resource permission for move/rename operations.
- Preserves create-only semantics and WebDAV `Overwrite: F`.
- Keeps delete, overwrite and sharing hard-denied.
- Adds workspace setup, Doctor status, documentation and regression tests.

## 3.4.0-r9

- Nextcloud-Workspace-Idempotenz gegen den realen Remote-Zustand verifiziert.
- Fehlende, lokal bereits abgeschlossene create-only Ziele werden sicher erneut erzeugt.
- Inhaltskonflikte werden ohne Ueberschreiben als Fehler markiert.
- Moves pruefen Quelle und Ziel vor einer Dublettenmeldung.

## 3.4.0-r10

- added evidence-based Personal Assistant monitoring with a 0-100 operational score
- separated core, mail reliability, classification quality, Nextcloud freshness,
  ActionPlan, knowledge-index and runtime component scores
- added explicit confidence, evidence, limitations and recommendations
- added local snapshot history and trend detection in a separate monitoring database
- added optional live Nextcloud latency and systemd unit checks
- exposed monitoring through the central agent tool registry
- added an optional hourly monitoring systemd timer
- kept monitoring read-only except for local snapshot storage

## 3.4.0-r11

- Added shared host ClamAV integration with `clamdscan` and optional `clamscan` fallback.
- Added fail-closed scanning of complete RFC822 mail and every physical attachment.
- Added a second antivirus gate directly before invoice uploads to Nextcloud.
- Added antivirus scanning for controlled Nextcloud workspace uploads and generic upload ActionPlans.
- Added `Agent/Virusverdacht` quarantine folder without automatic deletion.
- Added SHA-256 plus scanner/signature-aware scan cache and local audit database.
- Added agent tools `security antivirus doctor`, `self-test`, and `scan`.
- Added host installation helper and monitoring evidence for antivirus health.

## 3.4.0-r13

- direktes Nextcloud-Aufgabenwerkzeug auf Basis von CalDAV VTODO
- `tasks status`, `tasks list` und create-only `tasks create`
- Fälligkeit, Start, Priorität, Beschreibung und Kategorien
- Live-Prüfung der VTODO-Unterstützung des konfigurierten Kalenders
- ActionPlan, Policy, Audit, deterministische UID und Remote-Dublettenprüfung
- Löschen und Überschreiben bleiben verboten


## 3.4.0-r15 – Kontrolliertes Mail-Verschieben
Der Agent darf nach expliziter Einrichtung einzelne, eindeutig per Mail-ID ausgewaehlte Nachrichten zwischen vorhandenen Ordnern verschieben. Papierkorb, Spam/Junk, Virusverdacht, Loeschen, EXPUNGE und Ordneraenderungen bleiben gesperrt.

## r18 - Performance-Telemetrie ohne Funktionsaenderung

- Privacy-sichere Laufzeitmessung fuer Mail-Agent-Laeufe eingefuehrt.
- Ollama-Servermetriken (`total_duration`, `load_duration`, Prompt-/Eval-Tokens und -Dauer) werden erfasst.
- Phasen fuer Preflight, Mailabruf, Export, Virenscan, Parsing, Klassifikation, Routing, Verschieben und ausgewaehlte Datenbankschreibvorgaenge werden gemessen.
- Externe Prozesse werden ausschliesslich als feste Kategorien ohne Argumente protokolliert.
- Neue Auswertung: `./scripts/mail-agent.sh performance --limit 20`.
- Telemetrie ist fail-open, lokal, auf 20 MB begrenzt und veraendert keine produktive Entscheidung.

- Rechnungssteller werden nur aus expliziten Feldern oder plausiblen Firmenkoepfen uebernommen; Woerter wie `Lieferant` innerhalb eines Firmennamens gelten nicht als Feldbezeichner.
