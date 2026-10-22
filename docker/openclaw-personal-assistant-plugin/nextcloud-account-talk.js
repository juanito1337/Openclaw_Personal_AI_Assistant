// Native channel adapter for the pinned OpenClaw 2026.7.1 ChannelPlugin API.
// Credentials remain inside the Python worker and never cross this protocol.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

export const CHANNEL_ID = "nextcloud-account-talk";
const ACCOUNT_ID = "default";
const MAX_LINE = 128_000;

export class TalkWorkerBridge {
  constructor({ spawnProcess = spawn } = {}) {
    this.pending = new Map();
    this.sequence = 0;
    this.closed = false;
    this.child = spawnProcess("python3", ["-P", "-m", "personal_assistant.nextcloud_talk_account", "worker"], {
      cwd: "/opt/openclaw-agent",
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONPATH: "/opt/openclaw-agent", PYTHONSAFEPATH: "1" },
      shell: false,
    });
    const fail = () => {
      this.closed = true;
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error("talk-worker-unavailable"));
      }
      this.pending.clear();
    };
    this.child.once("error", fail);
    this.child.once("exit", fail);
    // Never forward subprocess stderr: errors from network libraries can
    // contain remote URLs or authentication details.
    this.child.stderr.on("data", () => {});
    this.lines = createInterface({ input: this.child.stdout, crlfDelay: Infinity });
    let bufferedBytes = 0;
    this.child.stdout.on("data", (chunk) => {
      bufferedBytes += chunk.length;
      if (bufferedBytes > MAX_LINE) {
        fail();
        this.child.kill("SIGTERM");
      }
      if (chunk.includes(10)) bufferedBytes = chunk.length - chunk.lastIndexOf(10) - 1;
    });
    this.lines.on("line", (line) => {
      try {
        if (Buffer.byteLength(line) > MAX_LINE) throw new Error("oversized");
        const response = JSON.parse(line);
        const pending = this.pending.get(response.request_id);
        if (!pending) return;
        this.pending.delete(response.request_id);
        clearTimeout(pending.timer);
        if (response.ok === true) pending.resolve(response.result);
        else pending.reject(new Error(/^talk-[a-z-]+$/.test(response.code) ? response.code : "talk-worker-error"));
      } catch {
        fail();
        this.child.kill("SIGTERM");
      }
    });
  }

  request(operation, args = {}) {
    if (this.closed) return Promise.reject(new Error("talk-worker-unavailable"));
    const requestId = ++this.sequence;
    const encoded = JSON.stringify({ ...args, operation, request_id: requestId }) + "\n";
    if (Buffer.byteLength(encoded) > MAX_LINE) return Promise.reject(new Error("talk-request-too-large"));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        // A timed-out send cannot be retried. Killing the worker also turns
        // persisted processing/sending states into uncertain on next start.
        this.close();
        reject(new Error("talk-worker-timeout"));
      }, 20_000);
      this.pending.set(requestId, { resolve, reject, timer });
      this.child.stdin.write(encoded, (error) => {
        if (error) {
          clearTimeout(timer);
          this.pending.delete(requestId);
          reject(new Error("talk-worker-unavailable"));
        }
      });
    });
  }

  close() {
    this.closed = true;
    this.child.kill("SIGTERM");
  }
}

function delay(milliseconds, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, milliseconds);
    signal.addEventListener("abort", done, { once: true });
  });
}

export async function dispatchTalkMessage({ message, ctx, core, worker }) {
  const bot = ctx.cfg.channels?.["nextcloud-talk"];
  const botAccounts = [bot, ...Object.values(bot?.accounts ?? {})];
  const overlaps = bot?.enabled !== false && botAccounts.some((account) => {
    if (!account || account.enabled === false) return false;
    const rooms = account.rooms ?? bot.rooms;
    if (!rooms) return true;
    return [rooms["*"], rooms[message.room]].some((entry) => entry && entry.enabled !== false);
  });
  if (overlaps) {
    await worker.request("finish", { account: message.account, room: message.room,
      inbound_id: message.id, success: false });
    throw new Error("talk-webhook-owner-conflict");
  }
  try {
    const route = core.routing.resolveAgentRoute({
      cfg: ctx.cfg, channel: CHANNEL_ID, accountId: ACCOUNT_ID,
      peer: { kind: "group", id: `${message.account}:${message.room}` },
    });
    // Room and authenticated stable sender are separately verified by the
    // worker. No plaintext control command is elevated to an approval here.
    const inbound = core.reply.finalizeInboundContext({
      Body: message.text,
      BodyForAgent: message.text,
      RawBody: message.text,
      CommandBody: message.text,
      From: `${CHANNEL_ID}:room:${message.room}`,
      To: `${CHANNEL_ID}:${message.room}`,
      SenderId: message.sender_id,
      SessionKey: route.sessionKey,
      AccountId: ACCOUNT_ID,
      ChatType: "group",
      ConversationLabel: `Nextcloud Talk ${message.room}`,
      GroupSubject: message.room,
      Provider: CHANNEL_ID,
      Surface: CHANNEL_ID,
      MessageSid: String(message.id),
      Timestamp: Number(message.timestamp) * 1000,
      OriginatingChannel: CHANNEL_ID,
      OriginatingTo: `${CHANNEL_ID}:${message.room}`,
      CommandAuthorized: false,
      WasMentioned: true,
    });
    let sequence = 0;
    let failed = false;
    const storePath = core.session.resolveStorePath(ctx.cfg.session?.store, { agentId: route.agentId });
    await core.inbound.dispatchReply({
      cfg: ctx.cfg, channel: CHANNEL_ID, accountId: ACCOUNT_ID,
      agentId: route.agentId, routeSessionKey: route.sessionKey,
      storePath, ctxPayload: inbound,
      recordInboundSession: core.session.recordInboundSession,
      dispatchReplyWithBufferedBlockDispatcher: core.reply.dispatchReplyWithBufferedBlockDispatcher,
      delivery: {
        deliver: async (payload) => {
          if (payload.mediaUrl || payload.mediaUrls?.length) throw new Error("talk-media-unsupported");
          if (!payload.text?.trim()) return;
          const chunks = core.text.chunkMarkdownText(payload.text, 3500);
          for (const text of chunks) {
            await worker.request("send", { account: message.account, room: message.room,
              inbound_id: message.id, sequence: sequence++, text });
            ctx.setStatus({ ...ctx.getStatus(), lastOutboundAt: Date.now() });
          }
        },
        onError: () => { failed = true; },
      },
      replyPipeline: {},
      replyOptions: { disableBlockStreaming: true },
      record: { onRecordError: () => { failed = true; } },
    });
    if (failed) throw new Error("talk-dispatch-uncertain");
    await worker.request("finish", { account: message.account, room: message.room,
      inbound_id: message.id, success: true });
  } catch {
    await worker.request("finish", { account: message.account, room: message.room,
      inbound_id: message.id, success: false }).catch(() => {});
    throw new Error("talk-dispatch-uncertain");
  }
}

export function createNextcloudAccountTalkChannel(runtime, { createWorker = () => new TalkWorkerBridge() } = {}) {
  let liveConfigured = false;
  let liveConnected = false;
  return {
    id: CHANNEL_ID,
    meta: {
      id: CHANNEL_ID, label: "Nextcloud Talk Konto", selectionLabel: "Nextcloud Talk (Kontoverbindung)",
      docsPath: "/plugins/personal-assistant-tools/nextcloud/setup", docsLabel: "Einrichtung",
      blurb: "Talk über die sicher verbundene Nextcloud-Anmeldung.",
    },
    capabilities: { chatTypes: ["group"], media: false, reactions: false,
      threads: false, nativeCommands: false, blockStreaming: false },
    config: {
      listAccountIds: () => [ACCOUNT_ID],
      defaultAccountId: () => ACCOUNT_ID,
      resolveAccount: () => ({ accountId: ACCOUNT_ID, enabled: true, name: "Nextcloud-Kontokanal" }),
      isEnabled: () => true,
      // The fixed worker is always available, including while the account is
      // unpaired. Its live snapshot separately reports configured/connected.
      isConfigured: () => true,
      describeAccount: () => ({ accountId: ACCOUNT_ID, enabled: true, configured: liveConfigured }),
    },
    status: {
      defaultRuntime: { accountId: ACCOUNT_ID, running: false, connected: false, configured: false },
      buildAccountSnapshot: ({ runtime: snapshot }) => ({ ...snapshot, accountId: ACCOUNT_ID,
        configured: liveConfigured, connected: liveConnected }),
    },
    gateway: {
      startAccount: async (ctx) => {
        const core = ctx.channelRuntime ?? runtime.channel;
        if (!core?.inbound?.dispatchReply || !core?.reply?.finalizeInboundContext) {
          throw new Error("talk-channel-runtime-unavailable");
        }
        const worker = createWorker();
        const abort = () => worker.close();
        ctx.abortSignal.addEventListener("abort", abort, { once: true });
        try {
          while (!ctx.abortSignal.aborted) {
            try {
              const result = await worker.request("poll");
              liveConfigured = result.status.enabled === true;
              liveConnected = result.status.connected === true;
              ctx.setStatus({ ...ctx.getStatus(), running: true,
                configured: result.status.enabled === true,
                connected: result.status.connected === true,
                lastError: null,
                ...(result.message ? { lastInboundAt: Date.now() } : {}),
              });
              if (result.message) await dispatchTalkMessage({ message: result.message, ctx, core, worker });
              else await delay(result.status.enabled ? 250 : 2000, ctx.abortSignal);
            } catch (error) {
              liveConnected = false;
              const code = /^talk-[a-z-]+$/.test(error?.message) ? error.message : "talk-channel-error";
              ctx.setStatus({ ...ctx.getStatus(), running: true, connected: false, lastError: code });
              if (worker.closed) throw new Error(code);
              await delay(5000, ctx.abortSignal);
            }
          }
        } finally {
          ctx.abortSignal.removeEventListener("abort", abort);
          worker.close();
          liveConnected = false;
          ctx.setStatus({ ...ctx.getStatus(), running: false, connected: false });
        }
      },
    },
  };
}
