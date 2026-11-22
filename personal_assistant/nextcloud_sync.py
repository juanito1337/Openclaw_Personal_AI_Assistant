"""Run an incremental worker batch for each exactly selected, readable source."""

from __future__ import annotations

from typing import Any

from .connectors.nextcloud.discovery import DiscoveredCollection
from .contracts.time import now_utc_iso
from .knowledge import KnowledgeIndexer
from .nextcloud_access import COLLECTIONS
from .nextcloud_connection import admit_projection, collection_resource_id
from .nextcloud_index_access import ScopedSyncStorage
from .nextcloud_native_binding import verify_native_access

_DOMAINS = {"FILES": "files", "VCARD": "contacts", "VEVENT": "calendar"}


def _run_source(service, source, batch_size):
    scope = source.scope
    with source.admit(scope.resource_id) as snapshot:
        if scope.component != "FILES":
            rows = snapshot.connection["discovery_snapshot"][COLLECTIONS[scope.component]]
            matches = [
                row
                for row in rows
                if collection_resource_id(snapshot.connection, row["href"]) == scope.resource_id
            ]
            if len(matches) != 1:
                raise PermissionError("scope-unbound")
            row = matches[0]
            collection = DiscoveredCollection(
                kind="addressbook" if scope.component == "VCARD" else "calendar",
                href=row["href"],
                name=row.get("name", ""),
                resource_id=scope.resource_id,
                components=(scope.component,),
            )
    if scope.component == "FILES":
        return service.nextcloud_files.sync_index(
            source,
            KnowledgeIndexer(service.config, source),
            resource_id=scope.resource_id,
            roots=(scope.path,),
            max_items=service.config.search.nextcloud_max_items,
            max_depth=service.config.search.nextcloud_max_depth,
            batch_size=batch_size,
        )
    connector = service.nextcloud_contacts if scope.component == "VCARD" else service.nextcloud_calendar
    return connector.sync_index(source, [collection], batch_size=batch_size)


def _changed_report(snapshot, role):
    # Publish only the affected domain and blocker. The previous account's
    # resource IDs and folder names no longer belong to the current view.
    sections: dict[str, dict[str, Any]] = {
        domain: {"authoritative": False, "sources": []} for domain in _DOMAINS.values()
    }
    for grant in snapshot.policy.grants:
        if grant.scope.component in _DOMAINS and grant.operations:
            sections[_DOMAINS[grant.scope.component]]["sources"].append(
                {
                    "complete": False,
                    "authoritative": False,
                    "reason": "policy-changed",
                }
            )
    return {
        **sections,
        "partial_scopes": [domain for domain, section in sections.items() if section["sources"]],
        "ok": False,
        "complete": False,
        "partial": True,
        "resume_required": False,
        "authoritative": False,
        "reason": "policy-changed",
        "worker": role,
        "observed_at": now_utc_iso(),
        "next_step": "repeat-with-current-selection",
    }


def sync_selected_sources(service, snapshot, batch_size):
    sections: dict[str, dict[str, Any]] = {
        domain: {"sources": [], "errors": 0, "resume_required": False} for domain in _DOMAINS.values()
    }
    try:
        with admit_projection(snapshot) as current:
            verify_native_access(current)
        for grant in snapshot.policy.grants:
            scope = grant.scope
            if scope.component not in _DOMAINS or not grant.operations:
                continue
            source = ScopedSyncStorage(service.storage, snapshot, scope)
            try:
                stats = _run_source(service, source, batch_size)
                with source.admit(scope.resource_id):
                    stats["withdrawn"] = source.withdrawn
                    stats["authoritative"] = not stats.get("errors") and stats.get("authoritative", True)
            except (PermissionError, RuntimeError) as exc:
                if str(exc) in {"policy-changed", "policy-integrity-error", "scope-invalid", "scope-unbound"}:
                    raise
                code = str(exc) if isinstance(exc, PermissionError) else "source-failed"
                if code in {
                    "folder-identity-changed",
                    "server-right-missing",
                    "rights-unverified",
                    "rights-stale",
                }:
                    source.withhold_failed_source(code)
                stats = {"errors": 1, "authoritative": False, "complete": False, "reason": code}
            section = sections[source.domain]
            section["sources"].append({**stats, **source.record, "observed_at": now_utc_iso()})
            section["errors"] += int(stats.get("errors") or not stats["authoritative"])
            section["resume_required"] |= bool(stats.get("resume_required"))
        with admit_projection(snapshot) as current:
            verify_native_access(current)
            partial = [domain for domain, section in sections.items() if section["errors"]]
            resume = any(section["resume_required"] for section in sections.values())
            observed = any(section["sources"] for section in sections.values())
            return {
                **sections,
                "ok": not partial,
                "complete": observed and not partial and not resume,
                "partial": bool(partial),
                "partial_scopes": partial,
                "resume_required": resume,
                "batch_size": batch_size,
                "worker": service.role,
                "observed_at": now_utc_iso(),
                "binding": snapshot.binding.to_dict(),
                "activity": "checked" if observed else "no-selected-source",
            }
    except PermissionError as exc:
        if str(exc) == "policy-changed":
            return _changed_report(snapshot, service.role)
        raise
