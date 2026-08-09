# Ollama-Prioritaetskoordination (r25)

## Zweck

OpenClaw ist der einzige Agent. Der technische `mail-worker` ist sein
automatisches Mail-Interface. R20 ordnet neue Ollama-Modellaufrufe, damit direkte
Benutzeranfragen nicht hinter neu beginnender Hintergrundklassifikation warten.

## Prioritaeten

1. `interactive`: OpenClaw und direkt angeforderte Mail-Tools
2. `normal`: sonstige explizite Agentenfunktionen
3. `maintenance`: Supervisor-Dry-Runs und sichere Wiederherstellung
4. `background`: automatische Mailklassifikation

Unmarkierte Anfragen gelten absichtlich als `interactive`, damit der bestehende
OpenClaw-Client ohne Anpassung Vorrang erhaelt.

## Sicherheits- und Leistungsmodell

- Der Proxy bindet ausschliesslich an `127.0.0.1` oder `::1`.
- Modellgenerierung besitzt zwei priorisierte Slots. Nicht-generierende Endpunkte wie
  `/api/tags`, `/api/version` und `/api/show` umgehen die Warteschlange.
- Ein laufender Aufruf wird nicht abgebrochen. Priorisierung gilt fuer den
  naechsten freien Slot. Automatische Mailarbeit belegt normalerweise nur einen
  Hintergrundslot; ein zweiter Hintergrundslot ist nur als expliziter Aufhol-Burst
  ohne aktive oder wartende Vordergrundanfrage erlaubt.
- Nach standardmaessig 600 Sekunden verhindert Starvation-Schutz, dass ein alter
  Hintergrundauftrag dauerhaft verdraengt wird.
- Maximal 128 wartende Aufrufe und ein Queue-Zeitlimit verhindern unbeschraenktes
  Wachstum.
- Native Ollama-NDJSON-Streams werden unveraendert durchgereicht.
- Bei einem internen Fehler der Schedulerlogik wird die einzelne Anfrage direkt
  an Ollama weitergeleitet. Faellt der gesamte Proxyprozess aus, greift die
  Compose-Restartpolicy; Healthcheck und Supervisor melden den Fehler.

## Betrieb

```bash
./scripts/assistant.sh ollama status
./scripts/assistant.sh ollama check
./scripts/assistant.sh ollama queue
```

Die nicht-geheime Konfiguration liegt in:

```text
/srv/openclaw/config/ollama-priority.env
```

Im Containerstack liest ausschliesslich die Rolle `ollama-proxy` diese
Serverkonfiguration. `gateway`, `agent-cli` und der egress-lose Supervisor fragen
den Status read-only ueber den festen privaten Compose-Endpunkt
`ollama-proxy:11435/healthz` ab. Sie erhalten weder den direkten Ollama-Upstream
noch dessen Serverkonfiguration. Ein nicht erreichbarer oder nicht gesunder Proxy
bleibt dabei ein sichtbarer Fehler.

## Telemetrie

Das Mail-Interface uebernimmt den Response-Header `X-Ollama-Queue-Wait-Ms` in die
bestehende r18-Telemetrie. Mailinhalte, Betreffzeilen und Modellantworten werden
nicht in der Performance-Datei gespeichert.


## Schreibende Betriebsbefehle

The supported interface is:

```bash
./scripts/assistant.sh ollama start
./scripts/assistant.sh ollama restart
```

`start` and `restart` verify the proxy and upstream before returning success. The
agent must not call `systemctl` or the helper script directly when a registered
command exists.

## R24: getrennte Zeitlimits

Jede geplante Modellanfrage uebergibt zwei getrennte Grenzen an den lokalen Proxy:

- `X-OpenClaw-Queue-Timeout-Seconds`: maximale Wartezeit auf einen freigegebenen Slot.
- `X-OpenClaw-Upstream-Timeout-Seconds`: maximale Laufzeit der eigentlichen Ollama-Anfrage nach Freigabe.

Der HTTP-Client erhaelt nur die Summe aus Queue-, Upstream- und kleiner Transportreserve.
Der Proxy antwortet bei Queue-Ablauf mit HTTP 503 und `error_type=queue_timeout`, bei
Ollama-Laufzeitueberschreitung mit HTTP 504 und `error_type=upstream_timeout`.

Fuer automatische Mail-Batches gilt: Queue-Timeouts werden nicht durch sofortige lange
Wiederholungen vervielfacht. Ein Upstream-Timeout darf hoechstens einen begrenzten
Split-Versuch ausloesen. Danach wird sicher auf Pruefung/Fallback gewechselt.


## R25: zwei priorisierte Slots

Nicht-geheime Proxy-Konfiguration:

```ini
OLLAMA_PRIORITY_MAX_CONCURRENCY=2
OLLAMA_PRIORITY_BACKGROUND_CONCURRENCY=1
OLLAMA_PRIORITY_BACKGROUND_BURST_CONCURRENCY=2
OLLAMA_PRIORITY_BACKGROUND_BURST_IDLE_SECONDS=5
```

Der Mail-Klassifizierer markiert den zweiten Hintergrundauftrag nur dann als Burst,
wenn `parallel_requests = 2` und `background_burst = true` gesetzt sind. Der Proxy
verweigert den zweiten Hintergrundslot, sobald eine interaktive oder normale Anfrage
aktiv oder wartend ist. Bereits laufende Hintergrundantworten werden nicht preemptiv
abgebrochen.

Der entfernte Ollama-Server muss mindestens zwei parallele Anfragen erlauben, zum
Beispiel mit `OLLAMA_NUM_PARALLEL=2`. Diese Servereinstellung wird nicht vom
OpenClaw-Installer veraendert.

## R26.1: konservativer Gemma-Betrieb fuer automatische Mailarbeit

Die zwei Proxy-Slots bleiben erhalten, damit eine direkte OpenClaw-Anfrage nicht auf
neu beginnende Hintergrundarbeit warten muss. Der automatische Mail-Agent selbst
startet jedoch nur noch eine Modellgruppe gleichzeitig:

```toml
parallel_requests = 1
background_burst = false
batch_size = 3
batch_retry_timeout_seconds = 300
batch_max_split_depth = 2
```

Damit laufen nicht mehr zwei grosse Gemma-31B-Mailprompts parallel. Ein freier zweiter
Proxy-Slot kann weiterhin von einer interaktiven Anfrage genutzt werden.

`done_reason = "length"` ist kein allgemeiner JSON-Fehler, sondern belegt, dass
Ollama die Ausgabe am `num_predict`-Limit beendet hat. Einzelanfragen erhalten in
diesem Fall genau einen erneuten Schema-Aufruf mit `single_retry_num_predict = 1024`.
Ein abgeschnittener Batch wird stattdessen in kleinere Gruppen geteilt. Derselbe grosse
Batch wird nicht nochmals mit `format = "json"` erzeugt.
