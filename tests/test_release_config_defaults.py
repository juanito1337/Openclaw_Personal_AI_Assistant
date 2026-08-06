from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant.cli import parser
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.tool_settings import load_tool_settings
from personal_assistant.tool_setup import configure_mail_move_tools


class ReleaseConfigDefaultsTests(unittest.TestCase):
    def test_release_tool_defaults_merge_with_instance_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            defaults = root / "tool-defaults.toml"
            overrides = root / "tools.toml"
            defaults.write_text(
                """
[mail.move]
enabled = false
resource_id = "mail-agent"
max_batch = 1
denied_destinations = ["trash", "agent/virusverdacht"]
denied_sources = ["agent/pruefen"]
""".strip()
                + "\n",
                encoding="utf-8",
            )
            overrides.write_text(
                """
[mail.move]
enabled = true
max_batch = 3
denied_destinations = ["Lokaler Papierkorb"]
denied_sources = ["Lokale Pruefung"]
""".strip()
                + "\n",
                encoding="utf-8",
            )

            settings = load_tool_settings(overrides, defaults_path=defaults)

            self.assertTrue(settings.mail.move.enabled)
            self.assertEqual(settings.mail.move.max_batch, 3)
            self.assertIn("trash", settings.mail.move.denied_destinations)
            self.assertIn("lokaler papierkorb", settings.mail.move.denied_destinations)
            self.assertIn("agent/pruefen", settings.mail.move.denied_sources)
            self.assertIn("lokale pruefung", settings.mail.move.denied_sources)

    def test_release_policy_cannot_be_removed_by_local_policy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            defaults = root / "policy-defaults.toml"
            local = root / "policies.toml"
            defaults.write_text(
                '[deny]\nactions = ["mail.delete"]\n[approval]\nactions = ["mail.send"]\n',
                encoding="utf-8",
            )
            local.write_text("[deny]\nactions = []\n[approval]\nactions = []\n", encoding="utf-8")
            registry = ResourceRegistry(root / "resources.toml")
            registry.resources["mail-agent"] = Resource(
                id="mail-agent",
                kind="email-service",
                connector="mail-agent",
                permissions=("read", "forward"),
            )

            engine = PolicyEngine(local, registry, defaults_path=defaults)

            self.assertTrue(engine.decide("mail-agent", "mail.send").requires_approval)
            self.assertFalse(engine.decide("mail-agent", "mail.delete").allowed)

    def test_container_points_to_image_owned_defaults(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn(
            "OPENCLAW_TOOL_DEFAULTS_CONFIG=/opt/openclaw-agent/personal_assistant/tool_defaults.toml",
            dockerfile,
        )
        self.assertIn(
            "OPENCLAW_POLICY_DEFAULTS_CONFIG: /opt/openclaw-agent/personal_assistant/policy_defaults.toml",
            compose,
        )
        draft = parser().parse_args(
            ["mail", "compose-draft", "--to", "a@example.test", "--subject", "S", "--body", "B"]
        )
        send = parser().parse_args(["mail", "compose-send", "--draft-id", "draft-1", "--yes"])
        self.assertEqual((draft.command, draft.mail_command), ("mail", "compose-draft"))
        self.assertEqual((send.command, send.mail_command, send.yes), ("mail", "compose-send", True))

    def test_direct_mail_setup_requires_and_records_forward_permission(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_dir = root / "personal_assistant"
            config_dir.mkdir()
            registry_path = config_dir / "resources.toml"
            registry = ResourceRegistry(registry_path)
            registry.upsert(
                Resource(
                    id="mail-agent",
                    kind="email-service",
                    connector="mail-agent",
                    permissions=("read", "move"),
                )
            )
            tools_path = config_dir / "tools.toml"

            with patch("personal_assistant.tool_setup.WORKSPACE_ROOT", root):
                with self.assertRaises(PermissionError):
                    configure_mail_move_tools(
                        approve_permissions=False,
                        path=tools_path,
                    )
                result = configure_mail_move_tools(
                    approve_permissions=True,
                    path=tools_path,
                )

            self.assertIn("forward", result["permissions"])
            self.assertIn("denied_sources", tools_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
