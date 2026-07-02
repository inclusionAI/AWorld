#!/usr/bin/env python3
"""Export x.com cookies from a CDP browser session into /tmp."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


REQUIRED_COOKIE_NAMES = {"auth_token", "ct0"}
DEFAULT_COOKIE_FILE = Path("/tmp/last_7_days_news_x_cookie.txt")


def run_agent_browser(port: int) -> object:
    cmd = [
        "/usr/local/bin/agent-browser",
        "--cdp",
        str(port),
        "cookies",
        "--json",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "agent-browser cookies command failed")

    text = result.stdout.strip()
    if not text:
        raise RuntimeError("agent-browser did not return any cookie data")

    return json.loads(text)


def normalize_cookie_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        maybe_items = payload.get("cookies")
        if isinstance(maybe_items, list):
            return [row for row in maybe_items if isinstance(row, dict)]
        data = payload.get("data")
        if isinstance(data, dict):
            maybe_items = data.get("cookies")
            if isinstance(maybe_items, list):
                return [row for row in maybe_items if isinstance(row, dict)]
    raise RuntimeError("Unable to recognize the output format from agent-browser cookies --json")


def filter_x_cookies(rows: list[dict]) -> list[dict]:
    filtered = []
    for row in rows:
        domain = str(row.get("domain", ""))
        if domain == "x.com" or domain.endswith(".x.com"):
            filtered.append(row)
    return filtered


def serialize_cookie_string(rows: list[dict]) -> str:
    # Put the required login cookies first, then keep the rest sorted by name.
    def sort_key(row: dict) -> tuple[int, str]:
        name = str(row.get("name", ""))
        priority = 0 if name in REQUIRED_COOKIE_NAMES else 1
        return (priority, name)

    ordered = sorted(rows, key=sort_key)
    return "; ".join(f"{row['name']}={row.get('value', '')}" for row in ordered if row.get("name"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9222, help="Chrome CDP port. Default: 9222")
    parser.add_argument(
        "--output",
        default=None,
        help="Cookie output file path. Default: /tmp/last_7_days_news_x_cookie.txt",
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else DEFAULT_COOKIE_FILE

    payload = run_agent_browser(args.port)
    rows = normalize_cookie_rows(payload)
    x_rows = filter_x_cookies(rows)

    if not x_rows:
        raise RuntimeError("No x.com cookies were read from the CDP browser. Make sure x.com is logged in inside that browser session.")

    names = {str(row.get("name", "")) for row in x_rows}
    missing = REQUIRED_COOKIE_NAMES - names
    if missing:
        raise RuntimeError(
            "Missing required cookies: "
            + ", ".join(sorted(missing))
            + ". Confirm that x.com login completed successfully in the browser window that was just opened."
        )

    cookie_string = serialize_cookie_string(x_rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(cookie_string + "\n", encoding="utf-8")

    print(f"Cookie file written: {output_path}")
    print(f"x.com cookie count: {len(x_rows)}")
    print("Required fields present: auth_token, ct0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Export failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
