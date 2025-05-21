# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import AsyncGenerator, Tuple

from aworld.core.agent.base import is_agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Message, EventType
from aworld.core.task import TaskItem
from aworld.logs.util import logger
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.event_runner import TaskType
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.utils import endless_detect


class DefaultAgentHandler(DefaultHandler):
    def __init__(self, swarm: Swarm):
        self.swarm = swarm

    @classmethod
    def name(cls):
        return "_sequential_agents_handler"

    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if message.category != EventType.AGENT:
            return

        data = message.payload
        if not data:
            # error message, p2p
            yield Message(
                category=EventType.TASK,
                payload=TaskItem(msg="no data to process.", data=data, stop=True),
                sender=self.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.ERROR
            )
            return

        if isinstance(data, Tuple) and isinstance(data[0], Observation):
            data = data[0]
            message.payload = data
        # data is Observation
        if isinstance(data, Observation):
            yield message
            return

        # data is List[ActionModel]
        for action in data:
            if not isinstance(action, ActionModel):
                # error message, p2p
                yield Message(
                    category=EventType.TASK,
                    payload=TaskItem(msg="action not a ActionModel.", data=data, stop=True),
                    sender=self.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.ERROR
                )
                return

        tools = []
        agents = []
        for action in data:
            if is_agent(action):
                agents.append(action)
            else:
                tools.append(action)

        if tools:
            yield Message(
                category=EventType.TOOL,
                payload=tools,
                sender=self.name(),
                session_id=Context.instance().session_id,
                receiver=DefaultToolHandler.name(),
                group_name=message.group_name
            )

        if agents and message.group_name:
            Context.instance().context_info[message.group_name] = (
                message.sender, {agent.name(): None for agent in agents}
            )

        for agent in agents:
            async for event in self._agent(agent, message):
                yield event

    async def _agent(self, action: ActionModel, message: Message):
        agent = self.swarm.agents.get(action.agent_name)
        # be handoff
        agent_name = action.tool_name
        if not agent_name:
            async for event in self._stop_check(action, message.caller):
                yield event
            return

        cur_agent = self.swarm.agents.get(agent_name)
        if not cur_agent or not agent:
            yield Message(
                category=EventType.TASK,
                payload=TaskItem(msg=f"Can not find {agent_name} or {action.agent_name} agent in swarm.",
                                 data=action,
                                 stop=True),
                sender=self.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.ERROR
            )
            return

        cur_agent._finished = False
        con = action.policy_info
        if action.params and 'content' in action.params:
            con = action.params['content']
        observation = Observation(content=con, observer=agent.name(), from_agent_name=agent.name())

        if agent.handoffs and agent_name not in agent.handoffs:
            if message.caller:
                message.receiver = message.caller
                message.caller = ''
                yield message
            else:
                yield Message(category=EventType.TASK,
                              payload=TaskItem(msg=f"Can not handoffs {agent_name} agent ", data=observation),
                              sender=self.name(),
                              session_id=Context.instance().session_id,
                              topic=TaskType.RERUN)
            return

        yield Message(
            category=EventType.AGENT,
            payload=observation,
            caller=message.caller,
            sender=action.agent_name,
            session_id=Context.instance().session_id,
            receiver=action.tool_name,
            topic=EventType.AGENT,
            group_name=message.group_name
        )

    async def _stop_check(self, action: ActionModel, caller: str):
        agent = self.swarm.agents.get(action.agent_name)
        idx = next((i for i, x in enumerate(self.swarm.ordered_agents) if x == agent), -1)
        if idx == -1:
            yield Message(
                category=EventType.TASK,
                payload=action,
                sender=self.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.ERROR,
            )
        elif idx == len(self.swarm.ordered_agents) - 1:
            # supported sequence loop
            if self.swarm.cur_step >= self.swarm.max_steps:
                # means the task finished
                yield Message(
                    category=EventType.TASK,
                    payload=action.policy_info,
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.FINISHED
                )
            else:
                self.swarm.cur_step += 1
                logger.info(f"execute loop {self.swarm.cur_step}.")
                yield Message(
                    category=EventType.TASK,
                    payload='',
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.START
                )
        else:
            # means the loop finished
            yield Message(
                category=EventType.AGENT,
                payload=Observation(content=action.policy_info),
                sender=agent.name(),
                session_id=Context.instance().session_id,
                receiver=self.swarm.ordered_agents[idx + 1].name()
            )


class DefaultAgentSocialHandler(DefaultAgentHandler):
    def __init__(self, swarm: Swarm, endless_threshold: int):
        super().__init__(swarm)

        self.endless_threshold = endless_threshold
        self.agent_calls = []

    @classmethod
    def name(cls):
        return "_social_agents_handler"

    async def _stop_check(self, action: ActionModel, caller: str):
        self.agent_calls.append(action.agent_name)

        agent = self.swarm.agents.get(action.agent_name)

        if endless_detect(self.agent_calls,
                          endless_threshold=self.endless_threshold,
                          root_agent_name=self.swarm.communicate_agent.name()):
            yield Message(
                category=EventType.TASK,
                payload=action.policy_info,
                sender=agent.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.FINISHED
            )
            return

        if not caller or caller == self.swarm.communicate_agent.name():
            if self.swarm.cur_step >= self.swarm.max_steps or self.swarm.finished:
                yield Message(
                    category=EventType.TASK,
                    payload=action.policy_info,
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.FINISHED
                )
            else:
                self.swarm.cur_step += 1
                logger.info(f"execute loop {self.swarm.cur_step}.")
                yield Message(
                    category=EventType.TASK,
                    payload=Observation(content=action.policy_info),
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.START,
                    receiver=self.swarm.communicate_agent.name()
                )
        else:
            yield Message(
                category=EventType.AGENT,
                payload=Observation(content=action.policy_info),
                sender=agent.name(),
                session_id=Context.instance().session_id,
                receiver=caller
            )
