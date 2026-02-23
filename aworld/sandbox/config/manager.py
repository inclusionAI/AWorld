# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Tool config manager: build mcp_config from env (endpoint, token) for builtin tools."""

import os
from typing import Any, Dict, List, Optional

from aworld.logs.util import logger

from aworld.sandbox.config.templates import (
    ENV_FILESYSTEM_ENDPOINT,
    ENV_FILESYSTEM_TOKEN,
    ENV_TERMINAL_ENDPOINT,
    ENV_TERMINAL_TOKEN,
    build_server_config,
    get_server_env,
)


class ToolConfigManager:
    """
    Build mcp_config for builtin tools (filesystem, terminal).
    Both local and remote: read endpoint (URL) and token from env, fill streamable-http config.
    """

    def __init__(self, mode: str = "local", workspace: Optional[List[str]] = None):
        self.mode = str(mode).lower().strip() if mode else "local"
        self.workspace = workspace or []

    def get_mcp_config(self, enabled_tools: List[str]) -> Dict[str, Any]:
        """
        Build mcp_config from env. Only add a tool if its endpoint env var is set.
        """
        mcp_servers: Dict[str, Any] = {}
        for name in enabled_tools:
            try:
                if name == "filesystem":
                    cfg = self._server_config_from_env(ENV_FILESYSTEM_ENDPOINT, ENV_FILESYSTEM_TOKEN)
                elif name == "terminal":
                    cfg = self._server_config_from_env(ENV_TERMINAL_ENDPOINT, ENV_TERMINAL_TOKEN)
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
                "No builtin tools configured. Set AWORLD_FILESYSTEM_ENDPOINT / "
                "AWORLD_TERMINAL_ENDPOINT (and optional TOKEN) in env or .env."
            )
        return {"mcpServers": mcp_servers}

    def _server_config_from_env(
        self, endpoint_var: str, token_var: str
    ) -> Optional[Dict[str, Any]]:
        url = os.getenv(endpoint_var)
        if not url or not url.strip():
            logger.debug(f"Skip: {endpoint_var} not set")
            return None
        token = os.getenv(token_var, "")
        env = get_server_env()
        return build_server_config(url.strip(), token or None, env=env or None)
