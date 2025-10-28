from typing import TypeVar, Generic, Any, Dict, List, Union, Callable

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ConfigDict
from aworld.core.common import Observation, ActionModel, ActionResult
from aworld.core.context.amni import AgentWorkingState, ApplicationAgentState, ApplicationContext
from aworld.core.context.amni.prompt.prompt_ext import ContextPromptTemplate
from aworld.core.context.amni.utils.context_log import PromptLogger
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.memory import AgentMemoryConfig
from aworld.logs.util import logger
from aworld.memory.models import MemoryAIMessage, MessageMetadata
from aworld.output import Output

# Define generic type variable, constrained to subclasses of AgentWorkingState
S = TypeVar('S', bound=AgentWorkingState)


class ApplicationAgent(Agent, Generic[S]):
    """
    Base class for application agents, supporting generic working state types

    This agent can work with different types of working states that inherit from AgentWorkingState.
    Provides basic functionality for state management, context operations, etc.
    """

    def __init__(self,
                 conf: Union[Dict[str, Any], ConfigDict, AgentConfig],
                 name: str,
                 resp_parse_func: Callable[..., Any] = None,
                 agent_memory_config: AgentMemoryConfig = None, **kwargs):
        super().__init__(conf=conf, name=name, resp_parse_func=resp_parse_func, agent_memory_config=agent_memory_config,
                         **kwargs)
        self.system_prompt_template = ContextPromptTemplate.from_template(self.system_prompt)

    def get_task_context(self, message: Message) -> ApplicationContext:
        return message.context

    async def send_outputs(self, message: Message, list_data: list[str]):
        for data in list_data:
            await self.send_output(message=message, data=data)

    async def send_output(self, message: Message, data: str):
        await message.context.outputs.add_output(Output(task_id=message.task_id, data=data))

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        return await super().async_policy(observation, info, message, **kwargs)

    def _log_messages(self, messages: List[Dict[str, Any]], context: ApplicationContext = None, **kwargs) -> None:
        """Log the sequence of messages for debugging purposes"""
        PromptLogger.log_agent_call_llm_messages(self, messages=messages, context=context)
