#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COOKIE_FILE="/tmp/last_7_days_news_x_cookie.txt"
PORT="9222"
FORCE_REFRESH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --force)
      FORCE_REFRESH=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: bash scripts/ensure_x_cookies.sh [--port 9222] [--force]" >&2
      exit 1
      ;;
  esac
done

has_required_cookies() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  grep -q 'auth_token=' "$path" && grep -q 'ct0=' "$path"
}

reuse_cookie_if_valid() {
  local path="$1"
  echo "Found candidate cookie file: $path"
  if python3 "$SCRIPT_DIR/validate_x_cookies.py" --cookie-file "$path"; then
    if [[ "$path" != "$COOKIE_FILE" ]]; then
      cp "$path" "$COOKIE_FILE"
      echo "Reused a valid cookie and synced it to: $COOKIE_FILE"
    fi
    echo "If you want to force a fresh login, run: bash scripts/ensure_x_cookies.sh --force"
    exit 0
  fi
  echo "The candidate cookie is invalid. Continuing with the refresh flow."
}

if [[ "$FORCE_REFRESH" -eq 0 ]]; then
  if has_required_cookies "$COOKIE_FILE"; then
    reuse_cookie_if_valid "$COOKIE_FILE"
  fi
  echo "No reusable valid X cookie was found. Starting the refresh flow."
fi

if [[ "$FORCE_REFRESH" -eq 1 ]]; then
  echo "Force refresh enabled. A new browser session will be opened and cookies will be exported again."
else
  echo "No usable X cookie was found. Preparing the login flow."
fi

bash "$SCRIPT_DIR/open_x_login_cdp.sh" "$PORT"

if [[ -t 0 ]]; then
  while true; do
    printf "Complete the x.com login in the browser window that just opened, then press Enter to continue: "
    read -r _
    if python3 "$SCRIPT_DIR/export_x_cookies.py" --port "$PORT"; then
      if python3 "$SCRIPT_DIR/validate_x_cookies.py" --cookie-file "$COOKIE_FILE"; then
        echo "The X cookie was refreshed successfully and passed active validation."
        exit 0
      fi
      echo "The cookie was exported, but active validation failed. Confirm that the current account is fully logged in to x.com."
    fi
    echo "The cookie export did not succeed yet. Make sure x.com login is complete, then press Enter to retry."
  done
fi

echo "The X login browser is open, but the current environment is non-interactive."
echo "Finish the login in the browser, then run the following command manually:"
echo "python3 $SCRIPT_DIR/export_x_cookies.py --port $PORT"
echo "After that, reply in the next turn that the login is complete before continuing the high-signal account sampling flow."
exit 2
