from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

MAIL_THREAD_VERSION = "mail-thread-v1"
MAIL_RETRIEVAL_TEXT_VERSION = "mail-retrieval-text-v1"
MAX_RELATION_IDS = 100
FALLBACK_WINDOW = timedelta(days=21)

_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:re|aw|antwort|wg|fw|fwd)\s*(?:\[[0-9]+\])?\s*:\s*",
    re.IGNORECASE,
)
_UNSAFE_FALLBACK_SUBJECT = re.compile(
    r"\b(?:newsletter|digest|monatsbericht|rechnung|invoice|receipt|zahlung|payment|statement)\b",
    re.IGNORECASE,
)
_QUOTED_LINE = re.compile(r"^\s*>")
_QUOTE_BOUNDARY = re.compile(
    r"^\s*(?:on\s+.{1,180}\s+wrote:|am\s+.{1,180}\s+schrieb(?:\s+.{0,80})?:|"
    r"-{2,}\s*(?:original message|urspr(?:u|ü)ngliche nachricht)\s*-{2,})\s*$",
    re.IGNORECASE,
)
_DISCLAIMER_BOUNDARY = re.compile(
    r"^\s*(?:this e-?mail and any attachments|this message and any attachments|"
    r"diese e-?mail und (?:etwaige|alle) anlagen|vertraulichkeitshinweis\s*:)",
    re.IGNORECASE,
)
_MESSAGE_ID = re.compile(r"<([^<>\r\n]{1,998})>")


@dataclass(frozen=True, slots=True)
class RetrievalText:
    text: str
    version: str
    removed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ThreadBuild:
    edges: tuple[dict[str, Any], ...]
    threads: tuple[dict[str, Any], ...]
    members: tuple[dict[str, Any], ...]
    diagnostics: dict[str, int]


def _canonical_message_id(value: object) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    match = _MESSAGE_ID.search(text)
    candidate = match.group(1) if match else text.strip("<>")
    candidate = "".join(candidate.split())
    return candidate[:998] if "@" in candidate else ""


def normalize_retrieval_text(value: str) -> RetrievalText:
    """Reduce repeated transport text without mutating the citable source.

    The rules deliberately recognize only strong line-oriented markers. The
    result is suitable for lexical ranking and later embedding input; callers
    must continue to retain and cite the original chunk.
    """

    kept: list[str] = []
    removed: set[str] = set()
    for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _QUOTE_BOUNDARY.match(line):
            removed.add("quoted-history")
            break
        if _DISCLAIMER_BOUNDARY.match(line) and any(item.strip() for item in kept):
            removed.add("disclaimer")
            break
        if line == "-- " and any(item.strip() for item in kept):
            removed.add("signature")
            break
        if _QUOTED_LINE.match(line):
            removed.add("quoted-line")
            continue
        kept.append(line.rstrip())
    while kept and not kept[-1].strip():
        kept.pop()
    return RetrievalText(
        text="\n".join(kept).strip(),
        version=MAIL_RETRIEVAL_TEXT_VERSION,
        removed=tuple(sorted(removed)),
    )


def normalize_reply_subject(value: str) -> tuple[str, bool]:
    subject = unicodedata.normalize("NFKC", str(value or "")).strip()
    had_prefix = False
    while True:
        stripped = _SUBJECT_PREFIX.sub("", subject, count=1)
        if stripped == subject:
            break
        had_prefix = True
        subject = stripped.strip()
    return " ".join(subject.casefold().split()), had_prefix


def _timestamp(record: dict[str, Any]) -> datetime:
    metadata = dict(record.get("metadata") or {})
    raw = str(
        metadata.get("received_at")
        or metadata.get("date")
        or record.get("modified_at")
        or ""
    )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _relations(record: dict[str, Any], field: str) -> tuple[list[str], bool, bool]:
    metadata = dict(record.get("metadata") or {})
    raw_values = metadata.get(field) or []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    had_header_evidence = bool(raw_values)
    result: list[str] = []
    seen: set[str] = set()
    invalid = False
    for raw in list(raw_values)[:MAX_RELATION_IDS]:
        relation = _canonical_message_id(raw)
        if not relation:
            invalid = True
            continue
        if relation not in seen:
            seen.add(relation)
            result.append(relation)
    if len(raw_values) > MAX_RELATION_IDS:
        invalid = True
    return result, had_header_evidence, invalid


def _participants(record: dict[str, Any]) -> frozenset[str]:
    metadata = dict(record.get("metadata") or {})
    raw = [metadata.get("sender_addr"), *(metadata.get("recipients") or [])]
    return frozenset(
        normalized
        for value in raw
        if (normalized := str(value or "").strip().casefold()) and "@" in normalized
    )


def _sender(record: dict[str, Any]) -> str:
    return str(dict(record.get("metadata") or {}).get("sender_addr") or "").strip().casefold()


def _fallback_subject(record: dict[str, Any]) -> str:
    normalized, prefixed = normalize_reply_subject(str(record.get("title") or ""))
    if (
        not prefixed
        or len(normalized) < 8
        or len(normalized.split()) < 2
        or _UNSAFE_FALLBACK_SUBJECT.search(normalized)
    ):
        return ""
    return normalized


def _thread_id(root_content_id: str) -> str:
    digest = hashlib.sha256(
        f"{MAIL_THREAD_VERSION}\0mail-agent\0{root_content_id}".encode()
    ).hexdigest()
    return f"thread:{digest}"


def _cycle(parent_by_child: dict[str, str], start: str) -> tuple[str, ...]:
    positions: dict[str, int] = {}
    path: list[str] = []
    current = start
    while current in parent_by_child:
        if current in positions:
            return tuple(path[positions[current] :])
        positions[current] = len(path)
        path.append(current)
        current = parent_by_child[current]
    return ()


def build_mail_threads(records: list[dict[str, Any]], *, generation: str) -> ThreadBuild:
    """Build a deterministic, acyclic graph from one complete projection."""

    by_id = {str(item["content_id"]): item for item in records}
    message_ids: dict[str, list[str]] = {}
    for content_id, record in by_id.items():
        message_id = _canonical_message_id(record.get("message_id"))
        if message_id:
            message_ids.setdefault(message_id, []).append(content_id)
    unique_message_ids = {
        message_id: values[0]
        for message_id, values in message_ids.items()
        if len(values) == 1
    }

    parent_by_child: dict[str, str] = {}
    selected: dict[str, tuple[str, str, str]] = {}
    raw_edges: list[dict[str, Any]] = []
    header_evidence: dict[str, bool] = {}
    diagnostics = {
        "header_links": 0,
        "fallback_links": 0,
        "unresolved_relations": 0,
        "invalid_relations": 0,
        "cycle_rejections": 0,
    }

    for content_id in sorted(by_id):
        record = by_id[content_id]
        in_reply_to, reply_present, reply_invalid = _relations(record, "in_reply_to")
        references, refs_present, refs_invalid = _relations(record, "references")
        header_evidence[content_id] = reply_present or refs_present
        diagnostics["invalid_relations"] += int(reply_invalid) + int(refs_invalid)
        for edge_type, header, values in (
            ("in-reply-to", "In-Reply-To", in_reply_to),
            ("reference", "References", references),
        ):
            for relation in values:
                related = unique_message_ids.get(relation)
                reason = "resolved"
                if relation in message_ids and relation not in unique_message_ids:
                    related = None
                    reason = "ambiguous-message-id"
                elif related == content_id:
                    related = None
                    reason = "self-reference"
                elif related is None:
                    reason = "missing-message-id"
                if related is None:
                    diagnostics["unresolved_relations"] += 1
                raw_edges.append(
                    {
                        "content_id": content_id,
                        "edge_type": edge_type,
                        "relation_message_id": relation,
                        "related_content_id": related,
                        "evidence_header": header,
                        "selected": False,
                        "certainty": "certain",
                        "reason": reason,
                        "index_generation": generation,
                    }
                )

        candidate: tuple[str, str, str] | None = None
        for relation in reversed(in_reply_to):
            related = unique_message_ids.get(relation)
            if related and related != content_id:
                candidate = (related, "in-reply-to", relation)
                break
        if candidate is None:
            for relation in reversed(references):
                related = unique_message_ids.get(relation)
                if related and related != content_id:
                    candidate = (related, "reference", relation)
                    break
        if candidate is not None:
            parent_by_child[content_id] = candidate[0]
            selected[content_id] = candidate

    while True:
        found: tuple[str, ...] = ()
        for content_id in sorted(parent_by_child):
            found = _cycle(parent_by_child, content_id)
            if found:
                break
        if not found:
            break
        rejected = max(found)
        parent_by_child.pop(rejected, None)
        selected.pop(rejected, None)
        diagnostics["cycle_rejections"] += 1

    ordered = sorted(by_id, key=lambda item: (_timestamp(by_id[item]), item))
    seen_by_subject: dict[str, list[str]] = {}
    for content_id in ordered:
        record = by_id[content_id]
        subject = _fallback_subject(record)
        if content_id not in parent_by_child and not header_evidence[content_id] and subject:
            child_time = _timestamp(record)
            child_participants = _participants(record)
            child_sender = _sender(record)
            candidates: list[str] = []
            for candidate_id in seen_by_subject.get(subject, []):
                candidate_record = by_id[candidate_id]
                delta = child_time - _timestamp(candidate_record)
                candidate_sender = _sender(candidate_record)
                candidate_participants = _participants(candidate_record)
                if (
                    timedelta(0) <= delta <= FALLBACK_WINDOW
                    and child_sender
                    and candidate_sender
                    and child_sender != candidate_sender
                    and child_sender in candidate_participants
                    and candidate_sender in child_participants
                    and len(child_participants & candidate_participants) >= 2
                ):
                    candidates.append(candidate_id)
            if candidates:
                latest_time = max(_timestamp(by_id[item]) for item in candidates)
                latest = [item for item in candidates if _timestamp(by_id[item]) == latest_time]
                if len(latest) == 1:
                    parent = latest[0]
                    relation = "subject:" + hashlib.sha256(subject.encode("utf-8")).hexdigest()
                    parent_by_child[content_id] = parent
                    selected[content_id] = (parent, "subject-participant-time", relation)
                    raw_edges.append(
                        {
                            "content_id": content_id,
                            "edge_type": "subject-participant-time",
                            "relation_message_id": relation,
                            "related_content_id": parent,
                            "evidence_header": "fallback",
                            "selected": True,
                            "certainty": "uncertain",
                            "reason": "conservative-fallback",
                            "index_generation": generation,
                        }
                    )
                    diagnostics["fallback_links"] += 1
        base_subject, _prefixed = normalize_reply_subject(str(record.get("title") or ""))
        if base_subject:
            seen_by_subject.setdefault(base_subject, []).append(content_id)

    for edge in raw_edges:
        chosen = selected.get(str(edge["content_id"]))
        if chosen and (edge["edge_type"], edge["relation_message_id"]) == (chosen[1], chosen[2]):
            edge["selected"] = True
            if edge["certainty"] == "certain":
                diagnostics["header_links"] += 1

    root_by_member: dict[str, str] = {}
    for content_id in sorted(by_id):
        current = content_id
        while current in parent_by_child:
            current = parent_by_child[current]
        root_by_member[content_id] = current

    grouped: dict[str, list[str]] = {}
    for content_id, root in root_by_member.items():
        grouped.setdefault(root, []).append(content_id)
    threads: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for root in sorted(grouped):
        ordered_members = sorted(grouped[root], key=lambda item: (_timestamp(by_id[item]), item))
        thread_id = _thread_id(root)
        uncertain = any(
            selected.get(item, ("", "", ""))[1] == "subject-participant-time"
            for item in ordered_members
        )
        timestamps = [_timestamp(by_id[item]).isoformat() for item in ordered_members]
        threads.append(
            {
                "thread_id": thread_id,
                "root_content_id": root,
                "thread_version": MAIL_THREAD_VERSION,
                "member_count": len(ordered_members),
                "first_at": min(timestamps),
                "last_at": max(timestamps),
                "uncertain": uncertain,
                "index_generation": generation,
            }
        )
        for position, content_id in enumerate(ordered_members):
            chosen = selected.get(content_id)
            members.append(
                {
                    "content_id": content_id,
                    "thread_id": thread_id,
                    "parent_content_id": chosen[0] if chosen else None,
                    "evidence_type": chosen[1] if chosen else "root",
                    "certainty": (
                        "uncertain"
                        if chosen and chosen[1] == "subject-participant-time"
                        else "certain"
                    ),
                    "position": position,
                    "index_generation": generation,
                }
            )

    raw_edges.sort(
        key=lambda item: (
            str(item["content_id"]),
            str(item["edge_type"]),
            str(item["relation_message_id"]),
        )
    )
    return ThreadBuild(
        edges=tuple(raw_edges),
        threads=tuple(threads),
        members=tuple(members),
        diagnostics=diagnostics,
    )


__all__ = [
    "MAIL_RETRIEVAL_TEXT_VERSION",
    "MAIL_THREAD_VERSION",
    "RetrievalText",
    "ThreadBuild",
    "build_mail_threads",
    "normalize_reply_subject",
    "normalize_retrieval_text",
]
