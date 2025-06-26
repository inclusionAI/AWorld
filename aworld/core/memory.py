import datetime
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Any, Literal, Union

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
