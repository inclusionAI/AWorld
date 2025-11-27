# qwen_agent tools base
from typing import Any, Dict, Optional, Union
import json


class BaseTool:
    """Base class for all tools"""

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}

    def _verify_json_format_args(self, params: Union[str, dict]) -> dict:
        """Verify and convert parameters to dict format"""
        if isinstance(params, str):
            try:
                return json.loads(params)
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON format: {params}")
        return params


def register_tool(tool_name: str):
    """Decorator to register a tool"""
    def decorator(func):
        func._tool_name = tool_name
        return func
    return decorator
