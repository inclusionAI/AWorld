# coding: utf-8

from examples.tools.one_time_tool import OneTimeTool
from examples.tools.tool_action import WriteAction
from aworld.core.envs.tool import ToolFactory


@ToolFactory.register(name="html", desc="html tool", supported_action=WriteAction)
class HtmlTool(OneTimeTool):
    """Html tool"""
