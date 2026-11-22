"""Account-bound selections. All evidence comes from the saved, measured inventory."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

# A completed scan bounds the cached rights for one day. Reads never renew it.
MAX_RIGHTS_AGE_SECONDS = 86400
MAX_GRANTS = 1024
READ = frozenset(("list", "read", "search"))
OPERATIONS = {component: READ | {"create", "update"} for component in ("VEVENT", "VTODO", "VCARD")}
OPERATIONS["FILES"] = READ | {"create_file", "mkdir"}
COLLECTIONS = {"VEVENT": "calendars", "VTODO": "task_lists", "VCARD": "addressbooks"}


class ConnectionError(RuntimeError):
    """A content-free store error, safe to return to an authenticated operator."""


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


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("scope-invalid")


def _text(value: object, limit: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value)
    ):
        raise ValueError("scope-invalid")
    return value


def path_segments(path: str | None) -> tuple[str, ...]:
    """Canonical, decoded, account-relative name vector; never a URL."""
    if path == "":
        return ()
    path = _text(path)
    segments = path.split("/")
    if (
        any(segment in {"", ".", ".."} for segment in segments)
        or any(c in path for c in "\\?#")
        or re.search(r"%[0-9a-fA-F]{2}", path)
        or unicodedata.normalize("NFC", path) != path
    ):
        raise ValueError("scope-invalid")
    return tuple(segments)


def _url_path(connection, href):
    _text(href, 8192)
    if href != href.strip() or any(c in href for c in "?#\\") or href.startswith("//"):
        raise ValueError("scope-invalid")
    base = urllib.parse.urlsplit(connection["server_url"])
    parsed = urllib.parse.urlsplit(href)
    if parsed.scheme or parsed.netloc:

        def origin(url):
            return (url.scheme, url.hostname, 443 if url.port is None and url.scheme == "https" else url.port)

        if origin(parsed) != origin(base) or parsed.username is not None or parsed.password is not None:
            raise ValueError("scope-invalid")
    path = parsed.path
    if not path.startswith("/") or "//" in path or re.search(r"%(?![0-9a-fA-F]{2})|%(?:2f|5c)", path, re.I):
        raise ValueError("scope-invalid")
    try:
        decoded = urllib.parse.unquote(path, errors="strict")
    except UnicodeError:
        raise ValueError("scope-invalid") from None
    return path_segments(decoded.strip("/"))


def folder_path(connection, href: str) -> str:
    base = urllib.parse.urlsplit(connection["server_url"])
    prefix = _url_path(connection, base.path or "/") + (
        "remote.php",
        "dav",
        "files",
        _text(connection["user_id"], 512),
    )
    segments = _url_path(connection, href)
    if segments[: len(prefix)] != prefix:
        raise ValueError("scope-invalid")
    return "/".join(segments[len(prefix) :])


@dataclass(frozen=True, slots=True)
class Scope:
    resource_id: str
    component: str
    path: str | None = None
    file_id: str | None = None

    def to_dict(self):
        return asdict(self)

    @property
    def key(self):
        return self.resource_id, self.component, self.path or ""


@dataclass(frozen=True, slots=True)
class Binding:
    account_id: str
    owner_user_id: str
    generation: str
    policy_revision: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Grant:
    scope: Scope
    operations: tuple[str, ...]

    def to_dict(self):
        return {"scope": self.scope.to_dict(), "operations": list(self.operations)}


@dataclass(frozen=True, slots=True)
class Policy:
    binding: Binding
    grants: tuple[Grant, ...]

    def to_dict(self):
        return {"schema_version": 1, **self.binding.to_dict(), "grants": [g.to_dict() for g in self.grants]}


@dataclass(frozen=True, slots=True)
class AccessSnapshot:
    mode: str
    connection: dict | None = field(default=None, repr=False)
    policy: Policy | None = None
    managed: bool = True

    @property
    def binding(self):
        return self.policy.binding if self.policy else None


def account_failure_observation(snapshot):
    """Latest failed account verification bound to this saved evidence, across metadata edits."""
    conn = snapshot.connection
    if not snapshot.managed or not conn or "setup_account_failure" not in conn:
        return None
    observation = conn["setup_account_failure"]
    if (
        not isinstance(observation, dict)
        or observation.get("account_id") != account_namespace(conn)
        or observation.get("owner_user_id") != conn["user_id"]
        or (
            observation.get("generation") is not None
            and (not snapshot.binding or observation["generation"] != snapshot.binding.generation)
        )
        or observation.get("evidence_at") != conn["discovery_snapshot"].get("checked_at")
        or not all(isinstance(observation.get(k), str) for k in ("code", "checked_at", "source_revision"))
    ):
        raise ConnectionError("policy-integrity-error")
    return observation


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    code: str
    binding: Binding | None
    scope: Scope | None
    operation: str


def _scope(connection, raw) -> Scope:
    if isinstance(raw, Scope):
        raw = raw.to_dict()
    _exact(raw, Scope.__slots__)
    scope = Scope(**raw)
    namespace = account_namespace(connection)
    if not isinstance(scope.component, str) or scope.component not in OPERATIONS:
        raise ValueError("scope-invalid")
    if scope.component == "FILES":
        if scope.resource_id != namespace + "-files":
            raise ValueError("scope-unbound")
        path_segments(scope.path)
        if not re.fullmatch(r"[1-9][0-9]{0,31}", _text(scope.file_id, 32)):
            raise ValueError("scope-invalid")
    else:
        if scope.path is not None or scope.file_id is not None:
            raise ValueError("scope-invalid")
        if not re.fullmatch(re.escape(namespace) + "-[a-f0-9]{16}", _text(scope.resource_id)):
            raise ValueError("scope-unbound")
    return scope


def inventory_matches_account(connection, inventory):
    """NC0's account fingerprint binds even origin-relative resource hrefs."""
    url = connection["server_url"]
    uid = connection["user_id"]
    return (
        isinstance(inventory, dict)
        and inventory.get("server_url") == url
        and inventory.get("user_id") == uid
        and inventory.get("account_id") == hashlib.sha256((url + "\0" + uid).encode()).hexdigest()
    )


def _evidence(connection, scope, inventory) -> dict:
    if not inventory_matches_account(connection, inventory):
        raise ValueError("scope-unbound")
    matches = []
    if scope.component == "FILES":
        for key in ("file_roots", "folders"):
            for row in inventory.get(key, []):
                if row.get("path") == scope.path:
                    if folder_path(connection, row["href"]) != scope.path:
                        raise ValueError("scope-invalid")
                    matches.append(row)
        if len(matches) == 1 and matches[0].get("file_id") != scope.file_id:
            raise ValueError("folder-identity-changed")
    else:
        for row in inventory.get(COLLECTIONS[scope.component], []):
            href = row.get("href", "")
            # Identity hashes are the existing transport identity. Reject URL
            # aliases before urljoin can collapse navigation in that identity.
            _url_path(connection, href)
            if collection_resource_id(connection, href) == scope.resource_id:
                if scope.component != "VCARD" and scope.component not in row.get("components", ()):
                    raise ValueError("scope-invalid")
                matches.append(row)
    if not matches:
        raise ValueError("rights-unverified")
    if len(matches) != 1:
        raise ValueError("scope-invalid")
    return matches[0]


def normalize_scope(connection, raw_scope, inventory) -> Scope:
    scope = _scope(connection, raw_scope)
    _evidence(connection, scope, inventory)
    return scope


def _grant(connection, raw):
    _exact(raw, ("scope", "operations"))
    scope = _scope(connection, raw["scope"])
    ops = raw["operations"]
    if (
        not isinstance(ops, list)
        or any(not isinstance(o, str) or o not in OPERATIONS[scope.component] for o in ops)
        or ops != sorted(set(ops))
    ):
        raise ValueError("operation-not-allowed")
    return Grant(scope, tuple(ops))


def parse_policy(connection, raw) -> Policy:
    _exact(raw, ("schema_version", *Binding.__slots__, "grants"))
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("policy-integrity-error")
    binding = Binding(**{k: _text(raw[k]) for k in Binding.__slots__})
    if binding.account_id != account_namespace(connection) or binding.owner_user_id != connection["user_id"]:
        raise ValueError("scope-unbound")
    for token in (binding.generation, binding.policy_revision):
        if token not in (uuid.UUID(token).hex, str(uuid.UUID(token))):
            raise ValueError("policy-integrity-error")
    if not isinstance(raw["grants"], list) or len(raw["grants"]) > MAX_GRANTS:
        raise ValueError("scope-invalid")
    grants = tuple(_grant(connection, g) for g in raw["grants"])
    keys = [g.scope.key for g in grants]
    if keys != sorted(set(keys)):
        raise ValueError("scope-invalid")
    return Policy(binding, grants)


def new_policy(connection) -> Policy:
    return Policy(
        Binding(account_namespace(connection), connection["user_id"], uuid.uuid4().hex, uuid.uuid4().hex), ()
    )


def _right(scope, operation, row, inventory):
    raw = row.get("checked_at")
    if "checked_at" not in row and (scope.component != "FILES" or scope.path == ""):
        raw = inventory.get("checked_at")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        age = (datetime.now(UTC) - stamp).total_seconds()
    except (TypeError, ValueError, AttributeError, OverflowError):
        return "rights-unverified"
    if age < 0 or age >= MAX_RIGHTS_AGE_SECONDS:
        return "rights-stale"
    check = row.get("read_check")
    if not isinstance(check, dict) or check.get("status") != "ok":
        return "rights-unverified"
    right = "read" if operation in READ else operation
    flag = row.get("can_" + right)
    if type(flag) is not bool:
        return "rights-unverified"
    if scope.component == "FILES":
        flags = row.get("file_permissions")
        if (
            row.get("privileges_verified") is not True
            or not isinstance(flags, str)
            or not re.fullmatch(r"[A-Z]{0,64}", flags)
        ):
            return "rights-unverified"
        marker = {"read": "G", "create_file": "C", "mkdir": "K"}[right]
        if marker not in flags:
            return "server-right-missing"
    else:
        permissions = check.get("permissions")
        if row.get("privileges_verified") is not True or not isinstance(permissions, list):
            return "rights-unverified"
        if right not in permissions:
            return "server-right-missing"
    return "" if flag else "server-right-missing"


def server_blocker(connection, scope, operation):
    """Use only the exact resource's bounded measurement and its original timestamp."""
    normalized = _scope(connection, scope)
    if operation not in OPERATIONS[normalized.component]:
        return "operation-not-allowed"
    inventory = connection["discovery_snapshot"]
    row = _evidence(connection, normalized, inventory)
    return _right(normalized, operation, row, inventory)


def decide_access(snapshot, scope, operation, *, target_path=None, expected_binding=None) -> Decision:
    normalized = None

    def result(code):
        return Decision(not code, code or "allowed", snapshot.binding, normalized, operation)

    if snapshot.mode != "active" or snapshot.connection is None or snapshot.policy is None:
        return result("scope-unbound")
    try:
        normalized = _scope(snapshot.connection, scope)
        if (
            expected_binding is not None
            and expected_binding != snapshot.binding
            and expected_binding != snapshot.binding.to_dict()
        ):
            return result("policy-changed")
        if normalized.component == "FILES":
            supplied = path_segments(normalized.path)
            target = supplied if target_path is None else path_segments(target_path)
            if target[: len(supplied)] != supplied:
                return result("scope-invalid")
            candidates = [
                grant
                for grant in snapshot.policy.grants
                if grant.scope.component == "FILES"
                and target[: len(path_segments(grant.scope.path))] == path_segments(grant.scope.path)
            ]
            selected = max(candidates, key=lambda g: len(path_segments(g.scope.path)), default=None)
        else:
            if target_path is not None:
                return result("scope-invalid")
            selected = next((g for g in snapshot.policy.grants if g.scope == normalized), None)
        if selected is None or not selected.operations:
            return result("scope-off")
        if operation not in selected.operations:
            return result("operation-not-allowed")
        if account_failure_observation(snapshot):
            return result("rights-unverified")
        inventory = snapshot.connection["discovery_snapshot"]
        if normalized != selected.scope:
            exact = _evidence(snapshot.connection, normalized, inventory)
            blocker = _right(normalized, operation, exact, inventory)
            if blocker:
                return result(blocker)
        row = _evidence(snapshot.connection, selected.scope, inventory)
        return result(_right(selected.scope, operation, row, inventory))
    except (ValueError, TypeError, KeyError, RuntimeError, AttributeError) as exc:
        code = str(exc)
        known = {"rights-unverified", "scope-unbound", "folder-identity-changed", "policy-integrity-error"}
        return result(code if code in known else "scope-invalid")


def migration_candidate(
    snapshot: AccessSnapshot, proven_legacy_scope: dict
) -> tuple[Policy, tuple[str, ...]]:
    """Intersect exact old account/revision/operation proof with fresh local evidence."""
    if snapshot.mode != "legacy" or snapshot.connection is None:
        raise ValueError("scope-unbound")
    conn = snapshot.connection
    policy = new_policy(conn)
    proof = proven_legacy_scope
    if (
        not isinstance(proof, dict)
        or proof.get("account_id") != policy.binding.account_id
        or proof.get("owner_user_id") != policy.binding.owner_user_id
        or proof.get("connection_revision") != conn["revision"]
        or not isinstance(proof.get("grants"), list)
        or len(proof["grants"]) > MAX_GRANTS
    ):
        return policy, ("legacy-scope-unconfirmed",)
    grants, withheld, keys = [], [], set()
    unknown_file_scope = False
    for raw in proof["grants"]:
        try:
            grant = _grant(conn, raw)
            if grant.scope.key in keys:
                return policy, ("legacy-scope-ambiguous",)
            keys.add(grant.scope.key)
            if grant.scope.path == "" and proof.get("account_root_confirmed") is not True:
                withheld.append("account-root-confirmation-required")
                continue
            inventory = conn["discovery_snapshot"]
            row = _evidence(conn, grant.scope, inventory)
            effective = []
            for op in grant.operations:
                code = _right(grant.scope, op, row, inventory)
                if code:
                    withheld.append(f"{grant.scope.resource_id}/{grant.scope.component}/{op}/{code}")
                else:
                    effective.append(op)
            grants.append(Grant(grant.scope, tuple(effective)))
        except (ValueError, RuntimeError, TypeError, KeyError):
            withheld.append("legacy-scope-unconfirmed")
            raw_scope = raw.get("scope") if isinstance(raw, dict) else None
            if not isinstance(raw_scope, dict) or raw_scope.get("component") not in tuple(COLLECTIONS):
                unknown_file_scope = True
    # A narrower unverified file grant must never be dropped under a broader one.
    if unknown_file_scope and any(g.scope.component == "FILES" for g in grants):
        grants = [g for g in grants if g.scope.component != "FILES"]
        withheld.append("file-scope-incomplete")
    return Policy(policy.binding, tuple(sorted(grants, key=lambda g: g.scope.key))), tuple(withheld)
