import os
from abc import ABC
from typing import Optional

from pydantic import BaseModel

from .chunker import ChunkIndex, Chunk, ArtifactStats, ChunkConfig, ChunkStoreConfig
from .embeddings.base import SearchResults
from .reranker.factory import RerankConfig
from .index import RetrievalPluginConfig
from aworld.output import Artifact

class RetrieverConfig(BaseModel):
    """retriever 配置"""
    type: str = "amni"
    chunk_config: Optional[ChunkConfig] = None
    chunk_store_config: Optional[ChunkStoreConfig] = None
    index_plugin_configs: Optional[list[RetrievalPluginConfig]]  = None
    reranker_config: Optional[RerankConfig]
    config: Optional[dict] = None


class BaseRetriever(ABC):

    def __init__(self, config: RetrieverConfig):
        self.config = config

    @classmethod
    def create(cls, rag_config: RetrieverConfig, **kwargs) -> 'BaseRetriever':
        """Async factory method to create and initialize AmniLightRag instance"""
        pass

    async def async_insert(self, workspace_id: str, artifact: Artifact, index=True) -> Optional[list[Chunk]]:
        """Async insert document into RAG"""
        pass

    async def async_search(self, workspace_id: str, user_query: str, search_filter: dict = None, top_k: int = None,
                           **kwargs) -> Optional[SearchResults]:
        """Async query RAG"""
        pass

    async def async_search_chunk_index(self, workspace_id: str, user_query: str, search_filter: dict = None,
                                       top_k: int = None) -> Optional[list[ChunkIndex]]:
        pass

    async def async_query_chunk_index(self, workspace_id: str, search_filter: dict = None) -> Optional[list[ChunkIndex]]:
        pass

    async def async_query_artifact_index(self, workspace_id: str, search_filter: dict = None) -> Optional[
        list[ArtifactStats]]:
        pass

    async def async_query_chunk(self, workspace_id: str, artifact_id: str, chunk_index: int) -> Optional[Chunk]:
        """
        query chunk by artifact ID and chunk index
        Args:
            workspace_id (str): workspace ID
            artifact_id (str): artifact ID
            chunk_index (int): chunk index

        Returns:
            Optional[Chunk]: chunk
        """
        pass

    async def get_artifact_chunks_by_range(self,workspace_id: str, artifact_id: str, start_index: int, end_index: int) -> list[Chunk]:
        """
        obtain chunks by range using efficient database range query
        Args:
            workspace_id (str): workspace ID
            artifact_id (str): artifact ID
            start_index (int): start chunk index (include)
            end_index (int): end chunk index (not include)
        Returns:
            list[Chunk]: specified range chunks list
        """
        pass


RETRIEVER_HOLDER = {}

class RetrieverFactory:
    """RagFactory"""

    @staticmethod
    def get_retriever(config: RetrieverConfig) -> BaseRetriever:
        """Get RAG instance"""
        if config.type == "amni":
            from .amniretriever import AmniRetriever
            return AmniRetriever.create(config)
        else:
            raise ValueError(f"Invalid RAG type: {config.type}")

    @classmethod
    def init(cls):
        if RETRIEVER_HOLDER.get("instance"):
            return
        retriever_config = RetrieverConfig(
            type=os.environ.get("AMNI_RAG_TYPE", "amni"),
            chunk_config=ChunkConfig(
                provider=os.getenv("CHUNK_PROVIDER"),
                chunk_size=int(os.getenv("CHUNK_SIZE", "512")),
                chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "200")),
                chunk_separator=os.getenv("CHUNK_SEPARATOR"),
            ),
            chunk_store_config=ChunkStoreConfig(
                provider=os.getenv("CHUNK_STORE_TYPE", "sqlite"),
                config={
                    "db_path": os.getenv("DB_PATH", "./data/amni_context.db"),
                    "table_name": os.getenv("CHUNK_STORE_TABLE_NAME", "chunks"),
                }
            ),
            index_plugin_configs=[
                RetrievalPluginConfig(type="semantic", config={
                    "wait_insert": False
                }),
                RetrievalPluginConfig(type="full_text", config={
                    "wait_insert": True
                })
            ],
            reranker_config=RerankConfig(
                provider=os.getenv("RERANKER_PROVIDER", "http"),
                config={
                    "base_url": os.getenv("RERANKER_BASE_URL"),
                    "api_key": os.getenv("RERANKER_API_KEY"),
                    "model_name": os.getenv("RERANKER_MODEL_NAME"),
                }
            )
        )
        RETRIEVER_HOLDER["instance"] = RetrieverFactory.get_retriever(
            config=retriever_config
        )

    @classmethod
    def instance(cls) -> BaseRetriever:
        """Get RAG instance"""
        if not RETRIEVER_HOLDER.get("instance"):
            cls.init()
        return RETRIEVER_HOLDER["instance"]