# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

CONTEXT_SKILL = "SKILL"

class ContextExecuteAction(ToolAction):
    """Definition of Context visit and setting supported action."""


    """
    Agent Skills Support
    """

    ACTIVE_SKILL = ToolActionInfo(
        name="active_skill",
        input_params={"skill_name": ParamInfo(name="skill_name",
                                                 type="str",
                                                 required=True,
                                                 desc="name of the skill to be activated")},
        desc="activate a skill help agent to perform a task")

    OFFLOAD_SKILL = ToolActionInfo(
        name="offload_skill",
        input_params={"skill_name": ParamInfo(name="skill_name",
                                                   type="str",
                                                   required=True,
                                                   desc="name of the skill to be offloaded")},
        desc="offload a skill help agent to perform a task")


@ToolFactory.register(name=CONTEXT_SKILL,
                      desc=CONTEXT_SKILL,
                      supported_action=ContextExecuteAction)
class ContextSkillTool(AsyncTool):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Init document tool."""
        super(ContextSkillTool, self).__init__(conf, **kwargs)
        self.cur_observation = None
        self.content = None
        self.keyframes = []
        self.init()
        self.step_finished = True

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        await super().reset(seed=seed, options=options)

        await self.close()
        self.step_finished = True
        return build_observation(observer=self.name(),
                                 ability=ContextExecuteAction.ACTIVE_SKILL.value.name), {}

    def init(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        pass

    async def finished(self) -> bool:
        return self.step_finished

    async def do_step(self, actions: list[ActionModel], message:Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=ContextExecuteAction.ACTIVE_SKILL.value.name)
        info = {}
        try:
            if not actions:
                raise ValueError("actions is empty")
            
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            action = actions[0]
            action_name = action.action_name
            if action_name == ContextExecuteAction.ACTIVE_SKILL.value.name:
                skill_name = action.params.get("skill_name", "")
                if not skill_name:
                    raise ValueError("skill name invalid")
                result = await message.context.active_skill(skill_name, namespace=action.agent_name)
                if not result:
                    raise ValueError("active skill failed")
            elif action_name == ContextExecuteAction.OFFLOAD_SKILL.value.name:
                skill_name = action.params.get("skill_name", "")
                if not skill_name:
                    raise ValueError("skill name invalid")
                result = await message.context.offload_skill(skill_name, namespace=action.action_name)
                if not result:
                    raise ValueError("offload skill failed")
            else:
                raise ValueError("action name invalid")

            observation.content = result
            observation.action_result.append(
                ActionResult(is_done=True,
                             success=True,
                             content=f"{result}",
                             keep=False))
            reward = 1.
        except Exception as e:
            fail_error = str(e)
            logger.warn(f"CONTEXTTool|failed do_step: {traceback.format_exc()}")
        finally:
            self.step_finished = True
        info["exception"] = fail_error
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)

 