import abc
import asyncio
import copy
import traceback
from typing import Optional, Any, List, Dict, Tuple

from aworld import trace
from aworld.config import AgentConfig, AgentMemoryConfig
from aworld.core.common import TaskStatus
# lazy import
from aworld.core.context.base import Context
from aworld.dataset.types import TrajectoryItem
from aworld.logs.util import logger
from aworld.memory.models import MemoryMessage, UserProfile, Fact
from aworld.output import Artifact, WorkSpace, StreamingOutputs
from .config import AgentContextConfig, AmniContextConfig, AmniConfigFactory
from .config import AgentContextConfig, AmniContextConfig, AmniConfigFactory, ContextEnvConfig
from .contexts import ContextManager
from .prompt.prompts import AMNI_CONTEXT_PROMPT
from .retrieval.artifacts import SearchArtifact
from .retrieval.artifacts.file import DirArtifact
from .retrieval.chunker import Chunk
from .retrieval.embeddings import EmbeddingsMetadata, SearchResults
from .state import ApplicationTaskContextState, ApplicationAgentState, TaskOutput, TaskWorkingState
from .state.agent_state import AgentWorkingState
from .state.common import WorkingState, TaskInput
from .state.task_state import SubTask
from .utils.text_cleaner import truncate_content
from .worksapces import ApplicationWorkspace
from .worksapces import ApplicationWorkspace, workspace_repo

DEFAULT_VALUE = None

SKILL_LIST_KEY = "skill_list"
ACTIVE_SKILLS_KEY = "active_skills"

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
        """Context Offloading - Store information outside the LLM's context via external storage

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

    def get_agent_context_config(self, namespace: str) -> AgentContextConfig:
        pass

    def get_agent_memory_config(self, namespace: str) -> AgentMemoryConfig:
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

    """
    Agent Skills Support
    """

    async def init_skill_list(self, skill_list: Dict[str, Any], namespace: str):
        """
        init skill list from agent
        """
        self.put(SKILL_LIST_KEY, skill_list, namespace=namespace)
        for skill_name, skill_config in skill_list.items():
            if skill_config.get('active', False):
                await self.active_skill(skill_name, namespace)

    async def active_skill(self, skill_name: str, namespace: str) -> str:
        """
        Activate a skill to help agent perform a task.

        Delegates to SkillService.active_skill().
        """
        return await self.skill_service.active_skill(skill_name, namespace)

    async def load_skill_agent_mcp_config(self, skill_agent: str) -> Dict[str, Any]:
        """
        Load skill agent MCP config.

        Delegates to SkillService.load_skill_agent_mcp_config().
        """
        return await self.skill_service.load_skill_agent_mcp_config(skill_agent)

    async def get_env_config(self, namespace: str) -> ContextEnvConfig:
        """
        Retrieve the environment configuration for a given namespace.

        Args:
            namespace (str): Namespace used to locate the target agent configuration.

        Returns:
            ContextEnvConfig: Environment configuration object.
        """
        if isinstance(self.get_config().agent_config, Dict):
            if (self.get_config().agent_config.get(namespace)
                    and self.get_config().agent_config.get(namespace).env_config
                    and isinstance(self.get_config().agent_config.get(namespace).env_config, ContextEnvConfig)):
                return self.get_config().agent_config.get(namespace).env_config
        if self.get_config().env_config:
            return self.get_config().env_config
        return ContextEnvConfig()


    async def offload_skill(self, skill_name: str, namespace: str) -> str:
        """
        Offload a skill to help agent perform a task.

        Delegates to SkillService.offload_skill().
        """
        return await self.skill_service.offload_skill(skill_name, namespace)

    async def get_active_skills(self, namespace: str) -> list[str]:
        """
        Get active skills from context.

        Delegates to SkillService.get_active_skills().
        """
        return await self.skill_service.get_active_skills(namespace)

    async def get_skill_list(self, namespace: str) -> Dict[str, Any]:
        """
        Get skill list from context.

        Delegates to SkillService.get_skill_list().
        """
        return await self.skill_service.get_skill_list(namespace)

    async def get_skill(self, skill_name: str, namespace: str) -> Dict[str, Any]:
        """
        Get a specific skill configuration.

        Delegates to SkillService.get_skill().
        """
        return await self.skill_service.get_skill(skill_name, namespace)

    async def get_skill_name_list(self, namespace: str) -> list[str]:
        """
        Get list of skill names.

        Delegates to SkillService.get_skill_name_list().
        """
        return await self.skill_service.get_skill_name_list(namespace)


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
        self._initialized = False

        # Initialize services (lazy initialization)
        self._knowledge_service = None
        self._skill_service = None
        self._task_state_service = None
        self._memory_service = None
        self._prompt_service = None
        self._freedom_space_service = None

    def get_config(self) -> AmniContextConfig:
        return self._config

    def get_agent_context_config(self, namespace: str) -> AgentContextConfig:
        return self.get_config().get_agent_context_config(namespace=namespace)

    def get_agent_memory_config(self, namespace: str) -> AgentMemoryConfig:
        return self.get_config().get_agent_memory_config(namespace=namespace)

    @property
    def knowledge_service(self):
        """Get KnowledgeService instance (lazy initialization)."""
        if self._knowledge_service is None:
            from .services import KnowledgeService
            self._knowledge_service = KnowledgeService(self)
        return self._knowledge_service

    @property
    def skill_service(self):
        """Get SkillService instance (lazy initialization)."""
        if self._skill_service is None:
            from .services import SkillService
            self._skill_service = SkillService(self)
        return self._skill_service

    @property
    def task_state_service(self):
        """Get TaskStateService instance (lazy initialization)."""
        if self._task_state_service is None:
            from .services import TaskStateService
            self._task_state_service = TaskStateService(self)
        return self._task_state_service

    @property
    def memory_service(self):
        """Get MemoryService instance (lazy initialization)."""
        if self._memory_service is None:
            from .services import MemoryService
            self._memory_service = MemoryService(self)
        return self._memory_service

    @property
    def prompt_service(self):
        """Get PromptService instance (lazy initialization)."""
        if self._prompt_service is None:
            from .services import PromptService
            self._prompt_service = PromptService(self)
        return self._prompt_service

    @property
    def freedom_space_service(self):
        """Get FreedomSpaceService instance (lazy initialization).

        """
        if self._freedom_space_service is None:
            from .services import FreedomSpaceService
            self._freedom_space_service = FreedomSpaceService(self)
        return self._freedom_space_service

    ####################### Context Build/Copy/Merge/Restore #######################

    @classmethod
    def create(cls,
               user_id: str = "user",
               session_id: str = None,
               task_id: str = None,
               task_content: str = "",
               context_config: AmniContextConfig = None,
               parent: "ApplicationContext" = None,
               **kwargs) -> "ApplicationContext":
        """
        Create ApplicationContext synchronously with simplified parameters.

        This is a synchronous factory method that creates a minimal ApplicationContext
        without requiring async operations. Workspace will be initialized lazily when needed.

        Args:
            user_id: User identifier, defaults to "user"
            session_id: Session identifier. If None, will be generated from timestamp
            task_id: Task identifier. If None, will be generated from timestamp
            task_content: Task content string, defaults to empty string
            context_config: Context configuration. If None, will create default config
            parent: Parent ApplicationContext for hierarchical contexts
            **kwargs: Additional arguments passed to __init__

        Returns:
            ApplicationContext: Created ApplicationContext instance

        Example:
            >>> context = ApplicationContext.create(
            ...     session_id="session_123",
            ...     task_id="task_456",
            ...     task_content="Do something"
            ... )
        """
        from datetime import datetime

        # Generate IDs if not provided
        if not session_id:
            session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        if not task_id:
            task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Create TaskInput with simplified parameters
        task_input = TaskInput(
            user_id=user_id,
            session_id=session_id,
            task_id=task_id,
            task_content=task_content,
            origin_user_input=task_content
        )

        # Create default config if not provided
        if not context_config:
            context_config = AmniConfigFactory.create()

        # Create minimal TaskWorkingState (synchronously, without async memory operations)
        task_working_state = TaskWorkingState(
            history_messages=[],
            user_profiles=[],
            kv_store={}
        )

        # Create ApplicationTaskContextState
        task_state = ApplicationTaskContextState(
            task_input=task_input,
            working_state=task_working_state,
            previous_round_results=[],
            task_output=TaskOutput()
        )

        # Create ApplicationContext with workspace=None (will be initialized lazily)
        context = cls(
            task_state=task_state,
            workspace=None,  # Workspace will be initialized lazily
            parent=parent,
            context_config=context_config,
            working_dir=None,
            task_id=task_id,
            trace_id=kwargs.get('trace_id'),
            session=kwargs.get('session'),
            engine=kwargs.get('engine')
        )

        # Store current round's input as a separate field
        context.put("origin_task_input", context.task_input)
        context.put("origin_task_output", context.task_output)

        return context

    @classmethod
    async def from_input(cls, task_input: TaskInput, workspace: WorkSpace = None, use_checkpoint: bool = False, context_config: AmniContextConfig = None,  **kwargs) -> "ApplicationContext":
        if not context_config:
            context_config = AmniConfigFactory.create()

        if not workspace:
            # build workspace for offload tool results
            workspace = await workspace_repo.get_session_workspace(session_id=task_input.session_id)
        try:
            if use_checkpoint:
                # restore context from checkpoint
                checkpoint = await get_context_manager().aget_checkpoint(task_input.session_id)
                if checkpoint:
                    logger.info(
                        f"[CONTEXT RESTORE]Restore context from checkpoint: {task_input.session_id} {await get_context_manager().aget_checkpoint(task_input.session_id)}")
                    # Reference context from previous unfinished task to get historical context
                    context: "ApplicationContext" = await get_context_manager().build_context_from_checkpoint(task_input.session_id)
                    # Store previous round's input as a separate field
                    context.put("origin_task_input", context.task_input)
                    context.put("origin_task_output", context.task_output)
                    # Update with current round's input
                    context.task_state.set_task_input(task_input)
                    context.task_state.set_task_output(TaskOutput())
                    history_messages = await get_context_manager().get_task_histories(task_input)
                    context.task_state.working_state.history_messages = history_messages
                    logger.info(f"[CONTEXT RESTORE]history_messages: len = {len(history_messages) if history_messages else 0}")

                    user_profiles = await get_context_manager().get_user_profiles(task_input)
                    context.task_state.working_state.user_profiles = user_profiles
                    logger.info(f"[CONTEXT RESTORE]user_profiles: len = {len(context.task_state.working_state.user_profiles) if context.task_state.working_state.user_profiles else 0}")

                    context._workspace = workspace

                    # Clear checkpoint to avoid duplicate context restoration when creating sub-task
                    get_context_manager().delete_checkpoint(task_input.session_id)
                    await context.init_working_dir()
                    return context
                else:
                    logger.info(f"[CONTEXT BUILD]Build new context {task_input.session_id}:{task_input.task_id}")
                    task_state = await cls._build_new_task_state(task_input)
                    context = ApplicationContext(task_state, workspace, context_config=context_config)
                    await context.init_working_dir()
                    return context
            else:
                task_state = await cls._build_new_task_state(task_input)
                context = ApplicationContext(task_state, workspace = workspace, context_config = context_config)
                # Store current round's input as a separate field
                context.put("origin_task_input", context.task_input)
                context.put("origin_task_output", context.task_output)
                await context.init_working_dir()
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

    async def build_sub_context(self, sub_task_content: str, sub_task_id: str = None, task_type: str = 'normal', **kwargs):
        logger.info(f"build_sub_context: {self.task_id} -> {sub_task_id}: {sub_task_content}")
        # force cast to string to avoid type error
        sub_task_content = str(sub_task_content)
        sub_task_input = self.task_state.task_input.new_subtask(sub_task_content, sub_task_id)
        agents = kwargs.get("agents")
        agent_list = []
        if agents:
            agent_list = [agent for agent_id, agent in agents.items()]

        sub_context = await self.build_sub_task_context(sub_task_input, agents=agent_list, task_type=task_type)

        # Record task relationship
        self.add_task_node(
            child_task_id=sub_context.task_id,
            parent_task_id=self.task_id,
            caller_agent_info=self.agent_info,
            caller_id=self.agent_info.current_agent_id if self.agent_info and hasattr(self.agent_info, 'current_agent_id') else None
        )

        return sub_context

    def add_sub_tasks(self, sub_task_inputs: list[TaskInput], task_type: str = 'normal'):
        """Add sub tasks to the task list.

        Delegates to TaskStateService.add_sub_task().

        Args:
            sub_task_inputs: List of task inputs.
            task_type: Task type, 'normal' or 'background'. Defaults to 'normal'.
        """
        for sub_task_input in sub_task_inputs:
            self.task_state_service.add_sub_task(sub_task_input, task_type)

    async def build_sub_task_context(self, sub_task_input: TaskInput,
                                     sub_task_history: list[MemoryMessage] = None,
                                     workspace: WorkSpace = None,
                                     agents = None,
                                     task_type: str = 'normal') -> "ApplicationContext":
        task_state = await self.build_sub_task_state(sub_task_input, sub_task_history)
        if not workspace:
            workspace = self.workspace

        sub_context = ApplicationContext(task_state, workspace, parent=self, context_config=self.get_config())
        # Initialize sub-context event bus (global event bus already started, no need to restart here)
        # Upsert sub task to task state
        self.task_state_service.upsert_sub_task(sub_task_input, task_type)

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

    async def init_swarm_state(self, swarm):
        """
        Build Swarm's Private State(Hack Code)
        Args:
            swarm: Swarm
        Returns:
        """
        pass


    async def build_agents_state(self, agents):
        """Build Multi Agent's Private State

        Args:
            agents: list of agents

        Returns:

        """
        for agent in agents:
            if isinstance(agent, list):
                # Iterate through each agent in the tuple
                for single_agent in agent:
                    await self.build_agent_state(single_agent)
            else:
                await self.build_agent_state(agent)

    async def init_agent_state(self, agent):
        """Build Single Agent Private State.

        Args:
            agent: Agent

        Returns:

        """
        await self.build_agent_state(agent)

    async def build_agent_state(self, agent):
        """Build Single Agent Private State.

        Args:
            agent: Agent

        Returns:

        """
        if not self.has_agent_state(agent.id()):
            logger.info(f"build_agent_state agent#{agent.id()}")
            application_agent_state = self._build_agent_state(agent_id=agent.id(), agent_config=agent.conf)

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

            if agent.conf and agent.conf.skill_configs:
                logger.debug(f"init_skill_list: {agent.id()}")
                await self.init_skill_list(namespace=agent.id(), skill_list=agent.conf.skill_configs)

    def _build_agent_state(self, agent_id: str, agent_config: AgentConfig) -> ApplicationAgentState:
        agent_state = ApplicationAgentState()

        # agent config
        agent_state.agent_id = agent_id
        agent_state.agent_config = agent_config

        self.set_agent_state(agent_id, agent_state)
        return agent_state

    def merge_sub_context(self, sub_task_context: 'ApplicationContext', **kwargs):
        logger.info(f"merge_sub_context: {sub_task_context.task_id} -> {self.task_id}")

        super().merge_sub_context(sub_task_context)

        # merge sub task kv_store
        sub_task_kv_store = sub_task_context.task_state_service.get_kv_store()
        if sub_task_kv_store:
            current_kv_store = self.task_state_service.get_kv_store()
            current_kv_store.update(sub_task_kv_store)

        # merge sub task status & result
        sub_task_id = sub_task_context.task_state_service.get_task_input().task_id
        # Iterate through sub_task_list to find matching sub_task_id
        sub_task_list = self.task_state_service.get_sub_task_list()
        for sub_task in sub_task_list or []:
            if sub_task.task_id == sub_task_id:
                sub_task.status = sub_task_context.task_status
                sub_task.result = sub_task_context.task_output_object

                # For background tasks, use the latest task state
                if sub_task.task_type == 'background' and sub_task_context._task:
                    sub_task.status = sub_task_context._task.task_status
                    # Update result from task response if available
                    if hasattr(sub_task_context._task, 'outputs') and sub_task_context._task.outputs:
                        # Get the latest output from task
                        pass  # Task output is already merged via task_output_object
                break

        # merge token
        cur_token_usage = self.token_usage
        self.add_token(sub_task_context.token_usage)
        logger.info(f"merge_sub_context tokens finished: {cur_token_usage} + {sub_task_context.token_usage} -> {self.token_usage}")

    async def update_task_after_run(self, task_response: 'TaskResponse'):
        if task_response and task_response.success:
            self.task_status = task_response.status
            self.task_output = task_response.answer
        else:
            self.task_status = task_response.status
            if self._task.outputs and isinstance(self._task.outputs, StreamingOutputs):
                self.task_output = self._task.outputs.get_message_output_content()
            else:
                self.task_output = task_response.msg

        self.task_output_object.actions_info = await self.get_actions_info()
        self.task_output_object.todo_info = await self.get_todo_info()

        if self.parent:
            self.parent.merge_sub_context(self)

    #################### Agent Isolated State ###################
    """
    Agent State Management
    
    Note: These methods delegate to TaskStateService. For direct service access, use context.task_state_service.
    """

    def set_agent_state(self, agent_id: str, agent_state: ApplicationAgentState):
        """
        Set agent state for the given agent_id.

        Delegates to TaskStateService.set_agent_state().
        """
        self.task_state_service.set_agent_state(agent_id, agent_state)

    def get_agent_state(self, agent_id: str) -> Optional[ApplicationAgentState]:
        """
        Get agent state for the given agent_id.

        Delegates to TaskStateService.get_agent_state().
        """
        return self.task_state_service.get_agent_state(agent_id)

    def has_agent_state(self, agent_id: str):
        """
        Check if agent state exists for the given agent_id.

        Delegates to TaskStateService.has_agent_state().
        """
        return self.task_state_service.has_agent_state(agent_id)

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
    def task_status(self) -> 'TaskStatus':
        """Get current task status."""
        return self.task_state_service.get_task_status()

    @task_status.setter
    def task_status(self, status: 'TaskStatus'):
        """Set task status."""
        self.task_state_service.set_task_status(status)

    @property
    def task_input_object(self) -> TaskInput:
        """Get task input object."""
        return self.task_state_service.get_task_input()

    @property
    def task_output_object(self) -> TaskOutput:
        """Get task output object."""
        return self.task_state_service.get_task_output()

    @property
    def sub_task_list(self) -> Optional[list[SubTask]]:
        """Get list of sub tasks."""
        return self.task_state_service.get_sub_task_list()

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
        """Get workspace. Returns None if not initialized. Use init_workspace() for async initialization."""
        return self._workspace

    @workspace.setter
    def workspace(self, workspace):
        self._workspace = workspace

    async def init_workspace(self) -> "ApplicationWorkspace":
        """
        Initialize workspace asynchronously (lazy initialization).

        This method should be called when workspace is needed for the first time.
        Workspace will be created based on session_id.

        Returns:
            ApplicationWorkspace: Initialized workspace instance
        """
        if self._workspace is None:
            self._workspace = await workspace_repo.get_session_workspace(session_id=self.session_id)
        return self._workspace

    async def _ensure_workspace(self) -> "ApplicationWorkspace":
        """
        Ensure workspace is initialized, initializing it if necessary.

        This is a helper method to be used in methods that require workspace.
        It automatically initializes workspace if it's None.

        Returns:
            ApplicationWorkspace: Initialized workspace instance

        Raises:
            RuntimeError: If workspace cannot be initialized (e.g., session_id is None)
        """
        if self._workspace is None:
            if not self.session_id:
                raise RuntimeError("Cannot initialize workspace: session_id is required")
            self._workspace = await workspace_repo.get_session_workspace(session_id=self.session_id)
        return self._workspace

    @property
    def model_config(self):
        """Get model configuration from task state."""
        return self.task_state_service.get_model_config()

    @property
    def history(self):
        """Get history messages from working state."""
        return self.task_state_service.get_history_messages()

    @property
    def tree(self) -> str:
        """Generate a tree representation showing the current context's position in the context hierarchy.
        
        Delegates to utils.build_context_tree().
        
        Returns:
            str: A formatted tree string showing the context hierarchy with subtasks
        """
        from .utils import build_context_tree
        return build_context_tree(self)

    @staticmethod
    async def user_similar_history(context: "ApplicationContext") -> str:
        pass

    ####################### Context logical schema #######################

    def get_from_artifacts(self, key: str, state: WorkingState):
        if not state:
            return DEFAULT_VALUE

        if key.endswith('/summary'):
            artifact_id = key[:-8]
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
        # Case 1: current.xxx - get from current context
        if key.startswith("current."):
            actual_field = key[8:]  # Remove "current." prefix
            return self.get_logical_schema_field(actual_field, context)
        # Case 2-4: parent.xxx, root.xxx, parent.parent.xxx etc. - use recursive path parsing
        elif key.startswith(("parent.", "root.")):
            # Split path
            parts = key.split('.')
            current_obj = context
            # Traverse each part of the path
            for part in parts[:-1]:
                if not hasattr(current_obj, part):
                    return None
                current_obj = getattr(current_obj, part)
                if current_obj is None:
                    return None
            # If final object is ApplicationContext, get field value from it
            if hasattr(current_obj, 'task_state'):
                # Get actual field name from the last part of the path
                # E.g. parent.parent.data -> we need to get data field
                actual_field = parts[-1] if len(parts) > 1 else key
                return ApplicationContext.get_logical_schema_field(key=actual_field, context=current_obj)
        # Case 5: xxx - get from current context and all parents, iterate until value is found
        else:
            # First try current context
            value = self.get_from_task_state(key, context.task_state)
            if value is not None and value != DEFAULT_VALUE:
                return value
            # Whether to recursively query parent task context
            if not recursive:
                return None
            # Then recursively traverse all parents
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
            # 1. get Key from current context
            if hasattr(context, key):
                value = getattr(context, key)
                if value is not None:
                    return str(value)

            # 2. get key from agent context
            agent_state = None
            if context.task_state.working_state and context.task_state.working_state.agent_states:
                agent_state = context.task_state.working_state.agent_states.get(agent_id)
            value = context.get_from_agent_state(key, agent_state)
            if value is not None:
                return value

            # 3. get key from Context parent
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

    ####################### Prompt Management #######################
    """
    Prompt Management Operations
    
    Note: These methods delegate to PromptService. For direct service access, use context.prompt_service.
    """

    async def pub_and_wait_system_prompt_event(self, system_prompt: str, user_query: str, agent_id: str,
                                               agent_name: str, namespace: str = "default"):
        """
        Publish and wait for system prompt event.

        Delegates to PromptService.pub_and_wait_system_prompt_event().
        """
        return await self.prompt_service.pub_and_wait_system_prompt_event(
            system_prompt, user_query, agent_id, agent_name, namespace
        )

    async def pub_and_wait_tool_result_event(self,
                                             tool_result: Any,
                                             tool_call_id: str,
                                             agent_id: str,
                                             agent_name: str,
                                             namespace: str = "default"):
        """
        Publish and wait for tool result event.

        Delegates to PromptService.pub_and_wait_tool_result_event().
        """
        return await self.prompt_service.pub_and_wait_tool_result_event(
            tool_result, tool_call_id, agent_id, agent_name, namespace
        )

    ####################### Context Write #######################

    async def offload_by_workspace(self, artifacts: list[Artifact], namespace="default", biz_id: str = None):
        """
        Context Offloading - Store information outside the LLM's context via external storage.

        Delegates to KnowledgeService.offload_by_workspace().
        """
        return await self.knowledge_service.offload_by_workspace(artifacts, namespace, biz_id)

    def need_index(self, artifact: Artifact):
        """
        Check if artifact needs indexing.

        Delegates to KnowledgeService._need_index().
        """
        return self.knowledge_service._need_index(artifact)


    async def load_context_by_workspace(
            self,
            search_filter: dict = None,
            namespace="default",
            top_k: int = 20,
            load_content: bool = True,
            load_index: bool = True,
            search_by_index: bool = True
    ):
        """
        Load knowledge context from workspace.

        Delegates to KnowledgeService.load_context_by_workspace().
        """
        return await self.knowledge_service.load_context_by_workspace(
            search_filter=search_filter,
            namespace=namespace,
            top_k=top_k,
            load_content=load_content,
            load_index=load_index,
            search_by_index=search_by_index
        )

    ####################### Context Write #######################

    def put(self, key: str, value: Any, namespace: str = "default") -> None:
        logger.debug(f"{id(self)}#put key: {key}, value: {value}, namespace: {namespace}")
        if self._is_default_namespace(namespace):
            self.task_state.working_state.kv_store[key] = value
            return
        if self.get_agent_state(namespace):
            self.get_agent_state(namespace).working_state.kv_store[key] = value

    @trace.func_span(span_name="ApplicationContext#add_knowledge_list", extract_args = False)
    async def add_knowledge_list(self, knowledge_list: List[Artifact], namespace: str = "default", build_index=True) -> None:
        """
        Add multiple knowledge artifacts in batch.

        Delegates to KnowledgeService.add_knowledge_list().
        """
        return await self.knowledge_service.add_knowledge_list(knowledge_list, namespace, build_index)

    async def add_knowledge(self, knowledge: Artifact, namespace: str = "default", index=True) -> None:
        """
        Add a single knowledge artifact.

        Delegates to KnowledgeService.add_knowledge().
        """
        return await self.knowledge_service.add_knowledge(knowledge, namespace, index)

    async def update_knowledge(self, knowledge: Artifact, namespace: str = "default") -> None:
        """
        Update an existing knowledge artifact.

        Delegates to KnowledgeService.update_knowledge().
        """
        return await self.knowledge_service.update_knowledge(knowledge, namespace)

    ####################### Freedom Space #######################
    """
    Freedom Space Management Operations
        
    Note: These methods delegate to FreedomSpaceService. For direct service access, use context.freedom_space_service.
    """

    @property
    def working_dir_env_mounted_path(self) -> str:
        """
        Get environment mounted path for freedom space.

        Delegates to FreedomSpaceService.get_env_mounted_path().
        """
        return self.freedom_space_service.get_env_mounted_path()

    def get_working_dir_path(self) -> str:
        """
        Get freedom space base path.

        Delegates to FreedomSpaceService.get_freedom_space_path().
        """
        return self.freedom_space_service.get_freedom_space_path()

    def abs_file_path(self, filename: str):
        """
        Get absolute file path in the environment.

        Delegates to FreedomSpaceService.get_abs_file_path().
        """
        return self.freedom_space_service.get_abs_file_path(filename)

    async def add_file(self, filename: Optional[str], content: Optional[Any], mime_type: Optional[str] = "text",
                       namespace: str = "default", origin_type: str = None, origin_path : str = None, refresh_workspace: bool = True) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Add a file to freedom space.

        Delegates to FreedomSpaceService.add_file().
        """
        return await self.freedom_space_service.add_file(filename, content, mime_type, namespace, origin_type, origin_path, refresh_workspace)

    async def init_working_dir(self) -> DirArtifact:
        """
        Initialize freedom space (working directory).

        Delegates to FreedomSpaceService.init_freedom_space().
        """
        return await self.freedom_space_service.init_freedom_space()

    async def load_working_dir(self) -> DirArtifact:
        """
        Load freedom space and reload files.

        Delegates to FreedomSpaceService.load_freedom_space().
        """
        return await self.freedom_space_service.load_freedom_space()

    async def refresh_working_dir(self):
        """
        Refresh freedom space and sync to workspace.

        Delegates to FreedomSpaceService.refresh_freedom_space().
        """
        return await self.freedom_space_service.refresh_freedom_space()




    #####################################################################

    async def add_task_output(self, output_artifact: Artifact, namespace: str = "default", index=True) -> None:
        """
        Add a task output artifact to task state and workspace.

        Delegates to KnowledgeService.add_task_output().
        """
        return await self.knowledge_service.add_task_output(output_artifact, namespace, index)


    ################################ Memory Management #####################################
    """
    Memory Management Operations
    
    Note: These methods delegate to MemoryService. For direct service access, use context.memory_service.
    """

    def add_history_message(self, memory_message: MemoryMessage, namespace: str = "default") -> None:
        """
        Add a memory message to the working state (short-term memory).

        Delegates to MemoryService.add_history_message().
        """
        self.memory_service.add_history_message(memory_message, namespace)

    ################################ Long Term Memory #####################################

    def add_fact(self, fact: Fact, namespace: str = "default", **kwargs):
        """
        Add a fact to working state (long-term memory).

        Delegates to MemoryService.add_fact().
        """
        self.memory_service.add_fact(fact, namespace, **kwargs)

    async def retrival_facts(self, namespace: str = "default", **kwargs) -> Optional[list[Fact]]:
        """
        Retrieve facts from long-term memory storage.

        Delegates to MemoryService.retrieval_facts().
        """
        return await self.memory_service.retrieval_facts(namespace, **kwargs)

    def get_facts(self, namespace: str = "default", **kwargs) -> Optional[list[Fact]]:
        """
        Get facts from working state (long-term memory).

        Delegates to MemoryService.get_facts().
        """
        return self.memory_service.get_facts(namespace, **kwargs)

    def get_user_profiles(self, namespace: str = "default") -> Optional[list[UserProfile]]:
        """
        Get user profiles from working state.

        Delegates to MemoryService.get_user_profiles().
        """
        return self.memory_service.get_user_profiles(namespace)

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
        Get todo information from workspace.

        Delegates to KnowledgeService.get_todo_info().
        """
        return await self.knowledge_service.get_todo()

    async def get_actions_info(self, namespace = "default"):
        """
        Get actions information from workspace.

        Delegates to KnowledgeService.get_actions_info().
        """
        return await self.knowledge_service.get_actions_info(namespace)

    async def consolidation(self, namespace = "default"):
        """
        Context consolidation: Extract and generate long-term memory from context.

        Delegates to MemoryService.consolidation().
        """
        return await self.memory_service.consolidation(namespace)

    ####################### Context Read #######################

    def get(self, key: str, namespace: str = "default") -> Any:
        """
        Get a value from key-value store.

        Delegates to TaskStateService.get_kv().
        """
        logger.info(f"{id(self)}#get value for namespace: {namespace} -> key: {key}")
        return self.task_state_service.get_kv(key, namespace)

    def get_memory_messages(self, last_n=100, namespace: str = "default") -> list[MemoryMessage]:
        """
        Get memory messages from working state (short-term memory).

        Delegates to MemoryService.get_memory_messages().
        """
        return self.memory_service.get_memory_messages(last_n, namespace)

    async def get_knowledge_by_id(self, knowledge_id: str, namespace: str = "default"):
        """
        Get a knowledge artifact by ID.

        Delegates to KnowledgeService.get_knowledge_by_id().
        """
        return await self.knowledge_service.get_knowledge_by_id(knowledge_id, namespace)

    async def get_knowledge_chunk(self, knowledge_id: str, chunk_index: int) -> Optional[Chunk]:
        """
        Get a specific chunk from a knowledge artifact.

        Delegates to KnowledgeService.get_knowledge_chunk().
        """
        return await self.knowledge_service.get_knowledge_chunk(knowledge_id, chunk_index)

    async def search_knowledge(self, user_query: str, top_k: int = None, search_filter:dict = None, namespace: str = "default"
                               ) -> Optional[SearchResults]:
        """
        Search knowledge using semantic search.

        Delegates to KnowledgeService.search_knowledge().
        """
        return await self.knowledge_service.search_knowledge(user_query, top_k, search_filter, namespace)

    async def delete_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> None:
        """
        Delete a knowledge artifact by ID.

        Delegates to KnowledgeService.delete_knowledge_by_id().
        """
        return await self.knowledge_service.delete_knowledge_by_id(knowledge_id, namespace)

    ####################### Context Internal Method #######################

    async def build_knowledge_context(self, namespace: str = "default", search_filter:dict = None, top_k=20) -> str:
        """
        Build knowledge context string.

        Delegates to KnowledgeService.build_knowledge_context().
        """
        return await self.knowledge_service.build_knowledge_context(namespace, search_filter, top_k)

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

    def to_dict(self) -> dict:
        result = {}

        # Serialize task_state using safe serialization function
        if self.task_state:
            try:
                result["task_state"] = self.task_state.model_dump()
            except Exception as e:
                logger.error(f"Failed to serialize task_state: {e}")
                result["task_state"] = {"error": str(e), "type": str(type(self.task_state))}
        else:
            result["task_state"] = None

        # Serialize workspace information
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
            # Deserialize task_state
            task_state = None
            if "task_state" in data and data["task_state"]:
                task_state_data = data["task_state"]
                try:
                    # Use Pydantic's model_validate method (v2) or parse_obj method (v1)
                    if hasattr(ApplicationTaskContextState, 'model_validate'):
                        task_state = ApplicationTaskContextState.model_validate(task_state_data, strict=False)
                    else:
                        # Manually build task_state
                        task_state = ApplicationTaskContextState(**task_state_data)
                except Exception as e:
                    logger.warning(f"Failed to deserialize task_state: {e} {traceback.format_exc()}")
                    # Create a basic task_state
                    raise e

            # Handle workspace - only basic info can be saved here, actual workspace needs to be recreated
            workspace = None
            if "workspace_info" in data and data["workspace_info"]:
                workspace_info = data["workspace_info"]
                if isinstance(workspace_info, dict) and "error" not in workspace_info:
                    # Note: Only basic workspace info can be saved here, actual workspace object needs to be recreated based on specific situation
                    logger.info(f"Workspace info preserved: {workspace_info}")
                    # workspace = WorkSpace.from_local_storages(...) # Need to implement based on specific situation

            return cls(task_state=task_state, workspace=workspace)

        except Exception as e:
            logger.error(f"Failed to deserialize ApplicationContext: {e}")
            # Return a basic ApplicationContext
            return cls(task_state=ApplicationTaskContextState())


    async def get_task_status(self):
        return self.root._task.task_status

    async def update_task_status(self, task_id: str, status: 'TaskStatus'):
        if task_id == self.task_id:
            self._task.task_status = status

    async def post_init(self):
        if self._initialized:
            return

        if self._task.swarm:
            await self.build_agents_state(self._task.swarm.ordered_agents)
        elif self._task.agent:
            await self.build_agent_state(self._task.agent)

        self._initialized = True

    async def add_task_trajectory(self, task_id: str, task_trajectory: List[Dict[str, Any]]):
        """Add trajectory data for a task.
        Delegate to root context to centralize storage.
        """
        if self.root != self:
            await self.root.add_task_trajectory(task_id, task_trajectory)
        else:
            await super().add_task_trajectory(task_id, task_trajectory)


    async def update_task_trajectory(self, message: Any, task_id: str = None, **kwargs):
        """Generate trajectory item from message and append to dataset.
        Delegate to root context.
        """
        if self.root != self:
            await self.root.update_task_trajectory(message, task_id, **kwargs)
        else:
            await super().update_task_trajectory(message, task_id, **kwargs)

    async def get_task_trajectory(self, task_id: str) -> List[TrajectoryItem]:
        """Get trajectory data for a task.
        Delegate to root context.
        """
        if self.root != self:
            return await self.root.get_task_trajectory(task_id)
        else:
            return await super().get_task_trajectory(task_id)

    def add_task_node(self, child_task_id: str, parent_task_id: str, caller_agent_info=None, **kwargs):
        """Record the relationship between child task and parent task.
        Delegate to root context.
        """
        agent_info = caller_agent_info
        if not agent_info:
            agent_info = self.agent_info
        if self.root != self:
            self.root.add_task_node(child_task_id, parent_task_id, caller_agent_info=agent_info, **kwargs)
        else:
            super().add_task_node(child_task_id, parent_task_id, caller_agent_info=agent_info, **kwargs)

    def add_background_task(self, task_id: str, agent_id: str, agent_name: str, parent_task_id: str = None):
        """Add a background task as a sub-task in sub_task_list."""
        from aworld.core.context.amni.state.common import TaskInput
        sub_task_input = TaskInput(
            user_id=self.user_id,
            session_id=self.session_id,
            task_id=task_id,
            task_content=f"Background task: {agent_name}",
            model=agent_id
        )
        self.task_state_service.add_sub_task(sub_task_input, task_type='background')
        
        # Also record in task graph for consistency
        self.add_task_node(
            child_task_id=task_id,
            parent_task_id=parent_task_id or self.task_id,
            caller_id=agent_id
        )

    def mark_background_task_completed(self, task_id: str):
        """Mark a background task as completed in sub_task_list."""
        sub_task_list = self.task_state_service.get_sub_task_list()
        for sub_task in sub_task_list or []:
            if sub_task.task_id == task_id:
                sub_task.status = 'success'
                break

    def has_pending_background_tasks(self, agent_id: str, parent_task_id: str = None) -> bool:
        """Check for pending background tasks in sub_task_list."""
        sub_task_list = self.task_state_service.get_sub_task_list()
        for sub_task in sub_task_list or []:
            if sub_task.task_type == 'background' and sub_task.status in ['running', 'init']:
                return True
        return False