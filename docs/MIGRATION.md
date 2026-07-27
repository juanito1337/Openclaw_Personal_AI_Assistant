# Migration

The migration script performs an atomic workspace replacement with full backup and
rollback. It preserves private runtime state and removes obsolete source, skills,
caches, and stale instructions from the active workspace.

Preserved when present:

```text
mail_agent/config.toml
mail_agent/rules.toml
mail_agent/data/
personal_assistant/config.toml
personal_assistant/resources.toml
personal_assistant/policies.toml
personal_assistant/data/
skills/openclaw-nextcloud/
IDENTITY.md
SOUL.md
USER.md
.git/
```

Secrets remain outside the workspace. If no central secrets file exists, the migration
copies the legacy `~/.config/mail-agent.env` to
`~/.config/personal-assistant/secrets.env` and leaves the legacy file intact.

Both timers remain disabled after migration until mail and assistant validation have
completed.
