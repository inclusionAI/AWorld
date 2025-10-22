import asyncio
import logging
import os
import traceback
from typing import Optional, List

from .retrieval.chunker import Chunk, ChunkIndex, ArtifactStats
from .retrieval.embeddings.base import SearchResults
from .retrieval.base import RetrieverFactory
from aworld.output import WorkSpace, Artifact, WorkspaceObserver, ArtifactRepository


class ApplicationWorkspace(WorkSpace):

    def __init__(self, workspace_id: Optional[str] = None, name: Optional[str] = None,
                 storage_path: Optional[str] = None, observers: Optional[List[WorkspaceObserver]] = None,
                 use_default_observer: bool = True, clear_existing: bool = False,
                 repository: Optional[ArtifactRepository] = None, **kwargs):
        super().__init__(workspace_id, name, storage_path, observers, use_default_observer, clear_existing, repository,
                         **kwargs)
        self._retriever = RetrieverFactory.instance()

    @property
    def retriever(self):
        return self._retriever

    @property
    def vector_collection(self):
        return f"ws_{self.workspace_id}"

    async def add_artifact(self, artifact: Artifact, index: bool = False, **kwargs) -> None:
        try:
            self._load_workspace_data()
            await self.retriever.async_insert(self.workspace_id, artifact, index)
            await super().add_artifact(artifact)
            logging.debug(f"add_artifact#{artifact.artifact_id} finished")
        except Exception as err:
            logging.warning(f"add_artifact Error is {err}, trace is {traceback.format_exc()}")

    async def read_artifact_content(self, artifact_id: str, start_line: int = 0, end_line: int = 10) -> Optional[str]:
        return self.get_artifact(artifact_id).content

    async def query_artifacts(self, search_filter: dict =None) -> Optional[list[Artifact]]:
        results = []
        if not search_filter:
            return None

        for artifact in self.artifacts:
            if search_filter:
                if all(key in artifact.metadata and artifact.metadata[key] == value for key, value in search_filter.items()):
                    results.append(artifact)
            else:
                results.append(artifact)
        return results


    """  Artifact Chunk CRUD  """

    async def get_artifact_chunks_index(self, search_filter: dict =None) -> Optional[list[ChunkIndex]]:
        return await self.retriever.async_query_chunk_index(self.workspace_id, search_filter)

    async def async_query_artifact_index(self, search_filter: dict =None) -> Optional[list[ArtifactStats]]:
        return await self.retriever.async_query_artifact_index(self.workspace_id, search_filter)

    async def get_artifact_content(self, artifact_id: str) -> Optional[Artifact]:
        artifact = self.get_artifact(artifact_id)
        if artifact:
            return artifact.content
        else:
            return None

    async def search_artifact_chunks_index(self, user_query: str, search_filter: dict =None, top_k: int = None) -> Optional[list[ChunkIndex]]:
        return await self.retriever.async_search_chunk_index(self.workspace_id, user_query, search_filter, top_k)

    async def search_artifact_chunks(self, user_query: str, search_filter: dict =None, top_k: int = None)->Optional[SearchResults]:
        if search_filter is None:
            search_filter = {}
        result =  await self.retriever.async_search(self.workspace_id, user_query, search_filter, top_k)
        return result

    async def get_artifact_chunk(self, artifact_id: str, chunk_index: int) -> Optional[Chunk]:
        return await self.retriever.async_query_chunk(self.workspace_id, artifact_id, chunk_index)

    async def get_artifact_chunks_by_range(self, artifact_id: str, start_index: int, end_index: int) -> list[Chunk]:
        """
        obtain chunks by range using efficient database range query
        
        Args:
            artifact_id (str): artifact ID
            start_index (int): start chunk index (include)
            end_index (int): end chunk index (not include)
            
        Returns:
            list[Chunk]: specified range chunks list
        """
        if start_index >= end_index:
            return []
        
        # 🚀 Use efficient range query directly from storage layer
        return await self.retriever.get_artifact_chunks_by_range(
            self.workspace_id,artifact_id, start_index, end_index
        )

    async def get_artifact_chunk_indices_by_range(self, artifact_id: str, start_index: int, end_index: int) -> list[ChunkIndex]:
        """
        obtain chunk indices by range using batch query for better performance
        
        Args:
            artifact_id (str): artifact ID
            start_index (int): start chunk index (include)
            end_index (int): end chunk index (not include)
            
        Returns:
            list[ChunkIndex]: specified range chunk indices list
        """
        if start_index >= end_index:
            return []
        
        # 🚀 Use batch query to get chunks first, then extract indices
        chunks = await self.get_artifact_chunks_by_range(artifact_id, start_index, end_index)
        
        # Convert chunks to ChunkIndex objects
        chunk_indices = []
        for chunk in chunks:
            chunk_indices.append(ChunkIndex(
                chunk_index=chunk.chunk_metadata.chunk_index,
                artifact_id=chunk.chunk_metadata.artifact_id,
                chunk_desc=chunk.chunk_metadata.chunk_desc
            ))
        
        return chunk_indices

    async def get_artifact_chunks_head_and_tail(self, artifact_id: str, top_k: int) -> tuple[list[Chunk], list[Chunk]]:
        """
        obtain head and tail chunks
        
        Args:
            artifact_id (str): artifact ID
            top_k (int): head and tail each take how many chunks
            
        Returns:
            tuple[list[Chunk], list[Chunk]]: (head chunks, tail chunks)
        """
        # first get artifact total chunk number
        artifact_stats = await self.retriever.async_query_artifact_index(self.workspace_id, {"artifact_id": artifact_id})
        if not artifact_stats:
            return [], []
        
        total_chunks = artifact_stats[0].chunk_count if artifact_stats else 0
        if total_chunks == 0:
            return [], []
        
        # calculate head and tail range
        head_end = min(top_k, total_chunks)
        tail_start = max(0, total_chunks - top_k)
        
        # concurrent get head and tail chunks
        head_chunks_task = self.get_artifact_chunks_by_range(artifact_id, 0, head_end)
        tail_chunks_task = self.get_artifact_chunks_by_range(artifact_id, tail_start, total_chunks)
        
        head_chunks, tail_chunks = await asyncio.gather(head_chunks_task, tail_chunks_task)
        
        return head_chunks, tail_chunks

    async def get_artifact_chunk_indices_middle_range(self, artifact_id: str, top_k: int) -> list[ChunkIndex]:
        """
        obtain middle range chunk indices (from topk to 2*topk)
        
        Args:
            artifact_id (str): artifact ID
            top_k (int): start offset
            
        Returns:
            list[ChunkIndex]: middle range chunk indices list
        """
        # first get artifact total chunk number
        artifact_stats = await self.retriever.async_query_artifact_index(self.workspace_id, {"artifact_id": artifact_id})
        if not artifact_stats:
            return []
        
        total_chunks = artifact_stats[0].chunk_count if artifact_stats else 0
        if total_chunks == 0:
            return []
        
        # calculate middle range
        start_index = top_k
        end_index = min(2 * top_k, total_chunks)
        
        if start_index >= total_chunks:
            return []
        
        return await self.get_artifact_chunk_indices_by_range(artifact_id, start_index, end_index)


async def load_workspace(workspace_id: str, workspace_type: str = None, workspace_parent_path: str = None) -> Optional[
    ApplicationWorkspace]:
    """
    This function is used to get the workspace by its id.
    It first checks the workspace type and then creates the workspace accordingly.
    If the workspace type is not valid, it raises an HTTPException.
    """
    if workspace_id is None:
        raise RuntimeError("workspace_id is None")
    if workspace_type is None:
        workspace_type = os.environ.get("WORKSPACE_TYPE", "local")
    if workspace_parent_path is None:
        workspace_parent_path = os.environ.get("WORKSPACE_PATH", "./data/workspaces")

    if workspace_type == "local":
        workspace = ApplicationWorkspace.from_local_storages(
            workspace_id,
            storage_path=os.path.join(workspace_parent_path,workspace_id),
        )
    elif workspace_type == "oss":
        workspace = ApplicationWorkspace.from_oss_storages(
            workspace_id,
            storage_path=os.path.join(workspace_parent_path, workspace_id),
        )
    else:
        raise RuntimeError("Invalid workspace type")
    return workspace


class Workspaces:
    """
    This class is used to get the workspace by its id.
    """
    async def get_session_workspace(self, session_id: str) -> ApplicationWorkspace:
        return await load_workspace(session_id)


workspace_repo = Workspaces()

