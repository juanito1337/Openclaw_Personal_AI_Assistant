/** Browser adapter for OpenClaw v2026.7.1, protocol 4 and v2 device signatures.
 * Uses only the existing, gateway-scoped operator device session. Never creates
 * identities, migrates tokens, requests broader scopes or reads gateway secrets.
 * Sources: ui/src/lib/nodes/index.ts, ui/src/app/gateway-scope.ts,
 * ui/src/api/gateway.ts and packages/gateway-client/src/device-auth.ts at the pin.
 */
const OPERATIONS = new Set(["status", "login.start", "login.poll", "login.cancel",
  "connection.apply", "connection.refresh", "folders.list", "talk.participants", "talk.configure"]);

export function gatewayEndpoint(origin, basePath = "") {
  const url = new URL(origin);
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)))
      || url.username || url.password || (basePath && !/^\/(?:[a-zA-Z0-9._~-]+\/?)*$/.test(basePath))) {
    throw new Error("Die Einrichtung benötigt HTTPS oder eine lokale OpenClaw-Verbindung.");
  }
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = basePath || "/";
  url.search = ""; url.hash = "";
  return url.toString();
}

export function credentialStorageKey(endpoint) {
  // Exact normalizeGatewayCredentialScope behavior at the pinned core tag.
  const url = new URL(endpoint);
  const path = url.pathname === "/" ? "" : url.pathname.replace(/\/+$/, "") || url.pathname;
  return `openclaw.device.auth.v1:${url.protocol}//${url.host}${path}${url.search}`;
}

function decode(value) {
  if (typeof value !== "string" || !/^[a-zA-Z0-9_-]+$/.test(value)) throw new Error("invalid-device-key");
  return Uint8Array.from(atob(value.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - value.length % 4) % 4)), character => character.charCodeAt(0));
}
function encode(bytes) {
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

export async function readOperatorIdentity(storage, endpoint, cryptoApi) {
  try {
    if (!cryptoApi?.subtle) throw new Error("no-crypto");
    const identity = JSON.parse(storage.getItem("openclaw-device-identity-v1") || "null");
    const store = JSON.parse(storage.getItem(credentialStorageKey(endpoint)) || "null");
    const entry = store?.tokens?.operator;
    if (identity?.version !== 1 || store?.version !== 1 || store.deviceId !== identity.deviceId
        || typeof identity.deviceId !== "string" || !/^[a-f0-9]{64}$/.test(identity.deviceId)
        || entry?.role !== "operator" || typeof entry.token !== "string" || !entry.token || entry.token.length > 8192
        || !Array.isArray(entry.scopes) || entry.scopes.length > 32
        || entry.scopes.some(scope => typeof scope !== "string" || !/^[a-zA-Z0-9_.-]{1,64}$/.test(scope))
        || !entry.scopes.includes("operator.admin")) throw new Error("no-admin-session");
    const publicBytes = decode(identity.publicKey);
    const seed = decode(identity.privateKey);
    if (publicBytes.length !== 32 || seed.length !== 32) throw new Error("invalid-device-key");
    const digest = new Uint8Array(await cryptoApi.subtle.digest("SHA-256", publicBytes));
    if (Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("") !== identity.deviceId) throw new Error("invalid-device-id");
    const pkcs8 = new Uint8Array(48);
    pkcs8.set([0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20]);
    pkcs8.set(seed, 16);
    let signingKey;
    try { signingKey = await cryptoApi.subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" }, false, ["sign"]); }
    finally { seed.fill(0); pkcs8.fill(0); }
    return { id: identity.deviceId, publicKey: identity.publicKey, signingKey, token: entry.token, scopes: [...entry.scopes] };
  } catch {
    throw new Error("Keine passende OpenClaw-Administratoranmeldung gefunden. OpenClaw im selben Browser und unter derselben Adresse öffnen, anmelden und hier erneut verbinden. Ein aktueller Browser mit Ed25519-Unterstützung ist erforderlich.");
  }
}

export async function connectParameters(identity, nonce, cryptoApi, browser = {}, now = Date.now()) {
  if (typeof nonce !== "string" || !nonce || nonce.length > 512 || /[\x00-\x1f|]/.test(nonce)) throw new Error("Ungültige OpenClaw-Anmeldeaufforderung.");
  const payload = ["v2", identity.id, "openclaw-control-ui", "webchat", "operator",
    identity.scopes.join(","), String(now), identity.token, nonce].join("|");
  const signature = await cryptoApi.subtle.sign("Ed25519", identity.signingKey, new TextEncoder().encode(payload));
  return {
    minProtocol: 4, maxProtocol: 4,
    client: { id: "openclaw-control-ui", version: "personal-assistant-setup", platform: browser.platform || "web", mode: "webchat" },
    role: "operator", scopes: identity.scopes,
    device: { id: identity.id, publicKey: identity.publicKey, signature: encode(new Uint8Array(signature)), signedAt: now, nonce },
    caps: [], auth: { token: identity.token, deviceToken: identity.token },
    userAgent: browser.userAgent || "", locale: browser.language || "de",
  };
}

export async function signSetupOperation(identity, operation, parameters, cryptoApi, now = Date.now()) {
  if (!OPERATIONS.has(operation)) throw new Error("Unbekannte Einrichtungsoperation.");
  const nonce = Array.from(cryptoApi.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
  const payload = JSON.stringify({ operation, parameters, actor: `device:${identity.id}`, issued_at: now, nonce });
  const signature = await cryptoApi.subtle.sign("Ed25519", identity.signingKey, new TextEncoder().encode(payload));
  return { payload, signature: encode(new Uint8Array(signature)) };
}

export function createSetupGateway({ origin, basePath = "", storage, cryptoApi = globalThis.crypto,
  WebSocketClass = globalThis.WebSocket, browser = globalThis.navigator ?? {}, onClose = () => {} }) {
  const endpoint = gatewayEndpoint(origin, basePath);
  let socket = null;
  let connected = false;
  let connectInFlight = false;
  let operatorIdentity = null;
  const pending = new Map();
  const disconnected = () => {
    connected = false; operatorIdentity = null;
    for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error("OpenClaw-Verbindung unterbrochen. Status vor einer erneuten Änderung prüfen.")); }
    pending.clear(); onClose();
  };
  function request(method, params) {
    if (!socket || socket.readyState !== 1) return Promise.reject(new Error("OpenClaw ist nicht verbunden."));
    return new Promise((resolve, reject) => {
      const id = cryptoApi.randomUUID();
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("OpenClaw antwortet nicht. Status prüfen; Änderungen werden nicht automatisch wiederholt.")); }, 50_000);
      pending.set(id, { resolve, reject, timer });
      try { socket.send(JSON.stringify({ type: "req", id, method, params })); }
      catch { clearTimeout(timer); pending.delete(id); reject(new Error("OpenClaw-Anfrage konnte nicht gesendet werden.")); }
    });
  }
  return {
    async connect() {
      if (connected || connectInFlight) throw new Error("Eine OpenClaw-Verbindung besteht bereits oder wird aufgebaut.");
      connectInFlight = true;
      try {
        const identity = await readOperatorIdentity(storage, endpoint, cryptoApi);
        await new Promise((resolve, reject) => {
          let challengeHandled = false;
          let done = false;
          const timer = setTimeout(() => finish(new Error("OpenClaw-Anmeldung hat nicht rechtzeitig geantwortet.")), 15_000);
          const finish = error => {
            if (done) return; done = true; clearTimeout(timer);
            if (error) { socket?.close(); reject(error); } else resolve();
          };
          try { socket = new WebSocketClass(endpoint); }
          catch { finish(new Error("Der Browser konnte keine sichere OpenClaw-Verbindung öffnen.")); return; }
          socket.addEventListener("message", async event => {
            try {
              if (typeof event.data !== "string" || event.data.length > 3 * 1024 * 1024) return;
              const value = JSON.parse(event.data);
              if (value.type === "event" && value.event === "connect.challenge" && !challengeHandled) {
                challengeHandled = true;
                const parameters = await connectParameters(identity, value.payload?.nonce, cryptoApi, browser);
                const hello = await request("connect", parameters);
                if (!hello?.auth?.scopes?.includes("operator.admin")) throw new Error("OpenClaw hat dieser Sitzung keine Administratorrechte erteilt.");
                operatorIdentity = identity; connected = true; finish();
              } else if (value.type === "res" && pending.has(value.id)) {
                const item = pending.get(value.id); pending.delete(value.id); clearTimeout(item.timer);
                if (value.ok === true) item.resolve(value.payload);
                else item.reject(new Error(typeof value.error?.message === "string" ? value.error.message.slice(0, 1024) : "OpenClaw hat die Anfrage abgelehnt."));
              }
            } catch (error) { if (!connected) finish(error); }
          });
          socket.addEventListener("close", () => { finish(new Error("OpenClaw-Verbindung geschlossen. In OpenClaw anmelden und erneut verbinden.")); disconnected(); });
          socket.addEventListener("error", () => finish(new Error("OpenClaw ist nicht erreichbar.")));
        });
      } finally { connectInFlight = false; }
    },
    async call(operation, parameters = {}) {
      if (!connected) throw new Error("Zuerst mit OpenClaw verbinden.");
      if (!OPERATIONS.has(operation)) throw new Error("Unbekannte Einrichtungsoperation.");
      const proof = await signSetupOperation(operatorIdentity, operation, parameters, cryptoApi);
      return request("personal_assistant.setup", { operation, parameters, proof });
    },
    close() { socket?.close(); connected = false; operatorIdentity = null; },
  };
}
