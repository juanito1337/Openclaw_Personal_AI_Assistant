"""Hermetic Python/JS parity and real plugin-hook regressions; no domain I/O."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from personal_assistant.action_completion import (
    action_completion_guard,
    advance_action_obligation,
    build_action_obligation,
)
from personal_assistant.tool_registry import tool_definitions

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = {operation.id: operation for operation in tool_definitions()}


def _node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _parity(prompt: str, domain: str, calls: list[dict] | None = None) -> dict:
    route = {"resolved": True, "routes": [{"domain": domain}]}
    state = build_action_obligation(prompt, route, turn_id="turn")
    assert state is not None
    for call in calls or []:
        op = OPERATIONS[call["id"]]
        state = advance_action_obligation(
            state,
            operation=op.id,
            mode=op.mode,
            ok=call.get("ok", True),
            postcondition_verified=call.get("verified", False),
            error=call.get("error"),
            argument_digest=call.get("digest", ""),
            payload=call.get("payload", {}),
            evidence_turn_id=call.get("turn", "turn"),
        )
    script = r"""
import {advanceActionObligation, buildActionObligation, guardActionCompletion}
  from './docker/openclaw-personal-assistant-plugin/runtime.js';
import contract from './docker/openclaw-personal-assistant-plugin/generated-tools.json' with {type:'json'};
const input = INPUT;
let state = buildActionObligation(contract, input.prompt, input.route, 'turn');
for (const call of input.calls) {
  const op = contract.operations.find((item) => item.tool_id === call.id);
  state = advanceActionObligation(contract, state, op, {
    ok: call.ok ?? true, postcondition_verified: call.verified ?? false,
    error: call.error, turn_id: call.turn ?? 'turn',
  }, call.payload ?? {}, call.digest ?? '');
}
const guard = guardActionCompletion(contract, state, 'Beide Aufgaben erledigt.');
console.log(JSON.stringify({state, guard}));
""".replace("INPUT", json.dumps({"prompt": prompt, "route": route, "calls": calls or []}))
    actual = _node(script)
    assert actual["state"] == state
    assert actual["guard"] == action_completion_guard(state, "Beide Aufgaben erledigt.")
    return state


@pytest.mark.parametrize(
    ("domain", "prompt", "reads", "write"),
    [
        (
            "tasks",
            "Bitte Aufgabe Test abschließen",
            ["nextcloud.tasks.status", "nextcloud.tasks.list"],
            "nextcloud.tasks.update",
        ),
        ("mail", "Leite die Mail Test weiter", ["mail.search", "mail.read"], "mail.forward-draft"),
        (
            "portfolio",
            "Importiere Depot.csv",
            ["portfolio.status", "portfolio.holdings"],
            "portfolio.import.csv.nextcloud.confirm",
        ),
        (
            "nextcloud",
            "Bitte Datei Test erstellen",
            ["nextcloud.list", "nextcloud.sync"],
            "nextcloud.workspace.write-text",
        ),
        (
            "invoices",
            "Bitte Rechnung Test aktualisieren",
            ["assistant.invoices.status", "assistant.invoices.list"],
            None,
        ),
        (
            "orders",
            "Bitte Auftrag Test aktualisieren",
            ["nextcloud.deck.orders.status", "nextcloud.deck.orders.list"],
            None,
        ),
    ],
)
def test_bounded_preparatory_reads_use_typed_domain_catalog(
    domain: str,
    prompt: str,
    reads: list[str],
    write: str | None,
) -> None:
    calls = [{"id": operation} for operation in reads]
    prepared = _parity(prompt, domain, calls)
    assert prepared["terminal_state"] is None
    assert prepared["current_step"] == "resolve-target"
    if write:
        complete = _parity(prompt, domain, [*calls, {"id": write, "verified": True, "digest": "a" * 64}])
        assert complete["terminal_state"] == ("approval-required" if "draft" in write else "completed")


def test_preparatory_reads_reject_foreign_domains_and_keep_call_limit() -> None:
    foreign = _parity("Bitte Aufgabe Test erstellen", "tasks", [{"id": "mail.search"}])
    assert foreign["last_error"] == "unexpected-workflow-step"
    bounded = _parity("Bitte Aufgabe Test erstellen", "tasks", [{"id": "nextcloud.tasks.status"}] * 13)
    assert bounded["tool_calls"] == 12
    assert bounded["last_error"] == "tool-call-limit"


@pytest.mark.parametrize(
    ("prompt", "count", "error"),
    [
        ("Erstelle zwei Aufgaben: Test A und Test B", 2, "multiple-targets-not-bound"),
        ("Erstelle Aufgabe Test A und Aufgabe Test B", 1, "multiple-targets-not-bound"),
        ("Erstelle eine Aufgabe Test A und eine Aufgabe Test B", 1, "multiple-targets-not-bound"),
        ("Create 3 tasks: A, B and C", 3, "multiple-targets-not-bound"),
        ("Bitte alle Aufgaben abschließen", 1, "multiple-targets-not-bound"),
        ("Bitte Aufgaben A und B erstellen", 1, "multiple-targets-not-bound"),
        ("Bitte fünf Aufgaben erstellen", 4, "target-limit"),
        ("Bitte Aufgabe erstellen:\n- A\n- B", 1, "multiple-targets-not-bound"),
        ("Bitte Aufgabe Test 2 erstellen", 1, None),
        ("Bitte Aufgabe Test für 2026-12-29 erstellen", 1, None),
        ('Bitte Aufgabe "zwei Aufgaben prüfen" erstellen', 1, None),
        ("Bitte Aufgabe X in meiner Aufgaben-Liste abschließen", 1, None),
        ("Bitte Aufgabe X aktualisieren und den Status der Aufgabe prüfen", 1, None),
        ("Erstelle Aufgabe X und setze die Priorität der Aufgabe auf 1", 1, None),
        ('Bitte Aufgabe X in der Liste "Kontakte" erstellen', 1, None),
        ("Bitte Aufgabe X in der Liste Kontakte erstellen", 1, None),
        ("Bitte einen Ordner Test erstellen", 1, None),
    ],
)
def test_multiple_scope_is_not_inferred_from_titles_dates_or_resource_names(
    prompt: str,
    count: int,
    error: str | None,
) -> None:
    state = _parity(prompt, "tasks")
    assert state["target_count"] == count
    assert state["last_error"] == error
    if error:
        after_one = _parity(prompt, "tasks", [{"id": "nextcloud.tasks.create", "verified": True}])
        assert after_one["completed_targets"] == 0
        assert after_one["terminal_state"] != "completed"
        assert not action_completion_guard(after_one, "Beide Aufgaben erledigt.")["ok"]
        assert not action_completion_guard(after_one, "Soll ich es erneut versuchen?")["ok"]


def _calendar_calls() -> list[dict]:
    return [
        {"id": "mail.search"},
        {"id": "mail.read"},
        {"id": "nextcloud.calendar.from-mail-preview", "payload": {"candidate_count": 2}},
    ]


def test_one_completed_target_never_proves_a_plural_completion_claim() -> None:
    state = _parity("Erstelle Aufgabe Test A", "tasks", [
        {"id": "nextcloud.tasks.create", "verified": True, "digest": "a" * 64},
    ])
    assert state["terminal_state"] == "completed"
    assert not action_completion_guard(state, "Beide Aufgaben erfolgreich angelegt.")["ok"]
    assert action_completion_guard(state, "Die Aufgabe Test A ist angelegt.")["ok"]


def test_mail_calendar_distinct_candidates_not_changed_arguments_prove_two_targets() -> None:
    prompt = "Trage Hinflug und Rueckflug aus der Mail als Termine ein"
    first = {
        "id": "nextcloud.calendar.from-mail-create",
        "verified": True,
        "digest": "a" * 64,
        "payload": {"candidate_id": "outbound"},
    }
    partial = _parity(prompt, "calendar", [*_calendar_calls(), first])
    assert partial["completed_targets"] == 1 and partial["terminal_state"] is None
    duplicate = _parity(prompt, "calendar", [*_calendar_calls(), first, {**first, "digest": "b" * 64}])
    assert duplicate["completed_targets"] == 1
    assert duplicate["last_error"] == "duplicate-target-evidence"
    complete = _parity(
        prompt,
        "calendar",
        [
            *_calendar_calls(),
            first,
            {**first, "digest": "b" * 64, "payload": {"candidate_id": "return"}},
        ],
    )
    assert complete["terminal_state"] == "completed"
    missing = _parity(prompt, "calendar", [*_calendar_calls(), {**first, "payload": {}}])
    assert missing["last_error"] == "target-identity-unverified"
    foreign = _parity(
        prompt,
        "calendar",
        [
            *_calendar_calls(),
            first,
            {
                "id": "nextcloud.tasks.update",
                "verified": True,
                "digest": "b" * 64,
            },
        ],
    )
    assert foreign["completed_targets"] == 1 and foreign["last_error"] == "unexpected-write-operation"
    old = _parity(prompt, "calendar", [*_calendar_calls(), {**first, "turn": "old-turn"}])
    assert old["completed_targets"] == 0
    unbound = _parity(prompt, "calendar", [*_calendar_calls(), {**first, "turn": ""}])
    assert unbound["completed_targets"] == 0


# Import the complete copied plugin package through Node's real ESM loader.
# Only SDK entry, clock and command execution have narrow test adapters; sibling
# modules, immutable UI assets, catalog, compiler and security guards stay real.
# Registration starts no worker or request. Clock injection needs no sleeps.
HOOK_HARNESS = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import * as runtime from './docker/openclaw-personal-assistant-plugin/runtime.js';
let now = 1000;
class FakeDate extends Date { static now() { return now; } }
const invocations = [];
const packageSource = path.resolve('docker/openclaw-personal-assistant-plugin');
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'personal-assistant-hooks-'));
process.once('exit', () => fs.rmSync(fixtureRoot, {recursive:true, force:true}));
const packageRoot = path.join(fixtureRoot, 'plugin');
fs.cpSync(packageSource, packageRoot, {recursive:true});
const adapter = {
  ...runtime,
  createRunRetention: (options) => runtime.createRunRetention({...options, now:()=>now}),
  createApprovalLedger: (ttl) => runtime.createApprovalLedger(ttl, {now:()=>now}),
  selectPendingConfirmation: (prompt,records) => runtime.selectPendingConfirmation(prompt,records,now),
  spawnJson: async (invocation) => {
    invocations.push(invocation);
    const data = invocation.argv.join(' ') === 'tools list'
      ? {tools: contract.operations.map((op)=>({id:op.tool_id,command:op.command}))}
      : {ok:true,complete:true,status:'completed',postcondition_verified:true,
         remote:{uid:'synthetic',etag:'test'}};
    return {returncode:0,stdout:JSON.stringify(data),stderr:''};
  },
};
globalThis.__hookTestAdapter = adapter;
globalThis.__hookTestDate = FakeDate;
fs.writeFileSync(path.join(packageRoot, 'fixture-sdk-entry.js'),
  'export const definePluginEntry = (entry) => entry;\n');
fs.writeFileSync(path.join(packageRoot, 'fixture-runtime.js'), `
export * from './runtime.js';
const adapter = globalThis.__hookTestAdapter;
export const createRunRetention = adapter.createRunRetention;
export const createApprovalLedger = adapter.createApprovalLedger;
export const selectPendingConfirmation = adapter.selectPendingConfirmation;
export const spawnJson = adapter.spawnJson;
`);
const location = path.join(packageRoot, 'index.js');
let source = fs.readFileSync(location, 'utf8');
for (const [original, replacement] of [
  ['from "openclaw/plugin-sdk/plugin-entry"', 'from "./fixture-sdk-entry.js"'],
  ['from "./runtime.js"', 'from "./fixture-runtime.js"'],
]) {
  assert.equal(source.split(original).length, 2, 'exactly one expected dependency injection');
  source = source.replace(original, replacement);
}
source = 'const Date = globalThis.__hookTestDate;\n' + source + `
export const fixtureStats = () => ({
  routes:routeByRun.size,prompts:promptByRun.size,evidence:evidenceByRun.size,
  sessions:sessionByRun.size,obligations:obligationByRun.size,
  retry:retryByRun.size,invalid:invalidArgumentsByRun.size,timing:turnLatencyByRun.size,
  confirmations:confirmationByRun.size,pending:pendingConfirmationsBySession.size,ledger:ledger.size(),
});
`;
fs.writeFileSync(location, source);
const imported = await import(pathToFileURL(location).href);
const loaded = {plugin:imported.default, testing:imported.testing, stats:imported.fixtureStats};
const {contract,obligationByRun,runRetention} = loaded.testing;
const hooks = new Map();
const factories = new Map();
const channels = new Map();
const gatewayMethods = new Map();
const httpRoutes = new Map();
process.env.OPENCLAW_SETUP_SOCKET = path.join(fixtureRoot, 'unopened-setup.sock');
process.env.OPENCLAW_NEXTCLOUD_CONNECTION_DIR = path.join(fixtureRoot, 'unopened-connection');
loaded.plugin.register({runtime:{}, on:(name,hook)=>hooks.set(name,hook),
  registerChannel:({plugin})=>channels.set(plugin.id,plugin),
  registerGatewayMethod:(name,handler,options)=>gatewayMethods.set(name,{handler,options}),
  registerHttpRoute:(route)=>httpRoutes.set(route.path,route),
  registerTool:(factory,metadata)=>factories.set(metadata.name,factory)});
assert.ok(channels.has('nextcloud-account-talk'));
assert.equal(gatewayMethods.get('personal_assistant.setup').options.scope, 'operator.admin');
assert.ok(httpRoutes.has('/plugins/personal-assistant-tools/nextcloud/setup'));
assert.equal(invocations.length, 0, 'registering the whole plugin starts no tool execution');
const begin = async (runId,prompt,sessionKey='session') =>
  hooks.get('before_prompt_build')({runId,prompt,sessionKey},{runId,sessionKey});
const groupFor = (operation) =>
  contract.native_tools.find((group)=>group.operations.includes(operation)).name;
const gate = async (runId,operation,args={},toolCallId='call',sessionKey='session') =>
  hooks.get('before_tool_call')({runId,toolName:groupFor(operation),toolCallId,
    params:{operation,arguments:args}},{runId,sessionKey});
const execute = async (operation,params,toolCallId='call',ctx={}) =>
  factories.get(groupFor(operation))(ctx).execute(toolCallId,params);
const reply = async (runId,text) =>
  hooks.get('reply_payload_sending')({runId,payload:{text}},{});
const operationCalls = () => invocations.filter((item)=>item.argv.join(' ')!=='tools list').length;
"""


def test_real_hooks_bound_completed_runs_and_fail_closed_after_eviction() -> None:
    data = _node(
        HOOK_HARNESS
        + r"""
for (let i=0;i<1000;i++) {
  await begin(`run-${i}`, 'Hallo', `session-${i}`);
  await reply(`run-${i}`, 'Hallo!');
}
const retained = loaded.stats();
const staleReply = await reply('run-0', 'Alles erledigt.');
const staleWrite = await gate('run-0','nextcloud.tasks.update',{uid:'x',expected_title:'Test'});
now += runtime.RUN_RETENTION.ttlMs;
runRetention.sweep();
console.log(JSON.stringify({retained,after:loaded.stats(),staleReply,staleWrite,calls:operationCalls()}));
"""
    )
    assert data["retained"]["routes"] == 128
    assert max(data["retained"].values()) <= 128
    assert all(value == 0 for value in data["after"].values())
    assert data["staleReply"]["reason"] == "personal-assistant-turn-state-guard"
    assert data["staleWrite"]["block"] and data["calls"] == 0


def test_actual_approval_resume_multiple_replies_and_terminal_readback() -> None:
    data = _node(
        HOOK_HARNESS
        + r"""
await begin('run', 'Bitte Aufgabe Test abschließen');
for (const id of ['nextcloud.tasks.status','nextcloud.tasks.list']) {
  const prepared = await gate('run',id,{},id);
  await execute(id,prepared.params,id); // Factory intentionally has no runId.
}
const approved = await gate('run','nextcloud.tasks.update',{uid:'x',expected_title:'Test'});
approved.requireApproval.onResolution('allow-once');
const sent = await execute('nextcloud.tasks.update',approved.params);
const first = await reply('run','Die Aufgabe wurde abgeschlossen.');
const second = await reply('run','Es gibt keine Aufgaben.');
const followup = await gate('run','nextcloud.tasks.update',{uid:'other',expected_title:'Other'},'other');
const readback = await gate('run','nextcloud.tasks.list',{},'readback');
await execute('nextcloud.tasks.list',readback.params,'readback');
const replay = await execute('nextcloud.tasks.update',approved.params);
console.log(JSON.stringify({sent,first:first??null,second,followup,readback,replay,
  state:obligationByRun.get('run'),calls:operationCalls()}));
"""
    )
    assert data["sent"]["details"]["personalAssistantEvidence"]["postcondition_verified"]
    assert data["first"] is None
    assert data["second"]["reason"] == "personal-assistant-action-completion-guard"
    assert data["followup"]["block"]
    assert "params" in data["readback"]
    assert data["state"]["terminal_state"] == "completed"
    assert data["calls"] == 4  # status, list, one update, read-back
    assert json.loads(data["replay"]["content"][0]["text"])["diagnostic"]["category"] == "approval-required"


def test_real_hooks_block_multiple_scope_before_approval_or_execution() -> None:
    data = _node(
        HOOK_HARNESS
        + r"""
await begin('run','Bitte zwei Aufgaben erstellen: A und B');
const blocked = await gate('run','nextcloud.tasks.create',{
  title:'A',description:'A',due:'2026-12-29',priority:0});
const answer = await reply('run','Beide Aufgaben erfolgreich angelegt.');
console.log(JSON.stringify({blocked,answer,calls:operationCalls(),stats:loaded.stats()}));
"""
    )
    assert data["blocked"]["blockReason"] == "multiple-targets-not-bound"
    assert data["answer"]["reason"] == "personal-assistant-action-completion-guard"
    assert data["calls"] == data["stats"]["ledger"] == 0


@pytest.mark.parametrize("change", ["arguments", "run", "call", "expiry", "eviction", "denied"])
def test_old_foreign_or_changed_approval_never_executes(change: str) -> None:
    script = (
        HOOK_HARNESS
        + r"""
await begin('original','Bitte Aufgabe Test abschließen');
const approved = await gate('original','nextcloud.tasks.update',{uid:'x',expected_title:'Test'});
approved.requireApproval.onResolution(CHANGE === 'denied' ? 'deny' : 'allow-once');
const params = structuredClone(approved.params);
let call = 'call';
if (CHANGE === 'arguments') params.arguments.uid = 'changed';
if (CHANGE === 'run') params.__approval_run_id = 'foreign';
if (CHANGE === 'call') call = 'foreign';
if (CHANGE === 'expiry') now += contract.limits.approval_ttl_seconds * 1000;
if (CHANGE === 'eviction') for (let i=0;i<128;i++) await begin(`other-${i}`,'Hallo');
const response = await execute('nextcloud.tasks.update',params,call);
console.log(JSON.stringify({response,calls:operationCalls()}));
"""
    )
    data = _node(script.replace("CHANGE", json.dumps(change)))
    assert data["calls"] == 0
    result = json.loads(data["response"]["content"][0]["text"])
    assert result["diagnostic"]["code"] == "missing-or-stale-bound-approval"
    assert result["result"]["executed"] is False


def test_retention_caps_abandoned_nonces_pending_sessions_and_prompt_bytes() -> None:
    data = _node(
        HOOK_HARNESS
        + r"""
for (let i=0;i<600;i++) {
  await begin(`run-${i}`,'Bitte Aufgabe Test erstellen',`s-${i}`);
  await gate(`run-${i}`,'nextcloud.tasks.create',{
    title:'Test',description:'Test',due:'2026-12-29',priority:0},`call-${i}`,`s-${i}`);
}
const pending = loaded.stats();
await begin('large','Bitte Aufgabe Test erstellen '+ 'x'.repeat(runtime.RUN_RETENTION.maxPromptBytes));
const oversized = await gate('large','nextcloud.tasks.update',{uid:'x',expected_title:'Test'});
const oversizedReply = await reply('large','Erledigt.');
now += contract.limits.approval_ttl_seconds * 1000;
await begin('new','JA','s-599');
console.log(JSON.stringify({pending,oversized,oversizedReply,after:loaded.stats(),
  confirmed:obligationByRun.get('new')??null,calls:operationCalls()}));
"""
    )
    assert data["pending"]["pending"] == data["pending"]["ledger"] == 128
    assert data["oversized"]["blockReason"] == "prompt-retention-limit"
    assert data["oversizedReply"]["reason"] == "personal-assistant-turn-state-guard"
    assert data["after"]["pending"] == data["after"]["ledger"] == 0
    assert data["confirmed"] is None and data["calls"] == 0


def test_approval_ledger_has_independent_ttl_and_capacity() -> None:
    data = _node(r"""
import {createApprovalLedger} from './docker/openclaw-personal-assistant-plugin/runtime.js';
let time=0;
const ledger=createApprovalLedger(180,{maxEntries:3,now:()=>time});
const args={operation:'test',args:{a:'b'},toolCallId:'call',runId:'run'};
const nonces=Array.from({length:4},()=>ledger.issue(args));
const size=ledger.size();
const evicted=ledger.consume({...args,nonce:nonces[0]});
time=180000;
const expired=ledger.consume({...args,nonce:nonces[3]});
console.log(JSON.stringify({size,evicted,expired,after:ledger.size()}));
""")
    assert data == {"size": 3, "evicted": False, "expired": False, "after": 0}


@pytest.mark.parametrize("change", ["none", "expiry", "foreign-session", "arguments", "operation"])
def test_simple_confirmation_is_current_session_bound_and_unchanged(change: str) -> None:
    script = HOOK_HARNESS + r"""
const args={title:'Test',description:'Test',due:'2026-12-29',priority:0};
await begin('request','Bitte Aufgabe Test erstellen','original-session');
await gate('request','nextcloud.tasks.create',args,'pending','original-session');
const session=CHANGE==='foreign-session'?'other-session':'original-session';
await begin('confirmation','JA',session);
if (CHANGE==='expiry') now+=contract.limits.approval_ttl_seconds*1000;
const operation=CHANGE==='operation'?'nextcloud.tasks.update':'nextcloud.tasks.create';
const submitted=CHANGE==='operation'?{uid:'x',expected_title:'Test'}:
  CHANGE==='arguments'?{...args,title:'changed'}:args;
const result=await gate('confirmation',operation,submitted,'confirmed',session);
let executed=null;
if(result.params&&!result.requireApproval) executed=await execute(operation,result.params,'confirmed');
console.log(JSON.stringify({result,executed,calls:operationCalls()}));
"""
    data = _node(script.replace("CHANGE", json.dumps(change)))
    if change == "none":
        assert "requireApproval" not in data["result"]
        assert data["calls"] == 1
    else:
        assert data["result"]["blockReason"] == "missing-or-stale-bound-confirmation"
        assert data["calls"] == 0


def test_repeat_prompt_hook_does_not_reset_terminal_evidence_or_approval_binding() -> None:
    data = _node(HOOK_HARNESS + r"""
const prompt='Bitte Aufgabe Test abschließen';
await begin('run',prompt);
const approved=await gate('run','nextcloud.tasks.update',{uid:'x',expected_title:'Test'});
await begin('run',prompt); // SDK prompt rebuild while native approval is pending.
approved.requireApproval.onResolution('allow-once');
await execute('nextcloud.tasks.update',approved.params);
await begin('run',prompt); // A second rebuild must not reopen a completed action.
const followup=await gate('run','nextcloud.tasks.update',{uid:'other',expected_title:'Other'});
console.log(JSON.stringify({followup,state:obligationByRun.get('run'),calls:operationCalls()}));
""")
    assert data["state"]["terminal_state"] == "completed"
    assert data["followup"]["block"]
    assert data["calls"] == 1


@pytest.mark.parametrize("expiration", ["approval", "run"])
def test_approval_is_rechecked_after_async_discovery_before_write_spawn(expiration: str) -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if (invocation.argv.join(' ')==='tools list') "
        + ("now+=contract.limits.approval_ttl_seconds*1000;" if expiration == "approval"
           else "now+=runtime.RUN_RETENTION.ttlMs;"),
    )
    data = _node(harness + r"""
await begin('run','Bitte Aufgabe Test abschließen');
const approved=await gate('run','nextcloud.tasks.update',{uid:'x',expected_title:'Test'});
approved.requireApproval.onResolution('allow-once');
const result=await execute('nextcloud.tasks.update',approved.params);
console.log(JSON.stringify({result,calls:operationCalls()}));
""")
    assert data["calls"] == 0
    result = json.loads(data["result"]["content"][0]["text"])
    assert result["result"]["executed"] is False
    assert result["diagnostic"]["category"] == (
        "approval-required" if expiration == "approval" else "turn-state-missing-or-expired"
    )


def test_concurrent_resume_of_one_approval_starts_exactly_one_write() -> None:
    data = _node(HOOK_HARNESS + r"""
await begin('run','Bitte Aufgabe Test abschließen');
const approved=await gate('run','nextcloud.tasks.update',{uid:'x',expected_title:'Test'});
approved.requireApproval.onResolution('allow-once');
const results=await Promise.all([
  execute('nextcloud.tasks.update',approved.params),execute('nextcloud.tasks.update',approved.params),
]);
console.log(JSON.stringify({results,calls:operationCalls(),state:obligationByRun.get('run')}));
""")
    assert data["calls"] == 1
    assert data["state"]["terminal_state"] == "completed"
    assert sum(row["details"]["personalAssistantEvidence"]["ok"] for row in data["results"]) == 1


@pytest.mark.parametrize("operation", ["portfolio.import.csv", "portfolio.import.csv.nextcloud",
                                      "portfolio.import.pp"])
def test_local_import_preview_is_preparation_never_import_completion(operation: str) -> None:
    prompt = "Importiere die Depotdatei depot.csv"
    preview = {"id": operation, "verified": True, "payload": {"ok": True, "dry_run": True}}
    state = _parity(prompt, "portfolio", [{"id": "portfolio.status"}, preview])
    assert state["completed_targets"] == 0
    assert state["terminal_state"] is None
    assert not action_completion_guard(state, "Das Depot wurde importiert.")["ok"]
    complete = _parity(prompt, "portfolio", [preview, {
        "id": "portfolio.import.csv.nextcloud.confirm", "verified": True, "digest": "a" * 64,
    }])
    assert complete["terminal_state"] == "completed"
    data = _node(r"""
import {makeEvidence} from './docker/openclaw-personal-assistant-plugin/runtime.js';
import contract from './docker/openclaw-personal-assistant-plugin/generated-tools.json' with {type:'json'};
const op=contract.operations.find((row)=>row.tool_id===OPERATION);
const evidence=makeEvidence(op,{returncode:0},{ok:true,dry_run:true},'run','call');
const missingFlag=makeEvidence(op,{returncode:0},{ok:true},'run','call');
console.log(JSON.stringify({evidence,missingFlag}));
""".replace("OPERATION", json.dumps(operation)))
    for evidence in data.values():
        assert not evidence["postcondition_verified"]
        assert "write-success" not in evidence["allowed_claims"]
