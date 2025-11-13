# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Tuple, Any

from aworld.core.common import ToolActionInfo, ParamInfo, ActionModel, ActionResult
from aworld.core.tool.action import ToolAction, ExecutableAction
from aworld.core.tool.action_factory import ActionFactory
from aworld.tools.async_template_tool import TemplateTool

from aworld.core.tool.base import ToolFactory


class HelloworldAction(ToolAction):
    HELLO_WORLD = ToolActionInfo(
        name="hello_world",
        input_params={
            "goal": ParamInfo(
                name="goal",
                type="str",
                required=True,
                desc="hello world."
            ),
        },
        desc="hello world.")


@ActionFactory.register(name=HelloworldAction.HELLO_WORLD.value.name,
                        desc=HelloworldAction.HELLO_WORLD.value.desc,
                        tool_name="hello_world")
class ExecuteAction(ExecutableAction):
    """Only one action, define it, implemented can be omitted. Act in tool."""
    async def async_act(self, action: ActionModel, **kwargs) -> Tuple[ActionResult, Any]:
        return ActionResult(content="hello world!", keep=True, is_done=True), None


# asyn is True means the tool name is `async_hello_world`
@ToolFactory.register(name="hello_world", desc="hello world tool", asyn=True, supported_action=HelloworldAction)
class HelloWorldTool(TemplateTool):
    """Hello world tool."""
