# OpenClaw-Architekturvertrag

Status: verbindlicher Ist-Vertrag ab M1. Owner und Reviewpflichten stehen in
[`owners.json`](owners.json); Entscheidungen werden im [ADR-Verzeichnis](adr/README.md)
nachvollzogen.

Das maschinenlesbare [Komponenten-Inventar](component-inventory.json) klassifiziert
aktive, Kompatibilitaets-, Migrations- und historische Bestandteile. Die
[Kompatibilitaetspolicy](compatibility-policy.json) setzt die direkte
Upgrade-Untergrenze auf `3.4.0-r26.1`; die Entscheidung und Entfernungsevidenz stehen
in [ADR-0010](adr/0010-m6-legacy-und-upgradegrenze.md) und
[`legacy-decisions.json`](legacy-decisions.json).
Recovery, gemessene Fixture-RTO/RPO-Grenzen, Releasecheckliste und das strikt
serielle Single-Writer-Canaryverfahren stehen im
[M8-Recoveryvertrag](RECOVERY_AND_RELEASE.md) und in
[ADR-0012](adr/0012-m8-recovery-und-agentenvertrag.md).

## Systemkontext

OpenClaw ist genau ein lokaler Personal Assistant. Gateway, Modellkoordinator,
Fachworker, Supervisor und CLI sind spezialisierte Prozesse desselben Produkts und
keine voneinander unabhaengigen Agenten. Sie verwenden eine Releaseidentitaet, einen
Toolvertrag und einen hostseitigen Instanzzustand.

```text
Jan / lokaler Administrator
        |
        v
OpenClaw Gateway und registrierte CLI
        |
        +--> Policy + ActionPlan + Audit --> kontrollierte externe Schreibpfade
        |
        +--> Fachworker --> IMAP/SMTP, Nextcloud, EODHD, Ollama, ClamAV
        |
        +--> lokaler Zustand unter /srv/openclaw und Docker-Volume clamav-db
```

Mail, Dokumente, Modellantworten und externe Serverdaten sind unvertrauenswuerdige
Eingaben. Nur Nutzervorgaben, lokale Administrator-Konfiguration und die fest
implementierten Policy-/Toolgrenzen erteilen Autoritaet.

## Containeransicht

Alle Rollen stammen aus demselben Commit und Release. Nach M7 verwenden Gateway,
Fachworker und CLI das volle `OPENCLAW_IMAGE`; der unabhaengige Ollama-Proxy und die
ClamAV-Wartung verwenden nach Messung kleinere Targets. Der verbindliche
[Image-Lieferkettenvertrag](IMAGE_SUPPLY_CHAIN.md) beschreibt Inhalt, Digests, SBOM,
Provenance, Scan, Signatur und Deployment-Gate. Die
[Rollenmatrix](CONTAINER_ROLES.md) dokumentiert Prozess, Mounts, Secrets, Netzwerk
und Schreibrechte; der [Datenkatalog](DATA_CATALOG.md) ordnet persistente Dateien
ihren logischen Ownern zu.

```text
gemeinsamer Commit + Release 3.4.0-r28
├── runtime
│   ├── gateway
│   ├── mail-/sync-/supervisor-/portfolio-/monitor-worker
│   ├── layout-init
│   └── agent-cli             (tools-Profil, kurzlebig)
├── proxy-runtime
│   └── ollama-proxy
└── maintenance-runtime
    └── clamav-update         (Maintenance-Profil, separates Schreibrecht)
```

Seit Layout 3 sind persistente Fachzustaende nach Owner getrennt und werden pro Rolle
read-only oder read-write gemountet. Nur `layout-init` sieht fuer die kurze, gestagte
Migration den gesamten State; Gateway und explizite CLI bleiben Universalrollen.
M4 begrenzt Config-/Secret-Sicht auf einzelne Dateien und ersetzt das Hostnetz durch
explizite Bridge-Netze. Der maschinenlesbare Vertrag ist
[`runtime-hardening.json`](runtime-hardening.json); ADR-0008 dokumentiert die
einzige Host-Gateway-Ausnahme fuer den Ollama-Proxy.

## Komponentenansicht

| Komponente | Verantwortung | Darf nicht umgehen |
| --- | --- | --- |
| `scripts/assistant.sh` und `personal_assistant/cli.py` | stabiler administrativer und Agenten-CLI-Einstieg | Toolregistry, Freigaben, Releasepruefung |
| `personal_assistant/tool_catalog/` | typisierte domaenennahe Toolvertraege samt Schema, Approval, Doku und Testanker | Live-Rechte und Policyentscheidungen |
| `personal_assistant/tool_registry.py` | kleine Projektion des statischen Katalogs auf konfigurierte Live-Werkzeuge | statischen Katalog als Instanzrecht ausgeben |
| `personal_assistant/cli_handlers/` | domaenenbezogene CLI-Ausfuehrung bei stabilem JSON-/Exitvertrag | Policy- und Approvalpruefungen |
| `personal_assistant/contracts/` | infrastruktur-neutrale Typen und Ports | konkrete Connector- oder `mail_agent`-Imports |
| `personal_assistant/bootstrap.py` und `adapters/` | Composition Root und konkrete Infrastrukturadapter | Berechtigungen erteilen |
| `personal_assistant/service.py` | Orchestrierung von Ressourcen, Suche und verbleibenden Fachdiensten | `policy.py`, `actions.py`, Connectorgrenzen |
| `personal_assistant/policy.py` | harte lokale Zulassungsentscheidungen | keine Modellentscheidung darf erweitern |
| `personal_assistant/actions.py` | ActionPlan, Idempotenz, Approval, Ausfuehrung und Audit | exakte Ressource und konfigurierte Rechte |
| `personal_assistant/connectors/` | eingeschraenkte Protokolladapter | Policy und ActionPlan |
| `personal_assistant/storage.py` | getrennte Core-/Wissensdatenbanken, ActionPlans, Index und Audit | Datenbankmigrationen |
| `mail_agent/` | IMAP-Triage, Klassifikation und mailspezifische Verarbeitung | Antivirus, Single-Writer, ActionBridge |
| `mail_agent/assistant_bridge.py` | Uebergang von Mailerkennung zu kontrollierten Assistant-Aktionen | Policy, Idempotenz und Audit |
| `docker/job_loop.py` | allowlist-basierter periodischer Worker-Dispatch | persistenter Sollzustand und Scheduler-Lease |
| `docker/entrypoint.sh` | Containerinitialisierung, Layoutmigration und read-only Release-Links | Config-/Daten-Erhaltung |

Die erzwungene Richtung ist `CLI/Worker -> Bootstrap -> Core -> Ports` und
`Bootstrap -> Adapter`; fachliche Adapter implementieren Ports und duerfen zum Core
zeigen. Core-Module importieren keine konkrete `mail_agent`-Infrastruktur. Ein
AST-basierter Schichtentest prueft diese Grenze und den gesamten internen
Importgraphen auf Zyklen.

## Runtime- und Datenmodell

- Programmcode und Defaults stammen aus dem Image unter `/opt/openclaw-agent`.
- Alle normalen Rollen besitzen ein read-only Root-Dateisystem und starten Shell-,
  Python- und Worker-Code ueber feste Pfade unter `/opt/openclaw-agent`.
- Persistenter Zustand liegt hostseitig unter `/srv/openclaw/state/v3`; Fachbereiche
  erscheinen unter `/var/lib/openclaw/<bereich>`, Instanz und Gateway getrennt unter
  `/home/node/.openclaw`.
- ActionPlan/Audit liegen in `shared/core/assistant.sqlite3`; Dokumente, FTS und
  Sync-Cursor liegen separat in `domains/knowledge/knowledge.sqlite3`.
- Der Mailworker veroeffentlicht unter `domains/mail/search_documents` eine
  checksumgebundene, atomare Suchprojektion. Der v2-Vertrag aus
  [ADR-0026](adr/0026-versionierter-mail-suchdatenvertrag.md) trennt immutable
  Contents von Occurrences und veraenderlichen Locatorn und publiziert
  wiederverwendbare Ordnerpartitionen nur durch ein atomisches Root. Der
  Sync-Worker liest diese Quelle read-only, validiert Alter, Digests und Coverage
  vor dem ersten Indexwrite und oeffnet die Mail-SQLite samt WAL nicht; die letzte
  vollstaendige Generation liegt im Wissens-Sync-Status. V1 bleibt lesbar. Der
  begrenzte M11.2-Crawler aus
  [ADR-0027](adr/0027-begrenzter-mail-vollkonto-backfill.md) publiziert v2 nur in
  ein getrenntes Mail-Owner-Staging mit seitenweisem Checkpoint und
  fail-closed Raw-/Anhangscan. Der M11.3-Reconciler aus
  [ADR-0028](adr/0028-transaktionale-mail-reconciliation.md) publiziert nur nach
  einem vollstaendigen autoritativen Ordnerabgleich Locatorwechsel und
  Tombstones, verwendet unveraenderten Content bis zu Chunks/FTS/Embeddings
  wieder und uebergibt Deltas samt Cursor transaktional an den Wissensindex.
  Seine Allowlist-Policy ist nicht als Job aktivierbar; der aktuelle
  Himalaya-Connector bleibt ohne belegte UID-/UIDVALIDITY-Semantik fail-closed.
  Die sichere lokale M11.4-Suche aus
  [ADR-0029](adr/0029-sichere-lokale-mail-lexik-und-tags.md) setzt eine typisierte
  Queryschicht vor feldgetrenntes Mail-FTS, filtert ueber belegte lokale Tags und
  gruppiert Chunks vor dem Ergebnislimit. Sie schreibt keine Maildaten und
  ersetzt bis zur Live-Locator-/Fallback-Abnahme nicht die aktuelle Serversuche.
  Der konservative M11.5-Graph aus
  [ADR-0030](adr/0030-konservative-mail-threads-und-kontext.md) bildet
  Headerbeziehungen azyklisch ab, trennt stabile Threadmetadaten von Locatorn und
  kennzeichnet einen engen Betreff-/Teilnehmerfallback stets als unsicher.
  Optionaler Kontext bleibt getrennt vom Querytreffer; Rankingtext darf
  wiederholte Zitate reduzieren, waehrend der zitierbare Originalchunk erhalten
  bleibt. Der M11.6-Vertrag aus
  [ADR-0031](adr/0031-versionierte-lokale-mail-embeddings.md) bindet lokale
  Float32-Vektoren an Content-/Retrieval-SHA, Chunk und vollen Modelldigest.
  Locatorwechsel teilen den Cache; alle realen Anfragen muessen durch den
  Ollama-Prioritaetsproxy. Exakte Kosinussuche ist die gemessene korrekte
  Ausgangsimplementierung und degradiert bei jedem Modellfehler sichtbar auf
  FTS. Kein echtes Modell, produktiver Job oder neue Suchpraeferenz ist aktiviert.
  Die M11.7-Orchestrierung aus
  [ADR-0032](adr/0032-hybrid-mail-search-und-live-locator.md) verwendet den
  lokalen Hybridpfad nur bei vollstaendiger, autoritativer und frischer
  Generation samt aktuellem Locator fuer jeden Content. Sie fusioniert
  erklaerbare Lexik-, Semantik-, Filter- und Threadsignale, validiert nur die
  Locator der positiven Treffer live und faellt bei jeder Unsicherheit sichtbar
  auf die Serversuche. `mail read` prueft Ordner, Mailbox-ID und erwarteten
  Betreff erneut; der Index autorisiert keine Aktion. M11.7 aktiviert weiterhin
  weder einen Indexjob noch ein Embeddingmodell.
- Instanzkonfiguration und Secrets liegen getrennt unter `/srv/openclaw/config` und
  `/srv/openclaw/secrets`; Rollen sehen daraus nur benoetigte read-only Dateien.
- ClamAV-Signaturen liegen im Docker-Volume `clamav-db`; nur `clamav-update` schreibt.
- Der Agent-Workspace enthaelt Instanzkonfiguration und kontrollierte lokale
  Dokumente. Sessions gehoeren dem Gateway-Teilbaum; fachliche Daten liegen in ihren
  Owner-Teilbaeumen. `AGENTS.md`, `HEARTBEAT.md` und der Runtime-Skill stammen
  weiterhin aus dem read-only Image. Das abgeschlossene persoenliche Profil aus
  `IDENTITY.md`, `SOUL.md`, `USER.md` und dem Workspace-Setupstatus bleibt dagegen
  persistente Instanzkonfiguration; seine attestierungsgebundene Layoutmigration
  ist in [ADR-0014](adr/0014-abgeschlossenes-workspace-profil.md) festgelegt.

## Sicherheits- und externe Grenzen

Die [Trust-Boundary-Dokumentation](TRUST_BOUNDARIES.md) beschreibt Eingaben, Secrets,
externe Reads/Writes, ActionPlan und Backupgrenzen. Wesentliche Invarianten sind:

1. genau ein produktiver Mailwriter,
2. externe Writes nur ueber registrierte, eng begrenzte Werkzeuge,
3. kein Ueberschreiben oder Loeschen ohne expliziten Vertrag,
4. ETag/`If-Match`, Idempotenz und Audit fuer bestehende Remoteobjekte,
5. ClamAV fail-closed vor Attachment- oder Uploadverarbeitung,
6. ein lokales Rollback ist kein Rollback bereits erfolgter Remoteaenderungen.

## Health, Backup und Rollback

Jeder langlebige Prozess besitzt einen rollenspezifischen Docker-Healthcheck. Ein
gesunder Prozess beweist nur Prozess- und Heartbeat-Gesundheit; der fachliche
Sollzustand wird separat in `job_control.json` gefuehrt. Release-Deployments stoppen
Writer, erzeugen und verifizieren einen lokalen Restorepunkt und fuehren einen
begrenzten Smoke-Test aus. Externe Restore-Hooks sind erforderlich, wenn Remote-Writes
vollstaendig rueckrollbar sein muessen.
Der M8-Drill prueft r26.1, den aktuellen Stand und ein fehlgeschlagenes Upgrade
bytegenau in temporaeren Roots. Ein fehlender externer Restore-Hook stoppt vor dem
Container-Down; ein laufzeitlich fehlschlagender Hook verhindert den lokalen
Wiederanlauf des alten Stands nicht, bleibt aber ein sichtbarer Rollbackfehler.

Native systemd-Writer sind kein primaerer Runtimepfad. Sie bleiben nur als explizit
verifizierte Legacy-Rollback-Untergrenze unter `legacy/systemd/` erhalten und
duerfen niemals parallel zu Container-Writern laufen. Das eingefrorene Paket besitzt
ein eigenes SHA-256-Manifest und kann ohne
`OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES` nicht durch seinen Intervallhelfer aktiviert
werden.

Portfolio-Research verwendet EODHD als einzige Faktenquelle und ein
quellcodeversioniertes deterministisches Mehrfaktormodell. Research- und
Profilhistorie bleiben beim Portfolio-Datenowner in dessen SQLite-Datenbank.
Sprachmodelle duerfen die belegten Ergebnisse erklaeren, aber weder Fakten noch
Scores ergaenzen. Die bestaetigte Investmentphilosophie ist append-only und wird
durch beobachtetes Feedback nie automatisch veraendert; die Entscheidung und
Enthaltungsgrenzen stehen in
[ADR-0016](adr/0016-providergebundenes-portfolio-research.md).

## Weiterentwicklung

Neue Komponenten und Tools folgen den [Erweiterungsregeln](EXTENDING.md), den
[Git-/Reviewregeln](../../CONTRIBUTING.md) und den vorhandenen ADRs. Offene
Architekturfragen werden als ADR mit Status `Proposed` erfasst; Dokumentation allein
behauptet keine noch nicht implementierte Isolation.
