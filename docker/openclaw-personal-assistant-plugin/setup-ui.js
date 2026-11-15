/** Operator-only Nextcloud setup bridge for the pinned OpenClaw 2026.7.1 API.
 * Public HTTP routes serve immutable assets only. All data and mutations use
 * authenticated Gateway RPC; no generic agent tool or HTTP credential bridge.
 */
import http from "node:http";
import { readFileSync } from "node:fs";
import { isDeepStrictEqual } from "node:util";
import { spawnJson } from "./runtime.js";

export const SETUP_RPC = "personal_assistant.setup";
export const SETUP_PATH = "/plugins/personal-assistant-tools/nextcloud";
export const SETUP_OPERATIONS = Object.freeze({
  status: {},
  "login.start": { server_url: "url" },
  "login.credentials": { server_url: "url", login_name: "login", app_password: "password" },
  "login.poll": { flow_id: "id" },
  "login.cancel": { flow_id: "id" },
  "connection.apply": { flow_id: "id", preview_digest: "digest", expected_revision: "revision", confirm_account_access: "true" },
  "connection.refresh": { expected_revision: "revision" },
  "folders.list": { path: "path" },
  "talk.participants": { room_token: "room" },
  "talk.configure": { enabled: "boolean", room_tokens: "rooms", allowed_user_ids: "users", expected_revision: "revision" },
});

class SetupError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

function plain(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value)
    && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null));
}

const textValue = (value, maximum, empty = false) => typeof value === "string"
  && value.length <= maximum && (empty || value.length > 0) && !/[\x00-\x1f\x7f]/.test(value);
const checks = {
  url: value => textValue(value, 2048) && /^https:\/\//i.test(value),
  login: value => textValue(value, 512) && !value.includes(":"),
  password: value => textValue(value, 4096),
  id: value => typeof value === "string" && /^[a-f0-9]{32}$/.test(value),
  digest: value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value),
  revision: value => textValue(value, 128, true),
  true: value => value === true,
  boolean: value => typeof value === "boolean",
  path: value => textValue(value, 2048, true),
  room: value => typeof value === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value),
  rooms: value => Array.isArray(value) && value.length <= 16 && value.every(item => checks.room(item)) && new Set(value).size === value.length,
  users: value => Array.isArray(value) && value.length <= 32 && value.every(item => textValue(item, 512)) && new Set(value).size === value.length,
};

export function validateSetupRequest(params) {
  if (!plain(params) || Object.keys(params).some(key => !["operation", "parameters", "proof"].includes(key))
      || !Object.hasOwn(SETUP_OPERATIONS, params.operation) || !plain(params.parameters)) {
    throw new SetupError("INVALID_REQUEST", "Unbekannte oder unvollständige Einrichtungsanfrage.");
  }
  const schema = SETUP_OPERATIONS[params.operation];
  if (Object.keys(params.parameters).length !== Object.keys(schema).length
      || Object.entries(schema).some(([key, kind]) => !Object.hasOwn(params.parameters, key) || !checks[kind](params.parameters[key]))) {
    throw new SetupError("INVALID_REQUEST", "Die Einrichtungsparameter sind ungültig.");
  }
  return { operation: params.operation, parameters: { ...params.parameters } };
}

export function validateSetupProof(proof, request, actor, now = Date.now()) {
  try {
    if (!plain(proof) || Object.keys(proof).length !== 2 || !textValue(proof.payload, 24 * 1024)
        || typeof proof.signature !== "string" || !/^[a-zA-Z0-9_-]{86}$/.test(proof.signature)) throw new Error();
    const signed = JSON.parse(proof.payload);
    if (!plain(signed) || Object.keys(signed).length !== 5 || signed.operation !== request.operation
        || !isDeepStrictEqual(signed.parameters, request.parameters) || signed.actor !== actor
        || !Number.isSafeInteger(signed.issued_at) || Math.abs(now - signed.issued_at) > 90_000
        || typeof signed.nonce !== "string" || !/^[a-f0-9]{32}$/.test(signed.nonce)) throw new Error();
    // The independent config owner verifies the Ed25519 signature against its
    // read-only pairing registry and consumes the nonce. The gateway only binds
    // the signed payload to this authenticated operator and exact request.
    return { payload: proof.payload, signature: proof.signature };
  } catch { throw new SetupError("FORBIDDEN", "Die Browserfreigabe fehlt, ist abgelaufen oder passt nicht zur Anfrage."); }
}

export function operatorActor(client) {
  const connect = client?.connect;
  // In-process plugin/agent dispatch is not a browser operator. The core checks
  // the signature/device binding before creating this authenticated client.
  if (!client || client.internal || connect?.role !== "operator"
      || !Array.isArray(connect.scopes) || !connect.scopes.includes("operator.admin")
      || typeof connect.device?.id !== "string" || !/^[a-f0-9]{64}$/.test(connect.device.id)) {
    throw new SetupError("FORBIDDEN", "Eine angemeldete OpenClaw-Operatorsitzung mit operator.admin und Gerätebindung ist erforderlich.");
  }
  return `device:${connect.device.id}`;
}

export function setupBackendRequest(payload, options = {}) {
  const socketPath = options.socketPath ?? process.env.OPENCLAW_SETUP_SOCKET ?? "/run/openclaw-setup/control.sock";
  if (typeof socketPath !== "string" || !socketPath.startsWith("/") || socketPath.includes("\0")) {
    return Promise.reject(new SetupError("UNAVAILABLE", "Der Einrichtungsdienst ist nicht konfiguriert."));
  }
  const data = JSON.stringify(payload);
  if (Buffer.byteLength(data) > 64 * 1024) {
    return Promise.reject(new SetupError("INVALID_REQUEST", "Die Einrichtungsanfrage ist zu groß."));
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      if (error) reject(error); else resolve(result);
    };
    const unavailable = () => new SetupError("UNAVAILABLE", "Der Einrichtungsdienst ist nicht erreichbar. Den Status prüfen, bevor eine Änderung erneut gestartet wird.");
    const request = http.request({ socketPath, path: "/rpc", method: "POST", headers: {
      "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data),
    } }, response => {
      const chunks = [];
      let length = 0;
      response.on("data", chunk => {
        length += chunk.length;
        if (length > 2 * 1024 * 1024) { finish(unavailable()); request.destroy(); }
        else chunks.push(chunk);
      });
      response.on("error", () => finish(unavailable()));
      response.on("end", () => {
        try {
          const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (response.statusCode !== 200 || !plain(value) || typeof value.ok !== "boolean") throw unavailable();
          if (value.ok === false) {
            if (!plain(value.error) || !textValue(value.error.code, 128) || !textValue(value.error.message, 1024)) throw unavailable();
            finish(new SetupError(value.error.code, value.error.message));
          } else if (plain(value.result)) finish(null, value.result);
          else throw unavailable();
        } catch (error) { finish(error instanceof SetupError ? error : unavailable()); }
      });
    });
    const deadline = setTimeout(() => { finish(unavailable()); request.destroy(); }, options.timeoutMs ?? 45_000);
    request.on("error", () => finish(unavailable()));
    request.end(data);
  });
}

const escapeHtml = value => value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

const RUNTIME_OPERATIONS = new Set(["status", "connection.apply", "connection.refresh", "talk.configure"]);
const RUNTIME_STATES = new Set(["active", "degraded", "disabled", "not-observed", "pending", "stale", "failed", "invalid"]);
const WORKER_CODES = new Set([
  "talk-disabled", "talk-configured", "talk-paired-no-history-replay", "talk-poll-ok", "talk-poll-failed",
  "talk-worker-not-observed", "talk-worker-status-path-invalid", "talk-worker-status-unreadable", "talk-worker-status-invalid",
]);
const RUNTIME_ISSUES = new Set([...WORKER_CODES, "talk-pairing-invalid", "talk-worker-configuration-not-observed",
  "talk-worker-status-stale", "talk-worker-connection-not-observed", "talk-processing-or-delivery-uncertain"]);
const WORKER_COUNTS = ["accepted", "processing", "uncertain", "completed", "delivery_uncertain"];
const nonnegative = value => Number.isFinite(value) && value >= 0;
const boundedCount = value => Number.isSafeInteger(value) && value >= 0;

function unavailableRuntime(connection, code = "talk-runtime-unavailable") {
  const enabled = connection.talk?.enabled === true;
  const rooms = Array.isArray(connection.talk?.room_tokens) ? connection.talk.room_tokens.length : 0;
  const users = Array.isArray(connection.talk?.allowed_user_ids) ? connection.talk.allowed_user_ids.length : 0;
  return { schema_version: 1, mode: "managed-account", channel: "nextcloud-account-talk",
    connection_revision: "", state: code === "talk-runtime-unavailable" ? "unavailable" : "pending", ok: false,
    configured: Boolean(rooms && users), enabled, connected: false, desired_state: enabled ? "ON" : "OFF",
    room_count: rooms, allowed_user_count: users,
    worker: { present: false, fresh: false, healthy: false, code }, issues: [code] };
}

function projectRuntime(value, connection) {
  if (!plain(value) || value.mode !== "managed-account" || value.channel !== "nextcloud-account-talk"
      || !textValue(value.connection_revision, 128) || !RUNTIME_STATES.has(value.state)
      || ["ok", "configured", "enabled", "connected"].some(key => typeof value[key] !== "boolean")
      || value.desired_state !== (value.enabled ? "ON" : "OFF")
      || !boundedCount(value.room_count) || value.room_count > 32
      || !boundedCount(value.allowed_user_count) || value.allowed_user_count > 32
      || !Array.isArray(value.issues) || value.issues.length > 16 || value.issues.some(code => !RUNTIME_ISSUES.has(code))) throw new Error();
  const source = value.worker;
  if (!plain(source) || !WORKER_CODES.has(source.code)
      || ["present", "fresh", "healthy"].some(key => typeof source[key] !== "boolean")) throw new Error();
  const worker = { present: source.present, fresh: source.fresh, healthy: source.healthy, code: source.code };
  for (const key of ["account_matches", "revision_matches", "selection_matches", "enabled", "connected"]) {
    if (Object.hasOwn(source, key)) {
      if (typeof source[key] !== "boolean") throw new Error();
      worker[key] = source[key];
    }
  }
  for (const key of ["observed_at", "age_seconds", "max_age_seconds"]) {
    if (Object.hasOwn(source, key)) {
      if (!nonnegative(source[key])) throw new Error();
      worker[key] = source[key];
    }
  }
  if (Object.hasOwn(source, "counts")) {
    if (!plain(source.counts)) throw new Error();
    worker.counts = {};
    for (const key of WORKER_COUNTS) {
      if (Object.hasOwn(source.counts, key)) {
        if (!boundedCount(source.counts[key])) throw new Error();
        worker.counts[key] = source.counts[key];
      }
    }
  }
  const now = Date.now() / 1000;
  if (worker.fresh && (!worker.present || !(worker.observed_at > 0) || worker.observed_at > now + 5
      || worker.max_age_seconds !== 90 || !nonnegative(worker.age_seconds))) throw new Error();
  if (worker.healthy && (!worker.fresh || !worker.enabled || !worker.connected || !worker.account_matches
      || !worker.revision_matches || !worker.selection_matches
      || !["talk-poll-ok", "talk-paired-no-history-replay"].includes(worker.code))) throw new Error();
  if (value.connected !== (["active", "degraded"].includes(value.state))
      || (value.connected && (!value.enabled || !worker.healthy))
      || (value.state === "disabled" && value.enabled)) throw new Error();
  if (value.connection_revision !== connection.revision) {
    return unavailableRuntime(connection, "talk-worker-configuration-not-observed");
  }
  const report = { schema_version: 1, mode: value.mode, channel: value.channel, connection_revision: value.connection_revision,
    state: value.state, ok: value.ok, configured: value.configured, enabled: value.enabled, connected: value.connected,
    desired_state: value.desired_state, room_count: value.room_count, allowed_user_count: value.allowed_user_count,
    worker, issues: [...new Set(value.issues)] };
  if (worker.fresh) {
    worker.age_seconds = Math.max(worker.age_seconds, now - worker.observed_at, 0);
    if (worker.age_seconds > worker.max_age_seconds) {
      worker.fresh = false; worker.healthy = false; report.connected = false;
      if (["active", "degraded", "failed"].includes(report.state)) {
        report.state = "stale"; report.ok = false;
        report.issues = [...new Set([...report.issues, "talk-worker-status-stale"])];
      }
    }
  }
  return report;
}

async function readRuntime(connection, execute) {
  let deadline;
  try {
    // The gateway already owns this telemetry mount. Keep the config owner's
    // filesystem isolation and reuse the registered read-only status path.
    // The backend can already consume 45s of the browser's 50s request limit.
    // Bound ancillary waiting independently of the subprocess kill grace. The
    // race also handles a later rejection after this read has timed out.
    const result = await Promise.race([
      execute(
        { executable: "/opt/openclaw-agent/scripts/assistant.sh", argv: ["talk", "status"], stdin: null },
        { tool_timeout_seconds: 5, max_capture_bytes: 32 * 1024, max_error_bytes: 1024 },
      ),
      new Promise((_resolve, reject) => { deadline = setTimeout(() => reject(new Error()), 2000); }),
    ]);
    // Exit one is a valid unhealthy diagnostic, not a transport failure.
    if (![0, 1].includes(result.returncode) || result.stdout_truncated || typeof result.stdout !== "string"
        || Buffer.byteLength(result.stdout) > 32 * 1024) throw new Error();
    return projectRuntime(JSON.parse(result.stdout), connection);
  } catch {
    // A telemetry read never reverses a successful save, nor echoes stderr.
    return unavailableRuntime(connection);
  } finally {
    clearTimeout(deadline);
  }
}

export function registerSetupUi(api, options = {}) {
  const backend = options.backend ?? setupBackendRequest;
  const runtimeExecute = options.runtimeExecute ?? spawnJson;
  api.registerGatewayMethod(SETUP_RPC, async ({ params, client, respond }) => {
    try {
      const actor = operatorActor(client);
      const request = validateSetupRequest(params);
      const proof = validateSetupProof(params.proof, request, actor);
      const result = await backend({ ...request, actor, proof });
      if (RUNTIME_OPERATIONS.has(request.operation) && result.connected === true) {
        respond(true, { ...result, talk_runtime: await readRuntime(result, runtimeExecute) });
      } else respond(true, result);
    } catch (error) {
      // Transport/system errors never expose socket paths or stack traces.
      respond(false, undefined, error instanceof SetupError
        ? { code: error.code, message: error.message }
        : { code: "UNAVAILABLE", message: "Die Einrichtung konnte nicht abgeschlossen werden. Den Status prüfen." });
    }
  }, { scope: "operator.admin" });

  let basePath = api.config?.gateway?.controlUi?.basePath ?? "";
  if (typeof basePath !== "string" || (basePath && !/^\/(?:[a-zA-Z0-9._~-]+\/?)*$/.test(basePath))) basePath = "";
  basePath = basePath.replace(/\/$/, "");
  const files = [
    ["", "setup-launch.html", "text/html; charset=utf-8"],
    ["/setup", "setup-ui.html", "text/html; charset=utf-8"],
    ["/setup-ui.css", "setup-ui.css", "text/css; charset=utf-8"],
    ["/setup-ui-browser.js", "setup-ui-browser.js", "text/javascript; charset=utf-8"],
    ["/setup-ui-auth.js", "setup-ui-auth.js", "text/javascript; charset=utf-8"],
  ];
  for (const [suffix, filename, contentType] of files) {
    const content = Buffer.from(readFileSync(new URL(filename, import.meta.url), "utf8")
      .replaceAll("__CONTROL_UI_BASE_PATH__", escapeHtml(basePath)));
    api.registerHttpRoute({ path: SETUP_PATH + suffix, auth: "plugin", match: "exact", handler: (req, res) => {
      if (!["GET", "HEAD"].includes(req.method)) {
        res.writeHead(405, { Allow: "GET, HEAD", "Cache-Control": "no-store" }); res.end(); return true;
      }
      res.writeHead(200, { "Content-Type": contentType, "Content-Length": content.length,
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors " + (suffix === "" ? "'self'" : "'none'"),
      });
      res.end(req.method === "HEAD" ? undefined : content);
      return true;
    } });
  }
  const descriptor = { id: "personal-assistant-nextcloud", surface: "tab", label: "Nextcloud einrichten",
    description: "Nextcloud verbinden, verfügbare Ressourcen prüfen und Talk einrichten.",
    path: SETUP_PATH, requiredScopes: ["operator.admin"], order: 40 };
  if (typeof api.session?.controls?.registerControlUiDescriptor === "function") api.session.controls.registerControlUiDescriptor(descriptor);
  else if (typeof api.registerControlUiDescriptor === "function") api.registerControlUiDescriptor(descriptor);
  else api.logger?.warn?.("Nextcloud setup: Control UI descriptor API unavailable; direct setup route remains available.");
}
