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
from aworld.core.exceptions import AworldException
from aworld.logs.util import logger
from aworld.planner.models import StepInfo
from aworld.planner.parse import parse_plan
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.utils import endless_detect
from aworld.output.base import StepOutput
from aworld.utils.run_util import exec_agent, exec_tool


class AgentHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        self.runner = runner
        self.swarm = runner.swarm
        self.endless_threshold = runner.endless_threshold
        self.task_id = runner.task.id

        self.agent_calls = []

    @classmethod
    def name(cls):
        return "_agents_handler"


class DefaultAgentHandler(AgentHandler):
    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if message.category != Constants.AGENT:
            if message.sender in self.swarm.agents and message.sender in AgentFactory:
                if self.agent_calls:
                    if self.agent_calls[-1] != message.sender:
                        self.agent_calls.append(message.sender)
                else:
                    self.agent_calls.append(message.sender)
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

        yield Message(
            category=Constants.AGENT,
            payload=observation,
            caller=message.caller,
            sender=action.agent_name,
            session_id=session_id,
            receiver=action.tool_name,
            headers=message.headers
        )

    async def _stop_check(self, action: ActionModel, message: Message) -> AsyncGenerator[Message, None]:
        if GraphBuildType.TEAM.value == self.swarm.build_type:
            agent = self.swarm.agents.get(action.agent_name)
            caller = self.swarm.agent_graph.root_agent.id() or message.caller
            if agent.id != self.swarm.agent_graph.root_agent.id():
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
                payload=TaskItem(msg=f"Can not find {action.agent_name} agent in ordered_agents: {self.swarm.ordered_agents}.",
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
            logger.info(f"FINISHED|_social_stop_check endless_detect|{self.agent_calls}|{self.endless_threshold}|{self.swarm.communicate_agent.id()}")
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
                logger.info(f"FINISHED|_social_stop_check finished|{self.swarm.cur_step}|{self.swarm.max_steps}|{self.swarm.finished}")
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


class DefaultTeamHandler(AgentHandler):
    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if message.category != Constants.MULTI_AGENT_TEAM:
            return
        logger.info(f"DefaultTeamHandler|handle|taskid={self.task_id}|is_sub_task={message.context._task.is_sub_task}")
        content = message.payload
        # data is List[ActionModel]
        for action in content:
            if not isinstance(action, ActionModel):
                # error message, p2p
                yield Message(
                    category=Constants.OUTPUT,
                    payload=StepOutput.build_failed_output(name=f"{message.caller or self.name()}",
                                                           step_num=0,
                                                           data="action not a ActionModel.",
                                                           task_id=self.task_id),
                    sender=self.name(),
                    session_id=message.session_id,
                    headers=message.headers
                )
                msg = Message(
                    category=Constants.TASK,
                    payload=TaskItem(msg="action not a ActionModel.", data=content, stop=True),
                    sender=self.name(),
                    session_id=message.session_id,
                    topic=TopicType.ERROR,
                    headers=message.headers
                )
                logger.info(f"agent handler send task message: {msg}")
                yield msg
                return

        logger.info(f"DefaultTeamHandler|content|{content}")
        plan = parse_plan(content[0].policy_info)
        logger.info(f"DefaultTeamHandler|plan|{plan}")
        step_infos = plan.step_infos
        steps = step_infos.steps
        dag = step_infos.dag
        if not steps or not dag:
            if plan.answer:
                logger.info(f"FINISHED|DefaultTeamHandler|plan|finished|{plan.answer}")
                yield Message(
                    category=Constants.TASK,
                    payload=plan.answer,
                    sender=self.name(),
                    session_id=message.session_id,
                    topic=TopicType.FINISHED,
                    headers=message.headers
                )
            else:
                raise AworldException("no steps and answer.")

        group_id = self.runner.task.group_id if self.runner.task.group_id else uuid.uuid4().hex
        self.runner.task.group_id = group_id
        merge_context = message.context
        for node in dag:
            if isinstance(node, list):
                logger.info(f"DefaultTeamHandler|parallel_node|start|{node}")
                # can parallel
                tasks = []

                for n in node:
                    new_context = merge_context.deep_copy()
                    step_info: StepInfo = steps.get(n)
                    agent = self.swarm.agents.get(step_info.id)
                    if agent:
                        tasks.append(exec_agent(step_info.input, agent, new_context,
                                                outputs=merge_context.outputs,
                                                sub_task=True,
                                                task_group_id=group_id))
                    else:
                        tasks.append(exec_tool(tool_name=step_info.id,
                                               params=step_info.parameters,
                                               context=new_context,
                                               sub_task=True,
                                               outputs=merge_context.outputs,
                                               task_group_id=group_id))

                res = await asyncio.gather(*tasks)
                for idx, t in enumerate(res):
                    merge_context.merge_context(t.context)
                    merge_context.save_action_trajectory(steps.get(node[idx]).id, t.answer)
                logger.info(f"DefaultTeamHandler|parallel_node|end|{res}")
                res = res[-1]
            else:
                logger.info(f"DefaultTeamHandler|single_node|start|{node}")
                step_info: StepInfo = steps.get(node)
                agent = self.swarm.agents.get(step_info.id)
                new_context = merge_context.deep_copy()
                if agent:
                    res = await exec_agent(step_info.input, agent, new_context, outputs=merge_context.outputs, sub_task=True, task_group_id=group_id)
                else:
                    res = await exec_tool(tool_name=step_info.id,
                                          params=step_info.parameters,
                                          context=new_context,
                                          outputs=merge_context.outputs,
                                          sub_task=True,
                                          task_group_id=group_id)
                merge_context.merge_context(res.context)
                merge_context.save_action_trajectory(step_info.id, res.answer, agent_name=agent.id())
                logger.info(f"DefaultTeamHandler|single_node|end|{res}")
        logger.info(f"DefaultTeamHandler|single_node|end|{res}")
        new_plan_input = Observation(content=merge_context.task_input)
        yield AgentMessage(session_id=message.session_id,
                           payload=new_plan_input,
                           sender=self.name(),
                           receiver=self.swarm.communicate_agent.id(),
                           headers={'context': merge_context})
