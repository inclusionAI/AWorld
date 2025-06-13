# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import traceback
import uuid

import aworld.trace as trace

from collections import OrderedDict
from typing import Dict, Any, List, Union, Callable

from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.agent_desc import get_agent_desc
from aworld.core.agent.base import BaseAgent, AgentResult, is_agent_by_name, is_agent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.event_bus import InMemoryEventbus
from aworld.core.tool.tool_desc import get_tool_desc
from aworld.core.event.base import Message, ToolMessage, Constants
from aworld.logs.util import logger
from aworld.mcp_client.utils import mcp_tool_desc_transform
from aworld.core.memory import MemoryItem
from aworld.memory.main import Memory
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model, acall_llm_model_stream
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.models.utils import tool_desc_transform, agent_desc_transform
from aworld.output import Outputs
from aworld.output.base import StepOutput, MessageOutput
from aworld.sandbox import Sandbox
from aworld.utils.common import sync_exec, nest_dict_counter
from string import Template
from aworld.core.context.base import Context


class Agent(BaseAgent[Observation, List[ActionModel]]):
    """Basic agent for unified protocol within the framework."""

    def __init__(self,
                 conf: Union[Dict[str, Any], ConfigDict, AgentConfig],
                 *,
                 name: str = None,
                 desc: str = None,
                 system_prompt: str = None,
                 agent_prompt: str = None,
                 tool_names: List[str] = [],
                 agent_names: List[str] = [],
                 mcp_servers: List[str] = [],
                 mcp_config: Dict[str, Any] = {},
                 sandbox: Sandbox = None,
                 memory_store: str = 'inmemory',
                 history_messages: int = 100,
                 resp_parse_func: Callable[..., Any] = None,
                 handler: Callable[..., Any] = None,
                 black_tool_actions: List[str] = [],
                 need_reset: bool = True,
                 step_reset: bool = False,
                 use_tools_in_prompt: bool = False,
                 event_driven: bool = True,
                 **kwargs):
        """A api class implementation of agent, using the `Observation` and `List[ActionModel]` protocols.

        Args:
            conf: Agent config, supported AgentConfig, ConfigDict or dict.
            resp_parse_func: Response parse function for the agent standard output, transform llm response.
        """
        super(Agent, self).__init__(conf,
                                    name=name,
                                    desc=desc,
                                    tool_names=tool_names,
                                    agent_names=agent_names,
                                    mcp_servers=mcp_servers,
                                    mcp_config=mcp_config,
                                    sandbox=sandbox,
                                    **kwargs)
        conf = self.conf
        self.model_name = conf.llm_config.llm_model_name if conf.llm_config.llm_model_name else conf.llm_model_name
        self._llm = None
        self.system_prompt: str = system_prompt if system_prompt else conf.system_prompt
        self.agent_prompt: str = agent_prompt if agent_prompt else conf.agent_prompt
        self.memory_store_name = memory_store if memory_store else "inmemory"
        self.memory = Memory.from_config({"memory_store": self.memory_store_name})

        self.event_driven = event_driven if event_driven is None else conf.get('event_driven', False)
        self.handler: Callable[..., Any] = handler

        # agent reset on create
        self.need_reset = need_reset if need_reset is None else conf.need_reset
        # whether to keep contextual information, False means keep, True means reset in every step by the agent call
        self.step_reset = step_reset if step_reset is None else True
        # tool_name: [tool_action1, tool_action2, ...]
        self.black_tool_actions: Dict[str, List[str]] = black_tool_actions if black_tool_actions \
            else conf.get('black_tool_actions', {})
        self.resp_parse_func = resp_parse_func if resp_parse_func else self.response_parse
        self.history_messages = history_messages if history_messages else 100
        self.use_tools_in_prompt = use_tools_in_prompt if use_tools_in_prompt is None else conf.use_tools_in_prompt

    def reset(self, options: Dict[str, Any]):
        super().reset(options)
        self.memory = Memory.from_config(
            {"memory_store": options.pop("memory_store") if options.get("memory_store") else "inmemory"})

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
            return sync_exec(mcp_tool_desc_transform, self.mcp_servers, self.mcp_config)
        except Exception as e:
            logger.error(f"mcp_is_tool error: {e}")
            return []

    def desc_transform(self):
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""

        # Stateless tool
        self.tools = self._env_tool()
        # Agents as tool
        self.tools.extend(self._handoffs_agent_as_tool())
        # MCP servers are tools
        self.tools.extend(self._mcp_is_tool())
        return self.tools

    async def async_desc_transform(self):
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""

        # Stateless tool
        self.tools = self._env_tool()
        # Agents as tool
        self.tools.extend(self._handoffs_agent_as_tool())
        # MCP servers are tools
        # todo sandbox
        if self.sandbox:
            sand_box = self.sandbox
            mcp_tools = await sand_box.mcpservers.list_tools()
            self.tools.extend(mcp_tools)
        else:
            self.tools.extend(await mcp_tool_desc_transform(self.mcp_servers, self.mcp_config))

    def _messages_transform(
            self,
            observation: Observation,
            sys_prompt: str = None,
            agent_prompt: str = None,
    ):
        messages = []
        if sys_prompt:
            messages.append({'role': 'system', 'content': sys_prompt if not self.use_tools_in_prompt else sys_prompt.format(
                tool_list=self.tools)})

        content = observation.content
        if agent_prompt and '{task}' in agent_prompt:
            content = agent_prompt.format(task=observation.content)

        cur_msg = {'role': 'user', 'content': content}
        # query from memory,
        # histories = self.memory.get_last_n(self.history_messages, filter={"session_id": self.context.session_id})
        histories = self.memory.get_last_n(self.history_messages)
        messages.extend(histories)

        action_results = observation.action_result
        if action_results:
            for action_result in action_results:
                cur_msg['role'] = 'tool'
                cur_msg['tool_call_id'] = action_result.tool_id

        agent_info = self.context.context_info.get(self.name())
        if (self.use_tools_in_prompt and "is_use_tool_prompt" in agent_info and "tool_calls"
                in agent_info and agent_prompt):
            cur_msg['content'] = agent_prompt.format(action_list=agent_info["tool_calls"],
                                                     result=content)

        if observation.images:
            urls = [{'type': 'text', 'text': content}]
            for image_url in observation.images:
                urls.append({'type': 'image_url', 'image_url': {"url": image_url}})

            cur_msg['content'] = urls
        messages.append(cur_msg)
        return messages

    def messages_transform(self,
                           content: str,
                           image_urls: List[str] = None,
                           sys_prompt: str = None,
                           agent_prompt: str = None,
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
        # def _safe_template_substitute(template_str: str, **kwargs) -> str:
        #     """
        #     使用 string.Template 进行安全的模板替换
        #     """
        #     try:
        #         # 将 {variable} 格式转换为 $variable 格式
        #         template_str = template_str.replace('{task}', '$task')
        #         template = Template(template_str)
        #         return template.safe_substitute(**kwargs)
        #     except Exception as e:
        #         print(f"模板替换失败: {e}")
        #         return template_str

        messages = []
        if sys_prompt:
            if '$task' in sys_prompt:
                sys_prompt = Template(sys_prompt).safe_substitute(task=content)
                # sys_prompt = sys_prompt.format(task=content)

            messages.append({'role': 'system', 'content': sys_prompt if not self.use_tools_in_prompt else sys_prompt.format(
                tool_list=self.tools)})

        histories = self.memory.get_last_n(self.history_messages)
        user_content = content
        if not histories and agent_prompt and '{task}' in agent_prompt:
            user_content = agent_prompt.format(task=content)

        cur_msg = {'role': 'user', 'content': user_content}
        # query from memory,
        # histories = self.memory.get_last_n(self.history_messages, filter={"session_id": self.context.session_id})

        if histories:
            # default use the first tool call
            for history in histories:
                if not self.use_tools_in_prompt and "tool_calls" in history.metadata and history.metadata['tool_calls']:
                    messages.append({'role': history.metadata['role'], 'content': history.content,
                                     'tool_calls': [history.metadata["tool_calls"][0]]})
                else:
                    messages.append({'role': history.metadata['role'], 'content': history.content,
                                     "tool_call_id": history.metadata.get("tool_call_id")})

            if not self.use_tools_in_prompt and "tool_calls" in histories[-1].metadata and histories[-1].metadata[
                'tool_calls']:
                tool_id = histories[-1].metadata["tool_calls"][0].id
                if tool_id:
                    cur_msg['role'] = 'tool'
                    cur_msg['tool_call_id'] = tool_id
            if self.use_tools_in_prompt and "is_use_tool_prompt" in histories[-1].metadata and "tool_calls" in \
                    histories[-1].metadata and agent_prompt:
                cur_msg['content'] = agent_prompt.format(action_list=histories[-1].metadata["tool_calls"],
                                                         result=content)

        if image_urls:
            urls = [{'type': 'text', 'text': content}]
            for image_url in image_urls:
                urls.append({'type': 'image_url', 'image_url': {"url": image_url}})

            cur_msg['content'] = urls
        messages.append(cur_msg)
        return messages

    def use_tool_list(self, resp: ModelResponse) -> List[Dict[str, Any]]:
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
            logger.debug(f"tool_parse error, content: {resp.content}, \nerror msg: {e}")
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
                    logger.warning(f"{tool_call.function.arguments} parse to json fail.")
                    params = {}
                # format in framework
                names = full_name.split("__")
                tool_name = names[0]
                if is_agent_by_name(tool_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_id=tool_call.id,
                                               agent_name=self.name(),
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_id=tool_call.id,
                                               action_name=action_name,
                                               agent_name=self.name(),
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
                if is_agent_by_name(tool_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_id=use_tool.get('id'),
                                               agent_name=self.name(),
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_id=use_tool.get('id'),
                                               action_name=action_name,
                                               agent_name=self.name(),
                                               params=params,
                                               policy_info=content))
        else:
            if content:
                content = content.replace("```json", "").replace("```", "")
            # no tool call, agent name is itself.
            results.append(ActionModel(agent_name=self.name(), policy_info=content))
        return AgentResult(actions=results, current_state=None, is_call_tool=is_call_tool)

    def _log_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Log the sequence of messages for debugging purposes"""
        logger.info(f"[{self.name()} agent] Invoking LLM with {len(messages)} messages:")
        for i, msg in enumerate(messages):
            prefix = msg.get('role')
            logger.info(f"[{self.name()} agent] Message {i + 1}: {prefix} ===================================")
            if isinstance(msg['content'], list):
                for item in msg['content']:
                    if item.get('type') == 'text':
                        logger.info(f"[{self.name()} agent] Text content: {item.get('text')}")
                    elif item.get('type') == 'image_url':
                        image_url = item.get('image_url', {}).get('url', '')
                        if image_url.startswith('data:image'):
                            logger.info(f"[{self.name()} agent] Image: [Base64 image data]")
                        else:
                            logger.info(f"[{self.name()} agent] Image URL: {image_url[:30]}...")
            else:
                content = str(msg['content'])
                chunk_size = 500
                for j in range(0, len(content), chunk_size):
                    chunk = content[j:j + chunk_size]
                    if j == 0:
                        logger.info(f"[{self.name()} agent] Content: {chunk}")
                    else:
                        logger.info(f"[{self.name()} agent] Content (continued): {chunk}")

            if 'tool_calls' in msg and msg['tool_calls']:
                for tool_call in msg.get('tool_calls'):
                    if isinstance(tool_call, dict):
                        logger.info(f"[{self.name()} agent] Tool call: {tool_call.get('name')} - ID: {tool_call.get('id')}")
                        args = str(tool_call.get('args', {}))[:1000]
                        logger.info(f"[{self.name()} agent] Tool args: {args}...")
                    elif isinstance(tool_call, ToolCall):
                        logger.info(f"[{self.name()} agent] Tool call: {tool_call.function.name} - ID: {tool_call.id}")
                        args = str(tool_call.function.arguments)[:1000]
                        logger.info(f"[{self.name()} agent] Tool args: {args}...")

    def _agent_result(self, actions: List[ActionModel], caller: str):
        if not actions:
            raise Exception(f'{self.name()} no action decision has been made.')

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
            _group_name = f"{self.name()}_{uuid.uuid1().hex}"

        # complex processing
        if _group_name:
            logger.warning(f"more than one agent an tool causing confusion, will choose the first one. {agents}")
            agents = [agents[0]] if agents else []
            for _, v in tools.items():
                actions = v
                break

        if agents:
            return Message(payload=actions,
                           caller=caller,
                           sender=self.name(),
                           receiver=actions[0].tool_name,
                           session_id=self.context.session_id,
                           category=Constants.AGENT)
        else:
            return ToolMessage(payload=actions,
                               caller=caller,
                               sender=self.name(),
                               receiver=actions[0].tool_name,
                               session_id=self.context.session_id)

    def post_run(self, policy_result: List[ActionModel], policy_input: Observation) -> Message:
        return self._agent_result(
            policy_result,
            policy_input.from_agent_name if policy_input.from_agent_name else policy_input.observer
        )

    async def async_post_run(self, policy_result: List[ActionModel], policy_input: Observation) -> Message:
        return self._agent_result(
            policy_result,
            policy_input.from_agent_name if policy_input.from_agent_name else policy_input.observer
        )

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
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
        parent_span = trace.get_current_span()

        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        self._finished = False
        self.desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
            observation.images = images
        messages = self.messages_transform(content=observation.content,
                                           image_urls=observation.images,
                                           sys_prompt=self.system_prompt,
                                           agent_prompt=self.agent_prompt)

        self._log_messages(messages)
        self.memory.add(MemoryItem(
            content=messages[-1]['content'],
            metadata={
                "role": messages[-1]['role'],
                "agent_name": self.name(),
                "tool_call_id": messages[-1].get("tool_call_id")
            }
        ))

        llm_response = None
        serializable_messages = self._to_serializable(messages)
        if parent_span:
            parent_span.set_attribute("step", step)
            parent_span.set_attribute("observation", json.dumps(observation.model_dump(exclude_none=True),
                                                                ensure_ascii=False) if observation else "")
            parent_span.set_attribute("messages", json.dumps(
                serializable_messages, ensure_ascii=False))

        try:
            llm_response = call_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=self.conf.llm_config.llm_temperature,
                tools=self.tools if not self.use_tools_in_prompt and self.tools else None
            )

            logger.info(f"Execute response: {json.dumps(llm_response.to_dict(), ensure_ascii=False)}")
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                use_tools = self.use_tool_list(llm_response)
                is_use_tool_prompt = len(use_tools) > 0
                if llm_response.error:
                    logger.info(f"llm result error: {llm_response.error}")
                else:
                    info = {
                        "role": "assistant",
                        "agent_name": self.name(),
                        "tool_calls": llm_response.tool_calls if not self.use_tools_in_prompt else use_tools,
                        "is_use_tool_prompt": is_use_tool_prompt if not self.use_tools_in_prompt else False
                    }
                    self.memory.add(MemoryItem(
                        content=llm_response.content,
                        metadata=info
                    ))
                    # rewrite
                    self.context.context_info[self.name()] = info
            else:
                logger.error(f"{self.name()} failed to get LLM response")
                raise RuntimeError(f"{self.name()} failed to get LLM response")

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True

        if output:
            output.add_part(MessageOutput(source=llm_response, json_parse=False))
            output.mark_finished()
        if parent_span:
            parent_span.set_attribute("actions", json.dumps(
                [action.model_dump() for action in agent_result.actions], ensure_ascii=False) if agent_result.actions else "")
        return agent_result.actions

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        outputs = None
        if kwargs.get("outputs") and isinstance(kwargs.get("outputs"), Outputs):
            outputs = kwargs.get("outputs")

        # Get current step information for trace recording
        step = kwargs.get("step", 0)
        exp_id = kwargs.get("exp_id", "")
        parent_span = trace.get_current_span()

        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        self._finished = False
        await self.async_desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        messages = self.messages_transform(content=observation.content,
                                           image_urls=images,
                                           sys_prompt=self.system_prompt,
                                           agent_prompt=self.agent_prompt)

        self._log_messages(messages)
        self.memory.add(MemoryItem(
            content=messages[-1]['content'],
            metadata={
                "role": messages[-1]['role'],
                "agent_name": self.name(),
                "tool_call_id": messages[-1].get("tool_call_id")
            }
        ))

        llm_response = None
        span_name = f"llm_call_{exp_id}"
        serializable_messages = self._to_serializable(messages)
        if parent_span:
            parent_span.set_attribute("step", step)
            parent_span.set_attribute("observation", json.dumps(observation.model_dump(exclude_none=True),
                                                                ensure_ascii=False) if observation else "")
            parent_span.set_attribute("messages", json.dumps(
                serializable_messages, ensure_ascii=False))

        try:
            stream_mode = kwargs.get("stream", False)
            if stream_mode:
                llm_response = ModelResponse(id="", model="", content="", tool_calls=[])
                resp_stream = acall_llm_model_stream(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=self.tools if not self.use_tools_in_prompt and self.tools else None,
                    stream=True
                )

                if InMemoryEventbus.instance() and resp_stream:
                    await InMemoryEventbus.instance().publish(Message(
                        category=Constants.OUTPUT,
                        payload=resp_stream,
                        sender=self.name(),
                        session_id=Context.instance().session_id
                    ))
                elif not self.event_driven and outputs and isinstance(outputs, Outputs):
                    await outputs.add_output(MessageOutput(source=resp_stream, json_parse=False))
                async for resp in resp_stream:
                    if resp.content:
                        llm_response.content += resp.content
                    if resp.tool_calls:
                        llm_response.tool_calls.extend(resp.tool_calls)
                    if resp.error:
                        llm_response.error = resp.error
                    llm_response.id = resp.id
                    llm_response.model = resp.model
                    llm_response.usage = nest_dict_counter(llm_response.usage, resp.usage)

            else:
                llm_response = await acall_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=self.tools if not self.use_tools_in_prompt and self.tools else None,
                    stream=kwargs.get("stream", False)
                )
                if InMemoryEventbus.instance() and llm_response:
                    await InMemoryEventbus.instance().publish(Message(
                        category=Constants.OUTPUT,
                        payload=llm_response,
                        sender=self.name(),
                        session_id=Context.instance().session_id
                    ))
                elif not self.event_driven and outputs and isinstance(outputs, Outputs):
                    await outputs.add_output(MessageOutput(source=llm_response, json_parse=False))

            logger.info(f"Execute response: {json.dumps(llm_response.to_dict(), ensure_ascii=False)}")

        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                use_tools = self.use_tool_list(llm_response)
                is_use_tool_prompt = len(use_tools) > 0
                if llm_response.error:
                    logger.info(f"llm result error: {llm_response.error}")
                else:
                    self.memory.add(MemoryItem(
                        content=llm_response.content,
                        metadata={
                            "role": "assistant",
                            "agent_name": self.name(),
                            "tool_calls": llm_response.tool_calls if not self.use_tools_in_prompt else use_tools,
                            "is_use_tool_prompt": is_use_tool_prompt if not self.use_tools_in_prompt else False
                        }
                    ))
            else:
                logger.error(f"{self.name()} failed to get LLM response")
                raise RuntimeError(f"{self.name()} failed to get LLM response")

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True
        if parent_span:
            parent_span.set_attribute("actions", json.dumps(
                [action.model_dump() for action in agent_result.actions], ensure_ascii=False) if agent_result.actions else "")
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
