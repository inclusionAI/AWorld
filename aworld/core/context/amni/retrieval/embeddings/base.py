from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field

class AmniEmbeddingsConfig(BaseModel):
    provider: str = "openai"
    api_key: str = ""
    model_name: str = "text-embedding-3-small"
    base_url: str = "https://api.openai.com/v1"
    context_length: int = 8191
    dimensions: int = 512
    timeout: int = 60

class EmbeddingsMetadata(BaseModel):
    artifact_id: str = Field(default="", description="Origin artifact ID")
    artifact_type: str = Field(default="", description="Origin artifact type")
    embedding_model: Optional[str] = Field(default=None, description="Embedding model")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Created at")
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Updated at")
    chunk_index: int = Field(default=0, description="Chunk index")
    chunk_size: int = Field(default=0, description="Chunk size")
    chunk_overlap: int = Field(default=0, description="Chunk overlap")
    chunk_desc: str = Field(default="", description="summary of chunk")

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

class SearchResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="ID")
    content: str = Field(..., description="Content")
    metadata: Optional[EmbeddingsMetadata] = Field(..., description="Metadata")
    score: Optional[float] = Field(default=None, description="Retrieved relevance score")


class SearchResults(BaseModel):
    docs: Optional[List[SearchResult]]
    search_at: int = Field(..., description="Retrieved at")


class Embeddings(ABC):
    """Interface for embedding models.
    Embeddings are used to convert artifacts and queries into a vector space.
    """
    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed query text."""
        raise NotImplementedError

    async def async_embed_query(self, text: str) -> list[float]:
        """Asynchronous Embed query text."""
        raise NotImplementedError


class EmbeddingsBase(Embeddings):
    """
    Base class for embedding implementations that contains common functionality.
    """

    def __init__(self, config: AmniEmbeddingsConfig):
        """
        Initialize EmbeddingsBase with configuration.
        Args:
            config (EmbeddingsConfig): Configuration for embedding model and API.
        """
        self.config = config


    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """
        Abstract method to embed a query string.
        Args:
            text (str): Text to embed.
        Returns:
            List[float]: Embedding vector.
        """
        pass

    @abstractmethod
    async def async_embed_query(self, text: str) -> List[float]:
        """
        Abstract method to asynchronously embed a query string.
        Args:
            text (str): Text to embed.
        Returns:
            List[float]: Embedding vector.
        """
        pass

class EmbeddingFactory:

    @staticmethod
    def get_embedder(embedding_config: AmniEmbeddingsConfig) -> Embeddings:
        if embedding_config.provider == "openai":
            from .openai_compatible import OpenAICompatibleEmbeddings
            return OpenAICompatibleEmbeddings(embedding_config)
        else:
            raise ValueError(f"Unsupported embedding provider: {embedding_config.provider}")