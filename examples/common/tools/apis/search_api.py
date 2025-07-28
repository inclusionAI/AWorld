# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.core.tool.base import ToolFactory
from aworld.tools.template_tool import TemplateTool
from examples.common.tools.tool_action import SearchAction


@ToolFactory.register(name="search_api",
                      desc="search tool",
                      supported_action=SearchAction,
                      conf_file_name=f'search_api_tool.yaml')
class SearchTool(TemplateTool):
    """Search Tool"""
