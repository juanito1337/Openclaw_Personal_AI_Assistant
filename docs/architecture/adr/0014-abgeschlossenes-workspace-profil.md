# ADR-0014: Abgeschlossenes Workspace-Profil bleibt aktive Instanzkonfiguration

- Status: Accepted
- Datum: 2026-08-09
- Entscheider: Architecture Maintainers, Security Maintainers
- Betroffene Milestones: M2, M3, M8

## Kontext

Die erste Layout-3-Aufteilung behandelte alle nicht release-eigenen Dateien an
der alten Workspace-Wurzel gleich und verschob sie nach
`v3/instance/local-workspace/`. Damit blieben die Daten zwar im Backup und State
erhalten, aber die von OpenClaw an der aktiven Workspace-Wurzel erwarteten
`IDENTITY.md`, `SOUL.md`, `USER.md` und `openclaw-workspace-state.json` waren nicht
mehr wirksam.

OpenClaw erzeugte daraufhin neue Profilvorlagen, eine `BOOTSTRAP.md` und einen
Status mit `bootstrapSeededAt`. Der bereits abgeschlossene Legacy-Status mit
`setupCompletedAt` sowie das bisherige Profil lagen ungenutzt eine Ebene tiefer.
Der Agent fragte deshalb erneut nach Name, Wesen, Vibe und Emoji.

## Entscheidung

`IDENTITY.md`, `SOUL.md`, `USER.md` und
`openclaw-workspace-state.json` sind explizite, persistente
Instanzkonfiguration. Eine neue Layout-1-zu-3-Migration publiziert diese Dateien
direkt unter `v3/instance/`; sie werden nicht nach `local-workspace/` verschoben.
`AGENTS.md`, `HEARTBEAT.md` und der Personal-Assistant-Skill bleiben weiterhin
release-eigen.

Bereits publizierte Layout-3-Zustaende werden beim naechsten `layout-init`
idempotent repariert, wenn alle folgenden Bedingungen belegt sind:

1. `local-workspace/openclaw-workspace-state.json` weist mit
   `setupCompletedAt` oder dem kompatiblen `onboardingCompletedAt` einen
   abgeschlossenen alten Setup aus.
2. Der aktive Setup ist noch nicht abgeschlossen und sein Status entspricht
   exakt OpenClaws unveraendertem `version: 1`-/`bootstrapSeededAt`-Vertrag.
3. Jede abweichende aktive Profildatei stimmt bytegenau mit dem von OpenClaw
   fuer `/home/node/.openclaw/workspace` attestierten generierten SHA-256 ueberein.
4. Ein als gestartet markierter Bootstrap wurde nicht bereits entfernt.

Das alte Profil und sein Setupstatus werden atomar an die aktive Wurzel kopiert;
die Exemplare unter `local-workspace/` bleiben als Recovery-Evidenz erhalten.
Eine vorhandene `BOOTSTRAP.md` wird nicht geloescht. Der uebernommene
`setupCompletedAt`-Status veranlasst OpenClaw, sie nicht mehr in den Agentenkontext
aufzunehmen.

Ein bereits abgeschlossener aktiver Setup gewinnt immer. Eine bearbeitete aktive
Profildatei, eine fehlende beziehungsweise unpassende Attestierung, ein entfernter
laufender Bootstrap, Symlinks, ungueltiges JSON und uebergrosse Dateien stoppen
die Reparatur fail-closed, bevor eine Profildatei ersetzt wird.

Alte `TOOLS.md`, `MEMORY.md`, `AGENTS.md` oder beliebige weitere
Workspace-Inhalte werden nicht durch diesen Vertrag reaktiviert. Insbesondere
historische Tool- und Memory-Anweisungen enthielten bereits als veraltet
klassifizierte Befehle und Integrationen. Sie bleiben unter `local-workspace/`
beziehungsweise im verifizierten Migrationsbackup fuer eine bewusste spaetere
Sichtung erhalten.

## Konsequenzen

Eine bestehende persoenliche Identitaet ueberlebt die Containeraufteilung, ohne
dass der Nutzer einen zweiten Bootstrap durchlaufen muss. Gleichzeitig kann die
Migration keine nach dem fehlerhaften Deployment bewusst neu bearbeitete oder
abgeschlossene Identitaet still ueberschreiben.

Die Reparatur ist eine State-Migration und wird nur durch `layout-init` vor dem
Gatewaystart ausgefuehrt. Der normale Deploymentvertrag erstellt vorher ein
verifiziertes lokales Backup; produktive Dateien werden nicht direkt im laufenden
Container editiert.

## Verifikation

Verhaltensregressionstests pruefen die direkte Erstpublikation des abgeschlossenen
Profils, die Reparatur eines bereits fehlerhaft publizierten Layouts anhand einer
echten OpenClaw-Attestierung, den Erhalt der Legacy-Evidenz, Idempotenz und den
fail-closed Konflikt bei einer nachtraeglich bearbeiteten aktiven Identitaet.
