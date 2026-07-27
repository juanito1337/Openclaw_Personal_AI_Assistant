from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mail_agent.config_migrate_r26 import migrate_mail_config


class R26InvoiceConfigMigrationTests(unittest.TestCase):
    def test_register_becomes_nextcloud_only_and_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "[invoices]\n"
                "metadata_enabled = false\n"
                "register_enabled = false\n"
                "register_dir = \"mail_agent/data/invoice_register\"\n"
                "register_delimiter = \",\"\n"
                "\n[runtime]\n"
                "database = \"mail_agent/data/test.sqlite3\"\n",
                encoding="utf-8",
            )
            first = migrate_mail_config(path)
            second = migrate_mail_config(path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertIn("metadata_enabled = true", text)
            self.assertIn("register_enabled = true", text)
            self.assertIn('register_delimiter = ";"', text)
            self.assertNotIn("register_dir", text)

    def test_missing_required_keys_are_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[invoices]\nenabled = true\n\n[digest]\nenabled = false\n", encoding="utf-8")
            result = migrate_mail_config(path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(result["changed"])
            self.assertIn("metadata_enabled = true", text)
            self.assertIn("register_enabled = true", text)
            self.assertIn('register_delimiter = ";"', text)


if __name__ == "__main__":
    unittest.main()
