import asyncio
import logging
import time
from workspacex.utils.timeit import timeit
from typing import List
import requests
import aiohttp
from workspacex.embedding.base import Embeddings, EmbeddingsConfig, EmbeddingsResult, EmbeddingsMetadata
from workspacex.artifact import Artifact
from workspacex.embedding.embeddings_base import EmbeddingsBase


class OllamaEmbeddings(EmbeddingsBase):
    """
    Embedding implementation using Ollama HTTP API.
    """
    def __init__(self, config: EmbeddingsConfig):
        """
        Initialize OllamaEmbeddings with configuration.
        Args:
            config (EmbeddingsConfig): Configuration for embedding model and API.
        """
        super().__init__(config)
    
    @timeit(logging.info, "Ollama embedding query completed in {elapsed_time:.3f} seconds")
    def embed_query(self, text: str) -> List[float]:
        """
        Embed a query string using Ollama HTTP API.
        Args:
            text (str): Text to embed.
        Returns:
            List[float]: Embedding vector.
        """
        url = self.config.base_url.rstrip('/') + "/api/embed"
        payload = {
            "model": self.config.model_name,
            "input": text
        }
        try:
            response = requests.post(url, json=payload, timeout=self.config.timeout)
            response.raise_for_status()
            data = response.json()
            # Ollama returns {"embedding": [...], ...}
            logging.debug(f"Ollama embedding response: {data}")
            return self.resolve_embedding(data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise RuntimeError(f"Ollama embedding API error: {e}")

    @timeit(logging.info, "Ollama async embedding query completed in {elapsed_time:.3f} seconds")
    async def async_embed_query(self, text: str) -> List[float]:
        """
        Asynchronously embed a query string using Ollama HTTP API.
        Args:
            text (str): Text to embed.
        Returns:
            List[float]: Embedding vector.
        """
        url = self.config.base_url.rstrip('/') + "/api/embed"
        payload = {
            "model": self.config.model_name,
            "input": text
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout)) as session:
                async with session.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    return self.resolve_embedding(data)
        except Exception as e:
            raise RuntimeError(f"Ollama async embedding API error: {e}")
      
    @staticmethod
    def resolve_embedding(data: dict) -> List[float]:
        """
        Resolve the embedding from the response data.
        Args:
            data (dict): Response data from Ollama API.
        Returns:
            List[float]: Embedding vector.
        """
        if "embeddings" in data and len(data["embeddings"]) > 0:
            return data["embeddings"][0]
        else:
            return None