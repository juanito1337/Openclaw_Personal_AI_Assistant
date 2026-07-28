# Command reference

Run from `${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}`.

## Diagnose

```bash
./scripts/mail-agent.sh guide
./scripts/mail-agent.sh doctor
./scripts/mail-agent.sh status
./scripts/mail-agent.sh test-config
./scripts/mail-agent.sh help <topic>
```

Topics: `files`, `config`, `performance`, `training`, `nextcloud`, `invoices`, `calendar`, `automation`, `security`.

## Configure and train

```bash
./scripts/mail-agent.sh configure
./scripts/mail-agent.sh nextcloud setup
./scripts/mail-agent.sh training status
./scripts/mail-agent.sh training rules
./scripts/mail-agent.sh training feedback --limit 30
./scripts/mail-agent.sh training rule-add spam domain newsletter.example
./scripts/mail-agent.sh training rule-add important address person@example.org
./scripts/mail-agent.sh training rule-remove spam domain newsletter.example
```

## Safe run sequence

```bash
./scripts/mail-agent.sh run --dry-run --no-digest --limit 20
./scripts/mail-agent.sh guide
./scripts/mail-agent.sh run --no-digest --limit 10
./scripts/mail-agent.sh run --drain --batch-size 20 --max-messages 500 --max-runtime 2400 --shutdown-reserve 180 --max-batches 100 --no-digest
```

Drain dry-runs execute one batch only because no mail is moved.

## Scheduling

```bash
./scripts/set-mail-agent-interval.sh status
./scripts/set-mail-agent-interval.sh 20m
./scripts/set-mail-agent-interval.sh 1h
```

Allowed idle intervals: `15m`, `20m`, `30m`, `1h`, `2h`.

## Nextcloud verification

```bash
./scripts/mail-agent.sh nextcloud verify-skill
./scripts/mail-agent.sh nextcloud skill-card
./scripts/mail-agent.sh nextcloud install-skill --yes
./scripts/mail-agent.sh nextcloud doctor
```

Use `--allow-review` only after the user explicitly reviews and accepts a registry `review` decision. Never override a blocked decision.
