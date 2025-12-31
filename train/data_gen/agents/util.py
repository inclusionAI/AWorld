# coding: utf-8
# Copyright (c) inclusionAI.
from typing import List

from train.data_gen.schema import GeneratedTool


def tools_meta(tools_desc: List[GeneratedTool], max_tool_num: int = 5) -> List[str]:
    """Generate tool metadata strings from tool descriptions.

    Args:
        tools_desc: List of tool description dicts
        max_tool_num: Maximum number of tools to include

    Returns:
        List of formatted tool metadata strings
    """
    tools_meta = []
    for i, tool in enumerate(tools_desc[:max_tool_num]):
        name = tool.spec.name
        desc = tool.spec.description
        params = tool.spec.parameters
        tools_meta.append(f"tool {i}, {name}: {desc}, params: {params}")
    return tools_meta
