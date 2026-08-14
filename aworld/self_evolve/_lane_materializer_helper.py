"""Minimal exec-only filesystem materializer.

This helper intentionally imports no AWorld modules.  It receives a bounded
resource specification over stdin, creates/probes the declared directories,
and returns observations over stdout.  Because it is entered through exec, it
cannot inherit measurement signing keys from the orchestrator heap.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def _main() -> int:
    payload = json.loads(sys.stdin.buffer.read(1_000_001))
    root = Path(payload["root"]).resolve()
    delay = float(payload.get("delay_seconds", 0.0))
    if delay:
        time.sleep(delay)
    encoded = (payload["marker_payload"] + "\n").encode("ascii")
    claims = []
    for item in payload["claims"]:
        dimension = item["dimension"]
        absolute = Path(item["declared_identity"]).absolute()
        if not absolute.resolve(strict=False).is_relative_to(root):
            raise ValueError(f"isolated {dimension} escapes materialization root")
        existing = [part for part in (absolute, *absolute.parents) if part.exists()]
        if any(part.is_symlink() for part in existing):
            raise ValueError(f"isolated {dimension} has a symlink component")
        absolute.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker = absolute / ".aworld-lane-owner"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(marker, flags, 0o600)
        except FileExistsError:
            if marker.is_symlink() or marker.read_bytes() != encoded:
                raise ValueError(f"isolated {dimension} ownership marker conflicts")
        else:
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short lane ownership marker write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        stat = absolute.stat(follow_symlinks=False)
        claims.append(
            {
                "dimension": dimension,
                "declared_identity": str(absolute),
                "observed_device": stat.st_dev,
                "observed_inode": stat.st_ino,
                "ownership_marker_fingerprint": "sha256:"
                + hashlib.sha256(encoded).hexdigest(),
            }
        )
    sys.stdout.write(json.dumps({"ok": True, "claims": claims}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        exit_code = _main()
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}")
        exit_code = 1
    raise SystemExit(exit_code)
