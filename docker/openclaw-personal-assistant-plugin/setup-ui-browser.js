import { createSetupGateway } from "./setup-ui-auth.js";

// All server names, resource metadata and errors are untrusted text, never HTML.
const $ = id => document.getElementById(id);
const state = { authenticated: false, busy: false, current: null, flow: null, ready: null, participantRooms: "" };
let gateway, talkExpiryTimer, diagnosticsExpiryTimer, folderExpiryTimer, showDiagnostics, showFolder;
let talkReceivedAt = 0, overviewReceivedAt = 0, overviewExpiryTimer, overviewRefreshState;
const access = { edits: new Map(), inspectedFolders: new Map(), rows: [], preview: null, blocked: false };
let accessExpiryTimer, rightsExpiryTimer;
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
  $("connect").disabled = false; $("connect").hidden = false; $("reload").hidden = true;
  $("auth-status").textContent = "Nicht verbunden – erneut mit OpenClaw verbinden";
  clearAccessDraft(); renderAccess();
  showDiagnostics?.(); showFolder?.();
  renderOverview(); renderTalkRuntime();
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
function resourceText(item, { collection = false } = {}) {
  const rights = [];
  if (item.can_read === true) rights.push("lesen");
  if (item.can_create_file === true) rights.push("Dateien erstellen");
  if (item.can_mkdir === true) rights.push("Unterordner erstellen");
  if (item.can_create === true && !("can_create_file" in item || "can_mkdir" in item)) rights.push("erstellen");
  if (item.can_update === true) rights.push("ändern");
  if (item.can_send === true) rights.push("antworten");
  const rightsLabel = collection ? "Rechte laut Auflistung" : "Serverrechte";
  const read = item.read_check;
  const access = read ? ` · Leseprüfung: ${read.status === "ok" ? "erfolgreich" : read.status === "failed" ? "fehlgeschlagen" : "noch nicht geprüft"}${read.code ? " (" + read.code + ")" : ""}` : "";
  let exactRights = "";
  if (collection && read?.status === "ok") {
    const measured = Array.isArray(read.permissions) ? read.permissions : [];
    const labels = [["read", "lesen"], ["create", "erstellen"], ["update", "ändern"]]
      .filter(([permission]) => measured.includes(permission)).map(([, label]) => label);
    exactRights = labels.length ? ` · In der Einzelprüfung bestätigte Rechte: ${labels.join(", ")}`
      : " · Keine Rechte aus der Einzelprüfung ausgewiesen";
  }
  if (collection && item.read_check_source === "retained") exactRights += ` · Frühere Einzelprüfung vom ${item.checked_at}`;
  return `${item.name || item.path || item.resource_id || item.token || "Ressource"}${rights.length ? ` · ${rightsLabel}: ${rights.join(", ")}` : " · Rechte nicht bestätigt"}${access}${exactRights}`;
}
const diagnosticLabels = { ok: "Erfolgreich geprüft", partial: "Teilweise geprüft", failed: "Prüfung fehlgeschlagen", empty: "Keine Sammlungen gefunden", skipped: "Noch nicht geprüft" };
const talkDiagnosticLabels = { ...diagnosticLabels, ok: "Metadaten erfolgreich geprüft", empty: "Keine Räume gefunden", unavailable: "Talk nicht verfügbar", unsupported: "Voraussetzung fehlt" };
const evidenceMaxAge = 86400;
const evidenceLabels = { fresh: "Messung innerhalb des 24-Stunden-Fensters", stale: "Prüfergebnis veraltet", unknown: "Alter der Prüfung unbekannt", superseded: "Früherer Prüfstand", disconnected: "OpenClaw-Verbindung fehlt" };
const diagnosticDomains = [["user", "Benutzer"], ["calendars", "Kalender"], ["task_lists", "Aufgabenlisten"],
  ["addressbooks", "Adressbücher"], ["files", "Ordner"], ["talk", "Nextcloud Talk"]];
function count(value) { return Number.isSafeInteger(value) && value >= 0 ? value : "?"; }
function evidenceTime(timestamp, freshness, requestedAt) {
  const { age_seconds: age, max_age_seconds: maximum, state: serverState } = freshness || {};
  if (!state.authenticated) return { state: "disconnected" };
  if (!timestamp || !Number.isFinite(Date.parse(timestamp)) || !["fresh", "stale"].includes(serverState)
    || !Number.isFinite(age) || age < 0 || !Number.isFinite(maximum) || maximum <= 0) return { state: "unknown" };
  const remaining = (Math.min(maximum, evidenceMaxAge) - age) * 1000 - Math.max(0, performance.now() - requestedAt);
  return serverState === "stale" || remaining <= 0 ? { state: "stale" } : { state: "fresh", remaining };
}
const overviewDomains = [
  ["account", "Konto"], ["calendars", "Kalender", "VEVENT"], ["task_lists", "Aufgabenlisten", "VTODO"],
  ["addressbooks", "Adressbücher", "VCARD"], ["files", "Dateien", "FILES"], ["talk", "Talk"],
];
const overviewCards = new Map();
const overviewLabels = {
  disconnected: "OpenClaw-Verbindung fehlt", unconfigured: "Kein Konto gespeichert",
  unknown: "Prüfstand unbekannt", failed: "Kontoprüfung fehlgeschlagen",
  stale: "Prüfergebnis veraltet", pending: "Serverprüfung läuft",
};
const overviewDateFormat = new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" });
function addOverviewCards() {
  for (const [domain, title, component] of overviewDomains) {
    const card = node("article", undefined, "overview-card");
    card.dataset.overviewDomain = domain;
    const status = node("strong", undefined, "overview-status");
    const facts = node("div", undefined, "overview-facts");
    const link = node("a", undefined, "overview-link");
    link.addEventListener("click", event => {
      const [id] = overviewDestination(domain, component);
      const target = $(id);
      if (!target) return;
      if (id === "server-url") $("credentials-option").open = true;
      event.preventDefault();
      target.focus();
    });
    card.append(node("h3", title), status, facts, link);
    overviewCards.set(domain, { card, status, facts, link, component });
    $("overview-cards").append(card);
  }
}
function overviewDestination(domain, component) {
  if (!state.authenticated) return ["auth-heading", "OpenClaw verbinden"];
  if (state.current?.state === "failed" && !state.current.connected) return ["access-heading", "Fehlerdiagnose ansehen"];
  if (domain === "account") return ["server-url", "Verbindung bearbeiten"];
  if (state.ready) return ["preview-heading", "Kontovorschau prüfen"];
  if (!state.current?.connected) return ["server-url", "Konto einrichten"];
  if (component && !$("access-section").hidden && !$("access-editor").hidden) {
    const id = `access-${component}`;
    return [$(id) ? id : "access-heading", "Freigaben bearbeiten"];
  }
  if (domain === "files" && !$("folders-section").hidden) return ["folders-heading", "Ordner prüfen"];
  if (domain === "talk" && !$("talk-section").hidden) return ["talk-heading", "Talk-Einstellung"];
  return ["diagnostics-heading", "Diagnose ansehen"];
}
function setOverviewCard(domain, stateName, title) {
  const { card, status, facts, link, component } = overviewCards.get(domain);
  card.dataset.state = stateName;
  status.textContent = title;
  facts.replaceChildren();
  const [destination, label] = overviewDestination(domain, component);
  link.href = `#${destination}`;
  link.textContent = label;
  link.setAttribute("aria-label", `${card.children[0].textContent} · ${label}`);
  return facts;
}
function appendOverviewTime(target, label, timestamp) {
  if (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) return;
  const line = node("p", `${label} `, "muted");
  const time = node("time", overviewDateFormat.format(new Date(timestamp)));
  time.dateTime = timestamp; time.title = timestamp;
  line.append(time); target.append(line);
}
function overviewSummary(current) {
  const summary = current?.access?.overview;
  if (!["active", "legacy"].includes(current?.access?.mode) || summary?.schema_version !== 1
    || !current?.account_id || summary.account_id !== current.account_id
    || !current.revision || summary.connection_revision !== current.revision) return null;
  return summary;
}
function validOverviewCount(value) { return Number.isSafeInteger(value) && value >= 0; }
function validOverviewFreshness(freshness) {
  if (freshness?.max_age_seconds !== evidenceMaxAge) return false;
  const stamped = typeof freshness.checked_at === "string" && Number.isFinite(Date.parse(freshness.checked_at))
    && Number.isFinite(freshness.age_seconds) && freshness.age_seconds >= 0;
  const missing = freshness.checked_at === null && freshness.age_seconds === null;
  if (["fresh", "stale"].includes(freshness.state)) return stamped;
  if (freshness.state === "failed") return stamped || missing;
  return freshness.state === "unknown" && missing;
}
function overviewPermissions(summary, component) {
  const counts = summary?.components?.[component];
  if (!counts || !["known", "selected", "readable", "creatable", "updatable", "blocked"].every(key => validOverviewCount(counts[key]))
    || typeof counts.complete !== "boolean" || counts.blocked > counts.selected
    || Math.max(counts.readable, counts.creatable, counts.updatable) > counts.known
    || !validOverviewFreshness(counts.freshness)) return null;
  return counts;
}
function overviewConnectionState(current) {
  if (!state.authenticated) return "disconnected";
  if (!current) return "unknown";
  if (current.state === "failed") return "failed";
  return current.connected === true ? null : "unconfigured";
}
function overviewIdentity(current, readAge) {
  const connectionState = overviewConnectionState(current);
  if (connectionState) return connectionState;
  if (overviewRefreshState) return overviewRefreshState;
  const user = current.preview?.diagnostics?.user;
  if (current.refresh_failure || user?.status === "failed") return "failed";
  if (user?.status !== "ok" || user.loaded !== true || !current.user_id || current.preview.user_id !== current.user_id) return "unknown";
  return readAge(current.preview.checked_at, current.diagnostics_freshness).state;
}
function overviewCollectionCheck(domain, current, readAge, accountState) {
  const check = current.preview?.diagnostics?.[domain];
  if (!check || accountState !== "fresh") return {};
  const retained = current.diagnostics_domain_freshness?.[domain];
  if (retained) {
    const { state } = readAge(retained.checked_at, retained);
    if (state !== "fresh") return { state };
  }
  const complete = ["ok", "empty"].includes(check.status) && check.inventory_complete === true && check.checks_complete === true
    && validOverviewCount(check.discovered) && check.checked === check.discovered
    && validOverviewCount(check.readable) && check.readable <= check.checked && check.failed === 0 && check.skipped === 0
    && Array.isArray(check.errors) && check.errors.length === 0
    && Array.isArray(check.limitations) && check.limitations.length === 0;
  const readable = ["ok", "empty", "partial"].includes(check.status) && validOverviewCount(check.readable)
    && validOverviewCount(check.checked) && check.readable <= check.checked ? check.readable : null;
  return { complete, discovered: check.discovered, readable };
}
function fileCreateTargets(summary, counts, readAge, accountState) {
  const targets = summary?.file_create_targets;
  const capped = summary?.file_create_targets_truncated;
  if (!counts || !Array.isArray(targets) || targets.length > 16 || targets.length > counts.creatable
      || capped !== (targets.length < counts.creatable)) return null;
  const keys = new Set();
  const checked = [];
  for (const target of targets) {
    const scope = target?.scope;
    if (scope?.component !== "FILES" || typeof scope.path !== "string" || !scope.file_id || typeof scope.file_id !== "string"
      || !scope.resource_id || typeof scope.resource_id !== "string" || target.freshness?.state !== "fresh"
      || !validOverviewFreshness(target.freshness)) return null;
    const key = scopeKey(scope);
    if (keys.has(key)) return null;
    keys.add(key);
    if (accountState === "fresh" && !access.blocked && readAge(target.freshness.checked_at, target.freshness).state === "fresh") checked.push(scope);
  }
  return { scopes: checked, capped, expired: checked.length < targets.length };
}
function appendCreateTargets(target, files) {
  if (files.scopes.length) {
    const list = node("ul", undefined, "overview-targets");
    const items = files.scopes.map(scope => {
      const item = node("li", `/${scope.path} · Ordner-ID ${scope.file_id}`);
      item.title = `Ressourcen-ID ${scope.resource_id}`;
      return item;
    });
    target.append(node("p", "Dateien erstellen in"), list);
    list.append(...items.slice(0, 2));
    if (items.length > 2) {
      const rest = node("details"); const list = node("ul", undefined, "overview-targets");
      rest.append(node("summary", `Weitere geprüfte Ziele (${items.length - 2})`), list);
      list.append(...items.slice(2)); target.append(rest);
    }
    target.append(node("p", "Unterordner separat prüfen. Engere Regeln können Zugriff begrenzen.", "muted"));
  }
  if (files.capped || files.expired) {
    const limitation = [files.capped ? "Zielliste gekürzt" : "", files.expired ? "Zielprüfungen veraltet" : ""].filter(Boolean).join(" · ");
    target.append(node("p", limitation, "muted"));
  }
}
function renderPermissionCard(domain, title, component, current, summary, readAge, accountState) {
  const counts = overviewPermissions(summary, component);
  const clippedDiagnostics = accountState === "unknown" && summary
    && current.preview?.results_may_be_truncated === true && !current.preview.diagnostics?.user
    && current.preview.user_id === current.user_id
    && readAge(current.preview.checked_at, current.diagnostics_freshness).state === "fresh";
  const accountEvidence = clippedDiagnostics ? "fresh" : accountState;
  const isFiles = domain === "files", files = isFiles ? fileCreateTargets(summary, counts, readAge, accountEvidence) : null;
  const check = current?.connected && !isFiles ? overviewCollectionCheck(domain, current, readAge, accountState) : {};
  const measured = counts && readAge(counts.freshness.checked_at, counts.freshness).state;
  const stateName = accountEvidence !== "fresh" ? accountEvidence : access.blocked ? "unknown" : check.state || measured || "unknown";
  const complete = !clippedDiagnostics && counts?.complete && (isFiles ? files && !files.capped && !files.expired
    : check.complete && check.discovered === counts.known);
  let label = overviewLabels[stateName];
  let displayState = stateName;
  if (stateName === "fresh") {
    displayState = "partial";
    label = "Unvollständig geprüft";
    if (isFiles && !files) { displayState = "unknown"; label = "Ordnerziele unbekannt"; }
    else if (complete && !isFiles && counts.known === 0 && counts.selected === 0) {
      displayState = "empty"; label = `Keine sichtbaren ${title} gefunden`;
    } else if (complete && counts.blocked === 0 && !isFiles && counts.known === counts.readable && check.readable === counts.known) {
      displayState = "ok"; label = `Alle sichtbaren ${title} für den Agenten lesbar`;
    } else if (complete && isFiles && files.scopes.length) {
      displayState = "ok"; label = "Dateierstellung für geprüfte Ordner erlaubt";
    } else if (complete && counts.selected === 0) {
      displayState = "disabled"; label = "Keine Freigaben ausgewählt";
    } else if (complete && counts.blocked > 0) label = "Freigaben teilweise blockiert";
    else if (complete) label = isFiles ? "Keine bestätigten Ziele zum Erstellen" : "Leserechte unterscheiden sich je Sammlung";
  }
  const target = setOverviewCard(domain, displayState, label);
  if (counts) {
    const total = [`${counts.known} erkannt`,
      validOverviewCount(check.readable) && !access.blocked ? `${check.readable} serverseitig lesbar` : "",
      `${counts.selected} ausgewählt${current.access.mode === "legacy" ? " (Altbestand)" : ""}`].filter(Boolean);
    target.append(node("p", total.join(" · ")));
    if (stateName === "fresh" && (!isFiles || files)) {
      const rights = [`${counts.readable} für den Agenten lesbar`];
      if (isFiles && !files.expired) rights.push(`${counts.creatable} mit Datei-Erstellrecht`);
      if (!isFiles) rights.push(`${counts.creatable} mit Erstellrecht`, `${counts.updatable} mit Änderungsrecht`);
      if (counts.blocked > 0) rights.push(`${counts.blocked} blockiert`);
      target.append(node("p", rights.join(" · ")));
    }
    if (files && accountEvidence === "fresh" && stateName !== "failed" && !access.blocked) appendCreateTargets(target, files);
    if (accountState !== "disconnected") appendOverviewTime(target, "Prüfstand", counts.freshness.checked_at);
  }
}
function renderOverview() {
  window.clearTimeout(overviewExpiryTimer);
  const current = state.current;
  let expiry = Infinity;
  const readAge = (timestamp, freshness) => {
    const age = freshness?.state === "failed" ? { state: "failed" } : evidenceTime(timestamp, freshness, overviewReceivedAt);
    if (age.remaining > 0) expiry = Math.min(expiry, age.remaining);
    return age;
  };
  const accountState = overviewIdentity(current, readAge);
  const hasCredentials = current?.credentials_stored === true;
  const accountLabel = accountState === "fresh" ? (hasCredentials ? "Konto bestätigt" : "Zugangsdaten unbestätigt")
    : current?.state === "failed" && accountState === "failed" ? "Verbindung nicht verifizierbar" : overviewLabels[accountState];
  const account = setOverviewCard("account", accountState === "fresh" ? (hasCredentials ? "ok" : "unknown") : accountState, accountLabel);
  if (current?.connected && state.authenticated) {
    appendOverviewTime(account, accountState === "failed" ? "Fehlgeschlagen am" : "Letzte Kontoprüfung",
      accountState === "failed" ? current.refresh_failure?.checked_at : current.preview?.checked_at);
  }
  const summary = overviewSummary(current);
  for (const [domain, title, component] of overviewDomains) {
    if (component) renderPermissionCard(domain, title, component, current, summary, readAge, accountState);
  }
  if (Number.isFinite(expiry)) overviewExpiryTimer = window.setTimeout(renderOverview, expiry + 1);
}

function pendingDiagnostics(text) {
  showDiagnostics = null; window.clearTimeout(diagnosticsExpiryTimer);
  $("diagnostics").replaceChildren(node("p", text, "muted"));
}
function renderDiagnostics(preview, { current, freshness, domainFreshness = {}, requestedAt = performance.now(), refreshFailure } = {}) {
  showDiagnostics = () => {
    window.clearTimeout(diagnosticsExpiryTimer);
    const target = $("diagnostics"); target.replaceChildren();
    const age = refreshFailure ? { state: "superseded" } : evidenceTime(preview?.checked_at, freshness, requestedAt);
    const ages = {};
    for (const [domain] of diagnosticDomains) {
      const retained = domainFreshness[domain];
      if (retained && age.state === "fresh") ages[domain] = { ...evidenceTime(retained.checked_at, retained, requestedAt),
        checked_at: retained.checked_at };
    }
    target.append(node("p", current ? "Gespeichertes Prüfergebnis. Für einen aktuellen Zugriffstest „Ressourcen neu erkennen“ wählen." : "Ergebnis der letzten Serverprüfung. Beachte die einzelnen Bereiche unten.", "muted"));
    appendDiagnostics(target, preview, age.state, current, ages);
    if (refreshFailure) {
      target.append(node("h3", "Erneute Prüfung fehlgeschlagen"));
      appendDiagnostics(target, refreshFailure, "unknown", current);
    }
    const remaining = [age, ...Object.values(ages)].map(measure => measure.remaining).filter(ms => ms > 0);
    if (remaining.length) diagnosticsExpiryTimer = window.setTimeout(showDiagnostics, Math.min(...remaining) + 1);
  };
  showDiagnostics();
}
function appendDiagnostics(target, preview, overallFreshness, current, ages = {}) {
  const report = preview?.diagnostics;
  if (preview?.checked_at) target.append(node("p", `Geprüft am: ${preview.checked_at}`, "muted"));
  if (preview?.oldest_collection_check_at) target.append(node("p", `Älteste berücksichtigte Sammlungsprüfung: ${preview.oldest_collection_check_at}`, "muted"));
  target.append(node("p", evidenceLabels[overallFreshness], "muted"));
  if (!report) {
    target.append(node("p", "Für diesen Stand liegen noch keine einzelnen Zugriffsprüfungen vor. Ressourcen neu erkennen oder das Konto anmelden.", "muted"));
    return;
  }
  const list = node("div", undefined, "diagnostic-list");
  for (const [key, label] of diagnosticDomains) {
    const freshness = ages[key]?.state ?? overallFreshness;
    const check = report[key] || {};
    const labels = key === "talk" ? talkDiagnosticLabels : diagnosticLabels;
    const status = Object.hasOwn(labels, check.status) ? check.status : "skipped";
    const historical = status === "ok" && freshness !== "fresh";
    const card = node("div", undefined, `diagnostic ${historical ? "partial" : status}`);
    card.dataset.domain = key; card.dataset.freshness = freshness;
    card.append(node("h3", label), node("strong", historical ? evidenceLabels[freshness] : labels[status]));
    if (ages[key]?.checked_at) card.append(node("p", `Älteste Einzelprüfung in diesem Bereich: ${ages[key].checked_at}`, "muted"));
    const reachable = report.availability?.status === "ok" ? "Server antwortete" : report.availability?.status === "failed" ? "Prüfung fehlgeschlagen" : "unbestätigt";
    card.append(node("p", `Erreichbarkeit: ${reachable}. Authentisierung: ${report.user?.loaded === true ? "Konto bei der Prüfung bestätigt" : "unbestätigt"}.`));
    if (key === "user") {
      card.append(node("p", check.loaded === true ? `${preview.display_name || preview.user_id} (${preview.user_id}) · Benutzer geladen` : "Benutzer konnte noch nicht bestätigt werden."));
      card.append(node("p", "Fähigkeit: Abfrage der Kontoidentität. Prüfumfang: ein Nextcloud-Konto."));
    } else if (key === "talk") {
      card.append(node("p", `Talk-App: ${check.app_available === true ? "verfügbar" : check.app_available === false ? "nicht verfügbar" : "nicht bestätigt"}. Raumzugriff: ${check.api_reachable === true ? "erreichbar" : check.api_reachable === false ? "fehlgeschlagen" : "nicht bestätigt"}.`));
      card.append(node("p", `Antwortzuordnung: ${check.reference_id_supported === true ? "unterstützt" : check.reference_id_supported === false ? "nicht unterstützt" : "nicht bestätigt"}.`));
      card.append(node("p", `${count(check.discovered)} ${check.discovered === 1 ? "Raum" : "Räume"} erfasst · ${count(check.writable)} mit bestätigtem Senderecht · ${count(check.send_unconfirmed)} mit ungeklärtem Senderecht.`));
      card.append(node("p", check.inventory_complete === true ? "Raumliste vollständig erfasst." : "Vollständigkeit der Raumliste nicht bestätigt."));
      card.append(node("p", "Geprüft werden Raummetadaten und vom Server gemeldete Rechte. Keine Chat-Inhalte gelesen und keine Testnachricht gesendet.", "muted"));
      const next = freshness !== "fresh" && ["ok", "empty", "unavailable", "unsupported"].includes(status) ? "Ressourcen neu erkennen und die Talk-Metadaten erneut prüfen."
        : status === "unavailable" ? "Talk in Nextcloud bereitstellen und Ressourcen neu erkennen."
        : status === "unsupported" ? "Die Talk-Version muss die Antwortzuordnung unterstützen (chat-reference-id)."
        : status === "empty" ? "Das Agentenkonto in Nextcloud zu einem Talk-Raum hinzufügen und Ressourcen neu erkennen."
        : status === "skipped" ? "Das Konto anmelden oder Ressourcen neu erkennen."
        : status === "failed" || status === "partial" ? "Fehler und Prüfgrenzen beachten; Erreichbarkeit und Kontorechte prüfen und Ressourcen neu erkennen."
        : "Die gewünschten Räume und Benutzer unten auswählen; dort den Chat-Dienst prüfen.";
      card.append(node("p", `Nächster Schritt: ${next}`, "muted"));
    } else if (key === "files") {
      card.append(node("p", `Fähigkeit: Ordnerauflistung per WebDAV ${check.root_readable === true ? "bei der Prüfung bestätigt" : "unbestätigt"}.`));
      card.append(node("p", check.root_readable === true ? "Kontohauptordner erreichbar und lesbar." : "Lesezugriff auf den Kontohauptordner nicht bestätigt."));
      card.append(node("p", `${count(check.discovered)} Ordner erfasst. ${check.inventory_complete === true ? "Auflistung der obersten Ebene vollständig." : "Auflistung der obersten Ebene nicht vollständig bestätigt."}`));
      card.append(node("p", "Unterordner einzeln unter „Ordner durchsuchen“ prüfen. Dateiinhalte wurden nicht geladen.", "muted"));
    } else {
      const protocol = key === "addressbooks" ? "CardDAV" : "CalDAV";
      card.append(node("p", `Fähigkeit: ${protocol}-Sammlungen ${check.inventory_complete === true || check.discovered > 0 ? "bei der Prüfung bestätigt" : "unbestätigt"}.`));
      card.append(node("p", `${count(check.discovered)} gefunden · ${count(check.readable)} lesbar · ${count(check.failed)} fehlgeschlagen · ${count(check.skipped)} ungeprüft`));
      if (Number.isSafeInteger(check.probed) && Number.isSafeInteger(check.retained)) {
        card.append(node("p", `In dieser Erkennung geprüft: ${check.probed}. Frühere Einzelprüfungen übernommen: ${check.retained}.`));
      }
      card.append(node("p", check.inventory_complete === true ? "Liste der Sammlungen vollständig erkannt." : "Vollständigkeit der Sammlungsliste nicht bestätigt."));
      card.append(node("p", `Geprüft wird der Zugriff auf die Sammlung und ihre Metadaten. ${key === "addressbooks" ? "Kontaktinhalte" : "Termin- und Aufgabeninhalte"} werden dabei nicht geladen.`, "muted"));
    }
    if (key === "talk") {
      const rooms = current?.talk?.room_tokens?.length || 0, users = current?.talk?.allowed_user_ids?.length || 0;
      card.append(node("p", current ? `Gespeicherte Auswahl: ${rooms} Räume und ${users} Benutzer. Laufzeitbeobachtung: siehe „Chat-Dienst“.`
        : "Gespeicherte Auswahl: noch offen. Laufzeitbeobachtung: nach der Einrichtung im Abschnitt „Chat-Dienst“.", "muted"));
    } else {
      const selection = current ? `Konto ${current.user_id}` : "diese Prüfung ist eine Vorschau";
      const runtime = ["user", "task_lists"].includes(key) ? "nicht anwendbar (Zugriff bei Bedarf)"
        : "kein Synchronisierungslauf für dieses Konto und diese Ressourcen belegt";
      card.append(node("p", `Gespeicherte Auswahl: ${selection}. Laufzeitbeobachtung: ${runtime}.`, "muted"));
      const next = status === "skipped" || report.user?.loaded !== true ? "Konto anmelden oder Ressourcen neu erkennen."
        : key === "files" && freshness === "fresh" && status === "ok" ? "Gewünschten Ordner unter „Ordner durchsuchen“ prüfen."
        : status === "failed" || status === "partial" ? "Fehlercodes und Serverrechte prüfen, dann Ressourcen neu erkennen."
        : "Bei Bedarf Ressourcen neu erkennen.";
      card.append(node("p", `Nächster Schritt: ${next}`, "muted"));
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
    const collection = ["calendars", "task_lists", "addressbooks"].includes(key);
    const list = node("ul"); for (const item of items) list.append(node("li", resourceText(item, { collection })));
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
  renderTalkRuntime();
  $("talk-section").hidden = false;
}
const runtimeLabels = {
  active: ["Verbunden", "In einem ausgewählten Raum mit dem Agenten schreiben. Bei Problemen den Status neu laden."],
  degraded: ["Verbindung mit Einschränkungen", "Ungewisse Verarbeitung oder Zustellung prüfen. Nachrichten nicht automatisch erneut senden."],
  disabled: ["Deaktiviert", "Zum Aktivieren Räume und Benutzer auswählen, Teilnehmer laden und die Talk-Einstellung speichern."],
  "not-observed": ["Noch keine Rückmeldung", "Kurz warten und „Status neu laden“ wählen. Bleibt die Rückmeldung aus, den Chat-Dienst prüfen."],
  pending: ["Neue Einstellung noch nicht bestätigt", "Kurz warten und „Status neu laden“ wählen; die neue Auswahl ist noch nicht vom Chat-Dienst bestätigt."],
  stale: ["Status veraltet", "„Status neu laden“ wählen. Bleibt die Rückmeldung veraltet, den Chat-Dienst und seine Verbindung prüfen."],
  failed: ["Verbindung fehlgeschlagen", "Nextcloud-Erreichbarkeit und Kontorechte prüfen, dann Ressourcen neu erkennen und den Status neu laden."],
  invalid: ["Auswahl unvollständig oder ungültig", "Ressourcen neu erkennen, Räume und Teilnehmer prüfen und die Talk-Einstellung erneut speichern."],
  unavailable: ["Verbindung nicht bestätigt", "„Status neu laden“ wählen; falls weiter keine Diagnose vorliegt, den Chat-Dienst prüfen."],
};
function renderTalkRuntime() {
  window.clearTimeout(talkExpiryTimer);
  const current = state.current;
  if (!current?.connected) {
    const status = overviewConnectionState(current);
    setOverviewCard("talk", status, overviewLabels[status]);
    return;
  }
  const target = $("talk-status"), runtime = current.talk_runtime, worker = runtime?.worker || {};
  const rooms = current.talk?.room_tokens || [], users = current.talk?.allowed_user_ids || [];
  const enabled = current.talk?.enabled === true;
  const elapsed = Math.max(0, (performance.now() - talkReceivedAt) / 1000);
  const age = typeof worker.age_seconds === "number" && Number.isFinite(worker.age_seconds) && worker.age_seconds >= 0 ? worker.age_seconds + elapsed : null;
  const maxAge = typeof worker.max_age_seconds === "number" && Number.isFinite(worker.max_age_seconds) && worker.max_age_seconds > 0 ? Math.min(90, worker.max_age_seconds) : 0;
  let status = runtime && Object.hasOwn(runtimeLabels, runtime.state) ? runtime.state : "unavailable";
  if (!state.authenticated) status = "unavailable";
  else if (!enabled) status = "disabled";
  else if (current.refresh_failure || current.preview?.diagnostics?.user?.status === "failed" || overviewRefreshState === "failed") status = "unavailable";
  else if (!runtime || runtime.mode !== "managed-account") status = "unavailable";
  else if (status !== "unavailable" && (runtime.connection_revision !== current.revision || runtime.room_count !== rooms.length || runtime.allowed_user_count !== users.length)) status = "pending";
  else if (["active", "degraded", "failed"].includes(status)) {
    if (age !== null && maxAge && age >= maxAge) status = "stale";
    else if (status !== "failed" && (runtime.enabled !== true || runtime.connected !== true || runtime.configured !== true
      || worker.present !== true || worker.fresh !== true || worker.healthy !== true
      || worker.account_matches !== true || worker.revision_matches !== true || worker.selection_matches !== true
      || !["talk-poll-ok", "talk-paired-no-history-replay"].includes(worker.code) || age === null || !maxAge)) status = "unavailable";
    else if (age !== null && maxAge) talkExpiryTimer = window.setTimeout(renderTalkRuntime, (maxAge - age) * 1000 + 1);
  } else if (status === "disabled") status = "pending";
  const [label, next] = runtimeLabels[status];
  const summary = setOverviewCard("talk", !state.authenticated ? "disconnected" : status === "active" ? "ok" : status,
    state.authenticated ? label : overviewLabels.disconnected);
  if (typeof current.talk?.enabled === "boolean" && Array.isArray(rooms) && Array.isArray(users)) {
    summary.append(node("p", `Gespeichert: ${enabled ? "an" : "aus"} · ${rooms.length} ${rooms.length === 1 ? "Raum" : "Räume"} · ${users.length} ${users.length === 1 ? "erlaubter Benutzer" : "erlaubte Benutzer"}`));
  } else summary.append(node("p", "Gespeicherte Auswahl unbekannt.", "muted"));
  target.dataset.state = status;
  target.className = `diagnostic ${status === "active" ? "ok" : status === "failed" || status === "invalid" ? "failed" : "partial"}`;
  target.replaceChildren(node("h3", "Chat-Dienst"), node("strong", label));
  target.append(node("p", `Gespeicherte Einstellung: ${enabled ? "aktiviert" : "deaktiviert"}. Gespeicherte Auswahl: ${rooms.length} ${rooms.length === 1 ? "Raum" : "Räume"} · ${users.length} ${users.length === 1 ? "erlaubter Benutzer" : "erlaubte Benutzer"}${users.length ? " (" + users.join(", ") + ")" : ""}.`));
  target.append(node("p", "Die Mitgliedschaft der gespeicherten Benutzer wird beim Statusabruf nicht erneut geprüft. Zum Ändern der Auswahl zuerst die Teilnehmer laden.", "muted"));
  if (typeof worker.observed_at === "number" && Number.isFinite(worker.observed_at)) {
    const observed = new Date(worker.observed_at * 1000);
    if (Number.isFinite(observed.getTime())) {
      target.append(node("p", `Letzte Rückmeldung: ${observed.toISOString()}${age === null ? "" : " · beim Anzeigen " + Math.floor(age) + " Sekunden alt"}.`, "muted"));
      if (state.authenticated) appendOverviewTime(summary, "Letzte Rückmeldung", observed.toISOString());
    }
  }
  if (worker.code) target.append(node("p", `Diagnosecode: ${worker.code}`, "muted"));
  if (Array.isArray(runtime?.issues) && runtime.issues.length) target.append(node("p", `Hinweise: ${runtime.issues.join(", ")}`, "muted"));
  target.append(node("p", `Nächster Schritt: ${state.authenticated ? next : "OpenClaw erneut verbinden und den Status neu laden."}`));
  target.append(node("p", "Die Rückmeldung des Chat-Dienstes ist keine Zustellbestätigung für einzelne Nachrichten. Es wird keine Testnachricht gesendet.", "muted"));
  target.append(node("p", "Die Rückmeldung bezieht sich auf den zuletzt abgefragten Raum. Weitere ausgewählte Räume werden nacheinander abgefragt.", "muted"));
}
const levelLabels = { off: "Aus", read: "Lesen", create: "Lesen und Erstellen", update: "Lesen, Erstellen und Ändern" };
const componentLabels = { VEVENT: "Kalender", VTODO: "Aufgabenliste", VCARD: "Adressbuch", FILES: "Ordner" };
const operationLabels = { read: "Lesen", list: "Auflisten", search: "Suchen", create: "Erstellen", update: "Ändern",
  create_file: "Dateien erstellen (C)", mkdir: "Unterordner erstellen (K)" };
const serverLabels = { verified: "Gemessen", partial: "Teilweise geprüft", stale: "Messung veraltet", unverified: "Rechte unbestätigt" };
const readLabels = { ok: "erfolgreich", failed: "fehlgeschlagen", skipped: "übersprungen", unknown: "unbestätigt" };
const accessConflicts = new Set(["nextcloud-connection-conflict", "nextcloud-policy-conflict", "access-preview-unavailable", "access-preview-changed"]);
const accessReasons = {
  "server-right-missing": "Das erforderliche Recht fehlt in Nextcloud",
  "rights-unverified": "Das Recht ist ungeprüft oder unbestätigt",
  "rights-stale": "Die letzte Prüfung ist veraltet",
  "folder-identity-changed": "Der Ordner wurde ersetzt",
  "scope-unbound": "Die Ressource lässt sich dem aktuellen Konto nicht sicher zuordnen",
  "scope-invalid": "Die Ressource lässt sich nicht eindeutig bestimmen",
  "scope-off": "Der Zugriff für den Agenten steht auf Aus",
  "operation-not-allowed": "Die gewählte Freigabe umfasst diesen Vorgang nicht",
  "policy-changed": "Die gespeicherte Auswahl hat sich geändert",
  "policy-integrity-error": "Die gespeicherten Freigaben konnten nicht geprüft werden",
  "account-root-confirmation-required": "Die Freigabe für das gesamte Konto braucht eine eigene Bestätigung",
  "legacy-scope-unconfirmed": "Die bisherige Auswahl ist nicht vollständig belegt",
  "legacy-before-unconfirmed": "Die bisherige Freigabe für diese Ressource ist unklar",
  "legacy-right-unconfirmed": "Das bisherige Recht wurde bei der Prüfung nicht bestätigt",
  "legacy-original-proof-missing": "Das Recht war bei der ursprünglichen Kontoprüfung unbestätigt",
  "legacy-outside-original-selection": "Dieses Recht gehörte nicht zur ursprünglichen Freigabe",
  "file-scope-incomplete": "Die bisherige Ordnerauswahl lässt sich nicht vollständig prüfen",
  "legacy-scope-ambiguous": "Die bisherige Freigabe lässt sich nicht eindeutig zuordnen",
  "legacy-bounded-explicit-selection": "Die bisherigen Rechte überschreiten den Umfang der Vorschau",
  "legacy-invoice-archive-unbound": "Das Ziel für das Rechnungsarchiv ist unbestätigt",
  "legacy-mail-calendar-unbound": "Das Kalenderziel für Termine aus E-Mails ist unbestätigt",
  "legacy-deck-orders-unbound": "Das Ziel für Aufträge in Deck ist unbestätigt",
};
const workflowLabels = { "search-and-sync": "Suche und Synchronisierung", "pending-actions": "Offene Aktionen",
  calendar: "Kalender", tasks: "Aufgaben", contacts: "Kontakte", workspace: "Dateien und Ordner",
  "invoice-archive": "Rechnungsarchiv", "mail-calendar": "Termine aus E-Mails", "deck-orders": "Aufträge in Deck" };
const workflowEffects = {
  "pending-actions": "Offene Pläne und bisherige Einzelbestätigungen müssen anhand der neuen Freigaben erneut geprüft werden.",
  "search-and-sync": "Suchergebnisse brauchen eine Prüfung mit den neuen Freigaben. Prüfe den Status der Synchronisierung separat.",
};
const workerLabels = { "nextcloud-sync": "Synchronisierung", "sync-worker": "Synchronisierung" };
function accessReason(code) {
  return Object.hasOwn(accessReasons, code) ? accessReasons[code] : "Zugriff unbestätigt";
}
function appendAccessCodes(target, codes) {
  const values = [...new Set(codes.filter(Boolean))];
  if (!values.length) return;
  const details = node("details", undefined, "muted");
  details.append(node("summary", "Diagnosecodes"), node("code", values.join(" · ")));
  target.append(details);
}
function pathKey(scope) { return JSON.stringify([scope.resource_id, scope.component, scope.path]); }
function scopeKey(scope) { return JSON.stringify([scope.resource_id, scope.component, scope.path, scope.file_id]); }
function rowKey(row) { return row.revoke_handle || scopeKey(row.scope); }
const rowRequestedAt = new WeakMap();
function rowEvidenceTime(row) {
  return evidenceTime(row.server?.checked_at, row.server?.freshness, rowRequestedAt.get(row));
}
function accessContext(current = state.current) {
  return JSON.stringify([current?.account_id, current?.revision, current?.access?.mode,
    current?.access?.binding?.generation, current?.access?.binding?.policy_revision]);
}
function clearAccessPreview() {
  window.clearTimeout(accessExpiryTimer);
  access.preview = null;
  $("access-confirm").checked = false;
  $("access-apply").disabled = true;
  $("access-review-panel").hidden = true;
  $("access-diff").replaceChildren();
}
function clearAccessDraft() {
  clearAccessPreview();
  access.edits.clear();
  access.inspectedFolders.clear();
  $("access-root").checked = false;
}
function operationText(operations, empty = "Keine") {
  return operations?.length ? operations.map(operation => operationLabels[operation] || operation).join(", ") : empty;
}
function scopeLabel(scope, name) {
  if (scope.component === "FILES") return scope.path ? `${scope.path} (Ordner)` : "Ganzes Konto (Kontohauptordner)";
  return `${name || scope.resource_id} (${componentLabels[scope.component] || scope.component})`;
}
function rowRightsState(row) {
  const time = rowEvidenceTime(row);
  if (time.state === "stale" || row.server?.state === "stale") return "stale";
  if (!state.authenticated || access.blocked || state.current?.refresh_failure
      || row.server?.read_check !== "ok" || time.state !== "fresh" || !Number.isFinite(time.remaining)) return "unverified";
  return row.server?.state;
}
function allowedLevels(row) {
  const measured = ["verified", "partial"].includes(rowRightsState(row));
  return (row.supported_levels || []).filter(level => Object.hasOwn(levelLabels, level) && (measured || level === "off"));
}
function accessLevelLabel(row, level) {
  const operations = row.server.operations;
  if (level === "update" && !operations.includes("create")) return "Lesen und Ändern";
  if (level === "create" && row.scope.component === "FILES") {
    const creates = ["create_file", "mkdir"].filter(operation => operations.includes(operation));
    return creates.length === 2 ? "Lesen, Dateien und Unterordner erstellen" : `Lesen und ${operationLabels[creates[0]]}`;
  }
  return levelLabels[level];
}
function mayEditAccess() {
  return state.authenticated && !state.flow && !access.blocked && state.current?.connected === true
    && ["active", "legacy"].includes(state.current?.access?.mode);
}
function updateAccessControls() {
  const rootEdit = [...access.edits.values()].find(edit => edit.scope.path === "");
  const legacyRoot = !rootEdit && state.current?.access?.mode === "legacy"
    && access.rows.some(row => row.scope.path === "" && allowedLevels(row).includes("read"));
  const editedRoot = rootEdit && rootEdit.level !== "off";
  $("access-root-wrap").hidden = !(legacyRoot || editedRoot);
  if ($("access-root-wrap").hidden) $("access-root").checked = false;
  $("access-review").disabled = !mayEditAccess() || (editedRoot && !$("access-root").checked)
    || (!access.edits.size && state.current?.access?.mode !== "legacy");
}
function appendAccessState(target, title, value, detail = "") {
  const cell = node("div", undefined, "access-state");
  cell.append(node("strong", title), node("p", value));
  if (detail) cell.append(node("p", detail, "muted"));
  target.append(cell);
}
function renderAccessRow(row, replacements, mode) {
  const { scope, server, worker } = row;
  const label = scopeLabel(scope, row.name);
  const card = node("article", undefined, "access-row");
  const identity = row.revoke_handle ? `Freigabekennung ${row.revoke_handle.slice(0, 12)}` : scope.file_id ? `Ordner-ID ${scope.file_id}` : `Ressourcen-ID ${scope.resource_id}`;
  card.append(node("h3", label), node("p", identity, "muted resource-identity"));
  const measures = node("div", undefined, "access-states");
  const rightsState = rowRightsState(row);
  const serverDetails = [serverLabels[rightsState] || "Rechte unbestätigt",
    server?.read_check_source === "retained" ? "Einzelprüfung aus früherer Erkennung" : "", server?.checked_at,
    `Leseprüfung ${readLabels[server?.read_check] || "unbestätigt"}`].filter(Boolean).join(" · ");
  appendAccessState(measures, "Nextcloud erlaubt", operationText(server?.operations, "Keine Rechte bestätigt"), serverDetails);
  appendAccessState(measures, "Für den Agenten freigegeben", mode === "legacy"
    ? "Altbestand · Vorschau zur Übernahme prüfen" : operationText(row.desired_operations, "Aus"));
  const effective = ["verified", "partial"].includes(rightsState) ? row.effective_operations : [];
  const effectiveText = operationText(effective, "Kein bestätigter Zugriff");
  const blockers = Object.entries(row.blockers || {}).filter(([operation]) => row.desired_operations?.includes(operation));
  appendAccessState(measures, "Aktuell wirksam", effectiveText,
    [blockers.map(([operation, code]) => `${operationLabels[operation] || operation}: ${accessReason(code)}`).join(" · "),
      effective?.some(operation => ["create", "update", "create_file", "mkdir"].includes(operation))
        ? "Fachliche Einzelbestätigung je Aktion bleibt erforderlich." : ""].filter(Boolean).join(". "));
  card.append(measures);
  const workerState = worker?.state === "not-applicable" ? "nicht anwendbar (Zugriff bei Bedarf)" : "nicht verfügbar";
  const workerName = Object.hasOwn(workerLabels, worker?.name) ? ` · ${workerLabels[worker.name]}` : "";
  card.append(node("p", `Laufzeitbeobachtung: ${workerState}${workerName}`, "muted"));
  if (row.next_step) card.append(node("p", row.next_step, "muted"));
  if (replacements || server?.code === "folder-identity-changed") {
    card.append(node("p", "Für diesen Pfad liegen unterschiedliche Ordneridentitäten vor. Eine Freigabe für die bisherige Ordner-ID gilt nicht für den Ersatzordner. Prüfe die ID in der Vorschau.", "muted"));
  }
  appendAccessCodes(card, [server?.code, ...blockers.map(([, code]) => code)]);
  const input = node("select");
  input.setAttribute("aria-label", `Zugriff ändern · ${label} · ${identity}`);
  input.dataset.component = scope.component;
  if (scope.component === "FILES") { input.dataset.path = scope.path; input.dataset.fileId = scope.file_id; }
  const unchanged = node("option", "Unverändert lassen"); unchanged.value = "";
  input.append(unchanged);
  const levels = allowedLevels(row);
  for (const level of levels) {
    const option = node("option", accessLevelLabel(row, level)); option.value = level;
    input.append(option);
  }
  input.disabled = !levels.length;
  const key = row.revoke_handle || pathKey(scope);
  const edit = access.edits.get(key);
  input.value = edit && scopeKey(edit.scope) === scopeKey(scope) ? edit.level : "";
  input.addEventListener("change", () => {
    if (!mayEditAccess()) return;
    clearAccessPreview();
    if (access.edits.get(key)?.scope.path === "") $("access-root").checked = false;
    if (levels.includes(input.value)) {
      if (!access.edits.has(key) && access.edits.size >= 64) {
        input.value = "";
        message("Bis zu 64 Ressourcen pro Vorschau ändern. Prüfe zuerst die aktuelle Auswahl.");
        return;
      }
      access.edits.set(key, { scope, level: input.value, ...(row.revoke_handle ? { revoke_handle: row.revoke_handle } : {}) });
      if (replacements) renderAccess();
    } else if (access.edits.has(key) && scopeKey(access.edits.get(key).scope) === scopeKey(scope)) {
      access.edits.delete(key);
    }
    updateAccessControls();
  });
  const control = node("label", undefined, "access-control");
  control.append(node("span", "Zugriff ändern"), input);
  card.append(control);
  return card;
}
function renderAccess() {
  window.clearTimeout(rightsExpiryTimer);
  const current = state.current, view = current?.access;
  $("access-section").hidden = !state.authenticated || Boolean(state.flow) || (!current?.connected && view?.mode !== "integrity-error");
  $("access-editor").hidden = !mayEditAccess();
  $("access-more").hidden = !mayEditAccess() || !Number.isSafeInteger(view.next_offset) || view.next_offset <= 0;
  const target = $("access-rows"); target.replaceChildren();
  const mode = view?.mode;
  const descriptions = {
    active: "Die gespeicherte Freigabe und die jetzt wirksamen Rechte stehen bei jeder Ressource. Änderungen gelten nach deiner Bestätigung der Vorschau.",
    legacy: "Prüfe die bisherigen Zugriffe in einer Vorschau, bevor du sie in die neuen Freigaben übernimmst. Unklare Zuordnungen werden dort ausgewiesen.",
    unconfigured: "Für dieses Konto ist noch keine Zugriffsrichtlinie eingerichtet.",
    "integrity-error": "Die gespeicherte Zugriffsrichtlinie kann nicht verifiziert werden.",
  };
  const description = node("p", access.blocked ? "Den Status neu laden, bevor du weitere Zugriffsänderungen vornimmst."
    : descriptions[mode] || "Für diesen Stand liegt keine Zugriffsübersicht vor.");
  if (view?.complete === false) description.append(node("span", " Die Zugriffsübersicht ist unvollständig."));
  if (view?.next_step) description.append(node("span", ` ${view.next_step}`));
  $("access-mode").replaceChildren(description);
  appendAccessCodes($("access-mode"), [view?.code]);
  const rows = new Map((view?.rows || []).map(row => [rowKey(row), row]));
  for (const [key, row] of access.inspectedFolders) rows.set(key, row);
  access.rows = [...rows.values()];
  if (!mayEditAccess()) return;
  if (!access.rows.length) target.append(node("p", "In diesem Stand sind keine Ressourcen für eine Einzelauswahl erfasst.", "muted"));
  const pathCounts = new Map();
  let nextExpiry = Infinity;
  for (const row of access.rows) {
    pathCounts.set(pathKey(row.scope), (pathCounts.get(pathKey(row.scope)) || 0) + 1);
    const key = row.revoke_handle || pathKey(row.scope), edit = access.edits.get(key);
    if (edit && scopeKey(row.scope) === scopeKey(edit.scope) && !allowedLevels(row).includes(edit.level)) {
      access.edits.delete(key); clearAccessPreview();
      message("Die Rechte für eine geplante Freigabe können nicht mehr bestätigt werden. Den Ordner oder die Sammlung erneut prüfen.");
    }
    const remaining = rowEvidenceTime(row).remaining;
    if (remaining > 0 && ["verified", "partial"].includes(rowRightsState(row))) nextExpiry = Math.min(nextExpiry, remaining);
  }
  const components = new Set();
  for (const row of access.rows) {
    const card = renderAccessRow(row, pathCounts.get(pathKey(row.scope)) > 1, mode);
    if (!components.has(row.scope.component)) {
      components.add(row.scope.component);
      card.id = `access-${row.scope.component}`;
      card.tabIndex = -1;
    }
    target.append(card);
  }
  if (Number.isFinite(nextExpiry)) rightsExpiryTimer = window.setTimeout(renderAccess, nextExpiry + 1);
  updateAccessControls();
}
function renderAccessPreview(result, params) {
  const target = $("access-diff"); target.replaceChildren();
  target.append(node("p", `Konto: ${state.current.user_id} · ${state.current.server_url}`));
  target.append(node("p", `Vorschau gültig bis ${new Date(result.expires_at).toLocaleString("de-DE")} (Ortszeit).`, "muted"));
  if (params.confirm_root) {
    const positiveRoot = result.changes.some(change => change.scope.path === "" && change.after_operations.length > 0);
    target.append(node("p", positiveRoot ? "Ganzes Konto (Kontohauptordner): Freigabe ausdrücklich bestätigt. Engere Ordnerregeln haben Vorrang."
      : "Ganzes Konto (Kontohauptordner): In dieser Vorschau ohne Freigabe.", "access-root-note"));
  }
  const changes = node("ul", undefined, "access-changes");
  for (const change of result.changes) {
    const item = node("li");
    const identifier = change.revoke_handle ? `Freigabekennung ${change.revoke_handle.slice(0, 12)}` : change.scope.file_id || change.scope.resource_id;
    item.append(node("strong", scopeLabel(change.scope, change.name)), node("span", ` · ${identifier}`, "muted"),
      node("p", `Vorher: ${operationText(change.before_operations, "Aus")} → Nachher: ${operationText(change.after_operations, "Aus")}`));
    changes.append(item);
  }
  target.append(changes);
  if (!result.changes.length) target.append(node("p", "Diese Vorschau übernimmt keine Freigaben."));
  if (result.affected_workflows.length) {
    const list = node("ul");
    for (const item of result.affected_workflows) {
      const label = Object.hasOwn(workflowLabels, item.workflow) ? workflowLabels[item.workflow] : "Weiterer Ablauf";
      const effect = Object.hasOwn(workflowEffects, item.workflow) ? workflowEffects[item.workflow] : item.effect;
      const entry = node("li");
      entry.append(node("strong", label), node("span", ` · ${effect}`));
      list.append(entry);
    }
    target.append(node("h4", "Betroffene Abläufe"), list);
  }
  if (result.withheld.length) {
    const list = node("ul");
    for (const item of result.withheld) {
      const row = item.scope && access.rows.find(row => scopeKey(row.scope) === scopeKey(item.scope));
      list.append(node("li", [item.scope && scopeLabel(item.scope, row?.name), item.operation && (operationLabels[item.operation] || item.operation),
        accessReason(item.code), item.next_step].filter(Boolean).join(" · ")));
    }
    target.append(node("h4", "Nicht freigegebene Zugriffe"), list);
    appendAccessCodes(target, result.withheld.map(item => item.code));
  }
  $("access-review-panel").hidden = false;
  $("access-review-heading").focus?.();
}
function expireAccessPreview() {
  clearAccessPreview();
  message("Die Zugriffsvorschau ist abgelaufen. Änderungen erneut prüfen.");
}
function isAccessPreviewCurrent() {
  const preview = access.preview;
  return mayEditAccess() && preview && preview.context === accessContext()
    && performance.now() < preview.deadline && Date.now() < Date.parse(preview.result.expires_at);
}
async function loadAccessAfterFailure(error) {
  const conflict = accessConflicts.has(error?.code);
  access.blocked = true; clearAccessDraft(); renderAccess(); renderOverview();
  let statusRead = false;
  if (state.authenticated) {
    try {
      const requestedAt = performance.now();
      renderStatus(await gateway.call("status"), requestedAt);
      statusRead = true;
    } catch { /* Explicit status reload remains available if this read cannot be verified. */ }
  }
  failure(new Error(conflict ? "Die Zugriffsvorschau passt nicht mehr zum aktuellen Stand. Änderungen erneut auswählen und prüfen."
    : "Die Speicherung der Freigaben konnte nicht bestätigt werden. Prüfe den angezeigten Stand vor einer neuen Änderung."));
  message(statusRead ? "Aktueller gespeicherter Stand geladen." : "Mit OpenClaw verbinden oder „Status neu laden“ wählen, um den gespeicherten Stand zu prüfen.");
}
$("access-root").addEventListener("change", () => { clearAccessPreview(); updateAccessControls(); });
$("access-more").addEventListener("click", () => run(async () => {
  if ($("access-more").hidden || !mayEditAccess()) return;
  const current = state.current, offset = current.access.next_offset, requestedAt = performance.now();
  let page;
  try {
    page = await gateway.call("status", { expected_revision: current.revision, access_offset: offset });
    if (!mayEditAccess() || !page?.connected || accessContext(page) !== accessContext(current)
      || (page.access.next_offset != null && (!Number.isSafeInteger(page.access.next_offset) || page.access.next_offset <= offset))) {
      throw new Error("Der Prüfstand für weitere Ressourcen hat sich geändert.");
    }
  } catch (error) {
    access.blocked = true; clearAccessDraft(); renderAccess(); renderOverview();
    if (state.authenticated) await loadStatus();
    throw error;
  }
  for (const row of page.access.rows) rowRequestedAt.set(row, requestedAt);
  const rows = new Map([...current.access.rows, ...page.access.rows].map(row => [rowKey(row), row]));
  // This continuation adds rows to the original status. Keep the original
  // overview and its age tied to that status instead of renewing its timer.
  state.current = { ...current, access: { ...page.access, overview: current.access.overview, rows: [...rows.values()] } };
  renderAccess(); renderOverview(); message("Weitere Ressourcen geladen.");
}));
$("access-review").addEventListener("click", () => run(async () => {
  if ($("access-review").disabled || !mayEditAccess()) return;
  clearAccessPreview();
  const context = accessContext(), current = state.current;
  const params = { account_id: current.account_id, expected_revision: current.revision,
    expected_policy_revision: current.access.binding?.policy_revision || "", edits: [...access.edits.values()].map(edit => edit.revoke_handle
      ? { revoke_handle: edit.revoke_handle, level: "off" } : edit),
    confirm_root: !$("access-root-wrap").hidden && $("access-root").checked };
  let result;
  try { result = await gateway.call("access.preview", params); }
  catch (error) {
    if (!accessConflicts.has(error?.code)) throw error;
    clearAccessDraft(); await loadStatus();
    throw new Error("Die Zugriffsübersicht hat sich geändert. Änderungen auf Basis des aktuellen Stands erneut auswählen.");
  }
  if (!mayEditAccess() || context !== accessContext() || result?.state !== "ready"
      || result.expected_revision !== params.expected_revision || result.expected_policy_revision !== params.expected_policy_revision
      || !result.preview_id || !result.preview_digest) throw new Error("Die Zugriffsvorschau passt nicht zum aktuellen Stand. Status neu laden.");
  const remaining = Date.parse(result.expires_at) - Date.now();
  if (!Number.isFinite(remaining) || remaining <= 0) { expireAccessPreview(); return; }
  access.preview = { result, context, deadline: performance.now() + remaining };
  accessExpiryTimer = window.setTimeout(expireAccessPreview, remaining);
  renderAccessPreview(result, params);
  message("Zugriffsvorschau bereit. Prüfe die Änderungen und bestätige sie zum Speichern.");
}));
$("access-confirm").addEventListener("change", () => { $("access-apply").disabled = !$("access-confirm").checked || !isAccessPreviewCurrent(); });
$("access-apply").addEventListener("click", () => {
  if (!$("access-confirm").checked || !access.preview || state.busy) return;
  if (!isAccessPreviewCurrent()) { expireAccessPreview(); return; }
  const { result } = access.preview, current = state.current;
  clearAccessPreview();
  return run(async () => {
    try {
      const requestedAt = performance.now();
      const saved = await gateway.call("access.apply", { preview_id: result.preview_id, preview_digest: result.preview_digest,
        expected_revision: result.expected_revision, expected_policy_revision: result.expected_policy_revision, confirm: true });
      const binding = saved?.access?.binding, previousBinding = current.access.binding;
      if (!saved?.connected || saved.account_id !== current.account_id || saved.user_id !== current.user_id
          || !saved.revision || saved.revision === current.revision || saved.access?.mode !== "active"
          || binding?.account_id !== current.account_id || binding?.owner_user_id !== current.user_id
          || !binding.policy_revision || !binding.generation || (previousBinding && binding.generation !== previousBinding.generation)) {
        await loadAccessAfterFailure(); return;
      }
      renderStatus(saved, requestedAt);
      message("Zugriffsrichtlinie gespeichert. Der neue Stand und die getrennte Laufzeitbeobachtung werden angezeigt.");
    } catch (error) { await loadAccessAfterFailure(error); }
  });
});
function renderStatus(current, requestedAt) {
  const previous = state.current;
  if (!current.connected || current.account_id !== state.current?.account_id || current.revision !== state.current?.revision) {
    showFolder = null; window.clearTimeout(folderExpiryTimer); $("folders").replaceChildren();
  }
  if (accessContext(current) !== accessContext()) clearAccessDraft();
  for (const row of current.access?.rows || []) rowRequestedAt.set(row, requestedAt);
  state.current = current;
  access.blocked = false;
  overviewRefreshState = null;
  renderAccess();
  // Include the entire RPC duration conservatively; transport time must never
  // extend a server observation's lifetime. This also avoids clock-skew claims.
  talkReceivedAt = overviewReceivedAt = requestedAt;
  window.clearTimeout(talkExpiryTimer);
  const displayName = current.preview?.user_id === current.user_id ? current.preview?.display_name : null;
  const account = displayName && displayName !== current.user_id ? `${displayName} (${current.user_id})` : current.user_id;
  const credentials = current.credentials_stored === true ? "App-Passwort gespeichert"
    : current.credentials_stored === false ? "Kein App-Passwort gespeichert" : "Speicherstatus des App-Passworts unbekannt";
  $("current").textContent = current.connected
    ? `${current.server_url} · ${account} · ${credentials}`
    : current.state === "failed" ? "Gespeicherte Verbindung nicht verifizierbar." : "Noch keine Nextcloud-Verbindung gespeichert.";
  if (!state.flow && (!previous || current.connected !== previous.connected || current.server_url !== previous.server_url
    || current.user_id !== previous.user_id)) {
    $("credentials-option").open = !current.connected;
    $("server-url").value = current.server_url || "";
    $("login-name").value = "";
  }
  $("connection-edit").textContent = current.connected ? "Verbindung bearbeiten" : "Verbindung einrichten";
  $("refresh").hidden = !current.connected || Boolean(state.flow);
  $("folders-section").hidden = !current.connected;
  $("talk-section").hidden = !current.connected;
  if (current.preview && !state.ready) {
    renderPreview(current.preview);
    const failure = current.refresh_failure;
    const refreshFailure = failure?.revision === current.revision && failure?.account_id === current.account_id ? failure : null;
    renderDiagnostics(current.preview, { current, freshness: current.diagnostics_freshness, domainFreshness: current.diagnostics_domain_freshness, requestedAt, refreshFailure });
  }
  if (!current.connected && !state.flow) {
    $("preview-section").hidden = true;
    pendingDiagnostics("Noch nicht geprüft. Melde zuerst das Nextcloud-Konto an.");
  }
  if (current.connected) renderTalk(current);
  else renderTalkRuntime();
  renderOverview();
}
function clearFlow() {
  state.flow = null; state.ready = null; $("login-pending").hidden = true;
  $("flow-actions").hidden = true; $("app-password").value = "";
  $("login-link").removeAttribute("href"); $("apply-panel").hidden = true;
  $("confirm-access").checked = false; $("apply").disabled = true;
  $("refresh").hidden = !state.current?.connected;
  $("talk-section").hidden = !state.current?.connected;
  renderAccess(); renderOverview(); renderTalkRuntime();
}
function showReady(result, requestedAt) {
  state.flow = result.flow_id; state.ready = result;
  renderAccess();
  renderPreview(result.preview); renderDiagnostics(result.preview, { freshness: result.diagnostics_freshness, domainFreshness: result.diagnostics_domain_freshness, requestedAt });
  $("login-pending").hidden = true; $("flow-actions").hidden = false;
  $("apply-panel").hidden = false; $("refresh").hidden = true;
  $("confirm-access").checked = false; $("apply").disabled = true;
  renderOverview();
  $("preview-heading").focus();
  message("Benutzer geladen. Prüfe die Zugriffsdiagnose und die Ressourcen, bevor du die Verbindung bestätigst.");
}
function showLoginFailure(result, options = {}) {
  if (!options.refresh) {
    clearFlow(); $("preview-section").hidden = true;
    renderDiagnostics({ diagnostics: result.diagnostics || {}, checked_at: result.checked_at });
  }
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
  let server;
  try { server = new URL($("server-url").value.trim()); }
  catch { throw new Error("Eine gültige HTTPS-Serveradresse eingeben."); }
  if (server.protocol !== "https:" || server.username || server.password || server.search || server.hash) throw new Error("Eine HTTPS-Serveradresse ohne Zugangsdaten, Suchparameter oder Fragment eingeben.");
  return server;
}
async function loadStatus({ initial = false } = {}) {
  const requestedAt = performance.now();
  clearAccessPreview();
  const current = await gateway.call("status");
  const previewInvalidated = state.ready && current.revision !== state.ready.expected_revision;
  if (previewInvalidated) clearFlow();
  renderStatus(current, requestedAt);
  message(previewInvalidated
    ? "Der gespeicherte Verbindungsstand hat sich geändert. Die alte Einrichtungsvorschau wurde geschlossen; der aktuelle Status wird angezeigt."
    : initial ? "" : "Gespeicherter Verbindungsstatus geladen.");
}

$("connect").addEventListener("click", () => run(async () => {
  if (window.top !== window.self) throw new Error("Diese Einrichtung bitte über den Link in einem eigenen OpenClaw-Tab öffnen.");
  gateway?.close();
  gateway = createSetupGateway({ origin: location.origin, basePath: document.body.dataset.controlUiBasePath || "",
    storage: window.localStorage, onClose: disconnected });
  await gateway.connect(); state.authenticated = true;
  $("auth-status").textContent = "Verbunden"; $("reload").hidden = false; $("connect").hidden = true;
  renderOverview(); renderTalkRuntime();
  await loadStatus({ initial: true });
}));
$("reload").addEventListener("click", () => run(loadStatus));
$("login-form").addEventListener("submit", event => { event.preventDefault(); return run(async () => {
  const server = selectedServer();
  clearAccessDraft(); clearFlow();
  pendingDiagnostics("Nextcloud-Anmeldung wird vorbereitet. Benutzer und Ressourcen sind noch nicht geprüft.");
  const result = await gateway.call("login.start", { server_url: server.toString() });
  clearFlow(); state.flow = result.flow_id;
  renderAccess();
  const login = new URL(result.login_url);
  if (login.protocol !== "https:" || login.origin !== server.origin || login.username || login.password) throw new Error("Die Nextcloud-Anmeldeadresse ist ungültig.");
  $("login-link").href = login.toString(); $("login-pending").hidden = false;
  $("flow-actions").hidden = false;
  $("preview-section").hidden = true; $("talk-section").hidden = true;
  $("refresh").hidden = true;
  renderOverview(); renderTalkRuntime();
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
      clearAccessDraft(); clearFlow(); $("preview-section").hidden = true; $("talk-section").hidden = true;
      pendingDiagnostics("Prüfung läuft: Benutzer, Kalender, Aufgabenlisten, Adressbücher, Ordner und Nextcloud Talk.");
      const requestedAt = performance.now();
      const result = await gateway.call("login.credentials", { server_url: server.toString(), login_name: name, app_password: password });
      if (result.state === "ready") showReady(result, requestedAt);
      else if (result.state === "failed") showLoginFailure(result);
      else throw new Error("Die Anmeldung lieferte kein prüfbares Ergebnis.");
    } finally { password = ""; $("app-password").value = ""; }
  }, { login: true });
});
$("poll").addEventListener("click", () => run(async () => {
  if (!state.flow) throw new Error("Zuerst eine Nextcloud-Anmeldung starten.");
  pendingDiagnostics("Anmeldestatus und verfügbare Ressourcen werden abgefragt …");
  const requestedAt = performance.now();
  const result = await gateway.call("login.poll", { flow_id: state.flow });
  if (result.state === "ready") {
    showReady(result, requestedAt);
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
  if (!state.ready || !$("confirm-access").checked) throw new Error("Die Verbindung mit dem angezeigten Konto zuerst bestätigen.");
  let result;
  const requestedAt = performance.now();
  try {
    result = await gateway.call("connection.apply", { flow_id: state.flow, preview_digest: state.ready.preview_digest,
      expected_revision: state.ready.expected_revision, confirm_account_access: true });
  } catch {
    clearFlow(); $("preview-section").hidden = true;
    pendingDiagnostics("Die Speicherung konnte nicht bestätigt werden. Lade zuerst den Status neu; die bisherige Vorschau ist geschlossen.");
    throw new Error("Die Verbindung konnte nicht bestätigt werden. „Status neu laden“ wählen und den gespeicherten Stand prüfen, bevor du eine neue Anmeldung beginnst.");
  }
  clearFlow(); renderStatus(result, requestedAt);
  $("credentials-option").open = false; $("login-name").value = ""; $("server-url").value = result.server_url;
  $("connection-edit").focus();
  message("Nextcloud-Verbindung gespeichert. Prüfe die Freigaben für den Agenten und die Talk-Einstellung separat.");
}));
$("refresh").addEventListener("click", () => run(async () => {
  clearAccessDraft();
  showFolder = null; window.clearTimeout(folderExpiryTimer); $("folders").replaceChildren();
  pendingDiagnostics("Benutzer und Ressourcen werden erneut auf dem Nextcloud-Server geprüft …");
  overviewRefreshState = "pending"; renderOverview();
  const requestedAt = performance.now();
  const current = state.current;
  let result;
  try { result = await gateway.call("connection.refresh", { expected_revision: current.revision }); }
  catch (error) {
    renderDiagnostics(current.preview, { current, refreshFailure: {} });
    access.blocked = true; overviewRefreshState = "failed";
    renderAccess(); renderOverview(); renderTalkRuntime();
    throw error;
  }
  if (result.state === "failed") {
    overviewRefreshState = "failed"; renderOverview(); renderTalkRuntime();
    clearFlow();
    if (result.source_revision === current.revision && result.account_id === current.account_id && result.revision !== current.revision) {
      await loadStatus();
      showLoginFailure(result, { refresh: true });
      message("Die erneute Prüfung ist fehlgeschlagen. Der letzte bestätigte Prüfstand und der aktuelle Fehler sind gespeichert.");
      return;
    }
    const matching = result.account_id === current.account_id && result.revision === current.revision;
    state.current = { ...current, refresh_failure: matching ? result : {} };
    renderAccess(); renderOverview(); renderTalkRuntime();
    renderDiagnostics(current.preview, { current, refreshFailure: matching ? result : {} });
    showLoginFailure(result, { refresh: true });
    message("Die erneute Prüfung ist fehlgeschlagen. Die bisher gespeicherte Verbindung bleibt unverändert; ihr alter Prüfstand belegt keinen aktuellen Zugriff.");
    return;
  }
  renderStatus(result, requestedAt); message("Ressourcen erneut erkannt. Angezeigte Einschränkungen bitte beachten.");
}));
function recordFolderMeasurement(path, row) {
  for (const prior of access.rows) {
    if (prior.scope.component !== "FILES" || prior.scope.path !== path || prior.scope.file_id === row?.scope.file_id) continue;
    const code = row ? "folder-identity-changed" : "rights-unverified";
    access.inspectedFolders.set(rowKey(prior), {
      ...prior,
      server: { operations: [], checked_at: null, state: "unverified", read_check: row ? "unknown" : "failed", code },
      effective_operations: [], supported_levels: prior.supported_levels.includes("off") ? ["off"] : [],
    });
  }
  if (row) access.inspectedFolders.set(rowKey(row), row);
  renderAccess();
}
async function browse(path) {
  const initial = state.current;
  const draft = [...access.edits];
  let removedChoices = 0;
  if (access.preview) clearAccessPreview();
  showFolder = null; window.clearTimeout(folderExpiryTimer);
  $("folders").replaceChildren(node("p", "Der angegebene Ordner wird geprüft …", "muted"));
  let result;
  const requestedAt = performance.now();
  try { result = await gateway.call("folders.list", { path }); }
  catch (error) {
    // A failed exact probe may have withdrawn persisted evidence.
    access.blocked = true;
    clearAccessDraft(); renderAccess(); renderOverview();
    if (state.authenticated) await loadStatus();
    recordFolderMeasurement(path.replace(/^\/+|\/+$/g, ""), null);
    $("folders").replaceChildren(node("p", "Der Zugriff auf diesen Ordner konnte nicht bestätigt werden.", "error"));
    throw error;
  }
  if (result.account_id === initial.account_id && result.source_revision === initial.revision
      && result.revision !== initial.revision) {
    await loadStatus();
    const latest = state.current, binding = initial.access?.binding;
    const ownMeasurement = binding && latest.account_id === initial.account_id && latest.user_id === initial.user_id
      && latest.revision === result.revision && latest.access?.binding?.generation === binding.generation
      && latest.access.binding.policy_revision === binding.policy_revision;
    for (const [key, edit] of draft) {
      const row = ownMeasurement && latest.access.rows.find(row => scopeKey(row.scope) === scopeKey(edit.scope));
      if (row && !edit.revoke_handle && !row.revoke_handle && allowedLevels(row).includes(edit.level)) {
        access.edits.set(key, edit);
      } else removedChoices++;
    }
    renderAccess();
  }
  const bound = result.account_id === state.current?.account_id && result.revision === state.current?.revision;
  if (result.revision && !bound) {
    access.blocked = true; clearAccessDraft(); renderAccess(); renderOverview();
    $("folders").replaceChildren(node("p", "Die Verbindung hat sich geändert. Den Status neu laden und den Ordner erneut prüfen.", "error"));
    message(""); return;
  }
  const candidate = bound && result.access_row;
  const exact = candidate?.scope?.component === "FILES" && candidate.scope.path === result.path && candidate.scope.file_id;
  const accessRow = exact ? candidate : null;
  if (accessRow) rowRequestedAt.set(accessRow, requestedAt);
  if (bound) recordFolderMeasurement(result.path ?? path, accessRow);
  $("folder-path").value = result.path || "";
  showFolder = () => {
    window.clearTimeout(folderExpiryTimer);
    const target = $("folders"); target.replaceChildren(); issues(target, result);
    const own = bound ? result.selected_folder : null;
    if (own) {
      const age = evidenceTime(result.checked_at, result.diagnostics_freshness, requestedAt);
      target.append(node("p", `Geprüft am: ${result.checked_at || "unbekannt"} · ${evidenceLabels[age.state]}`, "muted"));
      const right = value => value === true ? "bestätigt" : value === false ? "fehlt" : "unbekannt";
      target.append(node("p", `Geprüfter Ordner: ${own.path || "Kontohauptordner"}. Gemeldete Serverrechte. Lesen: ${right(own.can_read)}. Dateien erstellen: ${right(own.can_create_file)}. Unterordner erstellen: ${right(own.can_mkdir)}.`));
      target.append(node("p", `Leseprüfung: ${own.read_check?.status === "ok" ? "erfolgreich" : "unbestätigt"}${own.read_check?.code ? " (" + own.read_check.code + ")" : ""}. Prüfumfang: eine Ordnerebene.`, "muted"));
      if (age.remaining > 0) folderExpiryTimer = window.setTimeout(showFolder, age.remaining + 1);
      if (accessRow) {
        const link = node("a", "Zugriff für den Agenten festlegen"); link.href = "#access-heading";
        target.append(link);
      }
    }
    const list = node("ul");
    for (const item of result.folders || []) {
      const entry = node("li"); const button = node("button", resourceText(item), "secondary"); button.type = "button";
      button.addEventListener("click", () => run(() => browse(item.path)));
      entry.append(button); list.append(entry);
    }
    target.append(list);
  };
  showFolder();
  removedChoices = Math.max(removedChoices, draft.filter(([key]) => !access.edits.has(key)).length);
  message(removedChoices ? `Ordner geladen. ${removedChoices} frühere Auswahl(en) bitte erneut auswählen.` : "Ordner geladen.");
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
  clearAccessPreview();
  const requestedAt = performance.now();
  const result = await gateway.call("talk.configure", { enabled, room_tokens: rooms, allowed_user_ids: users, expected_revision: state.current.revision });
  renderStatus(result, requestedAt); message(enabled ? "Talk-Auswahl gespeichert und aktiviert. Ob der Chat-Dienst verbunden ist, zeigt der Abschnitt „Chat-Dienst“." : "Talk deaktiviert.");
}));
window.addEventListener("focus", () => {
  showDiagnostics?.(); showFolder?.(); renderOverview(); renderTalkRuntime();
  if (access.preview && !isAccessPreviewCurrent()) expireAccessPreview();
  if (access.rows.some(row => expiredRights(row) && ["verified", "partial"].includes(row.server?.state))) renderAccess();
});
window.addEventListener("pagehide", () => { $("app-password").value = ""; clearAccessDraft(); gateway?.close(); });
addOverviewCards();
renderOverview(); renderTalkRuntime();
