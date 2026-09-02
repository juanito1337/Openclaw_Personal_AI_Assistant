import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { readFileSync } from "node:fs";
import {
  approvalSeverity,
  compileInvocation,
  createApprovalLedger,
  guardAnswer,
  makeEvidence,
  routePrompt,
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
const retryByRun = new Set();
const invalidArgumentsByRun = new Map();
const liveToolsCache = { expiresAt: 0, value: null };
const ledger = createApprovalLedger(contract.limits.approval_ttl_seconds);
const metrics = {
  routed: 0,
  unresolved: 0,
  native_calls: 0,
  native_failures: 0,
  invalid_arguments: 0,
  repeated_invalid_arguments: 0,
  generic_blocks: 0,
  approvals_requested: 0,
  guard_revisions: 0,
  guard_replacements: 0,
};

function runKey(ctx, fallback = "unknown-run") {
  return String(ctx?.runId || ctx?.sessionId || ctx?.sessionKey || fallback);
}

function jsonToolResult(payload, details = {}) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
    details,
  };
}

async function readLiveTools() {
  if (liveToolsCache.value && liveToolsCache.expiresAt > Date.now()) return liveToolsCache.value;
  const result = await spawnJson(
    { executable: "/opt/openclaw-agent/scripts/assistant.sh", argv: ["tools", "list"], stdin: null },
    { ...contract.limits, tool_timeout_seconds: 60 },
  );
  if (result.returncode !== 0) throw new Error("live-tool-catalog-unavailable");
  const payload = JSON.parse(result.stdout);
  const tools = Array.isArray(payload) ? payload : payload.tools;
  if (!Array.isArray(tools)) throw new Error("live-tool-catalog-invalid");
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

function withoutNonce(params) {
  const { __approval_nonce: _nonce, ...copy } = params ?? {};
  return copy;
}

async function executeOperation(toolName, toolContext, toolCallId, rawParams) {
  const operation = operationFromCall(toolName, rawParams);
  const args = rawParams?.arguments ?? {};
  const currentRun = runKey(toolContext, toolCallId);
  try {
    validateArguments(operation.argument_schema, args);
  } catch (error) {
    const issue = String(error?.message ?? error);
    const signature = `${operation.tool_id}:${stableDigest(args)}:${issue}`;
    const seen = invalidArgumentsByRun.get(currentRun) ?? new Map();
    const previousInvalidCount = [...seen.values()].reduce((total, value) => total + value, 0);
    const count = (seen.get(signature) ?? 0) + 1;
    seen.set(signature, count);
    invalidArgumentsByRun.set(currentRun, seen);
    const retryAllowed = previousInvalidCount === 0;
    const result = {
      returncode: 2,
      stdout: "",
      stderr: "",
      error: `invalid-arguments:${issue}`,
    };
    const evidence = makeEvidence(operation, result, null, currentRun, toolCallId);
    const rows = evidenceByRun.get(currentRun) ?? [];
    rows.push(evidence);
    evidenceByRun.set(currentRun, rows.slice(-32));
    metrics.native_calls += 1;
    metrics.native_failures += 1;
    metrics.invalid_arguments += 1;
    if (!retryAllowed) metrics.repeated_invalid_arguments += 1;
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
  if (operation.mode !== "read") {
    const accepted = ledger.consume({
      nonce: rawParams?.__approval_nonce,
      operation: operation.tool_id,
      args,
      toolCallId,
      runId: currentRun,
    });
    if (!accepted) throw new Error("missing-or-stale-bound-approval");
  }
  let liveCommand = operation.command;
  if (operation.availability !== "always") {
    const liveTools = await readLiveTools();
    const live = liveTools.find((item) => item.id === operation.tool_id);
    if (!live) throw new Error("operation-disabled-in-live-capabilities");
    liveCommand = live.command;
  }
  const invocation = compileInvocation(operation, args, liveCommand);
  const result = await spawnJson(invocation, contract.limits);
  let payload = null;
  try {
    payload = JSON.parse(result.stdout);
  } catch {
    payload = null;
  }
  const evidence = makeEvidence(operation, result, payload, currentRun, toolCallId);
  const rows = evidenceByRun.get(currentRun) ?? [];
  rows.push(evidence);
  evidenceByRun.set(currentRun, rows.slice(-32));
  metrics.native_calls += 1;
  if (!evidence.ok) metrics.native_failures += 1;
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
  if (operation.tool_id === "assistant.agent-tools.status" && payload && typeof payload === "object") {
    response.result = { ...payload, runtime_metrics: { ...metrics }, pending_approvals: ledger.size() };
  }
  return jsonToolResult(response, { personalAssistantEvidence: evidence });
}

function buildRoutingContext(route) {
  const lines = [
    "PERSONAL_ASSISTANT_TOOL_ROUTE_V1",
    "Aktueller Zustand darf nur aus einem strukturierten Personal-Assistant-Tool dieses Laufs beantwortet werden.",
    "Keine gepunktete Katalog-ID und keinen assistant.sh-/Himalaya-Befehl ueber exec ausfuehren.",
    "Jeden nativen Aufruf mit operation und allen Pflichtfeldern unter arguments ausfuehren; arguments niemals leer lassen, wenn die Signatur Pflichtfelder nennt.",
    "Nach invalid-arguments genau einmal mit geaenderten vollstaendigen Argumenten korrigieren. Bei retry_allowed=false sofort stoppen und den Fehler berichten.",
    "Mail: letzte Mails mit mail.list und arguments.folder (normalerweise INBOX); Suche mit mail.search und arguments.query; mail.read erst nach einem Treffer mit folder, message_id und expected_subject.",
    "Bei Schreibwuenschen zuerst nur read-only identifizieren/vorschauen; das Schreibtool verlangt seine eigene Einzel-Freigabe.",
    `Route: ${JSON.stringify(route)}`,
  ];
  return lines.join("\n");
}

function safeReplacement(issues) {
  return `Ich kann diese Zustandsaussage nicht belegen. Der Personal-Assistant-Antwortschutz hat die Ausgabe gestoppt (${issues.join(", ")}). Bitte den registrierten Status- oder Suchpfad erneut ausfuehren; es wurde keine Schreibaktion ausgeloest.`;
}

export default definePluginEntry({
  id: contract.plugin_id,
  name: "Personal Assistant Tools",
  description: "Structured Personal Assistant operations with deterministic routing and evidence guards.",
  register(api) {
    for (const group of contract.native_tools) {
      api.registerTool(
        (toolContext) => ({
          name: group.name,
          label: group.name.replaceAll("_", " "),
          description: group.description,
          parameters: group.parameters,
          execute: async (toolCallId, rawParams) =>
            await executeOperation(group.name, toolContext, toolCallId, rawParams),
        }),
        // OpenClaw cannot infer a factory-backed tool's name during discovery.
        // Without this static registration metadata the plugin loads, but the
        // tool is absent from the agent's effective tool set.
        { name: group.name },
      );
    }

    api.on("before_prompt_build", async (event, ctx) => {
      const route = routePrompt(contract, event.prompt);
      const key = runKey(ctx);
      routeByRun.set(key, route);
      evidenceByRun.set(key, []);
      retryByRun.delete(key);
      invalidArgumentsByRun.delete(key);
      if (!route.resolved) {
        metrics.unresolved += 1;
        return undefined;
      }
      metrics.routed += 1;
      return { prependContext: buildRoutingContext(route) };
    });

    api.on("before_tool_call", async (event, ctx) => {
      const blocked = shouldBlockGenericTool(event.toolName, event.params);
      if (blocked) {
        metrics.generic_blocks += 1;
        return { block: true, blockReason: blocked };
      }
      const group = groupByName.get(event.toolName);
      if (!group) return undefined;
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
      if (operation.mode === "read") return undefined;
      const args = event.params?.arguments ?? {};
      const nonce = ledger.issue({
        operation: operation.tool_id,
        args,
        toolCallId: event.toolCallId,
        runId: runKey(ctx, event.toolCallId),
      });
      metrics.approvals_requested += 1;
      return {
        params: { ...withoutNonce(event.params), __approval_nonce: nonce },
        requireApproval: {
          title: `Personal Assistant: ${operation.tool_id}`,
          description: `Einmalige Freigabe ${operation.approval} fuer exakt diese Argumente.`,
          // OpenClaw's plugin approval protocol accepts info/warning/critical.
          // Keep all external writes at the strongest supported level and local
          // state changes visibly below that without weakening allow-once.
          severity: approvalSeverity(operation),
          allowedDecisions: ["allow-once", "deny"],
          timeoutMs: contract.limits.approval_timeout_seconds * 1000,
          timeoutBehavior: "deny",
          timeoutReason: "Einzelfreigabe abgelaufen; nichts ausgefuehrt",
        },
      };
    });

    api.on("after_tool_call", async (event, ctx) => {
      if (!groupByName.has(event.toolName)) return undefined;
      const evidence = event.result?.details?.personalAssistantEvidence;
      if (!evidence) return undefined;
      const key = runKey(ctx, event.toolCallId);
      const rows = evidenceByRun.get(key) ?? [];
      if (!rows.some((row) => row.run_id === evidence.run_id)) rows.push(evidence);
      evidenceByRun.set(key, rows.slice(-32));
      return undefined;
    });

    api.on("before_agent_finalize", async (event, ctx) => {
      const key = runKey(ctx, event.runId);
      const route = routeByRun.get(key);
      if (!route?.resolved) return undefined;
      const verdict = guardAnswer(contract, route, event.lastAssistantMessage, evidenceByRun.get(key) ?? []);
      if (verdict.ok || retryByRun.has(key)) return undefined;
      retryByRun.add(key);
      metrics.guard_revisions += 1;
      return {
        action: "revise",
        reason: verdict.issues.join(","),
        retry: {
          instruction: `Nutze jetzt das registrierte strukturierte Werkzeug und antworte nur aus seiner aktuellen Evidenz. Probleme: ${verdict.issues.join(", ")}`,
          idempotencyKey: `personal-assistant-evidence-${key}`,
          maxAttempts: 1,
        },
      };
    });

    api.on("reply_payload_sending", async (event, ctx) => {
      const key = runKey(ctx);
      const route = routeByRun.get(key);
      if (!route?.resolved || typeof event.payload?.text !== "string") return undefined;
      const verdict = guardAnswer(contract, route, event.payload.text, evidenceByRun.get(key) ?? []);
      if (verdict.ok) return undefined;
      metrics.guard_replacements += 1;
      return {
        payload: { ...event.payload, text: safeReplacement(verdict.issues) },
        reason: "personal-assistant-evidence-guard",
      };
    });
  },
});

export const testing = {
  contract,
  operationById,
  groupByName,
  metrics,
  tokenizeCommand,
};
