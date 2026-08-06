from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from personal_assistant.tool_registry import tool_definitions

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/personal-assistant"


def _markdown_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def test_generated_skill_contract_matches_typed_registry_and_release() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate-skill-tool-contract.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    contract = (SKILL / "references/tool-contract.md").read_text(encoding="utf-8")
    release = json.loads((ROOT / "RELEASE.json").read_text(encoding="utf-8"))["version"]
    assert f"Release: `{release}`" in contract
    rows = re.findall(r"^\| `([^`]+)` \| `(read|local-write|write)` \|", contract, re.MULTILINE)
    assert len(rows) == len(tool_definitions())
    assert len({tool_id for tool_id, _ in rows}) == len(rows)
    for definition in tool_definitions():
        expected = (
            f"| `{definition.id}` | `{definition.mode}` | "
            f"{'ja' if definition.writes_external_data else 'nein'} | `{definition.approval}` |"
        )
        assert expected in contract, definition.id
        assert f"`{_markdown_cell(definition.command)}`" in contract, definition.id


def test_skill_trigger_is_short_precise_and_routes_every_domain() -> None:
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = skill.split("---", 2)[1]
    description = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    assert description is not None
    assert 120 <= len(description.group(1)) <= 360
    assert "Use when Jan asks" in description.group(1)
    for name in (
        "runtime-security.md",
        "mail.md",
        "groupware.md",
        "records.md",
        "portfolio.md",
        "tool-contract.md",
    ):
        assert f"references/{name}" in skill
        assert (SKILL / "references" / name).is_file()


def test_no_second_agent_skill_or_independent_command_list_remains() -> None:
    assert not (ROOT / "skills/mail-chief-of-staff").exists()
    commands = (SKILL / "references/commands.md").read_text(encoding="utf-8")
    assert "tool-contract.md" in commands
    assert "./scripts/assistant.sh" not in commands
