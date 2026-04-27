#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

open_terminal() {
  local title="$1"
  local command="$2"

  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title="$title" -- bash -lc "$command; echo; echo '[done] Press Enter to close.'; read"
  elif command -v konsole >/dev/null 2>&1; then
    konsole --new-tab --title "$title" -e bash -lc "$command; echo; echo '[done] Press Enter to close.'; read"
  elif command -v xterm >/dev/null 2>&1; then
    xterm -T "$title" -e bash -lc "$command; echo; echo '[done] Press Enter to close.'; read" &
  else
    echo "No supported terminal found. Run these manually:"
    echo "$command"
  fi
}

open_terminal "llama.cpp server" "cd '$ROOT_DIR' && ./scripts/start_llama_example.sh"
open_terminal "Search Claw server" "cd '$ROOT_DIR' && python3 searchClaw.py --server"
open_terminal "WhatsApp bridge" "cd '$ROOT_DIR/whatsapp' && npm install && node bridge.js"
