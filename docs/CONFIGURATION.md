# Configuration

## Tracked examples

```text
mail_agent/config.example.toml
mail_agent/rules.example.toml
personal_assistant/config.example.toml
personal_assistant/resources.example.toml
personal_assistant/policies.example.toml
personal_assistant/tool_defaults.toml
personal_assistant/policy_defaults.toml
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

`tool_defaults.toml` and `policy_defaults.toml` are release-owned and updated
with the immutable image. The local `tools.toml` contains instance overrides;
the local `policies.toml` may add restrictions and approval requirements.
Release-owned deny and approval rules are additive and cannot be removed by an
older local file. Account/resource selections, folder names and explicitly
granted permissions remain persistent outside the image.

## Validation

```bash
./scripts/check-repo.sh
./scripts/mail-agent.sh doctor
./scripts/mail-agent.sh run --dry-run --no-digest --limit 20
./scripts/assistant.sh doctor
./scripts/assistant.sh capabilities
./scripts/assistant.sh index mail
```
