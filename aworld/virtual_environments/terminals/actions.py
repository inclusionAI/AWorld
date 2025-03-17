# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.core.envs.tool_action import ShellAction
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.common import Tools, ActionModel, ActionResult
from aworld.virtual_environments.action import ExecutableAction


@ActionFactory.register(name=ShellAction.EXECUTE_SCRIPT.value.name,
                        desc=ShellAction.EXECUTE_SCRIPT.value.desc,
                        tool_name=Tools.SHELL.value)
class ShellAction(ExecutableAction):
    """Only one action, define it, implemented can be omitted. Act in tool."""
