# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import AsyncGenerator

from aworld.core.agent.base import AgentFactory
from aworld.core.event.base import Message, Constants
from aworld.runners import HandlerFactory


@HandlerFactory.register(name=f'__{Constants.HUMAN}__')
class DefaultHumanHandler:
    def is_valid_message(self, message: Message):
        if message.category != Constants.HUMAN:
            if self.swarm and message.sender in self.swarm.agents and message.sender in AgentFactory:
                if self.agent_calls:
                    if self.agent_calls[-1] != message.sender:
                        self.agent_calls.append(message.sender)
                else:
                    self.agent_calls.append(message.sender)
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if not self.is_valid_message(message):
            return

        headers = {"context": message.context}
        session_id = message.session_id
        data = message.payload

        input(f"Human Confirm Info: {data}\nPlease Input:")

        yield Message(
            category=Constants.HUMAN,
            sender=self.name,
            receiver=message.sender,
            session_id=session_id,
            payload=data,
            headers=headers,
        )
        return
