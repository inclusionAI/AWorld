#!/usr/bin/env bash
# Start or stop builtin tool servers (filesystem, terminal) under tool_servers/.
# Usage: $0 [start|stop]   (default: start)
# Requires: uv, env vars AWORLD_FILESYSTEM_ENDPOINT, AWORLD_TERMINAL_ENDPOINT (optional: TOKEN, WORKSPACE).
# If endpoint vars are not set, exports defaults for local: http://localhost:8084, http://localhost:8081.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOGS_DIR"
TOOL_SERVERS_ROOT="$(cd "$SCRIPT_DIR/../tool_servers" && pwd)"

# 从脚本目录起向上最多 3 层查找 .env 并加载（项目根 .env 可被读到）
_env_dir="$SCRIPT_DIR"
for _ in 0 1 2 3; do
  if [[ -f "$_env_dir/.env" ]]; then
    echo "[env] Loading $_env_dir/.env"
    set -a
    # shellcheck source=/dev/null
    source "$_env_dir/.env"
    set +a
    break
  fi
  _env_dir="$(cd "$_env_dir/.." && pwd)"
  [[ -d "$_env_dir" ]] || break
done
unset _env_dir
FILESYSTEM_DIR="$TOOL_SERVERS_ROOT/filesystem"
TERMINAL_DIR="$TOOL_SERVERS_ROOT/terminal"

# Default ports (must match FastMCP port in each server)
FILESYSTEM_PORT="${FILESYSTEM_PORT:-8084}"
TERMINAL_PORT="${TERMINAL_PORT:-8081}"

# Parse action: start | stop (default start)
ACTION="${1:-start}"
case "$ACTION" in
  start|stop) ;;
  *)
    echo "Usage: $0 [start|stop]"
    echo "  start  - start all tool servers (restart if already running)"
    echo "  stop   - stop all running tool servers"
    exit 1
    ;;
esac

# Kill process(es) listening on given port
stop_port() {
  local port=$1
  local name=${2:-port $port}
  if ! command -v lsof &>/dev/null; then
    echo "⚠️  lsof not found, cannot stop $name"
    return 1
  fi
  local pids
  pids=$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -z "$pids" ]]; then
    echo "[stop] $name: no process on port $port"
    return 0
  fi
  for pid in $pids; do
    kill "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
    echo "[stop] $name: killed pid $pid (port $port)"
  done
  return 0
}

# Stop both tool servers (by port)
stop_services() {
  echo "Stopping tool servers..."
  stop_port "$FILESYSTEM_PORT" "filesystem"
  stop_port "$TERMINAL_PORT" "terminal"
  # give processes time to release ports
  sleep 1
  echo "Stopped."
}

if [[ "$ACTION" == "stop" ]]; then
  stop_services
  exit 0
fi

# ---------- start ----------
# Restart: stop any existing service on our ports first
stop_services

# Validate / set endpoint env (URL and TOKEN)
check_and_export() {
  local name=$1
  local default_url=$2
  local env_url_var=$3
  local env_token_var=$4

  if [[ -z "${!env_url_var}" ]]; then
    export "$env_url_var=$default_url"
    echo "[env] $env_url_var not set, using default: $default_url"
  else
    echo "[env] $env_url_var=${!env_url_var}"
  fi

  if [[ -n "$env_token_var" ]]; then
    if [[ -z "${!env_token_var}" ]]; then
      echo "[env] $env_token_var not set (optional)"
    else
      echo "[env] $env_token_var is set"
    fi
  fi
}

check_and_export "filesystem" "http://127.0.0.1:$FILESYSTEM_PORT" "AWORLD_FILESYSTEM_ENDPOINT" "AWORLD_FILESYSTEM_TOKEN"
check_and_export "terminal"   "http://127.0.0.1:$TERMINAL_PORT"   "AWORLD_TERMINAL_ENDPOINT"   "AWORLD_TERMINAL_TOKEN"

if [[ -z "${AWORLD_WORKSPACE:-}" ]]; then
  export AWORLD_WORKSPACE="${HOME}/workspace"
  echo "[env] AWORLD_WORKSPACE not set, using default: $AWORLD_WORKSPACE"
fi

if ! command -v uv &>/dev/null; then
  echo "❌ uv not found. Install uv first."
  exit 1
fi

# Start filesystem
if [[ ! -f "$FILESYSTEM_DIR/src/main.py" ]]; then
  echo "❌ filesystem: $FILESYSTEM_DIR/src/main.py not found"
else
  (cd "$FILESYSTEM_DIR" && uv run python src/main.py >> "$LOGS_DIR/aworld-filesystem.log" 2>&1) &
  echo "Started filesystem (pid $!) -> $AWORLD_FILESYSTEM_ENDPOINT"
fi

# Start terminal
if [[ ! -f "$TERMINAL_DIR/src/terminal.py" ]]; then
  echo "❌ terminal: $TERMINAL_DIR/src/terminal.py not found"
else
  (cd "$TERMINAL_DIR" && uv run python src/terminal.py >> "$LOGS_DIR/aworld-terminal.log" 2>&1) &
  echo "Started terminal (pid $!) -> $AWORLD_TERMINAL_ENDPOINT"
fi

sleep 2

# Check ports (success = process listening)
ok=0
if command -v lsof &>/dev/null; then
  if lsof -i ":$FILESYSTEM_PORT" -sTCP:LISTEN -t &>/dev/null; then
    echo "✅ filesystem: listening on port $FILESYSTEM_PORT"
    ((ok+=1))
  else
    echo "❌ filesystem: not listening on port $FILESYSTEM_PORT (see $LOGS_DIR/aworld-filesystem.log)"
  fi
  if lsof -i ":$TERMINAL_PORT" -sTCP:LISTEN -t &>/dev/null; then
    echo "✅ terminal: listening on port $TERMINAL_PORT"
    ((ok+=1))
  else
    echo "❌ terminal: not listening on port $TERMINAL_PORT (see $LOGS_DIR/aworld-terminal.log)"
  fi
else
  echo "⚠️  lsof not found, skip port check. Assume started."
  ok=2
fi

echo ""
if [[ $ok -eq 2 ]]; then
  echo "All tool servers started. Sandbox can use:"
  echo "  export AWORLD_FILESYSTEM_ENDPOINT=$AWORLD_FILESYSTEM_ENDPOINT"
  echo "  export AWORLD_TERMINAL_ENDPOINT=$AWORLD_TERMINAL_ENDPOINT"
  exit 0
else
  echo "Some servers failed to start. Check $LOGS_DIR/aworld-*.log"
  exit 1
fi
