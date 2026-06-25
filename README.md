# Search Claw

Search Claw is a local web-search assistant that can run from the terminal, as a small HTTP server, through WhatsApp, or through Discord slash commands.

It searches the web, opens result pages, and asks a local OpenAI-compatible LLM server, such as `llama.cpp`, to answer with sources.

The default setup uses:

- Python for the Search Claw agent
- DuckDuckGo Lite search as the search backend
- `llama.cpp` server as the local model API
- `whatsapp-web.js` for the WhatsApp bridge
- `discord.js` for the Discord bridge

> This project is designed for personal experimentation. Respect website terms, rate limits, Discord policies, WhatsApp policies, copyright, and local laws before using it commercially.

---

## Features

- Command-line search assistant
- HTTP server endpoint for app, WhatsApp, or Discord integrations
- WhatsApp bridge with `!` chat mode and `?` search mode
- Discord bridge with only two slash commands: `/chat` and `/search`
- Discord User Install command registration, so the commands can be installed to a user account
- `/chat` talks directly to the local LLM without browsing
- `/search` searches the web through Search Claw and opens up to 5 pages by default
- Chat history limit set to 6 turns by default for Discord `/chat` and WhatsApp `!` chat
- Works with a local `llama.cpp` OpenAI-compatible server
- Simple source validation to reduce unsourced answers
- Debug and verbose modes for troubleshooting

---

## Search Backend Notes

Search Claw uses DuckDuckGo Lite/HTML by default. DuckDuckGo may temporarily return
`202` or `429` for automated requests; when that happens, Search Claw reports that
the search backend is blocked instead of inventing sources.

Optional non-Google fallback:

```bash
SEARXNG_BASE_URL=http://127.0.0.1:8888
```

Use a local or private SearXNG instance if you want reliable bot-friendly search
without falling back to Google.

---

## Project structure

```text
search-claw/
├── README.md
├── LICENSE
├── requirements.txt
├── .env.example
├── run_all.sh
├── searchClaw.py
├── system_prompt.txt
├── discord/
│   ├── bridge.js
│   ├── package.json
│   └── .env.example
├── whatsapp/
│   ├── bridge.js
│   └── package.json
```

---

## How the Discord commands work

### `/chat`

Use `/chat` for normal conversation with your local model.

- Does **not** search the web
- Uses the local OpenAI-compatible LLM endpoint directly
- Keeps the last 6 turns of history by default
- Best for brainstorming, coding help, explanations, drafts, and general chat

Example:

```text
/chat message: Explain how this project works like I am new to coding.
```

### `/search`

Use `/search` when you want web information with sources.

- Calls the Python Search Claw server
- Searches the web
- Opens/fetches up to 5 pages by default
- Sends the gathered context to the local LLM
- Best for recent facts, niche topics, product/project research, or source-backed answers

Example:

```text
/search message: latest llama.cpp CUDA build options
```

---

## Requirements

### Python

- Python 3.10+
- `requests`
- `beautifulsoup4`
- `flask`

Install:

```bash
cd search-claw
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
cd search-claw
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks activation, run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again.

---

## Install `llama.cpp`

Search Claw expects an OpenAI-compatible chat completions endpoint at:

```text
http://127.0.0.1:8033/v1/chat/completions
```

### Linux with CUDA

Install build tools:

```bash
sudo apt update
sudo apt install -y git cmake build-essential
```

Clone and compile:

```bash
git clone https://github.com/ggml-org/llama.cpp.git ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j
```

Start the server:

```bash
~/llama.cpp/build/bin/llama-server \
  -m /home/disd/models/ColdBrew-Lucid.Q6_K.gguf \
  -c 4096 \
  -ngl 999 \
  --host 127.0.0.1 \
  --port 8033
```

If your GPU runs out of VRAM, lower `-ngl`, use a smaller quantization such as `Q4_K_M`, or run on CPU.

### Windows with Visual Studio Code

Install:

1. Visual Studio 2022 Build Tools with **Desktop development with C++**
2. CMake
3. Git
4. NVIDIA CUDA Toolkit, if you want GPU acceleration
5. Visual Studio Code

Open **Developer PowerShell for VS 2022**, then run:

```powershell
git clone https://github.com/ggml-org/llama.cpp.git C:\llama.cpp
cd C:\llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j
```

Start the server:

```powershell
C:\llama.cpp\build\bin\Release\llama-server.exe `
  -m C:\models\your-model.gguf `
  -c 4096 `
  -ngl 999 `
  --host 127.0.0.1 `
  --port 8033
```

If you do not use CUDA, build without `-DGGML_CUDA=ON`:

```powershell
cmake -B build
cmake --build build --config Release -j
```

---

## Model files

Put your `.gguf` model somewhere simple.

Linux example:

```text
/home/disd/models/ColdBrew-Lucid.Q6_K.gguf
```

Windows example:

```text
C:\models\ColdBrew-Lucid.Q4_K_M.gguf
```

When starting `llama-server`, the `-m` argument must point to the exact model file.

If you downloaded a model with Ollama, Ollama stores models in its own internal storage and does not usually expose a simple `.gguf` path. For `llama.cpp`, it is easier to download the `.gguf` file directly from Hugging Face and place it in `C:\models` or `/home/<you>/models`.

---

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Main variables:

```bash
LLAMA_BASE_URL=http://127.0.0.1:8033
LLAMA_MODEL=
SEARCH_CLAW_HOST=127.0.0.1
SEARCH_CLAW_PORT=8811
REQUIRE_SOURCES=true
```

`LLAMA_MODEL` can stay empty for many `llama.cpp` setups, but some OpenAI-compatible servers require a model name.

---

## Usage

### 1. Command-line mode

```bash
python3 searchClaw.py "Who is the president of France?" --debug
```

Verbose mode shows payloads and intermediate search results:

```bash
python3 searchClaw.py "latest llama.cpp CUDA build options" --debug --verbose
```

### 2. Server mode

Start the Search Claw HTTP server:

```bash
python3 searchClaw.py --server
```

Send a message:

```bash
curl -X POST http://127.0.0.1:8811/message \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is Bluetooth Poker 8?", "max_results":5}'
```

Response shape:

```json
{
  "ok": true,
  "answer": "...",
  "sources": [
    {"title": "...", "url": "..."}
  ]
}
```

---

## Discord bridge setup

The Discord bridge is in the `discord/` folder.

It registers two global slash commands:

- `/chat`
- `/search`

The bridge registers the commands as **User Install** commands only:

```js
integration_types: [1]
contexts: [0, 1, 2]
```

That means the commands are made for user installation, and can be available in guilds, bot DMs, and private channels depending on Discord's app installation settings.

### 1. Create a Discord application

1. Go to the Discord Developer Portal.
2. Click **New Application**.
3. Give it a name, for example `Search Claw Bot`.
4. Open the **Bot** page.
5. Create/reset the bot token.
6. Copy the token. You will use it as `DISCORD_TOKEN`.
7. Open **General Information**.
8. Copy **Application ID**. You will use it as `DISCORD_CLIENT_ID`.

### 2. Enable User Install

In the Discord Developer Portal:

1. Open your application.
2. Go to **Installation**.
3. Under **Installation Contexts**, enable **User Install**.
4. For User Install default settings, add the `applications.commands` scope.
5. Save changes.
6. Copy the Discord-provided install link.
7. Open the link and install the app to your user account.

For this bridge, you do **not** need Message Content Intent because it uses slash commands, not normal message reading.

### 3. Install Node dependencies

```bash
cd search-claw/discord
npm install
```

On Windows PowerShell:

```powershell
cd search-claw\discord
npm install
```

### 4. Create Discord environment file

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
copy .env.example .env
```

Edit `discord/.env`:

```env
DISCORD_TOKEN=your_bot_token_here
DISCORD_CLIENT_ID=your_application_client_id_here
PY_AGENT_URL=http://127.0.0.1:8811/message
MAX_OPEN_PAGES=5
LLAMA_BASE_URL=http://127.0.0.1:8033
LLAMA_MODEL=
CHAT_HISTORY_LIMIT=6
REGISTER_COMMANDS=true
```

### 5. Start the services

You need three running terminals for Discord search mode:

Terminal 1: local LLM server

```bash
~/llama.cpp/build/bin/llama-server \
  -m /path/to/your/model.gguf \
  -c 4096 \
  -ngl 999 \
  --host 127.0.0.1 \
  --port 8033
```

Terminal 2: Search Claw Python server

```bash
cd search-claw
source .venv/bin/activate
python3 searchClaw.py --server
```

Terminal 3: Discord bridge

```bash
cd search-claw/discord
node bridge.js
```

On Windows, Terminal 2 looks like:

```powershell
cd search-claw
.\.venv\Scripts\Activate.ps1
python searchClaw.py --server
```

And Terminal 3:

```powershell
cd search-claw\discord
node bridge.js
```

When the Discord bridge starts successfully, you should see something like:

```text
Registered global /chat and /search commands for User Install.
Discord bridge ready as Search Claw Bot#1234.
Config: {
  commands: [ '/chat', '/search' ],
  userInstallOnly: true,
  CHAT_HISTORY_LIMIT: 6,
  MAX_OPEN_PAGES: 5
}
```

Global Discord commands can take a little time to appear in the Discord client. Restarting Discord can help refresh the command list.

---

## WhatsApp bridge setup

Start the Search Claw server first:

```bash
python3 searchClaw.py --server
```

Then start the WhatsApp bridge:

```bash
cd whatsapp
npm install
node bridge.js
```

Scan the QR code in the terminal with WhatsApp.

By default:

- Everyone can use the bot
- Group chats are ignored
- `! message` = chat directly with the local LLM, no web search
- `? query` = search the web with Search Claw, opening up to `MAX_OPEN_PAGES` pages, default `5`
- WhatsApp chat mode keeps the last `CHAT_HISTORY_LIMIT` turns, default `6`

Example WhatsApp chat message:

```text
! explain this project simply
```

Example WhatsApp search message:

```text
? who is the president of France?
```

Useful WhatsApp environment settings are inherited from `.env` when using `./run_all.sh`:

```env
CHAT_PREFIX=!
SEARCH_PREFIX=?
ENABLE_CHAT_PREFIX=true
ENABLE_SEARCH_PREFIX=true
MAX_OPEN_PAGES=5
CHAT_HISTORY_LIMIT=6
IGNORE_FROM_ME=false
IGNORE_GROUPS=true
```

---

## Minimal one-time launcher: `./run_all.sh`

The easiest way to start Search Claw on Linux/macOS is the simplified launcher. It now handles the full local stack:

- creates and uses the Python virtual environment in `.venv/`
- installs Python requirements
- checks for `llama.cpp` in your home folder, for example `~/llama.cpp`
- if `llama.cpp` is missing, clones it into your home folder
- keeps separate `llama.cpp` CPU and CUDA builds and lets you choose one at launch
- starts the `llama-server`
- starts the Search Claw Python server
- starts Discord, WhatsApp, or both

Run it from the project root:

```bash
chmod +x run_all.sh
./run_all.sh
```

On the first run, it asks only the minimum needed. Every prompt shows a default value in brackets; pressing Enter with an empty answer keeps that proposed default.

- `1` = launch Discord only
- `2` = launch WhatsApp only
- `3` = launch both
- where to install/find `llama.cpp`, default: `~/llama.cpp`
- your `.gguf` model path, for example `~/models/ColdBrew-Lucid.Q4_K_M.gguf`
- a working NVIDIA CUDA GPU (the launcher is CUDA-only)
- Discord bot token, only if Discord is selected
- Discord Application ID / Client ID, only if Discord is selected

The Discord token prompt is visible on purpose, because some terminals block paste or look frozen when hidden input is used. Paste/type the token normally and press Enter. If your terminal still refuses paste or typing, let the launcher create `discord/.env`, stop it with `Ctrl+C`, then edit this line manually:

```env
DISCORD_TOKEN=your_real_token_here
```

Then run `./run_all.sh` again and press Enter to reuse the saved config.

Important: keep `discord/.env` as pure `KEY=value` lines. Do not add raw notes like `Discord token here` without a `#` at the beginning. The launcher now safely ignores accidental text lines instead of trying to execute them, but a clean file should look like this:

```env
DISCORD_TOKEN=your_real_token_here
DISCORD_CLIENT_ID=your_application_id_here
```

The launcher automatically creates these files/folders when needed:

```text
.env
discord/.env
.run_all.env
.venv/
~/llama.cpp/     # unless you choose another install folder
```

The default local config is:

```text
LLAMA_BASE_URL=http://127.0.0.1:8033
LLAMA_CPP_DIR=$HOME/llama.cpp
MODEL_PATH=$HOME/models/model.gguf
LLAMA_CTX=4096
LLAMA_MODE=cuda
LLAMA_CUDA_NGL=auto
LLAMA_FIT_TARGET_MIB=1024
LLAMA_FIT_MIN_CTX=4096
SEARCH_CLAW_PORT=8811
MAX_OPEN_PAGES=5
CHAT_HISTORY_LIMIT=6
CHAT_PREFIX=!
SEARCH_PREFIX=?
```

The launcher now uses CUDA exclusively and builds only `~/llama.cpp/build-cuda/`. It stops with a clear error if the NVIDIA driver cannot expose a CUDA GPU.

`LLAMA_CUDA_NGL=auto` and llama.cpp's `--fit` mode automatically keep as many layers as possible in VRAM while preserving the configured safety margin. Remaining model data stays memory-mapped from the GGUF files instead of requiring a second full copy in RAM. This is the GGUF/llama.cpp equivalent of AirLLM's layer-wise low-memory approach. Keep `--mmap` enabled for models close to or larger than available system RAM.

`LLAMA_FIT_TARGET_MIB` controls the VRAM safety margin. Increase it if other programs share the GPU. `LLAMA_FIT_MIN_CTX` is the minimum context size that automatic fitting may consider.

If `llama-server` fails at startup, the launcher no longer closes immediately. It keeps the terminal open, shows the last error lines, and saves logs in:

```text
logs/llama.cpp_LLM_server.log
logs/Search_Claw_Python_server.log
logs/Discord_bridge.log
logs/WhatsApp_bridge.log
```

Common `llama.cpp` startup errors are a wrong `.gguf` model path, a model that needs more RAM/VRAM, CUDA build problems, or port `8033` already being used.

The Python server runs inside `.venv`. When you press `Ctrl+C`, the launcher stops the llama.cpp server, Search Claw, Discord/WhatsApp, and deactivates the virtual environment.

On later runs, it asks whether to reuse the saved launcher config. Press Enter for yes. Choose `n` only when you want to change Discord/WhatsApp mode, model path, or the llama.cpp location.

## Troubleshooting

### Discord commands do not appear

Check these first:

- `DISCORD_CLIENT_ID` is the Application ID, not the public key.
- `DISCORD_TOKEN` is the bot token.
- The app has User Install enabled in the Installation page.
- User Install default settings include `applications.commands`.
- You installed the app to your user account using the Discord-provided install link.
- `REGISTER_COMMANDS=true` in `discord/.env`.
- Restart Discord or wait a few minutes for global commands to refresh.

### Discord says “The application did not respond”

Usually one of the services is not running.

Check:

- `llama-server` is running on port `8033`.
- `python3 searchClaw.py --server` is running on port `8811`.
- `node bridge.js` is running inside `discord/`.
- The `PY_AGENT_URL` and `LLAMA_BASE_URL` values are correct.

### `/chat` works but `/search` fails

That means the Discord bridge can reach the LLM, but not the Search Claw Python server.

Start it:

```bash
python3 searchClaw.py --server
```

Then test it:

```bash
curl http://127.0.0.1:8811/health
```

### `/search` works but `/chat` fails

That usually means the local LLM server is not reachable.

Test llama.cpp:

```bash
curl http://127.0.0.1:8033/v1/models
```

If it fails, restart `llama-server` and check the model path.

### CUDA out of memory

The launcher normally prevents this with `LLAMA_CUDA_NGL=auto` and `--fit`.

- Increase `LLAMA_FIT_TARGET_MIB`, for example to `2048`.
- Lower `LLAMA_CTX`, for example to `2048`.
- Stop other GPU applications.
- Use a smaller quantization or model if even the minimum working set cannot fit.

---

## Safety and reliability notes

Search Claw can search and summarize public web pages, but it is not a truth oracle. For high-stakes topics such as medical, legal, financial, political, or safety-critical questions, verify important claims against primary sources.

The included agent tries to cite sources, but web pages can be outdated, blocked, misleading, or unavailable. Treat the output as an assistant draft, not final proof.

Never commit your `.env` files. They contain secrets such as your Discord bot token.

---

## License

MIT
