#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
DEPLOYED=0

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t FILES < <(git ls-files)
else
  if [[ -f mail_agent/config.toml || -f personal_assistant/config.toml || -d mail_agent/data ]]; then
    DEPLOYED=1
    echo "Hinweis: installierter Workspace; pruefe Quellcode und ignoriere erlaubte lokale Laufzeitdaten." >&2
  else
    echo "Hinweis: kein Git-Repository; pruefe den bereinigten Quellbaum." >&2
  fi
  mapfile -t FILES < <(find . -type f -not -path './.git/*' -printf '%P\n' | sort)
fi

python3 - "$DEPLOYED" "${FILES[@]}" <<'PY'
from pathlib import PurePosixPath
import sys

deployed = sys.argv[1] == "1"
files = [PurePosixPath(item) for item in sys.argv[2:]]
errors: list[str] = []

allowed_deployed_prefixes = (
    "mail_agent/data/",
    "personal_assistant/data/",
    "skills/openclaw-nextcloud/",
    ".clawhub/",
)
allowed_deployed_files = {
    "mail_agent/config.toml",
    "mail_agent/rules.toml",
    "personal_assistant/config.toml",
    "personal_assistant/resources.toml",
    "personal_assistant/policies.toml",
}

for path in files:
    text = str(path)
    if deployed and (text in allowed_deployed_files or text.startswith(allowed_deployed_prefixes)):
        continue

    parts = set(path.parts)
    name = path.name
    if parts & {"legacy", "memory", ".clawhub", "__pycache__"}:
        errors.append(f"verbotener Legacy/Laufzeitpfad: {text}")
    if text in allowed_deployed_files | {"openclaw-workspace-state.json"}:
        errors.append(f"lokale Datei darf nicht verfolgt werden: {text}")
    if text.startswith((
        "skills/openclaw-nextcloud/",
        "skills/himalaya/",
        "skills/signal-cli/",
    )):
        errors.append(f"Fremd-Skill darf nicht vendort werden: {text}")
    if name.endswith((".pyc", ".sqlite", ".sqlite3", ".db", ".eml", ".msg", ".lock")):
        errors.append(f"Laufzeit-/Privatdatei: {text}")
    if ".sqlite3-" in name or ".backup-" in name or name.endswith(".bak"):
        errors.append(f"Backup oder SQLite-Seitendatei: {text}")
    if name.endswith(".log") or ".log." in name:
        errors.append(f"Logdatei: {text}")

if errors:
    print("Repository-Hygiene fehlgeschlagen:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)
PY

bash -n scripts/*.sh
find docker -type f -name "*.sh" -print0 | xargs -0 -r -n1 bash -n
if command -v systemd-analyze >/dev/null 2>&1; then
  UNIT_TMP=$(mktemp -d)
  trap 'rm -rf "$UNIT_TMP"' EXIT
  sed "s|%h/.openclaw/workspace|$ROOT|g" \
    deploy/systemd/mail-agent.service > "$UNIT_TMP/mail-agent.service"
  cp deploy/systemd/mail-agent.timer "$UNIT_TMP/mail-agent.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" deploy/systemd/personal-assistant-sync.service > "$UNIT_TMP/personal-assistant-sync.service"
  cp deploy/systemd/personal-assistant-sync.timer "$UNIT_TMP/personal-assistant-sync.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" deploy/systemd/personal-assistant-supervisor.service > "$UNIT_TMP/personal-assistant-supervisor.service"
  cp deploy/systemd/personal-assistant-supervisor.timer "$UNIT_TMP/personal-assistant-supervisor.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" deploy/systemd/personal-assistant-portfolio.service > "$UNIT_TMP/personal-assistant-portfolio.service"
  cp deploy/systemd/personal-assistant-portfolio.timer "$UNIT_TMP/personal-assistant-portfolio.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" deploy/systemd/personal-assistant-monitor.service > "$UNIT_TMP/personal-assistant-monitor.service"
  cp deploy/systemd/personal-assistant-monitor.timer "$UNIT_TMP/personal-assistant-monitor.timer"
  systemd-analyze verify \
    "$UNIT_TMP/mail-agent.service" "$UNIT_TMP/mail-agent.timer" \
    "$UNIT_TMP/personal-assistant-sync.service" "$UNIT_TMP/personal-assistant-sync.timer" \
    "$UNIT_TMP/personal-assistant-supervisor.service" "$UNIT_TMP/personal-assistant-supervisor.timer" \
    "$UNIT_TMP/personal-assistant-portfolio.service" "$UNIT_TMP/personal-assistant-portfolio.timer" \
    "$UNIT_TMP/personal-assistant-monitor.service" "$UNIT_TMP/personal-assistant-monitor.timer"
fi


if command -v ruby >/dev/null 2>&1; then
  ruby -e 'require "yaml"; YAML.safe_load_file("compose.yaml", aliases: true); YAML.safe_load_file("compose.build.yaml", aliases: true)'
fi

PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from pathlib import Path
for folder in (Path("mail_agent"), Path("personal_assistant"), Path("tests"), Path("docker")):
    for path in folder.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v </dev/null

echo "Repository-Pruefung erfolgreich."
