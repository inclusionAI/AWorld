# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""
Sandbox and the tool servers read configuration from environment variables.
When you run script/start_tool_servers.sh or start the tool server processes manually,
they also rely on these env vars (for example WORKSPACE for filesystem/terminal).

Example .env (at project root or next to the script):
  # Workspace: comma-separated directories allowed by filesystem and used as working dir for terminal
  AWORLD_WORKSPACE=/path/to/workspace
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any

# ============ Environment variable names (shared with .env, start_tool_servers.sh, and tool_servers) ============
ENV_FILESYSTEM_ENDPOINT = "AWORLD_FILESYSTEM_ENDPOINT"
ENV_FILESYSTEM_TOKEN = "AWORLD_FILESYSTEM_TOKEN"
ENV_TERMINAL_ENDPOINT = "AWORLD_TERMINAL_ENDPOINT"
ENV_TERMINAL_TOKEN = "AWORLD_TERMINAL_TOKEN"
ENV_WORKSPACE = "AWORLD_WORKSPACE"

# Server type and timeouts
STREAMABLE_HTTP_TYPE = "streamable-http"
STDIO_TYPE = "stdio"
DEFAULT_TIMEOUT = 9999.0
DEFAULT_SSE_READ_TIMEOUT = 9999.0
DEFAULT_CLIENT_SESSION_TIMEOUT = 9999.0

# Placeholder resolved at spawn time by mcp_client
PYTHON_CMD_PLACEHOLDER = "${PYTHON_CMD}"

# Builtin server script paths (relative to sandbox config package: .../aworld/sandbox/config/)
def _tool_servers_root() -> Path:
    return Path(__file__).resolve().parent.parent / "tool_servers"


def get_filesystem_script_path() -> str:
    """Absolute path to filesystem server main.py."""
    return str(_tool_servers_root() / "filesystem" / "src" / "main.py")


def get_terminal_script_path() -> str:
    """Absolute path to terminal server terminal.py."""
    return str(_tool_servers_root() / "terminal" / "src" / "terminal.py")


def get_server_env() -> Dict[str, str]:
    """Read env values that should be forwarded to MCP server processes (e.g. workspace)."""
    env: Dict[str, str] = {}
    v = os.environ.get(ENV_WORKSPACE, "").strip()
    if v:
        env[ENV_WORKSPACE] = v
    return env


def build_server_config(
    url: str,
    token: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> dict:
    """Build one mcpServer entry: streamable-http with url, optional Bearer token, and optional env for the server."""
    cfg: Dict[str, Any] = {
        "type": STREAMABLE_HTTP_TYPE,
        "url": url,
        "timeout": DEFAULT_TIMEOUT,
        "client_session_timeout_seconds": DEFAULT_CLIENT_SESSION_TIMEOUT,
        "sse_read_timeout": DEFAULT_SSE_READ_TIMEOUT,
    }
    if token and token.strip():
        cfg["headers"] = {"Authorization": f"Bearer {token.strip()}"}
    if env:
        cfg["env"] = dict(env)
    return cfg


def build_stdio_server_config(
    command: str,
    args: list,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
    client_session_timeout_seconds: float = DEFAULT_CLIENT_SESSION_TIMEOUT,
) -> Dict[str, Any]:
    """Build one mcpServer entry for stdio transport. command may contain ${PYTHON_CMD}."""
    cfg: Dict[str, Any] = {
        "type": STDIO_TYPE,
        "command": command,
        "args": list(args),
        "client_session_timeout_seconds": client_session_timeout_seconds,
    }
    if env:
        cfg["env"] = dict(env)
    if cwd:
        cfg["cwd"] = cwd
    return cfg
