import json
import logging
from typing import List

from aworld.config import AgentConfig
from aworld.core.agent.base import Agent
from aworld.models.llm import acall_llm_model
from aworld.output import MessageOutput


class StreamOutputAgent(Agent):

    def __init__(self, conf: AgentConfig, **kwargs
                 ):
        super().__init__(conf)


    async def async_call_llm(self, messages, json_parse = False) -> MessageOutput:
        # Async streaming with acall_llm_model
        async def async_generator():
            async for chunk in await acall_llm_model(self.llm, messages, stream=True):
                if chunk.content:
                    yield chunk.content

        return MessageOutput(source=async_generator(), json_parse=json_parse)
