# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Any, Dict, Tuple
from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult
from aworld.core.context.base import Context
from aworld.core.event import eventbus
from aworld.core.event.base import Message, Constants, TopicType
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger

from aworld.tools.utils import build_observation
from aworld.tools.human.actions import HumanExecuteAction


@ToolFactory.register(name="human_confirm",
                      desc="human confirm",
                      supported_action=HumanExecuteAction)
class HumanTool(AsyncTool):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Init document tool."""
        super(HumanTool, self).__init__(conf, **kwargs)
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
                                 ability=HumanExecuteAction.HUMAN_CONFIRM.value.name), {}

    def init(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        pass

    async def finished(self) -> bool:
        return self.step_finished

    async def do_step(self, actions: list[ActionModel], **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=HumanExecuteAction.HUMAN_CONFIRM.value.name)
        info = {}
        try:
            if not actions:
                raise ValueError("actions is empty")
            action = actions[0]
            confirm_content = action.params.get("confirm_content", "")
            if not confirm_content:
                raise ValueError("content invalid")
            output, error = await self.human_confirm(confirm_content)
            observation.content = output
            observation.action_result.append(
                ActionResult(is_done=True,
                             success=False if error else True,
                             content=f"{output}",
                             error=f"{error}",
                             keep=False))
            reward = 1.
        except Exception as e:
            fail_error = str(e)
        finally:
            self.step_finished = True
        info["exception"] = fail_error
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)

    async def human_confirm(self, confirm_content):
        error = None
        self.content = None
        try:
            self.content = confirm_content
            await eventbus.publish(Message(
                category=Constants.TASK,
                payload=confirm_content,
                sender=self.name(),
                session_id=Context.instance().session_id,
                topic=TopicType.HUMAN_CONFIRM
            ))
            return self.content, error
        except Exception as e:
            error = str(e)
            logger.warning(f"human_confirm error: {str(e)}")
        finally:
            pass

        return self.content, error
