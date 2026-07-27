from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mail_agent.config import load_config
from mail_agent.config_migrate_r25 import migrate_mail_config


class R25ConfigMigrationTests(unittest.TestCase):
    def test_known_r24_values_are_migrated_and_custom_values_preserved(self) -> None:
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        text = source.read_text(encoding="utf-8")
        text = text.replace("timeout_seconds = 600", "timeout_seconds = 300")
        text = text.replace("batch_timeout_seconds = 300", "batch_timeout_seconds = 180")
        text = text.replace("batch_retry_timeout_seconds = 300", "batch_retry_timeout_seconds = 120")
        text = text.replace("num_ctx = 16384", "num_ctx = 0")
        text = text.replace('keep_alive = "1h"', 'keep_alive = "30m"')
        text = text.replace("parallel_requests = 1\n", "")
        text = text.replace("parallel_requests = 2\n", "")
        text = text.replace("background_burst = false\n", "")
        text = text.replace("background_burst = true\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = text.replace("mail_agent/data/", str(root / "data") + "/")
            text = text.replace('rules_file = "mail_agent/rules.toml"', f'rules_file = "{root / "rules.toml"}"')
            text = text.replace('log_file = "mail_agent/data/mail_agent.log"', f'log_file = "{root / "mail.log"}"')
            config_path = root / "config.toml"
            config_path.write_text(text, encoding="utf-8")
            (root / "rules.toml").write_text(
                "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
                "[important]\naddresses=[]\ndomains=[]\n"
                "[routine]\naddresses=[]\ndomains=[]\n",
                encoding="utf-8",
            )
            result = migrate_mail_config(config_path)
            self.assertTrue(result["changed"])
            config = load_config(config_path)
            self.assertEqual(config.ollama.timeout_seconds, 600)
            self.assertEqual(config.ollama.batch_timeout_seconds, 300)
            self.assertEqual(config.ollama.batch_retry_timeout_seconds, 180)
            self.assertEqual(config.ollama.num_ctx, 16384)
            self.assertEqual(config.ollama.keep_alive, "1h")
            self.assertEqual(config.ollama.parallel_requests, 2)
            self.assertTrue(config.ollama.background_burst)

    def test_custom_tuning_is_not_overwritten_and_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "[ollama]\n"
                "base_url = \"http://127.0.0.1:11435\"\n"
                "model = \"gemma4:31b\"\n"
                "timeout_seconds = 900\n"
                "batch_timeout_seconds = 420\n"
                "batch_retry_timeout_seconds = 240\n"
                "num_ctx = 32768\n"
                "keep_alive = \"2h\"\n"
                "parallel_requests = 1\n"
                "background_burst = false\n"
                "\n[runtime]\ndatabase = \"mail_agent/data/test.sqlite3\"\n",
                encoding="utf-8",
            )
            first = migrate_mail_config(path)
            second = migrate_mail_config(path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertIn("timeout_seconds = 900", text)
            self.assertIn("num_ctx = 32768", text)
            self.assertIn('keep_alive = "2h"', text)
            self.assertIn("parallel_requests = 1", text)
            self.assertIn("background_burst = false", text)


if __name__ == "__main__":
    unittest.main()
