# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.core.envs.tool_action import GymAction
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.common import Tools
from aworld.virtual_environments import ExecutableAction


@ActionFactory.register(name=GymAction.PLAY.value.name,
                        desc=GymAction.PLAY.value.desc,
                        tool_name=Tools.GYM.value)
class GotoUrl(ExecutableAction):
    """"""