#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9222}"
TARGET_URL="${2:-https://x.com/i/flow/login}"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_ROOT="${TMPDIR:-/tmp}"
RUNTIME_DIR="${RUNTIME_ROOT%/}/last_7_days_news_runtime"
PROFILE_DIR="$RUNTIME_DIR/chrome-profile"
AGENT_BROWSER="/usr/local/bin/agent-browser"

mkdir -p "$RUNTIME_DIR"

if [[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]]; then
  CHROME_APP="/Applications/Google Chrome.app"
  CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else
  echo "Google Chrome was not found at the expected executable path." >&2
  exit 1
fi

if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use. Reusing the existing CDP browser session."
else
  OPEN_FAILED=0
  open -na "$CHROME_APP" --args \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE_DIR" \
    --no-first-run \
    --no-default-browser-check \
    --new-window \
    "https://x.com" || OPEN_FAILED=1

  if [[ "$OPEN_FAILED" -eq 1 ]]; then
    nohup "$CHROME_BIN" \
      --remote-debugging-port="$PORT" \
      --user-data-dir="$PROFILE_DIR" \
      --no-first-run \
      --no-default-browser-check \
      --new-window \
      "https://x.com" >/dev/null 2>&1 &
  fi

  for _ in $(seq 1 30); do
    if curl -s "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if ! curl -s "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "Chrome opened, but CDP port $PORT is not ready yet." >&2
  echo "Check whether a system prompt blocked the new instance, or retry with a different port." >&2
  exit 1
fi

if [[ -x "$AGENT_BROWSER" ]]; then
  "$AGENT_BROWSER" --cdp "$PORT" open "$TARGET_URL" >/dev/null 2>&1 || true
  "$AGENT_BROWSER" --cdp "$PORT" wait 2000 >/dev/null 2>&1 || true
fi

echo "The CDP browser is ready."
echo "Port: $PORT"
echo "Profile: $PROFILE_DIR"
echo "Next steps:"
echo "1. Log in to x.com in the browser window that just opened."
echo "2. After login completes, run: python3 scripts/export_x_cookies.py --port $PORT"
