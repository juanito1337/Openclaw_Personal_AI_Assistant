#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_assistant.nextcloud_talk import (  # noqa: E402
    PLUGIN_ID,
    PLUGIN_PATH,
    build_prepared_policy,
    canonical_json,
    default_config_path,
    default_contract_path,
    default_policy_path,
    default_secret_path,
    digest_json,
    doctor,
    file_digest,
    load_json_object,
    prepared_channel_config,
    status,
)

BACKUP_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}$")


def _emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _require_agent_cli() -> None:
    if (
        os.environ.get("OPENCLAW_RUNTIME", "").strip() != "container"
        or os.environ.get("OPENCLAW_ROLE", "").strip() != "agent-cli"
    ):
        raise PermissionError(
            "Talk-Konfiguration ist nur im Container in der kurzlebigen agent-cli-Rolle erlaubt"
        )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _target_gateway_config(current: dict[str, Any], channel: dict[str, Any]) -> dict[str, Any]:
    target = json.loads(json.dumps(current))
    plugins = target.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise ValueError("openclaw.json plugins muss ein JSON-Objekt sein")
    load = plugins.setdefault("load", {})
    if not isinstance(load, dict):
        raise ValueError("openclaw.json plugins.load muss ein JSON-Objekt sein")
    paths = load.get("paths")
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ValueError("openclaw.json plugins.load.paths muss eine String-Liste sein")
    if PLUGIN_PATH not in paths:
        raise ValueError("Immutable Nextcloud-Talk-Pluginmigration wurde noch nicht ausgefuehrt")
    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError("openclaw.json plugins.entries muss ein JSON-Objekt sein")
    current_entry = entries.get(PLUGIN_ID)
    if current_entry is not None and not isinstance(current_entry, dict):
        raise ValueError("Nextcloud-Talk-Plugineintrag muss ein JSON-Objekt sein")
    entries[PLUGIN_ID] = {"enabled": False}
    channels = target.setdefault("channels", {})
    if not isinstance(channels, dict):
        raise ValueError("openclaw.json channels muss ein JSON-Objekt sein")
    current_channel = channels.get(PLUGIN_ID)
    if isinstance(current_channel, dict) and any(
        key in current_channel for key in ("botSecret", "apiPassword")
    ):
        raise ValueError(
            "Inline-Secrets in der vorhandenen Talk-Konfiguration muessen zuerst sicher entfernt werden"
        )
    channels[PLUGIN_ID] = channel
    return target


def _plan(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config_path)
    current = load_json_object(config_path, label="Gateway-Konfiguration")
    policy = build_prepared_policy(
        base_url=args.base_url,
        webhook_public_url=args.webhook_public_url,
        expected_nextcloud_ips=args.expected_nextcloud_ip,
        expected_webhook_ips=args.expected_webhook_ip,
    )
    channel = prepared_channel_config(
        base_url=policy["base_url"], webhook_public_url=policy["webhook_public_url"]
    )
    target = _target_gateway_config(current, channel)
    current_digest = file_digest(config_path)
    target_digest = digest_json(target)
    policy_digest = digest_json(policy)
    preview = {
        "operation": "nextcloud-talk.prepare-disabled",
        "current_config_sha256": current_digest,
        "target_config_sha256": target_digest,
        "policy_sha256": policy_digest,
        "base_url": policy["base_url"],
        "webhook_public_url": policy["webhook_public_url"],
        "expected_nextcloud_ips": policy["expected_nextcloud_ips"],
        "expected_webhook_ips": policy["expected_webhook_ips"],
        "plugin_enabled_after": False,
        "channel_enabled_after": False,
        "inline_secret_after": False,
    }
    return {
        "ok": True,
        "mode": "preview",
        "changed": canonical_json(current) != canonical_json(target)
        or not Path(args.policy_path).is_file()
        or canonical_json(load_json_object(Path(args.policy_path), label="Talk-Policy"))
        != canonical_json(policy),
        "preview_sha256": digest_json(preview),
        "preview": preview,
        "target_config": target,
        "target_policy": policy,
    }


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key not in {"target_config", "target_policy"}}


def _backup(config_path: Path, policy_path: Path, backup_root: Path, preview_digest: str) -> str:
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"{timestamp}-{preview_digest[:12]}"
    target = backup_root / backup_id
    target.mkdir(parents=True, mode=0o700)
    os.chmod(target, 0o700)
    config_backup = target / "openclaw.json"
    shutil.copyfile(config_path, config_backup)
    os.chmod(config_backup, 0o600)
    policy_present = policy_path.is_file()
    policy_digest = None
    if policy_present:
        policy_backup = target / "policy.json"
        shutil.copyfile(policy_path, policy_backup)
        os.chmod(policy_backup, 0o600)
        policy_digest = file_digest(policy_backup)
    manifest = {
        "schema_version": 1,
        "backup_id": backup_id,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "openclaw_sha256": file_digest(config_backup),
        "policy_present": policy_present,
        "policy_sha256": policy_digest,
    }
    _atomic_write(target / "manifest.json", canonical_json(manifest))
    return backup_id


def _apply(args: argparse.Namespace) -> dict[str, Any]:
    _require_agent_cli()
    if not args.yes:
        raise PermissionError("Anwendung erfordert --yes")
    plan = _plan(args)
    if args.expected_preview_sha256 != plan["preview_sha256"]:
        raise ValueError(
            "Vorschau ist abgelaufen oder Argumente beziehungsweise Konfiguration wurden veraendert"
        )
    if not plan["changed"]:
        return {**_public_plan(plan), "mode": "apply", "changed": False, "backup_id": None}
    config_path = Path(args.config_path)
    policy_path = Path(args.policy_path)
    backup_root = policy_path.parent / "backups"
    backup_id = _backup(config_path, policy_path, backup_root, plan["preview_sha256"])
    try:
        # Disable the plugin/channel first. A crash before policy publication is
        # therefore visibly inconsistent but never accidentally activates Talk.
        _atomic_write(config_path, canonical_json(plan["target_config"]))
        _atomic_write(policy_path, canonical_json(plan["target_policy"]))
    except Exception:
        _restore_backup(
            config_path=config_path,
            policy_path=policy_path,
            backup_root=backup_root,
            backup_id=backup_id,
        )
        raise
    result = status(
        config_path=config_path,
        policy_path=policy_path,
        secret_path=Path(args.secret_path),
        contract_path=Path(args.contract_path),
    )
    if not result.get("ok"):
        _restore_backup(
            config_path=config_path,
            policy_path=policy_path,
            backup_root=backup_root,
            backup_id=backup_id,
        )
        raise RuntimeError(
            "Talk-Konfiguration bestand die lokale Ruecklesepruefung nicht; Backup restauriert"
        )
    return {
        **_public_plan(plan),
        "mode": "apply",
        "changed": True,
        "backup_id": backup_id,
        "postcondition": result,
    }


def _restore_backup(*, config_path: Path, policy_path: Path, backup_root: Path, backup_id: str) -> None:
    if not BACKUP_ID.fullmatch(backup_id):
        raise ValueError("Ungueltige Backup-ID")
    target = backup_root / backup_id
    if target.parent.resolve() != backup_root.resolve() or not target.is_dir() or target.is_symlink():
        raise ValueError("Talk-Backup wurde nicht gefunden")
    manifest = load_json_object(target / "manifest.json", label="Talk-Backupmanifest")
    if (
        set(manifest)
        != {
            "schema_version",
            "backup_id",
            "created_at",
            "openclaw_sha256",
            "policy_present",
            "policy_sha256",
        }
        or manifest.get("schema_version") != 1
        or manifest.get("backup_id") != backup_id
    ):
        raise ValueError("Talk-Backupmanifest hat eine ungueltige Identitaet oder Struktur")
    config_backup = target / "openclaw.json"
    if config_backup.is_symlink() or not config_backup.is_file():
        raise ValueError("Talk-Backup der Gateway-Konfiguration ist keine regulaere Datei")
    if file_digest(config_backup) != manifest.get("openclaw_sha256"):
        raise ValueError("Talk-Backup der Gateway-Konfiguration ist beschaedigt")
    policy_present = manifest.get("policy_present") is True
    if policy_present:
        policy_backup = target / "policy.json"
        if policy_backup.is_symlink() or not policy_backup.is_file():
            raise ValueError("Talk-Policy-Backup ist keine regulaere Datei")
        if file_digest(policy_backup) != manifest.get("policy_sha256"):
            raise ValueError("Talk-Policy-Backup ist beschaedigt")
    _atomic_write(config_path, config_backup.read_bytes())
    if policy_present:
        _atomic_write(policy_path, (target / "policy.json").read_bytes())
    elif policy_path.is_symlink() or policy_path.exists():
        if not policy_path.is_file() or policy_path.is_symlink():
            raise ValueError("Aktuelle Talk-Policy ist keine regulaere Datei")
        policy_path.unlink()


def _rollback(args: argparse.Namespace) -> dict[str, Any]:
    _require_agent_cli()
    if not args.yes:
        raise PermissionError("Rollback erfordert --yes")
    config_path = Path(args.config_path)
    policy_path = Path(args.policy_path)
    _restore_backup(
        config_path=config_path,
        policy_path=policy_path,
        backup_root=policy_path.parent / "backups",
        backup_id=args.backup_id,
    )
    return {
        "ok": True,
        "mode": "rollback",
        "backup_id": args.backup_id,
        "config_sha256": file_digest(config_path),
        "policy_present": policy_path.is_file(),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="M17 Nextcloud-Talk-Konfiguration")
    result.add_argument("--config-path", default=str(default_config_path()))
    result.add_argument("--policy-path", default=str(default_policy_path()))
    result.add_argument("--secret-path", default=str(default_secret_path()))
    result.add_argument("--contract-path", default=str(default_contract_path()))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--no-live", action="store_true")
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--base-url", required=True)
        command.add_argument("--webhook-public-url", required=True)
        command.add_argument("--expected-nextcloud-ip", action="append", required=True)
        command.add_argument("--expected-webhook-ip", action="append", required=True)
        if name == "apply":
            command.add_argument("--expected-preview-sha256", required=True)
            command.add_argument("--yes", action="store_true")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--backup-id", required=True)
    rollback.add_argument("--yes", action="store_true")
    return result


def main() -> int:
    os.umask(0o077)
    args = parser().parse_args()
    try:
        if args.command == "status":
            payload = status(
                config_path=Path(args.config_path),
                policy_path=Path(args.policy_path),
                secret_path=Path(args.secret_path),
                contract_path=Path(args.contract_path),
            )
        elif args.command == "doctor":
            payload = doctor(
                config_path=Path(args.config_path),
                policy_path=Path(args.policy_path),
                secret_path=Path(args.secret_path),
                contract_path=Path(args.contract_path),
                live=not args.no_live,
            )
        elif args.command == "plan":
            payload = _public_plan(_plan(args))
        elif args.command == "apply":
            payload = _apply(args)
        else:
            payload = _rollback(args)
        _emit(payload)
        return 0 if payload.get("ok") else 1
    except (OSError, ValueError, PermissionError, RuntimeError) as exc:
        _emit({"ok": False, "operation": f"nextcloud-talk.{args.command}", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
