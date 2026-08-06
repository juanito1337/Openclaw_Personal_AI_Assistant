#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_assistant.tool_registry import tool_definitions  # noqa: E402

DEFAULT_OUTPUT = ROOT / "skills/personal-assistant/references/tool-contract.md"


def _cell(value: object) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def render() -> str:
    release = json.loads((ROOT / "RELEASE.json").read_text(encoding="utf-8"))["version"]
    grouped: dict[str, list[object]] = {}
    for definition in tool_definitions():
        grouped.setdefault(definition.domain, []).append(definition)
    lines = [
        "# Generierter Skill-Toolvertrag",
        "",
        f"Release: `{release}`. Quelle: typisierte Tooldefinitionen unter",
        "`personal_assistant/tool_catalog/` und `RELEASE.json`. Nicht manuell bearbeiten;",
        "`python3 scripts/generate-skill-tool-contract.py` erzeugt diese Datei deterministisch.",
        "Die statische Liste belegt keine live erteilte Berechtigung; dafuer immer",
        "`./scripts/assistant.sh tools list` und `./scripts/assistant.sh capabilities` lesen.",
        "",
    ]
    for domain, definitions in grouped.items():
        lines.extend(
            [
                f"## {domain}",
                "",
                "| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |",
                "|---|---|---:|---|---|---|---|",
            ]
        )
        for definition in definitions:
            lines.append(
                "| `{}` | `{}` | {} | `{}` | `{}` | `{}` | `{}` |".format(
                    _cell(definition.id),
                    _cell(definition.mode),
                    "ja" if definition.writes_external_data else "nein",
                    _cell(definition.approval),
                    _cell(definition.availability),
                    _cell(definition.command),
                    _cell(definition.test_anchor),
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Skill-Projektion des Toolvertrags erzeugen")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    output = args.output.resolve()
    if args.check:
        actual = output.read_text(encoding="utf-8") if output.exists() else ""
        if actual != expected:
            print(f"Skill-Toolvertrag ist veraltet: {output}", file=sys.stderr)
            return 1
        print(f"Skill-Toolvertrag konsistent: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding="utf-8")
    print(f"Skill-Toolvertrag aktualisiert: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
