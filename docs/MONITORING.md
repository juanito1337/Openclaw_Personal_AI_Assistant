# Personal Assistant monitoring

## Purpose

Monitoring answers "how well is the assistant operating?" from measurable local
evidence. It never asks the language model to grade itself.

## Commands

```bash
./scripts/assistant.sh monitor status --days 7
./scripts/assistant.sh monitor status --days 7 --live
./scripts/assistant.sh monitor record --days 7 --live
./scripts/assistant.sh monitor history --days 30
```

## Score components

The total maximum is 100 points:

- Core and databases: 15
- Mail reliability: 20
- Classification quality, indirect: 15
- Nextcloud and freshness: 15
- ActionPlan execution: 15
- Knowledge index and search: 10
- Services, security and host resources: 5
- Depot and market-data pipeline: 5

The classification component uses review/uncertain rate, stored confidence and
feedback activity. It is explicitly indirect. Precision and recall require
confirmed labels from the user's correction workflow.

The market-data component is neutral when the optional portfolio tool is disabled.
When enabled, held-position quote coverage and freshness directly affect the
technical score. A missing required quote also blocks fresh portfolio analysis.
Trading-signal performance is not part of this score and is reported separately
by `portfolio performance`.

## Ratings

- 90-100: very good
- 75-89: good
- 60-74: degraded
- 40-59: poor
- below 40: critical

The report also states confidence based on available sample volume and live
checks. Low confidence must be communicated together with the score.

## Storage and privacy

Snapshots are local-only in:

```text
personal_assistant/data/monitoring.sqlite3
```

Reports contain aggregated counters, statuses and paths to local databases. They
do not store mail bodies, document text, passwords or Nextcloud tokens.
Snapshots older than 180 days are pruned when a new snapshot is recorded.

## Timer

The optional user timer records a live snapshot every hour with a randomized
five-minute delay. It does not modify mail or Nextcloud data.

## Immediate job-state monitoring

The performance score is historical and aggregate. Immediate ON/OFF/failure state
is handled by the separate job controller:

```bash
./scripts/assistant.sh jobs check --target all --deep
./scripts/assistant.sh jobs alerts
```

The five-minute supervisor records transitions and queues an immediate OpenClaw
system event for a new or resolved alert; the OpenClaw heartbeat reports
actionable active alerts without starting productive work.
