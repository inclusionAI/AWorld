# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import time
import traceback
import uuid
import copy
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Any, List, Union, Callable

import aworld.trace as trace
from aworld.core.agent.swarm import TeamSwarm
from aworld.config import ToolConfig
from aworld.config.conf import AgentConfig, ConfigDict, ContextRuleConfig, OptimizationConfig, \
    LlmCompressionConfig
from aworld.core.agent.agent_desc import get_agent_desc
from aworld.core.agent.base import AgentFactory, BaseAgent, AgentResult, is_agent_by_name, is_agent, AgentStatus
from aworld.core.common import ActionResult, Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.context.processor.prompt_processor import PromptProcessor
from aworld.core.event import eventbus
from aworld.core.event.base import Message, ToolMessage, Constants, AgentMessage, GroupMessage, TopicType
from aworld.core.memory import AgentMemoryConfig
from aworld.core.tool.base import ToolFactory, AsyncTool, Tool
from aworld.core.tool.tool_desc import get_tool_desc
from aworld.events.util import send_message
from aworld.logs.util import logger, color_log, Color, trace_logger
from aworld.mcp_client.utils import sandbox_mcp_tool_desc_transform
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MessageMetadata, MemoryAIMessage, MemoryToolMessage, MemoryHumanMessage, \
    MemorySystemMessage, MemoryMessage
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model, acall_llm_model_stream
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.models.utils import tool_desc_transform, agent_desc_transform
from aworld.output import Outputs
from aworld.output.base import StepOutput, MessageOutput, Output
from aworld.prompt import Prompt
from aworld.runners.hook.hooks import HookPoint
from aworld.trace.constants import SPAN_NAME_PREFIX_AGENT
from aworld.trace.instrumentation import semconv
from aworld.utils.common import sync_exec, nest_dict_counter


class Agent(BaseAgent[Observation, List[ActionModel]]):
    """Basic agent for unified protocol within the framework."""

    def __init__(self,
                 conf: Union[Dict[str, Any], ConfigDict, AgentConfig],
                 name: str,
                 resp_parse_func: Callable[..., Any] = None,
                 agent_memory_config: AgentMemoryConfig = None,
                 **kwargs):
        """A api class implementation of agent, using the `Observation` and `List[ActionModel]` protocols.

        Args:
            conf: Agent config, supported AgentConfig, ConfigDict or dict.
            resp_parse_func: Response parse function for the agent standard output, transform llm response.
        """
        super(Agent, self).__init__(conf, name, **kwargs)
        conf = self.conf
        self.model_name = conf.llm_config.llm_model_name if conf.llm_config.llm_model_name else conf.llm_model_name
        self._llm = None
        self.memory = MemoryFactory.instance()
        self.memory_config = agent_memory_config if agent_memory_config else AgentMemoryConfig()
        self.system_prompt: str = kwargs.pop("system_prompt") if kwargs.get("system_prompt") else conf.system_prompt
        self.system_prompt_template: str = kwargs.pop("system_prompt_template") if kwargs.get(
            "system_prompt_template") else conf.system_prompt_template
        if self.system_prompt_template:
            self.system_prompt = Prompt(self.system_prompt_template).get_prompt()
        self.agent_prompt: str = kwargs.get("agent_prompt") if kwargs.get("agent_prompt") else conf.agent_prompt
        self.event_driven = kwargs.pop(
            'event_driven', conf.get('event_driven', False))
        self.handler: Callable[..., Any] = kwargs.get('handler')

        self.need_reset = kwargs.get('need_reset') if kwargs.get(
            'need_reset') else conf.need_reset
        # whether to keep contextual information, False means keep, True means reset in every step by the agent call
        self.step_reset = kwargs.get(
            'step_reset') if kwargs.get('step_reset') else True
        # tool_name: [tool_action1, tool_action2, ...]
        self.black_tool_actions: Dict[str, List[str]] = kwargs.get("black_tool_actions") if kwargs.get(
            "black_tool_actions") else conf.get('black_tool_actions', {})
        self.resp_parse_func = resp_parse_func if resp_parse_func else self.response_parse
        self.history_messages = kwargs.get(
            "history_messages") if kwargs.get("history_messages") else 100
        self.use_tools_in_prompt = kwargs.get(
            'use_tools_in_prompt', conf.use_tools_in_prompt)
        self.context_rule = kwargs.get("context_rule") if kwargs.get(
            "context_rule") else conf.context_rule
        self.tools_instances = {}
        self.tools_conf = {}
        self.tools_aggregate_func = kwargs.get("tools_aggregate_func") if kwargs.get(
            "tools_aggregate_func") else self._tools_aggregate_func
        self.response_handler_name = kwargs.get("response_handler_name")

    def deep_copy(self):
        """Create a deep copy of the current Agent instance.

        Returns:
            Agent: A deep copy of the current Agent instance
        """
        new_agent = Agent(self.conf, self.name(), self.resp_parse_func, self.memory_config)
        new_agent._llm = None
        new_agent.system_prompt = self.system_prompt
        new_agent.system_prompt_template = self.system_prompt_template
        new_agent.agent_prompt = self.agent_prompt
        new_agent.event_driven = self.event_driven
        new_agent.handler = self.handler
        new_agent.need_reset = self.need_reset
        new_agent.step_reset = self.step_reset
        new_agent.black_tool_actions = copy.deepcopy(self.black_tool_actions)
        if self.resp_parse_func == self.response_parse:
            # If default response_parse method, use the new instance's method
            new_agent.resp_parse_func = new_agent.response_parse
        else:
            # If using a custom function, assign it directly
            new_agent.resp_parse_func = self.resp_parse_func
        new_agent.history_messages = self.history_messages
        new_agent.use_tools_in_prompt = self.use_tools_in_prompt
        new_agent.context_rule = self.context_rule
        new_agent.tool_names = self.tool_names
        new_agent.handoffs = copy.deepcopy(self.handoffs)
        new_agent.mcp_servers = copy.deepcopy(self.mcp_servers)
        new_agent.mcp_config = copy.deepcopy(self.mcp_config)
        new_agent.sandbox = self.sandbox

        return new_agent

    def reset(self, options: Dict[str, Any]):
        logger.info("[LLM_AGENT] reset start")
        super().reset(options)
        logger.info("[LLM_AGENT] reset finished")

    def set_tools_instances(self, tools, tools_conf):
        self.tools_instances = tools
        self.tools_conf = tools_conf

    @property
    def llm(self):
        # lazy
        if self._llm is None:
            llm_config = self.conf.llm_config or None
            conf = llm_config if llm_config and (
                    llm_config.llm_provider or llm_config.llm_base_url or llm_config.llm_api_key or llm_config.llm_model_name) else self.conf
            self._llm = get_llm_model(conf)
        return self._llm

    def _env_tool(self):
        """Description of agent as tool."""
        return tool_desc_transform(get_tool_desc(),
                                   tools=self.tool_names if self.tool_names else [],
                                   black_tool_actions=self.black_tool_actions)

    def _handoffs_agent_as_tool(self):
        """Description of agent as tool."""
        return agent_desc_transform(get_agent_desc(),
                                    agents=self.handoffs if self.handoffs else [])

    def _mcp_is_tool(self):
        """Description of mcp servers are tools."""
        try:
            return sync_exec(sandbox_mcp_tool_desc_transform, self.mcp_servers, self.mcp_config)
        except Exception as e:
            logger.error(f"mcp_is_tool error: {traceback.format_exc()}")
            return []

    def desc_transform(self):
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""

        # Stateless tool
        self.tools = self._env_tool()
        # Agents as tool
        self.tools.extend(self._handoffs_agent_as_tool())
        # MCP servers are tools
        mcp_is_tools = self._mcp_is_tool()
        if mcp_is_tools:
            self.tools.extend(mcp_is_tools)
        return self.tools

    async def async_desc_transform(self):
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""

        # Stateless tool
        self.tools = self._env_tool()
        # Agents as tool
        self.tools.extend(self._handoffs_agent_as_tool())
        # MCP servers are tools
        if self.sandbox:
            sand_box = self.sandbox
            mcp_tools = await sand_box.mcpservers.list_tools()
            self.tools.extend(mcp_tools)
        else:
            self.tools.extend(await sandbox_mcp_tool_desc_transform(self.mcp_servers, self.mcp_config))

    def messages_transform(self,
                           content: str,
                           image_urls: List[str] = None,
                           observation: Observation = None,
                           message: Message = None,
                           **kwargs):
        return sync_exec(self.async_messages_transform, content=content, image_urls=image_urls, observation=observation,
                         message=message, **kwargs)

    async def async_messages_transform(self,
                                       image_urls: List[str] = None,
                                       observation: Observation = None,
                                       message: Message = None,
                                       **kwargs):
        """Transform the original content to LLM messages of native format.

        Args:
            content: User content.
            image_urls: List of images encoded using base64.
            sys_prompt: Agent system prompt.
            max_step: The maximum list length obtained from memory.
        Returns:
            Message list for LLM.
        """
        agent_prompt = self.agent_prompt
        messages = []
        # append sys_prompt to memory
        sys_prompt = self.system_prompt
        if self.system_prompt_template:
            sys_prompt = Prompt(self.system_prompt_template, context=self.context).get_prompt(
                variables={"task": observation.content, "tool_list": self.tools})
        if sys_prompt:
            await self._add_system_message_to_memory(context=message.context, content=sys_prompt)

        session_id = message.context.get_task().session_id
        task_id = message.context.get_task().id
        histories = self.memory.get_all(filters={
            "agent_id": self.id(),
            "session_id": session_id,
            "task_id": task_id,
            "memory_type": "message"
        })
        last_history = histories[-1] if histories and len(histories) > 0 else None

        # append observation to memory
        if observation.is_tool_result:
            for action_item in observation.action_result:
                tool_call_id = action_item.tool_call_id
                content = action_item.content
                await self._add_tool_result_to_memory(tool_call_id, tool_result=content, context=message.context)
        elif not self.use_tools_in_prompt and last_history and last_history.metadata and "tool_calls" in last_history.metadata and \
                last_history.metadata[
                    'tool_calls']:
            for tool_call in last_history.metadata['tool_calls']:
                tool_call_id = tool_call['id']
                tool_name = tool_call['function']['name']
                if tool_name and tool_name == message.sender:
                    await self._add_tool_result_to_memory(tool_call_id, tool_result=observation.content,
                                                          context=message.context)
                    break
        else:
            content = observation.content
            logger.debug(f"agent_prompt: {agent_prompt}")
            if agent_prompt:
                content = agent_prompt.format(task=content, current_date=datetime.now().strftime("%Y-%m-%d"))
            if image_urls:
                urls = [{'type': 'text', 'text': content}]
                for image_url in image_urls:
                    urls.append(
                        {'type': 'image_url', 'image_url': {"url": image_url}})
                content = urls
            await self._add_human_input_to_memory(content, message.context, memory_type="message")

        # from memory get last n messages
        histories = self.memory.get_last_n(self.history_messages, filters={
            "agent_id": self.id(),
            "session_id": session_id,
            "task_id": task_id
        }, agent_memory_config=self.memory_config)
        if histories:
            # default use the first tool call
            for history in histories:
                if isinstance(history, MemoryMessage):
                    messages.append(history.to_openai_message())
                else:
                    if not self.use_tools_in_prompt and "tool_calls" in history.metadata and history.metadata[
                        'tool_calls']:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         'tool_calls': [history.metadata["tool_calls"][0]]})
                    else:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         "tool_call_id": history.metadata.get("tool_call_id")})

        # truncate and other process
        try:
            messages = self._process_messages(messages=messages, context=self.context)
        except Exception as e:
            logger.warning(f"Failed to process messages in messages_transform: {e}")
            logger.debug(f"Process messages error details: {traceback.format_exc()}")
        return messages

    def use_tool_list(self, resp: ModelResponse) -> List[Dict[str, Any]]:
        if not self.use_tools_in_prompt:
            return []
        tool_list = []
        try:
            if resp and hasattr(resp, 'content') and resp.content:
                content = resp.content.strip()
            else:
                return tool_list
            content = content.replace('\n', '').replace('\r', '')
            response_json = json.loads(content)
            if "use_tool_list" in response_json:
                use_tool_list = response_json["use_tool_list"]
                if use_tool_list:
                    for use_tool in use_tool_list:
                        tool_name = use_tool["tool"]
                        arguments = use_tool["arguments"]
                        if tool_name and arguments:
                            tool_list.append(use_tool)

            return tool_list
        except Exception as e:
            logger.debug(
                f"tool_parse error, content: {resp.content}, \nerror msg: {traceback.format_exc()}")
            return tool_list

    def response_parse(self, resp: ModelResponse) -> AgentResult:
        """Default parse response by LLM."""
        results = []
        if not resp:
            logger.warning("LLM no valid response!")
            return AgentResult(actions=[], current_state=None)

        use_tool_list = self.use_tool_list(resp)
        is_call_tool = False
        content = '' if resp.content is None else resp.content
        if resp.tool_calls:
            is_call_tool = True
            for tool_call in resp.tool_calls:
                full_name: str = tool_call.function.name
                if not full_name:
                    logger.warning("tool call response no tool name.")
                    continue
                try:
                    params = json.loads(tool_call.function.arguments)
                except:
                    logger.warning(
                        f"{tool_call.function.arguments} parse to json fail.")
                    params = {}
                # format in framework
                names = full_name.split("__")
                tool_name = names[0]
                logger.info(
                    f"param_info={params} tool_name={tool_name} full_name={full_name} is_agent_by_name={is_agent_by_name(full_name)} AgentFactory._agent_instance={AgentFactory._agent_instance}")
                if is_agent_by_name(full_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=full_name,
                                               tool_call_id=tool_call.id,
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(
                        names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_call_id=tool_call.id,
                                               action_name=action_name,
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content))
        elif use_tool_list and len(use_tool_list) > 0:
            is_call_tool = True
            for use_tool in use_tool_list:
                full_name = use_tool["tool"]
                if not full_name:
                    logger.warning("tool call response no tool name.")
                    continue
                params = use_tool["arguments"]
                if not params:
                    logger.warning("tool call response no tool params.")
                    continue
                names = full_name.split("__")
                tool_name = names[0]
                if is_agent_by_name(full_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=full_name,
                                               tool_call_id=use_tool.get('id'),
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(
                        names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_call_id=use_tool.get('id'),
                                               action_name=action_name,
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content))
        else:
            if content:
                content = content.replace("```json", "").replace("```", "")
            # no tool call, agent name is itself.
            results.append(ActionModel(
                agent_name=self.id(), policy_info=content))
        return AgentResult(actions=results, current_state=None, is_call_tool=is_call_tool)

    def _log_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Log the sequence of messages for debugging purposes"""
        logger.info(f"[agent] Invoking LLM with {len(messages)} messages:")
        for i, msg in enumerate(messages):
            prefix = msg.get('role')
            logger.info(
                f"[agent] Message {i + 1}: {prefix} ===================================")
            if isinstance(msg['content'], list):
                try:
                    for item in msg['content']:
                        if item.get('type') == 'text':
                            logger.info(
                                f"[agent] Text content: {item.get('text')}")
                        elif item.get('type') == 'image_url':
                            image_url = item.get('image_url', {}).get('url', '')
                            if image_url.startswith('data:image'):
                                logger.info(f"[agent] Image: [Base64 image data]")
                            else:
                                logger.info(
                                    f"[agent] Image URL: {image_url[:30]}...")
                except Exception as e:
                    logger.error(f"[agent] Error parsing msg['content']: {msg}. Error: {e}")
                    content = str(msg['content'])
                    chunk_size = 500
                    for j in range(0, len(content), chunk_size):
                        chunk = content[j:j + chunk_size]
                        if j == 0:
                            logger.info(f"[agent] Content: {chunk}")
                        else:
                            logger.info(f"[agent] Content (continued): {chunk}")
            else:
                content = str(msg['content'])
                chunk_size = 500
                for j in range(0, len(content), chunk_size):
                    chunk = content[j:j + chunk_size]
                    if j == 0:
                        logger.info(f"[agent] Content: {chunk}")
                    else:
                        logger.info(f"[agent] Content (continued): {chunk}")

            if 'tool_calls' in msg and msg['tool_calls']:
                for tool_call in msg.get('tool_calls'):
                    if isinstance(tool_call, dict):
                        logger.info(
                            f"[agent] Tool call: {tool_call.get('function', {}).get('name', {})} - ID: {tool_call.get('id')}")
                        args = str(tool_call.get('function', {}).get(
                            'arguments', {}))[:1000]
                        logger.info(f"[agent] Tool args: {args}...")
                    elif isinstance(tool_call, ToolCall):
                        logger.info(
                            f"[agent] Tool call: {tool_call.function.name} - ID: {tool_call.id}")
                        args = str(tool_call.function.arguments)[:1000]
                        logger.info(f"[agent] Tool args: {args}...")

    def _agent_result(self, actions: List[ActionModel], caller: str, input_message: Message):
        if not actions:
            raise Exception(f'{self.id()} no action decision has been made.')
        if self.response_handler_name:
            return Message(payload=actions,
                           caller=caller,
                           sender=self.id(),
                           receiver=actions[0].tool_name,
                           category=self.response_handler_name,
                           session_id=self.context.session_id if self.context else "",
                           headers=self._update_headers(input_message))

        tools = OrderedDict()
        agents = []
        for action in actions:
            if is_agent(action):
                agents.append(action)
            else:
                if action.tool_name not in tools:
                    tools[action.tool_name] = []
                tools[action.tool_name].append(action)

        _group_name = None
        # agents and tools exist simultaneously, more than one agent/tool name
        if (agents and tools) or len(agents) > 1 or len(tools) > 1:
            _group_name = f"{self.id()}_{uuid.uuid1().hex}"

        # complex processing
        if _group_name:
            return GroupMessage(payload=actions,
                                caller=caller,
                                sender=self.id(),
                                receiver=actions[0].tool_name,
                                session_id=self.context.session_id if self.context else "",
                                group_id=_group_name,
                                topic=TopicType.GROUP_ACTIONS,
                                headers=self._update_headers(input_message))
        elif agents:
            return AgentMessage(payload=actions,
                                caller=caller,
                                sender=self.id(),
                                receiver=actions[0].tool_name,
                                session_id=self.context.session_id if self.context else "",
                                headers=self._update_headers(input_message))

        else:
            return ToolMessage(payload=actions,
                               caller=caller,
                               sender=self.id(),
                               receiver=actions[0].tool_name,
                               session_id=self.context.session_id if self.context else "",
                               headers=self._update_headers(input_message))

    def post_run(self, policy_result: List[ActionModel], policy_input: Observation, message: Message = None) -> Message:
        return self._agent_result(
            policy_result,
            policy_input.from_agent_name if policy_input.from_agent_name else policy_input.observer,
            message
        )

    async def async_post_run(self, policy_result: List[ActionModel], policy_input: Observation,
                             message: Message = None) -> Message:
        return self._agent_result(
            policy_result,
            policy_input.from_agent_name if policy_input.from_agent_name else policy_input.observer,
            message
        )

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None, **kwargs) -> List[
        ActionModel]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        output = None
        if kwargs.get("output") and isinstance(kwargs.get("output"), StepOutput):
            output = kwargs["output"]

        # Get current step information for trace recording
        step = kwargs.get("step", 0)
        exp_id = kwargs.get("exp_id", None)
        source_span = trace.get_current_span()

        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        try:
            self._run_hooks_sync(self.context, HookPoint.PRE_LLM_CALL)
        except Exception as e:
            logger.warn(traceback.format_exc())

        self._finished = False
        self.desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
            observation.images = images
        messages = self.messages_transform(content=observation.content,
                                           image_urls=observation.images,
                                           observation=observation,
                                           message=message
                                           )

        self._log_messages(messages)

        llm_response = None
        span_name = f"llm_call_{exp_id}"
        serializable_messages = self._to_serializable(messages)
        with trace.span(span_name) as llm_span:
            llm_span.set_attributes({
                "exp_id": exp_id,
                "step": step,
                "messages": json.dumps(serializable_messages, ensure_ascii=False)
            })
            if source_span:
                source_span.set_attribute("messages", json.dumps(
                    serializable_messages, ensure_ascii=False))

            try:
                llm_response = call_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=self.tools if not self.use_tools_in_prompt and self.tools else None
                )

                logger.info(f"Execute response: {llm_response.message}")
            except Exception as e:
                logger.warn(traceback.format_exc())
                raise e
            finally:
                if llm_response:
                    if llm_response.error:
                        logger.info(f"llm result error: {llm_response.error}")
                    else:
                        sync_exec(self._add_llm_response_to_memory, llm_response=llm_response, context=message.context)
                        # rewrite
                        self.context.context_info[self.id()] = info
                else:
                    logger.error(f"{self.id()} failed to get LLM response")
                    raise RuntimeError(
                        f"{self.id()} failed to get LLM response")

        try:
            self._run_hooks_sync(self.context, HookPoint.POST_LLM_CALL)
        except Exception as e:
            logger.warn(traceback.format_exc())

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True

        if output:
            output.add_part(MessageOutput(source=llm_response, json_parse=False, task_id=self.context.task_id))
            output.mark_finished()
        return agent_result.actions

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        # Get current step information for trace recording
        source_span = trace.get_current_span()

        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        try:
            events = []
            async for event in self.run_hooks(self.context, HookPoint.PRE_LLM_CALL):
                events.append(event)
        except Exception as e:
            logger.warn(traceback.format_exc())

        self._finished = False
        messages = await self._prepare_llm_input(observation, info, message=message, **kwargs)

        serializable_messages = self._to_serializable(messages)
        llm_response = None
        if source_span:
            source_span.set_attribute("messages", json.dumps(
                serializable_messages, ensure_ascii=False))
        try:
            llm_response = await self._call_llm_model(observation, messages, info, message=message, **kwargs)
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                if llm_response.error:
                    logger.info(f"llm result error: {llm_response.error}")
                    if eventbus is not None:
                        output_message = Message(
                            category=Constants.OUTPUT,
                            payload=Output(
                                data=f"llm result error: {llm_response.error}"
                            ),
                            sender=self.id(),
                            session_id=self.context.session_id if self.context else "",
                            headers={"context": self.context}
                        )
                        await send_message(output_message)
                else:
                    await self._add_llm_response_to_memory(llm_response, message.context)
            else:
                logger.error(f"{self.id()} failed to get LLM response")
                raise RuntimeError(f"{self.id()} failed to get LLM response")

        try:
            events = []
            async for event in self.run_hooks(self.context, HookPoint.POST_LLM_CALL):
                events.append(event)
        except Exception as e:
            logger.warn(traceback.format_exc())

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        logger.info(f"agent_result: {agent_result}")
        if not agent_result.is_call_tool:
            self._finished = True
        return agent_result.actions

    def _to_serializable(self, obj):
        if isinstance(obj, dict):
            return {k: self._to_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._to_serializable(i) for i in obj]
        elif hasattr(obj, "to_dict"):
            return obj.to_dict()
        elif hasattr(obj, "model_dump"):
            return obj.model_dump()
        elif hasattr(obj, "dict"):
            return obj.dict()
        else:
            return obj

    async def llm_and_tool_execution(self, observation: Observation, messages: List[Dict[str, str]] = [],
                                     info: Dict[str, Any] = {}, message: Message = None, **kwargs) -> List[ActionModel]:
        """Perform combined LLM call and tool execution operations.

        Args:
            observation: The state observed from the environment
            info: Extended information to assist the agent in decision-making
            **kwargs: Other parameters

        Returns:
            ActionModel sequence. If a tool is executed, includes the tool execution result.
        """
        # Get current step information for trace recording
        llm_response = await self._call_llm_model(observation, messages, info, **kwargs)
        if llm_response:
            if llm_response.error:
                logger.info(f"llm result error: {llm_response.error}")
            else:
                await self._add_llm_response_to_memory(llm_response, message.context)
        else:
            logger.error(f"{self.id()} failed to get LLM response")
            raise RuntimeError(f"{self.id()} failed to get LLM response")

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True
            return agent_result.actions
        else:
            from aworld.utils.run_util import exec_tool
            tool_results = []
            for act in agent_result.actions:
                if is_agent(act):
                    continue
                act_result = await exec_tool(tool_name=act.tool_name,
                                             action_name=act.action_name,
                                             params=act.params,
                                             agent_name=self.id(),
                                             context=message.context.deep_copy(),
                                             sub_task=True,
                                             outputs=message.context.outputs,
                                             task_group_id=self.context.get_task().group_id or uuid.uuid4().hex)
                if not act_result.success:
                    color_log(f"Agent {self.id()} _execute_tool failed with exception: {act_result.msg}",
                              color=Color.red)
                    continue
                tool_results.append(
                    ActionResult(tool_call_id=act.tool_call_id, tool_name=act.tool_name, content=act_result.answer))
                await self._add_tool_result_to_memory(act.tool_call_id, act_result.answer,
                                                      context=message.context)
            result = sync_exec(self.tools_aggregate_func, tool_results)
            return result

    async def _tools_aggregate_func(self, tool_results: List[ActionResult]) -> List[ActionModel]:
        """Aggregate tool results
        Args:
            tool_results: Tool results
        Returns:
            ActionModel sequence
        """
        content = ""
        for res in tool_results:
            content += f"{res.content}\n"
        return [ActionModel(agent_name=self.id(), policy_info=content)]

    async def _prepare_llm_input(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                                 **kwargs):
        """Prepare LLM input
        Args:
            observation: The state observed from the environment
            info: Extended information to assist the agent in decision-making
            **kwargs: Other parameters
        """
        await self.async_desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        messages = await self.async_messages_transform(image_urls=images, observation=observation, message=message)

        self._log_messages(messages)

        return messages

    def _process_messages(self, messages: List[Dict[str, Any]],
                          context: Context = None) -> Message:
        origin_messages = messages
        st = time.time()
        with trace.span(f"{SPAN_NAME_PREFIX_AGENT}llm_context_process", attributes={
            "start_time": st,
            semconv.AGENT_ID: self.id()
        }) as compress_span:
            if self.context_rule is None:
                logger.debug('debug|skip process_messages context_rule is None')
                return messages
            origin_len = compressed_len = len(str(messages))
            origin_messages_count = truncated_messages_count = len(messages)
            try:
                prompt_processor = PromptProcessor(self.context_rule, self.conf.llm_config)
                result = prompt_processor.process_messages(messages, context)
                messages = result.processed_messages

                compressed_len = len(str(messages))
                truncated_messages_count = len(messages)
                logger.debug(
                    f'debug|llm_context_process|{origin_len}|{compressed_len}|{origin_messages_count}|{truncated_messages_count}|\n|{origin_messages}\n|{messages}')
                return messages
            finally:
                compress_span.set_attributes({
                    "end_time": time.time(),
                    "duration": time.time() - st,
                    # messages length
                    "origin_messages_count": origin_messages_count,
                    "truncated_messages_count": truncated_messages_count,
                    "truncated_ratio": round(truncated_messages_count / origin_messages_count,
                                             2) if origin_messages_count > 0 else 0,
                    # token length
                    "origin_len": origin_len,
                    "compressed_len": compressed_len,
                    "compress_ratio": round(compressed_len / origin_len, 2)
                })

    async def _call_llm_model(self, observation: Observation, messages: List[Dict[str, str]] = [],
                              info: Dict[str, Any] = {}, **kwargs) -> ModelResponse:
        """Perform LLM call
        Args:
            observation: The state observed from the environment
            info: Extended information to assist the agent in decision-making
            **kwargs: Other parameters
        Returns:
            LLM response
        """
        outputs = None
        if kwargs.get("outputs") and isinstance(kwargs.get("outputs"), Outputs):
            outputs = kwargs.get("outputs")

        llm_response = None
        source_span = trace.get_current_span()
        serializable_messages = self._to_serializable(messages)
        self.context.context_info["llm_input"] = serializable_messages

        if source_span:
            source_span.set_attribute("messages", json.dumps(
                serializable_messages, ensure_ascii=False))

        try:
            stream_mode = kwargs.get("stream", False)
            if stream_mode:
                llm_response = ModelResponse(
                    id="", model="", content="", tool_calls=[])
                resp_stream = acall_llm_model_stream(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=self.tools if not self.use_tools_in_prompt and self.tools else None,
                    stream=True
                )

                async def async_call_llm(resp_stream, json_parse=False):
                    llm_resp = ModelResponse(
                        id="", model="", content="", tool_calls=[])

                    # Async streaming with acall_llm_model
                    async def async_generator():
                        async for chunk in resp_stream:
                            if chunk.content:
                                llm_resp.content += chunk.content
                                yield chunk.content
                            if chunk.tool_calls:
                                llm_resp.tool_calls.extend(chunk.tool_calls)
                            if chunk.error:
                                llm_resp.error = chunk.error
                            llm_resp.id = chunk.id
                            llm_resp.model = chunk.model
                            llm_resp.usage = nest_dict_counter(
                                llm_resp.usage, chunk.usage)

                    return MessageOutput(source=async_generator(), json_parse=json_parse), llm_resp

                output, response = await async_call_llm(resp_stream)
                llm_response = response

                if eventbus is not None and resp_stream:
                    output_message = Message(
                        category=Constants.OUTPUT,
                        payload=output,
                        sender=self.id(),
                        session_id=self.context.session_id if self.context else "",
                        headers={"context": self.context}
                    )
                    await send_message(output_message)
                elif not self.event_driven and outputs:
                    outputs.add_output(output)

            else:
                llm_response = await acall_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=self.tools if not self.use_tools_in_prompt and self.tools else None,
                    stream=kwargs.get("stream", False)
                )
                if eventbus is None:
                    logger.warn(
                        "=============== eventbus is none ============")
                if eventbus is not None and llm_response:
                    await send_message(Message(
                        category=Constants.OUTPUT,
                        payload=llm_response,
                        sender=self.id(),
                        session_id=self.context.session_id if self.context else "",
                        headers={"context": self.context}
                    ))
                elif not self.event_driven and outputs:
                    outputs.add_output(MessageOutput(
                        source=llm_response, json_parse=False))

            logger.info(
                f"Execute response: {json.dumps(llm_response.to_dict(), ensure_ascii=False)}")

        except Exception as e:
            logger.warn(traceback.format_exc())
            if eventbus is not None:
                output_message = Message(
                    category=Constants.OUTPUT,
                    payload=Output(
                        data=f"Failed to call llm model: {e}"
                    ),
                    sender=self.id(),
                    session_id=self.context.session_id if self.context else "",
                    headers={"context": self.context}
                )
                await send_message(output_message)
            raise e
        finally:
            self.context.context_info["llm_output"] = llm_response
            return llm_response

    async def _execute_tool(self, actions: List[ActionModel], context_message: Message = None) -> Any:
        """Execute tool calls

        Args:
            action: The action(s) to execute

        Returns:
            The result of tool execution
        """
        tool_actions = []
        for act in actions:
            if is_agent(act):
                continue
            else:
                tool_actions.append(act)

        msg = None
        terminated = False
        # group action by tool name
        tool_mapping = dict()
        reward = 0.0
        # Directly use or use tools after creation.
        for act in tool_actions:
            if not self.tools_instances or (self.tools_instances and act.tool_name not in self.tools):
                # Dynamically only use default config in module.
                conf = self.tools_conf.get(act.tool_name)
                if not conf:
                    conf = ToolConfig(
                        exit_on_failure=self.task.conf.get('exit_on_failure'))
                tool = ToolFactory(act.tool_name, conf=conf,
                                   asyn=conf.use_async if conf else False)
                if isinstance(tool, Tool):
                    tool.reset()
                elif isinstance(tool, AsyncTool):
                    await tool.reset()
                tool_mapping[act.tool_name] = []
                self.tools_instances[act.tool_name] = tool
            if act.tool_name not in tool_mapping:
                tool_mapping[act.tool_name] = []
            tool_mapping[act.tool_name].append(act)

        observation = None

        for tool_name, action in tool_mapping.items():
            tool_message = ToolMessage(
                payload=action,
                session_id=self.context.session_id,
                headers={"context": self.context}
            )
            # Execute action using browser tool and unpack all return values
            if isinstance(self.tools_instances[tool_name], Tool):
                message = self.tools_instances[tool_name].step(tool_message)
            elif isinstance(self.tools_instances[tool_name], AsyncTool):
                # todo sandbox
                message = await self.tools_instances[tool_name].step(tool_message, agent=self)
            else:
                logger.warning(
                    f"Unsupported tool type: {self.tools_instances[tool_name]}")
                continue

            observation, reward, terminated, _, info = message.payload

            # Check if there's an exception in info
            if info.get("exception"):
                color_log(f"Agent {self.id()} _execute_tool failed with exception: {info['exception']}",
                          color=Color.red)
                msg = f"Agent {self.id()} _execute_tool failed with exception: {info['exception']}"
            logger.info(
                f"Agent {self.id()} _execute_tool finished by tool action: {action}.")
            log_ob = Observation(content='' if observation.content is None else observation.content,
                                 action_result=observation.action_result)
            trace_logger.info(
                f"{tool_name} observation: {log_ob}", color=Color.green)

            for action_result_item in observation.action_result:
                await self._add_tool_result_to_memory(action_result_item.tool_call_id, action_result_item.content,
                                                      context=context_message.context)

        return [ActionModel(agent_name=self.id(), policy_info=observation.content)]

    def _init_context(self, context: Context):
        super()._init_context(context)
        # Generate default configuration when context_rule is empty
        llm_config = self.conf.llm_config
        context_rule = self.context_rule
        if context_rule is None:
            self.context_rule = ContextRuleConfig(
                optimization_config=OptimizationConfig(
                    enabled=True,
                    max_token_budget_ratio=1.0
                ),
                llm_compression_config=LlmCompressionConfig(
                    enabled=False  # Compression disabled by default
                )
            )
        logger.debug(f'init_context llm_agent {self.name()} {self.conf} {self.context_rule}')

    def update_system_prompt(self, system_prompt: str):
        self.system_prompt = system_prompt
        logger.info(f"Agent {self.name()} system_prompt updated")

    def update_agent_prompt(self, agent_prompt: str):
        self.agent_prompt = agent_prompt
        logger.info(f"Agent {self.name()} agent_prompt updated")

    async def run_hooks(self, context: Context, hook_point: str):
        """Execute hooks asynchronously"""
        from aworld.runners.hook.hook_factory import HookFactory
        from aworld.core.event.base import Message

        # Get all hooks for the specified hook point
        all_hooks = HookFactory.hooks(hook_point)
        hooks = all_hooks.get(hook_point, [])

        for hook in hooks:
            try:
                # Create a temporary Message object to pass to the hook
                message = Message(
                    category="agent_hook",
                    payload=None,
                    sender=self.id(),
                    session_id=context.session_id if hasattr(
                        context, 'session_id') else None,
                    headers={"context": self.context}
                )

                # Execute hook
                msg = await hook.exec(message, context)
                if msg:
                    logger.debug(f"Hook {hook.point()} executed successfully")
                    yield msg
            except Exception as e:
                logger.warning(
                    f"Hook {hook.point()} execution failed: {traceback.format_exc()}")

    def _run_hooks_sync(self, context: Context, hook_point: str):
        """Execute hooks synchronously"""
        # Use sync_exec to execute asynchronous hook logic
        try:
            sync_exec(self.run_hooks, context, hook_point)
        except Exception as e:
            logger.warn(
                f"Failed to execute hooks for {hook_point}: {traceback.format_exc()}")

    async def _add_system_message_to_memory(self, context: Context, content: str):
        if not self.system_prompt:
            return
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id

        histories = self.memory.get_last_n(0, filters={
            "agent_id": self.id(),
            "session_id": session_id,
            "task_id": task_id
        }, agent_memory_config=self.memory_config)
        if histories and len(histories) > 0:
            logger.debug(
                f"🧠 [MEMORY:short-term] histories is not empty, do not need add system input to agent memory")
            return
        if not self.system_prompt:
            return
        content = await self.custom_system_prompt(context=context, content=content)
        logger.info(f'system prompt content: {content}')

        await self.memory.add(MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name(),
            )
        ), agent_memory_config=self.memory_config)
        logger.info(
            f"🧠 [MEMORY:short-term] Added system input to agent memory:  Agent#{self.id()}, 💬 {content[:100]}...")

    async def custom_system_prompt(self, context: Context, content: str):
        return content

    async def _add_human_input_to_memory(self, content: Any, context: Context, memory_type="init"):
        """Add user input to memory"""
        if not context.get_task():
            logger.error(f"Task is None")
        session_id = context.get_task().session_id
        user_id = context.get_task().user_id
        task_id = context.get_task().id

        await self.memory.add(MemoryHumanMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name(),
            ),
            memory_type=memory_type
        ), agent_memory_config=self.memory_config)
        logger.info(f"🧠 [MEMORY:short-term] Added human input to task memory: "
                    f"User#{user_id}, "
                    f"Session#{session_id}, "
                    f"Task#{task_id}, "
                    f"Agent#{self.id()}, 💬 {content[:100]}...")

    async def _add_llm_response_to_memory(self, llm_response, context: Context):
        """Add LLM response to memory"""
        custom_prompt_tool_calls = []
        if self.use_tools_in_prompt:
            custom_prompt_tool_calls = self.use_tool_list(llm_response)
        if not context.get_task():
            logger.error(f"Task is None")
        session_id = context.get_task().session_id
        user_id = context.get_task().user_id
        task_id = context.get_task().id

        await self.memory.add(MemoryAIMessage(
            content=llm_response.content,
            tool_calls=llm_response.tool_calls if not self.use_tools_in_prompt else custom_prompt_tool_calls,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name()
            )
        ), agent_memory_config=self.memory_config)
        logger.info(f"🧠 [MEMORY:short-term] Added LLM response to task memory: "
                    f"User#{user_id}, "
                    f"Session#{session_id}, "
                    f"Task#{task_id}, "
                    f"Agent#{self.id()},"
                    f" 💬 tool_calls size: {len(llm_response.tool_calls) if llm_response.tool_calls else 0},"
                    f" content: {llm_response.content[:100] if llm_response.content else ''}... ")

    async def _add_tool_result_to_memory(self, tool_call_id: str, tool_result: ActionResult, context: Context):
        """Add tool result to memory"""
        if hasattr(tool_result, 'content') and isinstance(tool_result.content, str) and tool_result.content.startswith(
                "data:image"):
            image_content = tool_result.content
            tool_result.content = "this picture is below "
            await self._do_add_tool_result_to_memory(tool_call_id, tool_result, context)
            image_content = [
                {
                    "type": "text",
                    "text": f"this is file of tool_call_id:{tool_result.tool_call_id}"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_content
                    }
                }
            ]
            await self._add_human_input_to_memory(image_content, context, "message")
        else:
            await self._do_add_tool_result_to_memory(tool_call_id, tool_result, context)

    async def _do_add_tool_result_to_memory(self, tool_call_id: str, tool_result: ActionResult, context: Context):
        """Add tool result to memory"""
        session_id = context.get_task().session_id
        user_id = context.get_task().user_id
        task_id = context.get_task().id

        await self.memory.add(MemoryToolMessage(
            content=tool_result.content if hasattr(tool_result, 'content') else tool_result,
            tool_call_id=tool_call_id,
            status="success",
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name(),
            )
        ), agent_memory_config=self.memory_config)
        logger.info(f"🧠 [MEMORY:short-term] Added tool result to task memory:"
                    f" User#{user_id}, "
                    f"Session#{session_id}, "
                    f"Task#{task_id}, "
                    f"Agent#{self.id()}, 💬 tool_call_id: {tool_call_id} ")

    def _update_headers(self, input_message: Message) -> Dict[str, Any]:
        headers = input_message.headers.copy()
        headers['context'] = self.context
        headers['level'] = headers.get('level', 0) + 1
        if input_message.group_id:
            headers['parent_group_id'] = input_message.group_id
        return headers
