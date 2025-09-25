from typing import Any, List

from openai import OpenAI

from aworld.core.memory import EmbeddingsConfig
from aworld.logs.util import logger
from aworld.memory.embeddings.base import EmbeddingsBase


class OpenAICompatibleEmbeddings(EmbeddingsBase):
    """
    OpenAI compatible embeddings using OpenAI-compatible HTTP API.

    - text-embedding-v4: [2048、1536、1024（默认）、768、512、256、128、64]
    - text-embedding-v3: [1024(默认)、512、256、128、64]
    - text-embedding-v2: [1536]
    - text-embedding-v1: [1536]
    """

    def __init__(self, config: EmbeddingsConfig):
        """
        Initialize OpenAICompatibleEmbeddings with configuration.
        Args:
            config (EmbeddingsConfig): Configuration for embedding model and API.
        """
        super().__init__(config)
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)


    def embed_query(self, text: str) -> List[float]:
        """
        Embed a query string using OpenAI-compatible HTTP API.
        Args:
            text (str): Text to embed.
        Returns:
            List[float]: Embedding vector.
        """
        try:
            response = self.client.embeddings.create(
                model=self.config.model_name,
                input=text,
                dimensions=self.config.dimensions)
            data = response.data
            logger.debug(f"OpenAI embedding response: {data}")
            return self.resolve_embedding(data)
        except Exception as e:
            raise RuntimeError(f"OpenAI embedding API error: {e}")

    async def async_embed_query(self, text: str) -> List[float]:
        """
        Asynchronously embed a query string using OpenAI-compatible HTTP API.
        Args:
            text (str): Text to embed.
        Returns:
            List[float]: Embedding vector.
        """
        try:
            response = self.client.embeddings.create(
                model=self.config.model_name,
                input=text,
                dimensions=self.config.dimensions)
            data = response.data
            logger.debug(f"OpenAI embedding response: {data}")
            return self.resolve_embedding(data)
        except Exception as e:
            raise RuntimeError(f"OpenAI async embedding API error: {e}")

    @staticmethod
    def resolve_embedding(data: list[Any]) -> List[float]:
        """
        Resolve the embedding from the response data (OpenAI format).
        Args:
            data (dict): Response data from OpenAI API.
        Returns:
            List[float]: Embedding vector.
        """
        return data[0].embedding
