import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";

const ASSISTANT = "/opt/openclaw-agent/scripts/assistant.sh";
const WORKDIR = "/opt/openclaw-agent";
const FORBIDDEN_TOKENS = new Set(["&&", "||", ";", ">", ">>", "<", "2>", "2>>"]);
const SECRET_PATH = /(?:^|[\s"'])(?:~\/|\/home\/[^/]+\/)?\.config\/personal-assistant\/(?:secrets\.env)?|\/srv\/openclaw\/secrets/i;
const RAW_DOMAIN_EXEC = /(?:assistant\.sh|(?:^|\s)himalaya(?:\s|$)|\b(?:mail|portfolio|nextcloud|tasks|calendar|contacts|invoices)\.[a-z0-9_.-]+\b)/i;

export function stableDigest(value) {
  const canonical = (item) => {
    if (Array.isArray(item)) return item.map(canonical);
    if (item && typeof item === "object") {
      return Object.fromEntries(
        Object.keys(item)
          .sort()
          .map((key) => [key, canonical(item[key])]),
      );
    }
    return item;
  };
  return createHash("sha256").update(JSON.stringify(canonical(value))).digest("hex");
}

export function approvalSeverity(operation) {
  return operation?.writes_external_data ? "critical" : "warning";
}

export function tokenizeCommand(command) {
  if (typeof command !== "string" || command.length === 0 || command.includes("\0")) {
    throw new Error("invalid-command-template");
  }
  const tokens = [];
  let token = "";
  let quote = "";
  let escaped = false;
  for (const character of command) {
    if (escaped) {
      token += character;
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = "";
      else token += character;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (/\s/u.test(character)) {
      if (token) {
        tokens.push(token);
        token = "";
      }
      continue;
    }
    token += character;
  }
  if (escaped || quote) throw new Error("invalid-command-quoting");
  if (token) tokens.push(token);
  return tokens;
}

function replaceOnce(value, placeholder, replacement) {
  const marker = `<${placeholder}>`;
  const offset = value.indexOf(marker);
  if (offset < 0) throw new Error(`missing-placeholder:${placeholder}`);
  return `${value.slice(0, offset)}${replacement}${value.slice(offset + marker.length)}`;
}

export function validateArguments(schema, input, { allowApprovalNonce = false } = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("arguments-must-be-object");
  }
  const properties = schema?.properties ?? {};
  const allowed = new Set(Object.keys(properties));
  if (allowApprovalNonce) allowed.add("__approval_nonce");
  for (const key of Object.keys(input)) {
    if (!allowed.has(key)) throw new Error(`unknown-argument:${key}`);
  }
  for (const key of schema?.required ?? []) {
    if (!(key in input)) throw new Error(`missing-argument:${key}`);
  }
  for (const [key, definition] of Object.entries(properties)) {
    if (!(key in input)) continue;
    const value = input[key];
    if (definition.type === "integer") {
      if (!Number.isInteger(value)) throw new Error(`invalid-integer:${key}`);
      if (definition.minimum !== undefined && value < definition.minimum) {
        throw new Error(`below-minimum:${key}`);
      }
      if (definition.maximum !== undefined && value > definition.maximum) {
        throw new Error(`above-maximum:${key}`);
      }
      continue;
    }
    if (typeof value !== "string") throw new Error(`invalid-string:${key}`);
    if (definition.minLength !== undefined && value.length < definition.minLength) {
      throw new Error(`too-short:${key}`);
    }
    if (definition.maxLength !== undefined && value.length > definition.maxLength) {
      throw new Error(`too-long:${key}`);
    }
    if (definition.enum && !definition.enum.includes(value)) throw new Error(`invalid-enum:${key}`);
    if (definition.pattern && !new RegExp(definition.pattern, "u").test(value)) {
      throw new Error(`invalid-pattern:${key}`);
    }
    if (value.includes("\0")) throw new Error(`nul-byte:${key}`);
  }
  return input;
}

export function compileInvocation(operation, input, liveCommand = operation.command) {
  validateArguments(operation.argument_schema, input, { allowApprovalNonce: true });
  let tokens = tokenizeCommand(liveCommand);
  let stdin = null;
  const pipeAt = tokens.indexOf("|");
  if (pipeAt >= 0) {
    if (tokens.filter((item) => item === "|").length !== 1) throw new Error("multiple-pipelines");
    const prefix = tokens.slice(0, pipeAt);
    if (prefix.length !== 3 || prefix[0] !== "printf" || prefix[1] !== "%s") {
      throw new Error("unsupported-pipeline");
    }
    stdin = String(input[operation.stdin_parameter] ?? "");
    tokens = tokens.slice(pipeAt + 1);
  }
  const templateMarkers = tokens.flatMap((token) =>
    [...token.matchAll(/<([^>]+)>/gu)].map((match) => match[1]),
  );
  const registeredMarkers = (operation.parameter_bindings ?? [])
    .filter((binding) => binding.parameter !== operation.stdin_parameter)
    .map((binding) => binding.placeholder);
  if (
    templateMarkers.length !== registeredMarkers.length
    || templateMarkers.some((marker, index) => marker !== registeredMarkers[index])
    || tokens.some((item) => /\{workspace_root\}|\{calendar_subject_prefix\}/u.test(item))
  ) {
    throw new Error("unresolved-command-template");
  }
  if (tokens[0] !== "./scripts/assistant.sh" && tokens[0] !== ASSISTANT) {
    throw new Error("unregistered-executable");
  }
  if (tokens.some((item) => FORBIDDEN_TOKENS.has(item) || item.includes("`") || item.includes("$"))) {
    throw new Error("shell-syntax-rejected");
  }
  let cursor = 0;
  for (const binding of operation.parameter_bindings ?? []) {
    if (binding.parameter === operation.stdin_parameter) continue;
    if (binding.required === false && !(binding.parameter in input)) {
      const marker = `<${binding.placeholder}>`;
      const index = tokens.findIndex((item, position) => position >= cursor && item.includes(marker));
      if (index <= 0 || !tokens[index - 1].startsWith("--")) {
        throw new Error(`optional-parameter-shape:${binding.parameter}`);
      }
      tokens.splice(index - 1, 2);
      cursor = Math.max(0, index - 1);
      continue;
    }
    let replaced = false;
    for (; cursor < tokens.length; cursor += 1) {
      if (!tokens[cursor].includes(`<${binding.placeholder}>`)) continue;
      tokens[cursor] = replaceOnce(
        tokens[cursor],
        binding.placeholder,
        String(input[binding.parameter]),
      );
      replaced = true;
      cursor += 1;
      break;
    }
    if (!replaced) throw new Error(`unbound-parameter:${binding.parameter}`);
  }
  return { executable: ASSISTANT, argv: tokens.slice(1), stdin };
}

function bounded(value, maximum) {
  const text = typeof value === "string" ? value : String(value ?? "");
  return Buffer.byteLength(text) <= maximum ? text : `${text.slice(0, maximum)}\n[truncated]`;
}

export async function spawnJson(invocation, limits, options = {}) {
  const runner = options.runner ?? spawn;
  const timeoutMs = Math.max(1, Number(limits.tool_timeout_seconds ?? 120)) * 1000;
  return await new Promise((resolve) => {
    const child = runner(invocation.executable, invocation.argv, {
      cwd: options.cwd ?? WORKDIR,
      env: options.env ?? process.env,
      stdio: ["pipe", "pipe", "pipe"],
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timer;
    let killTimer;
    let timedOut = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(killTimer);
      resolve(result);
    };
    child.stdout?.on("data", (chunk) => {
      stdout = bounded(stdout + String(chunk), limits.max_output_bytes);
    });
    child.stderr?.on("data", (chunk) => {
      stderr = bounded(stderr + String(chunk), limits.max_error_bytes);
    });
    child.on("error", (error) => finish({ returncode: 127, stdout, stderr, error: error.message }));
    child.on("close", (code, signal) =>
      finish({
        returncode: timedOut ? 124 : Number(code ?? 1),
        stdout,
        stderr,
        signal: signal ?? null,
        ...(timedOut ? { error: "tool-timeout" } : {}),
      }),
    );
    timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      killTimer = setTimeout(() => child.kill("SIGKILL"), 2000);
    }, timeoutMs);
    if (invocation.stdin !== null) child.stdin?.end(invocation.stdin);
    else child.stdin?.end();
  });
}

function parseJsonOutput(output) {
  try {
    return JSON.parse(output);
  } catch {
    return null;
  }
}

function classifyError(result, payload) {
  const detail = `${result.error ?? ""}\n${result.stderr ?? ""}`.toLowerCase();
  if (detail.includes("invalid-arguments")) return "invalid-arguments";
  if (detail.includes("missing-or-stale-bound-approval")) return "approval-required";
  if (result.returncode === 124 || detail.includes("timeout")) return "timeout";
  if (detail.includes("permission") || detail.includes("freigabe")) return "permission-denied";
  if (detail.includes("configuration") || detail.includes("umgebungsvariable")) {
    return "configuration-error";
  }
  if (payload && payload.complete === false) return "incomplete-result";
  return result.returncode === 0 ? null : "operation-failed";
}

function resultRows(payload) {
  if (Array.isArray(payload)) return payload;
  for (const key of ["results", "records", "messages", "items", "files", "events", "tasks", "contacts"]) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return null;
}

function isComplete(operation, payload, ok) {
  if (!ok) return false;
  if (typeof payload?.complete === "boolean") {
    return payload.complete && !(payload.folder_errors?.length > 0) && payload.results_may_be_truncated !== true;
  }
  if (["mail.search", "mail.search.local", "assistant.search"].includes(operation.tool_id)) {
    return false;
  }
  return true;
}

function postconditionVerified(operation, payload, ok) {
  if (operation.mode === "read" || !ok || !payload || typeof payload !== "object") return false;
  if (payload.delivery_uncertain === true || payload.conflict === true || payload.complete === false) return false;
  if (Object.hasOwn(payload, "postcondition_verified")) {
    return payload.postcondition_verified === true;
  }
  return payload.ok === true || payload.postcondition_verified === true || payload.verified === true;
}

export function makeEvidence(operation, result, payload, turnId, toolCallId) {
  const ok = result.returncode === 0 && payload !== null && payload?.ok !== false;
  const complete = isComplete(operation, payload, ok);
  const rows = resultRows(payload);
  const allowedClaims = ["tool-status", ok ? "positive-evidence" : "tool-error"];
  if (operation.tool_id === "assistant.version" && ok) allowedClaims.push("product-version");
  if (
    ["mail.search", "mail.search.local", "assistant.search"].includes(operation.tool_id) &&
    complete &&
    rows !== null &&
    rows.length === 0
  ) {
    allowedClaims.push("negative");
  }
  const postcondition = postconditionVerified(operation, payload, ok);
  if (postcondition) allowedClaims.push("write-success");
  return {
    tool_id: operation.tool_id,
    tool_version: 1,
    run_id: randomUUID(),
    turn_id: String(turnId || toolCallId || "unknown-turn"),
    domain: operation.domain,
    mode: operation.mode,
    ok,
    complete,
    freshness: payload?.freshness ?? payload?.observed_at ?? payload?.checked_at ?? null,
    coverage: payload?.coverage ?? null,
    results_may_be_truncated: payload?.results_may_be_truncated === true,
    error: classifyError(result, payload),
    approval: operation.approval,
    postcondition_verified: postcondition,
    allowed_claims: allowedClaims,
    next_actions: [],
  };
}

export function routePrompt(contract, prompt) {
  const normalized = String(prompt ?? "").normalize("NFKC").toLocaleLowerCase("de-DE");
  const routes = [];
  for (const route of contract.routes ?? []) {
    if ((route.patterns ?? []).some((pattern) => new RegExp(pattern, "iu").test(normalized))) {
      const { patterns: _patterns, ...publicRoute } = route;
      routes.push(publicRoute);
    }
    if (routes.length >= Number(contract.limits?.max_routed_domains ?? 3)) break;
  }
  return {
    schema_version: contract.schema_version,
    resolved: routes.length > 0,
    routes,
    read_only_prefetch_only: true,
    external_write_authorized: false,
  };
}

function normalizedText(value) {
  return String(value ?? "").normalize("NFKC").toLocaleLowerCase("de-DE").trim();
}

const ACTION_EXECUTE_PATTERNS = [
  /\b(?:trag|trage|tragt|tragen)\b.{0,80}\b(?:ein|kalender)\b/iu,
  /\b(?:eintragen|anlegen|erstellen|verschieben|aktualisieren|abschliessen|abschließen|send(?:e|en|et)|verschick(?:e|en|t)|beantwort(?:e|en|et))\b/iu,
  /\b(?:fuehre|führe)\b.{0,40}\b(?:aus|durch)\b/iu,
  /\b(?:crea|crear|anade|añade|agrega|envia|envía|actualiza|mueve|completa)\b/iu,
  /\b(?:create|add|send|move|update|complete)\b/iu,
];
const ACTION_PREVIEW_PATTERNS = [
  /\b(?:vorschau|preview|dry[- ]?run|simulier)\w*\b/iu,
  /\b(?:zeige|zeig)\b.{0,60}\b(?:zuerst|vorher|vorschlag)\b/iu,
  /\b(?:muestra|vista previa|simula)\b/iu,
];
const ACTION_EXPLAIN_PATTERNS = [
  /\b(?:erklaer|erklär|beschreib|wie (?:geht|funktioniert|wuerdest|würdest))\w*\b/iu,
  /\b(?:explain|how would|como funciona|cómo funciona)\b/iu,
];
const ACTION_PROMISE_PATTERNS = [
  /\bich (?:werde|mache|fuehre|führe|trage|lege)\b/iu,
  /\b(?:einen moment|gleich|jetzt werde ich|ich muss .*tool)\b/iu,
  /\b(?:i will|i am going to|give me a moment|voy a|ahora voy a)\b/iu,
];
const ACTION_WRITE_PROMISE_PATTERNS = [
  /\bich werde\b.{0,200}\b(?:abschicken|absenden|senden|versenden|verschicken|eintragen|anlegen|erstellen|verschieben|aktualisieren|abschliessen|abschließen)\b/iu,
  /\bich (?:sende|versende|verschicke|schicke|trage|lege|erstelle|verschiebe|aktualisiere|beantworte|schliesse|schließe)\b/iu,
  /\bich (?:fuehre|führe)\b.{0,100}\b(?:versand|aktion|vorgang|aenderung|änderung)\b.{0,50}\b(?:aus|durch)\b/iu,
  /\bi (?:will|am going to)\b.{0,200}\b(?:send|create|move|update|complete)\b/iu,
  /\b(?:voy a|ahora voy a)\b.{0,200}\b(?:enviar|crear|mover|actualizar|completar)\b/iu,
];
const ACTION_UNBOUND_RETRY_QUESTION_PATTERNS = [
  /\b(?:soll|sollte) ich\b.{0,180}\b(?:noch einmal|nochmal|erneut|wieder|versuch)/iu,
  /\b(?:moechtest|möchtest|willst) du\b.{0,180}\b(?:noch einmal|nochmal|erneut|wieder|versuch)/iu,
  /\bshould i\b.{0,180}\b(?:try again|retry)/iu,
  /\b(?:quieres que|debo)\b.{0,180}\b(?:intente de nuevo|reintente)/iu,
];

export function classifyActionIntent(contract, prompt, route) {
  if (!route?.resolved) return "ambiguous";
  const text = normalizedText(prompt);
  if (ACTION_PREVIEW_PATTERNS.some((pattern) => pattern.test(text))) return "preview";
  if (ACTION_EXPLAIN_PATTERNS.some((pattern) => pattern.test(text))) return "explain";
  if (ACTION_EXECUTE_PATTERNS.some((pattern) => pattern.test(text))) return "execute";
  return "ambiguous";
}

function actionTargetCount(text, workflowKind) {
  if (
    workflowKind === "mail-to-calendar" &&
    (/\bhin(?:-| )?und(?:-| )?rueckflug\b/iu.test(text) ||
      (/\bhinflug\b/iu.test(text) && /\br(?:ue|ü)ckflug\b/iu.test(text)) ||
      /\b(?:fluege|flüge|flights|vuelos)\b/iu.test(text))
  ) return 2;
  return 1;
}

export function buildActionObligation(contract, prompt, route, turnId) {
  if (classifyActionIntent(contract, prompt, route) !== "execute") return null;
  const domains = [...new Set((route?.routes ?? []).map((item) => String(item.domain ?? "")))].sort();
  const text = normalizedText(prompt);
  const mentionsMail = domains.includes("mail") || /\b(?:mail|e-?mail|nachricht|correo)\b/iu.test(text);
  const mentionsCalendar = domains.includes("calendar") || /\b(?:termin|kalender|flug|calendar|evento|vuelo)\b/iu.test(text);
  const workflowKind = mentionsMail && mentionsCalendar ? "mail-to-calendar" : "single-action";
  const steps = workflowKind === "mail-to-calendar"
    ? [...contract.action_completion.workflow_steps]
    : ["resolve-target", "execute-one", "verify-one", "finish"];
  const targetCount = actionTargetCount(text, workflowKind);
  const identity = { turn_id: String(turnId), intent: "execute", domains, workflow_kind: workflowKind, target_count: targetCount };
  return {
    schema_version: contract.action_completion.schema_version,
    obligation_id: stableDigest(identity).slice(0, 24),
    turn_id: String(turnId),
    intent: "execute",
    domains,
    workflow_kind: workflowKind,
    effect: workflowKind === "mail-to-calendar" ? "external-create" : "external-write",
    target_count: targetCount,
    completed_targets: 0,
    steps,
    current_step: steps[0],
    step_index: 0,
    tool_calls: 0,
    status: "open",
    terminal_state: null,
    last_error: null,
    write_operations: [],
    write_digests: [],
    required_slots: workflowKind === "mail-to-calendar"
      ? ["folder", "message_id", "expected_subject", "preview_digest", "candidate_id"]
      : [],
    postconditions: workflowKind === "mail-to-calendar"
      ? ["remote-uid-read-back", "remote-etag-present", "approved-fields-match"]
      : ["registered-operation-postcondition"],
    postconditions_verified: 0,
  };
}

export function advanceActionObligation(contract, obligation, operation, evidence, payload, argumentDigest = "") {
  if (!obligation || contract.action_completion.terminal_states.includes(obligation.terminal_state)) return obligation;
  const result = structuredClone(obligation);
  if (String(evidence?.turn_id ?? "") !== String(result.turn_id ?? "")) return result;
  if (result.tool_calls >= contract.action_completion.limits.max_tool_calls) {
    return { ...result, status: "terminal", terminal_state: "blocked", last_error: "tool-call-limit" };
  }
  result.tool_calls += 1;
  if (!evidence?.ok) {
    if (evidence?.error === "approval-required") {
      return {
        ...result,
        status: "terminal",
        terminal_state: "approval-required",
        last_error: "missing-or-stale-bound-approval",
      };
    }
    return { ...result, status: "terminal", terminal_state: "blocked", last_error: evidence?.error ?? "operation-failed" };
  }
  if (operation.mode !== "read") {
    if (
      (result.domains.length > 0 && !result.domains.includes(operation.domain)) ||
      (result.workflow_kind === "mail-to-calendar" && operation.tool_id !== "nextcloud.calendar.from-mail-create")
    ) {
      return { ...result, status: "terminal", terminal_state: "blocked", last_error: "unexpected-write-operation" };
    }
    if (["mail.reply-draft", "mail.compose-draft"].includes(operation.tool_id)) {
      return {
        ...result,
        status: "terminal",
        terminal_state: "approval-required",
        current_step: "execute-one",
        step_index: result.steps.indexOf("execute-one"),
        last_error: "presented-draft-send-approval-required",
      };
    }
    if (result.current_step === "resolve-target" && result.workflow_kind === "single-action") {
      result.current_step = "execute-one";
      result.step_index = result.steps.indexOf("execute-one");
    }
    if (result.current_step !== "execute-one") {
      return { ...result, status: "terminal", terminal_state: "blocked", last_error: "unexpected-workflow-step" };
    }
    result.write_operations.push(operation.tool_id);
    result.write_digests.push(argumentDigest);
    if (evidence.postcondition_verified !== true) {
      return { ...result, status: "terminal", terminal_state: "blocked", last_error: "write-postcondition-unverified" };
    }
    result.completed_targets += 1;
    result.postconditions_verified += 1;
    if (result.completed_targets >= result.target_count) {
      return { ...result, status: "terminal", terminal_state: "completed", current_step: "finish", step_index: result.steps.length - 1 };
    }
    result.current_step = "execute-one";
    result.step_index = result.steps.indexOf("execute-one");
    return result;
  }
  const mailCalendarProgress = {
    "select-source": { operations: ["mail.search", "mail.recent"], next: "read-source" },
    "read-source": { operations: ["mail.read"], next: "build-preview" },
    "build-preview": { operations: ["nextcloud.calendar.from-mail-preview"], next: "execute-one" },
  };
  const singleOperations = new Set([
    "nextcloud.calendar.status", "nextcloud.calendar.search", "nextcloud.tasks.status",
    "nextcloud.tasks.list", "nextcloud.contacts.status", "nextcloud.contacts.search",
    "mail.search", "mail.read",
  ]);
  let allowed = [];
  let nextStep = "";
  if (result.workflow_kind === "mail-to-calendar") {
    const transition = mailCalendarProgress[result.current_step];
    allowed = transition?.operations ?? [];
    nextStep = transition?.next ?? "";
  } else if (result.current_step === "resolve-target") {
    allowed = [...singleOperations];
    nextStep = "execute-one";
  }
  if (!allowed.includes(operation.tool_id)) {
    return { ...result, status: "terminal", terminal_state: "blocked", last_error: "unexpected-workflow-step" };
  }
  if (operation.tool_id === "nextcloud.calendar.from-mail-preview") {
    if (payload?.decision === "information-required") {
      return { ...result, status: "terminal", terminal_state: "information-required", last_error: "source-fields-incomplete" };
    }
    if (Number.isInteger(payload?.candidate_count)) {
      if (payload.candidate_count < result.target_count) {
        return { ...result, status: "terminal", terminal_state: "information-required", last_error: "requested-targets-missing" };
      }
      if (payload.candidate_count > contract.action_completion.limits.max_targets) {
        return { ...result, status: "terminal", terminal_state: "blocked", last_error: "target-limit" };
      }
      result.target_count = payload.candidate_count;
    }
  }
  if (nextStep) {
    result.current_step = nextStep;
    result.step_index = result.steps.indexOf(nextStep);
  }
  return result;
}

export function guardActionCompletion(contract, obligation, answer) {
  const text = normalizedText(answer);
  if (!obligation) {
    const issues = ACTION_WRITE_PROMISE_PATTERNS.some((pattern) => pattern.test(text))
      ? ["write-promise-without-action-obligation"]
      : [];
    return { ok: issues.length === 0, issues, terminal_state: null, fail_closed: true };
  }
  if (contract.action_completion.terminal_states.includes(obligation.terminal_state)) {
    const issues = [];
    if (
      ["approval-required", "information-required", "blocked"].includes(obligation.terminal_state)
      && ACTION_WRITE_PROMISE_PATTERNS.some((pattern) => pattern.test(text))
    ) {
      issues.push("write-promise-without-action-obligation");
    }
    if (
      ["approval-required", "blocked"].includes(obligation.terminal_state) &&
      ACTION_UNBOUND_RETRY_QUESTION_PATTERNS.some((pattern) => pattern.test(text))
    ) {
      issues.push("unbound-yes-no-retry-question");
    }
    if (obligation.terminal_state === "approval-required") {
      if (/(?:^|\s)\/approve(?:\s|$)/iu.test(text)) issues.push("stale-or-unbound-approval-command");
      if (/\b(?:mail\s+)?(?:gesendet|verschickt|sent|enviad[oa])\b/iu.test(text)) {
        issues.push("send-claim-before-completion");
      }
    }
    return {
      ok: issues.length === 0,
      issues,
      terminal_state: obligation.terminal_state,
      fail_closed: true,
    };
  }
  const issues = ["open-action-obligation"];
  if (!text) issues.push("empty-action-response");
  if (ACTION_PROMISE_PATTERNS.some((pattern) => pattern.test(text))) issues.push("future-promise-without-action");
  return { ok: false, issues, terminal_state: null, fail_closed: true };
}

function classifyClaims(contract, answer) {
  const normalized = String(answer ?? "").normalize("NFKC").toLocaleLowerCase("de-DE");
  return Object.entries(contract.claim_patterns ?? {})
    .filter(([, patterns]) => patterns.some((pattern) => new RegExp(pattern, "iu").test(normalized)))
    .map(([claim]) => claim);
}

export function guardAnswer(contract, route, answer, evidence) {
  const issues = [];
  for (const item of route?.routes ?? []) {
    const matching = evidence.filter(
      (row) => row.domain === item.domain && (item.operations ?? []).includes(row.tool_id),
    );
    if (matching.length === 0) issues.push(`missing-current-evidence:${item.domain}`);
  }
  const claims = classifyClaims(contract, answer);
  if (claims.includes("negative") && !evidence.some((row) => row.allowed_claims?.includes("negative"))) {
    issues.push("negative-claim-not-authorized");
  }
  if (
    claims.includes("product-version") &&
    !evidence.some((row) => row.ok && row.tool_id === "assistant.version" && row.allowed_claims?.includes("product-version"))
  ) {
    issues.push("version-claim-not-authorized");
  }
  if (
    claims.includes("write-success") &&
    (route?.routes ?? []).some((item) => item.claim_classes?.includes("write-success")) &&
    !evidence.some((row) => row.ok && row.allowed_claims?.includes("write-success"))
  ) {
    issues.push("write-success-not-authorized");
  }
  return { ok: issues.length === 0, claims, issues, fail_closed: true };
}

export function shouldBlockGenericTool(toolName, params) {
  const serialized = JSON.stringify(params ?? {});
  if (["read", "write", "edit", "apply_patch"].includes(toolName) && SECRET_PATH.test(serialized)) {
    return "Direkter Zugriff auf Personal-Assistant-Secrets ist gesperrt";
  }
  if (toolName === "exec" && (SECRET_PATH.test(serialized) || RAW_DOMAIN_EXEC.test(serialized))) {
    return "Fachanfragen muessen das registrierte strukturierte Personal-Assistant-Tool verwenden";
  }
  return "";
}

export function createApprovalLedger(ttlSeconds = 180) {
  const records = new Map();
  return {
    issue({ operation, args, toolCallId, runId }) {
      const nonce = randomUUID();
      records.set(nonce, {
        digest: stableDigest({ operation, args, toolCallId, runId }),
        expiresAt: Date.now() + ttlSeconds * 1000,
      });
      return nonce;
    },
    consume({ nonce, operation, args, toolCallId, runId }) {
      const record = records.get(nonce);
      records.delete(nonce);
      if (!record || record.expiresAt < Date.now()) return false;
      return record.digest === stableDigest({ operation, args, toolCallId, runId });
    },
    revoke(nonce) {
      records.delete(nonce);
    },
    size() {
      return records.size;
    },
  };
}

export async function loadContract(path = new URL("./generated-tools.json", import.meta.url)) {
  return JSON.parse(await readFile(path, "utf8"));
}

export const internal = { classifyClaims, classifyError, isComplete, postconditionVerified };
