# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Dict, List, Any

from aworld.config import RunConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.ralph_loop.ralph_runner import RalphRunner
from aworld.runners.utils import execute_runner


class RalphAgent(BaseAgent):

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        logger.info(f"Ralph mode execute task: {observation.content} starting...")

        task = message.context.get_task()
        completion_criteria = None
        if not task:
            task = observation.content
            completion_criteria = observation.info.get("completion_criteria")

        # Ralph mode pipeline (SOP)
        runner = RalphRunner(task=task, completion_criteria=completion_criteria)
        res = await execute_runner(runners=runner, run_conf=RunConfig())
        res = res.get("0")
        logger.info(f"Ralph mode execute task finished.")
        return [ActionModel(agent_name=self.id(), policy_info=res.answer)]
