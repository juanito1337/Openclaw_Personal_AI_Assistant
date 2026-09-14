#!/usr/bin/env bash
set -euo pipefail
umask 077

root=$(CDPATH='' cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
image=${OPENCLAW_M15_RUNTIME_IMAGE:-${1:-}}
[[ -n "$image" ]] || {
  echo "OPENCLAW_M15_RUNTIME_IMAGE oder Runtime-Image als Argument angeben." >&2
  exit 2
}
command -v docker >/dev/null 2>&1 || {
  echo "Docker fehlt." >&2
  exit 2
}
docker image inspect "$image" >/dev/null

fake_assistant="$root/tests/fixtures/m15/fake-assistant.sh"
docker run --rm --interactive --network none --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
  --mount "type=bind,src=$fake_assistant,dst=/opt/openclaw-agent/scripts/assistant.sh,readonly" \
  --entrypoint node \
  "$image" --input-type=module - <<'JS'
import plugin, {testing} from "/opt/openclaw-plugins/personal-assistant-tools/index.js";
import {spawnJson, shouldBlockGenericTool} from "/opt/openclaw-plugins/personal-assistant-tools/runtime.js";

const factories = [];
const hooks = new Map();
plugin.register({
  registerTool(factory, options) { factories.push({factory, options}); },
  on(name, handler) { hooks.set(name, handler); },
  logger: {warn() {}, info() {}, debug() {}},
});

function assert(value, message) {
  if (!value) throw new Error(message);
}
function nativeTool(name, runId) {
  const registration = factories.find(({options}) => options.name === name);
  assert(registration, `missing native tool: ${name}`);
  return registration.factory({runId, sessionId:`session-${runId}`});
}
async function invoke(runId, name, operation, args, callId) {
  const params = {operation, arguments:args};
  const before = await hooks.get("before_tool_call")(
    {toolName:name, params, toolCallId:callId}, {runId},
  );
  assert(before?.block !== true, `tool blocked: ${operation}: ${before?.blockReason}`);
  if (operation === "nextcloud.calendar.from-mail-create") {
    assert(before?.requireApproval?.allowedDecisions?.join(",") === "allow-once,deny", "write approval is not one-shot");
    assert(before?.requireApproval?.severity === "critical", "external write approval is not critical");
  }
  const result = await nativeTool(name, runId).execute(callId, before?.params ?? params);
  const payload = JSON.parse(result.content[0].text);
  await hooks.get("after_tool_call")({toolName:name, result, toolCallId:callId}, {runId});
  return {before, result, payload};
}
async function prepare(runId, messageId="synthetic-42", subject="Synthetic flights") {
  await hooks.get("before_prompt_build")(
    {prompt:"Trage Hinflug und Rueckflug aus der Mail als Termine in meinen Kalender ein.", messages:[]},
    {runId, sessionId:`session-${runId}`},
  );
  await invoke(runId, "personal_assistant_mail_read", "mail.search", {query:"Synthetic"}, `${runId}-search`);
  await invoke(runId, "personal_assistant_mail_read", "mail.read", {
    folder:"Agent/Test", message_id:messageId, expected_subject:subject,
  }, `${runId}-read`);
  return await invoke(runId, "personal_assistant_calendar_read", "nextcloud.calendar.from-mail-preview", {
    folder:"Agent/Test", message_id:messageId, expected_subject:subject,
  }, `${runId}-preview`);
}

const successRun = "m15-success";
const preview = await prepare(successRun);
assert(preview.payload.result.decision === "ready", "preview is not ready");
const writeBase = {
  folder:"Agent/Test", message_id:"synthetic-42", expected_subject:"Synthetic flights",
  preview_digest:"a".repeat(64),
};
const first = await invoke(successRun, "personal_assistant_calendar_write", "nextcloud.calendar.from-mail-create", {
  ...writeBase, candidate_id:"outbound",
}, `${successRun}-write-1`);
assert(first.payload.evidence.postcondition_verified === true, "first read-back is missing");
const revision = await hooks.get("before_agent_finalize")(
  {runId:successRun, lastAssistantMessage:"Ich werde den Rueckflug gleich eintragen."}, {runId:successRun},
);
assert(revision?.action === "revise" && revision?.retry?.maxAttempts === 1, "partial promise did not trigger one revision");
const second = await invoke(successRun, "personal_assistant_calendar_write", "nextcloud.calendar.from-mail-create", {
  ...writeBase, candidate_id:"inbound",
}, `${successRun}-write-2`);
assert(second.payload.evidence.postcondition_verified === true, "second read-back is missing");
assert(testing.obligationByRun.get(successRun)?.terminal_state === "completed", "two-target workflow did not complete");
const completedReply = await hooks.get("reply_payload_sending")(
  {runId:successRun, payload:{text:"Beide Termine wurden erfolgreich eingetragen."}},
  {channel:"synthetic"},
);
assert(completedReply === undefined, "grounded completion was replaced");
const replayResult = await nativeTool("personal_assistant_calendar_write", successRun).execute(
  `${successRun}-write-2`, second.before.params,
);
const replayPayload = JSON.parse(replayResult.content[0].text);
assert(replayPayload.evidence?.ok === false, "approval replay was accepted");
assert(replayPayload.evidence?.error === "approval-required", "approval replay error is untyped");
assert(replayPayload.result?.executed === false, "approval replay executed the operation");
assert(replayPayload.diagnostic?.retry_allowed === false, "approval replay permits automatic retry");

const staleRun = "m15-stale-mail-approval";
await hooks.get("before_prompt_build")(
  {prompt:"Sende den bereits vollstaendig angezeigten Mailentwurf jetzt.", messages:[]},
  {runId:staleRun, sessionId:`session-${staleRun}`},
);
const staleResult = await nativeTool("personal_assistant_mail_write", staleRun).execute(
  `${staleRun}-send`,
  {operation:"mail.compose-send", arguments:{draft_id:"synthetic-draft"}},
);
const stalePayload = JSON.parse(staleResult.content[0].text);
assert(stalePayload.evidence?.error === "approval-required", "stale mail approval is untyped");
assert(stalePayload.result?.external_write_attempted === false, "stale mail approval attempted a send");
assert(testing.obligationByRun.get(staleRun)?.terminal_state === "approval-required", "approval obligation did not terminate safely");
const staleRevision = await hooks.get("before_agent_finalize")(
  {runId:staleRun, lastAssistantMessage:"Bitte gib /approve ein, dann versende ich die Mail."},
  {runId:staleRun, sessionId:`session-${staleRun}`},
);
assert(staleRevision?.action === "revise", "bare approval retry instruction was not rejected");
const staleGuard = await hooks.get("reply_payload_sending")(
  {runId:staleRun, payload:{text:"Bitte gib /approve ein, dann versende ich die Mail."}},
  {channel:"synthetic"},
);
assert(staleGuard?.payload?.text?.includes("nicht ausgefuehrt"), "stale approval replacement hides non-execution");
assert(staleGuard?.payload?.text?.includes("exakten ID"), "stale approval replacement omits exact-id guidance");

const uncertainRun = "m15-delivery-uncertain";
await prepare(uncertainRun);
await invoke(uncertainRun, "personal_assistant_calendar_write", "nextcloud.calendar.from-mail-create", {
  ...writeBase, candidate_id:"outbound",
}, `${uncertainRun}-write-1`);
const uncertain = await invoke(uncertainRun, "personal_assistant_calendar_write", "nextcloud.calendar.from-mail-create", {
  ...writeBase, candidate_id:"delivery-uncertain",
}, `${uncertainRun}-write-2`);
assert(uncertain.payload.evidence.postcondition_verified === false, "uncertain delivery was accepted");
assert(testing.obligationByRun.get(uncertainRun)?.terminal_state === "blocked", "partial failure did not block");
assert(testing.obligationByRun.get(uncertainRun)?.completed_targets === 1, "partial success was hidden");

const informationRun = "m15-information";
await hooks.get("before_prompt_build")(
  {prompt:"Trage den Flug aus der Mail in meinen Kalender ein.", messages:[]}, {runId:informationRun},
);
await invoke(informationRun, "personal_assistant_mail_read", "mail.search", {query:"Synthetic"}, `${informationRun}-search`);
await invoke(informationRun, "personal_assistant_mail_read", "mail.read", {
  folder:"Agent/Test", message_id:"synthetic-43", expected_subject:"Incomplete flight",
}, `${informationRun}-read`);
await invoke(informationRun, "personal_assistant_calendar_read", "nextcloud.calendar.from-mail-preview", {
  folder:"Agent/Test", message_id:"synthetic-43", expected_subject:"Incomplete flight",
}, `${informationRun}-preview`);
assert(testing.obligationByRun.get(informationRun)?.terminal_state === "information-required", "missing source data did not abstain");

const networkRun = "m15-network";
await hooks.get("before_prompt_build")(
  {prompt:"Trage den Termin aus der Mail in meinen Kalender ein.", messages:[]}, {runId:networkRun},
);
const network = await invoke(networkRun, "personal_assistant_mail_read", "mail.search", {query:"Network"}, `${networkRun}-search`);
assert(network.payload.evidence.ok === false, "network failure was accepted");
assert(testing.obligationByRun.get(networkRun)?.terminal_state === "blocked", "network failure did not block");
const retryLoop = await hooks.get("reply_payload_sending")(
  {
    runId:networkRun,
    payload:{text:"Das System-Limit hat mich blockiert. Soll ich es jetzt noch einmal versuchen, die Flugzeiten einzutragen?"},
  },
  {channel:"synthetic"},
);
assert(retryLoop?.payload?.text?.includes("Belegter Blocker"), "blocked action hides its typed blocker");
assert(retryLoop?.payload?.text?.includes("Trage Hin- und Rueckflug"), "retry loop was not replaced with an explicit new instruction");

const timeout = await spawnJson(
  {executable:"/opt/openclaw-agent/scripts/assistant.sh", argv:["mail","search","--query","Timeout","--limit","50"], stdin:null},
  {tool_timeout_seconds:1, max_output_bytes:4096, max_error_bytes:4096},
);
assert(timeout.returncode === 124 && timeout.error === "tool-timeout", "timeout was not fail-closed");
assert(
  shouldBlockGenericTool("exec", {command:"/opt/openclaw-agent/scripts/assistant.sh calendar from-mail"}),
  "raw domain exec was not blocked",
);
const serializedMetrics = JSON.stringify(testing.metrics);
assert(!serializedMetrics.includes("Synthetic") && !serializedMetrics.includes("@"), "content leaked into metrics");

console.log(JSON.stringify({
  ok:true,
  image_plugin:true,
  scripted_replies:true,
  fake_mail_adapter:true,
  fake_caldav_adapter:true,
  approval_replay_blocked:true,
  stale_mail_approval_typed:true,
  bare_approval_retry_blocked:true,
  unbound_yes_no_retry_blocked:true,
  delivery_event_run_correlation:true,
  network_failure_blocked:true,
  timeout_blocked:true,
  partial_failure_visible:true,
  external_writes:0,
}));
JS

echo "M15-Integration erfolgreich: Plugin, Aktionsabschluss, Approval, Timeout und Teilfehler."
