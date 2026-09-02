"""Fail-closed M4 container entrypoint and strict environment-file loader."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ENV_ROOTS = (Path("/etc/openclaw-env"), Path("/run/openclaw-env"))
ROLE_ENV_FILES: dict[str, tuple[str, ...]] = {
    "standalone": (),
    "layout-init": (),
    "ollama-proxy": ("/etc/openclaw-env/ollama-priority.env",),
    "gateway": (
        "/run/openclaw-env/mail-agent.env",
        "/run/openclaw-env/personal-assistant.env",
        "/run/openclaw-env/gateway.env",
    ),
    "mail-worker": (
        "/etc/openclaw-env/mail-agent.env",
        "/run/openclaw-env/mail-agent.env",
        "/run/openclaw-env/personal-assistant.env",
    ),
    "sync-worker": (
        "/etc/openclaw-env/mail-agent.env",
        "/etc/openclaw-env/personal-assistant.env",
        "/run/openclaw-env/mail-agent.env",
        "/run/openclaw-env/personal-assistant.env",
    ),
    # The supervisor observes coordination telemetry only. It owns neither
    # domain credentials nor the gateway credential.
    "supervisor-worker": (),
    "portfolio-worker": (
        "/etc/openclaw-env/personal-assistant.env",
        "/run/openclaw-env/personal-assistant.env",
    ),
    "monitor-worker": (
        "/etc/openclaw-env/mail-agent.env",
        "/etc/openclaw-env/personal-assistant.env",
        "/run/openclaw-env/mail-agent.env",
        "/run/openclaw-env/personal-assistant.env",
    ),
    "agent-cli": (
        "/etc/openclaw-env/mail-agent.env",
        "/etc/openclaw-env/personal-assistant.env",
        "/run/openclaw-env/mail-agent.env",
        "/run/openclaw-env/personal-assistant.env",
        "/run/openclaw-env/gateway.env",
    ),
}

ALLOWED_KEYS = {
    "HIMALAYA_CONFIG",
    "MAIL_AGENT_ALLOW_FORCE",
    "MAIL_AGENT_CALDAV_PASSWORD",
    "MAIL_AGENT_CALDAV_URL",
    "MAIL_AGENT_CALDAV_USERNAME",
    "MAIL_AGENT_TELEMETRY",
    "NEXTCLOUD_TOKEN",
    "NEXTCLOUD_URL",
    "NEXTCLOUD_USER",
    "OLLAMA_PRIORITY_BACKGROUND_BURST_CONCURRENCY",
    "OLLAMA_PRIORITY_BACKGROUND_BURST_IDLE_SECONDS",
    "OLLAMA_PRIORITY_BACKGROUND_CONCURRENCY",
    "OLLAMA_PRIORITY_BUFFER_BYTES",
    "OLLAMA_PRIORITY_CONNECT_TIMEOUT",
    "OLLAMA_PRIORITY_LISTEN_HOST",
    "OLLAMA_PRIORITY_LISTEN_PORT",
    "OLLAMA_PRIORITY_LOG_LEVEL",
    "OLLAMA_PRIORITY_MAX_CONCURRENCY",
    "OLLAMA_PRIORITY_MAX_PENDING",
    "OLLAMA_PRIORITY_QUEUE_TIMEOUT",
    "OLLAMA_PRIORITY_STARVATION_SECONDS",
    "OLLAMA_PRIORITY_UPSTREAM",
    "OLLAMA_PRIORITY_UPSTREAM_TIMEOUT",
    "OPENCLAW_GATEWAY_PASSWORD",
    "OPENCLAW_GATEWAY_TOKEN",
    "PERSONAL_ASSISTANT_ALLOW_HTTP",
    "PORTFOLIO_EODHD_API_KEY",
    "PORTFOLIO_MARKET_DATA_API_KEY",
    "PYTHONUNBUFFERED",
}
KEY_RE = re.compile(r"([A-Z][A-Z0-9_]*)=(.*)")
MAX_ENV_BYTES = 64 * 1024
PERSONAL_ASSISTANT_PLUGIN_ID = "personal-assistant-tools"
PERSONAL_ASSISTANT_PLUGIN_PATH = "/opt/openclaw-plugins/personal-assistant-tools"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a bounded KEY=VALUE file without expansion or code execution."""
    if not any(path.parent == root for root in ENV_ROOTS):
        raise ValueError(f"Env-Datei ausserhalb der festen Mountwurzeln: {path}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ENV_BYTES:
        raise ValueError(f"Env-Datei ist nicht regulaer oder zu gross: {path}")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = KEY_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"Ungueltige KEY=VALUE-Zeile {path}:{number}")
        key, encoded = match.groups()
        if key not in ALLOWED_KEYS:
            raise ValueError(f"Nicht freigegebener Env-Schluessel {key} in {path}:{number}")
        if encoded.startswith(("'", '"')):
            try:
                parts = shlex.split(encoded, comments=False, posix=True)
            except ValueError as exc:
                raise ValueError(f"Ungueltige Quotierung {path}:{number}") from exc
            if len(parts) != 1:
                raise ValueError(f"Ungueltiger quotierter Wert {path}:{number}")
            value = parts[0]
        else:
            if any(character.isspace() for character in encoded):
                raise ValueError(f"Unquotiertes Leerzeichen {path}:{number}")
            value = encoded
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"Ungueltiges Steuerzeichen {path}:{number}")
        values[key] = value
    return values


def load_role_environment(role: str, environment: dict[str, str]) -> None:
    try:
        files = ROLE_ENV_FILES[role]
    except KeyError as exc:
        raise ValueError(f"Unbekannte Containerrolle: {role}") from exc
    for name in files:
        path = Path(name)
        if not path.is_file():
            raise ValueError(f"Erforderliche Env-Datei fehlt fuer {role}: {path}")
        environment.update(parse_env_file(path))


def load_mounted_role_environment(role: str, environment: dict[str, str]) -> bool:
    """Reload a role's mounted env files for a process started after PID 1.

    ``docker exec`` processes do not inherit variables which the container
    entrypoint added only to its child process.  The registered assistant CLI
    therefore uses this bounded variant: no mounted role file means that the
    command is a standalone image smoke; one visible role file makes the full
    role contract mandatory and is parsed with the same fail-closed loader as
    PID 1.
    """
    try:
        files = ROLE_ENV_FILES[role]
    except KeyError as exc:
        raise ValueError(f"Unbekannte Containerrolle: {role}") from exc
    if not files or not any(Path(name).is_file() for name in files):
        return False
    load_role_environment(role, environment)
    return True


def normalize_proxy_network(environment: dict[str, str]) -> None:
    """Pin the container listener and translate the former host-loopback default."""
    environment["OLLAMA_PRIORITY_LISTEN_HOST"] = "0.0.0.0"
    environment["OLLAMA_PRIORITY_LISTEN_PORT"] = "11435"
    upstream = environment.get("OLLAMA_PRIORITY_UPSTREAM", "").strip()
    parsed = urlsplit(upstream)
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        host = "host.docker.internal"
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        environment["OLLAMA_PRIORITY_UPSTREAM"] = urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )


def configure_custom_ca(environment: dict[str, str]) -> None:
    ca_dir = Path(environment.get("OPENCLAW_CA_DIR", "/etc/openclaw-ca"))
    if not ca_dir.is_dir():
        return
    certificates = sorted(path for path in ca_dir.glob("*.crt") if path.is_file() and path.stat().st_size)
    if not certificates:
        return
    runtime_dir = Path("/tmp/openclaw-ca")
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    bundle = runtime_dir / "ca-certificates.crt"
    source = Path("/etc/ssl/certs/ca-certificates.crt")
    with bundle.open("wb") as target:
        target.write(source.read_bytes())
        for certificate in certificates:
            target.write(b"\n")
            target.write(certificate.read_bytes())
    bundle.chmod(0o600)
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        environment[key] = str(bundle)


def ensure_personal_assistant_plugin_config(path: Path) -> bool:
    """Enable the immutable image-owned bridge without weakening other plugin policy."""

    from personal_assistant.immutable_plugins import ensure_tool_loop_detection_config

    if not path.is_file():
        raise ValueError(f"Gateway-Konfiguration fehlt: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("openclaw.json muss ein JSON-Objekt sein")
    tools = data.setdefault("tools", {})
    if not isinstance(tools, dict):
        raise ValueError("openclaw.json tools muss ein JSON-Objekt sein")
    also_allow = tools.setdefault("alsoAllow", [])
    if not isinstance(also_allow, list) or not all(isinstance(item, str) for item in also_allow):
        raise ValueError("openclaw.json tools.alsoAllow muss eine String-Liste sein")
    plugins = data.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise ValueError("openclaw.json plugins muss ein JSON-Objekt sein")
    load = plugins.setdefault("load", {})
    if not isinstance(load, dict):
        raise ValueError("openclaw.json plugins.load muss ein JSON-Objekt sein")
    paths = load.setdefault("paths", [])
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ValueError("openclaw.json plugins.load.paths muss eine String-Liste sein")
    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError("openclaw.json plugins.entries muss ein JSON-Objekt sein")
    entry = entries.setdefault(PERSONAL_ASSISTANT_PLUGIN_ID, {})
    if not isinstance(entry, dict):
        raise ValueError("Personal-Assistant-Pluginkonfiguration muss ein JSON-Objekt sein")
    hooks = entry.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Personal-Assistant-Plugin-Hooks muessen ein JSON-Objekt sein")

    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if PERSONAL_ASSISTANT_PLUGIN_ID not in also_allow:
        also_allow.append(PERSONAL_ASSISTANT_PLUGIN_ID)
    if PERSONAL_ASSISTANT_PLUGIN_PATH not in paths:
        paths.append(PERSONAL_ASSISTANT_PLUGIN_PATH)
    ensure_tool_loop_detection_config(tools)
    entry["enabled"] = True
    hooks["allowConversationAccess"] = True
    hooks["allowPromptInjection"] = True
    if before == json.dumps(data, ensure_ascii=False, sort_keys=True):
        return False

    temporary = path.with_name(f".{path.name}.personal-assistant-{os.getpid()}")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)
    return True


def verify_layout(workspace: Path) -> None:
    marker = workspace / ".layout-version.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if payload.get("layout") != 3:
        raise ValueError(f"Layout-v3-Marker ungueltig: {marker}")


def main(argv: list[str] | None = None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        raise ValueError("Containerkommando fehlt")
    environment = os.environ.copy()
    role = environment.get("OPENCLAW_ROLE", "standalone")
    load_role_environment(role, environment)
    if role == "ollama-proxy":
        normalize_proxy_network(environment)

    image_root = Path(environment.get("OPENCLAW_IMAGE_ROOT", "/opt/openclaw-agent")).resolve()
    workspace = Path(environment.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace")).resolve()
    state_root = Path(environment.get("OPENCLAW_STATE_ROOT", str(workspace.parent))).resolve()
    layout_mode = environment.get("OPENCLAW_LAYOUT_MODE", "migrate")
    protected = {
        "HOME": "/home/node",
        "OPENCLAW_IMAGE_ROOT": str(image_root),
        "OPENCLAW_CODE_ROOT": str(image_root),
        "OPENCLAW_RELEASE_ROOT": str(image_root),
        "OPENCLAW_STATE_ROOT": str(state_root),
        "OPENCLAW_WORKSPACE": str(workspace),
        "PYTHONPATH": str(image_root),
        "PYTHONSAFEPATH": "1",
        "PATH": f"{image_root}/scripts:/usr/local/bin:/usr/bin:/bin",
    }
    environment.update(protected)
    if layout_mode == "verify":
        environment.update(
            {
                "MAIL_AGENT_CONFIG": str(workspace / "mail_agent/config.toml"),
                "PERSONAL_ASSISTANT_CONFIG": str(workspace / "personal_assistant/config.toml"),
                "OPENCLAW_TOOLS_CONFIG": str(workspace / "personal_assistant/tools.toml"),
                "OPENCLAW_GATEWAY_DATA_DIR": "/home/node/.openclaw",
                "OPENCLAW_MAIL_DATA_DIR": "/var/lib/openclaw/mail",
                "OPENCLAW_CORE_DATA_DIR": "/var/lib/openclaw/core",
                "OPENCLAW_KNOWLEDGE_DATA_DIR": "/var/lib/openclaw/knowledge",
                "OPENCLAW_ORDERS_DATA_DIR": "/var/lib/openclaw/orders",
                "OPENCLAW_PORTFOLIO_DATA_DIR": "/var/lib/openclaw/portfolio",
                "OPENCLAW_MONITORING_DATA_DIR": "/var/lib/openclaw/monitoring",
                "OPENCLAW_SECURITY_DATA_DIR": "/var/lib/openclaw/security",
                "OPENCLAW_COORDINATION_DATA_DIR": "/var/lib/openclaw/coordination",
                "OPENCLAW_LOG_DIR": "/var/lib/openclaw/coordination/container_logs",
                "OPENCLAW_JOB_STATUS_DIR": "/var/lib/openclaw/coordination/container_jobs",
            }
        )

    configure_custom_ca(environment)
    if layout_mode == "migrate":
        subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "personal_assistant.runtime_layout",
                "migrate",
                "--image-root",
                str(image_root),
                "--state-root",
                str(state_root),
                "--workspace",
                str(workspace),
            ],
            env=environment,
            stdout=sys.stderr,
            stderr=sys.stderr,
            check=True,
        )
    elif layout_mode == "verify":
        verify_layout(workspace)
    else:
        raise ValueError(f"Unbekannter OPENCLAW_LAYOUT_MODE: {layout_mode}")

    if role == "gateway":
        from personal_assistant.immutable_plugins import (
            load_contract,
            synchronize_installed_plugin_index,
        )

        gateway_data = Path(environment["OPENCLAW_GATEWAY_DATA_DIR"])
        ensure_personal_assistant_plugin_config(gateway_data / "openclaw.json")
        plugin_contract = load_contract(Path("/usr/share/openclaw/immutable-plugins.json"))
        plugin_report = synchronize_installed_plugin_index(
            gateway_data / "state/openclaw.sqlite",
            plugin_contract,
        )
        if plugin_report["registry_rows_changed"]:
            print(json.dumps({"immutable_plugins": plugin_report}, sort_keys=True), file=sys.stderr)

        # Worker containers never receive the gateway credential. They publish
        # bounded event files to coordination; only this gateway-local relay
        # connects through an accepted loopback WebSocket.
        if environment.get("OPENCLAW_EVENT_QUEUE_DIR", "").strip():
            command = [
                sys.executable,
                "-P",
                "-m",
                "personal_assistant.gateway_events",
                "serve",
                "--",
                *command,
            ]

    executable = shutil.which(command[0], path=environment["PATH"]) if "/" not in command[0] else command[0]
    if executable is None:
        raise FileNotFoundError(command[0])
    os.chdir(workspace)
    os.execve(executable, command, environment)
    return 127


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Container-Initialisierung fehlgeschlagen: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
