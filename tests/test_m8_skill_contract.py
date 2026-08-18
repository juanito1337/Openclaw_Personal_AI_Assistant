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
    assert (
        "Use for Jan's OpenClaw Personal Assistant product version/release/update/status"
        in description.group(1)
    )
    assert "portfolio/stocks/holdings/quotes" in description.group(1)
    assert "not a dotted tool ID" in description.group(1)
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


def test_unqualified_version_question_routes_to_verified_product_release() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    runtime = " ".join((SKILL / "references/runtime-security.md").read_text(encoding="utf-8").split())
    catalog = {definition.id: definition for definition in tool_definitions()}
    version_tool = catalog["assistant.version"]

    assert '"Welche Version verwendest du?"' in skill
    assert '"What version are you?"' in skill
    assert "OpenClaw Local Personal Assistant product release" in skill
    assert "Never use `openclaw --version`" in skill
    assert "never answer `OpenClaw 2026.7.1`" in skill
    assert "An unqualified version question always means" in runtime
    assert version_tool.command == "./scripts/assistant.sh version --verify"
    assert "Produktrelease" in version_tool.description
    assert "keine eingebettete Core-, Plugin- oder CLI-Version" in version_tool.description


def test_skill_routes_registered_domain_commands_before_generic_fallbacks() -> None:
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
        "runtime": ("version --verify", "status", 'search "<Suchbegriff>"'),
        "portfolio": (
            "portfolio status",
            "portfolio holdings",
            "portfolio quotes status",
            "portfolio quotes refresh",
            "portfolio valuation",
        ),
        "security": ("security antivirus doctor",),
        "nextcloud": ('nextcloud list --path "Assistent"',),
        "mail": ("mail list ...", "mail search ...", "mail read ..."),
        "contacts": ("contacts status", "contacts list ..."),
        "calendar": ("calendar status", "calendar list ..."),
        "tasks": ("tasks status", "tasks list ..."),
        "orders": ("orders status", "orders list ..."),
        "invoices": ("invoices status", "invoices list ..."),
    }

    assert set(first_evidence_tools) == {definition.domain for definition in catalog.values()}
    for domain, command_suffixes in first_evidence_tools.items():
        for command_suffix in command_suffixes:
            assert f"`{command_suffix}`" in skill, (domain, command_suffix)

    assert "they never prove that registered data or a capability is absent" in normalized_skill
    assert "Only a successful registered holdings result may establish" in normalized_references
    assert "not memory, local workspace files or generic shell search" in normalized_references
    assert "Eigene Aktien, Wertpapiere und Depotpositionen" in catalog["portfolio.holdings"].description


def test_skill_distinguishes_tool_ids_from_executable_commands() -> None:
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    contract = (SKILL / "references/tool-contract.md").read_text(encoding="utf-8")
    installed_launcher = "/opt/openclaw-agent/scripts/assistant.sh"

    assert "A dotted Tool-ID identifies a catalog entry; it is never CLI syntax" in skill
    assert f"{installed_launcher} portfolio holdings" in skill
    assert f"{installed_launcher} portfolio.holdings" in skill
    assert "Invalid; never execute this form" in skill
    assert "Tool-IDs in der ersten Spalte sind ausschliesslich Bezeichner" in contract
    assert "`assistant.sh <gepunktete-tool-id>`" in contract
    assert "`./scripts/assistant.sh portfolio holdings`" in contract


def test_skill_refreshes_stale_quotes_before_claiming_current_prices() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())

    expected_refresh_flow = (
        "`portfolio quotes status`; if due, stale or missing and configured, "
        "`portfolio quotes refresh`; then `portfolio valuation`"
    )
    assert expected_refresh_flow in skill
    assert "first use `portfolio quotes status`" in portfolio
    assert "use `portfolio quotes refresh`; then use `portfolio valuation`" in portfolio
    assert "`--force` is only for an explicitly requested diagnostic refresh" in portfolio
    assert "Never guess either value or claim an old snapshot price is current" in portfolio


def test_skill_requires_current_portfolio_values_to_be_reported_in_eur() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())

    assert "report its EUR values only" in skill
    assert "report `price_eur`, not an unconverted foreign amount" in skill
    assert "Treat EUR as the mandatory reporting currency" in portfolio
    assert "Report `price_eur` from `portfolio quotes get`, and report `current_price_eur`" in portfolio
    assert "Never calculate a conversion in the model" in portfolio
    assert "The native `price` and `currency` are source context" in portfolio
    assert "every required `EUR<currency>.FOREX` pair" in portfolio


def test_failed_portfolio_status_requires_complete_diagnosis_and_next_action() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())

    for contract in (skill, portfolio):
        assert "`portfolio doctor`" in contract
        assert "`jobs check --target all --deep`" in contract
        assert "`configuration_ok`" in contract
        assert "`api_key_present`" in contract
    assert "do not answer from quote status alone" in skill
    assert "A failure explanation without the next bounded action is incomplete" in skill
    assert "request one exact mapping approval" in portfolio
    assert "Secret provisioning remains Jan's host action" in portfolio


def test_critical_quote_failure_never_guesses_mapping_or_offers_web_fallback() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())

    for contract in (skill, portfolio):
        assert "Aktienkurs fehlt oder ist kritisch veraltet" in contract
        assert "`mapping_confirmed`" in contract
        assert "`provider_symbol`" in contract
        assert "`registered_next_commands`" in contract
        assert "generic web search" in contract
    assert "the stored provider mapping is not an unresolved cause" in skill
    assert "never propose alternate tickers" in skill
    assert "Never invent or mention plausible alternatives such as `RHM.DE` or `RHN`" in portfolio
    assert "never call a temporary provider outage without registered evidence" in portfolio


def test_missing_mapping_uses_provider_bounded_ollama_suggestion() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())
    runtime = " ".join((SKILL / "references/runtime-security.md").read_text(encoding="utf-8").split())

    command = '`portfolio mapping suggest --isin "<ISIN>"`'
    assert command in skill
    assert command in portfolio
    assert command in runtime
    assert "select only a returned candidate plus one of its allowlisted MICs" in portfolio
    assert "reject every symbol, currency, candidate ID or MIC" in portfolio
    assert "Treat the result as an unstored proposal" in portfolio
    assert "explicit approval" in portfolio


def test_new_watchlist_security_is_discovered_before_asking_for_isin() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())

    command = '`portfolio mapping suggest --query "<Unternehmen-oder-Symbol>"`'
    assert command in skill
    assert command in portfolio
    assert "Do not ask Jan for the ISIN before this registered lookup was attempted" in portfolio
    assert "multiple distinct identities fail closed" in portfolio
    assert "Name search and proposal remain read-only" in portfolio


def test_agent_executes_approved_portfolio_workflow_instead_of_delegating_it() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())

    assert "Execute registered assistant commands yourself" in skill
    assert "Never delegate them to Jan as `docker exec` or shell instructions" in skill
    assert "present one bounded action and wait" in skill
    assert (
        "Run the registered setup, mapping, doctor, refresh, status, valuation and job commands yourself"
    ) in portfolio
    assert "never ask Jan to copy a `docker exec` wrapper" in portfolio
    assert "exact ISIN, name, symbol, MIC and currency" in portfolio
    assert "complete `next_action.command` verbatim" in portfolio
    assert "`portfolio mapping add` is not a command" in portfolio
    assert "`next_action.command` from that unchanged proposal verbatim" in skill
    assert "`jobs on portfolio` yourself" in portfolio
    assert "`jobs status --target portfolio --deep`" in portfolio
    assert "Do not broaden an approval to another ISIN, mapping, job or permission" in portfolio


def test_skill_forbids_configuration_patch_fallback_after_tool_failure() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())

    assert "Runtime configuration is administrator-owned" in skill
    for tool_name in ("`read`", "`write`", "`edit`", "`apply_patch`"):
        assert tool_name in skill
    for protected in (
        "personal_assistant/tools.toml",
        "mail_agent/config.toml",
        "openclaw.json",
    ):
        assert protected in skill
    assert "Do not try `--help`, workspace file discovery or configuration edits" in skill
    assert "Never inspect or edit `personal_assistant/tools.toml`" in portfolio
    assert "explicitly approved `agent-cli` path" in portfolio


def test_research_skill_requires_provider_evidence_and_preserves_profile_authority() -> None:
    skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    portfolio = " ".join((SKILL / "references/portfolio.md").read_text(encoding="utf-8").split())
    catalog = {definition.id: definition for definition in tool_definitions()}

    assert "portfolio/stocks/holdings/quotes/research/investment philosophy" in skill
    for command in (
        "`portfolio research status`",
        "`portfolio philosophy show`",
        "`portfolio research models`",
        "`portfolio research screen`",
        '`portfolio research analyze --isin "<ISIN>" --strategy "<Modell>"`',
    ):
        assert command in portfolio
    assert "Scores, coverage, pillars, verdicts, strengths, risks and blockers" in portfolio
    assert "must never supply a missing fact, alter a metric or score" in portfolio
    assert "turn `decision=abstain` into a candidate" in portfolio
    assert "`research-candidate` means only that the fixed research threshold was met" in portfolio
    assert "it never mutates the declared profile" in portfolio
    assert "Praise and criticism may be stated only" in portfolio
    assert catalog["portfolio.research.screen"].mode == "local-write"
    assert not catalog["portfolio.research.screen"].writes_external_data
    assert catalog["portfolio.philosophy.set"].approval == ("explicit-user-investment-profile-change")
    assert catalog["portfolio.philosophy.feedback"].approval == ("explicit-user-investment-feedback")


def test_no_second_agent_skill_or_independent_command_list_remains() -> None:
    assert not (ROOT / "skills/mail-chief-of-staff").exists()
    commands = (SKILL / "references/commands.md").read_text(encoding="utf-8")
    assert "tool-contract.md" in commands
    assert "./scripts/assistant.sh" not in commands
