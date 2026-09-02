from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from personal_assistant.agent_tool_orchestration import (
    build_native_tool_contract,
    compile_command_contract,
    guard_claims,
    route_intent,
    strict_argument_schema,
    verify_contract,
)
from personal_assistant.container_entrypoint import ensure_personal_assistant_plugin_config
from personal_assistant.tool_registry import tool_definitions

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "docker/openclaw-personal-assistant-plugin"
CORPUS = ROOT / "tests/fixtures/m13/tool-use-corpus.json"


class AgentToolContractTests(unittest.TestCase):
    def test_generator_and_generated_contract_are_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/generate-agent-tools.py"), "verify"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(verify_contract(PLUGIN / "generated-tools.json"), [])

    def test_every_supported_catalog_operation_is_exposed_exactly_once(self) -> None:
        payload = build_native_tool_contract()
        exposed = [
            operation
            for group in payload["native_tools"]
            for operation in group["operations"]
        ]
        supported = [item["tool_id"] for item in payload["operations"] if item["supported"]]
        self.assertCountEqual(exposed, supported)
        self.assertEqual(len(exposed), len(set(exposed)))
        excluded = [item for item in payload["operations"] if not item["supported"]]
        self.assertEqual(
            [(item["tool_id"], item["exclusion_reason"]) for item in excluded],
            [("mail.calendar-command", "non-cli-contract")],
        )
        self.assertEqual(payload["catalog_tool_count"], len(tool_definitions()))

    def test_schema_separates_duplicate_placeholder_by_cli_option(self) -> None:
        command = next(
            definition.command
            for definition in tool_definitions()
            if definition.id == "nextcloud.calendar.create"
        )
        schema, compiled = strict_argument_schema(command)
        self.assertTrue(compiled.supported)
        self.assertIn("start", schema["properties"])
        self.assertIn("end", schema["properties"])
        self.assertFalse(schema["additionalProperties"])

    def test_optional_catalog_arguments_remain_optional_in_schema_and_argv(self) -> None:
        payload = build_native_tool_contract()
        operation = next(
            item for item in payload["operations"]
            if item["tool_id"] == "portfolio.research.screen"
        )
        self.assertNotIn("exchange", operation["argument_schema"]["required"])
        self.assertNotIn("sector", operation["argument_schema"]["required"])
        self.assertIn("strategy", operation["argument_schema"]["required"])

    def test_shell_and_unknown_executable_templates_are_rejected(self) -> None:
        for command in (
            "./scripts/assistant.sh status && touch /tmp/unsafe",
            "sh -c './scripts/assistant.sh status'",
            "./scripts/assistant.sh status `id`",
        ):
            with self.subTest(command=command):
                self.assertFalse(compile_command_contract(command).supported)

    def test_generated_schemas_cannot_express_shell_or_unknown_effects(self) -> None:
        payload = json.loads((PLUGIN / "generated-tools.json").read_text(encoding="utf-8"))
        schema = json.loads((PLUGIN / "evidence.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(payload["security"]["argv_only"])
        self.assertFalse(payload["security"]["shell"])
        self.assertEqual(schema["properties"]["mode"]["enum"], ["read", "local-write", "write"])
        self.assertNotIn("command", schema["properties"])
        self.assertEqual(
            set(schema["properties"]["tool_id"]["enum"]),
            {
                item["tool_id"]
                for item in payload["operations"]
                if item["supported"]
            },
        )


class AgentRoutingAndGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    def test_corpus_is_synthetic_and_routes_behaviorally(self) -> None:
        self.assertEqual(self.corpus["privacy"], "synthetic-only")
        serialized = json.dumps(self.corpus, ensure_ascii=False)
        self.assertNotRegex(serialized, r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
        self.assertNotIn("/srv/openclaw", serialized)
        for case in self.corpus["cases"]:
            with self.subTest(case=case["id"]):
                route = route_intent(case["prompt"])
                domains = {item["domain"] for item in route["routes"]}
                self.assertTrue(set(case["expected_domains"]).issubset(domains), route)
                operations = {
                    operation
                    for item in route["routes"]
                    for operation in item["operations"]
                }
                self.assertTrue(set(case["allowed_first_operations"]) & operations, route)
                self.assertTrue(route["read_only_prefetch_only"])
                self.assertFalse(route["external_write_authorized"])

    def test_untrusted_result_cannot_change_user_selected_route(self) -> None:
        case = next(item for item in self.corpus["cases"] if item["id"] == "remote-prompt-injection")
        original = route_intent(case["prompt"])
        self.assertEqual([item["domain"] for item in original["routes"]], ["mail"])
        self.assertNotIn("runtime", [item["domain"] for item in original["routes"]])

    def test_incomplete_search_cannot_authorize_negative_claim(self) -> None:
        case = next(item for item in self.corpus["cases"] if item["id"] == "incomplete-mail-negative")
        verdict = guard_claims(
            route=route_intent(case["prompt"]),
            answer=case["scripted_answer"],
            evidence=case["scripted_evidence"],
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("negative-claim-not-authorized", verdict["issues"])

    def test_unverified_write_cannot_authorize_success_claim(self) -> None:
        case = next(item for item in self.corpus["cases"] if item["id"] == "unverified-write-success")
        verdict = guard_claims(
            route=route_intent(case["prompt"]),
            answer=case["scripted_answer"],
            evidence=case["scripted_evidence"],
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("write-success-not-authorized", verdict["issues"])

    def test_unresolved_conversation_does_not_force_tool(self) -> None:
        self.assertEqual(route_intent("Erzähl mir einen kurzen Witz")["routes"], [])

    def test_deterministic_replay_passes_without_external_writes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/benchmark-m13.py"), "--phase", "implemented"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["passed"], payload["case_count"])
        self.assertEqual(payload["external_writes"], 0)


class AgentPluginRuntimeTests(unittest.TestCase):
    def test_node_runtime_treats_hostile_arguments_as_data_and_binds_approval(self) -> None:
        script = r"""
import {
  compileInvocation, createApprovalLedger, guardAnswer, makeEvidence,
  routePrompt, shouldBlockGenericTool
} from './docker/openclaw-personal-assistant-plugin/runtime.js';
import contract from './docker/openclaw-personal-assistant-plugin/generated-tools.json' with {type:'json'};
const op = contract.operations.find((row) => row.tool_id === 'nextcloud.calendar.create');
const args = {
  title: 'Test; touch /tmp/m13-unsafe',
  start: '2026-09-02T10:00:00+02:00', end: '2026-09-02T11:00:00+02:00',
  location: 'Büro', description: '$(id) && echo unsafe'
};
const invocation = compileInvocation(op, args);
const researchOp = contract.operations.find((row) => row.tool_id === 'portfolio.research.screen');
const researchInvocation = compileInvocation(researchOp, {strategy:'quality-value'});
const ledger = createApprovalLedger(180);
const nonce = ledger.issue({operation: op.tool_id, args, toolCallId: 'call-1', runId: 'run-1'});
const accepted = ledger.consume({nonce, operation: op.tool_id, args, toolCallId: 'call-1', runId: 'run-1'});
const replay = ledger.consume({nonce, operation: op.tool_id, args, toolCallId: 'call-1', runId: 'run-1'});
const changedNonce = ledger.issue({operation: op.tool_id, args, toolCallId: 'call-2', runId: 'run-1'});
const changed = ledger.consume({
  nonce: changedNonce, operation: op.tool_id, args: {...args, title:'other'},
  toolCallId:'call-2', runId:'run-1'
});
const mailOp = contract.operations.find((row) => row.tool_id === 'mail.search');
const incomplete = makeEvidence(
  mailOp, {returncode:0,stderr:''}, {ok:true,complete:false,results:[]},
  'run-1','call-3'
);
const route = routePrompt(contract, 'Gibt es eine Mail zum Test?');
const guard = guardAnswer(contract, route, 'Nein, es gibt keine Mail.', [incomplete]);
console.log(JSON.stringify({invocation, researchInvocation, accepted, replay, changed, guard,
  execBlocked: shouldBlockGenericTool('exec',{
    command:'/opt/openclaw-agent/scripts/assistant.sh mail search --query Test'
  }),
  secretBlocked: shouldBlockGenericTool('read',{path:'/srv/openclaw/secrets/token'})}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["invocation"]["executable"], "/opt/openclaw-agent/scripts/assistant.sh")
        self.assertIn("Test; touch /tmp/m13-unsafe", payload["invocation"]["argv"])
        self.assertIn("$(id) && echo unsafe", payload["invocation"]["argv"])
        self.assertNotIn("--exchange", payload["researchInvocation"]["argv"])
        self.assertNotIn("--sector", payload["researchInvocation"]["argv"])
        self.assertTrue(payload["accepted"])
        self.assertFalse(payload["replay"])
        self.assertFalse(payload["changed"])
        self.assertFalse(payload["guard"]["ok"])
        self.assertTrue(payload["execBlocked"])
        self.assertTrue(payload["secretBlocked"])

    def test_gateway_plugin_configuration_is_idempotent_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "openclaw.json"
            path.write_text('{"plugins":{"load":{"paths":[]},"entries":{}}}\n', encoding="utf-8")
            self.assertTrue(ensure_personal_assistant_plugin_config(path))
            self.assertFalse(ensure_personal_assistant_plugin_config(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(
                "/opt/openclaw-plugins/personal-assistant-tools",
                payload["plugins"]["load"]["paths"],
            )
            entry = payload["plugins"]["entries"]["personal-assistant-tools"]
            self.assertEqual(payload["tools"]["alsoAllow"], ["personal-assistant-tools"])
            self.assertTrue(entry["enabled"])
            self.assertTrue(entry["hooks"]["allowConversationAccess"])
            self.assertTrue(entry["hooks"]["allowPromptInjection"])

            path.write_text('{"plugins":{"load":{"paths":"unsafe"}}}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "String-Liste"):
                ensure_personal_assistant_plugin_config(path)

            path.write_text('{"tools":{"alsoAllow":"unsafe"}}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "tools.alsoAllow"):
                ensure_personal_assistant_plugin_config(path)

    def test_plugin_manifest_contract_matches_generated_tools(self) -> None:
        manifest = json.loads((PLUGIN / "openclaw.plugin.json").read_text(encoding="utf-8"))
        generated = json.loads((PLUGIN / "generated-tools.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["contracts"]["tools"],
            [item["name"] for item in generated["native_tools"]],
        )
        self.assertTrue(
            all(
                re.fullmatch(r"personal_assistant_[a-z]+_(?:read|write)", name)
                for name in manifest["contracts"]["tools"]
            )
        )


if __name__ == "__main__":
    unittest.main()
