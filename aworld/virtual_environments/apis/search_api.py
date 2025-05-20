# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.config.common import Tools
from aworld.core.envs.tool import ToolFactory
from aworld.virtual_environments.one_time_tool import OneTimeTool
from aworld.virtual_environments.tool_action import SearchAction


@ToolFactory.register(name=Tools.SEARCH_API.value,
                      desc="search tool",
                      supported_action=SearchAction,
                      conf_file_name=f'{Tools.SEARCH_API.value}_tool.yaml')
class SearchTool(OneTimeTool):
    """Search Tool"""
