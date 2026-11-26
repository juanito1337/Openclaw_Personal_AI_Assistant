"""Manual native dry-run budget reaches the actual mail worker unchanged."""

import shlex

import pytest

from personal_assistant.cli import parser
from personal_assistant.cli_handlers.mail import run_external
from personal_assistant.tool_registry import tool_definitions


@pytest.mark.parametrize("source", ["default-cli", "catalog", "short-probe"])
def test_manual_dry_run_has_cooperative_budget_at_worker_boundary(monkeypatch, source):
    # A missing budget used to silently inherit the mail worker's 2400 seconds,
    # exceeding the native invocation and evidence windows.
    command = ["mail", "dry-run"]
    expected_runtime, expected_reserve = "900", "60"
    if source == "catalog":
        definition = next(item for item in tool_definitions() if item.id == "mail.dry-run")
        command = shlex.split(definition.command)[1:]
    elif source == "short-probe":
        command += ["--limit", "5", "--max-runtime", "90", "--shutdown-reserve", "15"]
        expected_runtime, expected_reserve = "90", "15"
    captured = {}

    def child(argv, environment, initial, *, reads_nextcloud):
        captured.update(argv=argv, environment=environment, reads_nextcloud=reads_nextcloud)
        return 1  # Preserve incomplete/failed worker outcome; never convert to success.

    monkeypatch.setattr("personal_assistant.cli_handlers.mail.run_mail_child", child)
    assert run_external(parser().parse_args(command)) == 1
    argv = captured["argv"]
    assert argv[3:] == [
        "run", "--dry-run", "--no-digest", "--limit", "5" if source == "short-probe" else "20",
        "--max-runtime", expected_runtime, "--shutdown-reserve", expected_reserve,
    ]
    assert captured["environment"]["OPENCLAW_OLLAMA_PRIORITY"] == "interactive"
    assert captured["reads_nextcloud"] is True


@pytest.mark.parametrize("runtime,reserve", [(0, 1), (-1, 1), (900, 0), (900, -1), (60, 60), (60, 61)])
def test_invalid_manual_dry_run_budget_starts_no_worker(monkeypatch, runtime, reserve):
    calls = []
    monkeypatch.setattr(
        "personal_assistant.cli_handlers.mail.run_mail_child", lambda *a, **k: calls.append(a),
    )
    args = parser().parse_args([
        "mail", "dry-run", "--max-runtime", str(runtime), "--shutdown-reserve", str(reserve),
    ])
    with pytest.raises(ValueError, match="shutdown-reserve"):
        run_external(args)
    assert calls == []
