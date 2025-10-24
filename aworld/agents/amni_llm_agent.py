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

    def init_working_state(self, agent_state: ApplicationAgentState) -> AgentWorkingState:
        """
        Custom working state initialization method

        Obtains generic type parameters through reflection and creates an instance

        Args:
            agent_state: Application agent state object
        """
        return AgentWorkingState()

    def get_working_state(self, context: ApplicationContext) -> S:
        """
        Get the agent's working state

        Retrieves the current agent's working state object from the application context

        Args:
            context: Application context object

        Returns:
            The agent's working state object, of type S (must be a subclass of AgentWorkingState)
        """
        # Get the current agent's state from the context
        agent_state = context.get_agent_state(self.id())
        if agent_state is None:
            agent_state = ApplicationAgentState()
            context.set_agent_state(self.id(), agent_state)
        # Return the working state portion
        return agent_state.working_state

    async def custom_system_prompt(self, context: Context, content: str, tool_list: List[str] = None):
        if isinstance(self.system_prompt_template, ContextPromptTemplate):
            return await self.system_prompt_template.async_format(context=context, task=content, tool_list=tool_list,
                                                                  # used to get agent context
                                                                  agent_id=self.id())
        else:
            return self.system_prompt_template.format(context=context, task=content, tool_list=tool_list)

    def _log_messages(self, messages: List[Dict[str, Any]], context: ApplicationContext = None, **kwargs) -> None:
        """Log the sequence of messages for debugging purposes"""
        PromptLogger.log_agent_call_llm_messages(self, messages=messages, context=context)

    async def _add_llm_response_to_memory(self, llm_response, context: Context, history_messages: list, **kwargs):
        """Add LLM response to memory"""
        session_id = context.get_task().session_id
        user_id = context.get_task().user_id
        task_id = context.get_task().id

        ai_message = MemoryAIMessage(
            content=llm_response.content,
            tool_calls=llm_response.tool_calls,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name()
            )
        )
        await self.memory.add(ai_message, agent_memory_config=self.memory_config)

        history_messages.append(ai_message.to_openai_message())
        self._log_messages(history_messages, context=context)

    async def _add_tool_result_to_memory(self, tool_call_id: str, tool_result: ActionResult,
                                         context: ApplicationContext):
        await context.pub_and_wait_tool_result_event(tool_result,
                                                     tool_call_id,
                                                     agent_id=self.id(),
                                                     agent_name=self.name(),
                                                     namespace=self.name())

    # System prompt append mode
    async def _add_system_message_to_memory(self, context: ApplicationContext, content: str):
        if not self.system_prompt:
            return

        await context.pub_and_wait_system_prompt_event(
            system_prompt=self.system_prompt,
            user_query=context.task_input,
            agent_id=self.id(),
            agent_name=self.name(),
            namespace=self.name())
        logger.info(f"_add_system_message_to_memory finish {self.id()}")
