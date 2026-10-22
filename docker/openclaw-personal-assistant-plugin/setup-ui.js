/** Operator-only Nextcloud setup bridge for the pinned OpenClaw 2026.7.1 API.
 * Public HTTP routes serve immutable assets only. All data and mutations use
 * authenticated Gateway RPC; no generic agent tool or HTTP credential bridge.
 */
import http from "node:http";
import { readFileSync } from "node:fs";
import { isDeepStrictEqual } from "node:util";

export const SETUP_RPC = "personal_assistant.setup";
export const SETUP_PATH = "/plugins/personal-assistant-tools/nextcloud";
export const SETUP_OPERATIONS = Object.freeze({
  status: {},
  "login.start": { server_url: "url" },
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

export function registerSetupUi(api, options = {}) {
  const backend = options.backend ?? setupBackendRequest;
  api.registerGatewayMethod(SETUP_RPC, async ({ params, client, respond }) => {
    try {
      const actor = operatorActor(client);
      const request = validateSetupRequest(params);
      const proof = validateSetupProof(params.proof, request, actor);
      respond(true, await backend({ ...request, actor, proof }));
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
