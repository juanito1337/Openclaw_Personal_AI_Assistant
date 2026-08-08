from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from personal_assistant.immutable_plugins import (
    load_contract,
    synchronize_installed_plugin_index,
)


class ImmutablePluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.contract = load_contract(self.root / "docker/openclaw-plugins/contract.json")

    @staticmethod
    def database(path: Path, records: dict[str, object]) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE installed_plugin_index ("
            "index_key TEXT PRIMARY KEY, install_records_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO installed_plugin_index VALUES (?, ?)",
            ("default", json.dumps(records)),
        )
        connection.commit()
        connection.close()

    def test_synchronizes_legacy_records_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "openclaw.sqlite"
            self.database(
                database,
                {
                    "brave": {
                        "resolvedName": "@openclaw/brave-plugin",
                        "resolvedVersion": "2026.6.11",
                        "installPath": "/home/user/.openclaw/npm/brave",
                    },
                    "signal": {
                        "resolvedName": "@openclaw/signal",
                        "resolvedVersion": "2026.6.11",
                        "installPath": "/home/user/.openclaw/npm/signal",
                    },
                },
            )

            first = synchronize_installed_plugin_index(database, self.contract)
            self.assertEqual(first["registry_rows_changed"], 1)
            self.assertEqual(first["managed_records_changed"], 2)
            connection = sqlite3.connect(database)
            raw = connection.execute("SELECT install_records_json FROM installed_plugin_index").fetchone()
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
            connection.close()
            self.assertIsNotNone(raw)
            assert raw is not None
            records = json.loads(raw[0])
            for plugin_id, contract in self.contract.items():
                self.assertEqual(records[plugin_id]["installPath"], contract["path"])
                self.assertEqual(records[plugin_id]["resolvedVersion"], contract["version"])
                self.assertEqual(records[plugin_id]["integrity"], contract["integrity"])
                self.assertEqual(records[plugin_id]["shasum"], contract["shasum"])

            modification_time = database.stat().st_mtime_ns
            second = synchronize_installed_plugin_index(database, self.contract)
            self.assertEqual(second["registry_rows_changed"], 0)
            self.assertEqual(second["managed_records_changed"], 0)
            self.assertEqual(database.stat().st_mtime_ns, modification_time)

    def test_unknown_managed_plugin_fails_without_database_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "openclaw.sqlite"
            records = {
                "calendar": {
                    "resolvedName": "@third-party/calendar",
                    "installPath": "/home/user/.openclaw/npm/calendar",
                }
            }
            self.database(database, records)

            with self.assertRaisesRegex(RuntimeError, "nicht im immutable Imagevertrag"):
                synchronize_installed_plugin_index(database, self.contract)

            connection = sqlite3.connect(database)
            raw = connection.execute("SELECT install_records_json FROM installed_plugin_index").fetchone()
            connection.close()
            self.assertIsNotNone(raw)
            assert raw is not None
            self.assertEqual(json.loads(raw[0]), records)


if __name__ == "__main__":
    unittest.main()
