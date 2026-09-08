from __future__ import annotations

import hashlib
import json
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .search_backfill import CHECKPOINT_SCHEMA, physical_attachments


class QuarantineClient(Protocol):
    def list_folders(self) -> tuple[list[str], str]: ...

    def export_message(self, folder: str, message_id: str, destination: Path) -> Any: ...

    def move_message(self, source: str, destination: str, message_id: str) -> Any: ...


class QuarantineScanner(Protocol):
    def scanner_identity(self, *, refresh: bool = False) -> str: ...

    def scan_bytes(
        self,
        data: bytes,
        *,
        name: str,
        source_type: str,
        use_cache: bool = True,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class BlockedCandidate:
    candidate_id: str
    run_id: str
    folder_id: str
    source: str
    message_id: str
    raw_sha256: str
    status: str

    @property
    def quarantine_eligible(self) -> bool:
        return self.status == "infected"


def _read_checkpoint(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Mail-Backfill-Checkpoint fehlt: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Mail-Backfill-Checkpoint ist ungueltig: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("Mail-Backfill-Checkpointvertrag ist ungueltig")
    if not str(payload.get("run_id") or ""):
        raise ValueError("Mail-Backfill-Checkpoint besitzt keine run_id")
    if not isinstance(payload.get("folders"), dict):
        raise ValueError("Mail-Backfill-Checkpoint besitzt keine Ordnerzustaende")
    return payload


def _candidate_id(
    run_id: str,
    folder_id: str,
    source: str,
    message_id: str,
    raw_sha256: str,
    status: str,
) -> str:
    material = "\0".join((run_id, folder_id, source, message_id, raw_sha256, status))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def blocked_candidates(path: Path) -> tuple[dict[str, Any], list[BlockedCandidate]]:
    checkpoint = _read_checkpoint(path)
    run_id = str(checkpoint["run_id"])
    candidates: list[BlockedCandidate] = []
    for folder_id, raw_state in sorted(checkpoint["folders"].items()):
        if not isinstance(raw_state, dict):
            raise ValueError("Mail-Backfill-Checkpoint enthaelt ungueltigen Ordnerzustand")
        source = str(raw_state.get("name") or "").strip()
        if not source:
            raise ValueError("Mail-Backfill-Checkpoint enthaelt einen namenlosen Ordner")
        blocked = raw_state.get("blocked") or []
        if not isinstance(blocked, list):
            raise ValueError("Mail-Backfill-Checkpoint enthaelt ungueltige Blockaden")
        for raw_item in blocked:
            if not isinstance(raw_item, dict):
                raise ValueError("Mail-Backfill-Checkpoint enthaelt ungueltige Blockade")
            message_id = str(raw_item.get("mailbox_id") or "").strip()
            raw_sha256 = str(raw_item.get("sha256") or "").strip().casefold()
            status = str(raw_item.get("status") or "").strip().casefold()
            if not message_id or len(raw_sha256) != 64:
                raise ValueError("Mail-Backfill-Checkpoint enthaelt unvollstaendige Blockade")
            try:
                bytes.fromhex(raw_sha256)
            except ValueError as exc:
                raise ValueError(
                    "Mail-Backfill-Checkpoint enthaelt ungueltigen SHA-256"
                ) from exc
            candidates.append(
                BlockedCandidate(
                    candidate_id=_candidate_id(
                        run_id,
                        str(folder_id),
                        source,
                        message_id,
                        raw_sha256,
                        status,
                    ),
                    run_id=run_id,
                    folder_id=str(folder_id),
                    source=source,
                    message_id=message_id,
                    raw_sha256=raw_sha256,
                    status=status,
                )
            )
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise ValueError("Mail-Backfill-Checkpoint enthaelt doppelte Blockaden")
    return checkpoint, candidates


def blocked_report(path: Path, *, malware_folder: str, limit: int = 100) -> dict[str, Any]:
    checkpoint, candidates = blocked_candidates(path)
    safe_limit = max(1, min(int(limit), 500))
    selected = candidates[:safe_limit]
    rows = []
    for item in selected:
        command = [
            "./scripts/assistant.sh",
            "mail",
            "index",
            "quarantine",
            "--candidate-id",
            item.candidate_id,
            "--expected-source",
            item.source,
            "--expected-message-id",
            item.message_id,
            "--expected-sha256",
            item.raw_sha256,
            "--yes",
        ]
        rows.append(
            {
                "candidate_id": item.candidate_id,
                "source": item.source,
                "message_id": item.message_id,
                "raw_sha256": item.raw_sha256,
                "status": item.status,
                "quarantine_eligible": item.quarantine_eligible,
                "destination": malware_folder if item.quarantine_eligible else "",
                "approval_required": item.quarantine_eligible,
                "approval": (
                    "explicit-user-single-infected-mail-quarantine"
                    if item.quarantine_eligible
                    else "not-applicable"
                ),
                "approval_command": " ".join(shlex.quote(value) for value in command),
            }
        )
    return {
        "ok": True,
        "read_only": True,
        "content_exposed": False,
        "run_id": str(checkpoint["run_id"]),
        "checkpoint_status": str(checkpoint.get("status") or ""),
        "complete": bool(checkpoint.get("complete")),
        "blocked_count": len(candidates),
        "infected_count": sum(item.quarantine_eligible for item in candidates),
        "count": len(rows),
        "limit": safe_limit,
        "results_may_be_truncated": len(candidates) > safe_limit,
        "candidates": rows,
        "writes_imap": False,
        "writes_local_index": False,
    }


def select_candidate(
    path: Path,
    *,
    candidate_id: str,
    expected_source: str,
    expected_message_id: str,
    expected_sha256: str,
) -> BlockedCandidate:
    _checkpoint, candidates = blocked_candidates(path)
    matches = [item for item in candidates if item.candidate_id == candidate_id]
    if len(matches) != 1:
        raise ValueError("Quarantaene-Kandidat wurde nicht eindeutig im aktuellen Checkpoint gefunden")
    candidate = matches[0]
    if (
        candidate.source != expected_source
        or candidate.message_id != expected_message_id
        or candidate.raw_sha256 != expected_sha256.casefold()
    ):
        raise PermissionError("Quarantaene-Erwartungswerte stimmen nicht mit dem Checkpoint ueberein")
    if not candidate.quarantine_eligible:
        raise PermissionError("Nur bestaetigte Antivirus-Funde duerfen quarantänisiert werden")
    return candidate


def verify_and_move_candidate(
    candidate: BlockedCandidate,
    *,
    destination: str,
    client: QuarantineClient,
    scanner: QuarantineScanner,
) -> dict[str, Any]:
    scanner_identity = scanner.scanner_identity(refresh=False)
    folders, error = client.list_folders()
    if error:
        raise RuntimeError(error)
    folder_map = {
        str(folder).strip().casefold(): str(folder).strip()
        for folder in folders
        if str(folder).strip()
    }
    source_real = folder_map.get(candidate.source.casefold())
    destination_real = folder_map.get(destination.strip().casefold())
    if source_real != candidate.source:
        raise RuntimeError("Quellordner des Quarantaene-Kandidaten ist nicht mehr exakt vorhanden")
    if not destination_real:
        raise RuntimeError("Konfigurierter Malware-Quarantaeneordner fehlt")
    if source_real.casefold() == destination_real.casefold():
        raise PermissionError("Mail befindet sich bereits im Malware-Quarantaeneordner")

    with tempfile.TemporaryDirectory(prefix="openclaw-index-quarantine-") as temp:
        os.chmod(temp, 0o700)
        target = Path(temp) / "message.eml"
        exported = client.export_message(source_real, candidate.message_id, target)
        if not bool(getattr(exported, "ok", False)) or not target.is_file():
            raise RuntimeError(
                str(getattr(exported, "detail", "")) or "Mail konnte nicht erneut exportiert werden"
            )
        os.chmod(target, 0o600)
        raw = target.read_bytes()

    current_sha256 = hashlib.sha256(raw).hexdigest()
    if current_sha256 != candidate.raw_sha256:
        raise PermissionError("Mailinhalt hat sich seit dem Indexlauf geaendert; Quarantaene abgebrochen")

    scan_results = [
        scanner.scan_bytes(
            raw,
            name=f"{candidate.message_id}.eml",
            source_type="mail-index-quarantine-raw",
            use_cache=False,
        )
    ]
    attachments = physical_attachments(raw)
    for index, (_name, payload) in enumerate(attachments, start=1):
        scan_results.append(
            scanner.scan_bytes(
                payload,
                name=f"attachment-{index}.bin",
                source_type="mail-index-quarantine-attachment",
                use_cache=False,
            )
        )
    statuses = [str(getattr(item, "status", "error")) for item in scan_results]
    if any(status not in {"clean", "infected"} for status in statuses):
        raise RuntimeError("Erneuter Antivirus-Scan war nicht eindeutig erfolgreich")
    infected_count = sum(status == "infected" for status in statuses)
    if infected_count == 0:
        raise PermissionError("Erneuter Antivirus-Scan bestaetigt keinen Fund; Quarantaene abgebrochen")

    moved = client.move_message(source_real, destination_real, candidate.message_id)
    return {
        "ok": bool(getattr(moved, "ok", False)),
        "detail": str(getattr(moved, "detail", "")),
        "source": source_real,
        "destination": destination_real,
        "message_id": candidate.message_id,
        "raw_sha256": current_sha256,
        "scanner_identity": scanner_identity,
        "scan_objects": len(scan_results),
        "infected_scan_objects": infected_count,
        "content_exposed": False,
        "writes_imap": bool(getattr(moved, "ok", False)),
    }
