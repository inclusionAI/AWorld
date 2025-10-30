# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import traceback
import uuid
from collections import OrderedDict
from typing import Dict, Any, List, Callable, Optional

import aworld.trace as trace
from aworld.core.agent.agent_desc import get_agent_desc
from aworld.core.agent.base import BaseAgent, AgentResult, is_agent_by_name, is_agent, AgentFactory
from aworld.core.common import ActionResult, Observation, ActionModel, Config, TaskItem
from aworld.core.context.base import Context
from aworld.core.context.prompts import StringPromptTemplate
from aworld.events import eventbus
from aworld.core.event.base import Message, ToolMessage, Constants, AgentMessage, GroupMessage, TopicType, \
    MemoryEventType as MemoryType, MemoryEventMessage
from aworld.core.model_output_parser import ModelOutputParser
from aworld.core.tool.tool_desc import get_tool_desc
from aworld.events.util import send_message, send_message_with_future
from aworld.logs.util import logger, Color
from aworld.mcp_client.utils import mcp_tool_desc_transform, process_mcp_tools, skill_translate_tools
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryItem
from aworld.memory.models import MemoryMessage
from aworld.models.llm import get_llm_model, acall_llm_model, acall_llm_model_stream
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.models.utils import tool_desc_transform, agent_desc_transform, usage_process
from aworld.output import Outputs
from aworld.output.base import MessageOutput, Output
from aworld.runners.hook.hooks import HookPoint
from aworld.sandbox.base import Sandbox
from aworld.utils.common import sync_exec, nest_dict_counter
from aworld.utils.serialized_util import to_serializable


class LlmOutputParser(ModelOutputParser[ModelResponse, AgentResult]):
    async def parse(self, resp: ModelResponse, **kwargs) -> AgentResult:
        """Standard parse based Openai API."""

        if not resp:
            logger.warning("no valid content to parse!")
            return AgentResult(actions=[], current_state=None)

        agent_id = kwargs.get("agent_id")
        if not agent_id:
            logger.warning("need agent_id param.")
            raise RuntimeError("no `agent_id` param.")

        results = []
        is_call_tool = False
        content = '' if resp.content is None else resp.content
        if kwargs.get("use_tools_in_prompt"):
            tool_calls = []
            for tool in self.use_tool_list(content):
                tool_calls.append(ToolCall.from_dict({
                    "id": tool.get("id"),
                    "function": {
                        "name": tool.get("tool"),
                        "arguments": tool.get("arguments")
                    }
                }))
            if tool_calls:
                resp.tool_calls = tool_calls

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
                    logger.warning(f"{tool_call.function.arguments} parse to json fail.")
                    params = {}
                # format in framework
                agent_info = AgentFactory.agent_instance(agent_id)
                if full_name and not full_name.startswith(
                        "mcp__") and agent_info and agent_info.sandbox and agent_info.sandbox.mcpservers and agent_info.sandbox.mcpservers.mcp_servers and len(
                        agent_info.sandbox.mcpservers.mcp_servers) > 0:
                    if agent_info.sandbox.mcpservers.map_tool_list:
                        _server_name = agent_info.sandbox.mcpservers.map_tool_list.get(full_name)
                        if _server_name:
                            full_name = f"mcp__{_server_name}__{full_name}"
                    else:
                        tmp_names = full_name.split("__")
                        tmp_tool_name = tmp_names[0]
                        if tmp_tool_name in agent_info.sandbox.mcpservers.mcp_servers:
                            full_name = f"mcp__{full_name}"
                names = full_name.split("__")
                tool_name = names[0]
                if is_agent_by_name(full_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=full_name,
                                               tool_call_id=tool_call.id,
                                               agent_name=agent_id,
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_call_id=tool_call.id,
                                               action_name=action_name,
                                               agent_name=agent_id,
                                               params=params,
                                               policy_info=content))
        else:
            content = content.replace("```json", "").replace("```", "")
            results.append(ActionModel(agent_name=agent_id, policy_info=content))

        return AgentResult(actions=results, current_state=None, is_call_tool=is_call_tool)

    def use_tool_list(self, content: str) -> List[Dict[str, Any]]:
        tool_list = []
        try:
            content = content.replace('\n', '').replace('\r', '')
            response_json = json.loads(content)
            use_tool_list = response_json.get("use_tool_list", [])
            for use_tool in use_tool_list:
                tool_name = use_tool.get("tool", None)
                if tool_name:
                    tool_list.append(use_tool)
        except Exception:
            logger.debug(f"tool_parse error, content: {content}, \n{traceback.format_exc()}")
        return tool_list


class Agent(BaseAgent[Observation, List[ActionModel]]):
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
                 model_output_parser: ModelOutputParser[..., AgentResult] = LlmOutputParser(),
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
            model_output_parser: Llm response parse function for the agent standard output, transform llm response.
            tool_aggregate_func: Aggregation strategy for multiple tool results.
            event_handler_name: Custom handlers for certain types of events.
        """
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
        self.memory = MemoryFactory.instance()
        self.memory_config = conf.memory_config
        self.system_prompt: str = system_prompt if system_prompt else conf.system_prompt
        self.event_driven = event_driven

        self.need_reset = need_reset if need_reset else conf.need_reset
        # whether to keep contextual information, False means keep, True means reset in every step by the agent call
        self.step_reset = step_reset
        # tool_name: [tool_action1, tool_action2, ...]
        # self.black_tool_actions: Dict[str, List[str]] = black_tool_actions if black_tool_actions \
        #     else conf.get('black_tool_actions', {})
        self.model_output_parser = model_output_parser
        self.use_tools_in_prompt = use_tools_in_prompt if use_tools_in_prompt else conf.use_tools_in_prompt
        self.tools_aggregate_func = tool_aggregate_func if tool_aggregate_func else self._tools_aggregate_func
        self.event_handler_name = event_handler_name

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
            self.tools = tool_desc_transform(get_tool_desc(),
                                             tools=self.tool_names if self.tool_names else [],
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

    def messages_transform(self,
                           content: str,
                           image_urls: List[str] = None,
                           observation: Observation = None,
                           message: Message = None,
                           **kwargs) -> List[Dict[str, Any]]:
        return sync_exec(self.async_messages_transform, image_urls=image_urls, observation=observation,
                         message=message, **kwargs)

    def _clean_redundant_tool_call_messages(self, histories: List[MemoryItem]) -> None:
        try:
            for i in range(len(histories) - 1, -1, -1):
                his = histories[i]
                if his.metadata and "tool_calls" in his.metadata and his.metadata['tool_calls']:
                    logger.info(f"Agent {self.id()} deleted tool call messages from memory: {his}")
                    self.memory.delete(his.id)
                else:
                    break
        except Exception:
            logger.error(f"Agent {self.id()} clean redundant tool_call_messages error: {traceback.format_exc()}")
            pass

    def postprocess_terminate_loop(self, message: Message):
        logger.info(f"Agent {self.id()} postprocess_terminate_loop: {self.loop_step}")
        super().postprocess_terminate_loop(message)
        try:
            session_id = message.context.get_task().session_id
            task_id = message.context.get_task().id
            histories = self.memory.get_all(filters={
                "agent_id": self.id(),
                "session_id": session_id,
                "task_id": task_id,
                "memory_type": "message"
            })
            self._clean_redundant_tool_call_messages(histories)
        except Exception:
            logger.error(f"Agent {self.id()} postprocess_terminate_loop error: {traceback.format_exc()}")
            pass


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

        session_id = message.context.get_task().session_id
        task_id = message.context.get_task().id
        histories = self.memory.get_all(filters={
            "agent_id": self.id(),
            "session_id": session_id,
            "task_id": task_id,
            "memory_type": "message"
        })

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

        # from memory get last n messages
        histories = self.memory.get_last_n(self.memory_config.history_rounds, filters={
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
        return messages

    async def init_observation(self, observation: Observation) -> Observation:
        # supported string only
        # if self.task and isinstance(self.task, str) and self.task != observation.content:
        #     observation.content = f"base task is: {self.task}\n{observation.content}"
        #     # `task` only needs to be processed once and reflected in the context
        #     self.task = None

        # default use origin observation
        return observation

    def _log_messages(self, messages: List[Dict[str, Any]],context: Context,  **kwargs) -> None:
        from aworld.core.context.amni import AmniContext
        if isinstance(context, AmniContext):
            from aworld.core.context.amni.utils.context_log import PromptLogger
            PromptLogger.log_agent_call_llm_messages(self, messages=messages, context=context, **kwargs)
            return
        """Log the sequence of messages for debugging purposes"""
        logger.info(f"[agent] Invoking LLM with {len(messages)} messages:")
        logger.debug(f"[agent] use tools: {self.tools}")
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
            return AgentMessage(payload=actions,
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

        # Get current step information for trace recording
        source_span = trace.get_current_span()
        self._finished = False
        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        try:
            events = []
            async for event in self.run_hooks(message.context, HookPoint.PRE_LLM_CALL):
                events.append(event)
        except Exception:
            logger.debug(traceback.format_exc())

        messages = await self.build_llm_input(observation, info, message=message, **kwargs)

        serializable_messages = to_serializable(messages)
        message.context.context_info["llm_input"] = serializable_messages
        llm_response = None
        if source_span:
            source_span.set_attribute("messages", json.dumps(serializable_messages, ensure_ascii=False))
        try:
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
                    await self._add_message_to_memory(payload=llm_response,
                                                      message_type=MemoryType.AI,
                                                      context=message.context)
            else:
                logger.error(f"{self.id()} failed to get LLM response")
                raise RuntimeError(f"{self.id()} failed to get LLM response")

        try:
            events = []
            async for event in self.run_hooks(message.context, HookPoint.POST_LLM_CALL):
                events.append(event)
        except Exception as e:
            logger.debug(traceback.format_exc())

        agent_result = await self.model_output_parser.parse(llm_response,
                                                            agent_id=self.id(),
                                                            use_tools_in_prompt=self.use_tools_in_prompt)
        logger.info(f"agent_result: {agent_result}")
        policy_result: Optional[List[ActionModel]] = None
        if self.is_agent_finished(llm_response, agent_result):
            policy_result = agent_result.actions
        else:
            if not self.wait_tool_result:
                policy_result = agent_result.actions
            else:
                policy_result = await self.execution_tools(agent_result.actions, message)
        await self.send_llm_response_output(llm_response, agent_result, message.context, kwargs.get("outputs"))
        return policy_result

    async def execution_tools(self, actions: List[ActionModel], message: Message = None, **kwargs) -> List[ActionModel]:
        """Tool execution operations.

        Returns:
            ActionModel sequence. Tool execution result.
        """
        from aworld.utils.run_util import exec_tool

        tool_results = []
        for act in actions:
            if is_agent(act):
                continue
            tool_exec_response = await exec_tool(tool_name=act.tool_name,
                                         action_name=act.action_name,
                                         params=act.params,
                                         agent_name=self.id(),
                                         context=message.context.deep_copy(),
                                         sub_task=True,
                                         outputs=message.context.outputs,
                                         task_group_id=message.context.get_task().group_id or uuid.uuid4().hex)
            if not tool_exec_response.success:
                logger.warning(f"Agent {self.id()} _execute_tool failed with exception: {tool_exec_response.msg}",
                               color=Color.red)
                continue
            act_res = ActionResult(tool_call_id=act.tool_call_id, tool_name=act.tool_name, content=tool_exec_response.answer)
            tool_results.append(act_res)
            await self._add_message_to_memory(payload=act_res, message_type=MemoryType.TOOL, context=message.context)
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
        messages = await self.async_messages_transform(image_urls=images, observation=observation, message=message)
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
            self._log_messages(messages, tools=tools, context=message.context)
            stream_mode = kwargs.get("stream", False) or self.conf.llm_config.llm_stream_call if self.conf.llm_config else False
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

            else:
                # logger.info(f"llm_agent|invoke_model|tools={self.tools}")
                llm_response = await acall_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=float_temperature,
                    tools=tools,
                    stream=kwargs.get("stream", False),
                    **kwargs
                )

            logger.info(f"LLM Execute response: {json.dumps(llm_response.to_dict(), ensure_ascii=False)}")
            if llm_response:
                usage_process(llm_response.usage, message.context)
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
        return llm_response


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
                    headers={"context": message.context}
                )

                # Execute hook
                msg = await hook.exec(message, context)
                if msg:
                    logger.debug(f"Hook {hook.point()} executed successfully")
                    yield msg
            except Exception as e:
                logger.warning(f"Hook {hook.point()} execution failed: {traceback.format_exc()}")

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
            return system_prompt_template.format(context=context, task=content, tool_list=tool_list)

    async def _add_message_to_memory(self, payload: Any, message_type: MemoryType, context: Context):
        memory_msg = MemoryEventMessage(
            payload=payload,
            agent=self,
            memory_event_type=message_type,
            headers={"context": context}
        )
        try:
            future = await send_message_with_future(memory_msg)
            results = await future.wait(timeout=300)
            if not results:
                logger.warning(f"Memory write task failed: {memory_msg}")
        except Exception as e:
            logger.warn(f"Memory write task failed: {traceback.format_exc()}")

    async def send_llm_response_output(self, llm_response: ModelResponse, agent_result: AgentResult, context: Context,
                                       outputs: Outputs = None):
        """Send LLM response to output"""
        if not llm_response or llm_response.error:
            return

        llm_resp_output = MessageOutput(
            source=llm_response,
            metadata={"agent_id": self.id(), "agent_name": self.name(), "is_finished": self.finished}
        )
        if eventbus is not None and llm_response:
            await send_message(Message(
                category=Constants.OUTPUT,
                payload=llm_resp_output,
                sender=self.id(),
                session_id=context.session_id if context else "",
                headers={"context": context}
            ))
        elif not self.event_driven and outputs:
            await outputs.add_output(llm_resp_output)

    def is_agent_finished(self, llm_response: ModelResponse, agent_result: AgentResult) -> bool:
        if not agent_result.is_call_tool:
            self._finished = True
        return self.finished

    def _update_headers(self, input_message: Message) -> Dict[str, Any]:
        headers = input_message.headers.copy()
        headers['context'] = input_message.context
        headers['level'] = headers.get('level', 0) + 1
        if input_message.group_id:
            headers['parent_group_id'] = input_message.group_id
        return headers

    async def _filter_tools(self, context: Context) -> List[Dict[str, Any]]:
        from aworld.core.context.amni import AmniContext
        if not isinstance(context, AmniContext) or not self.skill_configs:
            logger.info(f"llm_agent don't need _filter_tools .. agent#{type(self)}#{self.id()}")
            return self.tools
        # get current active skills
        skills = await context.get_active_skills(namespace=self.id())

        return await skill_translate_tools(skills=skills, skill_configs=self.skill_configs, tools=self.tools, tool_mapping=self.tool_mapping)
