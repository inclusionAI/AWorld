# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import abc
import json
import traceback
import uuid
from typing import Generic, TypeVar, Dict, Any, List, Tuple, Union, Callable

from pydantic import BaseModel

from aworld.config.conf import AgentConfig, load_config, ConfigDict
from aworld.core.agent.agent_desc import get_agent_desc
from aworld.core.common import Observation, ActionModel
from aworld.core.envs.tool_desc import get_tool_desc
from aworld.core.factory import Factory
from aworld.logs.util import logger
from aworld.mcp.utils import mcp_tool_desc_transform
from aworld.memory.base import MemoryItem
from aworld.memory.main import Memory
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.models.utils import tool_desc_transform, agent_desc_transform
from aworld.output import MessageOutput
from aworld.output.base import StepOutput
from aworld.utils.common import convert_to_snake, sync_exec

INPUT = TypeVar('INPUT')
OUTPUT = TypeVar('OUTPUT')


def is_agent_by_name(name: str) -> bool:
    return name in AgentFactory


class AgentStatus:
    # Init status
    START = 0
    # Agent is running for monitor or collection
    RUNNING = 1
    # Agent reject the task
    REJECT = 2
    # Agent is idle
    IDLE = 3
    # Agent meets exception
    ERROR = 4
    # End of one agent step
    DONE = 5
    # End of one task step
    FINISHED = 6


class AgentResult(BaseModel):
    current_state: Any
    actions: List[ActionModel]
    is_call_tool: bool = True


class MemoryModel(BaseModel):
    # TODO: memory module
    message: Dict = {}
    tool_calls: Any = None
    content: Any = None


class BaseAgent(Generic[INPUT, OUTPUT]):
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        self.conf = conf
        if isinstance(conf, ConfigDict):
            pass
        elif isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, AgentConfig):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())
        else:
            logger.warning(f"Unknown conf type: {type(conf)}")

        self._name = kwargs.pop("name", self.conf.get("name", convert_to_snake(self.__class__.__name__)))
        self._desc = kwargs.pop("desc") if kwargs.get("desc") else self.conf.get('desc', '')
        # Unique flag based agent name
        self.id = f"{self.name()}_{uuid.uuid1().hex[0:6]}"
        self.task = None
        # An agent can use the tool list
        self.tool_names: List[str] = kwargs.pop("tool_names", [])
        # An agent can delegate tasks to other agent
        self.handoffs: List[str] = kwargs.pop("agent_names", [])
        # Supported MCP server
        self.mcp_servers: List[str] = kwargs.pop("mcp_servers", [])
        self.trajectory: List[Tuple[INPUT, Dict[str, Any], AgentResult]] = []
        # all tools that the agent can use. note: string name/id only
        self.tools = []
        self.state = AgentStatus.START
        self._finished = True

        for k, v in kwargs.items():
            setattr(self, k, v)

    def name(self) -> str:
        return self._name

    def desc(self) -> str:
        return self._desc

    def run(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        return self.policy(observation, info, **kwargs)

    async def async_run(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        return await self.async_policy(observation, info, **kwargs)

    @abc.abstractmethod
    def policy(self, observation: INPUT, info: Dict[str, Any] = None, **kwargs) -> OUTPUT:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.
        """

    @abc.abstractmethod
    async def async_policy(self, observation: INPUT, info: Dict[str, Any] = None, **kwargs) -> OUTPUT:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.
        """

    def reset(self, options: Dict[str, Any]):
        """Clean agent instance state and reset."""
        if options is None:
            options = {}
        self.task = options.get("task")
        self.tool_names = options.get("tool_names", [])
        self.handoffs = options.get("agent_names", [])
        self.mcp_servers = options.get("mcp_servers", [])
        self.tools = []
        self.trajectory = []
        self._finished = True

    async def async_reset(self, options: Dict[str, Any]):
        """Clean agent instance state and reset."""
        self.task = options.get("task")

    @property
    def finished(self) -> bool:
        """Agent finished the thing, default is True."""
        return self._finished


class Agent(BaseAgent[Observation, Union[List[ActionModel], None]]):
    """Basic agent for unified protocol within the framework."""

    def __init__(self,
                 conf: Union[Dict[str, Any], ConfigDict, AgentConfig],
                 resp_parse_func: Callable[..., Any] = None,
                 **kwargs):
        """A base class implementation of agent, using the `Observation` and `List[ActionModel]` protocols.

        Args:
            conf: Agent config, supported AgentConfig, ConfigDict or dict.
            resp_parse_func: Response parse function for the agent standard output.
        """
        super(Agent, self).__init__(conf, **kwargs)
        self.model_name = conf.llm_config.llm_model_name if conf.llm_config.llm_model_name else conf.llm_model_name
        self._llm = None
        self.memory = Memory.from_config(
            {"memory_store": kwargs.pop("memory_store") if kwargs.get("memory_store") else "inmemory"})
        self.system_prompt: str = kwargs.pop("system_prompt") if kwargs.get("system_prompt") else conf.system_prompt
        self.agent_prompt: str = kwargs.get("agent_prompt") if kwargs.get("agent_prompt") else conf.agent_prompt
        self.output_prompt: str = kwargs.get("output_prompt") if kwargs.get("output_prompt") else conf.output_prompt

        self.need_reset = kwargs.get('need_reset') if kwargs.get('need_reset') else conf.need_reset
        # whether to keep contextual information, False means keep, True means reset in every step by the agent call
        self.step_reset = kwargs.get('step_reset') if kwargs.get('step_reset') else True
        # tool_name: [tool_action1, tool_action2, ...]
        self.black_tool_actions: Dict[str, List[str]] = kwargs.get("black_tool_actions") if kwargs.get(
            "black_tool_actions") else self.conf.get('black_tool_actions', {})
        self.resp_parse_func = resp_parse_func if resp_parse_func else self.response_parse
        self.history_messages = kwargs.get("history_messages") if kwargs.get("history_messages") else 100

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

    def env_tool(self):
        """Description of agent as tool."""
        return tool_desc_transform(get_tool_desc(),
                                   tools=self.tool_names if self.tool_names else [],
                                   black_tool_actions=self.black_tool_actions)

    def handoffs_agent_as_tool(self):
        """Description of agent as tool."""
        return agent_desc_transform(get_agent_desc(),
                                    agents=self.handoffs if self.handoffs else [])

    def mcp_is_tool(self):
        """Description of mcp servers are tools."""
        try:
            return sync_exec(mcp_tool_desc_transform, self.mcp_servers)
        except Exception as e:
            logger.error(f"mcp_is_tool error: {e}")
            return []

    def desc_transform(self):
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""

        # Stateless tool
        self.tools = self.env_tool()
        # Agents as tool
        self.tools.extend(self.handoffs_agent_as_tool())
        # MCP servers are tools
        self.tools.extend(self.mcp_is_tool())
        return self.tools

    async def async_desc_transform(self):
        """Transform of descriptions of supported tools, agents, and MCP servers in the framework to support function calls of LLM."""

        # Stateless tool
        self.tools = self.env_tool()
        # Agents as tool
        self.tools.extend(self.handoffs_agent_as_tool())
        # MCP servers are tools
        self.tools.extend(await mcp_tool_desc_transform(self.mcp_servers))

    def messages_transform(self,
                           content: str,
                           image_urls: List[str] = None,
                           sys_prompt: str = None,
                           agent_prompt: str = None,
                           output_prompt: str = None,
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
        messages = []
        if sys_prompt:
            messages.append({'role': 'system', 'content': sys_prompt})

        if agent_prompt:
            content = agent_prompt.format(task=content)
        if output_prompt:
            content += output_prompt

        cur_msg = {'role': 'user', 'content': content}
        # query from memory,
        histories = self.memory.get_last_n(self.history_messages)
        if histories:
            # default use the first tool call
            for history in histories:
                if "tool_calls" in history.metadata and history.metadata['tool_calls']:
                    messages.append({'role': history.metadata['role'], 'content': history.content,
                                     'tool_calls': [history.metadata["tool_calls"][0]]})
                else:
                    messages.append({'role': history.metadata['role'], 'content': history.content,
                                     "tool_call_id": history.metadata.get("tool_call_id")})

            if "tool_calls" in histories[-1].metadata and histories[-1].metadata['tool_calls']:
                tool_id = histories[-1].metadata["tool_calls"][0].id
                if tool_id:
                    cur_msg['role'] = 'tool'
                    cur_msg['tool_call_id'] = tool_id

        if image_urls:
            urls = [{'type': 'text', 'text': content}]
            for image_url in image_urls:
                urls.append({'type': 'image_url', 'image_url': {"url": image_url}})

            cur_msg['content'] = urls
        messages.append(cur_msg)
        return messages

    def response_parse(self, resp: ModelResponse) -> AgentResult:
        """Default parse response by LLM."""
        results = []
        if not resp:
            logger.warning("LLM no valid response!")
            return AgentResult(actions=[], current_state=None)

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
                    results.append(ActionModel(agent_name=tool_name,
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               action_name=action_name,
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
        logger.info(f"[agent] Invoking LLM with {len(messages)} messages:")
        for i, msg in enumerate(messages):
            prefix = msg.get('role')
            logger.info(f"[agent] Message {i + 1}: {prefix} ===================================")
            if isinstance(msg['content'], list):
                for item in msg['content']:
                    if item.get('type') == 'text':
                        logger.info(f"[agent] Text content: {item.get('text')}")
                    elif item.get('type') == 'image_url':
                        image_url = item.get('image_url', {}).get('url', '')
                        if image_url.startswith('data:image'):
                            logger.info(f"[agent] Image: [Base64 image data]")
                        else:
                            logger.info(f"[agent] Image URL: {image_url[:30]}...")
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
                        logger.info(f"[agent] Tool call: {tool_call.get('name')} - ID: {tool_call.get('id')}")
                        args = str(tool_call.get('args', {}))[:1000]
                        logger.info(f"[agent] Tool args: {args}...")
                    elif isinstance(tool_call, ToolCall):
                        logger.info(f"[agent] Tool call: {tool_call.function.name} - ID: {tool_call.id}")
                        args = str(tool_call.function.arguments)[:1000]
                        logger.info(f"[agent] Tool args: {args}...")

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        if kwargs.get("output") and isinstance(kwargs.get("output"), StepOutput):
            output = kwargs["output"]

        self._finished = False
        self.desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        messages = self.messages_transform(content=observation.content,
                                           image_urls=images,
                                           sys_prompt=self.system_prompt,
                                           agent_prompt=self.agent_prompt,
                                           output_prompt=self.output_prompt)

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
        try:
            llm_response = call_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=self.conf.llm_config.llm_temperature,
                tools=self.tools if self.tools else None
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
                    self.memory.add(MemoryItem(
                        content=llm_response.content,
                        metadata={
                            "role": "assistant",
                            "agent_name": self.name(),
                            "tool_calls": llm_response.tool_calls,
                        }
                    ))
            else:
                logger.error(f"{self.name()} failed to get LLM response")
                raise RuntimeError(f"{self.name()} failed to get LLM response")

        # output.add_part(MessageOutput(source=llm_response, json_parse=False))
        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True
        # output.mark_finished()
        return agent_result.actions

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """

        self._finished = False
        await self.async_desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        messages = self.messages_transform(content=observation.content,
                                           image_urls=images,
                                           sys_prompt=self.system_prompt,
                                           agent_prompt=self.agent_prompt,
                                           output_prompt=self.output_prompt)

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
        try:
            llm_response = await acall_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=self.conf.llm_config.llm_temperature,
                tools=self.tools if self.tools else None
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
                    self.memory.add(MemoryItem(
                        content=llm_response.content,
                        metadata={
                            "role": "assistant",
                            "agent_name": self.name(),
                            "tool_calls": llm_response.tool_calls,
                        }
                    ))
            else:
                logger.error(f"{self.name()} failed to get LLM response")
                raise RuntimeError(f"{self.name()} failed to get LLM response")

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True
        return agent_result.actions


class AgentManager(Factory):
    def __init__(self, type_name: str = None):
        super(AgentManager, self).__init__(type_name)
        self._agent_conf = {}
        self._agent_instance = {}

    def __call__(self, name: str = None, *args, **kwargs):
        if name is None:
            return self

        conf = self._agent_conf.get(name)
        if not conf:
            logger.warning(f"{name} not find conf in tool factory")
            conf = dict()
        elif isinstance(conf, BaseModel):
            conf = conf.model_dump()

        user_conf = kwargs.pop('conf', None)
        if user_conf:
            if isinstance(user_conf, BaseModel):
                conf.update(user_conf.model_dump())
            elif isinstance(user_conf, dict):
                conf.update(user_conf)
            else:
                logger.warning(f"Unknown conf type: {type(user_conf)}, ignored!")

        conf['name'] = name
        conf = ConfigDict(conf)
        if name in self._cls:
            agent = self._cls[name](conf=conf, **kwargs)
            self._agent_instance[name] = agent
        else:
            raise ValueError(f"Can not find {name} agent!")
        return agent

    def desc(self, name: str) -> str:
        if self._agent_instance.get(name, None) and self._agent_instance[name].desc:
            print("------------", self._agent_instance[name].desc)
            return self._agent_instance[name].desc
        return self._desc.get(name, "")

    def register(self, name: str, desc: str, conf_file_name: str = None, **kwargs):
        """Register a tool to tool factory.

        Args:
            name: Tool name
            desc: Tool description
            supported_action: Tool abilities
            conf_file_name: Default tool config
        """
        res = super(AgentManager, self).register(name, desc, **kwargs)
        conf_file_name = conf_file_name if conf_file_name else f"{name}.yaml"
        conf = load_config(conf_file_name, kwargs.get("dir"))
        if not conf:
            logger.warning(f"{conf_file_name} not find, will use default")
            # use general tool config
            conf = AgentConfig().model_dump()
        self._agent_conf[name] = conf
        return res


AgentFactory = AgentManager("agent_type")
