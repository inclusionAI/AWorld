# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time

import abc
import os
import uuid
from typing import Any, Dict, Generic, List, Tuple, TypeVar, Union, Optional

from pydantic import BaseModel

from aworld.config.conf import AgentConfig, ConfigDict, load_config, TaskRunMode
from aworld.core.common import ActionModel
from aworld.events import eventbus
from aworld.core.event.base import Constants, Message, AgentMessage, TopicType
from aworld.core.factory import Factory
from aworld.events.util import send_message
from aworld.logs.util import logger, digest_logger
from aworld.output.base import StepOutput
from aworld.sandbox import Sandbox
from aworld.utils.common import convert_to_snake, replace_env_variables, sync_exec
from aworld.mcp_client.utils import replace_mcp_servers_variables, extract_mcp_servers_from_config

INPUT = TypeVar("INPUT")
OUTPUT = TypeVar("OUTPUT")

# Forward declaration
AgentFactory = None


def is_agent_by_name(name: str) -> bool:
    return name in AgentFactory if AgentFactory else False


def is_agent(policy: ActionModel) -> bool:
    return is_agent_by_name(policy.tool_name) or (not policy.tool_name and not policy.action_name)


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

    def __init__(
        self,
        name: str,
        conf: Union[Dict[str, Any], ConfigDict, AgentConfig, None] = None,
        desc: str = None,
        agent_id: str = None,
        *,
        task: Any = None,
        tool_names: List[str] = None,
        agent_names: List[str] = None,
        mcp_servers: List[str] = None,
        mcp_config: Dict[str, Any] = None,
        black_tool_actions: Dict[str, List[str]] = None,
        feedback_tool_result: bool = True,
        wait_tool_result: bool = False,
        sandbox: Sandbox = None,
        **kwargs,
    ):
        """Base agent init.

        Args:
            conf: Agent config for internal processes.
            name: Agent name as identifier.
            desc: Agent description as tool description.
            task: The original task of the agent, will be automatically merged into messages after setting.
            tool_names: Tool names of local that agents can use.
            agent_names: Agents as tool name list.
            mcp_servers: Mcp names that the agent can use.
            mcp_config: Mcp config for mcp servers.
            feedback_tool_result: Whether feedback on the results of the tool.
                Agent1 uses tool1 when the value is True, it does not go to the other agent after obtaining the result of tool1.
                Instead, Agent1 uses the tool's result and makes a decision again.
            wait_tool_result: Whether wait on the results of the tool.
            sandbox: Sandbox instance for tool execution, advanced usage.
        """
        if conf is None:
            conf = AgentConfig()
        if isinstance(conf, ConfigDict):
            self.conf = conf
        elif isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, AgentConfig):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())
        else:
            logger.warning(f"Unknown conf type: {type(conf)}")

        self._init_id_name(name, agent_id)
        self._desc = desc if desc else self._name
        self.task: Any = task
        # An agent can use the tool list
        self.tool_names: List[str] = tool_names or []
        human_tools = self.conf.get("human_tools", [])
        for tool in human_tools:
            self.tool_names.append(tool)
        # An agent can delegate tasks to other agent
        self.handoffs: List[str] = agent_names or []
        if sandbox:
            # ✅ Tool Access Control Fix:
            # Prioritize agent's explicit mcp_servers parameter over sandbox defaults
            # This enables principle of least privilege: each agent specifies its own tool permissions
            #
            # Logic:
            # - If mcp_servers parameter is explicitly provided (even []), use it
            # - Otherwise, fall back to sandbox.mcp_servers
            agent_mcp_servers = mcp_servers if mcp_servers is not None else (sandbox.mcp_servers or [])
            self.mcp_servers: List[str] = extract_mcp_servers_from_config(sandbox.mcp_config, agent_mcp_servers)
            self.mcp_config: Dict[str, Any] = replace_env_variables(sandbox.mcp_config or {})
        else:
            self.mcp_config: Dict[str, Any] = replace_env_variables(mcp_config or {})
            self.mcp_servers: List[str] = extract_mcp_servers_from_config(self.mcp_config, mcp_servers or [])
        self.skill_configs: Dict[str, Any] = self.conf.get("skill_configs", {})
        # derive mcp_servers from skill_configs if provided
        if self.skill_configs:
            self.mcp_servers = replace_mcp_servers_variables(self.skill_configs, self.mcp_servers, [])
            from aworld.core.context.amni.tool.context_skill_tool import CONTEXT_SKILL
            self.tool_names.extend([CONTEXT_SKILL])
        ptc_tools = self.conf.get("ptc_tools", []) or kwargs.get("ptc_tools", [])
        if ptc_tools:
            self.ptc_tools = ptc_tools
            from aworld.experimental.ptc.ptc_tool import PTC_TOOL
            self.tool_names.extend([PTC_TOOL])
        else:
            self.ptc_tools = []

        # tool_name: [tool_action1, tool_action2, ...]
        self.black_tool_actions: Dict[str, List[str]] = black_tool_actions or {}
        self.trajectory: List[Tuple[INPUT, Dict[str, Any], AgentResult]] = []
        # all tools that the agent can use. note: string name/id only
        self.tools = []
        self.state = AgentStatus.START
        self._finished = True
        self.hooks: Dict[str, List[str]] = {}
        self.feedback_tool_result = feedback_tool_result
        self.wait_tool_result = wait_tool_result
        self.sandbox = sandbox
        if not self.sandbox and (self.mcp_servers or self.tool_names):
            self.sandbox = Sandbox(
                mcp_servers=self.mcp_servers, mcp_config=self.mcp_config,
                black_tool_actions=self.black_tool_actions,
                skill_configs=self.skill_configs
            )
        self.loop_step = 0
        self.max_loop_steps = kwargs.pop("max_loop_steps", 20)

        # Peer-to-peer communication capability (enabled by HybridBuilder)
        self._is_peer_enabled = False
        self._peer_agents: Dict[str, 'BaseAgent'] = {}
        self._current_context: Optional['Context'] = None

    def _init_id_name(self, name: str, agent_id: str = None):
        self._name = name if name else convert_to_snake(self.__class__.__name__)
        self._id = (
            agent_id if agent_id else f"{self._name}---uuid{uuid.uuid1().hex[0:6]}uuid"
        )

    def id(self) -> str:
        return self._id

    def name(self):
        return self._name

    def desc(self) -> str:
        return self._desc

    def run(self, message: Message, **kwargs) -> Message:
        message.context.update_agent_step(self.id())
        task = message.context.get_task()
        if task.conf.get("run_mode") == TaskRunMode.INTERACTIVE:
            agent = task.swarm.ordered_agents[0] if task.agent is None else task.agent
            message.context.new_trajectory_step(agent.id())
        caller = message.caller
        if caller and caller == self.id():
            self.loop_step += 1
        else:
            self.loop_step = 0
        should_term = self.sync_should_terminate_loop(message)
        if should_term:
            self.postprocess_terminate_loop(message)
            return AgentMessage(
                payload=message.payload,
                caller=message.sender,
                sender=self.id(),
                session_id=message.context.session_id,
                headers=message.headers
            )
        observation = message.payload
        sync_exec(
            send_message,
            Message(
                category=Constants.OUTPUT,
                payload=StepOutput.build_start_output(
                    name=f"{self.id()}", alias_name=self.name(), step_num=0
                ),
                sender=self.id(),
                session_id=message.context.session_id,
                headers={"context": message.context},
            ),
        )
        self.pre_run()
        result = self.policy(observation, message=message, **kwargs)
        final_result = self.post_run(result, observation, message)
        return final_result

    async def async_run(self, message: Message, **kwargs) -> Message:
        try:
            # Store context for peer communication
            self._current_context = message.context
            message.context.update_agent_step(self.id())
            task = message.context.get_task()
            if task and task.conf and task.conf.get("run_mode") == TaskRunMode.INTERACTIVE:
                agent = task.swarm.ordered_agents[0] if task.agent is None else task.agent
                message.context.new_trajectory_step(agent.id())
            caller = message.caller
            if caller and caller == self.id():
                self.loop_step += 1
            else:
                self.loop_step = 0
            should_term = await self.should_terminate_loop(message)
            if should_term:
                self.postprocess_terminate_loop(message)
                return AgentMessage(
                    payload=message.payload,
                    caller=message.sender,
                    sender=self.id(),
                    session_id=message.context.session_id,
                    headers=message.headers
                )
            observation = message.payload
            if eventbus is not None:
                await send_message(
                    Message(
                        category=Constants.OUTPUT,
                        payload=StepOutput.build_start_output(
                            name=f"{self.id()}", alias_name=self.name(), step_num=0
                        ),
                        sender=self.id(),
                        session_id=message.context.session_id,
                        headers={"context": message.context},
                    )
                )
            await self.async_pre_run(message)
            result = await self.async_policy(observation, message=message, **kwargs)
            final_result = await self.async_post_run(result, observation, message)
            if message.context and message.context.has_pending_background_tasks(self.id(), message.context.task_id):
                self._finished = False
            return final_result
        except Exception as e:
            from aworld.core.context.amni import AmniContext
            duration = None
            if isinstance(message.context, AmniContext):
                agent_start_times = message.context.get("agent_start_times") or {}
                if isinstance(agent_start_times, dict):
                    start_time = agent_start_times.get(self.id())
                    if isinstance(start_time, (int, float)):
                        duration = round(time.time() - start_time, 2)
            if duration is None:
                duration = round(time.time() - getattr(message.context, "_start", time.time()), 2)
            digest_logger.info(f"agent_run|{self.id()}|{getattr(message.context, 'user', 'default')}|{message.context.session_id}|{message.context.task_id}|{duration}|failed")
            raise e

    def policy(
            self, observation: INPUT, info: Dict[str, Any] = None, **kwargs
    ) -> OUTPUT:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.
        """
        return sync_exec(self.async_policy, observation, info=info, **kwargs)

    @abc.abstractmethod
    async def async_policy(
            self, observation: INPUT, info: Dict[str, Any] = None, **kwargs
    ) -> OUTPUT:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.
        """

    def reset(self, options: Dict[str, Any] = None):
        """Clean agent instance state and reset."""
        if options is None:
            options = {}
        self.tool_names = options.get("tool_names", self.tool_names)
        self.handoffs = options.get("agent_names", self.handoffs)
        self.mcp_servers = options.get("mcp_servers", self.mcp_servers)
        self.tools = []
        self.tool_mapping = {}
        self.trajectory = []
        self._finished = True

    async def async_reset(self, options: Dict[str, Any] = None):
        """Clean agent instance state and reset."""
        self.reset(options)

    @property
    def finished(self) -> bool:
        """Agent finished the thing, default is True."""
        return self._finished

    def pre_run(self):
        pass

    def post_run(
            self, policy_result: OUTPUT, input: INPUT, message: Message = None
    ) -> Message:
        return sync_exec(self.async_post_run, policy_result, input, message)

    async def async_pre_run(self, message: Message):
        from aworld.core.context.amni import AmniContext
        if isinstance(message.context, AmniContext):
            message.context.put("start", self.id())
            agent_start_times = message.context.get("agent_start_times") or {}
            if not isinstance(agent_start_times, dict):
                agent_start_times = {}
            if not agent_start_times.get(self.id()):
                agent_start_times[self.id()] = time.time()
                message.context.put("agent_start_times", agent_start_times)

    async def async_post_run(
            self, policy_result: OUTPUT, input: INPUT, message: Message = None
    ) -> Message:
        if isinstance(policy_result, list):
            for action in policy_result:
                # ActionModel agent_name
                if hasattr(action, "agent_name") and not getattr(action, "agent_name", None):
                    action.agent_name = self.id()
        if self._finished:
            from aworld.core.context.amni import AmniContext
            duration = None
            if isinstance(message.context, AmniContext):
                agent_start_times = message.context.get("agent_start_times") or {}
                if isinstance(agent_start_times, dict):
                    start_time = agent_start_times.get(self.id())
                    if isinstance(start_time, (int, float)):
                        duration = round(time.time() - start_time, 2)
            if duration is None:
                duration = round(time.time() - getattr(message.context, "_start", time.time()), 2)
            digest_logger.info(f"agent_run|{self.id()}|{getattr(message.context, 'user', 'default')}|{message.context.session_id}|{message.context.task_id}|{duration}|success")
        return AgentMessage(payload=policy_result, sender=self.id(), headers=message.headers)

    def sync_should_terminate_loop(self, message: Message) -> bool:
        return sync_exec(self.should_terminate_loop, message)

    async def should_terminate_loop(self, message: Message) -> bool:
        return False

    def postprocess_terminate_loop(self, message: Message):
        self.loop_step = 0

    def _update_headers(self, input_message: Message) -> Dict[str, Any]:
        headers = input_message.headers.copy()
        headers['context'] = input_message.context
        headers['level'] = headers.get('level', 0) + 1
        if input_message.group_id:
            headers['parent_group_id'] = input_message.group_id
        return headers

    # ===== Peer-to-peer Communication API (Hybrid Swarm) =====
    # Design Principle: All peer communication is NON-BLOCKING
    # - Orchestrator controls execution flow (serial/parallel)
    # - Executors share information freely without waiting
    # - No blocking ask/request patterns

    async def share_with_peer(
        self,
        peer_name: str,
        information: Any,
        info_type: str = "general"
    ) -> bool:
        """Share information with a peer agent (non-blocking, fire-and-forget).

        This method enables one-way information sharing between executor agents.
        The sender does NOT wait for acknowledgment or response.

        Use Cases:
        - Share intermediate results: "I finished filtering, here's the data format"
        - Notify about issues: "Found validation errors in these records"
        - Send alerts: "Detected anomaly, adjust your strategy"

        Args:
            peer_name: Name of the peer agent to share with
            information: The information to share (any JSON-serializable data)
            info_type: Type of information for categorization
                      (e.g., "result", "alert", "feedback", "status")

        Returns:
            bool: True if sent successfully (does not guarantee receipt)

        Raises:
            RuntimeError: If not in a Hybrid swarm
            ValueError: If peer not found

        Example:
            # FilterAgent shares filtered data format with TransformAgent
            >>> await self.share_with_peer(
            ...     peer_name="TransformAgent",
            ...     information={
            ...         "format": "standard_email",
            ...         "sample": "user@example.com",
            ...         "count": 42
            ...     },
            ...     info_type="data_format"
            ... )
            # Returns immediately, FilterAgent continues execution
        """
        if not self._is_peer_enabled:
            raise RuntimeError(
                f"Agent {self.name()} is not in a Hybrid swarm. "
                "Peer communication is only available with build_type=HYBRID."
            )

        peer_agent = self._find_peer_by_name(peer_name)
        if not peer_agent:
            available_peers = [p.name() for p in self._peer_agents.values()]
            raise ValueError(
                f"Peer '{peer_name}' not found. Available peers: {available_peers}"
            )

        if not self._current_context:
            raise RuntimeError("No context available. Peer communication requires an active context.")

        # Prepare message payload
        share_data = {
            "type": "share",
            "info_type": info_type,
            "information": information,
            "sender_name": self.name(),
            "timestamp": time.time()
        }

        # Send via EventManager (non-blocking)
        await self._current_context.event_manager.emit(
            data=share_data,
            sender=self.id(),
            receiver=peer_agent.id(),
            topic=TopicType.PEER_BROADCAST,
            session_id=self._current_context.session_id,
            event_type=Constants.AGENT
        )

        logger.info(
            f"[PeerComm] {self.name()} → {peer_name}: shared {info_type}"
        )
        return True

    async def broadcast_to_all_peers(
        self,
        information: Any,
        info_type: str = "broadcast"
    ) -> int:
        """Broadcast information to all peer agents (non-blocking).

        This method sends information to ALL peers in the Hybrid swarm.
        The sender does NOT wait for any acknowledgments.

        Use Cases:
        - System-wide alerts: "Critical error detected"
        - Status updates: "My stage is complete, pass_rate=85%"
        - Shared insights: "Detected pattern affecting all agents"

        Args:
            information: The information to broadcast (any JSON-serializable data)
            info_type: Type of broadcast for categorization
                      (e.g., "alert", "status", "completion", "warning")

        Returns:
            int: Number of peers the broadcast was sent to

        Raises:
            RuntimeError: If not in a Hybrid swarm

        Example:
            # ValidateAgent broadcasts validation complete status
            >>> peer_count = await self.broadcast_to_all_peers(
            ...     information={
            ...         "status": "validation_complete",
            ...         "pass_rate": 0.85,
            ...         "issues_found": 3
            ...     },
            ...     info_type="completion"
            ... )
            >>> logger.info(f"Notified {peer_count} peers")
            # Returns immediately, ValidateAgent continues
        """
        if not self._is_peer_enabled:
            raise RuntimeError(
                f"Agent {self.name()} is not in a Hybrid swarm. "
                "Peer communication is only available with build_type=HYBRID."
            )

        if not self._current_context:
            raise RuntimeError("No context available.")

        # Prepare broadcast payload
        broadcast_data = {
            "type": "broadcast",
            "info_type": info_type,
            "information": information,
            "sender_name": self.name(),
            "timestamp": time.time()
        }

        # Send to all peers (non-blocking)
        sent_count = 0
        for peer_id, peer_agent in self._peer_agents.items():
            await self._current_context.event_manager.emit(
                data=broadcast_data,
                sender=self.id(),
                receiver=peer_id,
                topic=TopicType.PEER_BROADCAST,
                session_id=self._current_context.session_id,
                event_type=Constants.AGENT
            )
            sent_count += 1

        logger.info(
            f"[PeerComm] {self.name()} → ALL: broadcast {info_type} to {sent_count} peers"
        )
        return sent_count

    def _find_peer_by_name(self, name: str) -> Optional['BaseAgent']:
        """Find peer agent by name.

        Args:
            name: Peer agent name

        Returns:
            BaseAgent: Peer agent instance or None if not found
        """
        for peer in self._peer_agents.values():
            if peer.name() == name:
                return peer
        return None


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
            logger.warning(f"{name} not find conf in agent factory")
            conf = dict()
        elif isinstance(conf, BaseModel):
            conf = conf.model_dump()

        user_conf = kwargs.pop("conf", None)
        if user_conf:
            if isinstance(user_conf, BaseModel):
                conf.update(user_conf.model_dump())
            elif isinstance(user_conf, dict):
                conf.update(user_conf)
            else:
                logger.warning(f"Unknown conf type: {type(user_conf)}, ignored!")

        conf["name"] = name
        conf = ConfigDict(conf)
        if name in self._cls:
            agent = self._cls[name](conf=conf, **kwargs)
            self._agent_instance[name] = agent
        else:
            raise ValueError(f"Can not find {name} agent!")
        return agent

    def desc(self, name: str) -> str:
        if self._agent_instance.get(name, None) and self._agent_instance[name].desc():
            return self._agent_instance[name].desc()
        return self._desc.get(name, "")

    def agent_instance(self, name: str) -> BaseAgent | None:
        if self._agent_instance.get(name, None):
            return self._agent_instance[name]
        return None

    def register(self, name: str, desc: str = '', conf_file_name: str = None, **kwargs):
        """Register a tool to tool factory.

        Args:
            name: Agent name
            desc: Agent description
            conf_file_name: Default agent config
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

    def unregister(self, name: str):
        super().unregister(name)
        if name in self._agent_instance:
            del self._agent_conf[name]
            del self._agent_instance[name]


AgentFactory = AgentManager("agent_type")
