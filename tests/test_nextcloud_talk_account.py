"""Behavioral tests for the account Talk channel, without real service access."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from personal_assistant.nextcloud_talk_account import (
    TalkAccountError,
    TalkAccountWorker,
    TalkInbox,
    account_identity,
)


def message(identifier=2, actor="jan", text="Welche Termine habe ich?"):
    return {
        "id": identifier,
        "actorType": "users",
        "actorId": actor,
        "message": text,
        "messageType": "comment",
        "messageParameters": {},
        "timestamp": 10,
    }


@pytest.fixture
def connection():
    return {
        "schema_version": 1,
        "revision": "one",
        "server_url": "https://cloud.example/nc",
        "login_name": "agent",
        "app_password": "never-print-this-secret",
        "user_id": "agent",
        "expected_ips": ["192.168.12.30"],
        "discovery_snapshot": {"capabilities": {"spreed": {"features": ["chat-reference-id"]}}},
        "talk": {"enabled": True, "room_tokens": ["roomA"], "allowed_user_ids": ["jan", "agent"]},
    }


@pytest.fixture
def inbox(tmp_path):
    result = TalkInbox(tmp_path / "talk.sqlite3")
    yield result
    result.close()


class Response:
    def __init__(self, data, status=200):
        self.status = status
        self.headers = {}
        self.payload = {"ocs": {"meta": {"statuscode": status}, "data": data}}

    def json(self):
        return self.payload


class Transport:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if method == "POST" and response.status == 201:
            response.payload["ocs"]["data"]["referenceId"] = parse_qs(kwargs["data"].decode())["referenceId"][
                0
            ]
        return response


def worker(inbox, connection, responses=()):
    transport = Transport(responses)
    instance = TalkAccountWorker(inbox, lambda: copy.deepcopy(connection), lambda *a, **kw: transport)
    return instance, transport


def send_request(claim, **kwargs):
    return {
        "account": claim["account"],
        "room": claim["room"],
        "inbound_id": claim["id"],
        "sequence": 0,
        "text": "Dein Kalender ist verbunden.",
        **kwargs,
    }


def test_first_pairing_skips_all_existing_history(inbox, connection):
    instance, transport = worker(inbox, connection, [Response([message(99)]), Response([message(100)])])
    first = instance.poll()
    assert first["message"] is None
    assert first["status"]["connected"] is True
    assert inbox.cursor(account_identity(connection), "roomA") == 99
    result = instance.poll()["message"]
    assert result["id"] == 100
    assert "lookIntoFuture=1" in transport.calls[1][1]
    assert "lastKnownMessageId=99" in transport.calls[1][1]
    assert "setReadMarker=0" in transport.calls[1][1]
    assert transport.calls[1][1].startswith("ocs/")  # Preserve /nc installation base.


def test_durable_acceptance_precedes_cursor_publication_and_restart(tmp_path, connection):
    path = tmp_path / "durable.sqlite3"
    inbox = TalkInbox(path)
    account = account_identity(connection)
    inbox.initialize(account, "roomA", 1)
    inbox.accept(account, "roomA", 3, [message(2), message(3)])
    claim = inbox.claim(account, ["roomA"], ["jan"])
    assert claim["id"] == 2
    inbox.close()  # Simulate failure during agent processing, before finish.
    inbox = TalkInbox(path)
    assert inbox.cursor(account, "roomA") == 3
    assert inbox.counts(account)["uncertain"] == 1
    assert inbox.claim(account, ["roomA"], ["jan"])["id"] == 3
    assert inbox.claim(account, ["roomA"], ["jan"]) is None
    inbox.close()


def test_unknown_sender_room_media_and_duplicates_cannot_trigger_turn(inbox, connection):
    media = {**message(4), "messageParameters": {"file": {"type": "file", "id": "12"}}}
    instance, _ = worker(
        inbox,
        connection,
        [
            Response([]),
            Response(
                [
                    message(1, "outsider"),
                    {**message(2), "actorType": "guests"},
                    {**message(3), "systemMessage": "user_added"},
                    media,
                    message(5),
                    message(5),
                ]
            ),
        ],
    )
    instance.poll()
    result = instance.poll()["message"]
    assert result["id"] == 5
    assert inbox.counts(instance.account)["processing"] == 1
    assert inbox.counts(instance.account)["accepted"] == 0
    assert inbox.cursor(instance.account, "roomA") == 5
    with pytest.raises(TalkAccountError, match="talk-send-invalid"):
        instance.send(send_request(result, room="notAllowed"))


def test_same_account_mobile_input_accepted_and_bot_echo_suppressed(inbox, connection):
    instance, transport = worker(inbox, connection, [Response([]), Response([message(2, "agent")])])
    instance.poll()
    claim = instance.poll()["message"]
    assert claim["sender_id"] == "agent"
    transport.responses.append(Response({"id": 3}, 201))
    assert instance.send(send_request(claim))["message_id"] == 3
    reference = parse_qs(transport.calls[-1][2]["data"].decode())["referenceId"][0]
    assert len(reference) == 64
    transport.responses.append(
        Response(
            [
                {**message(3, "agent"), "referenceId": reference},
                message(4, "agent", "Danke"),
            ]
        )
    )
    instance.command({"operation": "finish", **send_request(claim), "success": True})
    assert instance.poll()["message"]["id"] == 4
    with pytest.raises(TalkAccountError, match="active-message"):
        instance.send(send_request(claim))


def test_uncertain_send_is_never_retried_and_reference_still_suppresses_echo(inbox, connection):
    instance, transport = worker(
        inbox,
        connection,
        [Response([]), Response([message(2)]), TimeoutError("password=never-print-this-secret")],
    )
    instance.poll()
    claim = instance.poll()["message"]
    with pytest.raises(TalkAccountError, match="^talk-delivery-uncertain$"):
        instance.send(send_request(claim))
    assert inbox.counts(instance.account)["delivery_uncertain"] == 1
    with pytest.raises(TalkAccountError, match="already-attempted"):
        instance.send(send_request(claim))
    assert len(transport.calls) == 3
    reference = parse_qs(transport.calls[-1][2]["data"].decode())["referenceId"][0]
    assert inbox.is_own(instance.account, "roomA", {"id": 3, "referenceId": reference})


def test_account_replacement_cannot_deliver_old_response(inbox, connection):
    instance, _ = worker(inbox, connection, [Response([]), Response([message(2)])])
    instance.poll()
    claim = instance.poll()["message"]
    connection["revision"] = "two"
    connection["user_id"] = "different-agent"
    with pytest.raises(TalkAccountError, match="talk-send-invalid"):
        instance.send(send_request(claim))
    instance.command({"operation": "finish", **send_request(claim), "success": False})
    assert inbox.counts(claim["account"])["uncertain"] == 1


def test_disabled_and_unpaired_channel_makes_no_network_request(inbox, connection):
    instance, transport = worker(inbox, connection)
    connection["talk"]["enabled"] = False
    assert instance.poll()["status"]["enabled"] is False
    assert not transport.calls
    connection["talk"]["enabled"] = True
    connection["talk"]["allowed_user_ids"] = []
    with pytest.raises(TalkAccountError, match="pairing-required"):
        instance.poll()
    assert not transport.calls


def test_missing_reference_capability_fails_before_poll(inbox, connection):
    connection["discovery_snapshot"]["capabilities"]["spreed"]["features"] = []
    instance, transport = worker(inbox, connection)
    with pytest.raises(TalkAccountError, match="reference-id-required"):
        instance.poll()
    assert not transport.calls


def test_sender_revocation_stops_pending_reply(inbox, connection):
    instance, transport = worker(inbox, connection, [Response([]), Response([message(2)])])
    instance.poll()
    claim = instance.poll()["message"]
    connection["talk"]["allowed_user_ids"] = ["agent"]
    with pytest.raises(TalkAccountError, match="sender-revoked"):
        instance.send(send_request(claim))
    assert len(transport.calls) == 2


def test_revocation_before_claim_drops_queued_input(inbox, connection):
    account = account_identity(connection)
    inbox.initialize(account, "roomA", 0)
    inbox.accept(account, "roomA", 1, [message(1)])
    assert inbox.claim(account, ["roomA"], ["other"]) is None
    assert inbox.counts(account)["revoked"] == 1


def test_only_one_gateway_writer_owns_inbox(tmp_path):
    path = tmp_path / "one.sqlite3"
    first = TalkInbox(path)
    with pytest.raises(TalkAccountError, match="writer-already-active"):
        TalkInbox(path)
    first.close()
    second = TalkInbox(path)
    second.close()


def test_crash_between_send_and_readback_retains_own_reference(tmp_path, connection):
    path = tmp_path / "interrupted.sqlite3"
    account = account_identity(connection)
    inbox = TalkInbox(path)
    inbox.initialize(account, "roomA", 0)
    inbox.accept(account, "roomA", 1, [message(1)])
    inbox.claim(account, ["roomA"], ["jan"])
    reference = inbox.reserve_send(account, "roomA", 1, 0, "Antwort")
    inbox.close()
    inbox = TalkInbox(path)
    assert inbox.counts(account)["delivery_uncertain"] == 1
    assert inbox.is_own(account, "roomA", {"id": 2, "referenceId": reference})
    assert inbox.claim(account, ["roomA"], ["jan"]) is None
    with pytest.raises(TalkAccountError, match="without-active-message"):
        inbox.reserve_send(account, "roomA", 1, 0, "Antwort")
    inbox.close()


def test_invalid_batch_does_not_advance_cursor(inbox, connection):
    instance, _ = worker(inbox, connection, [Response([]), Response([message(2), {"id": "bad"}])])
    instance.poll()
    with pytest.raises(TalkAccountError, match="message-id-invalid"):
        instance.poll()
    assert inbox.cursor(instance.account, "roomA") == 0


def test_native_adapter_routes_existing_agent_and_retains_approval_boundary():
    root = Path(__file__).resolve().parents[1]
    script = r"""
import assert from 'node:assert/strict';
import {dispatchTalkMessage,createNextcloudAccountTalkChannel,CHANNEL_ID}
  from './docker/openclaw-personal-assistant-plugin/nextcloud-account-talk.js';
const calls=[];
const worker={request:async(op,args)=>{calls.push({op,args});return {};}};
const ctx={cfg:{},getStatus:()=>({}),setStatus:()=>{}};
const core={
 routing:{resolveAgentRoute:(p)=>{
   assert.equal(p.channel,CHANNEL_ID);return {sessionKey:'existing-agent-session',agentId:'main'};
 }},
 session:{resolveStorePath:()=>'/isolated/sessions',recordInboundSession:()=>{}},
 text:{chunkMarkdownText:(text)=>[text]},
 reply:{finalizeInboundContext:(p)=>p,dispatchReplyWithBufferedBlockDispatcher:()=>{}},
 inbound:{dispatchReply:async(p)=>{
   assert.equal(p.agentId,'main');assert.equal(p.ctxPayload.CommandAuthorized,false);
   assert.equal(p.ctxPayload.SenderId,'jan');assert.equal(p.ctxPayload.BodyForAgent,'Bitte Kalender');
   await p.delivery.deliver({text:'Bereit'});
 }},
};
const message={account:'a'.repeat(64),room:'roomA',id:2,sender_id:'jan',text:'Bitte Kalender',timestamp:1};
await dispatchTalkMessage({message,ctx,core,worker});
assert.deepEqual(calls.map(c=>c.op),['send','finish']);
assert.equal(calls[0].args.account,message.account);assert.equal(calls[1].args.success,true);
const channel=createNextcloudAccountTalkChannel({channel:core});
assert.equal(channel.capabilities.media,false);assert.equal(channel.capabilities.nativeCommands,false);
calls.length=0;
core.inbound.dispatchReply=async()=>{throw Error('private credential must never escape');};
await assert.rejects(dispatchTalkMessage({message,ctx,core,worker}),/^Error: talk-dispatch-uncertain$/);
assert.equal(calls[0].op,'finish');assert.equal(calls[0].args.success,false);
calls.length=0;
ctx.cfg.channels={'nextcloud-talk':{enabled:true,rooms:{roomA:{enabled:true}}}};
await assert.rejects(dispatchTalkMessage({message,ctx,core,worker}),/talk-webhook-owner-conflict/);
assert.equal(calls[0].op,'finish');assert.equal(calls[0].args.success,false);
const abort = new AbortController();
let observed;
const managed = createNextcloudAccountTalkChannel({channel:core}, {createWorker:()=>({
  request:async()=>({status:{enabled:true,connected:true},message:null}),close:()=>{},
})});
assert.equal(managed.config.describeAccount().configured,false);
assert.equal(managed.status.buildAccountSnapshot({runtime:{configured:true}}).configured,false);
await managed.gateway.startAccount({...ctx,abortSignal:abort.signal,setStatus:(snapshot)=>{
  if(snapshot.connected){
    observed=managed.config.describeAccount().configured;abort.abort();
  }
}});
assert.equal(observed,true);
assert.equal(managed.config.describeAccount().configured,true);
assert.equal(managed.status.buildAccountSnapshot({runtime:{connected:true}}).connected,false);
console.log('native-channel-contract-ok');
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native-channel-contract-ok"


def test_inbox_schema_failure_preserves_existing_data(tmp_path):
    import sqlite3

    path = tmp_path / "future.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version=99")
    db.execute("CREATE TABLE protected (value TEXT)")
    db.execute("INSERT INTO protected VALUES ('audit-history')")
    db.commit()
    db.close()
    with pytest.raises(TalkAccountError, match="schema-incompatible"):
        TalkInbox(path)
    db = sqlite3.connect(path)
    assert db.execute("SELECT value FROM protected").fetchone()[0] == "audit-history"
    db.close()


def test_real_worker_protocol_never_exposes_stored_credentials(tmp_path, connection):
    from personal_assistant.nextcloud_connection import ConnectionStore

    root = Path(__file__).resolve().parents[1]
    connection["discovery_snapshot"]["user_id"] = connection["user_id"]
    connection["talk"]["enabled"] = False
    store = ConnectionStore(tmp_path / "account")
    store.save(connection, expected_revision="")
    environment = dict(os.environ)
    environment.update(
        OPENCLAW_NEXTCLOUD_CONNECTION_DIR=str(tmp_path / "account"),
        OPENCLAW_CORE_DATA_DIR=str(tmp_path / "state"),
    )
    result = subprocess.run(
        [sys.executable, "-m", "personal_assistant.nextcloud_talk_account", "worker"],
        cwd=root,
        env=environment,
        input='{"request_id":1,"operation":"status"}\n{"request_id":2,"operation":"bad"}\n',
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[0]["result"]["enabled"] is False
    assert responses[1]["code"] == "talk-operation-unknown"
    assert connection["app_password"] not in result.stdout + result.stderr
    assert "Authorization" not in result.stdout + result.stderr
