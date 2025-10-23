import uuid
from abc import ABC, abstractmethod
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict

from aworld.output import Artifact


class ArtifactStats(BaseModel):
    artifact_id: str = Field(default="", description="artifact ID")
    chunk_count: Optional[int]= Field(default="", description="total chunk count")

class ChunkIndex(BaseModel):
    chunk_index: int = Field(default=0, description="Chunk index")
    artifact_id: str = Field(default="", description="Origin artifact ID")
    chunk_desc: Optional[str]= Field(default="", description="chunk desc for llm")

class ChunkMetadata(BaseModel):
    chunk_index: int = Field(default=0, description="Chunk index")
    chunk_size: int = Field(default=0, description="Chunk size")
    chunk_overlap: int = Field(default=0, description="Chunk overlap")
    artifact_id: str = Field(default="", description="Origin artifact ID")
    artifact_type: str = Field(default="", description="Origin artifact type")
    workspace_id: str = Field(default="", description="Origin workspace_id")
    chunk_desc: str= Field(default="", description="chunk desc for llm")
    biz_id: str = Field(default="", description="Origin biz_id")

    model_config = ConfigDict(extra="allow")



class Chunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Chunk ID")
    chunk_metadata: ChunkMetadata = Field(default=ChunkMetadata(), description="Chunk metadata")
    content: str = Field(default="", description="Chunk content")

    @property
    def parent_artifact_id(self) -> str:
        return self.chunk_metadata.parent_artifact_id

    @property
    def artifact_id(self) -> str:
        return self.chunk_metadata.artifact_id

    @property
    def artifact_type(self) -> str:
        return self.chunk_metadata.artifact_type

    @property
    def chunk_file_name(self) -> str:
        return f"{self.artifact_id}_chunk_{self.chunk_metadata.chunk_index}.json"

    def pre_n_chunk_file_name(self, pre_n) -> str:
        return f"{self.artifact_id}_chunk_{self.chunk_metadata.chunk_index - pre_n}.json"

    def next_n_chunk_file_name(self, next_n) -> str:
        return f"{self.artifact_id}_chunk_{self.chunk_metadata.chunk_index + next_n}.json"


class ChunkConfig(BaseModel):
    provider: str = Field(default="default", description="Text splitter")
    chunk_size: int = Field(default=512, description="Chunk size")
    chunk_overlap: int = Field(default=64, description="Chunk overlap")
    chunk_separator: str = Field(default="\n", description="Chunk separator")


class Chunker(ABC):
    """Chunk service interface"""

    @abstractmethod
    async def chunk(self, artifact: Artifact) -> list[Chunk]:
        pass


class ChunkerBase(Chunker, BaseModel):
    config: ChunkConfig = Field(default=ChunkConfig(), description="Chunk config")

    """Chunker base class"""

    async def chunk(self, artifact: Artifact) -> list[Chunk]:
        pass

    def _create_chunks(self, texts: List[str], artifact: Artifact) -> List[Chunk]:
        chunks: List[Chunk] = []
        for i, text in enumerate(texts):
            chunk = Chunk(
                chunk_id=f"{artifact.artifact_id}_chunk_{i}",
                content=text,
                chunk_metadata=ChunkMetadata(
                    chunk_index=i,
                    chunk_size=len(text),
                    chunk_overlap=self.config.chunk_overlap,
                    artifact_id=artifact.artifact_id,
                    artifact_type=artifact.artifact_type.value,
                    chunk_desc=text[:100], # TODO chunk summary
                    **artifact.metadata
                )
            )
            chunks.append(chunk)
        return chunks


class ChunkerFactory:
    """Chunker factory"""

    @staticmethod
    def get_chunker(config: ChunkConfig) -> Chunker:
        if config.provider == "default":
            from .contextualized import ContextualizedChunker
            return ContextualizedChunker(config)
        if config.provider == "smart":
            from .smart_chunker import SmartChunker
            return SmartChunker(config=config)
        else:
            raise ValueError(f"Unsupported text splitter: {config.provider}")
