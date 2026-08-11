# Changelog

## Unreleased – M8 End-to-End-Recovery, Skills und Releaseabschluss

- Der neue read-only Befehl `portfolio mapping suggest --isin` sucht die exakte
  ISIN bei EODHD und laesst Ollama nur einen providerseitig gelieferten
  Kandidaten samt allowlistetem MIC auswaehlen. Erfundenes Modelloutput wird
  verworfen; gespeichert wird weiterhin erst nach separater ausdruecklicher
  Freigabe ueber `portfolio watchlist add ... --yes`.

- Fehlgeschlagene Portfolio-Statusabfragen zeigen Konfigurationsblocker wie einen
  fehlenden EODHD-Schluessel bereits direkt an. Der Agent muss vor seiner Antwort
  Doctor und tiefen Jobcheck auswerten, alle unabhaengigen Ursachen nennen und
  eine konkrete genehmigungspflichtige Mapping-Aktion anbieten. Holdings koennen
  leere Symbol/MIC-Felder nicht mehr als bestaetigtes Mapping ausgeben.

- Unqualifizierte Versionsfragen werden im Agenten- und Skillvertrag eindeutig
  auf das verifizierte Produktrelease des OpenClaw Local Personal Assistant
  geroutet. Die eingebettete OpenClaw-Core-/Plugin-/CLI-Version darf nicht mehr
  als Produktidentitaet ausgegeben werden; ein CLI-Regressionstest belegt die
  Release-Antwort aus `RELEASE.json`.

- Container-Runtime-Wurzeln werden vor der alten Workspace-Pfadpruefung
  aufgeloest. Dadurch blockieren bereits migrierte Pfade unter
  `/var/lib/openclaw` weder Portfolio noch Doctor und Jobs. Der Agent fuehrt
  registrierte Portfolio-Mappings, Kurspruefungen und freigegebene Jobaktionen
  selbst aus, statt Jan `docker exec`-Befehle zu delegieren.
- Das Gateway ueberlagert die persistenten Mail- und Personal-Assistant-
  Konfigurationsordner read-only. Werkzeugfehler koennen dadurch nicht mehr per
  Datei-/Shell-Fallback in `tools.toml` umgangen werden; ausdrueckliche Setups
  bleiben auf die kurzlebige `agent-cli`-Rolle begrenzt. Layout-Init repariert
  ausschliesslich die fuenf mount-eigenen Datenpfade und erhaelt alle fachlichen
  Ressourcen- und Berechtigungswerte (ADR-0015).
- Layout-Init setzt bei einem vorhandenen Ollama-Provider fehlende explizite
  Modell-/Agenten-Timeouts auf 1800/3600 Sekunden, ohne Betreiberwerte zu
  ueberschreiben. Der registrierte Ollama-Livecheck verwendet aus Gateway- und
  Workerrollen den privaten Proxy-Health-Endpunkt und benoetigt dort keine
  serverseitige Upstream-Umgebung mehr.
- Der automatische Rollback normalisiert von Docker als root erzeugte
  Bind-Mount-Quellpfade vor dem lokalen `rsync`-Restore. Ein fehlgeschlagener
  Kandidaten-Smoke bleibt dadurch nicht mehr vor dem Neustart des verifizierten
  vorherigen Stacks stehen.
- Ein hermetischer, intern vernetzter Compose-Stack prueft Fake-IMAP/SMTP,
  WebDAV/CardDAV/CalDAV, Ollama, Marktdaten, ClamAV-Fixtures, ETag-Konflikt,
  Netzwerkverlust, Containercrash und einen exklusiven Mailwriter ohne produktive
  Konten, Secrets, Hostports oder Mounts.
- Der lokale Recovery-Drill sichert und restauriert r26.1, den aktuellen Stand und
  ein fehlgeschlagenes Upgrade bytegenau; RTO/RPO und die nicht abgedeckten
  Remotegrenzen werden maschinenlesbar und im Recoveryvertrag dokumentiert.
- Ein fehlender externer Restore-Hook bricht vor dem Containerstop ab. Scheitert
  ein vorhandener Hook zur Laufzeit, startet der verifizierte alte lokale Stand
  trotzdem, waehrend der unklare Remotezustand und Rollbackfehler sichtbar bleiben.
- `AGENTS.md` ist auf dauerhafte Invarianten konzentriert. Der kurze
  Personal-Assistant-Skill routet in Domaenenreferenzen; alle 125 Toolbefehle,
  Modi, Wirkungen, Approvals, Version und Testanker werden aus dem typisierten
  Katalog generiert und in CI gegen Drift geprueft.
- Releasecheckliste, Single-Writer-Canary und ADR-0012 schliessen die technische
  Roadmap ab. Ein produktives Deployment und ein echter externer Snapshot-Restore
  bleiben separate ausdrueckliche Operationsauftraege.
- Die Publish-CI trennt Rollen-, Supply-Chain-, M3- und M4-Abnahmen in einzeln
  benannte Schritte. Dynamische Vertragsfehler nennen die verletzte Invariante;
  die SIGTERM-Abnahme synchronisiert auf einen nachweisbar installierten Handler.
- M3 inspiziert restriktiven containerseitigen State UID-unabhaengig ueber einen
  read-only Pruefmount, statt CI-Hostzugriff mit einem fehlenden Pfad zu verwechseln.
- Das M4-Signalfixture macht nur seinen oeffentlichen read-only Layoutmarker fuer
  eine abweichende Image-UID lesbar und prueft diesen Fall als echtes Skriptverhalten.
- Die Memory-Abnahme unterscheidet Kernel-OOM-Kill, kontrollierten Allocator-
  `MemoryError` und fachfremde Prozessfehler, ohne die 64-MiB-Grenze zu lockern.
- Die Imagefreigabe publiziert SLSA-v1-Provenance und SPDX-SBOM nun Registry-nativ
  mit keyless Cosign. Damit bleibt der Attestierungsvertrag auch im privaten,
  benutzereigenen GitHub-Repository vollstaendig pruefbar, ohne die dort nicht
  verfuegbare GitHub-Attestation-API oder eine unnoetige Jobberechtigung.
- Das Deployment benoetigt kein unbelegtes Host-Cosign mehr: Signaturen und
  Attestierungen werden mit dem digest-gepinnten, read-only Cosign-Container aus
  dem Supply-Chain-Lock und read-only Registry-Anmeldung geprueft.
- Die Memory-Abnahme protokolliert den aufgebauten Speicherdruck schrittweise und
  erkennt einen runtime-spezifischen SIGKILL nur zusammen mit dem exakten Limit,
  leerem Docker-State-Fehler und unvollstaendiger Allokation als cgroup-Nachweis.
- Das Deployment gleicht den persistierten Runtime-Typ vor Aenderungen mit
  tatsaechlich laufenden Docker-/systemd-Writern ab, verifiziert den Writerstop und
  vererbt den automatischen Rollback auch in Compose-Shellfunktionen.
- Layoutmigrationen komprimieren getrennte SQLite-Datenbanken explizit auf dem
  State-Dateisystem, ersetzen sie erst nach Quick-Check atomar und entfernen
  bekannte unveroeffentlichte Stagingreste nach einem Fehler.
- Der Uebergang vom Legacy-systemd-Betrieb deaktiviert aktivierte Writer-Timer
  nun explizit, statt sie nur zu stoppen. Scheitert danach das vorbereitende
  Backup, wird die zuvor aufgezeichnete Aktivierungsmenge wiederhergestellt.
- Der Ollama-Prioritaetsproxy akzeptiert seinen nicht publizierten Wildcard-
  Listener ausschliesslich in der Containerrolle; Hostbetrieb und beliebige
  Nicht-Loopback-Adressen bleiben gesperrt. Der Image-Smoke startet nun den echten
  Proxy mit einem isolierten Fake-Upstream und prueft dessen Healthcheck.
- Brave und Signal werden versions- und integrity-gepinnt in das signierte
  Runtime-Image gebaut. Die Legacy-Migration ersetzt ihre Hostpfade durch
  read-only Imagepfade, synchronisiert den generierten Pluginindex auf den exakten
  Imagevertrag und entfernt nur im Staging alte ausfuehrbare npm-Payloads;
  unbekannte State-Plugins brechen fail-closed ab. `OPENCLAW_NIX_MODE=1` sperrt
  Install und Update zur Laufzeit.
- Der Layout-3-Init normalisiert jetzt die aktive Instanzkonfiguration statt nur
  die spaeter inaktive Legacy-Kopie auf den internen Ollama-Proxy; Migration und
  Neustart sind durch einen Verhaltensregressionstest abgedeckt.
- Mail-Kalender und -Kontakte verwenden die native, release-eigene
  CalDAV/CardDAV-Bruecke. Der breite workspace-lokale Nextcloud-Community-Skill
  wird nicht mehr ausgefuehrt; Kalenderauswahl ist exakt und mehrdeutige Auswahl
  bricht vor dem create-only `If-None-Match`-Write ab (ADR-0013).
- Container-Healthchecks und die Ollama-CLI loesen release-eigene Skripte jetzt
  aus `OPENCLAW_IMAGE_ROOT` statt aus dem beschreibbaren Workspace auf. Eine
  veraltete Kalender-Ressourcen-ID bleibt als explizite Konfigurationsdrift
  sichtbar und wird nicht still auf einen nur vermeintlich passenden Share
  umgebogen (ADR-0013).
- Der egress-lose Supervisor prueft den Ollama-Proxy jetzt als Client ueber dessen
  festen internen Health-Endpunkt. Er benoetigt weder die nur fuer den Proxyserver
  bestimmte `OLLAMA_PRIORITY_UPSTREAM`-Konfiguration noch zusaetzliche Mounts oder
  Netzwerkrechte; Unit- und Rollenimage-Smokes bilden den Live-Testfehler nach.
- Die Container-Migration und jeder Layout-3-Start normalisieren nun auch die
  globale sowie agentenspezifische Gateway-Modellkonfiguration vom nativen
  Loopback-Prioritaetsproxy auf `ollama-proxy:11435`. Abweichende Provider werden
  nicht still ueberschrieben; Migration, Neustart und Idempotenz sind durch echte
  JSON-Verhaltenspruefungen abgedeckt.
- Abgeschlossene OpenClaw-Profile bleiben mit `IDENTITY.md`, `SOUL.md`, `USER.md`
  und Setupstatus an der aktiven Layout-3-Instanzwurzel. Bereits falsch
  quarantinierte Profile werden nur ueber eine passende OpenClaw-SHA-256-
  Attestierung wiederhergestellt; bearbeitete oder abgeschlossene aktive Profile
  gewinnen fail-closed (ADR-0014).
- Der release-eigene Agentenvertrag aus `AGENTS.md` und `HEARTBEAT.md` wird in
  Layout 3 an der tatsaechlich gemounteten Instanzwurzel statt im inaktiven
  Legacy-Workspace publiziert. Der `personal-assistant`-Skill wird ueber die
  read-only OpenClaw-Zusatzwurzel `skills.load.extraDirs` registriert. Ein echter
  OpenClaw-Container-Smoke-Test verlangt, dass der Skill als bereit erkannt wird.
- Der Personal-Assistant-Skill erkennt nun auch umgangssprachliche Domaenenfragen
  wie "meine Aktien" und erzwingt fuer alle registrierten Domaenen den passenden
  Status-/Listen-/Suchpfad vor Gedächtnis-, Workspace- oder Shell-Suche. Ein
  gueltig leeres Ergebnis, eine deaktivierte Capability und ein Werkzeugfehler
  duerfen nicht mehr als derselbe Zustand beantwortet werden.
- Tool-IDs und CLI-Syntax sind im Skill nun unmissverstaendlich getrennt. Die
  Intent-Tabelle nennt ausfuehrbare Befehlssuffixe; der generierte Vertrag verbietet
  gepunktete IDs als `assistant.sh`-Argument und dokumentiert den installierten
  Container-Launcher explizit.
- Aktuelle Kursanfragen pruefen nun zuerst die Kursfrische, fuehren bei gueltiger
  Konfiguration den registrierten Refresh aus und werten erst danach Kurs oder
  Depotwert aus. Rollen-Worker schreiben ihr CLI-Log in das beschreibbare
  Koordinationsverzeichnis, statt am schreibgeschuetzten Core-Mount zu scheitern.

## Unreleased – M7 Reproduzierbare und attestierte Image-Lieferkette

- OpenClaw-Quellimage, Node-/Python-Alpine-Basisimages sowie Syft, Trivy und Cosign
  sind per Digest gepinnt; direkte Alpine-Pakete sowie Version, Archiv- und
  Binary-Pruefsumme des offiziellen Himalaya-amd64-Artefakts sind fail-closed
  festgelegt.
- Aus demselben Release entstehen ein voller Runtime-Target sowie deutlich
  schmalere Proxy- und ClamAV-Maintenance-Targets. Tests, Deployment, Legacy,
  Entwicklungsdokumente, Secrets und Laufzeitdaten bleiben aus allen Images.
- GitHub Actions sind commit-gepinnt und besitzen minimale Jobberechtigungen.
  CI erzeugt und prueft je Rolle SPDX-SBOM, SLSA-Provenance, kritischen CVE- und
  Secret-Scan, OCI-Identitaet sowie keyless Cosign-Signatur.
- Das Deployment akzeptiert drei unveraenderliche Digests und verifiziert Signatur,
  SLSA-/SPDX-Attestierungen, Release, Rolle und exakten Git-Commit vor dem Stoppen
  des laufenden Stacks; Backup und Rollback bewahren den kompletten Rollensatz.
- Zwei saubere No-Cache-Builds, Rollen-Smokes, Rootfs-Artefaktscan,
  Signatur-Negativtest, Vulnerability-Policy und reproduzierbare Groessen-/Start-/
  RAM-Messungen bilden die M7-Abnahme.
- Die bytegleichen OCI-Doppelbuilds und die M6/M7-Baseline dokumentieren 0
  kritische CVEs, 11,53 bis 94,50 Prozent kleinere Rollenimages sowie die bewusst
  akzeptierte, sichtbar weitergemessene Kaltstart-/RAM-Regression des Alpine-Pfads.

## Unreleased – M6 Evidenzbasierte Bereinigung und Legacy-Ausstieg

- Ein deterministisches Komponenten-Inventar klassifiziert alle Pythonmodule,
  Shell-Einstiege, Skills, systemd-Units, Migrationen und Dokumente samt Owner,
  Aufrufern, Tests, Coverage-Snapshot, Git-Datum und Rollbackrelevanz.
- Der alte Mail-Skill, ein doppelter Nextcloud-Listenwrapper, der ungenutzte
  Mail-Dateiclient sowie drei nicht aufgerufene Einmal-Konfigurationsmigrationen
  wurden mit maschinenlesbarer Nutzungs- und Ersatzpfad-Evidenz entfernt.
- Native systemd-Artefakte sind aus dem aktiven Deploymentbaum in ein
  fehlgeschlossenes, SHA-verifiziertes Kompatibilitaetspaket unter
  `legacy/systemd/` verschoben. Die Rueckfallfaehigkeit bleibt bis zur M8-
  Recovery-Entscheidung erhalten.
- Direkte Upgrades beginnen verbindlich bei `3.4.0-r26.1`; ein neutrales Fixture
  prueft den aktuellen Konfigurationsparser. Datenbank- und Container-State-
  Migrationen bleiben unveraendert verpflichtend.
- Aktive Hilfe und Nextcloud-Status verwenden nur registrierten Job-Controller
  beziehungsweise den zentralen ActionPlan-Dateiconnector. Positive und negative
  M6-Regressionspruefungen sichern Paketdrift und entfernte Oberflaechen.

## Unreleased – M5 Modulare Anwendungsdienste und Toolvertrag

- Alle 124 stabilen Toolprojektionen und die Top-Level-CLI-Hilfe sind als Golden
  Contracts fixiert; Modi, externe Wirkung, Approval und Fehlercodes werden
  regressionsgeprueft.
- Der zentrale 959-Zeilen-Registry-Builder ist durch typisierte Domaenenkataloge
  mit Handler-, Schema-, Doku- und Testankern sowie eine kleine Live-Projektion
  ersetzt. Die Befehlsreferenz wird deterministisch daraus erzeugt und in CI
  validiert.
- `tools list --catalog` und `capabilities --schema` funktionieren ohne
  Konfiguration oder Secrets. Live-Capabilities sind explizit als konfigurierte
  Instanzsicht getrennt.
- CLI-Domaenenhandler, ein eigener Portfolio-Importparser sowie neutrale
  Contracts/Ports reduzieren zentrale Dispatcher. Der konkrete Mailadapter wird
  nur am Bootstrap zusammengesetzt; Core-Rueckimporte und interne Importzyklen sind
  automatisiert verboten.
- Policy-Negativtests bestaetigen, dass M5 weder Berechtigungen noch externe
  Schreib-, Approval- oder Loeschvertraege erweitert.

## Unreleased – M4 Rollenbezogene Container-Haertung

- Hostnetzwerk entfernt: internes `backend`, begrenztes `egress`, nur Gateway auf
  `127.0.0.1:18789`; allein der Ollama-Proxy besitzt die in ADR-0008 dokumentierte
  Host-Gateway-Ausnahme.
- Alle Rollen laufen mit read-only Rootfs, `cap_drop: ALL`,
  `no-new-privileges`, explizit nicht-root, sicheren tmpfs-Pfaden, PID-/CPU-/
  RAM-Grenzen und begrenzter lokaler Logrotation.
- Ganze Config-/Secretwurzeln sind durch einzelne rollenbezogene Dateimounts
  ersetzt. Der Entry Point parst eine Schluessel-Whitelist als Daten und fuehrt
  weder Env-Dateien noch fremden Shellcode aus.
- Der isolierte ClamAV-Updater laeuft als `clamav` ohne Capabilities; Vollstaendigkeit,
  Frische und Scanner-/Signaturidentitaet werden fail-closed geprueft.
- Liveness, Readiness und fachlicher Jobzustand sind getrennt. Heartbeats bewahren
  wiederholte Fehler und behandeln bewusst deaktivierte Jobs als beobachtbar ready.
- Ein maschinenlesbarer M4-Vertrag sowie statische und isolierte Docker-Tests pruefen
  Mounts, Secrets, Netznegative, Signale, Rootfs, PID/OOM und Ressourcenlimits.

## Unreleased – M3 Datenbesitz, Mountgrenzen und Nebenlaeufigkeit

- State-Layout 3 trennt Instanz, Gateway/Sessions, Mail, Orders, Portfolio,
  Monitoring, Wissensindex, Core/ActionPlan, Security und bewusst geteilte
  Koordination; die alte kombinierte Assistant-DB wird verlustfrei aufgeteilt.
- Ein einmaliger `layout-init` besitzt den universellen State-Mount; Fachworker
  erhalten nur ihre in `state-access.json` beschriebenen `ro`/`rw`-Teilbaeume.
- Die Migration prueft Schreibbarkeit, UID, Freiplatz und SQLite-Integritaet, sichert
  Datenbanken ueber die SQLite-Backup-API, publiziert gestagt/atomar und besitzt
  einen SHA-verifizierten, traversal-sicheren Restorepfad.
- ActionPlan-Erzeugung ist unter Mehrprozesslast atomar idempotent. Scheduler-WAL,
  Owner/Token-Leases und Crash-Recovery bleiben als bewusst kleine gemeinsame
  Koordinationsgrenze in ADR-0007 dokumentiert.
- Reale Parallel-, Lock-, SIGKILL-, Full-/Read-only-Disk-, Backup-/Restore- und
  Compose-Mounttests sowie ein `strace`-Zugriffsauditor sichern M3 ab.

## Unreleased – M2 Unveraenderlicher Code und eindeutige Release-Ausfuehrung

- Gateway, Proxy, Worker und agent-cli starten Shell- und Python-Code nur noch aus
  `/opt/openclaw-agent`; beschreibbarer Workspace-Code wird weder ausgefuehrt noch
  fuer Python-Imports verwendet.
- Compose setzt das Container-Root-Dateisystem read-only und stellt nur ein
  gehaertetes temporaeres `/tmp` sowie die expliziten persistenten Mounts bereit.
- Die idempotente State-Layoutmigration 1 -> 2 sichert und entfernt alten
  synchronisierten Releasecode, bewahrt Konfiguration, Datenbanken, Sessions,
  Korrekturhistorie und lokale Dokumente und exponiert nur notwendige
  Agentenanweisungen/Skills als Image-Links.
- Deployments pruefen die Layoutgrenzen des Zielimages vor dem Stoppen des laufenden
  Stacks; Downgrades auf unbeschriftete Vor-M2-Images brechen fail-closed ab.
- Status und Doctor melden und verifizieren Release-Manifest, VERSION, OCI-/Source-
  Revision sowie reale Python-, Shell- und Worker-Pfade.
- Fixture- und Containerregressionstests pruefen Manipulationen im State-Workspace,
  read-only Imagecode, parallele Starts, fehlgeschlagene Migration, Idempotenz und
  Versions-/Revisionsabweichungen.

## Unreleased – M1 Architekturvertrag, ADRs und Git-Arbeitsweise

- `docs/architecture/README.md` ist der verbindliche Einstieg fuer Systemkontext,
  Container- und Komponentenansicht; Rollen-, Daten- und Trust-Boundary-Matrizen
  dokumentieren technische Ist-Rechte und bekannte Isolationsluecken.
- Sechs nummerierte ADRs entscheiden modularen Monolith, Single Writer,
  SQLite-Datenowner, unveraenderlichen Code, Legacy-Rollback-Untergrenze und den
  maschinenlesbaren Toolvertrag. Offene Folgefragen bleiben sichtbar.
- `CONTRIBUTING.md` und `SECURITY.md` definieren Branch-, Commit-, PR-, Review-,
  Migrations-, Release- und Schwachstellenregeln.
- Systemd-zentrierte Architektur- und alte Git-/Security-/Migrationsdokumente sind
  als nicht normative Historie archiviert; README und Kompatibilitaetslinks zeigen
  auf den aktuellen Containervertrag.
- Ein in `check-repo.sh` integrierter Dokumentationscheck prueft interne Links,
  eindeutige Owner, Releaseverweise, Zwei-Link-Erreichbarkeit und Rollen-/Datenmatrix.

## Unreleased – M0 Baseline, Testvollstaendigkeit und Integritaet

- pytest ist der einheitliche lokale und CI-Test-Runner. Er fuehrt die bisherigen
  349 unittest-Tests und die zuvor ausgelassenen 13 freien Rechnungs-Tests aus;
  getrennte Collection-Waechter verhindern unbemerkte Verkleinerungen.
- Gepinnte Ruff-, mypy-, ShellCheck-, Hadolint-, Coverage- und Build-Pruefungen,
  Compose-Validierung, `git diff --check` und Python-Kompilierung sind Bestandteil
  von `check-repo.sh` und GitHub Actions.
- Ein deterministischer Generator/Verifier deckt die exakte Git-Quellmenge ab und
  bindet `SOURCE_MANIFEST.sha256` in Release-Verifikation, Repository-Check und CI
  ein; positive und negative Regressionstests sichern alle Fehlerklassen.
- Wheel-Build, frische Installation, CLI-/Release-/Testprobe sowie Image-Build und
  Artefaktpruefung sind automatisiert. Die reproduzierbare M0-Messung dokumentiert
  Coverage, Typaltlasten, Modul-/Funktionsgroessen und CI-Containerkennzahlen.
- M0 aendert keine produktiven Container-Mounts, Netzwerke, Berechtigungen,
  Jobzustaende oder fachliche Schreiblogik.
- Die unabhaengige M0-Pruefung ersetzte die globale Ruff-E501-Ausnahme durch eine
  quellzeilengebundene, nicht wachsende Baseline und erweiterte die echten
  Collection-/Manifest-Regressionen.
- Die Artefaktpruefung blockiert nun auch produktive `.env`-Varianten,
  Laufzeitdatenbaeume, lokale virtuelle Umgebungen sowie Schluessel- und
  Zugangsdaten ausserhalb von `/opt/openclaw-agent`; Publish-Images werden vor dem
  Push geprueft.
- Rekursive Dockerignore-Regeln und eine enge Bereinigung nach dem Paketbau halten
  Python-Caches, `build/` und `*.egg-info` aus dem Laufzeitimage. Der dynamische
  Rootfs-Scan sowie der isolierte Container-CLI-Kaltstart sind lokal bestaetigt.

## 3.4.0-r27.2.5 – Robuste Mail-Suche und kontrollierte Kursabrufe

- Erfolgreiche Himalaya-Suchen ohne Treffer werden als vollstaendige leere
  Trefferliste behandelt; nichtleere ungueltige JSON-Ausgaben bleiben Fehler.
- Regressionstests decken leere Suchausgaben, kaputtes JSON und den optionalen
  Container-Smoke-Test fuer einen serverseitigen Nulltreffer ab.
- Depotpositionen und aktivierte Watchlist-Werte werden weiterhin zu einer
  eindeutigen Zielmenge zusammengefuehrt und nun ausdruecklich getestet.
- Der bezahlte EODHD-Zugang wird mit 15-Minuten-Intervall dokumentiert. HTTP
  401, 402 und 403 loesen bis zum naechsten UTC-Tag einen automatischen
  Cooldown aus, statt alle 15 Minuten erneut beim Anbieter anzufragen.
- Der Doctor liest seine Versionsidentitaet aus dem autoritativen
  `RELEASE.json`, statt eine veraltete Paketkonstante auszugeben.

## 3.4.0-r27.2.4 – Waehrungssichere aktuelle Depotbewertung

- Ein EODHD-Kursrefresh nimmt benoetigte FX-Paare wie `EURUSD.FOREX` in
  denselben begrenzten Batch wie die Aktienkurse auf und speichert Kurs,
  Quellzeit, Empfangszeit und Provider versioniert in der Portfolio-Datenbank.
- Der neue read-only Befehl `portfolio valuation` berechnet je Position den
  aktuellen Kurs in Originalwaehrung, den FX-konvertierten Kurs in der
  DKB-Snapshotwaehrung, Depotwert, Einstandswert und Gewinn/Verlust sowie
  konsistente Waehrungssummen.
- Fehlende, kritisch veraltete oder unplausibel zeitgestempelte Aktien-/FX-Kurse
  brechen die Gesamtbewertung fail-closed ab; Teilwerte werden nicht als
  vollstaendiger Depotgewinn summiert.
- Tool-Registry, Skill, Betriebsvertrag und Befehlsreferenz verpflichten den
  Agenten bei aktuellen Gewinnfragen auf das deterministische
  Bewertungswerkzeug statt auf manuelle EUR/USD-Arithmetik.
- Regressionstests pruefen EODHD-Aktien/FX-Batching, die Kehrwertumrechnung von
  EURUSD, korrekte EUR-Positionen und den Abbruch ohne erforderlichen FX-Kurs.

## 3.4.0-r27.2.3 – Bestaendige EODHD-Zuordnung bei neuen DKB-Snapshots

- Ein neuer DKB-Depotsnapshot darf die Waehrung einer bereits bestaetigten
  EODHD-Symbol-/MIC-Zuordnung nicht mehr mit der DKB-Snapshotwaehrung
  ueberschreiben.
- `portfolio holdings` zeigt `quote_currency` erst nach bestaetigter
  Marktdatenzuordnung; `currency` bleibt weiterhin die Waehrung der importierten
  DKB-Snapshotwerte.
- Ein Regressionstest bildet die kritische Reihenfolge DKB-Import,
  EODHD-Zuordnung und spaeterer neuer DKB-Snapshot ab.

## 3.4.0-r27.2.2 – Getrennte Snapshot- und Kurswaehrung

- DKB-Einstiegskurs, Bewertungskurs und Snapshot-Gewinne behalten ihre eigene
  Importwaehrung, auch wenn die bestaetigte EODHD-Zuordnung fuer ein
  US-Wertpapier in USD notiert.
- `portfolio holdings` gibt die DKB-Waehrung als `currency` und die
  Marktdatenwaehrung separat als `quote_currency` aus, damit der Agent keine
  EUR-Einstandswerte direkt mit USD-Kursen verrechnet.
- Bereits importierte identische DKB-Dateien koennen die Snapshot-Waehrung
  idempotent anhand derselben geprueften SHA-256 nachtragen.

## 3.4.0-r27.2.1 – Optionale DKB-Gewinnfelder

- Leere Werte in den vorhandenen DKB-Spalten `Absoluter Gewinn` oder
  `Relativer Gewinn` werden als unbekannt bewahrt und blockieren nicht mehr den
  gesamten Depotimport.
- Einstiegskurs, Bewertungskurs, Stückzahl, ISIN und die übrigen Pflichtdaten
  bleiben strikt validiert; ungültige nichtleere Gewinnwerte bleiben sichtbare
  Importfehler.
- Ein Regressionstest bildet den produktiv beobachteten DKB-Leerwert ab.

## 3.4.0-r27.2 – Vollstaendige Portfolio-Werkzeuge und DKB-Snapshotwerte

- Ein eigener read-only Befehl `portfolio quotes get --isin` liefert einen
  gespeicherten Einzelkurs mit Waehrung, Provider, Quellzeit und Frische, ohne
  dass der Agent SQLite direkt lesen oder eine Websuche improvisieren muss.
- Der Personal-Assistant-Skill, die Tool-Registry, der Betriebsvertrag und die
  Befehlsreferenz enthalten jetzt die vollstaendige Portfolio-Matrix
  einschliesslich Doctor, Watchlist-Aenderungen, Refresh/Force, Kursmarken und
  Jobsteuerung. Ungueltige Optionen wie `quotes status --detailed` und der
  erfundene Pfad `portfolio setup` werden ausdruecklich ausgeschlossen.
- Strikte DKB-CSV-Imports bewahren Einstiegskurs, Bewertungskurs, absoluten und
  relativen Gewinn sowie Assetklasse im unveraenderlichen Depot-Snapshot und
  geben diese Werte ueber `portfolio holdings` aus.
- Bereits importierte identische DKB-Dateien koennen die neuen Snapshotfelder
  sicher anhand derselben ClamAV-geprueften SHA-256 nachtragen, ohne einen
  zweiten Import oder eine neue Position anzulegen.
- Portfolio-Intervalle von 15, 30, 60, 90 und 120 Minuten werden kontrolliert
  unterstuetzt. Fuer ein kostenloses Kontingent mit 20 Aufrufen pro Tag ist 90
  Minuten der konservative Startwert; passende Frischegrenzen werden gesetzt.
- Regressionstests gleichen alle registrierten Portfolio-Werkzeuge gegen Skill
  und Betriebsvertrag ab und pruefen Einzelkursausgabe, DKB-Kennzahlen,
  idempotentes Backfill sowie das 90-Minuten-Setup.

### Serverseitige Mail-Suche

- `mail search` filtert jetzt direkt auf dem IMAP-Server ueber alle lesbaren
  Ordner, einschliesslich Review-Ordnern. Absender, Betreff und Textinhalt
  werden beruecksichtigt; alte Nachrichten verschwinden nicht mehr hinter der
  normalen Listen-Seitengroesse.
- Mehrteilige Suchanfragen verknuepfen bis zu zwoelf eindeutige Suchwoerter mit
  UND, wobei jedes Wort in Absender, Betreff oder Text vorkommen darf.
- Die Rueckgabe kennzeichnet mit `complete`, `folder_errors` und
  `results_may_be_truncated`, ob alle Ordner erfolgreich und ohne moegliche
  Trefferbegrenzung durchsucht wurden. Der Agent darf bei Teilfehlern oder
  erreichtem Limit nicht behaupten, eine Mail existiere nicht.
- Ein vollstaendiges Suchversagen, eine leere Ordnerliste und ueberlange
  Anfragen werden als sichtbare Fehler gemeldet statt als falsches
  Nulltreffer-Ergebnis.
- CLI-Hilfe, Tool-Registry, Personal-Assistant-Skill, Betriebsanweisung und
  Dokumentation beschreiben dieselbe Suchsemantik. Regressionstests decken alte
  Archivmails, Unicode-Namen, Teilausfaelle, Trefferlimits und die
  Agenten-Exposition ab.
- Die Aenderung wird erst mit einem neu gebauten und geprueften Container-Image
  aktiv. Nach dem Deployment ist ein Gateway-Neustart beziehungsweise eine neue
  Agentensitzung erforderlich, damit der aktualisierte Skillkontext geladen
  wird.

### DKB-CSV-Depotimport aus Nextcloud

- Der Portfolio-Monitor importiert neben Portfolio-Performance-XML jetzt das
  strikt validierte DKB-Depot-CSV-Format mit UTF-8/BOM, Semikolon-Trennung,
  deutschem Zahlenformat, Stichtag, Depotnummer, WKN, ISIN, Waehrung und
  Stueckzahl.
- Lokale CSVs bleiben auf den kontrollierten Portfolio-Importordner begrenzt.
  Alternativ kann eine exakt ausgewaehlte Datei direkt unter
  `Assistent/Finanzen/Portfolio/` aus Nextcloud gelesen werden.
- Nextcloud-Snapshots verwenden unveraenderliche, datierte Dateinamen. Das
  Datum im Namen muss mit dem einzigen CSV-Stichtag uebereinstimmen; alte
  Snapshots werden nie ueberschrieben.
- Download und Import sind groessenbegrenzt, an den zuvor gelisteten ETag
  gebunden, ClamAV-geprueft, SHA-256-idempotent und erfordern vor jedem
  produktiven `--yes`-Import einen Dry-Run.
- Tool-Registry, CLI, Skill, Betriebsvertrag, Dokumentation und Regressionstests
  beschreiben denselben sicheren Ablauf. Beliebige Broker-CSV-Layouts bleiben
  bewusst gesperrt.

## Test-Branch – Transaktionale Container-Remigration und sicherer Rollback

- Remigrationen sichern den bestehenden `/srv/openclaw`-Zustand vor dem
  Publish und stellen ihn bei einem Teilfehler wieder her.
- Ein expliziter Gateway-Auth-Modus bevorzugt ein passendes Legacy-Secret;
  unpassende alte Container-Secrets blockieren eine sichere Remigration nicht
  mehr und bleiben über den verifizierten Vorzustand wiederherstellbar.
- Release-Backups verknüpfen das Legacy-Migrationsarchiv mit Pfad,
  Archivmitglied und SHA-256. Ein unvollständiger Legacy-Workspace wird daraus
  wiederhergestellt, bevor ein Rollback die aktuellen Container stoppt.
- Test-Deployments prüfen Docker-Zugriff und vollständige Git-Revision, ohne
  Gruppenrechte selbst zu verändern.
- Vor und nach dem Start der Container wird geprüft, dass kein alter
  systemd-Writer aktiv oder per Timer aktiviert ist.
- Ein harmloses `systemctl reset-failed` für eine noch nicht geladene Unit macht
  einen anschließend erfolgreichen `jobs on`-Vorgang nicht mehr fälschlich
  fehlerhaft.

## Test-Branch – Image-verwaltete Tool-Standards und neue Mailentwuerfe

- Neue Mails koennen mit `mail compose-draft` vollstaendig vorbereitet und erst
  nach separater ausdruecklicher Freigabe mit `mail compose-send --yes`
  versendet werden.
- Direkte Mail-Suche, Lesen, Antwortentwuerfe und Versand werden nun korrekt an
  den Personal-Assistant-Core statt an das alte Mail-CLI geroutet.
- Releaseeigene Tool- und Policy-Standards liegen im unveraenderlichen Image.
  Lokale TOML-Dateien bleiben installationsbezogene Overrides; Sicherheits- und
  Genehmigungsregeln werden additiv zusammengefuehrt.
- Die direkte Mail-Einrichtung erteilt `read`, `move` und `forward` nur nach
  ausdruecklicher `--approve-permissions`-Freigabe.
- Container-Smoke-Tests pruefen die registrierten Faehigkeiten und den
  Compose-CLI-Einstieg, ohne eine Mail zu versenden.

## 3.4.0-r27.1 – Einheitliche EODHD-Depotkurse fuer US und Xetra

- EODHD ersetzt Twelve Data als einzigen Portfolio-Marktdatenanbieter und liefert US- sowie Xetra-Werte ueber denselben kontrollierten Live/Delayed-Endpunkt.
- Bis zu 20 bestaetigte Instrumente werden in einer begrenzten EODHD-Anfrage gebuendelt; RHM/XETR wird sicher zu RHM.XETRA und bestaetigte US-MICs werden zu `.US` uebersetzt.
- Der EODHD-Schluessel liegt getrennt als `PORTFOLIO_EODHD_API_KEY` im Host-Secrets-Verzeichnis; URL, Token und verkettete HTTP-Fehler werden aus Ausgaben und Tracebacks ferngehalten.
- Alte Twelve-Data-Konfiguration wird nach dem Update fail-closed deaktiviert und niemals stillschweigend mit einem anderen Anbieter oder alten Secret weiterverwendet.
- Kursfrische wird je Instrument anhand der Xetra- beziehungsweise US-Handelszeit bewertet; gespeicherte historische Kurse und Depot-Snapshots bleiben erhalten.
- Portfolio-Setup, Tool-Registry, Skill, Befehlsreferenz und Betriebsvertrag beschreiben EODHD, 15-Minuten-Abruf und die typische 15- bis 20-minuetige Kursverzoegerung konsistent.
- Regressionstests pruefen EODHD-Batchantworten, Secret-Redaktion, sichere Legacy-Deaktivierung und die vollstaendige Agenten-Exposition.

## 3.4.0-r27.0.1 – Container-Migrations- und Betriebsfixes

- Die Container-Migration schreibt aktive absolute Workspace-Pfade in openclaw.json sowie den produktiven TOML-Konfigurationen sicher von der nativen Home-Struktur auf /home/node/.openclaw/workspace um.
- Himalaya-Zugangsdaten werden bei einer Live-Migration aus secret-tool in geschuetzte Dateien unter /srv/openclaw/secrets uebernommen; der Container liest sie anschliessend ohne Desktop-Keyring.
- Oeffentliche lokale CA-Zertifikate unter /srv/openclaw/config/ca werden beim Containerstart automatisch mit dem System-Truststore zu einem Laufzeit-Bundle fuer Python, Requests und Node.js kombiniert.
- Der ClamAV-Updater besitzt einen eigenen Healthcheck fuer main-, daily- und bytecode-Signaturen und erbt nicht mehr versehentlich den Gateway-Port-Check.
- Eine fehlende Mail-Agent-Nextcloud-Sektion wird nur bei vollstaendig vorhandenen Nextcloud-Zugangsdaten automatisch und idempotent ergaenzt.
- calendar create wird in Tool-Registry, Skill und Dokumentation ausdruecklich ohne --yes beschrieben; ein Regressionstest verhindert die erneute Erzeugung des ungueltigen Schalters.
- refresh-deployment.sh aktualisiert Compose- und Deployment-Skripte aus einem Git-Checkout, ohne die produktive .env oder aktive lokale Hooks zu ueberschreiben.
- Die GHCR-Standardreferenz, Docker-only-Ausgangswerte und die fuer diese eingeschraenkte Nextcloud-Installation optionale externe Hook-Pflicht wurden an den produktiven Betrieb angepasst.

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
