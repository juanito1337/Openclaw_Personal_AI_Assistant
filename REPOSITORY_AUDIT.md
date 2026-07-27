# Repository-Audit

Ausgangsbasis ist der am 27.07.2026 bereitgestellte produktive Workspace
`3.4.0-r26.4`.

Nicht in diesen Git-Quellstand uebernommen wurden:

- OpenClaw-Sitzungen, Trajektorien und Agentendatenbanken
- SQLite-Datenbanken, WAL/SHM-Dateien, Logs und Lockdateien
- Mailinhalte, Anhaenge, Rechnungen, Suchindizes und Lernexporte
- produktive Nextcloud-, CardDAV- und CalDAV-Ressourcenauswahl
- produktive Mail- und Personal-Assistant-Konfigurationen
- Passwoerter, App-Tokens, Geraeteidentitaeten und Freigabedateien
- lokale Backups, Caches, installierte Fremd-Skills und Python-Caches
- persoenliche Runtime-/Persona-Dateien wie `USER.md`, `IDENTITY.md`, `SOUL.md`
  und `TOOLS.md`

Enthalten sind Quellcode, Tests, Dokumentation, Skills des Projekts,
Systemd-Vorlagen, sichere Beispielkonfigurationen und die Releasehistorie.

Die Beispielwerte wurden auf neutrale Domains, Benutzer und Ressourcen-IDs
bereinigt. Vor der Uebergabe wurden Repository-Hygiene, Shell-/Python-Syntax und
270 automatisierte Tests ueber `scripts/check-repo.sh` erfolgreich geprueft.

Fuer einen hermetischen Testlauf respektiert die schmale Mail-zu-Assistant-Bridge
nun auch `PERSONAL_ASSISTANT_CONFIG`, wenn kein expliziter Konfigurationspfad
uebergeben wurde. Im Produktivbetrieb ohne diese Variable bleibt der bisherige
Standardpfad unveraendert.
