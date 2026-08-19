from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personal_assistant.contracts.mail_projection import (
    PROJECTION_MANIFEST,
    SearchProjectionError,
    load_search_projection,
)
from personal_assistant.contracts.mail_projection_v2 import (
    CONTENT_KIND,
    NORMALIZATION_VERSION,
    OCCURRENCE_KIND,
    PARSER_VERSION,
    PARTITION_KIND,
    PROJECTION_SCHEMA_V2,
    ROOT_KIND,
    TAG_VERSION,
    MailLocator,
    canonical_json_bytes,
    canonical_message_id,
    canonical_partition_generation,
    canonical_root_generation,
    content_identity,
    locator_payload,
    occurrence_identity,
    parse_timestamp,
    require_safe_id,
    require_sha256,
    sha256_bytes,
)

from .models import ParsedMessage
from .utils import atomic_write_bytes, now_utc_iso


@dataclass(frozen=True, slots=True)
class ProjectionOccurrenceInput:
    message: ParsedMessage
    locators: tuple[MailLocator, ...]
    source_status: str = "active"


class PartitionedSearchSnapshotWriter:
    """Stage immutable v2 partitions and publish only through one root replace."""

    def __init__(
        self,
        root: Path,
        *,
        resource_id: str = "mail-agent",
        max_body_chars: int = 200_000,
    ) -> None:
        self.root = root
        self.resource_id = require_safe_id(resource_id, field="resource_id")
        self.max_body_chars = max_body_chars
        root.mkdir(parents=True, exist_ok=True)

    def _write_payload(self, prefix: str, identity: str, payload: dict[str, Any]) -> dict[str, str]:
        data = canonical_json_bytes(payload)
        digest = sha256_bytes(data)
        identity_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        filename = f"{prefix}-{identity_digest}-{digest[:16]}.json"
        atomic_write_bytes(self.root / filename, data)
        return {"filename": filename, "sha256": digest}

    def _content_payload(self, message: ParsedMessage) -> dict[str, Any]:
        raw_sha256 = sha256_bytes(message.raw)
        content_id = content_identity(self.resource_id, raw_sha256)
        message_id = canonical_message_id(message.message_id)
        return {
            "schema": PROJECTION_SCHEMA_V2,
            "kind": CONTENT_KIND,
            "content_id": content_id,
            "resource_id": self.resource_id,
            "raw_sha256": raw_sha256,
            "message_id": message_id,
            "identity_evidence": {
                "version": "mail-identity-v1",
                "method": "resource+raw-sha256",
                "message_id_present": bool(message_id),
            },
            "parser_version": PARSER_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "tag_version": TAG_VERSION,
            "embedding_version": None,
            "subject": message.subject,
            "sender_addr": message.sender_addr,
            "sender_name": message.sender_name,
            "body_text": message.body_text[: self.max_body_chars],
            "in_reply_to": list(message.in_reply_to),
            "references": list(message.references),
            "tags": [],
            "metadata": {
                "date": message.date,
                "received_at": message.received_at or message.date,
                "recipients": list(message.recipients),
                "attachments": [
                    {
                        "filename": item.filename,
                        "content_type": item.content_type,
                        "size": item.size,
                    }
                    for item in message.attachments
                ],
            },
        }

    def _write_content_payload(self, payload: dict[str, Any]) -> dict[str, str]:
        content_id = str(payload.get("content_id") or "")
        expected = content_identity(
            str(payload.get("resource_id") or ""),
            str(payload.get("raw_sha256") or ""),
        )
        if content_id != expected:
            raise SearchProjectionError("Content-ID stimmt vor Publikation nicht")
        reference = self._write_payload("content", content_id, payload)
        return {
            "content_id": content_id,
            "content_filename": reference["filename"],
            "content_sha256": reference["sha256"],
        }

    def _write_occurrence_payload(self, payload: dict[str, Any]) -> dict[str, str]:
        occurrence_id = str(payload.get("occurrence_id") or "")
        reference = self._write_payload("occurrence", occurrence_id, payload)
        return {
            "filename": reference["filename"],
            "sha256": reference["sha256"],
            "occurrence_id": occurrence_id,
            "content_id": str(payload["content_id"]),
        }

    def _occurrence_payload(
        self,
        item: ProjectionOccurrenceInput,
        content_reference: dict[str, str],
        *,
        observed_at: str,
    ) -> dict[str, Any]:
        if not item.locators:
            raise SearchProjectionError("Occurrence benoetigt mindestens einen Locator")
        locators = tuple(item.locators)
        for locator in locators:
            if locator.resource_id != self.resource_id:
                raise SearchProjectionError("Locator gehoert zu einer anderen Ressource")
        raw_sha256 = sha256_bytes(item.message.raw)
        occurrence_id = occurrence_identity(locators[0], raw_sha256)
        return {
            "schema": PROJECTION_SCHEMA_V2,
            "kind": OCCURRENCE_KIND,
            "occurrence_id": occurrence_id,
            "content_id": content_reference["content_id"],
            "resource_id": self.resource_id,
            "raw_sha256": raw_sha256,
            "content_filename": content_reference["content_filename"],
            "content_sha256": content_reference["content_sha256"],
            "source_status": item.source_status,
            "indexed_source_at": observed_at,
            "locators": [locator_payload(locator) for locator in locators],
        }

    def _publish_partition_references(
        self,
        *,
        partition_id: str,
        folder_id: str,
        folder_name: str,
        records: list[dict[str, str]],
        generated_at: str,
        complete: bool,
        authoritative: bool,
        tombstones: list[dict[str, str]],
    ) -> dict[str, Any]:
        partition = require_safe_id(partition_id, field="partition_id")
        stable_folder = require_safe_id(folder_id, field="folder_id")
        parse_timestamp(generated_at, field="partition.generated_at")
        if tombstones and not (complete and authoritative):
            raise SearchProjectionError(
                "Tombstones benoetigen einen vollstaendigen autoritativen Ordnerabgleich"
            )
        normalized_tombstones: list[dict[str, str]] = []
        for item in tombstones:
            occurrence_id = str(item.get("occurrence_id") or "")
            tombstoned_at = str(item.get("tombstoned_at") or "")
            if not occurrence_id or not tombstoned_at:
                raise SearchProjectionError("Tombstone ist unvollstaendig")
            parse_timestamp(tombstoned_at, field="tombstoned_at")
            normalized_tombstones.append(
                {"occurrence_id": occurrence_id, "tombstoned_at": tombstoned_at}
            )
        normalized_records = sorted(
            records,
            key=lambda row: (str(row["occurrence_id"]), str(row["filename"])),
        )
        occurrence_ids = [str(item["occurrence_id"]) for item in normalized_records]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise SearchProjectionError("Partition enthaelt doppelte Occurrences")
        generation = canonical_partition_generation(
            partition_id=partition,
            resource_id=self.resource_id,
            folder_id=stable_folder,
            complete=complete,
            authoritative=authoritative,
            records=normalized_records,
            tombstones=normalized_tombstones,
        )
        payload = {
            "schema": PROJECTION_SCHEMA_V2,
            "kind": PARTITION_KIND,
            "partition_id": partition,
            "resource_id": self.resource_id,
            "folder_id": stable_folder,
            "folder_name": str(folder_name or "")[:1000],
            "generated_at": generated_at,
            "complete": bool(complete),
            "authoritative": bool(authoritative),
            "partition_generation": generation,
            "record_count": len(normalized_records),
            "records": normalized_records,
            "tombstones": normalized_tombstones,
        }
        reference = self._write_payload("partition", partition, payload)
        return {
            "partition_id": partition,
            "filename": reference["filename"],
            "sha256": reference["sha256"],
            "partition_generation": generation,
            "complete": bool(complete),
            "authoritative": bool(authoritative),
        }

    def publish_partition(
        self,
        *,
        partition_id: str,
        folder_id: str,
        folder_name: str,
        occurrences: list[ProjectionOccurrenceInput],
        generated_at: str | None = None,
        complete: bool,
        authoritative: bool,
        tombstones: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        timestamp = generated_at or now_utc_iso()
        records: list[dict[str, str]] = []
        for item in occurrences:
            content_reference = self._write_content_payload(
                self._content_payload(item.message)
            )
            occurrence = self._occurrence_payload(
                item,
                content_reference,
                observed_at=timestamp,
            )
            records.append(self._write_occurrence_payload(occurrence))
        return self._publish_partition_references(
            partition_id=partition_id,
            folder_id=folder_id,
            folder_name=folder_name,
            records=records,
            generated_at=timestamp,
            complete=complete,
            authoritative=authoritative,
            tombstones=list(tombstones or []),
        )

    def publish_root(
        self,
        partitions: list[dict[str, Any]],
        *,
        expected_partition_ids: list[str],
        complete: bool,
        authoritative: bool,
        incomplete_partition_ids: list[str] | None = None,
        incomplete_reasons: dict[str, str] | None = None,
        generated_at: str | None = None,
    ) -> Path:
        timestamp = generated_at or now_utc_iso()
        parse_timestamp(timestamp, field="root.generated_at")
        actual = {str(item.get("partition_id") or "") for item in partitions}
        if len(actual) != len(partitions):
            raise SearchProjectionError("Root enthaelt doppelte Partitionen")
        expected = {
            require_safe_id(item, field="expected_partition_id")
            for item in expected_partition_ids
        }
        incomplete = {
            require_safe_id(item, field="incomplete_partition_id")
            for item in (incomplete_partition_ids or [])
        }
        partition_complete = {
            str(item.get("partition_id") or "")
            for item in partitions
            if bool(item.get("complete")) and bool(item.get("authoritative"))
        }
        if not actual.issubset(expected) or incomplete != expected - partition_complete:
            raise SearchProjectionError(
                "Coverage muss vollstaendige, partielle und fehlende Partitionen "
                "exakt ausweisen"
            )
        complete_ids = sorted(partition_complete)
        if complete and not (
            authoritative
            and partition_complete == actual
            and expected == actual == set(complete_ids)
            and not incomplete
        ):
            raise SearchProjectionError(
                "Globale Vollstaendigkeit benoetigt alle autoritativen Partitionen"
            )
        coverage = {
            "resource_id": self.resource_id,
            "authoritative": bool(authoritative),
            "expected_partition_ids": sorted(expected),
            "complete_partition_ids": complete_ids,
            "incomplete_partition_ids": sorted(incomplete),
            "incomplete_reasons": {
                key: str(value)[:500]
                for key, value in sorted((incomplete_reasons or {}).items())
                if key in incomplete
            },
        }
        normalized = sorted(
            [
                {
                    "partition_id": str(item["partition_id"]),
                    "filename": str(item["filename"]),
                    "sha256": str(item["sha256"]),
                    "partition_generation": str(item["partition_generation"]),
                }
                for item in partitions
            ],
            key=lambda row: (str(row["partition_id"]), str(row["filename"])),
        )
        generation = canonical_root_generation(
            complete=complete,
            coverage=coverage,
            partitions=normalized,
        )
        payload = {
            "schema": PROJECTION_SCHEMA_V2,
            "kind": ROOT_KIND,
            "generated_at": timestamp,
            "root_generation": generation,
            "complete": bool(complete),
            "coverage": coverage,
            "partition_count": len(normalized),
            "partitions": normalized,
        }
        path = self.root / PROJECTION_MANIFEST
        atomic_write_bytes(path, canonical_json_bytes(payload))
        return path


def republish_v1_projection(
    source_root: Path,
    target_root: Path,
    *,
    resource_id: str = "mail-agent",
) -> Path:
    """Republish v1 into a separate incomplete v2 staging generation."""

    if source_root.resolve() == target_root.resolve():
        raise ValueError("v1-Republication benoetigt ein separates Staging-Ziel")
    source = load_search_projection(source_root)
    if source.schema != 1:
        raise ValueError("Republication akzeptiert ausschliesslich Projektion v1")
    writer = PartitionedSearchSnapshotWriter(target_root, resource_id=resource_id)
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, str]]]] = {}
    for _path, legacy in source.records:
        raw_sha256 = require_sha256(legacy.get("sha256"), field="v1.sha256")
        content_id = content_identity(resource_id, raw_sha256)
        message_id = canonical_message_id(legacy.get("message_id"))
        content_payload = {
            "schema": PROJECTION_SCHEMA_V2,
            "kind": CONTENT_KIND,
            "content_id": content_id,
            "resource_id": resource_id,
            "raw_sha256": raw_sha256,
            "message_id": message_id,
            "identity_evidence": {
                "version": "mail-identity-v1",
                "method": "v1-resource+raw-sha256",
                "message_id_present": bool(message_id),
                "v1_stable_key": str(legacy["stable_key"]),
            },
            "parser_version": "mail-parser-v1-legacy-projection",
            "normalization_version": NORMALIZATION_VERSION,
            "tag_version": TAG_VERSION,
            "embedding_version": None,
            "subject": str(legacy.get("subject") or ""),
            "sender_addr": str(legacy.get("sender_addr") or ""),
            "sender_name": str(legacy.get("sender_name") or ""),
            "body_text": str(legacy.get("body_text") or ""),
            "in_reply_to": [],
            "references": [],
            "tags": [],
            "metadata": dict(legacy.get("metadata") or {}),
        }
        content_reference = writer._write_content_payload(content_payload)
        metadata = dict(legacy.get("metadata") or {})
        folder_name = str(metadata.get("source_folder") or "legacy-unknown")
        folder_digest = hashlib.sha256(folder_name.encode("utf-8")).hexdigest()[:24]
        folder_id = f"legacy-folder:{folder_digest}"
        locator = MailLocator(
            resource_id=resource_id,
            folder_id=folder_id,
            folder_name=folder_name,
            mailbox_id=str(legacy["stable_key"]),
            observed_at=str(legacy["indexed_source_at"]),
            quarantine=False,
        )
        occurrence_payload = {
            "schema": PROJECTION_SCHEMA_V2,
            "kind": OCCURRENCE_KIND,
            "occurrence_id": occurrence_identity(locator, raw_sha256),
            "content_id": content_id,
            "resource_id": resource_id,
            "raw_sha256": raw_sha256,
            "content_filename": content_reference["content_filename"],
            "content_sha256": content_reference["content_sha256"],
            "source_status": "legacy-unverified-locator",
            "indexed_source_at": str(legacy["indexed_source_at"]),
            "locators": [locator_payload(locator)],
        }
        occurrence_reference = writer._write_occurrence_payload(occurrence_payload)
        grouped.setdefault(folder_id, []).append((metadata, occurrence_reference))
    partition_references: list[dict[str, Any]] = []
    for folder_id in sorted(grouped):
        rows = grouped[folder_id]
        folder_name = str(rows[0][0].get("source_folder") or "legacy-unknown")
        partition_references.append(
            writer._publish_partition_references(
                partition_id=f"legacy:{folder_id.rsplit(':', 1)[-1]}",
                folder_id=folder_id,
                folder_name=folder_name,
                records=[item[1] for item in rows],
                generated_at=source.generated_at,
                complete=False,
                authoritative=False,
                tombstones=[],
            )
        )
    partition_ids = [str(item["partition_id"]) for item in partition_references]
    return writer.publish_root(
        partition_references,
        expected_partition_ids=partition_ids,
        complete=False,
        authoritative=False,
        incomplete_partition_ids=partition_ids,
        incomplete_reasons={
            item: "v1 enthaelt keinen autoritativen Vollkonto-/Locatornachweis"
            for item in partition_ids
        },
        generated_at=source.generated_at,
    )
