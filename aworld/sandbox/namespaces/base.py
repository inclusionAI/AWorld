# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Base class for tool namespaces (file, terminal)."""

import json
from abc import ABC
from typing import TYPE_CHECKING, Any, Dict, List

from aworld.logs.util import logger

if TYPE_CHECKING:
    from aworld.sandbox.implementations.sandbox import Sandbox


def _normalize_result(raw: Any) -> Dict[str, Any]:
    """Normalize MCP call result to {success, data, error}."""
    if raw is None:
        return {"success": False, "data": None, "error": "No result from tool"}
    if isinstance(raw, dict):
        if "success" in raw and "data" in raw and "error" in raw:
            return raw
        return {"success": True, "data": raw, "error": None}
    if isinstance(raw, str) and raw.startswith("Error:"):
        return {"success": False, "data": None, "error": raw[len("Error:") :].strip() or raw}
    return {"success": True, "data": raw, "error": None}


def resolve_service_name(sandbox: Any, logical_name: str) -> str:
    """
    Resolve logical server name (e.g. 'filesystem', 'terminal') to the config key to use.
    Prefer direct key in mcp_config["mcpServers"]; else find a key whose headers["MCP_SERVERS"]
    (comma-separated) contains logical_name. Fallback: return logical_name.
    """
    mcp_config = getattr(sandbox, "mcp_config", None) or getattr(sandbox, "_mcp_config", None)
    if not mcp_config:
        return logical_name
    servers = mcp_config.get("mcpServers") or {}
    if not servers:
        return logical_name
    if logical_name in servers:
        return logical_name
    for key, config in servers.items():
        headers = config.get("headers") or {}
        mcp_servers_header = (headers.get("MCP_SERVERS") or "").strip()
        if not mcp_servers_header:
            continue
        names = [n.strip() for n in mcp_servers_header.split(",") if n.strip()]
        if logical_name in names:
            return key
    return logical_name


def _parse_action_results(results: List[Any]) -> Dict[str, Any]:
    """Parse List[ActionResult] from call_tool into normalized dict."""
    if not results:
        return {"success": False, "data": None, "error": "No result from tool"}
    r = results[0]
    content = getattr(r, "content", None)
    success = getattr(r, "success", True)
    if content is None:
        return {"success": success, "data": None, "error": getattr(r, "content", "Empty result") or "Empty result"}
    try:
        data = json.loads(content) if isinstance(content, str) else content
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
    except (json.JSONDecodeError, TypeError):
        data = content
    return {"success": success, "data": data, "error": None}


class ToolNamespace(ABC):
    """Base for sandbox.file and sandbox.terminal."""

    def __init__(self, sandbox: "Sandbox", service_name: str):
        self._sandbox = sandbox
        self._service_name = service_name

    async def _call_tool(self, tool_name: str, **params: Any) -> Dict[str, Any]:
        """Call MCP tool and return normalized {success, data, error}."""
        if not getattr(self._sandbox, "mcpservers", None):
            logger.warning(
                f"Sandbox MCP servers not initialized. "
                f"Tool {self._service_name}.{tool_name} will not be available."
            )
            return {"success": False, "data": None, "error": "MCP servers not initialized"}
        try:
            action_list = [
                {
                    "tool_name": self._service_name,
                    "action_name": tool_name,
                    "params": params,
                }
            ]
            results = await self._sandbox.call_tool(action_list=action_list)
            return _parse_action_results(results or [])
        except Exception as e:
            logger.warning(f"Tool call failed {self._service_name}.{tool_name}: {e}")
            return {"success": False, "data": None, "error": str(e)}
