from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .mail_projection_types import (
    PROJECTION_MANIFEST,
    PROJECTION_SCHEMA_V2,
    SearchProjection,
    SearchProjectionError,
)

CONTENT_KIND = "mail-content"
OCCURRENCE_KIND = "mail-occurrence"
PARTITION_KIND = "mail-projection-partition"
ROOT_KIND = "mail-projection-root"
IDENTITY_VERSION = "mail-identity-v1"
PARSER_VERSION = "mail-parser-v1"
NORMALIZATION_VERSION = "mail-normalization-v1"
TAG_VERSION = "mail-tags-v1"

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.json$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_MESSAGE_ID = re.compile(r"<([^<>\r\n]{1,998})>")


@dataclass(frozen=True, slots=True)
class MailLocator:
    resource_id: str
    folder_id: str
    folder_name: str
    mailbox_id: str = ""
    uidvalidity: str = ""
    uid: str = ""
    observed_at: str = ""
    quarantine: bool = False


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_sha256(value: object, *, field: str) -> str:
    digest = str(value or "").strip().casefold()
    if not _HEX_64.fullmatch(digest):
        raise SearchProjectionError(f"Ungueltiger SHA-256-Wert fuer {field}")
    return digest


def require_safe_id(value: object, *, field: str) -> str:
    identifier = str(value or "").strip()
    if not _SAFE_ID.fullmatch(identifier):
        raise SearchProjectionError(f"Ungueltige ID fuer {field}")
    return identifier


def safe_projection_filename(value: object, *, field: str = "filename") -> str:
    filename = str(value or "").strip()
    if (
        not _SAFE_FILENAME.fullmatch(filename)
        or Path(filename).name != filename
        or filename == PROJECTION_MANIFEST
    ):
        raise SearchProjectionError(
            f"Projektionsvertrag enthaelt einen unsicheren Dateinamen fuer {field}"
        )
    return filename


def parse_timestamp(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SearchProjectionError(f"Ungueltiger Zeitpunkt fuer {field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def canonical_message_id(value: object) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    match = _MESSAGE_ID.search(text)
    candidate = match.group(1) if match else text.strip("<>")
    candidate = "".join(candidate.split())
    return candidate[:998] if "@" in candidate else ""


def content_identity(resource_id: str, raw_sha256: str) -> str:
    resource = require_safe_id(resource_id, field="resource_id")
    raw_digest = require_sha256(raw_sha256, field="raw_sha256")
    material = f"{IDENTITY_VERSION}\0content\0{resource}\0{raw_digest}"
    return "content:" + sha256_bytes(material.encode("utf-8"))


def locator_identity(locator: MailLocator) -> str:
    resource = require_safe_id(locator.resource_id, field="locator.resource_id")
    folder_id = require_safe_id(locator.folder_id, field="locator.folder_id")
    uidvalidity = str(locator.uidvalidity or "").strip()
    uid = str(locator.uid or "").strip()
    mailbox_id = str(locator.mailbox_id or "").strip()
    if bool(uidvalidity) != bool(uid):
        raise SearchProjectionError("UIDVALIDITY und UID muessen gemeinsam gesetzt sein")
    if uidvalidity and uid:
        connector_key = f"uid\0{uidvalidity}\0{uid}"
    elif mailbox_id:
        connector_key = f"mailbox\0{mailbox_id}"
    else:
        raise SearchProjectionError("Locator benoetigt UID oder mailbox_id")
    material = (
        f"{IDENTITY_VERSION}\0locator\0{resource}\0{folder_id}\0{connector_key}"
    )
    return "locator:" + sha256_bytes(material.encode("utf-8"))


def occurrence_identity(locator: MailLocator, raw_sha256: str) -> str:
    raw_digest = require_sha256(raw_sha256, field="raw_sha256")
    locator_id = locator_identity(locator)
    has_stable_uid = bool(str(locator.uidvalidity or "").strip())
    suffix = "" if has_stable_uid else f"\0{raw_digest}"
    material = f"{IDENTITY_VERSION}\0occurrence\0{locator_id}{suffix}"
    return "occurrence:" + sha256_bytes(material.encode("utf-8"))


def locator_payload(locator: MailLocator) -> dict[str, Any]:
    payload = asdict(locator)
    payload["locator_id"] = locator_identity(locator)
    payload["folder_name"] = str(locator.folder_name or "")[:1000]
    payload["mailbox_id"] = str(locator.mailbox_id or "")[:1000]
    payload["uidvalidity"] = str(locator.uidvalidity or "")[:80]
    payload["uid"] = str(locator.uid or "")[:80]
    if payload["observed_at"]:
        parse_timestamp(payload["observed_at"], field="locator.observed_at")
    return payload


def canonical_partition_generation(
    *,
    partition_id: str,
    resource_id: str,
    folder_id: str,
    complete: bool,
    authoritative: bool,
    records: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
) -> str:
    material = {
        "partition_id": partition_id,
        "resource_id": resource_id,
        "folder_id": folder_id,
        "complete": bool(complete),
        "authoritative": bool(authoritative),
        "records": sorted(
            records,
            key=lambda item: (str(item["occurrence_id"]), str(item["filename"])),
        ),
        "tombstones": sorted(
            tombstones,
            key=lambda item: (str(item["occurrence_id"]), str(item["tombstoned_at"])),
        ),
    }
    return sha256_bytes(canonical_json_bytes(material))


def canonical_root_generation(
    *,
    complete: bool,
    coverage: dict[str, Any],
    partitions: list[dict[str, Any]],
) -> str:
    material = {
        "complete": bool(complete),
        "coverage": coverage,
        "partitions": sorted(
            partitions,
            key=lambda item: (str(item["partition_id"]), str(item["filename"])),
        ),
    }
    return sha256_bytes(canonical_json_bytes(material))


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError as exc:
        raise SearchProjectionError(f"Fehlende {label}: {path.name}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SearchProjectionError(f"Ungueltige {label} {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SearchProjectionError(f"Ungueltige {label}: {path.name}")
    return payload, raw


def _validate_content(
    root: Path,
    reference: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    filename = safe_projection_filename(reference.get("content_filename"), field="content")
    path = root / filename
    payload, raw = _read_json(path, label="Contentdatei")
    if payload.get("schema") != PROJECTION_SCHEMA_V2 or payload.get("kind") != CONTENT_KIND:
        raise SearchProjectionError(f"Ungueltiger Contentvertrag in {filename}")
    expected_digest = require_sha256(reference.get("content_sha256"), field="content_sha256")
    if sha256_bytes(raw) != expected_digest:
        raise SearchProjectionError(f"Pruefsumme stimmt fuer {filename} nicht")
    resource_id = require_safe_id(payload.get("resource_id"), field="content.resource_id")
    raw_sha256 = require_sha256(payload.get("raw_sha256"), field="content.raw_sha256")
    content_id = str(payload.get("content_id") or "")
    if content_id != content_identity(resource_id, raw_sha256):
        raise SearchProjectionError(f"Content-ID stimmt fuer {filename} nicht")
    if str(reference.get("content_id") or "") != content_id:
        raise SearchProjectionError(f"Contentreferenz stimmt fuer {filename} nicht")
    if canonical_message_id(payload.get("message_id")) != str(payload.get("message_id") or ""):
        raise SearchProjectionError(f"Message-ID ist in {filename} nicht kanonisch")
    for field in ("parser_version", "normalization_version", "tag_version"):
        if not str(payload.get(field) or "").strip():
            raise SearchProjectionError(f"Contentvertrag {filename} enthaelt kein {field}")
    return path, payload


def _validate_occurrence(
    root: Path,
    reference: dict[str, Any],
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    filename = safe_projection_filename(reference.get("filename"), field="occurrence")
    path = root / filename
    payload, raw = _read_json(path, label="Occurrence-Datei")
    if (
        payload.get("schema") != PROJECTION_SCHEMA_V2
        or payload.get("kind") != OCCURRENCE_KIND
    ):
        raise SearchProjectionError(f"Ungueltiger Occurrence-Vertrag in {filename}")
    expected_digest = require_sha256(reference.get("sha256"), field="occurrence.sha256")
    if sha256_bytes(raw) != expected_digest:
        raise SearchProjectionError(f"Pruefsumme stimmt fuer {filename} nicht")
    occurrence_id = str(payload.get("occurrence_id") or "")
    if occurrence_id != str(reference.get("occurrence_id") or ""):
        raise SearchProjectionError(f"Occurrence-ID stimmt fuer {filename} nicht")
    resource_id = require_safe_id(payload.get("resource_id"), field="occurrence.resource_id")
    raw_sha256 = require_sha256(payload.get("raw_sha256"), field="occurrence.raw_sha256")
    locators = payload.get("locators")
    if not isinstance(locators, list) or not locators:
        raise SearchProjectionError(f"Occurrence {filename} besitzt keinen Locator")
    seen_locators: set[str] = set()
    expected_occurrence = ""
    for index, raw_locator in enumerate(locators):
        if not isinstance(raw_locator, dict):
            raise SearchProjectionError(f"Occurrence {filename} enthaelt ungueltigen Locator")
        locator = MailLocator(
            resource_id=str(raw_locator.get("resource_id") or ""),
            folder_id=str(raw_locator.get("folder_id") or ""),
            folder_name=str(raw_locator.get("folder_name") or ""),
            mailbox_id=str(raw_locator.get("mailbox_id") or ""),
            uidvalidity=str(raw_locator.get("uidvalidity") or ""),
            uid=str(raw_locator.get("uid") or ""),
            observed_at=str(raw_locator.get("observed_at") or ""),
            quarantine=bool(raw_locator.get("quarantine")),
        )
        if locator.resource_id != resource_id:
            raise SearchProjectionError(f"Locator-Ressource stimmt in {filename} nicht")
        locator_id = locator_identity(locator)
        if locator_id != str(raw_locator.get("locator_id") or ""):
            raise SearchProjectionError(f"Locator-ID stimmt in {filename} nicht")
        if locator_id in seen_locators:
            raise SearchProjectionError(f"Doppelter Locator in {filename}")
        seen_locators.add(locator_id)
        if index == 0:
            expected_occurrence = occurrence_identity(locator, raw_sha256)
    if occurrence_id != expected_occurrence:
        raise SearchProjectionError(f"Occurrence-Identitaet stimmt fuer {filename} nicht")
    parse_timestamp(payload.get("indexed_source_at"), field="indexed_source_at")
    content_path, content = _validate_content(root, payload)
    return path, payload, content_path, content


def _validate_partition(
    root: Path,
    reference: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[Path, dict[str, Any], Path, dict[str, Any]]]]:
    filename = safe_projection_filename(reference.get("filename"), field="partition")
    payload, raw = _read_json(root / filename, label="Partitionsmanifest")
    if payload.get("schema") != PROJECTION_SCHEMA_V2 or payload.get("kind") != PARTITION_KIND:
        raise SearchProjectionError(f"Ungueltiger Partitionsvertrag in {filename}")
    expected_digest = require_sha256(reference.get("sha256"), field="partition.sha256")
    if sha256_bytes(raw) != expected_digest:
        raise SearchProjectionError(f"Pruefsumme stimmt fuer {filename} nicht")
    partition_id = require_safe_id(payload.get("partition_id"), field="partition_id")
    if partition_id != str(reference.get("partition_id") or ""):
        raise SearchProjectionError(f"Partitions-ID stimmt fuer {filename} nicht")
    require_safe_id(payload.get("resource_id"), field="partition.resource_id")
    require_safe_id(payload.get("folder_id"), field="partition.folder_id")
    parse_timestamp(payload.get("generated_at"), field="partition.generated_at")
    records = payload.get("records")
    tombstones = payload.get("tombstones")
    if not isinstance(records, list) or int(payload.get("record_count") or 0) != len(records):
        raise SearchProjectionError(f"Partitionsmanifest {filename} ist unvollstaendig")
    if not isinstance(tombstones, list):
        raise SearchProjectionError(f"Partitionsmanifest {filename} hat ungueltige Tombstones")
    complete = bool(payload.get("complete"))
    authoritative = bool(payload.get("authoritative"))
    if tombstones and not (complete and authoritative):
        raise SearchProjectionError(
            f"Tombstones in {filename} benoetigen vollstaendigen autoritativen Abgleich"
        )
    normalized_tombstones: list[dict[str, Any]] = []
    for tombstone in tombstones:
        if not isinstance(tombstone, dict):
            raise SearchProjectionError(f"Ungueltiger Tombstone in {filename}")
        occurrence_id = str(tombstone.get("occurrence_id") or "")
        tombstoned_at = str(tombstone.get("tombstoned_at") or "")
        if not occurrence_id or not tombstoned_at:
            raise SearchProjectionError(f"Unvollstaendiger Tombstone in {filename}")
        parse_timestamp(tombstoned_at, field="tombstoned_at")
        normalized_tombstones.append(
            {"occurrence_id": occurrence_id, "tombstoned_at": tombstoned_at}
        )
    loaded: list[tuple[Path, dict[str, Any], Path, dict[str, Any]]] = []
    seen_occurrences: set[str] = set()
    normalized_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise SearchProjectionError(f"Ungueltiger Occurrence-Eintrag in {filename}")
        occurrence_id = str(record.get("occurrence_id") or "")
        if occurrence_id in seen_occurrences:
            raise SearchProjectionError(f"Doppelte Occurrence in {filename}")
        seen_occurrences.add(occurrence_id)
        occurrence = _validate_occurrence(root, record)
        if str(record.get("content_id") or "") != str(occurrence[1]["content_id"]):
            raise SearchProjectionError(f"Content-ID stimmt im Eintrag {filename} nicht")
        loaded.append(occurrence)
        normalized_records.append(
            {
                "filename": str(record.get("filename") or ""),
                "occurrence_id": occurrence_id,
                "content_id": str(record.get("content_id") or ""),
                "sha256": str(record.get("sha256") or ""),
            }
        )
    generation = canonical_partition_generation(
        partition_id=partition_id,
        resource_id=str(payload["resource_id"]),
        folder_id=str(payload["folder_id"]),
        complete=complete,
        authoritative=authoritative,
        records=normalized_records,
        tombstones=normalized_tombstones,
    )
    if generation != str(payload.get("partition_generation") or ""):
        raise SearchProjectionError(f"Partitionsgeneration stimmt fuer {filename} nicht")
    if generation != str(reference.get("partition_generation") or ""):
        raise SearchProjectionError(f"Partitionsreferenz stimmt fuer {filename} nicht")
    return payload, loaded


def _flatten_records(
    occurrences: list[tuple[Path, dict[str, Any], Path, dict[str, Any]]],
) -> tuple[tuple[Path, dict[str, Any]], ...]:
    grouped: dict[str, dict[str, Any]] = {}
    content_paths: dict[str, Path] = {}
    content_contracts: dict[str, bytes] = {}
    for _occurrence_path, occurrence, content_path, content in occurrences:
        content_id = str(content["content_id"])
        current = grouped.get(content_id)
        if current is None:
            current = {
                "schema": PROJECTION_SCHEMA_V2,
                "stable_key": content_id,
                "content_id": content_id,
                "message_id": str(content.get("message_id") or ""),
                "subject": str(content.get("subject") or ""),
                "sender_addr": str(content.get("sender_addr") or ""),
                "sender_name": str(content.get("sender_name") or ""),
                "body_text": str(content.get("body_text") or ""),
                "sha256": str(content.get("raw_sha256") or ""),
                "indexed_source_at": str(occurrence.get("indexed_source_at") or ""),
                "metadata": {
                    **dict(content.get("metadata") or {}),
                    "content_id": content_id,
                    "occurrence_ids": [],
                    "locators": [],
                    "source_status": str(occurrence.get("source_status") or "active"),
                    "embedding_version": content.get("embedding_version"),
                    "parser_version": content.get("parser_version"),
                    "normalization_version": content.get("normalization_version"),
                    "tag_version": content.get("tag_version"),
                    "in_reply_to": list(content.get("in_reply_to") or []),
                    "references": list(content.get("references") or []),
                },
            }
            grouped[content_id] = current
            content_paths[content_id] = content_path
            content_contracts[content_id] = canonical_json_bytes(content)
        elif canonical_json_bytes(content) != content_contracts[content_id]:
            raise SearchProjectionError(f"Widerspruechliche Contentdaten fuer {content_id}")
        metadata = current["metadata"]
        metadata["occurrence_ids"].append(str(occurrence["occurrence_id"]))
        metadata["locators"].extend(list(occurrence["locators"]))
        if str(occurrence.get("indexed_source_at") or "") > str(
            current["indexed_source_at"]
        ):
            current["indexed_source_at"] = str(occurrence["indexed_source_at"])
    flattened: list[tuple[Path, dict[str, Any]]] = []
    for content_id in sorted(grouped):
        payload = grouped[content_id]
        metadata = payload["metadata"]
        metadata["occurrence_ids"] = sorted(set(metadata["occurrence_ids"]))
        locators = {str(item["locator_id"]): item for item in metadata["locators"]}
        metadata["locators"] = [locators[key] for key in sorted(locators)]
        if metadata["locators"]:
            metadata["source_folder"] = str(metadata["locators"][0]["folder_name"])
        flattened.append((content_paths[content_id], payload))
    return tuple(flattened)


def load_search_projection_v2(
    root: Path,
    manifest: dict[str, Any],
    *,
    max_age_seconds: int | None = None,
    now: datetime | None = None,
) -> SearchProjection:
    if manifest.get("kind") != ROOT_KIND:
        raise SearchProjectionError("Projektionsmanifest v2 hat einen ungueltigen Typ")
    generated_at = str(manifest.get("generated_at") or "")
    generated = parse_timestamp(generated_at, field="root.generated_at")
    raw_partitions = manifest.get("partitions")
    if (
        not isinstance(raw_partitions, list)
        or int(manifest.get("partition_count") or 0) != len(raw_partitions)
    ):
        raise SearchProjectionError("Projektionsmanifest v2 ist unvollstaendig")
    normalized_references: list[dict[str, Any]] = []
    partition_payloads: list[dict[str, Any]] = []
    all_occurrences: list[tuple[Path, dict[str, Any], Path, dict[str, Any]]] = []
    seen_partitions: set[str] = set()
    seen_files: set[str] = set()
    seen_occurrences: set[str] = set()
    for reference in raw_partitions:
        if not isinstance(reference, dict):
            raise SearchProjectionError("Projektionsmanifest v2 hat ungueltige Partition")
        partition_id = str(reference.get("partition_id") or "")
        filename = safe_projection_filename(reference.get("filename"), field="partition")
        if partition_id in seen_partitions or filename in seen_files:
            raise SearchProjectionError("Projektionsmanifest v2 hat doppelte Partitionen")
        seen_partitions.add(partition_id)
        seen_files.add(filename)
        partition, occurrences = _validate_partition(root, reference)
        for _path, occurrence, _content_path, _content in occurrences:
            occurrence_id = str(occurrence["occurrence_id"])
            if occurrence_id in seen_occurrences:
                raise SearchProjectionError(
                    "Projektionsmanifest v2 enthaelt eine Occurrence mehrfach"
                )
            seen_occurrences.add(occurrence_id)
        all_occurrences.extend(occurrences)
        partition_payloads.append(partition)
        normalized_references.append(
            {
                "partition_id": partition_id,
                "filename": filename,
                "sha256": str(reference.get("sha256") or ""),
                "partition_generation": str(reference.get("partition_generation") or ""),
            }
        )
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        raise SearchProjectionError("Projektionsmanifest v2 hat keinen Coverage-Vertrag")
    expected = {str(item) for item in coverage.get("expected_partition_ids", [])}
    completed = {str(item) for item in coverage.get("complete_partition_ids", [])}
    incomplete = {str(item) for item in coverage.get("incomplete_partition_ids", [])}
    actual = set(seen_partitions)
    complete = bool(manifest.get("complete"))
    authoritative = bool(coverage.get("authoritative"))
    partition_complete = {
        str(item["partition_id"])
        for item in partition_payloads
        if bool(item.get("complete")) and bool(item.get("authoritative"))
    }
    if (
        not actual.issubset(expected)
        or completed != partition_complete
        or completed & incomplete
        or incomplete != expected - completed
    ):
        raise SearchProjectionError(
            "Coverage weist vollstaendige, partielle oder fehlende Partitionen "
            "nicht exakt aus"
        )
    if complete and not (
        authoritative
        and expected == actual == completed == partition_complete
        and not incomplete
    ):
        raise SearchProjectionError(
            "Globale Vollstaendigkeit ist ohne alle autoritativen Partitionen unzulaessig"
        )
    generation = canonical_root_generation(
        complete=complete,
        coverage=coverage,
        partitions=normalized_references,
    )
    if generation != str(manifest.get("root_generation") or ""):
        raise SearchProjectionError("Root-Generation der Mail-Suchprojektion stimmt nicht")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    age_seconds = max(0, int((current - generated).total_seconds()))
    if max_age_seconds is not None and age_seconds > max(0, int(max_age_seconds)):
        raise SearchProjectionError(
            f"Mail-Suchprojektion ist veraltet ({age_seconds}s > {int(max_age_seconds)}s)"
        )
    return SearchProjection(
        generation=generation,
        generated_at=generated_at,
        age_seconds=age_seconds,
        records=_flatten_records(all_occurrences),
        schema=PROJECTION_SCHEMA_V2,
        complete=complete,
        coverage=dict(coverage),
        partitions=tuple(dict(item) for item in normalized_references),
    )
