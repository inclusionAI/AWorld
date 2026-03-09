from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field

from aworld.core.memory import EmbeddingsConfig


class EmbeddingsMetadata(BaseModel):
    memory_id: str = Field(..., description="memory_id")
    agent_id: Optional[str] = Field(default=None, description="agent_id")
    session_id: Optional[str] = Field(default=None, description="session_id")
    task_id: Optional[str] = Field(default=None, description="task_id")
    user_id: Optional[str] = Field(default=None, description="user_id")
    application_id: Optional[str] = Field(default=None, description="application_id")
    memory_type: str = Field(..., description="memory_type")
    embedding_model: str = Field(..., description="Embedding model")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Created at")
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Updated at")

    model_config = ConfigDict(extra="allow")


class EmbeddingsResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="ID")
    embedding: Optional[list[float]] = Field(default=None, description="Embedding")
    content: str = Field(..., description="Content")
    metadata: Optional[EmbeddingsMetadata] = Field(..., description="Metadata")
    score: Optional[float] = Field(default=None, description="Retrieved relevance score")


class EmbeddingsResults(BaseModel):
    docs: Optional[List[EmbeddingsResult]]
    retrieved_at: int = Field(..., description="Retrieved at")


class Embeddings(ABC):
    """Interface for embedding models.
    Embeddings are used to convert artifacts and queries into a vector space.
    """

    def __init__(self, config: EmbeddingsConfig):
        """
        Initialize EmbeddingsBase with configuration.
        Args:
            config (EmbeddingsConfig): Configuration for embedding model and API.
        """
        self.config = config

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Abstract method to embed a query string.

        Args:
            text (str): Text to embed.

        Returns:
            List[float]: Embedding vector.
        """

    @abstractmethod
    async def async_embed_query(self, text: str) -> list[float]:
        """Abstract method to asynchronously embed a query string.

        Args:
            text (str): Text to embed.

        Returns:
            List[float]: Embedding vector.
        """
