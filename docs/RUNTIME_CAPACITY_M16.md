# M16.5 – Laufzeitkapazitaet und Latenzevidenz

Stand: 2026-09-20

M16.5 macht Ressourcen- und Latenzzustaende messbar, ohne dem Gateway den
Docker-Socket zu geben und ohne bestehende Limits anzuheben. Die bestehenden
Rollenlimits bleiben in
[`runtime-capacity-budgets.json`](architecture/runtime-capacity-budgets.json)
maschinenlesbar an den Hardeningvertrag gebunden.

## Zwei getrennte Messpfade

Der registrierte, read-only Agentenpfad zeigt ausschliesslich die eigene Cgroup:

```bash
./scripts/assistant.sh performance runtime
```

Er erfasst aktuellen und maximalen Speicher, Swap der Cgroup, CPU-Zeit, Block-I/O,
PIDs, `memory.events`, Host-RAM und Host-Swap. Hostwerte und Agentenverbrauch
bleiben getrennte Felder. Fremdlast wird nie dem Agenten zugerechnet. Restarts,
Startup-, Health- und Shutdownzeiten werden in dieser Sicht ausdrücklich als
`not_measured` markiert.

Der Operator-Collector korreliert dagegen Docker-Inspect, Docker-Stats und die
rollenlokale Cgroup-Sicht:

```bash
./scripts/runtime-capacity.py --output build/runtime-capacity.json
```

Dieser Befehl ist kein Agententool und wird nicht aus dem Gateway ausgeführt. Er
ist read-only, nutzt eine feste Rollenliste und verändert weder Container noch
Jobs. Eine produktive Messung benötigt weiterhin eine gesonderte ausdrückliche
Freigabe. Hermetische Abnahmen verwenden `--fixture`. Ein nicht laufender
Tool- oder Init-Container wird als `not-measured` ausgewiesen und setzt
`complete=false`; das ist weder ein erfundener Messwert noch ein Ausfall eines
residenten Dienstes. Nicht erreichbare residente Rollen bleiben dagegen
Blocker.

## Exit- und OOM-Vertrag

Die Ursachen sind disjunkt:

| Ursache | Notwendige Evidenz |
| --- | --- |
| `container-oom` | beendeter Container und Docker `OOMKilled=true` |
| `historical-container-oom` | aktuell laufender Container, aber historisches Docker-OOM-Bit |
| `child-oom` | Child-Exit 137/SIGKILL und positiver Cgroup-`oom_kill`-Delta |
| `health-failure` | `unhealthy`, aber keine OOM-Evidenz |
| `manual-stop` | explizites Stopereignis |
| `normal-exit` | beendeter Container mit Exit 0 |
| `process-failure` | anderer beendeter Prozess ohne OOM-Beleg |

Exit 137 allein beweist keinen OOM. Ein historisches OOM-Bit macht einen aktuell
gesunden Container nicht automatisch krank. Der bisher berichtete
Supervisor-Zustand ist damit technisch als historischer Container-OOM unter dem
früheren 512-MiB-Limit eingeordnet; die heutige 1-GiB-Grenze bleibt unverändert.
Ohne zeitgebundene Kernel-/Cgroup-Differenzevidenz wird kein betroffener
Childprozess erfunden.

## Rollenbudgets und ClamAV

Das Budgetdokument spiegelt ausschließlich `compose.yaml` und
`runtime-hardening.json`. Ein Peak über dem Budget oder eine Limitdrift ist ein
Blocker, kein automatischer Grund für mehr Speicher. Besonders bleiben getrennt:

- `clamd`: resident, netzlos, 2 GiB, frische Signaturen und fail-closed Scans;
- `clamav-update`: Maintenance-/Signaturschreiber, 2 GiB;
- Gateway und `agent-cli`: je 2 GiB;
- Mailworker: 2 GiB;
- übrige Worker und Ollama-Proxy: ihre bisherigen 1-GiB-Grenzen;
- kurzlebige Initialisierer: eigene enge Limits.

M16.5 ändert weder Scannertransport noch Signaturalter, Cacheidentität oder
Fail-closed-Verhalten.

## Latenzvertrag

Mail-Modellläufe weisen folgende nicht überlappende Komponenten aus:

1. Promptaufbereitung;
2. Queue-Wartezeit;
3. Ollama-Upstream-Inferenz;
4. Clienttransport;
5. externe Tool-/Command-Schleifen;
6. Antwortdekodierung und -finalisierung;
7. ausdrücklich unattribuierte Restzeit.

`accounted_ms + unattributed_ms - overlap_ms` muss exakt der gemessenen
Turn-Latenz entsprechen. Der Ollama-Proxy trennt zusätzlich aggregierte Queue-
und Upstreamzeiten sowie Request-/Responsebytes. Die native Toolbridge misst
Prompt-Routing, Toolschleifen, Finalisierung und Gesamtturn datenschutzarm.

## Große Ergebnisse und Context-Budget

Die Toolbridge erfasst vollständiges JSON bis zur festen Capture-Grenze, prüft es
und projiziert erst danach große Ergebnisarrays auf höchstens 100 Zeilen und
200.000 Byte. Die Projektion enthält Originalgröße, SHA-256, ursprüngliche und
sichtbare Zeilenzahl. Sie setzt immer `complete=false` und
`results_may_be_truncated=true`; dadurch sind negative Vollständigkeitsclaims
verboten. Nicht strukturierbare Ausgaben oberhalb der Capture-Grenze ergeben den
typisierten Fehler `output-limit` statt beschädigtem JSON.

## Reproduzierbare Abnahme

```bash
.venv/bin/python -m pytest -q \
  tests/test_runtime_capacity_m165.py \
  tests/test_performance_telemetry.py \
  tests/test_ollama_priority_proxy.py \
  tests/test_agent_tool_orchestration_m13.py
./scripts/check-repo.sh
```

Die Tests decken Container-/Child-OOM, normalen Exit, Healthfehler, manuellen
Stop, Budgetdrift, Cgroup-/Hosttrennung, Cold-/Warm-/Health-/Shutdown-Fixtures,
Latenzreconciliation, Timeout-/Concurrencypfade und große Toolergebnisse ab.
