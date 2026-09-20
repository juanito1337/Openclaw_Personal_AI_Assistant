# ADR-0044: Getrennte Runtimekapazitaet und belegte Kontextprojektion

Status: Accepted

Datum: 2026-09-20

## Kontext

Ein historisches Docker-`OOMKilled`-Bit wurde zusammen mit aktueller Health als
scheinbarer Widerspruch dargestellt. Rollenlimits waren vorhanden, aber Peaks,
Cgroup-Ereignisse, Host-Swap und Fremdlast besaßen keinen gemeinsamen Vertrag.
Zugleich konnte die native Toolbridge eine große JSON-Ausgabe roh abschneiden
und damit sowohl Parsebarkeit als auch Vollständigkeitsevidenz verlieren.

## Entscheidung

Die containerinterne Diagnostik liest nur ihre eigene Cgroup und `/proc`. Ein
separater, fester read-only Operator-Collector korreliert Dockerzustände für alle
Rollen; der Gateway erhält keinen Docker-Socket. Container-OOM, historischer OOM,
Child-OOM, Healthfehler, manueller Stop und normaler Exit sind disjunkte
Klassifikationen. Die bestehenden Limits werden als Budgets gespiegelt und in
M16.5 nicht erhöht.

Große Toolantworten werden vollständig bis zu einer festen Capture-Grenze
geparst. Erst danach wird eine digestgebundene, sichtbar unvollständige Projektion
erzeugt. Eine Projektion kann positive Einzelevidenz liefern, aber niemals einen
negativen Vollständigkeitsclaim autorisieren.

Modell- und Turntelemetrie trennt Promptaufbereitung, Queue, Upstream,
Clienttransport, Toolschleife, Finalisierung und unattribuierte Restzeit. Die
Komponenten werden gegen dieselbe Walltime reconciliert.

## Konsequenzen

- Der Supervisor kann trotz historischem OOM aktuell gesund sein; der historische
  Befund bleibt sichtbar und wird nicht in einen Child-OOM umgedeutet.
- Host-Swap und Fremdlast sind keine implizite Agentenlast.
- ClamAV bleibt resident, netzgetrennt und fail-closed; seine zwei Rollen werden
  unabhängig budgetiert.
- Große Ergebnisse überlasten nicht still den Modellkontext und verlieren ihren
  Evidenzstatus nicht.
- Vollständige produktive Peak-, Startup- und Shutdownmessungen bleiben ein
  gesondert freizugebender Operatorlauf und werden ohne Messung nicht behauptet.

