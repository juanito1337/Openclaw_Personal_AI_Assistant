"""Operator-owned Nextcloud connection; runtime readers never mutate this store.

The directory is an explicit role mount, not a fallback credential search path.
Only the setup service writes it. Public projections deliberately use allowlists.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import Resource

MAX_CONNECTION_BYTES = 4 * 1024 * 1024
CONNECTION_FILENAME = "connection.json"


class ConnectionError(RuntimeError):
    """A content-free store error, safe to return to an authenticated operator."""


def connection_directory() -> Path | None:
    configured = os.environ.get("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute():
        raise ConnectionError("nextcloud-connection-directory-must-be-absolute")
    return path


def load_connection(path: Path | None = None) -> dict[str, Any] | None:
    directory = path if path is not None else connection_directory()
    if directory is None:
        return None
    try:
        fd = os.open(directory / CONNECTION_FILENAME, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConnectionError("nextcloud-connection-unreadable") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ConnectionError("nextcloud-connection-file-permissions")
            raw = handle.read(MAX_CONNECTION_BYTES + 1)
        if len(raw) > MAX_CONNECTION_BYTES:
            raise ConnectionError("nextcloud-connection-too-large")
        value = json.loads(raw)
        _validate_connection(value)
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise ConnectionError("nextcloud-connection-invalid") from exc


def _validate_connection(value: Any) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ConnectionError("nextcloud-connection-schema")
    for key in ("revision", "server_url", "login_name", "app_password", "user_id"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ConnectionError("nextcloud-connection-incomplete")
    if not isinstance(value.get("expected_ips"), list) or not value["expected_ips"]:
        raise ConnectionError("nextcloud-connection-address-binding-missing")
    snapshot = value.get("discovery_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("user_id") != value["user_id"]:
        raise ConnectionError("nextcloud-connection-account-mismatch")
    talk = value.get("talk", {})
    if not isinstance(talk, dict):
        raise ConnectionError("nextcloud-connection-talk-invalid")
    if talk.get("enabled") and (not talk.get("room_tokens") or not talk.get("allowed_user_ids")):
        raise ConnectionError("nextcloud-talk-selection-required")


class ConnectionStore:
    """Atomic single-writer store with revision guards and a local prior snapshot."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
            raise ConnectionError("nextcloud-connection-directory-permissions")
        fd = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def read(self) -> dict[str, Any] | None:
        return load_connection(self.directory)

    def save(self, value: dict[str, Any], *, expected_revision: str) -> dict[str, Any]:
        candidate = dict(value)
        candidate["schema_version"] = 1
        candidate["revision"] = uuid.uuid4().hex
        _validate_connection(candidate)
        connection_resources(candidate)
        encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_CONNECTION_BYTES:
            raise ConnectionError("nextcloud-connection-too-large")
        with self._locked():
            current = self.read()
            if str((current or {}).get("revision", "")) != expected_revision:
                raise ConnectionError("nextcloud-connection-conflict")
            if current:
                previous = json.dumps(current, ensure_ascii=False, sort_keys=True).encode("utf-8")
                self._publish("previous.json", previous)
            event = {
                "timestamp": time.time(),
                "operation": "connection-save",
                "revision": candidate["revision"],
                "account_id": account_namespace(candidate),
            }
            self._audit({**event, "phase": "prepared"})
            try:
                self._publish(CONNECTION_FILENAME, encoded)
                result = self.read()
                if result is None or result["revision"] != candidate["revision"]:
                    raise ConnectionError("nextcloud-connection-readback-failed")
                self._audit({**event, "phase": "committed"})
            except Exception:
                if current:
                    self._publish(CONNECTION_FILENAME, json.dumps(current, sort_keys=True).encode("utf-8"))
                else:
                    (self.directory / CONNECTION_FILENAME).unlink(missing_ok=True)
                    directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                raise ConnectionError("nextcloud-connection-publication-failed") from None
            return public_connection(result)

    def _audit(self, event: dict[str, Any]) -> None:
        try:
            fd = os.open(
                self.directory / "audit.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "ab") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise ConnectionError("nextcloud-connection-audit-permissions")
                handle.write(json.dumps(event, sort_keys=True).encode("utf-8") + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            raise ConnectionError("nextcloud-connection-audit-failed") from None

    def _publish(self, filename: str, encoded: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".connection-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.directory / filename)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)


def account_namespace(connection: dict[str, Any]) -> str:
    identity = str(connection["server_url"]).rstrip("/") + "\n" + str(connection["user_id"])
    return "nextcloud-account-" + hashlib.sha256(identity.encode()).hexdigest()[:16]


def collection_resource_id(connection: dict[str, Any], href: str) -> str:
    """Identity is independent of absolute versus origin-relative DAV hrefs."""
    base = urllib.parse.urlsplit(str(connection["server_url"]))
    target = urllib.parse.urlsplit(urllib.parse.urljoin(connection["server_url"].rstrip("/") + "/", href))
    # HTTPS DAV servers may include :443 in absolute hrefs while the verified
    # account base omits it. Compare effective ports without accepting port 0
    # or a different scheme/host/non-default port.
    base_port = 443 if base.port is None and base.scheme == "https" else base.port
    target_port = 443 if target.port is None and target.scheme == "https" else target.port
    if (target.scheme, target.hostname, target_port) != (base.scheme, base.hostname, base_port):
        raise ConnectionError("nextcloud-resource-origin-mismatch")
    path = target.path.rstrip("/") + "/"
    if base.path.rstrip("/") and not path.startswith(base.path.rstrip("/") + "/"):
        raise ConnectionError("nextcloud-resource-base-mismatch")
    return account_namespace(connection) + "-" + hashlib.sha256(path.encode()).hexdigest()[:16]


def public_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    if connection is None:
        return {"connected": False, "revision": ""}
    snapshot = connection["discovery_snapshot"]
    return {
        "connected": True,
        "revision": connection["revision"],
        "server_url": connection["server_url"],
        "user_id": connection["user_id"],
        "account_id": account_namespace(connection),
        "complete": snapshot.get("complete") is True,
        "resources": [resource_view(item) for item in connection_resources(connection)],
        "talk": {
            "enabled": connection.get("talk", {}).get("enabled") is True,
            "room_tokens": list(connection.get("talk", {}).get("room_tokens", [])),
            "allowed_user_ids": list(connection.get("talk", {}).get("allowed_user_ids", [])),
        },
    }


def resource_view(resource: Resource) -> dict[str, Any]:
    return {
        "resource_id": resource.id,
        "name": resource.metadata.get("name", resource.id),
        "kind": resource.kind,
        "components": resource.metadata.get("components", []),
        "permissions": list(resource.permissions),
        "path": resource.metadata.get("path", ""),
    }


def connection_resources(connection: dict[str, Any]) -> list[Resource]:
    """Project measured server privileges; no permission follows from a URL."""
    snapshot = connection["discovery_snapshot"]
    namespace = account_namespace(connection)
    collections: dict[str, Resource] = {}
    for key, kind, component in (
        ("calendars", "calendar", "VEVENT"),
        ("task_lists", "calendar", "VTODO"),
        ("addressbooks", "addressbook", "VCARD"),
    ):
        for item in snapshot.get(key, []):
            href = str(item.get("href", ""))
            if not href or not item.get("can_read"):
                continue
            identifier = collection_resource_id(connection, href)
            permissions = tuple(
                name for name in ("read", "create", "update") if item.get("can_" + name) is True
            )
            components = list(item.get("components", []))
            if component not in components:
                components.append(component)
            if identifier in collections:
                prior = collections[identifier]
                if prior.kind != kind:
                    raise ConnectionError("nextcloud-resource-kind-ambiguous")
                components = sorted(set(components) | set(prior.metadata["components"]))
                # Conflicting duplicate evidence can restrict rights, never
                # expand them just because a component was encountered later.
                permissions = tuple(name for name in permissions if name in prior.permissions)
            collections[identifier] = Resource(
                id=identifier,
                kind=kind,
                connector="nextcloud",
                enabled=True,
                remote_id=href,
                permissions=permissions,
                metadata={
                    "name": str(item.get("name", "")),
                    "components": components,
                    "account_id": namespace,
                    "managed_connection": True,
                },
            )
    # The explicit account scope includes all visible folders; an empty root is
    # meaningful only on this generated, operator-authorized resource.
    roots = snapshot.get("file_roots", [])
    if len([item for item in roots if item.get("can_read")]) > 1:
        raise ConnectionError("nextcloud-file-root-ambiguous")
    for item in roots:
        if not item.get("can_read"):
            continue
        identifier = namespace + "-files"
        collections[identifier] = Resource(
            id=identifier,
            kind="file-root",
            connector="nextcloud",
            enabled=True,
            remote_id=str(item.get("href", "")),
            permissions=tuple(
                name for name in ("read", "create", "update") if item.get("can_" + name) is True
            ),
            metadata={
                "name": "Nextcloud-Dateien",
                "components": ["DAV-COLLECTION"],
                "allowed_roots": [],
                "account_root": True,
                "account_id": namespace,
                "managed_connection": True,
            },
        )
        break
    return list(collections.values())


def apply_registry_connection(registry: Any, connection: dict[str, Any]) -> None:
    """In-memory overlay; old account plans cannot resolve against new credentials."""
    registry.managed_connection = True
    registry.connection_revision = connection["revision"]
    registry.resources = {
        key: value
        for key, value in registry.resources.items()
        if value.connector != "nextcloud" and not value.connector.startswith("nextcloud-")
    }
    registry.resources.update({item.id: item for item in connection_resources(connection)})


def apply_tool_connection(settings: Any, connection: dict[str, Any]) -> Any:
    resources = connection_resources(connection)
    replacements: dict[str, Any] = {}
    for domain, component in (("calendar", "VEVENT"), ("tasks", "VTODO"), ("contacts", "VCARD")):
        available = [item for item in resources if component in item.metadata.get("components", [])]
        prior = getattr(settings.nextcloud, domain)
        # Multi-resource accounts have no implicit write target. A caller must
        # select an exact ID, and status/list expose every available resource.
        default = available[0].id if len(available) == 1 else ""
        replacements[domain] = replace(
            prior,
            enabled=bool(available),
            resource_id=default,
            resource_ids=tuple(item.id for item in available),
            allow_list=bool(available),
            allow_create=any("create" in item.permissions for item in available),
            allow_update=any("update" in item.permissions for item in available),
        )
    files = [item for item in resources if item.kind == "file-root"]
    replacements["workspace"] = replace(
        settings.nextcloud.workspace,
        enabled=bool(files),
        resource_id=files[0].id if files else "",
        root="",
        allow_mkdir=bool(files and "create" in files[0].permissions),
        allow_upload=bool(files and "create" in files[0].permissions),
        allow_write_text=bool(files and "create" in files[0].permissions),
        allow_move=False,
    )
    # Existing automated business workflows require a separately selected target
    # after account onboarding; don't send mail-derived data into a new account.
    replacements["deck_orders"] = replace(settings.nextcloud.deck_orders, enabled=False)
    return replace(
        settings,
        nextcloud=replace(settings.nextcloud, **replacements),
        mail=replace(
            settings.mail,
            invoices=replace(settings.mail.invoices, enabled=False),
            calendar_mail=replace(settings.mail.calendar_mail, enabled=False),
        ),
    )
