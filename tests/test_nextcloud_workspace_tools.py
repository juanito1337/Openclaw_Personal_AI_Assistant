from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from personal_assistant import cli as assistant_cli
from personal_assistant.actions import ActionService
from personal_assistant.cli_handlers.invoices import handle as handle_invoice_command
from personal_assistant.cli_handlers.nextcloud import handle as handle_nextcloud_command
from personal_assistant.connectors.nextcloud.files import NextcloudFiles, RemoteFile
from personal_assistant.models import ActionPlan, Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import (
    InvoiceToolSettings,
    MailToolSettings,
    NextcloudToolSettings,
    NextcloudWorkspaceToolSettings,
    ToolSettings,
)


class FakeActions:
    def __init__(self) -> None:
        self.plans: list[tuple[str, str, dict, str]] = []
        self.executed: list[str] = []

    def plan(self, action_type, resource_id, payload, idempotency_key=""):
        self.plans.append((action_type, resource_id, payload, idempotency_key))
        return ActionPlan(
            id="action-1",
            idempotency_key=idempotency_key,
            action_type=action_type,
            resource_id=resource_id,
            payload=payload,
            status="approved",
            requires_approval=False,
            created_at="now",
            updated_at="now",
            error="",
        )

    def execute_workspace(self, action_id):
        self.executed.append(action_id)
        plan = self.plans[-1]
        return ActionPlan(
            id=action_id,
            idempotency_key=plan[3],
            action_type=plan[0],
            resource_id=plan[1],
            payload=plan[2],
            status="completed",
            requires_approval=False,
            created_at="now",
            updated_at="now",
            error="",
        ), False


class FakeDavClient:
    def __init__(self) -> None:
        self.username = "openclaw"
        self.calls: list[tuple[str, str, dict]] = []

    @staticmethod
    def validate_url() -> str:
        return "https://cloud.example.test"

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return SimpleNamespace(status=201, reason="Created", data=b"payload")


class NextcloudWorkspaceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.outbox = self.root / "outbox"
        self.outbox.mkdir()
        self.settings = ToolSettings(
            path=self.root / "tools.toml",
            mail=MailToolSettings(),
            nextcloud=NextcloudToolSettings(
                workspace=NextcloudWorkspaceToolSettings(
                    enabled=True,
                    resource_id="nextcloud-files-main",
                    root="Assistent",
                    outbox=self.outbox,
                    allow_mkdir=True,
                    allow_upload=True,
                    allow_write_text=True,
                    allow_move=True,
                )
            ),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_workspace_tools_are_visible_to_agent(self) -> None:
        ids = {item.id for item in build_tool_registry(self.settings)}
        self.assertIn("nextcloud.workspace.mkdir", ids)
        self.assertIn("nextcloud.workspace.upload", ids)
        self.assertIn("nextcloud.workspace.write-text", ids)
        self.assertIn("nextcloud.workspace.move", ids)
        self.assertIn("nextcloud.workspace.configure", ids)

    def test_sync_command_propagates_degraded_result_as_exit_one(self) -> None:
        emitted: list[dict[str, object]] = []
        assistant = SimpleNamespace(sync_nextcloud=lambda: {"ok": False, "errors": 1})

        exit_code = handle_nextcloud_command(
            SimpleNamespace(nextcloud_command="sync"),
            assistant,
            emitted.append,
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(emitted[0]["ok"])

    def test_nextcloud_list_command_uses_native_service_without_local_mount(self) -> None:
        calls: list[tuple[str, int]] = []
        emitted: list[dict[str, object]] = []
        assistant = SimpleNamespace(
            list_nextcloud_files=lambda path, *, max_depth: (
                calls.append((path, max_depth))
                or {
                    "ok": True,
                    "connector": "native-nextcloud-webdav",
                    "root": path,
                    "complete": True,
                    "results_may_be_truncated": False,
                    "items": [],
                }
            )
        )

        exit_code = handle_nextcloud_command(
            SimpleNamespace(
                nextcloud_command="list",
                path="Assistent",
                max_depth=3,
            ),
            assistant,
            emitted.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [("Assistent", 3)])
        self.assertEqual(emitted[0]["connector"], "native-nextcloud-webdav")
        self.assertTrue(emitted[0]["complete"])
        self.assertFalse(emitted[0]["results_may_be_truncated"])

    def test_nextcloud_listing_marks_unexpanded_depth_as_incomplete(self) -> None:
        registry = ResourceRegistry(self.root / "resources.toml")
        registry.write(
            [
                Resource(
                    id="nextcloud-files-main",
                    kind="file-root",
                    connector="nextcloud",
                    enabled=True,
                    permissions=("read",),
                    metadata={"allowed_roots": ["Assistent"]},
                )
            ]
        )
        tree = {
            "Assistent": [
                RemoteFile(
                    href="/Assistent/Projekte/",
                    path="Assistent/Projekte",
                    name="Projekte",
                    is_collection=True,
                    content_type="",
                    size=0,
                    etag="folder-1",
                    modified_at="",
                )
            ],
            "Assistent/Projekte": [
                RemoteFile(
                    href="/Assistent/Projekte/Alpha/",
                    path="Assistent/Projekte/Alpha",
                    name="Alpha",
                    is_collection=True,
                    content_type="",
                    size=0,
                    etag="folder-2",
                    modified_at="",
                )
            ],
        }
        assistant = object.__new__(PersonalAssistant)
        assistant.nextcloud_files = SimpleNamespace(
            clean_path=NextcloudFiles.clean_path,
            list_folder=lambda path: tree[path],
        )
        assistant.registry = registry
        assistant.config = SimpleNamespace(
            nextcloud=SimpleNamespace(allowed_file_roots=("Assistent",))
        )

        result = assistant.list_nextcloud_files("Assistent", max_depth=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["connector"], "native-nextcloud-webdav")
        self.assertEqual(result["count"], 2)
        self.assertFalse(result["complete"])
        self.assertTrue(result["results_may_be_truncated"])
        self.assertEqual(result["unexpanded_folder_count"], 1)

    def test_invoice_file_tool_uses_configured_native_nextcloud_root(self) -> None:
        configured_root = "Mail-Agent/Rechnungen"
        settings = ToolSettings(
            path=self.root / "invoice-tools.toml",
            mail=MailToolSettings(
                invoices=InvoiceToolSettings(
                    enabled=True,
                    resource_id="nextcloud-invoices",
                    folder=configured_root,
                )
            ),
        )
        calls: list[tuple[str, int, str]] = []
        assistant = object.__new__(PersonalAssistant)
        assistant.tool_settings = settings
        assistant.list_nextcloud_files = lambda path, *, max_depth, resource_id: (
            calls.append((path, max_depth, resource_id))
            or {
                "root": path,
                "max_depth": max_depth,
                "items": [
                    {"path": f"{path}/2026/b.pdf", "kind": "file"},
                    {"path": f"{path}/2026/a.pdf", "kind": "file"},
                ],
            }
        )
        emitted: list[dict[str, object]] = []

        exit_code = handle_invoice_command(
            SimpleNamespace(invoices_command="files", limit=1),
            assistant,
            emitted.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [(configured_root, 3, "nextcloud-invoices")])
        self.assertEqual(emitted[0]["connector"], "native-nextcloud-webdav")
        self.assertEqual(emitted[0]["root"], configured_root)
        self.assertEqual(emitted[0]["returned"], 1)
        self.assertFalse(emitted[0]["complete"])
        self.assertTrue(emitted[0]["results_may_be_truncated"])
        self.assertEqual(emitted[0]["items"][0]["path"], f"{configured_root}/2026/a.pdf")

        tools = {tool.id: tool for tool in build_tool_registry(settings)}
        self.assertEqual(
            tools["assistant.invoices.files"].command,
            "./scripts/assistant.sh invoices files --limit 100",
        )
        self.assertEqual(tools["assistant.invoices.files"].mode, "read")

    def test_invoice_files_cli_uses_registered_handler_not_legacy_subprocess(self) -> None:
        config_path = self.root / "assistant.toml"
        config_path.touch()
        payload = {
            "ok": True,
            "connector": "native-nextcloud-webdav",
            "root": "Assistent/Rechnungen",
            "complete": True,
            "results_may_be_truncated": False,
            "items": [],
        }
        assistant = SimpleNamespace(
            list_invoice_files=lambda *, limit: {**payload, "limit": limit},
            close=lambda: None,
        )
        output = io.StringIO()

        with (
            patch.object(assistant_cli, "_load_secrets"),
            patch.object(
                assistant_cli,
                "load_config",
                return_value=SimpleNamespace(runtime=SimpleNamespace(log_file=self.root / "log")),
            ),
            patch.object(assistant_cli, "_logging"),
            patch.object(assistant_cli, "_record_interactive_activity"),
            patch.object(assistant_cli, "create_personal_assistant", return_value=assistant),
            patch.object(assistant_cli, "run_invoice_external") as legacy,
            redirect_stdout(output),
        ):
            exit_code = assistant_cli.main(
                ["--config", str(config_path), "invoices", "files", "--limit", "25"]
            )

        self.assertEqual(exit_code, 0)
        legacy.assert_not_called()
        self.assertIn('"connector": "native-nextcloud-webdav"', output.getvalue())
        self.assertIn('"limit": 25', output.getvalue())

    def test_policy_allows_only_inside_workspace_and_no_overwrite(self) -> None:
        registry_path = self.root / "resources.toml"
        registry = ResourceRegistry(registry_path)
        registry.write([
            Resource(
                id="nextcloud-files-main",
                kind="file-root",
                connector="nextcloud",
                enabled=True,
                permissions=("read", "create", "organize"),
                metadata={"allowed_roots": ["Assistent"]},
            )
        ])
        policy = PolicyEngine(self.root / "policies.toml", registry)
        self.assertTrue(
            policy.decide(
                "nextcloud-files-main", "files.mkdir", {"path": "Assistent/Projekte"}
            ).allowed
        )
        self.assertFalse(policy.decide("nextcloud-files-main", "files.mkdir", {"path": "Privat"}).allowed)
        self.assertTrue(policy.decide(
            "nextcloud-files-main",
            "files.move",
            {"source": "Assistent/a.txt", "destination": "Assistent/Archiv/a.txt", "overwrite": False},
        ).allowed)
        self.assertFalse(policy.decide(
            "nextcloud-files-main",
            "files.move",
            {"source": "Assistent/a.txt", "destination": "Privat/a.txt", "overwrite": False},
        ).allowed)
        self.assertFalse(policy.decide(
            "nextcloud-files-main",
            "files.create",
            {"path": "Assistent/a.txt", "overwrite": True},
        ).allowed)
        managed = {
            "path": "Assistent/Rechnungen/2026/Rechnungen_2026.csv",
            "overwrite": True,
            "managed_invoice_register": True,
            "year": 2026,
            "content_type": "text/csv; charset=utf-8",
            "sha256": "a" * 64,
        }
        self.assertTrue(policy.decide("nextcloud-files-main", "files.create", managed).allowed)
        self.assertFalse(policy.decide(
            "nextcloud-files-main", "files.create",
            {**managed, "path": "Assistent/Rechnungen/2026/anderer-name.csv"},
        ).allowed)
        self.assertFalse(policy.decide(
            "nextcloud-files-main", "files.create",
            {**managed, "year": 2025},
        ).allowed)
        self.assertFalse(
            policy.decide(
                "nextcloud-files-main", "files.delete", {"path": "Assistent/a.txt"}
            ).allowed
        )

    def test_move_uses_webdav_no_overwrite(self) -> None:
        client = FakeDavClient()
        files = NextcloudFiles(SimpleNamespace(), client)
        files.ensure_folder = lambda path: None
        files.move_new("Assistent/a.txt", "Assistent/Archiv/a.txt")
        method, path, kwargs = client.calls[-1]
        self.assertEqual(method, "MOVE")
        self.assertIn("Assistent/a.txt", path)
        self.assertEqual(kwargs["headers"]["Overwrite"], "F")
        self.assertTrue(kwargs["headers"]["Destination"].endswith("/Assistent/Archiv/a.txt"))

    def test_download_can_pin_the_previously_listed_etag(self) -> None:
        client = FakeDavClient()
        files = NextcloudFiles(SimpleNamespace(), client)
        self.assertEqual(
            files.download("Assistent/Finanzen/Portfolio/depot.csv", expected_etag="etag-1"),
            b"payload",
        )
        method, _, kwargs = client.calls[-1]
        self.assertEqual(method, "GET")
        self.assertEqual(kwargs["headers"]["If-Match"], '"etag-1"')
        self.assertEqual(kwargs["expected"], {200, 412})

    def _assistant(self) -> PersonalAssistant:
        assistant = object.__new__(PersonalAssistant)
        assistant.tool_settings = self.settings
        assistant.nextcloud_files = SimpleNamespace(clean_path=NextcloudFiles.clean_path)
        assistant.actions = FakeActions()
        assistant.config = SimpleNamespace(search=SimpleNamespace(max_file_bytes=25_000_000))
        return assistant

    def test_mkdir_uses_actionplan(self) -> None:
        assistant = self._assistant()
        result = assistant.workspace_mkdir("Assistent/Projekte/Alpha")
        self.assertTrue(result["ok"])
        self.assertEqual(assistant.actions.plans[0][0], "files.mkdir")
        self.assertEqual(assistant.actions.plans[0][2]["path"], "Assistent/Projekte/Alpha")

    def test_upload_is_restricted_to_outbox(self) -> None:
        assistant = self._assistant()
        allowed = self.outbox / "bericht.txt"
        allowed.write_text("Bericht", encoding="utf-8")
        result = assistant.workspace_upload(allowed, "Assistent/Berichte/bericht.txt")
        self.assertTrue(result["ok"])
        outside = self.root / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        with self.assertRaises(PermissionError):
            assistant.workspace_upload(outside, "Assistent/secret.txt")

    def test_write_text_stages_and_cleans_payload(self) -> None:
        assistant = self._assistant()
        result = assistant.workspace_write_text("Assistent/Notizen/test.md", "Hallo")
        self.assertTrue(result["ok"])
        staging = Path(assistant.actions.plans[0][2]["local_path"])
        self.assertFalse(staging.exists())
        self.assertEqual(assistant.actions.plans[0][0], "files.create")

    def test_move_stays_inside_root(self) -> None:
        assistant = self._assistant()
        result = assistant.workspace_move("Assistent/a.txt", "Assistent/Archiv/a.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(assistant.actions.plans[0][0], "files.move")
        with self.assertRaises(PermissionError):
            assistant.workspace_move("Assistent/a.txt", "Ausserhalb/a.txt")


class ReconciliationFiles:
    def __init__(self, remote: dict[str, bytes] | None = None) -> None:
        self.remote = dict(remote or {})
        self.uploads: list[str] = []
        self.folders: set[str] = set()
        self.moves: list[tuple[str, str]] = []

    def exists(self, path: str) -> bool:
        return path in self.remote or path in self.folders

    def download(self, path: str) -> bytes:
        return self.remote[path]

    def ensure_folder(self, path: str) -> None:
        self.folders.add(path)

    def upload_new(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        if path in self.remote:
            raise RuntimeError("overwrite forbidden")
        self.remote[path] = data
        self.uploads.append(path)

    def move_new(self, source: str, destination: str) -> None:
        if destination in self.remote:
            raise RuntimeError("overwrite forbidden")
        self.remote[destination] = self.remote.pop(source)
        self.moves.append((source, destination))


class WorkspaceReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = AssistantStorage(self.root / "assistant.sqlite3")
        self.registry = ResourceRegistry(self.root / "resources.toml")
        self.registry.write([
            Resource(
                id="nextcloud-files-main",
                kind="file-root",
                connector="nextcloud",
                enabled=True,
                permissions=("read", "create", "organize"),
                metadata={"allowed_roots": ["Assistent"]},
            )
        ])
        self.policy = PolicyEngine(self.root / "policies.toml", self.registry)
        self.files = ReconciliationFiles()
        self.actions = ActionService(
            self.storage,
            self.registry,
            self.policy,
            SimpleNamespace(),
            self.files,
            SimpleNamespace(),
            SimpleNamespace(),
        )

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def _completed_create(self, remote: str, data: bytes) -> ActionPlan:
        local = self.root / "payload.bin"
        local.write_bytes(data)
        import hashlib
        plan = self.actions.plan(
            "files.create",
            "nextcloud-files-main",
            {
                "local_path": str(local),
                "path": remote,
                "content_type": "application/octet-stream",
                "overwrite": False,
                "sha256": hashlib.sha256(data).hexdigest(),
            },
            idempotency_key=f"test:{remote}",
        )
        return self.storage.update_action(plan.id, "completed")

    def test_missing_completed_file_is_recreated(self) -> None:
        plan = self._completed_create("Assistent/Test/readme.txt", b"hello")
        result, duplicate = self.actions.execute_workspace(plan.id)
        self.assertFalse(duplicate)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.files.remote["Assistent/Test/readme.txt"], b"hello")
        self.assertEqual(self.files.uploads, ["Assistent/Test/readme.txt"])

    def test_existing_matching_file_is_verified_duplicate(self) -> None:
        plan = self._completed_create("Assistent/Test/readme.txt", b"hello")
        self.files.remote["Assistent/Test/readme.txt"] = b"hello"
        result, duplicate = self.actions.execute_workspace(plan.id)
        self.assertTrue(duplicate)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.files.uploads, [])

    def test_existing_different_file_is_never_overwritten(self) -> None:
        plan = self._completed_create("Assistent/Test/readme.txt", b"hello")
        self.files.remote["Assistent/Test/readme.txt"] = b"different"
        result, duplicate = self.actions.execute_workspace(plan.id)
        self.assertFalse(duplicate)
        self.assertEqual(result.status, "failed")
        self.assertIn("anderem Inhalt", result.error)
        self.assertEqual(self.files.remote["Assistent/Test/readme.txt"], b"different")
        self.assertEqual(self.files.uploads, [])

    def test_missing_completed_folder_is_recreated(self) -> None:
        plan = self.actions.plan(
            "files.mkdir",
            "nextcloud-files-main",
            {"path": "Assistent/Test", "overwrite": False},
            idempotency_key="test:mkdir",
        )
        self.storage.update_action(plan.id, "completed")
        result, duplicate = self.actions.execute_workspace(plan.id)
        self.assertFalse(duplicate)
        self.assertEqual(result.status, "completed")
        self.assertIn("Assistent/Test", self.files.folders)


if __name__ == "__main__":
    unittest.main()
