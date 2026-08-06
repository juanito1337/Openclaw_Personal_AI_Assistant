#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

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
    parser.add_argument("--ensure-gateway-auth", action="store_true")
    parser.add_argument("--normalize-ollama-proxy", action="store_true")
    parser.add_argument("--legacy-gateway-environment-file", type=Path)
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


GATEWAY_AUTH_KEYS = ("OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_GATEWAY_PASSWORD")


def parse_systemd_environment(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for item in shlex.split(path.read_text(encoding="utf-8")):
        key, separator, value = item.partition("=")
        if separator:
            values[key] = value
    return values


def gateway_config(state_dir: Path) -> tuple[Path, dict[str, object]]:
    path = state_dir / "openclaw.json"
    if not path.is_file():
        raise RuntimeError(f"OpenClaw-Konfiguration fehlt: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("openclaw.json muss ein JSON-Objekt enthalten")
    gateway = data.get("gateway")
    if not isinstance(gateway, dict) or gateway.get("mode") != "local":
        raise RuntimeError("openclaw.json muss gateway.mode=local enthalten")
    return path, data


def selected_gateway_credential(
    values: dict[str, str],
    *,
    configured_mode: str,
    source: str,
    allow_incompatible: bool = False,
) -> tuple[str, str, str] | None:
    available = [(key, values.get(key, "").strip()) for key in GATEWAY_AUTH_KEYS]
    available = [(key, value) for key, value in available if value]
    if not available:
        return None
    if configured_mode in {"token", "password"}:
        expected_key = (
            "OPENCLAW_GATEWAY_TOKEN"
            if configured_mode == "token"
            else "OPENCLAW_GATEWAY_PASSWORD"
        )
        expected_value = values.get(expected_key, "").strip()
        if expected_value:
            return expected_key, expected_value, source
        if allow_incompatible:
            return None
        raise RuntimeError(
            f"Gateway-Auth-Modus {configured_mode!r} widerspricht dem vorhandenen {source}-Secret"
        )
    if len(available) == 1:
        key, value = available[0]
        return key, value, source
    raise RuntimeError(
        f"{source} enthaelt Token und Passwort; gateway.auth.mode muss token oder password auswaehlen"
    )


def config_gateway_credentials(
    data: dict[str, object],
    environment: dict[str, str],
) -> dict[str, str]:
    gateway = data.get("gateway")
    auth = gateway.get("auth") if isinstance(gateway, dict) else None
    if not isinstance(auth, dict):
        return {}

    values: dict[str, str] = {}
    for field, env_key in (
        ("token", "OPENCLAW_GATEWAY_TOKEN"),
        ("password", "OPENCLAW_GATEWAY_PASSWORD"),
    ):
        configured = auth.get(field)
        if isinstance(configured, str) and configured.strip():
            values[env_key] = configured.strip()
            continue
        if isinstance(configured, dict):
            reference_id = configured.get("id")
            if isinstance(reference_id, str) and environment.get(reference_id, "").strip():
                values[env_key] = environment[reference_id].strip()
    return values


def ensure_gateway_auth(
    state_dir: Path,
    config_dir: Path,
    secrets_dir: Path,
    legacy_environment_file: Path | None,
) -> dict[str, str]:
    config_path, data = gateway_config(state_dir)
    gateway = data["gateway"]
    assert isinstance(gateway, dict)
    auth = gateway.get("auth")
    if auth is None:
        auth = {}
        gateway["auth"] = auth
    if not isinstance(auth, dict):
        raise RuntimeError("gateway.auth muss ein JSON-Objekt sein")
    configured_mode = str(auth.get("mode") or "").strip().lower()
    if configured_mode in {"none", "trusted-proxy"}:
        raise RuntimeError(
            f"gateway.auth.mode={configured_mode} ist fuer die direkte LAN-Containerbindung nicht zugelassen"
        )
    if configured_mode not in {"", "token", "password"}:
        raise RuntimeError(f"Unbekannter gateway.auth.mode: {configured_mode}")

    destination = secrets_dir / "gateway.env"
    existing = parse_env_files(secrets_dir)
    legacy_environment = parse_systemd_environment(legacy_environment_file)
    source_environment = parse_env_files(state_dir, config_dir)
    source_environment.update(legacy_environment)
    config_values = config_gateway_credentials(data, source_environment)

    candidates = (
        ("bestehendes Container-Secret", existing),
        ("systemd", legacy_environment),
        ("Legacy-Environment", source_environment),
        ("openclaw.json", config_values),
    )
    selected = None
    incompatible_sources: list[str] = []
    for source, values in candidates:
        available = any(values.get(key, "").strip() for key in GATEWAY_AUTH_KEYS)
        candidate = selected_gateway_credential(
            values,
            configured_mode=configured_mode,
            source=source,
            allow_incompatible=True,
        )
        if candidate is not None:
            selected = candidate
            break
        if available and configured_mode:
            incompatible_sources.append(source)

    if selected is None:
        if configured_mode and incompatible_sources:
            raise RuntimeError(
                f"Kein {configured_mode}-Secret fuer gateway.auth.mode={configured_mode} gefunden; "
                "unpassende Secrets: " + ", ".join(incompatible_sources)
            )
        if configured_mode == "password":
            raise RuntimeError(
                "gateway.auth.mode=password ist konfiguriert, aber kein Passwort-Secret wurde gefunden"
            )
        selected = ("OPENCLAW_GATEWAY_TOKEN", secrets.token_hex(32), "neu erzeugt")

    key, value, source = selected
    selected_mode = "token" if key.endswith("_TOKEN") else "password"
    if configured_mode and configured_mode != selected_mode:
        raise RuntimeError(
            f"gateway.auth.mode={configured_mode} passt nicht zum ausgewaehlten {selected_mode}-Secret"
        )
    if not configured_mode:
        auth["mode"] = selected_mode
        atomic_write(
            config_path,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )

    atomic_write(destination, f"{key}={shlex.quote(value)}\n", mode=0o600)
    return {
        "mode": selected_mode,
        "source": source,
        "file": str(destination),
        "replaced_incompatible_existing": (
            source != "bestehendes Container-Secret"
            and "bestehendes Container-Secret" in incompatible_sources
        ),
        "ignored_incompatible_sources": incompatible_sources,
    }


def normalize_ollama_proxy(state_dir: Path, config_dir: Path) -> dict[str, object]:
    path = state_dir / "workspace/mail_agent/config.toml"
    if not path.is_file():
        raise RuntimeError(f"Mail-Agent-Konfiguration fehlt: {path}")
    environment = parse_env_files(config_dir)
    raw_port = environment.get("OLLAMA_PRIORITY_LISTEN_PORT", "11435").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("OLLAMA_PRIORITY_LISTEN_PORT ist keine Zahl") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("OLLAMA_PRIORITY_LISTEN_PORT ist ungueltig")

    expected = "http://ollama-proxy:11435"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_ollama = False
    found = False
    changed = False
    rewritten: list[str] = []
    for line in lines:
        section = re.match(r"^\s*\[([^\]]+)\]\s*$", line.strip())
        if section:
            in_ollama = section.group(1).strip() == "ollama"
        if in_ollama and re.match(r"^\s*base_url\s*=", line):
            found = True
            newline = "\n" if line.endswith("\n") else ""
            replacement = f'base_url = "{expected}"{newline}'
            changed = changed or replacement != line
            rewritten.append(replacement)
        else:
            rewritten.append(line)
    if not found:
        raise RuntimeError("[ollama].base_url fehlt in mail_agent/config.toml")
    if changed:
        atomic_write(path, "".join(rewritten))
    model_changes: list[str] = []
    for model_path in sorted((state_dir / "agents").glob("*/agent/models.json")):
        payload = json.loads(model_path.read_text(encoding="utf-8"))
        providers = payload.get("providers")
        ollama = providers.get("ollama") if isinstance(providers, dict) else None
        if not isinstance(ollama, dict):
            continue
        key = "baseUrl" if "baseUrl" in ollama else "base_url"
        value = str(ollama.get(key) or "")
        parsed = urlsplit(value)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port != 11435:
            continue
        ollama[key] = expected
        atomic_write(model_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        model_changes.append(str(model_path))
    return {"changed": changed, "base_url": expected, "model_overrides_changed": model_changes}


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
    gateway_auth = {}
    if args.ensure_gateway_auth:
        gateway_auth = ensure_gateway_auth(
            args.state_dir,
            args.config_dir,
            args.secrets_dir,
            args.legacy_gateway_environment_file,
        )
    ollama_proxy = {}
    if args.normalize_ollama_proxy:
        ollama_proxy = normalize_ollama_proxy(args.state_dir, args.config_dir)

    report = {
        "ok": True,
        "source_workspace": old,
        "target_workspace": new,
        "path_changes": path_changes,
        "himalaya_secrets": secret_changes,
        "nextcloud_section_added": nextcloud_added,
        "gateway_auth": gateway_auth,
        "ollama_proxy": ollama_proxy,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # keep migration failure concise for the shell rollback trap
        print(f"Container-Konfigurationsmigration fehlgeschlagen: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
