#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_assistant.agent_tool_orchestration import (  # noqa: E402
    PLUGIN_ID,
    render_contract,
    verify_contract,
    write_contract,
)

PLUGIN_ROOT = ROOT / "docker/openclaw-personal-assistant-plugin"
CONTRACT = PLUGIN_ROOT / "generated-tools.json"
MANIFEST = PLUGIN_ROOT / "openclaw.plugin.json"
PACKAGE = PLUGIN_ROOT / "package.json"
EVIDENCE_SCHEMA = PLUGIN_ROOT / "evidence.schema.json"


def render_manifest() -> str:
    payload = json.loads(render_contract())
    tool_names = [item["name"] for item in payload["native_tools"]]
    return json.dumps(
        {
            "id": PLUGIN_ID,
            "name": "Personal Assistant Tools",
            "description": (
                "Native structured tools, deterministic routing and evidence guards "
                "for the local Personal Assistant"
            ),
            "version": payload["product_version"],
            "contracts": {"tools": tool_names},
            "activation": {"onStartup": True},
            "configSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def render_package() -> str:
    payload = json.loads(render_contract())
    return json.dumps(
        {
            "name": "@openclaw-local/personal-assistant-tools",
            "version": payload["product_version"],
            "private": True,
            "type": "module",
            "openclaw": {"extensions": ["./index.js"]},
            "peerDependencies": {"openclaw": "2026.7.1"},
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def render_evidence_schema() -> str:
    payload = json.loads(render_contract())
    supported = [item for item in payload["operations"] if item["supported"]]
    tool_ids = sorted(item["tool_id"] for item in supported)
    approvals = sorted({item["approval"] for item in supported})
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://openclaw.local/schemas/personal-assistant-evidence-v1.json",
        "title": "Personal Assistant current-turn evidence",
        "type": "object",
        "required": [
            "tool_id",
            "tool_version",
            "run_id",
            "turn_id",
            "domain",
            "mode",
            "ok",
            "complete",
            "freshness",
            "coverage",
            "results_may_be_truncated",
            "error",
            "approval",
            "postcondition_verified",
            "allowed_claims",
            "next_actions",
        ],
        "properties": {
            "tool_id": {"type": "string", "enum": tool_ids},
            "tool_version": {"const": payload["schema_version"]},
            "run_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "turn_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "domain": {"type": "string", "enum": sorted(_domain_names(supported))},
            "mode": {"type": "string", "enum": ["read", "local-write", "write"]},
            "ok": {"type": "boolean"},
            "complete": {"type": "boolean"},
            "freshness": {"type": ["string", "number", "object", "null"]},
            "coverage": {"type": ["number", "object", "array", "null"]},
            "results_may_be_truncated": {"type": "boolean"},
            "error": {
                "type": ["string", "null"],
                "enum": [
                    None,
                    "configuration-error",
                    "incomplete-result",
                    "invalid-arguments",
                    "operation-failed",
                    "permission-denied",
                    "timeout",
                ],
            },
            "approval": {"type": "string", "enum": approvals},
            "postcondition_verified": {"type": "boolean"},
            "allowed_claims": {
                "type": "array",
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "enum": [
                        "negative",
                        "positive-evidence",
                        "product-version",
                        "tool-error",
                        "tool-status",
                        "write-success",
                    ],
                },
            },
            "next_actions": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": tool_ids},
            },
        },
        "additionalProperties": False,
    }
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def _domain_names(operations: list[dict[str, object]]) -> set[str]:
    return {str(item["domain"]) for item in operations}


def verify() -> list[str]:
    errors = verify_contract(CONTRACT)
    if not MANIFEST.is_file():
        errors.append(f"OpenClaw-Pluginmanifest fehlt: {MANIFEST}")
    elif MANIFEST.read_text(encoding="utf-8") != render_manifest():
        errors.append("OpenClaw-Pluginmanifest ist nicht deterministisch aktuell")
    if not PACKAGE.is_file():
        errors.append(f"OpenClaw-Pluginpaket fehlt: {PACKAGE}")
    elif PACKAGE.read_text(encoding="utf-8") != render_package():
        errors.append("OpenClaw-Pluginpaket ist nicht deterministisch aktuell")
    if not EVIDENCE_SCHEMA.is_file():
        errors.append(f"Evidenzschema fehlt: {EVIDENCE_SCHEMA}")
    elif EVIDENCE_SCHEMA.read_text(encoding="utf-8") != render_evidence_schema():
        errors.append("Evidenzschema ist nicht deterministisch aktuell")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Native M13-Agentenwerkzeuge generieren")
    parser.add_argument("command", choices=("generate", "verify"))
    args = parser.parse_args()
    if args.command == "generate":
        PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
        write_contract(CONTRACT)
        MANIFEST.write_text(render_manifest(), encoding="utf-8", newline="\n")
        PACKAGE.write_text(render_package(), encoding="utf-8", newline="\n")
        EVIDENCE_SCHEMA.write_text(render_evidence_schema(), encoding="utf-8", newline="\n")
    errors = verify()
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "ok": True,
                "catalog_operations": payload["catalog_tool_count"],
                "native_tools": len(payload["native_tools"]),
                "excluded_operations": payload["excluded_operation_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
