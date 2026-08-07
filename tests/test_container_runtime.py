from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from personal_assistant.job_control import CommandResult, JobController


class ContainerWorkspaceTests(unittest.TestCase):
    def test_runtime_check_reports_each_invalid_oci_layout_label(self) -> None:
        root = Path(__file__).resolve().parents[1]
        checker = root / "scripts/check-container-runtime.sh"
        fake_docker = r'''#!/usr/bin/env python3
import os
import sys

arguments = sys.argv[1:]
if arguments == ["info"]:
    raise SystemExit(0)
if arguments[:2] == ["image", "inspect"]:
    if "--format" not in arguments:
        raise SystemExit(0)
    template = arguments[arguments.index("--format") + 1]
    if "layout-min" in template:
        print(os.environ.get("FAKE_LAYOUT_MIN", "1"))
    elif "layout-max" in template:
        print(os.environ.get("FAKE_LAYOUT_MAX", "3"))
    elif "image.revision" in template:
        print(os.environ.get("FAKE_REVISION", "fixture-revision"))
    raise SystemExit(0)
if arguments and arguments[0] == "run":
    if "/bin/chown" in arguments or "--name" in arguments:
        raise SystemExit(0)
    if any(item.endswith(":/fixture:ro") for item in arguments):
        print("fixture inspected inside docker", file=sys.stderr)
        raise SystemExit(73)
if arguments and arguments[0] == "rm":
    raise SystemExit(0)
print("unexpected fake docker call: " + " ".join(arguments), file=sys.stderr)
raise SystemExit(86)
'''
        cases = (
            (
                {"FAKE_REVISION": ""},
                "OCI-Label org.opencontainers.image.revision fehlt",
            ),
            (
                {"FAKE_LAYOUT_MIN": "0"},
                "OCI-Label org.opencontainers.image.openclaw.layout-min in fixture: "
                "erwartet '1', erhalten '0'",
            ),
            (
                {"FAKE_LAYOUT_MAX": "4"},
                "OCI-Label org.opencontainers.image.openclaw.layout-max in fixture: "
                "erwartet '3', erhalten '4'",
            ),
        )
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "docker"
            binary.write_text(fake_docker, encoding="utf-8")
            binary.chmod(0o755)
            for overrides, expected in cases:
                with self.subTest(expected=expected):
                    environment = os.environ.copy()
                    environment.update(overrides)
                    environment["PATH"] = f"{folder}:{environment['PATH']}"
                    result = subprocess.run(
                        [str(checker), "fixture"],
                        cwd=root,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(f"ERROR: M3: {expected}", result.stderr)

            environment = os.environ.copy()
            environment["PATH"] = f"{folder}:{environment['PATH']}"
            result = subprocess.run(
                [str(checker), "fixture"],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("fixture inspected inside docker", result.stderr)
            self.assertIn(
                "Layoutnachbedingungen im UID-unabhaengigen Pruefcontainer verletzt",
                result.stderr,
            )

    def test_container_publish_workflow_isolates_dynamic_m3_and_m4_steps(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/container.yml"
        ).read_text(encoding="utf-8")
        supply_chain = workflow.index("- name: Generate SBOM/provenance and scan every role")
        runtime = workflow.index("- name: Verify state isolation and immutable runtime")
        hardening = workflow.index("- name: Verify role hardening")
        publish = workflow.index("- name: Rebuild twice and require identical OCI artifacts")
        self.assertLess(supply_chain, runtime)
        self.assertLess(runtime, hardening)
        self.assertLess(hardening, publish)
        self.assertIn(
            "run: ./scripts/check-container-runtime.sh openclaw-agent:m7-candidate",
            workflow[runtime:hardening],
        )
        self.assertIn(
            "run: ./scripts/check-container-hardening.sh openclaw-agent:m7-candidate",
            workflow[hardening:publish],
        )

    def test_workspace_environment_controls_both_packages(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            environment = os.environ.copy()
            environment["OPENCLAW_WORKSPACE"] = folder
            code = (
                "from mail_agent.config import WORKSPACE_ROOT as a; "
                "from personal_assistant.config import WORKSPACE_ROOT as b; "
                "print(a); print(b)"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout.splitlines(), [folder, folder])

    def test_container_job_status_uses_worker_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            status_dir = root / "personal_assistant/data/container_jobs"
            status_dir.mkdir(parents=True)
            (status_dir / "mail.json").write_text(
                json.dumps(
                    {
                        "updated_at": datetime.now(UTC).isoformat(),
                        "state": "waiting",
                        "result": "success",
                        "last_exit_code": 0,
                    }
                ),
                encoding="utf-8",
            )

            def runner(command: list[str], timeout: int) -> CommandResult:
                del command, timeout
                return CommandResult(0, '{"ok": true}', "")

            with patch.dict(
                os.environ,
                {
                    "OPENCLAW_RUNTIME": "container",
                    "OPENCLAW_JOB_STATUS_DIR": str(status_dir),
                },
            ):
                controller = JobController(
                    workspace_root=root,
                    state_path=root / "personal_assistant/data/job_control.json",
                    runner=runner,
                )
                report = controller.status(target="mail")
                self.assertTrue(report["ok"])
                self.assertEqual(report["jobs"][0]["state"], "on")
                stopped = controller.off(target="mail")
                self.assertTrue(stopped["ok"])
                self.assertEqual(stopped["status"]["jobs"][1]["desired"], "off")

    def test_portfolio_warning_heartbeat_is_degraded_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            status_dir = root / "personal_assistant/data/container_jobs"
            status_dir.mkdir(parents=True)
            (status_dir / "portfolio.json").write_text(
                json.dumps(
                    {
                        "updated_at": datetime.now(UTC).isoformat(),
                        "state": "waiting",
                        "result": "degraded",
                        "last_exit_code": 1,
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "OPENCLAW_RUNTIME": "container",
                    "OPENCLAW_JOB_STATUS_DIR": str(status_dir),
                },
            ):
                controller = JobController(
                    workspace_root=root,
                    state_path=root / "personal_assistant/data/job_control.json",
                )
                controller.state["desired"]["portfolio"] = True
                report = controller.status(target="portfolio")
            self.assertFalse(report["ok"])
            self.assertEqual(report["jobs"][0]["state"], "degraded")
            self.assertEqual(
                report["jobs"][0]["issues"][0]["code"], "service-degraded"
            )

    def test_clamav_updater_has_its_own_database_healthcheck(self) -> None:
        root = Path(__file__).resolve().parents[1]
        compose = (root / "compose.yaml").read_text(encoding="utf-8")
        verifier = (root / "personal_assistant/clamav_health.py").read_text(encoding="utf-8")
        deploy = (root / "docker/scripts/deploy.sh").read_text(encoding="utf-8")
        clamav = compose.split("  clamav-update:\n", 1)[1].split("\nvolumes:\n", 1)[0]
        self.assertIn("personal_assistant.clamav_health", clamav)
        self.assertIn('SIGNATURE_GROUPS = ("main", "daily", "bytecode")', verifier)
        self.assertIn("max_age_seconds", verifier)
        self.assertIn("scanner_identity", verifier)
        self.assertNotIn("127.0.0.1:18789", clamav)
        self.assertNotIn("freshclam clamav-update --verbose || true", deploy)
        self.assertIn("-P -m personal_assistant.clamav_health", deploy)
        self.assertIn("wait_for_healthy clamav-update 180", deploy)

    def test_entrypoint_builds_runtime_ca_bundle_from_public_crt_files(self) -> None:
        entrypoint = (
            Path(__file__).resolve().parents[1] / "personal_assistant/container_entrypoint.py"
        ).read_text(encoding="utf-8")
        self.assertIn("configure_custom_ca", entrypoint)
        self.assertIn("SSL_CERT_FILE", entrypoint)
        self.assertIn("REQUESTS_CA_BUNDLE", entrypoint)
        self.assertIn("NODE_EXTRA_CA_CERTS", entrypoint)
        self.assertIn('glob("*.crt")', entrypoint)

    def test_branch_image_revision_is_bound_to_immutable_layout_marker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (root / "personal_assistant/container_entrypoint.py").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/container.yml").read_text(encoding="utf-8")
        local_build = (root / "docker/scripts/build-local.sh").read_text(encoding="utf-8")

        self.assertIn("ARG OPENCLAW_SOURCE_REVISION=local", dockerfile)
        self.assertIn(
            'org.opencontainers.image.revision="${OPENCLAW_SOURCE_REVISION}"',
            dockerfile,
        )
        self.assertIn("/opt/openclaw-agent/SOURCE_REVISION", dockerfile)
        self.assertIn('"personal_assistant.runtime_layout"', entrypoint)
        self.assertIn("OPENCLAW_IMAGE_REVISION=${OPENCLAW_SOURCE_REVISION}", dockerfile)
        self.assertIn('OPENCLAW_SOURCE_REVISION=${{ github.sha }}', workflow)
        self.assertIn('OPENCLAW_SOURCE_REVISION=$revision', local_build)

    def test_test_branch_push_builds_sha_tagged_container(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/container.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('- "test/**"', workflow)
        self.assertIn("type=ref,event=branch", workflow)
        self.assertIn("type=sha,prefix=sha-", workflow)
        self.assertIn("DOCKER_METADATA_SHORT_SHA_LENGTH: 12", workflow)
        self.assertIn("cancel-in-progress: true", workflow)

    def test_live_test_checks_docker_access_and_exports_exact_revision(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "docker/scripts/live-test-branch.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("docker info", helper)
        self.assertIn("sg docker -c", helper)
        self.assertNotIn("newgrp docker", helper)
        self.assertIn(
            'export OPENCLAW_EXPECTED_SOURCE_REVISION="$local_revision"',
            helper,
        )
        self.assertIn("@sha256:[0-9a-f]{64}", helper)
        self.assertIn('"$runtime_image" "$proxy_image" "$maintenance_image"', helper)

    def test_deploy_verifies_source_revision_and_disables_legacy_writers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        deploy = (root / "docker/scripts/deploy.sh").read_text(encoding="utf-8")
        verifier = (root / "docker/scripts/verify-image-supply-chain.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('"$SCRIPT_DIR/verify-image-supply-chain.sh"', deploy)
        self.assertIn("org.opencontainers.image.revision", verifier)
        self.assertIn('actual_revision" == "$expected_revision', verifier)
        self.assertIn("assert_legacy_writers_disabled", deploy)
        self.assertIn("validate_legacy_home", deploy)

    def test_deployment_checks_job_status_only_after_workers_are_healthy(self) -> None:
        root = Path(__file__).resolve().parents[1]
        smoke = (root / "docker/scripts/smoke-test.sh").read_text(encoding="utf-8")
        deploy = (root / "docker/scripts/deploy.sh").read_text(encoding="utf-8")
        jobs_command = (
            "/opt/openclaw-agent/scripts/assistant.sh "
            "jobs status --target all"
        )

        self.assertNotIn(jobs_command, smoke)
        workers_started = deploy.index(
            "compose up -d mail-worker sync-worker supervisor-worker portfolio-worker monitor-worker"
        )
        supervisor_healthy = deploy.index(
            "wait_for_healthy supervisor-worker 180", workers_started
        )
        portfolio_healthy = deploy.index(
            "wait_for_healthy portfolio-worker 180", supervisor_healthy
        )
        monitor_healthy = deploy.index(
            "wait_for_healthy monitor-worker 180", portfolio_healthy
        )
        jobs_checked = deploy.index(jobs_command, monitor_healthy)
        self.assertLess(workers_started, supervisor_healthy)
        self.assertLess(supervisor_healthy, portfolio_healthy)
        self.assertLess(portfolio_healthy, monitor_healthy)
        self.assertLess(monitor_healthy, jobs_checked)

    def test_business_workers_use_scheduler_but_supervisor_stays_independent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        compose = (root / "compose.yaml").read_text(encoding="utf-8")
        loop = (root / "docker/job_loop.py").read_text(encoding="utf-8")
        self.assertIn("monitor-worker:", compose)
        self.assertIn('job == "monitor"', loop)
        self.assertIn(
            'scheduler = None if args.job == "supervisor"',
            loop,
        )
        self.assertIn("scheduler.enqueue(", loop)
        self.assertIn("scheduler.renew(", loop)
        self.assertIn("OPENCLAW_SCHEDULER_SOURCE", loop)

    def test_packaged_hourly_monitor_units_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = root / "legacy/systemd/units/personal-assistant-monitor.service"
        timer = root / "legacy/systemd/units/personal-assistant-monitor.timer"
        self.assertTrue(service.is_file())
        self.assertTrue(timer.is_file())
        self.assertIn("monitor record --days 7 --live", service.read_text(encoding="utf-8"))
        self.assertIn("OnUnitInactiveSec=1h", timer.read_text(encoding="utf-8"))

    def test_smoke_test_checks_compose_cli_without_sending(self) -> None:
        smoke = (
            Path(__file__).resolve().parents[1] / "docker/scripts/smoke-test.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("assistant.sh capabilities", smoke)
        self.assertIn("assistant.sh mail compose-draft --help", smoke)
        self.assertIn("OPENCLAW_MAIL_SEARCH_SMOKE_ENABLED", smoke)
        self.assertIn("assistant.sh mail search --query", smoke)
        self.assertNotIn("assistant.sh mail compose-send", smoke)

    def test_rollback_restores_contents_without_removing_protected_roots(self) -> None:
        root = Path(__file__).resolve().parents[1]
        rollback = (root / "docker/scripts/rollback.sh").read_text(encoding="utf-8")
        restore = (root / "docker/scripts/restore-local-state.sh").read_text(encoding="utf-8")

        self.assertNotIn(
            'rm -rf "$OPENCLAW_STATE_DIR" "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR"',
            rollback,
        )
        self.assertIn('OPENCLAW_RESTORE_OFFLINE=YES', rollback)
        self.assertIn('tar -xzf "$backup/payload.tar.gz" -C "$restore_root"', restore)
        self.assertIn('rsync -a --delete "$restore_root/state/"', restore)
        self.assertIn(
            "setup-host.sh muss die geschuetzte Hoststruktur anlegen", restore
        )

    def test_legacy_rollback_never_replaces_the_original_workspace_from_container_state(self) -> None:
        rollback = (
            Path(__file__).resolve().parents[1] / "docker/scripts/rollback.sh"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            'rsync -a --delete "$OPENCLAW_STATE_DIR/" "$legacy_home/"',
            rollback,
        )
        self.assertIn(
            '[[ -x "$legacy_home/workspace/scripts/assistant.sh" ]]',
            rollback,
        )
        self.assertIn(
            "Verwende den unveraenderten systemd-Workspace weiter",
            rollback,
        )
        self.assertIn("restore_legacy_home_from_migration", rollback)
        self.assertIn(
            "Rollback wurde vor dem Stoppen der aktuellen Container abgebrochen",
            rollback,
        )
        self.assertLess(
            rollback.index("restore_legacy_home_from_migration"),
            rollback.index('echo "Stoppe aktuellen Containerstand."'),
        )

    def test_live_migration_validates_and_stages_before_publishing_state(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1] / "docker/scripts/migrate-live.sh"
        ).read_text(encoding="utf-8")

        source_validation = migration.index(
            '"$SOURCE_HOME/workspace/scripts/assistant.sh"'
        )
        stop_units = migration.index(
            'systemctl --user disable --now "$unit"'
        )
        stage_copy = migration.index(
            'rsync -a --delete "$SOURCE_HOME/" "$stage_state/"'
        )
        publish_state = migration.index(
            'rsync -a --delete "$stage_state/" "$OPENCLAW_STATE_DIR/"'
        )

        self.assertLess(source_validation, stop_units)
        self.assertLess(stop_units, stage_copy)
        self.assertLess(stage_copy, publish_state)
        self.assertIn("--ensure-gateway-auth", migration)
        self.assertIn("--normalize-ollama-proxy", migration)
        self.assertIn("PRAGMA quick_check;", migration)
        self.assertIn(
            '"$OPENCLAW_CONFIG_DIR/legacy-active-units.txt"',
            migration,
        )
        self.assertIn(
            'sort -u -o "$legacy_units_snapshot" "$legacy_units_snapshot"',
            migration,
        )
        self.assertIn(
            '"$source_member/workspace/scripts/assistant.sh"',
            migration,
        )
        self.assertIn('"$SCRIPT_DIR/backup.sh" "pre-container-remigration-$stamp"', migration)
        self.assertIn("restore_prepublish_state", migration)
        self.assertIn(
            "update_env_value OPENCLAW_LEGACY_MIGRATION_BACKUP",
            migration,
        )

    def test_release_backup_links_and_verifies_legacy_migration_archive(self) -> None:
        root = Path(__file__).resolve().parents[1]
        backup = (root / "docker/scripts/backup.sh").read_text(encoding="utf-8")
        verify = (root / "docker/scripts/verify-backup.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('"legacy_migration_backup"', backup)
        self.assertIn('"legacy_migration_member"', backup)
        self.assertIn('"legacy_migration_sha256"', backup)
        self.assertIn("SHA-256 des Legacy-Migrationsbackups stimmt nicht", verify)

    def test_release_backup_manifest_keeps_verified_legacy_archive(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            temporary = Path(folder)
            openclaw_root = temporary / "openclaw"
            for name in ("state", "config", "secrets", "backups/releases"):
                (openclaw_root / name).mkdir(parents=True)
            (openclaw_root / "state/data.txt").write_text(
                "state", encoding="utf-8"
            )

            legacy_source = temporary / "legacy-source/.openclaw"
            (legacy_source / "workspace/scripts").mkdir(parents=True)
            (legacy_source / "openclaw.json").write_text(
                '{"gateway":{"mode":"local"}}\n', encoding="utf-8"
            )
            for name in ("assistant.sh", "mail-agent.sh"):
                script = legacy_source / "workspace/scripts" / name
                script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                script.chmod(0o700)

            migration_archive = temporary / "legacy-migration.tar.gz"
            with tarfile.open(migration_archive, "w:gz") as archive:
                archive.add(legacy_source, arcname=".openclaw")
            migration_sha = subprocess.run(
                ["sha256sum", str(migration_archive)],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.split()[0]

            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            fake_sqlite = fake_bin / "sqlite3"
            fake_sqlite.write_text("#!/bin/sh\nprintf 'ok\\n'\n", encoding="utf-8")
            fake_sqlite.chmod(0o700)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment.get('PATH', '')}",
                    "OPENCLAW_ROOT": str(openclaw_root),
                    "OPENCLAW_STATE_DIR": str(openclaw_root / "state"),
                    "OPENCLAW_CONFIG_DIR": str(openclaw_root / "config"),
                    "OPENCLAW_SECRETS_DIR": str(openclaw_root / "secrets"),
                    "OPENCLAW_BACKUP_DIR": str(
                        openclaw_root / "backups/releases"
                    ),
                    "OPENCLAW_LEGACY_HOME": str(legacy_source),
                    "OPENCLAW_LEGACY_MIGRATION_BACKUP": str(
                        migration_archive
                    ),
                    "OPENCLAW_LEGACY_MIGRATION_MEMBER": ".openclaw",
                    "OPENCLAW_LEGACY_MIGRATION_SHA256": migration_sha,
                    "PREVIOUS_RUNTIME": "legacy-systemd",
                    "PREVIOUS_IMAGE": "previous:test",
                    "TARGET_IMAGE": "target:test",
                }
            )
            created = subprocess.run(
                [str(root / "docker/scripts/backup.sh"), "test-linked-legacy"],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            backup_id = created.stdout.strip().splitlines()[-1]
            backup_dir = openclaw_root / "backups/releases" / backup_id
            manifest = json.loads(
                (backup_dir / "manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(
                manifest["legacy_migration_backup"], str(migration_archive)
            )
            self.assertEqual(
                manifest["legacy_migration_sha256"], migration_sha
            )
            subprocess.run(
                [str(root / "docker/scripts/verify-backup.sh"), str(backup_dir)],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
