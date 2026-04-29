from __future__ import annotations

import os
import shlex
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path

from aworld_cli.memory.discovery import discover_workspace_instruction_layers

WORKSPACE_TEMPLATE = """# Workspace Instructions

## Context
Describe the current workspace and what matters here.

## Guidelines
- Add workspace-specific rules
- Capture important conventions
- Note files or directories that need special care

## Preferences
- Record stable coding or review preferences
"""

REMEMBERED_GUIDANCE_HEADER = "## Remembered Guidance"


def resolve_editor_argv() -> list[str]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    argv = shlex.split(editor)
    return argv or ["nano"]


def ensure_workspace_memory_file(workspace_path: str | os.PathLike[str]) -> tuple[Path, Path | None, str | None]:
    layers = discover_workspace_instruction_layers(workspace_path)
    target = layers.canonical_write_file
    target.parent.mkdir(parents=True, exist_ok=True)

    seeded_from: Path | None = None
    if not target.exists():
        if layers.compatibility_file is not None:
            target.write_text(
                layers.compatibility_file.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            seeded_from = layers.compatibility_file
        else:
            target.write_text(WORKSPACE_TEMPLATE, encoding="utf-8")

    return target, seeded_from, layers.warning


def open_in_editor(target: Path) -> subprocess.CompletedProcess:
    argv = resolve_editor_argv() + [str(target)]
    return subprocess.run(argv, check=False)


def append_remembered_guidance(
    workspace_path: str | os.PathLike[str],
    text: str,
) -> tuple[Path, bool]:
    target, _, _ = ensure_workspace_memory_file(workspace_path)
    content = target.read_text(encoding="utf-8").rstrip()

    if REMEMBERED_GUIDANCE_HEADER not in content:
        if content:
            content = f"{content}\n\n{REMEMBERED_GUIDANCE_HEADER}\n- {text}"
        else:
            content = f"{REMEMBERED_GUIDANCE_HEADER}\n- {text}"
        target.write_text(f"{content}\n", encoding="utf-8")
        return target, True

    existing_lines = content.splitlines()
    bullet = f"- {text}"
    if bullet in existing_lines:
        return target, False

    insertion = f"{content}\n{bullet}"
    target.write_text(f"{insertion}\n", encoding="utf-8")
    return target, True


def append_workspace_session_log(
    workspace_path: str | os.PathLike[str],
    session_id: str,
    payload: dict,
) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    safe_session_id = "".join(
        char for char in (session_id or "default") if char.isalnum() or char in ("-", "_")
    ).strip() or "default"
    log_path = sessions_dir / f"{safe_session_id}.jsonl"

    entry = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False))
        handle.write("\n")
    return log_path
