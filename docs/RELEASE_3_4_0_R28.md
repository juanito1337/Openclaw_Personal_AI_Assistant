# Release 3.4.0-r28: sichere Containerarchitektur, Mail- und Rechnungsqualitaet

Stand: 2026-08-18
Vorgaenger: `3.4.0-r27.2.5`
Git-Release-Tag: `r28`
Status: kumulativer Main-Release; produktive Installation bleibt ein separater,
ausdruecklich auszufuehrender Betriebsschritt

## Zweck und Umfang

`3.4.0-r28` fuehrt die zuvor auf Entwicklungs- und Testbranches einzeln
abgenommenen Milestones M0 bis M10 zusammen. Der Release modernisiert die
technische Basis vom gewachsenen Einzelprozess zu einem rollengetrennten
Docker-Stack, erweitert die nachvollziehbare Mail-Triage und fuehrt eine
beleggebundene Rechnungs-Neubewertung ein.

Die Promotion nach `main` aendert keinen laufenden Host. Sie startet keine Jobs,
veraendert keine Datei unter `/srv/openclaw` und fuehrt keine Mail-, Nextcloud-,
Kalender-, Kontakt-, Aufgaben-, Deck- oder Portfolioaktion aus. Erst der gesonderte
Deploymentpfad installiert die signierten Images und erstellt davor die
vorgeschriebenen Sicherungen.

## Wichtigste Ergebnisse

- Ein Personal Assistant laeuft als modularer Monolith in getrennten Containern.
  Gateway, Fachworker und Diagnose-CLI bleiben dasselbe Produkt und dieselbe
  Releaseidentitaet; sie sind keine voneinander unabhaengigen Agenten.
- Programmcode, Skills und Releasevertrag kommen unveraenderlich aus dem Image.
  Instanzkonfiguration, Secrets, Datenbanken und Laufzeitdaten bleiben unter
  `/srv/openclaw` ausserhalb des Images.
- Jede externe Schreibdomaene besitzt hoechstens einen produktiven Writer.
  Insbesondere duerfen alter systemd-Mailwriter und Container-Mailworker nie
  gleichzeitig laufen.
- Runtime-, Proxy- und Maintenance-Image entstehen aus demselben Git-Commit. Sie
  werden reproduzierbar gebaut, gescannt, mit SBOM und SLSA-Provenance versehen,
  keyless signiert und vor dem Deployment anhand ihres Digests verifiziert.
- Der Agent verwendet registrierte, typisierte Werkzeuge statt erfundener
  Shellbefehle. Tool-ID, exakter CLI-Befehl, Effekt, Freigabe und Verfuegbarkeit
  stammen aus einem gemeinsamen Vertrag.
- Mail-Unsicherheit besitzt eine geschlossene Review-Taxonomie und kontrollierte
  Einzelkorrekturen. Historische Prueffaelle werden nicht automatisch bewegt.
- Rechnungsfelder entstehen aus lokaler, versionierter Evidenz. Unsichere oder
  widerspruechliche Nummern, Daten und Betraege bleiben sichtbar in Pruefung.
- Eine Rechnungs-Neubewertung trennt read-only Vorschau, explizite
  Einzeluebernahme, Registerwirkung und Audit. Es existiert kein Bulk-Apply.

## Zielarchitektur

```text
Jan / lokaler Administrator
        |
        v
OpenClaw Gateway + registrierte CLI
        |
        +--> Ollama-Prioritaetsproxy --> lokales Ollama
        |
        +--> Mail-Worker -------------> IMAP/SMTP, ClamAV, Nextcloud
        +--> Portfolio-Worker --------> EODHD
        +--> Sync-Worker -------------> freigegebene read-only Quellen
        +--> Monitor-Worker ----------> technische Zustandsmessung
        +--> Supervisor-Worker -------> Sollzustand, Heartbeats, Alarme
        |
        +--> Policy + ActionPlan + Audit
                    |
                    +--> kontrollierte externe Schreibpfade

unveraenderliche Images              persistenter Hostzustand
runtime / proxy / maintenance        /srv/openclaw/{state,config,secrets,backups}
```

Der Stack verwendet explizite Bridge-Netze. Nur der Gateway-Port wird
standardmaessig an Host-Loopback publiziert. Der Ollama-Proxy ist die einzige
dokumentierte Host-Gateway-Ausnahme. Secrets werden rollenbezogen als einzelne
read-only Dateien eingebunden; Fachworker erhalten nur die fuer ihre Aufgabe
erforderlichen State-Mounts.

## Milestone-Uebersicht

| Milestone | Ergebnis |
| --- | --- |
| M0 | Einheitlicher pytest-Pfad, Collection-Untergrenze, gepinnte Werkzeuge, Lints, Wheel- und Manifestvertrag |
| M1 | Verbindlicher Architekturvertrag, Owner, ADRs, Git- und Erweiterungsregeln |
| M2 | Ausfuehrung ausschliesslich aus unveraenderlichem Releasecode im Image |
| M3 | Eindeutige Datenowner, Layout 3, rollenbezogene Mounts und SQLite-Nebenlaeufigkeitsvertrag |
| M4 | Minimale Rechte, read-only Rootfs, getrennte Netze, Config- und Secretgrenzen |
| M5 | Typisierte Domaenenwerkzeuge, stabile CLI und generierte Agentenbeschreibung |
| M6 | Evidenzbasierte Bereinigung, isolierte Legacy-Kompatibilitaet und Upgrade-Untergrenze |
| M7 | Reproduzierbare Rollenimages, SBOM, Provenance, Scans, Signatur und Digest-Gate |
| M8 | Recovery-Drill, Single-Writer-Canary, Rollbackvertrag und hermetische Integration |
| M9 | Mail-Review-Taxonomie, Triage, Einzelkorrektur, Lernqualitaet und Suchprojektion |
| M10 | Belegte Rechnungsfelder, OCR-Fusion, Vorschau, Einzeluebernahme und Backlog-Audit |

## Mail-Qualitaet in M9

M9 trennt eine fachlich unsichere Mail von einem technischen Verarbeitungsfehler.
Der persistente `review_reason` stammt aus einer geschlossenen Taxonomie; unklare
Altfaelle werden als `unknown-legacy` erhalten. Read-only Werkzeuge liefern
Aggregate, begrenzte Listeneintraege und einen Vorschlag fuer genau eine anhand
von Ordner, Mailbox-ID und erwartetem Betreff identifizierte Nachricht.

Eine Korrektur bewegt genau eine Nachricht erst nach ausdruecklicher Freigabe. Sie
sendet nichts, loescht nichts und fuehrt kein `EXPUNGE` aus. Die Aktivierung von
`Agent/Relevant`, ein Jobstart und die Bearbeitung eines historischen Backlogs
sind weiterhin getrennte Betriebsvorgaenge.

Der Mailworker veroeffentlicht fuer die Wissenssynchronisation eine atomare,
SHA-verifizierte Suchprojektion. Der Sync-Worker oeffnet nicht mehr die aktive
Mail-SQLite samt WAL/SHM. Eine fehlende, alte oder korrupte Projektion bleibt als
sichtbarer Syncfehler erhalten, statt unvollstaendige Daten zu indexieren.

Die verbindlichen Betriebsgrenzen stehen in
[`MAIL_QUALITY_ROLLOUT.md`](MAIL_QUALITY_ROLLOUT.md) und im
[Mail-Skillvertrag](../skills/personal-assistant/references/mail.md).

## Rechnungsqualitaet in M10

M10 ersetzt breite Heuristiken durch typisierte Feldkandidaten:

- Rechnungsnummern benoetigen einen belastbaren Anker und begrenzten Kontext.
  Kunden-, Bestell-, Liefer-, Vertrags-, Telefon-, Steuer-, Tracking- und
  IBAN-Felder sind explizite Ausschlussrollen. Ein Dateiname ist nie alleinige
  Evidenz.
- Rechnungs-, Leistungs- und Faelligkeitsdatum sind getrennte Rollen.
  Widersprueche fuehren fail-closed zu `review`.
- Brutto, Netto, Steuer, Steuersatz, Einzelpreis, Zwischensumme und Zahlbetrag
  werden typisiert und rechnerisch plausibilisiert. Der groesste Geldbetrag ist
  nicht automatisch der Rechnungsbetrag.
- Native PDF-Texte bleiben primaer. Lokale Tesseract-OCR wird nur bei
  unbrauchbaren Pflichtfeldern, fuer begrenzte Seiten und mit festem Budget
  ausgefuehrt. Feldweise Fusion bewahrt Konflikte; Ollama erzeugt keine fehlenden
  Nummern, Daten oder Betraege.

`invoices reprocess ... --dry-run` erzeugt eine schreibfreie Alt-/Neu-Vorschau
mit PDF-Hash und deterministischem `preview_sha256`. Nur ein neuer expliziter
Auftrag darf genau diesen Hash und Digest mit `invoices reprocess-apply ... --yes`
uebernehmen. Drift, manuell geschuetzte Werte, offene Konflikte, unplausible
Arithmetik oder ein ETag-Fehler stoppen die Uebernahme.

`invoices audit` liefert ausschliesslich aggregierte Triageinformationen. Es gibt
keine automatische historische Neubewertung, keine automatische Verschiebung von
PDFs und keinen Bulk-Apply. Der gesonderte
[`INVOICE_M10_ROLLOUT.md`](INVOICE_M10_ROLLOUT.md) bleibt fuer jede produktive
Einzeluebernahme verbindlich.

## Betriebs- und Diagnosekorrekturen

Die Testinstallation von M9/M10 hat mehrere reale Rollengrenzen sichtbar gemacht.
Der Release enthaelt die daraus entstandenen Regressionen:

- Der Sync-Worker darf Live-Discovery verwenden, ohne die read-only
  Core-Registry zu beschreiben. Er schreibt nur in Wissensindex und Koordination.
- Der Monitor kann geschlossene SQLite-Datenbanken auf read-only Mounts
  nebenwirkungsfrei lesen. Ein vorhandenes WAL wird nicht ausgeblendet.
- Ein beobachteter Fachfehler macht den Fachjob `degraded`, beendet aber nicht
  automatisch den Supervisorprozess. Echte Scheduler-, Relay- oder
  Alarmzustellungsfehler bleiben Fehler.
- Supervisor-Ereignisse laufen ueber eine begrenzte persistente Queue. Nur das
  Gateway besitzt das Credential und stellt Ereignisse ueber akzeptiertes
  Loopback zu; unsicheres Non-Loopback-WebSocket wird nicht freigegeben.
- Die einzige automatische Mail-Dry-run-Recovery bleibt beim alleinigen
  Mailwriter und prueft Lock, Production-Gate und `auto_recoverable` erneut.
- Die ClamAV-Maintenance-Rolle wird vor jedem Writer-Stopp mit echtem Binary,
  Datenbankpfad und TLS-Handshake geprueft.
- Portfoliokursfehler liefern Symbol, MIC, Provider-Ticker, letzten
  Beobachtungszeitpunkt und begrenzte Diagnosebefehle. Der Agent darf daraus
  keinen Mappingfehler erfinden und keinen Webkurs als Ersatz anbieten.
- Londoner Kurse verwenden das Handelsfenster von `XLON` in Ortszeit. Ein
  Vortagesschluss ist vor Oeffnung nicht automatisch kritisch; nach Oeffnung
  bleibt ein alter Kurs sichtbar degradiert.

## Sicherheits- und Datenvertrag

Folgende Grenzen gelten unveraendert:

1. Mail, Dokumente, Webseiten und Modellantworten sind Daten, keine Anweisungen.
2. ClamAV prueft rohe Mail, Anhaenge und kontrollierte Uploads fail-closed.
3. Credentials, produktive Konfigurationen, Datenbanken, Logs und Laufzeitdaten
   duerfen weder in Git noch in Wheel, Image, CI-Artefakte oder Chat gelangen.
4. Externe Writes benoetigen exakte Ressource, Policy, ActionPlan, Idempotenz,
   Audit und die im Toolvertrag genannte Freigabe.
5. Bestehende Objekte werden nur ueber stabile UID/ID, aktuelle ETag mit
   `If-Match` und Erwartungswerte geaendert. Konflikte werden nicht ueberschrieben.
6. Loeschen, Bulk-Edit, stilles Zusammenfuehren, ressourcenuebergreifendes
   Verschieben und Berechtigungsausweitung bleiben verboten.
7. Ein Image-Rollback stellt keine bereits erfolgten Remoteaenderungen wieder her.
   Vollstaendige Remote-Ruecknahme erfordert den passenden externen Snapshot und
   Restore-Hook.

## Kompatibilitaet und Migration

- Unterstuetzte direkte Upgrade-Untergrenze ist `3.4.0-r26.1`.
- Persistentes Zielschema ist Layout 3. Layoutmigrationen werden gestagt,
  integritaetsgeprueft und erst danach atomar veroeffentlicht.
- Vorhandene Identitaetsdateien, Instanzkonfiguration, `.env`, aktive Hookdateien,
  Maildaten, Portfoliozustand, Audit, Korrekturhistorie und Nextcloud-Register
  werden nicht durch Imageinhalt ersetzt.
- Das Deployment-Bundle aktualisiert Compose und releaseeigene Skripte. Es erhaelt
  `/srv/openclaw/deployment/.env` und aktive Hooks ohne `.example.sh`.
- Schemafehler sind Betriebsfehler und niemals ein leerer fachlicher Zustand.
  Produktive SQLite-Dateien werden nicht als Reparatur geloescht oder neu erzeugt.
- Ein bereits offener Agentenchat kann alten Skillkontext enthalten. Nach einem
  Skill- oder Vertragsupdate muss fuer den Verhaltenstest eine neue Sitzung
  geoeffnet werden.

## Reproduzierbare Releaseabnahme

Der verbindliche lokale und CI-Pfad lautet:

```bash
./scripts/assistant.sh version --verify
./scripts/bootstrap-dev.sh
./scripts/check-repo.sh
./scripts/check-wheel.sh
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
docker compose --env-file docker/deployment.env.example -f compose.yaml -f compose.build.yaml config --quiet
git diff --check
```

Die versionierte Collection-Untergrenze fordert 720 pytest-Items, darunter
mindestens 654 unittest-kompatible Tests und mindestens 13 freie
Rechnungs-pytest-Tests. Der letzte M10-Kandidat fuehrte zusaetzlich 80 Subtests
aus. Die dokumentierte Gesamt-Coverage liegt bei 64,25 Prozent einschliesslich
Branches; diese Zahl ist eine gemessene Baseline und keine nachtraeglich gewaehlte
Freigabeschwelle.

Der Repositorycheck umfasst Quellmanifest, Komponenten-Inventar,
Legacy-Paketintegritaet, ShellCheck, Hadolint, Compose, Ruff, mypy,
Python-Kompilierung, pytest mit Branch-Coverage, Architektur-/Tooldokumentation und
den lokalen Recovery-Drill. Das Wheel wird aus einem sauberen Checkout gebaut, in
einer frischen Umgebung installiert und dort erneut getestet.

Die dynamische Containerabnahme verwendet ausschliesslich neue Testimages,
temporaere Container, interne Netze und `.invalid`-Fixtures. Sie baut und prueft
Runtime, Proxy und Maintenance, scannt Rootfs und Quellen, erzeugt SBOM und
Provenance, prueft reproduzierbare OCI-Archive und fuehrt den hermetischen
M8-Protokoll-/Fehlerstack aus. Sie greift nicht auf den laufenden produktiven
Compose-Stack zu.

## Git- und Artefaktveroeffentlichung

Der freigegebene Ablauf ist:

1. Releasecommit auf Entwicklungs- und `test/**`-Branch pushen.
2. CI und den signierten Containerworkflow fuer exakt diesen Commit abnehmen.
3. Denselben Commit ohne Force-Push nach `main` vorspulen.
4. CI und den nun auch fuer `main` automatisch startenden Containerworkflow erneut
   abnehmen.
5. Erst danach den annotierten Tag `r28` auf exakt diesen Main-Commit setzen.
6. Runtime-, Proxy- und Maintenance-Digest aus dem Workflow sichern. Ein Tag ist
   keine unveraenderliche Deploymentreferenz.

Der Containerworkflow publiziert Images fuer `main`, `test/**` und Release-Tags.
Jeder Digest muss Release `3.4.0-r28`, die exakte 40-stellige Git-Revision und die
erwartete Rolle tragen. Cosign-Signatur, SPDX-SBOM- und SLSA-Provenance-
Attestierung werden unmittelbar nach dem Push erneut verifiziert.

## Checkout auf den neuen Main-Stand aktualisieren

In einem bereits mit Git verbundenen Arbeitsordner:

```bash
git fetch origin --tags
git switch main
git pull --ff-only origin main
./scripts/assistant.sh version --verify
git status --short
```

Die Verifikation muss `OpenClaw Local Personal Assistant` und
`3.4.0-r28` melden. Ein nicht leerer `git status --short` ist vor einer
Installation zuerst bewusst zu pruefen; lokale Benutzerarbeit wird nicht
ueberschrieben.

## Produktive Installation

Die Main-Promotion installiert noch nichts. Fuer die Installation muessen die
GitHub-Actions-Jobs `CI` und `Container image` fuer denselben Main-Commit gruen
sein. Aus der Zusammenfassung von `Container image` werden die drei vollstaendigen
`name@sha256:...`-Referenzen uebernommen.

Danach wird zuerst nur das Deployment-Bundle aktualisiert:

```bash
./docker/scripts/refresh-deployment.sh
```

Der eigentliche, zustandsveraendernde Schritt wird bewusst mit den echten Digests
ausgefuehrt. Platzhalter duerfen nicht unveraendert kopiert werden:

```bash
sg docker -c 'cd /srv/openclaw/deployment && set -a && . ./.env && set +a && export OPENCLAW_EXPECTED_SOURCE_REVISION="<40-stelliger-main-commit>" && ./scripts/deploy.sh "ghcr.io/juanito1337/openclaw_personal_ai_assistant@sha256:<runtime>" "ghcr.io/juanito1337/openclaw_personal_ai_assistant@sha256:<proxy>" "ghcr.io/juanito1337/openclaw_personal_ai_assistant@sha256:<maintenance>"'
```

`deploy.sh` prueft alle drei Signaturen, Attestierungen, Rollen, Release und
Revision, bevor es den laufenden Stack veraendert. Danach stoppt es die Writer,
erstellt und testet das lokale Releasebackup, fuehrt den gestuften Produktsmoke
aus und startet die Worker erst nach Erfolg. Bei einem Fehler wird der verifizierte
lokale vorherige Stand automatisch restauriert.

`docker/scripts/live-test-branch.sh` bleibt absichtlich auf `test/**` begrenzt und
ist nicht der Main-Installationspfad.

## Abnahme nach der Installation

Die folgenden Befehle sind read-only und pruefen Release, Rollen und Fachzustand:

```bash
sg docker -c 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"'

sg docker -c 'cd /srv/openclaw/deployment && docker compose --env-file .env --profile tools run --rm --no-deps agent-cli /opt/openclaw-agent/scripts/assistant.sh version --verify'

sg docker -c 'cd /srv/openclaw/deployment && docker compose --env-file .env --profile tools run --rm --no-deps agent-cli /opt/openclaw-agent/scripts/assistant.sh jobs status --target all --deep'

sg docker -c 'cd /srv/openclaw/deployment && docker compose --env-file .env --profile tools run --rm --no-deps agent-cli /opt/openclaw-agent/scripts/assistant.sh mail status'

sg docker -c 'cd /srv/openclaw/deployment && docker compose --env-file .env --profile tools run --rm --no-deps agent-cli /opt/openclaw-agent/scripts/assistant.sh invoices status'

sg docker -c 'cd /srv/openclaw/deployment && docker compose --env-file .env --profile tools run --rm --no-deps agent-cli /opt/openclaw-agent/scripts/assistant.sh portfolio doctor'
```

Ein Fachsystem darf dabei sichtbar `degraded` sein, wenn seine echten externen
Daten fehlen oder alt sind. Das ist nicht dasselbe wie ein gesunder Fachzustand.
Die Ursache muss anhand des registrierten Doctors und `jobs check --target all
--deep` untersucht werden; Credentials, Rechte oder Jobs werden nicht automatisch
geaendert.

## Rollback und Wiederherstellungsgrenzen

Der erfolgreiche Deploy-Lauf gibt eine Backup-ID aus. Ein lokaler Rollback erfolgt
nur bei gestoppten Writern ueber den dokumentierten `rollback.sh`-Pfad und genau
diese verifizierte ID. Archivpruefsumme, Test-Restore und SQLite-Integritaet muessen
vor dem Start des alten Rollensatzes gruen sein.

Das lokale Releasebackup umfasst den lokalen OpenClaw-Zustand, Konfiguration und
Secrets. Es kann keine bereits erfolgreich verschobene Mail, geaenderte
Nextcloud-Datei, CardDAV-Karte, CalDAV-Komponente oder Deck-Karte zuruecksetzen.
Wenn solche Writes Teil des Canary sind, muessen der zugehoerige externe Snapshot
und Restore-Hook vor dem Deployment nachweislich funktionieren. Ohne externen
Restore bleibt der Remotezustand bei einem Rollback ausdruecklich unklar.

## Bekannte Grenzen und bewusst offene Punkte

- Die 48 historisch beobachteten Rechnungs-Prueffaelle werden nicht automatisch
  neu bewertet. Jede Uebernahme braucht eine neue Vorschau und Einzelentscheidung.
- Der M9-Rollout verschiebt den historischen Inhalt von `Agent/Pruefen` nicht
  automatisch. Eine Backlog-Triage bleibt ein eigener Auftrag.
- EODHD-Research-Endpunkte koennen vom gebuchten Tarif mit HTTP 403 abgelehnt
  werden. Der Agent liefert dann `abstain` und verwendet keine erfundenen oder
  stillen Ersatzdaten.
- Ein veralteter Aktienkurs darf keine Gesamtbewertung vortaeuschen. Die
  EUR-Bewertung bleibt bei fehlenden oder kritisch alten Aktien- oder FX-Daten
  fail-closed.
- Produktions-RTO und Remote-RPO sind ohne reale externe Restore-Uebung keine
  zugesicherten SLA-Werte. Die versionierten M8-Werte betreffen kleine lokale
  Fixtures.
- Bestehende offene Chats koennen alten Skillkontext enthalten. Fuer die
  Funktionsabnahme nach Installation ist eine neue Agentensitzung erforderlich.

## Weiterfuehrende Dokumente

- [Architekturvertrag](architecture/README.md)
- [Rollen- und Mountmatrix](architecture/CONTAINER_ROLES.md)
- [Datenkatalog](architecture/DATA_CATALOG.md)
- [Image-Lieferkette](architecture/IMAGE_SUPPLY_CHAIN.md)
- [Recovery- und Releasevertrag](architecture/RECOVERY_AND_RELEASE.md)
- [Docker-Betriebsanleitung](DOCKER_DEPLOYMENT.md)
- [Build- und CI-Nachweise](BUILD_AND_CI.md)
- [Testanleitung und Baseline](TESTING.md)
- [Mail-Rolloutvertrag](MAIL_QUALITY_ROLLOUT.md)
- [Rechnungs-Rolloutvertrag](INVOICE_M10_ROLLOUT.md)
- [Chronologischer Changelog](../CHANGELOG.md)
