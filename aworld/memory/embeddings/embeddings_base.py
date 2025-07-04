import asyncio
import time
from abc import abstractmethod
from typing import List

from workspacex.artifact import Artifact
from workspacex.embedding.base import Embeddings, EmbeddingsConfig, EmbeddingsResult, EmbeddingsMetadata


class EmbeddingsBase(Embeddings):
    """
    Base class for embedding implementations that contains common functionality.
    """
    def __init__(self, config: EmbeddingsConfig):
        """
        Initialize EmbeddingsBase with configuration.
        Args:
            config (EmbeddingsConfig): Configuration for embedding model and API.
        """
        self.config = config

    def embed_artifacts(self, artifacts: List[Artifact]) -> List[EmbeddingsResult]:
        """
        Embed a list of artifacts.
        Args:
            artifacts (List[Artifact]): List of artifacts to embed.
        Returns:
            List[EmbeddingsResult]: List of embedding results.
        """
        results = []
        for artifact in artifacts:
            result = self._embed_artifact(artifact)
            results.append(result)
        return results
    
    def embed_artifact(self, artifact: Artifact) -> EmbeddingsResult:
        """
        Embed a single artifact.
        Args:
            artifact (Artifact): Artifact to embed.
        Returns:
            EmbeddingsResult: Embedding result for the artifact.
        """
        return self._embed_artifact(artifact)

    def _embed_artifact(self, artifact: Artifact) -> EmbeddingsResult:
        """
        Internal method to embed a single artifact.
        Args:
            artifact (Artifact): Artifact to embed.
        Returns:
            EmbeddingsResult: Embedding result for the artifact.
        """
        embedding = self.embed_query(artifact.get_embedding_text())
        now = int(time.time())
        metadata = EmbeddingsMetadata(
            artifact_id=artifact.artifact_id,
            embedding_model=self.config.model_name,
            created_at=now,
            updated_at=now,
            artifact_type=artifact.artifact_type.name,
            parent_id=artifact.parent_id
        )
        return EmbeddingsResult(
            id=artifact.artifact_id,
            embedding=embedding,
            content=artifact.get_embedding_text(),
            metadata=metadata
        )

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

    async def async_embed_artifacts(self, artifacts: List[Artifact]) -> List[EmbeddingsResult]:
        """
        Asynchronously embed a list of artifacts.
        Args:
            artifacts (List[Artifact]): List of artifacts to embed.
        Returns:
            List[EmbeddingsResult]: List of embedding results.
        """
        return await asyncio.gather(*[self._async_embed_artifact(artifact) for artifact in artifacts])
    
    async def async_embed_artifact(self, artifact: Artifact) -> EmbeddingsResult:
        """
        Asynchronously embed a single artifact.
        Args:
            artifact (Artifact): Artifact to embed.
        Returns:
            EmbeddingsResult: Embedding result for the artifact.
        """
        return await self._async_embed_artifact(artifact)

    async def _async_embed_artifact(self, artifact: Artifact) -> EmbeddingsResult:
        """
        Internal method to asynchronously embed a single artifact.
        Args:
            artifact (Artifact): Artifact to embed.
        Returns:
            EmbeddingsResult: Embedding result for the artifact.
        """
        embedding = await self.async_embed_query(artifact.get_embedding_text())
        now = int(time.time())
        metadata = EmbeddingsMetadata(
            artifact_id=artifact.artifact_id,
            embedding_model=self.config.model_name,
            created_at=now,
            updated_at=now,
            artifact_type=artifact.artifact_type.name,
            parent_id=artifact.parent_id
        )
        return EmbeddingsResult(
            id=artifact.artifact_id,
            embedding=embedding,
            content=artifact.get_embedding_text(),
            metadata=metadata
        )

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