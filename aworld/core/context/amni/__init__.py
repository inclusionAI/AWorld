import abc
import asyncio
import copy
import os
import time
import traceback
import uuid
from typing import Optional, Any, Literal, List, Dict

from aworld import trace
from aworld.config import AgentConfig, ContextRuleConfig
from aworld.config.conf import AgentMemoryConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.context.base import Context
from aworld.memory.main import Memory
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryMessage, UserProfile, Fact
from aworld.output import Artifact, WorkSpace
from aworld.output.artifact import ArtifactAttachment
from .config import AgentContextConfig, AmniContextConfig, AmniConfigFactory
from .logger import logger, amni_prompt_logger
from .contexts import ContextManager
from .retrieval.embeddings import EmbeddingsMetadata, SearchResults
from .retrieval.chunker import Chunk
from .event.base import ContextEvent, SystemPromptEvent
from .event.event_bus import EventBus, EventType
from .event.event_bus import get_global_event_bus, start_global_event_bus, stop_global_event_bus, \
    is_global_event_bus_started
from .retrieval.artifacts.file import DirArtifact
from .prompt.prompts import AMNI_CONTEXT_PROMPT
from .retrieval.artifacts import SearchArtifact
from .state import ApplicationTaskContextState, ApplicationAgentState, TaskOutput, TaskWorkingState
from .state.agent_state import AgentWorkingState
from .state.common import WorkingState, TaskInput
from .state.task_state import SubTask
from .worksapces import ApplicationWorkspace

DEFAULT_VALUE = None

class AmniContext(Context):
    """
    AmniContext - Ant Mind Neuro-Intelligence Context Engine

    * A = Ant - Represents the parent company Ant Group
    * M = Mind - Positioned as a "digital brain" with memory, understanding, and thinking capabilities
    * N = Neuro - Represents neural networks, the foundation for AI and deep learning
    * I = Intelligence - The ultimate value output goal
    
    Core Features:
    - Context Write: Manages application-level data including task state, workspace, and agent state

    - Context Read: Read short-term, long-term from memory; files from workspace; task start from checkpoint

    - Context Pruning: Context Pruning is a technique that reduces the size of the context by removing unnecessary information

    - Context Isolation: Each context is isolated from other contexts, and reference parent context. Support Multi-Agent Context Isolation, every agent has its own context in task, context use taskstate shared by all agents. Provides logical schema field access with upward traversal to parent contexts

    - Context Offload: Offload large context to workspace, and load context from workspace

    - Context Consolidation: Consolidate context generate long-term memory, and can be referenced by other contexts cross conversation

    - Context Prompt: Build Prompt From Context Use Context Prompt Template, supports referencing context information in prompts using {{xxx}} syntax

    ## Usage Example
    
    Here's how to use the context prompt template system:
    
    ```python
    # 1. Define task split prompt template
    split_task_prompt = (
        "Split the task {{task_input}} into 5 subtasks,\n"
        "---------------------------------------------------------------\n"
        "{{ai_context}}\n"
        "---------------------------------------------------------------"
    )

    # 2. Create context prompt template object
    split_task_prompt_template = ContextPromptTemplate(
        template=split_task_prompt
    )

    # 3. Async format prompt, fill context variables
    prompt = await split_task_prompt_template.async_format(
        context=context,
    )
    ```

    The context system organizes information into three main memory categories:

    - **WORKING MEMORY**: Working memory containing current task-related information
    - **SHORT MEMORY**: Short-term memory containing conversation history and runtime data
    - **LONG MEMORY**: Long-term memory containing facts and user profiles

    
    ## Basic Field References

    - {{session_id}} - Session ID
    - {{user_id}} - User ID
    - {{task_input}} - Task input content
    - {{task_output}} - Task execution result
    - {{task_status}} - Task status ['INIT', 'PROCESSING', 'SUCCESS', 'FAILED']
    - {{task_history}} - Task execution history (structured format)
    - {{plan_task_list}} - Planned task list
    - {{model_config}} - Model configuration

    ## Context Hierarchy References

    - {{current.{KEY}}} - Fields within current runtime Task Context
    - {{parent.{KEY}}} - Fields within parent Task Context
    - {{root.{KEY}}} - Fields within root Task Context

    ## Memory and Knowledge References

    - {{history}} - Conversation history
    - {{summaries}} - Conversation summaries
    - {{facts}} - Facts from task execution process
    - {{user_profiles}} - User profiles
    - {{knowledge}} - All referenceable file indices
    - {{knowledge/{ARTIFACT_ID}}} - Specific artifact content
    - {{knowledge/{ARTIFACT_ID}/summary}} - Specific artifact summary

    ## Runtime KV Storage

    - {{foo}} - Runtime data set via task_context.put("foo", "bar")

    ## Time Variables

    - {{current_time}} - Current time in HH:MM:SS format
    - {{current_date}} - Current date in YYYY-MM-DD format
    - {{current_datetime}} - Current datetime in YYYY-MM-DD HH:MM:SS format
    - {{current_timestamp}} - Current Unix timestamp
    - {{current_weekday}} - Current weekday name
    - {{current_month}} - Current month name
    - {{current_year}} - Current year

    ## Logical Schema Mapping

    The system supports hierarchical context traversal with these prefixes:

    - `current.{KEY}` - Access fields in current runtime Task Context
    - `parent.{KEY}` - Access fields in parent Task Context  
    - `root.{KEY}` - Access fields in root Task Context

    By default, the system performs upward traversal queries, but this can be limited
    using the specific prefixes above.

    """

    def __init__(self, config: Optional['AmniContextConfig'] = None, **kwargs):
        super().__init__(**kwargs)

    @trace.func_span(span_name="ApplicationContext#build_sub_context")
    async def build_sub_context(self, sub_task_content: Any, sub_task_id: str = None, **kwargs):
        pass

    @trace.func_span(span_name="ApplicationContext#merge_sub_context")
    def merge_sub_context(self, sub_task_context: 'AmniContext', **kwargs):
        pass

    @trace.func_span(span_name="ApplicationContext#offload_by_workspace")
    async def offload_by_workspace(self, artifacts: list[Artifact], namespace="default"):
        """
        Context Offloading - Store information outside the LLM's context via external storage

        This function implements the core concept of Context Offloading: storing information
        outside the LLM's context, use workspace file system that store and manage the data.
        The process includes:

        1. Adding this batch artifacts to the context knowledge base for externalized storage
        2. Building knowledge_index to establish information indexing structure
        3. Retrieving relevant knowledge chunks by cur batch for categorized management
        4. Returning offload context to provide LLM access to externally stored information

        This approach effectively reduces LLM context size while maintaining access
        to important information through external storage mechanisms.

        Args:
            artifacts (list[Artifact]): Artifacts to be offloaded to external storage
            namespace (str, optional): Namespace for isolating information from different sources. Defaults to "default".

        Returns:
            str: Offload context containing relevant information retrieved from external storage
        """
        pass

    @trace.func_span(span_name="ApplicationContext#load_context_by_workspace")
    async def load_context_by_workspace(self, search_filter: dict = None,
                                        namespace="default",
                                        top_k: int = 30,
                                        load_content: bool = True,
                                        load_index: bool = True,
                                        use_search: bool = True):
        pass


    async def snapshot(self):
        await get_context_manager().save_context(self)

    @trace.func_span(span_name="ApplicationContext#consolidation")
    async def consolidation(self):
        """
        Context consolidation: Extract and generate long-term memory from context,enabling the Agent to continuously learn user preferences and behavior patterns,thereby enhancing its understanding and overall capabilities 🚀

        - User Profile: User Profile is information related to the user extracted from the context, which helps the Agent better understand the user and thus assist the user in completing tasks.
        - Agent Experience: Agent Experience is information related to the Agent task execution extracted from the context, which helps the Agent decompose tasks and enables experience reuse and error correction in tool usage.

        Returns:

        """
        pass


    ####################### Context read #######################

    @abc.abstractmethod
    def get(self, key: str, namespace: str = "default") -> Any:
        """
        Retrieve context information from the state.

        First checks agent-specific working state if agent_id is provided,
        otherwise falls back to task-level custom information.

        Args:
            key (str): The key to retrieve
            namespace (str, optional): Agent ID for agent-specific retrieval.
                                    Defaults to None for task-level retrieval.

        Returns:
            Any: The stored value, or None if not found
        """
        pass

    @abc.abstractmethod
    def get_memory_messages(self, last_n=100, namespace: str = "default") -> list[MemoryMessage]:
        """
        Retrieve memory messages from the working state.

        Args:
            last_n: latest count
            namespace (str, optional): Namespace to retrieve messages from. Defaults to "default".

        Returns:
            list[MemoryMessage]: List of memory messages stored in the namespace
        """
        pass

    @abc.abstractmethod
    async def get_knowledge_by_id(self, knowledge_id: str, namespace: str = "default"):
        """
        get special artifact from working state
        Args:
            knowledge_id:
            namespace:

        Returns:

        """
        pass

    @abc.abstractmethod
    async def get_knowledge_chunk(self, knowledge_id: str, chunk_index: int) -> Optional[Chunk]:
        pass

    # @abc.abstractmethod
    async def get_sensitive_data(self, key) -> Optional[str | dict[str, str]]:
        pass

    # @abc.abstractmethod
    async def set_sensitive_data(self, key, value: [str | dict[str, str]]):
        pass

    def get_config(self) -> AmniContextConfig:
        pass

    ####################### Context Write #######################

    @abc.abstractmethod
    def put(self, key: str, value: Any, namespace: str = "default") -> None:
        """
        Add context information to the state.

        Stores key-value pairs in both agent-specific working state and task-level custom information.
        If namespace is provided and agent state exists, the value is stored in agent's working state.
        The value is always stored in task-level custom information for global access.

        Args:
            key (str): The key to store the value under
            value (Any): The value to store
            namespace (str, optional): Namespace for agent-specific storage.
                                    Defaults to "default". Use agent_id for private agent storage.
        """
        pass


    @abc.abstractmethod
    async def add_knowledge_list(self, knowledge_list: List[Artifact], namespace: str = "default",
                                 index=True) -> None:
        pass

    @abc.abstractmethod
    async def add_knowledge(self, knowledge: Artifact, namespace: str = "default", index=True) -> None:
        """
        Add a single knowledge artifact to the working state and workspace.

        Saves the artifact to the working state and optionally to the workspace
        if workspace is available.

        Args:
            knowledge (Artifact): The artifact to add as knowledge
            namespace (str, optional): Namespace for storage. Defaults to "default".
        """
        pass

    # @abc.abstractmethod
    async def delete_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> None:
        """
         from context delete knowledge

        Args:
            knowledge_id: knowledge_id
            namespace: namespace

        Returns:

        """
        pass

    @abc.abstractmethod
    async def add_task_output(self, output_artifact: Artifact, namespace: str = "default", index=True) -> None:
        """
        Add a single knowledge artifact to the working state and workspace.

        Saves the artifact to the working state and optionally to the workspace
        if workspace is available.

        Args:
            artifact (Artifact): The artifact to add as knowledge
            namespace (str, optional): Namespace for storage. Defaults to "default".
        """
        pass

    @abc.abstractmethod
    def add_history_message(self, memory_message: MemoryMessage, namespace: str = "default") -> None:
        """
        Add a memory message to the working state.

        Stores a memory message in the specified namespace's working state
        for later retrieval and processing.

        Args:
            memory_message (MemoryMessage): The memory message to add
            namespace (str, optional): Namespace for storage. Defaults to "default".
        """
        pass

    @abc.abstractmethod
    def add_fact(self, fact: Fact, namespace: str = "default", **kwargs):
        pass





# Global context manager instance
CONTEXT_MANAGER: Optional[ContextManager] = None

def get_context_manager() -> ContextManager:
    """
    Get the global context manager instance.
    
    Creates a new ContextManager if one doesn't exist.
    
    Returns:
        ContextManager: The global context manager instance
    """
    global CONTEXT_MANAGER
    if CONTEXT_MANAGER is None:
        CONTEXT_MANAGER = ContextManager()
    return CONTEXT_MANAGER


class ApplicationContext(AmniContext):
    """
    ApplicationContext - Application-level context manager that supports referencing context information in prompts via template variables
    """
    

    def __init__(self,
                 task_state: ApplicationTaskContextState,
                 workspace: ApplicationWorkspace = None,
                 parent: "ApplicationContext" = None,
                 context_config: AmniContextConfig = None,
                 working_dir: DirArtifact = None,
                 **kwargs):
        super().__init__(**kwargs)
        self.task_state = task_state
        self._workspace = workspace
        self._parent = parent
        self._config = context_config
        self._working_dir = working_dir

    def get_config(self) -> AmniContextConfig:
        return self._config

    ####################### Context Build/Copy/Merge/Restore #######################

    @classmethod
    async def from_input(cls, task_input: TaskInput, workspace: WorkSpace = None, use_checkpoint: bool = True, context_config: AmniContextConfig = None,  **kwargs) -> "ApplicationContext":
        if not context_config:
            context_config = AmniConfigFactory.create()
        try:
            await start_global_event_bus(context_config)
        except Exception as e:
            logger.warning(f"Failed to start global event bus: {e} {traceback.format_exc()}")
        try:
            if use_checkpoint:
                # restore context from checkpoint
                checkpoint = await get_context_manager().aget_checkpoint(task_input.session_id)
                if checkpoint:
                    logger.info(
                        f"[CONTEXT RESTORE]Restore context from checkpoint: {task_input.session_id} {await get_context_manager().aget_checkpoint(task_input.session_id)}")
                    # 引用上一轮未完成任务的context，可以取到历史的上下文
                    context: "ApplicationContext" = await get_context_manager().build_context_from_checkpoint(task_input.session_id)
                    # 将上一轮的输入作为单独的字段
                    context.put("origin_task_input", context.task_input)
                    context.put("origin_task_output", context.task_output)
                    # 更新这一轮的输入
                    context.task_state.set_task_input(task_input)
                    context.task_state.set_task_output(TaskOutput())
                    history_messages = await get_context_manager().get_task_histories(task_input)
                    context.task_state.working_state.history_messages = history_messages
                    logger.info(f"[CONTEXT RESTORE]history_messages: len = {len(history_messages) if history_messages else 0}")

                    user_profiles = await get_context_manager().get_user_profiles(task_input)
                    context.task_state.working_state.user_profiles = user_profiles
                    logger.info(f"[CONTEXT RESTORE]user_profiles: len = {len(context.task_state.working_state.user_profiles) if context.task_state.working_state.user_profiles else 0}")

                    context._workspace = workspace

                    # clear checkpoint, avoid duplicate restore context when creating sub-task
                    get_context_manager().delete_checkpoint(task_input.session_id)
                    return context
                else:
                    logger.info(f"[CONTEXT BUILD]Build new context {task_input.session_id}:{task_input.task_id}")
                    task_state = await cls._build_new_task_state(task_input)
                    return ApplicationContext(task_state, workspace, context_config)
            else:
                task_state = await cls._build_new_task_state(task_input)
                context = ApplicationContext(task_state, workspace = workspace, context_config = context_config)
                # 将当前轮的输入作为单独的字段
                context.put("origin_task_input", context.task_input)
                context.put("origin_task_output", context.task_output)
                return context
        except Exception as e:
            # Handle specific exceptions or re-raise with context
            raise RuntimeError(f"Failed to create ApplicationContext: {e}, trace is {traceback.format_exc()}")

    @staticmethod
    async def _build_new_task_state(task_input: TaskInput) -> ApplicationTaskContextState:
        """
        Build a completely new task state for a fresh context.
        
        This method creates a brand new ApplicationTaskContextState by:
        1. Retrieving the current user's historical task data assembly context
        2. Building a new working state with fresh memory and user profiles
        3. Initializing a clean task output structure
        
        Args:
            task_input (TaskInput): The input parameters for the new task

        Returns:
            ApplicationTaskContextState: A newly constructed task state with user history context
        """
        if not task_input:
            raise ValueError("task_input cannot be None")
        
        history_messages = await get_context_manager().get_task_histories(task_input)
        logger.info(f"[CONTEXT BUILD]history_messages: len = {len(history_messages) if history_messages else 0}")
        
        user_profiles = await get_context_manager().get_user_profiles(task_input)
        logger.info(f"[CONTEXT BUILD]user_profiles: len = {len(user_profiles) if user_profiles else 0}")

        # previous_round_results=await get_context_manager().get_user_similar_task(task_input)
        # logger.info(f"[CONTEXT BUILD]previous_round_results: len = {len(previous_round_results) if previous_round_results else 0}")

        task_working_state = TaskWorkingState(
            history_messages=history_messages,
            user_profiles=user_profiles,
            kv_store= {}
        )

        return ApplicationTaskContextState(
            task_input=task_input,
            working_state=task_working_state,
            previous_round_results=[],
            task_output=TaskOutput()
        )

    async def build_sub_context(self, sub_task_content: str, sub_task_id: str = None, **kwargs):
        logger.info(f"build_sub_context: {self.task_id} -> {sub_task_id}: {sub_task_content}")
        sub_task_input = self.task_state.task_input.new_subtask(sub_task_content, sub_task_id)
        agents: Dict[str,BaseAgent] = kwargs.get("agents")
        agent_list = []
        if agents:
            agent_list = [agent for agent_id, agent in agents.items()]
        return await self.build_sub_task_context(sub_task_input, agents=agent_list)

    def add_sub_tasks(self, sub_task_inputs: list[TaskInput]):
        for sub_task_input in sub_task_inputs:
            sub_task = SubTask(
                task_id=sub_task_input.task_id,
                input=sub_task_input,
                status='INIT'
            )
            self.task_state.working_state.sub_task_list.append(sub_task)


    async def build_sub_task_context(self, sub_task_input: TaskInput,
                                     sub_task_history: list[MemoryMessage] = None,
                                     workspace: WorkSpace = None,
                                     agents: list[BaseAgent] = None) -> "ApplicationContext":
        task_state = await self.build_sub_task_state(sub_task_input, sub_task_history)
        if not workspace:
            workspace = self.workspace

        sub_context = ApplicationContext(task_state, workspace, parent=self)
        # 启动子上下文的事件总线（全局事件总线已启动，这里不需要重复启动）
        # upsert sub task to task state
        self.task_state.working_state.upsert_subtask_by_input(sub_task_input)

        if agents:
            await sub_context.build_agents_state(agents)

        return sub_context

    async def build_sub_task_state(self, sub_task_input: TaskInput,
                                   sub_task_history: list[MemoryMessage] = None) -> ApplicationTaskContextState:

        return ApplicationTaskContextState(
            task_input=sub_task_input,
            parent_task=self.task_state.task_input,
            working_state=await self._build_sub_task_working_state(sub_task_input),
            task_output=TaskOutput(),
        )

    async def _build_sub_task_working_state(self, task_input: TaskInput) -> TaskWorkingState:
        parent_working_state = self.task_state.working_state
        return TaskWorkingState(
            history_messages=await get_context_manager().get_task_histories(task_input),
            user_profiles=await get_context_manager().get_user_profiles(task_input),
            kv_store=copy.deepcopy(
                parent_working_state.kv_store) if parent_working_state and parent_working_state.kv_store else {}
        )

    async def build_agents_state(self, agents: list[BaseAgent]):
        """
        Build Multi Agent's Private State

        Args:
            agents: list of agents

        Returns:

        """
        for agent in agents:
            if isinstance(agent, list):
                # 遍历 tuple 中的每个 agent
                for single_agent in agent:
                    await self.build_agent_state(single_agent)
            else:
                await self.build_agent_state(agent)

    async def build_agent_state(self, agent: BaseAgent):
        """
        Build Single Agent Private State.

        Args:
            agent: Agent

        Returns:

        """
        if not self.has_agent_state(agent.id()):
            logger.info(f"build_agent_state agent#{agent.id()}")
            application_agent_state = await self._build_agent_state(agent_id=agent.id(), agent_config=agent.conf)

            # check agent has init_working_state method, if has, call it to set working_state
            if hasattr(agent, 'init_working_state') and callable(getattr(agent, 'init_working_state')):
                custom_method = getattr(agent, 'init_working_state')
                # check if init_working_state is a coroutine function
                if asyncio.iscoroutinefunction(custom_method):
                    application_agent_state.working_state = await custom_method(application_agent_state)
                else:
                    application_agent_state.working_state = custom_method(application_agent_state)
            else:
                # if no init_working_state method, use default AgentWorkingState
                application_agent_state.working_state = AgentWorkingState()

    async def _build_agent_state(self, agent_id: str, agent_config: AgentConfig) -> ApplicationAgentState:
        agent_state = ApplicationAgentState()

        # agent config
        agent_state.agent_id = agent_id
        agent_state.agent_config = agent_config

        # restore context
        agent_state.memory_config = await get_context_manager()._get_memory_config(agent_id)
        agent_state.context_rule = ContextRuleConfig()

        self.set_agent_state(agent_id, agent_state)
        return agent_state

    def merge_sub_context(self, sub_task_context: 'ApplicationContext', **kwargs):
        logger.info(f"merge_sub_context: {sub_task_context.task_id} -> {self.task_id}")

        super().merge_sub_context(sub_task_context)

        # merge sub task kv_store
        if sub_task_context.task_state.working_state.kv_store:
            self.task_state.working_state.kv_store.update(
                sub_task_context.task_state.working_state.kv_store)

        # merge sub task status & result
        sub_task_id = sub_task_context.task_state.task_input.task_id
        # 遍历sub_task_list，找到sub_task_id一致的
        for sub_task in self.task_state.working_state.sub_task_list:
            if sub_task.task_id == sub_task_id:
                sub_task.status = sub_task_context.task_status
                sub_task.result = sub_task_context.task_output_object
                break

        # merge token
        cur_token_usage = self.token_usage
        self.add_token(sub_task_context.token_usage)
        logger.info(f"merge_sub_context tokens finished: {cur_token_usage} + {sub_task_context.token_usage} -> {self.token_usage}")

    async def update_task_after_run(self, task_response: 'TaskResponse'):
        if task_response and task_response.success:
            self.task_status = 'SUCCESS'
            self.task_output = task_response.answer
            self.task_output_object.actions_info = await self.get_actions_info()
            self.task_output_object.todo_info = await self.get_todo_info()
        else:
            self.task_status = 'FAILED'
            self.task_output = task_response.msg
            self.task_output_object.actions_info = await self.get_actions_info()
            self.task_output_object.todo_info = await self.get_todo_info()

        if self.parent:
            self.parent.merge_sub_context(self)



    #################### Agent Isolated State ###################

    def set_agent_state(self, agent_id: str, agent_state: ApplicationAgentState):
        self.task_state.set_agent_state(agent_id, agent_state)

    def get_agent_state(self, agent_id: str) -> Optional[ApplicationAgentState]:
        return self.task_state.get_agent_state(agent_id)

    def has_agent_state(self, agent_id: str):
        return self.task_state.has_agent_state(agent_id)

    ####################### Properties #######################

    @property
    def user(self):
        return self.task_state.task_input.user_id

    @property
    def user_id(self):
        return self.task_state.task_input.user_id

    @property
    def session_id(self):
        return self.task_state.task_input.session_id

    @property
    def task_id(self):
        return self.task_state.task_input.task_id

    @task_id.setter
    def task_id(self, task_id):
        if task_id is not None:
            self._task_id = task_id
            self.task_state.task_input.task_id = task_id

    @property
    def task_input(self):
        return self.task_state.task_input.task_content

    @task_input.setter
    def task_input(self, new_task_input: str):
        if self._task:
            self._task.input = new_task_input
        self.task_state.task_input.task_content = new_task_input

    @property
    def origin_user_input(self):
        return self.task_state.task_input.origin_user_input

    @origin_user_input.setter
    def origin_user_input(self, new_origin_user_input: str):
        self.task_state.task_input.origin_user_input = new_origin_user_input

    @property
    def task_output(self) -> str:
        return self.task_state.task_output.result

    @task_output.setter
    def task_output(self, result):
        self.task_state.task_output.result = result

    @property
    def task_status(self) -> Literal['INIT', 'PROCESSING', 'SUCCESS', 'FAILED']:
        return self.task_state.working_state.status

    @task_status.setter
    def task_status(self, status: Literal['INIT', 'PROCESSING', 'SUCCESS', 'FAILED']):
        self.task_state.working_state.status = status

    @property
    def task_input_object(self) -> TaskInput:
        return self.task_state.task_input

    @property
    def task_output_object(self) -> TaskOutput:
        return self.task_state.task_output

    @property
    def sub_task_list(self) -> Optional[list[SubTask]]:
        return self.task_state.working_state.sub_task_list

    @property
    def parent(self) -> Optional["ApplicationContext"]:
        if self._parent is not None:
            return self._parent
        return None

    @property
    def root(self) -> "ApplicationContext":
        """
        Get main task history from root parent context.
        
        Traverses up the parent chain until reaching the root context (_parent = None).
        
        Returns:
            list: The task history from the root context, or empty list if no root found
        """
        parent = self._parent
        while parent is not None and parent._parent is not None:
            parent = parent._parent
        
        if parent is not None:
            return parent
        return self

    @property
    def workspace(self):
        return self._workspace

    @workspace.setter
    def workspace(self, workspace):
        self._workspace = workspace

    @property
    def model_config(self):
        return self.task_state.model_config

    @property
    def history(self):
        if hasattr(self.task_state, 'working_state') and self.task_state.working_state:
            return self.task_state.working_state.history_messages
        return []

    @property
    def tree(self) -> str:
        """
        Generate a tree representation showing the current context's position in the context hierarchy.
        
        Traverses up the parent chain to build a visual tree structure that shows
        the current context's location relative to its parent contexts, including subtasks.
        
        Returns:
            str: A formatted tree string showing the context hierarchy with subtasks
        """
        # 1. 收集整个上下文层次结构
        context_path = []
        current = self
        while current is not None:
            context_path.append(current)
            current = getattr(current, '_parent', None)
        
        # 反转列表，使根上下文在前
        context_path.reverse()
        
        # 2. 获取当前任务ID
        current_task_id = getattr(self, 'task_id', None)
        
        # 3. 创建一个集合来跟踪已处理的任务ID
        processed_task_ids = set()
        
        # 4. 添加全局标记确保当前任务只显示一次
        current_task_marked = False
        
        # 5. 创建结果列表
        tree_lines = []
        
        # 6. 递归构建树
        def build_tree(context, level, prefix):
            nonlocal current_task_marked
            
            # 获取上下文标识符
            context_id = getattr(context, 'task_id', None) or getattr(context, 'session_id', 'unknown')
            task_content = getattr(context, 'task_input', '')
            
            # 构建描述
            swarm_desc = ':'.join([agent.name() for agent in context.swarm.topology])
            context_desc = f"[T]{context_id}: [R]{task_content} : [O]{context.task_input_object.origin_user_input}" if task_content else str(context_id)
            
            # 检查是否为当前上下文且尚未标记当前任务
            is_current = context is self and not current_task_marked
            
            # 添加当前上下文行，只有当上下文ID未处理过时才添加
            if context_id not in processed_task_ids:
                if is_current:
                    tree_lines.append(f"{prefix}📍 {context_desc} (current)")
                    current_task_marked = True
                else:
                    tree_lines.append(f"{prefix}├─ {context_desc}")
                
                # 标记为已处理
                processed_task_ids.add(context_id)
            
            # 获取子任务列表
            sub_tasks = []
            if hasattr(context, 'task_state') and context.task_state:
                if hasattr(context.task_state.working_state, 'sub_task_list') and context.task_state.working_state.sub_task_list:
                    sub_tasks = context.task_state.working_state.sub_task_list
            
            # 检查是否有下一级上下文
            next_context_index = level + 1
            next_context = context_path[next_context_index] if next_context_index < len(context_path) else None
            next_context_id = getattr(next_context, 'task_id', None) if next_context else None
            
            # 计算子任务的缩进
            child_prefix = prefix + "│   "
            
            # 按原始顺序处理子任务
            valid_sub_tasks = []
            next_context_sub_task_index = -1
            
            # 收集有效的子任务（未处理过的）
            for i, sub_task in enumerate(sub_tasks):
                sub_task_id = getattr(sub_task, 'task_id', None)
                if sub_task_id and sub_task_id not in processed_task_ids:
                    valid_sub_tasks.append((i, sub_task))
                    # 检查是否为下一级上下文
                    if sub_task_id == next_context_id:
                        next_context_sub_task_index = len(valid_sub_tasks) - 1
            
            # 处理有效子任务
            for i, (original_index, sub_task) in enumerate(valid_sub_tasks):
                sub_task_id = getattr(sub_task, 'task_id', None)
                
                # 获取子任务内容
                subtask_content = ""
                if hasattr(sub_task, 'input') and sub_task.input:
                    subtask_content = getattr(sub_task.input, 'task_content', str(sub_task.input))
                else:
                    subtask_content = str(sub_task)
                
                # 确定是否为最后一个子任务
                is_last = i == len(valid_sub_tasks) - 1
                
                # 选择适当的连接符
                connector = "└─" if is_last else "├─"
                
                # 是否为包含下一级上下文的子任务
                is_next_context_task = i == next_context_sub_task_index and next_context
                
                # 添加子任务行
                if sub_task_id == current_task_id and not current_task_marked:
                    tree_lines.append(f"{child_prefix}{connector} 📍{swarm_desc} {sub_task_id}: {subtask_content} (current)")
                    current_task_marked = True
                else:
                    tree_lines.append(f"{child_prefix}{connector} {swarm_desc} {sub_task_id}: {subtask_content}")
                
                # 标记为已处理
                processed_task_ids.add(sub_task_id)
                
                # 如果是包含下一级上下文的子任务，递归处理下一级上下文
                if is_next_context_task:
                    next_child_prefix = child_prefix + ("    " if is_last else "│   ")
                    build_tree(next_context, level + 1, next_child_prefix)
        
        # 从根上下文开始构建树
        if context_path:
            build_tree(context_path[0], 0, "")
        
        # 添加树标题
        tree_header = "Context Tree (from root to current):\n"
        
        return tree_header + "\n".join(tree_lines)

    @staticmethod
    async def user_similar_history(context: "ApplicationContext") -> str:
        pass
        # get_context_manager().

    @staticmethod
    async def ai_context(context: "ApplicationContext") -> str:
        """
        Asynchronously assembly the context for the AI.

        This method gathers context from various sources like knowledge base,
        memory, user profile, etc., and compiles it into a single string.

        Returns:
            str: The compiled AI context.
        """

        # retrival relevant memory
        previous_round_results = (
            f"<relevant_conversation_history>\n"
            f"{chr(10).join([str(item.to_openai_message()) for item in context.root.task_state.previous_round_results])}\n"
            f"</relevant_conversation_history>\n"
        )

        knowledge_context = await context.build_knowledge_context()

        # retrival user_profile memory

        # retrival facts memory

        # retrival agent's relation memory

        # retrival working memory


        # retrival aigc content


        return (
            f"----------------------------------"
            f"{previous_round_results}"
            f"----------------------------------"
            f"{knowledge_context}"
        )

    ####################### Context logical schema #######################

    def get_from_artifacts(self, key: str, state: WorkingState):
        if not state:
            return DEFAULT_VALUE

        # 检查 key 是否为 "ARTIFACT_ID/summary" 格式
        if key.endswith('/summary'):
            artifact_id = key[:-8]  # 移除 '/summary' 后缀
            artifact_s = state.get_knowledge(artifact_id)
            if artifact_s:
                return artifact_s
            return DEFAULT_VALUE

        artifact_s = state.get_knowledge(key)
        if artifact_s:
            return artifact_s
        return DEFAULT_VALUE

    def get_from_working_state(self, key: str, state: WorkingState):
        if not state:
            return DEFAULT_VALUE

        # short and long term memory
        if key == 'history':
            return [f"{item.to_openai_message()}\n\n" for item in state.history_messages]
        elif key == 'summaries':
            return state.summaries
        elif key == 'facts':
            return state.facts
        elif key == 'user_profiles':
            return state.user_profiles

        # kv store short term memory
        if key in state.kv_store:
            return state.kv_store[key]

        # knowledge
        if key == 'knowledge':
            return state.knowledge_index

        return self.get_from_artifacts(key, state)

    def get_from_agent_state(self, key: str, state: ApplicationAgentState):
        if not state:
            return DEFAULT_VALUE
        return self.get_from_working_state(key, state.working_state)

    def get_from_task_state(self, key: str, state: ApplicationTaskContextState):
        if not state:
            return DEFAULT_VALUE
        return self.get_from_working_state(key, state.working_state)

    def get_from_context_hierarchy(self, key: str,
                                   context: "ApplicationContext",
                                   recursive: bool = True) -> Optional[str]:
        # 情况1: current.xxx - 取当前context
        if key.startswith("current."):
            actual_field = key[8:]  # 移除 "current." 前缀
            return self.get_logical_schema_field(actual_field, context)
        # 情况2-4: parent.xxx, root.xxx, parent.parent.xxx 等 - 使用递归解析路径
        elif key.startswith(("parent.", "root.")):
            # 分割路径
            parts = key.split('.')
            current_obj = context
            # 遍历路径的每一部分
            for part in parts[:-1]:
                if not hasattr(current_obj, part):
                    return None
                current_obj = getattr(current_obj, part)
                if current_obj is None:
                    return None
            # 如果最终对象是 ApplicationContext，则从其中获取字段值
            if hasattr(current_obj, 'task_state'):
                # 这里需要从路径的最后一部分获取实际的字段名
                # 例如：parent.parent.data -> 我们需要获取 data 字段
                actual_field = parts[-1] if len(parts) > 1 else key
                return ApplicationContext.get_logical_schema_field(key=actual_field, context=current_obj)
        # 情况5: xxx - 取当前context及所有parent，遍历调用get_from_task_state，直到取到值为止
        else:
            # 首先尝试当前context
            value = self.get_from_task_state(key, context.task_state)
            if value is not None and value != DEFAULT_VALUE:
                return value
            # 是否递归查询parent task context
            if not recursive:
                return None
            # 然后递归遍历所有parent
            current_parent = getattr(context, 'parent', None)
            while current_parent:
                value = ApplicationContext.get_logical_schema_field(key=key, context=current_parent, recursive=False)
                if value is not None and value != DEFAULT_VALUE:
                    return value
                current_parent = getattr(current_parent, 'parent', None)

        return None

    @staticmethod
    def get_logical_schema_field(key: str, context: "ApplicationContext" = None, recursive: bool = True,
                  agent_id: str = None):
        if not context:
            return DEFAULT_VALUE
        try:
            # 1. 从 ApplicationContext 属性中获取
            if hasattr(context, key):
                value = getattr(context, key)
                if value is not None:
                    return str(value)

            # 2. 从agent context中获取
            agent_state = None
            if context.task_state.working_state and context.task_state.working_state.agent_states:
                agent_state = context.task_state.working_state.agent_states.get(agent_id)
            value = context.get_from_agent_state(key, agent_state)
            if value is not None:
                return value

            # 3. 查询Task Context， 处理 field_path 的5种情况
            value = context.get_from_context_hierarchy(key, context, recursive)
            if value is not None and value != DEFAULT_VALUE:
                return value

            result = str(value) if value is not None else DEFAULT_VALUE

            logger.debug(f"Field retrieval: '{key}' -> '{result}'")
            return result

        except Exception as e:
            logger.warning(f"Error getting field '{key}': {e} {traceback.format_exc()}")
            return DEFAULT_VALUE

    ####################### Context Long Term Memory Processor Event #######################

    async def pub_and_wait_event(self, event: ContextEvent):
        event_bus = await get_global_event_bus()
        await event_bus.publish_and_wait(event)

    async def pub_and_wait_system_prompt_event(self, event_type: str, system_prompt: str, user_query: str, agent_id: str,
                                               agent_name: str,  context: Context, namespace: str = "default"):
        event_bus = await get_global_event_bus()
        await event_bus.publish_and_wait(
            EventBus.create_system_prompt_event(event_type=event_type, system_prompt=system_prompt, user_query=user_query, agent_id=agent_id,
                                        agent_name=agent_name, context=context, namespace=namespace))

    async def pub_and_wait_tool_result_event(self,
                                             tool_result: Any,
                                             tool_call_id: str,
                                             agent_id: str,
                                             agent_name: str,
                                             namespace: str = "default"):
        logger.info(f"publish tool result event process start ")
        start_time = time.time()
        """发布并等待工具结果事件"""
        event_bus = await get_global_event_bus()
        await event_bus.publish_and_wait(
            EventBus.create_tool_result_event(
                tool_result=tool_result,
                context=self,
                tool_call_id=tool_call_id,
                agent_id=agent_id,
                agent_name=agent_name,
                namespace=namespace
            )
        )
        logger.info(f"publish tool result event process finished, use {time.time() - start_time:.3f} seconds")

    ####################### Context Write #######################

    async def offload_by_workspace(self, artifacts: list[Artifact], namespace="default", biz_id: str = None):
        """
        Context Offloading - Store information outside the LLM's context via external storage
        """
        if not artifacts:
            return ""

        use_index = self.need_index(artifacts[0])
        ## 1. add knowledge to workspace
        if not biz_id:
            biz_id = str(uuid.uuid4())
        for artifact in artifacts:
            artifact.metadata.update({
                "biz_id": biz_id
            })
        await self.add_knowledge_list(artifacts, namespace=namespace, build_index=use_index)
        # 增加一条策略 单页面不大于40K
        if len(artifacts) == 1 and len(artifacts[0].content) < 40_000:
            logger.info(f"directly return artifacts content: {len(artifacts[0].content)}")
            return f"{artifacts[0].content}"
        logger.info(f"add artifacts to context: {[artifact.artifact_id for artifact in artifacts]}")
        artifact_context = "This is cur action result: a list of knowledge artifacts:"
        artifact_context += "\n<knowledge_list>\n"
        search_tasks = []
        for artifact in artifacts:
            search_tasks.append(self._get_knowledge_index_context(artifact, load_chunk_content_size=5))
        search_task_results = await asyncio.gather(*search_tasks)
        artifact_context += "\n".join(search_task_results)
        artifact_context += "</knowledge_list>"
        return f"{artifact_context}"

    def need_index(self, artifact: Artifact):
        return isinstance(artifact, SearchArtifact)

    async def _get_knowledge_index_context(self,
                                           knowledge: Artifact,
                                           namespace: str = "default",
                                           load_chunk_indicis: bool = True,
                                           load_chunk_content_size: int = 5):
        knowledge_context = "<knowledge>\n"
        knowledge_context += f"<id>{knowledge.artifact_id}</id>\n"

        if knowledge.summary:
            knowledge_context += f"{knowledge.summary}\n"

        knowledge_chunk_context = ""
        if knowledge.metadata.get("chunked"):
            total_chunk = knowledge.metadata.get("chunks")
            chunk_count_desc = f"Total is {total_chunk} chunks"
            knowledge_context += f"<chunks description='{chunk_count_desc}'>\n"

            # load chunk index
            if load_chunk_indicis:
                pass

            # load head and tail chunks
            if load_chunk_content_size:
                def _format_chunk_content(_chunk: Chunk) -> str:
                    return (
                        f"  <knowledge_chunk>\n"
                        f"    <chunk_id>{_chunk.chunk_id}</chunk_id>\n"
                        f"    <chunk_index>{_chunk.chunk_metadata.chunk_index}</chunk_index>\n"
                        f"    <chunk_content>{_chunk.content}</chunk_content>\n"
                        f"  </knowledge_chunk>\n"
                    )

                head_chunks, tail_chunks = await self._workspace.get_artifact_chunks_head_and_tail(
                    knowledge.artifact_id,
                    load_chunk_content_size
                )
                # add head chunks
                if head_chunks:
                    knowledge_chunk_context += f"\n<head_chunks start='{head_chunks[0].chunk_id}' end='{head_chunks[len(head_chunks)-1].chunk_id}'>\n"
                    for chunk in head_chunks:
                        knowledge_chunk_context += _format_chunk_content(chunk)
                    knowledge_chunk_context += f"\n</head_chunks>\n"

                # add tail chunks
                if tail_chunks:
                    knowledge_chunk_context += f"<tail_chunks  start='{tail_chunks[0].chunk_id}' end='{tail_chunks[len(tail_chunks)-1].chunk_id}'>\n"
                    for chunk in tail_chunks:
                        knowledge_chunk_context += _format_chunk_content(chunk)
                    knowledge_chunk_context += f"\n</tail_chunks>\n"
            knowledge_context += f"{knowledge_chunk_context}\n</chunks>\n"
        knowledge_context += "</knowledge>\n"
        return knowledge_context

    async def load_context_by_workspace(
            self,
            search_filter: dict = None,
            namespace="default",
            top_k: int = 20,
            load_content: bool = True,
            load_index: bool = True,
            search_by_index: bool = True
    ):
        if not search_filter:
            search_filter = {}

        ## 1. get knowledge_chunk_index with biz_id
        knowledge_index_context = ""
        knowledge_chunk_context = ""
        if search_by_index:
            if load_index:
                artifacts_indicis = await self._workspace.search_artifact_chunks_index(
                    self.task_input,
                    search_filter=search_filter,
                    top_k=top_k * 3
                )
                if artifacts_indicis:
                    for item in artifacts_indicis:
                        knowledge_index_context += f"{item.model_dump()}\n"
            if load_content:
                knowledge_chunk_context = await self._load_artifact_chunks_by_workspace(search_filter=search_filter,
                                                                                        namespace=namespace,
                                                                                        top_k=top_k)
        else:
            start_time = time.time()
            artifacts_indicis = await self._workspace.async_query_artifact_index(search_filter=search_filter)
            logger.info(f"📊 artifacts_indicis loaded successfully in {time.time() - start_time:.3f} seconds")

            if artifacts_indicis:
                # 📈 1. get artifact statistics info
                artifact_stats = await self._get_artifact_statistics(artifacts_indicis)
                if artifact_stats:
                    knowledge_index_context += artifact_stats

                # 🔍 2. process load_index logic - each artifact read the index from topk to 2*topk
                if load_index:
                    knowledge_index_context += await self._load_artifact_index_context(
                        artifact_chunk_indicis=artifacts_indicis,
                        top_k=top_k
                    )

                # 📄 3. process load_content logic - each artifact keep head-topk and tail-topk chunks
                if load_content:
                    knowledge_chunk_context += await self._load_artifact_content_context(
                        chunk_indicis=artifacts_indicis,
                        top_k=top_k
                    )

        ## 3. format context
        knowledge_context = AMNI_CONTEXT_PROMPT["KNOWLEDGE_PART"].format(
            knowledge_index=knowledge_index_context,
            knowledge_chunks=knowledge_chunk_context
        )

        return knowledge_context


    async def _load_artifact_chunks_by_workspace(self, search_filter: dict,
                                                 namespace="default",
                                                 top_k: int = 20):
        knowledge_chunk_context = ""


        knowledge_chunks = await self.search_knowledge(user_query=self.task_input,
                                                       namespace=namespace,
                                                       search_filter=search_filter,
                                                       top_k=top_k)
        if not knowledge_chunks:
            return knowledge_chunk_context

        for item in knowledge_chunks.docs:
            metadata: EmbeddingsMetadata = item.metadata
            knowledge_chunk_context += (
                f"<knowledge_chunk>\n"
                f"<chunk_id>{item.id}</chunk_id>\n"
                f"<chunk_index>{metadata.chunk_index}</chunk_id>\n"
                f"<relevant_score>{item.score:.3f}</relevant_score>\n"
                f"<origin_knowledge_id>{metadata.artifact_id}</origin_knowledge_id>\n"
                f"<origin_knowledge_type>{metadata.artifact_type}</origin_knowledge_type>\n"
                f"<chunk_content>{item.content}</chunk_content>\n"
                f"</knowledge_chunk>\n")
        return knowledge_chunk_context

    ####################### Context Write #######################

    def put(self, key: str, value: Any, namespace: str = "default") -> None:
        if self._is_default_namespace(namespace):
            self.task_state.working_state.kv_store[key] = value
            return
        if self.get_agent_state(namespace):
            self.get_agent_state(namespace).working_state.kv_store[key] = value

    @trace.func_span(span_name="ApplicationContext#add_knowledge_list", extract_args = False)
    async def add_knowledge_list(self, knowledge_list: List[Artifact], namespace: str = "default", build_index=True) -> None:
        logger.debug(f"add_knowledge_list start")

        if knowledge_list:
            logger.debug(f"🧠 Start adding knowledge in batch, total {len(knowledge_list)} items")
            start_time = time.time()

            # for knowledge in knowledge_list:
            #     await self.add_knowledge(knowledge, namespace, build_index)
            await asyncio.gather(*(self.add_knowledge(knowledge, namespace, build_index) for knowledge in knowledge_list))
            elapsed = time.time() - start_time
            logger.info(f"✅ Batch add {len(knowledge_list)} knowledge addition completed, elapsed time: {elapsed:.3f} seconds")
        logger.debug(f"add_knowledge_list end")

    async def add_knowledge(self, knowledge: Artifact, namespace: str = "default", index=True) -> None:
        logger.debug(f"add knowledge #{knowledge.artifact_id} start")
        self._get_working_state(namespace).save_knowledge(knowledge)
        if self._workspace:
            await self._workspace.add_artifact(knowledge, index=index)
            logger.info(f"add knowledge to#{knowledge.artifact_id} workspace finished")
        logger.debug(f"add knowledge #{knowledge.artifact_id} finished")

    async def update_knowledge(self, knowledge: Artifact, namespace: str = "default") -> None:
        self._get_working_state(namespace).save_knowledge(knowledge)
        if self._workspace:
            await self._workspace.update_artifact(artifact_id=knowledge.artifact_id, content=knowledge.content)

    ####################### Context User Working Directory #######################

    @property
    def working_dir_root(self) -> str:
        return os.environ['DIR_ARTIFACT_MOUNT_BASE_PATH']

    async def add_file(self, filename: Optional[str], content: Optional[Any], mime_type: Optional[str] = "text",
                       knowledge_id: Optional[str] = None, namespace: str = "default"):
        # save metadata
        file = ArtifactAttachment(filename=filename, mime_type=mime_type, content=content)
        dir_artifact: DirArtifact = await self.load_working_dir(knowledge_id)
        # 持久化保存到目录内的新文件
        dir_artifact.add_file(file)
        # 刷新目录索引
        await self.add_knowledge(dir_artifact, namespace, index=False)

    async def init_working_dir(self, knowledge_id: Optional[str] = None) -> DirArtifact:
        if knowledge_id:
            # reset current context working dir
            self._working_dir = await self.get_knowledge_by_id(knowledge_id)
            return self._working_dir
        if self._working_dir:
            return self._working_dir
        # init by env, 因为mcp container没有本地环境，本地mcp tool实现也不全，所以本地测试时使用with_local_repository会导致文件找不到

        self._working_dir = DirArtifact.with_local_repository(base_path=str(self._workspace.repository.storage_path) + "/tempfiles")
        # else:
        #     self._working_dir = DirArtifact.with_oss_repository(
        #         access_key_id=os.environ['DIR_ARTIFACT_OSS_ACCESS_KEY_ID'],
        #         access_key_secret=os.environ['DIR_ARTIFACT_OSS_ACCESS_KEY_SECRET'],
        #         endpoint=os.environ['DIR_ARTIFACT_OSS_ENDPOINT'],
        #         bucket_name=os.environ['DIR_ARTIFACT_OSS_BUCKET_NAME'],
        #         base_path=os.environ['DIR_ARTIFACT_OSS_BASE_PATH'] + "/sid-" + self.session_id)
        
        return self._working_dir


    async def load_working_dir(self, knowledge_id: Optional[str] = None) -> DirArtifact:
        await self.init_working_dir(knowledge_id)
        self._working_dir.reload_working_files()
        return self._working_dir

    #####################################################################

    async def add_task_output(self, output_artifact: Artifact, namespace: str = "default", index=True) -> None:
        self.task_state.task_output.add_file(output_artifact.artifact_id, output_artifact.summary)
        if self._workspace:
            await self._workspace.add_artifact(output_artifact, index=index)


    def add_history_message(self, memory_message: MemoryMessage, namespace: str = "default") -> None:
        ## hook call processor such as tool_node_with_pruning
        self._get_working_state(namespace).history_messages.append(memory_message)


    ################################ Long Term Memory #####################################

    def add_fact(self, fact: Fact, namespace: str = "default", **kwargs):
        self.root._get_working_state(namespace).facts.append(fact)

    async def retrival_facts(self, namespace: str = "default", **kwargs) -> Optional[list[Fact]]:
        if not self._get_working_state(namespace):
            return []
        st = time.time()
        memory = MemoryFactory.instance()
        todo_info = await self.get_todo_info()
        current_task = "current_task: " + self.task_input
        concat_task_input = current_task + (todo_info if todo_info else "")
        facts = await memory.retrival_facts(user_id=self.user_id, user_input=concat_task_input, limit=10)
        logger.info(f"get_facts cost: {time.time() - st}")
        return facts

    def get_facts(self, namespace: str = "default", **kwargs) -> Optional[list[Fact]]:
        if not self._get_working_state(namespace):
            return []
        return self._get_working_state(namespace).facts

    def get_user_profiles(self, namespace: str = "default") -> Optional[list[UserProfile]]:
        return self._get_working_state(namespace).user_profiles

    #####################################################################

    def get_history_messages(self, namespace: str = "default") -> Optional[list[MemoryMessage]]:
        return self._get_working_state(namespace).history_messages

    def get_history_desc(self, namespace: str = "default"):
        history_messages = self.get_history_messages(namespace=namespace)
        result = ""
        for message in history_messages:
            result += f"{message.to_openai_message()}\n"
        return result

    async def get_todo_info(self):
        """
        cooperate info from working state
        Args:
        Returns:

        """
        self._workspace._load_workspace_data()
        todo_info = (
            "Below is the task execute todo information, explaining the current task progress:\n"
        )
        artifact = self._workspace.get_artifact(f"session_{self.session_id}_todo")
        if not artifact:
            return "Todo is Empty"
        todo_info += f"{artifact.content}"
        return todo_info

    async def get_actions_info(self, namespace = "default"):
        """
        cooperate info from working state
        Args:
        Returns:

        """
        self._workspace._load_workspace_data()
        artifacts = await self._workspace.query_artifacts(search_filter={
            "context_type": "actions_info",
            "task_id": self.task_id
        })
        logger.info(f"get_actions_info: {len(artifacts)}")
        actions_info = (
            "\nBelow is the actions information, including both successful and failed experiences, "
            "as well as key knowledge and insights obtained during the process ，"
            "\n充分使用这些信息:\n"
            "<knowledge_list>"
        )
        for artifact in artifacts:
            actions_info += f"  <knowledge id='{artifact.artifact_id}' summary='{artifact.summary}<'>: </knowledge>\n"
        actions_info += f"\n</knowledge_list>\n\n<tips>\n"
        actions_info += f"you can use get_knowledge(knowledge_id_xxx) to got detail content\n"
        actions_info += f"</tips>\n"
        return actions_info

    async def consolidation(self, namespace = "default"):
        consolidation_event = EventBus.create_context_event(event_type=EventType.CONTEXT_CONSOLIDATION, context=self.deep_copy(), namespace=namespace)
        event_bus = await get_global_event_bus()
        await event_bus.publish(consolidation_event)
        logger.info(f"context#{self.task_id}[{namespace}] -> consolidation trigger")

    ####################### Context Read #######################

    def get(self, key: str, namespace: str = "default") -> Any:
        if self._is_default_namespace(namespace):
            return self.task_state.working_state.kv_store.get(key)

        if self._get_working_state(namespace):
            return self._get_working_state(namespace).kv_store.get(key)

    def get_memory_messages(self, last_n=100, namespace: str = "default") -> list[MemoryMessage]:
        return self._get_working_state(namespace).memory_messages[:last_n]

    async def get_knowledge_by_id(self, knowledge_id: str, namespace: str = "default"):
        # check knowledge in the namespace
        return self._get_knowledge(knowledge_id)


    def _get_knowledge(self, knowledge_id: str) -> Optional[Artifact]:
        return self.workspace.get_artifact(knowledge_id)

    async def get_knowledge_chunk(self, knowledge_id: str, chunk_index: int) -> Optional[Chunk]:
        return await self._workspace.get_artifact_chunk(knowledge_id, chunk_index=chunk_index)

    async def search_knowledge(self, user_query: str, top_k: int = None, search_filter:dict = None, namespace: str = "default"
                               ) -> Optional[SearchResults]:
        """
        semantic search knowledge from working state

        Args:
            user_query:
            namespace:

        Returns:

        """
        if self._workspace:
            if not search_filter:
                search_filter = {}
            search_filter = {
                # "type": "knowledge",
                **search_filter
            }
            return await self._workspace.search_artifact_chunks(user_query=user_query, search_filter=search_filter,
                                                                top_k=top_k)
        return None

    ####################### Context Internal Method #######################

    async def build_knowledge_context(self, namespace: str = "default", search_filter:dict = None, top_k=20) -> str:
        return await self.load_context_by_workspace(search_filter, namespace=namespace, top_k=top_k)

    def _get_working_state(self, namespace: str = "default") -> Optional[WorkingState]:
        if self._is_default_namespace(namespace):
            return self.task_state.working_state
        if not self.get_agent_state(namespace):
            return None
        return self.get_agent_state(namespace).working_state

    def _is_default_namespace(self, namespace):
        return namespace == "default"

    def deep_copy(self) -> 'ApplicationContext':
        return self

    def merge_context(self, other_context: 'ApplicationContext') -> None:
        super().merge_context(other_context)
        # Merge task_state
        if hasattr(other_context, 'task_state') and other_context.task_state:
            try:
                for key, value in other_context.task_state.items():
                    # If key already exists, add suffix to avoid overwriting
                    self.task_state[key] = value
            except Exception as e:
                logger.warning(f"Failed to merge task_state: {e}")

    def to_dict(self) -> dict:
        result = {}

        # 序列化task_state - 使用安全的序列化函数
        if self.task_state:
            try:
                result["task_state"] = self.task_state.model_dump()
            except Exception as e:
                logger.error(f"Failed to serialize task_state: {e}")
                result["task_state"] = {"error": str(e), "type": str(type(self.task_state))}
        else:
            result["task_state"] = None

        # 序列化workspace信息
        if self._workspace:
            try:
                result["workspace_info"] = {
                    "workspace_id": getattr(self._workspace, 'workspace_id', None),
                    "storage_path": getattr(self._workspace, 'storage_path', None),
                    "workspace_type": getattr(self._workspace, 'workspace_type', None)
                }
            except Exception as e:
                logger.warning(f"Failed to serialize workspace: {e}")
                result["workspace_info"] = {"error": str(e)}
        else:
            result["workspace_info"] = None

        return result

    @classmethod
    def from_dict(cls, data: dict) -> 'ApplicationContext':
        try:
            # 反序列化task_state
            task_state = None
            if "task_state" in data and data["task_state"]:
                task_state_data = data["task_state"]
                try:
                    # 使用Pydantic的model_validate方法（v2）或parse_obj方法（v1）
                    if hasattr(ApplicationTaskContextState, 'model_validate'):
                        task_state = ApplicationTaskContextState.model_validate(task_state_data, strict=False)
                    else:
                        # 手动构建task_state
                        task_state = ApplicationTaskContextState(**task_state_data)
                except Exception as e:
                    logger.warning(f"Failed to deserialize task_state: {e} {traceback.format_exc()}")
                    # 创建一个基本的task_state
                    raise e

            # 处理workspace - 这里只能保存基本信息，实际workspace需要重新创建
            workspace = None
            if "workspace_info" in data and data["workspace_info"]:
                workspace_info = data["workspace_info"]
                if isinstance(workspace_info, dict) and "error" not in workspace_info:
                    # 注意：这里只能保存workspace的基本信息，实际workspace对象需要根据具体情况重新创建
                    logger.info(f"Workspace info preserved: {workspace_info}")
                    # workspace = WorkSpace.from_local_storages(...) # 需要根据具体情况实现

            return cls(task_state=task_state, workspace=workspace)

        except Exception as e:
            logger.error(f"Failed to deserialize ApplicationContext: {e}")
            # 返回一个基本的ApplicationContext
            return cls(task_state=ApplicationTaskContextState())

    async def _get_artifact_statistics(self, chunk_indicis: list) -> str:
        if not chunk_indicis:
            return ""
        # generate statistics info
        artifact_count_info = ", ".join(
            [f"{item.artifact_id}: {item.chunk_count} chunks " for item in chunk_indicis[:100]]
        )

        summary_prompt = (
            f"📊 Total {len(chunk_indicis)} artifacts.\n"
            f"📈 details is: \n {artifact_count_info}"
        )

        return summary_prompt

    async def _load_artifact_index_context(self, artifact_chunk_indicis: list, top_k: int) -> str:
        if not artifact_chunk_indicis:
            return ""

        # 按artifact_id分组
        artifact_chunks = {}
        for chunk_item in artifact_chunk_indicis:
            if hasattr(chunk_item, "artifact_id"):
                artifact_id = chunk_item.artifact_id
                if artifact_id not in artifact_chunks:
                    artifact_chunks[artifact_id] = []
                artifact_chunks[artifact_id].append(chunk_item)

        # 🚀 为每个artifact获取中间范围的索引，使用高效的范围查询
        tasks = []
        for artifact_id in artifact_chunks.keys():
            task = self._workspace.get_artifact_chunk_indices_middle_range(artifact_id, top_k)
            tasks.append(task)
        knowledge_index_context = ""
        if tasks:
            middle_range_indices = await asyncio.gather(*tasks)
            for artifact_id, indices in zip(artifact_chunks.keys(), middle_range_indices):
                if indices:
                    knowledge_index_context += f"\n📄 Artifact {artifact_id} (第{top_k}到{2*top_k}个chunk): index :\n"
                    for item in indices:
                        knowledge_index_context += f"{item.model_dump()}\n"

        return knowledge_index_context

    async def _load_artifact_content_context(self, chunk_indicis: list, top_k: int) -> str:
        if not chunk_indicis:
            return ""

        knowledge_chunk_context = ""

        # group by artifact_id
        artifact_chunks = {}
        for chunk_item in chunk_indicis:
            if hasattr(chunk_item, "artifact_id"):
                artifact_id = chunk_item.artifact_id
                if artifact_id not in artifact_chunks:
                    artifact_chunks[artifact_id] = []
                artifact_chunks[artifact_id].append(chunk_item)

        # 🚀 for each artifact get head and tail chunks using efficient range queries
        tasks = []
        for artifact_id in artifact_chunks.keys():
            task = self._workspace.get_artifact_chunks_head_and_tail(artifact_id, top_k)
            tasks.append(task)

        if tasks:
            head_tail_chunks = await asyncio.gather(*tasks)
            for artifact_id, (head_chunks, tail_chunks) in zip(artifact_chunks.keys(), head_tail_chunks):
                if head_chunks or tail_chunks:
                    knowledge_chunk_context += f"\n📄 Artifact {artifact_id} 内容:\n"

                    # add head chunks
                    if head_chunks:
                        knowledge_chunk_context += f"🔝 head chunks ({len(head_chunks)}个):\n"
                        for chunk in head_chunks:
                            knowledge_chunk_context += self._format_chunk_content(chunk)

                    # add tail chunks
                    if tail_chunks:
                        knowledge_chunk_context += f"🔚 tail chunks ({len(tail_chunks)}个):\n"
                        for chunk in tail_chunks:
                            knowledge_chunk_context += self._format_chunk_content(chunk)

        return knowledge_chunk_context

    def _format_chunk_content(self, chunk) -> str:
        return (
            f"<knowledge_chunk>\n"
            f"<chunk_id>{chunk.chunk_id}</chunk_id>\n"
            f"<chunk_index>{chunk.chunk_metadata.chunk_index}</chunk_index>\n"
            f"<origin_knowledge_id>{chunk.chunk_metadata.artifact_id}</origin_knowledge_id>\n"
            f"<origin_knowledge_type>{chunk.chunk_metadata.artifact_type}</origin_knowledge_type>\n"
            f"<chunk_content>{chunk.content}</chunk_content>\n"
            f"</knowledge_chunk>\n"
        )
