# ADR-0015: Geschuetzte Gateway-Konfiguration

- Status: Accepted
- Datum: 2026-08-11
- Entscheider: Architecture Maintainers, Operations Maintainers
- Betroffene Milestones: M2, M4, M5, M8

## Kontext

Das interaktive Gateway benoetigt Schreibzugriff auf Profil, Memory und mehrere
fachliche Datenbereiche. Der bisherige breite Instanzmount machte dadurch auch
`mail_agent/` und `personal_assistant/` beschreibbar. Nach einem gescheiterten
Portfolio-Aufruf wich das Modell auf generische Dateiwerkzeuge aus und aenderte
fuenf Pfade in `personal_assistant/tools.toml`. Die fachlichen Datenbanken blieben
dank spaeter Runtime-Ueberschreibung intakt, die Konfigurationsintegritaet war
aber verletzt. Eine Skillanweisung allein bildet dafuer keine ausreichende
technische Grenze.

## Entscheidung

Der Gateway-Workspace bleibt fuer Profil und Memory beschreibbar. Zwei
verschachtelte Bind-Mounts ueberlagern die Instanzordner `mail_agent/` und
`personal_assistant/` im Gateway read-only. Das gilt auch fuer Shellzugriffe im
Container. Fachliche Laufzeitdaten bleiben in ihren bestehenden Domaenenmounts
beschreibbar; Holdings, Watchlist, Kurscache, Jobs und kontrollierte externe
Aktionen aendern ihre Rechte nicht.

Administrative Setup-Kommandos, die Instanzkonfiguration veraendern, laufen nur
in der explizit gewaehlten, kurzlebigen Compose-Rolle `agent-cli`. Sie behaelt den
beschreibbaren Instanzmount und unterliegt weiterhin dem typisierten Approval-
Vertrag. Der Gateway-Agent darf nach einem Fehler weder Konfigurationsdateien
lesen/patchen noch `--help` oder Workspace-Discovery als Befehlsersatz verwenden.

Layout-Init normalisiert bei jedem Start genau fuenf durch Compose festgelegte
Pfade in der aktiven `tools.toml`: Workspace-Outbox, Orders-Datenbank,
Antivirus-Tempverzeichnis sowie Portfolio-Datenbank und -Inbox. Andere Werte,
Kommentare, Ressourcen-IDs und Berechtigungen bleiben byteweise erhalten. Nicht
sicher als einfache Stringzuweisung erkennbare Werte brechen fail-closed ab.

## Konsequenzen

Ein Modell kann die betroffenen Konfigurationen weder mit OpenClaw-Dateitools noch
ueber `exec` veraendern. Explizites Setup ist nicht mehr direkt in einer laufenden
Gateway-Sitzung moeglich und muss ueber den dokumentierten Operatorpfad ausgefuehrt
werden. Das ist eine gewollte Trennung zwischen fachlicher Assistenz und lokaler
Berechtigungs-/Konfigurationsverwaltung.

Die Pfadnormalisierung repariert auch bereits entstandene Abweichungen beim
naechsten Kandidatenstart, ohne eine alte Gesamtsicherung zurueckzuspielen. Eine
vorher erzeugte leere Datei an einem falschen Workspace-Pfad wird nicht
automatisch geloescht; sie bleibt bis zu einer separat verifizierten,
backup-geschuetzten Bereinigung als Diagnosebeleg erhalten.

## Verifikation

Compose-Vertragstests pruefen die beiden read-only Gateway-Overlays und den
weiterhin beschreibbaren `agent-cli`-Workspace. Layout-Verhaltenstests beginnen
mit den beobachteten fuenf falschen Pfaden, pruefen deren exakte Reparatur,
erhaltene Grants und einen idempotenten zweiten Start. Skilltests sichern die
Fehlerroute ohne `--help`, Datei-Discovery oder Konfigurationspatch ab.
