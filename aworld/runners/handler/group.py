# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import json
import traceback
from typing import AsyncGenerator, List, Dict, Any, Tuple

from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import is_agent, is_agent_by_name
from aworld.core.common import ActionModel, TaskItem, Observation, ActionResult
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants, TopicType, GroupMessage, MemoryEventMessage, MemoryEventType
from aworld.core.task import TaskResponse
from aworld.events.util import send_message_with_future, send_message
from aworld.logs.util import logger
from aworld.output.base import StepOutput
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.handler.tool import DefaultToolHandler
from aworld.runners.state_manager import RuntimeStateManager, RunNodeStatus
from aworld.utils.serialized_util import to_serializable
from aworld.utils.run_util import exec_agent


class GroupHandler(DefaultHandler):
    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        super().__init__(runner)
        self.runner = runner
        self.swarm = runner.swarm
        self.endless_threshold = runner.endless_threshold
        self.task_id = runner.task.id

    @classmethod
    def name(cls):
        return "_group_handler"


@HandlerFactory.register(name=f'__{Constants.GROUP}__')
class DefaultGroupHandler(GroupHandler):
    def is_valid_message(self, message: Message):
        if message.category != Constants.GROUP:
            return False
        return True

    async def _do_handle(self, message: GroupMessage) -> AsyncGenerator[Message, None]:
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
            agent_actions_map = {}
            for action in message.payload:
                if not isinstance(action, ActionModel):
                    # error message, p2p
                    async for event in self._send_failed_message(message, message.payload, message):
                        yield event
                    return
                if is_agent(action):
                    agents.append(action)
                    agent_name = action.tool_name
                    if agent_name not in agent_actions_map:
                        agent_actions_map[agent_name] = []
                    agent_actions_map[agent_name].append(action)
                else:
                    tools.append(action)
            logger.info(f"DefaultGroupHandler receive group message with {len(agents)} agents and {len(tools)} tools.")

            # Process each agent's actions
            agent_messages = {}
            for agent_name, actions in agent_actions_map.items():
                # Get original agent
                original_agent = self.swarm.agents.get(agent_name)
                if not original_agent:
                    logger.warning(f"DefaultGroupHandler|agent_as_tool|Can not find {agent_name} agent in swarm.")
                    error_msg = Message(
                        category=Constants.TASK,
                        payload=TaskItem(msg=f"Can not find {agent_name} agent in swarm.",
                                         data=actions,
                                         stop=True),
                        sender=self.name(),
                        session_id=message.session_id,
                        topic=TopicType.ERROR,
                        headers={'context': self.context}
                    )
                    yield error_msg
                    return

                # Create agent copies and execute for each action
                for action in actions:
                    msg = await self._build_agent_message(action, message)
                    if msg.category != Constants.AGENT:
                        yield msg
                        return
                    self._update_headers(msg, message)
                    agent_copy = self.copy_agent(original_agent)
                    con = action.policy_info
                    if action.params and 'content' in action.params:
                        try:
                            con = Observation.model_validate(action.params)
                        except Exception as e:
                            logger.error(f"DefaultGroupHandler|agent_as_tool|Can not build observation for {agent_name} agent.{e}")
                            con = action.params["content"]

                    if agent_name not in agent_messages:
                        agent_messages[agent_name] = []
                    agent_messages[agent_name].append((con, agent_copy, msg))
            agent_node_ids, agent_tasks = await self._parallel_exec_agents_actions(agent_messages, message)
            node_ids.extend(agent_node_ids)

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
            group_meta_data = message.headers.copy()
            group_meta_data["context"] = message.context.deep_copy()
            group_meta_data["context"].set_task(message.context.get_task())
            await state_manager.create_group(group_id, message.session_id, node_ids,
                                             message.headers.get('parent_group_id'),
                                             group_meta_data)
            for _, acts in agent_messages.items():
                for act in acts:
                    self.runner.state_manager.start_message_node(act[2])
            for msg in action_messages:
                yield msg
            await self.process_agent_task_parallel(agent_tasks, message)

        elif message.topic == TopicType.GROUP_RESULTS:
            # merge group results
            action_results = []
            group_results = message.payload
            group_sender = None
            group_sender_node_id = None
            agent_context = self.context.deep_copy()
            agent_context._task = self.context.get_task()
            receiver_results = {}

            logger.info(f"DefaultGroupHandler receive group result with {len(group_results)} nodes.")

            for node_id, handle_res_list in group_results.items():
                if not handle_res_list:
                    logger.warn(f"{self.name()} get group result with empty handle_res.")
                    await send_message(Message(
                        category=Constants.TASK,
                        payload=TaskItem(msg=f"Empty handle_res_list for node: {node_id}.",
                                         data=handle_res_list,
                                         stop=True,),
                        sender=self.name(),
                        session_id=message.context.session_id,
                        topic=TopicType.ERROR,
                        headers={"context": message.context}
                    ))
                    return
                node = state_manager._find_node(node_id)
                tool_call_id = node.metadata.get('root_tool_call_id')
                is_tool = not tool_call_id and not node.metadata.get('root_agent_id')
                if not group_sender:
                    group_sender = node.metadata.get('group_sender')
                if not group_sender_node_id:
                    group_sender_node_id = node.metadata.get('group_sender_node_id')
                node_results = []
                sub_task_id = None
                for handle_res in handle_res_list:
                    res_msg = handle_res.result
                    res_status = handle_res.status
                    if res_status == RunNodeStatus.FAILED or not res_msg:
                        logger.warn(f"{self.name()} get group result with failed handle_res: {handle_res}.")
                        return
                    receiver = res_msg.receiver
                    if not receiver:
                        if res_msg.category == Constants.TASK:
                            yield res_msg
                            logger.info(f"{self.name()} get group result with task message: {res_msg}, return.")
                            return
                        logger.warn(f"{self.name()} get group result with empty receiver: {res_msg}.")
                        continue

                    if receiver != group_sender:
                        if receiver not in receiver_results:
                            receiver_results[receiver] = []
                        receiver_results[receiver].append(res_msg)
                    else:
                        if is_tool and isinstance(res_msg.payload, Observation) and res_msg.payload.is_tool_result:
                            action_results.extend(res_msg.payload.action_result)
                        elif isinstance(res_msg.payload, Observation) and res_msg.payload.content:
                            node_results.append(res_msg.payload.content)
                            self._merge_context(agent_context, res_msg.context)
                        else:
                            node_results.append(res_msg.payload)
                            self._merge_context(agent_context, res_msg.context)
                    if res_msg.headers and res_msg.headers.get('sub_task_id'):
                        sub_task_id = res_msg.headers.get('sub_task_id')

                if node_results:
                    act_res = ActionResult(
                        content=json.dumps(to_serializable(node_results), ensure_ascii=False),
                        tool_call_id=tool_call_id or "",
                        tool_name=node.metadata.get('root_agent_id'),
                        metadata={
                            "sub_task_id": sub_task_id
                        }
                    )
                    action_results.append(act_res)
            if action_results:
                group_res_msg = Message(
                    category=Constants.AGENT,
                    payload=Observation(content="", action_result=action_results),
                    caller=message.caller,
                    sender=self.name(),
                    session_id=message.session_id,
                    receiver=group_sender,
                    headers={'context': agent_context}
                )
                receiver_results[group_sender] = [group_res_msg]

            for receiver, res_msgs in receiver_results.items():
                result_message = self._merge_result_messages(res_msgs, message, group_sender_node_id)
                group_headers = {}
                group_sender_node = state_manager._find_node(group_sender_node_id)
                if group_sender_node:
                    group_headers.update(group_sender_node.metadata.copy())
                    group_headers['level'] = headers.get('level', 0) + 1
                group_headers['context'] = result_message.context or self.context
                result_message.headers = group_headers
                receive_agent = self.swarm.agents.get(receiver)
                # add tool message to receive_agent's memory
                if (result_message.payload
                        and isinstance(result_message.payload, Observation)
                        and result_message.payload.is_tool_result):
                    as_tool_results = []
                    as_human_messages = []
                    for action_item in result_message.payload.action_result:
                        if not action_item.tool_call_id:
                            as_human_messages.append(action_item)
                        elif not is_agent_by_name(action_item.tool_name):
                            as_tool_results.append(action_item)
                            # tool.step results already added to memory
                            continue
                        else:
                            as_tool_results.append(action_item)
                            memory_msg = MemoryEventMessage(
                                payload=action_item,
                                agent=receive_agent,
                                memory_event_type=MemoryEventType.TOOL,
                                headers=message.headers
                            )
                            try:
                                logger.info(f"add tool message to memory: {memory_msg}")
                                future = await send_message_with_future(memory_msg)
                                results = await future.wait(context=message.context)
                                if not results:
                                    logger.warning(f"Memory write task failed: {memory_msg}")
                            except Exception as e:
                                logger.warn(f"Memory write task failed: {e}. {traceback.format_exc()}")
                    if as_human_messages:
                        human_message_input = [{item.tool_name: item.content} for item in as_human_messages]
                        human_message_content = json.dumps(to_serializable(human_message_input), ensure_ascii=False)
                        result_message.payload.content = human_message_content
                        result_message.payload.action_result = as_tool_results
                        memory_msg = MemoryEventMessage(
                            payload={"content":human_message_content,
                                     "memory_type": "message"},
                            agent=receive_agent,
                            memory_event_type=MemoryEventType.HUMAN,
                            headers=message.headers
                        )
                        try:
                            logger.info(f"add human message to memory: {memory_msg}")
                            future = await send_message_with_future(memory_msg)
                            results = await future.wait(context=message.context)
                            if not results:
                                logger.warning(f"Memory write task failed: {memory_msg}")
                        except Exception as e:
                            logger.warn(f"Memory write task failed: {e}. {traceback.format_exc()}")
                logger.info(f"group_handler result message: {result_message}")
                yield result_message

    def copy_agent(self, agent: Agent):
        """Create a copy of the agent
        
        Args:
            agent: Original agent object
            
        Returns:
            Deep copy of the agent
        """
        return agent

    async def _parallel_exec_agents_actions(self, agent_messages: Dict[str, List[Tuple[str, Agent, Message]]],
                                            message: Message):
        """Execute multiple agent actions in parallel

        Args:
            agent_messages: Messages for agent actions
        """
        tasks = {}
        messages_ids = []
        for agent_name, acts in agent_messages.items():
            for idx, act in enumerate(acts):
                agent_message = act[2]
                messages_ids.append(agent_message.id)
                tasks[agent_message.id] = exec_agent(act[0], act[1], self.context, sub_task=True,
                                                     outputs=self.context.outputs,
                                                     tool_call_id=agent_message.headers.get('root_tool_call_id'))
        return messages_ids, tasks

    async def process_agent_tasks(self, agent_tasks, input_message):
        """Process agent async tasks

        Args:
            agent_tasks: Agent async tasks
        """
        root_agent_set = set()
        for node_id, task in agent_tasks.items():
            res = await task
            logger.info(f"{node_id} finished task: {res}")
            state_manager = self.runner.state_manager
            node = state_manager._find_node(node_id)
            if not node:
                logger.warn(f"DefaultGroupHandler|Can not find agent_task {node_id} in state_manager.")
                return
            root_agent_id = node.metadata.get('root_agent_id')
            root_agent_set.add(root_agent_id)
            self.context.merge_sub_context(res.context)
            msg = Message(
                category=Constants.AGENT,
                payload=[ActionModel(policy_info=res.answer, agent_name=root_agent_id)],
                sender=root_agent_id,
                session_id=node.session_id,
                headers={'context': self.context,
                         'root_agent_id': root_agent_id,
                         'root_tool_call_id': node.metadata.get('root_tool_call_id')}
            )
            finish_group_messages = []
            async for event in self.runner._inner_handler_process(
                    results=[msg],
                    handlers=self.runner.handlers
            ):
                # Only AGENT and TASK messages
                if isinstance(event, Message) and (
                        event.category == Constants.AGENT or event.category == Constants.TASK):
                    event.headers["sub_task_id"] = res.id
                    await self.context.add_task_trajectory(res.id, res.trajectory)
                    finish_group_messages.append(event)
                    logger.debug(f"event context: {event.context},context.task: {event.context.get_task()}")
            await state_manager.finish_sub_group(node.metadata.get('group_id'), node_id, finish_group_messages)
        for agent_id in root_agent_set:
            agent = self.swarm.agents.get(agent_id)
            if agent:
                agent._finished = True

    async def process_agent_task_parallel(self, agent_tasks, input_message):
        """Process agent async tasks in parallel

        Args:
            agent_tasks: Agent async tasks (dict mapping node_id to task)
        """
        # Prepare for parallel execution - keep node_id and task correspondence
        node_ids = list(agent_tasks.keys())
        tasks = [agent_tasks[node_id] for node_id in node_ids]

        # Execute all tasks in parallel
        results = await asyncio.gather(*tasks)

        # Process each result with its corresponding node_id
        root_agent_set = set()
        state_manager = self.runner.state_manager

        for node_id, res in zip(node_ids, results):
            logger.info(f"{node_id} finished task: {res}")
            node = state_manager._find_node(node_id)
            if not node:
                logger.warn(f"DefaultGroupHandler|Can not find agent_task {node_id} in state_manager.")
                return
            root_agent_id = node.metadata.get('root_agent_id')
            root_agent_set.add(root_agent_id)
            self.context.merge_sub_context(res.context)
            msg = Message(
                category=Constants.AGENT,
                payload=[ActionModel(policy_info=res.answer, agent_name=root_agent_id)],
                sender=root_agent_id,
                session_id=node.session_id,
                headers={'context': self.context,
                         'root_agent_id': root_agent_id,
                         'root_tool_call_id': node.metadata.get('root_tool_call_id')}
            )
            finish_group_messages = []
            async for event in self.runner._inner_handler_process(
                    results=[msg],
                    handlers=self.runner.handlers
            ):
                # Only AGENT and TASK messages
                if isinstance(event, Message) and (
                        event.category == Constants.AGENT or event.category == Constants.TASK):
                    finish_group_messages.append(event)
                    event.headers["sub_task_id"] = res.id
                    await input_message.context.add_task_trajectory(res.id, res.trajectory)
            await state_manager.finish_sub_group(node.metadata.get('group_id'), node_id, finish_group_messages)

        for agent_id in root_agent_set:
            agent = self.swarm.agents.get(agent_id)
            if agent:
                agent._finished = True

    async def update_trajectory_to_context(self, task_responses: List[TaskResponse]):

        pass

    def _merge_result_messages(self, res_msgs: List[Message], input_message: Message, group_sender_node_id: str):
        """Merge multiple result messages

        Args:
            res_msgs: Result messages
        """
        if len(res_msgs) == 1:
            return res_msgs[0]
        input_list = []
        new_context = input_message.context.deep_copy()
        new_context._task = self.context.get_task()
        for message in res_msgs:
            map = {}
            map[message.sender] = message.payload
            input_list.append(map)
            new_context.merge_context(message.context)
        return Message(
            category=Constants.AGENT,
            payload=Observation(content=input_list),
            sender=self.name(),
            receiver=res_msgs[0].receiver,
            session_id=res_msgs[0].session_id,
            headers={
                'context': new_context
            }
        )

    async def _build_agent_message(self, action: ActionModel, message: Message) -> Message:
        session_id = message.session_id
        headers = {
            "context": message.context,
            "root_tool_call_id": action.tool_call_id
        }
        from_agent = self.swarm.agents.get(action.agent_name)
        tool_name = action.tool_name

        if not tool_name:
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

        cur_agent = self.swarm.agents.get(tool_name)
        if not cur_agent:
            return Message(
                category=Constants.TASK,
                payload=TaskItem(msg=f"Can not find {tool_name} agent in swarm.",
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
        observation = Observation(content=con, observer=from_agent.id(), from_agent_name=from_agent.id())

        return Message(
            category=Constants.AGENT,
            payload=observation,
            caller=message.caller,
            sender=action.agent_name,
            session_id=session_id,
            receiver=cur_agent.id(),
            headers=headers
        )

    async def _build_tool_message(self, actions: List[ActionModel], message: Message):
        session_id = message.session_id
        headers = {"context": message.context.deep_copy()}
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
        headers = message.headers.copy()
        context = message.context.deep_copy()
        context.set_task(self.context.get_task())
        headers['context'] = context
        headers['group_id'] = parent_message.group_id
        headers['root_message_id'] = message.id
        headers['root_agent_id'] = message.receiver if message.category == Constants.AGENT else ''
        headers['level'] = 0
        headers['group_sender'] = parent_message.sender
        headers['group_sender_node_id'] = parent_message.id
        headers['parent_group_id'] = parent_message.headers.get('parent_group_id')
        message.headers = headers

    def _merge_context(self, context: Context, new_context: Context):
        if not new_context:
            return
        if not context:
            context = new_context
            return
        context.merge_context(new_context)
