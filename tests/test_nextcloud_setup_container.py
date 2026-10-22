from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from personal_assistant import container_entrypoint
from personal_assistant.runtime_layout import ensure_gateway_devices_directory

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "docker/scripts"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    root = tmp_path / "instance"
    for name in ("state", "config", "secrets/nextcloud-connection", "backups/releases", "deployment"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "deployment/.env").write_text("", encoding="utf-8")
    (root / "deployment/compose.yaml").write_text("services: {}\n", encoding="utf-8")
    binary = tmp_path / "bin"
    binary.mkdir()
    _stub(binary / "sqlite3", "printf 'ok\\n'\n")
    _stub(
        binary / "docker",
        'printf "%s\\n" "$*" >> "$SETUP_TEST_LOG"\n'
        'if [[ "$1 ${2:-}" == "container ls" ]]; then\n'
        '  [[ "${SETUP_TEST_API_FAILURE:-false}" != true ]] || exit 7\n'
        '  if [[ " $* " == *"openclaw-setup-service"* ]]; then\n'
        '    printf "%s" "${SETUP_TEST_STATE:-}"\n'
        "  fi\n"
        'elif [[ "$1 ${2:-}" == "image inspect" ]]; then\n'
        '  [[ "${SETUP_TEST_API_FAILURE:-false}" != true ]] || exit 7\n'
        '  printf "%s" "${SETUP_TEST_IMAGE_SUPPORT:-1}"\n'
        'elif [[ " $* " == *" ps -q "* ]]; then printf "fixture-id\\n"\n'
        'elif [[ "$1" == inspect ]]; then printf "healthy\\n"\n'
        "fi\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{binary}:{env['PATH']}",
            "OPENCLAW_ROOT": str(root),
            "OPENCLAW_STATE_DIR": str(root / "state"),
            "OPENCLAW_CONFIG_DIR": str(root / "config"),
            "OPENCLAW_SECRETS_DIR": str(root / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(root / "backups/releases"),
            "OPENCLAW_DEPLOY_ENV": str(root / "deployment/.env"),
            "OPENCLAW_COMPOSE_FILE": str(root / "deployment/compose.yaml"),
            "OPENCLAW_TALK_PROXY_ENABLED": "false",
            "OPENCLAW_IMAGE": "fixture.invalid/runtime:fixture",
            "SETUP_TEST_LOG": str(tmp_path / "docker.log"),
            "SETUP_TEST_BIN": str(binary),
        }
    )
    return env, root


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if command[:2] == ["bash", "-c"]:
        # Some sandbox launchers prefix /usr/bin for inline shells. Reassert
        # the fixture command directory so a test never reaches real Docker.
        command = [*command[:2], 'PATH="$SETUP_TEST_BIN:$PATH"; export PATH; ' + command[2], *command[3:]]
    return subprocess.run(command, env=env, text=True, capture_output=True, check=False)


@pytest.fixture(scope="module")
def compose() -> dict:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "tools",
            "--profile",
            "maintenance",
            "--profile",
            "talk",
            "--env-file",
            "docker/deployment.env.example",
            "-f",
            "compose.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_only_owner_writes_connection_store_and_only_gateway_connects(compose: dict) -> None:
    readers = {"gateway", "mail-worker", "sync-worker", "monitor-worker", "agent-cli"}
    actual_readers = set()
    for role, service in compose["services"].items():
        mounts = {item["target"]: item for item in service.get("volumes", [])}
        if "/run/openclaw-nextcloud" in mounts:
            mount = mounts["/run/openclaw-nextcloud"]
            assert mount["type"] == "bind"
            assert mount["source"].endswith("/secrets/nextcloud-connection")
            assert mount.get("bind", {}).get("create_host_path", False) is False
            assert bool(mount.get("read_only")) is (role != "setup-service")
            assert service["environment"]["OPENCLAW_NEXTCLOUD_CONNECTION_DIR"] == "/run/openclaw-nextcloud"
            if role != "setup-service":
                actual_readers.add(role)
        if "/run/openclaw-setup" in mounts:
            assert role in {"setup-service", "gateway"}
            assert bool(mounts["/run/openclaw-setup"].get("read_only")) is (role == "gateway")
    assert actual_readers == readers
    gateway = compose["services"]["gateway"]
    assert gateway["environment"]["OPENCLAW_SETUP_SOCKET"] == "/run/openclaw-setup/control.sock"
    assert gateway["depends_on"]["setup-service"]["condition"] == "service_healthy"


def test_owner_has_bounded_resources_no_shared_state_credentials_or_public_port(compose: dict) -> None:
    owner = compose["services"]["setup-service"]
    assert owner["image"] == compose["services"]["gateway"]["image"]
    assert owner["user"] == "1000:1000"
    assert owner["read_only"] is True
    assert owner["cap_drop"] == ["ALL"]
    assert owner["pids_limit"] == 128
    assert int(owner["mem_limit"]) == 128 * 1024**2
    assert float(owner["cpus"]) == 0.5
    assert not owner.get("ports")
    assert not owner.get("profiles")
    assert set(owner["networks"]) == {"setup-egress"}
    assert {
        name for name, service in compose["services"].items() if "setup-egress" in service.get("networks", {})
    } == {"setup-service"}
    assert {item["target"] for item in owner["volumes"]} == {
        "/run/openclaw-nextcloud",
        "/run/openclaw-setup",
        "/etc/openclaw-ca",
        "/run/openclaw-gateway-devices",
    }
    assert owner["command"][-2:] == ["--socket", "/run/openclaw-setup/control.sock"]
    assert owner["healthcheck"]["test"][-1] == "setup"
    assert container_entrypoint.ROLE_ENV_FILES["setup-service"] == ()
    pairing = next(item for item in owner["volumes"] if item["target"] == "/run/openclaw-gateway-devices")
    assert pairing["source"].endswith("/state/v3/gateway/devices")
    assert pairing["read_only"] is True
    assert pairing.get("bind", {}).get("create_host_path", False) is False
    assert owner["environment"]["OPENCLAW_SETUP_DEVICES_FILE"] == "/run/openclaw-gateway-devices/paired.json"


def test_owner_entrypoint_never_migrates_or_opens_legacy_domain_layout(tmp_path: Path) -> None:
    class Executed(Exception):
        pass

    environment = {"OPENCLAW_ROLE": "setup-service", "OPENCLAW_IMAGE_ROOT": str(tmp_path)}
    command = ["python3", "-P", "-m", "personal_assistant.nextcloud_setup"]
    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(container_entrypoint, "configure_custom_ca"),
        patch.object(container_entrypoint, "verify_layout") as verify,
        patch.object(container_entrypoint.subprocess, "run") as migrate,
        patch.object(container_entrypoint.shutil, "which", return_value="/usr/bin/python3"),
        patch.object(container_entrypoint.os, "chdir") as chdir,
        patch.object(container_entrypoint.os, "execve", side_effect=Executed) as execute,
        pytest.raises(Executed),
    ):
        container_entrypoint.main(command)
    verify.assert_not_called()
    migrate.assert_not_called()
    chdir.assert_called_once_with(tmp_path)
    assert execute.call_args.args[1] == command
    assert execute.call_args.args[2]["OPENCLAW_WORKSPACE"] == str(tmp_path)
    assert "OPENCLAW_MAIL_DATA_DIR" not in execute.call_args.args[2]


def test_setup_gateway_denies_generic_process_and_file_tools_preserving_native_bridge(tmp_path: Path) -> None:
    path = tmp_path / "openclaw.json"
    path.write_text('{"tools":{"deny":["browser"]}}', encoding="utf-8")
    container_entrypoint.ensure_personal_assistant_plugin_config(path)
    assert json.loads(path.read_text())["tools"]["deny"] == ["browser"]
    assert container_entrypoint.ensure_personal_assistant_plugin_config(path, setup_enabled=True)
    payload = json.loads(path.read_text())
    assert payload["tools"]["deny"] == ["browser", "group:runtime", "group:fs"]
    assert "personal-assistant-tools" in payload["tools"]["alsoAllow"]
    assert payload["plugins"]["entries"]["personal-assistant-tools"]["enabled"] is True
    assert not container_entrypoint.ensure_personal_assistant_plugin_config(path, setup_enabled=True)


@pytest.mark.parametrize("deny", [None, "group:runtime", ["valid", 1], {}])
def test_setup_gateway_rejects_malformed_deny_without_publishing(tmp_path: Path, deny: object) -> None:
    path = tmp_path / "openclaw.json"
    original = json.dumps({"tools": {"deny": deny}})
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="tools.deny"):
        container_entrypoint.ensure_personal_assistant_plugin_config(path, setup_enabled=True)
    assert path.read_text() == original


def test_layout_prepares_only_private_empty_device_directory_and_rejects_redirect(tmp_path: Path) -> None:
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    assert ensure_gateway_devices_directory(gateway)
    devices = gateway / "devices"
    assert devices.stat().st_mode & 0o777 == 0o700
    assert list(devices.iterdir()) == []
    marker = devices / "fixture.txt"
    marker.write_text("fixture-original", encoding="utf-8")
    assert not ensure_gateway_devices_directory(gateway)
    assert marker.read_text() == "fixture-original"
    marker.unlink()
    devices.rmdir()
    devices.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="Geraeteverzeichnis"):
        ensure_gateway_devices_directory(gateway)


@pytest.mark.parametrize(
    "state", ["running", "restarting", "paused", "removing", "unknown", "exited\nrunning"]
)
def test_backup_and_restore_block_unproven_owner_shutdown(tmp_path: Path, state: str) -> None:
    env, root = _environment(tmp_path)
    env["SETUP_TEST_STATE"] = state
    env["OPENCLAW_RESTORE_OFFLINE"] = "YES"
    fixture = root / "secrets/nextcloud-connection/fixture.txt"
    fixture.write_text("fixture-original", encoding="utf-8")
    for script in ("backup.sh", "restore-local-state.sh"):
        result = _run([str(SCRIPTS / script), "fixture"], env)
        assert result.returncode != 0
        assert "Setup-Service" in result.stderr
    assert fixture.read_text() == "fixture-original"
    assert not list((root / "backups/releases").iterdir())


def test_docker_api_failure_is_not_absence(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["SETUP_TEST_API_FAILURE"] = "true"
    result = _run([str(SCRIPTS / "backup.sh"), "fixture"], env)
    assert result.returncode != 0
    assert "Setup-Service" in result.stderr


@pytest.mark.parametrize("state", ["", "created", "exited", "dead"])
def test_offline_backup_contains_store_and_restore_preserves_file_mode(tmp_path: Path, state: str) -> None:
    env, root = _environment(tmp_path)
    env["SETUP_TEST_STATE"] = state
    fixture = root / "secrets/nextcloud-connection/fixture.txt"
    fixture.write_text("fixture-original", encoding="utf-8")
    fixture.chmod(0o600)
    result = _run([str(SCRIPTS / "backup.sh"), "fixture"], env)
    assert result.returncode == 0, result.stderr
    backup = root / "backups/releases" / result.stdout.strip().splitlines()[-1]
    with tarfile.open(backup / "payload.tar.gz") as archive:
        info = archive.getmember("secrets/nextcloud-connection/fixture.txt")
        assert info.mode == 0o600
        assert not any("setup-control" in item for item in archive.getnames())
    fixture.write_text("fixture-changed", encoding="utf-8")
    env["OPENCLAW_RESTORE_OFFLINE"] = "YES"
    result = _run([str(SCRIPTS / "restore-local-state.sh"), str(backup)], env)
    assert result.returncode == 0, result.stderr
    assert fixture.read_text() == "fixture-original"
    assert fixture.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("support,starts,code", [("1", True, 0), ("<no value>", False, 0), ("2", False, 1)])
def test_rollback_starts_owner_only_for_compatible_image(
    tmp_path: Path, support: str, starts: bool, code: int
) -> None:
    env, _ = _environment(tmp_path)
    env["SETUP_TEST_IMAGE_SUPPORT"] = support
    result = _run(
        ["bash", "-c", '. "$1"; start_setup_service_if_supported', "fixture", str(SCRIPTS / "common.sh")],
        env,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert ("up -d --no-deps setup-service" in Path(env["SETUP_TEST_LOG"]).read_text()) is starts


def test_refresh_preserves_connection_contents_and_rejects_symlink(tmp_path: Path) -> None:
    env, root = _environment(tmp_path)
    binary = Path(env["SETUP_TEST_BIN"])
    # Exercise a host UID different from the container UID without changing
    # fixture ownership or invoking the runner's real sudo command.
    _stub(binary / "stat", "printf '1001:1001\\n'\n")
    _stub(binary / "chown", 'printf "%s\\n" "$*" >> "$SETUP_TEST_CHOWN_LOG"\n')
    _stub(binary / "sudo", "exit 97\n")
    env["SETUP_TEST_CHOWN_LOG"] = str(tmp_path / "chown.log")
    directory = root / "secrets/nextcloud-connection"
    fixture = directory / "fixture.txt"
    fixture.write_text("fixture-original", encoding="utf-8")
    result = _run([str(SCRIPTS / "refresh-deployment.sh")], env)
    assert result.returncode == 0, result.stderr
    assert fixture.read_text() == "fixture-original"
    assert directory.stat().st_mode & 0o777 == 0o700
    assert f"1000:1000 {directory}" in Path(env["SETUP_TEST_CHOWN_LOG"]).read_text().splitlines()
    shutil.move(str(directory), str(root / "untouched"))
    directory.symlink_to(root / "untouched", target_is_directory=True)
    result = _run([str(SCRIPTS / "refresh-deployment.sh")], env)
    assert result.returncode != 0
    assert "Ungueltiger Nextcloud-Verbindungsstore" in result.stderr
    assert fixture.read_text() == "fixture-original"


def test_new_runtime_prepares_nonroot_socket_volume_and_advertises_owner_support() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'org.opencontainers.image.openclaw.setup-owner="1"' in dockerfile
    assert "chown node:node /run/openclaw-setup" in dockerfile
    assert "chmod 0700 /run/openclaw-setup" in dockerfile


def _real_sqlite(env: dict[str, str]) -> None:
    binary = shutil.which("sqlite3")
    if binary is None:
        pytest.skip("SQLite CLI required for backup integrity fixture")
    _stub(Path(env["SETUP_TEST_BIN"]) / "sqlite3", f'exec {shlex.quote(binary)} "$@"\n')


def test_nonce_database_survives_backup_restore_without_resetting_replay_state(tmp_path: Path) -> None:
    env, root = _environment(tmp_path)
    _real_sqlite(env)
    database = root / "secrets/nextcloud-connection/operator-nonces.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE used (nonce TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO used VALUES ('fixture-used-nonce')")
    result = _run([str(SCRIPTS / "backup.sh"), "nonce-fixture"], env)
    assert result.returncode == 0, result.stderr
    backup = root / "backups/releases" / result.stdout.strip().splitlines()[-1]
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO used VALUES ('fixture-later-nonce')")
    env["OPENCLAW_RESTORE_OFFLINE"] = "YES"
    result = _run([str(SCRIPTS / "restore-local-state.sh"), str(backup)], env)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT nonce FROM used").fetchall() == [("fixture-used-nonce",)]


@pytest.mark.parametrize("symlink", [False, True])
def test_backup_rejects_invalid_nonce_database_before_archive(tmp_path: Path, symlink: bool) -> None:
    env, root = _environment(tmp_path)
    _real_sqlite(env)
    database = root / "secrets/nextcloud-connection/operator-nonces.sqlite3"
    if symlink:
        outside = tmp_path / "unrelated"
        outside.write_text("fixture-unrelated", encoding="utf-8")
        database.symlink_to(outside)
    else:
        database.write_bytes(b"fixture-invalid-database")
    result = _run([str(SCRIPTS / "backup.sh"), "nonce-fixture"], env)
    assert result.returncode != 0
    assert "Replaydatenbank" in result.stderr
    assert not list((root / "backups/releases").rglob("payload.tar.gz"))


def test_restore_rejects_invalid_nonce_database_before_replacing_local_state(tmp_path: Path) -> None:
    env, root = _environment(tmp_path)
    _real_sqlite(env)
    # Produce a checksum-valid archive through the fixture checker, then use
    # real SQLite for restore. Content integrity remains an independent gate.
    database = root / "secrets/nextcloud-connection/operator-nonces.sqlite3"
    database.write_bytes(b"fixture-invalid-database")
    _stub(Path(env["SETUP_TEST_BIN"]) / "sqlite3", "printf 'ok\\n'\n")
    result = _run([str(SCRIPTS / "backup.sh"), "nonce-fixture"], env)
    assert result.returncode == 0, result.stderr
    backup = root / "backups/releases" / result.stdout.strip().splitlines()[-1]
    _real_sqlite(env)
    database.unlink()
    marker = root / "state/fixture.txt"
    marker.write_text("fixture-current", encoding="utf-8")
    env["OPENCLAW_RESTORE_OFFLINE"] = "YES"
    result = _run([str(SCRIPTS / "restore-local-state.sh"), str(backup)], env)
    assert result.returncode != 0
    assert "Replaydatenbank" in result.stderr
    assert marker.read_text() == "fixture-current"
    assert not database.exists()
