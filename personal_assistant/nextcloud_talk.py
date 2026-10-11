from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import ssl
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PLUGIN_ID = "nextcloud-talk"
PLUGIN_PACKAGE = "@openclaw/nextcloud-talk"
PLUGIN_VERSION = "2026.7.1"
PLUGIN_PATH = "/opt/openclaw-plugins/node_modules/@openclaw/nextcloud-talk"
POLICY_SCHEMA_VERSION = 1
WEBHOOK_PATH = "/nextcloud-talk-webhook"
WEBHOOK_PORT = 8788
WEBHOOK_HOST = "0.0.0.0"
BOT_SECRET_FILE = "/run/openclaw-secrets/nextcloud-talk-bot-secret"
HISTORY_LIMIT = 20
TEXT_CHUNK_LIMIT = 3500

POLICY_KEYS = {
    "schema_version",
    "state",
    "base_url",
    "webhook_public_url",
    "expected_nextcloud_ips",
    "expected_webhook_ips",
    "channel_config_sha256",
}
CHANNEL_KEYS = {
    "enabled",
    "baseUrl",
    "botSecretFile",
    "dmPolicy",
    "webhookPort",
    "webhookHost",
    "webhookPath",
    "webhookPublicUrl",
    "allowFrom",
    "groupAllowFrom",
    "groupPolicy",
    "rooms",
    "network",
    "historyLimit",
    "dmHistoryLimit",
    "contextVisibility",
    "textChunkLimit",
    "chunkMode",
    "blockStreaming",
}


def gateway_data_dir() -> Path:
    return Path(os.environ.get("OPENCLAW_GATEWAY_DATA_DIR", "~/.openclaw")).expanduser()


def default_config_path() -> Path:
    return gateway_data_dir() / "openclaw.json"


def default_policy_path() -> Path:
    return gateway_data_dir() / ".nextcloud-talk/policy.json"


def default_secret_path() -> Path:
    return Path(BOT_SECRET_FILE)


def default_contract_path() -> Path:
    configured = os.environ.get("OPENCLAW_IMMUTABLE_PLUGIN_CONTRACT", "").strip()
    if configured:
        return Path(configured)
    installed = Path("/usr/share/openclaw/immutable-plugins.json")
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[1] / "docker/openclaw-plugins/contract.json"


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} fehlt") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} darf kein Symlink sein")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} muss eine regulaere Datei sein")
    if metadata.st_size > 4 * 1024 * 1024:
        raise ValueError(f"{label} ist unerwartet gross")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} ist kein gueltiges JSON-Objekt") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} muss ein JSON-Objekt enthalten")
    return payload


def normalize_https_url(value: str, *, label: str, required_path: str | None = None) -> str:
    raw = value.strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise ValueError(f"{label} muss eine vollstaendige HTTPS-URL sein")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} darf keine Zugangsdaten, Query oder Fragment enthalten")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError(f"{label} muss einen TLS-validierbaren DNS-Namen verwenden")
    path = parsed.path or "/"
    if required_path is not None and path != required_path:
        raise ValueError(f"{label} muss exakt den Pfad {required_path} verwenden")
    if required_path is None and path not in {"", "/"}:
        raise ValueError(f"{label} darf keinen Unterpfad enthalten")
    if required_path is not None and parsed.port not in {None, 443}:
        raise ValueError(f"{label} darf nur den HTTPS-Standardport 443 verwenden")
    normalized_path = required_path if required_path is not None else ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"https://{parsed.hostname.casefold()}{port}{normalized_path}"


def normalize_private_ips(values: list[str] | tuple[str, ...], *, label: str) -> list[str]:
    result: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError(f"{label} enthaelt keine gueltige IP-Adresse") from exc
        if (
            not address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError(f"{label} muss auf eine private LAN-Adresse begrenzt sein")
        rendered = str(address)
        if rendered not in result:
            result.append(rendered)
    if not result:
        raise ValueError(f"{label} darf nicht leer sein")
    return sorted(result)


def prepared_channel_config(*, base_url: str, webhook_public_url: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "baseUrl": normalize_https_url(base_url, label="Nextcloud-Basis-URL"),
        "botSecretFile": BOT_SECRET_FILE,
        "dmPolicy": "disabled",
        "webhookPort": WEBHOOK_PORT,
        "webhookHost": WEBHOOK_HOST,
        "webhookPath": WEBHOOK_PATH,
        "webhookPublicUrl": normalize_https_url(
            webhook_public_url,
            label="oeffentliche Webhook-URL",
            required_path=WEBHOOK_PATH,
        ),
        "allowFrom": [],
        "groupAllowFrom": [],
        "groupPolicy": "disabled",
        "rooms": {},
        "network": {"dangerouslyAllowPrivateNetwork": True},
        "historyLimit": HISTORY_LIMIT,
        "dmHistoryLimit": HISTORY_LIMIT,
        "contextVisibility": "allowlist",
        "textChunkLimit": TEXT_CHUNK_LIMIT,
        "chunkMode": "newline",
        "blockStreaming": True,
    }


def build_prepared_policy(
    *,
    base_url: str,
    webhook_public_url: str,
    expected_nextcloud_ips: list[str] | tuple[str, ...],
    expected_webhook_ips: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    channel = prepared_channel_config(base_url=base_url, webhook_public_url=webhook_public_url)
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "state": "prepared-disabled",
        "base_url": channel["baseUrl"],
        "webhook_public_url": channel["webhookPublicUrl"],
        "expected_nextcloud_ips": normalize_private_ips(
            expected_nextcloud_ips, label="erwartete Nextcloud-IP"
        ),
        "expected_webhook_ips": normalize_private_ips(
            expected_webhook_ips, label="erwartete Webhook-IP"
        ),
        "channel_config_sha256": digest_json(channel),
    }


def validate_policy(payload: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(payload) - POLICY_KEYS)
    if unknown:
        raise ValueError(f"Talk-Policy enthaelt unbekannte Felder: {', '.join(unknown)}")
    missing = sorted(POLICY_KEYS - set(payload))
    if missing:
        raise ValueError(f"Talk-Policy enthaelt nicht alle Pflichtfelder: {', '.join(missing)}")
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise ValueError("Talk-Policy hat eine unbekannte Schemaversion")
    if payload.get("state") != "prepared-disabled":
        raise ValueError("Talk-Policy ist nicht im sicheren vorbereiteten Zustand")
    policy = build_prepared_policy(
        base_url=str(payload.get("base_url", "")),
        webhook_public_url=str(payload.get("webhook_public_url", "")),
        expected_nextcloud_ips=list(payload.get("expected_nextcloud_ips") or []),
        expected_webhook_ips=list(payload.get("expected_webhook_ips") or []),
    )
    if payload != policy:
        raise ValueError("Talk-Policy ist nicht kanonisch oder wurde veraendert")
    return policy


def inspect_secret(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "present": False,
        "regular": False,
        "symlink": False,
        "mode": None,
        "readable": False,
        "nonempty": False,
        "size_within_limit": False,
        "ok": False,
    }
    try:
        metadata = path.lstat()
    except OSError:
        return result
    result["present"] = True
    result["symlink"] = stat.S_ISLNK(metadata.st_mode)
    result["regular"] = stat.S_ISREG(metadata.st_mode)
    result["mode"] = f"{stat.S_IMODE(metadata.st_mode):04o}"
    result["size_within_limit"] = 0 < metadata.st_size <= 4096
    if result["symlink"] or not result["regular"] or stat.S_IMODE(metadata.st_mode) & 0o077:
        return result
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            content = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
    except OSError:
        return result
    result["readable"] = True
    result["nonempty"] = bool(content.strip())
    result["size_within_limit"] = bool(content) and len(content) <= 4096
    result["ok"] = bool(result["nonempty"] and result["size_within_limit"])
    return result


def _plugin_contract(path: Path) -> dict[str, Any]:
    try:
        payload = load_json_object(path, label="Immutable-Pluginvertrag")
        plugins = payload.get("plugins")
        entry = plugins.get(PLUGIN_ID) if isinstance(plugins, dict) else None
        valid = bool(
            isinstance(entry, dict)
            and entry.get("package") == PLUGIN_PACKAGE
            and entry.get("version") == PLUGIN_VERSION
            and entry.get("path") == PLUGIN_PATH
            and isinstance(entry.get("integrity"), str)
            and entry.get("integrity")
        )
        return {
            "present": True,
            "valid": valid,
            "version": entry.get("version") if isinstance(entry, dict) else None,
        }
    except ValueError:
        return {"present": False, "valid": False, "version": None}


def _safe_disabled_without_policy(config: dict[str, Any]) -> bool:
    channels = config.get("channels")
    channel_absent = not isinstance(channels, dict) or PLUGIN_ID not in channels
    plugins = config.get("plugins")
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    entry = entries.get(PLUGIN_ID) if isinstance(entries, dict) else None
    return channel_absent and (
        entry is None or (isinstance(entry, dict) and entry.get("enabled") is not True)
    )


def status(
    *,
    config_path: Path | None = None,
    policy_path: Path | None = None,
    secret_path: Path | None = None,
    contract_path: Path | None = None,
) -> dict[str, Any]:
    config_path = config_path or default_config_path()
    policy_path = policy_path or default_policy_path()
    secret_path = secret_path or default_secret_path()
    contract_path = contract_path or default_contract_path()
    plugin = _plugin_contract(contract_path)
    secret = inspect_secret(secret_path)
    issues: list[str] = []
    warnings: list[str] = []
    try:
        config = load_json_object(config_path, label="Gateway-Konfiguration")
    except ValueError as exc:
        return {
            "ok": False,
            "state": "invalid",
            "configured": False,
            "enabled": False,
            "plugin": plugin,
            "secret": secret,
            "issues": [str(exc)],
            "warnings": [],
        }
    if not policy_path.exists():
        safe = _safe_disabled_without_policy(config)
        if not safe:
            issues.append("Talk-Konfiguration ist ohne release-eigene Policy vorhanden")
        else:
            warnings.append("Talk-Kanal ist noch nicht vorbereitet")
        return {
            "ok": safe,
            "state": "disabled" if safe else "invalid",
            "configured": False,
            "enabled": False,
            "plugin": plugin,
            "secret": secret,
            "ready_for_activation": False,
            "issues": issues,
            "warnings": warnings,
        }
    try:
        policy = validate_policy(load_json_object(policy_path, label="Talk-Policy"))
    except ValueError as exc:
        issues.append(str(exc))
        policy = None
    channel: Any = None
    plugin_entry: Any = None
    plugin_path_present = False
    plugins = config.get("plugins")
    if isinstance(plugins, dict):
        entries = plugins.get("entries")
        plugin_entry = entries.get(PLUGIN_ID) if isinstance(entries, dict) else None
        load = plugins.get("load")
        paths = load.get("paths") if isinstance(load, dict) else None
        plugin_path_present = isinstance(paths, list) and PLUGIN_PATH in paths
    channels = config.get("channels")
    if isinstance(channels, dict):
        channel = channels.get(PLUGIN_ID)
    if not plugin["valid"]:
        issues.append("Gepinnter Nextcloud-Talk-Pluginvertrag fehlt oder ist ungueltig")
    if not plugin_path_present:
        issues.append("Immutable Nextcloud-Talk-Pluginpfad fehlt in der Gateway-Konfiguration")
    if not isinstance(plugin_entry, dict) or set(plugin_entry) - {"enabled"}:
        issues.append("Nextcloud-Talk-Plugineintrag fehlt oder enthaelt unbekannte Felder")
    elif plugin_entry.get("enabled") is not False:
        issues.append("Nextcloud-Talk-Plugin ist ausserhalb der Aktivierungsstufe eingeschaltet")
    if not isinstance(channel, dict):
        issues.append("Vorbereitete Nextcloud-Talk-Kanalkonfiguration fehlt")
    elif set(channel) != CHANNEL_KEYS:
        issues.append("Nextcloud-Talk-Kanalkonfiguration enthaelt fehlende oder unbekannte Felder")
    elif policy is not None:
        expected_channel = prepared_channel_config(
            base_url=policy["base_url"], webhook_public_url=policy["webhook_public_url"]
        )
        if channel != expected_channel or digest_json(channel) != policy["channel_config_sha256"]:
            issues.append("Talk-Policy und Gateway-Kanalkonfiguration stimmen nicht ueberein")
    if not secret["ok"]:
        warnings.append("Botsecret fehlt, ist leer, unlesbar, zu gross, ein Symlink oder zu weit berechtigt")
    configured = policy is not None and isinstance(channel, dict)
    return {
        "ok": not issues,
        "state": "prepared-disabled" if not issues else "invalid",
        "configured": configured,
        "enabled": False,
        "plugin": {
            **plugin,
            "path_configured": plugin_path_present,
            "entry_enabled": plugin_entry.get("enabled") if isinstance(plugin_entry, dict) else None,
        },
        "policy": {
            "schema_version": policy.get("schema_version") if policy else None,
            "state": policy.get("state") if policy else None,
            "expected_nextcloud_ip_count": len(policy.get("expected_nextcloud_ips", [])) if policy else 0,
            "expected_webhook_ip_count": len(policy.get("expected_webhook_ips", [])) if policy else 0,
        },
        "secret": secret,
        "ready_for_activation": bool(not issues and secret["ok"]),
        "issues": issues,
        "warnings": warnings,
    }


def _resolve_ips(hostname: str) -> list[str]:
    addresses: list[str] = []
    for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
        rendered = str(ipaddress.ip_address(item[4][0]))
        if rendered not in addresses:
            addresses.append(rendered)
    return sorted(addresses)


def _tls_probe(hostname: str, port: int, *, timeout: float) -> dict[str, Any]:
    context = ssl.create_default_context()
    with (
        socket.create_connection((hostname, port), timeout=timeout) as raw,
        context.wrap_socket(raw, server_hostname=hostname) as secured,
    ):
        return {
            "ok": True,
            "protocol": secured.version(),
            "peer_certificate_present": bool(secured.getpeercert()),
        }


def _nextcloud_status(base_url: str, *, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/status.php"
    request = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-M17-Doctor/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final = urllib.parse.urlsplit(response.geturl())
        expected = urllib.parse.urlsplit(base_url)
        if (final.scheme.casefold(), final.hostname, final.port) != (
            expected.scheme.casefold(),
            expected.hostname,
            expected.port,
        ):
            raise ValueError("Nextcloud-Status wurde auf einen anderen Origin umgeleitet")
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("Nextcloud-Statusantwort ist unerwartet gross")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Nextcloud-Statusantwort ist kein JSON-Objekt")
    return {
        "ok": bool(payload.get("installed")) and not bool(payload.get("maintenance")),
        "installed": bool(payload.get("installed")),
        "maintenance": bool(payload.get("maintenance")),
        "version": str(payload.get("version") or ""),
    }


def doctor(
    *,
    config_path: Path | None = None,
    policy_path: Path | None = None,
    secret_path: Path | None = None,
    contract_path: Path | None = None,
    live: bool = True,
    timeout: float = 5.0,
) -> dict[str, Any]:
    report = status(
        config_path=config_path,
        policy_path=policy_path,
        secret_path=secret_path,
        contract_path=contract_path,
    )
    live_report: dict[str, Any] = {"requested": live, "ok": None}
    if not live:
        report["live"] = live_report
        return report
    policy_path = policy_path or default_policy_path()
    try:
        policy = validate_policy(load_json_object(policy_path, label="Talk-Policy"))
        base = urllib.parse.urlsplit(policy["base_url"])
        webhook = urllib.parse.urlsplit(policy["webhook_public_url"])
        nextcloud_ips = _resolve_ips(str(base.hostname))
        webhook_ips = _resolve_ips(str(webhook.hostname))
        nextcloud_dns_ok = nextcloud_ips == policy["expected_nextcloud_ips"]
        webhook_dns_ok = webhook_ips == policy["expected_webhook_ips"]
        if not nextcloud_dns_ok or not webhook_dns_ok:
            raise ValueError(
                "DNS-Ziel weicht von der gebundenen Nextcloud- oder Webhook-IP ab; "
                "Live-Verbindungen wurden nicht gestartet"
            )
        nextcloud_tls = _tls_probe(str(base.hostname), base.port or 443, timeout=timeout)
        webhook_tls = _tls_probe(str(webhook.hostname), webhook.port or 443, timeout=timeout)
        nextcloud = _nextcloud_status(policy["base_url"], timeout=timeout)
        live_ok = bool(
            nextcloud_dns_ok
            and webhook_dns_ok
            and nextcloud_tls["ok"]
            and webhook_tls["ok"]
            and nextcloud["ok"]
        )
        live_report = {
            "requested": True,
            "ok": live_ok,
            "nextcloud_dns": {"ok": nextcloud_dns_ok, "address_count": len(nextcloud_ips)},
            "webhook_dns": {"ok": webhook_dns_ok, "address_count": len(webhook_ips)},
            "nextcloud_tls": nextcloud_tls,
            "webhook_tls": webhook_tls,
            "nextcloud": nextcloud,
            "talk_app": {"status": "not-measured", "reason": "Bot/API-Zugang noch nicht aktiviert"},
        }
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        live_report = {"requested": True, "ok": False, "error": str(exc)}
    report["live"] = live_report
    report["ok"] = bool(report.get("ok") and live_report.get("ok"))
    return report


__all__ = [
    "BOT_SECRET_FILE",
    "PLUGIN_ID",
    "PLUGIN_PATH",
    "WEBHOOK_PATH",
    "build_prepared_policy",
    "canonical_json",
    "default_config_path",
    "default_contract_path",
    "default_policy_path",
    "default_secret_path",
    "digest_json",
    "doctor",
    "file_digest",
    "load_json_object",
    "prepared_channel_config",
    "status",
    "validate_policy",
]
