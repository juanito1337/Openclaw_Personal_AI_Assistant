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
                f"Abweichende Ollama-URL in {record['path']}: {current}; erwartet {old_normalized} oder {new_normalized}"
            )
        path = Path(record["path"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        ollama = payload["providers"]["ollama"]
        key = "baseUrl" if "baseUrl" in ollama else "base_url"
        ollama[key] = new_normalized
        _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        changed.append(str(path))
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Konfigurationshilfe fuer den OpenClaw-Ollama-Prioritaetsproxy")
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
