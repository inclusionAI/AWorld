from __future__ import annotations

import hashlib
import json
from pathlib import Path


class SkillPackageFingerprintError(RuntimeError):
    """Raised when a skill package cannot form an immutable identity."""


def fingerprint_skill_package(skill_root: str | Path) -> str:
    """Fingerprint a complete skill package without importing self-evolve.

    This module deliberately depends only on the standard library so the CLI's
    task-time skill resolver can attest activation while the wider ``aworld``
    runtime is still being initialized.
    """

    root = Path(skill_root).expanduser().resolve()
    if not root.is_dir():
        raise SkillPackageFingerprintError(
            f"skill root is not a directory: {root}"
        )
    package_entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SkillPackageFingerprintError(
                "skill package cannot contain symlinks"
            )
        if path.is_file():
            content = path.read_bytes()
            stat = path.stat()
            package_entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                    "size": stat.st_size,
                    "mode": stat.st_mode & 0o777,
                }
            )
    encoded = json.dumps(
        {"files": package_entries},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
