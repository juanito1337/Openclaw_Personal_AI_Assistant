#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_assistant.tool_registry import tool_definitions  # noqa: E402

DEFAULT_OUTPUT = ROOT / "docs/COMMAND_REFERENCE.md"


def _cell(value: object) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def render() -> str:
    lines = [
        "# Generierte Befehlsreferenz",
        "",
        "Diese Datei wird deterministisch aus den domaenennahen typisierten Toolvertraegen erzeugt.",
        "Nicht manuell bearbeiten; `python3 scripts/generate-command-reference.py` aktualisiert sie.",
        "Der Katalog beschreibt bekannte Werkzeuge, nicht die live erteilten Rechte einer Instanz.",
        "",
        "Konfigurationsfreie Sicht: `./scripts/assistant.sh tools list --catalog` und",
        "`./scripts/assistant.sh capabilities --schema`. Live-Sicht:",
        "`./scripts/assistant.sh tools list` und `./scripts/assistant.sh capabilities`.",
        "",
    ]
    grouped: dict[str, list[object]] = {}
    for definition in tool_definitions():
        grouped.setdefault(definition.domain, []).append(definition)
    for domain, tools in grouped.items():
        lines.extend(
            [
                f"## {domain}",
                "",
                "| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |",
                "|---|---|---:|---|---|---|---|---|",
            ]
        )
        for tool in tools:
            lines.append(
                "| `{}` | `{}` | {} | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                    _cell(tool.id),
                    _cell(tool.mode),
                    "ja" if tool.writes_external_data else "nein",
                    _cell(tool.approval),
                    _cell(tool.availability),
                    _cell(tool.command),
                    _cell(tool.documentation_anchor),
                    _cell(tool.test_anchor),
                )
            )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Typisierte OpenClaw-Befehlsreferenz erzeugen")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    output = args.output.resolve()
    if args.check:
        actual = output.read_text(encoding="utf-8") if output.exists() else ""
        if actual != expected:
            print(f"Befehlsreferenz ist veraltet: {output}")
            return 1
        print(f"Befehlsreferenz konsistent: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding="utf-8")
    print(f"Befehlsreferenz aktualisiert: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
