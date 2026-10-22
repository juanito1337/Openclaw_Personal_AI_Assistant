from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from personal_assistant.agent_tool_orchestration import build_native_tool_contract
from personal_assistant.tool_registry import tool_definitions

ROOT = Path(__file__).resolve().parents[1]


class NextcloudResourceToolContractTests(unittest.TestCase):
    def test_resource_selection_is_optional_without_weakening_write_approval(self) -> None:
        operations = {
            operation["tool_id"]: operation
            for operation in build_native_tool_contract()["operations"]
        }
        for definition in tool_definitions():
            if definition.domain not in {"calendar", "contacts", "tasks"}:
                continue
            if definition.id.endswith((".discover", ".configure")):
                continue
            with self.subTest(operation=definition.id):
                for schema in (
                    definition.argument_schema,
                    operations[definition.id]["argument_schema"],
                ):
                    self.assertIn("resource", schema["properties"])
                    self.assertNotIn("resource", schema["required"])
                if definition.mode == "write":
                    self.assertNotEqual(definition.approval, "none")
                    self.assertTrue(definition.writes_external_data)

    @unittest.skipUnless(shutil.which("node"), "node is required for native argv checks")
    def test_native_call_preserves_exact_resource_or_omits_the_whole_option(self) -> None:
        operations = {
            operation["tool_id"]: operation
            for operation in build_native_tool_contract()["operations"]
        }
        selected = {
            operation_id: operations[operation_id]
            for operation_id in (
                "nextcloud.calendar.list",
                "nextcloud.contacts.list",
                "nextcloud.tasks.list",
                "nextcloud.tasks.update",
                "nextcloud.list",
            )
        }
        script = """
import assert from 'node:assert/strict';
import {compileInvocation} from './docker/openclaw-personal-assistant-plugin/runtime.js';
const operations = JSON.parse(process.argv[1]);
for (const [id, operation] of Object.entries(operations)) {
  const args = id.endsWith('.update') ? {uid:'same-uid', expected_title:'A task'} : {};
  const legacy = compileInvocation(operation, args);
  const parameter = id === 'nextcloud.list' ? 'path' : 'resource';
  assert.ok(!legacy.argv.includes('--' + parameter));
  assert.ok(!legacy.argv.some(value => value.includes('<' + parameter)));
  const selection = parameter === 'path'
    ? 'Documents/Exact Folder' : 'nextcloud-account:fixture-namespace:calendar:exact-id';
  const managed = compileInvocation(operation, {...args, [parameter]: selection});
  const option = managed.argv.indexOf('--' + parameter);
  assert.ok(option >= 0);
  assert.equal(managed.argv[option + 1], selection);
  assert.deepEqual(
    managed.argv.filter((_value, index) => index !== option && index !== option + 1),
    legacy.argv,
  );
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, json.dumps(selected)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
