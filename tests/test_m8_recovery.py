from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _backup(root: Path, *, external_reference: str = "") -> Path:
    source = root / "backup-source"
    for name in ("state", "config/himalaya", "secrets"):
        (source / name).mkdir(parents=True, exist_ok=True)
    (source / "state/release.txt").write_text("safe-old-state\n", encoding="utf-8")
    (source / "config/release.conf").write_text("safe-old-config\n", encoding="utf-8")
    (source / "secrets/fixture.env").write_text("M8_FIXTURE=not-a-secret\n", encoding="utf-8")
    backup = root / "openclaw/backups/releases/m8-backup"
    backup.mkdir(parents=True)
    archive = backup / "payload.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("state", "config", "secrets"):
            bundle.add(source / name, arcname=name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (backup / "payload.tar.gz.sha256").write_text(
        f"{digest}  payload.tar.gz\n", encoding="utf-8"
    )
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "backup_id": "m8-backup",
                "created_at": "2026-08-06T00:00:00+00:00",
                "previous_image": "fixture.invalid/openclaw:old",
                "target_image": "fixture.invalid/openclaw:new",
                "previous_proxy_image": "fixture.invalid/openclaw:old-proxy",
                "previous_maintenance_image": "fixture.invalid/openclaw:old-maintenance",
                "archive_sha256": digest,
                "external_backup_reference": external_reference,
                "previous_runtime": "docker",
                "verified": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return backup


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    openclaw = tmp_path / "openclaw"
    for name in ("state", "config/himalaya", "secrets", "backups/releases"):
        (openclaw / name).mkdir(parents=True, exist_ok=True)
    (openclaw / "state/release.txt").write_text("unsafe-new-state\n", encoding="utf-8")
    env_file = tmp_path / "deployment.env"
    env_file.write_text(
        "OPENCLAW_IMAGE=fixture.invalid/openclaw:new\n"
        "OPENCLAW_PROXY_IMAGE=fixture.invalid/openclaw:new-proxy\n"
        "OPENCLAW_MAINTENANCE_IMAGE=fixture.invalid/openclaw:new-maintenance\n"
        "OPENCLAW_CURRENT_RUNTIME=docker\n",
        encoding="utf-8",
    )
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$M8_DOCKER_LOG\"\n"
        "if [[ \" $* \" == *\" ps -q \"* ]]; then printf 'm8-fixture-id\\n'; fi\n"
        "if [[ \"$1\" == inspect ]]; then printf 'running\\n'; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    chown = fake_bin / "chown"
    chown.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf '%s\\n' \"$*\" >> \"$M8_CHOWN_LOG\"\n"
        "for argument in \"$@\"; do\n"
        "  case \"$argument\" in\n"
        "    \"$OPENCLAW_ROOT\"/*) chmod -R u+rwX \"$argument\" ;;\n"
        "  esac\n"
        "done\n",
        encoding="utf-8",
    )
    chown.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "HOME": str(tmp_path / "home"),
            "OPENCLAW_DEPLOY_ENV": str(env_file),
            "OPENCLAW_COMPOSE_FILE": str(compose_file),
            "OPENCLAW_ROOT": str(openclaw),
            "OPENCLAW_STATE_DIR": str(openclaw / "state"),
            "OPENCLAW_CONFIG_DIR": str(openclaw / "config"),
            "OPENCLAW_SECRETS_DIR": str(openclaw / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(openclaw / "backups/releases"),
            "HIMALAYA_CONFIG_DIR": str(openclaw / "config/himalaya"),
            "M8_DOCKER_LOG": str(docker_log),
            "M8_CHOWN_LOG": str(tmp_path / "chown.log"),
            "BACKUP_RETENTION_RELEASES": "100",
        }
    )
    return environment, docker_log, env_file


def test_local_restore_requires_explicit_offline_contract(tmp_path: Path) -> None:
    environment, _, _ = _environment(tmp_path)
    backup = _backup(tmp_path)
    result = subprocess.run(
        [str(ROOT / "docker/scripts/restore-local-state.sh"), str(backup)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "OPENCLAW_RESTORE_OFFLINE=YES" in result.stderr
    assert (tmp_path / "openclaw/state/release.txt").read_text() == "unsafe-new-state\n"


def test_missing_external_restore_hook_aborts_before_container_stop(tmp_path: Path) -> None:
    environment, docker_log, _ = _environment(tmp_path)
    _backup(tmp_path, external_reference="fixture-snapshot")
    result = subprocess.run(
        [str(ROOT / "docker/scripts/rollback.sh"), "m8-backup", "--automatic"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "vor dem Stoppen abgebrochen" in result.stderr
    assert not docker_log.exists() or " compose " not in docker_log.read_text()
    assert (tmp_path / "openclaw/state/release.txt").read_text() == "unsafe-new-state\n"


def test_failed_external_restore_still_starts_verified_old_local_state(tmp_path: Path) -> None:
    environment, docker_log, env_file = _environment(tmp_path)
    _backup(tmp_path, external_reference="fixture-snapshot")
    hook = tmp_path / "restore-hook"
    hook.write_text("#!/bin/sh\necho 'fixture restore failed' >&2\nexit 9\n", encoding="utf-8")
    hook.chmod(0o755)
    environment["OPENCLAW_EXTERNAL_RESTORE_HOOK"] = str(hook)
    result = subprocess.run(
        [str(ROOT / "docker/scripts/rollback.sh"), "m8-backup", "--automatic"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "lokaler Rollback wird fortgesetzt" in result.stderr
    assert "Lokaler Rollback abgeschlossen" in result.stderr
    assert (tmp_path / "openclaw/state/release.txt").read_text() == "safe-old-state\n"
    commands = docker_log.read_text(encoding="utf-8")
    assert "compose" in commands and "down --remove-orphans" in commands
    assert "up -d ollama-proxy gateway" in commands
    assert "up -d mail-worker sync-worker supervisor-worker portfolio-worker monitor-worker" in commands
    deployed = env_file.read_text(encoding="utf-8")
    assert "OPENCLAW_IMAGE=fixture.invalid/openclaw:old\n" in deployed
    assert "OPENCLAW_CURRENT_RUNTIME=docker\n" in deployed


def test_rollback_normalizes_runtime_owned_paths_before_restore(tmp_path: Path) -> None:
    environment, docker_log, _ = _environment(tmp_path)
    _backup(tmp_path)
    runtime_created = tmp_path / "openclaw/state/v3/gateway/workspace"
    runtime_created.mkdir(parents=True)
    stale = runtime_created / "root-created.txt"
    stale.write_text("must be replaced\n", encoding="utf-8")
    stale.chmod(0o400)
    runtime_created.chmod(0o500)

    result = subprocess.run(
        [str(ROOT / "docker/scripts/rollback.sh"), "m8-backup", "--automatic"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not runtime_created.exists()
    assert (tmp_path / "openclaw/state/release.txt").read_text() == "safe-old-state\n"
    chown_calls = (tmp_path / "chown.log").read_text(encoding="utf-8").splitlines()
    assert len(chown_calls) == 2
    assert all(str(tmp_path / "openclaw/state") in call for call in chown_calls)
    commands = docker_log.read_text(encoding="utf-8")
    assert "up -d ollama-proxy gateway" in commands


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_maintenance_preflight_failure_keeps_running_stack_untouched(tmp_path: Path) -> None:
    scripts = tmp_path / "deployment/scripts"
    scripts.mkdir(parents=True)
    for name in ("deploy.sh", "common.sh"):
        shutil.copy2(ROOT / "docker/scripts" / name, scripts / name)
    _stub(scripts / "verify-image-supply-chain.sh", "exit 0\n")
    _stub(
        scripts / "check-maintenance-runtime.sh",
        "echo 'injected libcurl relocation failure' >&2\nexit 127\n",
    )
    layout_called = tmp_path / "layout.called"
    _stub(scripts / "check-layout-compatibility.py", f"touch {layout_called}\n")
    backup_called = tmp_path / "backup.called"
    _stub(scripts / "backup.sh", f"touch {backup_called}\nprintf 'backup-id\n'\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    _stub(
        fake_bin / "docker",
        'printf "%s\\n" "$*" >> "$M8_DOCKER_LOG"\n'
        'case " $* " in *"State.Running"*) printf "false\\n";; esac\n'
        "exit 0\n",
    )
    _stub(fake_bin / "systemctl", "exit 1\n")
    env_file = tmp_path / "deployment/.env"
    env_file.write_text(
        "OPENCLAW_IMAGE=fixture.invalid/old:runtime\n"
        "OPENCLAW_PROXY_IMAGE=fixture.invalid/old:proxy\n"
        "OPENCLAW_MAINTENANCE_IMAGE=fixture.invalid/old:maintenance\n"
        "OPENCLAW_CURRENT_RUNTIME=docker\n"
        "OPENCLAW_EXPECTED_SOURCE_REVISION=0123456789abcdef0123456789abcdef01234567\n"
        "OPENCLAW_WRITE_TEST_ENABLED=false\n"
        "REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=false\n",
        encoding="utf-8",
    )
    compose_file = tmp_path / "deployment/compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    openclaw = tmp_path / "openclaw"
    for name in ("state", "config", "secrets", "backups/releases"):
        (openclaw / name).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "OPENCLAW_DEPLOY_ENV": str(env_file),
            "OPENCLAW_COMPOSE_FILE": str(compose_file),
            "OPENCLAW_ROOT": str(openclaw),
            "OPENCLAW_STATE_DIR": str(openclaw / "state"),
            "OPENCLAW_CONFIG_DIR": str(openclaw / "config"),
            "OPENCLAW_SECRETS_DIR": str(openclaw / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(openclaw / "backups/releases"),
            "M8_DOCKER_LOG": str(docker_log),
        }
    )

    result = subprocess.run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 127
    assert "injected libcurl relocation failure" in result.stderr
    assert not layout_called.exists()
    assert not backup_called.exists()
    commands = docker_log.read_text(encoding="utf-8")
    assert " compose stop " not in f" {commands} "


def test_deploy_rejects_legacy_marker_with_running_docker_writer(tmp_path: Path) -> None:
    scripts = tmp_path / "deployment/scripts"
    scripts.mkdir(parents=True)
    for name in ("deploy.sh", "common.sh"):
        shutil.copy2(ROOT / "docker/scripts" / name, scripts / name)
    _stub(scripts / "verify-image-supply-chain.sh", "exit 0\n")
    _stub(scripts / "check-maintenance-runtime.sh", "exit 0\n")
    (scripts / "check-layout-compatibility.py").write_text(
        "#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8"
    )
    backup_called = tmp_path / "backup.called"
    _stub(scripts / "backup.sh", f"touch {backup_called}\nprintf 'backup-id\\n'\n")
    _stub(scripts / "rollback.sh", "exit 0\n")
    _stub(scripts / "smoke-test.sh", "exit 0\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    _stub(
        fake_bin / "docker",
        'printf "%s\\n" "$*" >> "$M8_DOCKER_LOG"\n'
        'case " $* " in\n'
        '  *"State.Running"*"openclaw-mail-worker"*) printf "true\\n";;\n'
        '  *"State.Running"*) printf "false\\n";;\n'
        'esac\n'
        "exit 0\n",
    )
    _stub(fake_bin / "systemctl", "exit 1\n")
    env_file = tmp_path / "deployment/.env"
    env_file.write_text(
        "OPENCLAW_IMAGE=fixture.invalid/old:runtime\n"
        "OPENCLAW_PROXY_IMAGE=fixture.invalid/old:proxy\n"
        "OPENCLAW_MAINTENANCE_IMAGE=fixture.invalid/old:maintenance\n"
        "OPENCLAW_CURRENT_RUNTIME=legacy-systemd\n"
        "OPENCLAW_EXPECTED_SOURCE_REVISION=0123456789abcdef0123456789abcdef01234567\n"
        "OPENCLAW_WRITE_TEST_ENABLED=false\n"
        "REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=false\n",
        encoding="utf-8",
    )
    compose_file = tmp_path / "deployment/compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    openclaw = tmp_path / "openclaw"
    for name in ("state", "config", "secrets", "backups/releases"):
        (openclaw / name).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "OPENCLAW_DEPLOY_ENV": str(env_file),
            "OPENCLAW_COMPOSE_FILE": str(compose_file),
            "OPENCLAW_ROOT": str(openclaw),
            "OPENCLAW_STATE_DIR": str(openclaw / "state"),
            "OPENCLAW_CONFIG_DIR": str(openclaw / "config"),
            "OPENCLAW_SECRETS_DIR": str(openclaw / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(openclaw / "backups/releases"),
            "M8_DOCKER_LOG": str(docker_log),
        }
    )
    result = subprocess.run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Runtime-Identitaet widerspruechlich" in result.stderr
    assert not backup_called.exists()
    assert " stop mail-worker " not in f" {docker_log.read_text(encoding='utf-8')} "

    env_file.write_text(
        env_file.read_text(encoding="utf-8").replace(
            "OPENCLAW_CURRENT_RUNTIME=legacy-systemd",
            "OPENCLAW_CURRENT_RUNTIME=docker",
        ),
        encoding="utf-8",
    )
    failed_stop = subprocess.run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed_stop.returncode != 0
    assert "Docker-Writer laufen noch" in failed_stop.stderr
    assert not backup_called.exists()
    assert " stop mail-worker " in f" {docker_log.read_text(encoding='utf-8')} "


def test_legacy_deploy_disables_enabled_writer_timer_and_restores_it_on_backup_failure(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "deployment/scripts"
    scripts.mkdir(parents=True)
    for name in ("deploy.sh", "common.sh"):
        shutil.copy2(ROOT / "docker/scripts" / name, scripts / name)
    _stub(scripts / "verify-image-supply-chain.sh", "exit 0\n")
    _stub(scripts / "check-maintenance-runtime.sh", "exit 0\n")
    (scripts / "check-layout-compatibility.py").write_text(
        "#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8"
    )
    backup_called = tmp_path / "backup.called"
    _stub(
        scripts / "backup.sh",
        'test -f "$M8_SYSTEMCTL_STATE/disabled"\n'
        f"touch {backup_called}\n"
        "echo 'injected backup failure' >&2\n"
        "exit 19\n",
    )
    _stub(scripts / "rollback.sh", "exit 0\n")
    _stub(scripts / "smoke-test.sh", "exit 0\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _stub(
        fake_bin / "docker",
        'if [ "${1:-}" = "inspect" ]; then printf "false\\n"; fi\n'
        "exit 0\n",
    )
    systemctl_state = tmp_path / "systemctl-state"
    systemctl_state.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        'printf "%s\\n" "$*" >> "$M8_SYSTEMCTL_LOG"\n'
        'case "$*" in\n'
        '  "--user show-environment") exit 0;;\n'
        '  "--user is-enabled --quiet mail-agent.timer")\n'
        '    test ! -f "$M8_SYSTEMCTL_STATE/disabled"; exit;;\n'
        '  "--user is-active --quiet mail-agent.timer")\n'
        '    test ! -f "$M8_SYSTEMCTL_STATE/disabled"; exit;;\n'
        '  "--user disable --now mail-agent.timer")\n'
        '    touch "$M8_SYSTEMCTL_STATE/disabled"; exit 0;;\n'
        '  "--user enable --now mail-agent.timer")\n'
        '    rm -f "$M8_SYSTEMCTL_STATE/disabled"; '
        'touch "$M8_SYSTEMCTL_STATE/restored"; exit 0;;\n'
        '  "--user stop "*) exit 0;;\n'
        '  "--user is-active --quiet "*) exit 1;;\n'
        '  "--user is-enabled --quiet "*) exit 1;;\n'
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    env_file = tmp_path / "deployment/.env"
    env_file.write_text(
        "OPENCLAW_IMAGE=fixture.invalid/old:runtime\n"
        "OPENCLAW_PROXY_IMAGE=fixture.invalid/old:proxy\n"
        "OPENCLAW_MAINTENANCE_IMAGE=fixture.invalid/old:maintenance\n"
        "OPENCLAW_CURRENT_RUNTIME=legacy-systemd\n"
        "OPENCLAW_EXPECTED_SOURCE_REVISION=0123456789abcdef0123456789abcdef01234567\n"
        "OPENCLAW_WRITE_TEST_ENABLED=false\n"
        "REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=false\n",
        encoding="utf-8",
    )
    compose_file = tmp_path / "deployment/compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    openclaw = tmp_path / "openclaw"
    for name in ("state", "config", "secrets", "backups/releases"):
        (openclaw / name).mkdir(parents=True, exist_ok=True)
    (openclaw / "config/legacy-active-units.txt").write_text(
        "mail-agent.timer\n", encoding="utf-8"
    )
    legacy_home = tmp_path / "legacy"
    (legacy_home / "workspace/scripts").mkdir(parents=True)
    (legacy_home / "openclaw.json").write_text("{}\n", encoding="utf-8")
    _stub(legacy_home / "workspace/scripts/assistant.sh", "exit 0\n")
    _stub(legacy_home / "workspace/scripts/mail-agent.sh", "exit 0\n")

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "OPENCLAW_DEPLOY_ENV": str(env_file),
            "OPENCLAW_COMPOSE_FILE": str(compose_file),
            "OPENCLAW_ROOT": str(openclaw),
            "OPENCLAW_STATE_DIR": str(openclaw / "state"),
            "OPENCLAW_CONFIG_DIR": str(openclaw / "config"),
            "OPENCLAW_SECRETS_DIR": str(openclaw / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(openclaw / "backups/releases"),
            "OPENCLAW_LEGACY_HOME": str(legacy_home),
            "M8_SYSTEMCTL_LOG": str(systemctl_log),
            "M8_SYSTEMCTL_STATE": str(systemctl_state),
        }
    )
    result = subprocess.run(
        [
            str(scripts / "deploy.sh"),
            "fixture.invalid/new:runtime",
            "fixture.invalid/new:proxy",
            "fixture.invalid/new:maintenance",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 19
    assert "injected backup failure" in result.stderr
    assert "Vorbereitendes Backup fehlgeschlagen" in result.stderr
    assert backup_called.exists()
    assert (systemctl_state / "restored").exists()
    commands = systemctl_log.read_text(encoding="utf-8").splitlines()
    disabled = commands.index("--user disable --now mail-agent.timer")
    restored = commands.index("--user enable --now mail-agent.timer")
    assert disabled < restored


def test_failed_product_smoke_runs_automatic_rollback_and_surfaces_rollback_failure(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "deployment/scripts"
    scripts.mkdir(parents=True)
    for name in ("deploy.sh", "common.sh"):
        shutil.copy2(ROOT / "docker/scripts" / name, scripts / name)
    _stub(scripts / "verify-image-supply-chain.sh", "exit 0\n")
    _stub(scripts / "check-maintenance-runtime.sh", "exit 0\n")
    (scripts / "check-layout-compatibility.py").write_text(
        "#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8"
    )
    _stub(scripts / "backup.sh", "printf 'm8-backup\\n'\n")
    _stub(scripts / "smoke-test.sh", "echo 'injected smoke failure' >&2\nexit 23\n")
    rollback_log = tmp_path / "rollback.log"
    _stub(scripts / "rollback.sh", 'printf "%s\\n" "$*" >> "$M8_ROLLBACK_LOG"\nexit 0\n')

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _stub(
        fake_bin / "docker",
        'if [ "${1:-}" = "compose" ]; then\n'
        '  if [ "${M8_FAIL_CANDIDATE_UP:-}" = "true" ] && '
        'printf "%s" " $* " | grep -Fq " up -d ollama-proxy gateway "; then exit 31; fi\n'
        '  case " $* " in *" ps -q "*) printf "m8-id\\n";; esac\n'
        'elif [ "${1:-}" = "inspect" ]; then\n'
        '  case " $* " in *"State.Running"*) printf "false\\n";; *) printf "running\\n";; esac\n'
        'fi\n'
        "exit 0\n",
    )
    _stub(fake_bin / "systemctl", "exit 1\n")
    env_file = tmp_path / "deployment/.env"
    env_file.write_text(
        "OPENCLAW_IMAGE=fixture.invalid/old:runtime\n"
        "OPENCLAW_PROXY_IMAGE=fixture.invalid/old:proxy\n"
        "OPENCLAW_MAINTENANCE_IMAGE=fixture.invalid/old:maintenance\n"
        "OPENCLAW_CURRENT_RUNTIME=docker\n"
        "OPENCLAW_EXPECTED_SOURCE_REVISION=0123456789abcdef0123456789abcdef01234567\n"
        "OPENCLAW_WRITE_TEST_ENABLED=false\n"
        "REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=false\n",
        encoding="utf-8",
    )
    compose_file = tmp_path / "deployment/compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    openclaw = tmp_path / "openclaw"
    for name in ("state", "config", "secrets", "backups/releases"):
        (openclaw / name).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "OPENCLAW_DEPLOY_ENV": str(env_file),
            "OPENCLAW_COMPOSE_FILE": str(compose_file),
            "OPENCLAW_ROOT": str(openclaw),
            "OPENCLAW_STATE_DIR": str(openclaw / "state"),
            "OPENCLAW_CONFIG_DIR": str(openclaw / "config"),
            "OPENCLAW_SECRETS_DIR": str(openclaw / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(openclaw / "backups/releases"),
            "M8_ROLLBACK_LOG": str(rollback_log),
        }
    )
    command = [
        str(scripts / "deploy.sh"),
        "fixture.invalid/new:runtime",
        "fixture.invalid/new:proxy",
        "fixture.invalid/new:maintenance",
    ]
    failed_smoke = subprocess.run(
        command, cwd=tmp_path, env=environment, text=True, capture_output=True, check=False
    )
    assert failed_smoke.returncode == 23
    assert "injected smoke failure" in failed_smoke.stderr
    assert rollback_log.read_text(encoding="utf-8").strip() == "m8-backup --automatic"

    rollback_log.unlink()
    environment["M8_FAIL_CANDIDATE_UP"] = "true"
    env_file.write_text(env_file.read_text().replace("fixture.invalid/new", "fixture.invalid/old"))
    failed_compose = subprocess.run(
        command, cwd=tmp_path, env=environment, text=True, capture_output=True, check=False
    )
    assert failed_compose.returncode == 31
    assert "Deployment fehlgeschlagen (Code 31)" in failed_compose.stderr
    assert rollback_log.read_text(encoding="utf-8").strip() == "m8-backup --automatic"
    environment.pop("M8_FAIL_CANDIDATE_UP")

    _stub(scripts / "rollback.sh", "exit 9\n")
    env_file.write_text(env_file.read_text().replace("fixture.invalid/new", "fixture.invalid/old"))
    failed_rollback = subprocess.run(
        command, cwd=tmp_path, env=environment, text=True, capture_output=True, check=False
    )
    assert failed_rollback.returncode == 70
    assert "Rollback meldet einen Fehler" in failed_rollback.stderr


def test_deploy_activates_relevant_folder_only_after_backup_and_before_smoke(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "deployment/scripts"
    scripts.mkdir(parents=True)
    for name in ("deploy.sh", "common.sh"):
        shutil.copy2(ROOT / "docker/scripts" / name, scripts / name)
    events = tmp_path / "events.log"
    _stub(scripts / "verify-image-supply-chain.sh", "exit 0\n")
    _stub(scripts / "check-maintenance-runtime.sh", "exit 0\n")
    (scripts / "check-layout-compatibility.py").write_text(
        "#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8"
    )
    _stub(scripts / "backup.sh", 'printf "backup\\n" >> "$M9_EVENTS"\nprintf "m9-backup\\n"\n')
    _stub(scripts / "smoke-test.sh", 'printf "smoke\\n" >> "$M9_EVENTS"\nexit 29\n')
    _stub(
        scripts / "rollback.sh",
        'printf "rollback:%s\\n" "$*" >> "$M9_EVENTS"\nexit 0\n',
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _stub(
        fake_bin / "docker",
        'printf "docker:%s\\n" "$*" >> "$M9_EVENTS"\n'
        'if [ "${1:-}" = "compose" ]; then\n'
        '  case " $* " in *" ps -q "*) printf "m9-id\\n";; esac\n'
        'elif [ "${1:-}" = "inspect" ]; then\n'
        '  case " $* " in *"State.Running"*) printf "false\\n";; *) printf "running\\n";; esac\n'
        'fi\n'
        "exit 0\n",
    )
    _stub(fake_bin / "systemctl", "exit 1\n")
    env_file = tmp_path / "deployment/.env"
    env_file.write_text(
        "OPENCLAW_IMAGE=fixture.invalid/old:runtime\n"
        "OPENCLAW_PROXY_IMAGE=fixture.invalid/old:proxy\n"
        "OPENCLAW_MAINTENANCE_IMAGE=fixture.invalid/old:maintenance\n"
        "OPENCLAW_CURRENT_RUNTIME=docker\n"
        "OPENCLAW_WRITE_TEST_ENABLED=false\n"
        "REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=false\n",
        encoding="utf-8",
    )
    compose_file = tmp_path / "deployment/compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    openclaw = tmp_path / "openclaw"
    for name in ("state", "config", "secrets", "backups/releases"):
        (openclaw / name).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "OPENCLAW_DEPLOY_ENV": str(env_file),
            "OPENCLAW_COMPOSE_FILE": str(compose_file),
            "OPENCLAW_ROOT": str(openclaw),
            "OPENCLAW_STATE_DIR": str(openclaw / "state"),
            "OPENCLAW_CONFIG_DIR": str(openclaw / "config"),
            "OPENCLAW_SECRETS_DIR": str(openclaw / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(openclaw / "backups/releases"),
            "OPENCLAW_EXPECTED_SOURCE_REVISION": "0123456789abcdef0123456789abcdef01234567",
            "OPENCLAW_MAIL_RELEVANT_FOLDER": "Agent/Relevant",
            "OPENCLAW_MAIL_RELEVANT_FOLDER_APPROVED": "true",
            "M9_EVENTS": str(events),
        }
    )
    command = [
        str(scripts / "deploy.sh"),
        "fixture.invalid/new:runtime",
        "fixture.invalid/new:proxy",
        "fixture.invalid/new:maintenance",
    ]

    result = subprocess.run(
        command, cwd=tmp_path, env=environment, text=True, capture_output=True, check=False
    )

    assert result.returncode == 29
    lines = events.read_text(encoding="utf-8").splitlines()
    backup_index = lines.index("backup")
    activation_index = next(
        index
        for index, line in enumerate(lines)
        if "mail folders activate-relevant --relevant Agent/Relevant --yes" in line
    )
    smoke_index = lines.index("smoke")
    rollback_index = lines.index("rollback:m9-backup --automatic")
    assert backup_index < activation_index < smoke_index < rollback_index

    before = lines.count("backup")
    environment["OPENCLAW_MAIL_RELEVANT_FOLDER_APPROVED"] = "false"
    rejected = subprocess.run(
        command, cwd=tmp_path, env=environment, text=True, capture_output=True, check=False
    )
    assert rejected.returncode == 2
    assert "ausdrueckliche Freigabe" in rejected.stderr
    assert events.read_text(encoding="utf-8").splitlines().count("backup") == before
