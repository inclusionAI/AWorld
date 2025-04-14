# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import List, Dict

from aworld.core.envs.tool import ToolFactory
from aworld.virtual_environments import tool_action_desc


def get_actions() -> List[str]:
    res = []
    for _, tool_info in tool_action_desc().items():
        actions = tool_info.get("actions")
        if not actions:
            continue

        for action in actions:
            res.append(action['name'])
    return res


def get_actions_by_tools(tool_names: Dict = None) -> List[str]:
    if not tool_names:
        return get_actions()

    res = []
    for tool_name, tool_info in tool_action_desc().items():
        if tool_name not in tool_names:
            continue

        actions = tool_info.get("actions")
        if not actions:
            continue

        for action in actions:
            res.append(action['name'])
    return res


def get_tool_desc():
    return tool_action_desc()


def get_tool_desc_by_name(name: str):
    return tool_action_desc().get(name, None)


def is_tool_by_name(name: str) -> bool:
    return name in ToolFactory
