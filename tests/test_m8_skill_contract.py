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
    assert "portfolio/stocks/holdings/quotes" in description.group(1)
    assert "before memory, workspace or shell search" in description.group(1)
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


def test_skill_routes_registered_domain_reads_before_generic_fallbacks() -> None:
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    references = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((SKILL / "references").glob("*.md"))
        if path.name != "tool-contract.md"
    )
    normalized_skill = " ".join(skill.split())
    normalized_references = " ".join(references.split())
    catalog = {definition.id: definition for definition in tool_definitions()}
    first_evidence_tools = {
        "runtime": ("assistant.version", "assistant.status", "assistant.search"),
        "portfolio": ("portfolio.status", "portfolio.holdings", "portfolio.valuation"),
        "security": ("security.antivirus.doctor",),
        "nextcloud": ("nextcloud.list",),
        "mail": ("mail.list", "mail.search", "mail.read"),
        "contacts": ("nextcloud.contacts.status", "nextcloud.contacts.list"),
        "calendar": ("nextcloud.calendar.status", "nextcloud.calendar.list"),
        "tasks": ("nextcloud.tasks.status", "nextcloud.tasks.list"),
        "orders": ("nextcloud.deck.orders.status", "nextcloud.deck.orders.list"),
        "invoices": ("assistant.invoices.status", "assistant.invoices.list"),
    }

    assert set(first_evidence_tools) == {definition.domain for definition in catalog.values()}
    for domain, tool_ids in first_evidence_tools.items():
        for tool_id in tool_ids:
            assert tool_id in catalog, (domain, tool_id)
            assert f"`{tool_id}`" in skill, (domain, tool_id)

    assert "they never prove that registered data or a capability is absent" in normalized_skill
    assert (
        "Only a successful registered holdings result may establish" in normalized_references
    )
    assert (
        "not memory, local workspace files or generic shell search" in normalized_references
    )
    assert "Eigene Aktien, Wertpapiere und Depotpositionen" in catalog[
        "portfolio.holdings"
    ].description


def test_no_second_agent_skill_or_independent_command_list_remains() -> None:
    assert not (ROOT / "skills/mail-chief-of-staff").exists()
    commands = (SKILL / "references/commands.md").read_text(encoding="utf-8")
    assert "tool-contract.md" in commands
    assert "./scripts/assistant.sh" not in commands
