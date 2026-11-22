import test from "node:test";
import assert from "node:assert/strict";
import { createHash, createPublicKey, generateKeyPairSync, verify, webcrypto } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import http from "node:http";
import { registerSetupUi, SETUP_PATH, SETUP_RPC, setupBackendRequest, validateSetupRequest,
  validateSetupProof, operatorActor } from "../docker/openclaw-personal-assistant-plugin/setup-ui.js";
import { credentialStorageKey, gatewayEndpoint, readOperatorIdentity, connectParameters,
  signSetupOperation, createSetupGateway } from "../docker/openclaw-personal-assistant-plugin/setup-ui-auth.js";

const DEVICE = "a".repeat(64);
const client = () => ({ connect: { role: "operator", scopes: ["operator.admin"], device: { id: DEVICE } } });
const proof = (operation = "status", parameters = {}, actor = `device:${DEVICE}`, issued_at = Date.now()) => ({
  payload: JSON.stringify({ operation, parameters, actor, issued_at, nonce: "b".repeat(32) }), signature: "A".repeat(86),
});
function registration(backend, options = {}) {
  const methods = new Map(), routes = [], descriptors = [];
  registerSetupUi({ config: {}, registerGatewayMethod(name, handler, opts) { methods.set(name, { handler, opts }); },
    registerHttpRoute(route) { routes.push(route); }, registerControlUiDescriptor(value) { descriptors.push(value); } }, { backend, ...options });
  return { methods, routes, descriptors };
}
async function dispatch(registry, requestClient = client(), params = { operation: "status", parameters: {}, proof: proof() }) {
  let response;
  await registry.methods.get(SETUP_RPC).handler({ params, client: requestClient, respond(...args) { response = args; } });
  return response;
}

const savedConnection = () => ({ connected: true, revision: "revision-one", talk: {
  enabled: true, room_tokens: ["roomA"], allowed_user_ids: ["jan"],
} });
function runtimeReport(changes = {}) {
  return { ok: true, mode: "managed-account", channel: "nextcloud-account-talk", connection_revision: "revision-one",
    state: "active", configured: true, enabled: true, connected: true, desired_state: "ON", room_count: 1, allowed_user_count: 1,
    issues: [], warnings: [], limitations: ["Do not forward arbitrary strings"],
    worker: { present: true, fresh: true, healthy: true, observed_at: Date.now() / 1000 - 1, age_seconds: 1,
      max_age_seconds: 90, account_matches: true, revision_matches: true, selection_matches: true,
      enabled: true, connected: true, code: "talk-poll-ok", counts: { completed: 2, uncertain: 0 } }, ...changes };
}
function runtimeResult(report = runtimeReport(), changes = {}) {
  return { returncode: report.ok ? 0 : 1, stdout: JSON.stringify(report), stderr: "", stdout_truncated: false, ...changes };
}

test("saved setup status includes fresh revision-bound worker evidence from a fixed bounded command", async () => {
  const report = runtimeReport(); report.secret = "never-return-this"; report.worker.secret = "never-return-this";
  let execution;
  const api = registration(async () => savedConnection(), { runtimeExecute: async (invocation, limits) => {
    execution = { invocation, limits }; return runtimeResult(report);
  } });
  const response = await dispatch(api);
  assert.equal(response[0], true);
  const runtime = response[1].talk_runtime;
  assert.equal(runtime.state, "active"); assert.equal(runtime.connected, true); assert.equal(runtime.schema_version, 1);
  assert.equal(runtime.connection_revision, "revision-one"); assert.equal(runtime.worker.observed_at, report.worker.observed_at);
  assert.equal(runtime.worker.max_age_seconds, 90); assert.equal(runtime.worker.counts.completed, 2);
  assert.ok(!JSON.stringify(response).includes("never-return-this"));
  assert.ok(!JSON.stringify(runtime).includes("Do not forward arbitrary strings"));
  assert.deepEqual(execution.invocation, { executable: "/opt/openclaw-agent/scripts/assistant.sh", argv: ["talk", "status"], stdin: null });
  assert.ok(execution.limits.tool_timeout_seconds > 0 && execution.limits.tool_timeout_seconds <= 10);
  assert.ok(execution.limits.max_capture_bytes <= 32 * 1024);
});

test("valid unsuccessful Talk status preserves worker failure despite command exit one", async () => {
  const report = runtimeReport({ ok: false, state: "failed", connected: false, issues: ["talk-poll-failed"] });
  report.worker = { ...report.worker, healthy: false, connected: false, code: "talk-poll-failed" };
  const api = registration(async () => savedConnection(), { runtimeExecute: async () => runtimeResult(report) });
  const response = await dispatch(api);
  assert.equal(response[0], true); assert.equal(response[1].talk_runtime.state, "failed");
  assert.equal(response[1].talk_runtime.connected, false);
  assert.deepEqual(response[1].talk_runtime.issues, ["talk-poll-failed"]);
});

test("runtime from another saved revision cannot establish a current connection", async () => {
  const api = registration(async () => savedConnection(), { runtimeExecute: async () => runtimeResult(runtimeReport({ connection_revision: "different-revision" })) });
  const runtime = (await dispatch(api))[1].talk_runtime;
  assert.equal(runtime.state, "pending"); assert.equal(runtime.connected, false); assert.equal(runtime.worker.healthy, false);
  assert.deepEqual(runtime.issues, ["talk-worker-configuration-not-observed"]);
});

test("worker evidence expiring during collection is stale instead of connected", async () => {
  const report = runtimeReport(); report.worker.observed_at = Date.now() / 1000 - 91;
  const api = registration(async () => savedConnection(), { runtimeExecute: async () => runtimeResult(report) });
  const runtime = (await dispatch(api))[1].talk_runtime;
  assert.equal(runtime.state, "stale"); assert.equal(runtime.connected, false);
  assert.equal(runtime.worker.fresh, false); assert.equal(runtime.worker.healthy, false);
  assert.ok(runtime.issues.includes("talk-worker-status-stale"));
});

test("expired failed observation does not establish a current worker outage", async () => {
  const report = runtimeReport({ ok: false, state: "failed", connected: false, issues: ["talk-poll-failed"] });
  report.worker = { ...report.worker, observed_at: Date.now() / 1000 - 91,
    healthy: false, connected: false, code: "talk-poll-failed" };
  const api = registration(async () => savedConnection(), { runtimeExecute: async () => runtimeResult(report) });
  const runtime = (await dispatch(api))[1].talk_runtime;
  assert.equal(runtime.state, "stale"); assert.equal(runtime.connected, false); assert.equal(runtime.worker.fresh, false);
  assert.ok(runtime.issues.includes("talk-worker-status-stale"));
});

test("ancillary runtime deadline preserves a completed write even when execution never settles", async context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let started, rejectRuntime, response;
  const runtimeStarted = new Promise(resolve => { started = resolve; });
  const api = registration(async () => savedConnection(), { runtimeExecute: () => {
    started(); return new Promise((_resolve, reject) => { rejectRuntime = reject; });
  } });
  const parameters = { enabled: true, room_tokens: ["roomA"], allowed_user_ids: ["jan"], expected_revision: "prior" };
  const pending = dispatch(api, client(), { operation: "talk.configure", parameters, proof: proof("talk.configure", parameters) })
    .then(value => { response = value; });
  await runtimeStarted;
  context.mock.timers.tick(2100);
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(response, "completed write must return before the ancillary read can consume the browser deadline");
  assert.equal(response[0], true); assert.equal(response[1].revision, "revision-one");
  assert.equal(response[1].talk_runtime.state, "unavailable");
  // A late process failure must stay handled after the operator has its result.
  rejectRuntime(new Error("never-return-this"));
  await new Promise(resolve => setImmediate(resolve));
  await pending;
  assert.ok(!JSON.stringify(response).includes("never-return-this"));
});

test("malformed or unavailable runtime never reverses a successful setup save or leaks raw errors", async () => {
  const parameters = { enabled: true, room_tokens: ["roomA"], allowed_user_ids: ["jan"], expected_revision: "prior" };
  const cases = [
    () => { throw new Error("never-return-this"); },
    () => runtimeResult(undefined, { returncode: 124, stderr: "never-return-this" }),
    () => runtimeResult(undefined, { stdout: "never-return-this" }),
    () => runtimeResult(undefined, { stdout_truncated: true }),
    () => runtimeResult(undefined, { stdout: " ".repeat(32 * 1024 + 1) }),
    () => runtimeResult(runtimeReport({ mode: "legacy" })),
    () => runtimeResult(runtimeReport({ issues: ["never-return-this"] })),
    () => runtimeResult(runtimeReport({ connected: "yes" })),
    () => runtimeResult(runtimeReport({ worker: { ...runtimeReport().worker, code: "never-return-this" } })),
  ];
  for (const execute of cases) {
    const api = registration(async () => savedConnection(), { runtimeExecute: execute });
    const response = await dispatch(api, client(), { operation: "talk.configure", parameters, proof: proof("talk.configure", parameters) });
    assert.equal(response[0], true); assert.equal(response[1].revision, "revision-one");
    assert.equal(response[1].talk_runtime.state, "unavailable"); assert.equal(response[1].talk_runtime.connected, false);
    assert.deepEqual(response[1].talk_runtime.issues, ["talk-runtime-unavailable"]);
    assert.ok(!JSON.stringify(response).includes("never-return-this"));
  }
});

test("runtime reads follow authorization and successful saved-account operations only", async () => {
  let calls = 0;
  const options = { runtimeExecute: async () => { calls++; return runtimeResult(); } };
  const unauthorized = registration(async () => savedConnection(), options);
  assert.equal((await dispatch(unauthorized, null))[0], false);
  const failed = registration(async () => { throw new Error("backend-failed"); }, options);
  assert.equal((await dispatch(failed))[0], false);
  for (const result of [{ connected: false, revision: "" }, { state: "failed", code: "authentication-failed" }]) {
    const response = await dispatch(registration(async () => result, options));
    assert.equal(response[1].talk_runtime, undefined);
  }
  const parameters = { room_token: "roomA" };
  await dispatch(unauthorized, client(), { operation: "talk.participants", parameters, proof: proof("talk.participants", parameters) });
  assert.equal(calls, 0);
  for (const [operation, parameters] of [["status", {}], ["connection.refresh", { expected_revision: "prior" }],
    ["connection.apply", { flow_id: "a".repeat(32), preview_digest: "b".repeat(64), expected_revision: "", confirm_account_access: true }],
    ["talk.configure", { enabled: true, room_tokens: ["roomA"], allowed_user_ids: ["jan"], expected_revision: "prior" }]]) {
    const response = await dispatch(unauthorized, client(), { operation, parameters, proof: proof(operation, parameters) });
    assert.equal(response[1].talk_runtime.state, "active");
  }
  assert.equal(calls, 4);
});

test("all setup calls require an external operator admin device and exact signed actor", async () => {
  const calls = [];
  const api = registration(async request => { calls.push(request); return { connected: false }; });
  assert.equal(api.methods.get(SETUP_RPC).opts.scope, "operator.admin");
  assert.equal(api.descriptors[0].surface, "tab");
  assert.deepEqual(api.descriptors[0].requiredScopes, ["operator.admin"]);
  assert.deepEqual((await dispatch(api)).slice(0, 2), [true, { connected: false }]);
  assert.equal(calls[0].actor, `device:${DEVICE}`);
  assert.equal(JSON.parse(calls[0].proof.payload).actor, `device:${DEVICE}`);
  for (const invalid of [null, {}, { ...client(), internal: { pluginRuntimeOwnerId: "agent" } },
    { connect: { ...client().connect, role: "node" } }, { connect: { ...client().connect, scopes: ["operator.read"] } },
    { connect: { ...client().connect, device: null } }, { connect: { ...client().connect, device: { id: "forged" } } }]) {
    const result = await dispatch(api, invalid);
    assert.equal(result[0], false); assert.equal(result[2].code, "FORBIDDEN");
  }
  assert.equal(calls.length, 1);
  assert.equal(operatorActor(client()), `device:${DEVICE}`);
});

test("unknown operations, additional fields and unbound parameters never reach the backend", async () => {
  let calls = 0; const api = registration(async () => { calls++; return {}; });
  for (const params of [
    { operation: "exec", parameters: {}, proof: proof() },
    { operation: "status", parameters: {}, actor: "attacker", proof: proof() },
    { operation: "status", parameters: { argv: "anything" }, proof: proof() },
    { operation: "status", parameters: {} },
    { operation: "status", parameters: {}, proof: proof("status", {}, "device:" + "c".repeat(64)) },
    { operation: "status", parameters: {}, proof: proof("login.start", { server_url: "https://example.test" }) },
    { operation: "status", parameters: {}, proof: proof("status", {}, `device:${DEVICE}`, Date.now() - 90_001) },
    { operation: "login.start", parameters: { server_url: "http://example.test" }, proof: proof() },
  ]) assert.equal((await dispatch(api, client(), params))[0], false);
  assert.equal(calls, 0);
  assert.throws(() => validateSetupRequest({ operation: "connection.apply", parameters: {
    flow_id: "a".repeat(32), preview_digest: "b".repeat(64), expected_revision: "", confirm_account_access: false,
  } }));
  assert.throws(() => validateSetupRequest({ operation: "talk.configure", parameters: {
    enabled: true, room_tokens: ["room", "room"], allowed_user_ids: ["jan"], expected_revision: "revision",
  } }));
  assert.doesNotThrow(() => validateSetupRequest({ operation: "talk.configure", parameters: {
    enabled: true, room_tokens: ["room_1-a"], allowed_user_ids: ["jan"], expected_revision: "revision",
  } }));
});

test("app-password RPC is admin-only, closed, bounded and bound to the exact credential proof", async () => {
  const parameters = { server_url: "https://cloud.example.test/nc", login_name: "assistant@example.test",
    app_password: "synthetic-app-password-never-in-output" };
  const calls = [];
  const api = registration(async value => { calls.push(value); return { state: "ready", flow_id: "c".repeat(32) }; });
  const request = { operation: "login.credentials", parameters, proof: proof("login.credentials", parameters) };
  assert.deepEqual((await dispatch(api, client(), request)).slice(0, 2), [true, { state: "ready", flow_id: "c".repeat(32) }]);
  assert.deepEqual(calls[0].parameters, parameters);
  assert.equal(calls[0].actor, `device:${DEVICE}`);
  for (const invalid of [
    { ...parameters, app_password: "" }, { ...parameters, app_password: "x".repeat(4097) },
    { ...parameters, app_password: "bad\npassword" }, { ...parameters, app_password: "bad\x7fpassword" },
    { ...parameters, login_name: "x".repeat(513) }, { ...parameters, login_name: "bad:user" },
    { ...parameters, login_name: "" }, { ...parameters, server_url: "http://cloud.example.test" },
    { ...parameters, authorization: "allow" }, { server_url: parameters.server_url, login_name: parameters.login_name },
  ]) {
    const response = await dispatch(api, client(), { operation: "login.credentials", parameters: invalid,
      proof: proof("login.credentials", invalid) });
    assert.equal(response[0], false);
    assert.equal(response[2].code, "INVALID_REQUEST");
    assert.ok(!JSON.stringify(response).includes(parameters.app_password));
  }
  assert.equal((await dispatch(api, { connect: { ...client().connect, scopes: ["operator.read"] } }, request))[2].code, "FORBIDDEN");
  assert.equal((await dispatch(api, client(), { ...request, parameters: { ...parameters, app_password: "changed" } }))[2].code, "FORBIDDEN");
  assert.equal(calls.length, 1);
  const broken = registration(async () => { throw new Error(parameters.app_password); });
  const failure = await dispatch(broken, client(), request);
  assert.equal(failure[2].code, "UNAVAILABLE");
  assert.ok(!JSON.stringify(failure).includes(parameters.app_password));
});

test("structured identity failure passes as sanitized data without adding gateway error metadata", async () => {
  const result = { state: "failed", code: "authentication-failed", diagnostics: {
    schema_version: 1, user: { status: "failed", loaded: false, code: "authentication-failed" },
    calendars: { status: "skipped" }, task_lists: { status: "skipped" }, files: { status: "skipped" },
  } };
  const api = registration(async () => result);
  assert.deepEqual(await dispatch(api), [true, result]);
});

test("static routes serve no state, reject writes and frame only the credential-free launcher", () => {
  const api = registration(() => { throw new Error("must not run"); });
  for (const route of api.routes) {
    assert.equal(route.auth, "plugin"); assert.equal(route.match, "exact");
    const response = { writeHead(status, headers) { this.status = status; this.headers = headers; }, end(body) { this.body = body; } };
    route.handler({ method: "GET" }, response);
    assert.equal(response.status, 200); assert.equal(response.headers["Cache-Control"], "no-store");
    assert.equal(response.headers["Referrer-Policy"], "no-referrer");
    assert.match(response.headers["Content-Security-Policy"], route.path === SETUP_PATH ? /frame-ancestors 'self'/ : /frame-ancestors 'none'/);
    route.handler({ method: "HEAD" }, response); assert.equal(response.body, undefined);
    route.handler({ method: "POST" }, response); assert.equal(response.status, 405);
  }
  const shell = api.routes.find(route => route.path === SETUP_PATH);
  let html; shell.handler({ method: "GET" }, { writeHead() {}, end(body) { html = body.toString(); } });
  assert.ok(!html.includes("<script")); assert.ok(!html.includes("token="));
  assert.match(html, /Einrichtung in OpenClaw öffnen/);
});

test("Unix transport preserves public errors, binds the proof and never leaks socket/system details", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "setup-ui-test-"));
  const socketPath = join(temporary, "control.sock");
  const requests = []; let response = { ok: true, result: { connected: false } };
  const server = http.createServer((request, reply) => {
    let body = ""; request.on("data", chunk => { body += chunk; }); request.on("end", () => {
      requests.push({ path: request.url, method: request.method, body: JSON.parse(body) });
      reply.writeHead(200, { "Content-Type": "application/json" }); reply.end(JSON.stringify(response));
    });
  });
  try {
    await new Promise(resolve => server.listen(socketPath, resolve));
    const payload = { operation: "status", parameters: {}, actor: `device:${DEVICE}`, proof: proof() };
    assert.deepEqual(await setupBackendRequest(payload, { socketPath }), { connected: false });
    assert.deepEqual(requests[0], { path: "/rpc", method: "POST", body: payload });
    response = { ok: false, error: { code: "nextcloud-connection-conflict", message: "Verbindung wurde geändert." } };
    await assert.rejects(setupBackendRequest(payload, { socketPath }), error => error.code === "nextcloud-connection-conflict");
    response = { ok: true, result: "invalid" };
    await assert.rejects(setupBackendRequest(payload, { socketPath }), error => error.code === "UNAVAILABLE" && !error.message.includes(socketPath));
  } finally { await new Promise(resolve => server.close(resolve)); await rm(temporary, { recursive: true, force: true }); }
  await assert.rejects(setupBackendRequest({}, { socketPath }), error => !error.message.includes(socketPath) && error.code === "UNAVAILABLE");
});

function fixture() {
  const pair = generateKeyPairSync("ed25519");
  const privateBytes = pair.privateKey.export({ format: "der", type: "pkcs8" }).subarray(-32);
  const publicBytes = pair.publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  const identity = { version: 1, deviceId: createHash("sha256").update(publicBytes).digest("hex"),
    publicKey: publicBytes.toString("base64url"), privateKey: privateBytes.toString("base64url") };
  const auth = { version: 1, deviceId: identity.deviceId, tokens: { operator: { role: "operator", token: "synthetic-browser-token", scopes: ["operator.admin", "operator.read"] } } };
  const endpoint = "wss://openclaw.example.test/dashboard";
  const data = new Map([["openclaw-device-identity-v1", JSON.stringify(identity)], [credentialStorageKey(endpoint), JSON.stringify(auth)]]);
  const reads = []; const storage = { getItem(key) { reads.push(key); return data.get(key) || null; } };
  return { pair, identity, auth, endpoint, data, reads, storage };
}

test("credential reuse is scoped to this gateway; no legacy migration, identity creation or scope expansion", async () => {
  const value = fixture();
  assert.equal(credentialStorageKey("wss://openclaw.example.test/dashboard///?route=one"), "openclaw.device.auth.v1:wss://openclaw.example.test/dashboard?route=one");
  assert.equal(credentialStorageKey("wss://openclaw.example.test/"), "openclaw.device.auth.v1:wss://openclaw.example.test");
  assert.equal(gatewayEndpoint("https://openclaw.example.test", "/dashboard"), value.endpoint);
  assert.throws(() => gatewayEndpoint("http://remote.example.test"));
  assert.throws(() => gatewayEndpoint("https://openclaw.example.test", "//attacker.test"));
  const identity = await readOperatorIdentity(value.storage, value.endpoint, webcrypto);
  assert.deepEqual(identity.scopes, ["operator.admin", "operator.read"]);
  assert.deepEqual(value.reads, ["openclaw-device-identity-v1", credentialStorageKey(value.endpoint)]);
  await assert.rejects(readOperatorIdentity(value.storage, "wss://openclaw.example.test/sibling", webcrypto), /Administratoranmeldung/);
  value.auth.tokens.operator.scopes = ["operator.read"];
  value.data.set(credentialStorageKey(value.endpoint), JSON.stringify(value.auth));
  await assert.rejects(readOperatorIdentity(value.storage, value.endpoint, webcrypto), /Administratoranmeldung/);
  value.data.delete(credentialStorageKey(value.endpoint));
  value.data.set("openclaw.device.auth.v1", JSON.stringify(value.auth));
  await assert.rejects(readOperatorIdentity(value.storage, value.endpoint, webcrypto), /Administratoranmeldung/);
});

test("browser signatures match pinned v2 gateway auth and per-operation proof binding", async () => {
  const value = fixture(); const identity = await readOperatorIdentity(value.storage, value.endpoint, webcrypto);
  const params = await connectParameters(identity, "challenge", webcrypto, {}, 1234);
  assert.equal(params.minProtocol, 4); assert.equal(params.maxProtocol, 4);
  const expected = ["v2", identity.id, "openclaw-control-ui", "webchat", "operator", "operator.admin,operator.read", "1234", "synthetic-browser-token", "challenge"].join("|");
  assert.ok(verify(null, Buffer.from(expected), value.pair.publicKey, Buffer.from(params.device.signature, "base64url")));
  const signed = await signSetupOperation(identity, "login.start", { server_url: "https://cloud.example.test" }, webcrypto, 1234);
  assert.ok(!signed.payload.includes("synthetic-browser-token"));
  assert.ok(verify(null, Buffer.from(signed.payload), value.pair.publicKey, Buffer.from(signed.signature, "base64url")));
  const parsed = JSON.parse(signed.payload);
  assert.deepEqual(Object.keys(parsed), ["operation", "parameters", "actor", "issued_at", "nonce"]);
  assert.match(parsed.nonce, /^[0-9a-f]{32}$/);
  assert.doesNotThrow(() => validateSetupProof(signed, { operation: parsed.operation, parameters: parsed.parameters }, parsed.actor, 1234));
  assert.throws(() => validateSetupProof(signed, { operation: "status", parameters: {} }, parsed.actor, 1234));
  assert.throws(() => validateSetupProof(signed, { operation: parsed.operation, parameters: parsed.parameters }, parsed.actor, 100_000));
  const publicKey = createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(identity.publicKey, "base64url")]), format: "der", type: "spki" });
  assert.ok(verify(null, Buffer.from(signed.payload), publicKey, Buffer.from(signed.signature, "base64url")));
  const parameters = { server_url: "https://cloud.example.test", login_name: "assistant", app_password: "synthetic-app-password" };
  const credentialProof = await signSetupOperation(identity, "login.credentials", parameters, webcrypto, 1234);
  assert.ok(verify(null, Buffer.from(credentialProof.payload), publicKey, Buffer.from(credentialProof.signature, "base64url")));
  assert.doesNotThrow(() => validateSetupProof(credentialProof, { operation: "login.credentials", parameters }, parsed.actor, 1234));
  assert.throws(() => validateSetupProof(credentialProof, { operation: "login.credentials", parameters: { ...parameters, app_password: "changed" } }, parsed.actor, 1234));
});

test("browser authenticates before setup RPC, signs every operation and never auto-retries a rejected write", async () => {
  const value = fixture(); const sent = [];
  class Socket extends EventTarget {
    readyState = 1;
    constructor(url) { super(); assert.equal(url, value.endpoint); queueMicrotask(() => this.frame({ type: "event", event: "connect.challenge", payload: { nonce: "challenge" } })); }
    frame(value) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); }
    send(raw) {
      const request = JSON.parse(raw); sent.push(request);
      queueMicrotask(() => this.frame({ type: "res", id: request.id, ok: request.method === "connect",
        payload: { auth: { scopes: ["operator.admin"] } }, error: { code: "conflict", message: "Verbindung geändert" } }));
    }
    close() { this.readyState = 3; this.dispatchEvent(new Event("close")); }
  }
  const gateway = createSetupGateway({ origin: "https://openclaw.example.test", basePath: "/dashboard", storage: value.storage,
    cryptoApi: webcrypto, WebSocketClass: Socket, browser: {} });
  await assert.rejects(gateway.call("status"), /Zuerst/);
  await gateway.connect();
  await assert.rejects(gateway.call("connection.refresh", { expected_revision: "revision" }), error => error.code === "conflict" && /Verbindung geändert/.test(error.message));
  assert.equal(sent.length, 2); assert.equal(sent[0].method, "connect"); assert.equal(sent[1].method, SETUP_RPC);
  const actual = sent[1].params;
  assert.ok(verify(null, Buffer.from(actual.proof.payload), value.pair.publicKey, Buffer.from(actual.proof.signature, "base64url")));
  assert.deepEqual(JSON.parse(actual.proof.payload).parameters, { expected_revision: "revision" });
  assert.ok(!JSON.stringify(actual).includes("synthetic-browser-token"));
  await assert.rejects(gateway.call("exec", {}), /Unbekannte/); assert.equal(sent.length, 2);
  gateway.close();
});

test("access preview and confirmation require the closed signed admin requests", async () => {
  const account_id = "nextcloud-account-" + "a".repeat(16);
  const scope = { resource_id: account_id+"-files", component: "FILES", path: "Belege", file_id: "19" };
  const revisions = {expected_revision: "c".repeat(32), expected_policy_revision: "d".repeat(32)};
  const edits = [{scope, level:"create"}];
  const parameters = { account_id, ...revisions, edits, confirm_root:false };
  const calls = [];
  const api = registration(async request => { calls.push(request); return savedConnection(); },
    {runtimeExecute: async () => runtimeResult()});
  assert.equal((await dispatch(api,client(),{operation:"access.preview",parameters,proof:proof("access.preview",parameters)}))[0],true);
  for (const changes of [{edits:[...edits,...edits]}, {edits:[{scope:{...scope,path:"階".repeat(800)},level:"create"}]},
    {edits:[{scope:{...scope,extra:1},level:"create"}]}, {edits:[{scope,level:"update"}]}, {unbound:true}]) {
    const bad = {...parameters,...changes};
    assert.equal((await dispatch(api,client(),{operation:"access.preview",parameters:bad,proof:proof("access.preview",bad)}))[0],false);
  }
  const confirm = {preview_id:"e".repeat(32),preview_digest:"f".repeat(64),...revisions,confirm:true};
  const result = await dispatch(api,client(),{operation:"access.apply",parameters:confirm,proof:proof("access.apply",confirm)});
  assert.equal(result[1].talk_runtime.state,"active");
  assert.equal(calls.length,2);
});

test("revision-bound status continuations use the signed status method", async () => {
  const value = fixture();
  const identity = await readOperatorIdentity(value.storage, value.endpoint, webcrypto);
  const parameters = {expected_revision:"9".repeat(32),access_offset:28};
  const signed = await signSetupOperation(identity,"status",parameters,webcrypto);
  const request = validateSetupRequest({operation:"status",parameters,proof:signed});
  assert.doesNotThrow(() => validateSetupProof(signed,request,"device:"+identity.id));
  for (const bad of [{...parameters,access_offset:true}, {...parameters,access_offset:0},
                     {...parameters,access_offset:5120}, {...parameters,extra:1}]) {
    assert.throws(() => validateSetupRequest({operation:"status",parameters:bad,proof:signed}));
  }
  assert.throws(() => validateSetupProof(signed,{operation:"status",parameters:{...parameters,access_offset:29}},"device:"+identity.id));
  const account_id = "nextcloud-account-"+"e".repeat(16);
  const scope = {resource_id:account_id+"-files",component:"FILES",path:"Belege/Grüße",file_id:"12"};
  const pending = {account_id, expected_revision:"7".repeat(32), expected_policy_revision:"8".repeat(32),
                  edits:[{scope,level:"create"}],confirm_root:false};
  const preview = await signSetupOperation(identity,"access.preview",pending,webcrypto);
  assert.ok(verify(null,Buffer.from(preview.payload),value.pair.publicKey,Buffer.from(preview.signature,"base64url")));
  await assert.rejects(signSetupOperation(identity,"access.preview",{
    ...pending,edits:Array.from({length:32},(_,i)=>({scope:{...scope,path:i+"壁".repeat(400)},level:"create"})),
  },webcrypto),/zu groß/);
});

test("an opaque revocation is a signed Off-only edit with closed fields", async () => {
  const id = fixture(); const identity = await readOperatorIdentity(id.storage,id.endpoint,webcrypto);
  const edit = {revoke_handle:"4".repeat(64),level:"off"};
  const parameters = {account_id:"nextcloud-account-"+"e".repeat(16),expected_revision:"7".repeat(32),
    expected_policy_revision:"8".repeat(32),edits:[edit],confirm_root:false};
  const signed = await signSetupOperation(identity,"access.preview",parameters,webcrypto);
  const parsed = validateSetupRequest({operation:"access.preview",parameters,proof:signed});
  validateSetupProof(signed,parsed,"device:"+identity.id);
  for (const edits of [[{...edit,level:"read"}],[edit,edit],[{...edit,scope:{}}],[{...edit,revoke_handle:"short"}]]) {
    assert.throws(() => validateSetupRequest({operation:"access.preview",parameters:{...parameters,edits}}));
  }
});
