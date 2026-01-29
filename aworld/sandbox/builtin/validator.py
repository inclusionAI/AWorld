# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Tool configuration validator for builtin tools."""

from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.builtin.base import SERVICE_TOOL_MAPPING


class BuiltinToolValidator:
    """Validate user-configured MCP servers against builtin tool requirements."""
    
    def __init__(self):
        """Initialize validator."""
        pass
    
    async def validate_tool_availability(
        self,
        service_name: str,
        tool_name: str,
        available_tools: List[Dict[str, Any]]
    ) -> bool:
        """Validate if a tool is available in the configured MCP server.
        
        Args:
            service_name: Service name (filesystem/terminal)
            tool_name: Tool name to check
            available_tools: List of available tools from MCP server
            
        Returns:
            True if tool is available, False otherwise
        """
        # Build full tool name: service_name__tool_name
        full_tool_name = f"{service_name}__{tool_name}"
        
        # Check if tool exists in available tools
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            
            function_info = tool.get("function", {})
            if not isinstance(function_info, dict):
                continue
            
            tool_function_name = function_info.get("name", "")
            if tool_function_name == full_tool_name:
                return True
        
        return False
    
    async def validate_service_tools(
        self,
        service_name: str,
        available_tools: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate all tools for a service.
        
        Args:
            service_name: Service name (filesystem/terminal)
            available_tools: List of available tools from MCP server
            
        Returns:
            Dict with validation results:
            {
                "has_all_tools": bool,
                "missing_tools": List[str],
                "available_tools": List[str]
            }
        """
        if service_name not in SERVICE_TOOL_MAPPING:
            return {
                "has_all_tools": False,
                "missing_tools": [],
                "available_tools": []
            }
        
        required_tools = list(SERVICE_TOOL_MAPPING[service_name].values())
        available_tool_names = []
        missing_tools = []
        
        # Extract available tool names
        for tool in available_tools:
            if not isinstance(tool, dict):
                continue
            
            function_info = tool.get("function", {})
            if not isinstance(function_info, dict):
                continue
            
            tool_function_name = function_info.get("name", "")
            # Remove service prefix to get tool name
            if tool_function_name.startswith(f"{service_name}__"):
                tool_name = tool_function_name[len(f"{service_name}__"):]
                available_tool_names.append(tool_name)
        
        # Check which required tools are missing
        for required_tool in required_tools:
            if required_tool not in available_tool_names:
                missing_tools.append(required_tool)
        
        return {
            "has_all_tools": len(missing_tools) == 0,
            "missing_tools": missing_tools,
            "available_tools": available_tool_names
        }

