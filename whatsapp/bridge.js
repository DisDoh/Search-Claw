const qrcode = require("qrcode-terminal");
const axios = require("axios");
const { Client, LocalAuth } = require("whatsapp-web.js");

// Accept both:
// ! = chat with LLM
// ? = ask a web-search question
const ENABLE_CHAT_PREFIX = true;
const CHAT_PREFIX = "!";

const ENABLE_SEARCH_PREFIX = true;
const SEARCH_PREFIX = "?";

// Respond to your own messages too
const IGNORE_FROM_ME = false;

// Allow everyone
const ALLOWLIST = null;

// Ignore group chats
const IGNORE_GROUPS = true;

const PY_AGENT_URL = "http://127.0.0.1:8811/message";
const AXIOS_TIMEOUT = 180000; // 3 minutes
const WHATSAPP_MAX_CHARS = 3500;

// Number of recent chat messages to include for chat mode
const CHAT_HISTORY_LIMIT = 6;

// Search agent: maximum number of pages the Python agent may open
const MAX_OPEN_PAGES = 5;

// WhatsApp Web can reload itself while whatsapp-web.js is injecting its
// helpers. Puppeteer then rejects with "Execution context was destroyed".
// Treat that navigation race as transient instead of crashing the launcher.
const INITIALIZE_MAX_ATTEMPTS = 5;
const INITIALIZE_RETRY_DELAY_MS = 2000;

// Do not include temporary acknowledgement messages in the LLM memory.
// These are messages sent by this bridge before the real answer arrives.
const IGNORE_HISTORY_TEXTS = new Set([
  "working on it...",
  "working on it",
  "searching the web...",
  "searching the web",
  "agent error. check bridge logs.",
]);

function normalizeHistoryText(s) {
  return String(s || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

function shouldIgnoreHistoryMessage(body) {
  const normalized = normalizeHistoryText(body);
  return IGNORE_HISTORY_TEXTS.has(normalized);
}

function shouldIncludeHistoryMessage(message) {
  if (!message || typeof message.body !== "string") return false;

  const body = message.body.trim();
  if (!body) return false;
  if (message.type && message.type !== "chat") return false;
  if (shouldIgnoreHistoryMessage(body)) return false;
  if (parsePrefixedCommand(body)) return false;

  return true;
}

// Dedup to avoid double replies (message + message_create)
const SEEN_TTL_MS = 60_000;
const seen = new Map();

function seenRecently(key) {
  const now = Date.now();
  for (const [k, t] of seen.entries()) {
    if (now - t > SEEN_TTL_MS) seen.delete(k);
  }
  if (seen.has(key)) return true;
  seen.set(key, now);
  return false;
}

const client = new Client({
  authStrategy: new LocalAuth({ clientId: "searchclaw-whatsapp" }),
  puppeteer: {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

function trimReply(s) {
  s = String(s || "");
  return s.length > WHATSAPP_MAX_CHARS
    ? s.slice(0, WHATSAPP_MAX_CHARS) + "\n\n[truncated]"
    : s;
}

async function getReplyChatId(msg) {
  try {
    const chat = await msg.getChat();
    if (chat && chat.id && chat.id._serialized) return chat.id._serialized;
  } catch {}

  if (msg.fromMe && msg.to) return msg.to;
  return msg.from;
}

function parsePrefixedCommand(raw) {
  const text = String(raw || "").trim();
  if (!text) return null;

  if (ENABLE_CHAT_PREFIX && text.startsWith(CHAT_PREFIX)) {
    const content = text.slice(CHAT_PREFIX.length).trim();
    if (!content) return null;
    return {
      mode: "chat",
      text: content,
      prefix: CHAT_PREFIX,
    };
  }

  if (ENABLE_SEARCH_PREFIX && text.startsWith(SEARCH_PREFIX)) {
    const content = text.slice(SEARCH_PREFIX.length).trim();
    if (!content) return null;
    return {
      mode: "search",
      text: content,
      prefix: SEARCH_PREFIX,
    };
  }

  return null;
}

function ackMessageForMode(mode) {
  if (mode === "search") return "Searching the web...";
  return "Working on it...";
}

async function getRecentChatHistory(replyChatId, limit = 3) {
  try {
    const chat = await client.getChatById(replyChatId);
    if (!chat) return [];
    const rawMessages = await client.pupPage.evaluate(
      async (chatId, fetchLimit) => {
        const chat = await window.WWebJS.getChat(chatId, { getAsModel: false });
        if (!chat || !chat.msgs || typeof chat.msgs.getModelsArray !== "function") {
          return [];
        }

        const msgs = chat.msgs.getModelsArray();
        if (!Array.isArray(msgs) || msgs.length === 0) {
          return [];
        }

        const sorted = msgs
          .filter(m => m && !m.isNotification)
          .sort((a, b) => (a.t || 0) - (b.t || 0));

        return sorted.slice(-fetchLimit).map(m => window.WWebJS.getMessageModel(m));
      },
      replyChatId,
      Math.max(limit + 8, 12)
    );

    if (!Array.isArray(rawMessages) || rawMessages.length === 0) return [];

    const history = rawMessages
      .filter(shouldIncludeHistoryMessage)
      .sort((a, b) => {
        const ta = a.timestamp || 0;
        const tb = b.timestamp || 0;
        return ta - tb;
      })
      .map(m => ({
        role: m.fromMe ? "assistant" : "user",
        text: m.body.trim(),
        timestamp: m.timestamp || null,
        id: m.id && m.id._serialized ? m.id._serialized : null,
      }));

    // Keep last N messages from this discussion
    return history.slice(-limit);
  } catch (e) {
    console.error("HISTORY_ERROR:", e?.message || String(e));
    return [];
  }
}

async function handleMessage(msg, tag) {
  const raw = (msg.body || "").trim();
  if (!raw) return;

  const msgId = msg.id && msg.id._serialized ? msg.id._serialized : null;
  const key = msgId || `${msg.from}|${msg.to}|${msg.fromMe}|${raw}`;

  if (seenRecently(key)) {
    console.log(tag, "DEDUP SKIP:", key);
    return;
  }

  console.log(tag, {
    id: msgId,
    from: msg.from,
    to: msg.to,
    fromMe: msg.fromMe,
    body: msg.body,
    type: msg.type,
  });

  if (IGNORE_FROM_ME && msg.fromMe) return;

  const parsed = parsePrefixedCommand(raw);
  if (!parsed) return;

  const { mode, text, prefix } = parsed;

  const replyChatId = await getReplyChatId(msg);
  console.log("REPLY_CHAT_ID:", replyChatId);
  console.log("MODE:", mode, "PREFIX:", prefix, "TEXT:", text);

  // Ignore group chats
  if (IGNORE_GROUPS && replyChatId.endsWith("@g.us")) {
    console.log("IGNORED group chat:", replyChatId);
    return;
  }

  // Allowlist disabled => allow everyone
  if (!msg.fromMe) {
    if (ALLOWLIST && !ALLOWLIST.has(msg.from)) {
      console.log("BLOCKED by allowlist:", msg.from);
      return;
    }
  }

  try {
    await client.sendMessage(replyChatId, ackMessageForMode(mode));

    let history = [];
    if (mode === "chat") {
      history = await getRecentChatHistory(replyChatId, CHAT_HISTORY_LIMIT);
    }

    const payload = {
      text,
      mode,              // "chat" or "search"
      prefix,            // "!" or "?"
      chat_id: replyChatId,
    };

    // Each mode sends only its own part:
    // - chat mode: conversation memory
    // - search mode: page opening limit
    if (mode === "chat") {
      payload.history = history;
    }

    if (mode === "search") {
      payload.max_open_pages = MAX_OPEN_PAGES;
    }

    console.log("PAYLOAD:", JSON.stringify(payload, null, 2));

    const r = await axios.post(
      PY_AGENT_URL,
      payload,
      {
        timeout: AXIOS_TIMEOUT,
        headers: { "Content-Type": "application/json" },
      }
    );

    const reply = r?.data?.reply ? r.data.reply : "No reply generated.";
    await client.sendMessage(replyChatId, trimReply(reply));
  } catch (e) {
    const detail = e?.response?.data || e?.message || String(e);
    console.error("BRIDGE_ERROR:", detail);
    try {
      await client.sendMessage(replyChatId, "Agent error. Check bridge logs.");
    } catch {}
  }
}

client.on("qr", (qr) => {
  console.log("\nScan this QR in WhatsApp:");
  console.log("WhatsApp → Settings → Linked devices → Link a device\n");
  qrcode.generate(qr, { small: true });
});

client.on("ready", () => {
  console.log("✅ WhatsApp bridge ready.");
  console.log("Config:", {
    ENABLE_CHAT_PREFIX,
    CHAT_PREFIX,
    ENABLE_SEARCH_PREFIX,
    SEARCH_PREFIX,
    IGNORE_FROM_ME,
    IGNORE_GROUPS,
    ALLOWLIST,
    CHAT_HISTORY_LIMIT,
    MAX_OPEN_PAGES,
  });
});

client.on("auth_failure", (msg) => console.error("❌ Auth failure:", msg));
client.on("disconnected", (reason) => console.error("❌ Disconnected:", reason));

client.on("message", (msg) => handleMessage(msg, "[message]"));
client.on("message_create", (msg) => handleMessage(msg, "[message_create]"));

function isTransientInitializationError(error) {
  const message = String(error?.message || error || "").toLowerCase();
  return [
    "execution context was destroyed",
    "cannot find context with specified id",
    "most likely because of a navigation",
    "inspected target navigated or closed",
    "target closed",
    "session closed",
  ].some(fragment => message.includes(fragment));
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function closeBrowserAfterFailedInitialization() {
  try {
    if (client.pupBrowser) await client.destroy();
  } catch (error) {
    console.warn(
      "WhatsApp browser cleanup warning:",
      error?.message || String(error)
    );
  } finally {
    client.pupBrowser = null;
    client.pupPage = null;
  }
}

async function initializeClient() {
  for (let attempt = 1; attempt <= INITIALIZE_MAX_ATTEMPTS; attempt += 1) {
    try {
      await client.initialize();
      return;
    } catch (error) {
      const transient = isTransientInitializationError(error);
      console.error(
        `WhatsApp initialization failed (attempt ${attempt}/${INITIALIZE_MAX_ATTEMPTS}):`,
        error?.message || String(error)
      );

      if (!transient || attempt === INITIALIZE_MAX_ATTEMPTS) {
        await closeBrowserAfterFailedInitialization();
        process.exitCode = 1;
        return;
      }

      await closeBrowserAfterFailedInitialization();
      const delay = INITIALIZE_RETRY_DELAY_MS * attempt;
      console.warn(`WhatsApp Web navigated during startup; retrying in ${delay}ms...`);
      await sleep(delay);
    }
  }
}

initializeClient();
