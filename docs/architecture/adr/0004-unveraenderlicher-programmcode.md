# ADR-0004: Programmcode wird nur aus dem Image ausgefuehrt

- Status: Accepted
- Datum: 2026-08-05
- Entscheider: Architecture Maintainers
- Betroffene Milestones: M2

## Kontext

Der Releasevertrag verlangt ein unveraenderliches Image. Vor M2 kopierte der
Entrypoint Skripte, Pakete und Skilldateien in den gemeinsam beschreibbaren
Workspace; einzelne Compose-Kommandos starteten Skripte von dort.

## Entscheidung

Ausfuehrbarer Produktcode, Defaults und Runtime-Skills stammen aus dem read-only
Image unter `/opt/openclaw-agent`. Container-Python verwendet `-P` und einen vom
Entrypoint nach dem Laden der Instanzumgebung erneut gesetzten Image-Suchpfad.
Persistenter State enthaelt nur Instanzzustand, Konfiguration, Benutzerdaten und
kontrollierte nicht ausfuehrbare Metadaten. Agentenanweisungen und der
Personal-Assistant-Skill werden als bei jedem Start erneuerte Links auf das
read-only Image exponiert.

Offizielle externe OpenClaw-Plugins sind ebenfalls Programmcode. Benoetigte
Plugins werden mit exakter npm-Lockdatei unter `/opt/openclaw-plugins` in das
Runtime-Image gebaut und ueber feste Imagepfade geladen. Verwaltete npm-Payloads
im beschreibbaren Gateway-State sind unzulaessig; unbekannte Legacy-Plugins
blockieren die Migration, und der Container deaktiviert Pluginmutation mit
`OPENCLAW_NIX_MODE=1`.

## Konsequenzen

Layout 2 entfernt zuvor synchronisierten Releasecode nach einer verifizierten lokalen
Sicherung und erhaelt Konfiguration, Datenbanken, Sessions, Korrekturhistorie und
lokale Dokumente. Unbeschriftete Images vor M2 akzeptieren nur Layout 1; ein solcher
Downgrade wird deshalb vor dem Stoppen des laufenden Stacks abgebrochen. M2-Images
akzeptieren Layout 1 und 2.

## Verifikation

Gerenderte Commands, read-only RootFS, sichere Python-Pfade, manipulierte
Workspace-Skripte, Upgrade-/Downgrade-Fixtures, parallele Starts, Revision-Mismatch,
Plugin-Lockintegritaet, State-Plugin-Negativmigration und echte Runtime-Imports der
Imageplugins werden lokal und in CI geprueft.

## Offene Fragen

Keine offene M2-Frage. Fuer Agentensitzungen sind `AGENTS.md`, `HEARTBEAT.md` und der
Personal-Assistant-Skill erforderlich; weitere Release-Metadaten liest die CLI direkt
aus dem Image.
