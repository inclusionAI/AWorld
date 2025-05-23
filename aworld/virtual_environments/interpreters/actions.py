# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.virtual_environments.tool_action import PythonToolAction
from aworld.core.envs.action_factory import ActionFactory
from aworld.virtual_environments.action import ExecutableAction


@ActionFactory.register(name=PythonToolAction.EXECUTE.value.name,
                        desc=PythonToolAction.EXECUTE.value.desc,
                        tool_name="python_execute")
class ExecuteAction(ExecutableAction):
    """Only one action, define it, implemented can be omitted."""