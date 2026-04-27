const qrcode = require("qrcode-terminal");
const axios = require("axios");
const { Client, LocalAuth } = require("whatsapp-web.js");

// Respond only to messages starting with "!"
const REQUIRE_PREFIX = true;
const PREFIX = "!";

// Respond to your own messages too
const IGNORE_FROM_ME = false;

// Allow everyone. Set to a Set([...]) if you want an allowlist.
const ALLOWLIST = null;

// Ignore group chats
const IGNORE_GROUPS = true;

const PY_AGENT_URL = process.env.PY_AGENT_URL || "http://127.0.0.1:8811/message";
const AXIOS_TIMEOUT = 180000; // 3 minutes
const WHATSAPP_MAX_CHARS = 3500;

// Dedup to avoid double replies from message + message_create events
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

function trimForWhatsApp(text) {
  if (!text) return "No answer.";
  if (text.length <= WHATSAPP_MAX_CHARS) return text;
  return text.slice(0, WHATSAPP_MAX_CHARS - 80) + "\n\n[Answer trimmed for WhatsApp.]";
}

function stripPrefix(body) {
  const trimmed = (body || "").trim();
  if (!REQUIRE_PREFIX) return trimmed;
  if (!trimmed.startsWith(PREFIX)) return null;
  return trimmed.slice(PREFIX.length).trim();
}

async function handleMessage(message) {
  try {
    if (IGNORE_FROM_ME && message.fromMe) return;

    const chat = await message.getChat();
    if (IGNORE_GROUPS && chat.isGroup) return;

    const senderId = message.fromMe ? message.to : message.from;
    if (ALLOWLIST && !ALLOWLIST.has(senderId)) return;

    const query = stripPrefix(message.body);
    if (!query) return;

    const uniqueKey = message.id && message.id._serialized ? message.id._serialized : `${senderId}:${message.timestamp}:${message.body}`;
    if (seenRecently(uniqueKey)) return;

    await chat.sendStateTyping();

    const response = await axios.post(
      PY_AGENT_URL,
      { message: query },
      { timeout: AXIOS_TIMEOUT }
    );

    const answer = response.data && response.data.answer ? response.data.answer : JSON.stringify(response.data);
    await message.reply(trimForWhatsApp(answer));
  } catch (err) {
    const detail = err.response && err.response.data ? JSON.stringify(err.response.data) : err.message;
    console.error("Bridge error:", detail);
    try {
      await message.reply("Search Claw error: " + detail.slice(0, 500));
    } catch (_) {}
  }
}

const client = new Client({
  authStrategy: new LocalAuth({ clientId: "search-claw" }),
  puppeteer: {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"]
  }
});

client.on("qr", qr => {
  console.log("Scan this QR code with WhatsApp:");
  qrcode.generate(qr, { small: true });
});

client.on("ready", () => {
  console.log("WhatsApp bridge ready.");
  console.log(`Forwarding messages to ${PY_AGENT_URL}`);
});

client.on("message", handleMessage);
client.on("message_create", handleMessage);

client.initialize();
