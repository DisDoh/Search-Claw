# Search Claw

Search Claw is a local web-search assistant that can run from the terminal, as a small HTTP server, or through WhatsApp. It searches the web, fetches result pages, and asks a local OpenAI-compatible LLM server, such as `llama.cpp`, to answer with sources.

The default setup uses:

- Python for the Search Claw agent
- DuckDuckGo HTML search as the search backend
- `llama.cpp` server as the local model API
- `whatsapp-web.js` as the WhatsApp bridge

> This project is designed for personal experimentation. Respect website terms, rate limits, WhatsApp policies, copyright, and local laws before using it commercially.

---

## Features

- Command-line search assistant
- HTTP server endpoint for app or WhatsApp integrations
- WhatsApp bridge with prefix-based commands
- Ignores group chats by default
- Works with a local `llama.cpp` OpenAI-compatible server
- Simple source validation to reduce unsourced answers
- Debug and verbose modes for troubleshooting

---

## Project structure

```text
search-claw/
├── README.md
├── LICENSE
├── requirements.txt
├── .env.example
├── searchClaw.py
├── system_prompt.txt
├── whatsapp/
│   ├── bridge.js
│   └── package.json
└── scripts/
    ├── run_all.sh
    └── start_llama_example.sh
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

### Node.js for WhatsApp bridge

- Node.js 18+
- npm

Install:

```bash
cd search-claw/whatsapp
npm install
```

### Local LLM server

Search Claw expects an OpenAI-compatible chat completions endpoint at:

```text
http://127.0.0.1:8033/v1/chat/completions
```

A typical `llama.cpp` server command looks like this:

```bash
./llama.cpp/build/bin/llama-server \
  -m /home/disd/models/ColdBrew-Lucid.Q6_K.gguf \
  -c 4096 \
  -ngl 999 \
  --host 127.0.0.1 \
  --port 8033
```

If your GPU runs out of VRAM, lower `-ngl`, use a smaller quantization such as `Q4_K_M`, or run on CPU.

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

`LLAMA_MODEL` can stay empty for many `llama.cpp` setups, but some servers require a model name.

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
  -d '{"message":"What is Bluetooth Poker 8?"}'
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

### 3. WhatsApp bridge

Start the Search Claw server first:

```bash
python3 searchClaw.py --server
```

Then start the WhatsApp bridge:

```bash
cd whatsapp
node bridge.js
```

Scan the QR code in the terminal with WhatsApp.

By default:

- Everyone can use the bot
- Group chats are ignored
- Messages must start with `!`

Example WhatsApp message:

```text
! who is the president of France?
```

---

## Run everything in terminals

Edit the model path in `scripts/run_all.sh`, then run:

```bash
chmod +x scripts/run_all.sh scripts/start_llama_example.sh
./scripts/run_all.sh
```

This opens separate terminal windows for:

1. `llama.cpp` server
2. WhatsApp bridge
3. Search Claw HTTP server

---

## Safety and reliability notes

Search Claw can search and summarize public web pages, but it is not a truth oracle. For high-stakes topics such as medical, legal, financial, political, or safety-critical questions, verify important claims against primary sources.

The included agent tries to cite sources, but web pages can be outdated, blocked, misleading, or unavailable. Treat the output as an assistant draft, not final proof.

---

## Commercial use checklist

Before selling or deploying a Search Claw-based service, check:

- WhatsApp Business Platform and WhatsApp terms
- Website scraping rules and robots policies
- Privacy and data protection obligations, especially in the EU/Switzerland
- Logging and user consent
- Abuse prevention and rate limiting
- Clear disclaimers for generated answers
- Model licensing and dependency licenses

---

## Troubleshooting

### `cudaMalloc failed: out of memory`

Use a smaller model, smaller quantization, lower context size, or reduce GPU layers:

```bash
-ngl 20
```

or CPU-only:

```bash
-ngl 0
```

### WhatsApp replies twice

The bridge includes deduplication, but if it still happens, make sure you are not running two bridge processes at the same time.

### No answer from local model

Check that the LLM server is running:

```bash
curl http://127.0.0.1:8033/v1/models
```

Then test Search Claw directly:

```bash
python3 searchClaw.py "test" --debug --verbose
```

### Search returns weak results

DuckDuckGo HTML can occasionally block, rate limit, or return thin results. Wait, reduce frequency, or plug in a proper search API.

---

## License

MIT License. See `LICENSE`.
