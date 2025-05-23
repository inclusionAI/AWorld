# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.core.envs.tool import ToolFactory
from aworld.virtual_environments.one_time_tool import OneTimeTool
from aworld.virtual_environments.tool_action import SearchAction


@ToolFactory.register(name="search_api",
                      desc="search tool",
                      supported_action=SearchAction,
                      conf_file_name=f'search_api_tool.yaml')
class SearchTool(OneTimeTool):
    """Search Tool"""
