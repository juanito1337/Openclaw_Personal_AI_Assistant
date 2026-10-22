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
function registration(backend) {
  const methods = new Map(), routes = [], descriptors = [];
  registerSetupUi({ config: {}, registerGatewayMethod(name, handler, opts) { methods.set(name, { handler, opts }); },
    registerHttpRoute(route) { routes.push(route); }, registerControlUiDescriptor(value) { descriptors.push(value); } }, { backend });
  return { methods, routes, descriptors };
}
async function dispatch(registry, requestClient = client(), params = { operation: "status", parameters: {}, proof: proof() }) {
  let response;
  await registry.methods.get(SETUP_RPC).handler({ params, client: requestClient, respond(...args) { response = args; } });
  return response;
}

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
  await assert.rejects(gateway.call("connection.refresh", { expected_revision: "revision" }), /Verbindung geändert/);
  assert.equal(sent.length, 2); assert.equal(sent[0].method, "connect"); assert.equal(sent[1].method, SETUP_RPC);
  const actual = sent[1].params;
  assert.ok(verify(null, Buffer.from(actual.proof.payload), value.pair.publicKey, Buffer.from(actual.proof.signature, "base64url")));
  assert.deepEqual(JSON.parse(actual.proof.payload).parameters, { expected_revision: "revision" });
  assert.ok(!JSON.stringify(actual).includes("synthetic-browser-token"));
  await assert.rejects(gateway.call("exec", {}), /Unbekannte/); assert.equal(sent.length, 2);
  gateway.close();
});
