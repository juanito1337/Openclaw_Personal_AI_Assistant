from __future__ import annotations

import html
import json
import re
import sqlite3
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

MAIL_SEARCH_QUERY_VERSION = "mail-query-v1"
MAIL_SEARCH_RANKING_VERSION = "mail-bm25-v1"
MAIL_SEARCH_TAG_VERSION = "mail-tags-v1"

BM25_WEIGHTS = {"subject": 8.0, "sender": 4.0, "body": 1.0}
EXACT_PHRASE_BOOST = 2.0
EXACT_SENDER_BOOST = 3.0
MAX_QUERY_CHARS = 500
MAX_QUERY_TERMS = 24
MAX_TERM_CHARS = 100
MAX_RESULT_LIMIT = 200
MAX_RANKED_DOCUMENTS = 10_000
MAX_MATCHED_CHUNKS = 100_000
SNIPPET_CHARS = 320

TAG_NAMESPACES = frozenset(
    {
        "attachment-type",
        "category",
        "folder",
        "has",
        "kind",
        "month",
        "participant",
        "quarantine",
        "review",
        "sender",
        "sender-domain",
        "year",
    }
)
DECLARED_TAG_NAMESPACES = frozenset({"category", "kind", "review"})
TAG_SOURCES = frozenset({"classifier", "extractor", "model", "rule", "user"})
CATEGORY_VALUES = frozenset(
    {"appointment", "invoice", "order", "relevant", "routine", "spam", "uncertain"}
)
REVIEW_VALUES = frozenset(
    {
        "appointment-review",
        "classification-uncertain",
        "invoice-review",
        "relevant-not-forwarded",
        "routine-below-threshold",
        "safety-blocked",
        "spam-below-threshold",
        "unknown-legacy",
    }
)
KIND_VALUES = frozenset({"calendar", "invoice", "order"})

_WORD = re.compile(r"[^\W_]+(?:[._+@-][^\W_]+)*\*?", re.UNICODE)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def normalize_tag_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return " ".join(normalized.split())


def _valid_tag_value(namespace: str, value: str) -> bool:
    if not value or len(value) > 255 or any(ord(char) < 32 for char in value):
        return False
    if namespace == "has":
        return value == "attachment"
    if namespace == "quarantine":
        return value in {"yes", "no"}
    if namespace == "category":
        return value in CATEGORY_VALUES
    if namespace == "review":
        return value in REVIEW_VALUES
    if namespace == "kind":
        return value in KIND_VALUES
    if namespace == "year":
        return bool(re.fullmatch(r"\d{4}", value))
    if namespace == "month":
        return bool(re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value))
    if namespace in {"sender", "participant"}:
        return "@" in value and " " not in value
    if namespace == "sender-domain":
        return "." in value and "@" not in value and " " not in value
    return True


@dataclass(frozen=True, slots=True)
class MailTag:
    namespace: str
    value: str
    source: str
    source_version: str
    confidence: float | None
    evidence: dict[str, Any]
    active: bool
    uncertainty: str = ""

    def __post_init__(self) -> None:
        if self.namespace not in TAG_NAMESPACES:
            raise ValueError(f"Unbekannter lokaler Tag-Namensraum: {self.namespace}")
        if not _valid_tag_value(self.namespace, self.value):
            raise ValueError(f"Ungueltiger lokaler Tag-Wert fuer {self.namespace}")
        if self.source not in TAG_SOURCES and self.source not in {"locator", "parser"}:
            raise ValueError(f"Unbekannte lokale Tag-Quelle: {self.source}")
        if not self.source_version or len(self.source_version) > 100:
            raise ValueError("Lokale Tag-Quellversion fehlt oder ist zu lang")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Lokale Tag-Konfidenz muss zwischen 0 und 1 liegen")
        if self.source == "model" and self.active:
            raise ValueError("Modellvorschlaege duerfen keine aktiven lokalen Tags sein")


def parse_tag_filter(value: str) -> tuple[str, str]:
    namespace, separator, raw_value = str(value or "").partition(":")
    namespace = normalize_tag_value(namespace)
    tag_value = normalize_tag_value(raw_value)
    if not separator or namespace not in TAG_NAMESPACES:
        raise ValueError("Tagfilter muss <geschlossener-namensraum>:<wert> verwenden")
    if not _valid_tag_value(namespace, tag_value):
        raise ValueError(f"Ungueltiger Tagfilter fuer {namespace}")
    return namespace, tag_value


@dataclass(frozen=True, slots=True)
class QueryTerm:
    value: str
    phrase: bool = False
    prefix: bool = False


@dataclass(frozen=True, slots=True)
class ParsedMailQuery:
    terms: tuple[QueryTerm, ...]
    match: str
    plain_text: str


def _fts_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def parse_mail_query(raw: str) -> ParsedMailQuery:
    value = unicodedata.normalize("NFKC", str(raw or "")).strip()
    if len(value) > MAX_QUERY_CHARS:
        raise ValueError(f"Suchtext darf hoechstens {MAX_QUERY_CHARS} Zeichen enthalten")
    terms: list[QueryTerm] = []
    position = 0
    while position < len(value):
        if value[position].isspace():
            position += 1
            continue
        phrase = value[position] == '"'
        if phrase:
            end = value.find('"', position + 1)
            if end < 0:
                end = len(value)
            segment = value[position + 1 : end]
            position = end + 1
            tokens = [item.group(0).removesuffix("*") for item in _WORD.finditer(segment)]
            phrase_value = " ".join(token[:MAX_TERM_CHARS] for token in tokens if token)
            if phrase_value:
                terms.append(QueryTerm(phrase_value, phrase=True))
        else:
            next_space = position
            while next_space < len(value) and not value[next_space].isspace():
                next_space += 1
            segment = value[position:next_space]
            position = next_space
            for item in _WORD.finditer(segment):
                token = item.group(0)
                prefix = token.endswith("*") and len(token) > 2
                token = token.removesuffix("*")[:MAX_TERM_CHARS]
                if token:
                    terms.append(QueryTerm(token, prefix=prefix))
        if len(terms) > MAX_QUERY_TERMS:
            raise ValueError(f"Suchtext darf hoechstens {MAX_QUERY_TERMS} Terme enthalten")
    expressions = [
        _fts_quote(term.value) + ("*" if term.prefix else "")
        for term in terms
    ]
    return ParsedMailQuery(
        tuple(terms),
        " AND ".join(expressions),
        " ".join(term.value for term in terms),
    )


def _parse_date_boundary(value: str, *, inclusive_end: bool) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if len(raw) == 10:
            parsed = datetime.combine(date.fromisoformat(raw), datetime.min.time(), tzinfo=UTC)
            if inclusive_end:
                parsed += timedelta(days=1)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            parsed = parsed.astimezone(UTC)
    except ValueError as exc:
        raise ValueError(f"Ungueltige ISO-Zeitgrenze: {raw}") from exc
    return parsed.isoformat()


@dataclass(frozen=True, slots=True)
class MailSearchFilters:
    sender: str = ""
    participant: str = ""
    after: str = ""
    before: str = ""
    folder: str = ""
    category: str = ""
    review_reason: str = ""
    has_attachment: bool | None = None
    attachment_type: str = ""
    tags: tuple[str, ...] = ()

    def normalized(self) -> MailSearchFilters:
        sender = normalize_tag_value(self.sender)
        participant = normalize_tag_value(self.participant)
        folder = normalize_tag_value(self.folder)
        category = normalize_tag_value(self.category)
        review = normalize_tag_value(self.review_reason)
        attachment_type = normalize_tag_value(self.attachment_type)
        if sender and not _valid_tag_value("sender", sender):
            raise ValueError("Absenderfilter benoetigt eine vollstaendige E-Mail-Adresse")
        if participant and not _valid_tag_value("participant", participant):
            raise ValueError("Teilnehmerfilter benoetigt eine vollstaendige E-Mail-Adresse")
        if category and category not in CATEGORY_VALUES:
            raise ValueError(f"Unbekannte Mailkategorie: {category}")
        if review and review not in REVIEW_VALUES:
            raise ValueError(f"Unbekannter Review-Grund: {review}")
        parsed_tags = tuple(
            f"{namespace}:{value}" for namespace, value in map(parse_tag_filter, self.tags)
        )
        return MailSearchFilters(
            sender=sender,
            participant=participant,
            after=_parse_date_boundary(self.after, inclusive_end=False),
            before=_parse_date_boundary(self.before, inclusive_end=True),
            folder=folder,
            category=category,
            review_reason=review,
            has_attachment=self.has_attachment,
            attachment_type=attachment_type,
            tags=parsed_tags,
        )


def _email(value: Any) -> str:
    candidate = normalize_tag_value(str(value or ""))
    return candidate if _valid_tag_value("participant", candidate) else ""


def _timestamp_tags(metadata: dict[str, Any]) -> list[MailTag]:
    raw = str(metadata.get("received_at") or metadata.get("date") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return []
    evidence = {"field": "received_at"}
    return [
        MailTag("year", f"{parsed.year:04d}", "parser", MAIL_SEARCH_TAG_VERSION, 1.0, evidence, True),
        MailTag(
            "month",
            f"{parsed.year:04d}-{parsed.month:02d}",
            "parser",
            MAIL_SEARCH_TAG_VERSION,
            1.0,
            evidence,
            True,
        ),
    ]


def build_mail_tags(metadata: dict[str, Any]) -> list[MailTag]:
    """Build active structural tags and separated, validated declared tags."""

    tags: list[MailTag] = []
    sender = _email(metadata.get("sender_addr"))
    if sender:
        tags.extend(
            [
                MailTag("sender", sender, "parser", MAIL_SEARCH_TAG_VERSION, 1.0, {"field": "from"}, True),
                MailTag(
                    "participant",
                    sender,
                    "parser",
                    MAIL_SEARCH_TAG_VERSION,
                    1.0,
                    {"field": "from"},
                    True,
                ),
            ]
        )
        domain = sender.rsplit("@", 1)[-1]
        if _valid_tag_value("sender-domain", domain):
            tags.append(
                MailTag(
                    "sender-domain",
                    domain,
                    "parser",
                    MAIL_SEARCH_TAG_VERSION,
                    1.0,
                    {"field": "from"},
                    True,
                )
            )
    for recipient in metadata.get("recipients") or []:
        address = _email(recipient)
        if address:
            tags.append(
                MailTag(
                    "participant",
                    address,
                    "parser",
                    MAIL_SEARCH_TAG_VERSION,
                    1.0,
                    {"field": "recipient"},
                    True,
                )
            )
    current_locators = list(metadata.get("locators") or [])
    for locator in current_locators:
        folder = normalize_tag_value(str(locator.get("folder_name") or ""))
        if folder:
            tags.append(
                MailTag(
                    "folder",
                    folder,
                    "locator",
                    MAIL_SEARCH_TAG_VERSION,
                    1.0,
                    {"locator_id": str(locator.get("locator_id") or "")},
                    True,
                )
            )
    if current_locators:
        quarantine = "yes" if all(bool(item.get("quarantine")) for item in current_locators) else "no"
        tags.append(
            MailTag(
                "quarantine",
                quarantine,
                "locator",
                MAIL_SEARCH_TAG_VERSION,
                1.0,
                {"locator_count": len(current_locators)},
                True,
            )
        )
    attachments = list(metadata.get("attachments") or [])
    if attachments:
        tags.append(
            MailTag(
                "has",
                "attachment",
                "parser",
                MAIL_SEARCH_TAG_VERSION,
                1.0,
                {"count": len(attachments)},
                True,
            )
        )
    for attachment in attachments:
        content_type = normalize_tag_value(str(attachment.get("content_type") or ""))
        filename = normalize_tag_value(str(attachment.get("filename") or ""))
        values = {content_type} if content_type else set()
        if "/" in content_type:
            values.add(content_type.rsplit("/", 1)[-1])
        if "." in filename:
            values.add(filename.rsplit(".", 1)[-1])
        for value in sorted(values):
            if _valid_tag_value("attachment-type", value):
                tags.append(
                    MailTag(
                        "attachment-type",
                        value,
                        "parser",
                        MAIL_SEARCH_TAG_VERSION,
                        1.0,
                        {"field": "attachment-metadata"},
                        True,
                    )
                )
    tags.extend(_timestamp_tags(metadata))

    for raw in metadata.get("declared_tags") or []:
        if not isinstance(raw, dict):
            continue
        namespace = normalize_tag_value(str(raw.get("namespace") or ""))
        value = normalize_tag_value(str(raw.get("value") or ""))
        source = normalize_tag_value(str(raw.get("source") or ""))
        version = str(raw.get("source_version") or "").strip()[:100]
        if (
            namespace not in DECLARED_TAG_NAMESPACES
            or source not in TAG_SOURCES
            or not _valid_tag_value(namespace, value)
            or not version
        ):
            continue
        confidence: float | None
        try:
            confidence = float(raw["confidence"]) if raw.get("confidence") is not None else None
        except (TypeError, ValueError):
            confidence = None
        raw_evidence = raw.get("evidence")
        evidence: dict[str, Any] = (
            {str(key): value for key, value in raw_evidence.items()}
            if isinstance(raw_evidence, dict)
            else {}
        )
        requested_active = bool(raw.get("active", True))
        uncertainty = normalize_tag_value(str(raw.get("uncertainty") or ""))[:100]
        active = requested_active and source != "model" and bool(evidence)
        if source == "model":
            uncertainty = uncertainty or "model-proposal"
        elif requested_active and not evidence:
            uncertainty = uncertainty or "missing-evidence"
        try:
            tags.append(
                MailTag(
                    namespace,
                    value,
                    source,
                    version,
                    confidence,
                    evidence,
                    active,
                    uncertainty,
                )
            )
        except ValueError:
            continue
    unique: dict[tuple[str, str, str, str], MailTag] = {}
    for tag in tags:
        unique[(tag.namespace, tag.value, tag.source, tag.source_version)] = tag
    return [unique[key] for key in sorted(unique)]


def normalize_declared_mail_tags(
    raw_tags: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the canonical closed subset allowed in immutable projections."""

    return [
        asdict(tag)
        for tag in build_mail_tags({"declared_tags": list(raw_tags)})
        if tag.namespace in DECLARED_TAG_NAMESPACES
    ]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _safe_text(value: str) -> str:
    without_ansi = _ANSI.sub("", value)
    parser = _TextExtractor()
    try:
        parser.feed(without_ansi)
        text = " ".join(parser.parts)
    except Exception:
        text = without_ansi.replace("<", " ").replace(">", " ")
    text = "".join(char if char in "\n\t" or ord(char) >= 32 else " " for char in text)
    return " ".join(html.unescape(text).split())


def _search_fold(value: str) -> tuple[str, list[int]]:
    folded: list[str] = []
    indices: list[int] = []
    for index, char in enumerate(value):
        for normalized in unicodedata.normalize("NFKD", char).casefold():
            if not unicodedata.combining(normalized):
                folded.append(normalized)
                indices.append(index)
    return "".join(folded), indices


def query_centered_snippet(text: str, query: ParsedMailQuery) -> str:
    clean = _safe_text(text)
    if not clean:
        return ""
    folded, indices = _search_fold(clean)
    match_start = -1
    match_end = -1
    candidates = [term.value for term in query.terms]
    for candidate in sorted(candidates, key=len, reverse=True):
        needle, _unused = _search_fold(candidate)
        position = folded.find(needle)
        if position >= 0 and indices:
            match_start = indices[position]
            match_end = indices[min(len(indices) - 1, position + max(1, len(needle)) - 1)] + 1
            break
    if match_start < 0:
        start = 0
    else:
        center = (match_start + match_end) // 2
        start = max(0, center - SNIPPET_CHARS // 2)
        start = max(0, clean.rfind(" ", max(0, start - 30), start + 1))
    end = min(len(clean), start + SNIPPET_CHARS)
    snippet = clean[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(clean):
        snippet += "…"
    return snippet[: SNIPPET_CHARS + 1]


def _best_snippet_source(
    query: ParsedMailQuery,
    *,
    subject: str,
    sender: str,
    body: str,
) -> str:
    if not query.terms:
        return body
    candidates = (body, f"Betreff: {subject}", f"Absender: {sender}")

    def matches(value: str) -> int:
        folded, _indices = _search_fold(_safe_text(value))
        return sum(
            1
            for term in query.terms
            if _search_fold(term.value)[0] in folded
        )

    return max(candidates, key=matches)


class MailLexicalSearch:
    def __init__(self, connection: sqlite3.Connection, *, fts_enabled: bool) -> None:
        self.connection = connection
        self.fts_enabled = fts_enabled

    @staticmethod
    def _tag_clause(
        clauses: list[str], params: list[Any], namespace: str, value: str, *, negate: bool = False
    ) -> None:
        operator = "NOT EXISTS" if negate else "EXISTS"
        clauses.append(
            f"{operator} (SELECT 1 FROM mail_search_tags mt "
            "WHERE mt.content_id=d.content_id AND mt.active=1 "
            "AND mt.namespace=? AND mt.value=?)"
        )
        params.extend([namespace, value])

    def _where(self, filters: MailSearchFilters) -> tuple[list[str], list[Any]]:
        clauses = ["d.source_type='email'", "d.resource_id='mail-agent'"]
        params: list[Any] = []
        for namespace, value in (
            ("sender", filters.sender),
            ("participant", filters.participant),
            ("folder", filters.folder),
            ("category", filters.category),
            ("review", filters.review_reason),
            ("attachment-type", filters.attachment_type),
        ):
            if value:
                self._tag_clause(clauses, params, namespace, value)
        if filters.has_attachment is not None:
            self._tag_clause(
                clauses,
                params,
                "has",
                "attachment",
                negate=not filters.has_attachment,
            )
        for raw in filters.tags:
            namespace, value = parse_tag_filter(raw)
            self._tag_clause(clauses, params, namespace, value)
        timestamp = (
            "COALESCE(json_extract(d.metadata_json,'$.received_at'),"
            "json_extract(d.metadata_json,'$.date'),d.modified_at,'')"
        )
        if filters.after:
            clauses.append(f"{timestamp}>=?")
            params.append(filters.after)
        if filters.before:
            clauses.append(f"{timestamp}<?")
            params.append(filters.before)
        return clauses, params

    def _index_state(self, *, max_age_seconds: int) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT generation,source_generated_at,imported_at,complete,source_status,coverage_json
            FROM mail_search_generations ORDER BY imported_at DESC,generation DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return {
                "state": "missing",
                "complete": False,
                "fresh": False,
                "absence_proven": False,
                "source_generation": "",
            }
        try:
            coverage = json.loads(str(row["coverage_json"] or "{}"))
        except json.JSONDecodeError:
            coverage = {}
        try:
            generated = datetime.fromisoformat(
                str(row["source_generated_at"]).replace("Z", "+00:00")
            )
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=UTC)
            age_seconds = max(0, int((datetime.now(UTC) - generated.astimezone(UTC)).total_seconds()))
        except (TypeError, ValueError):
            age_seconds = None
        complete = bool(row["complete"]) and str(row["source_status"]) == "active"
        authoritative = bool(coverage.get("authoritative"))
        fresh = age_seconds is not None and age_seconds <= max_age_seconds
        return {
            "state": "complete" if complete else "incomplete",
            "complete": complete,
            "authoritative": authoritative,
            "fresh": fresh,
            "age_seconds": age_seconds,
            "max_age_seconds": max_age_seconds,
            "absence_proven": complete and authoritative and fresh,
            "source_generation": str(row["generation"]),
            "source_generated_at": str(row["source_generated_at"]),
            "coverage": coverage,
        }

    def search(
        self,
        raw_query: str,
        *,
        filters: MailSearchFilters | None = None,
        limit: int = 20,
        max_age_seconds: int = 7200,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        query = parse_mail_query(raw_query)
        normalized_filters = (filters or MailSearchFilters()).normalized()
        limit = max(1, min(int(limit), MAX_RESULT_LIMIT))
        if not query.match and not any(
            (
                normalized_filters.sender,
                normalized_filters.participant,
                normalized_filters.after,
                normalized_filters.before,
                normalized_filters.folder,
                normalized_filters.category,
                normalized_filters.review_reason,
                normalized_filters.attachment_type,
                normalized_filters.tags,
                normalized_filters.has_attachment is not None,
            )
        ):
            raise ValueError("Lokale Mail-Suche benoetigt Suchtext oder mindestens einen Filter")
        if query.match and not self.fts_enabled:
            raise RuntimeError("Sichere lokale Mail-FTS5-Suche ist nicht verfuegbar")

        clauses, filter_params = self._where(normalized_filters)
        params: list[Any] = []
        from_clause = "chunks c JOIN documents d ON d.id=c.document_id"
        rank_expression = "0.0"
        if query.match:
            from_clause = (
                "mail_search_fts JOIN chunks c ON c.id=mail_search_fts.rowid "
                "JOIN documents d ON d.id=c.document_id"
            )
            clauses.insert(0, "mail_search_fts MATCH ?")
            params.append(query.match)
            rank_expression = (
                "bm25(mail_search_fts,0.0,0.0,0.0,"
                f"{BM25_WEIGHTS['subject']},{BM25_WEIGHTS['sender']},{BM25_WEIGHTS['body']})"
            )
        params.extend(filter_params)
        where = " AND ".join(clauses)
        count_row = self.connection.execute(
            f"SELECT COUNT(*) AS chunks,COUNT(DISTINCT d.id) AS documents "
            f"FROM {from_clause} WHERE {where}",
            params,
        ).fetchone()
        matched_chunks = int(count_row["chunks"] if count_row else 0)
        matched_documents = int(count_row["documents"] if count_row else 0)
        if query.match:
            chunk_rows = self.connection.execute(
                f"""
                SELECT d.*,c.text AS matched_text,{rank_expression} AS rank
                FROM {from_clause}
                WHERE {where}
                ORDER BY rank ASC,d.modified_at DESC,d.id DESC,c.chunk_index ASC
                LIMIT ?
                """,
                [*params, min(MAX_MATCHED_CHUNKS, matched_chunks)],
            ).fetchall()
        else:
            chunk_rows = self.connection.execute(
                f"""
                SELECT d.*,c.text AS matched_text,0.0 AS rank
                FROM {from_clause}
                WHERE {where}
                GROUP BY d.id
                ORDER BY d.modified_at DESC,d.id DESC
                LIMIT ?
                """,
                [*params, min(MAX_RANKED_DOCUMENTS, matched_documents)],
            ).fetchall()
        best_rows: dict[int, sqlite3.Row] = {}
        for row in chunk_rows:
            document_id = int(row["id"])
            previous = best_rows.get(document_id)
            if previous is None or float(row["rank"] or 0.0) < float(
                previous["rank"] or 0.0
            ):
                best_rows[document_id] = row
        rows = list(best_rows.values())[:MAX_RANKED_DOCUMENTS]

        ranked: list[tuple[float, sqlite3.Row, dict[str, float | bool]]] = []
        query_plain = normalize_tag_value(query.plain_text)
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            sender_values = {
                normalize_tag_value(str(metadata.get("sender_addr") or "")),
                normalize_tag_value(str(metadata.get("sender_name") or "")),
            }
            title = normalize_tag_value(str(row["title"] or ""))
            body = normalize_tag_value(str(row["matched_text"] or ""))
            exact_phrase = bool(query_plain and (query_plain in title or query_plain in body))
            exact_sender = bool(query_plain and query_plain in sender_values)
            lexical = -float(row["rank"] or 0.0)
            phrase_boost = EXACT_PHRASE_BOOST if exact_phrase else 0.0
            sender_boost = EXACT_SENDER_BOOST if exact_sender else 0.0
            score = lexical + phrase_boost + sender_boost
            ranked.append(
                (
                    score,
                    row,
                    {
                        "bm25": round(lexical, 8),
                        "exact_phrase_boost": phrase_boost,
                        "exact_sender_boost": sender_boost,
                        "recency_boost_applied": False,
                    },
                )
            )
        ranked.sort(
            key=lambda item: (
                -item[0],
                str(item[1]["modified_at"] or ""),
                -int(item[1]["id"]),
            )
        )

        results: list[dict[str, Any]] = []
        for score, row, components in ranked[:limit]:
            document_id = int(row["id"])
            snippet_row: sqlite3.Row | None
            if query.match:
                snippet_row = self.connection.execute(
                    f"""
                    SELECT c.text,mail_search_fts.subject AS matched_subject,
                           mail_search_fts.sender AS matched_sender,
                           mail_search_fts.body AS matched_body,
                           {rank_expression} AS rank
                    FROM mail_search_fts
                    JOIN chunks c ON c.id=mail_search_fts.rowid
                    WHERE mail_search_fts MATCH ? AND c.document_id=?
                    ORDER BY rank ASC,c.chunk_index ASC LIMIT 1
                    """,
                    (query.match, document_id),
                ).fetchone()
            else:
                snippet_row = self.connection.execute(
                    "SELECT text,0.0 AS rank FROM chunks WHERE document_id=? "
                    "ORDER BY chunk_index ASC LIMIT 1",
                    (document_id,),
                ).fetchone()
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            content_id = str(row["content_id"] or row["source_id"])
            tag_rows = self.connection.execute(
                """
                SELECT namespace,value,source,source_version,confidence,evidence_json,
                       active,uncertainty
                FROM mail_search_tags WHERE content_id=?
                ORDER BY active DESC,namespace,value,source,source_version
                """,
                (str(row["content_id"] or ""),),
            ).fetchall()
            tags: list[dict[str, Any]] = []
            for tag in tag_rows:
                try:
                    evidence = json.loads(str(tag["evidence_json"] or "{}"))
                except json.JSONDecodeError:
                    evidence = {}
                tags.append(
                    {
                        "namespace": str(tag["namespace"]),
                        "value": str(tag["value"]),
                        "source": str(tag["source"]),
                        "source_version": str(tag["source_version"]),
                        "confidence": tag["confidence"],
                        "evidence": evidence,
                        "active": bool(tag["active"]),
                        "uncertainty": str(tag["uncertainty"] or ""),
                    }
                )
            results.append(
                {
                    "document_id": document_id,
                    "content_id": content_id,
                    "source_id": str(row["source_id"]),
                    "title": str(row["title"]),
                    "uri": str(row["uri"]),
                    "date": str(
                        metadata.get("received_at")
                        or metadata.get("date")
                        or row["modified_at"]
                        or ""
                    ),
                    "sender": {
                        "name": str(metadata.get("sender_name") or ""),
                        "address": str(metadata.get("sender_addr") or ""),
                    },
                    "folders": sorted(
                        {
                            str(item.get("folder_name") or "")
                            for item in metadata.get("locators") or []
                            if str(item.get("folder_name") or "")
                        }
                    ),
                    "snippet": query_centered_snippet(
                        _best_snippet_source(
                            query,
                            subject=str(snippet_row["matched_subject"] or ""),
                            sender=str(snippet_row["matched_sender"] or ""),
                            body=str(snippet_row["matched_body"] or ""),
                        )
                        if query.match and snippet_row
                        else str(snippet_row["text"] or "")
                        if snippet_row
                        else "",
                        query,
                    ),
                    "score": round(score, 8),
                    "ranking": components,
                    "match": {
                        "query_version": MAIL_SEARCH_QUERY_VERSION,
                        "ranking_version": MAIL_SEARCH_RANKING_VERSION,
                        "matched_chunk_count": int(
                            self.connection.execute(
                                "SELECT COUNT(*) FROM mail_search_fts "
                                "JOIN chunks c ON c.id=mail_search_fts.rowid "
                                "WHERE mail_search_fts MATCH ? AND c.document_id=?",
                                (query.match, document_id),
                            ).fetchone()[0]
                        )
                        if query.match
                        else int(
                            self.connection.execute(
                                "SELECT COUNT(*) FROM chunks WHERE document_id=?",
                                (document_id,),
                            ).fetchone()[0]
                        ),
                    },
                    "tags": tags,
                    "source_generation": str(row["index_generation"] or ""),
                    "source_status": str(row["source_status"] or ""),
                }
            )
        index_state = self._index_state(max_age_seconds=max_age_seconds)
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return {
            "ok": True,
            "path": "local-mail-lexical",
            "read_only": True,
            "complete": bool(index_state.get("absence_proven")),
            "results_may_be_truncated": (
                matched_chunks > MAX_MATCHED_CHUNKS
                or matched_documents > MAX_RANKED_DOCUMENTS
            ),
            "count": len(results),
            "results": results,
            "index": index_state,
            "ranking": {
                "version": MAIL_SEARCH_RANKING_VERSION,
                "bm25_weights": BM25_WEIGHTS,
                "exact_phrase_boost": EXACT_PHRASE_BOOST,
                "exact_sender_boost": EXACT_SENDER_BOOST,
                "recency_boost_applied": False,
            },
            "metrics": {
                "matched_chunks": matched_chunks,
                "matched_documents": matched_documents,
                "deduplicated_chunks": max(0, matched_chunks - matched_documents),
                "ranked_documents": len(rows),
                "returned_documents": len(results),
                "latency_ms": latency_ms,
            },
        }


__all__ = [
    "BM25_WEIGHTS",
    "MAIL_SEARCH_QUERY_VERSION",
    "MAIL_SEARCH_RANKING_VERSION",
    "MAIL_SEARCH_TAG_VERSION",
    "MailLexicalSearch",
    "MailSearchFilters",
    "MailTag",
    "TAG_NAMESPACES",
    "build_mail_tags",
    "normalize_declared_mail_tags",
    "parse_mail_query",
    "parse_tag_filter",
    "query_centered_snippet",
]
