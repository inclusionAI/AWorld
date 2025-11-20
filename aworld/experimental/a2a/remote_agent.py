import abc

from typing import Dict, Any, List, Union, Optional
from a2a.types import (
    AgentCard,
)

from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import BaseAgent
from aworld.core.common import ActionResult, Observation, ActionModel, Config, TaskItem
from aworld.core.event.base import Message, AgentMessage, Constants
from aworld.core.task import Task, TaskResponse
from aworld.logs.util import logger
from aworld.config.conf import ConfigDict, AgentConfig
from aworld.utils.common import sync_exec
from aworld.experimental.a2a.client_proxy import A2AClientProxy
from aworld.experimental.a2a.config import ClientConfig


class RemoteAgent(BaseAgent):

    def __init__(self, name: str,
                 conf: ConfigDict | None = None,
                 agent_id: str = None,
                 *,
                 task: Any = None,
                 tool_names: List[str] = None,
                 event_driven: bool = True,
                 **kwargs, ):
        self._init_id_name(name, agent_id)
        self.conf = conf
        if not self.conf:
            self.conf = ConfigDict(AgentConfig().model_dump())
        self.task: Any = task
        self.context = kwargs.get("context", None)
        self.tool_names: List[str] = tool_names or []
        self.event_driven: bool = event_driven
        self._desc: Optional[str] = None

    def desc(self) -> str:
        if not self._desc:
            self._desc = sync_exec(self.remote_agent_desc)
        return self._desc

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None, **kwargs) -> List[
        ActionModel]:
        return sync_exec(self.async_policy, observation, info, message, **kwargs)

    async def async_policy(self,
                           observation: Observation,
                           info: Dict[str, Any] = {},
                           message: Message = None,
                           **kwargs) -> List[
        ActionModel]:
        logger.info(f"Agent{type(self)}#{self.id()}: async_policy start")
        # temporary state context
        self.context = message.context
        remote_task = Task(input=observation.content)

        response_task = await self.call_agent(remote_task, info, **kwargs)
        logger.info(f"Agent{type(self)}#{self.id()}: async_policy end, response_task: {response_task}")
        result = ActionModel(agent_name=self.id(), policy_info=response_task.answer)
        self._finished = True
        await Agent.send_agent_response_output(self, response_task, message.context)
        return [result]

    async def async_post_run(self, policy_result: List[ActionModel], policy_input: Observation,
                             message: Message = None) -> Message:
        caller = policy_input.from_agent_name if policy_input.from_agent_name else policy_input.observer
        return AgentMessage(payload=policy_result,
                            caller=caller,
                            sender=self.id(),
                            session_id=message.context.session_id if message.context else "",
                            headers=self._update_headers(message))

    @abc.abstractmethod
    async def call_agent(self, request_task: Task, info: Dict[str, Any] = {}, **kwargs) -> TaskResponse:
        """Call the remote agent to execute the task.

        Args:
            request_task: The task to be executed by the remote agent.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def remote_agent_desc(self):
        """Get the description of the remote agent.

        Returns:
            The description of the remote agent.
        """
        raise NotImplementedError


class A2ARemoteAgent(RemoteAgent):

    def __init__(self, name: str,
                 agent_card: Union[AgentCard, str],
                 conf: ConfigDict | None = None,
                 agent_id: str = None,
                 *,
                 task: Any = None,
                 **kwargs, ):
        super().__init__(name, conf, agent_id, task=task, **kwargs)
        self.streaming = kwargs.get("stream",
                                    False) or self.conf.llm_config.llm_stream_call if self.conf.llm_config else False
        self.client_proxy = A2AClientProxy(agent_card, config=ClientConfig(streaming=self.streaming))

    async def remote_agent_desc(self):
        agent_card = await self.client_proxy.get_or_init_agent_card()
        if not agent_card:
            logger.error(f"get agent card failed, agent_id: {self.id()}")
            raise ValueError(f"get agent card failed, agent_id: {self.id()}")
        return agent_card.description

    async def call_agent(self, request_task: Task, info: Dict[str, Any] = {}, **kwargs) -> TaskResponse:
        """Call the remote agent to execute the task.

        Args:
            request_task: The task to be executed by the remote agent.
        """
        if self.streaming:
            async for event in self.client_proxy.send_task_stream(request_task, info, **kwargs):
                # TODO send core message to eventbus
                if isinstance(event, TaskResponse):
                    return event
        else:
            return await self.client_proxy.send_task(request_task, info, **kwargs)
