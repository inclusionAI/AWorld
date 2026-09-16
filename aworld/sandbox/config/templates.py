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
ENV_MAC_UI_AUTOMATION_ENABLED = "AWORLD_ENABLE_MAC_UI_AUTOMATION"
ENV_MAC_UI_AUTOMATION_BACKEND = "AWORLD_MAC_UI_AUTOMATION_BACKEND"
ENV_DOCKER_CONTAINER = "AWORLD_DOCKER_CONTAINER"
ENV_DOCKER_BINARY = "AWORLD_DOCKER_BINARY"
ENV_DOCKER_WORKDIR = "AWORLD_DOCKER_WORKDIR"
ENV_DOCKER_ALLOWED_DIRECTORIES = "AWORLD_DOCKER_ALLOWED_DIRECTORIES"
ENV_DOCKER_SHELL = "AWORLD_DOCKER_SHELL"
ENV_DOCKER_MAX_OUTPUT_BYTES = "AWORLD_DOCKER_MAX_OUTPUT_BYTES"
ENV_DOCKER_OUTPUT_HEAD_BYTES = "AWORLD_DOCKER_OUTPUT_HEAD_BYTES"
ENV_DOCKER_ARTIFACT_DIRECTORY = "AWORLD_DOCKER_ARTIFACT_DIRECTORY"
ENV_FILESYSTEM_LIMITS = (
    "AWORLD_FILESYSTEM_MAX_READ_BYTES",
    "AWORLD_FILESYSTEM_MAX_BINARY_BYTES",
    "AWORLD_FILESYSTEM_MAX_LINE_BYTES",
    "AWORLD_FILESYSTEM_MAX_SCAN_BYTES",
    "AWORLD_FILESYSTEM_MAX_RESULT_LINES",
    "AWORLD_FILESYSTEM_MAX_LIST_ENTRIES",
    "AWORLD_FILESYSTEM_MAX_SEARCH_MATCHES",
    "AWORLD_FILESYSTEM_MAX_SEARCH_PER_FILE",
    "AWORLD_FILESYSTEM_MAX_SEARCH_FILES",
    "AWORLD_FILESYSTEM_MAX_SEARCH_OUTPUT_BYTES",
    "AWORLD_FILESYSTEM_MAX_SEARCH_DEPTH",
    "AWORLD_FILESYSTEM_SEARCH_TIMEOUT_SECONDS",
    "AWORLD_FILESYSTEM_MAX_EDIT_BYTES",
    "AWORLD_FILESYSTEM_MAX_COPY_BYTES",
    "AWORLD_FILESYSTEM_COPY_TIMEOUT_SECONDS",
    "AWORLD_FILESYSTEM_MAX_PARSE_BYTES",
    "AWORLD_FILESYSTEM_MAX_PARSE_OUTPUT_BYTES",
    "AWORLD_FILESYSTEM_MAX_PARSE_MEMORY_BYTES",
    "AWORLD_FILESYSTEM_PARSE_TIMEOUT_SECONDS",
    "AWORLD_FILESYSTEM_MAX_ARCHIVE_MEMBERS",
    "AWORLD_FILESYSTEM_MAX_ARCHIVE_MEMBER_BYTES",
    "AWORLD_FILESYSTEM_MAX_ARCHIVE_UNCOMPRESSED_BYTES",
    "AWORLD_FILESYSTEM_MAX_ARCHIVE_COMPRESSION_RATIO",
    "AWORLD_FILESYSTEM_MAX_WORKBOOK_SHEETS",
    "AWORLD_FILESYSTEM_MAX_WORKBOOK_ROWS",
    "AWORLD_FILESYSTEM_MAX_WORKBOOK_COLUMNS",
    "AWORLD_FILESYSTEM_MAX_WORKBOOK_CELLS",
    "AWORLD_FILESYSTEM_MAX_WORKBOOK_MERGED_CELLS",
)
ENV_TERMINAL_POLICY = (
    "AWORLD_TERMINAL_CAPTURE_MAX_BYTES",
    "AWORLD_TERMINAL_ARTIFACT_MAX_BYTES",
    "AWORLD_TERMINAL_ARTIFACT_READ_MAX_BYTES",
    "AWORLD_TERMINAL_ARTIFACT_DIR",
    "AWORLD_TERMINAL_MAX_TIMEOUT_SECONDS",
    "AWORLD_TASK_DEADLINE_EPOCH_SECONDS",
    "AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS",
    "TERMINAL_CAPTURE_MAX_BYTES",
    "TERMINAL_TIMEOUT",
)

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


def get_mac_ui_automation_script_path() -> str:
    """Absolute path to the macOS UI automation server main.py."""
    return str(_tool_servers_root() / "platforms" / "mac" / "ui_automation" / "src" / "main.py")


def get_docker_script_path() -> str:
    """Absolute path to the host-side Docker MCP bridge."""
    return str(_tool_servers_root() / "docker" / "src" / "server.py")


def get_server_env() -> Dict[str, str]:
    """
    Read env values that should be forwarded to MCP server processes.

    This includes:
    - AWORLD_WORKSPACE: workspace directories
    - Log suppression vars: prevent verbose DEBUG/INFO logs from appearing in CLI
    """
    env: Dict[str, str] = {}

    # Workspace configuration
    v = os.environ.get(ENV_WORKSPACE, "").strip()
    if v:
        env[ENV_WORKSPACE] = v

    # Built-in stdio servers receive an explicit environment rather than the
    # controller's full process environment. Forward only explicitly configured
    # filesystem policy knobs so general CLI users can tune finite hard limits.
    for name in ENV_FILESYSTEM_LIMITS:
        value = os.environ.get(name, "").strip()
        if value:
            env[name] = value

    for name in ENV_TERMINAL_POLICY:
        value = os.environ.get(name, "").strip()
        if value:
            env[name] = value

    mac_ui_enabled = os.environ.get(ENV_MAC_UI_AUTOMATION_ENABLED, "").strip()
    if mac_ui_enabled:
        env[ENV_MAC_UI_AUTOMATION_ENABLED] = mac_ui_enabled

    mac_ui_backend = os.environ.get(ENV_MAC_UI_AUTOMATION_BACKEND, "").strip()
    if mac_ui_backend:
        env[ENV_MAC_UI_AUTOMATION_BACKEND] = mac_ui_backend

    # Log suppression for MCP server subprocesses
    # These environment variables suppress verbose logging from Python-based MCP servers
    log_suppression_vars = {
        "PYTHONWARNINGS": "ignore",           # Suppress Python warnings
        "MCP_LOG_LEVEL": "WARNING",           # MCP server log level
        "LOG_LEVEL": "WARNING",               # Generic log level
        "LOGLEVEL": "WARNING",                # Alternative log level var
        "AWORLD_DISABLE_CONSOLE_LOG": "true"  # For aworld-based MCP servers
    }

    # Only set log suppression if not explicitly disabled by user
    preserve_logs = os.environ.get('AWORLD_PRESERVE_MCP_LOGS', 'false').lower() in ('true', '1', 'yes')
    if not preserve_logs:
        env.update(log_suppression_vars)

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
