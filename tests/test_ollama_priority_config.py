from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from personal_assistant.ollama_priority_config import (
    find_model_overrides,
    normalize_base_url,
    normalize_gateway_base_url,
    normalize_model_overrides,
    read_mail_base_url,
    set_mail_base_url,
    set_model_overrides,
)


class OllamaPriorityConfigTests(unittest.TestCase):
    def test_normalize_accepts_root_and_rejects_subpath_or_credentials(self) -> None:
        self.assertEqual(normalize_base_url("HTTP://Example.COM:11434/"), "http://example.com:11434")
        with self.assertRaises(ValueError):
            normalize_base_url("http://example.com:11434/v1")
        with self.assertRaises(ValueError):
            normalize_base_url("http://user:secret@example.com:11434")

    def test_mail_config_rewrite_preserves_other_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                '[ollama]\nbase_url = "http://192.168.2.24:11434" # keep\n'
                'model = "gemma4:31b"\n\n[other]\nx=1\n',
                encoding="utf-8",
            )
            self.assertEqual(read_mail_base_url(path), "http://192.168.2.24:11434")
            set_mail_base_url(path, "http://127.0.0.1:11435")
            text = path.read_text(encoding="utf-8")
            self.assertIn('base_url = "http://127.0.0.1:11435" # keep', text)
            self.assertIn('model = "gemma4:31b"', text)
            self.assertEqual(read_mail_base_url(path), "http://127.0.0.1:11435")

    def test_model_overrides_are_only_rewritten_when_they_match_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "main/agent/models.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "ollama": {"baseUrl": "http://192.168.2.24:11434"}
                        }
                    }
                ),
                encoding="utf-8",
            )
            records = find_model_overrides(root)
            self.assertEqual(records[0]["base_url"], "http://192.168.2.24:11434")
            changed = set_model_overrides(root, "http://192.168.2.24:11434", "http://127.0.0.1:11435")
            self.assertEqual(changed, [str(path)])
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["providers"]["ollama"]["baseUrl"], "http://127.0.0.1:11435")
            # Reinstall is idempotent when the override already points to the proxy.
            self.assertEqual(
                set_model_overrides(root, "http://10.0.0.1:11434", "http://127.0.0.1:11435"),
                [],
            )
            payload["providers"]["ollama"]["baseUrl"] = "http://10.0.0.2:11434"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                set_model_overrides(root, "http://10.0.0.1:11434", "http://127.0.0.1:11435")

    def test_container_normalization_updates_gateway_and_overrides_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway = root / "openclaw.json"
            agents = root / "agents"
            model_path = agents / "main/agent/models.json"
            model_path.parent.mkdir(parents=True)
            gateway.write_text(
                json.dumps(
                    {
                        "gateway": {"mode": "local"},
                        "models": {
                            "providers": {
                                "ollama": {"baseUrl": "http://127.0.0.1:11435"}
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            model_path.write_text(
                json.dumps(
                    {"providers": {"ollama": {"baseUrl": "http://localhost:11435"}}}
                ),
                encoding="utf-8",
            )

            expected = "http://ollama-proxy:11435"
            self.assertTrue(normalize_gateway_base_url(gateway, expected))
            self.assertEqual(normalize_model_overrides(agents, expected), [str(model_path)])
            self.assertFalse(normalize_gateway_base_url(gateway, expected))
            self.assertEqual(normalize_model_overrides(agents, expected), [])
            payload = json.loads(gateway.read_text(encoding="utf-8"))
            self.assertEqual(payload["gateway"], {"mode": "local"})
            self.assertEqual(
                payload["models"]["providers"]["ollama"]["baseUrl"],
                expected,
            )

    def test_container_normalization_rejects_unrelated_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "openclaw.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "providers": {
                                "ollama": {"baseUrl": "http://other-host:11434"}
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            before = path.read_bytes()

            with self.assertRaisesRegex(ValueError, "Abweichende Ollama-URL"):
                normalize_gateway_base_url(path, "http://ollama-proxy:11435")

            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
