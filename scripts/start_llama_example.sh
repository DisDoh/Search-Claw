#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${MODEL_PATH:-/home/disd/models/ColdBrew-Lucid.Q6_K.gguf}"
LLAMA_BIN="${LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
PORT="${PORT:-8033}"
HOST="${HOST:-127.0.0.1}"
CTX="${CTX:-4096}"
NGL="${NGL:-999}"

exec "$LLAMA_BIN" \
  -m "$MODEL_PATH" \
  -c "$CTX" \
  -ngl "$NGL" \
  --host "$HOST" \
  --port "$PORT"
