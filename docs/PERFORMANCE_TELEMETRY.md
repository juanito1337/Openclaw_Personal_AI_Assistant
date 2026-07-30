# Mail-Agent Performance-Telemetrie (r24)

## Ziel

r18 misst, wo ein Mail-Agent-Lauf Zeit verbraucht, ohne Klassifikation, Regeln,
Timeouts, Modellparameter, Sicherheitsfreigaben oder Mailaktionen zu veraendern.
Die Messung ist fail-open: Ein Schreib- oder Auswertungsfehler der Telemetrie darf
einen produktiven Lauf niemals blockieren.

This detailed phase telemetry remains mail-specific. Common whole-job queue wait,
duration, outcome, lease and deadline telemetry for mail, portfolio, sync and
monitoring is stored by the adaptive scheduler. See
`docs/ADAPTIVE_SCHEDULER.md`.

## Nutzung

```bash
./scripts/mail-agent.sh performance --limit 20
./scripts/mail-agent.sh performance --limit 5 --raw
```

Die verdichtete Ansicht zeigt unter anderem:

- Laufzeit pro Lauf und pro verarbeiteter Mail
- langsamste Phasen (Preflight, Export, Virenscan, Parsing, Klassifikation, Routing)
- externe Kommandokategorien und deren Laufzeiten
- Ollama Client-, Server-, Lade-, Prompt- und Ausgabedauer
- `prompt_eval_count` und `eval_count`
- Fehler- und Timeoutzaehler

## Speicherung und Rotation

Die Messwerte liegen lokal neben der Mail-Agent-Datenbank:

```text
mail_agent/data/performance.jsonl
```

Bei 20 MB wird die vorherige Datei einmalig nach `performance.jsonl.1` rotiert.
Die aktive Datei wird mit Dateirechten `0600` angelegt.

## Datenschutz

Die Performance-Datei speichert nicht:

- Absender oder Empfaenger
- Betreffzeilen
- Mailtexte oder Modellantworten
- Anhangsnamen oder Anhangsinhalte
- Mailbox-IDs
- Kommandoargumente
- Zugangsdaten oder Tokens

Externe Prozesse werden nur als feste Kategorien wie
`himalaya.message.export`, `himalaya.message.move` oder `antivirus.command`
erfasst.

## Deaktivierung

Nur fuer Diagnosezwecke kann die Messung pro Prozess deaktiviert werden:

```bash
MAIL_AGENT_TELEMETRY=0 ./scripts/mail-agent.sh run --dry-run --no-digest --limit 5
```

Die Standardkonfiguration bleibt aktiviert, weil die Messung nur geringe lokale
CPU- und I/O-Kosten verursacht.

## Auswertung fuer r19

Vor einer Aenderung von Batchgroesse, Kontextfenster, `num_predict` oder
Parallelisierung sollten mindestens 20 bis 50 reale Laeufe vorliegen. Relevante
Kennzahlen sind insbesondere:

- Anteil `ollama.client` an der Gesamtlaufzeit
- `load_duration_ms` gegenueber `total_duration_ms`
- Prompt-Tokens je Modellversuch
- Client-Overhead gegenueber Ollama-Serverzeit
- Maximaldauer einzelner Batches
- Zeit fuer Export, Virenscan und Parsing

## Abgebrochene Laeufe

Waehrend eines Laufs wird zusaetzlich ein kleiner, datensparsamer Checkpoint unter
`mail_agent/data/performance-inflight.json` gefuehrt. Wird der Prozess zum Beispiel
von systemd beendet, erkennt der naechste Start den liegen gebliebenen Checkpoint
und schreibt einen Datensatz mit `outcome: interrupted` und der letzten Phase.
Der Checkpoint enthält keine Mail- oder Kommandoinhalte und wird nach einem normal
beendeten Lauf entfernt.

## R24: belastbare Inflight-Erfassung

R24 schreibt einen Ollama-Versuch bereits vor dem HTTP-Aufruf in den Inflight-Checkpoint.
Dadurch bleibt bei einem Signal oder Clientabbruch sichtbar, ob der Lauf in der Queue oder
im Upstream-Modellaufruf hing. Queue-Wartezeit und Modelllaufzeit werden getrennt erfasst.

Ein Checkpoint wird nur dann als `interrupted` abgeschlossen, wenn sein Besitzerprozess
nach Boot-ID, PID und Prozessstartkennung wirklich nicht mehr lebt. Ein zweiter Prozess
ueberschreibt einen lebenden Checkpoint nicht. Alte oder mehrfach geschriebene Datensaetze
mit derselben `run_id` werden bei `performance` auf den neuesten Stand verdichtet.

Moegliche Abbruchangaben sind unter anderem `queue_timeout`, `upstream_timeout`,
`runtime-reserve` und `owner-process-not-alive`. Der Checkpoint enthaelt weiterhin keine
Mailtexte, Absender, Betreffe oder Modellantworten.


## R25 parallel slots

Proxy status and mail telemetry expose active slot count, configured maximum
concurrency, maximum observed concurrency and parallel mail-group runs. These values
contain no mail content or identifiers.
