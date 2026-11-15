import test from "node:test";
import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, verify, webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";

// Execute the shipped UI and real Ed25519 signing against a small DOM/protocol
// fixture. The optional real-browser harness also verifies native form behavior.
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
const password = "synthetic-nextcloud-app-password-only";
const flow = "a".repeat(32);
const digest = "b".repeat(64);
function collection(overrides = {}) {
  return { status: "ok", inventory_complete: true, discovered: 1, checked: 1, readable: 1, failed: 0, skipped: 0,
    checks_complete: true, content_checked: false, scope: "collection-metadata", errors: [], limitations: [], ...overrides };
}
function previewFixture() {
  return { user_id: "agent", display_name: "Agent Account", server_url: "https://cloud.example.test/", checked_at: "2026-10-25T11:30:00Z", complete: true, errors: [], limitations: [],
    diagnostics: { schema_version: 1, user: { status: "ok", loaded: true, code: "" }, calendars: collection(), task_lists: collection(),
      files: { status: "ok", inventory_complete: true, scope: "first-level", depth: 1, root_readable: true, discovered: 2, content_checked: false, recursive: false, errors: [], limitations: [] } },
    calendars: [{ name: "<img src=x onerror=alert(1)>", can_read: true, read_check: { status: "ok", code: "", scope: "collection-metadata", content_checked: false } }],
    task_lists: [], addressbooks: [], file_roots: [], folders: [], talk_rooms: [{ token: "roomA", name: "Privater Raum", can_read: true, can_send: true }] };
}
function talkDiscovery(overrides = {}) {
  return { status: "ok", app_available: true, api_reachable: true, reference_id_supported: true,
    inventory_complete: true, discovered: 2, writable: 1, send_unconfirmed: 0, code: "", errors: [], limitations: [],
    scope: "room-metadata", content_checked: false, ...overrides };
}
function talkCurrent(runtime = {}) {
  const preview = previewFixture(); preview.diagnostics.talk = talkDiscovery();
  return { connected: true, revision: "saved", server_url: preview.server_url, user_id: "agent", preview,
    talk: { enabled: true, room_tokens: ["roomA"], allowed_user_ids: ["jan"] },
    talk_runtime: { schema_version: 1, mode: "managed-account", channel: "nextcloud-account-talk", connection_revision: "saved",
      state: "active", configured: true, enabled: true, connected: true, room_count: 1, allowed_user_count: 1,
      worker: { present: true, fresh: true, healthy: true, account_matches: true, revision_matches: true, selection_matches: true,
        observed_at: 1794744000, age_seconds: 10, max_age_seconds: 90, code: "talk-poll-ok" }, issues: [], ...runtime } };
}
function failedDiagnostics() {
  return { schema_version: 1, user: { status: "failed", loaded: false, code: "authentication-failed" },
    calendars: collection({ status: "skipped", discovered: 0, checked: 0, readable: 0, inventory_complete: false, checks_complete: false }),
    task_lists: collection({ status: "skipped", discovered: 0, checked: 0, readable: 0, inventory_complete: false, checks_complete: false }),
    files: { status: "skipped", root_readable: false, discovered: 0, inventory_complete: false, scope: "first-level", depth: 1, content_checked: false, recursive: false } };
}
function ready(preview = previewFixture(), expected_revision = "") {
  return { state: "ready", flow_id: flow, preview_digest: digest, expected_revision, preview };
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
async function waitFor(predicate) {
  for (let count = 0; count < 100; count++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 2)); }
  assert.fail("UI fixture did not reach its expected state");
}
let moduleSequence = 0;
async function withUi(options, exercise) {
  const keys = generateKeyPairSync("ed25519");
  const publicBytes = keys.publicKey.export({ type: "spki", format: "der" }).subarray(-32);
  const deviceId = createHash("sha256").update(publicBytes).digest("hex");
  const storageData = new Map([
    ["openclaw-device-identity-v1", JSON.stringify({ version: 1, deviceId, publicKey: publicBytes.toString("base64url"), privateKey: keys.privateKey.export({ type: "pkcs8", format: "der" }).subarray(-32).toString("base64url") })],
    ["openclaw.device.auth.v1:wss://openclaw.example.test", JSON.stringify({ version: 1, deviceId, tokens: { operator: { role: "operator", token: "synthetic-ui-token", scopes: ["operator.admin"] } } })],
  ]);
  const initialStorage = [...storageData];
  const preview = options.preview || previewFixture();
  let current = options.current || { connected: false, revision: "" };
  const operations = [], sockets = [];
  class Socket extends EventTarget {
    readyState = 1;
    constructor(url) { super(); assert.equal(url, "wss://openclaw.example.test/"); sockets.push(this); queueMicrotask(() => this.frame({ type: "event", event: "connect.challenge", payload: { nonce: "browser-ui-challenge" } })); }
    frame(value) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); }
    async send(raw) {
      const request = JSON.parse(raw); let payload;
      if (request.method === "connect") payload = { auth: { scopes: ["operator.admin"] } };
      else {
        const { operation, parameters, proof } = request.params;
        assert.equal(request.method, "personal_assistant.setup");
        assert.ok(verify(null, Buffer.from(proof.payload), keys.publicKey, Buffer.from(proof.signature, "base64url")));
        const signed = JSON.parse(proof.payload);
        assert.equal(signed.operation, operation); assert.deepEqual(signed.parameters, parameters); assert.equal(signed.actor, `device:${deviceId}`);
        operations.push({ operation, parameters });
        if (options.respond) payload = await options.respond(operation, parameters);
        if (payload === undefined) switch (operation) {
          case "status": payload = current; break;
          case "login.start": payload = { flow_id: flow, state: "pending", login_url: "https://cloud.example.test/login/flow" }; break;
          case "login.poll": case "login.credentials": payload = ready(preview, current.revision); break;
          case "login.cancel": payload = { state: "cancelled" }; break;
          case "connection.apply":
            assert.deepEqual(parameters, { flow_id: flow, preview_digest: digest, expected_revision: current.revision, confirm_account_access: true });
            current = { connected: true, revision: "revision-1", server_url: preview.server_url, user_id: "agent", last_verified_at: preview.checked_at, preview, talk: { enabled: false, room_tokens: [], allowed_user_ids: [] } }; payload = current; break;
          case "folders.list": payload = { complete: true, path: "Projects", folders: [{ name: "Projects", path: "Projects", can_read: true }] }; break;
          case "talk.participants": payload = { room_token: "roomA", participants: [{ user_id: "agent", name: "Agent" }, { user_id: "jan", name: "Jan" }] }; break;
          case "talk.configure":
            assert.deepEqual(parameters, { enabled: true, room_tokens: ["roomA"], allowed_user_ids: ["jan"], expected_revision: "revision-1" });
            current = { ...current, revision: "revision-2", talk: parameters }; payload = current; break;
          default: throw new Error(`Unexpected UI operation ${operation}`);
        }
      }
      if (request.method === "personal_assistant.setup") now += options.replyDelayMs || 0;
      if (payload instanceof Error) this.frame({ type: "res", id: request.id, ok: false, error: { code: "fixture-rejected", message: payload.message } });
      else this.frame({ type: "res", id: request.id, ok: true, payload });
    }
    close() { this.readyState = 3; this.dispatchEvent(new Event("close")); }
  }
  const html = readFileSync(new URL("../docker/openclaw-personal-assistant-plugin/setup-ui.html", import.meta.url), "utf8");
  const elements = new Map(Array.from(html.matchAll(/id="([^"]+)"/g), match => [match[1], new Element()]));
  const windowEvents = new Map();
  const timers = new Map(); let nextTimer = 0, now = 1000;
  const storage = { getItem: key => storageData.get(key) || null, setItem: () => assert.fail("Setup must not persist credentials in browser storage") };
  const fakeWindow = { localStorage: storage, sessionStorage: storage, addEventListener: (event, handler) => windowEvents.set(event, handler),
    setTimeout: (handler, delay) => { const id = ++nextTimer; timers.set(id, { handler, at: now + delay }); return id; },
    clearTimeout: id => timers.delete(id) };
  const advanceTime = milliseconds => {
    now += milliseconds;
    for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.handler(); }
  };
  fakeWindow.top = fakeWindow; fakeWindow.self = fakeWindow;
  const replacement = { window: fakeWindow, location: { origin: "https://openclaw.example.test" },
    document: { getElementById: id => elements.get(id), createElement: tag => new Element(tag), body: new Element("body") },
    WebSocket: Socket, crypto: webcrypto, performance: { now: () => now } };
  const previous = Object.fromEntries(Object.keys(replacement).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const $ = id => elements.get(id);
  const enterCredentials = (server = "https://cloud.example.test") => { $("server-url").value = server; $("login-name").value = " agent-login "; $("app-password").value = password; };
  const assertNoSecret = () => {
    assert.equal($("app-password").value, "");
    for (const element of elements.values()) assert.equal(element.textContent.includes(password), false);
    assert.deepEqual([...storageData], initialStorage);
  };
  try {
    for (const [key, value] of Object.entries(replacement)) Object.defineProperty(globalThis, key, { configurable: true, value });
    await import(`../docker/openclaw-personal-assistant-plugin/setup-ui-browser.js?fixture=${++moduleSequence}`);
    await $("connect").trigger("click"); assert.equal($("actions").disabled, false);
    await exercise({ $, html, preview, operations, sockets, windowEvents, enterCredentials, assertNoSecret, advanceTime });
  } finally {
    for (const socket of sockets) socket.close();
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
}

test("actual UI completes login, preview confirmation, folder browsing and explicit Talk selection", async () => {
  await withUi({}, async ({ $, operations }) => {
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
  });
});

test("direct app password is signed, cleared before a delayed reply and saved only after preview confirmation", async () => {
  const reply = deferred();
  await withUi({ respond: operation => operation === "login.credentials" ? reply.promise : undefined }, async ({ $, html, operations, enterCredentials, assertNoSecret }) => {
    assert.match(html, /type="password" id="app-password" autocomplete="off"/);
    enterCredentials("https://cloud.example.test/nextcloud");
    const pending = $("credentials-form").trigger("submit");
    assertNoSecret(); // Synchronous: even signing/transport have not completed yet.
    await waitFor(() => operations.some(item => item.operation === "login.credentials"));
    assert.equal($("actions").disabled, true);
    assert.deepEqual(operations.at(-1).parameters, { server_url: "https://cloud.example.test/nextcloud", login_name: "agent-login", app_password: password });
    assert.equal(operations.some(item => item.operation === "connection.apply"), false);
    reply.resolve(ready()); await pending;
    assertNoSecret(); assert.equal($("apply-panel").hidden, false); assert.equal($("apply").disabled, true);
    assert.equal($("flow-actions").hidden, false); assert.equal($("login-pending").hidden, true);
    assert.match($("diagnostics").textContent, /Agent Account \(agent\) · Benutzer geladen/);
    await $("apply").trigger("click"); assert.equal(operations.some(item => item.operation === "connection.apply"), false);
    $("confirm-access").checked = true; await $("confirm-access").trigger("change"); await $("apply").trigger("click");
    assert.deepEqual(operations.at(-1), { operation: "connection.apply", parameters: { flow_id: flow, preview_digest: digest, expected_revision: "", confirm_account_access: true } });
    assertNoSecret(); assert.match($("diagnostics").textContent, /Gespeichertes Prüfergebnis/);
  });
});

test("failed identity shows skipped domains without saving or replacing the connected account", async () => {
  const preview = previewFixture();
  const current = { connected: true, revision: "existing-revision", server_url: preview.server_url, user_id: "previous-account", preview };
  await withUi({ current, respond: operation => operation === "login.credentials" ? { state: "failed", code: "authentication-failed", diagnostics: failedDiagnostics() } : undefined }, async ({ $, operations, enterCredentials, assertNoSecret }) => {
    enterCredentials(); await $("credentials-form").trigger("submit");
    assertNoSecret(); assert.equal($("error").hidden, false); assert.match($("error").textContent, /Anmeldedaten abgewiesen/);
    assert.match($("current").textContent, /previous-account/); assert.equal($("apply-panel").hidden, true);
    assert.equal($("preview-section").hidden, true); assert.equal($("flow-actions").hidden, true);
    const diagnostics = $("diagnostics").textContent;
    assert.match(diagnostics, /BenutzerPrüfung fehlgeschlagen/);
    for (const label of ["Kalender", "Aufgabenlisten", "Ordner"]) assert.ok(diagnostics.includes(`${label}Noch nicht geprüft`));
    assert.doesNotMatch(diagnostics, /Benutzer geladen|Erfolgreich geprüft/);
    assert.deepEqual(operations.map(item => item.operation), ["status", "login.credentials"]);
    await $("reload").trigger("click"); assert.match($("current").textContent, /previous-account/);
  });
});

test("failed apply closes the approval and cannot resubmit it or replace the saved connection", async () => {
  const preview = previewFixture();
  const current = { connected: true, revision: "previous-revision", server_url: preview.server_url, user_id: "previous-account", preview };
  await withUi({ current, respond: operation => operation === "connection.apply" ? new Error("synthetic-publication-conflict") : undefined }, async ({ $, operations, enterCredentials, assertNoSecret }) => {
    const savedDescription = $("current").textContent;
    enterCredentials(); await $("credentials-form").trigger("submit");
    $("confirm-access").checked = true; await $("confirm-access").trigger("change");
    await $("apply").trigger("click");
    assert.equal(operations.filter(item => item.operation === "connection.apply").length, 1);
    assert.equal($("apply-panel").hidden, true); assert.equal($("preview-section").hidden, true);
    assert.equal($("flow-actions").hidden, true); assert.equal($("confirm-access").checked, false); assert.equal($("apply").disabled, true);
    assert.equal($("current").textContent, savedDescription); assertNoSecret();
    assert.match($("error").textContent, /Status neu laden/);
    assert.match($("diagnostics").textContent, /Speicherung konnte nicht bestätigt werden/);
    assert.doesNotMatch($("notice").textContent, /gespeichert und geprüft/);
    // Even a stale/programmatic click and re-check cannot revive the old flow.
    $("confirm-access").checked = true; await $("confirm-access").trigger("change");
    assert.equal($("apply").disabled, true); await $("apply").trigger("click");
    assert.equal(operations.filter(item => item.operation === "connection.apply").length, 1);
    await $("reload").trigger("click");
    assert.equal($("current").textContent, savedDescription);
    assert.match($("diagnostics").textContent, /Gespeichertes Prüfergebnis/);
    assert.equal($("apply-panel").hidden, true);
    assert.deepEqual(operations.map(item => item.operation), ["status", "login.credentials", "connection.apply", "status"]);
  });
});

test("partial and bounded read probes remain distinct from inventory, server rights and content access", async () => {
  const preview = previewFixture(); preview.complete = false;
  preview.diagnostics.calendars = collection({ status: "partial", discovered: 4, checked: 2, readable: 1, failed: 1, skipped: 2, checks_complete: false,
    errors: [{ resource_id: "calendar-b", code: "permission-denied" }], limitations: ["read-check-budget-exhausted"] });
  preview.diagnostics.task_lists = collection({ status: "skipped", discovered: 2, checked: 0, readable: 0, skipped: 2, checks_complete: false });
  preview.calendars[0].read_check = { status: "failed", code: "permission-denied", scope: "collection-metadata", content_checked: false };
  await withUi({ preview }, async ({ $, enterCredentials }) => {
    enterCredentials(); await $("credentials-form").trigger("submit");
    const diagnostics = $("diagnostics").textContent;
    assert.match(diagnostics, /2026-10-25T11:30:00Z/);
    assert.match(diagnostics, /KalenderTeilweise geprüft/); assert.match(diagnostics, /4 gefunden · 1 lesbar · 1 fehlgeschlagen · 2 ungeprüft/);
    assert.match(diagnostics, /AufgabenlistenNoch nicht geprüft/); assert.match(diagnostics, /2 gefunden · 0 lesbar · 0 fehlgeschlagen · 2 ungeprüft/);
    assert.match(diagnostics, /calendar-b: permission-denied/); assert.match(diagnostics, /read-check-budget-exhausted/);
    assert.match(diagnostics, /Termin- und Aufgabeninhalte werden dabei nicht geladen/);
    assert.match(diagnostics, /Auflistung der obersten Ebene vollständig/); assert.match(diagnostics, /Unterordner einzeln/);
    assert.match(diagnostics, /Dateiinhalte wurden nicht geladen/);
    assert.match($("preview").textContent, /Serverrechte: lesen · Leseprüfung: fehlgeschlagen \(permission-denied\)/);
    assert.match($("preview").textContent, /Erkennung ist unvollständig/);
  });
});

test("stored diagnostics and legacy snapshots never claim a current access check", async () => {
  for (const hasDiagnostics of [true, false]) {
    const preview = previewFixture(); if (!hasDiagnostics) delete preview.diagnostics;
    await withUi({ current: { connected: true, revision: "saved", server_url: preview.server_url, user_id: "agent", last_verified_at: "2026-10-20T09:00:00Z", preview } }, async ({ $, operations }) => {
      assert.match($("diagnostics").textContent, /Gespeichertes Prüfergebnis/);
      assert.match($("diagnostics").textContent, /aktuellen Zugriffstest/);
      assert.doesNotMatch($("diagnostics").textContent, /Prüfergebnis dieser Anmeldung/);
      if (!hasDiagnostics) assert.match($("diagnostics").textContent, /noch keine einzelnen Zugriffsprüfungen/);
      assert.deepEqual(operations.map(item => item.operation), ["status"]);
    });
  }
});

test("Talk discovery distinguishes absent, unsupported, incomplete and unknown instead of claiming connection", async () => {
  for (const [check, label, detail] of [
    [talkDiscovery(), "Metadaten erfolgreich geprüft", "2 Räume erfasst · 1 mit bestätigtem Senderecht"],
    [talkDiscovery({ status: "unavailable", app_available: false, api_reachable: null, reference_id_supported: null }), "Talk nicht verfügbar", "Talk-App: nicht verfügbar"],
    [talkDiscovery({ status: "unsupported", reference_id_supported: false }), "Voraussetzung fehlt", "Antwortzuordnung: nicht unterstützt"],
    [talkDiscovery({ status: "failed", app_available: null, api_reachable: null, reference_id_supported: null, code: "transport-error" }), "Prüfung fehlgeschlagen", "Talk-App: nicht bestätigt"],
    [talkDiscovery({ status: "empty", discovered: 0, writable: 0 }), "Keine Räume gefunden", "Raumliste vollständig erfasst"],
    [talkDiscovery({ status: "partial", inventory_complete: false, send_unconfirmed: 1, limitations: ["talk_rooms:resource-limit"] }), "Teilweise geprüft", "1 mit ungeklärtem Senderecht"],
  ]) {
    const preview = previewFixture(); preview.diagnostics.talk = check;
    await withUi({ preview }, async ({ $, enterCredentials, operations }) => {
      enterCredentials(); await $("credentials-form").trigger("submit");
      assert.match($("diagnostics").textContent, new RegExp(`Nextcloud Talk${label}`));
      assert.ok($("diagnostics").textContent.includes(detail));
      assert.match($("diagnostics").textContent, /Keine Chat-Inhalte gelesen und keine Testnachricht gesendet/);
      assert.equal($("talk-section").hidden, true);
      assert.equal(operations.some(item => item.operation === "talk.configure"), false);
    });
  }
});

test("saved Talk activation without worker evidence remains unconfirmed", async () => {
  const current = talkCurrent(); delete current.talk_runtime;
  await withUi({ current }, async ({ $ }) => {
    assert.equal($("talk-status").dataset.state, "unavailable");
    assert.match($("talk-status").textContent, /Verbindung nicht bestätigt/);
    assert.match($("talk-status").textContent, /Gespeicherte Auswahl: 1 Raum/);
    assert.match($("talk-status").textContent, /jan/);
    assert.match($("talk-status").textContent, /Mitgliedschaft.*nicht erneut geprüft/);
  });
});

test("an unavailable runtime report with no trusted revision remains unavailable", async () => {
  await withUi({ current: talkCurrent({ state: "unavailable", connection_revision: "", connected: false,
    worker: { present: false, fresh: false, healthy: false, code: "talk-runtime-unavailable" }, issues: ["talk-runtime-unavailable"] }) }, async ({ $ }) => {
    assert.equal($("talk-status").dataset.state, "unavailable");
    assert.match($("talk-status").textContent, /Verbindung nicht bestätigt/);
  });
});

test("Talk runtime states stay distinct and provide a next step", async () => {
  for (const [status, label] of [
    ["active", "Verbunden"], ["degraded", "Verbindung mit Einschränkungen"], ["disabled", "Deaktiviert"],
    ["not-observed", "Noch keine Rückmeldung"], ["pending", "Neue Einstellung noch nicht bestätigt"],
    ["stale", "Status veraltet"], ["failed", "Verbindung fehlgeschlagen"], ["invalid", "Auswahl unvollständig oder ungültig"],
    ["unavailable", "Verbindung nicht bestätigt"],
  ]) {
    const current = talkCurrent({ state: status });
    if (status === "disabled") { current.talk.enabled = false; current.talk_runtime.enabled = false; }
    await withUi({ current }, async ({ $ }) => {
      assert.equal($("talk-status").dataset.state, status);
      assert.ok($("talk-status").textContent.includes(label));
      assert.match($("talk-status").textContent, /Nächster Schritt:/);
      assert.match($("talk-status").textContent, /keine Zustellbestätigung/);
    });
  }
});

test("healthy Talk status requires matching saved configuration and fresh worker evidence", async () => {
  for (const mutation of [
    current => { current.talk_runtime.connection_revision = "other-revision"; },
    current => { current.talk_runtime.worker.fresh = false; },
    current => { current.talk_runtime.worker.healthy = false; },
    current => { current.talk_runtime.worker.account_matches = false; },
    current => { current.talk_runtime.worker.revision_matches = false; },
    current => { current.talk_runtime.worker.selection_matches = false; },
    current => { current.talk_runtime.worker.age_seconds = 91; },
    current => { current.talk_runtime.worker.age_seconds = null; },
    current => { current.talk_runtime.room_count = 2; },
    current => { current.talk_runtime.enabled = false; },
    current => { current.talk_runtime.worker.code = "talk-poll-failed"; },
  ]) {
    const current = talkCurrent(); mutation(current);
    await withUi({ current }, async ({ $ }) => {
      assert.notEqual($("talk-status").dataset.state, "active");
      assert.doesNotMatch($("talk-status").textContent, /Chat-DienstVerbunden/);
    });
  }
});

test("Talk evidence expires in an open page and status reload obtains a new observation", async () => {
  await withUi({ current: talkCurrent() }, async ({ $, advanceTime, operations }) => {
    assert.equal($("talk-status").dataset.state, "active");
    assert.match($("talk-status").textContent, /Letzte Rückmeldung:/);
    advanceTime(81_000);
    assert.equal($("talk-status").dataset.state, "stale");
    assert.match($("talk-status").textContent, /Status neu laden/);
    assert.deepEqual(operations.map(item => item.operation), ["status"]);
    await $("reload").trigger("click");
    assert.equal($("talk-status").dataset.state, "active");
  });
});

test("network time cannot extend the lifetime of a healthy Talk observation", async () => {
  const current = talkCurrent(); current.talk_runtime.worker.age_seconds = 89;
  await withUi({ current, replyDelayMs: 2000 }, async ({ $ }) => {
    assert.equal($("talk-status").dataset.state, "stale");
  });
});

test("a failed poll becomes historical instead of proving an indefinite outage", async () => {
  const current = talkCurrent({ state: "failed", connected: false });
  Object.assign(current.talk_runtime.worker, { healthy: false, code: "talk-poll-failed" });
  await withUi({ current }, async ({ $, advanceTime }) => {
    assert.equal($("talk-status").dataset.state, "failed");
    advanceTime(81_000);
    assert.equal($("talk-status").dataset.state, "stale");
    assert.match($("talk-status").textContent, /talk-poll-failed/);
  });
});

test("gateway disconnect removes the healthy Talk claim without altering saved selection", async () => {
  await withUi({ current: talkCurrent() }, async ({ $, sockets, advanceTime }) => {
    sockets[0].close(); advanceTime(81_000);
    assert.equal($("talk-status").dataset.state, "unavailable");
    assert.match($("talk-status").textContent, /OpenClaw erneut verbinden/);
    assert.match($("talk-status").textContent, /jan/);
  });
});

test("failed refresh reports the identity failure and keeps the saved account unchanged", async () => {
  const preview = previewFixture();
  await withUi({ current: { connected: true, revision: "saved", server_url: preview.server_url, user_id: "previous-account", preview },
    respond: operation => operation === "connection.refresh" ? { state: "failed", code: "authentication-failed", diagnostics: failedDiagnostics() } : undefined }, async ({ $, operations }) => {
    await $("refresh").trigger("click");
    assert.deepEqual(operations.at(-1), { operation: "connection.refresh", parameters: { expected_revision: "saved" } });
    assert.equal($("error").hidden, false); assert.match($("diagnostics").textContent, /BenutzerPrüfung fehlgeschlagen/);
    assert.match($("current").textContent, /previous-account/); assert.match($("notice").textContent, /unverändert/);
    assert.equal($("apply-panel").hidden, true); assert.doesNotMatch($("notice").textContent, /Ressourcen erneut erkannt/);
    await $("reload").trigger("click"); assert.match($("current").textContent, /previous-account/);
  });
});

test("invalid direct-login input clears password before any credential RPC", async () => {
  for (const server of ["http://cloud.example.test", "https://username:password@cloud.example.test", "https://cloud.example.test/?secret=no", "invalid"]) {
    await withUi({}, async ({ $, operations, enterCredentials, assertNoSecret }) => {
      enterCredentials(server); await $("credentials-form").trigger("submit");
      assertNoSecret(); assert.equal($("error").hidden, false);
      assert.deepEqual(operations.map(item => item.operation), ["status"]);
    });
  }
});

test("cancel, pagehide and disconnect clear a newly typed password without a credential RPC", async () => {
  for (const action of ["cancel", "pagehide", "disconnect"]) {
    await withUi({}, async ({ $, operations, sockets, windowEvents, enterCredentials, assertNoSecret }) => {
      enterCredentials(); await $("credentials-form").trigger("submit");
      $("app-password").value = password;
      if (action === "cancel") await $("cancel").trigger("click");
      else if (action === "pagehide") windowEvents.get("pagehide")();
      else sockets[0].close();
      assertNoSecret(); assert.equal(operations.filter(item => item.operation === "login.credentials").length, 1);
      if (action === "cancel") { assert.equal($("apply-panel").hidden, true); assert.ok(operations.some(item => item.operation === "login.cancel")); }
      else assert.equal($("actions").disabled, true);
    });
  }
});

test("cancel clears a newly typed password before the cancellation RPC completes", async () => {
  const reply = deferred();
  await withUi({ respond: operation => operation === "login.cancel" ? reply.promise : undefined }, async ({ $, operations, enterCredentials, assertNoSecret }) => {
    enterCredentials(); await $("credentials-form").trigger("submit");
    $("app-password").value = password;
    const pending = $("cancel").trigger("click");
    try {
      assertNoSecret();
      await waitFor(() => operations.some(item => item.operation === "login.cancel"));
    } finally { reply.resolve({ state: "cancelled" }); await pending; }
    assertNoSecret();
  });
});

test("Loginv2 identity failure uses the same failed diagnostics without an applicable preview", async () => {
  await withUi({ respond: operation => operation === "login.poll" ? { state: "failed", code: "invalid-user-id", diagnostics: failedDiagnostics() } : undefined }, async ({ $, operations }) => {
    $("server-url").value = "https://cloud.example.test";
    await $("login-form").trigger("submit"); await $("poll").trigger("click");
    assert.equal($("apply-panel").hidden, true); assert.equal($("preview-section").hidden, true);
    assert.match($("diagnostics").textContent, /BenutzerPrüfung fehlgeschlagen/);
    assert.match($("error").textContent, /keinen eindeutigen Benutzer/);
    assert.equal(operations.some(item => item.operation === "connection.apply"), false);
  });
});
