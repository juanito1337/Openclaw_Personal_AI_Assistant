from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from personal_assistant.config import SelfManagementConfig
from personal_assistant.models import Resource
from personal_assistant.policy import DEFAULT_DENIED_ACTIONS, PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import (
    build_tool_registry,
    capability_schema,
    static_tool_catalog,
    tool_definitions,
)
from personal_assistant.tool_settings import ToolSettings

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests/golden"


def fully_enabled_settings(path: Path) -> ToolSettings:
    settings = ToolSettings(path=path)
    settings.mail.move.enabled = True
    settings.mail.invoices.enabled = True
    settings.mail.calendar_mail.enabled = True
    workspace = settings.nextcloud.workspace
    workspace.enabled = True
    workspace.allow_mkdir = True
    workspace.allow_write_text = True
    workspace.allow_upload = True
    workspace.allow_move = True
    for direct in (
        settings.nextcloud.calendar,
        settings.nextcloud.tasks,
        settings.nextcloud.contacts,
    ):
        direct.enabled = True
        direct.allow_list = True
        direct.allow_create = True
        direct.allow_update = True
    settings.nextcloud.deck_orders.enabled = True
    return settings


class M5ToolContractTests(unittest.TestCase):
    maxDiff = None

    def test_live_registry_matches_reviewed_golden_contract(self) -> None:
        expected = json.loads((GOLDEN / "m5-tool-contract.json").read_text(encoding="utf-8"))
        actual = [
            tool.to_dict() for tool in build_tool_registry(fully_enabled_settings(Path("/tmp/m5-tools.toml")))
        ]
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 160)

    def test_top_level_help_matches_characterized_output(self) -> None:
        expected = (GOLDEN / "m5-cli-help.txt").read_text(encoding="utf-8")
        characterized = re.sub(r"\s+", " ", expected).strip()
        for columns in ("80", "200"):
            with self.subTest(columns=columns):
                completed = subprocess.run(
                    [sys.executable, "-m", "personal_assistant", "--help"],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "COLUMNS": columns, "LINES": "24"},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(re.sub(r"\s+", " ", completed.stdout).strip(), characterized)

    def test_static_catalog_is_typed_complete_and_has_real_anchors(self) -> None:
        definitions = tool_definitions()
        self.assertEqual(len(definitions), 160)
        self.assertEqual(len({tool.id for tool in definitions}), len(definitions))
        for tool in definitions:
            self.assertIn(tool.mode, {"read", "local-write", "write"})
            self.assertEqual(tool.mode == "write", tool.writes_external_data, tool.id)
            self.assertTrue(tool.approval, tool.id)
            self.assertEqual(tool.argument_schema.get("type"), "object", tool.id)
            self.assertTrue(tool.output_schema.get("type"), tool.id)
            self.assertTrue(tool.error_codes, tool.id)
            docs_path = ROOT / tool.documentation_anchor.split("#", 1)[0]
            self.assertTrue(docs_path.is_file(), f"{tool.id}: {docs_path}")
            self.assertTrue((ROOT / tool.test_anchor).is_file(), f"{tool.id}: {tool.test_anchor}")
            module_name, function_name = tool.handler.split(":", 1)
            handler = getattr(importlib.import_module(module_name), function_name)
            self.assertTrue(callable(handler), tool.id)

    def test_static_catalog_and_capability_schema_are_config_free(self) -> None:
        missing = ROOT / "tests" / "does-not-exist-m5.toml"
        commands = (
            ["--config", str(missing), "tools", "list", "--catalog"],
            ["--config", str(missing), "capabilities", "--schema"],
        )
        for arguments in commands:
            completed = subprocess.run(
                [sys.executable, "-m", "personal_assistant", *arguments],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertNotIn("Konfiguration fehlt", completed.stderr)
            self.assertNotIn("resources", payload if payload.get("view") == "static-catalog" else {})
        catalog = static_tool_catalog()
        self.assertEqual(catalog["view"], "static-catalog")
        self.assertFalse(catalog["configured"])
        self.assertFalse(catalog["authoritative_for_permissions"])
        self.assertEqual(len(catalog["tools"]), 160)
        self.assertEqual(capability_schema()["properties"]["view"]["const"], "live-capabilities")

    def test_live_capabilities_are_explicitly_separate_from_catalog(self) -> None:
        assistant = object.__new__(PersonalAssistant)
        assistant.registry = SimpleNamespace(list=lambda: [], resources={})
        assistant.settings = SimpleNamespace(list_safe=lambda: {})
        assistant.config = SimpleNamespace(self_management=SelfManagementConfig(enabled=False))
        assistant.tool_settings = SimpleNamespace(operations_profile="standard")
        assistant.tools = lambda: []
        payload = PersonalAssistant.capabilities(assistant)
        self.assertEqual(payload["view"], "live-capabilities")
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["operations_profile"]["name"], "standard")
        self.assertTrue(payload["operations_profile"]["automatic_at_process_start"])
        principles = " ".join(payload["principles"])
        self.assertIn("Secret-Dateien und ihre Verzeichnisse", principles)
        self.assertIn("Status-/Doctor-Werkzeuge", principles)
        self.assertEqual(payload["tools"], [])

    def test_policy_negative_and_approval_contracts_remain_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            registry = ResourceRegistry(root / "resources.toml")
            registry.write(
                [
                    Resource(
                        id="files",
                        kind="file-root",
                        connector="nextcloud",
                        permissions=("read", "create"),
                        metadata={"allowed_roots": ["Assistent"]},
                    ),
                    Resource(
                        id="calendar",
                        kind="calendar",
                        connector="nextcloud",
                        permissions=("read", "update"),
                    ),
                ]
            )
            engine = PolicyEngine(root / "policies.toml", registry)
            for action in sorted(DEFAULT_DENIED_ACTIONS):
                decision = engine.decide("files", action, {"path": "Assistent/item"})
                self.assertFalse(decision.allowed, action)
            outside = engine.decide("files", "files.create", {"path": "Privat/item"})
            self.assertFalse(outside.allowed)
            overwrite = engine.decide("files", "files.create", {"path": "Assistent/item", "overwrite": True})
            self.assertFalse(overwrite.allowed)
            update = engine.decide("calendar", "calendar.update", {})
            self.assertTrue(update.allowed)
            self.assertTrue(update.requires_approval)

    def test_internal_import_graph_is_acyclic_and_core_has_no_mail_adapter_backimport(self) -> None:
        modules: dict[str, Path] = {}
        for package in ("personal_assistant", "mail_agent"):
            for path in (ROOT / package).rglob("*.py"):
                module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
                if module.endswith(".__init__"):
                    module = module.removesuffix(".__init__")
                modules[module] = path
        graph = {module: set() for module in modules}
        for module, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            package = module.rsplit(".", 1)[0] if "." in module else module
            for node in ast.walk(tree):
                target = ""
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        parts = package.split(".")
                        base = ".".join(parts[: len(parts) - node.level + 1])
                        target = (base + "." + (node.module or "")).strip(".")
                    else:
                        target = node.module or ""
                    if target in modules:
                        graph[module].add(target)
                elif isinstance(node, ast.Import):
                    graph[module].update(alias.name for alias in node.names if alias.name in modules)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                self.fail(f"Importzyklus bei {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in graph[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)
        core_prefixes = (
            "personal_assistant.contracts",
            "personal_assistant.service",
            "personal_assistant.policy",
            "personal_assistant.storage",
            "personal_assistant.contact_tools",
        )
        for module, dependencies in graph.items():
            if module.startswith(core_prefixes):
                self.assertFalse(
                    {item for item in dependencies if item.startswith("mail_agent")},
                    f"Core-Rueckimport in {module}",
                )


if __name__ == "__main__":
    unittest.main()
