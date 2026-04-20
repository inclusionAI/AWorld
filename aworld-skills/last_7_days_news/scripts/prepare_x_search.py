#!/usr/bin/env python3
"""Prepare a logged-in browser session for X high-signal sampling.

The filename is kept for backward compatibility, but the behavior has changed:
- only ensure that a valid login session is available
- open and remain on https://x.com/home by default so the next step can switch to
  high-signal profile timelines
- do not use the search page as the default collection entry point
- only use the validated cookie stored in /tmp
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


AGENT_BROWSER = "/usr/local/bin/agent-browser"
REQUIRED_COOKIE_NAMES = {"auth_token", "ct0"}
DEFAULT_COOKIE_FILE = Path("/tmp/last_7_days_news_x_cookie.txt")


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        raise RuntimeError(stderr or stdout or "Command execution failed")
    return result


def parse_cookie_file(cookie_file: Path) -> dict[str, str]:
    if not cookie_file.exists():
        raise RuntimeError(f"Cookie file does not exist: {cookie_file}")

    raw = cookie_file.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError("Cookie file is empty")

    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        chunk = part.strip()
        if not chunk or "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        cookies[name.strip()] = value.strip()

    missing = REQUIRED_COOKIE_NAMES - set(cookies)
    if missing:
        raise RuntimeError("Cookie file is missing required fields: " + ", ".join(sorted(missing)))

    return cookies


def resolve_cookie_file() -> Path:
    return DEFAULT_COOKIE_FILE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9222, help="CDP port. Default: 9222")
    parser.add_argument(
        "--query",
        default=None,
        help="Legacy option kept for compatibility. The script no longer opens search automatically; apply keyword filtering locally instead.",
    )
    parser.add_argument(
        "--tab",
        choices=["top", "live"],
        default="top",
        help="Legacy option kept for compatibility. The script no longer opens search automatically.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force a new login and refresh the cookie before preparing the X sampling browser.",
    )
    args = parser.parse_args()

    skill_dir = Path(__file__).resolve().parent.parent
    ensure_script = skill_dir / "scripts" / "ensure_x_cookies.sh"
    open_script = skill_dir / "scripts" / "open_x_login_cdp.sh"
    cookie_file = resolve_cookie_file()

    ensure_cmd = ["bash", str(ensure_script), "--port", str(args.port)]
    if args.force_refresh:
        ensure_cmd.append("--force")
    ensure_result = run(ensure_cmd, check=False)
    if ensure_result.returncode == 2:
        message = (ensure_result.stdout + "\n" + ensure_result.stderr).strip()
        print(message)
        print("The browser is open and waiting for the user to complete the X login.")
        print("After the user confirms in the next turn that the login is complete, rerun the cookie export and continue high-signal account sampling.")
        return 0

    if ensure_result.returncode != 0:
        message = (ensure_result.stdout + "\n" + ensure_result.stderr).strip()
        raise RuntimeError(message or "ensure_x_cookies.sh failed")

    run(["bash", str(open_script), str(args.port)])

    cookies = parse_cookie_file(cookie_file)
    for name, value in cookies.items():
        run(
            [
                AGENT_BROWSER,
                "--cdp",
                str(args.port),
                "cookies",
                "set",
                name,
                value,
                "--url",
                "https://x.com",
            ]
        )

    run([AGENT_BROWSER, "--cdp", str(args.port), "open", "https://x.com/home"])
    run([AGENT_BROWSER, "--cdp", str(args.port), "wait", "3000"])
    title_result = run([AGENT_BROWSER, "--cdp", str(args.port), "get", "title"], check=False)
    url_result = run([AGENT_BROWSER, "--cdp", str(args.port), "get", "url"], check=False)

    if args.query:
        print("Ignored the --query / --tab arguments. The default collection path is now high-signal account timelines first, with Home feed as a supplement only.")
        print("For collection, open profile timelines from the account list first. Only try a narrow search page query when profile samples are insufficient.")

    print(f"Prepared an X logged-in browser session on port: {args.port}")
    print(f"Cookie source: {cookie_file}")
    print(f"Page title: {(title_result.stdout or '').strip()}")
    print(f"Current URL: {(url_result.stdout or '').strip()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
