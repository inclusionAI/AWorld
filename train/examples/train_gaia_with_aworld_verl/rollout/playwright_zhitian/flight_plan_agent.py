# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, List, Any, Optional

from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import is_agent_by_name
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.output import Output
from aworld.core.context.base import Context
# from aworldspace.agents.fast_agent.tools.out_put_tools import build_parent_task_status_output, build_logo_output
import logging

class FlightPlanAgent(Agent):
    start_flag: bool = True

    def _process_messages(self, messages: List[Dict[str, Any]],
                          context: Context = None) -> Optional[List[Dict[str, Any]]]:
        if not messages:
            return messages

        preserved_tail = 3
        cutoff = max(len(messages) - preserved_tail, 0)
        processed: List[Dict[str, Any]] = []

        for idx, message in enumerate(messages):
            if idx < cutoff and isinstance(message, dict) and message.get("role") == "tool":
                modified_message = dict(message)
                modified_message["content"] = "history tool return, you may ignore it."
                processed.append(modified_message)
            else:
                processed.append(message)
        logging.info(f"[GaiaPlayWrightAgent] _process_messages processed: {processed}")
        return processed

    # async def send_logo_output(self, message: Message):
    #     await self.send_output(message=message, data=build_logo_output())

    # async def send_outputs(self, message: Message, list_data: list[str]):
    #     for data in list_data:
    #         await self.send_output(message=message, data=data)

    # async def send_output(self, message: Message, data: str):
    #     await message.context.outputs.add_output(Output(task_id=message.task_id, data=data))

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},
                           message: Message = None, **kwargs) -> List[ActionModel]:
        if self.start_flag:
            # await self.send_outputs(message=message,
            #                         list_data=[build_logo_output(), f'OK, I have received your task.\n\n'])
            self.start_flag = False

        return await super().async_policy(observation, info, message, **kwargs)


    max_loop = 100

    async def should_terminate_loop(self, message: Message) -> bool:
        return self.loop_step >= self.max_loop

        # # special process of execute, if the tool is agent, split the content by &
        # content = action_model_list[0].policy_info
        # print(f"begin_action_model_list: {action_model_list}")
        # if is_agent_by_name(action_model_list[0].tool_name):
        #     action_model_list = [ActionModel(tool_name=action_model_list[0].tool_name,
        #                                      tool_call_id=action_model_list[0].tool_call_id,
        #                                      agent_name=action_model_list[0].agent_name,
        #                                      action_name=action_model_list[0].action_name,
        #                                      policy_info=content)]
        #     # actions = content.split('&')
        #     # action_model_list = [ActionModel(tool_name=action_model_list[0].tool_name,
        #     #                                  tool_call_id=action_model_list[0].tool_call_id,
        #     #                                  agent_name=action_model_list[0].agent_name,
        #     #                                  action_name=action_model_list[0].action_name,
        #     #                                  policy_info=action) for idx, action in enumerate(actions)]
        # return action_model_list