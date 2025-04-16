# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import abc
import json
import traceback
import uuid
from typing import Generic, TypeVar, Dict, Any, List, Tuple, Union, Callable

from aworld.core.agent.agent_desc import get_agent_desc
from aworld.core.envs.tool_desc import get_tool_desc
from aworld.logs.util import logger
from aworld.mcp.utils import mcp_tool_desc_transform
from aworld.models.llm import get_llm_model, call_llm_model
from aworld.models.model_response import ModelResponse
from pydantic import BaseModel

from aworld.config.conf import AgentConfig, load_config, ConfigDict
from aworld.core.common import Observation, ActionModel
from aworld.core.factory import Factory
from aworld.models.utils import tool_desc_transform, agent_desc_transform
from aworld.utils.common import convert_to_snake, is_abstract_method, sync_exec

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
        self._tools = []
        self.state = AgentStatus.START
        self._finished = True

        for k, v in kwargs.items():
            setattr(self, k, v)

    def name(self) -> str:
        return self._name

    def desc(self) -> str:
        return self._desc

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
                 executor: Callable[..., Any] = None,
                 resp_parse_func: Callable[..., Any] = None,
                 **kwargs):
        """A base class implementation of agent, using the `Observation` and `List[ActionModel]` protocols.

        Args:
            conf: Agent config, supported AgentConfig, ConfigDict or dict.
            executor: The agent special executor.
            resp_parse_func: Response parse function for the agent standard output.
        """
        super(Agent, self).__init__(conf, **kwargs)
        self.model_name = conf.llm_config.llm_model_name if conf.llm_config.llm_model_name else conf.llm_model_name
        self._llm = None
        self.memory = []
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
        self.executor = executor if executor else agent_executor
        agent_executor.register(self.name(), self)

    def reset(self, options: Dict[str, Any]):
        super().reset(options)
        self.memory = []

    @property
    def llm(self):
        # lazy
        if self._llm is None:
            conf = self.conf.llm_config if self.conf.llm_config.llm_base_url or self.conf.llm_config.llm_api_key else self.conf
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
        self.tools = tool_desc_transform(get_tool_desc(),
                                         tools=self.tool_names if self.tool_names else [])
        # Agents as tool
        self.tools.extend(self.handoffs_agent_as_tool())
        # MCP servers are tools
        self.tools.extend(self.mcp_is_tool())

    def messages_transform(self,
                           content: str,
                           image_urls: List[str] = None,
                           sys_prompt: str = None,
                           agent_prompt: str = None,
                           output_prompt: str = None,
                           max_step: int = 100,
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
        # query from memory, TODO: memory.query()
        histories = self.memory[-max_step:]
        if histories:
            # default use the first tool call
            for history in histories:
                messages.append(history.message)
                if history.tool_calls:
                    messages.append({'role': 'assistant', 'content': '', 'tool_calls': [history.tool_calls[0]]})
                else:
                    messages.append({'role': 'assistant', 'content': history.content})

            if histories[-1].tool_calls:
                tool_id = histories[-1].tool_calls[0].id
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
                params = json.loads(tool_call.function.arguments)
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

    @abc.abstractmethod
    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """

    @abc.abstractmethod
    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """


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


class AgentExecutor(object):
    """The default executor for agent execution can be used for sequential execution by the user."""

    def __init__(self, agent: Agent = None):
        self.agent = agent
        self.agents: Dict[str, Agent] = {}

    def register(self, name: str, agent: Agent):
        self.agents[name] = agent

    def execute(self, observation: Observation, **kwargs) -> List[ActionModel]:
        """"""
        return self.execute_agent(observation, self.agent, **kwargs)

    async def async_execute(self, observation: Observation, **kwargs) -> List[ActionModel]:
        """"""
        return await self.async_execute_agent(observation, self.agent, **kwargs)

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

            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.get('tool_calls'):
                    logger.info(f"[agent] Tool call: {tool_call.get('name')} - ID: {tool_call.get('id')}")
                    args = str(tool_call.get('args', {}))[:1000]
                    logger.info(f"[agent] Tool args: {args}...")

    def execute_agent(self,
                      observation: Observation,
                      agent: Agent,
                      **kwargs) -> List[ActionModel]:
        """The synchronous execution process of the agent with some hooks.

        Args:
            observation: Observation source from a tool or an agent.
            agent: The special agent instance.
        """
        agent = self._get_or_create_agent(observation.to_agent_name, agent, kwargs.get('conf'))
        agent._finished = False
        if is_abstract_method(agent, 'policy'):
            agent.desc_transform()
            images = observation.images if agent.conf.use_vision else None
            if agent.conf.use_vision and not images and observation.image:
                images = [observation.image]
            messages = agent.messages_transform(content=observation.content,
                                                image_urls=images,
                                                sys_prompt=agent.system_prompt,
                                                agent_prompt=agent.agent_prompt,
                                                output_prompt=agent.output_prompt)

            self._log_messages(messages)

            llm_response = None
            try:
                llm_response = call_llm_model(
                    agent.llm,
                    messages=messages,
                    model=agent.model_name,
                    temperature=agent.conf.llm_config.llm_temperature,
                    tools=agent.tools if agent.tools else None
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
                        agent.memory.append(MemoryModel(message=messages[-1],
                                                        tool_calls=llm_response.tool_calls,
                                                        content=llm_response.content))
                else:
                    logger.error(f"{agent.name()} failed to get LLM response")
                    raise RuntimeError(f"{agent.name()} failed to get LLM response")

            agent_result = sync_exec(agent.resp_parse_func, llm_response)
            if not agent_result.is_call_tool:
                agent._finished = True
            return agent_result.actions
        else:
            try:
                actions = agent.policy(observation, kwargs)
                return actions
            except:
                logger.warning(traceback.format_exc())
                return [ActionModel(agent_name=agent.name())]

    async def async_execute_agent(self,
                                  observation: Observation,
                                  agent: Agent,
                                  **kwargs) -> List[ActionModel]:
        """The asynchronous execution process of the agent.

        Args:
            observation: Observation source from a tool or an agent.
            agent: The special agent instance.
        """
        agent = self._get_or_create_agent(observation.to_agent_name, agent, kwargs.get('conf'))
        agent._finished = False
        if is_abstract_method(agent, 'async_policy'):
            await agent.async_desc_transform()
            images = observation.images
            if not images and observation.image:
                images = [observation.image]
            messages = agent.messages_transform(content=observation.content,
                                                image_urls=images,
                                                sys_prompt=agent.system_prompt,
                                                agent_prompt=agent.agent_prompt,
                                                output_prompt=agent.output_prompt)
            llm_response = None
            try:
                # TODO: models interface update
                llm_response = call_llm_model(
                    agent.llm,
                    messages=messages,
                    model=agent.model_name,
                    temperature=agent.conf.llm_config.llm_temperature,
                    tools=agent.tools if agent.tools else None
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
                        agent.memory.append(MemoryModel(message=messages[-1],
                                                        tool_calls=llm_response.tool_calls,
                                                        content=llm_response.content))
                else:
                    logger.error(f"{agent.name()} failed to get LLM response")
                    raise RuntimeError(f"{agent.name()} failed to get LLM response")

            agent_result = sync_exec(agent.resp_parse_func, llm_response)
            if not agent_result.is_call_tool:
                agent._finished = True
            return agent_result.actions
        else:
            try:
                actions = await agent.async_policy(observation, kwargs)
                return actions
            except:
                logger.warning(traceback.format_exc())
                return [ActionModel(agent_name=agent.name())]

    def _get_or_create_agent(self, name: str, agent: Agent = None, conf=None):
        if agent is None:
            agent = self.agents.get(name)
            if agent is None:
                agent = AgentFactory(name, conf=conf if conf else AgentConfig())
                self.agents[name] = agent
        return agent


agent_executor = AgentExecutor()
