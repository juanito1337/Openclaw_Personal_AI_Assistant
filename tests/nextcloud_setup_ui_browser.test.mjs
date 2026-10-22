import test from "node:test";
import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, verify, webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";

// A minimal DOM runs the actual UI module and event handlers without a browser
// dependency. The separate auth tests exercise its real cryptography/transport.
class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.value = ""; this.hidden = false; this.disabled = false; this.checked = false; this.dataset = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map(child => child.textContent).join(""); }
  set innerHTML(_value) { throw new Error("Remote content must not become HTML"); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.text = ""; }
  addEventListener(event, handler) { this.events.set(event, handler); }
  removeAttribute(name) { delete this[name]; }
  querySelectorAll(selector) {
    const result = [];
    for (const child of this.children) {
      if (selector === "input[type=checkbox]:checked" && child.tag === "input" && child.type === "checkbox" && child.checked) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
  async trigger(event) { return this.events.get(event)?.({ preventDefault() {} }); }
}

test("actual UI completes login, preview confirmation, folder browsing and explicit Talk selection", async () => {
  const keys = generateKeyPairSync("ed25519");
  const publicBytes = keys.publicKey.export({ type: "spki", format: "der" }).subarray(-32);
  const deviceId = createHash("sha256").update(publicBytes).digest("hex");
  const storageData = new Map([
    ["openclaw-device-identity-v1", JSON.stringify({ version: 1, deviceId, publicKey: publicBytes.toString("base64url"), privateKey: keys.privateKey.export({ type: "pkcs8", format: "der" }).subarray(-32).toString("base64url") })],
    ["openclaw.device.auth.v1:wss://openclaw.example.test", JSON.stringify({ version: 1, deviceId, tokens: { operator: { role: "operator", token: "synthetic-ui-token", scopes: ["operator.admin"] } } })],
  ]);
  const preview = { user_id: "agent", server_url: "https://cloud.example.test/", complete: true, errors: [], limitations: [],
    calendars: [{ name: "<img src=x onerror=alert(1)>", can_read: true }], task_lists: [], addressbooks: [], file_roots: [], folders: [],
    talk_rooms: [{ token: "roomA", name: "Privater Raum", can_read: true, can_send: true }] };
  let current = { connected: false, revision: "" };
  const operations = [];
  class Socket extends EventTarget {
    readyState = 1;
    constructor(url) { super(); assert.equal(url, "wss://openclaw.example.test/"); queueMicrotask(() => this.frame({ type: "event", event: "connect.challenge", payload: { nonce: "browser-ui-challenge" } })); }
    frame(value) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); }
    send(raw) {
      const request = JSON.parse(raw); let payload;
      if (request.method === "connect") payload = { auth: { scopes: ["operator.admin"] } };
      else {
        const { operation, parameters, proof } = request.params;
        assert.equal(request.method, "personal_assistant.setup");
        assert.ok(verify(null, Buffer.from(proof.payload), keys.publicKey, Buffer.from(proof.signature, "base64url")));
        const signed = JSON.parse(proof.payload);
        assert.equal(signed.operation, operation); assert.deepEqual(signed.parameters, parameters); assert.equal(signed.actor, `device:${deviceId}`);
        operations.push({ operation, parameters });
        switch (operation) {
          case "status": payload = current; break;
          case "login.start": payload = { flow_id: "a".repeat(32), state: "pending", login_url: "https://cloud.example.test/login/flow" }; break;
          case "login.poll": payload = { state: "ready", flow_id: "a".repeat(32), preview_digest: "b".repeat(64), expected_revision: "", preview }; break;
          case "connection.apply":
            assert.deepEqual(parameters, { flow_id: "a".repeat(32), preview_digest: "b".repeat(64), expected_revision: "", confirm_account_access: true });
            current = { connected: true, revision: "revision-1", server_url: preview.server_url, user_id: "agent", preview, talk: { enabled: false, room_tokens: [], allowed_user_ids: [] } }; payload = current; break;
          case "folders.list": payload = { complete: true, path: "Projects", folders: [{ name: "Projects", path: "Projects", can_read: true }] }; break;
          case "talk.participants": payload = { room_token: "roomA", participants: [{ user_id: "agent", name: "Agent" }, { user_id: "jan", name: "Jan" }] }; break;
          case "talk.configure":
            assert.deepEqual(parameters, { enabled: true, room_tokens: ["roomA"], allowed_user_ids: ["jan"], expected_revision: "revision-1" });
            current = { ...current, revision: "revision-2", talk: parameters }; payload = current; break;
          default: throw new Error(`Unexpected UI operation ${operation}`);
        }
      }
      queueMicrotask(() => this.frame({ type: "res", id: request.id, ok: true, payload }));
    }
    close() { this.readyState = 3; this.dispatchEvent(new Event("close")); }
  }
  const html = readFileSync(new URL("../docker/openclaw-personal-assistant-plugin/setup-ui.html", import.meta.url), "utf8");
  const elements = new Map(Array.from(html.matchAll(/id="([^"]+)"/g), match => [match[1], new Element()]));
  const fakeWindow = { localStorage: { getItem: key => storageData.get(key) || null }, addEventListener() {} };
  fakeWindow.top = fakeWindow; fakeWindow.self = fakeWindow;
  const replacement = { window: fakeWindow, location: { origin: "https://openclaw.example.test" },
    document: { getElementById: id => elements.get(id), createElement: tag => new Element(tag), body: new Element("body") },
    WebSocket: Socket, crypto: webcrypto };
  const previous = Object.fromEntries(Object.keys(replacement).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  try {
    for (const [key, value] of Object.entries(replacement)) Object.defineProperty(globalThis, key, { configurable: true, value });
    await import("../docker/openclaw-personal-assistant-plugin/setup-ui-browser.js");
    const $ = id => elements.get(id);
    await $("connect").trigger("click"); assert.equal($("actions").disabled, false);
    $("server-url").value = "https://cloud.example.test";
    await $("login-form").trigger("submit"); assert.equal($("login-link").href, "https://cloud.example.test/login/flow");
    await $("poll").trigger("click"); assert.equal($("apply-panel").hidden, false); assert.equal($("apply").disabled, true);
    assert.match($("preview").textContent, /<img src=x onerror=alert\(1\)>/);
    const beforeConfirmation = operations.length;
    await $("apply").trigger("click"); assert.equal(operations.length, beforeConfirmation); assert.equal($("error").hidden, false);
    $("confirm-access").checked = true; await $("confirm-access").trigger("change");
    await $("apply").trigger("click"); assert.equal($("error").hidden, true); assert.equal($("talk-section").hidden, false);
    $("folder-path").value = "Projects"; await $("folder-form").trigger("submit"); assert.match($("folders").textContent, /Projects/);
    const room = $("talk-rooms").children[0].children[0]; room.checked = true; await $("talk-rooms").trigger("change");
    await $("participants").trigger("click");
    const jan = $("talk-users").children[1].children[0]; assert.equal(jan.value, "jan"); jan.checked = true;
    $("talk-enabled").checked = true; await $("save-talk").trigger("click");
    assert.equal($("error").hidden, true); assert.match($("notice").textContent, /aktiviert/);
    assert.deepEqual(operations.map(item => item.operation), ["status", "login.start", "login.poll", "connection.apply", "folders.list", "talk.participants", "talk.configure"]);
  } finally {
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
});
