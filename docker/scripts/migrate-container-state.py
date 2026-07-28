#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


NEXTCLOUD_SECTION = """
[nextcloud]
enabled = true
skill_package = "@keithvassallomt/openclaw-nextcloud"
skill_dir = "skills/openclaw-nextcloud"
base_url_env = "NEXTCLOUD_URL"
username_env = "NEXTCLOUD_USER"
token_env = "NEXTCLOUD_TOKEN"
calendar = ""
addressbook = ""
contacts_enabled = true
contacts_prevent_spam = true
trust_contacts_for_calendar = false
contact_importance_boost = 1
contact_cache_ttl_seconds = 3600
contact_cache_file = "mail_agent/data/nextcloud_contacts_cache.json"
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Container-sichere Pfade, Himalaya-Secrets und optionale Nextcloud-Konfiguration migrieren."
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--secrets-dir", type=Path, required=True)
    parser.add_argument("--source-workspace", required=True)
    parser.add_argument("--target-workspace", required=True)
    parser.add_argument("--enable-nextcloud-if-configured", action="store_true")
    return parser.parse_args()


def atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    if mode is None and path.exists():
        mode = path.stat().st_mode & 0o777
    os.chmod(temporary, mode if mode is not None else 0o600)
    os.replace(temporary, path)


def rewrite_json_strings(value: object, old: str, new: str) -> tuple[object, int]:
    if isinstance(value, dict):
        changed = 0
        rewritten: dict[object, object] = {}
        for key, item in value.items():
            new_item, item_changed = rewrite_json_strings(item, old, new)
            rewritten[key] = new_item
            changed += item_changed
        return rewritten, changed
    if isinstance(value, list):
        changed = 0
        rewritten_list: list[object] = []
        for item in value:
            new_item, item_changed = rewrite_json_strings(item, old, new)
            rewritten_list.append(new_item)
            changed += item_changed
        return rewritten_list, changed
    if isinstance(value, str) and old in value:
        return value.replace(old, new), value.count(old)
    return value, 0


def rewrite_active_paths(state_dir: Path, old: str, new: str) -> list[str]:
    changed_files: list[str] = []
    openclaw_json = state_dir / "openclaw.json"
    if openclaw_json.is_file():
        data = json.loads(openclaw_json.read_text(encoding="utf-8"))
        rewritten, count = rewrite_json_strings(data, old, new)
        if count:
            atomic_write(
                openclaw_json,
                json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
            )
            changed_files.append(f"{openclaw_json} ({count} Pfadwerte)")

    workspace = state_dir / "workspace"
    candidates = [
        workspace / "mail_agent/config.toml",
        workspace / "mail_agent/rules.toml",
        workspace / "personal_assistant/config.toml",
        workspace / "personal_assistant/resources.toml",
        workspace / "personal_assistant/policies.toml",
        workspace / "personal_assistant/tools.toml",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if not count:
            continue
        atomic_write(path, text.replace(old, new))
        changed_files.append(f"{path} ({count} Pfade)")
    return changed_files


def parse_env_files(*directories: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.env")):
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if value and value[0:1] == value[-1:] and value[0:1] in {'"', "'"}:
                    value = value[1:-1]
                values[key] = value
    return values


def ensure_nextcloud_section(state_dir: Path, config_dir: Path, secrets_dir: Path) -> bool:
    path = state_dir / "workspace/mail_agent/config.toml"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^\s*\[nextcloud\]\s*$", text):
        return False

    env = dict(os.environ)
    env.update(parse_env_files(config_dir, secrets_dir))
    required = ("NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_TOKEN")
    if not all(env.get(key, "").strip() for key in required):
        return False

    updated = text.rstrip() + "\n\n" + NEXTCLOUD_SECTION + "\n"
    atomic_write(path, updated)
    return True


AUTH_PATTERNS = {
    "imap": re.compile(r'(?m)^(\s*backend\.auth\.command\s*=\s*)"([^"]*)"\s*$'),
    "smtp": re.compile(r'(?m)^(\s*message\.send\.backend\.auth\.command\s*=\s*)"([^"]*)"\s*$'),
}


def shell_quote_toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def extract_secret(command: str) -> str:
    result = subprocess.run(
        command,
        shell=True,
        executable="/bin/sh",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"Exit-Code {result.returncode}"
        raise RuntimeError(f"Secret-Befehl fehlgeschlagen ({shlex.join(['/bin/sh', '-c', command])}): {detail}")
    secret = result.stdout.rstrip("\r\n")
    if not secret:
        raise RuntimeError(f"Secret-Befehl lieferte keinen Wert: {command}")
    return secret


def migrate_himalaya_secrets(config_dir: Path, secrets_dir: Path) -> list[str]:
    path = config_dir / "himalaya/config.toml"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    changes: list[str] = []

    for kind, pattern in AUTH_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        command = match.group(2)
        target_in_container = f"/run/openclaw-secrets/himalaya-{kind}-password"
        replacement_command = f"cat {target_in_container}"
        if command == replacement_command:
            continue
        if "secret-tool" not in command:
            continue

        secret_path = secrets_dir / f"himalaya-{kind}-password"
        if not secret_path.is_file() or secret_path.stat().st_size == 0:
            secret = extract_secret(command)
            atomic_write(secret_path, secret + "\n", mode=0o600)
        else:
            os.chmod(secret_path, 0o600)

        replacement = match.group(1) + shell_quote_toml(replacement_command)
        text = text[: match.start()] + replacement + text[match.end() :]
        changes.append(kind)

    if changes:
        atomic_write(path, text)
    return changes


def main() -> int:
    args = parse_args()
    old = args.source_workspace.rstrip("/")
    new = args.target_workspace.rstrip("/")
    if not old or not new or old == new:
        raise SystemExit("Quell- und Ziel-Workspace muessen verschieden und nicht leer sein.")

    path_changes = rewrite_active_paths(args.state_dir, old, new)
    secret_changes = migrate_himalaya_secrets(args.config_dir, args.secrets_dir)
    nextcloud_added = False
    if args.enable_nextcloud_if_configured:
        nextcloud_added = ensure_nextcloud_section(args.state_dir, args.config_dir, args.secrets_dir)

    report = {
        "ok": True,
        "source_workspace": old,
        "target_workspace": new,
        "path_changes": path_changes,
        "himalaya_secrets": secret_changes,
        "nextcloud_section_added": nextcloud_added,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # keep migration failure concise for the shell rollback trap
        print(f"Container-Konfigurationsmigration fehlgeschlagen: {exc}", file=sys.stderr)
        raise SystemExit(1)
