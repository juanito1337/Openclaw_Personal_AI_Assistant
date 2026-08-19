from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .mail_projection_types import (
    PROJECTION_MANIFEST,
    PROJECTION_SCHEMA,
    PROJECTION_SCHEMA_V2,
    SearchProjection,
    SearchProjectionError,
)


def canonical_projection_generation(records: list[dict[str, Any]]) -> str:
    source = "\n".join(
        f"{item['stable_key']}\0{item['filename']}\0{item['sha256']}"
        for item in sorted(records, key=lambda row: (row["stable_key"], row["filename"]))
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_projection_record(
    path: Path, expected: dict[str, Any] | None = None
) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SearchProjectionError(f"Ungueltiger Projektionsdatensatz {path.name}: {exc}") from exc
    if not isinstance(payload, dict) or int(payload.get("schema") or 0) != PROJECTION_SCHEMA:
        raise SearchProjectionError(f"Ungueltiges Projektionsschema in {path.name}")
    stable_key = str(payload.get("stable_key") or "").strip()
    indexed_at = str(payload.get("indexed_source_at") or "").strip()
    if not stable_key or not indexed_at:
        raise SearchProjectionError(f"Unvollstaendiger Projektionsdatensatz {path.name}")
    try:
        _parse_timestamp(indexed_at)
    except (TypeError, ValueError) as exc:
        raise SearchProjectionError(f"Ungueltiger Projektionszeitpunkt in {path.name}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        if str(expected.get("stable_key") or "") != stable_key:
            raise SearchProjectionError(f"Stable-Key stimmt fuer {path.name} nicht")
        if str(expected.get("sha256") or "") != digest:
            raise SearchProjectionError(f"Pruefsumme stimmt fuer {path.name} nicht")
        if str(expected.get("indexed_source_at") or "") != indexed_at:
            raise SearchProjectionError(f"Quellzeitpunkt stimmt fuer {path.name} nicht")
    return payload, digest


def load_search_projection(
    root: Path,
    *,
    max_age_seconds: int | None = None,
    now: datetime | None = None,
) -> SearchProjection:
    manifest_path = root / PROJECTION_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SearchProjectionError("Mail-Suchprojektion wurde noch nicht veroeffentlicht") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SearchProjectionError(f"Projektionsmanifest ist ungueltig: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SearchProjectionError("Projektionsmanifest hat ein ungueltiges Schema")
    try:
        schema = int(manifest.get("schema") or 0)
    except (TypeError, ValueError) as exc:
        raise SearchProjectionError("Projektionsmanifest hat ein ungueltiges Schema") from exc
    if schema == PROJECTION_SCHEMA_V2:
        from .mail_projection_v2 import load_search_projection_v2

        return load_search_projection_v2(
            root,
            manifest,
            max_age_seconds=max_age_seconds,
            now=now,
        )
    if schema != PROJECTION_SCHEMA:
        raise SearchProjectionError(f"Unbekannte Mail-Projektionsversion: {schema}")
    raw_records = manifest.get("records")
    if not isinstance(raw_records, list) or int(manifest.get("record_count") or 0) != len(raw_records):
        raise SearchProjectionError("Projektionsmanifest ist unvollstaendig")

    records: list[tuple[Path, dict[str, Any]]] = []
    references: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    seen_keys: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise SearchProjectionError("Projektionsmanifest enthaelt einen ungueltigen Eintrag")
        filename = str(raw.get("filename") or "")
        stable_key = str(raw.get("stable_key") or "")
        if not filename or Path(filename).name != filename or filename == PROJECTION_MANIFEST:
            raise SearchProjectionError("Projektionsmanifest enthaelt einen unsicheren Dateinamen")
        if filename in seen_files or stable_key in seen_keys:
            raise SearchProjectionError("Projektionsmanifest enthaelt doppelte Eintraege")
        seen_files.add(filename)
        seen_keys.add(stable_key)
        path = root / filename
        payload, digest = load_projection_record(path, raw)
        reference = {
            "filename": filename,
            "stable_key": stable_key,
            "sha256": digest,
            "indexed_source_at": str(payload["indexed_source_at"]),
        }
        references.append(reference)
        records.append((path, payload))

    generation = canonical_projection_generation(references)
    if generation != str(manifest.get("source_generation") or ""):
        raise SearchProjectionError("Quellgeneration der Mail-Suchprojektion stimmt nicht")
    generated_at = str(manifest.get("generated_at") or "")
    try:
        generated = _parse_timestamp(generated_at)
    except (TypeError, ValueError) as exc:
        raise SearchProjectionError("Projektionsmanifest hat keinen gueltigen Zeitpunkt") from exc
    current = (now or datetime.now(UTC)).astimezone(UTC)
    age_seconds = max(0, int((current - generated).total_seconds()))
    if max_age_seconds is not None and age_seconds > max(0, int(max_age_seconds)):
        raise SearchProjectionError(
            f"Mail-Suchprojektion ist veraltet ({age_seconds}s > {int(max_age_seconds)}s)"
        )
    return SearchProjection(
        generation,
        generated_at,
        age_seconds,
        tuple(records),
        schema=PROJECTION_SCHEMA,
        complete=True,
        coverage={
            "contract": "v1-generation-only",
            "account_coverage_proven": False,
        },
    )
