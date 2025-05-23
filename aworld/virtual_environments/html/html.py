# coding: utf-8

from aworld.virtual_environments.one_time_tool import OneTimeTool
from aworld.virtual_environments.tool_action import WriteAction
from aworld.core.envs.tool import ToolFactory


@ToolFactory.register(name="html", desc="html tool", supported_action=WriteAction)
class HtmlTool(OneTimeTool):
    """Html tool"""
