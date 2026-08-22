from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant.cli import parser
from personal_assistant.models import Resource
from personal_assistant.registry import ResourceRegistry
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

    def activate(self) -> dict[str, object]:
        with patch("personal_assistant.tool_setup.WORKSPACE_ROOT", self.root):
            return configure_standard_operations_tools(
                approve_permissions=True,
                path=self.tools,
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
