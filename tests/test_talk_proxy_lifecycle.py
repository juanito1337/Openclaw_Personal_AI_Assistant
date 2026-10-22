from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "docker/scripts"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    root = tmp_path / "instance"
    for name in ("state", "config", "secrets/talk-proxy-pki", "backups/releases"):
        (root / name).mkdir(parents=True, exist_ok=True)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    env_file = deployment / ".env"
    env_file.write_text("", encoding="utf-8")
    (deployment / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _stub(fake_bin / "sqlite3", "printf 'ok\\n'\n")
    _stub(fake_bin / "systemctl", "exit 1\n")
    _stub(
        fake_bin / "docker",
        'printf "%s\\n" "$*" >> "$TALK_TEST_DOCKER_LOG"\n'
        'if [[ "$1 ${2:-}" == "container ls" ]]; then\n'
        '  if [[ " $* " == *"openclaw-setup-service"* ]]; then exit 0; fi\n'
        '  [[ "${TALK_TEST_DOCKER_FAILURE:-false}" != true ]] || exit 7\n'
        '  if [[ -n "${TALK_TEST_STATE:-}" ]]; then printf "%s\\n" "$TALK_TEST_STATE";\n'
        '  elif [[ -f "$TALK_TEST_RUNNING" ]]; then printf "running\\n"; fi\n'
        'elif [[ "$1 ${2:-}" == "container inspect" ]]; then\n'
        '  if [[ " $* " == *"OPENCLAW_TALK_PROXY_IP"* ]]; then printf "192.168.2.30\\n";\n'
        '  elif [[ " $* " == *"OPENCLAW_TALK_NEXTCLOUD_IP"* ]]; then printf "192.168.2.4\\n"; fi\n'
        'elif [[ " $* " == *"org.opencontainers.image.openclaw.talk-proxy"* ]]; then\n'
        '  printf "%s\\n" "${TALK_TEST_IMAGE_SUPPORT:-1}"\n'
        'elif [[ " $* " == *" stop talk-proxy "* || " $* " == *" down "* ]]; then\n'
        '  rm -f "$TALK_TEST_RUNNING"\n'
        'elif [[ " $* " == *" up -d --no-deps talk-proxy "* ]]; then\n'
        '  [[ "${TALK_TEST_START_FAILURE:-false}" != true ]] || exit 7\n'
        '  touch "$TALK_TEST_RUNNING"\n'
        'elif [[ " $* " == *" ps -q "* ]]; then printf "test-id\\n"\n'
        'elif [[ "$1" == inspect ]]; then\n'
        '  if [[ " $* " == *"State.Running"* ]]; then printf "false\\n"; else printf "healthy\\n"; fi\n'
        "fi\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "OPENCLAW_ROOT": str(root),
            "OPENCLAW_STATE_DIR": str(root / "state"),
            "OPENCLAW_CONFIG_DIR": str(root / "config"),
            "OPENCLAW_SECRETS_DIR": str(root / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(root / "backups/releases"),
            "OPENCLAW_DEPLOY_ENV": str(env_file),
            "OPENCLAW_COMPOSE_FILE": str(deployment / "compose.yaml"),
            "OPENCLAW_TALK_PROXY_IP": "192.168.2.38",
            "OPENCLAW_TALK_NEXTCLOUD_IP": "192.168.2.3",
            "TALK_TEST_DOCKER_LOG": str(tmp_path / "docker.log"),
            "TALK_TEST_BIN": str(fake_bin),
            "TALK_TEST_RUNNING": str(tmp_path / "running"),
        }
    )
    return env, env_file, root


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if command[:2] == ["bash", "-c"]:
        # Keep inline-shell fixtures hermetic under launchers that prefix PATH.
        command = [*command[:2], 'PATH="$TALK_TEST_BIN:$PATH"; export PATH; ' + command[2], *command[3:]]
    return subprocess.run(command, env=env, text=True, capture_output=True, check=False)


@pytest.mark.parametrize("value,expected", [("true", 0), ("false", 0), ("1", 2), ("TRUE", 2)])
def test_talk_activation_is_strict_boolean(tmp_path: Path, value: str, expected: int) -> None:
    env, _, _ = _environment(tmp_path)
    result = _run(
        ["bash", "-c", '. "$1"; validate_talk_proxy_flag "$2"', "test", str(SCRIPTS / "common.sh"), value],
        env,
    )
    assert result.returncode == expected


@pytest.mark.parametrize("running,api_failure", [(True, False), (False, True)])
def test_backup_rejects_online_or_unproven_ca_state(tmp_path: Path, running: bool, api_failure: bool) -> None:
    env, _, root = _environment(tmp_path)
    if running:
        Path(env["TALK_TEST_RUNNING"]).touch()
    env["TALK_TEST_DOCKER_FAILURE"] = str(api_failure).lower()
    result = _run([str(SCRIPTS / "backup.sh"), "test"], env)
    assert result.returncode != 0
    assert "Talk-Proxy muss" in result.stderr
    assert not list((root / "backups/releases").iterdir())


@pytest.mark.parametrize("state", ["running", "restarting", "paused", "removing", "unknown"])
def test_backup_rejects_every_non_offline_proxy_state(tmp_path: Path, state: str) -> None:
    env, _, root = _environment(tmp_path)
    env["TALK_TEST_STATE"] = state
    result = _run([str(SCRIPTS / "backup.sh"), "test"], env)
    assert result.returncode != 0
    assert "Talk-Proxy muss" in result.stderr
    assert not list((root / "backups/releases").iterdir())


@pytest.mark.parametrize("state", ["created", "exited", "dead"])
def test_offline_proof_accepts_only_inert_existing_containers(tmp_path: Path, state: str) -> None:
    env, _, _ = _environment(tmp_path)
    env["TALK_TEST_STATE"] = state
    result = _run(["bash", "-c", '. "$1"; talk_proxy_is_offline', "test", str(SCRIPTS / "common.sh")], env)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("state,expected", [("running", "true"), ("restarting", "true"), ("exited", "false")])
def test_previous_state_preserves_restart_policy_intent(tmp_path: Path, state: str, expected: str) -> None:
    env, _, _ = _environment(tmp_path)
    env["TALK_TEST_STATE"] = state
    result = _run(["bash", "-c", '. "$1"; talk_proxy_running_state', "test", str(SCRIPTS / "common.sh")], env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_paused_proxy_is_not_silently_unpaused_by_deployment(tmp_path: Path) -> None:
    env, _, scripts = _deployment(tmp_path)
    env["TALK_TEST_STATE"] = "paused"
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    assert result.returncode != 0
    assert "pausiert" in result.stderr
    assert " stop " not in Path(env["TALK_TEST_DOCKER_LOG"]).read_text()


def test_backup_covers_private_ca_and_records_first_activation_as_off(tmp_path: Path) -> None:
    env, _, root = _environment(tmp_path)
    fixture = root / "secrets/talk-proxy-pki/test-only.txt"
    fixture.write_text("synthetic PKI state; not a private key", encoding="utf-8")
    env.update(PREVIOUS_TALK_PROXY_ENABLED="false", TARGET_TALK_PROXY_ENABLED="true")
    result = _run([str(SCRIPTS / "backup.sh"), "test"], env)
    assert result.returncode == 0, result.stderr
    backup = root / "backups/releases" / result.stdout.strip().splitlines()[-1]
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["previous_talk_proxy_enabled"] is False
    assert manifest["target_talk_proxy_enabled"] is True
    assert manifest["target_talk_proxy_ip"] == "192.168.2.38"
    assert manifest["target_talk_nextcloud_ip"] == "192.168.2.3"
    with tarfile.open(backup / "payload.tar.gz") as archive:
        assert "secrets/talk-proxy-pki/test-only.txt" in archive.getnames()


def _deployment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    env, env_file, root = _environment(tmp_path)
    scripts = env_file.parent / "scripts"
    scripts.mkdir()
    for name in ("deploy.sh", "common.sh"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    for name in ("verify-image-supply-chain.sh", "check-maintenance-runtime.sh", "smoke-test.sh"):
        _stub(scripts / name, "exit 0\n")
    (scripts / "check-layout-compatibility.py").write_text("pass\n", encoding="utf-8")
    _stub(
        scripts / "backup.sh",
        'printf "%s\\n" "$PREVIOUS_TALK_PROXY_ENABLED" > "$TALK_TEST_PREVIOUS"\nexit 19\n',
    )
    env["TALK_TEST_PREVIOUS"] = str(tmp_path / "previous")
    env_file.write_text(
        "OPENCLAW_IMAGE=fixture.invalid/old:runtime\n"
        "OPENCLAW_PROXY_IMAGE=fixture.invalid/old:proxy\n"
        "OPENCLAW_MAINTENANCE_IMAGE=fixture.invalid/old:maintenance\n"
        "OPENCLAW_CURRENT_RUNTIME=docker\n"
        "OPENCLAW_EXPECTED_SOURCE_REVISION=0123456789abcdef0123456789abcdef01234567\n"
        "OPENCLAW_WRITE_TEST_ENABLED=false\n"
        "REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=false\n"
        "OPENCLAW_TALK_PROXY_ENABLED=true\n",
        encoding="utf-8",
    )
    return env, env_file, scripts


@pytest.mark.parametrize("previous_running", [False, True])
def test_failed_activation_backup_restores_actual_previous_proxy_state(
    tmp_path: Path, previous_running: bool
) -> None:
    env, env_file, scripts = _deployment(tmp_path)
    if previous_running:
        Path(env["TALK_TEST_RUNNING"]).touch()
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    assert result.returncode == 19, result.stderr
    previous = str(previous_running).lower()
    assert Path(env["TALK_TEST_PREVIOUS"]).read_text().strip() == previous
    assert f"OPENCLAW_TALK_PROXY_ENABLED={previous}\n" in env_file.read_text()
    if previous_running:
        assert "OPENCLAW_TALK_PROXY_IP=192.168.2.30\n" in env_file.read_text()
        assert "OPENCLAW_TALK_NEXTCLOUD_IP=192.168.2.4\n" in env_file.read_text()
    commands = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert "stop talk-proxy" in commands
    assert ("up -d --no-deps talk-proxy" in commands) is previous_running


def test_failed_previous_proxy_recovery_remains_explicit_failure(tmp_path: Path) -> None:
    env, _, scripts = _deployment(tmp_path)
    Path(env["TALK_TEST_RUNNING"]).touch()
    env["TALK_TEST_START_FAILURE"] = "true"
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    assert result.returncode == 70, result.stderr
    assert "nicht verifiziert wiederhergestellt" in result.stderr


def test_missing_caddy_image_label_rejected_before_stack_stop(tmp_path: Path) -> None:
    env, _, scripts = _deployment(tmp_path)
    env["TALK_TEST_IMAGE_SUPPORT"] = "0"
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    assert result.returncode != 0
    assert "unterstuetzt den integrierten Talk-Proxy nicht" in result.stderr
    assert " stop " not in Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert not Path(env["TALK_TEST_PREVIOUS"]).exists()


def test_candidate_proxy_starts_only_after_backup_and_product_smoke(tmp_path: Path) -> None:
    env, _, scripts = _deployment(tmp_path)
    _stub(
        scripts / "backup.sh",
        'printf "verified-backup\\n" >> "$TALK_TEST_DOCKER_LOG"\nprintf "test-backup\\n"\n',
    )
    _stub(scripts / "smoke-test.sh", 'printf "product-smoke\\n" >> "$TALK_TEST_DOCKER_LOG"\n')
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    assert result.returncode == 0, result.stderr
    commands = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert commands.index("verified-backup") < commands.index("product-smoke")
    assert commands.index("product-smoke") < commands.index("up -d --no-deps talk-proxy")


@pytest.mark.parametrize("previous_enabled", [None, False, True])
def test_rollback_restores_recorded_activation_and_old_manifest_defaults_off(
    tmp_path: Path, previous_enabled: bool | None
) -> None:
    env, env_file, root = _environment(tmp_path)
    (root / "config/himalaya").mkdir()
    env["HIMALAYA_CONFIG_DIR"] = str(root / "config/himalaya")
    scripts = env_file.parent / "scripts"
    scripts.mkdir()
    for name in ("rollback.sh", "common.sh"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    for name in ("verify-backup.sh", "restore-local-state.sh", "backup.sh"):
        _stub(scripts / name, "exit 0\n")
    env_file.write_text("OPENCLAW_TALK_PROXY_ENABLED=true\n", encoding="utf-8")
    backup = root / "backups/releases/test-backup"
    backup.mkdir()
    manifest: dict[str, object] = {
        "previous_runtime": "docker",
        "previous_image": "fixture.invalid/old:runtime",
        "previous_proxy_image": "fixture.invalid/old:proxy",
        "previous_maintenance_image": "fixture.invalid/old:maintenance",
        "previous_clamd_image": "fixture.invalid/old:maintenance",
        "previous_talk_proxy_ip": "192.168.2.30",
        "previous_talk_nextcloud_ip": "192.168.2.4",
    }
    if previous_enabled is not None:
        manifest["previous_talk_proxy_enabled"] = previous_enabled
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    Path(env["TALK_TEST_RUNNING"]).touch()
    result = _run([str(scripts / "rollback.sh"), "test-backup", "--automatic"], env)
    assert result.returncode == 0, result.stderr
    expected = previous_enabled is True
    assert f"OPENCLAW_TALK_PROXY_ENABLED={str(expected).lower()}\n" in env_file.read_text()
    if expected:
        assert "OPENCLAW_TALK_PROXY_IP=192.168.2.30\n" in env_file.read_text()
        assert "OPENCLAW_TALK_NEXTCLOUD_IP=192.168.2.4\n" in env_file.read_text()
    commands = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert "--profile talk down --remove-orphans" in commands
    assert ("up -d --no-deps talk-proxy" in commands) is expected


@pytest.mark.parametrize(
    "address,nextcloud",
    [
        ("8.8.8.8", "192.168.2.3"),
        ("127.0.0.1", "192.168.2.3"),
        ("192.168.2.38", "home-agent"),
        ("192.168.2.38", "192.168.2.38"),
    ],
)
def test_invalid_proxy_addresses_fail_before_disruption(tmp_path: Path, address: str, nextcloud: str) -> None:
    env, _, scripts = _deployment(tmp_path)
    env.update(OPENCLAW_TALK_PROXY_IP=address, OPENCLAW_TALK_NEXTCLOUD_IP=nextcloud)
    result = _run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        env,
    )
    assert result.returncode != 0
    assert " stop " not in Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert not Path(env["TALK_TEST_PREVIOUS"]).exists()


def test_manual_backup_records_stopped_container_addresses_not_candidate_env(tmp_path: Path) -> None:
    env, _, root = _environment(tmp_path)
    env["OPENCLAW_TALK_PROXY_ENABLED"] = "true"
    result = _run([str(SCRIPTS / "backup.sh"), "test"], env)
    assert result.returncode == 0, result.stderr
    backup = root / "backups/releases" / result.stdout.strip().splitlines()[-1]
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["previous_talk_proxy_ip"] == "192.168.2.30"
    assert manifest["previous_talk_nextcloud_ip"] == "192.168.2.4"
    assert manifest["target_talk_proxy_ip"] == "192.168.2.38"
    assert manifest["target_talk_nextcloud_ip"] == "192.168.2.3"
    commands = Path(env["TALK_TEST_DOCKER_LOG"]).read_text()
    assert "{{json .Config.Env}}" not in commands


def test_refresh_preserves_ca_contents_and_does_not_import_trust(tmp_path: Path) -> None:
    env, _, root = _environment(tmp_path)
    # refresh-deployment uses the instance's deployment directory.
    (root / "deployment").mkdir()
    fixture = root / "secrets/talk-proxy-pki/test-only.txt"
    fixture.write_text("synthetic persistent state", encoding="utf-8")
    result = _run([str(SCRIPTS / "refresh-deployment.sh")], env)
    assert result.returncode == 0, result.stderr
    assert fixture.read_text() == "synthetic persistent state"
    assert (root / "secrets/talk-proxy-pki").stat().st_mode & 0o777 == 0o700
    assert (root / "config/talk-proxy-public").is_dir()
    assert not (root / "config/ca").exists()
