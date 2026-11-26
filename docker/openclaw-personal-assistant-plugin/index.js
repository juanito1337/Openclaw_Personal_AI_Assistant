import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { readFileSync } from "node:fs";
import { registerSetupUi } from "./setup-ui.js";
import { createNextcloudAccountTalkChannel } from "./nextcloud-account-talk.js";
import {
  advanceActionObligation,
  actionWriteBlock,
  approvalDecision,
  approvalSeverity,
  buildActionObligation,
  compileInvocation,
  createApprovalLedger,
  createRunRetention,
  dryRunStatusAnswer,
  RUN_RETENTION,
  guardAnswer,
  guardActionCompletion,
  isSimpleConfirmation,
  makeEvidence,
  projectPayload,
  readNativeAccess,
  requiresNativeAccess,
  routePrompt,
  selectPendingConfirmation,
  shouldBlockGenericTool,
  spawnJson,
  stableDigest,
  tokenizeCommand,
  validateArguments,
} from "./runtime.js";

// OpenClaw loads plugin entrypoints through its synchronous discovery loader.
// Top-level await works when this file is imported directly by Node, but is not
// supported by that loader. Keep initialization synchronous so discovery and the
// actual gateway runtime exercise the same code path.
const contract = JSON.parse(
  readFileSync(new URL("./generated-tools.json", import.meta.url), "utf8"),
);
const operationById = new Map(contract.operations.map((operation) => [operation.tool_id, operation]));
const groupByName = new Map(contract.native_tools.map((group) => [group.name, group]));
const routeByRun = new Map();
const evidenceByRun = new Map();
const obligationByRun = new Map();
const retryByRun = new Set();
const invalidArgumentsByRun = new Map();
const turnLatencyByRun = new Map();
const promptByRun = new Map();
const sessionByRun = new Map();
const requesterByRun = new Map();
const confirmationByRun = new Map();
const writeInFlightByRun = new Map();
const pendingConfirmationsBySession = new Map();
const liveToolsCache = { expiresAt: 0, value: null };
const ledger = createApprovalLedger(contract.limits.approval_ttl_seconds);
function revokeRunAuthorizations(key) {
  ledger.revokeRun(key);
  confirmationByRun.delete(key);
  for (const [session, records] of pendingConfirmationsBySession) {
    const remaining = records.filter((record) => record.run_id !== key);
    if (remaining.length) pendingConfirmationsBySession.set(session, remaining);
    else pendingConfirmationsBySession.delete(session);
  }
}
const runRetention = createRunRetention({ onAuthorizationExpire: revokeRunAuthorizations, onEvict(key) {
  for (const store of [routeByRun, evidenceByRun, obligationByRun, retryByRun,
    invalidArgumentsByRun, turnLatencyByRun, promptByRun, sessionByRun, requesterByRun, confirmationByRun,
    writeInFlightByRun]) store.delete(key);
  revokeRunAuthorizations(key);
} });
const metrics = {
  routed: 0,
  unresolved: 0,
  native_calls: 0,
  native_failures: 0,
  invalid_arguments: 0,
  repeated_invalid_arguments: 0,
  generic_blocks: 0,
  approvals_requested: 0,
  approvals_satisfied_by_explicit_request: 0,
  approvals_satisfied_by_simple_confirmation: 0,
  guard_revisions: 0,
  guard_replacements: 0,
  action_obligations: 0,
  action_completed: 0,
  action_blocked: 0,
  action_approval_required: 0,
  action_information_required: 0,
  action_partial: 0,
  approval_failures: 0,
  turns_completed: 0,
  prompt_preparation_total_ms: 0,
  tool_loop_total_ms: 0,
  answer_finalization_total_ms: 0,
  turn_total_ms: 0,
};

function conversationKey(ctx, event = null) {
  return String(event?.sessionKey || ctx?.sessionKey || ctx?.sessionId || "");
}

function sdkSender(ctx) {
  const sender = ctx?.requesterSenderId || ctx?.senderId;
  return typeof sender === "string" && sender.length ? stableDigest(sender) : null;
}

function sharedSession(ctx, session) {
  return ["group", "channel"].includes(ctx?.chatType) || /(?:^|:)(?:group|channel):/u.test(session);
}

function livePendingConfirmations(session) {
  const now = Date.now();
  // Sweep every session, including abandoned conversations, not just this one.
  for (const [key, records] of pendingConfirmationsBySession) {
    const live = records.filter((item) => item.expires_at > now);
    if (live.length) pendingConfirmationsBySession.set(key, live);
    else pendingConfirmationsBySession.delete(key);
  }
  return pendingConfirmationsBySession.get(session) ?? [];
}

function rememberPendingConfirmation(session, record) {
  if (!session) return;
  const records = livePendingConfirmations(session)
    .filter((item) => item.arguments_digest !== record.arguments_digest || item.operation !== record.operation);
  records.push(record);
  pendingConfirmationsBySession.set(session, records.slice(-4));
  while (pendingConfirmationsBySession.size > RUN_RETENTION.maxRuns) {
    pendingConfirmationsBySession.delete(pendingConfirmationsBySession.keys().next().value);
  }
}

async function currentPendingConfirmation(record, ctx, session) {
  if (!record || !requiresNativeAccess(record.operation)) return record;
  const sender = sdkSender(ctx);
  if (record.sender !== sender || (!sender && sharedSession(ctx, session))) return null;
  try {
    const current = await readNativeAccess(spawnJson);
    if (record.accessDigest === stableDigest(current) && livePendingConfirmations(session).includes(record)) {
      return record;
    }
  } catch {
    // Store failure and revocation both suppress old arguments and confirmation.
  }
  return null;
}

function removePendingConfirmation(session, confirmationId) {
  if (!session) return;
  const records = livePendingConfirmations(session).filter((item) => item.id !== confirmationId);
  if (records.length > 0) pendingConfirmationsBySession.set(session, records);
  else pendingConfirmationsBySession.delete(session);
}

function runKey(ctx, fallback = "unknown-run") {
  return String(ctx?.runId || ctx?.sessionId || ctx?.sessionKey || fallback);
}

function jsonToolResult(payload, details = {}) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
    details,
  };
}

async function readLiveTools({ fresh = false } = {}) {
  if (!fresh && liveToolsCache.value && liveToolsCache.expiresAt > Date.now()) return liveToolsCache.value;
  const result = await spawnJson(
    { executable: "/opt/openclaw-agent/scripts/assistant.sh", argv: ["tools", "list"], stdin: null },
    { ...contract.limits, tool_timeout_seconds: 60 },
  );
  if (result.returncode !== 0) throw new Error("live-tool-catalog-unavailable");
  let payload;
  try {
    payload = JSON.parse(result.stdout);
  } catch {
    throw new Error("live-tool-catalog-invalid");
  }
  const tools = Array.isArray(payload) ? payload : payload?.tools;
  if (!Array.isArray(tools) || tools.some((item) =>
    !item || typeof item.id !== "string" || !item.id ||
    typeof item.command !== "string" || !item.command
  )) throw new Error("live-tool-catalog-invalid");
  liveToolsCache.value = tools;
  liveToolsCache.expiresAt = Date.now() + 30_000;
  return tools;
}

function operationFromCall(toolName, rawParams) {
  const group = groupByName.get(toolName);
  const operationId = typeof rawParams?.operation === "string" ? rawParams.operation : "";
  if (!group || !group.operations.includes(operationId)) throw new Error("operation-not-in-tool-group");
  const operation = operationById.get(operationId);
  if (!operation?.supported) throw new Error("operation-not-supported");
  return operation;
}

function withoutApprovalBinding(params) {
  return { operation: params.operation, arguments: params.arguments ?? {} };
}

function recordActionEvidence(currentRun, operation, evidence, payload, args) {
  if (!runRetention.has(currentRun)) return;
  const obligation = obligationByRun.get(currentRun);
  if (!obligation) return;
  const next = advanceActionObligation(
    contract,
    obligation,
    operation,
    evidence,
    payload,
    stableDigest(args),
  );
  obligationByRun.set(currentRun, next);
  if (!obligation.terminal_state && next.terminal_state) {
    if (next.terminal_state === "completed") metrics.action_completed += 1;
    else if (next.terminal_state === "approval-required") metrics.action_approval_required += 1;
    else if (next.terminal_state === "information-required") metrics.action_information_required += 1;
    else metrics.action_blocked += 1;
    if (next.completed_targets > 0 && next.completed_targets < next.target_count) {
      metrics.action_partial += 1;
    }
  }
}

function rememberEvidence(key, evidence) {
  if (!runRetention.has(key)) return;
  const rows = evidenceByRun.get(key) ?? [];
  // The complete envelope is returned to the tool caller. Reply guards only
  // need these fixed-size proof fields, never arbitrary remote coverage trees.
  if (!rows.some((row) => row.run_id === evidence.run_id)) rows.push({
    tool_id: evidence.tool_id, domain: evidence.domain, run_id: evidence.run_id,
    turn_id: evidence.turn_id, ok: evidence.ok, postcondition_verified: evidence.postcondition_verified,
    allowed_claims: evidence.allowed_claims,
    complete: evidence.complete, calendar_day: evidence.calendar_day, error: evidence.error,
  });
  evidenceByRun.set(key, rows.slice(-32));
}

function stoppedOperation(operation, currentRun, toolCallId, error, remember = true) {
  const approvalMissing = error === "missing-or-stale-bound-approval";
  const payload = { ok: false, complete: false,
    status: approvalMissing ? "approval-required" : error === "tool-aborted" ? "cancelled" : "blocked", executed: false,
    external_write_attempted: false, postcondition_verified: false };
  const evidence = makeEvidence(operation, { returncode: 77, stdout: "", stderr: "", error }, payload, currentRun, toolCallId);
  if (remember) rememberEvidence(currentRun, evidence);
  return jsonToolResult({ evidence, result: payload,
    diagnostic: { category: approvalMissing ? "approval-required" : error,
      code: error, retry_allowed: false, new_tool_call_required: true } },
  { personalAssistantEvidence: evidence, personalAssistantEvidenceAdmissible: remember });
}

async function executeOperation(toolName, toolContext, toolCallId, rawParams, signal) {
  const operation = operationFromCall(toolName, rawParams);
  const key = rawParams?.__approval_run_id || runKey(toolContext, toolCallId);
  const binding = runRetention.acquireInvocation(key, rawParams?.__run_generation);
  if (!binding) return stoppedOperation(operation, key, toolCallId,
    operation.mode === "read" ? "turn-state-missing-or-expired" : "missing-or-stale-bound-approval", false);
  try {
    if (operation.tool_id === 'mail.dry-run' && runRetention.readStarted(key)) {
      return stoppedOperation(operation, key, toolCallId, 'read-execution-not-current-or-already-started', false);
    }
    const response = await executeBoundOperation(toolName, toolContext, toolCallId, rawParams, signal, binding);
    response.details.personalAssistantGeneration = binding.generation;
    return response;
  } finally {
    runRetention.releaseInvocation(binding);
  }
}

async function executeBoundOperation(toolName, toolContext, toolCallId, rawParams, signal, binding) {
  const operation = operationFromCall(toolName, rawParams);
  const args = rawParams?.arguments ?? {};
  // A tool factory can be created before OpenClaw resumes a call after native
  // approval and therefore need not carry the hook's runId.  The opaque run
  // binding is injected by before_tool_call into OpenClaw's frozen approved
  // parameters.  The unguessable nonce still binds operation, arguments, call
  // and original run in the in-memory ledger.
  const approvalRunId = typeof rawParams?.__approval_run_id === "string"
    ? rawParams.__approval_run_id
    : "";
  const currentRun = approvalRunId || runKey(toolContext, toolCallId);
  if (signal?.aborted) return stoppedOperation(operation, currentRun, toolCallId, "tool-aborted");
  const retained = runRetention.canAuthorize(currentRun);
  if (!retained && operation.mode === "read") {
    return stoppedOperation(operation, currentRun, toolCallId, "turn-state-missing-or-expired");
  }
  try {
    validateArguments(operation.argument_schema, args);
  } catch (error) {
    const issue = String(error?.message ?? error).slice(0, 256);
    const signature = `${operation.tool_id}:${stableDigest(args)}:${issue}`;
    const seen = invalidArgumentsByRun.get(currentRun) ?? new Map();
    const previousInvalidCount = [...seen.values()].reduce((total, value) => total + value, 0);
    const count = (seen.get(signature) ?? 0) + 1;
    seen.set(signature, count);
    while (seen.size > 2) seen.delete(seen.keys().next().value);
    if (retained) invalidArgumentsByRun.set(currentRun, seen);
    const retryAllowed = previousInvalidCount === 0;
    const result = {
      returncode: 2,
      stdout: "",
      stderr: "",
      error: `invalid-arguments:${issue}`,
    };
    const evidence = makeEvidence(operation, result, null, currentRun, toolCallId);
    rememberEvidence(currentRun, evidence);
    metrics.native_calls += 1;
    metrics.native_failures += 1;
    metrics.invalid_arguments += 1;
    if (!retryAllowed) metrics.repeated_invalid_arguments += 1;
    recordActionEvidence(currentRun, operation, evidence, null, args);
    return jsonToolResult(
      {
        evidence,
        result: null,
        diagnostic: {
          category: "invalid-arguments",
          detail: issue,
          required_arguments: operation.argument_schema?.required ?? [],
          retry_allowed: retryAllowed,
          fatal: !retryAllowed,
          instruction: retryAllowed
            ? "Argumente genau einmal mit allen Pflichtfeldern korrigieren. Den unveraenderten Aufruf nicht wiederholen."
            : "Sofort stoppen, den identischen Aufruf nicht erneut versuchen und den konkreten Argumentfehler berichten.",
        },
      },
      { personalAssistantEvidence: evidence },
    );
  }
  const approvalBinding = {
      nonce: rawParams?.__approval_nonce,
      operation: operation.tool_id,
      args,
      toolCallId,
      runId: approvalRunId,
  };
  const approvalFailure = () => {
      const result = {
        returncode: 77,
        stdout: "",
        stderr: "",
        error: "missing-or-stale-bound-approval",
      };
      const payload = {
        ok: false,
        complete: false,
        status: "approval-required",
        approval_state: "missing-or-stale",
        executed: false,
        external_write_attempted: false,
        postcondition_verified: false,
      };
      const evidence = makeEvidence(operation, result, payload, currentRun, toolCallId);
      rememberEvidence(currentRun, evidence);
      metrics.native_calls += 1;
      metrics.native_failures += 1;
      metrics.approval_failures += 1;
      recordActionEvidence(currentRun, operation, evidence, payload, args);
      return jsonToolResult(
        {
          evidence,
          result: payload,
          diagnostic: {
            category: "approval-required",
            code: "missing-or-stale-bound-approval",
            retry_allowed: false,
            new_tool_call_required: true,
            instruction: ["mail.reply-send", "mail.compose-send", "mail.forward-send"].includes(operation.tool_id)
              ? "Die gebundene Einzelfreigabe war beim Start nicht mehr gueltig; es wurde keine Mail versendet. Nicht automatisch wiederholen und kein blosses /approve anfordern. Jan muss den unveraenderten, bereits vollstaendig angezeigten Entwurf erneut zum Versand anweisen; der neue native Freigabedialog ist per Schaltflaeche oder mit seinem exakten /approve <ID> allow-once zu bestaetigen."
              : operation.approval_policy?.simple_confirmation_satisfies === true
                ? "Die gebundene Einzelfreigabe war beim Start nicht mehr gueltig; die Aktion wurde nicht ausgefuehrt. Nicht automatisch wiederholen und kein blosses /approve anfordern. Wenn genau diese eine unveraenderte Aktion noch als ausstehend gespeichert ist, kann Jan sie mit JA bestaetigen; andernfalls muss er die konkrete Aktion erneut vollstaendig anweisen."
                : "Die gebundene Einzelfreigabe war beim Start nicht mehr gueltig; die Aktion wurde nicht ausgefuehrt. Nicht automatisch wiederholen und kein blosses /approve anfordern. Jan muss die konkrete Aktion erneut anweisen und den neuen nativen Freigabedialog per Schaltflaeche oder mit seinem exakten /approve <ID> allow-once bestaetigen.",
          },
        },
        { personalAssistantEvidence: evidence },
      );
  };
  const accessBound = requiresNativeAccess(operation.tool_id);
  let nativeAccess;
  const integrityFailure = () => {
    ledger.revoke(approvalBinding.nonce);
    const response = stoppedOperation(operation, currentRun, toolCallId, "policy-integrity-error");
    recordActionEvidence(currentRun, operation, response.details.personalAssistantEvidence, null, args);
    return response;
  };
  if (accessBound) {
    const session = toolContext?.sessionKey;
    const sender = sdkSender(toolContext);
    const original = requesterByRun.get(currentRun)?.sender;
    if ((session && session !== sessionByRun.get(currentRun)) || (sender && original && sender !== original)) {
      ledger.revoke(approvalBinding.nonce);
      return approvalFailure();
    }
    // A resumed factory can expose a sender that was absent at issuance.
    // Only the sender actually sealed by the trusted hook belongs in the nonce.
    approvalBinding.sender = original ?? null;
    try {
      nativeAccess = await readNativeAccess(spawnJson);
    } catch {
      return integrityFailure();
    }
    approvalBinding.nativeAccess = nativeAccess;
  }
  if (operation.mode !== "read") {
    // Check early without spending the nonce on read-only capability discovery.
    // Consume atomically, with no await in between, only at the real write start.
    if (writeInFlightByRun.has(currentRun)) {
      return stoppedOperation(operation, currentRun, toolCallId, "write-already-in-flight");
    }
    if (!ledger.check(approvalBinding)) return approvalFailure();
    const block = !runRetention.canAuthorize(currentRun) ? "turn-state-missing-or-expired"
      : actionWriteBlock(contract, obligationByRun.get(currentRun), operation, args);
    if (block) return stoppedOperation(operation, currentRun, toolCallId, block);
  }
  let liveCommand = operation.command;
  if (operation.availability !== "always") {
    try {
      const liveTools = await readLiveTools({ fresh: accessBound });
      const live = liveTools.find((item) => item.id === operation.tool_id);
      if (!live) throw new Error("operation-disabled-in-live-capabilities");
      liveCommand = live.command;
    } catch (error) {
      // Discovery failures are current-turn evidence too. Never expose raw
      // catalog output or start the domain command after a failed check.
      const code = ["operation-disabled-in-live-capabilities", "live-tool-catalog-invalid"]
        .includes(error?.message) ? error.message : "live-tool-catalog-unavailable";
      const response = stoppedOperation(operation, currentRun, toolCallId, code);
      metrics.native_calls += 1;
      metrics.native_failures += 1;
      recordActionEvidence(currentRun, operation, response.details.personalAssistantEvidence, null, args);
      return response;
    }
  }
  const invocation = compileInvocation(operation, args, liveCommand);
  if (accessBound && operation.availability !== "always") {
    try {
      const current = await readNativeAccess(spawnJson);
      if (stableDigest(current) !== stableDigest(nativeAccess)) {
        ledger.revoke(approvalBinding.nonce);
        if (operation.mode !== "read") return approvalFailure();
        return stoppedOperation(operation, currentRun, toolCallId, "policy-changed");
      }
      approvalBinding.nativeAccess = current;
    } catch {
      return integrityFailure();
    }
  }
  // Capability discovery may have yielded to other runs. Never resurrect an
  // evicted/expired run or act after another call made its obligation terminal.
  const executionBlock = !(runRetention.invocationCurrent(binding) && runRetention.canAuthorize(currentRun))
    ? "turn-state-missing-or-expired"
    : actionWriteBlock(contract, obligationByRun.get(currentRun), operation, args);
  if (executionBlock) return stoppedOperation(operation, currentRun, toolCallId, executionBlock);
  if (operation.mode !== "read" && writeInFlightByRun.has(currentRun)) {
    return stoppedOperation(operation, currentRun, toolCallId, "write-already-in-flight");
  }
  if (signal?.aborted) return stoppedOperation(operation, currentRun, toolCallId, "tool-aborted");
  const executionLimits = { ...contract.limits, ...operation.execution_limits };
  const retainedRead = operation.tool_id === "mail.dry-run" && operation.mode === "read";
  if (retainedRead && !runRetention.retainRead(currentRun, toolCallId,
      executionLimits.tool_timeout_seconds * 1000, executionLimits.finalization_grace_seconds * 1000)) {
    return stoppedOperation(operation, currentRun, toolCallId, "read-execution-not-current-or-already-started", false);
  }
  if (operation.mode !== "read" && !ledger.consume(approvalBinding)) return approvalFailure();
  if (operation.mode !== "read") writeInFlightByRun.set(currentRun, approvalBinding.nonce);
  const toolStarted = Date.now();
  let result;
  try {
    result = await spawnJson(invocation, executionLimits, { nativeAccess, signal: retainedRead ? signal : undefined });
  } finally {
    if (retainedRead) runRetention.finishRead(currentRun, toolCallId);
    if (operation.mode !== "read" && writeInFlightByRun.get(currentRun) === approvalBinding.nonce) {
      writeInFlightByRun.delete(currentRun);
    }
  }
  if (!runRetention.invocationCurrent(binding)) {
    // The command ran, but its originating context was evicted or expired.
    // Discard its payload and never publish positive evidence into a new run.
    const evidence = makeEvidence(operation,
      { returncode: 77, error: "turn-state-missing-or-expired" }, null, currentRun, toolCallId);
    return jsonToolResult({ evidence, result: null, diagnostic: {
      category: "turn-state-missing-or-expired", retry_allowed: false, new_tool_call_required: true,
    } }, { personalAssistantEvidence: evidence });
  }
  const toolElapsed = Math.max(0, Date.now() - toolStarted);
  metrics.tool_loop_total_ms += toolElapsed;
  const currentTiming = turnLatencyByRun.get(currentRun);
  if (currentTiming) currentTiming.tool_loop_ms += toolElapsed;
  let payload = null;
  try {
    payload = JSON.parse(result.stdout);
  } catch {
    payload = null;
  }
  if (payload !== null) payload = projectPayload(payload, contract.limits).payload;
  const evidence = makeEvidence(operation, result, payload, currentRun, toolCallId);
  rememberEvidence(currentRun, evidence);
  metrics.native_calls += 1;
  if (!evidence.ok) metrics.native_failures += 1;
  recordActionEvidence(currentRun, operation, evidence, payload, args);
  const response = {
    evidence,
    result: payload,
    diagnostic: evidence.ok
      ? null
      : {
          category: evidence.error,
          stderr: String(result.stderr ?? "").slice(0, contract.limits.max_error_bytes),
        },
  };
  if (operation.tool_id === 'mail.dry-run') {
    response.response_instruction = `Antworte zum Dry-Run exakt: ${dryRunStatusAnswer(contract, [evidence])}`;
  }
  if (operation.tool_id === "assistant.agent-tools.status" && payload && typeof payload === "object") {
    response.result = { ...payload, runtime_metrics: { ...metrics }, pending_approvals: ledger.size() };
  }
  return jsonToolResult(response, { personalAssistantEvidence: evidence });
}

function buildRoutingContext(route, obligation, confirmation = null) {
  const lines = [
    "PERSONAL_ASSISTANT_TOOL_ROUTE_V1",
    "Aktueller Zustand darf nur aus einem strukturierten Personal-Assistant-Tool dieses Laufs beantwortet werden.",
    "Keine gepunktete Katalog-ID und keinen assistant.sh-/Himalaya-Befehl ueber exec ausfuehren.",
    "Jeden nativen Aufruf mit operation und allen Pflichtfeldern unter arguments ausfuehren; arguments niemals leer lassen, wenn die Signatur Pflichtfelder nennt.",
    "Nach invalid-arguments genau einmal mit geaenderten vollstaendigen Argumenten korrigieren. Bei retry_allowed=false sofort stoppen und den Fehler berichten.",
    "Mail: letzte eingegangene Mails des gesamten Kontos mit mail.recent und leerem arguments-Objekt; INBOX kann nach automatischen Verschiebungen leer sein. mail.list nur fuer einen ausdruecklich genannten Einzelordner mit arguments.folder; Suche mit mail.search und arguments.query; mail.read erst nach einem Treffer mit folder, message_id und expected_subject.",
    `Mail-Dry-Run: Nutze mail.dry-run mit arguments: {}. Bei erfolgreichem vollstaendigem Nachweis antworte exakt: ${contract.mail_dry_run_status.success_answer} Bei Fehlern verwende die belegte response_instruction des Tools. Dieser Nachweis erlaubt keine Aussagen zu Mailinhalten oder Schreibaktionen.`,
    "Kalender: Fuer heutige Termine nextcloud.calendar.today mit leerem arguments-Objekt verwenden; damit werden alle ausgewaehlten Kalender in der konfigurierten Zeitzone samt Serienvorkommen geprueft. calendar.list oder eine Textsuche belegt keinen leeren Tag. Nur absence_proven=true mit vollstaendigem Tagesumfang erlaubt exakt die Antwort: Heute hast du keine Termine. Keine weiteren Aussagen an diesen Abwesenheitsbeleg anhaengen; sonst positive Teilbefunde und konkrete Einschraenkungen nennen.",
    "Bei Schreibwuenschen zuerst read-only identifizieren/vorschauen. Ein eindeutiger aktueller Nutzerauftrag kann eine begrenzte Routineaktion bereits freigeben; sensible Aenderungen behalten ihren nativen Einmal-Dialog.",
    "Freigaben: Die Schaltflaeche ist der normale Dialogweg; eine sichtbare ID ist nur technischer Fallback. Ein einfaches Ja gilt nur fuer genau eine intern gespeicherte, unveraenderte und noch gueltige Aktion. Bei missing-or-stale-bound-approval wurde nichts ausgefuehrt: nicht automatisch wiederholen und keinen alten Befehl empfehlen.",
    "Mail: Ein Entwurf ist kein Versand. Nach mail.reply-draft, mail.compose-draft oder mail.forward-draft Empfaenger, Betreff und vollstaendigen Text anzeigen; bei Weiterleitungen auch ZIP-Digest und Original-Anhangsnamen. Den Turn als approval-required beenden. Den passenden reply-, compose- oder forward-send erst nach einer danach erteilten ausdruecklichen Versandanweisung und eigener nativer Einzelfreigabe aufrufen.",
    "Eine ACTION_OBLIGATION_V1 muss in diesem Turn durch registrierte Tool-Evidenz bis zu einem typisierten Endzustand gefuehrt werden. Blosse Zukunftsversprechen, Meta-Ankuendigungen oder ein stiller Abbruch sind kein Abschluss.",
    `Route: ${JSON.stringify(route)}`,
    `ACTION_OBLIGATION_V1: ${JSON.stringify(obligation)}`,
  ];
  if (confirmation) {
    lines.push(
      `CONFIRMED_ACTION_V1: ${JSON.stringify({ operation: confirmation.operation, arguments: confirmation.arguments })}`,
      "Die kurze Nutzerbestaetigung ist intern exakt an diese eine Aktion gebunden. Fuehre jetzt genau diese registrierte Operation mit unveraenderten Argumenten aus; jede Abweichung benoetigt eine neue Freigabe.",
    );
  }
  return lines.join("\n");
}

function safeReplacement(issues) {
  return `Ich kann diese Zustandsaussage nicht belegen. Der Personal-Assistant-Antwortschutz hat die Ausgabe gestoppt (${issues.join(", ")}). Bitte den registrierten Status- oder Suchpfad erneut ausfuehren; keine weitere Schreibaktion wird automatisch gestartet.`;
}

function safeActionReplacement(obligation, issues) {
  const state = obligation?.terminal_state ?? "open";
  const step = obligation?.current_step ?? "unknown";
  const restartInstruction = obligation?.workflow_kind === "mail-to-calendar"
    ? "Bitte sende als neue, eigenstaendige Anweisung: \"Trage Hin- und Rueckflug aus der zuvor ausgewaehlten Mail als zwei Termine in meinen Kalender ein.\""
    : "Bitte weise die konkrete Aktion als neuen, vollstaendigen Satz erneut an.";
  if (state === "approval-required") {
    const progress = obligation?.completed_targets > 0
      ? ` Bereits belegt abgeschlossen: ${obligation.completed_targets} von ${obligation.target_count}.` : "";
    return `Die gebundene Einzelfreigabe ist nicht mehr gueltig oder fuer den naechsten Schritt noch nicht erteilt; dieser nicht freigegebene Schritt wurde nicht ausgefuehrt.${progress} Ein blosses /approve kann keine alte Freigabe wiederbeleben. Wenn genau eine unveraenderte begrenzte Aktion noch intern als ausstehend gebunden ist, reicht ein kurzes JA; andernfalls gilt: ${restartInstruction} Bei einer sensiblen Aktion bestaetige den neuen nativen Dialog per Schaltflaeche oder mit der dort angezeigten exakten ID als allow-once.`;
  }
  if (state === "information-required" && obligation?.last_error === "multiple-targets-not-bound") {
    return "Der Mehrfachauftrag ist nicht eindeutig an einzelne Ziele gebunden; es wurde keine Schreibaktion gestartet. Bitte weise zunaechst genau eine konkrete Aktion mit ihrem Ziel vollstaendig an. Ein blosses JA kann den fehlenden Zielbezug nicht ersetzen.";
  }
  if (state === "blocked") {
    const completed = Number(obligation?.completed_targets ?? 0);
    const total = Number(obligation?.target_count ?? 0);
    const progress = total > 1 ? ` Belegt abgeschlossen: ${completed} von ${total}.` : "";
    return `Die angeforderte Aktion wurde nicht vollstaendig abgeschlossen. Belegter Blocker: ${obligation?.last_error ?? "unknown"}.${progress} Eine automatische Fortsetzung und eine blosse Ja/Nein-Bestaetigung sind nicht zulaessig. ${restartInstruction}`;
  }
  return `Die angeforderte Aktion ist nicht als abgeschlossen belegt. Der Aktionsschutz hat die Ausgabe gestoppt (${issues.join(", ")}; Zustand=${state}; Schritt=${step}). Bitte den registrierten naechsten Werkzeugschritt ausfuehren oder den belegten Blocker melden.`;
}

export default definePluginEntry({
  id: contract.plugin_id,
  name: "Personal Assistant Tools",
  description: "Structured Personal Assistant operations with deterministic routing and evidence guards.",
  register(api) {
    // Only the dedicated deployment roles provide these mounts/transports.
    // Registration starts no remote activity; the account worker remains idle
    // until the operator has paired Nextcloud and explicitly enabled Talk.
    if (process.env.OPENCLAW_SETUP_SOCKET && api.pluginConfig?.setupUiEnabled !== false) {
      registerSetupUi(api);
    }
    if (process.env.OPENCLAW_NEXTCLOUD_CONNECTION_DIR && api.pluginConfig?.accountTalkEnabled !== false) {
      api.registerChannel({ plugin: createNextcloudAccountTalkChannel(api.runtime) });
    }
    for (const group of contract.native_tools) {
      api.registerTool(
        (toolContext) => ({
          name: group.name,
          label: group.name.replaceAll("_", " "),
          description: group.description,
          parameters: group.parameters,
          execute: async (toolCallId, rawParams, signal) =>
            await executeOperation(group.name, toolContext, toolCallId, rawParams, signal),
        }),
        // OpenClaw cannot infer a factory-backed tool's name during discovery.
        // Without this static registration metadata the plugin loads, but the
        // tool is absent from the agent's effective tool set.
        { name: group.name },
      );
    }

    api.on("before_prompt_build", async (event, ctx) => {
      const hookStarted = Date.now();
      const key = String(event.runId || runKey(ctx));
      const session = conversationKey(ctx, event);
      const prompt = String(event.prompt ?? "");
      if (runRetention.has(key) && (promptByRun.get(key) !== prompt || sessionByRun.get(key) !== session)) {
        runRetention.retire(key);
        return { prependContext: "turn-state-missing-or-expired; neue Anweisung benoetigt eine neue Run-ID" };
      }
      if (runRetention.has(key) && promptByRun.get(key) === prompt && sessionByRun.get(key) === session) {
        const retainedRoute = routeByRun.get(key);
        const prior = confirmationByRun.get(key);
        if (prior && !await currentPendingConfirmation(prior, ctx, session)) {
          confirmationByRun.delete(key);
          removePendingConfirmation(session, prior.id);
          return { prependContext: "missing-or-stale-bound-confirmation" };
        }
        return retainedRoute?.resolved
          ? { prependContext: buildRoutingContext(retainedRoute, obligationByRun.get(key), confirmationByRun.get(key)) }
          : undefined;
      }
      if (!runRetention.start(key)) return { prependContext: "turn-state-missing-or-expired" };
      requesterByRun.set(key, { sender: sdkSender(ctx), shared: sharedSession(ctx, session) });
      // Do not truncate into an apparently explicit authorization. An oversized
      // prompt can be retried by the user with one bounded, complete instruction.
      if (prompt.length > RUN_RETENTION.maxPromptBytes || Buffer.byteLength(prompt) > RUN_RETENTION.maxPromptBytes) {
        promptByRun.set(key, null);
        routeByRun.set(key, { resolved: false, routes: [] });
        sessionByRun.set(key, session);
        return { prependContext: "PERSONAL_ASSISTANT_TOOL_ROUTE_V1: prompt-retention-limit; keine Schreibaktion starten. Bitte eine einzelne kuerzere, vollstaendige Anweisung anfordern." };
      }
      let route = routePrompt(contract, event.prompt);
      let confirmation = null;
      if (isSimpleConfirmation(event.prompt)) {
        const pending = livePendingConfirmations(session);
        const selected = selectPendingConfirmation(event.prompt, pending);
        confirmation = await currentPendingConfirmation(selected.confirmation, ctx, session);
        if (!runRetention.has(key)) return { prependContext: "turn-state-missing-or-expired" };
        if (confirmation) {
          route = confirmation.route;
          confirmationByRun.set(key, confirmation);
        } else {
          confirmationByRun.delete(key);
        }
      } else {
        confirmationByRun.delete(key);
      }
      const timing = {
        started_at_ms: hookStarted,
        prompt_preparation_ms: 0,
        tool_loop_ms: 0,
        answer_finalization_ms: 0,
      };
      routeByRun.set(key, route);
      promptByRun.set(key, String(event.prompt ?? ""));
      sessionByRun.set(key, session);
      evidenceByRun.set(key, []);
      const obligation = confirmation
        ? buildActionObligation(contract, confirmation.prompt, route, key)
        : buildActionObligation(contract, event.prompt, route, key);
      if (obligation) {
        obligationByRun.set(key, obligation);
        metrics.action_obligations += 1;
      } else {
        obligationByRun.delete(key);
      }
      retryByRun.delete(key);
      invalidArgumentsByRun.delete(key);
      timing.prompt_preparation_ms = Math.max(0, Date.now() - hookStarted);
      metrics.prompt_preparation_total_ms += timing.prompt_preparation_ms;
      turnLatencyByRun.set(key, timing);
      if (!route.resolved) {
        metrics.unresolved += 1;
        return undefined;
      }
      metrics.routed += 1;
      return { prependContext: buildRoutingContext(route, obligation, confirmation) };
    });

    api.on("before_tool_call", async (event, ctx) => {
      const blocked = shouldBlockGenericTool(event.toolName, event.params);
      if (blocked) {
        metrics.generic_blocks += 1;
        return { block: true, blockReason: blocked };
      }
      const group = groupByName.get(event.toolName);
      if (!group) return undefined;
      const approvalRunId = String(event.runId || runKey(ctx, event.toolCallId));
      if (!runRetention.canAuthorize(approvalRunId)) return { block: true, blockReason: "turn-state-missing-or-expired" };
      const generation = runRetention.generation(approvalRunId);
      let operation;
      try {
        operation = operationFromCall(event.toolName, event.params);
      } catch (error) {
        return { block: true, blockReason: String(error?.message ?? error) };
      }
      try {
        validateArguments(operation.argument_schema, event.params?.arguments ?? {});
      } catch (error) {
        if (operation.mode !== "read") {
          return {
            block: true,
            blockReason: `invalid-arguments:${String(error?.message ?? error)}; keine Freigabe erzeugt`,
          };
        }
        const key = runKey(ctx, event.toolCallId);
        const previousInvalidCount = [
          ...(invalidArgumentsByRun.get(key)?.values() ?? []),
        ].reduce((total, value) => total + value, 0);
        if (previousInvalidCount > 0) {
          return {
            block: true,
            blockReason: `invalid-arguments:${String(error?.message ?? error)}; Korrekturversuch bereits verbraucht`,
          };
        }
      }
      if (operation.mode === "read") {
        const obligation = obligationByRun.get(approvalRunId);
        if (obligation && !obligation.terminal_state && obligation.tool_calls >= contract.action_completion.limits.max_tool_calls) {
          return { block: true, blockReason: "tool-call-limit" };
        }
        return { params: { ...withoutApprovalBinding(event.params), __approval_run_id: approvalRunId,
          __run_generation: generation } };
      }
      if (!runRetention.canAuthorize(approvalRunId)) return { block: true, blockReason: "turn-state-missing-or-expired" };
      const args = event.params?.arguments ?? {};
      let nativeAccess;
      let sender;
      if (requiresNativeAccess(operation.tool_id)) {
        const session = conversationKey(ctx, event);
        const hookSender = sdkSender(ctx);
        const requester = requesterByRun.get(approvalRunId);
        const original = requester?.sender;
        if ((session && session !== sessionByRun.get(approvalRunId)) || (hookSender && original && hookSender !== original)) {
          return { block: true, blockReason: "missing-or-stale-bound-approval" };
        }
        sender = hookSender ?? original ?? null;
        requesterByRun.set(approvalRunId, { sender, shared: requester?.shared || sharedSession(ctx, session) });
        try {
          nativeAccess = await readNativeAccess(spawnJson);
        } catch {
          return { block: true, blockReason: "policy-integrity-error" };
        }
        if (!runRetention.canAuthorize(approvalRunId) || runRetention.generation(approvalRunId) !== generation) {
          return { block: true, blockReason: "turn-state-missing-or-expired" };
        }
      }
      const accessDigest = nativeAccess ? stableDigest(nativeAccess) : null;
      const actionBlock = promptByRun.get(approvalRunId) === null ? "prompt-retention-limit"
        : actionWriteBlock(contract, obligationByRun.get(approvalRunId), operation, args);
      if (actionBlock) return { block: true, blockReason: actionBlock };
      const session = sessionByRun.get(approvalRunId) ?? conversationKey(ctx, event);
      const confirmation = confirmationByRun.get(approvalRunId) ?? null;
      const canConfirm = !nativeAccess || Boolean(sender) || !requesterByRun.get(approvalRunId)?.shared;
      if (isSimpleConfirmation(promptByRun.get(approvalRunId))) {
        const live = livePendingConfirmations(session);
        const valid = confirmation && confirmation.expires_at > Date.now()
          && live.some((record) => record.id === confirmation.id)
          && canConfirm && confirmation.accessDigest === accessDigest && confirmation.sender === sender
          && confirmation.operation === operation.tool_id
          && confirmation.arguments_digest === stableDigest(args);
        if (!valid) {
          confirmationByRun.delete(approvalRunId);
          const obligation = obligationByRun.get(approvalRunId);
          if (obligation && !obligation.terminal_state) obligationByRun.set(approvalRunId, {
            ...obligation, status: "terminal", terminal_state: "approval-required",
            last_error: "missing-or-stale-bound-confirmation",
          });
          return { block: true, blockReason: "missing-or-stale-bound-confirmation" };
        }
      }
      const decision = approvalDecision(operation, {
        prompt: promptByRun.get(approvalRunId) ?? "",
        route: routeByRun.get(approvalRunId),
        args,
        confirmed: confirmation,
      });
      const nonce = ledger.issue({
        operation: operation.tool_id,
        args,
        toolCallId: event.toolCallId,
        runId: approvalRunId,
        nativeAccess,
        sender,
      });
      const boundParams = {
        ...withoutApprovalBinding(event.params),
        __approval_nonce: nonce,
        __approval_run_id: approvalRunId,
        __run_generation: generation,
      };
      if (decision.authorized) {
        if (decision.source === "single-pending-confirmation" && confirmation) {
          if (nativeAccess) ledger.revoke(confirmation.id);
          removePendingConfirmation(session, confirmation.id);
          confirmationByRun.delete(approvalRunId);
          metrics.approvals_satisfied_by_simple_confirmation += 1;
        } else if (decision.source === "explicit-user-request") {
          metrics.approvals_satisfied_by_explicit_request += 1;
        }
        return { params: boundParams };
      }
      let pendingConfirmation = null;
      if (session && canConfirm && operation.approval_policy?.simple_confirmation_satisfies === true) {
        pendingConfirmation = {
          id: nativeAccess ? nonce : stableDigest({ session, operation: operation.tool_id, args, approvalRunId }).slice(0, 24),
          accessDigest,
          sender,
          operation: operation.tool_id,
          arguments: structuredClone(args),
          arguments_digest: stableDigest(args),
          run_id: approvalRunId,
          prompt: promptByRun.get(approvalRunId) ?? "",
          route: routeByRun.get(approvalRunId),
          expires_at: Date.now() + contract.limits.approval_ttl_seconds * 1000,
        };
        rememberPendingConfirmation(session, pendingConfirmation);
      }
      metrics.approvals_requested += 1;
      return {
        params: boundParams,
        requireApproval: {
          title: `Personal Assistant: ${operation.tool_id}`,
          description: pendingConfirmation
            ? `Einmalige Freigabe fuer exakt diese Aktion. Die Schaltflaeche genuegt; nach einem Timeout kann ein einfaches Ja nur diese eine unveraenderte Aktion bestaetigen.`
            : `Sensible Einmal-Freigabe ${operation.approval} fuer exakt diese Argumente.`,
          // OpenClaw's plugin approval protocol accepts info/warning/critical.
          // Whenever a native dialog is required, external writes stay at the
          // strongest supported level and local changes remain visibly below it.
          severity: approvalSeverity(operation),
          allowedDecisions: ["allow-once", "deny"],
          timeoutMs: contract.limits.approval_timeout_seconds * 1000,
          onResolution(decision) {
            if (decision !== "allow-once") ledger.revoke(nonce);
            if (pendingConfirmation && ["allow-once", "deny"].includes(decision)) {
              removePendingConfirmation(session, pendingConfirmation.id);
            }
          },
        },
      };
    });

    api.on("after_tool_call", async (event, ctx) => {
      if (!groupByName.has(event.toolName)) return undefined;
      const evidence = event.result?.details?.personalAssistantEvidence;
      if (!evidence) return undefined;
      if (event.result.details.personalAssistantEvidenceAdmissible === false) return undefined;
      const key = String(event.runId || runKey(ctx, event.toolCallId));
      if (String(evidence.turn_id ?? "") !== key) return undefined;
      if (!event.result?.details?.personalAssistantGeneration ||
          event.result.details.personalAssistantGeneration !== runRetention.generation(key)) return undefined;
      rememberEvidence(key, evidence);
      return undefined;
    });

    api.on("before_agent_finalize", async (event, ctx) => {
      const finalizationStarted = Date.now();
      const key = String(event.runId || runKey(ctx));
      if (!runRetention.has(key)) return undefined; // Delivery guard fails closed below.
      const route = routeByRun.get(key);
      if (!route?.resolved) return undefined;
      if (route.routes.length === 1 && route.routes[0].id === 'mail-dry-run' &&
          dryRunStatusAnswer(contract, evidenceByRun.get(key) ?? [])) return undefined;
      const evidenceVerdict = guardAnswer(contract, route, event.lastAssistantMessage, evidenceByRun.get(key) ?? []);
      const actionVerdict = guardActionCompletion(
        contract,
        obligationByRun.get(key),
        event.lastAssistantMessage,
      );
      const issues = [...new Set([...evidenceVerdict.issues, ...actionVerdict.issues])];
      const timing = turnLatencyByRun.get(key);
      if (timing) {
        const elapsed = Math.max(0, Date.now() - finalizationStarted);
        timing.answer_finalization_ms += elapsed;
        metrics.answer_finalization_total_ms += elapsed;
      }
      if (issues.length === 0 || retryByRun.has(key)) return undefined;
      retryByRun.add(key);
      metrics.guard_revisions += 1;
      const instruction = route.calendar_day_request === "today" && !obligationByRun.get(key)
        ? "Nutze nextcloud.calendar.today ohne Ressourceneinschraenkung, sofern noch kein aktueller Tagesbefund vorliegt. Bei vollstaendig belegtem leerem Tag antworte exakt: Heute hast du keine Termine. Bei Fehler oder Teilbefund nenne vorhandene Termine und die konkrete Einschraenkung; behaupte keinen leeren Tag."
        : "Fuehre jetzt ausschliesslich den naechsten registrierten Werkzeugschritt der ACTION_OBLIGATION_V1 aus oder berichte den bereits belegten typisierten Blocker. Kein Zukunftsversprechen und keine Meta-Ankuendigung.";
      return {
        action: "revise",
        reason: issues.join(","),
        retry: {
          instruction: `${instruction} Probleme: ${issues.join(", ")}`,
          idempotencyKey: `personal-assistant-action-evidence-${key}`,
          maxAttempts: 1,
        },
      };
    });

    api.on("reply_payload_sending", async (event, ctx) => {
      // Delivery correlation belongs to the event. Its message context may
      // intentionally omit the originating run ID.
      const key = String(event.runId || event.sessionKey || runKey(ctx));
      if (typeof event.payload?.text === "string" && (!runRetention.has(key) || promptByRun.get(key) === null)) {
        return { payload: { ...event.payload, text: "Der gebundene Turn-Kontext fehlt, ist abgelaufen oder zu gross. Ein Abschluss ist nicht belegt. Bitte die konkrete Aktion erneut vollstaendig anweisen; keine automatische Wiederholung." }, reason: "personal-assistant-turn-state-guard" };
      }
      const timing = turnLatencyByRun.get(key);
      if (timing) {
        const total = Math.max(0, Date.now() - timing.started_at_ms);
        metrics.turns_completed += 1;
        metrics.turn_total_ms += total;
        metrics.last_turn_latency = {
          prompt_preparation_ms: timing.prompt_preparation_ms,
          tool_loop_ms: timing.tool_loop_ms,
          answer_finalization_ms: timing.answer_finalization_ms,
          unattributed_model_and_orchestration_ms: Math.max(
            0,
            total
              - timing.prompt_preparation_ms
              - timing.tool_loop_ms
              - timing.answer_finalization_ms,
          ),
          turn_latency_ms: total,
        };
        turnLatencyByRun.delete(key);
      }
      const route = routeByRun.get(key);
      if (!route?.resolved || typeof event.payload?.text !== "string") return undefined;
      if (route.routes.length === 1 && route.routes[0].id === 'mail-dry-run') {
        const text = dryRunStatusAnswer(contract, evidenceByRun.get(key) ?? []);
        if (text && text !== event.payload.text) {
          return { payload: { ...event.payload, text }, reason: 'personal-assistant-dry-run-status' };
        }
      }
      const evidenceVerdict = guardAnswer(contract, route, event.payload.text, evidenceByRun.get(key) ?? []);
      let obligation = obligationByRun.get(key);
      const actionVerdict = guardActionCompletion(contract, obligation, event.payload.text);
      const issues = [...new Set([...evidenceVerdict.issues, ...actionVerdict.issues])];
      if (issues.length === 0) return undefined;
      if (obligation && !obligation.terminal_state) {
        obligation = {
          ...obligation,
          status: "terminal",
          terminal_state: "blocked",
          last_error: "guard-revision-exhausted",
        };
        obligationByRun.set(key, obligation);
        metrics.action_blocked += 1;
        if (obligation.completed_targets > 0) metrics.action_partial += 1;
      }
      metrics.guard_replacements += 1;
      return {
        payload: {
          ...event.payload,
          text: obligation
            ? safeActionReplacement(obligation, issues)
            : safeReplacement(issues),
        },
        reason: obligation
          ? "personal-assistant-action-completion-guard"
          : "personal-assistant-evidence-guard",
      };
    });
  },
});

export const testing = {
  contract,
  operationById,
  groupByName,
  metrics,
  obligationByRun,
  runRetention,
  tokenizeCommand,
};
