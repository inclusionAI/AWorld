import uuid
import abc

from typing import Dict, Any, List, Union
from a2a.types import (
    AgentCard,
)
from aworld.core.agent.base import BaseAgent
from aworld.core.common import ActionResult, Observation, ActionModel, Config, TaskItem
from aworld.core.event.base import Message
from aworld.core.task import Task, TaskResponse
from aworld.logs.util import logger
from aworld.config.conf import ConfigDict
from aworld.utils.common import sync_exec, convert_to_snake
from aworld.experimental.a2a.client_proxy import A2AClientProxy
from aworld.experimental.a2a.config import ClientConfig


class RemoteAgent(BaseAgent):

    def __init__(self, name: str,
                 conf: ConfigDict | None = None,
                 desc: str = None,
                 agent_id: str = None,
                 *,
                 task: Any = None,
                 **kwargs,):
        self.name = name
        self.conf = conf
        self._name = name if name else convert_to_snake(self.__class__.__name__)
        self._desc = desc if desc else self._name
        self._id = (
            agent_id if agent_id else f"{self._name}---uuid{uuid.uuid1().hex[0:6]}uuid"
        )
        self.task: Any = task
        self.context = kwargs.get("context", None)

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None, **kwargs) -> List[
            ActionModel]:
        return sync_exec(self.async_policy, observation, info, message, **kwargs)

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None, **kwargs) -> List[
            ActionModel]:

        logger.info(f"Agent{type(self)}#{self.id()}: async_policy start")
        # temporary state context
        self.context = message.context
        remote_task = Task(input=observation.content)

        response_task = await self.call_agent(remote_task, info, **kwargs)
        result = ActionModel(agent_name=self.id(), policy_info=response_task.answer)
        return [result]

    @abc.abstractmethod
    async def call_agent(self, request_task: Task, info: Dict[str, Any] = {}, **kwargs) -> TaskResponse:
        """Call the remote agent to execute the task.

        Args:
            request_task: The task to be executed by the remote agent.
        """
        raise NotImplementedError


class A2ARemoteAgent(RemoteAgent):

    def __init__(self, name: str,
                 agent_card: Union[AgentCard, str],
                 conf: ConfigDict | None = None,
                 desc: str = None,
                 agent_id: str = None,
                 *,
                 task: Any = None,
                 **kwargs,):
        super().__init__(name, conf, desc, agent_id, task=task, **kwargs)
        self.streaming = kwargs.get("stream",
                                    False) or self.conf.llm_config.llm_stream_call if self.conf.llm_config else False
        self.client_proxy = A2AClientProxy(agent_card, config=ClientConfig(streaming=self.streaming))

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
