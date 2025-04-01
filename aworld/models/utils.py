# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, Any, List

from aworld.logs.util import logger


def agent_desc_transform(
    agent_dict: Dict[str, Any], agents: List[str] = None, provider: str = "openai"
) -> List[Dict[str, Any]]:
    """Default implement transform framework standard protocol to openai protocol of agent description."""
    agent_as_tools = []
    if provider and "openai" in provider:
        for agent_name, agent_info in agent_dict.items():
            if agents and agent_name not in agents:
                logger.info(
                    f"{agent_name} can not supported in {agents}, you can set `tools` params to support it."
                )
                continue

            for action in agent_info["abilities"]:
                # 构建参数属性
                properties = {}
                required = []
                for param_name, param_info in action["params"].items():
                    properties[param_name] = {
                        "description": param_info["desc"],
                        "type": (
                            param_info["type"]
                            if param_info["type"] != "str"
                            else "string"
                        ),
                    }
                    if param_info.get("required", False):
                        required.append(param_name)

                openai_function_schema = {
                    "name": f'{agent_name}__{action["name"]}',
                    "description": action["desc"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }

                agent_as_tools.append(
                    {"type": "function", "function": openai_function_schema}
                )
    return agent_as_tools


def tool_desc_transform(
    tool_dict: Dict[str, Any], tools: List[str] = None, provider: str = "openai"
) -> List[Dict[str, Any]]:
    """Default implement transform framework standard protocol to openai protocol of tool description."""
    openai_tools = []
    if provider and "openai" in provider:
        for tool_name, tool_info in tool_dict.items():
            if tools and tool_name not in tools:
                logger.warning(
                    f"{tool_name} can not supported in {tools}, you can set `tools` params to support it."
                )
                continue

            for action in tool_info["actions"]:
                # 构建参数属性
                properties = {}
                required = []
                for param_name, param_info in action["params"].items():
                    properties[param_name] = {
                        "description": param_info["desc"],
                        "type": (
                            param_info["type"]
                            if param_info["type"] != "str"
                            else "string"
                        ),
                    }
                    if param_info.get("required", False):
                        required.append(param_name)

                openai_function_schema = {
                    "name": f'{tool_name}__{action["name"]}',
                    "description": action["desc"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }

                openai_tools.append(
                    {"type": "function", "function": openai_function_schema}
                )
    return openai_tools
