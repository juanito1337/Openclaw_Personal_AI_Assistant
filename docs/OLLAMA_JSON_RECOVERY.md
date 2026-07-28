# Ollama-JSON- und Timeout-Wiederherstellung (R26.1)

## Beobachteter Fehler

Die produktive Telemetrie zeigte zwei zusammenhaengende Fehlerbilder:

- Einzelantworten endeten bei exakt 512 Ausgabetokens mit `done_reason = "length"`.
  Das JSON war dadurch syntaktisch unvollstaendig.
- Der bisherige Fallback wiederholte dieselbe Anfrage mit `format = "json"`, aber
  erneut mit 512 Tokens. Auch diese Antwort wurde abgeschnitten.
- Grosse oder parallel gestartete Gemma-31B-Batches liefen teilweise in das
  180-Sekunden-Limit der Split-Wiederholung.

## Verhalten ab R26.1

1. `done_reason = "length"` wird vor dem JSON-Parser erkannt.
2. Eine Einzelmail erhaelt genau einen erneuten Schema-Aufruf mit 1024 Tokens.
3. Ein Batch erhaelt keinen gleich grossen zweiten Generierungslauf, sondern wird
   in kleinere Gruppen zerlegt.
4. Automatische Mailgruppen laufen sequenziell und enthalten standardmaessig
   hoechstens drei Mails.
5. Split-Gruppen erhalten bis zu 300 Sekunden und koennen bis zu Einzelmails
   heruntergeteilt werden.
6. Normale, nicht abgeschnittene Schemafehler duerfen weiterhin genau einmal auf
   `format = "json"` ausweichen.

## Relevante Konfiguration

```toml
[ollama]
batch_size = 3
batch_prefetch = 9
batch_retry_timeout_seconds = 300
batch_max_split_depth = 2
parallel_requests = 1
background_burst = false
num_predict = 512
single_retry_num_predict = 1024
batch_num_predict = 2048
```

`single_retry_num_predict` wird nur verwendet, wenn Ollama selbst den Abbruchgrund
`length` meldet. Es verdoppelt nicht pauschal die Ausgabelaenge jeder Mail.

## Kontrolle

```bash
./scripts/mail-agent.sh test-config
./scripts/mail-agent.sh doctor
./scripts/mail-agent.sh performance --limit 20
journalctl --user -u mail-agent.service -n 200 --no-pager
```

Neue Telemetriezaehler:

- `truncated_outputs`: Modellantworten mit `done_reason = length`
- `truncation_retries`: ausgefuehrte groessere Einzel-Schema-Retries
- `batch_splits`: in kleinere Gruppen zerlegte Batches
