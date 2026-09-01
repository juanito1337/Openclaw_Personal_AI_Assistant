#!/usr/bin/env bash
set -euo pipefail
umask 077

root=$(CDPATH='' cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
image=${OPENCLAW_M13_RUNTIME_IMAGE:-${1:-}}
[[ -n "$image" ]] || {
  echo "OPENCLAW_M13_RUNTIME_IMAGE oder Runtime-Image als Argument angeben." >&2
  exit 2
}
command -v docker >/dev/null 2>&1 || {
  echo "Docker fehlt." >&2
  exit 2
}
docker image inspect "$image" >/dev/null

fixture="$root/tests/fixtures/container/immutable-plugins-openclaw.json"
fake_assistant="$root/tests/fixtures/m13/fake-assistant.sh"
docker run --rm --network none --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
  --tmpfs /home/node/.openclaw:rw,nosuid,nodev,noexec,size=32m,mode=0700,uid=1000,gid=1000 \
  --mount "type=bind,src=$fixture,dst=/home/node/.openclaw/openclaw.json,readonly" \
  --entrypoint openclaw \
  "$image" plugins inspect personal-assistant-tools --runtime --json >/dev/null

result=$(docker run --rm --network none --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
  --mount "type=bind,src=$fake_assistant,dst=/opt/openclaw-agent/scripts/assistant.sh,readonly" \
  --entrypoint node \
  "$image" --input-type=module -e '
import plugin from "/opt/openclaw-plugins/personal-assistant-tools/index.js";
const factories = [];
const hooks = new Map();
plugin.register({
  registerTool(factory) { factories.push(factory); },
  on(name, handler) { hooks.set(name, handler); },
  logger: {warn() {}, info() {}, debug() {}},
});
function nativeTool(name, runId) {
  const factory = factories.find((candidate) => candidate({runId:"probe"}).name === name);
  if (!factory) throw new Error(`native tool missing: ${name}`);
  return factory({runId,sessionId:`session-${runId}`});
}
async function invoke(name, operation, args, runId, callId) {
  const result = await nativeTool(name, runId).execute(callId, {operation,arguments:args});
  const payload = JSON.parse(result.content[0].text);
  if (!payload.evidence || payload.evidence.tool_id !== operation) {
    throw new Error(`evidence mismatch: ${operation}`);
  }
  return payload;
}
await hooks.get("before_prompt_build")(
  {prompt:"Welche Agenten-Version ist installiert?", messages:[]},
  {runId:"run-version",sessionId:"session-version"},
);
const versionPayload = await invoke(
  "personal_assistant_runtime_read", "assistant.version", {}, "run-version", "call-version",
);
if (!versionPayload.evidence.ok || versionPayload.evidence.tool_id !== "assistant.version") {
  throw new Error("version evidence failed");
}
const finalVerdict = await hooks.get("before_agent_finalize")(
  {runId:"run-version",lastAssistantMessage:"OpenClaw Local Personal Assistant 3.4.0-r28"},
  {runId:"run-version",sessionId:"session-version"},
);
if (finalVerdict !== undefined) throw new Error("grounded version was rejected");
const statusPayload = await invoke(
  "personal_assistant_runtime_read", "assistant.status", {}, "run-status", "call-status",
);
const mailPayload = await invoke(
  "personal_assistant_mail_read", "mail.search", {query:"Synthetic"}, "run-mail-positive", "call-mail-positive",
);
if (!mailPayload.evidence.complete || mailPayload.result.results.length !== 1) {
  throw new Error("complete mail evidence failed");
}
const nextcloudPayload = await invoke(
  "personal_assistant_nextcloud_read", "nextcloud.list", {}, "run-nextcloud", "call-nextcloud",
);
const taskPayload = await invoke(
  "personal_assistant_tasks_read", "nextcloud.tasks.status", {}, "run-tasks", "call-tasks",
);
const portfolioPayload = await invoke(
  "personal_assistant_portfolio_read", "portfolio.holdings", {}, "run-portfolio", "call-portfolio",
);
if (![statusPayload,nextcloudPayload,taskPayload,portfolioPayload].every((payload) => payload.evidence.ok)) {
  throw new Error("cross-domain read evidence failed");
}
const rawBlock = await hooks.get("before_tool_call")(
  {toolName:"exec",params:{command:"/opt/openclaw-agent/scripts/assistant.sh mail search --query Test"},toolCallId:"raw"},
  {runId:"run-raw"},
);
if (rawBlock?.block !== true) throw new Error("raw domain exec not blocked");
const writeArgs = {uid:"synthetic-uid",expected_title:"Synthetic task"};
const writeApproval = await hooks.get("before_tool_call")(
  {toolName:"personal_assistant_tasks_write",params:{operation:"nextcloud.tasks.update",arguments:writeArgs},toolCallId:"write"},
  {runId:"run-write"},
);
if (!writeApproval?.requireApproval || writeApproval.requireApproval.allowedDecisions.join(",") !== "allow-once,deny") {
  throw new Error("bound approval missing");
}
const writeResult = await nativeTool("personal_assistant_tasks_write", "run-write").execute(
  "write", writeApproval.params,
);
const writePayload = JSON.parse(writeResult.content[0].text);
if (!writePayload.evidence.ok || !writePayload.evidence.postcondition_verified || !writePayload.evidence.allowed_claims.includes("write-success")) {
  throw new Error("synthetic write postcondition evidence missing");
}
let replayBlocked = false;
try {
  await nativeTool("personal_assistant_tasks_write", "run-write").execute("write", writeApproval.params);
} catch (error) {
  replayBlocked = String(error).includes("missing-or-stale-bound-approval");
}
if (!replayBlocked) throw new Error("single-use approval replay was accepted");
await hooks.get("before_prompt_build")(
  {prompt:"Gibt es eine Mail zu Partial?",messages:[]},
  {runId:"run-mail-partial",sessionId:"session-mail-partial"},
);
const partialPayload = await invoke(
  "personal_assistant_mail_read", "mail.search", {query:"Partial"}, "run-mail-partial", "call-mail-partial",
);
if (partialPayload.evidence.complete || partialPayload.evidence.allowed_claims.includes("negative")) {
  throw new Error("partial mail result authorized a negative claim");
}
const revision = await hooks.get("before_agent_finalize")(
  {runId:"run-mail-partial",lastAssistantMessage:"Nein, es gibt keine Mail."},
  {runId:"run-mail-partial",sessionId:"session-mail-partial"},
);
if (revision?.action !== "revise" || revision.retry?.maxAttempts !== 1) {
  throw new Error("single guard revision missing");
}
const guarded = await hooks.get("reply_payload_sending")(
  {payload:{text:"Nein, es gibt keine Mail."}},
  {runId:"run-mail-partial",sessionId:"session-mail-partial"},
);
if (!guarded?.payload?.text?.includes("nicht belegen")) throw new Error("reply guard failed");
console.log(JSON.stringify({
  ok:true,
  native_tool_count:factories.length,
  domains_executed:["runtime","mail","nextcloud","tasks","portfolio"],
  version_evidence:true,
  complete_positive_mail:true,
  partial_negative_blocked:true,
  single_retry:true,
  raw_exec_blocked:true,
  synthetic_write_executed:true,
  write_postcondition_verified:true,
  approval_replay_blocked:true,
  allow_once_only:true,
  ungrounded_reply_replaced:true,
  external_writes:0,
  productive_writes:0,
}));
')

mkdir -p "$root/build"
printf '%s\n' "$result" > "$root/build/m13-integration.json"
python3 - "$result" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["ok"] is True
assert payload["native_tool_count"] == 19
assert payload["external_writes"] == 0
assert payload["productive_writes"] == 0
assert payload["synthetic_write_executed"] is True
assert payload["write_postcondition_verified"] is True
assert payload["approval_replay_blocked"] is True
assert payload["domains_executed"] == ["runtime", "mail", "nextcloud", "tasks", "portfolio"]
assert payload["partial_negative_blocked"] is True
PY
echo "M13-Integration erfolgreich: native Tools, Routing, Einmalfreigabe, synthetischer Write und Antwort-Guard."
