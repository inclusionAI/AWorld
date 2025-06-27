import datetime
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Any, Literal, Union, List, Dict

from pydantic import BaseModel, Field, ConfigDict

from aworld.models.llm import LLMModel


class MemoryItem(BaseModel):
    id: str = Field(description="id")
    content: Any = Field(description="content")
    created_at: Optional[str] = Field(None, description="created at")
    updated_at: Optional[str] = Field(None, description="updated at")
    metadata: dict = Field(
        description="metadata, use to store additional information, such as user_id, agent_id, run_id, task_id, etc.")
    tags: list[str] = Field(description="tags")
    histories: list["MemoryItem"] = Field(default_factory=list)
    deleted: bool = Field(default=False)
    memory_type: Literal["init", "message", "summary", "agent_experience", "user_profile"] = Field(default="message")
    version: int = Field(description="version")

    def __init__(self, **data):
        # Set default values for optional fields
        if "id" not in data:
            data["id"] = str(uuid.uuid4())
        if "created_at" not in data:
            data["created_at"] = datetime.datetime.now().isoformat()
        if "updated_at" not in data:
            data["updated_at"] = data["created_at"]
        if "metadata" not in data:
            data["metadata"] = {}
        if "tags" not in data:
            data["tags"] = []
        if "version" not in data:
            data["version"] = 1

        super().__init__(**data)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryItem":
        """Create a MemoryItem instance from a dictionary.

        Args:
            data (dict): A dictionary containing the memory item data.

        Returns:
            MemoryItem: An instance of MemoryItem.
        """
        return cls(**data)


class MemoryStore(ABC):
    """
    Memory store interface for messages history
    """

    @abstractmethod
    def add(self, memory_item: MemoryItem):
        pass

    @abstractmethod
    def get(self, memory_id) -> Optional[MemoryItem]:
        pass

    @abstractmethod
    def get_first(self, filters: dict = None) -> Optional[MemoryItem]:
        pass

    @abstractmethod
    def total_rounds(self, filters: dict = None) -> int:
        pass

    @abstractmethod
    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        pass

    @abstractmethod
    def get_last_n(self, last_rounds, filters: dict = None) -> list[MemoryItem]:
        pass

    @abstractmethod
    def update(self, memory_item: MemoryItem):
        pass

    @abstractmethod
    def delete(self, memory_id):
        pass

    @abstractmethod
    def history(self, memory_id) -> list[MemoryItem] | None:
        pass


class MemoryBase(ABC):

    @abstractmethod
    def get(self, memory_id) -> Optional[MemoryItem]:
        """Get item in memory by ID.

        Args:
            memory_id (str): ID of the memory to retrieve.

        Returns:
            dict: Retrieved memory.
        """

    @abstractmethod
    def get_all(self, filters: dict = None) -> Optional[list[MemoryItem]]:
        """List all items in memory store.

        Args:
            filters (dict, optional): Filters to apply to the search. Defaults to None.
            - user_id (str, optional): ID of the user to search for. Defaults to None.
            - agent_id (str, optional): ID of the agent to search for. Defaults to None.
            - session_id (str, optional): ID of the session to search for. Defaults to None.

        Returns:
            list: List of all memories.
        """

    @abstractmethod
    def get_last_n(self, last_rounds, filters: dict = None) -> Optional[list[MemoryItem]]:
        """get last_rounds memories.

        Args:
            last_rounds (int): Number of last rounds to retrieve.
            filters (dict, optional): Filters to apply to the search. Defaults to None.
            - user_id (str, optional): ID of the user to search for. Defaults to None.
            - agent_id (str, optional): ID of the agent to search for. Defaults to None.
            - session_id (str, optional): ID of the session to search for. Defaults to None.
        Returns:
            list: List of latest memories.
        """

    @abstractmethod
    def search(self, query, limit=100, filters=None) -> Optional[list[MemoryItem]]:
        """
        Search for memories.
        Hybrid search: Retrieve memories from vector store and memory store.


        Args:
            query (str): Query to search for.
            limit (int, optional): Limit the number of results. Defaults to 100.
            filters (dict, optional): Filters to apply to the search. Defaults to None.
            - user_id (str, optional): ID of the user to search for. Defaults to None.
            - agent_id (str, optional): ID of the agent to search for. Defaults to None.
            - session_id (str, optional): ID of the session to search for. Defaults to None.

        Returns:
            list: List of search results.
        """

    @abstractmethod
    def add(self, memory_item: MemoryItem, filters: dict = None):
        """Add memory in the memory store.

        Step 1: Add memory to memory store
        Step 2: Add memory to vector store

        Args:
            memory_item (MemoryItem): memory item.
            metadata (dict, optional): metadata to add.
             - user_id (str, optional): ID of the user to search for. Defaults to None.
             - agent_id (str, optional): ID of the agent to search for. Defaults to None.
             - session_id (str, optional): ID of the session to search for. Defaults to None.
            tags (list, optional): tags to add.
            memory_type (str, optional): memory type.
            version (int, optional): version of the memory.
        """

    @abstractmethod
    def update(self, memory_item: MemoryItem):
        """Update a memory by ID.

        Args:
            memory_item (MemoryItem): memory item.

        Returns:
            dict: Updated memory.
        """

    @abstractmethod
    async def async_gen_cur_round_summary(self, to_be_summary: MemoryItem, filters: dict, last_rounds: int) -> str:
        """A tool for reducing the context length of the current round.

        Step 1: Retrieve historical conversation content based on filters and last_rounds
        Step 2: Extract current round content and most relevant historical content  
        Step 3: Generate corresponding summary for the current round

        Args:
            to_be_summary (MemoryItem): msg to summary.
            filters (dict): filters to get memory list.
            last_rounds (int): last rounds of memory list.

        Returns:
            str: summary memory.
        """

    @abstractmethod
    async def async_gen_multi_rounds_summary(self, to_be_summary: list[MemoryItem]) -> str:
        """A tool for summarizing the list of memory item.

        Args:
            to_be_summary (list[MemoryItem]): the list of memory item.
        """

    @abstractmethod
    async def async_gen_summary(self, filters: dict, last_rounds: int) -> str:
        """A tool for summarizing the conversation history.

        Step 1: Retrieve historical conversation content based on filters and last_rounds
        Step 2: Generate corresponding summary for conversation history

        Args:
            filters (dict): filters to get memory list.
            last_rounds (int): last rounds of memory list.
        """

    @abstractmethod
    def delete(self, memory_id):
        """Delete a memory by ID.

        Args:
            memory_id (str): ID of the memory to delete.
        """


SUMMARY_PROMPT = """
You are a helpful assistant that summarizes the conversation history.
- Summarize the following text in one clear and concise paragraph, capturing the key ideas without missing critical points. 
- Ensure the summary is easy to understand and avoids excessive detail.

Here are the content: 
{context}
"""


class TriggerConfig(BaseModel):
    """Configuration for memory processing triggers."""

    # Message count based triggers
    message_count_threshold: int = Field(default=10,
                                         description="Trigger processing when message count reaches this threshold")

    # Time based triggers
    enable_time_based_trigger: bool = Field(default=False, description="Enable time-based triggers")
    time_interval_minutes: int = Field(default=60, description="Time interval in minutes for periodic processing")

    # Content importance triggers
    enable_importance_trigger: bool = Field(default=True, description="Enable content importance based triggers")
    importance_keywords: List[str] = Field(default_factory=lambda: ["error", "success", "完成", "失败"],
                                           description="Keywords that indicate important content")

    # Memory type specific triggers
    user_profile_trigger_threshold: int = Field(default=5,
                                                description="Trigger user profile extraction after N user messages")
    agent_experience_trigger_threshold: int = Field(default=8,
                                                    description="Trigger agent experience extraction after N agent actions")


class ExtractionConfig(BaseModel):
    """Configuration for memory extraction processes."""

    # User profile extraction
    enable_user_profile_extraction: bool = Field(default=True, description="Enable user profile extraction")
    user_profile_max_items: int = Field(default=5, description="Maximum user profiles to extract per session")
    user_profile_confidence_threshold: float = Field(default=0.7,
                                                     description="Minimum confidence score for user profile extraction")

    # Agent experience extraction
    enable_agent_experience_extraction: bool = Field(default=True, description="Enable agent experience extraction")
    agent_experience_max_items: int = Field(default=3, description="Maximum agent experiences to extract per session")
    agent_experience_confidence_threshold: float = Field(default=0.8,
                                                         description="Minimum confidence score for agent experience extraction")

    # LLM prompts for extraction
    user_profile_extraction_prompt: str = Field(
        default="""Analyze the following conversation and extract user profile information.
Focus on:
1. Personal information (age, occupation, location, etc.)
2. Preferences and habits
3. Skills and interests
4. Communication style

Format your response as JSON with key-value pairs:
{{"personal_info": {{"age": "25", "occupation": "developer"}}, "preferences": {{"coding_style": "clean code"}}}}

Conversation:
{messages}""",
        description="Prompt template for user profile extraction"
    )

    agent_experience_extraction_prompt: str = Field(
        default="""Analyze the following conversation and extract agent experience patterns.
Focus on:
1. Skills demonstrated by the agent
2. Action sequences that led to success
3. Problem-solving approaches
4. Tool usage patterns

Format your response as JSON with skill-actions pairs:
{{"skill": "code_debugging", "actions": ["analyze_error", "identify_root_cause", "provide_solution", "verify_fix"]}}

Conversation:
{messages}""",
        description="Prompt template for agent experience extraction"
    )


class StorageConfig(BaseModel):
    """Configuration for long-term memory storage."""

    # Storage strategy
    enable_deduplication: bool = Field(default=True, description="Enable deduplication of similar memories")
    similarity_threshold: float = Field(default=0.9, description="Similarity threshold for deduplication")

    # Retention policy
    max_user_profiles_per_user: int = Field(default=50, description="Maximum user profiles to keep per user")
    max_agent_experiences_per_agent: int = Field(default=100, description="Maximum agent experiences to keep per agent")

    # Cleanup policy
    enable_auto_cleanup: bool = Field(default=True, description="Enable automatic cleanup of old memories")
    cleanup_interval_days: int = Field(default=30, description="Cleanup interval in days")
    max_memory_age_days: int = Field(default=365, description="Maximum age of memories before cleanup")


class ProcessingConfig(BaseModel):
    """Configuration for memory processing behavior."""

    # Processing mode
    enable_background_processing: bool = Field(default=True, description="Enable background processing")
    enable_real_time_processing: bool = Field(default=False, description="Enable real-time processing")

    # Performance settings
    max_concurrent_tasks: int = Field(default=3, description="Maximum concurrent processing tasks")
    processing_timeout_seconds: int = Field(default=30, description="Timeout for processing tasks")

    # Retry policy
    max_retry_attempts: int = Field(default=3, description="Maximum retry attempts for failed tasks")
    retry_delay_seconds: int = Field(default=5, description="Delay between retry attempts")

    # Context retrieval
    enable_context_retrieval: bool = Field(default=True,
                                           description="Enable retrieval of relevant context during processing")
    max_context_items: int = Field(default=5, description="Maximum number of context items to retrieve")


class LongTermConfig(BaseModel):
    """
    Configuration for long-term memory processing.
    Provides user-friendly settings for controlling long-term memory behavior.
    """

    model_config = ConfigDict(
        from_attributes=True,
        validate_default=True,
        revalidate_instances='always',
        validate_assignment=True,
        arbitrary_types_allowed=True
    )

    # Sub-configurations
    trigger: TriggerConfig = Field(default_factory=TriggerConfig, description="Trigger configuration")
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig, description="Extraction configuration")
    storage: StorageConfig = Field(default_factory=StorageConfig, description="Storage configuration")
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig, description="Processing configuration")

    # Application-specific settings
    application_id: Optional[str] = Field(default=None, description="Application identifier for multi-tenant support")
    custom_metadata: Dict[str, Any] = Field(default_factory=dict,
                                            description="Custom metadata for application-specific settings")

    def should_trigger_by_message_count(self, message_count: int) -> bool:
        """Check if processing should be triggered by message count."""
        return message_count >= self.trigger.message_count_threshold

    def should_extract_user_profiles(self) -> bool:
        """Check if user profile extraction is enabled."""
        return self.extraction.enable_user_profile_extraction

    def should_extract_agent_experiences(self) -> bool:
        """Check if agent experience extraction is enabled."""
        return self.extraction.enable_agent_experience_extraction

    def get_user_profile_prompt(self, messages: str) -> str:
        """Get formatted user profile extraction prompt."""
        return self.extraction.user_profile_extraction_prompt.format(messages=messages)

    def get_agent_experience_prompt(self, messages: str) -> str:
        """Get formatted agent experience extraction prompt."""
        return self.extraction.agent_experience_extraction_prompt.format(messages=messages)

    def is_background_processing_enabled(self) -> bool:
        """Check if background processing is enabled."""
        return self.processing.enable_background_processing

    def get_max_context_items(self) -> int:
        """Get maximum number of context items to retrieve."""
        return self.processing.max_context_items if self.processing.enable_context_retrieval else 0

    @classmethod
    def create_simple_config(
            cls,
            application_id: str = "SYSTEM",
            message_threshold: int = 10,
            enable_user_profiles: bool = True,
            enable_agent_experiences: bool = True,
            enable_background: bool = True
    ) -> "LongTermConfig":
        """
        Create a simple configuration with common settings.

        Args:
            message_threshold: Number of messages to trigger processing
            enable_user_profiles: Enable user profile extraction
            enable_agent_experiences: Enable agent experience extraction
            enable_background: Enable background processing

        Returns:
            LongTermConfig instance with simple settings
        """
        return cls(
            application_id=application_id,
            trigger=TriggerConfig(message_count_threshold=message_threshold),
            extraction=ExtractionConfig(
                enable_user_profile_extraction=enable_user_profiles,
                enable_agent_experience_extraction=enable_agent_experiences
            ),
            processing=ProcessingConfig(enable_background_processing=enable_background)
        )

    @classmethod
    def create_lightweight_config(cls) -> "LongTermConfig":
        """
        Create a lightweight configuration for minimal resource usage.

        Returns:
            LongTermConfig instance optimized for lightweight usage
        """
        return cls(
            trigger=TriggerConfig(
                message_count_threshold=20,
                enable_time_based_trigger=False,
                enable_importance_trigger=False
            ),
            extraction=ExtractionConfig(
                user_profile_max_items=2,
                agent_experience_max_items=1
            ),
            storage=StorageConfig(
                max_user_profiles_per_user=20,
                max_agent_experiences_per_agent=30
            ),
            processing=ProcessingConfig(
                max_concurrent_tasks=1,
                enable_context_retrieval=False
            )
        )

    @classmethod
    def create_comprehensive_config(cls) -> "LongTermConfig":
        """
        Create a comprehensive configuration with all features enabled.

        Returns:
            LongTermConfig instance with comprehensive settings
        """
        return cls(
            trigger=TriggerConfig(
                message_count_threshold=5,
                enable_time_based_trigger=True,
                time_interval_minutes=30,
                enable_importance_trigger=True
            ),
            extraction=ExtractionConfig(
                user_profile_max_items=10,
                agent_experience_max_items=5,
                user_profile_confidence_threshold=0.6,
                agent_experience_confidence_threshold=0.7
            ),
            storage=StorageConfig(
                max_user_profiles_per_user=100,
                max_agent_experiences_per_agent=200
            ),
            processing=ProcessingConfig(
                max_concurrent_tasks=5,
                enable_real_time_processing=True,
                max_context_items=10
            )
        )

class MemoryConfig(BaseModel):
    """Configuration for procedural memory."""

    model_config = ConfigDict(
        from_attributes=True, validate_default=True, revalidate_instances='always', validate_assignment=True,
        arbitrary_types_allowed=True
    )

    # Memory Config
    provider: Literal['inmemory', 'mem0'] = 'inmemory'
    enable_summary: bool = Field(default=False, description="enable_summary use llm to create summary memory")
    summary_rounds: int = Field(default=5, description="rounds of message msg; when the number of messages is greater than the summary_rounds, the summary will be created")
    summary_single_context_length: int = Field(default=4000, description=" when the content length is greater than the summary_single_context_length, the summary will be created")
    summary_prompt: str = Field(default=SUMMARY_PROMPT, description="summary prompt")

    # Long-term memory config
    enable_long_term: bool = Field(default=False, description="enable_long_term use to store long-term memory")
    long_term_config: Optional[LongTermConfig] = Field(default=None, description="long_term_config")

    # Embedder settings
    embedder_provider: Literal['openai', 'gemini', 'ollama', 'huggingface'] = 'huggingface'
    embedder_model: str = Field(min_length=2, default='all-MiniLM-L6-v2')
    embedder_dims: int = Field(default=384, gt=10, lt=10000)

    # LLM settings - the LLM instance can be passed separately
    llm_provider: Literal['openai', 'langchain'] = 'langchain'
    llm_instance: Optional[Union[LLMModel]] = None

    # Vector store settings
    vector_store_provider: Literal['faiss'] = 'faiss'
    vector_store_base_path: str = Field(default='/tmp/mem0_aworld')

    @property
    def vector_store_path(self) -> str:
        """Returns the full vector store path for the current configuration. e.g. /tmp/mem0_384_faiss"""
        return f'{self.vector_store_base_path}_{self.embedder_dims}_{self.vector_store_provider}'

    @property
    def embedder_config_dict(self) -> dict[str, Any]:
        """Returns the embedder configuration dictionary."""
        return {
            'provider': self.embedder_provider,
            'config': {'model': self.embedder_model, 'embedding_dims': self.embedder_dims},
        }

    @property
    def llm_config_dict(self) -> dict[str, Any]:
        """Returns the LLM configuration dictionary."""
        return {'provider': self.llm_provider, 'config': {'model': self.llm_instance}}

    @property
    def vector_store_config_dict(self) -> dict[str, Any]:
        """Returns the vector store configuration dictionary."""
        return {
            'provider': self.vector_store_provider,
            'config': {
                'embedding_model_dims': self.embedder_dims,
                'path': self.vector_store_path,
            },
        }

    @property
    def full_config_dict(self) -> dict[str, dict[str, Any]]:
        """Returns the complete configuration dictionary for Mem0."""
        return {
            'embedder': self.embedder_config_dict,
            'llm': self.llm_config_dict,
            'vector_store': self.vector_store_config_dict,
        }
