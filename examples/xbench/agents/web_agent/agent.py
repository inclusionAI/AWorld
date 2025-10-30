from typing import Dict, Any, List


from aworld.agents.amni_llm_agent import ApplicationAgent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger


class WebAgent(ApplicationAgent):
    """
    Web browsing agent for reading and analyzing web page content
    """
    max_loop = 100

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        return await super().async_policy(observation, info, message, **kwargs)

    async def async_post_run(self, policy_result: List[ActionModel], policy_input: Observation,
                             message: Message = None) -> Message:
        if self._finished:
            try:
                # Get todo_info and actions_info, handle potential None values and exceptions
                todo_info = await self.get_task_context(message).get_todo_info()
                actions_info = await self.get_task_context(message).get_actions_info()

                # Set to task_output_object
                self.get_task_context(message).task_output_object.todo_info = todo_info
                self.get_task_context(message).task_output_object.actions_info = actions_info

                # Safely concatenate strings, handle None values
                if todo_info is not None:
                    policy_result[0].policy_info += todo_info
                if actions_info is not None:
                    policy_result[0].policy_info += actions_info

            except Exception as e:
                # Log error but don't interrupt execution flow
                logger.error(f"❌ Error in WebAgent async_post_run: {e}")
                # Ensure policy_result[0].policy_info is string type
                if not isinstance(policy_result[0].policy_info, str):
                    policy_result[0].policy_info = str(policy_result[0].policy_info) if policy_result[
                        0].policy_info else ""

        return await super().async_post_run(policy_result, policy_input, message)

    async def should_terminate_loop(self, message: Message) -> bool:
        return self.loop_step >= self.max_loop
