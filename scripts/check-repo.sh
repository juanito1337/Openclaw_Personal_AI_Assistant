#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
DEPLOYED=0
DEV_PYTHON="$ROOT/.venv/bin/python"
export PATH="$ROOT/.venv/bin:$ROOT/.tools/bin:$PATH"

if [[ ! -x "$DEV_PYTHON" ]] || ! command -v shellcheck >/dev/null || ! command -v hadolint >/dev/null; then
  echo "Gepinnte Entwicklungswerkzeuge fehlen. Zuerst ./scripts/bootstrap-dev.sh ausfuehren." >&2
  exit 2
fi

"$DEV_PYTHON" scripts/source-manifest.py verify
"$DEV_PYTHON" scripts/verify-legacy-package.py verify
"$DEV_PYTHON" scripts/generate-component-inventory.py verify
"$DEV_PYTHON" scripts/m7_supply_chain.py verify-lock

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t FILES < <(git ls-files --cached --others --exclude-standard)
else
  if [[ -f mail_agent/config.toml || -f personal_assistant/config.toml || -d mail_agent/data ]]; then
    DEPLOYED=1
    echo "Hinweis: installierter Workspace; pruefe Quellcode und ignoriere erlaubte lokale Laufzeitdaten." >&2
  else
    echo "Hinweis: kein Git-Repository; pruefe den bereinigten Quellbaum." >&2
  fi
  mapfile -t FILES < <(find . -type f -not -path './.git/*' -printf '%P\n' | sort)
fi

"$DEV_PYTHON" - "$DEPLOYED" "${FILES[@]}" <<'PY'
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
    allowed_legacy = text.startswith("legacy/systemd/")
    if ("legacy" in parts and not allowed_legacy) or parts & {"memory", ".clawhub", "__pycache__"}:
        errors.append(f"verbotener Legacy/Laufzeitpfad: {text}")
    if text in allowed_deployed_files | {"openclaw-workspace-state.json"}:
        errors.append(f"lokale Datei darf nicht verfolgt werden: {text}")
    if text.startswith((
        "skills/openclaw-nextcloud/",
        "skills/himalaya/",
        "skills/signal-cli/",
    )):
        errors.append(f"Fremd-Skill darf nicht vendort werden: {text}")
    if text != "requirements-dev.lock" and name.endswith((".pyc", ".sqlite", ".sqlite3", ".db", ".eml", ".msg", ".lock")):
        errors.append(f"Laufzeit-/Privatdatei: {text}")
    if name.casefold().endswith(".pdf"):
        errors.append(f"PDF darf nicht im Quellbestand liegen: {text}")
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
bash -n legacy/systemd/set-mail-agent-interval.sh
find docker -type f -name "*.sh" -print0 | xargs -0 -r -n1 bash -n
mapfile -d '' -t SHELL_FILES < <(
  while IFS= read -r -d '' shell_file; do
    [[ -f "$shell_file" ]] && printf '%s\0' "$shell_file"
  done < <(git ls-files --cached --others --exclude-standard -z '*.sh')
)
shellcheck "${SHELL_FILES[@]}"
hadolint Dockerfile
if command -v systemd-analyze >/dev/null 2>&1; then
  UNIT_TMP=$(mktemp -d)
  trap 'rm -rf "$UNIT_TMP"' EXIT
  sed "s|%h/.openclaw/workspace|$ROOT|g" \
    legacy/systemd/units/mail-agent.service > "$UNIT_TMP/mail-agent.service"
  cp legacy/systemd/units/mail-agent.timer "$UNIT_TMP/mail-agent.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" legacy/systemd/units/personal-assistant-sync.service > "$UNIT_TMP/personal-assistant-sync.service"
  cp legacy/systemd/units/personal-assistant-sync.timer "$UNIT_TMP/personal-assistant-sync.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" legacy/systemd/units/personal-assistant-supervisor.service > "$UNIT_TMP/personal-assistant-supervisor.service"
  cp legacy/systemd/units/personal-assistant-supervisor.timer "$UNIT_TMP/personal-assistant-supervisor.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" legacy/systemd/units/personal-assistant-portfolio.service > "$UNIT_TMP/personal-assistant-portfolio.service"
  cp legacy/systemd/units/personal-assistant-portfolio.timer "$UNIT_TMP/personal-assistant-portfolio.timer"
  sed "s|%h/.openclaw/workspace|$ROOT|g" legacy/systemd/units/personal-assistant-monitor.service > "$UNIT_TMP/personal-assistant-monitor.service"
  cp legacy/systemd/units/personal-assistant-monitor.timer "$UNIT_TMP/personal-assistant-monitor.timer"
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

docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
docker compose --env-file docker/deployment.env.example -f compose.yaml -f compose.build.yaml config --quiet
git diff --check
"$DEV_PYTHON" scripts/check-docs.py
"$DEV_PYTHON" scripts/generate-command-reference.py --check
"$DEV_PYTHON" scripts/generate-skill-tool-contract.py --check

"$DEV_PYTHON" scripts/check-ruff.py \
  --baseline tests/ruff-baseline.json \
  mail_agent personal_assistant docker tests scripts
"$DEV_PYTHON" scripts/check-mypy.py \
  --baseline tests/mypy-baseline.json \
  mail_agent personal_assistant docker

PYTHONDONTWRITEBYTECODE=1 "$DEV_PYTHON" - <<'PY'
from pathlib import Path
for folder in (Path("mail_agent"), Path("personal_assistant"), Path("tests"), Path("docker"), Path("scripts")):
    for path in folder.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY

./scripts/run-tests.sh </dev/null
"$DEV_PYTHON" scripts/quality-baseline.py >/dev/null
"$DEV_PYTHON" scripts/m8-recovery-drill.py --output build/m8-recovery.json >/dev/null

echo "Repository-Pruefung erfolgreich."
