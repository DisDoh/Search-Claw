#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"
DISCORD_ENV_FILE="$ROOT_DIR/discord/.env"
RUN_ENV_FILE="$ROOT_DIR/.run_all.env"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PIDS=()
PID_NAMES=()
PID_LOGS=()
LOG_DIR="$ROOT_DIR/logs"
CLEANING_UP=false

say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

pause_on_error() {
  local code="${1:-0}"
  if [[ "$code" != "0" && "$code" != "130" && "$code" != "143" ]]; then
    printf '\n'
    warn "The launcher stopped because of an error. The terminal will stay open so you can read it."
    if [[ -r /dev/tty ]]; then
      read -r -p "Press Enter to close..." _ < /dev/tty || true
    elif [[ -t 0 ]]; then
      read -r -p "Press Enter to close..." _ || true
    else
      warn "No interactive terminal was available for the pause prompt."
    fi
  fi
}

get_env_value() {
  local file="$1" key="$2" default="${3:-}"
  if [[ -f "$file" ]]; then
    local line
    line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
    if [[ -n "$line" ]]; then
      local value="${line#*=}"
      value="${value%\"}"; value="${value#\"}"
      value="${value%\'}"; value="${value#\'}"
      printf '%s' "$value"
      return 0
    fi
  fi
  printf '%s' "$default"
}

ask() {
  local label="$1" default="${2:-}" value
  read -r -p "$label [$default]: " value || true
  printf '%s' "${value:-$default}"
}

ask_discord_token() {
  local default="${1:-}" value
  echo "" >&2
  echo "Discord token input: paste or type the token normally, then press Enter." >&2
  echo "Tip: if your terminal blocks paste, open discord/.env after this script creates it and edit DISCORD_TOKEN there." >&2
  read -r -p "Discord bot token [$default]: " value || true
  printf '%s' "${value:-$default}"
}

ask_yes_no() {
  local label="$1" default="${2:-y}" value
  read -r -p "$label [$default]: " value || true
  value="${value:-$default}"
  [[ "$value" =~ ^[Yy] ]]
}

ask_mode() {
  local default_mode="${1:-both}" default_choice mode
  case "$default_mode" in
    discord) default_choice="1" ;;
    whatsapp) default_choice="2" ;;
    both|*) default_choice="3" ;;
  esac
  while true; do
    read -r -p "Launch what? 1=Discord, 2=WhatsApp, 3=Both [$default_choice]: " mode || true
    mode="${mode:-$default_choice}"
    case "$mode" in
      1|discord|Discord) echo "discord"; return 0 ;;
      2|whatsapp|WhatsApp) echo "whatsapp"; return 0 ;;
      3|both|Both) echo "both"; return 0 ;;
      *) echo "Please choose 1, 2, or 3. Press Enter to keep the default shown in brackets." ;;
    esac
  done
}

write_default_env() {
  local llama_dir="$1" model_path="$2" cuda="$3"
  local ngl="0"
  [[ "$cuda" == "true" ]] && ngl="999"
  cat > "$ENV_FILE" <<ENV
# Created by run_all.sh
LLAMA_BASE_URL=http://127.0.0.1:8033
LLAMA_MODEL=
LLAMA_TEMPERATURE=0.7
LLAMA_TOP_P=0.9
LLAMA_MAX_TOKENS=900

# llama.cpp launcher config
LLAMA_CPP_DIR=$llama_dir
MODEL_PATH=$model_path
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8033
LLAMA_CTX=4096
LLAMA_NGL=$ngl
LLAMA_BUILD_CUDA=$cuda

SEARCH_CLAW_HOST=127.0.0.1
SEARCH_CLAW_PORT=8811
REQUIRE_SOURCES=true

PY_AGENT_URL=http://127.0.0.1:8811/message
MAX_OPEN_PAGES=5
CHAT_HISTORY_LIMIT=6

ENABLE_CHAT_PREFIX=true
CHAT_PREFIX=!
ENABLE_SEARCH_PREFIX=true
SEARCH_PREFIX=?
IGNORE_FROM_ME=false
IGNORE_GROUPS=true
WHATSAPP_MAX_CHARS=3500
ENV
}

write_discord_env() {
  local token="$1" client_id="$2"
  cat > "$DISCORD_ENV_FILE" <<ENV
# Created by run_all.sh
DISCORD_TOKEN=$token
DISCORD_CLIENT_ID=$client_id

PY_AGENT_URL=http://127.0.0.1:8811/message
MAX_OPEN_PAGES=5
CHAT_HISTORY_LIMIT=6

LLAMA_BASE_URL=http://127.0.0.1:8033
LLAMA_MODEL=
LLAMA_TEMPERATURE=0.7
LLAMA_TOP_P=0.9
LLAMA_MAX_TOKENS=900

DISCORD_MAX_CHARS=1900
AXIOS_TIMEOUT=180000
REGISTER_COMMANDS=true
ENV
}

write_run_env() {
  local mode="$1"
  cat > "$RUN_ENV_FILE" <<ENV
# Created by run_all.sh
LAUNCH_MODE=$mode
ENV
}


fix_discord_token_prefix() {
  [[ -f "$DISCORD_ENV_FILE" ]] || return 0

  local tmp="$DISCORD_ENV_FILE.tmp" fixed="false" line
  : > "$tmp"
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^DISCORD_TOKEN= ]]; then
      if [[ "$fixed" == "false" && -n "${line#DISCORD_TOKEN=}" ]]; then
        printf '%s\n' "$line" >> "$tmp"
        fixed="true"
      elif [[ "$fixed" == "true" ]]; then
        printf '%s\n' "$line" >> "$tmp"
      fi
    elif [[ "$fixed" == "false" && -n "$line" && ! "$line" =~ ^[[:space:]]*# && ! "$line" == *=* && ! "$line" =~ [[:space:]] ]]; then
      printf 'DISCORD_TOKEN=%s\n' "$line" >> "$tmp"
      fixed="true"
    elif [[ "$fixed" == "false" && "$line" =~ ^(Discord token input:|Tip:|Discord bot token ) ]]; then
      continue
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$DISCORD_ENV_FILE"
  mv "$tmp" "$DISCORD_ENV_FILE"
}

load_env() {
  local file="$1"
  [[ -f "$file" ]] || return 0

  # Do not `source` .env files. A token or a pasted note with spaces can make Bash
  # try to execute text like "Discord ..." as a command. This parser only exports
  # clean KEY=value lines and safely ignores comments / accidental text.
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    value="${value#\"}"; value="${value%\"}"
    value="${value#'}"; value="${value%'}"
    export "$key=$value"
  done < "$file"
}

configure_minimal() {
  say "Minimal Search Claw setup"
  local mode token client_id old_token old_client_id
  local llama_dir model_path cuda_choice use_cuda

  local saved_mode cuda_default saved_cuda
  saved_mode="$(get_env_value "$RUN_ENV_FILE" LAUNCH_MODE "both")"
  mode="$(ask_mode "$saved_mode")"

  llama_dir="$(ask "llama.cpp install folder" "$(get_env_value "$ENV_FILE" LLAMA_CPP_DIR "$HOME/llama.cpp")")"
  model_path="$(ask "GGUF model path" "$(get_env_value "$ENV_FILE" MODEL_PATH "$HOME/models/model.gguf")")"

  saved_cuda="$(get_env_value "$ENV_FILE" LLAMA_BUILD_CUDA "false")"
  cuda_default="n"
  [[ "$saved_cuda" == "true" ]] && cuda_default="y"
  use_cuda="false"
  if ask_yes_no "Compile llama.cpp with CUDA?" "$cuda_default"; then
    use_cuda="true"
  fi

  write_default_env "$llama_dir" "$model_path" "$use_cuda"

  if [[ "$mode" == "discord" || "$mode" == "both" ]]; then
    old_token="$(get_env_value "$DISCORD_ENV_FILE" DISCORD_TOKEN "put_your_discord_bot_token_here")"
    old_client_id="$(get_env_value "$DISCORD_ENV_FILE" DISCORD_CLIENT_ID "put_your_discord_application_client_id_here")"
    token="$(ask_discord_token "$old_token")"
    client_id="$(ask "Discord Application ID / Client ID" "$old_client_id")"
    write_discord_env "$token" "$client_id"

    if [[ "$token" == "put_your_discord_bot_token_here" || -z "$token" ]]; then
      warn "Discord token is still empty/placeholder. Edit $DISCORD_ENV_FILE and set DISCORD_TOKEN before Discord can start."
    fi
    if [[ "$client_id" == "put_your_discord_application_client_id_here" || -z "$client_id" ]]; then
      warn "Discord Client ID is still empty/placeholder. Edit $DISCORD_ENV_FILE and set DISCORD_CLIENT_ID before command registration can work."
    fi
  fi

  write_run_env "$mode"
  echo "Saved config. To change it later, delete .run_all.env or edit .env / discord/.env."
}

setup_python() {
  say "Python virtual environment"
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python not found. Install Python 3.10+ or set PYTHON_BIN=/path/to/python."
  [[ -d "$VENV_DIR" ]] || "$PYTHON_BIN" -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install -r "$ROOT_DIR/requirements.txt"
  deactivate || true
}

setup_node() {
  local dir="$1" name="$2"
  command -v npm >/dev/null 2>&1 || fail "npm not found. Install Node.js LTS first."
  if [[ ! -d "$dir/node_modules" ]]; then
    say "Installing $name dependencies"
    (cd "$dir" && npm install)
  fi
}

setup_llama_cpp() {
  say "llama.cpp"
  local llama_dir="${LLAMA_CPP_DIR:-$HOME/llama.cpp}"
  local build_cuda="${LLAMA_BUILD_CUDA:-false}"
  local cuda_flag="OFF"
  [[ "$build_cuda" == "true" ]] && cuda_flag="ON"

  command -v git >/dev/null 2>&1 || fail "git not found. Install git first."
  command -v cmake >/dev/null 2>&1 || fail "cmake not found. Install cmake first."

  if [[ ! -d "$llama_dir/.git" ]]; then
    warn "llama.cpp not found in $llama_dir. Installing it there..."
    mkdir -p "$(dirname "$llama_dir")"
    git clone https://github.com/ggml-org/llama.cpp.git "$llama_dir"
  else
    echo "Found llama.cpp in $llama_dir"
  fi

  if [[ ! -x "$llama_dir/build/bin/llama-server" ]]; then
    say "Compiling llama.cpp ($([[ "$cuda_flag" == "ON" ]] && echo CUDA || echo CPU))"
    cmake -S "$llama_dir" -B "$llama_dir/build" -DGGML_CUDA="$cuda_flag"
    cmake --build "$llama_dir/build" --config Release -j "$(nproc 2>/dev/null || echo 4)"
  fi

  [[ -x "$llama_dir/build/bin/llama-server" ]] || fail "llama-server was not built at $llama_dir/build/bin/llama-server"
  [[ -f "${MODEL_PATH:-}" ]] || fail "GGUF model not found: ${MODEL_PATH:-empty}. Edit MODEL_PATH in .env or run this script again and enter the correct path."
}

pid_command() {
  ps -p "$1" -o command= 2>/dev/null || true
}

stop_stale_pid() {
  local pid="$1" reason="$2" pgid
  [[ -n "$pid" && "$pid" != "$$" ]] || return 0
  kill -0 "$pid" >/dev/null 2>&1 || return 0

  warn "Stopping old Search Claw process $pid ($reason)"
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  if [[ -n "$pgid" && "$pgid" != "$$" && "$pgid" != "$(ps -o pgid= -p "$$" | tr -d ' ')" ]]; then
    kill -TERM -- "-$pgid" >/dev/null 2>&1 || true
  else
    kill -TERM "$pid" >/dev/null 2>&1 || true
  fi

  local i
  for i in {1..20}; do
    kill -0 "$pid" >/dev/null 2>&1 || return 0
    sleep 0.2
  done

  if [[ -n "$pgid" && "$pgid" != "$$" && "$pgid" != "$(ps -o pgid= -p "$$" | tr -d ' ')" ]]; then
    kill -KILL -- "-$pgid" >/dev/null 2>&1 || true
  else
    kill -KILL "$pid" >/dev/null 2>&1 || true
  fi
}

stop_matching_pids() {
  local pattern="$1" reason="$2" pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    stop_stale_pid "$pid" "$reason"
  done < <(pgrep -f "$pattern" 2>/dev/null || true)
}

port_pids() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

ensure_port_available() {
  local port="$1" service="$2" pid cmd
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    cmd="$(pid_command "$pid")"
    case "$service" in
      llama)
        if [[ "$cmd" == *"llama-server"* && ( "$cmd" == *"--port ${LLAMA_PORT:-8033}"* || "$cmd" == *"${MODEL_PATH:-}"* ) ]]; then
          stop_stale_pid "$pid" "old llama.cpp server on port $port"
        else
          fail "Port $port is already in use by PID $pid: $cmd"
        fi
        ;;
      search)
        if [[ "$cmd" == *"$ROOT_DIR/searchClaw.py"* || "$cmd" == *"searchClaw.py --server"* ]]; then
          stop_stale_pid "$pid" "old Search Claw Python server on port $port"
        else
          fail "Port $port is already in use by PID $pid: $cmd"
        fi
        ;;
      *)
        fail "Internal launcher error: unknown service '$service'."
        ;;
    esac
  done < <(port_pids "$port")
}

cleanup_stale_search_claw_processes() {
  say "Checking for old Search Claw processes"
  command -v lsof >/dev/null 2>&1 || fail "lsof not found. Install lsof so the launcher can check occupied ports."
  command -v pgrep >/dev/null 2>&1 || fail "pgrep not found. Install procps so the launcher can find old bridge/browser processes."
  ensure_port_available "${LLAMA_PORT:-8033}" "llama"
  ensure_port_available "${SEARCH_CLAW_PORT:-8811}" "search"
  stop_matching_pids "$ROOT_DIR/discord/bridge\\.js" "old Discord bridge"
  stop_matching_pids "$ROOT_DIR/whatsapp/bridge\\.js" "old WhatsApp bridge"
  stop_matching_pids "$ROOT_DIR/whatsapp/\\.wwebjs_auth/session-searchclaw-whatsapp" "old WhatsApp browser session"
}

safe_log_name() {
  printf '%s' "$1" | tr ' /' '__' | tr -cd 'A-Za-z0-9_.-'
}

start_process() {
  local name="$1" cmd="$2" mode="${3:-visible}"
  mkdir -p "$LOG_DIR"
  local log_file="$LOG_DIR/$(safe_log_name "$name").log"
  say "Starting $name"
  echo "Log: $log_file"
  if command -v setsid >/dev/null 2>&1; then
    if [[ "$mode" == "log_only" ]]; then
      : > "$log_file"
      setsid bash -lc "$cmd" >> "$log_file" 2>&1 &
    else
      # Visible mode keeps QR codes and bridge output in the terminal, while also saving logs.
      setsid bash -c 'set -o pipefail; bash -lc "$1" 2>&1 | tee -a "$2"' _ "$cmd" "$log_file" &
    fi
  elif [[ "$mode" == "log_only" ]]; then
    : > "$log_file"
    bash -lc "$cmd" >> "$log_file" 2>&1 &
  else
    # Visible mode keeps QR codes and bridge output in the terminal, while also saving logs.
    (set -o pipefail; bash -lc "$cmd" 2>&1 | tee -a "$log_file") &
  fi
  local pid=$!
  PIDS+=("$pid")
  PID_NAMES+=("$name")
  PID_LOGS+=("$log_file")
  echo "$name PID: $pid"
}

stop_process_group() {
  local pid="$1"
  if kill -0 -- "-$pid" >/dev/null 2>&1; then
    kill -TERM -- "-$pid" >/dev/null 2>&1 || true
  else
    kill -TERM "$pid" >/dev/null 2>&1 || true
  fi
}

check_process_alive() {
  local index="$1"
  [[ -n "${PIDS[$index]:-}" ]] || fail "Internal launcher error: no process recorded at index $index."
  local pid="${PIDS[$index]}"
  local name="${PID_NAMES[$index]}"
  local log="${PID_LOGS[$index]}"
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    warn "$name stopped immediately. Showing the last log lines:"
    echo "----- $log -----"
    tail -n 80 "$log" 2>/dev/null || true
    echo "----------------"
    fail "$name failed to start. Fix the error above and run ./run_all.sh again."
  fi
}

wait_for_llama_ready() {
  local base_url="http://${LLAMA_HOST:-127.0.0.1}:${LLAMA_PORT:-8033}"
  local timeout="${LLAMA_READY_TIMEOUT:-300}"
  local waited=0
  command -v curl >/dev/null 2>&1 || fail "curl not found. Install curl so the launcher can wait for llama.cpp readiness."
  say "Waiting for llama.cpp to finish loading"

  while (( waited < timeout )); do
    check_process_alive 0

    if curl --fail --silent --max-time 2 "$base_url/health" >/dev/null 2>&1; then
      echo "llama.cpp is ready at $base_url"
      return 0
    fi
    if curl --fail --silent --max-time 2 "$base_url/v1/models" >/dev/null 2>&1; then
      echo "llama.cpp is ready at $base_url"
      return 0
    fi

    sleep 2
    waited=$((waited + 2))
    if (( waited % 20 == 0 )); then
      echo "Still waiting for llama.cpp... ${waited}s/${timeout}s"
    fi
  done

  warn "llama.cpp did not become ready within ${timeout}s. Showing the last log lines:"
  echo "----- ${PID_LOGS[0]} -----"
  tail -n 120 "${PID_LOGS[0]}" 2>/dev/null || true
  echo "----------------"
  fail "llama.cpp is running but not ready. Try increasing LLAMA_READY_TIMEOUT in .env if your model loads slowly."
}

monitor_processes() {
  while true; do
    sleep 2
    local i pid name log
    for i in "${!PIDS[@]}"; do
      pid="${PIDS[$i]}"
      name="${PID_NAMES[$i]}"
      log="${PID_LOGS[$i]}"
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        warn "$name stopped. Showing the last log lines:"
        echo "----- $log -----"
        tail -n 80 "$log" 2>/dev/null || true
        echo "----------------"
        fail "$name stopped unexpectedly."
      fi
    done
  done
}

check_all_processes_alive() {
  local i
  for i in "${!PIDS[@]}"; do
    check_process_alive "$i"
  done
}

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [[ "$CLEANING_UP" == "true" ]]; then
    exit "$code"
  fi
  CLEANING_UP=true
  printf '\n'
  warn "Stopping services..."
  for pid in "${PIDS[@]:-}"; do
    stop_process_group "$pid"
  done
  sleep 1
  for pid in "${PIDS[@]:-}"; do
    if kill -0 -- "-$pid" >/dev/null 2>&1; then
      kill -KILL -- "-$pid" >/dev/null 2>&1 || true
    elif kill -0 "$pid" >/dev/null 2>&1; then
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait >/dev/null 2>&1 || true
  if declare -F deactivate >/dev/null 2>&1; then deactivate || true; fi
  warn "Closed. Python virtual environment deactivated."
  pause_on_error "$code"
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT_DIR"

if [[ ! -f "$RUN_ENV_FILE" ]]; then
  configure_minimal
else
  read -r -p "Use saved launcher config? [Y/n]: " reuse || true
  if [[ "${reuse:-Y}" =~ ^[Nn] ]]; then
    configure_minimal
  fi
fi

setup_python
fix_discord_token_prefix
load_env "$ENV_FILE"
load_env "$RUN_ENV_FILE"
setup_llama_cpp
cleanup_stale_search_claw_processes

case "${LAUNCH_MODE:-both}" in
  discord)
    fix_discord_token_prefix
    load_env "$DISCORD_ENV_FILE"
    if [[ "${DISCORD_TOKEN:-}" == "put_your_discord_bot_token_here" || -z "${DISCORD_TOKEN:-}" ]]; then
      fail "Discord token missing. Edit $DISCORD_ENV_FILE and set DISCORD_TOKEN, then run ./run_all.sh again."
    fi
    setup_node "$ROOT_DIR/discord" "Discord"
    ;;
  whatsapp)
    setup_node "$ROOT_DIR/whatsapp" "WhatsApp"
    ;;
  both)
    fix_discord_token_prefix
    load_env "$DISCORD_ENV_FILE"
    if [[ "${DISCORD_TOKEN:-}" == "put_your_discord_bot_token_here" || -z "${DISCORD_TOKEN:-}" ]]; then
      fail "Discord token missing. Edit $DISCORD_ENV_FILE and set DISCORD_TOKEN, then run ./run_all.sh again."
    fi
    setup_node "$ROOT_DIR/discord" "Discord"
    setup_node "$ROOT_DIR/whatsapp" "WhatsApp"
    ;;
  *) fail "Unknown LAUNCH_MODE in .run_all.env. Delete it and run again." ;;
esac

start_process "llama.cpp LLM server" "'$LLAMA_CPP_DIR/build/bin/llama-server' -m '$MODEL_PATH' -c '${LLAMA_CTX:-4096}' -ngl '${LLAMA_NGL:-0}' --host '${LLAMA_HOST:-127.0.0.1}' --port '${LLAMA_PORT:-8033}'" "log_only"
wait_for_llama_ready

start_process "Search Claw Python server" "cd '$ROOT_DIR' && source '$VENV_DIR/bin/activate' && set -a && source '$ENV_FILE' && set +a && python searchClaw.py --server --host '${SEARCH_CLAW_HOST:-127.0.0.1}' --port '${SEARCH_CLAW_PORT:-8811}'; deactivate || true"

case "${LAUNCH_MODE:-both}" in
  discord)
    start_process "Discord bridge" "cd '$ROOT_DIR/discord' && node bridge.js"
    ;;
  whatsapp)
    start_process "WhatsApp bridge" "cd '$ROOT_DIR/whatsapp' && set -a && source '$ENV_FILE' && set +a && node bridge.js"
    ;;
  both)
    start_process "Discord bridge" "cd '$ROOT_DIR/discord' && node bridge.js"
    start_process "WhatsApp bridge" "cd '$ROOT_DIR/whatsapp' && set -a && source '$ENV_FILE' && set +a && node bridge.js"
    ;;
esac

sleep 3
check_all_processes_alive

say "Running. Press Ctrl+C to stop everything."
monitor_processes
