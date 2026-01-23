# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Tool router for builtin tools - routes calls to MCP servers or builtin implementations."""

import json
from typing import Dict, Any, Optional, List, TYPE_CHECKING

from aworld.logs.util import logger
from aworld.sandbox.builtin.base import SERVICE_TOOL_MAPPING
from aworld.sandbox.builtin.exceptions import ToolNotAvailableError, ToolNotConfiguredError
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
    
    async def route_call(
        self,
        service_name: str,
        tool_name: str,
        builtin_impl: Any,
        **kwargs
    ) -> Any:
        """Route tool call to MCP server or builtin implementation.
        
        Args:
            service_name: Service name (filesystem/terminal)
            tool_name: Tool name to call
            builtin_impl: Builtin tool implementation instance
            *args: Method arguments
            **kwargs: Method keyword arguments
            
        Returns:
            Tool execution result
            
        Raises:
            ToolNotAvailableError: If user configured service but tool doesn't exist
            ToolNotConfiguredError: If service not configured and no fallback
        """
        # Check if user has configured this service
        if await self._has_user_config(service_name):
            # User has configured the service, validate tool exists
            available_tools = None
            if hasattr(self.sandbox, 'mcpservers') and self.sandbox.mcpservers:
                # Try cache first
                available_tools = self.sandbox.mcpservers.tool_list
            
            if not await self._validate_tool_exists(service_name, tool_name, available_tools):
                # Tool not found in user configuration
                raise ToolNotAvailableError(service_name, tool_name)
            
            # Tool exists, use MCP call
            logger.info(f"Using MCP server for {service_name}.{tool_name}")
            
            # Convert arguments to tool parameters
            params = await self._convert_args_to_tool_params(service_name, tool_name, **kwargs)
            
            # Call MCP tool
            # Context is optional for MCP calls
            context = getattr(self.sandbox, '_current_context', None)
            results = await self.sandbox.mcpservers.call_tool(
                action_list=[{
                    "tool_name": service_name,
                    "action_name": tool_name,
                    "params": params
                }],
                context=context
            )
            
            # Parse and return result
            return await self._parse_tool_result(results)
        
        else:
            # User has not configured the service, use builtin implementation
            logger.info(f"Using builtin implementation for {service_name}.{tool_name}")
            
            # Call builtin implementation
            return await builtin_impl.execute(tool_name, **kwargs)

