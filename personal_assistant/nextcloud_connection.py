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
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import nextcloud_access as access
from .models import Resource
from .nextcloud_access import ConnectionError as ConnectionError
from .nextcloud_access import account_namespace as account_namespace
from .nextcloud_access import collection_resource_id as collection_resource_id

MAX_CONNECTION_BYTES = 4 * 1024 * 1024
CONNECTION_FILENAME = "connection.json"


def connection_directory() -> Path | None:
    configured = os.environ.get("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute():
        raise ConnectionError("nextcloud-connection-directory-must-be-absolute")
    return path


def required_mode() -> bool:
    """PID 1 and docker-exec processes both use the installed role contract."""
    return (
        os.environ.get("OPENCLAW_RUNTIME") == "container"
        or os.environ.get("OPENCLAW_ROLE") == "setup-service"
        or os.environ.get("OPENCLAW_NC_MODE_REQUIRED") == "1"
    )


def _unmounted_role() -> bool:
    return os.environ.get("OPENCLAW_ROLE") in {
        "portfolio-worker",
        "supervisor-worker",
        "ollama-proxy",
        "layout-init",
    }


def _strict_object(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate-key")
    return obj


def _read_json(path, *, limit=MAX_CONNECTION_BYTES):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConnectionError("nextcloud-connection-unreadable") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ConnectionError("nextcloud-connection-file-permissions")
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise ConnectionError("nextcloud-connection-too-large")
        value = json.loads(raw, object_pairs_hook=_strict_object)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ConnectionError("nextcloud-connection-invalid") from exc


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _validate_connection(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] not in {1, 2}
    ):
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
    if value["schema_version"] == 2:
        if not access.inventory_matches_account(value, snapshot):
            raise ConnectionError("policy-integrity-error")
        for key in ("calendars", "task_lists", "addressbooks", "folders", "file_roots"):
            rows = snapshot.get(key, [])
            if not isinstance(rows, list) or len(rows) > 10000 or not all(isinstance(r, dict) for r in rows):
                raise ConnectionError("policy-integrity-error")
        access.parse_policy(value, value.get("access_policy"))
    elif "access_policy" in value:
        raise ConnectionError("policy-integrity-error")


def load_access_snapshot(path: Path | None = None) -> access.AccessSnapshot:
    directory = path if path is not None else connection_directory()
    if directory is None:
        if required_mode():
            if _unmounted_role():
                return access.AccessSnapshot("unconfigured")
            raise ConnectionError("policy-integrity-error")
        return access.AccessSnapshot("legacy", managed=False)
    return ConnectionStore(directory).snapshot()


def load_connection(path: Path | None = None) -> dict[str, Any] | None:
    return load_access_snapshot(path).connection


def _mode(installation_id, mode, record):
    policy = (record or {}).get("access_policy", {})
    return {
        "schema_version": 1,
        "installation_id": installation_id,
        "mode": mode,
        "generation": policy.get("generation", ""),
        "policy_revision": policy.get("policy_revision", ""),
        "record_digest": hashlib.sha256(_encoded(record)).hexdigest() if record else "",
    }


class ConnectionStore:
    """The owner and readers share one installation-bound stable lock inode."""

    def __init__(self, directory: Path, *, require_mode: bool = False) -> None:
        self.directory = directory
        self.require_mode = require_mode or required_mode()

    @property
    def _managed(self):
        return self.require_mode or os.path.lexists(self.directory / "mode.json")

    @contextmanager
    def _locked(self, *, shared=False) -> Iterator[str]:
        managed = self._managed
        if not managed and not shared:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
                raise ConnectionError("nextcloud-connection-directory-permissions")
            flags = os.O_RDONLY if shared or managed else os.O_CREAT | os.O_RDWR
            fd = os.open(self.directory / ".lock", flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        except (OSError, ConnectionError):
            raise ConnectionError(
                "policy-integrity-error" if managed else "nextcloud-connection-unreadable"
            ) from None
        with os.fdopen(fd, "rb") as lock:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ConnectionError("policy-integrity-error")
            fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                token = lock.read(33).decode("ascii")
                if (token or managed) and (
                    uuid.UUID(token).hex != token
                    or not os.path.samestat(info, (self.directory / ".lock").lstat())
                ):
                    raise ValueError()
            except (OSError, ValueError):
                raise ConnectionError("policy-integrity-error") from None
            yield token

    def _snapshot(self, token, *, allow_prepared=False):
        try:
            record = _read_json(self.directory / CONNECTION_FILENAME)
            if record is not None:
                _validate_connection(record)
            if not token:
                if self._managed or (record and record["schema_version"] == 2):
                    raise ConnectionError("policy-integrity-error")
                return access.AccessSnapshot("legacy", record, managed=False)
            mode = _read_json(self.directory / "mode.json", limit=2048)
            status = (mode or {}).get("mode")
            effective_mode = "active" if allow_prepared and status == "activating" else status
            if (
                effective_mode not in {"unconfigured", "legacy", "active"}
                or _encoded(mode) != _encoded(_mode(token, status, record))
                or (effective_mode == "unconfigured" and record is not None)
                or (effective_mode == "active" and (record or {}).get("schema_version") != 2)
                or (effective_mode == "legacy" and record is not None and record["schema_version"] != 1)
            ):
                raise ConnectionError("policy-integrity-error")
            policy = (
                access.parse_policy(record, record["access_policy"]) if effective_mode == "active" else None
            )
            return access.AccessSnapshot(effective_mode, record, policy)
        except (TypeError, AttributeError, ValueError, KeyError, OSError, ConnectionError) as exc:
            if token or self._managed:
                raise ConnectionError("policy-integrity-error") from None
            if isinstance(exc, ConnectionError):
                raise
            raise ConnectionError("nextcloud-connection-invalid") from None

    def snapshot(self) -> access.AccessSnapshot:
        # Unmanaged v1 readers predate the lock. Never bootstrap a read-only mount.
        if not self._managed and not os.path.lexists(self.directory / ".lock"):
            return self._snapshot("")
        with self._locked(shared=True) as token:
            return self._snapshot(token)

    def read(self) -> dict[str, Any] | None:
        return self.snapshot().connection

    @contextmanager
    def admit(self, expected_binding):
        """Hold through one remote request or the release of one local answer."""
        with self._locked(shared=True) as token:
            snapshot = self._snapshot(token)
            if snapshot.binding is None or expected_binding not in (
                snapshot.binding,
                snapshot.binding.to_dict(),
            ):
                raise ConnectionError("policy-changed")
            yield snapshot

    def recover(self):
        """Owner-only finalization of an exact prepared and published v2 record."""
        with self._locked() as token:
            snapshot = self._snapshot(token, allow_prepared=True)
            mode = _read_json(self.directory / "mode.json", limit=2048)
            if snapshot.mode != "active" or mode["mode"] != "activating":
                raise ConnectionError("policy-integrity-error")
            self._audit(
                {
                    "operation": "policy-recovery",
                    "phase": "committed",
                    "timestamp": time.time(),
                    "revision": snapshot.connection["revision"],
                }
            )
            self._publish("mode.json", _encoded({**mode, "mode": "active"}))
            return self._snapshot(token)

    def save(
        self,
        value: dict[str, Any],
        *,
        expected_revision: str,
        expected_policy_revision: str | None = None,
        confirm_account_root: bool = False,
    ) -> dict[str, Any]:
        with self._locked() as token:
            snapshot = self._snapshot(token)
            current = snapshot.connection
            if (current or {}).get("revision", "") != expected_revision:
                raise ConnectionError("nextcloud-connection-conflict")
            revision = snapshot.binding.policy_revision if snapshot.binding else ""
            if expected_policy_revision is not None and expected_policy_revision != revision:
                raise ConnectionError("nextcloud-policy-conflict")
            candidate = {**value, "schema_version": 1, "revision": uuid.uuid4().hex}
            supplied = candidate.pop("access_policy", None)
            switch = current is not None and account_namespace(current) != account_namespace(candidate)
            if token and (snapshot.mode != "legacy" or switch or supplied is not None or current is None):
                candidate["schema_version"] = 2
                policy = self._candidate_policy(
                    snapshot, candidate, supplied, expected_policy_revision, switch, confirm_account_root
                )
                candidate["access_policy"] = policy.to_dict()
            elif supplied is not None or value.get("schema_version") == 2:
                raise ConnectionError("policy-integrity-error")
            _validate_connection(candidate)
            connection_resources(candidate)
            encoded = _encoded(candidate)
            if len(encoded) > MAX_CONNECTION_BYTES:
                raise ConnectionError("nextcloud-connection-too-large")
            if current:
                self._publish("previous.json", _encoded(current))
            event = {
                "timestamp": time.time(),
                "operation": "connection-save",
                "revision": candidate["revision"],
                "account_id": account_namespace(candidate),
            }
            self._audit({**event, "phase": "prepared"})
            active = candidate["schema_version"] == 2
            try:
                if token and active:
                    self._publish("mode.json", _encoded(_mode(token, "activating", candidate)))
                self._publish(CONNECTION_FILENAME, encoded)
                if _read_json(self.directory / CONNECTION_FILENAME) != candidate:
                    raise ConnectionError("nextcloud-connection-readback-failed")
                self._audit({**event, "phase": "committed"})
                if token:
                    self._publish(
                        "mode.json", _encoded(_mode(token, "active" if active else "legacy", candidate))
                    )
                result = self._snapshot(token).connection
            except Exception:
                if not token and current:
                    self._publish(CONNECTION_FILENAME, _encoded(current))
                elif not token:
                    (self.directory / CONNECTION_FILENAME).unlink(missing_ok=True)
                    _fsync_dir(self.directory)
                raise ConnectionError("nextcloud-connection-publication-failed") from None
            return public_connection(result)

    @staticmethod
    def _candidate_policy(snapshot, candidate, supplied, expected_policy_revision, switch, confirm_root):
        if switch:
            candidate["talk"] = {"enabled": False, "room_tokens": [], "allowed_user_ids": []}
        policy = snapshot.policy if snapshot.policy and not switch else access.new_policy(candidate)
        if supplied is None or supplied == policy.to_dict() or switch:
            return policy
        if expected_policy_revision is None:
            raise ConnectionError("nextcloud-policy-conflict")
        try:
            requested = access.parse_policy(candidate, supplied)
            if snapshot.binding and requested.binding != snapshot.binding:
                raise ConnectionError("nextcloud-policy-conflict")
            if snapshot.connection is None and requested.grants:
                raise ConnectionError("first-login-requires-empty-policy")
            existing = {g.scope: g.operations for g in policy.grants}
            for grant in requested.grants:
                additions = set(grant.operations).difference(existing.get(grant.scope, ()))
                if not additions:
                    continue
                if grant.scope.path == "" and confirm_root is not True:
                    raise ConnectionError("account-root-confirmation-required")
                for operation in additions:
                    code = access.server_blocker(candidate, grant.scope, operation)
                    if code:
                        raise ConnectionError(code)
            return access.Policy(replace(policy.binding, policy_revision=uuid.uuid4().hex), requested.grants)
        except (ValueError, TypeError, KeyError):
            raise ConnectionError("policy-integrity-error") from None

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


def _fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _receipt(path):
    receipt = _read_json(path, limit=1024)
    if receipt is not None and (
        set(receipt) != {"schema_version", "status", "installation_id"}
        or type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or receipt["status"] not in {"ready", "preparing"}
        or uuid.UUID(receipt["installation_id"]).hex != receipt["installation_id"]
    ):
        raise ValueError()
    return receipt


def initialize_access_store(state: Path, directory: Path, *, transition: str) -> None:
    """Offline installer only. Caller proves backup, writer stop and release transition.

    The first receipt is create-only. No partial preparation is guessed or rolled
    back. A ready receipt authorizes verification alone, including on redeploy.
    """
    if transition not in {
        "fresh",
        "verified-pre-policy",
        "verify",
        "verify-backup",
        "verify-installed-backup",
    }:
        raise ConnectionError("policy-integrity-error")
    try:
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _initialize_locked(state, directory, transition)
        finally:
            os.close(fd)
    except (ValueError, OSError, TypeError, KeyError, AttributeError, ConnectionError):
        raise ConnectionError("policy-integrity-error") from None


def _initialize_locked(state, directory, transition):
    migrations = state / ".layout-migrations"
    if migrations.is_symlink() or directory.is_symlink():
        raise ValueError()
    path = migrations / "nextcloud-access-v1.json"
    receipt = _receipt(path)
    store = ConnectionStore(directory, require_mode=True)
    if receipt is not None:
        with store._locked(shared=True) as token:
            if token != receipt["installation_id"]:
                raise ValueError()
            snap = store._snapshot(token)
        if receipt["status"] == "ready":
            return
        if snap.mode not in {"unconfigured", "legacy"}:
            raise ValueError()
        if transition in {"verify-backup", "verify-installed-backup"}:
            return
        if transition in {"fresh", "verified-pre-policy"} and snap.mode != (
            "unconfigured" if transition == "fresh" else "legacy"
        ):
            raise ValueError()
    else:
        if os.path.lexists(directory / "mode.json") or transition in {"verify", "verify-installed-backup"}:
            raise ValueError()
        record = _read_json(directory / CONNECTION_FILENAME)
        if record is not None:
            _validate_connection(record)
            if record["schema_version"] != 1 or transition == "fresh":
                raise ValueError()
        lockfile = directory / ".lock"
        # An installation ID left in the lock proves this is not a pre-policy
        # store, even if both the receipt and mode have been lost.
        if os.path.lexists(lockfile):
            check = os.open(lockfile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(check)
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size:
                    raise ValueError()
            finally:
                os.close(check)
        if transition == "verify-backup":
            return
        if directory.stat().st_mode & 0o077:
            raise ValueError()
        migrations.mkdir(mode=0o700, exist_ok=True)
        _fsync_dir(state)
        token = uuid.uuid4().hex
        receipt = {"schema_version": 1, "installation_id": token, "status": "preparing"}
        fd = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(_encoded(receipt))
            handle.flush()
            os.fsync(fd)
        _fsync_dir(migrations)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if not os.path.lexists(lockfile):
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(lockfile, flags, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if lock.read(1) or not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError()
            lock.write(token.encode("ascii"))
            lock.flush()
            os.fsync(fd)
            store._publish(
                "mode.json",
                _encoded(_mode(token, "unconfigured" if transition == "fresh" else "legacy", record)),
            )
            store._snapshot(token)
    # os.replace of the exclusive receipt is durable only after the directory sync.
    ConnectionStore(migrations)._publish(path.name, _encoded({**receipt, "status": "ready"}))
    if _receipt(path) != {**receipt, "status": "ready"}:
        raise ValueError()


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


def component_operations(resource: Resource, component: str) -> tuple[str, ...]:
    by_component = resource.metadata.get("component_operations")
    return resource.permissions if by_component is None else tuple(by_component.get(component, ()))


def _projection(value):
    if isinstance(value, access.AccessSnapshot):
        return value
    current = load_access_snapshot()
    if current.managed and current.mode == "legacy":
        if current.connection != value:
            raise ConnectionError("policy-integrity-error")
        return current
    if current.mode != "legacy" or (value and value.get("schema_version") == 2):
        if current.connection != value or (value and current.mode != "active"):
            raise ConnectionError("policy-integrity-error")
        return current
    return access.AccessSnapshot("legacy", value, managed=False)


def conn_revision(snapshot):
    return (snapshot.connection or {}).get("revision", "")


@contextmanager
def admit_projection(initial):
    """Hold a reader lease only while dispatching or releasing one assembled result."""
    from .nextcloud_native_envelope import EXPECTED_ACCESS, NATIVE_CALL, verify_native_access

    if initial is None or not initial.managed:
        if NATIVE_CALL in os.environ or EXPECTED_ACCESS in os.environ:
            verify_native_access(initial or load_access_snapshot())
        yield initial
        return
    directory = connection_directory()
    if directory is None:
        current = load_access_snapshot()
        if current.mode != "unconfigured" or initial.mode != current.mode:
            raise PermissionError("policy-changed")
        verify_native_access(current)
        yield current
        return
    store = ConnectionStore(directory)
    with store._locked(shared=True) as token:
        current = store._snapshot(token)
        if (
            not current.managed
            or initial.mode != current.mode
            or initial.binding != current.binding
            or conn_revision(initial) != conn_revision(current)
        ):
            raise PermissionError("policy-changed")
        verify_native_access(current)
        yield current


def check_projection_revision(expected):
    if expected:
        snapshot = load_access_snapshot()
        if conn_revision(snapshot) != expected:
            raise PermissionError("policy-changed")
        return snapshot
    return None


def effective_resources(snapshot: access.AccessSnapshot) -> list[Resource]:
    """Agent-visible projection of selected components and folder entries."""
    if not snapshot.connection or access.account_failure_observation(snapshot):
        return []
    if snapshot.mode == "legacy":
        return connection_resources(snapshot.connection)
    if snapshot.mode != "active" or not snapshot.policy:
        return []
    conn = snapshot.connection
    resources: dict[str, Resource] = {}
    folders = {}
    recheck_folders = {}
    for grant in snapshot.policy.grants:
        scope = grant.scope
        decisions = [access.decide_access(snapshot, scope, op) for op in grant.operations]
        operations = [decision.operation for decision in decisions if decision.allowed]
        if scope.component == "FILES" and any(
            d.operation == "list" and d.code == "rights-stale" for d in decisions
        ):
            recheck_folders[scope.path] = ["list"]
        if not operations:
            continue
        if scope.component == "FILES":
            folders[scope.path] = operations
            continue
        rows = conn["discovery_snapshot"].get(access.COLLECTIONS[scope.component], ())
        row = next(item for item in rows if collection_resource_id(conn, item["href"]) == scope.resource_id)
        item = resources.setdefault(
            scope.resource_id,
            Resource(
                id=scope.resource_id,
                kind="addressbook" if scope.component == "VCARD" else "calendar",
                connector="nextcloud",
                enabled=True,
                remote_id=row["href"],
                metadata={
                    "name": row.get("name", ""),
                    "account_id": snapshot.binding.account_id,
                    "managed_connection": True,
                    "component_operations": {},
                },
            ),
        )
        by_component = item.metadata["component_operations"]
        by_component[scope.component] = operations
        item.metadata["components"] = list(by_component)
        resources[scope.resource_id] = replace(
            item,
            permissions=tuple(
                op for op in ("read", "create", "update") if all(op in ops for ops in by_component.values())
            ),
        )
    if folders or recheck_folders:
        identifier = snapshot.binding.account_id + "-files"
        folder_operations = {op for values in folders.values() for op in values}
        permissions = []
        if folder_operations & access.READ:
            permissions.append("read")
        if folder_operations & {"create_file", "mkdir"}:
            permissions.append("create")
        resources[identifier] = Resource(
            id=identifier,
            kind="file-root",
            connector="nextcloud",
            enabled=True,
            remote_id=identifier,
            permissions=tuple(permissions),
            metadata={
                "name": "Nextcloud-Dateien",
                "components": ["FILES"],
                "managed_connection": True,
                "account_id": snapshot.binding.account_id,
                "allowed_roots": sorted(folders),
                "account_root": "" in folders,
                "folder_operations": folders,
                "folder_recheck_operations": recheck_folders,
            },
        )
    return list(resources.values())


def apply_registry_connection(registry: Any, connection: dict[str, Any] | access.AccessSnapshot) -> None:
    """In-memory overlay; old account plans cannot resolve against new credentials."""
    registry.managed_connection = True
    snapshot = _projection(connection)
    registry.access_snapshot = snapshot
    registry.connection_revision = str((snapshot.connection or {}).get("revision", ""))
    registry.resources = {
        key: value
        for key, value in registry.resources.items()
        if value.connector != "nextcloud" and not value.connector.startswith("nextcloud-")
    }
    registry.resources.update({item.id: item for item in effective_resources(snapshot)})


def apply_tool_connection(settings: Any, connection: dict[str, Any] | access.AccessSnapshot) -> Any:
    snapshot = _projection(connection)
    resources = effective_resources(snapshot)
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
            allow_list=any(
                ("list" if snapshot.mode == "active" else "read") in component_operations(item, component)
                for item in available
            ),
            allow_create=any("create" in component_operations(item, component) for item in available),
            allow_update=any("update" in component_operations(item, component) for item in available),
        )
    files = [item for item in resources if item.kind == "file-root"]
    allowed_file_ops = {
        op for item in files for ops in item.metadata.get("folder_operations", {}).values() for op in ops
    }
    file_create = (
        "create_file" in allowed_file_ops
        if snapshot.mode == "active"
        else bool(files and "create" in files[0].permissions)
    )
    mkdir = "mkdir" in allowed_file_ops if snapshot.mode == "active" else file_create
    replacements["workspace"] = replace(
        settings.nextcloud.workspace,
        enabled=bool(files),
        resource_id=files[0].id if files else "",
        root="",
        allow_mkdir=mkdir,
        allow_upload=file_create,
        allow_write_text=file_create,
        allow_move=False,
    )
    # Existing automated business workflows require a separately selected target
    # after account onboarding; don't send mail-derived data into a new account.
    replacements["deck_orders"] = replace(settings.nextcloud.deck_orders, enabled=False)
    return replace(
        settings,
        nextcloud=replace(settings.nextcloud, **replacements),
        nextcloud_access_revision=conn_revision(snapshot),
        nextcloud_access_mode=snapshot.mode,
        nextcloud_access_managed=snapshot.managed,
        nextcloud_files_list_effective="list" in allowed_file_ops,
        nextcloud_files_list_recheck=any(item.metadata.get("folder_recheck_operations") for item in files),
        mail=replace(
            settings.mail,
            invoices=replace(
                settings.mail.invoices,
                enabled=False,
                disabled_reason="managed-account-archive-selection-required",
            ),
            calendar_mail=replace(settings.mail.calendar_mail, enabled=False),
        ),
    )
