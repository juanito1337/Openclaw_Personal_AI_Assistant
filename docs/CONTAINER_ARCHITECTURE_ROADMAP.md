# Roadmap zur sicheren Container-Architektur

Stand: 2026-08-05
Gepruefte Release-Identitaet: `3.4.0-r27.2.5`
Status dieses Dokuments: Architekturplanung, keine Produktionsmigration

## Zweck und Leitplanken

Diese Roadmap modernisiert den bestehenden OpenClaw Personal Assistant schrittweise
von einem historisch gewachsenen modularen Monolithen zu einem sicheren,
erweiterbaren und effizient betreibbaren Container-Stack. Es bleibt genau ein
Personal Assistant. Gateway, Mail, Portfolio, Synchronisation, Monitoring und
Supervisor sind spezialisierte Prozesse dieses Assistenten und keine voneinander
unabhaengigen Agenten.

Jeder Milestone wird als eigener kleiner Pull Request umgesetzt. Ein Milestone darf
erst beginnen, wenn der vorherige Milestone inklusive Dokumentation, Migrationstest
und Rollback-Nachweis gruen ist. Produktive Daten, Konfiguration und Secrets bleiben
aus Git und aus dem Image ausgeschlossen. Kein Milestone darf gleichzeitig einen
Legacy- und einen Container-Mail-Writer aktivieren.

Die Roadmap ersetzt weder die Sicherheitsregeln in `AGENTS.md` noch den
Release-/Rollback-Vertrag in `docs/DOCKER_DEPLOYMENT.md`. Bei Widerspruechen gilt der
strengere, durch Tests abgesicherte Vertrag.

## Verifizierter Ist-Stand

### Erhaltenswerte Grundlagen

- `RELEASE.json` ist die verbindliche Release-Identitaet; `version --verify` meldet
  fuer den geprueften Stand `3.4.0-r27.2.5` ohne Versionskonflikt.
- Die Anwendung besitzt bereits kontrollierte ActionPlans, create-only- und
  ETag/`If-Match`-Grenzen, ClamAV-Gates, persistente Job-Sollzustaende, einen
  adaptiven Scheduler sowie verifizierte lokale Deployment-Backups.
- Compose trennt Gateway, Ollama-Proxy und fachliche Worker bereits in eigene
  Prozesse mit Healthchecks und unabhaengigen Lebenszyklen.
- `./scripts/check-repo.sh` ist erfolgreich und fuehrt aktuell 349 Tests aus.
- Der Deploymentpfad pinnt ein gezogenes Zielimage vor dem Start auf dessen
  Registry-Digest und verhindert den parallelen Betrieb bekannter Legacy-Writer.

Diese Eigenschaften sind Sicherheitsinvarianten. Refactorings muessen sie durch
Charakterisierungstests bewahren, nicht neu interpretieren.

### Wesentliche Architektur- und Betriebsrisiken

| Prioritaet | Befund | Risiko |
| --- | --- | --- |
| P0 | `docker/entrypoint.sh` kopiert Release-Code und Shellskripte aus dem Image in das gemeinsam beschreibbare State-Volume; mehrere Container starten Skripte von dort. | Die dokumentierte Grenze zwischen unveraenderlichem Programm und veraenderlichem Zustand ist nicht vollstaendig durchgesetzt. Ein kompromittierter Worker kann ausfuehrbaren Code fuer andere Rollen veraendern. |
| P0 | M3 hat den universellen State-Mount entfernt; Konfigurations-/Secret-Sicht und `network_mode: host` bleiben bis M4 breit. | State-Fehlerbereiche sind getrennt; Secret- und Netz-Angriffsbereich bleibt zu gross. |
| P0 | `SOURCE_MANIFEST.sha256` stimmt bei 56 Eintraegen nicht mit `HEAD` ueberein und enthaelt 15 getrackte Dateien nicht. Weder `version --verify` noch das CI-Gate erkennen das. | Die behauptete Quellintegritaet ist nicht belastbar. |
| P1 | Die Dokumente beschreiben gleichzeitig einen systemd-basierten modularen Monolithen und einen Container-Stack. | Entwickler und Agent koennen falsche Betriebs- oder Erweiterungspfade waehlen. |
| P1 | Kernfunktionen sind erneut stark gebuendelt: `service.py` 2359 Zeilen, `portfolio.py` 2160, `cli.py` 1684, `tool_registry.py` 980; einzelne Funktionen umfassen bis zu 959 Zeilen. | Hohe Aenderungskopplung, schwer pruefbare Berechtigungs- und Fehlerpfade. |
| P1 | `mail_agent` importiert Core-Module, waehrend der Core zugleich konkrete `mail_agent`-Implementierungen importiert. | Die beabsichtigte Abhaengigkeitsrichtung ist nicht technisch erzwungen. |
| P1 | CI startet nur `unittest discover`. 13 freie Tests in `test_invoice_ocr_register.py` werden dadurch nicht gesammelt. Ruff und mypy sind konfiguriert, aber nicht installiert oder ausgefuehrt. | Gruene CI deckt nicht alle vorhandenen Tests und Qualitaetsregeln ab. |
| P1 | Basis-/Builder-Images und Buildwerkzeuge sind ueber Tags statt Digests referenziert; SBOM, Provenance, Signatur und Image-Scan fehlen im Workflow. | Unzureichend reproduzierbare und nachweisbare Software-Lieferkette. |
| P2 | Mehrere wahrscheinliche Altpfade sind weiterhin vorhanden, zum Beispiel das nicht ausgerollte Skill-Paket `mail-chief-of-staff`, systemd-Helfer und ein nur von Tests importierter Mail-Nextcloud-Dateiclient. | Dokumentationsdrift, groessere Wartungs- und Angriffsoberflaeche. Loeschung ist ohne Nutzungs- und Rollbacknachweis jedoch zu riskant. |
| P2 | Runtime-Introspektion wie `tools list` und `capabilities` verlangt im Checkout bereits eine produktionsnahe Konfiguration. | Tool-/Skill-Vertraege lassen sich in Staging und CI nicht vollstaendig isoliert pruefen. |

Der Docker-Daemon war waehrend der Erstanalyse nicht zugaenglich. Die dynamische
M0-Nachpruefung wurde am 2026-08-05 in einer autorisierten lokalen Testumgebung
nachgeholt; Image-Build, vollstaendiger Rootfs-Artefaktscan und isolierter
CLI-Kaltstart waren erfolgreich. Produktive Container wurden dabei nicht
veraendert. Mount- und Rollenbefunde bleiben Gegenstand der spaeteren Milestones.

## Zielbild

Das Ziel ist kein unnoetig verteiltes Microservice-System, sondern ein klar
geschnittener, rollenbasierter Container-Stack aus einem gemeinsamen Release:

1. Release-Code, Baseline-Policies und ausfuehrbare Skripte werden nur aus
   read-only Images geladen.
2. Persistenter Zustand liegt ausschliesslich unter `/srv/openclaw` und ist nach
   Verantwortlichkeit getrennt. Jede Rolle sieht nur die benoetigten Teilbaeume.
3. Secrets werden pro Rolle und moeglichst als einzelne read-only Dateien
   bereitgestellt. Ein Worker erhaelt keine fremden Zugangsdaten.
4. Das Gateway ist der Kontrollpunkt fuer Agentenkontext und Toolauswahl.
   Schreibende externe Aktionen bleiben hinter Policy, ActionPlan, Idempotenz und
   Audit.
5. Mail, Portfolio, Wissen und Monitoring besitzen getrennte Anwendungsdienste
   und Datenbanken. Bewusst gemeinsamer Zustand wie Job-Sollzustand und Scheduler
   wird klein, dokumentiert und transaktional gehalten.
6. CLI, Tool-Registry, Capability-Modell, Skill-Referenz und Tests werden aus
   einem typisierten Toolvertrag abgeleitet oder automatisch gegeneinander
   validiert.
7. Builds sind reproduzierbar, auf Digests gepinnt, gescannt und mit SBOM,
   Provenance und Release-Identitaet nachvollziehbar.
8. Jede Migration hat ein Vorwaertsformat, einen getesteten Rueckweg und eine
   explizite Untergrenze fuer noch unterstuetzte Altstaende.

### Messbare Zielkriterien

- Null ausfuehrbare Produktdateien werden aus einem read-write State-Mount geladen.
- Null normale Anwendungscontainer laufen als root; begruendete Maintenance-Ausnahmen
  sind isoliert, capability-minimiert und getestet.
- Jede Rolle besitzt eine dokumentierte Mount-, Secret-, Netzwerk- und
  Berechtigungsmatrix; CI prueft die gerenderte Compose-Konfiguration dagegen.
- `git ls-files` und das Quellmanifest stimmen zu 100 Prozent ueberein; jede
  Hashabweichung stoppt CI und Release-Verifikation.
- Alle gesammelten Tests laufen in CI. Neue freie Tests koennen nicht unbemerkt
  uebersprungen werden.
- Kritische Sicherheitsgrenzen (Policy, ActionPlan, ETag, create-only,
  Antivirus, Single Writer, Backup/Restore) besitzen positive und negative Tests.
- Keine manuell gepflegte Skill-Aussage darf einer registrierten Capability oder
  CLI-Hilfe widersprechen.
- Imagegroesse, Startzeit, Idle-RAM, Job-Laufzeit und Restore-Zeit werden vor und
  nach einem Architektur-Milestone verglichen; Verschlechterungen benoetigen eine
  dokumentierte Entscheidung.

## Milestone-Reihenfolge

| Milestone | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M0 | Vertrauenswuerdige, reproduzierbare Baseline | keine |
| M1 | Verbindliche Architektur- und Git-Dokumentation | M0 |
| M2 | Tatsaechlich unveraenderlicher Programmcode | M1 |
| M3 | Getrennter Zustand und sichere Nebenlaeufigkeit | M2 |
| M4 | Rollenbezogene Container-Haertung | M3 |
| M5 | Modulare Anwendungs- und Toolarchitektur | M4 |
| M6 | Nachweisbares Entfernen von Altlasten | M5 |
| M7 | Reproduzierbare, minimale und nachweisbare Images | M6 |
| M8 | End-to-End-Migration, Recovery und nachhaltige Agentendokumentation | M7 |

## M0 – Baseline, Testvollstaendigkeit und Integritaet

Umsetzungsstand: implementiert und lokal statisch sowie dynamisch geprueft am
2026-08-05. Der
reproduzierbare Pruefvertrag und die gemessenen Ausgangswerte stehen in
`docs/TESTING.md`; Wheel- und Container-CI sind in `docs/BUILD_AND_CI.md`
beschrieben. Das separate Testimage wurde erfolgreich gebaut, im exportierten
Root-Dateisystem auf Secrets und Laufzeitdaten geprueft und per isolierter CLI-Hilfe
gestartet. Diese Baseline ist die Eingangsvoraussetzung fuer M1.

### Ergebnis

CI und lokale Repository-Pruefung liefern eine belastbare Ausgangsbasis, bevor
Runtime- oder Datenpfade geaendert werden. Dieser Milestone veraendert keine
produktive Funktion und keine Freigabe.

### Pflichtpruefungen

- Einheitlichen Test-Runner festlegen und alle 349 bestehenden unittest-Tests
  sowie die 13 freien Rechnungs-Tests sammeln.
- Testabbruch bei unterschiedlicher Collection-Zahl zwischen lokaler Pruefung und CI.
- Ruff, mypy, ShellCheck, Dockerfile-/Compose-Lint und `git diff --check` in CI.
- Exakte, deterministische Erzeugung und Verifikation von `SOURCE_MANIFEST.sha256`;
  das Manifest selbst wird nachvollziehbar ausgeschlossen.
- Build eines Wheels und eines Containers aus einem sauberen Checkout.
- Compose-Rendering mit Beispielkonfiguration ohne produktive Secrets.
- Ausgangswerte fuer Testabdeckung, Imagegroesse, Startzeit und Modulkomplexitaet
  dokumentieren. Der erste Milestone friert die Baseline ein, statt unrealistische
  Grenzwerte nachtraeglich zu erfinden.

### Entwicklungs-Prompt

~~~~text
Arbeite ausschliesslich an Milestone M0 der Datei
docs/CONTAINER_ARCHITECTURE_ROADMAP.md. Lies AGENTS.md vollstaendig und fuehre zuerst
./scripts/assistant.sh version --verify aus. Veraendere weder Produktivkonfiguration
noch Jobzustand und starte keinen schreibenden Mail-/Nextcloud-Test.

Schliesse die Qualitaetsluecken der Repository-Baseline: Verwende einen einheitlichen
Test-Runner, der alle unittest- und freien pytest-Tests sammelt; mache eine unerwartet
kleinere Testcollection zu einem Fehler. Fuehre die in pyproject.toml angekuendigten
Ruff- und mypy-Pruefungen wirklich aus. Ergaenze Shell-, Dockerfile- und
Compose-Linting. Implementiere einen deterministischen Generator/Verifier fuer
SOURCE_MANIFEST.sha256, der exakt alle vorgesehenen getrackten Quelldateien abdeckt,
sich selbst eindeutig ausschliesst und bei fehlenden, zusaetzlichen oder geaenderten
Dateien fehlschlaegt. Binde die Pruefung in version --verify, check-repo.sh und CI ein.

Baue Wheel und Container in CI aus einem sauberen Checkout. Dokumentiere Toolversionen
und die gemessene Baseline fuer Testzahl, Coverage, Imagegroesse, Startzeit und die
groessten Module/Funktionen. Pinne neue Entwicklungswerkzeuge reproduzierbar. Aendere
noch keine Container-Mounts, Netzwerke oder Anwendungsarchitektur.

Tests: negative Manifest-Fixtures, vollstaendige Testcollection, Wheel-Install in
frischer Umgebung, docker compose config, Shellsyntax und vorhandene Regressionstests.
Zeige am Ende Befehle, Testzahlen und verbleibende Einschraenkungen. Beende die Arbeit
nach M0.
~~~~

### Abnahme

- CI beweist, dass alle vorhandenen Testarten gesammelt werden.
- Ein absichtlich geaenderter oder nicht gelisteter Quelltext laesst Manifest- und
  Release-Pruefung scheitern.
- Ein sauberer Checkout erzeugt reproduzierbar installierbare Artefakte.

## M1 – Architekturvertrag, ADRs und Git-Arbeitsweise

Umsetzungsstand: implementiert am 2026-08-05. Der verbindliche Einstieg liegt unter
`docs/architecture/README.md`; Rollenmatrix, Datenkatalog, Trust Boundaries,
Erweiterungsregeln, ADRs, `CONTRIBUTING.md` und `SECURITY.md` werden durch den lokalen
und CI-weit identischen Dokumentationscheck abgesichert. Historische Zielbilder sind
als nicht normative Archivstaende erhalten.

### Ergebnis

Eine einzige aktuelle Dokumentationsstruktur beschreibt Systemkontext,
Containerrollen, Komponenten, Datenbesitz, Trust Boundaries, Toolvertraege,
Migrationen und Erweiterungsregeln. Alte Dokumente werden verlinkt, korrigiert oder
als historische Release-Notizen gekennzeichnet.

### Pflichtartefakte

- `docs/architecture/README.md` als Einstieg mit Kontext-, Container- und
  Komponentenansicht.
- Rollenmatrix fuer Prozess, Daten, Mounts, Secrets, Netz und Schreibrechte.
- Datenkatalog fuer jede SQLite-Datei, JSON-Zustandsdatei, Logklasse und deren Owner.
- ADR-Verzeichnis mit Template und ersten Entscheidungen: modularer Monolith im
  Multi-Container-Betrieb, Single-Writer, SQLite-Grenzen, unveraenderlicher Code,
  Legacy-Rollback-Untergrenze und Toolvertrag als Single Source of Truth.
- `CONTRIBUTING.md`, `SECURITY.md` und `docs/TESTING.md` mit Branch-, Commit-, PR-,
  Release-, Migrations- und Reviewregeln.
- Architektur-Dokumentationsindex im README. Chronologische Release-Details gehoeren
  in `CHANGELOG.md`, nicht als konkurrierendes Zielbild in den Architektureinstieg.

### Entwicklungs-Prompt

~~~~text
Setze nur Milestone M1 aus docs/CONTAINER_ARCHITECTURE_ROADMAP.md um. Nutze die in M0
erzeugte Baseline. Fuehre keine Runtime- oder Datenmigration durch.

Erstelle eine verbindliche Git-Dokumentation fuer die aktuelle und geplante
Architektur. Erfasse Systemkontext, Containerrollen, Komponenten, Abhaengigkeitsrichtung,
Trust Boundaries, Datenbanken/Zustandsdateien, Mounts, Secrets, Netzverbindungen,
externe Schreibpfade, Healthchecks und Backup-/Rollbackgrenzen. Dokumentiere explizit,
dass mehrere Prozesse zu genau einem Personal Assistant gehoeren.

Fuehre ADRs mit nummeriertem Template ein und entscheide nur Punkte, die durch den
aktuellen Code belegt sind. Markiere offene Entscheidungen als solche. Erstelle
CONTRIBUTING.md, SECURITY.md und docs/TESTING.md mit Conventional-Commit- oder einer
gleichwertig eindeutigen Commitkonvention, kleinen Milestone-PRs, Pflichtchecks,
Migrationsregeln und Release-Checkliste. Bereinige Widersprueche zwischen README,
ARCHITECTURE.md, ASSISTANT_ARCHITECTURE.md und DOCKER_DEPLOYMENT.md, ohne historische
Information zu vernichten; verschiebe Historie bei Bedarf in einen klaren Archivpfad.

Fuege Dokumentationstests fuer gueltige interne Links, eindeutige Dokument-Owner,
aktuelle Releaseverweise und das Vorhandensein der Rollen-/Datenmatrix hinzu. Beende
nach M1 und liste jede noch offene ADR-Frage auf.
~~~~

### Abnahme

- Ein neuer Entwickler findet von `README.md` in hoechstens zwei Links Zielbild,
  Betriebsmodell, Testanleitung und Erweiterungsregeln.
- Kein aktives Architekturdokument bezeichnet systemd noch als primaeren Runtimepfad.
- Historische systemd-Rollbackunterstuetzung ist klar von aktiver Architektur getrennt.

## M2 – Unveraenderlicher Code und eindeutige Release-Ausfuehrung

Umsetzungsstand: implementiert am 2026-08-05. Image-, State- und Workspace-Pfade
sind technisch getrennt; Layout 2, Runtime-Identitaet und die dynamische
Containerabnahme werden durch lokale und CI-Tests abgesichert. Die anschliessende
State-Aufteilung ist in M3 umgesetzt.

### Ergebnis

Python-Pakete, Shellskripte, Worker-Loop, Defaults und Runtime-Skills werden aus dem
read-only Image ausgefuehrt. Persistente Volumes enthalten nur Instanzzustand,
Konfiguration, erlaubte Workspace-Dokumente und Daten, aber keinen ausfuehrbaren
Produktcode.

### Pflichtarbeiten

- Ist-Abhaengigkeiten des OpenClaw-Workspace von Release-Dokumenten, Skills und
  Skripten durch Tests erfassen.
- Imagepfad, Datenpfad und Agent-Workspace technisch trennen; keine Ausfuehrung von
  `scripts/*.sh` oder Python-Modulen aus dem State-Mount.
- Nur notwendige, nicht ausfuehrbare Release-Metadaten kontrolliert in den
  Agent-Workspace spiegeln oder als read-only Imagepfad exponieren.
- Upgrade- und Downgradepfade fuer bestehende `/srv/openclaw/state`-Layouts bauen.
- Laufende Version, OCI-Revision und ausgefuehrter Codepfad in Status/Doctor sichtbar
  und gegeneinander verifiziert machen.

### Entwicklungs-Prompt

~~~~text
Setze nur Milestone M2 der Container-Architektur-Roadmap um. Bewahre alle stabilen
assistant.sh-Kommandos und die Release-/Rollbackregeln. Erstelle vor jeder
Migrationssimulation eine verifizierte lokale Fixture-Sicherung; beruehre keine
Produktivdaten.

Entferne die Ausfuehrung von Release-Code aus dem beschreibbaren State-Volume. Trenne
Image-Root, OpenClaw-Agent-Workspace und persistente Instanzdaten mit eindeutigen
Pfadkonstanten. Gateway, Proxy, Worker und agent-cli muessen Shell- und Python-Code aus
dem Image starten. Das State-Volume darf keinen ausfuehrbaren Produktcode benoetigen.
Erhalte die fuer Agentensitzungen notwendigen Anweisungen und Skills ueber einen
read-only oder kontrolliert generierten Dokumentpfad; dokumentiere die Entscheidung
als ADR.

Fuege eine idempotente Layoutmigration fuer bestehende Installationen hinzu. Sie darf
Konfiguration, Datenbanken, Sessions, Korrekturhistorie und lokale Agentendokumente
nicht verlieren. Ein Downgrade muss entweder getestet funktionieren oder vor dem
Stoppen des laufenden Stacks mit klarer Meldung abbrechen. Status und Doctor sollen
Image-Revision, Release-Manifest und reale Executable-Pfade melden.

Tests: Manipulation eines alten Workspace-Skripts darf das Containerverhalten nicht
aendern; read-only Imagecode; Upgrade/Downgrade-Fixtures; paralleler Containerstart;
fehlgeschlagene Synchronisation; Version-/Revision-Mismatch; bestehende Tool- und
Smoke-Tests. Beende nach M2.
~~~~

### Abnahme

- Ein Test veraendert absichtlich eine Datei im State-Workspace; kein Prozess laedt
  daraus Produktcode.
- Ein Neustart mit unveraendertem Image veraendert keine Instanzkonfiguration.
- Ein Imagewechsel aktualisiert Code atomar ohne `rsync --delete` ueber Runtime-Code.

## M3 – Datenbesitz, Mountgrenzen und Nebenlaeufigkeit

Umsetzungsstand: implementiert am 2026-08-05. Layout 3, maschinenlesbare
Mountmatrix, gestagte Migration/Restore, instrumentierte Zugriffsinventur und reale
Mehrprozess-/Crash-Tests sind lokal validiert. Core- und Wissensdaten liegen in
getrennten SQLite-Dateien. M4 wurde nicht begonnen.

### Ergebnis

Jede Containerrolle erhaelt nur die benoetigten persistenten Teilbaeume. Gemeinsamer
Zustand ist bewusst klein und besitzt einen dokumentierten Konsistenzvertrag.

### Pflichtarbeiten

- Tatsachliche Datei- und SQLite-Zugriffe je Rolle instrumentiert inventarisieren.
- Ziellayout unter `/srv/openclaw/state` mit getrennten Bereichen fuer Mail,
  Portfolio, Wissen, Monitoring, Gateway/Sessions und bewusst geteilte Koordination.
- Owner, Schema, Migration, Backupkonsistenz, Locking und Aufbewahrung je Datei
  dokumentieren.
- Entscheiden und per ADR belegen, ob der gemeinsame Scheduler bei SQLite/WAL bleibt
  oder einen kleinen Coordinator-Prozess erhaelt. Keine Entscheidung allein wegen
  des Begriffs „Microservice“.
- Atomare Migration mit Preflight, freiem Speicher, UID/GID, SQLite-Checks,
  Staging-Publish und Rollback.

### Entwicklungs-Prompt

~~~~text
Setze nur M3 aus docs/CONTAINER_ARCHITECTURE_ROADMAP.md um. Beginne mit einer
instrumentierten Read/Write-Matrix fuer jeden Container und jede persistente Datei.
Rate keine Mountanforderungen. Bewahre den Single-Writer-Vertrag und alle Datenbank-
und ActionPlan-Historien.

Teile den derzeit universellen State-Mount in rollenbezogene Teilbaeume. Gib jeder
Rolle nur die benoetigten read-only oder read-write Mounts. Definiere fuer bewusst
gemeinsame Daten wie Job-Sollzustand, Outbox/Audit und Scheduler einen expliziten
Owner- und Transaktionsvertrag. Bewerte SQLite WAL, Locks, Backupkonsistenz und
Crash-Recovery unter realer Mehrprozesslast. Dokumentiere per ADR, ob die bestehende
Shared-SQLite-Loesung ausreicht oder ein enger Koordinator notwendig ist.

Implementiere eine idempotente, gestagte Layoutmigration samt verifiziertem Backup,
Integritaetspruefung und Rueckweg. Keine Datenbank darf zur Reparatur geloescht oder
neu initialisiert werden.

Tests: Parallelzugriff aller Worker, Lock-Contention, SIGKILL waehrend Commit,
abgelaufene Scheduler-Lease, voller/read-only Datentraeger, konsistenter Backup-Satz,
Restore des alten und neuen Layouts sowie Mount-Matrix aus gerendertem Compose. Messe
Latenz und I/O vor/nach der Aenderung. Beende nach M3.
~~~~

### Abnahme

- Kein fachlicher Worker kann die Datenbank eines unbeteiligten Subsystems schreiben.
- Backup und Restore erzeugen einen zeitlich konsistenten, startbaren Zustand.
- Scheduler- und ActionPlan-Invarianten bestehen unter paralleler Last und Crashs.

## M4 – Rollenbezogene Container-Haertung

Umsetzungsstatus 2026-08-05: implementiert und durch
`docs/architecture/runtime-hardening.json`, statische Regressionstests sowie den
isolierten Docker-Prueflauf `scripts/check-container-hardening.sh` abgesichert.
M5 wurde dabei nicht begonnen.

### Ergebnis

Netz, Secrets, Linux-Rechte, Dateisystem und Ressourcen sind pro Rolle minimal und
durch maschinenlesbare Tests abgesichert.

### Pflichtarbeiten

- `network_mode: host` je Rolle durch explizite Netze/Ports ersetzen oder eine enge,
  begruendete Ausnahme als ADR dokumentieren.
- Secrets pro Rolle auf einzelne Dateien begrenzen; keine Shell-Auswertung beliebiger
  `*.env`-Dateien im Containerentrypoint.
- `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, nicht-root UID/GID, sichere
  `tmpfs`-Pfade, `pids_limit`, Logrotation und begruendete Ressourcenlimits.
- ClamAV-Updater als eng isolierte Maintenance-Rolle; Signaturfrische und
  Scanneridentitaet fail-closed pruefen.
- Healthcheck, Liveness, Readiness und fachlichen Jobzustand begrifflich und technisch
  trennen.

### Entwicklungs-Prompt

~~~~text
Setze ausschliesslich M4 der Roadmap um. Nutze die M3-Mountmatrix als verbindliche
Eingabe. Erweitere keine fachlichen Berechtigungen und aendere keine Zugangsdaten.

Haerte jede Compose-Rolle nach Least Privilege: separate Netze und nur notwendige
veroeffentlichte Ports, rollenbezogene Secret-Dateien, read-only Root-Dateisystem,
cap_drop ALL, no-new-privileges, expliziter nicht-root Benutzer, minimale tmpfs-Pfade,
PID-/CPU-/RAM-Grenzen und begrenzte Logrotation. Entferne das Sourcen von Env-Dateien
als Shellcode und verwende einen strikt validierenden KEY=VALUE-Loader oder direkte
Secret-Dateien. Begruende jede unvermeidbare Ausnahme in einer ADR mit Bedrohung,
Kompensation und spaeterem Prueftermin.

Trenne Prozess-Liveness, Abhaengigkeits-Readiness und fachlichen Jobzustand. Ein
deaktivierter Job bleibt beobachtbar gesund; ein frischer Heartbeat darf einen
wiederholt fehlgeschlagenen fachlichen Lauf nicht als erfolgreich maskieren.

Tests: docker/compose inspect gegen die Rollenmatrix, Zugriff auf fremde Secrets und
Mounts muss scheitern, Netzwerk-Negativtests, read-only-Dateisystem, Signalhandling,
OOM/PID-Grenzen, fehlende oder alte ClamAV-Signaturen, Healthcheck-Semantik und
bestehende Smoke-/Rollbacktests. Beende nach M4.
~~~~

### Abnahme

- Universelle State-, Config- und Secret-Mounts sind entfernt.
- Alle normalen Rollen laufen nicht-root und ohne Linux-Capabilities.
- Jede Netzwerk- und Root-Ausnahme ist klein, getestet und per ADR begruendet.

## M5 – Modulare Anwendungsdienste und ein Toolvertrag

### Ergebnis

Die Codebasis bleibt als Produkt zusammenhaengend, besitzt aber technisch erzwungene
Domänengrenzen. CLI, Registry, Policy, Skill und Tests driften nicht mehr auseinander.

### Pflichtarbeiten

- Charakterisierung der stabilen CLI-Ausgaben, Tool-IDs, Approvalregeln und
  Fehlercodes vor dem Refactoring.
- `personal_assistant/cli.py`, `service.py`, `tool_registry.py`, `portfolio.py` und
  `mail_agent` schrittweise nach Domaenen zerlegen.
- Ein schmales Paket fuer gemeinsame Contracts/Ports; fachliche Core-Module duerfen
  keine konkrete Mail-Infrastruktur importieren.
- Tooldefinitionen co-lokal mit Handler, Schema, Modus, Approval, Dokumentationsanker
  und Tests verwalten. Daraus Registry- und Command-Referenz generieren oder streng
  validieren.
- Konfigurationsfreie Introspektion fuer `--help`, `tools list --catalog` und
  Capability-Schema; Live-Berechtigungen bleiben eine getrennte Instanzsicht.

### Entwicklungs-Prompt

~~~~text
Setze nur M5 der Container-Architektur-Roadmap um. Schreibe zuerst Golden-/
Charakterisierungstests fuer alle stabilen CLI-Kommandos, Tool-IDs, Modes,
Approvalregeln, Policyentscheidungen und maschinenlesbaren Fehlercodes. Aendere keine
fachliche Berechtigung und keinen externen Schreibvertrag.

Zerlege die grossen Dispatcher und Services inkrementell nach Domaenen, zum Beispiel
Runtime/Jobs, Mail, Nextcloud Files, Kontakte, Kalender, Aufgaben, Rechnungen,
Bestellungen und Portfolio. Fuehre ein kleines gemeinsames Contract-/Port-Paket ein,
sodass Abhaengigkeiten von Fachadaptern zum Core zeigen und der Core keine konkreten
mail_agent-Implementierungen importiert. Vermeide einen neuen zentralen Service
Locator.

Ersetze den 959-Zeilen-Registry-Builder durch typisierte, domänennah registrierte
Toolbeschreibungen. Jede Beschreibung enthaelt Handler/Command, Argument- oder
Ausgabeschema, read/local-write/write, externe Wirkung, Approvalvertrag,
Dokumentationsanker und Testanker. Erzeuge die Befehlsreferenz oder validiere sie
vollstaendig daraus. Stelle einen konfigurationsfreien statischen Katalog bereit und
trenne ihn klar von live konfigurierten Capabilities.

Tests: unveraenderte CLI-/JSON-Vertraege, Import-Layer-Test, keine Importzyklen,
Registry-Duplikate, jedes Tool in CLI+Registry+Skill/Referenz+Regressionstest,
Policy-Negativtests sowie alle vorhandenen Integrationstests. Refactore in kleinen
Commits und beende nach M5.
~~~~

### Abnahme

- Die Paketrichtung ist automatisiert geprueft und frei von Rueckimporten aus dem Core
  in konkrete Mailadapter.
- Kein einzelner zentraler Registry-/CLI-Dispatcher muss fuer eine neue Domaene um
  hunderte Zeilen erweitert werden.
- Statischer Toolkatalog und Live-Capabilities sind ohne widerspruechliche Aussagen
  getrennt abrufbar.

### Umsetzungsstand 2026-08-06

M5 ist umgesetzt. Die bisherigen 124 Live-Toolprojektionen und die Top-Level-Hilfe
sind als Golden Contracts charakterisiert. Der zentrale 959-Zeilen-Builder wurde
durch typisierte Domaenenkataloge und eine kleine Live-Projektion ersetzt; die
Befehlsreferenz wird daraus deterministisch erzeugt. Domaenenparser und -handler
entlasten den CLI-Dispatcher; Workspace-, Mail-, Portfolio-, Bestell- und
Sicherheitsdienste sind als schmale Anwendungs-Mixins getrennt. Portfolio-
Importparser besitzen ein eigenes Modul und die konkrete Mail-Infrastruktur wird
am Bootstrap gegen einen neutralen Core-Port verdrahtet.

`tools list --catalog` und `capabilities --schema` laufen ohne Konfiguration. Die
Live-Sicht kennzeichnet sich getrennt als `live-capabilities`. Golden-, Policy-,
Importgraph-, Handler-, Doku-/Testanker- und vorhandene Integrationstests sichern
den unveraenderten fachlichen Berechtigungs- und Schreibvertrag. M6 ist nicht Teil
dieser Umsetzung.

## M6 – Evidenzbasierte Bereinigung und Legacy-Ausstieg

Umsetzungsstand: implementiert am 2026-08-06. Das maschinenlesbare Inventar,
Entfernungsevidenz und die direkte Upgrade-Untergrenze liegen unter
`docs/architecture/`; das eingefrorene Legacy-systemd-Paket unter
`legacy/systemd/`. Lokale, Wheel- und isolierte Containerabnahme sind in
`docs/TESTING.md` protokolliert. M7 ist nicht Teil dieser Umsetzung.

### Ergebnis

Unbenutzte Komponenten, doppelte Connectoren, veraltete Skills, systemd-Helfer und
ueberholte Migrationsstufen werden entfernt oder mit eindeutigem Ablaufdatum
isoliert. Es wird nichts allein aufgrund eines Namens geloescht.

### Kandidaten fuer die Pruefung

- `skills/mail-chief-of-staff/`: alte Version, wird vom Containerentrypoint nicht in
  den produktiven Skillpfad synchronisiert und verweist auf direkte Mailskripte.
- `mail_agent/nextcloud_files.py`: statisch nur von Tests importiert; moegliche
  Vorgaengerimplementierung des ActionPlan-Pfads.
- `scripts/nextcloud-list.sh`: dupliziert einen registrierten Assistant-Befehl.
- `scripts/set-mail-agent-interval.sh` und `deploy/systemd/*`: fuer Containerbetrieb
  ueberholt, aber eventuell noch fuer verifizierten Legacy-Rollback erforderlich.
- `config_migrate_r25.py`, `config_migrate_r26.py`, `config_migrate_r261.py`: nur nach
  Definition einer minimal unterstuetzten Upgrade-Version entfernbar.
- Release-spezifische Hotfix-Dokumente und doppelte Architekturtexte: in ein
  historisches Archiv verschieben, wenn sie nicht mehr operativ gelten.

### Entwicklungs-Prompt

~~~~text
Setze ausschliesslich M6 der Roadmap um. Erstelle zuerst ein maschinenlesbares
Inventar aller Pythonmodule, Shellentrypoints, Skills, systemd-Units, Migrationen und
Dokumente mit Owner, produktivem Aufrufer, Testabdeckung, letztem Git-Aenderungsdatum,
Runtime-Telemetrie soweit datenschutzkonform und Rollbackrelevanz.

Klassifiziere jeden Kandidaten als aktiv, Kompatibilitaet, Migration-only,
deprecated oder unbenutzt. Entferne nur Elemente, fuer die statische Referenzen,
dynamische Charakterisierung, Deployment-/Rollbackpfad und unterstuetzte
Upgrade-Untergrenze gemeinsam belegen, dass sie nicht benoetigt werden. Ersetze
verbotene oder doppelte Direktwerkzeuge durch den registrierten Assistant-Pfad.

Fuer systemd gilt: Trenne aktive Installationsartefakte von einem eingefrorenen,
verifizierten Legacy-Rollbackpaket. Entferne die Rueckfallfaehigkeit erst nach einer
expliziten ADR mit End-of-Support-Version und getesteter Container-zu-Container-
Recovery. Entferne niemals produktive Datenbanken oder Korrekturhistorie.

Aktualisiere Registry, Dokumentation, Skills, Packaging und Tests in demselben Commit
wie jede Entfernung. Fuege Negativtests hinzu, dass entfernte Befehle nicht mehr als
unterstuetzt beworben werden. Vergleiche Imagegroesse, Importzeit und Tests vor/nach
der Bereinigung. Beende nach M6.
~~~~

### Abnahme

- Fuer jede entfernte Datei existiert im PR ein nachvollziehbarer Nutzungsnachweis.
- Es gibt genau einen aktiven Connector-/Commandpfad je Capability.
- Die definierte minimale Upgrade- und Rollbackversion funktioniert mit Fixtures.

## M7 – Reproduzierbare und effiziente Image-Lieferkette

Umsetzungsstand: implementiert am 2026-08-06. Drei gemessene Runtime-Targets,
Digest-/Action-Lock, SPDX/SLSA/Cosign-Freigabe, Critical-CVE-/Secret-Sperren und das
Pre-Stop-Deployment-Gate sind lokal und in CI verankert. M8 wurde nicht begonnen.

### Ergebnis

Images sind minimal nach Rollenbedarf, digest-gepinnt, gescannt und durch SBOM,
Provenance und Signatur eindeutig einem getesteten Git-Stand zuordenbar.

### Pflichtarbeiten

- Basis- und Builder-Images per Digest pinnen; Himalaya- und Systemabhaengigkeiten
  reproduzierbar verifizieren.
- GitHub Actions auf unveraenderliche Action-Commits pinnen und Berechtigungen weiter
  minimieren.
- SBOM, Build-Provenance, Vulnerability- und Secret-Scan, OCI-Labels und Signatur.
- Nach Messung entscheiden, ob ein gemeinsames Runtime-Image oder mehrere
  Multi-Stage-Ziele (Gateway, Core-Worker, Mail/OCR, Maintenance) kleiner und sicherer
  sind. Alle Rollen bleiben dieselbe Release-Identitaet.
- Tests, Deploymentskripte, Legacy-Units und Entwicklungsdokumente aus
  Produktionsimages ausschliessen, sofern sie zur Laufzeit nicht benoetigt werden.

### Entwicklungs-Prompt

~~~~text
Setze nur M7 aus docs/CONTAINER_ARCHITECTURE_ROADMAP.md um. Verwende die gemessene
M0/M6-Baseline und optimiere nicht auf Vermutung.

Pinne alle Basis- und Builder-Images per Digest und verifiziere die Himalaya-Binary
sowie Buildabhaengigkeiten reproduzierbar. Pinne GitHub Actions auf Commit-SHAs und
verwende minimale Jobberechtigungen. Erzeuge fuer jedes Release eine SBOM und
Build-Provenance, scanne Quellbaum und Images auf Secrets und bekannte Schwachstellen
und signiere die unveraenderlichen Image-Digests. Deployment muss Signatur,
Provenance, Release-ID und erwartete Git-Revision vor dem Stoppen des alten Stacks
pruefen.

Erstelle anhand gemessener Abhaengigkeiten Multi-Stage-Runtimeziele nur dort, wo sie
Angriffsoberflaeche oder Groesse klar reduzieren. Gateway benoetigt nicht automatisch
OCR-, Mail- und Antivirus-Werkzeuge; ein Worker erhaelt nicht automatisch die gesamte
OpenClaw-Gateway-Laufzeit. Bewahre eine gemeinsame Releaseversion und kompatible
Schemas. Entferne Tests und Deploymentartefakte aus Runtime-Layern, wenn keine
Laufzeitabhaengigkeit besteht.

Tests: Build zweimal aus sauberem Checkout, SBOM-/Provenance-Pruefung, Signatur-
Negativtest, Vulnerability-Policy, Rollen-Smoke-Tests, Vergleich von Imagegroesse,
CVE-Anzahl, Buildzeit, Startzeit und RAM. Beende nach M7.
~~~~

### Abnahme

- Ein nicht signiertes oder zur Revision unpassendes Image wird vor dem Stoppen der
  laufenden Version abgelehnt.
- Fuer jede Rolle ist der Inhalt des Images begruendet und gemessen.
- Kritische oder nicht freigegebene Schwachstellen stoppen den Releaseprozess.

## M8 – End-to-End-Recovery, Skills und Releaseabschluss

Umsetzungsstand: implementiert am 2026-08-06. Der hermetische Protokollstack,
Failure-Injections, Drei-Szenarien-Restore, gemessene Fixture-RTO/RPO-Grenzen,
generierte Skillprojektion, ADR-0012, Releasecheckliste und serieller
Single-Writer-Canary stehen unter
`docs/architecture/RECOVERY_AND_RELEASE.md`. M8 aktiviert keinen produktiven Stack
und behauptet keinen Remote-Rollback ohne externen Snapshot.

### Ergebnis

Der neue Stack ist unter realistischen Fehlern deploy-, upgrade- und restorebar.
Agentenanweisungen sind knapp, exakt, versionskonsistent und automatisch mit den
Toolvertraegen geprueft.

### Pflichtarbeiten

- Hermetischer Integrationstest-Stack mit Fake-IMAP/SMTP, Fake-WebDAV/CardDAV/CalDAV,
  Fake-Ollama/Marktdaten und kontrollierten ClamAV-Fixtures. Keine produktiven
  Konten oder Secrets in CI.
- Failure-Injection fuer Containercrash, Netzwerkverlust, DB-Lock, volles Volume,
  ungueltige Migration, fehlgeschlagenen Smoke-Test, verlorene Scheduler-Lease und
  Restore-Hook-Fehler.
- Backup/Restore-Rehearsal fuer letzte unterstuetzte Altversion, aktuelle Version und
  fehlgeschlagenes Upgrade; RTO/RPO und Remote-Grenzen dokumentieren.
- `AGENTS.md` auf dauerhafte Betriebsinvarianten fokussieren. Der
  `personal-assistant`-Skill bekommt eine praezise Triggerbeschreibung und
  domänenspezifische Referenzen aus dem Toolvertrag. Veraltete Zweit-Agent-Skills
  werden nach M6-Nachweis entfernt.
- Releasecheckliste und Pilot-/Canary-Verfahren ohne zweiten Writer.

### Entwicklungs-Prompt

~~~~text
Setze ausschliesslich M8 der Container-Architektur-Roadmap um. Verwende keine
produktiven Konten in automatisierten Tests und fuehre keinen produktiven Write-Smoke
ohne die im Betriebsvertrag verlangte verifizierte Sicherung aus.

Baue einen hermetischen End-to-End-Teststack fuer IMAP/SMTP, WebDAV/CardDAV/CalDAV,
Ollama/Marktdaten und Antivirus-Fixtures. Pruefe normale Workflows sowie gezielte
Fehler: Prozessabbruch, Netzwerkverlust, ETag-Konflikt, DB-Lock, volles/read-only
Volume, verlorene Lease, fehlerhafte Migration, fehlgeschlagener Produkt-Smoke-Test
und fehlender externer Restore-Hook. Beweise, dass zu jedem Zeitpunkt hoechstens ein
Mail-Writer aktiv ist und dass ein Rollback den vorherigen lokalen Zustand wirklich
startet. Dokumentiere ehrlich, welche Remote-Aenderungen ohne externen Snapshot nicht
rueckgaengig sind.

Konsolidiere danach die Agentendokumentation. AGENTS.md enthaelt dauerhafte
Sicherheits- und Betriebsinvarianten; der Personal-Assistant-Skill besitzt eine kurze,
praezise Triggerbeschreibung und nach Domaenen getrennte Referenzen. Generiere oder
validiere Befehle, Modes, Approvalregeln und Versionsidentitaet aus dem typisierten
Toolvertrag. Kein Skill darf eine Capability versprechen, die CLI, Registry, Policy
und Test nicht gemeinsam belegen.

Fuehre einen dokumentierten Restore-Drill und anschliessend einen Single-Writer-
Canaryplan durch. Liefere RTO/RPO-Messwerte, offene Remote-Rollbackgrenzen, finale
Architekturdiagramme, ADR-Status und die vollstaendige Releasecheckliste. Beende nach
M8; ein produktives Deployment ist ein separater expliziter Auftrag.
~~~~

### Abnahme

- Ein automatisierter Fehler im neuen Stack fuehrt nachweisbar zum sicheren alten
  lokalen Stand oder bricht vor dem Stoppen des laufenden Stacks ab.
- Skill, CLI, Registry, Policy und Tests bilden denselben Capability-Vertrag ab.
- Die Dokumentation nennt gemessene Recovery-Zeiten und behauptet keinen Remote-
  Rollback, den das System nicht leisten kann.

## Globale Definition of Done fuer jeden Milestone

1. `./scripts/assistant.sh version --verify` ist konsistent.
2. Der vollstaendige, in M0 festgelegte Test- und Lintlauf ist gruen.
3. Neue oder geaenderte Capability ist in CLI, Registry, Policy, Skill/Referenz und
   Regressionstest sichtbar; andernfalls gilt sie als nicht fertig.
4. Daten-/Schemaaenderungen besitzen Upgrade-, Idempotenz-, Konflikt- und
   Restoretests mit realistischen Altfixtures.
5. Keine Secrets, Mails, Dokumentinhalte, produktiven Datenbanken oder Logs gelangen
   in Git, Buildkontext, Testartefakte oder Chat.
6. Sicherheitsgrenzen werden mit Negativtests geprueft, nicht nur durch Suche nach
   Textfragmenten in Implementierungsdateien.
7. Dokumentation, ADR, Changelog und messbare Vorher-/Nachherwerte werden im selben
   PR aktualisiert.
8. `git status`, `git diff --check`, Quellmanifest und Compose-Rendering sind sauber.
9. Der PR implementiert nur den benannten Milestone und listet bewusst verschobene
   Arbeiten auf.
10. Aktivierung, produktive Migration oder Erweiterung externer Rechte erfolgt nur
    nach einem separaten ausdruecklichen Auftrag.

## Empfohlener naechster Schritt

Die technische Roadmap M0-M8 ist abgearbeitet. Der naechste Schritt ist keine
weitere Architekturmutation, sondern eine getrennt beauftragte, anhand der
M8-Releasecheckliste vorbereitete Produktionsentscheidung. Vor einem produktiven
Canary muessen Zielhost-RTO, externer Snapshot-Restore und der serielle
Single-Writer-Uebergang mit eigener Freigabe geplant werden.
