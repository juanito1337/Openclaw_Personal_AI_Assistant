from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant.cli import parser
from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection
from personal_assistant.models import Resource
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_settings import load_tool_settings
from personal_assistant.tool_setup import configure_standard_operations_tools


class StandardOperationsProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_dir = self.root / "personal_assistant"
        self.config_dir.mkdir()
        self.tools = self.config_dir / "tools.toml"
        self.tools.write_text(
            """
[mail]
enabled = true

[mail.move]
enabled = false
resource_id = "mail-agent"

[nextcloud.workspace]
enabled = true
resource_id = "nextcloud-files-main"
allow_mkdir = false
allow_upload = false
allow_write_text = false
allow_move = false

[nextcloud.calendar]
enabled = true
resource_id = "calendar-main"
allow_create = true
allow_list = true
allow_update = false

[nextcloud.tasks]
enabled = true
resource_id = "tasks-main"
allow_create = true
allow_list = true
allow_update = false

[nextcloud.contacts]
enabled = true
resource_id = "contacts-main"
allow_list = true
allow_create = false
allow_update = false

[nextcloud.deck_orders]
enabled = false
""".strip()
            + "\n",
            encoding="utf-8",
        )
        self.registry = ResourceRegistry(self.config_dir / "resources.toml")
        self.registry.write(
            [
                Resource(
                    id="mail-agent",
                    kind="email-service",
                    connector="mail-agent",
                    permissions=("read", "move", "forward"),
                ),
                Resource(
                    id="nextcloud-files-main",
                    kind="file-root",
                    connector="nextcloud",
                    permissions=("read", "create", "move"),
                    metadata={"allowed_roots": ["Assistent"]},
                ),
                Resource(
                    id="calendar-main",
                    kind="calendar",
                    connector="nextcloud",
                    permissions=("read", "create", "update"),
                    metadata={"components": ["VEVENT"]},
                ),
                Resource(
                    id="tasks-main",
                    kind="calendar",
                    connector="nextcloud",
                    permissions=("read", "create", "update"),
                    metadata={"components": ["VTODO"]},
                ),
                Resource(
                    id="contacts-main",
                    kind="addressbook",
                    connector="nextcloud",
                    permissions=("read", "create", "update"),
                ),
            ]
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def activate(
        self, *, verified_resources: dict[str, Resource] | None = None
    ) -> dict[str, object]:
        with patch("personal_assistant.tool_setup.WORKSPACE_ROOT", self.root):
            return configure_standard_operations_tools(
                approve_permissions=True,
                path=self.tools,
                verified_resources=verified_resources,
            )

    def test_profile_activates_all_configured_normal_operations_once(self) -> None:
        registry_before = (self.config_dir / "resources.toml").read_bytes()

        result = self.activate()

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "standard-operations")
        self.assertEqual(
            result["activated"],
            ["mail", "workspace", "calendar", "tasks", "contacts"],
        )
        capabilities = result["capabilities"]
        self.assertTrue(capabilities["mail_move"])
        for domain in ("calendar", "tasks", "contacts"):
            self.assertTrue(capabilities[domain]["create"])
            self.assertTrue(capabilities[domain]["list"])
            self.assertTrue(capabilities[domain]["update"])
        self.assertTrue(capabilities["workspace"]["move"])
        self.assertFalse(result["permissions_expanded_in_registry"])
        self.assertFalse(result["external_data_changed"])
        self.assertTrue(result["concrete_write_approval_still_required"])
        self.assertEqual(result["protections"]["delete"], "denied")
        self.assertEqual(
            (self.config_dir / "resources.toml").read_bytes(),
            registry_before,
        )
        self.assertTrue(Path(str(result["backup"])).is_file())

    def test_profile_is_idempotent(self) -> None:
        first = self.activate()
        second = self.activate()

        self.assertTrue(first["backup"])
        self.assertEqual(second["backup"], "")
        self.assertEqual(second["activated"], first["activated"])

    def test_profile_fails_atomically_when_registered_permission_is_missing(self) -> None:
        resource = self.registry.get("tasks-main")
        self.registry.upsert(
            Resource(
                id=resource.id,
                kind=resource.kind,
                connector=resource.connector,
                permissions=("read", "create"),
                metadata=resource.metadata,
            )
        )
        tools_before = self.tools.read_bytes()

        with (
            patch("personal_assistant.tool_setup.WORKSPACE_ROOT", self.root),
            self.assertRaisesRegex(PermissionError, "tasks:.*update"),
        ):
            configure_standard_operations_tools(
                approve_permissions=True,
                path=self.tools,
            )

        self.assertEqual(self.tools.read_bytes(), tools_before)

    def test_profile_registers_only_exact_preverified_missing_permissions(self) -> None:
        workspace = self.registry.get("nextcloud-files-main")
        tasks = self.registry.get("tasks-main")
        self.registry.upsert(
            Resource(
                id=workspace.id,
                kind=workspace.kind,
                connector=workspace.connector,
                permissions=("read", "create"),
                metadata=workspace.metadata,
            )
        )
        self.registry.upsert(
            Resource(
                id=tasks.id,
                kind=tasks.kind,
                connector=tasks.connector,
                permissions=("read", "create"),
                metadata=tasks.metadata,
            )
        )
        result = self.activate(
            verified_resources={
                workspace.id: Resource(
                    id=workspace.id,
                    kind=workspace.kind,
                    connector=workspace.connector,
                    permissions=("read", "create", "move"),
                    metadata={**workspace.metadata, "discovery_source": "live-webdav-depth-0"},
                ),
                tasks.id: Resource(
                    id=tasks.id,
                    kind=tasks.kind,
                    connector=tasks.connector,
                    permissions=("read", "create", "update"),
                    metadata={**tasks.metadata, "discovery_source": "live-caldav"},
                ),
            }
        )

        self.assertTrue(result["permissions_expanded_in_registry"])
        self.assertEqual(
            result["registry_permission_expansions"],
            {"nextcloud-files-main": ["move"], "tasks-main": ["update"]},
        )
        refreshed = ResourceRegistry(self.config_dir / "resources.toml")
        self.assertIn("move", refreshed.get("nextcloud-files-main").permissions)
        self.assertIn("update", refreshed.get("tasks-main").permissions)

    def test_service_uses_live_dav_evidence_before_registering_permissions(self) -> None:
        workspace = self.registry.get("nextcloud-files-main")
        tasks = self.registry.get("tasks-main")
        self.registry.upsert(
            Resource(
                id=workspace.id,
                kind=workspace.kind,
                connector=workspace.connector,
                permissions=("read", "create"),
                metadata=workspace.metadata,
            )
        )
        self.registry.upsert(
            Resource(
                id=tasks.id,
                kind=tasks.kind,
                connector=tasks.connector,
                permissions=("read", "create"),
                metadata=tasks.metadata,
            )
        )

        class Discovery:
            def file_collection_capabilities(self, path: str) -> dict[str, object]:
                return {
                    "ok": True,
                    "read_only": True,
                    "path": path,
                    "href": "/remote.php/dav/files/jan/Assistent/",
                    "privileges": ["{DAV:}all"],
                    "can_read": True,
                    "can_create": True,
                    "can_update": True,
                    "can_move": True,
                }

            def calendar_collections(self) -> list[DiscoveredCollection]:
                return [
                    DiscoveredCollection(
                        kind="calendar",
                        href="/cal/tasks/",
                        name="Arbeit",
                        resource_id="tasks-main",
                        components=("VTODO",),
                        privileges=("{DAV:}all",),
                        can_read=True,
                        can_create=True,
                        can_update=True,
                    )
                ]

            def addressbooks(self) -> list[DiscoveredCollection]:
                return []

        assistant = PersonalAssistant.__new__(PersonalAssistant)
        assistant.tool_settings = load_tool_settings(self.tools)
        assistant.registry = ResourceRegistry(self.config_dir / "resources.toml")
        assistant.nextcloud_discovery = Discovery()

        result = assistant.standard_operations_configure(approve_permissions=True)

        self.assertTrue(result["ok"])
        self.assertEqual(
            set(result["verification"]),
            {"workspace", "tasks"},
        )
        self.assertTrue(result["verification"]["workspace"]["read_only"])
        self.assertFalse(result["resource_selection_changed"])

    def test_service_rejects_unconfirmed_live_update_without_partial_write(self) -> None:
        tasks = self.registry.get("tasks-main")
        self.registry.upsert(
            Resource(
                id=tasks.id,
                kind=tasks.kind,
                connector=tasks.connector,
                permissions=("read", "create"),
                metadata=tasks.metadata,
            )
        )
        registry_before = (self.config_dir / "resources.toml").read_bytes()
        tools_before = self.tools.read_bytes()

        class Discovery:
            def calendar_collections(self) -> list[DiscoveredCollection]:
                return [
                    DiscoveredCollection(
                        kind="calendar",
                        href="/cal/tasks/",
                        name="Arbeit",
                        resource_id="tasks-main",
                        components=("VTODO",),
                        privileges=("{DAV:}read",),
                        can_read=True,
                        can_create=True,
                        can_update=False,
                    )
                ]

            def addressbooks(self) -> list[DiscoveredCollection]:
                return []

        assistant = PersonalAssistant.__new__(PersonalAssistant)
        assistant.tool_settings = load_tool_settings(self.tools)
        assistant.registry = ResourceRegistry(self.config_dir / "resources.toml")
        assistant.nextcloud_discovery = Discovery()

        with self.assertRaisesRegex(PermissionError, "tasks:.*update"):
            assistant.standard_operations_configure(approve_permissions=True)

        self.assertEqual((self.config_dir / "resources.toml").read_bytes(), registry_before)
        self.assertEqual(self.tools.read_bytes(), tools_before)

    def test_profile_requires_one_explicit_approval(self) -> None:
        with (
            patch("personal_assistant.tool_setup.WORKSPACE_ROOT", self.root),
            self.assertRaisesRegex(PermissionError, "--yes"),
        ):
            configure_standard_operations_tools(path=self.tools)

    def test_gateway_cannot_change_the_profile_or_mounts(self) -> None:
        missing = self.root / "missing-tools.toml"
        with (
            patch.dict(
                os.environ,
                {"OPENCLAW_RUNTIME": "container", "OPENCLAW_ROLE": "gateway"},
                clear=False,
            ),
            self.assertRaisesRegex(PermissionError, "agent-cli-Rolle"),
        ):
            configure_standard_operations_tools(
                approve_permissions=True,
                path=missing,
            )
        self.assertFalse(missing.exists())

    def test_cli_exposes_one_standard_profile_command(self) -> None:
        args = parser().parse_args(["setup", "standard-operations", "--yes"])
        self.assertEqual(args.command, "setup")
        self.assertEqual(args.setup_command, "standard-operations")
        self.assertTrue(args.yes)


if __name__ == "__main__":
    unittest.main()
