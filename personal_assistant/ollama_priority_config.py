from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def normalize_base_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Ollama-Basis-URL muss eine vollstaendige HTTP(S)-URL sein")
    if parsed.username or parsed.password:
        raise ValueError("Ollama-Basis-URL darf keine Zugangsdaten enthalten")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Ollama-Basis-URL darf keinen Unterpfad, Query oder Fragment enthalten")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def read_mail_base_url(path: Path) -> str:
    with Path(path).open("rb") as handle:
        payload = tomllib.load(handle)
    ollama = payload.get("ollama")
    if not isinstance(ollama, dict):
        raise ValueError("Abschnitt [ollama] fehlt")
    return normalize_base_url(str(ollama.get("base_url") or ""))


def _atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = mode
    if existing_mode is None and path.exists():
        existing_mode = path.stat().st_mode & 0o777
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def set_mail_base_url(path: Path, new_url: str) -> str:
    path = Path(path)
    normalized = normalize_base_url(new_url)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_ollama = False
    replaced = False
    pattern = re.compile(r"^(\s*base_url\s*=\s*)([\"']).*?\2(\s*(?:#.*)?(?:\r?\n)?)$")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_ollama = stripped == "[ollama]"
            continue
        if not in_ollama:
            continue
        match = pattern.match(line)
        if match:
            quote = match.group(2)
            lines[index] = f"{match.group(1)}{quote}{normalized}{quote}{match.group(3)}"
            replaced = True
            break
    if not replaced:
        raise ValueError("ollama.base_url konnte in der Konfiguration nicht ersetzt werden")
    _atomic_write(path, "".join(lines))
    return normalized


def _provider_base_url(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return None
    ollama = providers.get("ollama")
    if not isinstance(ollama, dict):
        return None
    value = ollama.get("baseUrl") or ollama.get("base_url")
    return str(value).strip() if value else None


def _is_loopback_priority_proxy(value: str) -> bool:
    normalized = normalize_base_url(value)
    parsed = urlsplit(normalized)
    return (
        parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.port == 11435
    )


def _set_provider_base_url(payload: dict[str, Any], new_url: str) -> None:
    providers = payload["providers"]
    ollama = providers["ollama"]
    key = "baseUrl" if "baseUrl" in ollama else "base_url"
    ollama[key] = new_url


def normalize_gateway_base_url(path: Path, new_url: str) -> bool:
    """Replace a legacy host-loopback proxy URL in active openclaw.json.

    Container migration may only translate the known native priority-proxy
    endpoint. An unrelated provider URL is configuration drift and fails closed
    instead of being overwritten silently.
    """

    path = Path(path)
    if not path.is_file():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"openclaw.json muss ein JSON-Objekt enthalten: {path}")
    models = payload.get("models")
    if not isinstance(models, dict):
        return False
    current_value = _provider_base_url(models)
    if not current_value:
        return False
    current = normalize_base_url(current_value)
    expected = normalize_base_url(new_url)
    if current == expected:
        return False
    if not _is_loopback_priority_proxy(current):
        raise ValueError(
            f"Abweichende Ollama-URL in {path}: {current}; erwartet den "
            f"nativen Loopback-Proxy auf Port 11435 oder {expected}"
        )
    _set_provider_base_url(models, expected)
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return True


def ensure_gateway_timeouts(
    path: Path,
    *,
    provider_timeout_seconds: int,
    agent_timeout_seconds: int,
) -> bool:
    """Add explicit slow-provider limits without replacing operator choices."""

    if provider_timeout_seconds <= 0 or agent_timeout_seconds < provider_timeout_seconds:
        raise ValueError("Agent-Timeout muss positiv und mindestens so gross wie der Provider-Timeout sein")
    path = Path(path)
    if not path.is_file():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"openclaw.json muss ein JSON-Objekt enthalten: {path}")
    models = payload.get("models")
    if not isinstance(models, dict):
        return False
    providers = models.get("providers")
    if not isinstance(providers, dict):
        return False
    ollama = providers.get("ollama")
    if not isinstance(ollama, dict):
        return False

    changed = False
    if "timeoutSeconds" not in ollama:
        ollama["timeoutSeconds"] = provider_timeout_seconds
        changed = True
    agents = payload.get("agents")
    if agents is None:
        agents = {}
        payload["agents"] = agents
    if not isinstance(agents, dict):
        raise ValueError(f"agents in openclaw.json muss ein JSON-Objekt sein: {path}")
    defaults = agents.get("defaults")
    if defaults is None:
        defaults = {}
        agents["defaults"] = defaults
    if not isinstance(defaults, dict):
        raise ValueError(f"agents.defaults in openclaw.json muss ein JSON-Objekt sein: {path}")
    if "timeoutSeconds" not in defaults:
        defaults["timeoutSeconds"] = agent_timeout_seconds
        changed = True
    if changed:
        _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return changed


def find_model_overrides(agents_root: Path) -> list[dict[str, str]]:
    root = Path(agents_root).expanduser()
    results: list[dict[str, str]] = []
    if not root.exists():
        return results
    for path in sorted(root.glob("*/agent/models.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            value = _provider_base_url(payload)
            if value:
                results.append({"path": str(path), "base_url": normalize_base_url(value)})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            results.append({"path": str(path), "error": str(exc)})
    return results


def set_model_overrides(agents_root: Path, old_url: str, new_url: str) -> list[str]:
    old_normalized = normalize_base_url(old_url)
    new_normalized = normalize_base_url(new_url)
    changed: list[str] = []
    for record in find_model_overrides(agents_root):
        if record.get("error"):
            raise ValueError(f"Ungueltige models.json: {record['path']}: {record['error']}")
        current = record.get("base_url")
        if current == new_normalized:
            continue
        if current != old_normalized:
            raise ValueError(
                f"Abweichende Ollama-URL in {record['path']}: {current}; "
                f"erwartet {old_normalized} oder {new_normalized}"
            )
        path = Path(record["path"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        _set_provider_base_url(payload, new_normalized)
        _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        changed.append(str(path))
    return changed


def normalize_model_overrides(agents_root: Path, new_url: str) -> list[str]:
    """Translate known host-loopback model overrides to the container proxy."""

    expected = normalize_base_url(new_url)
    changed: list[str] = []
    for record in find_model_overrides(agents_root):
        if record.get("error"):
            raise ValueError(f"Ungueltige models.json: {record['path']}: {record['error']}")
        current = record.get("base_url")
        if current == expected:
            continue
        if current is None or not _is_loopback_priority_proxy(current):
            raise ValueError(
                f"Abweichende Ollama-URL in {record['path']}: {current}; erwartet den "
                f"nativen Loopback-Proxy auf Port 11435 oder {expected}"
            )
        path = Path(record["path"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        _set_provider_base_url(payload, expected)
        _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        changed.append(str(path))
    return changed


def ensure_model_override_timeouts(
    agents_root: Path,
    *,
    provider_timeout_seconds: int,
) -> list[str]:
    """Add the provider timeout to explicit per-agent Ollama catalogs."""

    if provider_timeout_seconds <= 0:
        raise ValueError("Provider-Timeout muss positiv sein")
    changed: list[str] = []
    root = Path(agents_root).expanduser()
    if not root.exists():
        return changed
    for path in sorted(root.glob("*/agent/models.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Ungueltige models.json: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Ungueltige models.json: {path}: kein JSON-Objekt")
        providers = payload.get("providers")
        if not isinstance(providers, dict):
            continue
        ollama = providers.get("ollama")
        if not isinstance(ollama, dict) or "timeoutSeconds" in ollama:
            continue
        ollama["timeoutSeconds"] = provider_timeout_seconds
        _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        changed.append(str(path))
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Konfigurationshilfe fuer den OpenClaw-Ollama-Prioritaetsproxy"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    get_mail = sub.add_parser("get-mail")
    get_mail.add_argument("path", type=Path)
    set_mail = sub.add_parser("set-mail")
    set_mail.add_argument("path", type=Path)
    set_mail.add_argument("url")
    inspect = sub.add_parser("inspect-overrides")
    inspect.add_argument("agents_root", type=Path)
    update = sub.add_parser("set-overrides")
    update.add_argument("agents_root", type=Path)
    update.add_argument("old_url")
    update.add_argument("new_url")
    normalize = sub.add_parser("normalize")
    normalize.add_argument("url")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "get-mail":
            print(read_mail_base_url(args.path))
        elif args.command == "set-mail":
            print(set_mail_base_url(args.path, args.url))
        elif args.command == "inspect-overrides":
            print(json.dumps(find_model_overrides(args.agents_root), indent=2, ensure_ascii=False))
        elif args.command == "set-overrides":
            changed = set_model_overrides(args.agents_root, args.old_url, args.new_url)
            print(json.dumps({"ok": True, "changed": changed}, indent=2, ensure_ascii=False))
        elif args.command == "normalize":
            print(normalize_base_url(args.url))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
