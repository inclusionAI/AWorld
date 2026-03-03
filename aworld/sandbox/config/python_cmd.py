# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Resolve PYTHON_CMD for spawning builtin tool servers (stdio).

Priority: tool_server dir .env → AWORLD_PYTHON_EXECUTABLE → sys.executable → "python".
"""

import os
import sys
from pathlib import Path
from typing import Optional

ENV_PYTHON_CMD = "AWORLD_PYTHON_EXECUTABLE"
PLACEHOLDER_PYTHON_CMD = "${PYTHON_CMD}"


def _load_dotenv_from_tool_server(server_name: str) -> None:
    """Load .env from tool_servers/<server_name>/ if present (optional)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        root = Path(__file__).resolve().parent.parent.parent
        tool_servers_root = root / "tool_servers"
        env_file = tool_servers_root / server_name / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)
    except Exception:
        pass


def resolve_python_cmd(server_name: Optional[str] = None) -> str:
    """
    Resolve the Python executable for running tool servers.

    Priority:
    1. AWORLD_PYTHON_EXECUTABLE (after loading tool_server .env if present)
    2. sys.executable
    3. "python"

    Args:
        server_name: Optional builtin server name (e.g. "filesystem", "terminal")
                     used to load .env from tool_servers/<server_name>/.env first.

    Returns:
        Path or name of the Python executable.
    """
    if server_name:
        _load_dotenv_from_tool_server(server_name)
    cmd = os.environ.get(ENV_PYTHON_CMD, "").strip()
    if cmd:
        return cmd
    if sys.executable:
        return sys.executable
    return "python"


def resolve_command_placeholder(command: str, server_name: Optional[str] = None) -> str:
    """Replace ${PYTHON_CMD} in command string with resolved executable."""
    if PLACEHOLDER_PYTHON_CMD not in command:
        return command
    return command.replace(PLACEHOLDER_PYTHON_CMD, resolve_python_cmd(server_name))
