import asyncio
import logging
from typing import List, Any, Dict, Union

from aworld.core.agent.base import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworldspace.models.exception import TaskTerminatedException
from aworldspace.routes.tasks import task_manager


class TaskAgent(Agent):

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},  **kwargs) -> Union[
        List[ActionModel], None]:
        context: Context = kwargs.get("context")
        if context and context.task_id:
            if await task_manager.check_task_is_terminated(context.task_id):
                logging.warning(f"TaskAgent-async_policy: task#{context.task_id} is terminated")
                raise TaskTerminatedException(f"TaskAgent-async_policy: task#{context.task_id} is terminated")

        # await asyncio.sleep(30)
        return await super().async_policy(observation, info, **kwargs)
