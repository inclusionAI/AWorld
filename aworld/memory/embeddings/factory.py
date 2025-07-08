from typing import Optional

from aworld.core.memory import EmbeddingsConfig
from aworld.memory.embeddings.base import Embeddings
from aworld.memory.embeddings.ollama import OllamaEmbeddings
from aworld.memory.embeddings.openai_compatible import OpenAICompatibleEmbeddings


class EmbedderFactory:

    @staticmethod
    def get_embedder(config: EmbeddingsConfig) -> Optional[Embeddings]:
        if not config:
            return None
        if config.provider == "openai":
            return OpenAICompatibleEmbeddings(config)
        elif config.provider == "ollama":
            return OllamaEmbeddings(config)
        else:
            raise ValueError(f"Unsupported provider: {config.provider}")