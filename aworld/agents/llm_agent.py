# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import json
import os
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Any, List, Callable, Optional, Union

import aworld.trace as trace
from aworld.config.conf import AgentConfig, TaskConfig, TaskRunMode
from aworld.core.agent.agent_desc import get_agent_desc
from aworld.core.agent.base import BaseAgent, AgentResult, is_agent_by_name, is_agent, AgentFactory
from aworld.core.common import ActionResult, Observation, ActionModel, Config, TaskItem
from aworld.core.context.base import Context
from aworld.core.context.prompts import StringPromptTemplate
from aworld.core.event.base import Message, ToolMessage, Constants, AgentMessage, GroupMessage, TopicType, \
    MemoryEventType as MemoryType, MemoryEventMessage, ChunkMessage
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.model_output_parser import ModelOutputParser
from aworld.core.tool.tool_desc import get_tool_desc
from aworld.events import eventbus
from aworld.events.util import send_message, send_message_with_future
from aworld.logs.prompt_log import PromptLogger
from aworld.logs.util import logger, Color
from aworld.mcp_client.utils import mcp_tool_desc_transform, process_mcp_tools, skill_translate_tools
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryItem, MemoryAIMessage, MemoryMessage, MemoryToolMessage
from aworld.models.llm import get_llm_model, acall_llm_model, acall_llm_model_stream, apply_chat_template, \
    ModelResponseParser
from aworld.models.model_response import ModelResponse
from aworld.models.utils import tool_desc_transform, agent_desc_transform, usage_process
from aworld.output import Outputs
from aworld.output.base import MessageOutput, Output
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.hook.utils import run_hooks
from aworld.sandbox.base import Sandbox
from aworld.utils.common import sync_exec, nest_dict_counter
from aworld.utils.serialized_util import to_serializable
import aworld.runners.hook.agent_hooks


class LlmOutputParser(ModelOutputParser[ModelResponse, AgentResult]):
    async def parse(self, resp: ModelResponse, **kwargs) -> AgentResult:
        """Parse agent result based ModelResponse."""

        if not resp:
            logger.warning("⚠️ no valid content to parse!")
            return AgentResult(actions=[], current_state=None)

        agent_id = kwargs.get("agent_id")
        if not agent_id:
            logger.warning("⚠️ need agent_id param.")
            raise RuntimeError("no `agent_id` param.")

        results = []
        is_call_tool = False
        content = '' if resp.content is None else resp.content

        # Log parsing start
        logger.debug(f"🔍 [Agent:{agent_id}] Starting to parse model response, has_tool_calls={bool(resp.tool_calls)}, content_length={len(content)}")

        if resp.tool_calls:
            is_call_tool = True
            logger.info(f"🛠️ [Agent:{agent_id}] Processing {len(resp.tool_calls)} tool call(s)")
            for idx, tool_call in enumerate(resp.tool_calls):
                full_name: str = tool_call.function.name
                if not full_name:
                    logger.warning(f"⚠️ [Agent:{agent_id}] Tool call #{idx+1} has no tool name, skipping.")
                    continue
                
                logger.info(f"🔧 [Agent:{agent_id}] Processing tool call #{idx+1}: {full_name}, call_id={tool_call.id}")
                
                try:
                    params = json.loads(tool_call.function.arguments)
                    logger.debug(f"✅ [Agent:{agent_id}] Successfully parsed tool arguments for {full_name}: {len(params)} param(s)")
                except Exception as e:
                    logger.warning(f"⚠️ [Agent:{agent_id}] Failed to parse tool arguments for {full_name}: {tool_call.function.arguments}, error={str(e)}")
                    params = {}
                
                # format in framework
                agent_info = AgentFactory.agent_instance(agent_id)
                original_name = full_name
                if (not full_name.startswith("mcp__") and agent_info and agent_info.sandbox and
                        agent_info.sandbox.mcpservers and agent_info.sandbox.mcpservers.mcp_servers):
                    if agent_info.sandbox.mcpservers.map_tool_list:
                        _server_name = agent_info.sandbox.mcpservers.map_tool_list.get(full_name)
                        if _server_name:
                            full_name = f"mcp__{_server_name}__{full_name}"
                            logger.info(f"🔄 [Agent:{agent_id}] Mapped tool name: {original_name} -> {full_name} (via map_tool_list)")
                    else:
                        tmp_names = full_name.split("__")
                        tmp_tool_name = tmp_names[0]
                        if tmp_tool_name in agent_info.sandbox.mcpservers.mcp_servers:
                            full_name = f"mcp__{full_name}"
                            logger.info(f"🔄 [Agent:{agent_id}] Mapped tool name: {original_name} -> {full_name} (via mcp_servers)")
                
                names = full_name.split("__")
                tool_name = names[0]
                if is_agent_by_name(full_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=full_name,
                                               tool_call_id=tool_call.id,
                                               agent_name=agent_id,
                                               params=params,
                                               policy_info=content + param_info))
                    logger.debug(f"🤖 [Agent:{agent_id}] Added agent action: {full_name}")
                else:
                    action_name = '__'.join(names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_call_id=tool_call.id,
                                               action_name=action_name,
                                               agent_name=agent_id,
                                               params=params,
                                               policy_info=content))
                    logger.info(f"🔨 [Agent:{agent_id}] Added tool action: {tool_name}_{action_name}")
        else:
            results.append(ActionModel(agent_name=agent_id, policy_info=content))
            logger.debug(f"💬 [Agent:{agent_id}] No tool calls, added text response action (content_length={len(content)})")

        logger.info(f"✅ [Agent:{agent_id}] Parse completed: {len(results)} action(s), is_call_tool={is_call_tool}")
        return AgentResult(actions=results, current_state=None, is_call_tool=is_call_tool)


class LLMAgent(BaseAgent[Observation, List[ActionModel]]):
    """Basic agent for unified protocol within the framework."""

    def __init__(self,
                 name: str,
                 conf: Config | None = None,
                 desc: str = None,
                 agent_id: str = None,
                 *,
                 task: Any = None,
                 tool_names: List[str] = None,
                 agent_names: List[str] = None,
                 mcp_servers: List[str] = None,
                 mcp_config: Dict[str, Any] = None,
                 feedback_tool_result: bool = True,
                 wait_tool_result: bool = False,
                 sandbox: Sandbox = None,
                 system_prompt: str = None,
                 need_reset: bool = True,
                 step_reset: bool = True,
                 use_tools_in_prompt: bool = False,
                 black_tool_actions: Dict[str, List[str]] = None,
                 model_output_parser: Union[ModelOutputParser[..., AgentResult], Callable[
                     [ModelResponse, Any], AgentResult]] = LlmOutputParser(),
                 tool_aggregate_func: Callable[..., Any] = None,
                 event_handler_name: str = None,
                 event_driven: bool = True,
                 skill_configs: Dict[str, Any] = None,
                 **kwargs):
        """A api class implementation of agent, using the `Observation` and `List[ActionModel]` protocols.

        Args:
            system_prompt: Instruction of the agent.
            need_reset: Whether need to reset the status in start.
            step_reset: Reset the status at each step
            use_tools_in_prompt: Whether the tool description in prompt.
            black_tool_actions: Black list of actions of the tool.
            model_output_parser: Llm response parse function for the agent result, transform llm response.
            output_converter: Function to convert ModelResponse to AgentResult.
            tool_aggregate_func: Aggregation strategy for multiple tool results.
            event_handler_name: Custom handlers for certain types of events.
        """
        if conf is None:
            model_name = os.getenv("LLM_MODEL_NAME")
            api_key = os.getenv("LLM_API_KEY")
            base_url = os.getenv("LLM_BASE_URL")

            assert api_key and model_name, (
                "LLM_MODEL_NAME and LLM_API_KEY (environment variables) must be set, or pass AgentConfig explicitly"
            )
            logger.info(f"AgentConfig is empty, using env variables:\n LLM_BASE_URL={base_url}\n"
                        f"LLM_MODEL_NAME={model_name}")

            conf = AgentConfig(
                llm_provider=os.getenv("LLM_PROVIDER", "openai"),
                llm_model_name=model_name,
                llm_api_key=api_key,
                llm_base_url=base_url,
                llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
            )
        super(Agent, self).__init__(name, conf, desc, agent_id,
                                    task=task,
                                    tool_names=tool_names,
                                    agent_names=agent_names,
                                    mcp_servers=mcp_servers,
                                    mcp_config=mcp_config,
                                    black_tool_actions=black_tool_actions,
                                    feedback_tool_result=feedback_tool_result,
                                    wait_tool_result=wait_tool_result,
                                    sandbox=sandbox,
                                    skill_configs=skill_configs,
                                    **kwargs)
        conf = self.conf
        self.model_name = conf.llm_config.llm_model_name
        self._llm = None
        self.memory_config = conf.memory_config
        self.system_prompt: str = system_prompt if system_prompt else conf.system_prompt
        self.event_driven = event_driven

        self.need_reset = need_reset if need_reset else conf.need_reset
        # whether to keep contextual information, False means keep, True means reset in every step by the agent call
        self.step_reset = step_reset

        # Initialize output parser and converter
        # Agent layer parsing (conversion to AgentResult) happens here
        self.output_converter = model_output_parser or LlmOutputParser()

        # To maintain compatibility, we use a new parser class for the Model layer
        # if the user hasn't explicitly set one in llm_config.
        # LLM layer parsing (e.g. tool extraction) happens there,
        if self.conf.llm_config and not self.conf.llm_config.llm_response_parser:
            self.conf.llm_config.llm_response_parser = ModelResponseParser()

        self.use_tools_in_prompt = use_tools_in_prompt if use_tools_in_prompt else conf.use_tools_in_prompt
        self.tools_aggregate_func = tool_aggregate_func if tool_aggregate_func else self._tools_aggregate_func
        self.event_handler_name = event_handler_name
        self.context = kwargs.get("context", None)

    @property
    def llm(self):
        # lazy
        if self._llm is None:
            llm_config = self.conf.llm_config or None
            conf = llm_config if llm_config and (
                    llm_config.llm_provider or llm_config.llm_base_url or llm_config.llm_api_key or llm_config.llm_model_name) else self.conf
            self._llm = get_llm_model(conf)
        return self._llm

    def desc_transform(self, context: Context) -> None:
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""
        sync_exec(self.async_desc_transform, context)

    async def async_desc_transform(self, context: Context) -> None:
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""

        # Stateless tool
        try:
            tool_names = self.tool_names or []
            if context.get_agent_context_config(self.id()).automated_reasoning_orchestrator:
                from aworld.core.context.amni.tool.context_planning_tool import CONTEXT_PLANNING
                if CONTEXT_PLANNING not in tool_names:
                    tool_names.extend([CONTEXT_PLANNING])

            if context.get_agent_context_config(self.id()).automated_cognitive_ingestion:
                from aworld.core.context.amni.tool.context_knowledge_tool import CONTEXT_KNOWLEDGE
                if CONTEXT_KNOWLEDGE not in tool_names:
                    tool_names.extend([CONTEXT_KNOWLEDGE])
            self.tools = tool_desc_transform(get_tool_desc(),
                                             tools=tool_names,
                                             black_tool_actions=self.black_tool_actions)
        except:
            logger.warning(f"{self.id()} get tools desc fail, no tool to use. error: {traceback.format_exc()}")
        # Agents as tool
        try:
            self.tools.extend(agent_desc_transform(get_agent_desc(),
                                                   agents=self.handoffs if self.handoffs else []))
        except:
            logger.warning(f"{self.id()} get agent desc fail, no agent as tool to use. error: {traceback.format_exc()}")
        # MCP servers are tools
        try:
            if self.sandbox:
                mcp_tools = await self.sandbox.mcpservers.list_tools(context)
                processed_tools, tool_mapping = await process_mcp_tools(mcp_tools)
                self.sandbox.mcpservers.map_tool_list = tool_mapping
                self.tools.extend(processed_tools)
                self.tool_mapping = tool_mapping
            else:
                self.tools.extend(await mcp_tool_desc_transform(self.mcp_servers, self.mcp_config))
        except:
            logger.warning(f"{self.id()} get MCP desc fail, no MCP to use. error: {traceback.format_exc()}")

        await self.process_by_ptc(self.tools, context)

    def messages_transform(self,
                           content: str,
                           image_urls: List[str] = None,
                           observation: Observation = None,
                           message: Message = None,
                           **kwargs) -> List[Dict[str, Any]]:
        return sync_exec(self.async_messages_transform, image_urls=image_urls, observation=observation,
                         message=message, **kwargs)

    def _is_amni_context(self, context: Context):
        from aworld.core.context.amni import AmniContext
        return isinstance(context, AmniContext)

    def _build_memory_filters(self, context: Context, additional_filters: Dict[str, Any] = None) -> Dict[str, Any]:
        filters = {"agent_id": self.id()}

        agent_memory_config = context.get_agent_memory_config(self.id())

        query_scope = agent_memory_config.history_scope if agent_memory_config and agent_memory_config.history_scope else "task"
        task = context.get_task()

        if query_scope == "user":
            # Pass user_id when query_scope is user
            if hasattr(context, 'user_id') and context.user_id:
                filters["user_id"] = context.user_id
            elif hasattr(task, 'user_id') and task.user_id:
                filters["user_id"] = task.user_id
        elif query_scope == "session":
            # Pass session_id when query_scope is session
            if task and task.session_id:
                filters["session_id"] = task.session_id
        else:  # query_scope == "task" or default
            # Pass task_id when query_scope is task
            if task and task.id:
                filters["task_id"] = task.id

        # Add additional filter conditions
        if additional_filters:
            filters.update(additional_filters)

        return filters

    def _clean_redundant_tool_call_messages(self, histories: List[MemoryItem]) -> None:
        try:
            for i in range(len(histories) - 1, -1, -1):
                his = histories[i]
                if his.metadata and "tool_calls" in his.metadata and his.metadata['tool_calls']:
                    logger.info(f"Agent {self.id()} deleted tool call messages from memory: {his}")
                    MemoryFactory.instance().delete(his.id)
                else:
                    break
        except Exception:
            logger.error(f"Agent {self.id()} clean redundant tool_call_messages error: {traceback.format_exc()}")

    def postprocess_terminate_loop(self, message: Message):
        logger.info(f"Agent {self.id()} postprocess_terminate_loop: {self.loop_step}")
        super().postprocess_terminate_loop(message)
        try:
            filters = self._build_memory_filters(message.context, additional_filters={"memory_type": "message"})
            histories = MemoryFactory.instance().get_all(filters=filters)
            self._clean_redundant_tool_call_messages(histories)
        except Exception:
            logger.error(f"Agent {self.id()} postprocess_terminate_loop error: {traceback.format_exc()}")

    async def async_messages_transform(self,
                                       image_urls: List[str] = None,
                                       observation: Observation = None,
                                       message: Message = None,
                                       **kwargs) -> List[Dict[str, Any]]:
        """Transform the original content to LLM messages of native format.

        Args:
            observation: Observation by env.
            image_urls: List of images encoded using base64.
            message: Event received by the Agent.
        Returns:
            Message list for LLM.
        """
        messages = []
        # append sys_prompt to memory
        content = await self.custom_system_prompt(context=message.context,
                                                  content=observation.content,
                                                  tool_list=self.tools)
        if self.system_prompt:
            await self._add_message_to_memory(context=message.context, payload=content, message_type=MemoryType.SYSTEM)

        filters = self._build_memory_filters(message.context, additional_filters={"memory_type": "message", "load_pending_memory": True})
        histories = MemoryFactory.instance().get_all(filters=filters)

        # append observation to memory
        tool_result_added = False
        if observation.is_tool_result:
            # Tool already writes results to memory in tool layer. Skip here to avoid duplication.
            tool_result_added = True

        if not tool_result_added:
            self._clean_redundant_tool_call_messages(histories)
            content = observation.content
            if image_urls:
                urls = [{'type': 'text', 'text': content}]
                for image_url in image_urls:
                    urls.append(
                        {'type': 'image_url', 'image_url': {"url": image_url}})
                content = urls
            await self._add_message_to_memory(payload={"content": content, "memory_type": "init"},
                                              message_type=MemoryType.HUMAN,
                                              context=message.context)

        memory = MemoryFactory.instance()
        # from memory get last n messages
        filters = self._build_memory_filters(message.context)
        agent_memory_config = self.memory_config
        if self._is_amni_context(message.context):
            agent_context_config = message.context.get_config().get_agent_context_config(self.id())
            agent_memory_config = agent_context_config.to_memory_config()
        histories = memory.get_last_n(agent_memory_config.history_rounds, filters=filters,
                                      agent_memory_config=agent_memory_config)
        if histories:
            tool_calls_map = {}
            last_tool_calls = []
            for history in histories:
                if len(last_tool_calls) > 0 and len(tool_calls_map) == len(last_tool_calls):
                    # Maintain the order of tool calls
                    for tool_call_id in last_tool_calls:
                        if tool_call_id not in tool_calls_map:
                            raise AWorldRuntimeException(
                                f"tool_calls mismatch! {tool_call_id} not found in {tool_calls_map}, messages: {messages}")
                        messages.append(tool_calls_map.get(tool_call_id))
                    tool_calls_map = {}
                    last_tool_calls = []

                if isinstance(history, MemoryMessage):
                    if isinstance(history, MemoryToolMessage):
                        tool_calls_map[history.tool_call_id] = history.to_openai_message()
                    else:
                        messages.append(history.to_openai_message())
                        if isinstance(history, MemoryAIMessage) and history.tool_calls:
                            last_tool_calls.extend([tool_call.id for tool_call in history.tool_calls])
                else:
                    role = history.metadata['role']
                    if role == 'tool':
                        msg = {'role': history.metadata['role'], 'content': history.content,
                               'tool_call_id': history.metadata.get('tool_call_id')}
                        tool_calls_map[history.metadata.get("tool_call_id")] = msg
                    else:
                        if not self.use_tools_in_prompt and history.metadata.get('tool_calls'):
                            messages.append({'role': history.metadata['role'], 'content': history.content,
                                             'tool_calls': [history.metadata['tool_calls']]})
                            last_tool_calls.extend(
                                [tool_call.get('id') for tool_call in history.metadata['tool_calls']])
                        else:
                            messages.append({'role': history.metadata['role'], 'content': history.content,
                                             "tool_call_id": history.metadata.get("tool_call_id")})
            if len(last_tool_calls) > 0 and len(tool_calls_map) == len(last_tool_calls):
                for tool_call_id in last_tool_calls:
                    if tool_call_id not in tool_calls_map:
                        raise AWorldRuntimeException(
                            f"tool_calls mismatch! {tool_call_id} not found in {tool_calls_map}, messages: {messages}")
                    messages.append(tool_calls_map.get(tool_call_id))
                tool_calls_map = {}
                last_tool_calls = []

        return messages

    async def init_observation(self, observation: Observation) -> Observation:
        # default use origin observation
        return observation

    def _log_messages(self, messages: List[Dict[str, Any]], context: Context, **kwargs) -> None:
        PromptLogger.log_agent_call_llm_messages(self, messages=messages, context=context, **kwargs)

    def _agent_result(self, actions: List[ActionModel], caller: str, input_message: Message):
        if not actions:
            raise Exception(f'{self.id()} no action decision has been made.')
        if self.event_handler_name:
            return Message(payload=actions,
                           caller=caller,
                           sender=self.id(),
                           receiver=actions[0].tool_name,
                           category=self.event_handler_name,
                           session_id=input_message.context.session_id if input_message.context else "",
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
        if (agents and tools) or len(agents) > 1 or len(tools) > 1 or (len(agents) == 1 and agents[0].tool_name):
            _group_name = f"{self.id()}_{uuid.uuid1().hex}"

        # complex processing
        if _group_name:
            return GroupMessage(payload=actions,
                                caller=caller,
                                sender=self.id(),
                                receiver=actions[0].tool_name,
                                session_id=input_message.context.session_id if input_message.context else "",
                                group_id=_group_name,
                                topic=TopicType.GROUP_ACTIONS,
                                headers=self._update_headers(input_message))
        elif agents:
            payload = actions
            if self.wait_tool_result and any(action.params.get('is_tool_result', False) for action in actions):
                content = ''
                content += ''.join(action.policy_info for action in actions)
                action_result = [ActionResult(content=action.policy_info) for action in actions]
                payload = Observation(content=content, action_result=action_result)

            return AgentMessage(payload=payload,
                                caller=caller,
                                sender=self.id(),
                                receiver=actions[0].tool_name,
                                session_id=input_message.context.session_id if input_message.context else "",
                                headers=self._update_headers(input_message))

        else:
            return ToolMessage(payload=actions,
                               caller=caller,
                               sender=self.id(),
                               receiver=actions[0].tool_name,
                               session_id=input_message.context.session_id if input_message.context else "",
                               headers=self._update_headers(input_message))

    def post_run(self, policy_result: List[ActionModel], policy_input: Observation, message: Message = None) -> Message:
        return self._agent_result(
            policy_result,
            policy_input.from_agent_name if policy_input.from_agent_name else policy_input.observer,
            message
        )

    async def async_post_run(self, policy_result: List[ActionModel], policy_input: Observation,
                             message: Message = None) -> Message:

        # Check for pending messages in memory store
        memory = MemoryFactory.instance()
        # Accessing memory_store.pending_memory_items directly for check
        if hasattr(memory, 'memory_store') and hasattr(memory.memory_store, 'pending_memory_items'):
            filters = self._build_memory_filters(self.context)
            # Filter out pending items for current task
            pending_items = [item for item in memory.memory_store.pending_memory_items
                             if memory.memory_store._filter_memory_item(item, filters)]
            if pending_items:
                logger.info(f"🧠 [Agent:{self.id()}] Found {len(pending_items)} pending memory items, "
                            f"holding task execution. Pending content: {pending_items[0]}...")
                self._finished = False
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
        return sync_exec(self.async_policy, observation, info, message, **kwargs)

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        logger.info(f"Agent{type(self)}#{self.id()}: async_policy start")
        # temporary state context
        self.context = message.context

        # Get current step information for trace recording
        source_span = trace.get_current_span()
        self._finished = False
        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        messages = await self.build_llm_input(observation, info, message=message, **kwargs)

        serializable_messages = to_serializable(messages)
        message.context.context_info["llm_input"] = serializable_messages
        llm_response = None
        agent_result = None
        if source_span:
            source_span.set_attribute("messages", json.dumps(serializable_messages, ensure_ascii=False))
        # Record LLM call start time (used to set MemoryMessage's start_time)
        llm_call_start_time = datetime.now().isoformat()
        message.context.context_info["llm_call_start_time"] = llm_call_start_time

        try:
            events = []
            async for event in run_hooks(message.context, HookPoint.PRE_LLM_CALL, hook_from=self.id(),
                                         payload=observation):
                events.append(event)
        except Exception as e:
            logger.error(f"{self.id()} failed to run PRE_LLM_CALL hooks: {e}, traceback is {traceback.format_exc()}")
            raise e

        try:
            response_parse_args = {
                "use_tools_in_prompt": self.use_tools_in_prompt,
                "agent_id": self.id()
            }
            kwargs["response_parse_args"] = response_parse_args
            llm_response = await self.invoke_model(messages, message=message, **kwargs)
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
                            session_id=message.context.session_id if message.context else "",
                            headers={"context": message.context}
                        )
                        await send_message(output_message)
                else:
                    if self.output_converter and isinstance(self.output_converter, Callable):
                        if asyncio.iscoroutinefunction(self.output_converter):
                            agent_result = await self.output_converter(llm_response,
                                                                       agent_id=self.id(),
                                                                       use_tools_in_prompt=self.use_tools_in_prompt)
                        else:
                            agent_result = self.output_converter(llm_response,
                                                                 agent_id=self.id(),
                                                                 use_tools_in_prompt=self.use_tools_in_prompt)
                    else:
                        agent_result = await self.output_converter.parse(llm_response,
                                                                         agent_id=self.id(),
                                                                         use_tools_in_prompt=self.use_tools_in_prompt)
                    # skip summary on final round
                    await self._add_message_to_memory(payload=llm_response,
                                                      message_type=MemoryType.AI,
                                                      context=message.context,
                                                      skip_summary=self.is_agent_finished(llm_response, agent_result))

                    try:
                        events = []
                        async for event in run_hooks(message.context, HookPoint.POST_LLM_CALL, hook_from=self.id(),
                                                     payload=llm_response, agent_message=message):
                            events.append(event)
                    except Exception as e:
                        logger.error(
                            f"{self.id()} failed to run POST_LLM_CALL hooks: {e}, traceback is {traceback.format_exc()}")
                        raise e
            else:
                logger.error(f"{self.id()} failed to get LLM response")
                raise RuntimeError(f"{self.id()} failed to get LLM response")

        logger.info(f"agent_result: {agent_result}")

        if self.is_agent_finished(llm_response, agent_result):
            policy_result = agent_result.actions
        else:
            # Record all tool call start times (used to set MemoryMessage's start_time)
            for act in agent_result.actions:
                tool_call_start_time = datetime.now().isoformat()
                message.context.context_info[f"tool_call_start_time_{act.tool_call_id}"] = tool_call_start_time

            if not self.wait_tool_result:
                policy_result = agent_result.actions
            else:
                policy_result = await self.execution_tools(agent_result.actions, message)
        await self.send_agent_response_output(self, llm_response, message.context, kwargs.get("outputs"))
        return policy_result

    async def execution_tools(self, actions: List[ActionModel], message: Message = None, **kwargs) -> List[ActionModel]:
        """Tool execution operations.

        Returns:
            ActionModel sequence. Tool execution result.
        """
        from aworld.utils.run_util import exec_tool, exec_agent

        tool_results = []
        for act in actions:
            context = message.context.deep_copy()
            context.agent_info.current_tool_call_id = act.tool_call_id
            if is_agent(act):
                content = act.policy_info
                if act.params and 'content' in act.params:
                    content = act.params['content']
                task_conf = TaskConfig(run_mode=message.context.get_task().conf.run_mode)
                act_result = await exec_agent(question=content,
                                              agent=AgentFactory.agent_instance(act.tool_name),
                                              context=context,
                                              sub_task=True,
                                              outputs=message.context.outputs,
                                              task_group_id=message.context.get_task().group_id or uuid.uuid4().hex,
                                              task_conf=task_conf)
            else:
                act_result = await exec_tool(tool_name=act.tool_name,
                                             action_name=act.action_name,
                                             params=act.params,
                                             agent_name=self.id(),
                                             context=context,
                                             sub_task=True,
                                             outputs=message.context.outputs,
                                             task_group_id=message.context.get_task().group_id or uuid.uuid4().hex)

            # tool hooks
            try:
                events = []
                async for event in run_hooks(context=message.context, hook_point=HookPoint.POST_TOOL_CALL,
                                             hook_from=self.id(), payload=act_result):
                    events.append(event)
            except Exception:
                logger.debug(traceback.format_exc())

            if not act_result or not act_result.success:
                error_msg = act_result.msg if act_result else "Unknown error"
                logger.warning(f"Agent {self.id()} _execute_tool failed with exception: {error_msg}",
                               color=Color.red)
                continue
            act_res = ActionResult(tool_call_id=act.tool_call_id, tool_name=act.tool_name, content=act_result.answer)
            tool_results.append(act_res)
            await self._add_message_to_memory(payload=act_res, message_type=MemoryType.TOOL, context=message.context)
        result = sync_exec(self.tools_aggregate_func, tool_results)
        await self._add_tool_result_token_ids_to_context(message.context)
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
        params = {"is_tool_result": True}
        return [ActionModel(agent_name=self.id(), policy_info=content, params=params)]

    async def build_llm_input(self,
                              observation: Observation,
                              info: Dict[str, Any] = {},
                              message: Message = None,
                              **kwargs):
        """Build LLM input.

        Args:
            observation: The state observed from the environment
            info: Extended information to assist the agent in decision-making
        """
        await self.async_desc_transform(message.context)
        # observation secondary processing
        observation = await self.init_observation(observation)
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        try:
            messages = await self.async_messages_transform(image_urls=images, observation=observation, message=message)
        except Exception as e:
            logger.error(f"Failed to transform llm messages: {e}. {traceback.format_exc()}")
        # truncate and other process
        try:
            messages = self._process_messages(messages=messages, context=message.context)
        except Exception as e:
            logger.warning(f"Failed to process messages in messages_transform: {e}")
            logger.debug(f"Process messages error details: {traceback.format_exc()}")
        return messages

    def _process_messages(self, messages: List[Dict[str, Any]],
                          context: Context = None) -> Optional[List[Dict[str, Any]]]:
        return messages

    async def invoke_model(self,
                           messages: List[Dict[str, str]] = [],
                           message: Message = None,
                           **kwargs) -> ModelResponse:
        """Perform LLM call.

        Args:
            messages: LLM model input messages.
            message: Event message.
            **kwargs: Other parameters

        Returns:
            LLM response
        """
        llm_response = None
        try:
            tools = await self._filter_tools(message.context)
            if not tools:
                # Some model must be clearly defined as None
                tools = None
            self._log_messages(messages, tools=tools, context=message.context)
            stream_mode = kwargs.get("stream",
                                     False) or self.conf.llm_config.llm_stream_call if self.conf.llm_config else False
            float_temperature = float(self.conf.llm_config.llm_temperature)
            if stream_mode:
                llm_response = ModelResponse(
                    id="", model="", content="", tool_calls=[])
                resp_stream = acall_llm_model_stream(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=float_temperature,
                    tools=tools,
                    stream=True,
                    context=message.context,
                    **kwargs
                )

                async for chunk in resp_stream:
                    if chunk.content:
                        llm_response.content += chunk.content
                    if chunk.tool_calls:
                        llm_response.tool_calls.extend(chunk.tool_calls)
                    if chunk.error:
                        llm_response.error = chunk.error
                    llm_response.id = chunk.id
                    llm_response.model = chunk.model
                    llm_response.usage = nest_dict_counter(
                        llm_response.usage, chunk.usage, ignore_zero=False)
                    llm_response.message.update(chunk.message)
                    await send_message(ChunkMessage(payload=chunk,
                                                    source_type="llm",
                                                    session_id=message.context.session_id,
                                                    headers=message.headers))

            else:
                llm_response = await acall_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=float_temperature,
                    tools=tools,
                    stream=kwargs.get("stream", False),
                    context=message.context,
                    **kwargs
                )

            logger.info(f"LLM Execute response: {json.dumps(llm_response.to_dict(), ensure_ascii=False)}")
            if llm_response:
                usage_process(llm_response.usage, message.context)
            return llm_response
        except Exception as e:
            logger.warn(traceback.format_exc())
            await send_message(Message(
                category=Constants.OUTPUT,
                payload=Output(
                    data=f"Failed to call llm model: {e}"
                ),
                sender=self.id(),
                session_id=message.context.session_id if message.context else "",
                headers={"context": message.context}
            ))

            if "Please reduce the length of the messages" in str(e):
                # Meaning context too long, will return directly. You can develop a Processor to truncate or compress it.
                await send_message(Message(
                    category=Constants.TASK,
                    topic=TopicType.CANCEL,
                    payload=TaskItem(data=messages, msg=str(e)),
                    sender=self.id(),
                    priority=-1,
                    session_id=message.context.session_id if message.context else "",
                    headers={"context": message.context}
                ))
                return ModelResponse(id=uuid.uuid4().hex, model=self.model_name, content=to_serializable(messages))
            raise e
        finally:
            message.context.context_info["llm_output"] = llm_response

    async def custom_system_prompt(self, context: Context, content: str, tool_list: List[str] = None):
        logger.info(f"llm_agent custom_system_prompt .. agent#{type(self)}#{self.id()}")
        from aworld.core.context.amni.prompt.prompt_ext import ContextPromptTemplate
        from aworld.core.context.amni import AmniContext
        if isinstance(context, AmniContext):
            system_prompt_template = ContextPromptTemplate.from_template(self.system_prompt)
            return await system_prompt_template.async_format(context=context, task=content, tool_list=tool_list,
                                                             agent_id=self.id())
        else:
            system_prompt_template = StringPromptTemplate.from_template(self.system_prompt)
            system_prompt = system_prompt_template.format(context=context, task=content, tool_list=tool_list)
            if self.ptc_tools:
                from aworld.experimental.ptc.ptc_neuron import PTC_NEURON_PROMPT
                system_prompt += PTC_NEURON_PROMPT
            return system_prompt

    async def _add_message_to_memory(self, payload: Any, message_type: MemoryType, context: Context,
                                     skip_summary: bool = False):
        memory_msg = MemoryEventMessage(
            payload=payload,
            agent=self,
            memory_event_type=message_type,
            headers={"context": context, "skip_summary": skip_summary}
        )

        # Send through message system (DIRECT mode handling is now in send_message_with_future)
        try:
            future = await send_message_with_future(memory_msg)
            results = await future.wait(context=context)
            if not results:
                logger.warning(f"Memory write task failed: {memory_msg}")
        except Exception as e:
            logger.warn(f"Memory write task failed: {traceback.format_exc()}")

    @staticmethod
    async def send_agent_response_output(agent: BaseAgent, response: Any, context: Context, outputs: Outputs = None):
        if not response:
            return
        resp_output = MessageOutput(
            source=response,
            metadata={"agent_id": agent.id(), "agent_name": agent.name(), "is_finished": agent.finished}
        )
        if eventbus is not None:
            await send_message(Message(
                category=Constants.OUTPUT,
                payload=resp_output,
                sender=agent.id(),
                session_id=context.session_id if context else "",
                headers={"context": context}
            ))
        elif outputs:
            await outputs.add_output(resp_output)

    def is_agent_finished(self, llm_response: ModelResponse, agent_result: AgentResult) -> bool:
        if not agent_result.is_call_tool:
            self._finished = True
        return self.finished

    async def _filter_tools(self, context: Context) -> List[Dict[str, Any]]:
        from aworld.core.context.amni import AmniContext
        if not isinstance(context, AmniContext) or not self.skill_configs:
            logger.info(f"llm_agent don't need _filter_tools .. agent#{type(self)}#{self.id()}")
            return self.tools
        # get current active skills
        skills = await context.get_active_skills(namespace=self.id())

        return await skill_translate_tools(skills=skills, skill_configs=self.skill_configs, tools=self.tools,
                                           tool_mapping=self.tool_mapping)

    async def _add_tool_result_token_ids_to_context(self, context: Context):
        """Add tool result token ids to context"""
        if context.get_task().conf.get("run_mode") != TaskRunMode.INTERACTIVE:
            return
        filters = self._build_memory_filters(context, additional_filters={"memory_type": "message"})
        memory = MemoryFactory.instance()
        histories = memory.get_all(filters=filters)
        tool_openai_messages_after_last_assistant = []
        found_assistant = False
        tool_call_ids = []
        for i in range(len(histories) - 1, -1, -1):
            history = histories[i]
            if hasattr(history, 'role') and history.role == 'assistant':
                found_assistant = True
                break
            elif not found_assistant and hasattr(history, 'role') and history.role == 'tool':
                tool_openai_messages_after_last_assistant.append(history.to_openai_message())
                tool_call_ids.append(history.tool_call_id)

        if tool_openai_messages_after_last_assistant:
            tool_result_token_ids = apply_chat_template(self.llm, tool_openai_messages_after_last_assistant)
            context.add_tool_resp_token_ids(tool_resp_token_ids=tool_result_token_ids,
                                            resp_tool_call_ids=tool_call_ids,
                                            agent_id=self.id())

    @staticmethod
    async def to_dict(agent, override: Dict[str, Any] = None):
        """Agent attribute dict."""
        attr_dict = {
            "name": agent.name(),
            "conf": agent.conf,
            "desc": agent.desc(),
            "task": agent.task,
            "tool_names": agent.tool_names,
            "agent_names": agent.handoffs,
            "mcp_servers": agent.mcp_servers,
            "mcp_config": agent.mcp_config,
            "feedback_tool_result": agent.feedback_tool_result,
            "wait_tool_result": agent.wait_tool_result,
            "system_prompt": agent.system_prompt,
            "need_reset": agent.need_reset,
            "step_reset": agent.step_reset,
            "use_tools_in_prompt": agent.use_tools_in_prompt,
            "black_tool_actions": agent.black_tool_actions,
            "model_output_parser": agent.model_output_parser,
            "tool_aggregate_func": agent.tools_aggregate_func,
            "event_handler_name": agent.event_handler_name,
            "event_driven": agent.event_driven,
            "skill_configs": agent.skill_configs
        }
        if override:
            attr_dict.update(override)
        return attr_dict

    @staticmethod
    def from_dict(attr_dict: Dict[str, Any]) -> 'Agent':
        return Agent(**attr_dict)

    async def process_by_ptc(self, tools, context: Context):
        if not hasattr(self, "ptc_tools") or not self.ptc_tools:
            return
        ptc_tools = self.ptc_tools

        for tool in tools:
            if tool["function"]["name"] in ptc_tools:
                tool["function"]["description"] = "[allow_code_execution]" + tool["function"]["description"]
                logger.debug(f"ptc augmented tool: {tool['function']['description']}")


# Considering compatibility and current universality, we still use Agent to represent LLM Agent.
Agent = LLMAgent
