#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${AWORLD_GATEWAY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="${AWORLD_GATEWAY_LAUNCHD_LOG_DIR:-$HOME/Documents/logs}"
OUT_LOG="${AWORLD_GATEWAY_LAUNCHD_OUT_LOG:-$LOG_DIR/aworld-gateway.launchd.out.log}"
ERR_LOG="${AWORLD_GATEWAY_LAUNCHD_ERR_LOG:-$LOG_DIR/aworld-gateway.launchd.err.log}"
MAX_BYTES="${AWORLD_GATEWAY_LAUNCHD_LOG_MAX_BYTES:-16777216}"
BACKUPS="${AWORLD_GATEWAY_LAUNCHD_LOG_BACKUPS:-5}"

file_size_bytes() {
  local file="$1"
  if stat -f %z "$file" >/dev/null 2>&1; then
    stat -f %z "$file"
    return
  fi
  stat -c %s "$file"
}

rotate_log_if_needed() {
  local file="$1"
  local size
  local index

  [[ -f "$file" ]] || return 0
  size="$(file_size_bytes "$file")"
  [[ "$size" =~ ^[0-9]+$ ]] || return 0
  (( size >= MAX_BYTES )) || return 0

  for ((index = BACKUPS; index >= 1; index--)); do
    if (( index == BACKUPS )); then
      rm -f "$file.$index"
    elif [[ -f "$file.$index" ]]; then
      mv "$file.$index" "$file.$((index + 1))"
    fi
  done

  mv "$file" "$file.1"
}

mkdir -p "$LOG_DIR" "$ROOT_DIR/logs"
rotate_log_if_needed "$OUT_LOG"
rotate_log_if_needed "$ERR_LOG"
exec >>"$OUT_LOG" 2>>"$ERR_LOG"

export AWORLD_DISABLE_CONSOLE_LOG="${AWORLD_DISABLE_CONSOLE_LOG:-true}"
export AWORLD_GATEWAY_CONSOLE_LOG="${AWORLD_GATEWAY_CONSOLE_LOG:-false}"
export AWORLD_GATEWAY_UVICORN_LOG_LEVEL="${AWORLD_GATEWAY_UVICORN_LOG_LEVEL:-warning}"
export AWORLD_LOG_PATH="${AWORLD_LOG_PATH:-$ROOT_DIR/logs}"
export AWORLD_GATEWAY_LOG_PATH="${AWORLD_GATEWAY_LOG_PATH:-$ROOT_DIR/logs/gateway.log}"
export PYTHONPATH="$ROOT_DIR/aworld-cli/src:$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT_DIR"

if command -v aworld-cli >/dev/null 2>&1; then
  exec aworld-cli gateway server "$@"
fi

exec "${PYTHON:-python3}" -m aworld_cli.main gateway server "$@"
