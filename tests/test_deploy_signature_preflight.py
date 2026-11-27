"""Deployment must leave writers running when candidate signature prerequisites fail."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_talk_proxy_lifecycle import _deployment, _run, _stub

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "docker/scripts/check-maintenance-runtime.sh"


@pytest.mark.parametrize("health_exit", [1, 23])
def test_stale_or_unreadable_candidate_signatures_do_not_stop_writers(
    tmp_path: Path,
    health_exit: int,
) -> None:
    env, env_file, scripts = _deployment(tmp_path)
    before = env_file.read_bytes()
    checks = tmp_path / "checks.log"
    env["SIGNATURE_TEST_CHECKS"] = str(checks)
    env["SIGNATURE_TEST_EXIT"] = str(health_exit)
    _stub(
        scripts / "check-maintenance-runtime.sh",
        'printf "%s\\n" "$*" >> "$SIGNATURE_TEST_CHECKS"\n'
        'if [ "${2:-}" = --signatures ]; then exit "$SIGNATURE_TEST_EXIT"; fi\n',
    )
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    assert result.returncode == health_exit, result.stdout + result.stderr
    assert " stop " not in Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert not Path(env["TALK_TEST_PREVIOUS"]).exists()
    assert env_file.read_bytes() == before
    assert checks.read_text().splitlines() == [
        "fixture.invalid/new:maintenance",
        "fixture.invalid/new:maintenance --signatures",
    ]


def test_fresh_candidate_signatures_allow_the_existing_backup_boundary(tmp_path: Path) -> None:
    env, _, scripts = _deployment(tmp_path)
    _stub(
        scripts / "check-maintenance-runtime.sh",
        'if [ "${2:-}" = --signatures ]; then '
        'printf "candidate-signatures-fresh\\n" >> "$TALK_TEST_DOCKER_LOG"; fi\n',
    )
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    # The fixture intentionally fails backup: a successful early check must reach
    # the existing rollback-safe lifecycle, never skip or replace backup.
    assert result.returncode == 19, result.stdout + result.stderr
    commands = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert commands.index("candidate-signatures-fresh") < commands.index(" stop setup-service")
    assert Path(env["TALK_TEST_PREVIOUS"]).exists()


def _signature_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    binary = tmp_path / "docker"
    commands = tmp_path / "docker.jsonl"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['SIGNATURE_TEST_COMMANDS'], 'a') as log:\n"
        "    log.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['volume', 'inspect']:\n"
        "    if os.environ.get('SIGNATURE_TEST_VOLUME_MISSING') == 'true':\n"
        "        raise SystemExit(1)\n"
        "    print(os.environ.get('SIGNATURE_TEST_VOLUME', "
        "'openclaw-agent_clamav-db openclaw-agent clamav-db'))\n"
        "    raise SystemExit(0)\n"
        "if args and args[0] == 'run':\n"
        "    if 'personal_assistant.clamav_health' in args:\n"
        "        raise SystemExit(int(os.environ.get('SIGNATURE_TEST_HEALTH_EXIT', '0')))\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(86)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    env = os.environ.copy()
    env.update({"PATH": f"{tmp_path}:{env['PATH']}", "SIGNATURE_TEST_COMMANDS": str(commands)})
    env.pop("COMPOSE_PROJECT_NAME", None)
    env.pop("CLAMAV_SIGNATURE_MAX_AGE_SECONDS", None)
    return env, commands


@pytest.mark.parametrize("exit_code", [0, 1])
def test_signature_mode_uses_only_candidate_health_with_controlled_read_only_volume(
    tmp_path: Path,
    exit_code: int,
) -> None:
    env, log = _signature_environment(tmp_path)
    env["SIGNATURE_TEST_HEALTH_EXIT"] = str(exit_code)
    result = subprocess.run(
        [str(CHECKER), "fixture.invalid/candidate:maintenance", "--signatures"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0][:2] == ["volume", "inspect"]
    assert calls[0][-1] == "openclaw-agent_clamav-db"
    run = calls[1]
    assert run[0] == "run" and "--rm" in run and "--read-only" in run
    assert run[run.index("--network") + 1] == "none"
    assert run[run.index("--user") + 1] == "100:101"
    assert run[run.index("--cap-drop") + 1] == "ALL"
    assert run[run.index("--security-opt") + 1] == "no-new-privileges:true"
    assert run.count("--mount") == 1
    assert run[run.index("--mount") + 1] == (
        "type=volume,src=openclaw-agent_clamav-db,dst=/var/lib/clamav,readonly,volume-nocopy"
    )
    assert "CLAMAV_DATABASE_DIR=/var/lib/clamav" in run
    assert "CLAMAV_SIGNATURE_MAX_AGE_SECONDS=172800" in run
    assert run[-5:] == [
        "python3",
        "fixture.invalid/candidate:maintenance",
        "-P",
        "-m",
        "personal_assistant.clamav_health",
    ]
    assert all(
        "freshclam" not in call and "personal_assistant.clamav_transport" not in call for call in calls
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"SIGNATURE_TEST_VOLUME_MISSING": "true"},
        {"SIGNATURE_TEST_VOLUME": "openclaw-agent_clamav-db other-project clamav-db"},
        {"COMPOSE_PROJECT_NAME": "invalid/project"},
    ],
)
def test_unknown_signature_volume_never_creates_or_mounts_a_database(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    env, log = _signature_environment(tmp_path)
    env.update(overrides)
    result = subprocess.run(
        [str(CHECKER), "fixture.invalid/candidate:maintenance", "--signatures"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert not any(call[0] == "run" for call in calls)


@pytest.mark.parametrize("initial_runtime", ["fresh", "legacy-systemd"])
def test_initial_signature_bootstrap_remains_after_backup(tmp_path: Path, initial_runtime: str) -> None:
    env, env_file, scripts = _deployment(tmp_path)
    checks = tmp_path / "initial-checks.log"
    env["SIGNATURE_TEST_CHECKS"] = str(checks)
    _stub(
        scripts / "check-maintenance-runtime.sh",
        'printf "%s\\n" "$*" >> "$SIGNATURE_TEST_CHECKS"\nif [ "${2:-}" = --signatures ]; then exit 44; fi\n',
    )
    extra = []
    if initial_runtime == "fresh":
        state = Path(env["OPENCLAW_STATE_DIR"])
        shutil.rmtree(state)
        state.mkdir()
        extra = ["--fresh-nextcloud-installation"]
    else:
        env_file.write_text(
            env_file.read_text().replace(
                "OPENCLAW_CURRENT_RUNTIME=docker", "OPENCLAW_CURRENT_RUNTIME=legacy-systemd"
            )
        )
        legacy = tmp_path / "legacy"
        (legacy / "workspace/scripts").mkdir(parents=True)
        (legacy / "openclaw.json").write_text("{}")
        _stub(legacy / "workspace/scripts/assistant.sh", "exit 0\n")
        _stub(legacy / "workspace/scripts/mail-agent.sh", "exit 0\n")
        env["OPENCLAW_LEGACY_HOME"] = str(legacy)
        _stub(
            Path(env["TALK_TEST_BIN"]) / "systemctl",
            'case "$*" in "--user show-environment"|"--user stop "*) exit 0;; esac\nexit 1\n',
        )
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
            *extra,
        ],
        env,
    )
    assert result.returncode == 19, result.stdout + result.stderr
    assert checks.read_text().splitlines() == ["fixture.invalid/new:maintenance"]
    assert Path(env["TALK_TEST_PREVIOUS"]).exists()


def test_explicit_compose_project_keeps_its_selected_signature_volume(tmp_path: Path) -> None:
    env, log = _signature_environment(tmp_path)
    env.update(
        {
            "COMPOSE_PROJECT_NAME": "selected-instance",
            "SIGNATURE_TEST_VOLUME": "selected-instance_clamav-db selected-instance clamav-db",
        }
    )
    result = subprocess.run(
        [str(CHECKER), "fixture.invalid/candidate:maintenance", "--signatures"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0][-1] == "selected-instance_clamav-db"
    assert (
        "type=volume,src=selected-instance_clamav-db,dst=/var/lib/clamav,readonly,volume-nocopy" in calls[1]
    )
