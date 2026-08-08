# ADR-0013: Native Nextcloud-Bruecke und aktive Runtime-Normalisierung

- Status: Accepted
- Datum: 2026-08-08
- Entscheider: Architecture Maintainers, Operations Maintainers
- Betroffene Milestones: M2, M4, M6, M8

## Kontext

Der erste signierte M8-Live-Test erreichte Gateway und Ollama-Proxy, scheiterte
aber im Mail-Doctor. Die Layout-1-Migration normalisierte zwar ihre eigene
Workspace-Kopie auf `http://ollama-proxy:11435`; Layout 3 publizierte danach eine
separate Instanzkopie, deren alte Loopback-Adresse ungeprueft aktiv blieb.

Die Mail-Kontakt- und Kalenderintegration erwartete ausserdem weiterhin den
breiten Community-Skill `openclaw-nextcloud` im beschreibbaren Workspace. Das
widersprach dem seit M2 verbindlichen Vertrag, nach dem produktiv nur
release-eigener, unveraenderlicher Code ausgefuehrt wird. Der Skill bot zudem
Delete-, Share- und freie WebDAV-Oberflaechen, die der Mail-Agent nicht benoetigt.

## Entscheidung

Layout 3 normalisiert die tatsaechlich publizierte Instanzkonfiguration bei der
Erstmigration und bei jedem spaeteren Layout-Start idempotent auf den internen
Ollama-Proxy. Die Legacy-Kopie bleibt fuer Rollbacknachweise unangetastet.

Mail-Kalender und -Kontakte verwenden die bereits release-eigenen
`personal_assistant.connectors.nextcloud`-Bausteine. Die Kompatibilitaetsklasse
des Mail-Agenten stellt nur folgende Operationen bereit:

- CalDAV-/CardDAV-Collections read-only entdecken,
- Kontakte read-only auflisten und lokal auf E-Mail-Adressen begrenzen,
- genau einen Kalender anhand stabiler Ressourcen-ID, exaktem Namen oder href
  aufloesen,
- einen neuen VEVENT mit `If-None-Match: *` anlegen.

Mehrdeutige Kalenderauswahl bricht fail-closed ab. Delete, Edit, Share, beliebiger
Dateiupload und freie WebDAV-Aufrufe sind keine Mail-Brueckenoberflaeche. Die alten
Konfigurationsfelder `skill_package` und `skill_dir` werden beim Laden nur noch
ignoriert, damit vorhandene Konfigurationen kompatibel bleiben; sie waehlen keinen
ausfuehrbaren Code mehr aus. Historische CLI-Namen zur Skillpruefung sind harmlose
Kompatibilitaetsaliases und installieren nichts.

## Konsequenzen

Der Container benoetigt keinen ausfuehrbaren Nextcloud-Skill im persistenten
Workspace und keine neue Drittanbieter-Supply-Chain. Dry-Run-Fingerprints binden
stattdessen den nativen Connectorcode. Die zentrale Kalender-Ressourcen-ID aus
`tools.toml` wird auch fuer den Mail-Kalenderpfad verwendet, sofern die alte
Mail-Konfiguration keinen exakten Kalendernamen enthaelt.

Die Bezeichnung `calendar.backend = "nextcloud_skill"` bleibt vorlaeufig als
Konfigurationskompatibilitaet erhalten; Status und Doctor melden den effektiven
Backendtyp `native-caldav-carddav`.

## Verifikation

Regressionstests pruefen die Normalisierung der aktiven v3-Konfiguration bei
Migration und Neustart, die Unabhaengigkeit von Workspace-Skillcode, exakte
Ressourcenaufloesung, fail-closed Mehrdeutigkeit und den create-only nativen
Kalenderaufruf. Der Rollenimage-Smoke prueft zusaetzlich, dass der native Connector
im read-only Image vorhanden ist und kein Nextcloud-Community-Skill im Workspace
benoetigt wird.
