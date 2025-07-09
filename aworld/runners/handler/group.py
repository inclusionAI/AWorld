# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import copy
from typing import AsyncGenerator, List

from aworld.core.agent.base import is_agent
from aworld.core.common import ActionModel, TaskItem, Observation, ActionResult
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants, TopicType, GroupMessage
from aworld.logs.util import logger
from aworld.output.base import StepOutput
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.state_manager import RuntimeStateManager, RunNodeStatus


class GroupHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        self.runner = runner
        self.swarm = runner.swarm
        self.endless_threshold = runner.endless_threshold
        self.task_id = runner.task.id

    @classmethod
    def name(cls):
        return "_group_handler"


class DefaultGroupHandler(GroupHandler):
    async def handle(self, message: GroupMessage) -> AsyncGenerator[Message, None]:
        if message.category != Constants.GROUP:
            return
        self.context = message.context
        group_id = message.group_id
        headers = {'context': self.context}
        state_manager = RuntimeStateManager.instance()

        if message.topic == TopicType.GROUP_ACTIONS:
            # message.payload is List[ActionModel]
            node_ids = []
            action_messages = []
            agents = []
            tools = []
            for action in message.payload:
                if not isinstance(action, ActionModel):
                    # error message, p2p
                    async for event in self._send_failed_message(message, message.payload, message):
                        yield event
                    return
                if is_agent(action):
                    agents.append(action)
                else:
                    tools.append(action)

            for action in agents:
                msg = await self._build_agent_message(action, message)
                if msg.category != Constants.AGENT:
                    yield msg
                    return
                self._update_headers(msg, message)
                action_messages.append(msg)
                node_ids.append(msg.id)

            if tools:
                tool_mapping = {}
                for action in tools:
                    tool_name = action.tool_name
                    if tool_name not in tool_mapping:
                        tool_mapping[tool_name] = []
                    tool_mapping[tool_name].append(action)
                for tool_name, actions in tool_mapping.items():
                    msg = await self._build_tool_message(actions, message)
                    self._update_headers(msg, message)
                    action_messages.append(msg)
                    node_ids.append(msg.id)

            # create group
            await state_manager.create_group(group_id, message.session_id, node_ids, message.headers.get('parent_group_id'))
            for msg in action_messages:
                yield msg

        elif message.topic == TopicType.GROUP_RESULTS:
            # merge group results
            action_results = []
            group_results = message.payload
            group_sender = None
            agent_context = copy.deepcopy(self.context)
            for node_id, handle_res_list in group_results.items():
                if not handle_res_list:
                    logger.warn(f"{self.name()} get group result with empty handle_res.")
                    return
                node = state_manager._find_node(node_id)
                tool_call_id = node.metadata.get('root_tool_call_id')
                is_tool = not tool_call_id and not node.metadata.get('root_agent_id')
                if not group_sender:
                    group_sender = node.metadata.get('group_sender')
                node_results = []
                for handle_res in handle_res_list:
                    res_msg = handle_res.result
                    res_status = handle_res.status
                    if res_status == RunNodeStatus.FAILED or not res_msg:
                        logger.warn(f"{self.name()} get group result with failed handle_res.")
                        return
                    if is_tool and isinstance(res_msg.payload, Observation):
                        action_results.extend(res_msg.payload.action_result)
                    else:
                        node_results.append(res_msg.payload)
                        self._merge_context(agent_context, res_msg.context)
                if node_results and tool_call_id:
                    act_res = ActionResult(
                        content=node_results,
                        tool_call_id=tool_call_id
                    )
                    action_results.append(act_res)

            agent_input = Observation(
                content="",
                action_result=action_results
            )
            yield Message(
                category = Constants.AGENT,
                payload = agent_input,
                caller = message.caller,
                sender = self.name(),
                session_id = message.session_id,
                receiver = group_sender,
                headers ={
                    'context': agent_context,
                    'group_id': message.headers.get('parent_group_id')
                }
            )

    async def _build_agent_message(self, action: ActionModel, message: Message) -> Message:
        session_id = message.session_id
        headers = {
            "context": message.context,
            "root_tool_call_id": action.tool_call_id
        }
        agent = self.swarm.agents.get(action.agent_name)
        agent_name = action.tool_name
        if not agent_name:
            logger.warn(f"{self.name()} get agent action with empty tool_name.")
            return Message(
                category=Constants.TASK,
                payload=TaskItem(msg=f"Empty tool_name in group_action: {action}.",
                                 data=action,
                                 stop=True),
                sender=self.name(),
                session_id=session_id,
                topic=TopicType.ERROR,
                headers=headers
            )
        cur_agent = self.swarm.agents.get(agent_name)
        if not cur_agent or not agent:
            return Message(
                category=Constants.TASK,
                payload=TaskItem(msg=f"Can not find {agent_name} or {action.agent_name} agent in swarm.",
                                 data=action,
                                 stop=True),
                sender=self.name(),
                session_id=session_id,
                topic=TopicType.ERROR,
                headers=headers
            )

        cur_agent._finished = False
        con = action.policy_info
        if action.params and 'content' in action.params:
            con = action.params['content']
        observation = Observation(content=con, observer=agent.id(), from_agent_name=agent.id())

        return Message(
            category=Constants.AGENT,
            payload=observation,
            caller=message.caller,
            sender=action.agent_name,
            session_id=session_id,
            receiver=action.tool_name,
            headers=headers
        )

    async def _build_tool_message(self, actions: List[ActionModel], message: Message):
        session_id = message.session_id
        headers = {"context": copy.deepcopy(message.context)}
        return Message(
            category=Constants.TOOL,
            payload=actions,
            sender=self.name(),
            session_id=session_id,
            receiver=DefaultToolHandler.name(),
            headers=headers
        )

    async def _send_failed_message(self, message, data, result_msg):
        yield Message(
            category=Constants.OUTPUT,
            payload=StepOutput.build_failed_output(name=f"{message.caller or self.name()}",
                                                   step_num=0,
                                                   data=result_msg,
                                                   task_id=self.task_id),
            sender=self.name(),
            session_id=self.context.session_id,
            headers=message.headers
        )
        yield Message(
            category=Constants.TASK,
            payload=TaskItem(msg=result_msg, data=data, stop=True),
            sender=self.name(),
            session_id=self.context.session_id,
            topic=TopicType.ERROR,
            headers=message.headers
        )

    def _update_headers(self, message: Message, parent_message: Message):
        headers = message.headers
        headers['group_id'] = parent_message.group_id
        headers['root_message_id'] = message.id
        headers['root_agent_id'] = message.receiver if message.category == Constants.AGENT else ''
        headers['level'] = 0
        headers['parent_group_id'] = parent_message.headers.get('parent_group_id')

    def _merge_context(self, context: Context, new_context: Context):
        if not new_context:
            return
        if not context:
            context = new_context
            return

        context.context_info._data.update(new_context.context_info._data)
        context.agent_info.update(new_context.agent_info)
        context.trajectories.update(new_context.trajectories)


