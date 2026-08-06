from __future__ import annotations

from personal_assistant.contracts.tools import ToolDefinition, define

TOOLS: tuple[ToolDefinition, ...] = (
    define(
        id="security.antivirus.doctor",
        domain="security",
        description="ClamAV-Dienst, Signaturstand und einen lokalen Testscan pruefen",
        command="./scripts/assistant.sh security antivirus doctor",
        mode="read",
        writes_external_data=False,
        approval="none",
        availability="always",
        documentation_anchor="AGENTS.md#host-antivirus-and-attachment-gate",
        test_anchor="tests/test_antivirus_tool.py",
    ),
    define(
        id="security.antivirus.self-test",
        domain="security",
        description="Harmlosen EICAR-Test ausfuehren und die Malware-Erkennung nachweisen",
        command="./scripts/assistant.sh security antivirus self-test",
        mode="read",
        writes_external_data=False,
        approval="none",
        availability="always",
        documentation_anchor="AGENTS.md#host-antivirus-and-attachment-gate",
        test_anchor="tests/test_antivirus_tool.py",
    ),
    define(
        id="security.antivirus.scan",
        domain="security",
        description="Datei aus der kontrollierten Workspace-Outbox vor weiterer Verwendung auf Schadsoftware pruefen",
        command='./scripts/assistant.sh security antivirus scan --file "personal_assistant/data/workspace_outbox/<Datei>"',
        mode="read",
        writes_external_data=False,
        approval="host-antivirus-read-only",
        availability="always",
        documentation_anchor="AGENTS.md#host-antivirus-and-attachment-gate",
        test_anchor="tests/test_antivirus_tool.py",
    ),
)
