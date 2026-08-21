from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from mail_agent.himalaya import HimalayaClient

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "docker/himalaya-agent-guard.sh"


class _UnusedRunner:
    pass


def _client(binary: str) -> HimalayaClient:
    config = SimpleNamespace(
        mailbox=SimpleNamespace(
            himalaya_binary=binary,
            account="gmx",
        )
    )
    return HimalayaClient(config, _UnusedRunner())  # type: ignore[arg-type]


def test_raw_himalaya_mail_search_is_blocked_with_registered_next_step() -> None:
    result = subprocess.run(
        ["sh", str(GUARD), "envelope", "list", "--account", "synthetic"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 64
    assert "/opt/openclaw-agent/scripts/assistant.sh mail search" in result.stderr
    assert "Keine Maildaten mit grep" in result.stderr
    assert result.stdout == ""


def test_registered_connector_uses_verified_internal_binary(monkeypatch) -> None:
    internal = "/usr/local/libexec/openclaw/himalaya"
    monkeypatch.setenv("OPENCLAW_HIMALAYA_BINARY", internal)

    assert _client("himalaya")._prefix()[:2] == [internal, "--account"]
    assert _client("/usr/local/bin/himalaya")._prefix()[:2] == [internal, "--account"]


def test_custom_himalaya_binary_is_not_silently_replaced(monkeypatch) -> None:
    monkeypatch.setenv(
        "OPENCLAW_HIMALAYA_BINARY",
        "/usr/local/libexec/openclaw/himalaya",
    )

    assert _client("/fixture/custom-himalaya")._prefix()[0] == "/fixture/custom-himalaya"


def test_mail_contract_forbids_raw_client_and_shell_filter_fallback() -> None:
    contracts = "\n".join(
        (
            (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
            (ROOT / "skills/personal-assistant/SKILL.md").read_text(encoding="utf-8"),
            (ROOT / "skills/personal-assistant/references/mail.md").read_text(
                encoding="utf-8"
            ),
        )
    )

    assert "never execute the `himalaya` binary directly" in contracts
    assert "Never execute `himalaya` directly" in contracts
    assert "`himalaya` executable is an internal connector" in contracts
    assert "grep` exit code 1" in contracts
    assert 'mail search --query "<text>" --limit 50' in contracts
