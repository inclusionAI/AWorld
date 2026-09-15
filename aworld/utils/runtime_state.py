# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Paths for framework-owned runtime state.

``AWORLD_CONTROL_ROOT`` relocates operational state produced while AWorld is
running (for example cron state, CLI session workspaces, and tool-call logs).
It deliberately does not change the agent's working directory or the sandbox
workspace: task artifacts must continue to be created where the caller asked.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional


AWORLD_CONTROL_ROOT_ENV = "AWORLD_CONTROL_ROOT"


def get_runtime_state_root(
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    """Return the configured framework-state root, if one is configured."""

    source = os.environ if environ is None else environ
    raw_value = source.get(AWORLD_CONTROL_ROOT_ENV, "").strip()
    if not raw_value:
        return None
    return Path(raw_value).expanduser().resolve()


def runtime_state_path(
    *relative_parts: str | os.PathLike[str],
    default: str | os.PathLike[str],
) -> Path:
    """Resolve a state path while preserving legacy defaults when unset.

    Relative parts are intentionally required.  An absolute child would escape
    the configured control root and make the isolation guarantee misleading.
    """

    root = get_runtime_state_root()
    if root is None:
        return Path(default).expanduser()

    result = root
    for raw_part in relative_parts:
        part = Path(raw_part)
        if part.is_absolute():
            raise ValueError("runtime state path components must be relative")
        result = result / part
    return result
