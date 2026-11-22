"""The native approval and CLI adapter use only the current verified public binding."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_agent_execution_reliability import HOOK_HARNESS, _node
from test_nextcloud_access import grant, managed, measured, scope, update_policy

from personal_assistant import nextcloud_connection as nc

ROOT = Path(__file__).resolve().parents[1]

# Only substitute the role mount/interpreter working directory. At the domain
# boundary a harmless actual child returns the two reserved environment fields.
HARNESS = (
    HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        """invocations.push(invocation);
    if (invocation.argv.join(' ') !== 'tools list') {
      const observed = path.join(fixtureRoot, 'child-environment.json');
      fs.rmSync(observed,{force:true});
      const child = await runtime.spawnJson({executable:process.execPath,argv:['-e',
        "require('node:fs').writeFileSync(process.argv[1],JSON.stringify({"
        +"received:process.env.OPENCLAW_NC_EXPECTED_ACCESS??null,"
        +"native:process.env.OPENCLAW_NC_NATIVE_CALL??null,ok:true}))",observed],stdin:null},
        limits,{...options,cwd:fixtureRoot});
      return {...child,stdout:fs.readFileSync(observed,'utf8')};
    }""",
    )
    + r"""
const first = {mode:'active',binding:{account_id:'a',owner_user_id:'owner',
  generation:'g',policy_revision:'r'}};
const setAccess=(value)=>{readerResult={returncode:0,stdout:JSON.stringify(value),stderr:''};};
setAccess(first);
const op='nextcloud.tasks.update', args={uid:'x',expected_title:'Test'};
const issue=async (extra={})=>{
  await begin('run','Bitte Aufgabe Test abschließen');
  return hooks.get('before_tool_call')({runId:'run',toolCallId:'call',toolName:groupFor(op),
    params:{operation:op,arguments:args,...extra}},{runId:'run',sessionKey:'session'});
};
const body=(response)=>JSON.parse(response.content[0].text);
"""
)


@pytest.mark.parametrize(
    "change", ["policy", "account", "during-discovery", "activation", "invalid-store", "foreign-session"]
)
def test_stale_native_approval_cannot_reach_domain_child(change):
    got = _node(
        HARNESS
        + """
if (CHANGE==='activation') setAccess({mode:'legacy',binding:null});
const approved=await issue();
approved.requireApproval.onResolution('allow-once');
const changed={mode:'active',binding:{...first.binding,policy_revision:'r2'}};
if (CHANGE==='account') changed.binding={
  ...first.binding,account_id:'b',owner_user_id:'other',generation:'g2'};
if (CHANGE==='during-discovery') afterCatalog=()=>setAccess(changed);
else if (CHANGE==='invalid-store') readerResult={returncode:1,stdout:'',stderr:'private-error'};
else if (CHANGE!=='foreign-session') setAccess(changed);
const ctx=CHANGE==='foreign-session'?{sessionKey:'other'}:{};
const result=body(await execute(op,approved.params,'call',ctx));
console.log(JSON.stringify({result,calls:operationCalls()}));
""".replace("CHANGE", json.dumps(change))
    )
    assert got["calls"] == 0
    assert got["result"]["result"]["executed"] is False
    assert "private-error" not in json.dumps(got)


@pytest.mark.parametrize(
    "bad",
    [
        {"mode": "made-up", "binding": None},
        {"mode": "active", "binding": {}},
        {"mode": "legacy", "binding": None, "connection": "private"},
    ],
)
def test_invalid_public_read_blocks_before_display(bad):
    got = _node(
        HARNESS
        + f"setAccess({json.dumps(bad)});"
        + "console.log(JSON.stringify({gate:await issue(),stats:loaded.stats()}));"
    )
    assert got["gate"] == {"block": True, "blockReason": "policy-integrity-error"}
    assert got["stats"]["ledger"] == 0


def test_confirmed_pending_action_keeps_original_access():
    got = _node(
        HARNESS
        + """
const create='nextcloud.tasks.create', fields={title:'Test',description:'Test',due:'2026-12-29',priority:0};
await begin('request','Bitte Aufgabe Test erstellen');
await gate('request',create,fields,'pending');
setAccess({mode:'active',binding:{...first.binding,policy_revision:'replacement'}});
await begin('confirm','JA');
const result=await gate('confirm',create,fields,'confirmed');
console.log(JSON.stringify({result,calls:operationCalls()}));
"""
    )
    assert got["result"]["blockReason"] == "missing-or-stale-bound-confirmation"
    assert got["calls"] == 0


def test_child_receives_only_sealed_binding_and_static_status_ignores_it():
    got = _node(
        HARNESS
        + """
process.env.OPENCLAW_NC_EXPECTED_ACCESS='forged'; process.env.OPENCLAW_NC_NATIVE_CALL='forged';
const approved=await issue({__nextcloud_access:{mode:'legacy',binding:null}});
approved.requireApproval.onResolution('allow-once');
setAccess(structuredClone(first)); // Connection-only refresh has identical public authority.
const applied=body(await execute(op,approved.params));
readerResult={returncode:1,stdout:'',stderr:'private-error'};
await begin('static','Status');
const staticOp='assistant.agent-tools.status', ready=await gate('static',staticOp);
const status=body(await execute(staticOp,ready.params,'call',{runId:'static'}));
console.log(JSON.stringify({approved:approved.params,applied,status,calls:operationCalls()}));
"""
    )
    assert "__nextcloud_access" not in got["approved"]
    assert "owner" not in json.dumps(got["approved"])
    assert got["applied"]["result"]["native"] == "1"
    assert json.loads(got["applied"]["result"]["received"]) == {
        "mode": "active",
        "binding": {"account_id": "a", "owner_user_id": "owner", "generation": "g", "policy_revision": "r"},
    }
    assert got["status"]["result"]["native"] is None and got["status"]["result"]["received"] is None
    assert got["calls"] == 2


def reader(directory):
    return subprocess.run(
        [sys.executable, "-P", "-s", "-m", "personal_assistant.nextcloud_native_binding"],
        cwd=ROOT,
        env={
            "PYTHONPATH": str(ROOT),
            "OPENCLAW_NEXTCLOUD_CONNECTION_DIR": str(directory),
            "OPENCLAW_NC_MODE_REQUIRED": "1",
        },
        capture_output=True,
        text=True,
        timeout=8,
    )


def test_private_reader_only_emits_public_values_and_requires_intact_store(tmp_path):
    store = managed(tmp_path)
    assert json.loads(reader(store.directory).stdout) == {"mode": "unconfigured", "binding": None}
    conn = measured()
    store.save(conn, expected_revision="")
    before = store.snapshot()
    assert json.loads(reader(store.directory).stdout) == {
        "mode": "active",
        "binding": before.binding.to_dict(),
    }
    old = reader(store.directory).stdout
    store.save(conn, expected_revision=before.connection["revision"])
    assert reader(store.directory).stdout == old
    update_policy(store, [grant(scope(conn), ["list"])])
    assert reader(store.directory).stdout != old
    (store.directory / "mode.json").unlink()
    result = reader(store.directory)
    assert result.returncode != 0 and not result.stdout
    assert result.stderr.strip() == "policy-integrity-error"


def test_selected_native_binding_must_match_again_on_python_admission(tmp_path, monkeypatch):
    from personal_assistant.nextcloud_native_binding import verify_native_access

    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    snapshot = store.snapshot()
    verify_native_access(snapshot)
    monkeypatch.setenv("OPENCLAW_NC_NATIVE_CALL", "1")
    with pytest.raises(nc.ConnectionError, match="scope-unbound"):
        verify_native_access(snapshot)
    for malformed in ("{}", "[]", "null", '{"mode":"legacy","mode":"legacy","binding":null}', " " * 9000):
        monkeypatch.setenv("OPENCLAW_NC_EXPECTED_ACCESS", malformed)
        with pytest.raises(nc.ConnectionError, match="scope-invalid"):
            verify_native_access(snapshot)
    monkeypatch.setenv("OPENCLAW_NC_EXPECTED_ACCESS", reader(store.directory).stdout.strip())
    verify_native_access(snapshot)
    updated = update_policy(store, [grant(scope(conn), ["list"])])
    with pytest.raises(nc.ConnectionError, match="policy-changed"):
        verify_native_access(updated)


@pytest.mark.parametrize(
    ("operation", "args", "guarded"),
    [
        ("portfolio.import.csv.nextcloud", {"nextcloud_path": "Portfolio/Beispiel.csv"}, True),
        ("mail.invoice-archive", {}, True),
        ("mail.status", {}, True),
        ("mail.doctor", {}, True),
        ("assistant.search", {"query": "Beispiel"}, True),
        ("assistant.invoices.files", {}, True),
        ("assistant.invoices.reprocess-apply", {"hash": "a" * 64, "expected_preview_sha256": "b" * 64}, True),
        ("mail.review.suggest", {"folder": "Test", "message_id": "7", "expected_subject": "Beispiel"}, True),
        ("assistant.invoices.audit", {}, False),
        ("assistant.invoices.export", {"year": 2026}, False),
        ("nextcloud.deck.orders.list", {}, False),
        ("portfolio.holdings", {}, False),
        ("mail.recent", {}, False),
    ],
)
def test_catalog_binding_follows_direct_and_indirect_sources(operation, args, guarded):
    got = _node(
        HARNESS
        + """
readerResult={returncode:1,stdout:'',stderr:'fixture unavailable'};
await begin('run','Status');
const operation=OPERATION;
const prepared=await gate('run',operation,ARGS);
const result=prepared.block ? {error:prepared.blockReason} : body(await execute(operation,prepared.params));
const error=result.error??result.diagnostic?.category;
console.log(JSON.stringify({error,calls:operationCalls(),readerCalls}));
""".replace("OPERATION", json.dumps(operation)).replace("ARGS", json.dumps(args))
    )
    if guarded:
        assert got == {"error": "policy-integrity-error", "calls": 0, "readerCalls": 1}
    else:
        assert got.get("error") is None and got["calls"] == 1 and got["readerCalls"] == 0


def test_marked_native_call_rejects_unverified_legacy_mode(monkeypatch):
    from personal_assistant.nextcloud_access import AccessSnapshot
    from personal_assistant.nextcloud_native_binding import verify_native_access

    monkeypatch.setenv("OPENCLAW_NC_NATIVE_CALL", "1")
    monkeypatch.setenv("OPENCLAW_NC_EXPECTED_ACCESS", '{"mode":"legacy","binding":null}')
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        verify_native_access(AccessSnapshot("legacy", managed=False))


@pytest.mark.parametrize("actors", [("one", "two"), ("one", ""), ("", ""), ("one", "one")])
def test_shared_session_short_confirmation_requires_same_known_sender(actors):
    got = _node(
        HARNESS
        + """
const [original,confirming]=ACTORS, sessionKey='agent:main:nextcloud-account-talk:group:fixture';
const create='nextcloud.tasks.create';
const fields={title:'Private fixture',description:'Example',due:'2026-12-29',priority:0};
const prompt=(runId,prompt,requesterSenderId)=>hooks.get('before_prompt_build')({runId,prompt,sessionKey},
  {runId,sessionKey,requesterSenderId});
const submit=(runId,requesterSenderId)=>hooks.get('before_tool_call')({runId,toolName:groupFor(create),
  toolCallId:'call',params:{operation:create,arguments:fields}},{runId,sessionKey,requesterSenderId});
await prompt('request','Bitte Aufgabe erstellen',original);
await submit('request',original);
const resume=await prompt('confirmation','JA',confirming);
const result=await submit('confirmation',confirming);
console.log(JSON.stringify({result,resume}));
""".replace("ACTORS", json.dumps(actors))
    )
    if actors == ("one", "one"):
        assert "params" in got["result"] and "requireApproval" not in got["result"]
        assert "Private fixture" in json.dumps(got["resume"])
    else:
        assert got["result"]["blockReason"] == "missing-or-stale-bound-confirmation"
        assert "Private fixture" not in json.dumps(got.get("resume"))


def test_frozen_dialog_can_resume_when_only_factory_exposes_actor():
    got = _node(
        HARNESS
        + """
const issued=await issue();
issued.requireApproval.onResolution('allow-once');
const response=body(await execute(op,issued.params,'call',{sessionKey:'session',requesterSenderId:'one'}));
console.log(JSON.stringify({response,calls:operationCalls()}));
"""
    )
    assert got["calls"] == 1
    assert got["response"]["result"]["native"] == "1"


def test_shared_context_only_on_prompt_cannot_downgrade_to_personal_confirmation():
    got = _node(
        HARNESS
        + """
const create='nextcloud.tasks.create';
const fields={title:'Private fixture',description:'Example',due:'2026-12-29',priority:0};
await hooks.get('before_prompt_build')({runId:'request',prompt:'Bitte Aufgabe erstellen',sessionKey:'space'},
  {runId:'request',sessionKey:'space',chatType:'group'});
const issued=await gate('request',create,fields,'first','space');
await begin('new','JA','space');
const result=await gate('new',create,fields,'new-call','space');
console.log(JSON.stringify({dialog:Boolean(issued.requireApproval),result}));
"""
    )
    assert got["dialog"]
    assert got["result"]["blockReason"] == "missing-or-stale-bound-confirmation"


def test_affected_call_checks_live_tool_again_despite_warm_catalog():
    harness = HOOK_HARNESS.replace(
        "invocations.push(invocation);",
        """invocations.push(invocation);
        if (invocation.argv.join(' ')==='tools list' && catalogDisabled) {
          return {returncode:0,stdout:'[]',stderr:''};
        }""",
    )
    got = _node(
        harness
        + """
let catalogDisabled=false;
const op='nextcloud.tasks.list';
await begin('warm','Status');
const primed=await gate('warm',op);
await execute(op,primed.params);
catalogDisabled=true;
await begin('second','Status');
const next=await gate('second',op);
const result=JSON.parse((await execute(op,next.params)).content[0].text);
console.log(JSON.stringify({result,calls:operationCalls()}));
"""
    )
    assert got["calls"] == 1
    assert got["result"]["diagnostic"]["category"] == "operation-disabled-in-live-capabilities"


def test_deck_order_list_dispatches_only_to_local_order_history(tmp_path):
    from argparse import Namespace
    from unittest.mock import Mock

    from personal_assistant.cli_handlers.orders import handle
    from personal_assistant.connectors.nextcloud.deck import NextcloudDeck
    from personal_assistant.orders import OrderDeckService, OrderEvent, OrderStore
    from personal_assistant.services.orders import OrderApplicationMixin
    from personal_assistant.tool_settings import DeckOrdersToolSettings

    store = OrderStore(tmp_path / "orders.sqlite3")
    deck = Mock(spec=NextcloudDeck)
    app = OrderApplicationMixin()
    app.order_service = OrderDeckService(DeckOrdersToolSettings(), None, None, None, deck, store=store)
    try:
        store.upsert_event(
            OrderEvent("order_confirmation", 1.0, merchant="Fixture", order_number="A1"),
            stable_key="fixture",
            subject="",
            sender="",
        )
        result = []
        args = Namespace(command="orders", orders_command="list", status="", limit=100)
        assert handle(args, app, result.append) == 0
        assert [row["order_number"] for row in result[0]["orders"]] == ["A1"]
        assert not deck.mock_calls and "live" not in result[0]
    finally:
        store.close()
