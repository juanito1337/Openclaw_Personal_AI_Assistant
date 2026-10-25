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
  $("app-password").value = "";
  state.authenticated = false; $("actions").disabled = true;
  $("connect").disabled = false; $("reload").hidden = true;
  $("auth-status").textContent = "Nicht verbunden – erneut mit OpenClaw verbinden";
}
async function run(action, { login = false } = {}) {
  if (state.busy) return;
  state.busy = true; $("actions").disabled = true; $("reload").disabled = true; $("connect").disabled = true;
  $("error").hidden = true; message("Bitte warten …");
  try { await action(); }
  catch (error) {
    message(""); failure(error);
    if (login) renderDiagnostics({ diagnostics: {} });
  }
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
  const read = item.read_check;
  const access = read ? ` · Leseprüfung: ${read.status === "ok" ? "erfolgreich" : read.status === "failed" ? "fehlgeschlagen" : "noch nicht geprüft"}${read.code ? " (" + read.code + ")" : ""}` : "";
  return `${item.name || item.path || item.resource_id || item.token || "Ressource"}${rights.length ? " · Serverrechte: " + rights.join(", ") : " · Rechte nicht bestätigt"}${access}`;
}
const diagnosticLabels = { ok: "Erfolgreich geprüft", partial: "Teilweise geprüft", failed: "Prüfung fehlgeschlagen", empty: "Keine Sammlungen gefunden", skipped: "Noch nicht geprüft" };
function count(value) { return Number.isSafeInteger(value) && value >= 0 ? value : "?"; }
function pendingDiagnostics(text) {
  $("diagnostics").replaceChildren(node("p", text, "muted"));
}
function renderDiagnostics(preview, { saved = false, checkedAt = "" } = {}) {
  const target = $("diagnostics"); target.replaceChildren();
  const report = preview?.diagnostics;
  const timestamp = preview?.checked_at || checkedAt;
  target.append(node("p", saved ? "Gespeichertes Prüfergebnis. Für einen aktuellen Zugriffstest „Ressourcen neu erkennen“ wählen." : "Ergebnis der letzten Serverprüfung. Beachte die einzelnen Bereiche unten.", "muted"));
  if (timestamp) target.append(node("p", `Geprüft am: ${timestamp}`, "muted"));
  if (!report) {
    target.append(node("p", "Für diesen Stand liegen noch keine einzelnen Zugriffsprüfungen vor. Ressourcen neu erkennen oder das Konto anmelden.", "muted"));
    return;
  }
  const list = node("div", undefined, "diagnostic-list");
  for (const [key, label] of [["user", "Benutzer"], ["calendars", "Kalender"], ["task_lists", "Aufgabenlisten"], ["files", "Ordner"]]) {
    const check = report[key] || {};
    const status = Object.hasOwn(diagnosticLabels, check.status) ? check.status : "skipped";
    const card = node("div", undefined, `diagnostic ${status}`);
    card.append(node("h3", label), node("strong", diagnosticLabels[status]));
    if (key === "user") {
      card.append(node("p", check.loaded === true ? `${preview.display_name || preview.user_id} (${preview.user_id}) · Benutzer geladen` : "Benutzer konnte noch nicht bestätigt werden."));
    } else if (key === "files") {
      card.append(node("p", check.root_readable === true ? "Kontohauptordner erreichbar und lesbar." : "Lesezugriff auf den Kontohauptordner nicht bestätigt."));
      card.append(node("p", `${count(check.discovered)} Ordner erfasst. ${check.inventory_complete === true ? "Auflistung der obersten Ebene vollständig." : "Auflistung der obersten Ebene nicht vollständig bestätigt."}`));
      card.append(node("p", "Unterordner einzeln unter „Ordner durchsuchen“ prüfen. Dateiinhalte wurden nicht geladen.", "muted"));
    } else {
      card.append(node("p", `${count(check.discovered)} gefunden · ${count(check.readable)} lesbar · ${count(check.failed)} fehlgeschlagen · ${count(check.skipped)} ungeprüft`));
      card.append(node("p", check.inventory_complete === true ? "Liste der Sammlungen vollständig erkannt." : "Vollständigkeit der Sammlungsliste nicht bestätigt."));
      card.append(node("p", "Geprüft wird der Zugriff auf die Sammlung und ihre Metadaten. Termin- und Aufgabeninhalte werden dabei nicht geladen.", "muted"));
    }
    if (check.code) card.append(node("p", `Fehlercode: ${check.code}`, "muted"));
    if (Array.isArray(check.errors) && check.errors.length) {
      const errors = node("ul");
      for (const error of check.errors) errors.append(node("li", [error.resource_id, error.code].filter(Boolean).join(": ")));
      card.append(errors);
    }
    if (Array.isArray(check.limitations) && check.limitations.length) card.append(node("p", `Prüfgrenzen: ${check.limitations.join(", ")}`, "muted"));
    list.append(card);
  }
  target.append(list);
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
    ? `Verbindung gespeichert: ${current.server_url} als ${current.user_id}. Letzte Prüfung: ${current.last_verified_at || "unbekannt"}.`
    : "Noch keine Nextcloud-Verbindung gespeichert.";
  if (current.connected && !state.flow) $("server-url").value = current.server_url;
  $("refresh").hidden = !current.connected;
  $("folders-section").hidden = !current.connected;
  $("talk-section").hidden = !current.connected;
  if (current.preview && !state.ready) {
    renderPreview(current.preview);
    renderDiagnostics(current.preview, { saved: true, checkedAt: current.last_verified_at });
  }
  if (!current.connected && !state.flow) {
    $("preview-section").hidden = true;
    $("diagnostics").replaceChildren(node("p", "Noch nicht geprüft. Melde zuerst das Nextcloud-Konto an.", "muted"));
  }
  if (current.connected) renderTalk(current);
}
function clearFlow() {
  state.flow = null; state.ready = null; $("login-pending").hidden = true;
  $("flow-actions").hidden = true; $("app-password").value = "";
  $("login-link").removeAttribute("href"); $("apply-panel").hidden = true;
  $("confirm-access").checked = false; $("apply").disabled = true;
}
function showReady(result) {
  state.flow = result.flow_id; state.ready = result;
  renderPreview(result.preview); renderDiagnostics(result.preview);
  $("login-pending").hidden = true; $("flow-actions").hidden = false;
  $("apply-panel").hidden = false; $("refresh").hidden = true;
  $("confirm-access").checked = false; $("apply").disabled = true;
  message("Benutzer geladen. Prüfe die Zugriffsdiagnose und die Ressourcen, bevor du die Verbindung bestätigst.");
}
function showLoginFailure(result) {
  clearFlow(); $("preview-section").hidden = true;
  renderDiagnostics({ diagnostics: result.diagnostics || {}, checked_at: result.checked_at });
  const reasons = {
    "authentication-failed": "Nextcloud hat die Anmeldedaten abgewiesen. Anmeldename und App-Passwort prüfen.",
    "permission-denied": "Nextcloud hat die Benutzerabfrage verweigert. Die Zugriffsrechte des Kontos prüfen.",
    "invalid-user-id": "Nextcloud hat keinen eindeutigen Benutzer geliefert. Die Verbindung wurde nicht gespeichert.",
    "account-changed": "Der zurückgemeldete Benutzer passt nicht zum verbundenen Konto. Bitte neu anmelden.",
    "tls-error": "Die TLS-Prüfung ist fehlgeschlagen. Servername und Zertifikat der Nextcloud müssen zusammenpassen.",
    "foreign-origin": "Nextcloud liefert eine andere Serveradresse. Die öffentliche Serveradresse in Nextcloud prüfen.",
    "dns-failed": "Der Nextcloud-Servername konnte nicht aufgelöst werden. Die Serveradresse und DNS-Verbindung prüfen.",
    "transport-error": "Der Nextcloud-Server ist momentan nicht erreichbar. Serveradresse und Netzwerkverbindung prüfen.",
    "request-timeout": "Die Serverprüfung hat zu lange gedauert. Die Erreichbarkeit der Nextcloud prüfen und später erneut anmelden.",
    "rate-limited": "Nextcloud begrenzt derzeit die Anfragen. Bitte warten und die Anmeldung später erneut prüfen.",
    "invalid-credentials": "Anmeldename und App-Passwort haben ein ungültiges Format. Die Eingaben prüfen.",
  };
  failure(new Error(reasons[result.code] || "Die Anmeldung konnte nicht geprüft werden. Serveradresse und Anmeldedaten prüfen."));
  message("Die Anmeldung wurde nicht gespeichert. Nach der Korrektur erneut prüfen.");
}
function selectedServer() {
  const server = new URL($("server-url").value.trim());
  if (server.protocol !== "https:" || server.username || server.password || server.search || server.hash) throw new Error("Eine HTTPS-Serveradresse ohne Zugangsdaten, Suchparameter oder Fragment eingeben.");
  return server;
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
  const server = selectedServer();
  clearFlow();
  pendingDiagnostics("Nextcloud-Anmeldung wird vorbereitet. Benutzer und Ressourcen sind noch nicht geprüft.");
  const result = await gateway.call("login.start", { server_url: server.toString() });
  clearFlow(); state.flow = result.flow_id;
  const login = new URL(result.login_url);
  if (login.protocol !== "https:" || login.origin !== server.origin || login.username || login.password) throw new Error("Die Nextcloud-Anmeldeadresse ist ungültig.");
  $("login-link").href = login.toString(); $("login-pending").hidden = false;
  $("flow-actions").hidden = false;
  $("preview-section").hidden = true; $("talk-section").hidden = true;
  pendingDiagnostics("Warte auf die Bestätigung bei Nextcloud. Danach „Anmeldung prüfen“ wählen, um Benutzer und Ressourcen zu laden.");
  message("Die Nextcloud-Anmeldung ist bereit. Über den Link öffnen und anschließend hier prüfen.");
}, { login: true }); });
$("credentials-form").addEventListener("submit", event => {
  event.preventDefault();
  let password = $("app-password").value;
  $("app-password").value = "";
  return run(async () => {
    try {
      const server = selectedServer();
      const name = $("login-name").value.trim();
      if (!name || !password) throw new Error("Anmeldename und Nextcloud-App-Passwort eingeben.");
      clearFlow(); $("preview-section").hidden = true; $("talk-section").hidden = true;
      pendingDiagnostics("Prüfung läuft: Benutzer, Kalender, Aufgabenlisten und Ordner. Das Ergebnis erscheint nach der Serverantwort.");
      message("Benutzer und Zugriff auf Kalender, Aufgabenlisten und Ordner werden geprüft …");
      const result = await gateway.call("login.credentials", { server_url: server.toString(), login_name: name, app_password: password });
      if (result.state === "ready") showReady(result);
      else if (result.state === "failed") showLoginFailure(result);
      else throw new Error("Die Anmeldung lieferte kein prüfbares Ergebnis.");
    } finally { password = ""; $("app-password").value = ""; }
  }, { login: true });
});
$("poll").addEventListener("click", () => run(async () => {
  if (!state.flow) throw new Error("Zuerst eine Nextcloud-Anmeldung starten.");
  pendingDiagnostics("Anmeldestatus und verfügbare Ressourcen werden abgefragt …");
  const result = await gateway.call("login.poll", { flow_id: state.flow });
  if (result.state === "ready") {
    showReady(result);
  } else if (result.state === "failed") showLoginFailure(result);
  else if (result.state === "expired" || result.state === "cancelled") {
    clearFlow(); pendingDiagnostics("Die Anmeldung ist beendet. Benutzer und Ressourcen wurden nicht bestätigt.");
    message("Die Anmeldung ist abgelaufen oder wurde abgebrochen. Bitte eine neue Anmeldung starten.");
  } else {
    pendingDiagnostics("Warte auf die Bestätigung bei Nextcloud. Benutzer und Ressourcen sind noch nicht geprüft.");
    message("Die Bestätigung von Nextcloud steht noch aus. Nach der Anmeldung erneut prüfen.");
  }
}, { login: true }));
$("cancel").addEventListener("click", () => {
  $("app-password").value = "";
  return run(async () => {
  if (state.flow) await gateway.call("login.cancel", { flow_id: state.flow });
  clearFlow(); $("preview-section").hidden = true; await loadStatus(); message("Anmeldung abgebrochen.");
  });
});
$("confirm-access").addEventListener("change", () => { $("apply").disabled = !$("confirm-access").checked || !state.ready; });
$("apply").addEventListener("click", () => run(async () => {
  if (!state.ready || !$("confirm-access").checked) throw new Error("Die angezeigten Kontoressourcen zuerst bestätigen.");
  let result;
  try {
    result = await gateway.call("connection.apply", { flow_id: state.flow, preview_digest: state.ready.preview_digest,
      expected_revision: state.ready.expected_revision, confirm_account_access: true });
  } catch {
    clearFlow(); $("preview-section").hidden = true;
    pendingDiagnostics("Die Speicherung konnte nicht bestätigt werden. Lade zuerst den Status neu; die bisherige Vorschau ist geschlossen.");
    throw new Error("Die Verbindung konnte nicht bestätigt werden. „Status neu laden“ wählen und den gespeicherten Stand prüfen, bevor du eine neue Anmeldung beginnst.");
  }
  clearFlow(); renderStatus(result); message("Nextcloud-Verbindung gespeichert und geprüft. Talk kann jetzt separat eingerichtet werden.");
}));
$("refresh").addEventListener("click", () => run(async () => {
  pendingDiagnostics("Benutzer und Ressourcen werden erneut auf dem Nextcloud-Server geprüft …");
  const result = await gateway.call("connection.refresh", { expected_revision: state.current.revision });
  if (result.state === "failed") {
    showLoginFailure(result);
    message("Die erneute Prüfung ist fehlgeschlagen. Die bisher gespeicherte Verbindung bleibt unverändert; ihr alter Prüfstand belegt keinen aktuellen Zugriff.");
    return;
  }
  renderStatus(result); message("Ressourcen erneut erkannt. Angezeigte Einschränkungen bitte beachten.");
}, { login: true }));
async function browse(path) {
  $("folders").replaceChildren(node("p", "Der angegebene Ordner wird geprüft …", "muted"));
  let result;
  try { result = await gateway.call("folders.list", { path }); }
  catch (error) {
    $("folders").replaceChildren(node("p", "Der Zugriff auf diesen Ordner konnte nicht bestätigt werden.", "error"));
    throw error;
  }
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
window.addEventListener("pagehide", () => { $("app-password").value = ""; gateway?.close(); });
