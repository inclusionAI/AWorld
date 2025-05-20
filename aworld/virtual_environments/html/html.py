# coding: utf-8

from aworld.config.common import Tools
from aworld.virtual_environments.one_time_tool import OneTimeTool
from aworld.virtual_environments.tool_action import WriteAction
from aworld.core.envs.tool import ToolFactory, Tool


@ToolFactory.register(name=Tools.HTML.value, desc="html tool", supported_action=WriteAction)
class HtmlTool(OneTimeTool):
    """Html tool"""
