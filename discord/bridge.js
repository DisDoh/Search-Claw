/*
  Search Claw Discord Bridge
  - /chat   = chat through the Search Claw Python agent
  - /search = search the web through the Search Claw Python agent

  Supports Discord User Install commands by registering global commands with:
  integration_types: [1]  // USER_INSTALL
  contexts: [0, 1, 2]    // GUILD, BOT_DM, PRIVATE_CHANNEL
*/

require("dotenv").config();
const axios = require("axios");
const {
  Client,
  GatewayIntentBits,
  REST,
  Routes,
  SlashCommandBuilder,
  ApplicationIntegrationType,
  InteractionContextType,
} = require("discord.js");

const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID;

const PY_AGENT_URL = process.env.PY_AGENT_URL || "http://127.0.0.1:8811/message";

const CHAT_HISTORY_LIMIT = Number.parseInt(process.env.CHAT_HISTORY_LIMIT || "6", 10);
const MAX_OPEN_PAGES = Number.parseInt(process.env.MAX_OPEN_PAGES || "5", 10);
const DISCORD_MAX_CHARS = Number.parseInt(process.env.DISCORD_MAX_CHARS || "1900", 10);
const AXIOS_TIMEOUT = Number.parseInt(process.env.AXIOS_TIMEOUT || "180000", 10);
const REGISTER_COMMANDS = (process.env.REGISTER_COMMANDS || "true").toLowerCase() !== "false";

if (!DISCORD_TOKEN || !DISCORD_CLIENT_ID) {
  console.error("Missing DISCORD_TOKEN or DISCORD_CLIENT_ID in discord/.env");
  process.exit(1);
}

const chatHistories = new Map();

function commandBuilder(name, description) {
  return new SlashCommandBuilder()
    .setName(name)
    .setDescription(description)
    .setIntegrationTypes(ApplicationIntegrationType.UserInstall)
    .setContexts(
      InteractionContextType.Guild,
      InteractionContextType.BotDM,
      InteractionContextType.PrivateChannel
    )
    .addStringOption(option =>
      option
        .setName("message")
        .setDescription(name === "search" ? "What should Search Claw search for?" : "What do you want to say to Search Claw?")
        .setRequired(true)
    );
}

const commands = [
  commandBuilder("chat", "Talk with Search Claw without web search."),
  commandBuilder("search", "Search the web with Search Claw and answer with sources."),
].map(command => command.toJSON());

function getHistoryKey(interaction) {
  const scopeId = interaction.guildId || interaction.channelId || "dm";
  return `${interaction.user.id}:${scopeId}`;
}

function pushHistory(key, userMessage, assistantMessage) {
  const history = chatHistories.get(key) || [];
  history.push({ role: "user", content: userMessage });
  history.push({ role: "assistant", content: assistantMessage });

  const maxMessages = Math.max(1, CHAT_HISTORY_LIMIT) * 2;
  while (history.length > maxMessages) history.shift();
  chatHistories.set(key, history);
}

function splitForDiscord(text) {
  const clean = String(text || "No answer.").trim() || "No answer.";
  if (clean.length <= DISCORD_MAX_CHARS) return [clean];

  const chunks = [];
  let remaining = clean;
  while (remaining.length > DISCORD_MAX_CHARS) {
    let cut = remaining.lastIndexOf("\n", DISCORD_MAX_CHARS);
    if (cut < DISCORD_MAX_CHARS * 0.5) cut = remaining.lastIndexOf(" ", DISCORD_MAX_CHARS);
    if (cut < DISCORD_MAX_CHARS * 0.5) cut = DISCORD_MAX_CHARS;
    chunks.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
  if (remaining) chunks.push(remaining);
  return chunks.slice(0, 5);
}

async function replyLong(interaction, text) {
  const chunks = splitForDiscord(text);
  await interaction.editReply(chunks[0]);
  for (const chunk of chunks.slice(1)) {
    await interaction.followUp({ content: chunk, ephemeral: false });
  }
}

async function registerCommands() {
  if (!REGISTER_COMMANDS) return;

  const rest = new REST({ version: "10" }).setToken(DISCORD_TOKEN);
  await rest.put(Routes.applicationCommands(DISCORD_CLIENT_ID), { body: commands });
  console.log("Registered global /chat and /search commands for User Install.");
}

function normalizeHistory(history) {
  return history.map(item => ({
    role: item.role,
    content: item.content,
    text: item.content,
  }));
}

function modeForCommand(commandName) {
  return commandName === "chat" ? "chat" : "search";
}

function prefixForCommand(commandName) {
  return commandName === "chat" ? "/chat" : "/search";
}

async function runAgentRequest(interaction, message) {
  const mode = modeForCommand(interaction.commandName);
  const historyKey = getHistoryKey(interaction);
  const history = chatHistories.get(historyKey) || [];
  const chatId = `${interaction.guildId || "dm"}:${interaction.channelId || "unknown"}:${interaction.user.id}`;

  const payload = {
    text: message,
    message,
    mode,
    prefix: prefixForCommand(interaction.commandName),
    command: interaction.commandName,
    chat_id: chatId,
  };

  if (mode === "chat") {
    payload.history = normalizeHistory(history);
  }

  if (mode === "search") {
    payload.max_results = MAX_OPEN_PAGES;
    payload.max_open_pages = MAX_OPEN_PAGES;
  }

  const response = await axios.post(
    PY_AGENT_URL,
    payload,
    {
      timeout: AXIOS_TIMEOUT,
      headers: { "Content-Type": "application/json" },
    }
  );

  const answer = response.data?.answer || response.data?.reply || JSON.stringify(response.data, null, 2);

  if (mode === "chat") {
    pushHistory(historyKey, message, answer);
  }
  return answer;
}

const client = new Client({
  intents: [GatewayIntentBits.Guilds],
});

client.once("clientReady", readyClient => {
  console.log(`Discord bridge ready as ${readyClient.user.tag}.`);
  console.log("Config:", {
    commands: ["/chat", "/search"],
    integrationTypes: ["UserInstall"],
    contexts: ["Guild", "BotDM", "PrivateChannel"],
    CHAT_HISTORY_LIMIT,
    MAX_OPEN_PAGES,
    PY_AGENT_URL,
  });
});

client.on("interactionCreate", async interaction => {
  if (!interaction.isChatInputCommand()) return;
  if (!["chat", "search"].includes(interaction.commandName)) return;

  const message = interaction.options.getString("message", true).trim();
  if (!message) return interaction.reply({ content: "Please send a non-empty message.", ephemeral: true });

  try {
    await interaction.deferReply();
    const answer = await runAgentRequest(interaction, message);
    await replyLong(interaction, answer);
  } catch (err) {
    const detail = err.response?.data ? JSON.stringify(err.response.data) : err.message;
    console.error("Discord bridge error:", detail);

    const safeMessage = `Search Claw Discord bridge error: ${String(detail).slice(0, 1500)}`;
    if (interaction.deferred || interaction.replied) {
      await interaction.editReply(safeMessage).catch(() => {});
    } else {
      await interaction.reply({ content: safeMessage, ephemeral: true }).catch(() => {});
    }
  }
});

(async () => {
  await registerCommands();
  await client.login(DISCORD_TOKEN);
})();
