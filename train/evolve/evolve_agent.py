# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Dict, List, Any

from aworld.core.agent.base import BaseAgent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.experimental.aworld_cli.core import agent
from aworld.logs.util import logger
from aworld.runners.utils import execute_runner
from train.evolve.config import EvolutionConfig
from train.evolve.evolution_runner import EvolutionRunner


@agent(name="evolve_agent",
       desc="Possess the ability to self-evolve, conduct training and evaluation, and perform data synthesis.")
class EvolveAgent(BaseAgent):
    """Evolution pipeline wrapper, as a skill (TODO)."""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        logger.info(f"Task: {observation.content} auto evolve starting...")

        task = message.context.get_task()
        if not task:
            task = observation.content

        evolve_conf = EvolutionConfig()
        # Pipeline that can be used independently
        runner = EvolutionRunner(task=task, config=evolve_conf)
        res = await execute_runner(runners=runner, run_conf=evolve_conf.run_conf)
        res = res.get("0")
        logger.info(f"Task: {observation.content} auto evolve finished.")
        return [ActionModel(agent_name=self.id(), policy_info=res.answer)]
