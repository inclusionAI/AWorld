#!/usr/bin/env python3
"""Actively validate whether the X cookie stored in /tmp still logs in to x.com."""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


REQUIRED_COOKIE_NAMES = {"auth_token", "ct0"}
SUCCESS_MARKERS = ('link "Home"', 'link "Profile"', 'link "Post"', 'button "Account menu"')
FAIL_MARKERS = ('link "Log in"', 'link "Sign in"', 'link "Create account"')
AGENT_BROWSER = "/usr/local/bin/agent-browser"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
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


def choose_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_cdp(port: int, timeout_seconds: int = 30) -> None:
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"CDP port did not become ready: {last_error}")


def launch_temp_chrome(port: int, profile_dir: str, headed: bool) -> subprocess.Popen[str]:
    if not Path(CHROME_BIN).exists():
        raise RuntimeError(f"Chrome executable not found: {CHROME_BIN}")

    cmd = [
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://x.com/home",
    ]
    if not headed:
        cmd.insert(1, "--headless=new")

    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def run_cdp(port: int, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run([AGENT_BROWSER, "--cdp", str(port), *args], check=check)


def is_navigation_timeout(result: subprocess.CompletedProcess[str]) -> bool:
    text = ((result.stderr or "") + "\n" + (result.stdout or "")).lower()
    return "timeout" in text and "page.goto" in text


def validate_cookie_login(cookie_file: Path, headed: bool) -> tuple[bool, str]:
    cookies = parse_cookie_file(cookie_file)
    port = choose_free_port()
    temp_dir = tempfile.mkdtemp(prefix="last7news-validate-")
    chrome_process: subprocess.Popen[str] | None = None

    try:
        chrome_process = launch_temp_chrome(port, temp_dir, headed)
        wait_for_cdp(port)

        run_cdp(port, "cookies", "clear", check=False)
        for name, value in cookies.items():
            run_cdp(port, "cookies", "set", name, value, "--url", "https://x.com")

        open_result = run_cdp(port, "open", "https://x.com/home", check=False)
        if open_result.returncode != 0 and not is_navigation_timeout(open_result):
            detail = (open_result.stderr or open_result.stdout or "").strip()
            return False, f"Failed to open x.com/home during cookie validation: {detail}"

        # X often times out on the first page load even when login is valid.
        # Continue and rely on URL + snapshot markers instead of failing early.
        run_cdp(port, "wait", "6000", check=False)

        url_result = run_cdp(port, "get", "url", check=False)
        snapshot_result = run_cdp(port, "snapshot", "-i", check=False)
        page_text = (url_result.stdout or "") + "\n" + (snapshot_result.stdout or "")

        if any(marker in page_text for marker in SUCCESS_MARKERS):
            return True, "Cookie validation passed. The current cookie still reaches an authenticated x.com session."
        if any(marker in page_text for marker in FAIL_MARKERS):
            return False, "The cookie is no longer valid. The page is still on the login or sign-up entry points."
        if "/home" not in (url_result.stdout or ""):
            return False, "The cookie did not take effect reliably. The browser did not stabilize on the /home page."
        return False, "Cookie validation is inconclusive. A fresh login and export is recommended."
    finally:
        if chrome_process is not None:
            chrome_process.terminate()
            try:
                chrome_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome_process.kill()
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cookie-file",
        default=None,
        help="Cookie file to validate. Default: /tmp/last_7_days_news_x_cookie.txt",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run validation with a visible browser instead of headless mode.",
    )
    args = parser.parse_args()

    cookie_file = Path(args.cookie_file) if args.cookie_file else DEFAULT_COOKIE_FILE

    is_valid, message = validate_cookie_login(cookie_file, headed=args.headed)
    print(message)
    return 0 if is_valid else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
