from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from personal_assistant.job_control import CommandResult, JobController


class ContainerWorkspaceTests(unittest.TestCase):
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
                        "updated_at": datetime.now(timezone.utc).isoformat(),
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
                        "updated_at": datetime.now(timezone.utc).isoformat(),
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
        compose = (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(encoding="utf-8")
        clamav = compose.split("  clamav-update:\n", 1)[1].split("\nvolumes:\n", 1)[0]
        self.assertIn("/var/lib/clamav/main.cvd", clamav)
        self.assertIn("/var/lib/clamav/daily.cvd", clamav)
        self.assertIn("/var/lib/clamav/bytecode.cvd", clamav)
        self.assertNotIn("127.0.0.1:18789", clamav)

    def test_entrypoint_builds_runtime_ca_bundle_from_public_crt_files(self) -> None:
        entrypoint = (Path(__file__).resolve().parents[1] / "docker/entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("configure_custom_ca", entrypoint)
        self.assertIn("SSL_CERT_FILE", entrypoint)
        self.assertIn("REQUESTS_CA_BUNDLE", entrypoint)
        self.assertIn("NODE_EXTRA_CA_CERTS", entrypoint)
        self.assertIn("-name '*.crt'", entrypoint)

    def test_branch_image_revision_invalidates_workspace_source_marker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (root / "docker/entrypoint.sh").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/container.yml").read_text(encoding="utf-8")
        local_build = (root / "docker/scripts/build-local.sh").read_text(encoding="utf-8")

        self.assertIn("ARG OPENCLAW_SOURCE_REVISION=local", dockerfile)
        self.assertIn(
            'LABEL org.opencontainers.image.revision="${OPENCLAW_SOURCE_REVISION}"',
            dockerfile,
        )
        self.assertIn("/opt/openclaw-agent/SOURCE_REVISION", dockerfile)
        self.assertIn('source_id="$version@$source_revision"', entrypoint)
        self.assertIn('OPENCLAW_SOURCE_REVISION=${{ github.sha }}', workflow)
        self.assertIn('--build-arg OPENCLAW_SOURCE_REVISION="$revision"', local_build)

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

    def test_deploy_verifies_source_revision_and_disables_legacy_writers(self) -> None:
        deploy = (
            Path(__file__).resolve().parents[1] / "docker/scripts/deploy.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("org.opencontainers.image.revision", deploy)
        self.assertIn("/opt/openclaw-agent/SOURCE_REVISION", deploy)
        self.assertIn("assert_legacy_writers_disabled", deploy)
        self.assertIn("validate_legacy_home", deploy)

    def test_deployment_checks_job_status_only_after_workers_are_healthy(self) -> None:
        root = Path(__file__).resolve().parents[1]
        smoke = (root / "docker/scripts/smoke-test.sh").read_text(encoding="utf-8")
        deploy = (root / "docker/scripts/deploy.sh").read_text(encoding="utf-8")
        jobs_command = (
            "/home/node/.openclaw/workspace/scripts/assistant.sh "
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
        service = root / "deploy/systemd/personal-assistant-monitor.service"
        timer = root / "deploy/systemd/personal-assistant-monitor.timer"
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
        self.assertNotIn("assistant.sh mail compose-send", smoke)

    def test_rollback_restores_contents_without_removing_protected_roots(self) -> None:
        rollback = (
            Path(__file__).resolve().parents[1] / "docker/scripts/rollback.sh"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            'rm -rf "$OPENCLAW_STATE_DIR" "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR"',
            rollback,
        )
        self.assertIn('tar -xzf "$backup/payload.tar.gz" -C "$restore_root"', rollback)
        self.assertIn('rsync -a --delete "$source/" "$target/"', rollback)
        self.assertIn(
            "setup-host.sh muss die geschuetzte Hoststruktur anlegen", rollback
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
