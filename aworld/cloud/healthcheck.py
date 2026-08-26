"""Container health checks for the Cloud MVP roles."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path


def _server(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return False
    return response.status == 200 and payload.get("ok") is True


def _worker(path: Path, max_age_seconds: float) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return 0 <= age <= max_age_seconds and payload.get("ok") is True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="service", required=True)
    server = subparsers.add_parser("server")
    server.add_argument("--url", default="http://127.0.0.1:8000/healthz")
    worker = subparsers.add_parser("worker")
    worker.add_argument("--path", type=Path, required=True)
    worker.add_argument("--max-age-seconds", type=float, default=20)
    args = parser.parse_args(argv)
    healthy = (
        _server(args.url)
        if args.service == "server"
        else _worker(args.path, args.max_age_seconds)
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
