# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import uuid
from typing import AsyncGenerator, Tuple

from aworld.agents.loop_llm_agent import LoopableAgent
from aworld.core.agent.base import is_agent, AgentFactory
from aworld.core.agent.swarm import GraphBuildType
from aworld.core.common import ActionModel, Observation, TaskItem
from aworld.core.event.base import Message, Constants, TopicType, AgentMessage
from aworld.logs.util import logger
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.utils import endless_detect
from aworld.output.base import StepOutput


class AgentHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        super().__init__()
        self.runner = runner
        self.swarm = runner.swarm
        self.endless_threshold = runner.endless_threshold
        self.task_id = runner.task.id

        self.agent_calls = []

    @classmethod
    def name(cls):
        return "_agents_handler"


@HandlerFactory.register(name=f'__{Constants.AGENT}__')
class DefaultAgentHandler(AgentHandler):
    def is_valid_message(self, message: Message):
        if message.category != Constants.AGENT:
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
        if not data:
            # error message, p2p
            yield Message(
                category=Constants.OUTPUT,
                payload=StepOutput.build_failed_output(name=f"{message.caller or self.name()}",
                                                       step_num=0,
                                                       data="no data to process.",
                                                       task_id=self.task_id),
                sender=self.name(),
                session_id=session_id,
                headers=headers
            )
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(msg="no data to process.", data=data, stop=True),
                sender=self.name(),
                session_id=session_id,
                topic=TopicType.ERROR,
                headers=headers
            )
            return

        if isinstance(data, Tuple) and isinstance(data[0], Observation):
            data = data[0]
            message.payload = data
        # data is Observation
        if isinstance(data, Observation):
            if not self.swarm:
                msg = Message(
                    category=Constants.TASK,
                    payload=data.content,
                    sender=data.observer,
                    session_id=session_id,
                    topic=TopicType.FINISHED,
                    headers=headers
                )
                logger.info(f"FINISHED|agent handler send finished message: {msg}")
                yield msg
                return

            agent = self.swarm.agents.get(message.receiver)
            # agent + tool completion protocol.
            if agent and agent.finished and data.info.get('done'):
                self.swarm.cur_step += 1
                if agent.id() == self.swarm.communicate_agent.id():
                    msg = Message(
                        category=Constants.TASK,
                        payload=data.content,
                        sender=agent.id(),
                        session_id=session_id,
                        topic=TopicType.FINISHED,
                        headers=headers
                    )
                    logger.info(f"FINISHED|agent handler send finished message: {msg}")
                    yield msg
                else:
                    msg = Message(
                        category=Constants.AGENT,
                        payload=Observation(content=data.content),
                        sender=agent.id(),
                        session_id=session_id,
                        receiver=self.swarm.communicate_agent.id(),
                        headers=message.headers
                    )
                    logger.info(f"agent handler send agent message: {msg}")
                    yield msg
            else:
                if data.info.get('done'):
                    agent_name = self.agent_calls[-1]
                    async for event in self._stop_check(ActionModel(agent_name=agent_name, policy_info=data.content),
                                                        message):
                        yield event
                elif not message.receiver:
                    agent_name = message.sender
                    async for event in self._stop_check(ActionModel(agent_name=agent_name, policy_info=data.content),
                                                        message):
                        yield event
                else:
                    logger.info(f"agent handler send observation message: {message}")
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
                                                           data="action not a ActionModel.",
                                                           task_id=self.task_id),
                    sender=self.name(),
                    session_id=session_id,
                    headers=headers
                )
                msg = Message(
                    category=Constants.TASK,
                    payload=TaskItem(msg="action not a ActionModel.", data=data, stop=True),
                    sender=self.name(),
                    session_id=session_id,
                    topic=TopicType.ERROR,
                    headers=headers
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
                session_id=session_id,
                receiver=DefaultToolHandler.name(),
                headers=message.headers
            )
            logger.info(f"agent handler send tool message: {msg}")
            yield msg
        else:
            yield Message(
                category=Constants.OUTPUT,
                payload=StepOutput.build_finished_output(name=f"{message.caller or self.name()}",
                                                         step_num=0,
                                                         task_id=self.task_id),
                sender=self.name(),
                receiver=agents[0].tool_name,
                session_id=session_id,
                headers=headers
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
            async for event in self._stop_check(action, message):
                yield event
            return

        headers = {"context": message.context}
        session_id = message.session_id
        cur_agent = self.swarm.agents.get(agent_name)
        if not cur_agent or not agent:
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(msg=f"Can not find {agent_name} or {action.agent_name} agent in swarm.",
                                 data=action,
                                 stop=True),
                sender=self.name(),
                session_id=session_id,
                topic=TopicType.ERROR,
                headers=headers
            )
            return

        cur_agent._finished = False
        con = action.policy_info
        if action.params and 'content' in action.params:
            con = action.params['content']
        observation = Observation(content=con, observer=agent.id(), from_agent_name=agent.id())

        if agent.handoffs and agent_name not in agent.handoffs:
            if message.caller:
                message.receiver = message.caller
                message.caller = ''
                yield message
            else:
                yield Message(category=Constants.TASK,
                              payload=TaskItem(msg=f"Can not handoffs {agent_name} agent ", data=observation),
                              sender=self.name(),
                              session_id=session_id,
                              topic=TopicType.RERUN,
                              headers=headers)
            return

        headers = message.headers.copy()
        # headers.update({"agent_as_tool": True})
        yield Message(
            category=Constants.AGENT,
            payload=observation,
            caller=message.caller,
            sender=action.agent_name,
            session_id=session_id,
            receiver=action.tool_name,
            headers=headers,
        )

    async def _stop_check(self, action: ActionModel, message: Message) -> AsyncGenerator[Message, None]:
        if GraphBuildType.TEAM.value == self.swarm.build_type:
            caller = message.caller
            session_id = message.session_id
            agent = self.swarm.agents.get(action.agent_name)
            if ((not caller or caller == self.swarm.communicate_agent.id())
                    and (self.swarm.cur_step >= self.swarm.max_steps or self.swarm.finished or
                         (agent.id() == self.swarm.agent_graph.root_agent.id() and agent.finished))):
                logger.info(
                    f"FINISHED|_social_stop_check finished|{self.swarm.cur_step}|{self.swarm.max_steps}|{self.swarm.finished}")
                yield Message(
                    category=Constants.TASK,
                    payload=action.policy_info,
                    sender=agent.id(),
                    session_id=session_id,
                    topic=TopicType.FINISHED,
                    headers={"context": message.context}
                )
            agent = self.swarm.agents.get(action.agent_name)
            caller = self.swarm.agent_graph.root_agent.id() or message.caller
            if agent.id() != self.swarm.agent_graph.root_agent.id():
                logger.info(f"_stop_check Team|{agent.id()} --> {caller}")
                yield Message(
                    category=Constants.AGENT,
                    payload=Observation(content=action.policy_info),
                    sender=agent.id(),
                    session_id=message.session_id,
                    receiver=caller,
                    headers=message.headers
                )
                return
        if GraphBuildType.WORKFLOW.value != self.swarm.build_type:
            async for event in self._social_stop_check(action, message):
                yield event
        else:
            if self.swarm.has_cycle:
                async for event in self._loop_sequence_stop_check(action, message):
                    yield event
            else:
                async for event in self._sequence_stop_check(action, message):
                    yield event

    async def _sequence_stop_check(self, action: ActionModel, message: Message) -> AsyncGenerator[Message, None]:
        headers = {"context": message.context}
        session_id = message.session_id
        agent = self.swarm.agents.get(action.agent_name)
        ordered_agents = self.swarm.ordered_agents
        idx = next((i for i, x in enumerate(ordered_agents) if x == agent), -1)
        if idx == -1:
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(
                    msg=f"Can not find {action.agent_name} agent in ordered_agents: {self.swarm.ordered_agents}.",
                    data=action,
                    stop=True),
                sender=self.name(),
                session_id=session_id,
                topic=TopicType.ERROR,
                headers=headers
            )
            return

        # The last agent
        logger.info(f"_sequence_stop_check idx|{idx}|{len(self.swarm.ordered_agents)}")
        if idx == len(self.swarm.ordered_agents) - 1:
            receiver = None
            # agent loop
            if isinstance(agent, LoopableAgent):
                agent.cur_run_times += 1
                if not agent.finished:
                    receiver = agent.goto

            if receiver:
                yield Message(
                    category=Constants.AGENT,
                    payload=Observation(content=action.policy_info),
                    sender=agent.id(),
                    session_id=session_id,
                    receiver=receiver,
                    headers=message.headers
                )
            else:
                logger.info(f"FINISHED|_sequence_stop_check execute loop {self.swarm.cur_step}. "
                            f"message: {message}. session_id: {session_id}.")
                yield Message(
                    category=Constants.TASK,
                    payload=action.policy_info,
                    sender=agent.id(),
                    session_id=session_id,
                    topic=TopicType.FINISHED,
                    headers=headers
                )
            return

            # loop agent type
        if isinstance(agent, LoopableAgent):
            agent.cur_run_times += 1
            if agent.finished:
                receiver = self.swarm.ordered_agents[idx + 1].id()
            else:
                receiver = agent.goto
        else:
            # means the loop finished
            receiver = self.swarm.ordered_agents[idx + 1].id()
        yield Message(
            category=Constants.AGENT,
            payload=Observation(content=action.policy_info),
            sender=agent.id(),
            session_id=session_id,
            receiver=receiver,
            headers=message.headers
        )

    async def _loop_sequence_stop_check(self, action: ActionModel, message: Message) -> AsyncGenerator[Message, None]:
        headers = {"context": message.context}
        session_id = message.session_id
        agent = self.swarm.agents.get(action.agent_name)
        idx = next((i for i, x in enumerate(self.swarm.ordered_agents) if x == agent), -1)
        if idx == -1:
            # unknown agent, means something wrong
            yield Message(
                category=Constants.TASK,
                payload=action,
                sender=self.name(),
                session_id=session_id,
                topic=TopicType.ERROR,
                headers=headers
            )
            return
        if idx == len(self.swarm.ordered_agents) - 1:
            # supported sequence loop
            if self.swarm.cur_step >= self.swarm.max_steps:
                receiver = None
                # agent loop
                if isinstance(agent, LoopableAgent):
                    agent.cur_run_times += 1
                    if not agent.finished:
                        receiver = agent.goto

                if receiver:
                    yield Message(
                        category=Constants.AGENT,
                        payload=Observation(content=action.policy_info),
                        sender=agent.id(),
                        session_id=session_id,
                        receiver=receiver,
                        headers=message.headers
                    )
                else:
                    # means the task finished
                    logger.info(f"FINISHED|_loop_sequence_stop_check execute loop {self.swarm.cur_step}. ")
                    yield Message(
                        category=Constants.TASK,
                        payload=action.policy_info,
                        sender=agent.id(),
                        session_id=session_id,
                        topic=TopicType.FINISHED,
                        headers=headers
                    )
            else:
                self.swarm.cur_step += 1
                logger.debug(f"_loop_sequence_stop_check execute loop {self.swarm.cur_step}.")
                yield Message(
                    category=Constants.TASK,
                    payload='',
                    sender=agent.id(),
                    session_id=session_id,
                    topic=TopicType.START,
                    headers=headers
                )
            return

        if isinstance(agent, LoopableAgent):
            agent.cur_run_times += 1
            if agent.finished:
                receiver = self.swarm.ordered_agents[idx + 1].id()
            else:
                receiver = agent.goto
        else:
            # means the loop finished
            receiver = self.swarm.ordered_agents[idx + 1].id()
        yield Message(
            category=Constants.AGENT,
            payload=Observation(content=action.policy_info),
            sender=agent.name(),
            session_id=session_id,
            receiver=receiver,
            headers=message.headers
        )

    async def _social_stop_check(self, action: ActionModel, message: Message) -> AsyncGenerator[Message, None]:
        headers = {"context": message.context}
        agent = self.swarm.agents.get(action.agent_name)
        caller = message.caller
        session_id = message.session_id
        if endless_detect(self.agent_calls,
                          endless_threshold=self.endless_threshold,
                          root_agent_name=self.swarm.communicate_agent.id()):
            logger.info(
                f"FINISHED|_social_stop_check endless_detect|{self.agent_calls}|{self.endless_threshold}|{self.swarm.communicate_agent.id()}")
            yield Message(
                category=Constants.TASK,
                payload=action.policy_info,
                sender=agent.id(),
                session_id=session_id,
                topic=TopicType.FINISHED,
                headers=headers
            )
            return

        if not caller or caller == self.swarm.communicate_agent.id():
            if self.swarm.cur_step >= self.swarm.max_steps or self.swarm.finished:
                logger.info(
                    f"FINISHED|_social_stop_check finished|{self.swarm.cur_step}|{self.swarm.max_steps}|{self.swarm.finished}")
                yield Message(
                    category=Constants.TASK,
                    payload=action.policy_info,
                    sender=agent.id(),
                    session_id=session_id,
                    topic=TopicType.FINISHED,
                    headers=headers
                )
            else:
                self.swarm.cur_step += 1
                logger.info(f"_social_stop_check execute loop {self.swarm.cur_step}.")
                yield Message(
                    category=Constants.AGENT,
                    payload=Observation(content=action.policy_info),
                    sender=agent.id(),
                    session_id=session_id,
                    receiver=self.swarm.communicate_agent.id(),
                    headers=message.headers
                )
        else:
            idx = 0
            for idx, name in enumerate(self.agent_calls[::-1]):
                if name == agent.id():
                    break
            idx = len(self.agent_calls) - idx - 1
            if idx:
                caller = self.agent_calls[idx - 1]

            yield Message(
                category=Constants.AGENT,
                payload=Observation(content=action.policy_info),
                sender=agent.id(),
                session_id=session_id,
                receiver=caller,
                headers=message.headers
            )


    def is_group_finish(self, event: Message) -> bool:
        """Determine if an event triggers group completion"""
        if not isinstance(event, Message) or not event.group_id:
            return False

        agent_id = event.sender
        if not agent_id:
            return False

        agent = self.swarm.agents.get(agent_id)
        if not agent:
            return False

        return agent._finished and agent.id() == event.headers.get('root_agent_id', '')

    async def post_handle(self, input:Message, output: Message) -> Message:
        new_context = output.context.deep_copy()
        new_context._task = output.context.get_task()
        output.context = new_context
        if self.is_group_finish(output):
            from aworld.runners.state_manager import RuntimeStateManager
            state_mng = RuntimeStateManager.instance()
            await state_mng.finish_sub_group(output.group_id, output.headers.get('root_message_id'),
                                             [output])
            return None
        return output
