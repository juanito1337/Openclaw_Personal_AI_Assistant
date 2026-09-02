from __future__ import annotations

import hashlib
import json
import re
import shlex
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .release import release_path
from .tool_registry import tool_definitions

SCHEMA_VERSION = 1
CONTRACT_ID = "openclaw-personal-assistant-native-tools-v1"
PLUGIN_ID = "personal-assistant-tools"
PLUGIN_PATH = "/opt/openclaw-plugins/personal-assistant-tools"
MAX_ROUTED_DOMAINS = 3

_PLACEHOLDER = re.compile(r"<([^>]+)>")
_SHELL_OPERATORS = frozenset({"&&", "||", ";", ">", ">>", "<", "2>", "2>>"})
_DOMAIN_TOOL_NAMES = {
    domain: {
        "read": f"personal_assistant_{domain}_read",
        "write": f"personal_assistant_{domain}_write",
    }
    for domain in (
        "runtime",
        "mail",
        "nextcloud",
        "contacts",
        "calendar",
        "tasks",
        "orders",
        "invoices",
        "portfolio",
        "security",
    )
}

_PROPERTY_NAMES = {
    "Version": "version",
    "Suchbegriff": "query",
    "ISIN": "isin",
    "Unternehmen-oder-Symbol": "query",
    "Datei-im-Importordner": "file",
    "Datei-DD.MM.YYYY.csv": "file",
    "Name": "name",
    "Symbol": "symbol",
    "MIC": "mic",
    "ISO": "currency",
    "Boerse-optional": "exchange",
    "Sektor-optional": "sector",
    "Prozent": "percent",
    "Kommaliste": "comma_list",
    "Notiz": "notes",
    "Research-Kandidaten-ID": "candidate_id",
    "Begruendung": "reason",
    "Kurs": "threshold",
    "Regel-ID": "rule_id",
    "Datei": "file",
    "Grund": "reason",
    "Ordner": "folder",
    "exakter Ordner": "folder",
    "ID": "id",
    "Mail-ID": "message_id",
    "Betreff": "expected_subject",
    "Entwurfs-ID": "draft_id",
    "Entwurf": "body",
    "Empfaenger": "to",
    "Quelle": "source",
    "Ziel": "destination",
    "Typ": "label",
    "resource_id": "resource_id",
    "Titel": "title",
    "aktueller Titel": "expected_title",
    "aktueller Name": "expected_name",
    "neue Telefonnummer": "phone",
    "E-Mail": "email",
    "Telefon": "phone",
    "Firma": "organization",
    "ISO-8601": "start",
    "Ort": "location",
    "Beschreibung": "description",
    "YYYY-MM-DD oder ISO-8601": "due",
    "YYYY": "year",
    "SHA256": "sha256",
    "Digest": "expected_preview_sha256",
    "YYYY-MM-DD": "date",
    "Nr": "number",
    "Steller": "supplier",
    "Kategorie": "category",
    "Betrag": "gross",
    "Inhalt": "content",
    "Terminbeschreibung": "event_description",
}


def _ascii_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_") or "value"


def parameter_name(raw: str) -> str:
    if raw in _PROPERTY_NAMES:
        return _PROPERTY_NAMES[raw]
    if "|" in raw and not raw.startswith(("YYYY", "ISO")):
        return "value"
    if re.fullmatch(r"\d+-\d+", raw):
        return "value"
    return _ascii_identifier(raw)


def _parameter_schema(raw: str, name: str) -> dict[str, Any]:
    if raw == "ISIN":
        return {"type": "string", "pattern": "^[A-Z]{2}[A-Z0-9]{9}[0-9]$"}
    if raw in {"SHA256", "Digest"}:
        return {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    if raw == "ISO":
        return {"type": "string", "pattern": "^[A-Z]{3}$"}
    if raw == "MIC":
        return {"type": "string", "pattern": "^[A-Z0-9]{4}$"}
    if raw == "YYYY":
        return {"type": "integer", "minimum": 1970, "maximum": 2200}
    if range_match := re.fullmatch(r"(?P<low>\d+)-(?P<high>\d+)", raw):
        return {
            "type": "integer",
            "minimum": int(range_match.group("low")),
            "maximum": int(range_match.group("high")),
        }
    if "|" in raw and all(re.fullmatch(r"[A-Za-z0-9-]+", item) for item in raw.split("|")):
        return {"type": "string", "enum": raw.split("|")}
    if raw in {"ISO-8601", "YYYY-MM-DD oder ISO-8601"}:
        return {"type": "string", "minLength": 10, "maxLength": 64}
    if raw == "YYYY-MM-DD":
        return {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}
    if name in {"file", "folder", "source", "destination"}:
        return {"type": "string", "minLength": 1, "maxLength": 1024}
    if name in {"query", "body", "content", "description", "notes", "reason"}:
        return {"type": "string", "minLength": 1, "maxLength": 20000}
    if name in {"percent", "threshold", "gross"}:
        return {"type": "string", "pattern": "^-?\\d+(?:[.,]\\d+)?$", "maxLength": 40}
    return {"type": "string", "minLength": 1, "maxLength": 2048}


def _unique_parameter_name(candidate: str, used: set[str]) -> str:
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used:
        suffix += 1
    result = f"{candidate}_{suffix}"
    used.add(result)
    return result


def _parameter_bindings(tokens: list[str], stdin_raw: str | None) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    used: set[str] = set()
    if stdin_raw:
        result.append((stdin_raw, _unique_parameter_name(parameter_name(stdin_raw), used)))
    for index, token in enumerate(tokens[1:], start=1):
        for raw in _PLACEHOLDER.findall(token):
            previous = tokens[index - 1] if index else ""
            candidate = (
                _ascii_identifier(previous.removeprefix("--"))
                if previous.startswith("--")
                else parameter_name(raw)
            )
            result.append((raw, _unique_parameter_name(candidate, used)))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CompiledCommand:
    supported: bool
    bindings: tuple[tuple[str, str], ...]
    stdin_parameter: str | None = None
    exclusion_reason: str = ""


def compile_command_contract(command: str) -> CompiledCommand:
    """Validate the catalog command shape without ever executing a shell."""

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        return CompiledCommand(False, (), exclusion_reason=f"invalid-command-template:{exc}")
    if not tokens:
        return CompiledCommand(False, (), exclusion_reason="empty-command-template")
    if any(token in _SHELL_OPERATORS for token in tokens):
        return CompiledCommand(False, (), exclusion_reason="shell-operator-not-supported")

    stdin_raw: str | None = None
    if "|" in tokens:
        if tokens.count("|") != 1:
            return CompiledCommand(False, (), exclusion_reason="multiple-pipelines-not-supported")
        pipe = tokens.index("|")
        prefix = tokens[:pipe]
        tokens = tokens[pipe + 1 :]
        if len(prefix) != 3 or prefix[:2] != ["printf", "%s"]:
            return CompiledCommand(False, (), exclusion_reason="pipeline-not-supported")
        match = _PLACEHOLDER.fullmatch(prefix[2])
        if match is None:
            return CompiledCommand(False, (), exclusion_reason="stdin-template-not-parameterized")
        stdin_raw = match.group(1)

    if not tokens or tokens[0] != "./scripts/assistant.sh":
        return CompiledCommand(False, (), exclusion_reason="non-cli-contract")
    if any("`" in token or "$" in token or "\x00" in token for token in tokens):
        return CompiledCommand(False, (), exclusion_reason="unsafe-command-template")
    bindings = _parameter_bindings(tokens, stdin_raw)
    return CompiledCommand(
        True,
        bindings,
        stdin_parameter=bindings[0][1] if stdin_raw else None,
    )


def strict_argument_schema(command: str) -> tuple[dict[str, Any], CompiledCommand]:
    compiled = compile_command_contract(command)
    properties = {
        name: _parameter_schema(raw, name) for raw, name in compiled.bindings
    }
    required = [
        name for raw, name in compiled.bindings if not raw.casefold().endswith("-optional")
    ]
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": False,
    }
    return schema, compiled


def _group_kind(mode: str) -> str:
    return "read" if mode == "read" else "write"


def _allowed_claims(tool_id: str, mode: str) -> list[str]:
    claims = ["tool-status", "positive-evidence", "tool-error"]
    if tool_id == "assistant.version":
        claims.append("product-version")
    if tool_id in {"mail.search", "mail.search.local", "assistant.search"}:
        claims.append("conditional-negative")
    if mode != "read":
        claims.append("write-success-with-postcondition")
    return claims


def _route_definitions() -> list[dict[str, Any]]:
    """Small deterministic intent contract; only the user prompt is routed."""

    return [
        {
            "id": "product-version",
            "domain": "runtime",
            "patterns": [r"\bversion(?:sstand)?\b", r"\brelease\b"],
            "tool": _DOMAIN_TOOL_NAMES["runtime"]["read"],
            "operations": ["assistant.version"],
            "claim_classes": ["product-version"],
        },
        {
            "id": "mail-search",
            "domain": "mail",
            "patterns": [
                r"\b(?:e-?mails?|mails?|postfach|nachricht(?:en)?)\b",
                r"\bcorreo(?:s)?\b",
            ],
            "tool": _DOMAIN_TOOL_NAMES["mail"]["read"],
            "operations": ["mail.search", "mail.list", "mail.read"],
            "claim_classes": ["mail-state", "negative"],
        },
        {
            "id": "nextcloud-files",
            "domain": "nextcloud",
            "patterns": [r"\bnextcloud\b", r"\bwebdav\b", r"\bcloud[- ]?datei"],
            "tool": _DOMAIN_TOOL_NAMES["nextcloud"]["read"],
            "operations": ["nextcloud.list", "nextcloud.sync"],
            "claim_classes": ["remote-state", "negative"],
        },
        {
            "id": "invoices",
            "domain": "invoices",
            "patterns": [r"\brechnung(?:en)?\b", r"\binvoice(?:s)?\b", r"\bocr\b"],
            "tool": _DOMAIN_TOOL_NAMES["invoices"]["read"],
            "operations": [
                "assistant.invoices.status",
                "assistant.invoices.audit",
                "assistant.invoices.files",
                "assistant.invoices.list",
            ],
            "claim_classes": ["record-state", "remote-state", "negative"],
        },
        {
            "id": "contacts",
            "domain": "contacts",
            "patterns": [r"\bkontakt(?:e|en)?\b", r"\baddressbuch\b", r"\bcontact(?:s)?\b"],
            "tool": _DOMAIN_TOOL_NAMES["contacts"]["read"],
            "operations": [
                "nextcloud.contacts.status",
                "nextcloud.contacts.search",
                "nextcloud.contacts.list",
            ],
            "claim_classes": ["remote-state", "negative"],
        },
        {
            "id": "calendar",
            "domain": "calendar",
            "patterns": [r"\btermin(?:e|en)?\b", r"\bkalender\b", r"\bcalendar\b"],
            "tool": _DOMAIN_TOOL_NAMES["calendar"]["read"],
            "operations": [
                "nextcloud.calendar.status",
                "nextcloud.calendar.search",
                "nextcloud.calendar.list",
            ],
            "claim_classes": ["remote-state", "negative"],
        },
        {
            "id": "tasks",
            "domain": "tasks",
            "patterns": [r"\baufgabe(?:n)?\b", r"\bto-?do(?:s)?\b", r"\btasks?\b"],
            "tool": _DOMAIN_TOOL_NAMES["tasks"]["read"],
            "operations": ["nextcloud.tasks.status", "nextcloud.tasks.list"],
            "claim_classes": ["remote-state", "negative", "write-success"],
        },
        {
            "id": "orders",
            "domain": "orders",
            "patterns": [r"\bbestellung(?:en)?\b", r"\blieferung(?:en)?\b", r"\borders?\b"],
            "tool": _DOMAIN_TOOL_NAMES["orders"]["read"],
            "operations": ["nextcloud.deck.orders.status", "nextcloud.deck.orders.list"],
            "claim_classes": ["remote-state", "negative"],
        },
        {
            "id": "portfolio",
            "domain": "portfolio",
            "patterns": [
                r"\bportfolio\b",
                r"\bdepot\b",
                r"\baktie(?:n)?\b",
                r"\bwatchlist\b",
                r"\b(?:kurs|kurse|boerse|boersenplatz)\b",
                r"\b(?:isin|ticker|mapping|kursmapping|symbol)\b",
            ],
            "tool": _DOMAIN_TOOL_NAMES["portfolio"]["read"],
            "operations": [
                "portfolio.holdings",
                "portfolio.quotes.status",
                "portfolio.valuation",
                "portfolio.status",
                "portfolio.mapping.discover",
                "portfolio.mapping.suggest",
                "portfolio.research.status",
            ],
            "claim_classes": ["portfolio-state", "negative", "write-success"],
        },
        {
            "id": "runtime-jobs",
            "domain": "runtime",
            "patterns": [r"\bjob(?:s)?\b", r"\bscheduler\b", r"\bollama\b", r"\bmonitor(?:ing)?\b"],
            "tool": _DOMAIN_TOOL_NAMES["runtime"]["read"],
            "operations": [
                "assistant.jobs.status",
                "assistant.scheduler.status",
                "assistant.ollama.status",
                "assistant.monitor.status",
            ],
            "claim_classes": ["runtime-state", "write-success"],
        },
        {
            "id": "antivirus",
            "domain": "security",
            "patterns": [r"\bclamav\b", r"\bantivirus\b", r"\bvirenscan"],
            "tool": _DOMAIN_TOOL_NAMES["security"]["read"],
            "operations": ["security.antivirus.doctor", "security.antivirus.self-test"],
            "claim_classes": ["security-state"],
        },
    ]


def route_intent(prompt: str) -> dict[str, Any]:
    normalized = unicodedata.normalize("NFKC", prompt).casefold()
    routes: list[dict[str, Any]] = []
    for definition in _route_definitions():
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in definition["patterns"]):
            routes.append(
                {
                    key: value
                    for key, value in definition.items()
                    if key not in {"patterns"}
                }
            )
    routes = routes[:MAX_ROUTED_DOMAINS]
    return {
        "schema_version": SCHEMA_VERSION,
        "resolved": bool(routes),
        "routes": routes,
        "read_only_prefetch_only": True,
        "external_write_authorized": False,
    }


def _claim_patterns() -> dict[str, tuple[str, ...]]:
    return {
        "negative": (
            r"\bkeine(?:n|r|s)?\b.*\b(?:mail|nachricht|datei|termin|aufgabe|kontakt|treffer)",
            r"\bnichts gefunden\b",
            r"\bnicht vorhanden\b",
            r"\bno (?:mail|message|file|event|task|contact).*(?:found|exists)\b",
            r"\bno (?:hay|existe).*(?:correo|archivo|tarea|contacto)\b",
        ),
        "write-success": (
            r"\b(?:erfolgreich|erledigt|abgeschlossen|gesendet|verschoben|aktualisiert|angelegt)\b",
            r"\b(?:completed|sent|moved|updated|created)\b",
            r"\b(?:completad[oa]|enviad[oa]|movid[oa]|actualizad[oa]|cread[oa])\b",
        ),
        "product-version": (r"\b(?:openclaw|personal assistant)\s+(?:version\s+)?\d{4}|\b3\.4\.0-",),
    }


def classify_claims(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [
        claim
        for claim, patterns in _claim_patterns().items()
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)
    ]


def guard_claims(
    *,
    route: Mapping[str, Any],
    answer: str,
    evidence: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    routes = list(route.get("routes") or [])
    evidence_rows = list(evidence)
    issues: list[str] = []
    for item in routes:
        domain = str(item.get("domain") or "")
        operations = set(item.get("operations") or [])
        matching = [
            row
            for row in evidence_rows
            if row.get("domain") == domain and row.get("tool_id") in operations
        ]
        if not matching:
            issues.append(f"missing-current-evidence:{domain}")
    claims = classify_claims(answer)
    if "negative" in claims and not any(
        "negative" in set(row.get("allowed_claims") or []) for row in evidence_rows
    ):
        issues.append("negative-claim-not-authorized")
    if "product-version" in claims and not any(
        row.get("tool_id") == "assistant.version"
        and bool(row.get("ok"))
        and "product-version" in set(row.get("allowed_claims") or [])
        for row in evidence_rows
    ):
        issues.append("version-claim-not-authorized")
    if "write-success" in claims and any(
        "write-success" in set(item.get("claim_classes") or []) for item in routes
    ) and not any(
        bool(row.get("ok")) and "write-success" in set(row.get("allowed_claims") or [])
        for row in evidence_rows
    ):
        issues.append("write-success-not-authorized")
    return {
        "ok": not issues,
        "claims": claims,
        "issues": issues,
        "fail_closed": True,
    }


def _operation_entry(definition: Any) -> dict[str, Any]:
    argument_schema, compiled = strict_argument_schema(definition.command)
    return {
        "tool_id": definition.id,
        "domain": definition.domain,
        "description": definition.description,
        "mode": definition.mode,
        "writes_external_data": definition.writes_external_data,
        "approval": definition.approval,
        "availability": definition.availability,
        "command": definition.command,
        "argument_schema": argument_schema,
        "parameter_bindings": [
            {
                "placeholder": raw,
                "parameter": name,
                "required": name in argument_schema["required"],
            }
            for raw, name in compiled.bindings
        ],
        "stdin_parameter": compiled.stdin_parameter,
        "supported": compiled.supported,
        "exclusion_reason": compiled.exclusion_reason,
        "allowed_claims": _allowed_claims(definition.id, definition.mode),
        "error_codes": list(definition.error_codes),
    }


def _merge_group_argument_properties(selected: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a provider-friendly union while runtime validation stays operation-specific."""

    properties: dict[str, Any] = {}
    for item in selected:
        for name, definition in item["argument_schema"]["properties"].items():
            current = properties.get(name)
            if current is None or current == definition:
                properties[name] = definition
                continue
            if (
                current.get("type") == definition.get("type") == "string"
                and "enum" in current
                and "enum" in definition
            ):
                properties[name] = {
                    "type": "string",
                    "enum": sorted(set(current["enum"]) | set(definition["enum"])),
                }
                continue
            variants = list(current.get("anyOf", [current]))
            if definition not in variants:
                variants.append(definition)
            properties[name] = {"anyOf": variants}
    return properties


def _group_argument_contract(selected: list[dict[str, Any]]) -> str:
    signatures: list[str] = []
    for item in selected:
        schema = item["argument_schema"]
        required = list(schema["required"])
        optional = sorted(set(schema["properties"]) - set(required))
        fields = [*required, *(f"{name}?" for name in optional)]
        rendered = ", ".join(fields) if fields else "keine"
        signatures.append(f"{item['tool_id']}: {rendered}")
    return "; ".join(signatures)


def build_native_tool_contract() -> dict[str, Any]:
    operations = [_operation_entry(definition) for definition in tool_definitions()]
    supported = [item for item in operations if item["supported"]]
    groups: list[dict[str, Any]] = []
    for domain, names in _DOMAIN_TOOL_NAMES.items():
        for kind, native_name in names.items():
            selected = [
                item
                for item in supported
                if item["domain"] == domain and _group_kind(str(item["mode"])) == kind
            ]
            if not selected:
                continue
            group_operations = [item["tool_id"] for item in selected]
            argument_contract = _group_argument_contract(selected)
            domain_guidance = ""
            if domain == "mail" and kind == "read":
                domain_guidance = (
                    " Fuer letzte oder aktuelle Mails mail.list mit arguments.folder verwenden "
                    "(ohne andere Nutzerangabe normalerweise INBOX); fuer Absender-, Betreff- "
                    "oder Inhaltssuche mail.search mit arguments.query. mail.read erst nach einem "
                    "Treffer mit arguments.folder, arguments.message_id und "
                    "arguments.expected_subject aufrufen. Nie ein leeres arguments-Objekt senden. "
                    "Nach invalid-arguments hoechstens einmal mit vollstaendigen geaenderten "
                    "Argumenten korrigieren; bei retry_allowed=false sofort stoppen."
                )
            elif domain == "portfolio" and kind == "read":
                domain_guidance = (
                    " Bei bereits bekannter ISIN immer portfolio.mapping.suggest mit "
                    "arguments.isin verwenden; portfolio.mapping.discover ist nur fuer einen "
                    "Namen oder Ticker ohne bekannte ISIN und verlangt arguments.query. "
                    "Nie mit leerem arguments-Objekt aufrufen und nie durch Websuche ersetzen."
                )
            groups.append(
                {
                    "name": native_name,
                    "domain": domain,
                    "kind": kind,
                    "description": (
                        f"Registered {domain} {kind} operations of the OpenClaw Personal Assistant. "
                        "Choose exactly one catalog operation and provide all required structured "
                        f"arguments.{domain_guidance}"
                    ),
                    # Some local providers do not expose nested JSON-Schema oneOf branches to
                    # the model reliably. Keep a simple top-level discriminator and a visible
                    # union of argument properties. executeOperation still validates the exact
                    # selected operation schema before compiling argv.
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": group_operations,
                                "description": "Exakt eine registrierte Katalogoperation auswaehlen.",
                            },
                            "arguments": {
                                "type": "object",
                                "properties": _merge_group_argument_properties(selected),
                                "additionalProperties": False,
                                "description": (
                                    "Argumente der gewaehlten Operation. Pflichtfelder nie "
                                    f"weglassen. Signaturen: {argument_contract}"
                                ),
                            },
                        },
                        "required": ["operation", "arguments"],
                        "additionalProperties": False,
                    },
                    "operations": group_operations,
                }
            )
    release = json.loads(release_path().read_text(encoding="utf-8"))
    catalog_payload = json.dumps(operations, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "plugin_id": PLUGIN_ID,
        "plugin_path": PLUGIN_PATH,
        "product_version": str(release.get("version") or "unknown"),
        "catalog_sha256": hashlib.sha256(catalog_payload.encode("utf-8")).hexdigest(),
        "catalog_tool_count": len(operations),
        "supported_operation_count": len(supported),
        "excluded_operation_count": len(operations) - len(supported),
        "operations": operations,
        "native_tools": groups,
        "routes": _route_definitions(),
        "claim_patterns": {key: list(value) for key, value in _claim_patterns().items()},
        "limits": {
            "max_output_bytes": 1_000_000,
            "max_error_bytes": 8_000,
            "tool_timeout_seconds": 120,
            "approval_timeout_seconds": 120,
            "approval_ttl_seconds": 180,
            "max_routed_domains": MAX_ROUTED_DOMAINS,
        },
        "security": {
            "shell": False,
            "argv_only": True,
            "writes_require_allow_once": True,
            "conversation_guard_fail_closed": True,
            "router_may_execute_writes": False,
            "content_may_change_route": False,
            "tool_loop_detection_required": True,
        },
    }


def render_contract() -> str:
    return json.dumps(build_native_tool_contract(), ensure_ascii=False, indent=2) + "\n"


def write_contract(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_contract(), encoding="utf-8", newline="\n")


def verify_contract(path: Path) -> list[str]:
    if not path.is_file():
        return [f"Native Toolvertrag fehlt: {path}"]
    actual = path.read_text(encoding="utf-8")
    expected = render_contract()
    if actual != expected:
        return ["Native Toolvertrag ist nicht deterministisch aktuell"]
    try:
        payload = json.loads(actual)
    except json.JSONDecodeError as exc:
        return [f"Native Toolvertrag ist ungueltiges JSON: {exc}"]
    names = [item["name"] for item in payload.get("native_tools", [])]
    if len(names) != len(set(names)):
        return ["Native Toolnamen sind nicht eindeutig"]
    exposed = {
        operation
        for item in payload.get("native_tools", [])
        for operation in item.get("operations", [])
    }
    supported = {
        item["tool_id"] for item in payload.get("operations", []) if item.get("supported")
    }
    if exposed != supported:
        return ["Native Toolgruppen und unterstuetzte Katalogoperationen driften"]
    return []


def status(contract_path: Path) -> dict[str, Any]:
    errors = verify_contract(contract_path)
    payload: dict[str, Any] = {}
    if contract_path.is_file():
        parsed = json.loads(contract_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            payload = parsed
    return {
        "ok": not errors,
        "schema_version": payload.get("schema_version"),
        "contract_id": payload.get("contract_id"),
        "product_version": payload.get("product_version"),
        "catalog_sha256": payload.get("catalog_sha256"),
        "catalog_tool_count": payload.get("catalog_tool_count", 0),
        "supported_operation_count": payload.get("supported_operation_count", 0),
        "excluded_operation_count": payload.get("excluded_operation_count", 0),
        "native_tool_count": len(payload.get("native_tools", [])),
        "plugin_id": PLUGIN_ID,
        "plugin_path": PLUGIN_PATH,
        "argv_only": True,
        "shell": False,
        "errors": errors,
    }


__all__ = [
    "CONTRACT_ID",
    "PLUGIN_ID",
    "PLUGIN_PATH",
    "SCHEMA_VERSION",
    "build_native_tool_contract",
    "classify_claims",
    "compile_command_contract",
    "guard_claims",
    "render_contract",
    "route_intent",
    "status",
    "strict_argument_schema",
    "verify_contract",
    "write_contract",
]
