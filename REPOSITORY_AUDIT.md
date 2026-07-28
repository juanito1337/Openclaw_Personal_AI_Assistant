# Repository-Audit R27.0

Ausgangsbasis ist der am 27.07.2026 bereitgestellte produktive OpenClaw-
Workspace `3.4.0-r26.4`. R27.0 ist kumulativ und enthaelt weiterhin die
Rechnungs-, Ollama-Zuverlaessigkeits-, Kontakt-, Kalender-, Task- und
Capability-Korrekturen aus R26 bis R26.4.

## Neu in R27.0

- unveraenderliches Docker-Image auf Basis des offiziellen OpenClaw-Images
- Programmcode und Abhaengigkeiten im Image; produktiver Zustand ausserhalb
- persistenter OpenClaw-Zustand unter `/srv/openclaw/state`
- getrennte Hostverzeichnisse fuer Konfiguration, Secrets und Backups
- getrennte Container fuer Gateway, Ollama-Prioritaetsproxy, Mail-Worker,
  Index-Synchronisation und Supervisor
- einmalige Migration des bisherigen `~/.openclaw`-Live-Zustands
- verifiziertes Pre-Update-Backup mit SHA-256, Manifest, SQLite-Quick-Check
  und testweisem Restore in ein temporaeres Verzeichnis
- begrenzter schreibender Produkttest nach dem Containerwechsel
- automatischer Rollback auf Image und lokalen Datenstand
- verpflichtende externe Backup- und Restore-Hooks, wenn der Produkttest
  IMAP, Nextcloud, CardDAV oder CalDAV veraendern darf
- GitHub-Actions-Workflow fuer Tests und das private GHCR-Image
- Aufloesung eines Release-Tags auf einen unveraenderlichen Registry-Digest

## Persistenz- und Sicherheitsgrenzen

Nicht im Image gespeichert werden produktive Konfigurationen, Passwoerter,
Tokens, Datenbanken, Sitzungen, E-Mails, Rechnungen, Logs, Lockdateien oder
persoenliche Agentendaten. Diese Daten werden als Host-Mounts bereitgestellt.

Das lokale Release-Backup umfasst `state`, `config` und `secrets`. Aenderungen
an externen Systemen lassen sich dadurch allein nicht rueckgaengig machen.
Deshalb verweigert `deploy.sh` den schreibenden Produkttest standardmaessig,
wenn kein ausfuehrbarer externer Backup- und Restore-Hook konfiguriert ist.

Es darf niemals gleichzeitig ein alter systemd-Writer und ein Docker-Writer
auf denselben Produktivdaten arbeiten. Das Migrations- und Deployment-Skript
stoppt die bekannten alten Writer vor dem Backup und Containerstart.

## Nicht in diesen Quellstand uebernommen

- produktive SQLite-Datenbanken, WAL/SHM-Dateien, Logs und Lockdateien
- E-Mail-Inhalte, Anhaenge, Rechnungen, Suchindizes und Lernexporte
- produktive Nextcloud-, CardDAV- und CalDAV-Ressourcenkennungen
- produktive Mail- und Personal-Assistant-Konfigurationen
- Passwoerter, App-Tokens, private Schluessel und Geraeteidentitaeten
- lokale Backups, Caches, Sitzungen, Agententrajektorien und Fremd-Skills
- persoenliche Runtime-/Persona-Dateien wie `USER.md`, `IDENTITY.md`,
  `SOUL.md`, `TOOLS.md` und `MEMORY.md`

## Verifikation

- 272 automatisierte Unit- und Regressionstests bestanden
- neue Container-Workspace- und Worker-Heartbeat-Tests bestanden
- Shell-Syntax aller Deployment-, Backup- und Rollback-Skripte geprueft
- Compose-Dateien erfolgreich als YAML mit Alias-Unterstuetzung geparst
- Python-Quellen ohne Bytecode-Erzeugung kompiliert
- Backup, SHA-256-Pruefung, Manifest und temporaerer Restore mit einer echten
  SQLite-Testdatenbank erfolgreich durchlaufen
- Entrypoint-Synchronisation gegen einen temporaeren persistenten Workspace
  geprueft; produktive Konfigurationen und Fremd-Skills blieben erhalten
- Repository-Hygiene- und Geheimnisscan ohne produktive Zugangsdaten

Ein Docker- oder Podman-Daemon ist in der Erstellungsumgebung nicht vorhanden.
Das OCI-Image selbst konnte deshalb hier nicht gebaut oder gestartet werden.
Der Dockerfile-, Compose- und Skriptstand wird durch GitHub Actions oder auf
dem Zielserver gebaut und muss dort vor der Migration einmal erfolgreich
gebaut beziehungsweise aus GHCR gezogen werden.
