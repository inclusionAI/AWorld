# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import AsyncGenerator, Tuple

from aworld.core.agent.base import is_agent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import ActionModel, Observation, TaskItem
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants
from aworld.logs.util import logger
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.utils import endless_detect, TaskType
from aworld.output.base import StepOutput, Output, ToolResultOutput


class AgentHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        self.swarm = runner.swarm
        self.endless_threshold = runner.endless_threshold

        self.agent_calls = []

    @classmethod
    def name(cls):
        return "_agents_handler"


class DefaultAgentHandler(AgentHandler):
    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if message.category != Constants.AGENT:
            return

        data = message.payload
        if not data:
            # error message, p2p
            yield Message(
                category=Constants.OUTPUT,
                payload=StepOutput.build_failed_output(name=f"{message.caller or self.name()}",
                                                       step_num=0,
                                                       data="no data to process."),
                sender=self.name(),
                session_id=Context.instance().session_id
            )
            yield Message(
                category=Constants.TASK,
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
            agent = self.swarm.agents.get(message.receiver)
            # agent + tool completion protocol.
            if agent and agent.finished and data.info.get('done'):
                self.swarm.cur_step += 1
                if agent.name() == self.swarm.communicate_agent.name():
                    msg = Message(
                        category=Constants.TASK,
                        payload=data.content,
                        sender=agent.name(),
                        session_id=Context.instance().session_id,
                        topic=TaskType.FINISHED
                    )
                    logger.info(f"agent handler send finished message: {msg}")
                    yield msg
                else:
                    msg = Message(
                        category=Constants.AGENT,
                        payload=Observation(content=data.content),
                        sender=agent.name(),
                        session_id=Context.instance().session_id,
                        receiver=self.swarm.communicate_agent.name()
                    )
                    logger.info(f"agent handler send agent message: {msg}")
                    yield msg
            else:
                logger.info(f"agent handler send message: {message}")
                yield message
            return

        # data is List[ActionModel]
        for action in data:
            if not isinstance(action, ActionModel):
                # error message, p2p
                yield Message(
                    category=Constants.OUTPUT,
                    payload=StepOutput.build_failed_output(name=f"{message.caller or self.name()}",
                                                           step_num=0,
                                                           data="action not a ActionModel."),
                    sender=self.name(),
                    session_id=Context.instance().session_id
                )
                msg = Message(
                    category=Constants.TASK,
                    payload=TaskItem(msg="action not a ActionModel.", data=data, stop=True),
                    sender=self.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.ERROR
                )
                logger.info(f"agent handler send task message: {msg}")
                yield msg
                return

        tools = []
        agents = []
        for action in data:
            if is_agent(action):
                agents.append(action)
            else:
                tools.append(action)

        if tools:
            msg = Message(
                category=Constants.TOOL,
                payload=tools,
                sender=self.name(),
                session_id=Context.instance().session_id,
                receiver=DefaultToolHandler.name(),
            )
            logger.info(f"agent handler send tool message: {msg}")
            yield msg
        else:
            yield Message(
                category=Constants.OUTPUT,
                payload=StepOutput.build_finished_output(name=f"{message.caller or self.name()}",
                                                         step_num=0),
                sender=self.name(),
                receiver=agents[0].tool_name,
                session_id=Context.instance().session_id
            )

        for agent in agents:
            async for event in self._agent(agent, message):
                logger.info(f"agent handler send message: {event}")
                yield event

    async def _agent(self, action: ActionModel, message: Message):
        self.agent_calls.append(action.agent_name)
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
                category=Constants.TASK,
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
                yield Message(category=Constants.TASK,
                              payload=TaskItem(msg=f"Can not handoffs {agent_name} agent ", data=observation),
                              sender=self.name(),
                              session_id=Context.instance().session_id,
                              topic=TaskType.RERUN)
            return

        yield Message(
            category=Constants.AGENT,
            payload=observation,
            caller=message.caller,
            sender=action.agent_name,
            session_id=Context.instance().session_id,
            receiver=action.tool_name,
        )

    async def _stop_check(self, action: ActionModel, caller: str) -> AsyncGenerator[Message, None]:
        if 'social' in self.swarm.topology_type:
            async for event in self._social_stop_check(action, caller):
                yield event
        else:
            if 'loop' in self.swarm.topology_type:
                async for event in self._loop_sequence_stop_check(action, caller):
                    yield event
            else:
                async for event in self._sequence_stop_check(action, caller):
                    yield event

    async def _sequence_stop_check(self, action: ActionModel, caller: str) -> AsyncGenerator[Message, None]:
        agent = self.swarm.agents.get(action.agent_name)
        idx = next((i for i, x in enumerate(self.swarm.ordered_agents) if x == agent), -1)
        if idx == -1:
            yield Message(
                category=Constants.TASK,
                payload=action,
                sender=self.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.ERROR,
            )
        elif idx == len(self.swarm.ordered_agents) - 1:
            logger.info(f"execute loop {self.swarm.cur_step}.")
            yield Message(
                category=Constants.TASK,
                payload=action.policy_info,
                sender=agent.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.FINISHED
            )
        else:
            # means the loop finished
            yield Message(
                category=Constants.AGENT,
                payload=Observation(content=action.policy_info),
                sender=agent.name(),
                session_id=Context.instance().session_id,
                receiver=self.swarm.ordered_agents[idx + 1].name()
            )

    async def _loop_sequence_stop_check(self, action: ActionModel, caller: str) -> AsyncGenerator[Message, None]:
        agent = self.swarm.agents.get(action.agent_name)
        idx = next((i for i, x in enumerate(self.swarm.ordered_agents) if x == agent), -1)
        if idx == -1:
            yield Message(
                category=Constants.TASK,
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
                    category=Constants.TASK,
                    payload=action.policy_info,
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.FINISHED
                )
            else:
                self.swarm.cur_step += 1
                logger.info(f"execute loop {self.swarm.cur_step}.")
                yield Message(
                    category=Constants.TASK,
                    payload='',
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.START
                )
        else:
            # means the loop finished
            yield Message(
                category=Constants.AGENT,
                payload=Observation(content=action.policy_info),
                sender=agent.name(),
                session_id=Context.instance().session_id,
                receiver=self.swarm.ordered_agents[idx + 1].name()
            )

    async def _social_stop_check(self, action: ActionModel, caller: str) -> AsyncGenerator[Message, None]:
        agent = self.swarm.agents.get(action.agent_name)

        if endless_detect(self.agent_calls,
                          endless_threshold=self.endless_threshold,
                          root_agent_name=self.swarm.communicate_agent.name()):
            yield Message(
                category=Constants.TASK,
                payload=action.policy_info,
                sender=agent.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.FINISHED
            )
            return

        if not caller or caller == self.swarm.communicate_agent.name():
            if self.swarm.cur_step >= self.swarm.max_steps or self.swarm.finished:
                yield Message(
                    category=Constants.TASK,
                    payload=action.policy_info,
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.FINISHED
                )
            else:
                self.swarm.cur_step += 1
                logger.info(f"execute loop {self.swarm.cur_step}.")
                yield Message(
                    category=Constants.AGENT,
                    payload=Observation(content=action.policy_info),
                    sender=agent.name(),
                    session_id=Context.instance().session_id,
                    receiver=self.swarm.communicate_agent.name()
                )
        else:
            idx = 0
            for idx, name in enumerate(self.agent_calls[::-1]):
                if name == agent.name():
                    break
            idx = len(self.agent_calls) - idx - 1
            if idx:
                caller = self.agent_calls[idx - 1]

            yield Message(
                category=Constants.AGENT,
                payload=Observation(content=action.policy_info),
                sender=agent.name(),
                session_id=Context.instance().session_id,
                receiver=caller
            )
