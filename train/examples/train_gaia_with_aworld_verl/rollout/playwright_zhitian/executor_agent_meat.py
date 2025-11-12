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
from aworld.logs.util import logger


class ExecuteAgent(Agent):
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
        logger.info(f"[GaiaPlayWrightAgent] _process_messages processed: {processed}")
        return processed

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},
                           message: Message = None, **kwargs) -> List[ActionModel]:
        return await super().async_policy(observation, info, message, **kwargs)
