from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_assistant.registry import ResourceRegistry  # noqa: E402

DUPLICATE_TOML = '''
[[resources]]
id = "mail-agent"
kind = "email-service"
connector = "mail-agent"
enabled = true
remote_id = "primary"
permissions = ["read"]

[[resources]]
id = "nextcloud-main"
kind = "nextcloud-instance"
connector = "nextcloud"
enabled = false
remote_id = "old"
permissions = ["read"]
name = "Alt"

[[resources]]
id = "nextcloud-main"
kind = "nextcloud-instance"
connector = "nextcloud"
enabled = true
remote_id = "new"
permissions = ["read"]
name = "Neu"
'''


class ResourceRegistryRepairTests(unittest.TestCase):
    def test_registry_tolerates_duplicate_and_keeps_last(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resources.toml"
            path.write_text(DUPLICATE_TOML, encoding="utf-8")
            registry = ResourceRegistry(path)
            self.assertEqual(registry.duplicate_ids, ["nextcloud-main"])
            self.assertTrue(registry.get("nextcloud-main").enabled)
            self.assertEqual(registry.get("nextcloud-main").remote_id, "new")
            self.assertEqual(registry.get("nextcloud-main").metadata["name"], "Neu")

    def test_standalone_repair_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resources.toml"
            path.write_text(DUPLICATE_TOML, encoding="utf-8")
            script = ROOT / "scripts" / "repair-resources.py"
            first = subprocess.run(
                [sys.executable, str(script), str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("Ressourcen: 3 -> 2", first.stdout)
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            ids = [item["id"] for item in data["resources"]]
            self.assertEqual(len(ids), len(set(ids)))
            current = next(item for item in data["resources"] if item["id"] == "nextcloud-main")
            self.assertTrue(current["enabled"])
            self.assertEqual(current["remote_id"], "new")
            before = path.read_bytes()
            second = subprocess.run(
                [sys.executable, str(script), str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("Keine doppelten IDs gefunden", second.stdout)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
