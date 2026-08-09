from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
import os
import signal
import sqlite3
import subprocess
import tempfile
import time
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from personal_assistant.runtime_layout import (
    CONTAINER_GATEWAY_WORKSPACE,
    LAYOUT_VERSION,
    _prune_assistant_database,
    backup_state,
    migrate_layout,
    restore_backup,
)
from personal_assistant.storage import AssistantStorage
from personal_assistant.work_scheduler import AdaptiveWorkScheduler

ROOT = Path(__file__).resolve().parents[1]


def _create_action(database: str, ready: multiprocessing.Event, output: multiprocessing.Queue) -> None:
    ready.wait(10)
    storage = AssistantStorage(Path(database))
    try:
        plan = storage.create_action(
            idempotency_key="parallel-action",
            action_type="files.create",
            resource_id="fixture",
            payload={"path": "Assistent/fixture.txt"},
            requires_approval=True,
        )
        output.put(plan.id)
    finally:
        storage.close()


def _scheduler_enqueue(database: str, job: str, ready: multiprocessing.Event) -> None:
    ready.wait(10)
    scheduler = AdaptiveWorkScheduler(Path(database), arbitration_seconds=0)
    try:
        scheduler.enqueue(job, owner=f"test:{job}", arbitration_seconds=0)
    finally:
        scheduler.close()


def _uncommitted_write(database: str, ready: multiprocessing.Event) -> None:
    connection = sqlite3.connect(database, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS crash_fixture(value TEXT)")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("INSERT INTO crash_fixture(value) VALUES('must-rollback')")
    ready.set()
    time.sleep(30)


class StateLayoutM3Tests(unittest.TestCase):
    def _state(self, folder: str) -> tuple[Path, Path]:
        state = Path(folder) / "state"
        workspace = state / "workspace"
        (workspace / "mail_agent/data").mkdir(parents=True)
        (workspace / "personal_assistant/data").mkdir(parents=True)
        mail = sqlite3.connect(workspace / "mail_agent/data/mail_agent.sqlite3")
        mail.execute("CREATE TABLE corrections(id INTEGER PRIMARY KEY, label TEXT)")
        mail.execute("INSERT INTO corrections(label) VALUES('keep')")
        mail.commit()
        mail.close()
        core = AssistantStorage(workspace / "personal_assistant/data/assistant.sqlite3")
        core.create_action(
            idempotency_key="keep-action",
            action_type="files.create",
            resource_id="fixture",
            payload={"path": "Assistent/keep.txt"},
            requires_approval=True,
        )
        core.index_document(
            source_type="fixture",
            resource_id="fixture",
            source_id="knowledge-1",
            uri="fixture://knowledge-1",
            title="Getrennter Wissensstand",
            chunks=["Dieser Inhalt muss in die Wissensdatenbank migriert werden."],
        )
        core.close()
        (workspace / "LOCAL_NOTES.md").write_text("keep\n", encoding="utf-8")
        (workspace / "AGENTS.md").symlink_to("/opt/openclaw-agent/AGENTS.md")
        (state / "openclaw.json").write_text(
            '{"gateway":{"mode":"local"}}\n',
            encoding="utf-8",
        )
        return state, workspace

    def _completed_profile(self, workspace: Path) -> dict[str, bytes]:
        contents = {
            "IDENTITY.md": b"# IDENTITY.md\n\n- Name: Ada\n",
            "SOUL.md": b"# SOUL.md\n\nPraezise und warm.\n",
            "USER.md": b"# USER.md\n\n- Name: Jan\n",
            "openclaw-workspace-state.json": (
                b'{"version":1,"setupCompletedAt":"2026-07-21T06:48:03.443Z"}\n'
            ),
        }
        for name, content in contents.items():
            (workspace / name).write_bytes(content)
        return contents

    def _simulate_old_v3_profile_bug(
        self,
        state: Path,
    ) -> tuple[Path, dict[str, bytes]]:
        active = state / "v3/instance"
        completed = {
            name: (active / name).read_bytes()
            for name in (*("IDENTITY.md", "SOUL.md", "USER.md"),
                         "openclaw-workspace-state.json")
        }
        legacy = active / "local-workspace"
        legacy.mkdir(parents=True, exist_ok=True)
        for name, content in completed.items():
            (legacy / name).write_bytes(content)

        generated = {
            "IDENTITY.md": b"# IDENTITY.md\n\n- Name:\n",
            "SOUL.md": b"# SOUL.md\n\nWho are you?\n",
            "USER.md": b"# USER.md\n\n- Name:\n",
        }
        for name, content in generated.items():
            (active / name).write_bytes(content)
        (active / "BOOTSTRAP.md").write_text("# BOOTSTRAP.md\n", encoding="utf-8")
        (active / "openclaw-workspace-state.json").write_text(
            '{"version":1,"bootstrapSeededAt":"2026-08-09T09:51:05.113Z"}\n',
            encoding="utf-8",
        )

        workspace_id = hashlib.sha256(
            CONTAINER_GATEWAY_WORKSPACE.encode("utf-8")
        ).hexdigest()
        attestation = state / f"v3/gateway/workspace-attestations/{workspace_id}.attested"
        attestation.parent.mkdir(parents=True, exist_ok=True)
        lines = ["openclaw-workspace-attestation:v1", "2026-08-09T09:51:05.113Z"]
        for name, content in generated.items():
            lines.append(f"generated:{name}:{hashlib.sha256(content).hexdigest()}")
        attestation.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return active, completed

    def test_initial_layout_keeps_completed_identity_profile_active(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            expected = self._completed_profile(workspace)
            (workspace / "TOOLS.md").write_text(
                "# Legacy TOOLS.md\n\nUnsafe historical commands.\n",
                encoding="utf-8",
            )

            migrate_layout(ROOT, state, workspace)
            active = state / "v3/instance"

            for name, content in expected.items():
                self.assertEqual((active / name).read_bytes(), content)
                self.assertFalse((active / "local-workspace" / name).exists())
            self.assertFalse((active / "TOOLS.md").exists())
            self.assertTrue((active / "local-workspace/TOOLS.md").is_file())

    def test_release_contract_is_published_to_active_v3_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)

            first = migrate_layout(ROOT, state, workspace)
            active = state / "v3/instance"

            self.assertEqual(
                os.readlink(active / "AGENTS.md"),
                str(ROOT / "AGENTS.md"),
            )
            self.assertEqual(
                os.readlink(active / "HEARTBEAT.md"),
                str(ROOT / "HEARTBEAT.md"),
            )
            self.assertFalse((active / "skills/personal-assistant").exists())
            self.assertFalse((workspace / "AGENTS.md").exists())
            self.assertFalse((workspace / "skills/personal-assistant").exists())
            gateway = json.loads(
                (state / "v3/gateway/openclaw.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                gateway["skills"]["load"]["extraDirs"],
                ["/opt/openclaw-agent/skills"],
            )
            self.assertNotIn("skills/personal-assistant", first.release_links)
            self.assertFalse(migrate_layout(ROOT, state, workspace).changed)

    def test_existing_v3_replaces_generated_agent_contract_with_release_links(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            migrate_layout(ROOT, state, workspace)
            active = state / "v3/instance"
            (active / "AGENTS.md").unlink()
            (active / "HEARTBEAT.md").unlink()
            (active / "skills").mkdir()
            (active / "skills/personal-assistant").symlink_to(
                ROOT / "skills/personal-assistant",
                target_is_directory=True,
            )
            (active / "AGENTS.md").write_text(
                "# generic OpenClaw agent instructions\n",
                encoding="utf-8",
            )
            (active / "HEARTBEAT.md").write_text(
                "# generic OpenClaw heartbeat\n",
                encoding="utf-8",
            )

            report = migrate_layout(ROOT, state, workspace)

            self.assertTrue(report.changed)
            self.assertEqual(os.readlink(active / "AGENTS.md"), str(ROOT / "AGENTS.md"))
            self.assertFalse((active / "skills/personal-assistant").exists())
            gateway = json.loads(
                (state / "v3/gateway/openclaw.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                gateway["skills"]["load"]["extraDirs"],
                ["/opt/openclaw-agent/skills"],
            )

    def test_skill_root_preserves_existing_shared_skill_directories(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            (state / "openclaw.json").write_text(
                json.dumps(
                    {
                        "gateway": {"mode": "local"},
                        "skills": {"load": {"extraDirs": ["/trusted/shared-skills"]}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            migrate_layout(ROOT, state, workspace)

            gateway = json.loads(
                (state / "v3/gateway/openclaw.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                gateway["skills"]["load"]["extraDirs"],
                ["/trusted/shared-skills", "/opt/openclaw-agent/skills"],
            )

    def test_invalid_shared_skill_configuration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            (state / "openclaw.json").write_text(
                json.dumps(
                    {
                        "gateway": {"mode": "local"},
                        "skills": {"load": {"extraDirs": "/untrusted/skills"}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "extraDirs"):
                migrate_layout(ROOT, state, workspace)

    def test_existing_v3_recovers_attested_generated_profile_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            self._completed_profile(workspace)
            migrate_layout(ROOT, state, workspace)
            active, expected = self._simulate_old_v3_profile_bug(state)

            recovered = migrate_layout(ROOT, state, workspace)

            self.assertTrue(recovered.changed)
            for name, content in expected.items():
                self.assertEqual((active / name).read_bytes(), content)
                self.assertEqual(
                    (active / "local-workspace" / name).read_bytes(),
                    content,
                )
            self.assertTrue((active / "BOOTSTRAP.md").is_file())
            self.assertFalse(migrate_layout(ROOT, state, workspace).changed)

    def test_existing_v3_does_not_overwrite_edited_pending_identity(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            self._completed_profile(workspace)
            migrate_layout(ROOT, state, workspace)
            active, _expected = self._simulate_old_v3_profile_bug(state)
            edited = b"# IDENTITY.md\n\n- Name: User changed this pending profile\n"
            (active / "IDENTITY.md").write_bytes(edited)
            state_before = (active / "openclaw-workspace-state.json").read_bytes()
            soul_before = (active / "SOUL.md").read_bytes()

            with self.assertRaisesRegex(RuntimeError, "wurde veraendert"):
                migrate_layout(ROOT, state, workspace)

            self.assertEqual((active / "IDENTITY.md").read_bytes(), edited)
            self.assertEqual((active / "SOUL.md").read_bytes(), soul_before)
            self.assertEqual(
                (active / "openclaw-workspace-state.json").read_bytes(),
                state_before,
            )

    def test_existing_v3_does_not_overwrite_edited_pending_setup_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            self._completed_profile(workspace)
            migrate_layout(ROOT, state, workspace)
            active, _expected = self._simulate_old_v3_profile_bug(state)
            edited_state = (
                b'{"version":1,"bootstrapSeededAt":"2026-08-09T09:51:05.113Z",'
                b'"userNote":"keep"}\n'
            )
            (active / "openclaw-workspace-state.json").write_bytes(edited_state)
            identity_before = (active / "IDENTITY.md").read_bytes()

            with self.assertRaisesRegex(RuntimeError, "kein unveraenderter"):
                migrate_layout(ROOT, state, workspace)

            self.assertEqual(
                (active / "openclaw-workspace-state.json").read_bytes(),
                edited_state,
            )
            self.assertEqual((active / "IDENTITY.md").read_bytes(), identity_before)

    def test_existing_v3_keeps_completed_active_identity(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            self._completed_profile(workspace)
            migrate_layout(ROOT, state, workspace)
            active, _expected = self._simulate_old_v3_profile_bug(state)
            replacement = b"# IDENTITY.md\n\n- Name: Newly completed identity\n"
            (active / "IDENTITY.md").write_bytes(replacement)
            (active / "openclaw-workspace-state.json").write_text(
                '{"version":1,"setupCompletedAt":"2026-08-09T10:15:00Z"}\n',
                encoding="utf-8",
            )

            report = migrate_layout(ROOT, state, workspace)

            self.assertFalse(report.changed)
            self.assertEqual((active / "IDENTITY.md").read_bytes(), replacement)

    def test_staged_migration_preserves_databases_and_supports_old_and_new_restore(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            report = migrate_layout(ROOT, state, workspace)
            self.assertEqual(report.layout, LAYOUT_VERSION)
            self.assertEqual(LAYOUT_VERSION, 3)
            mail = state / "v3/domains/mail/mail_agent.sqlite3"
            core = state / "v3/shared/core/assistant.sqlite3"
            knowledge = state / "v3/domains/knowledge/knowledge.sqlite3"
            self.assertEqual(
                sqlite3.connect(mail).execute("PRAGMA quick_check").fetchone()[0], "ok"
            )
            self.assertEqual(
                sqlite3.connect(core)
                .execute("SELECT COUNT(*) FROM action_plans")
                .fetchone()[0],
                1,
            )
            core_connection = sqlite3.connect(core)
            self.assertIsNone(
                core_connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
                ).fetchone()
            )
            core_connection.close()
            knowledge_connection = sqlite3.connect(knowledge)
            self.assertEqual(
                knowledge_connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                1,
            )
            self.assertIsNone(
                knowledge_connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_plans'"
                ).fetchone()
            )
            knowledge_connection.close()
            with patch.dict(
                os.environ,
                {"OPENCLAW_KNOWLEDGE_DATA_DIR": str(knowledge.parent)},
            ):
                sync_storage = AssistantStorage(
                    core,
                    read_only=True,
                    knowledge_read_only=False,
                )
                sync_storage.index_document(
                    source_type="fixture",
                    resource_id="fixture",
                    source_id="knowledge-2",
                    uri="fixture://knowledge-2",
                    title="Read-only Core",
                    chunks=["Der Sync darf nur in den Wissensbereich schreiben."],
                )
                with self.assertRaises(sqlite3.OperationalError):
                    sync_storage.create_action(
                        idempotency_key="forbidden-sync-action",
                        action_type="files.create",
                        resource_id="fixture",
                        payload={"path": "Assistent/forbidden.txt"},
                        requires_approval=True,
                    )
                sync_storage.close()
            self.assertEqual(
                (state / "v3/instance/local-workspace/LOCAL_NOTES.md").read_text(encoding="utf-8"),
                "keep\n",
            )

            old_restore = Path(folder) / "restore-old"
            restore_backup(Path(str(report.backup)), old_restore)
            self.assertTrue((old_restore / "workspace/mail_agent/data/mail_agent.sqlite3").is_file())
            self.assertFalse((old_restore / "workspace/AGENTS.md").exists())

            archive, _ = backup_state(state, workspace)
            new_restore = Path(folder) / "restore-new"
            restore_backup(archive, new_restore)
            self.assertTrue((new_restore / ".container-layout.json").is_file())
            self.assertEqual(
                sqlite3.connect(new_restore / "v3/shared/core/assistant.sqlite3")
                .execute("PRAGMA quick_check")
                .fetchone()[0],
                "ok",
            )
            self.assertEqual(
                sqlite3.connect(new_restore / "v3/domains/knowledge/knowledge.sqlite3")
                .execute("PRAGMA quick_check")
                .fetchone()[0],
                "ok",
            )

    def test_preflight_full_or_read_only_disk_changes_nothing(self) -> None:
        Usage = namedtuple("Usage", "total used free")
        for access, free, expected in (
            (False, 10_000_000, "nicht beschreibbar"),
            (True, 0, "Zu wenig freier Speicher"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                state, workspace = self._state(folder)
                with (
                    patch("personal_assistant.runtime_layout.os.access", return_value=access),
                    patch(
                        "personal_assistant.runtime_layout.shutil.disk_usage",
                        return_value=Usage(10_000_000, 10_000_000 - free, free),
                    ),
                    self.assertRaisesRegex(RuntimeError, expected),
                ):
                    migrate_layout(ROOT, state, workspace)
                self.assertFalse((state / "v3").exists())
                self.assertFalse((state / ".layout-migrations/backups").exists())

    def test_database_pruning_compacts_via_atomic_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "assistant.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE retained(value TEXT)")
            connection.execute("CREATE TABLE discarded(value BLOB)")
            connection.executemany(
                "INSERT INTO discarded(value) VALUES(?)",
                [(b"x" * 4096,) for _ in range(256)],
            )
            connection.execute("INSERT INTO retained(value) VALUES('keep')")
            connection.commit()
            connection.close()
            before = database.stat().st_size
            os.chmod(database, 0o600)

            _prune_assistant_database(database, ("discarded",))

            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute("SELECT value FROM retained").fetchone()[0],
                "keep",
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discarded'"
                ).fetchone()
            )
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            connection.close()
            self.assertLess(database.stat().st_size, before)
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(database.parent.glob(".*.vacuum-*")), [])

    def test_failed_layout_build_removes_only_staging_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._state(folder)
            stale = state / ".layout-migrations/staging/v3-stale-fixture"
            stale.mkdir(parents=True)
            (stale / "partial").write_text("discard\n", encoding="utf-8")
            with (
                patch(
                    "personal_assistant.runtime_layout._prune_assistant_database",
                    side_effect=RuntimeError("injected compaction failure"),
                ),
                self.assertRaisesRegex(RuntimeError, "injected compaction failure"),
            ):
                migrate_layout(ROOT, state, workspace)

            self.assertFalse((state / "v3").exists())
            self.assertEqual(list((state / ".layout-migrations/staging").iterdir()), [])
            source = workspace / "personal_assistant/data/assistant.sqlite3"
            connection = sqlite3.connect(source)
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM action_plans").fetchone()[0], 1)
            connection.close()

    def test_actionplan_idempotency_survives_real_multiprocess_contention(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "assistant.sqlite3"
            AssistantStorage(database).close()
            ready = multiprocessing.Event()
            output: multiprocessing.Queue = multiprocessing.Queue()
            processes = [
                multiprocessing.Process(target=_create_action, args=(str(database), ready, output))
                for _ in range(8)
            ]
            for process in processes:
                process.start()
            ready.set()
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            ids = [output.get(timeout=2) for _ in processes]
            self.assertEqual(len(set(ids)), 1)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM action_plans").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            connection.close()

    def test_all_business_workers_share_scheduler_without_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "scheduler.sqlite3"
            AdaptiveWorkScheduler(database, arbitration_seconds=0).close()
            ready = multiprocessing.Event()
            processes = [
                multiprocessing.Process(target=_scheduler_enqueue, args=(str(database), job, ready))
                for job in ("mail", "sync", "portfolio", "monitor")
            ]
            for process in processes:
                process.start()
            ready.set()
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_queue").fetchone()[0], 4)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            connection.close()

    def test_sigkill_rolls_back_uncommitted_wal_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder) / "crash.sqlite3")
            ready = multiprocessing.Event()
            process = multiprocessing.Process(target=_uncommitted_write, args=(database, ready))
            process.start()
            self.assertTrue(ready.wait(10))
            os.kill(process.pid, signal.SIGKILL)
            process.join(10)
            self.assertEqual(process.exitcode, -signal.SIGKILL)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM crash_fixture").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            connection.close()

    def test_rendered_compose_matches_machine_readable_mount_contract(self) -> None:
        rendered = subprocess.run(
            [
                "docker", "compose", "--profile", "tools", "--env-file", "docker/deployment.env.example",
                "-f", "compose.yaml", "config", "--format", "json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        compose = json.loads(rendered.stdout)
        contract = json.loads(
            (ROOT / "docs/architecture/state-access.json").read_text(encoding="utf-8")
        )
        roots = contract["roots"]
        for role, expected in contract["roles"].items():
            if role == "clamav-update":
                continue
            volumes = compose["services"][role].get("volumes", [])
            if role == "layout-init":
                state_mounts = [
                    item for item in volumes
                    if item["target"] == "/var/lib/openclaw-state"
                ]
                self.assertEqual(len(state_mounts), 1)
                continue
            actual = {}
            for volume in volumes:
                target = volume["target"]
                matching = [name for name, root in roots.items() if target == root]
                if matching:
                    actual[matching[0]] = "ro" if volume.get("read_only") else "rw"
            self.assertEqual(actual, expected, role)
        self.assertNotIn("portfolio", contract["roles"]["mail-worker"])
        self.assertNotIn("knowledge", contract["roles"]["mail-worker"])
        self.assertNotIn("mail", contract["roles"]["portfolio-worker"])

    def test_trace_parser_detects_write_outside_role_contract(self) -> None:
        script = ROOT / "scripts/audit-state-access.py"
        spec = importlib.util.spec_from_file_location("audit_state_access", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        contract = json.loads(
            (ROOT / "docs/architecture/state-access.json").read_text(encoding="utf-8")
        )
        trace = 'openat(AT_FDCWD, "/var/lib/openclaw/portfolio/portfolio.sqlite3", O_RDWR) = 3\n'
        violations = module.evaluate("mail-worker", module.parse_trace(trace), contract)
        self.assertEqual(violations[0]["root"], "portfolio")


if __name__ == "__main__":
    unittest.main()
