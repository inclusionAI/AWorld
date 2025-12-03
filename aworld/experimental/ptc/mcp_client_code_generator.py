# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Tool Function Generator - Convert MCP tool schemas to Python functions for PTC.

This module generates Python function code that can be used in PTC (Programmatic Tool Calling)
scripts. The generated functions call MCP tools through the sandbox.mcpservers.call_tool interface.
"""

from typing import Any, Dict, List, Optional
import re

from aworld.logs.util import logger


class MCPToolInfo:
    """Information about an MCP tool.
    
    Example:
        tool_info = MCPToolInfo(
            name="browser_click",
            description="Click on a browser element",
            server_name="ms-playwright",
            input_schema={
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Element to click"},
                    "ref": {"type": "string", "description": "Element reference"}
                },
                "required": ["element"]
            }
        )
    """
    
    def __init__(
        self,
        name: str,
        description: str,
        server_name: str,
        input_schema: Optional[Dict[str, Any]] = None,
    ):
        """Initialize MCP tool information.
        
        Args:
            name: Tool name
            description: Tool description
            server_name: MCP server name
            input_schema: JSON schema for tool parameters
        """
        self.name = name
        self.description = description
        self.server_name = server_name
        self.input_schema = input_schema or {}
    
    def get_parameters(self) -> Dict[str, Dict[str, Any]]:
        """Extract parameter information from input schema.
        
        Returns:
            Dictionary mapping parameter names to their info (type, required, description, default)
        """
        params = {}
        schema = self.input_schema
        
        if not isinstance(schema, dict):
            return params
        
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        
        for param_name, param_info in properties.items():
            if not isinstance(param_info, dict):
                continue
            
            params[param_name] = {
                "type": param_info.get("type", "string"),
                "required": param_name in required,
                "description": param_info.get("description", ""),
                "default": param_info.get("default"),
            }
        
        return params


class ToolFunctionGenerator:
    """Generates Python function code from MCP tool schemas for PTC usage.
    
    Example:
        generator = ToolFunctionGenerator()
        tools = [
            MCPToolInfo(
                name="read_file",
                description="Read a file",
                server_name="filesystem-server",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"}
                    },
                    "required": ["path"]
                }
            )
        ]
        code = generator.generate_tool_module("filesystem-server", tools)
    """
    
    def generate_tool_module(
        self, server_name: str, tools: List[MCPToolInfo]
    ) -> str:
        """Generate a complete Python module for a server's tools.
        
        Args:
            server_name: Name of the MCP server
            tools: List of tools from this server
            
        Returns:
            Complete Python module code as string
        """
        # Use simple formatted message to avoid incompatible structured logging kwargs
        logger.info(f"Generating tool module for server={server_name}, tool_count={len(tools)}")
        
        code = f'''"""
Auto-generated tool functions for MCP server: {server_name}

This module provides Python functions that call tools on the {server_name} MCP server.
Functions are automatically generated from the MCP tool schemas.
These functions are designed to be used within PTC (Programmatic Tool Calling) scripts
running in a sandbox environment.

Usage:
    # In your PTC script, you need to have access to a sandbox instance
    from aworld.sandbox import Sandbox
    
    sandbox = Sandbox(mcp_servers=["{server_name}"], mcp_config={{...}})
    
    # Import and use the generated functions
    from {server_name}_tools import *
    
    # Call tools directly
    result = await read_file(path="/path/to/file.txt")
"""

from typing import Any, List, Dict, Optional
import json

# Global sandbox instance - must be set before calling any tool functions
_sandbox = None
_context = None


def set_sandbox(sandbox_instance: Any) -> None:
    """Set the sandbox instance for tool calls.
    
    Args:
        sandbox_instance: Sandbox instance with mcpservers configured
    """
    global _sandbox
    _sandbox = sandbox_instance


def set_context(context_instance: Any) -> None:
    """Set the default context instance for tool calls.
    
    Args:
        context_instance: Context instance that will be passed to mcpservers.call_tool
    """
    global _context
    _context = context_instance


async def _call_mcp_tool(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """Call an MCP tool via sandbox.mcpservers.call_tool.
    
    Args:
        server_name: Name of the MCP server
        tool_name: Name of the tool
        arguments: Tool arguments
        
    Returns:
        Tool result content (extracted from ActionResult)
        
    Raises:
        RuntimeError: If sandbox is not set or tool call fails
    """
    global _sandbox, _context
    
    if _sandbox is None:
        raise RuntimeError(
            "Sandbox not initialized. Call set_sandbox(sandbox_instance) first. "
            "This module must be used within a PTC sandbox environment."
        )
    
    try:
        # Call tool through sandbox
        results = await _sandbox.mcpservers.call_tool(
            [
                {{
                    "tool_name": server_name,
                    "action_name": tool_name,
                    "params": arguments
                }}
            ],
            context=_context,
        )
        
        # Extract content from first ActionResult
        if results and len(results) > 0:
            result = results[0]
            # ActionResult has a content field
            content = result.content if hasattr(result, 'content') else result.get('content', '')
            
            # Handle different content types
            if content is None:
                return None
            
            # If content is a string, try to parse as JSON
            # Note: MCP server calls return json.dumps(content_list), so content is JSON string
            # function_tool and api calls may return plain strings
            if isinstance(content, str):
                content_str = content.strip()
                # Try to parse JSON if it looks like JSON
                if content_str and (content_str.startswith("[") or content_str.startswith("{{")):
                    try:
                        parsed = json.loads(content)
                        # For MCP server calls, parsed is typically a list
                        # If it's a list with a single element, return that element for convenience
                        # Otherwise return the whole list
                        if isinstance(parsed, list):
                            if len(parsed) == 1:
                                return parsed[0]
                            else:
                                return parsed
                        # If parsed is dict or other type, return as-is
                        return parsed
                    except json.JSONDecodeError:
                        # If JSON parsing fails, return the string as-is
                        # This handles function_tool/api calls that return plain strings
                        return content
                else:
                    # Not JSON format, return string as-is
                    return content
            
            # If content is already a list, handle it
            elif isinstance(content, list):
                # If list has a single element, return that element for convenience
                # This matches the common pattern: json.loads(content)[0]
                if len(content) == 1:
                    return content[0]
                else:
                    return content
            
            # If content is a dict, return it directly
            elif isinstance(content, dict):
                return content
            
            # For other types (int, float, bool, etc.), return as-is
            else:
                return content
        else:
            return None
            
    except Exception as e:
        error_msg = "MCP tool call failed: " + str(e)
        print("ERROR: " + error_msg, file=__import__('sys').stderr)
        print("Server: " + server_name + ", Tool: " + tool_name, file=__import__('sys').stderr)
        raise RuntimeError(error_msg) from e


'''
        
        # Generate functions for each tool
        for tool in tools:
            code += self._generate_function(tool, server_name)
            code += "\n\n"
        
        return code
    
    def _generate_function(self, tool: MCPToolInfo, server_name: str) -> str:
        """Generate Python function for a single tool.
        
        Args:
            tool: Tool information
            server_name: MCP server name
            
        Returns:
            Python function code
        """
        # Generate function signature
        func_name = tool.name.replace("-", "_").replace(".", "_")
        params = tool.get_parameters()
        
        # Build parameter list - required parameters must come before optional
        param_list = []
        
        # First add required parameters
        for param_name, param_info in params.items():
            if param_info["required"]:
                param_type = self._map_json_type_to_python(param_info["type"])
                param_list.append(f"{param_name}: {param_type}")
        
        # Then add optional parameters
        for param_name, param_info in params.items():
            if not param_info["required"]:
                param_type = self._map_json_type_to_python(param_info["type"])
                default = param_info.get("default")
                if default is None:
                    param_list.append(f"{param_name}: Optional[{param_type}] = None")
                else:
                    default_repr = self._format_default_value(default, param_type)
                    param_list.append(f"{param_name}: {param_type} = {default_repr}")
        
        param_str = ", ".join(param_list)
        
        # Generate docstring
        docstring = self._generate_docstring(tool, params)
        
        # Generate function body
        arg_dict_entries = []
        for param_name in params.keys():
            arg_dict_entries.append(f'        "{param_name}": {param_name},')
        
        args_dict = "\n".join(arg_dict_entries)
        
        # Extract return type from description for better type hints
        return_type, _ = self._extract_return_info(tool.description)
        
        function_code = f'''async def {func_name}({param_str}) -> {return_type}:
    """{docstring}"""
    arguments = {{
{args_dict}
    }}
    
    # Remove None values
    arguments = {{k: v for k, v in arguments.items() if v is not None}}
    
    return await _call_mcp_tool("{server_name}", "{tool.name}", arguments)'''
        
        return function_code
    
    def _generate_docstring(
        self, tool: MCPToolInfo, params: Dict[str, Any]
    ) -> str:
        """Generate docstring for a tool function.
        
        Args:
            tool: Tool information
            params: Parameter information
            
        Returns:
            Formatted docstring
        """
        lines = []
        
        # Add description
        if tool.description:
            # Escape backslashes to avoid syntax warnings in docstrings
            escaped_desc = tool.description.replace("\\", "\\\\")
            lines.append(escaped_desc)
            lines.append("")
        
        # Add parameters
        if params:
            lines.append("Args:")
            for param_name, param_info in params.items():
                param_desc = param_info.get("description", "")
                # Escape backslashes to avoid syntax warnings in docstrings
                escaped_desc = param_desc.replace("\\", "\\\\")
                param_type = param_info["type"]
                required = " (required)" if param_info["required"] else ""
                lines.append(f"    {param_name} ({param_type}){required}: {escaped_desc}")
            lines.append("")
        
        # Add returns - extract from description if available
        return_type, return_desc = self._extract_return_info(tool.description)
        lines.append("Returns:")
        # Format multiline return descriptions properly
        return_lines = return_desc.split('\n')
        first_line = return_lines[0].strip() if return_lines else ""
        if return_type != "Any":
            lines.append(f"    {return_type}: {first_line}")
        else:
            lines.append(f"    {first_line}")
        # Add remaining lines with proper indentation
        for line in return_lines[1:]:
            stripped = line.strip()
            if stripped:
                lines.append(f"    {stripped}")
        lines.append("")
        
        # Add example
        example_args = []
        for param_name, param_info in params.items():
            if param_info["required"]:
                example_val = self._generate_example_value(param_info["type"])
                example_args.append(f'{param_name}={example_val}')
        
        if example_args:
            func_name = tool.name.replace("-", "_").replace(".", "_")
            example_call = f"{func_name}({', '.join(example_args[:2])})"  # Limit to 2 args
            lines.append("Example:")
            lines.append(f"    result = await {example_call}")
        
        # Join with newlines and proper indentation
        # Each line after the first needs 4 spaces of indentation
        return "\n    ".join(lines)
    
    def _map_json_type_to_python(self, json_type: str) -> str:
        """Map JSON schema type to Python type hint.
        
        Args:
            json_type: JSON schema type
            
        Returns:
            Python type hint string
        """
        type_map = {
            "string": "str",
            "number": "float",
            "integer": "int",
            "boolean": "bool",
            "array": "List",
            "object": "Dict",
            "null": "None",
        }
        
        return type_map.get(json_type, "Any")
    
    def _format_default_value(self, default: Any, param_type: str) -> str:
        """Format default value for Python code.
        
        Args:
            default: Default value
            param_type: Parameter type
            
        Returns:
            Formatted default value as string
        """
        if param_type == "str":
            return repr(str(default))
        elif param_type == "int":
            return str(int(default))
        elif param_type == "float":
            return str(float(default))
        elif param_type == "bool":
            return str(bool(default))
        elif param_type == "List":
            if isinstance(default, list):
                return repr(default)
            return "[]"
        elif param_type == "Dict":
            if isinstance(default, dict):
                return repr(default)
            return "{}"
        else:
            return repr(default)
    
    def _generate_example_value(self, param_type: str) -> str:
        """Generate example value for a parameter type.
        
        Args:
            param_type: Parameter type
            
        Returns:
            Example value as string
        """
        examples = {
            "string": '"example"',
            "number": "42.0",
            "integer": "42",
            "boolean": "True",
            "array": "[]",
            "object": "{}",
        }
        
        return examples.get(param_type, '""')
    
    def _extract_return_info(self, description: str) -> tuple[str, str]:
        """Extract return type info from tool description's Returns: section.
        
        Parses the description to find a Returns: section and extracts:
        - return_type: A type hint string (e.g., "dict", "list[dict]")
        - return_description: The description of what's returned
        
        Args:
            description: Tool description that may contain Returns: section
            
        Returns:
            Tuple of (return_type, return_description)
            Returns ("Any", "Tool execution result") if no Returns: section found
        """
        if not description:
            return ("Any", "Tool execution result")
        
        # Look for "Returns:" section in description
        # Pattern matches "Returns:" followed by content until next section or end
        returns_pattern = r'Returns?:\s*\n?\s*(.*?)(?:\n\s*(?:Args?:|Example|Note|Raises?:|HIGH PTC|VERY HIGH|MEDIUM PTC|$)|\Z)'
        match = re.search(returns_pattern, description, re.IGNORECASE | re.DOTALL)
        
        if not match:
            return ("Any", "Tool execution result")
        
        returns_text = match.group(1).strip()
        
        # If returns_text is empty, return default
        if not returns_text:
            return ("Any", "Tool execution result")
        
        # Try to extract type hint from common patterns:
        # "dict: {...}" or "dict with..." or "Dictionary containing..."
        # "list[dict]" or "List of dicts"
        type_hint = "Any"
        
        type_patterns = [
            (r'^(dict|Dict)\s*[:{]', 'dict'),
            (r'^(list|List)\s*\[?.*?dict', 'List[Dict[str, Any]]'),
            (r'^(list|List)\s*\[?.*?str', 'List[str]'),
            (r'^(list|List)\s*\[?', 'List[Any]'),
            (r'^(str|string|String)', 'str'),
            (r'^(int|integer|Integer)', 'int'),
            (r'^(bool|boolean|Boolean)', 'bool'),
            (r'^(float|number|Number)', 'float'),
        ]
        
        for pattern, hint in type_patterns:
            if re.search(pattern, returns_text, re.IGNORECASE):
                type_hint = hint
                break
        
        return (type_hint, returns_text)


def generate_ptc_tool_module_from_openai_tools(
    openai_tools: List[Dict[str, Any]], server_name: str
) -> str:
    """Generate PTC tool module from OpenAI-format tool descriptions.
    
    This function converts tools from mcp_tool_desc_transform format to MCPToolInfo
    and generates a Python module for PTC usage.
    
    Args:
        openai_tools: List of tools in OpenAI format (from mcp_tool_desc_transform)
        server_name: Name of the MCP server
        
    Returns:
        Complete Python module code as string
        
    Example:
        from aworld.mcp_client.utils import mcp_tool_desc_transform
        
        # Get tools in OpenAI format
        tools = await mcp_tool_desc_transform(
            tools=["filesystem-server"],
            mcp_config=mcp_config
        )
        
        # Filter tools for specific server
        server_tools = [t for t in tools if t["function"]["name"].startswith("filesystem-server__")]
        
        # Generate module
        code = generate_ptc_tool_module_from_openai_tools(server_tools, "filesystem-server")
    """
    generator = ToolFunctionGenerator()
    mcp_tools = []
    
    for tool in openai_tools:
        if not isinstance(tool, dict) or "function" not in tool:
            continue
        
        func_info = tool["function"]
        tool_name = func_info.get("name", "")
        
        # Extract server name from tool name (format: "server_name__tool_name")
        if "__" in tool_name:
            parts = tool_name.split("__", 1)
            actual_server_name = parts[0]
            actual_tool_name = parts[1]
        else:
            # If no separator, assume tool_name is the actual tool name
            actual_server_name = server_name
            actual_tool_name = tool_name
        
        # Only process tools for the specified server
        if actual_server_name != server_name:
            continue
        
        description = func_info.get("description", "")
        parameters = func_info.get("parameters", {})
        
        # Convert OpenAI parameters format to JSON schema format
        input_schema = {
            "type": "object",
            "properties": {},
            "required": parameters.get("required", []),
        }
        
        for param_name, param_info in parameters.get("properties", {}).items():
            input_schema["properties"][param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", ""),
            }
            if "default" in param_info:
                input_schema["properties"][param_name]["default"] = param_info["default"]
        
        mcp_tool = MCPToolInfo(
            name=actual_tool_name,
            description=description,
            server_name=actual_server_name,
            input_schema=input_schema,
        )
        mcp_tools.append(mcp_tool)
    
    return generator.generate_tool_module(server_name, mcp_tools)
