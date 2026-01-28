# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Tool router for builtin tools - routes calls to MCP servers or builtin implementations."""

import json
from typing import Dict, Any, Optional, List, TYPE_CHECKING

from aworld.logs.util import logger
from aworld.sandbox.builtin.validator import BuiltinToolValidator

if TYPE_CHECKING:
    from aworld.sandbox.base import Sandbox


class BuiltinToolRouter:
    """Router for builtin tool calls - routes to MCP or builtin implementation."""
    
    def __init__(self, sandbox: "Sandbox"):
        """
        Args:
            sandbox: Sandbox instance
        """
        self.sandbox = sandbox
        self.validator = BuiltinToolValidator()
    
    async def _has_user_config(self, service_name: str) -> bool:
        """Check if user has configured the service.
        
        Args:
            service_name: Service name (filesystem/terminal)
            
        Returns:
            True if service is configured, False otherwise
        """
        if not self.sandbox.mcp_config:
            return False
        
        mcp_servers = self.sandbox.mcp_config.get("mcpServers", {})
        return service_name in mcp_servers
    
    async def _validate_tool_exists(
        self,
        service_name: str,
        tool_name: str,
        available_tools: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """Validate if tool exists in user-configured MCP server.
        
        Args:
            service_name: Service name (filesystem/terminal)
            tool_name: Tool name to check
            available_tools: List of available tools. If None, will fetch from sandbox.
            
        Returns:
            True if tool exists, False otherwise
        """
        if available_tools is None:
            # Get tools from sandbox
            if not hasattr(self.sandbox, 'mcpservers') or not self.sandbox.mcpservers:
                return False
            
            # Try to get from cache first
            tool_list = self.sandbox.mcpservers.tool_list
            if not tool_list:
                # If not cached, fetch asynchronously
                try:
                    tool_list = await self.sandbox.mcpservers.list_tools()
                except Exception as e:
                    logger.warning(f"Failed to list tools for validation: {e}")
                    return False
            
            if not tool_list:
                return False
            
            available_tools = tool_list
        
        return await self.validator.validate_tool_availability(
            service_name, tool_name, available_tools
        )
    
    async def _convert_args_to_tool_params(
        self,
        service_name: str,
        tool_name: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Convert method arguments to tool parameters.
        
        This is a simple implementation that passes kwargs directly.
        Can be extended to handle more complex parameter mapping.
        
        Args:
            service_name: Service name
            tool_name: Tool name
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            Dict of tool parameters
        """
        # For now, just use kwargs directly
        # Can be extended for more complex mapping
        params = kwargs.copy()
        
        # Handle positional arguments if needed
        # This would require knowing the tool signature
        # For now, we assume all parameters are passed as kwargs
        
        return params
    
    async def _parse_tool_result(self, results: list) -> Any:
        """Parse tool execution result.
        
        Args:
            results: List of ActionResult from MCP call
            
        Returns:
            Parsed result (usually the content string)
        """
        if not results or len(results) == 0:
            return None
        
        # Get first result
        result = results[0]
        
        # Extract content
        if hasattr(result, 'content'):
            content = result.content
            # If content is JSON string, try to parse
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    # If it's a dict with 'message' field, return that
                    if isinstance(parsed, dict) and 'message' in parsed:
                        return parsed['message']
                    return parsed
                except (json.JSONDecodeError, ValueError):
                    return content
            return content
        
        return str(result)
    
    def _normalize_result(
        self,
        service_name: str,
        tool_name: str,
        source: str,
        raw: Any,
    ) -> Dict[str, Any]:
        """Normalize local/remote raw result to a minimal unified schema.

        Final schema (three core fields):
            {
                "success": bool,
                "data": Any,
                "error": str | None,
            }
        """
        # Convention: error strings start with "Error:"
        if isinstance(raw, str) and raw.startswith("Error:"):
            msg = raw
            err = raw[len("Error:"):].strip() or raw
            return {
                "success": False,
                "data": None,
                "error": err or msg,
            }

        # Remote terminal/other tools may return structured JSON (already parsed to dict)
        if isinstance(raw, dict):
            success = raw.get("success")
            message = raw.get("message")

            # If remote result already has a success field, respect it
            if success is not None:
                success_bool = bool(success)
                data = raw.get("data", raw)
                error = raw.get("error")
                if not success_bool and not error and isinstance(message, str):
                    error = message
                return {
                    "success": success_bool,
                    "data": data,
                    "error": error,
                }

            # If there is no success field, treat the whole dict as successful data
            return {
                "success": True,
                "data": raw,
                "error": None,
            }

        # Other types (plain string/list/etc.) are treated as successful data
        return {
            "success": True,
            "data": raw,
            "error": None,
        }
    
    async def route_call(
        self,
        service_name: str,
        tool_name: str,
        builtin_impl: Any,
        **kwargs
    ) -> Any:
        """Route tool call by sandbox.mode: local -> builtin, remote -> MCP.
        
        - mode=local: Always use local FilesystemTool/TerminalTool (local workspace/bash).
        - mode=remote: Use MCP servers for filesystem/terminal; if service not configured
          or tool call fails, returns an error result.
        
        Args:
            service_name: Service name (filesystem/terminal)
            tool_name: Tool name to call
            builtin_impl: Builtin tool implementation instance
            **kwargs: Method keyword arguments
            
        Returns:
            Dict with unified schema:
                {
                    "success": bool,
                    "data": Any,
                    "error": str | None,
                }
        """
        mode = getattr(self.sandbox, "mode", "local") or "local"
        mode = str(mode).lower().strip()

        # mode=local: always use local implementation (workspace / bash)
        if mode == "local":
            logger.info(f"Mode=local: using builtin implementation for {service_name}.{tool_name}")
            raw = await builtin_impl.execute(tool_name, **kwargs)
            return self._normalize_result(service_name, tool_name, "local", raw)

        # mode=remote: always use sandbox.call_tool to reach remote services.
        # Before calling, ensure that the corresponding service (filesystem/terminal)
        # is configured in mcp_config.
        if mode == "remote":
            # In remote mode, the target service must exist, otherwise return an error
            if not await self._has_user_config(service_name):
                msg = (
                    f"In remote mode but no '{service_name}' service configured in mcp_config. "
                    f"Add mcp_config['mcpServers']['{service_name}'] to use remote {service_name}."
                )
                logger.warning(msg)
                return self._normalize_result(service_name, tool_name, "remote", f"Error: {msg}")

            logger.info(f"Mode=remote: using MCP server for {service_name}.{tool_name}")
            params = await self._convert_args_to_tool_params(service_name, tool_name, **kwargs)
            try:
                results = await self.sandbox.call_tool(
                    action_list=[{
                        "tool_name": service_name,
                        "action_name": tool_name,
                        "params": params,
                    }],
                )
                parsed = await self._parse_tool_result(results)
                if parsed is None:
                    return self._normalize_result(
                        service_name, tool_name, "remote", "Error: Remote tool returned no result."
                    )
                return self._normalize_result(service_name, tool_name, "remote", parsed)
            except Exception as e:
                logger.warning(f"Remote {service_name}.{tool_name} failed: {e}")
                return self._normalize_result(
                    service_name,
                    tool_name,
                    "remote",
                    f"Error: Remote tool call failed: {str(e)}",
                )

        # Unknown mode: treat as local
        logger.info(f"Unknown mode={mode!r}, using builtin implementation for {service_name}.{tool_name}")
        raw = await builtin_impl.execute(tool_name, **kwargs)
        return self._normalize_result(service_name, tool_name, "local", raw)

