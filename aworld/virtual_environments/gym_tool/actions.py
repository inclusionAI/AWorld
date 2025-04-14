# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.config.common import Tools
from aworld.config.tool_action import GymAction
from aworld.core.envs.action_factory import ActionFactory
from aworld.virtual_environments.action import ExecutableAction


@ActionFactory.register(name=GymAction.PLAY.value.name,
                        desc=GymAction.PLAY.value.desc,
                        tool_name=Tools.GYM.value)
class Play(ExecutableAction):
    """"""