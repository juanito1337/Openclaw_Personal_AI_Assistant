from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Protocol

from personal_assistant.contracts.mail_projection_v2 import MailLocator, canonical_json_bytes

from .himalaya import HimalayaClient
from .models import Envelope, ParsedMessage
from .parser import parse_eml
from .search_projection_v2 import PartitionedSearchSnapshotWriter, ProjectionOccurrenceInput
from .utils import atomic_write_bytes, now_utc_iso

CHECKPOINT_SCHEMA = 1
_PAGE_OUT_OF_BOUNDS = re.compile(r"(?:out of bound|page[^\n]{0,80}bound)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    paging: bool
    raw_fetch: bool
    uid: bool = False
    uidvalidity: bool = False
    uidnext: bool = False
    modseq: bool = False
    condstore: bool = False
    qresync: bool = False
    idle: bool = False
    folder_stable_id: bool = False
    folder_identity_assurance: str = "unknown"
    cursor_contract: str = "page-number"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BackfillFolder:
    folder_id: str
    name: str
    uidvalidity: str = ""


@dataclass(frozen=True, slots=True)
class BackfillEnvelope:
    mailbox_id: str
    uid: str = ""
    subject: str = ""
    sender_name: str = ""
    sender_addr: str = ""
    date: str = ""
    received_at: str = ""

    def parser_envelope(self) -> Envelope:
        return Envelope(
            mailbox_id=self.mailbox_id,
            subject=self.subject,
            sender_name=self.sender_name,
            sender_addr=self.sender_addr,
            date=self.date,
            received_at=self.received_at,
        )


@dataclass(frozen=True, slots=True)
class BackfillPage:
    items: tuple[BackfillEnvelope, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class BackfillLimits:
    page_size: int = 50
    max_pages: int = 200
    max_messages: int = 10_000
    max_bytes: int = 1_000_000_000
    max_message_bytes: int = 100_000_000
    max_runtime_seconds: float = 3600.0
    request_interval_seconds: float = 0.0

    def validated(self) -> BackfillLimits:
        if not 1 <= self.page_size <= 500:
            raise ValueError("page_size muss zwischen 1 und 500 liegen")
        if not 1 <= self.max_pages <= 1_000_000:
            raise ValueError("max_pages muss zwischen 1 und 1000000 liegen")
        if not 1 <= self.max_messages <= 10_000_000:
            raise ValueError("max_messages muss zwischen 1 und 10000000 liegen")
        if not 1 <= self.max_bytes <= 10_000_000_000_000:
            raise ValueError("max_bytes ist ungueltig")
        if not 1 <= self.max_message_bytes <= self.max_bytes:
            raise ValueError("max_message_bytes ist ungueltig")
        if not 0 < self.max_runtime_seconds <= 604_800:
            raise ValueError("max_runtime_seconds ist ungueltig")
        if not 0 <= self.request_interval_seconds <= 60:
            raise ValueError("request_interval_seconds ist ungueltig")
        return self


class BackfillBackendError(RuntimeError):
    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        super().__init__(detail)


class BackfillBackend(Protocol):
    def capabilities(self) -> ConnectorCapabilities: ...

    def inventory(self) -> list[BackfillFolder]: ...

    def fetch_page(self, folder: BackfillFolder, *, page: int, page_size: int) -> BackfillPage: ...

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes: ...


class AntivirusGate(Protocol):
    def scanner_identity(self, *, refresh: bool = False) -> str: ...

    def scan_bytes(self, data: bytes, *, name: str, source_type: str, use_cache: bool = True) -> Any: ...


def _folder_name_id(resource_id: str, name: str) -> str:
    digest = hashlib.sha256(f"{resource_id}\0{name}".encode()).hexdigest()[:32]
    return f"folder:{digest}"


class HimalayaBackfillBackend:
    """Honest Himalaya 1.2 adapter: paging/raw yes, IMAP delta metadata no."""

    def __init__(self, client: HimalayaClient, *, resource_id: str = "mail-agent") -> None:
        self.client = client
        self.resource_id = resource_id

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            paging=True,
            raw_fetch=True,
            cursor_contract="bounded-page-number-fallback",
        )

    def inventory(self) -> list[BackfillFolder]:
        names, error = self.client.list_folders()
        if error:
            raise BackfillBackendError("folder-inventory-error", error)
        unique = sorted({name for name in names if str(name).strip()}, key=str.casefold)
        return [BackfillFolder(_folder_name_id(self.resource_id, name), name) for name in unique]

    def fetch_page(self, folder: BackfillFolder, *, page: int, page_size: int) -> BackfillPage:
        effective_size = max(1, min(page_size, self.client.config.mailbox.page_size))
        rows, error = self.client.list_envelopes_page(folder.name, page=page, page_size=effective_size)
        if error:
            if page > 1 and _PAGE_OUT_OF_BOUNDS.search(error):
                return BackfillPage((), False)
            raise BackfillBackendError("folder-page-error", error)
        items = tuple(
            BackfillEnvelope(
                mailbox_id=row.mailbox_id,
                subject=row.subject,
                sender_name=row.sender_name,
                sender_addr=row.sender_addr,
                date=row.date,
                received_at=row.received_at,
            )
            for row in rows
        )
        return BackfillPage(items, len(items) == effective_size)

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes:
        with tempfile.TemporaryDirectory(prefix="openclaw-mail-backfill-") as temp:
            destination = Path(temp) / "message.eml"
            result = self.client.export_message(folder.name, envelope.mailbox_id, destination)
            if not result.ok:
                raise BackfillBackendError("raw-fetch-error", result.detail or result.status)
            try:
                return destination.read_bytes()
            except OSError as exc:
                raise BackfillBackendError("raw-fetch-error", str(exc)) from exc


def physical_attachments(raw: bytes) -> list[tuple[str, bytes]]:
    """Decode physical attachments only for sequential fail-closed scanning."""

    try:
        message = BytesParser(policy=policy.compat32).parsebytes(raw)
        parts = list(message.walk())
    except Exception as exc:
        raise BackfillBackendError("mail-parse-error", str(exc)) from exc
    result: list[tuple[str, bytes]] = []
    for part in parts:
        try:
            if part.is_multipart():
                continue
            filename = str(part.get_filename() or "")
            disposition = str(part.get_content_disposition() or "")
            if not filename and disposition.casefold() != "attachment":
                continue
            payload = part.get_payload(decode=True) or b""
            if not isinstance(payload, bytes):
                payload = str(payload).encode("utf-8", errors="replace")
            result.append((Path(filename).name[:300] or "attachment.bin", payload))
        except Exception as exc:
            raise BackfillBackendError("attachment-decode-error", str(exc)) from exc
    return result


class MailSearchBackfill:
    def __init__(
        self,
        backend: BackfillBackend,
        antivirus: AntivirusGate | None,
        *,
        projection_root: Path,
        checkpoint_path: Path,
        resource_id: str = "mail-agent",
        quarantine_folders: tuple[str, ...] = (),
        limits: BackfillLimits | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        after_partition: Callable[[str, int], None] | None = None,
        tag_resolver: Callable[[ParsedMessage], tuple[dict[str, Any], ...]] | None = None,
        include_folders: tuple[str, ...] = (),
    ) -> None:
        self.backend = backend
        self.antivirus = antivirus
        self.projection_root = projection_root
        self.checkpoint_path = checkpoint_path
        self.resource_id = resource_id
        self.quarantine = {item.casefold() for item in quarantine_folders}
        self.limits = (limits or BackfillLimits()).validated()
        self.monotonic = monotonic
        self.sleep = sleep
        self.after_partition = after_partition
        self.tag_resolver = tag_resolver
        self.include_folders = {item.casefold() for item in include_folders if item.strip()}

    def _selected_folders(self, folders: list[BackfillFolder]) -> list[BackfillFolder]:
        if not self.include_folders:
            return folders
        selected = [row for row in folders if row.name.casefold() in self.include_folders]
        missing = sorted(
            self.include_folders - {row.name.casefold() for row in selected}
        )
        if missing:
            raise BackfillBackendError(
                "canary-folder-missing",
                "Mindestens ein explizit gewaehlter Canary-Ordner fehlt",
            )
        return selected

    def _load_checkpoint(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ungueltiger Mail-Backfill-Checkpoint: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != CHECKPOINT_SCHEMA:
            raise RuntimeError("Ungueltiger Mail-Backfill-Checkpointvertrag")
        return payload

    def _write_checkpoint(self, payload: dict[str, Any]) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(self.checkpoint_path, canonical_json_bytes(payload))

    @staticmethod
    def _inventory_payload(folders: list[BackfillFolder]) -> list[dict[str, str]]:
        return [
            {"folder_id": row.folder_id, "name": row.name, "uidvalidity": row.uidvalidity}
            for row in sorted(folders, key=lambda item: (item.folder_id, item.name.casefold()))
        ]

    def plan(self) -> dict[str, Any]:
        capabilities = self.backend.capabilities()
        folders = self._selected_folders(self.backend.inventory())
        previous = self._load_checkpoint()
        previous_inventory = {
            str(row.get("folder_id")): str(row.get("name"))
            for row in (previous or {}).get("inventory", [])
            if isinstance(row, dict)
        }
        current_inventory = {row.folder_id: row.name for row in folders}
        added = sorted(set(current_inventory) - set(previous_inventory))
        removed = sorted(set(previous_inventory) - set(current_inventory))
        renamed = sorted(
            folder_id
            for folder_id in set(current_inventory) & set(previous_inventory)
            if current_inventory[folder_id] != previous_inventory[folder_id]
        )
        capability_issues = []
        if not capabilities.uidvalidity:
            capability_issues.append(
                "UIDVALIDITY fehlt; Fallback ist nicht autoritativ und complete bleibt false"
            )
        if not (capabilities.condstore and capabilities.qresync and capabilities.modseq):
            capability_issues.append("Kein belegter Delta-Vertrag; M11.2 verwendet begrenzten Vollscan")
        return {
            "ok": True,
            "mode": "read-only-plan",
            "writes_imap": False,
            "writes_local_index": False,
            "resource_id": self.resource_id,
            "folder_count": len(folders),
            "folders": [
                {
                    **row,
                    "quarantine_untrusted": row["name"].casefold() in self.quarantine,
                }
                for row in self._inventory_payload(folders)
            ],
            "folder_changes": {
                "added": [current_inventory[item] for item in added],
                "removed": [previous_inventory[item] for item in removed],
                "renamed": [
                    {
                        "folder_id": item,
                        "from": previous_inventory[item],
                        "to": current_inventory[item],
                    }
                    for item in renamed
                ],
            },
            "capabilities": capabilities.to_dict(),
            "capability_issues": capability_issues,
            "execution_policy": {
                "initial_scan": (
                    "bounded-folder-canary" if self.include_folders else "bounded-full-scan"
                ),
                "delta_cursor_used": False,
                "idle_used": False,
                "fallback": capabilities.cursor_contract,
            },
            "limits": asdict(self.limits),
            "approval_required": True,
            "approval": "explicit-user-local-mail-index-backfill",
        }

    @staticmethod
    def _fingerprint(
        inventory: list[dict[str, str]],
        capabilities: ConnectorCapabilities,
        limits: BackfillLimits,
        scanner_identity: str,
    ) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "inventory": inventory,
                    "capabilities": capabilities.to_dict(),
                    "limits": asdict(limits),
                    "scanner_identity": scanner_identity,
                }
            )
        ).hexdigest()

    def run(self, *, approved: bool) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Lokaler Mail-Index-Backfill benoetigt --yes und explizite Freigabe")
        if self.antivirus is None:
            raise RuntimeError("Fail-closed Antivirus-Gate fehlt")
        started = self.monotonic()
        capabilities = self.backend.capabilities()
        if not capabilities.paging or not capabilities.raw_fetch:
            raise RuntimeError("Connector belegt kein Paging und vollstaendiges Raw-Fetch")
        folders = self._selected_folders(self.backend.inventory())
        inventory = self._inventory_payload(folders)
        scanner_identity = self.antivirus.scanner_identity(refresh=False)
        fingerprint = self._fingerprint(inventory, capabilities, self.limits, scanner_identity)
        checkpoint = self._load_checkpoint()
        resumed = bool(
            checkpoint
            and checkpoint.get("fingerprint") == fingerprint
            and checkpoint.get("status") != "complete"
        )
        if not resumed:
            checkpoint = {
                "schema": CHECKPOINT_SCHEMA,
                "run_id": hashlib.sha256(f"{now_utc_iso()}\0{fingerprint}".encode()).hexdigest()[:24],
                "started_at": now_utc_iso(),
                "updated_at": now_utc_iso(),
                "status": "running",
                "fingerprint": fingerprint,
                "scanner_identity": scanner_identity,
                "inventory": inventory,
                "capabilities": capabilities.to_dict(),
                "folders": {
                    row.folder_id: {
                        "name": row.name,
                        "uidvalidity": row.uidvalidity,
                        "next_page": 1,
                        "done": False,
                        "partitions": [],
                        "messages": 0,
                        "bytes": 0,
                        "blocked": [],
                        "error": "",
                    }
                    for row in folders
                },
                "metrics": {
                    "pages": 0,
                    "messages": 0,
                    "bytes": 0,
                    "backend_calls": 1,
                    "peak_page_messages": 0,
                    "peak_page_raw_bytes": 0,
                    "peak_attachment_bytes": 0,
                },
            }
            self._write_checkpoint(checkpoint)
        assert checkpoint is not None
        metrics = checkpoint["metrics"]
        if resumed:
            metrics["backend_calls"] += 1  # Current read-only folder inventory.
        invocation_pages = 0
        invocation_messages = 0
        invocation_bytes = 0
        last_request_at: float | None = None
        stop_reason = ""

        def time_remaining() -> float:
            return self.limits.max_runtime_seconds - (self.monotonic() - started)

        def throttle() -> None:
            nonlocal last_request_at
            if time_remaining() <= 0:
                raise BackfillBackendError("runtime-limit", "Laufzeitbudget erreicht")
            if last_request_at is not None:
                delay = self.limits.request_interval_seconds - (self.monotonic() - last_request_at)
                if delay > 0:
                    if delay >= time_remaining():
                        raise BackfillBackendError(
                            "rate-limit-budget", "Rate-Limit passt nicht ins Laufzeitbudget"
                        )
                    self.sleep(delay)
            last_request_at = self.monotonic()
            metrics["backend_calls"] += 1

        writer = PartitionedSearchSnapshotWriter(self.projection_root, resource_id=self.resource_id)
        folder_by_id = {row.folder_id: row for row in folders}
        try:
            for folder_id in sorted(folder_by_id):
                folder = folder_by_id[folder_id]
                state = checkpoint["folders"][folder_id]
                if state["done"]:
                    continue
                while not state["done"]:
                    if invocation_pages >= self.limits.max_pages:
                        raise BackfillBackendError("page-limit", "Seitenlimit erreicht")
                    if invocation_messages >= self.limits.max_messages:
                        raise BackfillBackendError("message-limit", "Nachrichtenlimit erreicht")
                    page_number = int(state["next_page"])
                    throttle()
                    page = self.backend.fetch_page(folder, page=page_number, page_size=self.limits.page_size)
                    page_bytes = 0
                    occurrences: list[ProjectionOccurrenceInput] = []
                    blocked: list[dict[str, str]] = []
                    for envelope in page.items:
                        if invocation_messages + len(occurrences) + len(blocked) >= self.limits.max_messages:
                            raise BackfillBackendError("message-limit", "Nachrichtenlimit erreicht")
                        throttle()
                        raw = self.backend.fetch_raw(folder, envelope)
                        page_bytes += len(raw)
                        if invocation_bytes + page_bytes > self.limits.max_bytes:
                            raise BackfillBackendError("byte-limit", "Bytebudget erreicht")
                        raw_digest = hashlib.sha256(raw).hexdigest()
                        if len(raw) > self.limits.max_message_bytes:
                            blocked.append(
                                {
                                    "mailbox_id": envelope.mailbox_id,
                                    "sha256": raw_digest,
                                    "status": "too-large",
                                }
                            )
                            continue
                        raw_scan = self.antivirus.scan_bytes(
                            raw,
                            name=f"{envelope.mailbox_id}.eml",
                            source_type="mail-search-backfill-raw",
                        )
                        if not bool(getattr(raw_scan, "clean", False)):
                            blocked.append(
                                {
                                    "mailbox_id": envelope.mailbox_id,
                                    "sha256": raw_digest,
                                    "status": str(getattr(raw_scan, "status", "scanner-error")),
                                }
                            )
                            continue
                        attachment_failed = ""
                        try:
                            attachments = physical_attachments(raw)
                        except BackfillBackendError as exc:
                            attachments = []
                            attachment_failed = exc.kind
                        for name, payload in attachments:
                            metrics["peak_attachment_bytes"] = max(
                                metrics["peak_attachment_bytes"], len(payload)
                            )
                            scan = self.antivirus.scan_bytes(
                                payload,
                                name=name,
                                source_type="mail-search-backfill-attachment",
                            )
                            if not bool(getattr(scan, "clean", False)):
                                attachment_failed = str(getattr(scan, "status", "scanner-error"))
                                break
                        if attachment_failed:
                            blocked.append(
                                {
                                    "mailbox_id": envelope.mailbox_id,
                                    "sha256": raw_digest,
                                    "status": attachment_failed,
                                }
                            )
                            continue
                        try:
                            parsed = parse_eml(raw, envelope.parser_envelope(), folder.name)
                        except Exception:
                            blocked.append(
                                {
                                    "mailbox_id": envelope.mailbox_id,
                                    "sha256": raw_digest,
                                    "status": "mail-parse-error",
                                }
                            )
                            continue
                        stable_uid = bool(
                            capabilities.uid
                            and capabilities.uidvalidity
                            and folder.uidvalidity
                            and envelope.uid
                        )
                        locator = MailLocator(
                            resource_id=self.resource_id,
                            folder_id=folder.folder_id,
                            folder_name=folder.name,
                            mailbox_id=envelope.mailbox_id,
                            uidvalidity=folder.uidvalidity if stable_uid else "",
                            uid=envelope.uid if stable_uid else "",
                            observed_at=str(checkpoint["started_at"]),
                            quarantine=folder.name.casefold() in self.quarantine,
                        )
                        occurrences.append(
                            ProjectionOccurrenceInput(
                                parsed,
                                (locator,),
                                "quarantine-untrusted" if locator.quarantine else "active",
                                self.tag_resolver(parsed) if self.tag_resolver else (),
                            )
                        )
                    metrics["peak_page_messages"] = max(metrics["peak_page_messages"], len(page.items))
                    metrics["peak_page_raw_bytes"] = max(metrics["peak_page_raw_bytes"], page_bytes)
                    if time_remaining() <= 0:
                        raise BackfillBackendError("runtime-limit", "Laufzeitbudget erreicht")
                    page_complete = bool(
                        not blocked
                        and capabilities.uid
                        and capabilities.uidvalidity
                        and folder.uidvalidity
                        and all(item.uid for item in page.items)
                    )
                    partition_id = f"{folder.folder_id}:p{page_number:08d}"
                    reference = writer.publish_partition(
                        partition_id=partition_id,
                        folder_id=folder.folder_id,
                        folder_name=folder.name,
                        occurrences=occurrences,
                        generated_at=str(checkpoint["started_at"]),
                        complete=page_complete,
                        authoritative=page_complete,
                    )
                    if self.after_partition is not None:
                        self.after_partition(folder.folder_id, page_number)
                    # Safe boundary: projection first, cursor/checkpoint second.
                    state["partitions"].append(reference)
                    state["messages"] += len(page.items)
                    state["bytes"] += page_bytes
                    state["blocked"].extend(blocked)
                    state["next_page"] = page_number + 1
                    state["done"] = not page.has_more
                    state["error"] = ""
                    metrics["pages"] += 1
                    metrics["messages"] += len(page.items)
                    metrics["bytes"] += page_bytes
                    invocation_pages += 1
                    invocation_messages += len(page.items)
                    invocation_bytes += page_bytes
                    checkpoint["updated_at"] = now_utc_iso()
                    checkpoint["status"] = "running"
                    self._write_checkpoint(checkpoint)
        except BackfillBackendError as exc:
            stop_reason = exc.kind
            for folder_id in sorted(folder_by_id):
                state = checkpoint["folders"][folder_id]
                if not state["done"]:
                    state["error"] = exc.kind
                    break
        except Exception:
            checkpoint["status"] = "interrupted"
            checkpoint["updated_at"] = now_utc_iso()
            self._write_checkpoint(checkpoint)
            raise

        partition_refs: list[dict[str, Any]] = []
        expected: list[str] = []
        incomplete_reasons: dict[str, str] = {}
        all_done = True
        all_pages_authoritative = True
        for folder_id in sorted(folder_by_id):
            state = checkpoint["folders"][folder_id]
            refs = list(state["partitions"])
            partition_refs.extend(refs)
            expected.extend(str(item["partition_id"]) for item in refs)
            for item in refs:
                if not (item.get("complete") and item.get("authoritative")):
                    all_pages_authoritative = False
                    incomplete_reasons[str(item["partition_id"])] = (
                        "UIDVALIDITY fehlt oder mindestens ein Inhalt wurde fail-closed blockiert"
                    )
            if not state["done"]:
                all_done = False
                marker = f"{folder_id}:pending"
                expected.append(marker)
                incomplete_reasons[marker] = state["error"] or stop_reason or "unterbrochen"
        complete = all_done and all_pages_authoritative
        completed_ids = {
            str(item["partition_id"])
            for item in partition_refs
            if item.get("complete") and item.get("authoritative")
        }
        incomplete = sorted(set(expected) - completed_ids)
        manifest = writer.publish_root(
            partition_refs,
            expected_partition_ids=expected,
            complete=complete,
            authoritative=complete,
            incomplete_partition_ids=incomplete,
            incomplete_reasons=incomplete_reasons,
            generated_at=str(checkpoint["started_at"]),
        )
        checkpoint["status"] = "complete" if complete else "incomplete"
        checkpoint["updated_at"] = now_utc_iso()
        checkpoint["root_manifest"] = str(manifest)
        checkpoint["complete"] = complete
        checkpoint["stop_reason"] = stop_reason
        self._write_checkpoint(checkpoint)
        blocked_count = sum(len(item["blocked"]) for item in checkpoint["folders"].values())
        return {
            "ok": complete,
            "complete": complete,
            "resumed": resumed,
            "writes_imap": False,
            "writes_provider_flags": False,
            "local_projection_written": True,
            "run_id": checkpoint["run_id"],
            "stop_reason": stop_reason,
            "folder_count": len(folders),
            "blocked_count": blocked_count,
            "checkpoint": str(self.checkpoint_path),
            "manifest": str(manifest),
            "capabilities": capabilities.to_dict(),
            "metrics": dict(metrics),
            "invocation": {
                "pages": invocation_pages,
                "messages": invocation_messages,
                "bytes": invocation_bytes,
            },
        }
