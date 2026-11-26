"""Native dry-run deadlines, bounded evidence retention and process cancellation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_agent_execution_reliability import HOOK_HARNESS, _node

from personal_assistant.agent_tool_orchestration import guard_claims, route_intent

SUCCESS = "Der Mail-Dry-Run wurde erfolgreich abgeschlossen."


@pytest.mark.parametrize("prompt,operation,code,answer,expected", [
    ("Bitte Mail-Dry-Run prüfen.", "mail.dry-run", 0, SUCCESS, True),
    ("Führe den Mail-Dry-Run für mein Postfach durch.", "mail.dry-run", 0, SUCCESS, True),
    ("Bitte mach einen Dry-Run für meine Mails.", "mail.dry-run", 0, SUCCESS, True),
    ("Führe einen Dry-Run für das Postfach durch.", "mail.dry-run", 0, SUCCESS, True),
    ("Bitte Mail-Dry-Run prüfen.", "mail.dry-run", 124,
     "Der Mail-Dry-Run ist fehlgeschlagen oder unvollständig (Fehler: timeout).", True),
    ("Bitte Mail-Dry-Run prüfen.", "mail.recent", 0, SUCCESS, False),
    ("Bitte Mail-Dry-Run prüfen.", "mail.recent", 0, "Der Mail-Dry-Run ist beendet.", False),
    ("Bitte Mail-Dry-Run prüfen.", "mail.dry-run", 124, "Der Mail-Dry-Run hat die Prüfung bestanden.", False),
    ("Bitte Mail-Dry-Run prüfen.", "mail.dry-run", 124, SUCCESS, False),
    ("Bitte Mail-Dry-Run prüfen.", "mail.dry-run", 0, SUCCESS + " Die Mail wurde gesendet.", False),
    ("Bitte Mail-Dry-Run prüfen.", "mail.dry-run", 0, "Du hast Post von Alice.", False),
    ("Welche Mails habe ich bekommen?", "mail.dry-run", 0, "Du hast Post von Alice.", False),
    ("Suche nach einer Mail zum Dry-Run.", "mail.search", 0, "Die Suche lieferte einen Treffer.", True),
    ("Suche nach Mail-Dry-Run.", "mail.search", 0, "Die Suche lieferte einen Treffer.", True),
    ("Bitte Mail-Dry-Run prüfen und zeige die letzten Mails.", "mail.dry-run", 0, SUCCESS, False),
])
def test_dry_run_status_proof_is_operation_and_answer_bound_in_python_and_js(
    prompt, operation, code, answer, expected,
) -> None:
    script = r"""
import {makeEvidence,routePrompt,guardAnswer} from './docker/openclaw-personal-assistant-plugin/runtime.js';
import contract from './docker/openclaw-personal-assistant-plugin/generated-tools.json' with {type:'json'};
const input=INPUT;
const operation=contract.operations.find(row=>row.tool_id===input.operation);
// The real mail worker has no synthetic ok/complete fields.
const payload={processed:5,skipped:0,actions:[],
  errors:input.code?['mail-runtime-budget-exhausted']:[],classifier:{}};
const evidence=makeEvidence(operation,{returncode:input.code,error:input.code?'tool-timeout':null},
  payload,'turn','call');
const route=routePrompt(contract,input.prompt);
console.log(JSON.stringify({route,evidence,guard:guardAnswer(contract,route,input.answer,[evidence])}));
""".replace("INPUT", json.dumps(dict(prompt=prompt, operation=operation, code=code, answer=answer)))
    data = _node(script)
    route = route_intent(prompt)
    assert data["route"] == route
    result = guard_claims(route=route, answer=answer, evidence=[data["evidence"]])
    assert data["guard"] == result
    assert result["ok"] is expected


@pytest.mark.parametrize("code", [0, 124])
def test_final_delivery_uses_only_exact_executed_dry_run_status(code: int) -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if(invocation.argv[1]==='dry-run'){now+=901000;"
        f"return {{returncode:{code},error:{json.dumps('tool-timeout' if code else None)},"
        "stdout:JSON.stringify({processed:5,actions:[],errors:[]}),stderr:''};}",
    )
    data = _node(harness + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const prepared=await gate('run','mail.dry-run');
await execute('mail.dry-run',prepared.params);
const text='Prüfung bestanden; die Mail wurde gesendet.';
const revise=await hooks.get('before_agent_finalize')({runId:'run',lastAssistantMessage:text},{});
now+=121000;
const delivery=await reply('run',text);
console.log(JSON.stringify({revise:revise??null,delivery,calls:operationCalls()}));
""")
    assert data["revise"] is None
    expected = SUCCESS if code == 0 else (
        "Der Mail-Dry-Run ist fehlgeschlagen oder unvollständig (Fehler: timeout)."
    )
    assert data["delivery"]["payload"]["text"] == expected
    assert data["calls"] == 1


def test_read_context_hard_cap_and_active_invocation_admission_are_bounded() -> None:
    data = _node(r"""
import {createRunRetention} from './docker/openclaw-personal-assistant-plugin/runtime.js';
let now=0;
const retention=createRunRetention({now:()=>now});
retention.start('late'); now=899000;
retention.retainRead('late','call',960000,1800000);
now=1859000; retention.finishRead('late','call');
now=3599999;
const before={context:retention.has('late'),authorization:retention.canAuthorize('late')};
now=3600000;
const expired=retention.has('late');
const reservations=[];
for(let i=0;i<129;i++) {
  retention.start('run-'+i);
  reservations.push(retention.acquireInvocation('run-'+i,retention.generation('run-'+i)));
}
const restarted=retention.start('run-0');
console.log(JSON.stringify({before,expired,count:reservations.filter(Boolean).length,
  contextCount:retention.size(),restarted}));
""")
    assert data == {
        "before": {"context": True, "authorization": False}, "expired": False,
        "count": 128, "contextCount": 128, "restarted": False,
    }


def test_evicted_active_dry_run_cannot_reinitialize_its_turn_or_publish_old_evidence() -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if(invocation.argv[1]==='dry-run'){"
        "for(let i=0;i<128;i++) await begin('other-'+i,'Hallo','other-'+i);"
        "await begin('run','Bitte Mail-Dry-Run prüfen.');}",
    ).replace(
        "evidence:evidenceByRun.size,",
        "evidence:evidenceByRun.size,evidenceRows:[...evidenceByRun.values()].flat().length,",
    )
    data = _node(harness + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const prepared=await gate('run','mail.dry-run');
const oldResult=await execute('mail.dry-run',prepared.params);
const delivered=await reply('run','Der Mail-Dry-Run wurde erfolgreich abgeschlossen.');
const write=await gate('run','mail.compose-send',{draft_id:'synthetic'});
await begin('run','Welche Mails habe ich bekommen?');
await hooks.get('after_tool_call')({runId:'run',toolName:groupFor('mail.dry-run'),result:oldResult},{});
console.log(JSON.stringify({oldResult,delivered,write,stats:loaded.stats()}));
""")
    assert data["delivered"]["reason"] == "personal-assistant-turn-state-guard"
    assert data["write"]["blockReason"] == "turn-state-missing-or-expired"
    assert json.loads(data["oldResult"]["content"][0]["text"])["evidence"]["ok"] is False
    assert data["stats"]["evidenceRows"] == 0


def test_completion_grace_rejects_new_read_operations() -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if(invocation.argv[1]==='dry-run') now+=901000;",
    )
    data = _node(harness + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const dry=await gate('run','mail.dry-run');
const read=await gate('run','mail.recent',{},'read');
await execute('mail.dry-run',dry.params);
const gateAfter=await gate('run','mail.recent',{},'new-read');
const staleRead=await execute('mail.recent',read.params,'read');
console.log(JSON.stringify({gateAfter,staleRead,calls:operationCalls()}));
""")
    assert data["gateAfter"]["blockReason"] == "turn-state-missing-or-expired"
    assert json.loads(data["staleRead"]["content"][0]["text"])["result"]["executed"] is False
    assert data["calls"] == 1


@pytest.mark.parametrize("aborted", [False, True])
def test_after_tool_callback_cannot_admit_rejected_duplicate_as_new_outcome(aborted: bool) -> None:
    script = HOOK_HARNESS + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const prepared=await gate('run','mail.dry-run');
await execute('mail.dry-run',prepared.params);
const controller=new AbortController(); if(ABORTED) controller.abort();
const repeated=await factories.get(groupFor('mail.dry-run'))({})
  .execute('second',prepared.params,controller.signal);
await hooks.get('after_tool_call')({runId:'run',toolName:groupFor('mail.dry-run'),result:repeated},{});
const result=await reply('run','Der Mail-Dry-Run wurde erfolgreich abgeschlossen.');
console.log(JSON.stringify({result:result??null,calls:operationCalls()}));
"""
    data = _node(script.replace("ABORTED", json.dumps(aborted)))
    assert data["result"] is None
    assert data["calls"] == 1


def test_chat_abort_does_not_interrupt_an_already_approved_write() -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if(invocation.argv[1]==='compose-send'){"
        "globalThis.controller.abort();"
        "if(options?.signal?.aborted) return {returncode:130,stdout:'',error:'tool-aborted'};}",
    )
    data = _node(harness + r"""
await begin('run','Bitte den Mailentwurf senden.');
const prepared=await gate('run','mail.compose-send',{draft_id:'synthetic'});
prepared.requireApproval.onResolution('allow-once');
globalThis.controller=new AbortController();
const result=await factories.get(groupFor('mail.compose-send'))({})
  .execute('call',prepared.params,controller.signal);
console.log(JSON.stringify({result,calls:operationCalls()}));
""")
    result = json.loads(data["result"]["content"][0]["text"])
    assert data["calls"] == 1
    assert result["evidence"]["postcondition_verified"] is True


def test_changed_prompt_retires_original_authorization_instead_of_reusing_it() -> None:
    data = _node(HOOK_HARNESS + r"""
await begin('run','Bitte Portfolio: erstelle DE0000000001 Synthetic SYN XETR EUR');
await begin('run','Erkläre mir Portfolio im Allgemeinen.');
const result=await gate('run','portfolio.watchlist.add',{
  isin:'DE0000000001',name:'Synthetic',symbol:'SYN',mic:'XETR',currency:'EUR'
});
console.log(JSON.stringify({result,calls:operationCalls()}));
""")
    assert data["result"]["blockReason"] == "turn-state-missing-or-expired"
    assert data["calls"] == 0


def test_only_dry_run_uses_long_watchdog_and_sdk_abort_signal() -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push({...invocation, observedTimeout:limits.tool_timeout_seconds, "
        "observedSignal:options?.signal===globalThis.expectedSignal});",
    )
    data = _node(harness + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const controller = new AbortController();
globalThis.expectedSignal = controller.signal;
for (const operation of ['mail.status','mail.dry-run']) {
  const prepared = await gate('run',operation,{},operation);
  await factories.get(groupFor(operation))({}).execute(operation,prepared.params,controller.signal);
}
console.log(JSON.stringify({calls:invocations,limits:contract.limits}));
""")
    calls = {call["argv"][1]: call for call in data["calls"]}
    assert calls["status"]["observedTimeout"] == 120
    assert calls["dry-run"]["observedTimeout"] == 960
    assert calls["dry-run"]["observedSignal"] is True
    assert data["limits"]["approval_ttl_seconds"] == 180


def test_long_dry_run_keeps_evidence_but_cannot_extend_write_authorization() -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if (invocation.argv[1]==='dry-run') now+=901000;",
    )
    data = _node(harness + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const prepared=await gate('run','mail.dry-run');
const result=await execute('mail.dry-run',prepared.params);
const retained=loaded.stats();
now+=121000; // A healthy model response can exceed the old 120-second tail.
const delivered=await reply('run','Der Mail-Dry-Run wurde erfolgreich abgeschlossen.');
const write=await gate('run','mail.compose-send',{draft_id:'synthetic'});
const repeated=await execute('mail.dry-run',prepared.params,'second-call');
await hooks.get('after_tool_call')({runId:'run',toolName:groupFor('mail.dry-run'),result:repeated},{});
const afterRejected=await reply('run','Der Mail-Dry-Run wurde erfolgreich abgeschlossen.');
await begin('run','Bitte Mail-Dry-Run prüfen. ');
const afterReentry=await gate('run','mail.compose-send',{draft_id:'synthetic'});
now+=1679000;
const expired=await reply('run','Der Mail-Dry-Run ist beendet.');
console.log(JSON.stringify({result,retained,delivered:delivered??null,write,repeated,expired,
  afterRejected:afterRejected??null,afterReentry,
  calls:operationCalls()}));
""")
    assert data["retained"]["evidence"] == 1
    assert data["delivered"] is None
    assert data["afterRejected"] is None
    assert data["afterReentry"]["blockReason"] == "turn-state-missing-or-expired"
    assert data["write"]["blockReason"] == "turn-state-missing-or-expired"
    assert data["calls"] == 1
    repeated = json.loads(data["repeated"]["content"][0]["text"])
    assert repeated["result"]["executed"] is False
    assert data["expired"]["reason"] == "personal-assistant-turn-state-guard"


def test_retained_dry_run_revokes_pending_confirmation_at_original_deadline() -> None:
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if (invocation.argv[1]==='dry-run') now+=20000;",
    )
    data = _node(harness + r"""
await begin('run','Bitte Mail und Portfolio prüfen.');
now+=890000;
await gate('run','portfolio.watchlist.add',{
  isin:'DE0000000001',name:'Synthetic',symbol:'SYN',mic:'XETR',currency:'EUR'
});
const before=loaded.stats();
const prepared=await gate('run','mail.dry-run');
await execute('mail.dry-run',prepared.params);
const after=loaded.stats();
await begin('confirmation','JA');
const resumed=await gate('confirmation','portfolio.watchlist.add',{
  isin:'DE0000000001',name:'Synthetic',symbol:'SYN',mic:'XETR',currency:'EUR'
});
console.log(JSON.stringify({before,after,resumed}));
""")
    assert data["before"]["pending"] == 1
    assert data["after"]["evidence"] == 1
    assert data["after"]["pending"] == data["after"]["ledger"] == 0
    assert data["resumed"]["blockReason"] == "missing-or-stale-bound-confirmation"


@pytest.mark.parametrize("eviction", ["capacity", "deadline"])
def test_dry_run_result_cannot_resurrect_evicted_or_overdue_turn(eviction: str) -> None:
    change = (
        "for(let i=0;i<128;i++) await begin('other-'+i,'Hallo','other-'+i);"
        if eviction == "capacity" else "now+=2760000;"
    )
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        "invocations.push(invocation); if (invocation.argv[1]==='dry-run') {" + change + "}",
    )
    data = _node(harness + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const prepared=await gate('run','mail.dry-run');
await execute('mail.dry-run',prepared.params);
const result=await reply('run','Der Mail-Dry-Run ist beendet.');
console.log(JSON.stringify({result,retained:runRetention.has('run'),stats:loaded.stats()}));
""")
    assert data["retained"] is False
    assert data["result"]["reason"] == "personal-assistant-turn-state-guard"
    assert max(data["stats"].values()) <= 128


def test_preaborted_native_call_starts_no_child() -> None:
    data = _node(r"""
import {spawnJson} from './docker/openclaw-personal-assistant-plugin/runtime.js';
const controller=new AbortController(); controller.abort();
let calls=0;
const result=await spawnJson({executable:process.execPath,argv:['-e','process.exit(0)'],stdin:null},
  {tool_timeout_seconds:1},{cwd:process.cwd(),signal:controller.signal,
    runner:(...args)=>{calls++; throw new Error('preaborted-child-started');}})
  .catch(error=>({error:error.message}));
console.log(JSON.stringify({calls,result}));
""")
    assert data["calls"] == 0
    assert data["result"]["error"] == "tool-aborted"
    assert data["result"]["returncode"] == 130


def test_preaborted_plugin_call_returns_bound_cancellation_evidence() -> None:
    data = _node(HOOK_HARNESS + r"""
await begin('run','Bitte Mail-Dry-Run prüfen.');
const prepared=await gate('run','mail.dry-run');
const controller=new AbortController(); controller.abort();
const result=await factories.get(groupFor('mail.dry-run'))({})
  .execute('call',prepared.params,controller.signal);
console.log(JSON.stringify({result,calls:operationCalls()}));
""")
    result = json.loads(data["result"]["content"][0]["text"])
    assert data["calls"] == 0
    assert result["result"]["executed"] is False
    assert result["diagnostic"]["retry_allowed"] is False
    evidence = result["evidence"]
    assert evidence["error"] == "cancelled"
    assert evidence["ok"] is evidence["complete"] is evidence["postcondition_verified"] is False
    assert "positive-evidence" not in evidence["allowed_claims"]
    assert "write-success" not in evidence["allowed_claims"]
    schema_path = (
        Path(__file__).resolve().parents[1] / "docker/openclaw-personal-assistant-plugin/evidence.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    assert evidence["error"] in schema["properties"]["error"]["enum"]


@pytest.mark.parametrize("termination", ["timeout", "abort"])
def test_native_stop_closes_descendants_and_inherited_pipes(termination: str) -> None:
    # The descendant ignores TERM and holds the pipes after its parent exits.
    # Without process-group KILL the old runner waits for its six-second exit.
    data = _node(r"""
import {spawnJson,makeEvidence} from './docker/openclaw-personal-assistant-plugin/runtime.js';
import {spawn} from 'node:child_process';
const termination=TERMINATION;
const controller=new AbortController();
const grandchild="process.on('SIGTERM',()=>{});setTimeout(()=>{},6000);"+
  "require('node:fs').writeSync(1,'{\"ok\":true,\"complete\":true}')";
const parent=`const {spawn}=require('node:child_process');
  spawn(process.execPath,['-e',${JSON.stringify(grandchild)}],{stdio:'inherit'});setInterval(()=>{},1000);`;
const started=Date.now();
const result=await spawnJson({executable:process.execPath,argv:['-e',parent],stdin:null},
  {tool_timeout_seconds:termination==='timeout'?1:10,max_capture_bytes:1000,max_error_bytes:1000},
  {cwd:process.cwd(),signal:controller.signal,runner:(...args)=>{
    const child=spawn(...args);
    if(termination==='abort') child.stdout.on('data',()=>controller.abort());
    return child;
  }});
const evidence=makeEvidence({tool_id:'mail.dry-run',domain:'mail',mode:'read',approval:'none'},
  result,JSON.parse(result.stdout),'turn','call');
console.log(JSON.stringify({elapsed:Date.now()-started,result,evidence}));
""".replace("TERMINATION", json.dumps(termination)))
    assert data["elapsed"] < 4500
    assert data["result"]["returncode"] == (124 if termination == "timeout" else 130)
    assert data["result"]["error"] == ("tool-timeout" if termination == "timeout" else "tool-aborted")
    assert data["evidence"]["ok"] is data["evidence"]["complete"] is False
    assert "positive-evidence" not in data["evidence"]["allowed_claims"]
