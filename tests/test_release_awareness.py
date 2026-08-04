from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from personal_assistant.cli import parser
from personal_assistant.release import release_report, verify_release
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings


class ReleaseAwarenessTests(unittest.TestCase):
    def test_installed_package_manifest_is_consistent(self) -> None:
        report = release_report(verify=True, include_history=True, limit=20)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["version"], "3.4.0-r27.2.5")
        self.assertEqual(report["history"][0]["version"], "3.4.0-r27.2.5")

    def test_history_since_r18_lists_only_newer_releases(self) -> None:
        report = release_report(include_history=True, since="3.4.0-r18", limit=22)
        versions = [item["version"] for item in report["history"]]
        self.assertEqual(versions[0], "3.4.0-r27.2.5")
        self.assertEqual(versions[1], "3.4.0-r27.2.4")
        self.assertEqual(versions[-1], "3.4.0-r22.4")
        self.assertEqual(len(versions), 22)
        self.assertTrue(report["since_found"])

    def test_document_mismatch_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "schema_version": 1,
                "version": "3.4.0-r20.2",
                "release": "r20.2",
                "title": "test",
                "history": [{"version": "3.4.0-r20.2", "changes": []}],
            }
            (root / "RELEASE.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "AGENTS.md").write_text("3.4.0-r20.2", encoding="utf-8")
            (root / "README.md").write_text("stale 3.4.0-r18", encoding="utf-8")
            (root / "CHANGELOG.md").write_text("3.4.0-r20.2", encoding="utf-8")
            result = verify_release(root)
            self.assertFalse(result["ok"])
            self.assertTrue(any("README.md" in issue for issue in result["issues"]))

    def test_cli_exposes_version_options(self) -> None:
        args = parser().parse_args(["version", "--verify", "--history", "--since", "r18", "--limit", "7"])
        self.assertTrue(args.verify)
        self.assertTrue(args.history)
        self.assertEqual(args.since, "r18")
        self.assertEqual(args.limit, 7)

    def test_tool_registry_exposes_version_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = ToolSettings(path=Path(tmp) / "tools.toml")
            ids = {item.id for item in build_tool_registry(settings)}
        self.assertIn("assistant.version", ids)
        self.assertIn("assistant.version.history", ids)
        self.assertIn("assistant.version.since", ids)


if __name__ == "__main__":
    unittest.main()
