"""Current local visibility for owner-produced Nextcloud index generations."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

from .nextcloud_access import Scope, account_failure_observation, decide_access, path_segments
from .nextcloud_connection import admit_projection
from .nextcloud_native_binding import verify_native_access

DOCUMENT_COMPONENTS = {"nextcloud-file": "FILES", "calendar-event": "VEVENT", "contact": "VCARD"}


def scope_record(snapshot, scope, target_path=None):
    return {"binding": snapshot.binding.to_dict(), "scope": scope.to_dict(), "target_path": target_path}


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("scope-invalid")
    return fields


def _selected_folder(snapshot, path):
    target = path_segments(path)
    matches = (
        grant.scope
        for grant in snapshot.policy.grants
        if grant.scope.component == "FILES"
        and target[: len(path_segments(grant.scope.path))] == path_segments(grant.scope.path)
    )
    return max(matches, key=lambda scope: len(path_segments(scope.path)), default=None)


def document_visible(snapshot, kind, resource_id, source_id, generation):
    if snapshot.mode == "legacy":
        return kind not in DOCUMENT_COMPONENTS or not account_failure_observation(snapshot)
    if kind == "email" and resource_id == "mail-agent":
        return True
    component = DOCUMENT_COMPONENTS.get(kind)
    if component is None or snapshot.mode != "active":
        return False
    try:
        if not isinstance(generation, str) or len(generation) > 32768:
            return False
        record = json.loads(generation, object_pairs_hook=_unique_fields)
        if (
            set(record) != {"binding", "scope", "target_path"}
            or record["binding"] != snapshot.binding.to_dict()
        ):
            return False
        scope = Scope(**record["scope"])
        if record["scope"] != scope.to_dict():
            return False
        path = source_id if component == "FILES" else None
        if path is not None and scope != _selected_folder(snapshot, path):
            return False
        if scope.component != component or resource_id != scope.resource_id or path != record["target_path"]:
            return False
        return all(
            decide_access(snapshot, scope, operation, target_path=path).allowed
            for operation in ("read", "search")
        )
    except (TypeError, ValueError, KeyError, RecursionError):
        return False


def sync_state_visible(snapshot, row):
    if snapshot.mode == "legacy":
        domain = row["scope"].split(":", 1)[0]
        return domain not in {"calendar", "contacts", "files"} or not account_failure_observation(snapshot)
    if row["scope"] == "projection" and row["resource_id"] == "mail-agent":
        return True
    if snapshot.mode != "active":
        return False
    try:
        record = json.loads(row["detail"])["nextcloud_source"]
        scope = Scope(**record["scope"])
        if (
            record != scope_record(snapshot, scope)
            or row["resource_id"] != scope.resource_id
            or scope not in (grant.scope for grant in snapshot.policy.grants)
        ):
            return False
        return all(
            decide_access(snapshot, scope, operation).allowed for operation in ("list", "read", "search")
        )
    except (TypeError, ValueError, KeyError, RecursionError):
        return False


class ScopedSyncStorage:
    """One worker view of its existing knowledge DB, bound to one selected source."""

    def __init__(self, storage, snapshot, scope):
        self.storage = storage
        self.snapshot = snapshot
        self.scope = scope
        self.core_read_only = storage.core_read_only
        self.withdrawn = 0
        self.record = scope_record(snapshot, scope)
        self.domain = {"FILES": "files", "VEVENT": "calendar", "VCARD": "contacts"}[scope.component]
        # Cursor and ETag histories must never cross two selections, including A -> B -> A.
        wire = json.dumps(self.record, sort_keys=True, separators=(",", ":"))
        self.inventory_scope = self.domain + ":" + hashlib.sha256(wire.encode()).hexdigest()

    @contextmanager
    def admit(self, resource_id, target_path=None):
        if resource_id != self.scope.resource_id:
            raise PermissionError("scope-unbound")
        with admit_projection(self.snapshot) as current:
            verify_native_access(current)
            for operation in ("list", "read", "search"):
                decision = decide_access(current, self.scope, operation, target_path=target_path)
                if not decision.allowed:
                    raise PermissionError(decision.code)
            if target_path is not None and not self.owns_path(target_path):
                raise PermissionError("scope-unbound")
            yield current

    def owns_path(self, path):
        """Each explicit descendant is a separate source, even under an enabled parent."""
        return self.scope == _selected_folder(self.snapshot, path)

    def _checkpoint(self, scope):
        if scope != self.domain:
            raise PermissionError("scope-unbound")
        return self.inventory_scope

    def get_sync_state(self, resource_id, scope):
        with self.admit(resource_id):
            return self.storage.get_sync_state(resource_id, self._checkpoint(scope))

    def set_sync_state(self, resource_id, scope, *, detail="", **values):
        with self.admit(resource_id):
            info = json.loads(detail) if detail else {}
            info["nextcloud_source"] = self.record
            self.storage.set_sync_state(
                resource_id, self._checkpoint(scope), detail=json.dumps(info), **values
            )

    def get_sync_inventory(self, resource_id, scope, remote_id):
        target = remote_id if self.scope.component == "FILES" else None
        with self.admit(resource_id, target):
            return self.storage.get_sync_inventory(resource_id, self._checkpoint(scope), remote_id)

    def upsert_sync_inventory(self, *, resource_id, scope, remote_id, source_type, **values):
        target = remote_id if self.scope.component == "FILES" else None
        if DOCUMENT_COMPONENTS.get(source_type) != self.scope.component:
            raise PermissionError("scope-unbound")
        with self.admit(resource_id, target):
            self.storage.upsert_sync_inventory(
                resource_id=resource_id,
                scope=self._checkpoint(scope),
                remote_id=remote_id,
                source_type=source_type,
                **values,
            )

    def find_sync_inventory_by_etag(self, resource_id, scope, etag):
        # In active mode always verify newly seen locators from their actual source.
        # ETags alone cannot prove two object identities or preserve file provenance.
        with self.admit(resource_id):
            self._checkpoint(scope)
            return None

    def get_document(self, resource_id, source_id):
        target = source_id if self.scope.component == "FILES" else None
        with self.admit(resource_id, target):
            row = self.storage.get_document(resource_id, source_id)
            if row is not None and document_visible(
                self.snapshot, row["source_type"], resource_id, source_id, row["index_generation"]
            ):
                return row
            return None

    def index_document(self, *, resource_id, source_type, source_id, **values):
        if DOCUMENT_COMPONENTS.get(source_type) != self.scope.component:
            raise PermissionError("scope-unbound")
        target = source_id if self.scope.component == "FILES" else None
        with self.admit(resource_id, target):
            return self.storage._index_document(
                resource_id=resource_id,
                source_type=source_type,
                source_id=source_id,
                index_generation=json.dumps({**self.record, "target_path": target}),
                **values,
            )

    def withhold_failed_source(self, code):
        """A real rights/identity contradiction invalidates even matching cached ETags."""
        with admit_projection(self.snapshot) as current:
            verify_native_access(current)
            connection = self.storage.knowledge_connection
            with connection:
                connection.execute(
                    """UPDATE documents SET index_generation=NULL WHERE resource_id=?
                       AND CASE WHEN json_valid(index_generation) THEN
                         json_extract(index_generation,'$.binding')=json(?)
                         AND json_extract(index_generation,'$.scope')=json(?) END""",
                    (
                        self.scope.resource_id,
                        json.dumps(self.record["binding"]),
                        json.dumps(self.record["scope"]),
                    ),
                )
                connection.execute(
                    "DELETE FROM sync_inventory WHERE resource_id=? AND scope=?",
                    (self.scope.resource_id, self.inventory_scope),
                )
                self.storage.set_sync_state(
                    self.scope.resource_id,
                    self.inventory_scope,
                    cursor="",
                    status="blocked",
                    detail=json.dumps({"nextcloud_source": self.record, "reason": code}),
                )

    def withdraw_document(self, connection, source_id):
        target = source_id if self.scope.component == "FILES" else None
        generation = json.dumps({**self.record, "target_path": target})
        self.withdrawn += connection.execute(
            "UPDATE documents SET index_generation=NULL "
            "WHERE resource_id=? AND source_id=? AND index_generation=?",
            (self.scope.resource_id, source_id, generation),
        ).rowcount

    def reconcile_sync_inventory(self, *, resource_id, scope, remote_ids):
        with self.admit(resource_id):
            return self.storage.reconcile_sync_inventory(
                resource_id=resource_id,
                scope=self._checkpoint(scope),
                remote_ids=remote_ids,
                current_source=self,
            )

    def audit(self, event, detail, *, resource_id):
        with self.admit(resource_id):
            self.storage.audit(event, {"code": "nextcloud-sync-failed"}, resource_id=resource_id)


def require_scoped_sync(client, storage, component) -> ScopedSyncStorage | None:
    snapshot = getattr(client, "access_snapshot", None)
    if snapshot is None or snapshot.mode == "legacy":
        return None
    if (
        snapshot.mode != "active"
        or not isinstance(storage, ScopedSyncStorage)
        or storage.snapshot != snapshot
        or storage.scope.component != component
    ):
        raise PermissionError("scope-unbound")
    with storage.admit(storage.scope.resource_id):
        return storage
