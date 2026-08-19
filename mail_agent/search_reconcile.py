from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from personal_assistant.contracts.mail_projection import load_search_projection
from personal_assistant.contracts.mail_projection_types import PROJECTION_MANIFEST
from personal_assistant.contracts.mail_projection_v2 import (
    MailLocator,
    canonical_json_bytes,
    locator_identity,
    occurrence_identity,
    require_sha256,
)

from .parser import parse_eml
from .search_backfill import (
    AntivirusGate,
    BackfillBackendError,
    BackfillEnvelope,
    BackfillFolder,
    ConnectorCapabilities,
    HimalayaBackfillBackend,
    physical_attachments,
)
from .search_projection_v2 import PartitionedSearchSnapshotWriter
from .utils import atomic_write_bytes, now_utc_iso

STATE_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class ReconcileLimits:
    max_folders: int = 500
    max_messages: int = 100_000
    max_bytes: int = 2_000_000_000
    max_message_bytes: int = 100_000_000
    max_runtime_seconds: float = 3600.0
    request_interval_seconds: float = 0.0
    retention_generations: int = 2

    def validated(self) -> ReconcileLimits:
        if not 1 <= self.max_folders <= 10_000:
            raise ValueError("max_folders ist ungueltig")
        if not 1 <= self.max_messages <= 10_000_000:
            raise ValueError("max_messages ist ungueltig")
        if not 1 <= self.max_bytes <= 10_000_000_000_000:
            raise ValueError("max_bytes ist ungueltig")
        if not 1 <= self.max_message_bytes <= self.max_bytes:
            raise ValueError("max_message_bytes ist ungueltig")
        if not 0 < self.max_runtime_seconds <= 604_800:
            raise ValueError("max_runtime_seconds ist ungueltig")
        if not 0 <= self.request_interval_seconds <= 60:
            raise ValueError("request_interval_seconds ist ungueltig")
        if not 2 <= self.retention_generations <= 20:
            raise ValueError("retention_generations muss mindestens 2 sein")
        return self


@dataclass(frozen=True, slots=True)
class ReconcileObservation:
    mailbox_id: str
    uid: str
    subject: str = ""
    sender_name: str = ""
    sender_addr: str = ""
    date: str = ""
    received_at: str = ""
    raw_sha256: str = ""
    raw_sha256_verified: bool = False
    move_from_locator_id: str = ""

    def envelope(self) -> BackfillEnvelope:
        return BackfillEnvelope(
            mailbox_id=self.mailbox_id,
            uid=self.uid,
            subject=self.subject,
            sender_name=self.sender_name,
            sender_addr=self.sender_addr,
            date=self.date,
            received_at=self.received_at,
        )


@dataclass(frozen=True, slots=True)
class FolderReconcileScan:
    items: tuple[ReconcileObservation, ...]
    cursor: str
    complete: bool
    authoritative: bool
    error: str = ""


class ReconcileBackend(Protocol):
    def capabilities(self) -> ConnectorCapabilities: ...

    def inventory(self) -> list[BackfillFolder]: ...

    def scan_folder(
        self,
        folder: BackfillFolder,
        *,
        previous_cursor: str,
        max_messages: int,
    ) -> FolderReconcileScan: ...

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes: ...


class HimalayaReconcileBackend:
    """Expose the honest fallback: current Himalaya cannot attest M11.3 deltas."""

    def __init__(self, backend: HimalayaBackfillBackend) -> None:
        self.backend = backend

    def capabilities(self) -> ConnectorCapabilities:
        return self.backend.capabilities()

    def inventory(self) -> list[BackfillFolder]:
        return self.backend.inventory()

    def scan_folder(
        self,
        folder: BackfillFolder,
        *,
        previous_cursor: str,
        max_messages: int,
    ) -> FolderReconcileScan:
        del folder, previous_cursor, max_messages
        raise BackfillBackendError(
            "authoritative-connector-required",
            "Himalaya exponiert keine UIDVALIDITY-/UID-Deltaidentitaet",
        )

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes:
        return self.backend.fetch_raw(folder, envelope)


@dataclass(slots=True)
class _OldOccurrence:
    reference: dict[str, str]
    payload: dict[str, Any]
    content_reference: dict[str, str]
    content: dict[str, Any]
    partition_id: str
    folder_id: str

    @property
    def occurrence_id(self) -> str:
        return str(self.payload["occurrence_id"])

    @property
    def raw_sha256(self) -> str:
        return str(self.payload["raw_sha256"])

    @property
    def locators(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.payload.get("locators", [])]


@dataclass(slots=True)
class _FinalOccurrence:
    occurrence_id: str
    raw_sha256: str
    content_reference: dict[str, str]
    reference: dict[str, str]
    locators: tuple[MailLocator, ...]
    folder_id: str


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Ungueltige Projektionsdatei: {path.name}")
    return payload


def _load_graph(root: Path) -> tuple[dict[str, Any], dict[str, _OldOccurrence]]:
    projection = load_search_projection(root)
    if projection.schema != 2 or not projection.complete:
        raise ValueError("M11.3 benoetigt eine vollstaendige v2-Ausgangsgeneration")
    root_payload = _read_json(root / PROJECTION_MANIFEST)
    result: dict[str, _OldOccurrence] = {}
    for partition_reference in root_payload.get("partitions", []):
        partition = _read_json(root / str(partition_reference["filename"]))
        for occurrence_reference in partition.get("records", []):
            occurrence = _read_json(root / str(occurrence_reference["filename"]))
            content_reference = {
                "content_id": str(occurrence["content_id"]),
                "content_filename": str(occurrence["content_filename"]),
                "content_sha256": str(occurrence["content_sha256"]),
            }
            content = _read_json(root / content_reference["content_filename"])
            item = _OldOccurrence(
                reference={key: str(occurrence_reference[key]) for key in (
                    "filename", "sha256", "occurrence_id", "content_id"
                )},
                payload=occurrence,
                content_reference=content_reference,
                content=content,
                partition_id=str(partition["partition_id"]),
                folder_id=str(partition["folder_id"]),
            )
            result[item.occurrence_id] = item
    return root_payload, result


def _locator_from_payload(payload: dict[str, Any], *, current: bool | None = None) -> MailLocator:
    return MailLocator(
        resource_id=str(payload.get("resource_id") or ""),
        folder_id=str(payload.get("folder_id") or ""),
        folder_name=str(payload.get("folder_name") or ""),
        mailbox_id=str(payload.get("mailbox_id") or ""),
        uidvalidity=str(payload.get("uidvalidity") or ""),
        uid=str(payload.get("uid") or ""),
        observed_at=str(payload.get("observed_at") or ""),
        quarantine=bool(payload.get("quarantine")),
        is_current=bool(payload.get("is_current", True)) if current is None else current,
    )


def _partition_id(folder_id: str) -> str:
    return "reconcile:" + hashlib.sha256(folder_id.encode()).hexdigest()[:32]


class ReconcileIncomplete(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class MailSearchReconciler:
    def __init__(
        self,
        backend: ReconcileBackend,
        antivirus: AntivirusGate,
        *,
        projection_root: Path,
        state_path: Path,
        quarantine_folders: tuple[str, ...] = (),
        resource_id: str = "mail-agent",
        limits: ReconcileLimits | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self.backend = backend
        self.antivirus = antivirus
        self.projection_root = projection_root
        self.state_path = state_path
        self.quarantine = {item.casefold() for item in quarantine_folders}
        self.resource_id = resource_id
        self.limits = (limits or ReconcileLimits()).validated()
        self.monotonic = monotonic
        self.sleep = sleep
        self.hook = hook
        self._run_started = 0.0
        self._last_request_at: float | None = None

    def _event(self, name: str) -> None:
        if self.hook is not None:
            self.hook(name)

    def _state(self) -> dict[str, Any]:
        try:
            payload = _read_json(self.state_path)
        except FileNotFoundError:
            return {}
        if payload.get("schema") != STATE_SCHEMA:
            raise ValueError("Ungueltiger M11.3-Reconcile-State")
        return payload

    def _write_state(
        self,
        *,
        generation: str,
        scanner_identity: str,
        cursors: dict[str, str],
    ) -> None:
        atomic_write_bytes(
            self.state_path,
            canonical_json_bytes(
                {
                    "schema": STATE_SCHEMA,
                    "root_generation": generation,
                    "scanner_identity": scanner_identity,
                    "folder_cursors": dict(sorted(cursors.items())),
                    "updated_at": now_utc_iso(),
                }
            ),
        )

    def _failed(self, code: str, detail: str, metrics: dict[str, Any]) -> dict[str, Any]:
        metrics["failed"] += 1
        return {
            "ok": False,
            "complete": False,
            "published": False,
            "cursor_advanced": False,
            "writes_imap": False,
            "error": {"code": code, "detail": str(detail)[:500]},
            "metrics": metrics,
        }

    def _throttle(self) -> None:
        remaining = self.limits.max_runtime_seconds - (
            self.monotonic() - self._run_started
        )
        if remaining <= 0:
            raise ReconcileIncomplete("runtime-limit", "Laufzeitbudget erreicht")
        if self._last_request_at is not None:
            delay = self.limits.request_interval_seconds - (
                self.monotonic() - self._last_request_at
            )
            if delay > 0:
                if delay >= remaining:
                    raise ReconcileIncomplete(
                        "rate-limit-budget", "Rate-Limit passt nicht ins Laufzeitbudget"
                    )
                self.sleep(delay)
        self._last_request_at = self.monotonic()

    def _fetch_raw(
        self,
        folder: BackfillFolder,
        observation: ReconcileObservation,
        metrics: dict[str, Any],
    ) -> bytes:
        self._throttle()
        raw = self.backend.fetch_raw(folder, observation.envelope())
        metrics["body_fetches"] += 1
        metrics["bytes"] += len(raw)
        if metrics["bytes"] > self.limits.max_bytes:
            raise ReconcileIncomplete("byte-limit", "Bytebudget erreicht")
        if len(raw) > self.limits.max_message_bytes:
            raise ReconcileIncomplete("message-too-large", "Einzelmailgroesse erreicht")
        return raw

    def _scan_raw(self, raw: bytes, observation: ReconcileObservation, metrics: dict[str, Any]) -> None:
        try:
            scan = self.antivirus.scan_bytes(
                raw,
                name=f"{observation.mailbox_id}.eml",
                source_type="mail-search-reconcile-raw",
            )
        except Exception as exc:
            raise ReconcileIncomplete("antivirus-error", type(exc).__name__) from exc
        metrics["clamav_calls"] += 1
        if not bool(getattr(scan, "clean", False)):
            raise ReconcileIncomplete(
                "antivirus-blocked", str(getattr(scan, "status", "scanner-error"))
            )
        for name, payload in physical_attachments(raw):
            try:
                attachment_scan = self.antivirus.scan_bytes(
                    payload,
                    name=name,
                    source_type="mail-search-reconcile-attachment",
                )
            except Exception as exc:
                raise ReconcileIncomplete("antivirus-error", type(exc).__name__) from exc
            metrics["clamav_calls"] += 1
            if not bool(getattr(attachment_scan, "clean", False)):
                raise ReconcileIncomplete(
                    "antivirus-blocked",
                    str(getattr(attachment_scan, "status", "scanner-error")),
                )

    @staticmethod
    def _content_reference(old: _OldOccurrence) -> dict[str, str]:
        return dict(old.content_reference)

    def _archive_root(self, payload: dict[str, Any]) -> Path:
        generation = require_sha256(payload.get("root_generation"), field="root_generation")
        path = self.projection_root / f"root-{generation}.json"
        atomic_write_bytes(path, canonical_json_bytes(payload))
        return path

    def _retention(self) -> dict[str, int]:
        histories: list[tuple[str, Path, dict[str, Any]]] = []
        for path in self.projection_root.glob("root-*.json"):
            try:
                payload = _read_json(path)
                histories.append((str(payload.get("generated_at") or ""), path, payload))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue
        histories.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        active = _read_json(self.projection_root / PROJECTION_MANIFEST)
        active_generation = str(active.get("root_generation") or "")
        active_rows = [
            item for item in histories
            if str(item[2].get("root_generation") or "") == active_generation
        ]
        kept = active_rows[:1]
        kept.extend(
            item
            for item in histories
            if item not in kept
        )
        kept = kept[: self.limits.retention_generations]
        protected = {PROJECTION_MANIFEST, *(item[1].name for item in kept)}
        for _stamp, _path, root in kept:
            for partition_ref in root.get("partitions", []):
                partition_name = str(partition_ref.get("filename") or "")
                protected.add(partition_name)
                try:
                    partition = _read_json(self.projection_root / partition_name)
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    continue
                for occurrence_ref in partition.get("records", []):
                    occurrence_name = str(occurrence_ref.get("filename") or "")
                    protected.add(occurrence_name)
                    try:
                        occurrence = _read_json(self.projection_root / occurrence_name)
                    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                        continue
                    protected.add(str(occurrence.get("content_filename") or ""))
        removed = 0
        prefixes = ("root-", "partition-", "occurrence-", "content-")
        for path in self.projection_root.glob("*.json"):
            if path.name in protected or not path.name.startswith(prefixes):
                continue
            path.unlink()
            removed += 1
        return {"kept_generations": len(kept), "removed_files": removed}

    def run(self, *, approved: bool) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Mail-Reconciliation benoetigt --yes und explizite Freigabe")
        started = self.monotonic()
        self._run_started = started
        self._last_request_at = None
        metrics: dict[str, Any] = {
            "seen": 0,
            "new": 0,
            "changed": 0,
            "moved": 0,
            "copied": 0,
            "removed": 0,
            "unchanged": 0,
            "blocked": 0,
            "failed": 0,
            "header_fetches": 0,
            "body_fetches": 0,
            "bytes": 0,
            "parser_calls": 0,
            "ocr_calls": 0,
            "clamav_calls": 0,
            "model_calls": 0,
            "fts_rows_changed": 0,
            "embeddings_reused": 0,
            "embeddings_new": 0,
        }
        capabilities = self.backend.capabilities()
        required = (
            capabilities.paging
            and capabilities.raw_fetch
            and capabilities.uid
            and capabilities.uidvalidity
            and capabilities.folder_stable_id
        )
        if not required:
            return self._failed(
                "authoritative-connector-required",
                "UID, UIDVALIDITY, stabile Ordner-ID, Paging und Raw-Fetch sind erforderlich",
                metrics,
            )
        try:
            previous_root, old_occurrences = _load_graph(self.projection_root)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return self._failed("invalid-baseline", str(exc), metrics)
        try:
            folders = self.backend.inventory()
        except BackfillBackendError as exc:
            return self._failed(exc.kind, str(exc), metrics)
        if len(folders) > self.limits.max_folders:
            return self._failed("folder-limit", "Ordnerlimit erreicht", metrics)
        if any(not folder.folder_id or not folder.uidvalidity for folder in folders):
            return self._failed(
                "authoritative-folder-identity-required",
                "Jeder Ordner benoetigt stabile ID und UIDVALIDITY",
                metrics,
            )
        state = self._state()
        scanner_identity = self.antivirus.scanner_identity(refresh=False)
        force_rescan = bool(
            state.get("scanner_identity")
            and state.get("scanner_identity") != scanner_identity
        )
        scans: dict[str, FolderReconcileScan] = {}
        folder_by_id = {folder.folder_id: folder for folder in folders}
        cursors = dict(state.get("folder_cursors") or {})
        try:
            for folder in sorted(folders, key=lambda item: item.folder_id):
                elapsed = self.monotonic() - started
                if elapsed >= self.limits.max_runtime_seconds:
                    raise ReconcileIncomplete("runtime-limit", "Laufzeitbudget erreicht")
                self._throttle()
                scan = self.backend.scan_folder(
                    folder,
                    previous_cursor=str(cursors.get(folder.folder_id) or ""),
                    max_messages=self.limits.max_messages - metrics["seen"],
                )
                metrics["header_fetches"] += len(scan.items)
                metrics["seen"] += len(scan.items)
                if metrics["seen"] > self.limits.max_messages:
                    raise ReconcileIncomplete("message-limit", "Nachrichtenlimit erreicht")
                if not scan.complete or not scan.authoritative or scan.error:
                    raise ReconcileIncomplete(
                        "partial-folder-scan", scan.error or folder.name
                    )
                scans[folder.folder_id] = scan
                cursors[folder.folder_id] = scan.cursor
            self._event("after-scan")
        except (BackfillBackendError, ReconcileIncomplete) as exc:
            code = exc.kind if isinstance(exc, BackfillBackendError) else exc.code
            return self._failed(code, str(exc), metrics)

        observed: list[tuple[BackfillFolder, ReconcileObservation, MailLocator]] = []
        present_locator_ids: set[str] = set()
        try:
            for folder_id, scan in scans.items():
                folder = folder_by_id[folder_id]
                for item in scan.items:
                    if not item.uid:
                        raise ReconcileIncomplete("missing-uid", folder.name)
                    locator = MailLocator(
                        resource_id=self.resource_id,
                        folder_id=folder.folder_id,
                        folder_name=folder.name,
                        mailbox_id=item.mailbox_id,
                        uidvalidity=folder.uidvalidity,
                        uid=item.uid,
                        observed_at=now_utc_iso(),
                        quarantine=folder.name.casefold() in self.quarantine,
                    )
                    locator_id = locator_identity(locator)
                    if locator_id in present_locator_ids:
                        raise ReconcileIncomplete("duplicate-locator", locator_id)
                    present_locator_ids.add(locator_id)
                    observed.append((folder, item, locator))
        except ReconcileIncomplete as exc:
            return self._failed(exc.code, str(exc), metrics)

        old_by_locator: dict[str, _OldOccurrence] = {}
        old_by_raw: dict[str, list[_OldOccurrence]] = defaultdict(list)
        for old in old_occurrences.values():
            old_by_raw[old.raw_sha256].append(old)
            for raw_locator in old.locators:
                if bool(raw_locator.get("is_current", True)):
                    old_by_locator[str(raw_locator["locator_id"])] = old
        matched: dict[str, list[tuple[BackfillFolder, ReconcileObservation, MailLocator]]] = defaultdict(list)
        pending: list[tuple[BackfillFolder, ReconcileObservation, MailLocator]] = []
        for row in observed:
            locator_id = locator_identity(row[2])
            old_match = old_by_locator.get(locator_id)
            if old_match is None:
                pending.append(row)
            else:
                matched[old_match.occurrence_id].append(row)

        writer = PartitionedSearchSnapshotWriter(
            self.projection_root, resource_id=self.resource_id
        )
        final: dict[str, _FinalOccurrence] = {}
        used_move_sources: set[str] = set()

        def publish_reused(
            old: _OldOccurrence,
            current_rows: list[tuple[BackfillFolder, ReconcileObservation, MailLocator]],
            *,
            content_reference: dict[str, str] | None = None,
            raw_sha256: str | None = None,
        ) -> _FinalOccurrence:
            locators: list[MailLocator] = []
            for payload in old.locators:
                old_locator = _locator_from_payload(payload)
                matching = next(
                    (
                        row[2]
                        for row in current_rows
                        if locator_identity(row[2]) == locator_identity(old_locator)
                    ),
                    None,
                )
                if matching is not None:
                    unchanged_location = (
                        old_locator.folder_name == matching.folder_name
                        and old_locator.quarantine == matching.quarantine
                        and old_locator.is_current
                    )
                    locators.append(old_locator if unchanged_location else matching)
                else:
                    locators.append(replace(old_locator, is_current=False))
            for _folder, _item, locator in current_rows:
                if locator_identity(locator) not in {
                    locator_identity(value) for value in locators
                }:
                    locators.append(locator)
            if not any(item.is_current for item in locators):
                raise ReconcileIncomplete("missing-current-locator", old.occurrence_id)
            status = (
                "quarantine-untrusted"
                if all(item.quarantine for item in locators if item.is_current)
                else "active"
            )
            digest = raw_sha256 or old.raw_sha256
            reference = writer.publish_reused_occurrence(
                content_reference=content_reference or old.content_reference,
                raw_sha256=digest,
                locators=tuple(locators),
                source_status=status,
                observed_at=now_utc_iso(),
                expected_occurrence_id=old.occurrence_id,
            )
            current_folder = next(item.folder_id for item in locators if item.is_current)
            return _FinalOccurrence(
                old.occurrence_id,
                digest,
                content_reference or old.content_reference,
                reference,
                tuple(locators),
                current_folder,
            )

        try:
            for occurrence_id, rows in matched.items():
                old = old_occurrences[occurrence_id]
                provider_changed = any(
                    item.raw_sha256_verified
                    and item.raw_sha256
                    and item.raw_sha256 != old.raw_sha256
                    for _folder, item, _locator in rows
                )
                if force_rescan or provider_changed:
                    matched_raw = self._fetch_raw(rows[0][0], rows[0][1], metrics)
                    digest = hashlib.sha256(matched_raw).hexdigest()
                    if provider_changed and digest != rows[0][1].raw_sha256:
                        raise ReconcileIncomplete(
                            "provider-digest-mismatch", rows[0][1].mailbox_id
                        )
                    if force_rescan or digest != old.raw_sha256:
                        self._scan_raw(matched_raw, rows[0][1], metrics)
                    if digest != old.raw_sha256:
                        try:
                            parsed = parse_eml(
                                matched_raw,
                                rows[0][1].envelope().parser_envelope(),
                                rows[0][0].name,
                            )
                        except Exception as exc:
                            raise ReconcileIncomplete(
                                "mail-parse-error", type(exc).__name__
                            ) from exc
                        metrics["parser_calls"] += 1
                        content_reference = writer.publish_content(parsed)
                        final[occurrence_id] = publish_reused(
                            old,
                            rows,
                            content_reference=content_reference,
                            raw_sha256=digest,
                        )
                        metrics["changed"] += 1
                        continue
                exact = True
                for _folder, _item, locator in rows:
                    old_payload = next(
                        item for item in old.locators
                        if str(item["locator_id"]) == locator_identity(locator)
                    )
                    if (
                        str(old_payload.get("folder_name") or "") != locator.folder_name
                        or bool(old_payload.get("quarantine")) != locator.quarantine
                    ):
                        exact = False
                if exact and len(rows) == sum(
                    bool(item.get("is_current", True)) for item in old.locators
                ):
                    final[occurrence_id] = _FinalOccurrence(
                        occurrence_id,
                        old.raw_sha256,
                        old.content_reference,
                        old.reference,
                        tuple(_locator_from_payload(item) for item in old.locators),
                        rows[0][0].folder_id,
                    )
                    metrics["unchanged"] += 1
                else:
                    final[occurrence_id] = publish_reused(old, rows)
                    metrics["moved"] += 1

            pending_by_hint: dict[str, int] = defaultdict(int)
            for _folder, item, _locator in pending:
                if item.raw_sha256_verified and item.raw_sha256:
                    pending_by_hint[item.raw_sha256] += 1
            for folder, item, locator in pending:
                digest = ""
                raw: bytes | None = None
                if item.raw_sha256_verified and item.raw_sha256:
                    digest = require_sha256(item.raw_sha256, field="observation.raw_sha256")
                else:
                    raw = self._fetch_raw(folder, item, metrics)
                    digest = hashlib.sha256(raw).hexdigest()
                candidates = [
                    old for old in old_by_raw.get(digest, [])
                    if old.occurrence_id not in final
                    and old.occurrence_id not in used_move_sources
                    and all(
                        str(value["locator_id"]) not in present_locator_ids
                        for value in old.locators
                        if bool(value.get("is_current", True))
                    )
                    and not any(
                        str(value.get("folder_id") or "") == locator.folder_id
                        and str(value.get("uidvalidity") or "") != locator.uidvalidity
                        for value in old.locators
                        if bool(value.get("is_current", True))
                    )
                ]
                explicit = old_by_locator.get(item.move_from_locator_id)
                move_source = (
                    explicit
                    if explicit is not None and explicit.raw_sha256 == digest
                    else candidates[0]
                    if len(candidates) == 1 and pending_by_hint.get(digest, 1) == 1
                    else None
                )
                content_old = old_by_raw.get(digest, [None])[0]
                if content_old is not None:
                    content_reference = self._content_reference(content_old)
                else:
                    if raw is None:
                        raw = self._fetch_raw(folder, item, metrics)
                        if hashlib.sha256(raw).hexdigest() != digest:
                            raise ReconcileIncomplete(
                                "provider-digest-mismatch", item.mailbox_id
                            )
                    self._scan_raw(raw, item, metrics)
                    try:
                        parsed = parse_eml(
                            raw, item.envelope().parser_envelope(), folder.name
                        )
                    except Exception as exc:
                        raise ReconcileIncomplete(
                            "mail-parse-error", type(exc).__name__
                        ) from exc
                    metrics["parser_calls"] += 1
                    content_reference = writer.publish_content(parsed)
                if move_source is not None:
                    final[move_source.occurrence_id] = publish_reused(
                        move_source,
                        [(folder, item, locator)],
                    )
                    used_move_sources.add(move_source.occurrence_id)
                    metrics["moved"] += 1
                    continue
                status = "quarantine-untrusted" if locator.quarantine else "active"
                reference = writer.publish_reused_occurrence(
                    content_reference=content_reference,
                    raw_sha256=digest,
                    locators=(locator,),
                    source_status=status,
                    observed_at=now_utc_iso(),
                )
                occurrence_id = occurrence_identity(locator, digest)
                final[occurrence_id] = _FinalOccurrence(
                    occurrence_id,
                    digest,
                    content_reference,
                    reference,
                    (locator,),
                    folder.folder_id,
                )
                if content_old is None:
                    metrics["new"] += 1
                else:
                    uidvalidity_reset = any(
                        str(value.get("folder_id") or "") == locator.folder_id
                        and str(value.get("uidvalidity") or "") != locator.uidvalidity
                        for old in old_by_raw.get(digest, [])
                        for value in old.locators
                        if bool(value.get("is_current", True))
                    )
                    metrics["changed" if uidvalidity_reset else "copied"] += 1
        except (BackfillBackendError, ReconcileIncomplete) as exc:
            metrics["blocked"] += int(
                isinstance(exc, ReconcileIncomplete)
                and exc.code in {"antivirus-blocked", "message-too-large"}
            )
            code = exc.kind if isinstance(exc, BackfillBackendError) else exc.code
            return self._failed(code, str(exc), metrics)

        tombstones_by_folder: dict[str, list[dict[str, str]]] = defaultdict(list)
        folder_names = {item.folder_id: item.name for item in folders}
        timestamp = now_utc_iso()
        for old in old_occurrences.values():
            current_final = final.get(old.occurrence_id)
            final_current_ids = {
                locator_identity(item)
                for item in (current_final.locators if current_final else ())
                if item.is_current
            }
            for raw_locator in old.locators:
                if not bool(raw_locator.get("is_current", True)):
                    continue
                locator_id = str(raw_locator["locator_id"])
                if locator_id in final_current_ids:
                    continue
                folder_id = str(raw_locator["folder_id"])
                folder_names.setdefault(folder_id, str(raw_locator.get("folder_name") or ""))
                tombstones_by_folder[folder_id].append(
                    {
                        "occurrence_id": old.occurrence_id,
                        "locator_id": locator_id,
                        "tombstoned_at": timestamp,
                        "reason": "moved" if current_final else "deleted",
                    }
                )
                metrics["removed"] += 1

        changed = any(
            metrics[key] for key in ("new", "changed", "moved", "copied", "removed")
        )
        if not changed and not force_rescan:
            self._write_state(
                generation=str(previous_root["root_generation"]),
                scanner_identity=scanner_identity,
                cursors=cursors,
            )
            return {
                "ok": True,
                "complete": True,
                "published": False,
                "cursor_advanced": True,
                "no_op": True,
                "writes_imap": False,
                "root_generation": str(previous_root["root_generation"]),
                "metrics": metrics,
            }

        records_by_folder: dict[str, list[dict[str, str]]] = defaultdict(list)
        for final_item in final.values():
            records_by_folder[final_item.folder_id].append(final_item.reference)
        partition_refs: list[dict[str, Any]] = []
        all_folder_ids = sorted(
            set(folder_by_id) | set(records_by_folder) | set(tombstones_by_folder)
        )
        for folder_id in all_folder_ids:
            partition_refs.append(
                writer.publish_partition_references(
                    partition_id=_partition_id(folder_id),
                    folder_id=folder_id,
                    folder_name=folder_names.get(folder_id, folder_id),
                    records=records_by_folder.get(folder_id, []),
                    generated_at=timestamp,
                    tombstones=tombstones_by_folder.get(folder_id, []),
                )
            )
        self._event("before-root")
        self._archive_root(previous_root)
        writer.publish_root(
            partition_refs,
            expected_partition_ids=[str(item["partition_id"]) for item in partition_refs],
            complete=True,
            authoritative=True,
            generated_at=timestamp,
        )
        published_root = _read_json(self.projection_root / PROJECTION_MANIFEST)
        verified = load_search_projection(self.projection_root)
        if not verified.complete or verified.generation != published_root["root_generation"]:
            raise RuntimeError("Publizierte Reconciliation konnte nicht verifiziert werden")
        self._archive_root(published_root)
        self._event("after-root")
        self._write_state(
            generation=verified.generation,
            scanner_identity=scanner_identity,
            cursors=cursors,
        )
        self._event("after-cursor")
        return {
            "ok": True,
            "complete": True,
            "published": True,
            "cursor_advanced": True,
            "no_op": False,
            "writes_imap": False,
            "root_generation": verified.generation,
            "retention": self._retention(),
            "metrics": metrics,
        }
