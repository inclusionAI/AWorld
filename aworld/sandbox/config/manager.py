# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Tool config manager: build mcp_config for process-local builtin tools."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from aworld.logs.util import logger

from aworld.sandbox.config.templates import (
    PYTHON_CMD_PLACEHOLDER,
    build_stdio_server_config,
    get_filesystem_script_path,
    get_mac_ui_automation_script_path,
    get_terminal_script_path,
    get_server_env,
    ENV_WORKSPACE,
)


class ToolConfigManager:
    """
    Build mcp_config for builtin tools (filesystem, terminal).

    - builtin_tools always generate local stdio configurations (command + args, using the ${PYTHON_CMD} placeholder).
    - If the user provides an mcp_config entry for the same server name (e.g. type=streamable-http, url=...),
      the user config overrides the builtin defaults in Sandbox._merge_mcp_configs.
    """

    def __init__(self, mode: str = "local", workspaces: Optional[List[str]] = None):
        self.mode = str(mode).lower().strip() if mode else "local"
        if self.mode not in {"local", "remote"}:
            raise ValueError("mode must be either 'local' or 'remote'")
        self.workspaces = workspaces or []

    def get_mcp_config(self, enabled_tools: List[str]) -> Dict[str, Any]:
        """
        Build mcp_config for each enabled builtin tool using stdio (spawn subprocess).
        """
        if enabled_tools and self.mode != "local":
            raise ValueError(
                "builtin_tools are process-local stdio services and cannot be "
                "enabled with mode='remote'; configure an explicit remote MCP "
                "service instead"
            )
        mcp_servers: Dict[str, Any] = {}
        for name in enabled_tools:
            try:
                if name == "filesystem":
                    cfg = self._config_for_filesystem()
                elif name == "terminal":
                    cfg = self._config_for_terminal()
                elif name == "mac_ui_automation":
                    cfg = self._config_for_mac_ui_automation()
                else:
                    logger.warning(f"Unknown builtin tool: {name}")
                    continue
                if cfg is not None:
                    mcp_servers[name] = cfg
            except Exception as e:
                logger.error(
                    f"Failed to configure builtin tool '{name}': {e}. "
                    f"Tool '{name}' will not be available.",
                    exc_info=True,
                )
        if not mcp_servers:
            logger.warning(
                "No builtin tools configured. Use builtin_tools e.g. ['filesystem','terminal'] "
                "or pass an explicit mcp_config."
            )
        return {"mcpServers": mcp_servers}

    def _stdio_working_directory(self, env: Dict[str, str]) -> str:
        """Pin stdio children to an explicit directory at config-build time."""

        candidates = list(self.workspaces)
        if not candidates:
            configured = (env or {}).get(ENV_WORKSPACE, "")
            candidates = [item.strip() for item in configured.split(",") if item.strip()]
        selected = candidates[0] if candidates else os.getcwd()
        return str(Path(selected).expanduser().resolve())

    def _config_for_filesystem(self) -> Optional[Dict[str, Any]]:
        script_path = get_filesystem_script_path()
        env = get_server_env()
        # If workspaces is explicitly set on the sandbox, override AWORLD_WORKSPACE
        if self.workspaces:
            env = dict(env) if env else {}
            env[ENV_WORKSPACE] = ",".join(self.workspaces)
        return build_stdio_server_config(
            command=PYTHON_CMD_PLACEHOLDER,
            args=[script_path, "--stdio"],
            env=env or None,
            cwd=self._stdio_working_directory(env),
        )

    def _config_for_terminal(self) -> Optional[Dict[str, Any]]:
        script_path = get_terminal_script_path()
        env = dict(get_server_env())
        if self.workspaces:
            env[ENV_WORKSPACE] = ",".join(self.workspaces)
        working_directory = self._stdio_working_directory(env)
        env.setdefault(
            "AWORLD_TERMINAL_ARTIFACT_DIR",
            str(
                Path(working_directory)
                / ".aworld"
                / "artifacts"
                / "terminal-output"
            ),
        )
        return build_stdio_server_config(
            command=PYTHON_CMD_PLACEHOLDER,
            args=[script_path, "--stdio"],
            env=env or None,
            cwd=working_directory,
        )

    def _config_for_mac_ui_automation(self) -> Optional[Dict[str, Any]]:
        script_path = get_mac_ui_automation_script_path()
        env = get_server_env()
        return build_stdio_server_config(
            command=PYTHON_CMD_PLACEHOLDER,
            args=[script_path, "--stdio"],
            env=env or None,
            cwd=self._stdio_working_directory(env),
        )
