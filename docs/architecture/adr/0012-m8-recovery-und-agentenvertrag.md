# ADR-0012: Verifizierte lokale Recovery und generierter Agentenvertrag

- Status: Accepted
- Datum: 2026-08-06
- Entscheider: Architecture Maintainers, Operations Maintainers, Tool Contract Maintainers
- Betroffene Milestones: M8

## Kontext

M7 lieferte reproduzierbare Rollenimages, belegte aber noch keinen vollstaendigen
Container-zu-Container-Restore unter Fehlern. `rollback.sh` stoppte den Stack, bevor
es das Vorhandensein eines externen Restore-Hooks pruefte, und beendete sich bei
einem Hookfehler vor der lokalen Wiederherstellung. Gleichzeitig enthielten
`AGENTS.md` und der Personal-Assistant-Skill viele release- und domaenenspezifische
Wiederholungen neben dem typisierten Toolkatalog.

## Entscheidung

Lokale Wiederherstellung wird in `restore-local-state.sh` zentralisiert und nur mit
explizitem Offline-Vertrag ausgefuehrt. Fehlende externe Restore-Hooks brechen vor
dem Containerstop ab. Scheitert ein vorhandener Hook erst nach dem Stop, wird der
verifizierte alte lokale Stand trotzdem gestartet; der Rollback bleibt ungleich
null, weil Remote-Recovery nicht belegt ist. Ein fehlgeschlagener automatischer
Rollback macht das Deployment mit Exitcode 70 eindeutig unveroeffentlichbar.

Ein hermetischer, portloser Compose-Stack prueft die benoetigten externen Protokolle,
ETag-Konflikt, Antivirus-Fixtures, Netzwerk-/Prozessfehler und den exklusiven
Mailwriter. Der lokale Drill deckt die minimale direkte Upgrade-Version r26.1, den
aktuellen Stand und ein fehlgeschlagenes Upgrade ab. Produktivdaten und
`/srv/openclaw` bleiben ausserhalb dieser Abnahme.

`AGENTS.md` enthaelt nur dauerhafte Autoritaets-, Runtime-, Write-, Failure- und
Recovery-Invarianten. Der kurze Personal-Assistant-Skill routet in
domaenenspezifische Referenzen. Eine deterministische Projektion aller typisierten
Tooldefinitionen erzeugt Tool-ID, Befehl, Modus, externe Wirkung, Approval,
Verfuegbarkeit, Release und Testanker; Drift ist Teil des Repositorychecks.

## Konsequenzen

Ein lokaler alter Stand startet auch dann wieder, wenn Remote-Recovery fehlschlaegt,
ohne diesen Remotezustand faelschlich als erfolgreich zu melden. Vollstaendige
Remote-Ruecknahme bleibt von externen Snapshots abhaengig. Canary ist zeitlich
seriell: alter Writer aus, dann hoechstens ein Kandidatenwriter.

Die M6-Entfernungsbedingung fuer Legacy-systemd ist technisch teilweise erfuellt,
aber M8 fuehrt bewusst kein produktives Deployment durch. Das eingefrorene Paket
bleibt daher erhalten. Seine spaetere Entfernung braucht eine separate
End-of-Support-/Produktionsentscheidung und darf nicht aus dem Fixture-Drill
abgeleitet werden.

## Verifikation

Hermetischer Dockerstack, dynamischer echter `deploy.sh`-Smoke-Fehler,
Rollback-Hook-Positiv-/Negativpfade, bytegenauer Drei-Szenarien-Restore,
Failure-Injections aus M3/M8, generierter 124-Tool-Skillvertrag, Dokumentationscheck,
vollstaendige Suite, Wheel- und Rollenimageabnahme.

## Offene Grenzen

Der gemessene RTO enthaelt weder produktive Datenmenge noch Registry-Pull, externen
Restore oder produktive Health-Konvergenz. Ein produktiver Canary und ein
externer Snapshot-Restore sind separate freigabepflichtige Operationsaufgaben.
