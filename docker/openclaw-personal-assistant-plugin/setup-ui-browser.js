import { createSetupGateway } from "./setup-ui-auth.js";

// All server names, resource metadata and errors are untrusted text, never HTML.
const $ = id => document.getElementById(id);
const state = { authenticated: false, busy: false, current: null, flow: null, ready: null, participantRooms: "" };
let gateway;
function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = String(text);
  if (className) result.className = className;
  return result;
}
function message(text) { $("notice").textContent = text; }
function failure(error) { $("error").textContent = error?.message || "Die Einrichtung konnte nicht abgeschlossen werden."; $("error").hidden = false; }
function disconnected() {
  state.authenticated = false; $("actions").disabled = true;
  $("connect").disabled = false; $("reload").hidden = true;
  $("auth-status").textContent = "Nicht verbunden – erneut mit OpenClaw verbinden";
}
async function run(action) {
  if (state.busy) return;
  state.busy = true; $("actions").disabled = true; $("reload").disabled = true; $("connect").disabled = true;
  $("error").hidden = true; message("Bitte warten …");
  try { await action(); }
  catch (error) { message(""); failure(error); }
  finally {
    state.busy = false; $("actions").disabled = !state.authenticated;
    $("reload").disabled = false; $("connect").disabled = state.authenticated;
  }
}
function checked(container) {
  return Array.from($(container).querySelectorAll("input[type=checkbox]:checked"), input => input.value);
}
function choice(value, text, selected, disabled = false) {
  const label = node("label", undefined, "choice"); const input = node("input");
  input.type = "checkbox"; input.value = value; input.checked = selected; input.disabled = disabled;
  label.append(input, node("span", text)); return label;
}
function issues(target, value) {
  if (value.complete !== true) target.append(node("p", "Die Erkennung ist unvollständig. Nicht angezeigte Ressourcen sind nicht als fehlend bestätigt.", "error"));
  const errors = Array.isArray(value.errors) ? value.errors : [];
  const limits = Array.isArray(value.limitations) ? value.limitations : [];
  if (errors.length || limits.length) {
    const list = node("ul");
    for (const error of errors) list.append(node("li", typeof error === "string" ? error : [error.domain, error.code, error.message].filter(Boolean).join(": ")));
    for (const limit of limits) list.append(node("li", String(limit)));
    target.append(list);
  }
  if (value.results_may_be_truncated) target.append(node("p", "Die Ergebnisliste kann gekürzt sein.", "muted"));
}
function resourceText(item) {
  const rights = [];
  if (item.can_read === true) rights.push("lesen");
  if (item.can_create === true) rights.push("erstellen");
  if (item.can_update === true) rights.push("ändern");
  if (item.can_send === true) rights.push("antworten");
  return `${item.name || item.path || item.resource_id || item.token || "Ressource"}${rights.length ? " · " + rights.join(", ") : " · Rechte nicht bestätigt"}`;
}
function renderPreview(preview) {
  const target = $("preview"); target.replaceChildren();
  target.append(node("p", `Konto: ${preview.user_id || "unbekannt"} · ${preview.server_url || ""}`));
  issues(target, preview);
  for (const [key, label] of [["calendars", "Kalender"], ["task_lists", "Aufgabenlisten"], ["addressbooks", "Adressbücher"],
    ["file_roots", "Dateibereiche"], ["folders", "Ordner auf oberster Ebene"], ["talk_rooms", "Talk-Räume"]]) {
    const items = Array.isArray(preview[key]) ? preview[key] : [];
    const details = node("details"); details.open = true;
    details.append(node("summary", `${label} (${items.length})`));
    if (!items.length) details.append(node("p", "Keine Einträge in dieser Vorschau.", "muted"));
    const list = node("ul"); for (const item of items) list.append(node("li", resourceText(item)));
    details.append(list); target.append(details);
  }
  target.append(node("p", "Unterordner werden beim Durchsuchen nachgeladen. Die Erkennung ändert keine Berechtigungen auf dem Nextcloud-Server.", "muted"));
  $("preview-section").hidden = false;
}
function renderTalk(current) {
  const rooms = current.preview?.talk_rooms || [];
  const selected = new Set(current.talk?.room_tokens || []);
  $("talk-rooms").replaceChildren(); $("talk-users").replaceChildren(); state.participantRooms = "";
  for (const room of rooms) $("talk-rooms").append(choice(room.token, resourceText(room), selected.has(room.token) && room.can_send === true, room.can_send !== true));
  if (!rooms.length) $("talk-rooms").append(node("p", "Keine Talk-Räume erfasst. Ressourcen neu erkennen oder das Konto in Nextcloud zu einem Raum hinzufügen."));
  $("talk-enabled").checked = current.talk?.enabled === true;
  $("talk-status").textContent = current.talk?.enabled
    ? "Talk ist für die gespeicherten Räume und Benutzer aktiviert. Zum Ändern der Auswahl zuerst die Teilnehmer laden."
    : "Talk ist deaktiviert. Die Aktivierung benötigt eine ausdrückliche Raum- und Benutzerauswahl.";
  $("talk-section").hidden = false;
}
function renderStatus(current) {
  state.current = current;
  $("current").textContent = current.connected
    ? `Verbunden mit ${current.server_url} als ${current.user_id}. Letzte Prüfung: ${current.last_verified_at || "unbekannt"}.`
    : "Noch keine Nextcloud-Verbindung gespeichert.";
  if (current.connected && !state.flow) $("server-url").value = current.server_url;
  $("refresh").hidden = !current.connected;
  $("folders-section").hidden = !current.connected;
  $("talk-section").hidden = !current.connected;
  if (current.preview && !state.ready) renderPreview(current.preview);
  if (current.connected) renderTalk(current);
}
function clearFlow() {
  state.flow = null; state.ready = null; $("login-pending").hidden = true;
  $("login-link").removeAttribute("href"); $("apply-panel").hidden = true;
  $("confirm-access").checked = false; $("apply").disabled = true;
}
async function loadStatus() {
  const current = await gateway.call("status");
  const previewInvalidated = state.ready && current.revision !== state.ready.expected_revision;
  if (previewInvalidated) clearFlow();
  renderStatus(current);
  message(previewInvalidated
    ? "Der gespeicherte Verbindungsstand hat sich geändert. Die alte Einrichtungsvorschau wurde geschlossen; der aktuelle Status wird angezeigt."
    : "Gespeicherter Verbindungsstatus geladen.");
}

$("connect").addEventListener("click", () => run(async () => {
  if (window.top !== window.self) throw new Error("Diese Einrichtung bitte über den Link in einem eigenen OpenClaw-Tab öffnen.");
  gateway?.close();
  gateway = createSetupGateway({ origin: location.origin, basePath: document.body.dataset.controlUiBasePath || "",
    storage: window.localStorage, onClose: disconnected });
  await gateway.connect(); state.authenticated = true;
  $("auth-status").textContent = "Als OpenClaw-Administrator verbunden"; $("reload").hidden = false;
  await loadStatus();
}));
$("reload").addEventListener("click", () => run(loadStatus));
$("login-form").addEventListener("submit", event => { event.preventDefault(); return run(async () => {
  const server = new URL($("server-url").value.trim());
  if (server.protocol !== "https:" || server.username || server.password || server.search || server.hash) throw new Error("Eine HTTPS-Serveradresse ohne Zugangsdaten, Suchparameter oder Fragment eingeben.");
  const result = await gateway.call("login.start", { server_url: server.toString() });
  clearFlow(); state.flow = result.flow_id;
  const login = new URL(result.login_url);
  if (login.protocol !== "https:" || login.origin !== server.origin || login.username || login.password) throw new Error("Die Nextcloud-Anmeldeadresse ist ungültig.");
  $("login-link").href = login.toString(); $("login-pending").hidden = false;
  $("preview-section").hidden = true; $("talk-section").hidden = true;
  message("Die Nextcloud-Anmeldung ist bereit. Über den Link öffnen und anschließend hier prüfen.");
}); });
$("poll").addEventListener("click", () => run(async () => {
  if (!state.flow) throw new Error("Zuerst eine Nextcloud-Anmeldung starten.");
  const result = await gateway.call("login.poll", { flow_id: state.flow });
  if (result.state === "ready") {
    state.ready = result; renderPreview(result.preview);
    $("apply-panel").hidden = false; $("refresh").hidden = true;
    $("confirm-access").checked = false; $("apply").disabled = true;
    message("Anmeldung bestätigt. Prüfe die Ressourcen und bestätige die Verbindung.");
  } else message(result.state === "expired" ? "Die Anmeldung ist abgelaufen. Bitte eine neue Anmeldung starten." : "Die Bestätigung von Nextcloud steht noch aus. Nach der Anmeldung erneut prüfen.");
}));
$("cancel").addEventListener("click", () => run(async () => {
  if (state.flow) await gateway.call("login.cancel", { flow_id: state.flow });
  clearFlow(); $("preview-section").hidden = true; await loadStatus(); message("Anmeldung abgebrochen.");
}));
$("confirm-access").addEventListener("change", () => { $("apply").disabled = !$("confirm-access").checked || !state.ready; });
$("apply").addEventListener("click", () => run(async () => {
  if (!state.ready || !$("confirm-access").checked) throw new Error("Die angezeigten Kontoressourcen zuerst bestätigen.");
  const result = await gateway.call("connection.apply", { flow_id: state.flow, preview_digest: state.ready.preview_digest,
    expected_revision: state.ready.expected_revision, confirm_account_access: true });
  clearFlow(); renderStatus(result); message("Nextcloud-Verbindung gespeichert und geprüft. Talk kann jetzt separat eingerichtet werden.");
}));
$("refresh").addEventListener("click", () => run(async () => {
  const result = await gateway.call("connection.refresh", { expected_revision: state.current.revision });
  renderStatus(result); message("Ressourcen erneut erkannt. Angezeigte Einschränkungen bitte beachten.");
}));
async function browse(path) {
  const result = await gateway.call("folders.list", { path });
  $("folder-path").value = result.path || ""; $("folders").replaceChildren(); issues($("folders"), result);
  const list = node("ul");
  for (const item of result.folders || []) {
    const entry = node("li"); const button = node("button", resourceText(item), "secondary"); button.type = "button";
    button.addEventListener("click", () => run(() => browse(item.path)));
    entry.append(button); list.append(entry);
  }
  $("folders").append(list); message("Ordner geladen.");
}
$("folder-form").addEventListener("submit", event => { event.preventDefault(); return run(() => browse($("folder-path").value)); });
$("talk-rooms").addEventListener("change", () => { state.participantRooms = ""; $("talk-users").replaceChildren(); });
$("participants").addEventListener("click", () => run(async () => {
  const rooms = checked("talk-rooms").sort();
  if (!rooms.length || rooms.length > 16) throw new Error("Zwischen einem und 16 Talk-Räumen auswählen.");
  const users = new Map();
  for (const token of rooms) {
    const result = await gateway.call("talk.participants", { room_token: token });
    for (const user of result.participants || []) if (user.user_id !== state.current.user_id) users.set(user.user_id, user);
  }
  $("talk-users").replaceChildren(node("h3", "Erlaubte Benutzer"));
  const selected = new Set(state.current.talk?.allowed_user_ids || []);
  for (const [id, user] of users) $("talk-users").append(choice(id, `${user.name} (${id})`, selected.has(id)));
  if (!users.size) $("talk-users").append(node("p", "Keine weiteren angemeldeten Benutzer in diesen Räumen gefunden."));
  state.participantRooms = JSON.stringify(rooms); message("Teilnehmer geladen. Die erlaubten Benutzer ausdrücklich auswählen.");
}));
$("save-talk").addEventListener("click", () => run(async () => {
  const enabled = $("talk-enabled").checked; const rooms = checked("talk-rooms").sort(); const users = checked("talk-users");
  if (enabled && (state.participantRooms !== JSON.stringify(rooms) || !users.length || users.length > 32)) throw new Error("Teilnehmer der aktuellen Raumauswahl laden und zwischen einem und 32 Benutzern auswählen.");
  const result = await gateway.call("talk.configure", { enabled, room_tokens: rooms, allowed_user_ids: users, expected_revision: state.current.revision });
  renderStatus(result); message(enabled ? "Talk-Auswahl gespeichert und aktiviert." : "Talk deaktiviert.");
}));
window.addEventListener("pagehide", () => gateway?.close());
