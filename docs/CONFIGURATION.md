# Configuration

## Tracked examples

```text
mail_agent/config.example.toml
mail_agent/rules.example.toml
personal_assistant/config.example.toml
personal_assistant/resources.example.toml
personal_assistant/policies.example.toml
```

## Local files

```text
mail_agent/config.toml
mail_agent/rules.toml
mail_agent/data/
personal_assistant/config.toml
personal_assistant/resources.toml
personal_assistant/policies.toml
personal_assistant/data/
~/.config/personal-assistant/secrets.env
~/.config/mail-agent.env
```

The Resource Registry is the supported extension point for additional accounts,
calendars, address books, task lists, file roots, and future connectors. Safe settings
are changed through `assistant.sh settings`; permissions and credentials require
explicit setup or approval flows.

## Validation

```bash
./scripts/check-repo.sh
./scripts/mail-agent.sh doctor
./scripts/mail-agent.sh run --dry-run --no-digest --limit 20
./scripts/assistant.sh doctor
./scripts/assistant.sh capabilities
./scripts/assistant.sh index mail
```
