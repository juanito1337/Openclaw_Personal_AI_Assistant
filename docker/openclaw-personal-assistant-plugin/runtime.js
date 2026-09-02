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
  if (tokens.some((item) => /<[^>]+>|\{workspace_root\}|\{calendar_subject_prefix\}/u.test(item))) {
    throw new Error("unresolved-command-template");
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
    size() {
      return records.size;
    },
  };
}

export async function loadContract(path = new URL("./generated-tools.json", import.meta.url)) {
  return JSON.parse(await readFile(path, "utf8"));
}

export const internal = { classifyClaims, classifyError, isComplete, postconditionVerified };
