# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import AsyncGenerator

from aworld.core.agent.base import AgentFactory
from aworld.core.event.base import Message, Constants
from aworld.runners import HandlerFactory
from aworld.runners.handler import DefaultHandler
from aworld.runners.state_manager import RuntimeStateManager


@HandlerFactory.register(name=f'__{Constants.HUMAN}__')
class DefaultHumanHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        super().__init__()
        self.runner = runner
        self.swarm = runner.swarm
        self.endless_threshold = runner.endless_threshold
        self.task_id = runner.task.id

        self.agent_calls = []

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

        human_input = input(f"Human Confirm Info: {data}\nPlease Input:")

        # # mark message node success
        # state_mng = RuntimeStateManager.instance()
        # state_mng.run_succeed(node_id=message.id,
        #                       result_msg="run DefaultHumanHandler succeed",
        #                       results=[human_input])

        yield Message(
            category=Constants.HUMAN,
            sender=self.name,
            receiver=message.sender,
            session_id=session_id,
            payload=human_input,
            headers=headers,
        )
        return
