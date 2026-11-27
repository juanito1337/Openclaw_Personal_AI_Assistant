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
        "    if 'personal_assistant.clamav_vendor' in args:\n"
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
    assert run[run.index("--network") + 1] == "bridge"
    assert run[run.index("--user") + 1] == "100:101"
    assert run[run.index("--cap-drop") + 1] == "ALL"
    assert run[run.index("--security-opt") + 1] == "no-new-privileges:true"
    assert run.count("--mount") == 1
    assert run[run.index("--mount") + 1] == (
        "type=volume,src=openclaw-agent_clamav-db,dst=/var/lib/clamav,readonly,volume-nocopy"
    )
    assert "CLAMAV_DATABASE_DIR=/var/lib/clamav" in run
    assert run[-6:] == [
        "python3",
        "fixture.invalid/candidate:maintenance",
        "-P",
        "-m",
        "personal_assistant.clamav_vendor",
        "--check",
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


@pytest.mark.parametrize("running", [True, False])
def test_backup_failure_restores_actual_updater_state(tmp_path: Path, running: bool) -> None:
    env, _, scripts = _deployment(tmp_path)
    marker = Path(env["TALK_TEST_RUNNING"] + ".updater")
    if running:
        marker.touch()
    result = _run(
        [str(scripts / "deploy.sh"), "new:runtime", "new:proxy", "new:maintenance"], env
    )
    assert result.returncode == 19, result.stderr
    assert marker.exists() is running
    log = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert log.index("stop clamav-update") < log.index("stop setup-service")
    assert (" start clamav-update" in log) is running


def test_unverified_updater_stop_leaves_other_services_running(tmp_path: Path) -> None:
    env, _, scripts = _deployment(tmp_path)
    Path(env["TALK_TEST_RUNNING"] + ".updater").touch()
    env["UPDATER_TEST_IGNORE_STOP"] = "true"
    result = _run([str(scripts / "deploy.sh"), "new:runtime", "new:proxy", "new:maintenance"], env)
    assert result.returncode != 0
    log = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert "stop clamav-update" in log and "stop setup-service" not in log
    assert "up -d --no-deps ollama-proxy" not in log
    assert "ClamAV-Updater" in result.stderr


def test_old_current_signatures_require_verified_recovery_before_stop(tmp_path: Path) -> None:
    """Old health rejects the age; signed candidate vendor comparison accepts the same files."""
    from personal_assistant.clamav_health import signature_age_seconds

    age = signature_age_seconds("ClamAV 1.4.3/28194/Mon Jan  1 00:00:00 2024", now=1704412800)
    assert age is not None and age > 172800
    env, env_file, scripts = _deployment(tmp_path)
    with env_file.open("a") as stream:
        stream.write("OPENCLAW_CLAMD_IMAGE=fixture.invalid/old:maintenance\n")
    env["TEST_LEGACY_AGE"] = str(age)
    _stub(
        scripts / "check-maintenance-runtime.sh",
        'if [[ "$1" == fixture.invalid/old:* ]]; then (( TEST_LEGACY_AGE <= 172800 )); exit; fi\n'
        'if [[ "${2:-}" == --signatures ]]; then echo "vendor-equal" >> "$TALK_TEST_DOCKER_LOG"; fi\n'
        'if [[ "${2:-}" == --recovery ]]; then exit 27; fi\n',
    )
    result = _run([str(scripts / "deploy.sh"), "new:runtime", "new:proxy", "new:maintenance"], env)
    log = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert "vendor-equal" in log
    assert result.returncode != 0 and " stop " not in log
    assert not Path(env["TALK_TEST_PREVIOUS"]).exists()


@pytest.mark.parametrize("failure", ["", "scanner", "signature-bootstrap"])
def test_recovery_exercises_only_private_writable_database(tmp_path, monkeypatch, failure):
    import runpy

    tools = runpy.run_path(str(CHECKER.with_name("check-clamav-recovery.py")))
    check = tools["check"]
    logs = []
    volume = "a" * 64
    scanner = "b" * 64
    image = "r.invalid/recovery@sha256:" + "3" * 64

    def docker(*args, **kwargs):
        logs.append(args)
        if args[:2] == ("volume", "create"):
            return volume
        if args[:2] == ("run", "-d"):
            return scanner
        if args[0] == "inspect":
            return "false" if failure == "scanner" else "true"
        if args[0] == "ps":
            return scanner if failure != "signature-bootstrap" else ""
        if "--once" in args and failure == "signature-bootstrap":
            raise RuntimeError("fake-bootstrap")
        return ""

    monkeypatch.setitem(check.__globals__, "docker", docker)
    if failure:
        with pytest.raises(RuntimeError, match="recovery-(scanner-readiness|signature-bootstrap)-failed"):
            check(image, "live")
    else:
        check(image, "live")
    for args in logs:
        if args[0] == "run":
            assert image in args and "--read-only" in args
            for item in args:
                if "src=live," in item:
                    assert ",readonly,volume-nocopy" in item
                    assert args[args.index("--network") + 1] == "none"
            if "--once" in args:
                assert args[args.index("--network") + 1] == "bridge" and "src=live," not in str(args)
    assert logs[-1] == ("volume", "rm", volume)
    if failure != "signature-bootstrap":
        assert ("rm", "-f", scanner) in logs


def test_verified_backup_keeps_observed_images_and_distinct_recovery_image(tmp_path):
    from test_nextcloud_setup_container import _initialize_policy_fixture
    from test_talk_proxy_lifecycle import _environment

    env, _, root = _environment(tmp_path)
    _initialize_policy_fixture(root)
    candidate = "fixture.invalid/new-maintenance@sha256:" + "a" * 64
    env.update(
        PREVIOUS_MAINTENANCE_IMAGE="fixture.invalid/old:maintenance",
        PREVIOUS_CLAMD_IMAGE="fixture.invalid/old:scanner",
        TARGET_MAINTENANCE_IMAGE=candidate, TARGET_CLAMD_IMAGE=candidate,
        CLAMAV_RECOVERY_IMAGE=candidate, CLAMAV_RECOVERY_REVISION="2" * 40,
        OPENCLAW_EXPECTED_RELEASE="test-release",
    )
    result = _run([str(CHECKER.with_name("backup.sh")), "recovery"], env)
    assert result.returncode == 0, result.stderr
    backup = root / "backups/releases" / result.stdout.strip().splitlines()[-1]
    manifest_file = backup / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    assert manifest["previous_maintenance_image"] == "fixture.invalid/old:maintenance"
    assert manifest["previous_clamd_image"] == "fixture.invalid/old:scanner"
    assert manifest["recovery_maintenance_image"] == manifest["recovery_clamd_image"] == candidate
    assert manifest["recovery_maintenance_revision"] == "2" * 40
    manifest["recovery_clamd_image"] = manifest["previous_clamd_image"]
    manifest_file.write_text(json.dumps(manifest))
    assert _run([str(CHECKER.with_name("verify-backup.sh")), str(backup)], env).returncode != 0


@pytest.mark.parametrize("automatic,preflight,remaining", [
    (False, 0, ""), (False, 33, ""), (True, 0, ""), (True, 33, ""), (True, 0, "running"),
])
def test_rollback_verifies_recovery_and_fences_failed_candidate(tmp_path, preflight, automatic, remaining):
    from test_talk_proxy_lifecycle import _environment

    env, env_file, root = _environment(tmp_path)
    (root / "config/himalaya").mkdir()
    env["HIMALAYA_CONFIG_DIR"] = str(root / "config/himalaya")
    env["RECOVERY_TEST_RC"] = str(preflight)
    env["TEST_REMAINING_STATE"] = remaining
    scripts = env_file.parent / "scripts"
    scripts.mkdir()
    for name in ("rollback.sh", "common.sh"):
        shutil.copy2(CHECKER.with_name(name), scripts / name)
    for name in ("verify-backup.sh", "restore-local-state.sh", "backup.sh"):
        _stub(scripts / name, "exit 0\n")
    _stub(scripts / "verify-image-supply-chain.sh", 'echo "verified $*" >> "$TALK_TEST_DOCKER_LOG"\n')
    _stub(scripts / "check-maintenance-runtime.sh",
          'echo "smoke $*" >> "$TALK_TEST_DOCKER_LOG"\nexit "$RECOVERY_TEST_RC"\n')
    image = "fixture.invalid/new-maintenance@sha256:" + "9" * 64
    backup = root / "backups/releases/test-backup"
    backup.mkdir()
    (backup / "manifest.json").write_text(json.dumps({
        "previous_runtime": "docker", "previous_image": "old-runtime", "previous_proxy_image": "old-proxy",
        "previous_maintenance_image": "old-maintenance", "previous_clamd_image": "old-scanner",
        "target_maintenance_image": image, "target_clamd_image": image, "recovery_maintenance_image": image,
        "recovery_clamd_image": image, "recovery_maintenance_revision": "1" * 40,
        "recovery_maintenance_release": "test-release", "recovery_clamav_policy": "vendor-v1",
        "recovery_clamav_reason": "previous-image-build-age-policy", "previous_clamav_updater_running": False,
    }))
    args = [str(scripts / "rollback.sh"), "test-backup"]
    if automatic:
        args.append("--automatic")
    result = _run(args, env)
    log = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    if remaining:
        assert result.returncode != 0
        assert "down --remove-orphans" in log and "verified " not in log and "up -d" not in log
        return
    assert result.returncode == preflight, result.stderr
    assert log.index(f"verified {image}") < log.index(f"smoke {image} --recovery")
    if automatic:
        offline = log.index("com.docker.compose.project=")
        assert log.index("down --remove-orphans") < offline < log.index("verified ")
    if preflight:
        if not automatic:
            assert "down --remove-orphans" not in log
        assert "up -d" not in log
    else:
        if not automatic:
            assert log.index(f"smoke {image} --recovery") < log.index("down --remove-orphans")
        values = env_file.read_text()
        assert "OPENCLAW_IMAGE=old-runtime" in values and "OPENCLAW_PROXY_IMAGE=old-proxy" in values
        assert f"OPENCLAW_MAINTENANCE_IMAGE={image}" in values and f"OPENCLAW_CLAMD_IMAGE={image}" in values
        assert "jobs status --target all --deep" in log
