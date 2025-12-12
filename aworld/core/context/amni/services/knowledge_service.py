# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Knowledge Service - Manages knowledge artifacts and search operations.

This service abstracts knowledge management operations from ApplicationContext,
providing a clean interface for adding, updating, retrieving, and searching knowledge.
"""
import abc
from typing import Optional, List, Dict, Any

from aworld.output import Artifact
from aworld.core.context.amni.retrieval.chunker import Chunk
from aworld.core.context.amni.retrieval.embeddings import SearchResults
from aworld.core.context.amni.state.common import WorkingState


class IKnowledgeService(abc.ABC):
    """Interface for knowledge management operations."""
    
    @abc.abstractmethod
    async def add_knowledge(self, knowledge: Artifact, namespace: str = "default", index: bool = True) -> None:
        """
        Add a single knowledge artifact.
        
        Args:
            knowledge: The artifact to add
            namespace: Namespace for storage
            index: Whether to build index for the artifact
        """
        pass
    
    @abc.abstractmethod
    async def add_knowledge_list(self, knowledge_list: List[Artifact], namespace: str = "default", build_index: bool = True) -> None:
        """
        Add multiple knowledge artifacts in batch.
        
        Args:
            knowledge_list: List of artifacts to add
            namespace: Namespace for storage
            build_index: Whether to build index for artifacts
        """
        pass
    
    @abc.abstractmethod
    async def update_knowledge(self, knowledge: Artifact, namespace: str = "default") -> None:
        """
        Update an existing knowledge artifact.
        
        Args:
            knowledge: The artifact to update
            namespace: Namespace for storage
        """
        pass
    
    @abc.abstractmethod
    async def delete_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> None:
        """
        Delete a knowledge artifact by ID.
        
        Args:
            knowledge_id: ID of the artifact to delete
            namespace: Namespace for storage
        """
        pass
    
    @abc.abstractmethod
    async def get_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> Optional[Artifact]:
        """
        Get a knowledge artifact by ID.
        
        Args:
            knowledge_id: ID of the artifact
            namespace: Namespace for storage
            
        Returns:
            Artifact if found, None otherwise
        """
        pass
    
    @abc.abstractmethod
    async def get_knowledge_chunk(self, knowledge_id: str, chunk_index: int) -> Optional[Chunk]:
        """
        Get a specific chunk from a knowledge artifact.
        
        Args:
            knowledge_id: ID of the artifact
            chunk_index: Index of the chunk
            
        Returns:
            Chunk if found, None otherwise
        """
        pass
    
    @abc.abstractmethod
    async def search_knowledge(self, user_query: str, top_k: int = None, search_filter: Dict[str, Any] = None, namespace: str = "default") -> Optional[SearchResults]:
        """
        Search knowledge using semantic search.
        
        Args:
            user_query: Search query string
            top_k: Number of results to return
            search_filter: Additional search filters
            namespace: Namespace for search
            
        Returns:
            SearchResults if workspace is available, None otherwise
        """
        pass
    
    @abc.abstractmethod
    async def offload_by_workspace(self, artifacts: List[Artifact], namespace: str = "default", biz_id: str = None) -> str:
        """
        Offload artifacts to workspace (context offloading).
        
        Args:
            artifacts: List of artifacts to offload
            namespace: Namespace for storage
            biz_id: Business ID for grouping artifacts
            
        Returns:
            Offload context string
        """
        pass
    
    @abc.abstractmethod
    async def load_context_by_workspace(self, search_filter: Dict[str, Any] = None, namespace: str = "default", 
                                        top_k: int = 20, load_content: bool = True, load_index: bool = True, 
                                        search_by_index: bool = True) -> str:
        """
        Load knowledge context from workspace.
        
        Args:
            search_filter: Search filter dictionary
            namespace: Namespace for search
            top_k: Number of results to return
            load_content: Whether to load chunk content
            load_index: Whether to load chunk indices
            search_by_index: Whether to search by index
            
        Returns:
            Formatted knowledge context string
        """
        pass
    
    @abc.abstractmethod
    async def build_knowledge_context(self, namespace: str = "default", search_filter: Dict[str, Any] = None, top_k: int = 20) -> str:
        """
        Build knowledge context string.
        
        Args:
            namespace: Namespace for search
            search_filter: Search filter dictionary
            top_k: Number of results to return
            
        Returns:
            Formatted knowledge context string
        """
        pass
    
    @abc.abstractmethod
    async def get_todo_info(self) -> str:
        """
        Get todo information from workspace.
        
        Returns:
            Formatted todo information string, or "Todo is Empty" if no todo found
        """
        pass
    
    @abc.abstractmethod
    async def get_actions_info(self, namespace: str = "default") -> str:
        """
        Get actions information from workspace.
        
        Args:
            namespace: Namespace for retrieval
            
        Returns:
            Formatted actions information string
        """
        pass


class KnowledgeService(IKnowledgeService):
    """
    Knowledge Service implementation.
    
    Manages knowledge artifacts stored in working state and workspace.
    Provides operations for adding, updating, retrieving, and searching knowledge.
    """
    
    def __init__(self, context):
        """
        Initialize KnowledgeService with ApplicationContext.
        
        Args:
            context: ApplicationContext instance that provides workspace and working state access
        """
        self._context = context
    
    def _get_working_state(self, namespace: str = "default") -> Optional[WorkingState]:
        """Get working state for the given namespace."""
        return self._context._get_working_state(namespace)
    
    async def add_knowledge(self, knowledge: Artifact, namespace: str = "default", index: bool = True) -> None:
        """Add a single knowledge artifact."""
        from aworld.logs.util import logger
        
        logger.debug(f"add knowledge #{knowledge.artifact_id} start")
        self._get_working_state(namespace).save_knowledge(knowledge)
        
        # Workspace is optional, only add if initialized
        if self._context._workspace:
            await self._context._workspace.add_artifact(knowledge, index=index)
            logger.info(f"add knowledge to#{knowledge.artifact_id} workspace finished")
        logger.debug(f"add knowledge #{knowledge.artifact_id} finished")
    
    async def add_knowledge_list(self, knowledge_list: List[Artifact], namespace: str = "default", build_index: bool = True) -> None:
        """Add multiple knowledge artifacts in batch."""
        import asyncio
        import time
        from aworld.logs.util import logger
        
        logger.debug(f"add_knowledge_list start")
        
        if knowledge_list:
            logger.debug(f"🧠 Start adding knowledge in batch, total {len(knowledge_list)} items")
            start_time = time.time()
            
            # Batch process all knowledge items concurrently
            await asyncio.gather(*(self.add_knowledge(knowledge, namespace, build_index) for knowledge in knowledge_list))
            elapsed = time.time() - start_time
            logger.info(f"✅ Batch add {len(knowledge_list)} knowledge addition completed, elapsed time: {elapsed:.3f} seconds")
        logger.debug(f"add_knowledge_list end")
    
    async def update_knowledge(self, knowledge: Artifact, namespace: str = "default") -> None:
        """Update an existing knowledge artifact."""
        self._get_working_state(namespace).save_knowledge(knowledge)
        
        # Workspace is optional, only update if initialized
        if self._context._workspace:
            await self._context._workspace.update_artifact(artifact_id=knowledge.artifact_id, content=knowledge.content)
    
    async def delete_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> None:
        """Delete a knowledge artifact by ID."""
        # Implementation depends on workspace and working state
        # This is a placeholder - actual implementation may vary
        working_state = self._get_working_state(namespace)
        if working_state:
            # Remove from working state if there's a method for it
            pass
        
        if self._context._workspace:
            # Delete from workspace if there's a method for it
            pass
    
    async def get_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> Optional[Artifact]:
        """Get a knowledge artifact by ID."""
        return self._context._get_knowledge(knowledge_id)
    
    async def get_knowledge_chunk(self, knowledge_id: str, chunk_index: int) -> Optional[Chunk]:
        """Get a specific chunk from a knowledge artifact."""
        workspace = await self._context._ensure_workspace()
        return await workspace.get_artifact_chunk(knowledge_id, chunk_index=chunk_index)
    
    async def search_knowledge(self, user_query: str, top_k: int = None, search_filter: Dict[str, Any] = None, namespace: str = "default") -> Optional[SearchResults]:
        """Search knowledge using semantic search."""
        if self._context._workspace:
            if not search_filter:
                search_filter = {}
            search_filter = {
                # "type": "knowledge",
                **search_filter
            }
            return await self._context._workspace.search_artifact_chunks(
                user_query=user_query,
                search_filter=search_filter,
                top_k=top_k
            )
        return None
    
    def _need_index(self, artifact: Artifact) -> bool:
        """Check if artifact needs indexing."""
        from aworld.core.context.amni.retrieval.artifacts import SearchArtifact
        return isinstance(artifact, SearchArtifact)
    
    def _format_chunk_content(self, chunk) -> str:
        """Format chunk content for display."""
        return (
            f"<knowledge_chunk>\n"
            f"<chunk_id>{chunk.chunk_id}</chunk_id>\n"
            f"<chunk_index>{chunk.chunk_metadata.chunk_index}</chunk_index>\n"
            f"<origin_knowledge_id>{chunk.chunk_metadata.artifact_id}</origin_knowledge_id>\n"
            f"<origin_knowledge_type>{chunk.chunk_metadata.artifact_type}</origin_knowledge_type>\n"
            f"<chunk_content>{chunk.content}</chunk_content>\n"
            f"</knowledge_chunk>\n"
        )
    
    async def _get_knowledge_index_context(self, knowledge: Artifact, load_chunk_content_size: int = 5) -> str:
        """Get knowledge index context for a single artifact."""
        from aworld.core.context.amni.retrieval.chunker import Chunk
        from aworld.core.context.amni.utils.text_cleaner import truncate_content
        
        knowledge_context = "<knowledge>\n"
        knowledge_context += f"<id>{knowledge.artifact_id}</id>\n"
        
        if knowledge.summary:
            knowledge_context += f"{knowledge.summary}\n"
        
        knowledge_chunk_context = ""
        if knowledge.metadata.get("chunked"):
            total_chunk = knowledge.metadata.get("chunks")
            chunk_count_desc = f"Total is {total_chunk} chunks"
            knowledge_context += f"<chunks description='{chunk_count_desc}'>\n"
            
            # Load head and tail chunks
            if load_chunk_content_size:
                def _format_chunk_content_internal(_chunk: Chunk) -> str:
                    return (
                        f"  <knowledge_chunk>\n"
                        f"    <chunk_id>{_chunk.chunk_id}</chunk_id>\n"
                        f"    <chunk_index>{_chunk.chunk_metadata.chunk_index}</chunk_index>\n"
                        f"    <chunk_content>{truncate_content(_chunk.content, 1000)}</chunk_content>\n"
                        f"  </knowledge_chunk>\n"
                    )
                
                # Ensure workspace is initialized
                workspace = await self._context._ensure_workspace()
                head_chunks, tail_chunks = await workspace.get_artifact_chunks_head_and_tail(
                    knowledge.artifact_id,
                    load_chunk_content_size
                )
                # Add head chunks
                if head_chunks:
                    knowledge_chunk_context += f"\n<head_chunks start='{head_chunks[0].chunk_id}' end='{head_chunks[len(head_chunks)-1].chunk_id}'>\n"
                    for chunk in head_chunks:
                        knowledge_chunk_context += _format_chunk_content_internal(chunk)
                    knowledge_chunk_context += f"\n</head_chunks>\n"
                
                # Add tail chunks
                if tail_chunks:
                    knowledge_chunk_context += f"<tail_chunks  start='{tail_chunks[0].chunk_id}' end='{tail_chunks[len(tail_chunks)-1].chunk_id}'>\n"
                    for chunk in tail_chunks:
                        knowledge_chunk_context += _format_chunk_content_internal(chunk)
                    knowledge_chunk_context += f"\n</tail_chunks>\n"
            knowledge_context += f"{knowledge_chunk_context}\n</chunks>\n"
        knowledge_context += "</knowledge>\n"
        return knowledge_context
    
    async def _get_artifact_statistics(self, chunk_indicis: list) -> str:
        """Get artifact statistics information."""
        if not chunk_indicis:
            return ""
        # Generate statistics info
        artifact_count_info = ", ".join(
            [f"{item.artifact_id}: {item.chunk_count} chunks " for item in chunk_indicis[:100]]
        )
        
        summary_prompt = (
            f"📊 Total {len(chunk_indicis)} artifacts.\n"
            f"📈 details is: \n {artifact_count_info}"
        )
        
        return summary_prompt
    
    async def _load_artifact_index_context(self, artifact_chunk_indicis: list, top_k: int) -> str:
        """Load artifact index context."""
        import asyncio
        
        if not artifact_chunk_indicis:
            return ""
        
        # Ensure workspace is initialized
        workspace = await self._context._ensure_workspace()
        
        # Group by artifact_id
        artifact_chunks = {}
        for chunk_item in artifact_chunk_indicis:
            if hasattr(chunk_item, "artifact_id"):
                artifact_id = chunk_item.artifact_id
                if artifact_id not in artifact_chunks:
                    artifact_chunks[artifact_id] = []
                artifact_chunks[artifact_id].append(chunk_item)
        
        # Get middle range indices for each artifact using efficient range queries
        tasks = []
        for artifact_id in artifact_chunks.keys():
            task = workspace.get_artifact_chunk_indices_middle_range(artifact_id, top_k)
            tasks.append(task)
        knowledge_index_context = ""
        if tasks:
            middle_range_indices = await asyncio.gather(*tasks)
            for artifact_id, indices in zip(artifact_chunks.keys(), middle_range_indices):
                if indices:
                    knowledge_index_context += f"\n📄 Artifact {artifact_id} (chunks {top_k} to {2*top_k}): index :\n"
                    for item in indices:
                        knowledge_index_context += f"{item.model_dump()}\n"
        
        return knowledge_index_context
    
    async def _load_artifact_content_context(self, chunk_indicis: list, top_k: int) -> str:
        """Load artifact content context."""
        import asyncio
        
        if not chunk_indicis:
            return ""
        
        knowledge_chunk_context = ""
        
        # Group by artifact_id
        artifact_chunks = {}
        for chunk_item in chunk_indicis:
            if hasattr(chunk_item, "artifact_id"):
                artifact_id = chunk_item.artifact_id
                if artifact_id not in artifact_chunks:
                    artifact_chunks[artifact_id] = []
                artifact_chunks[artifact_id].append(chunk_item)
        
        # Ensure workspace is initialized
        workspace = await self._context._ensure_workspace()
        
        # Get head and tail chunks for each artifact using efficient range queries
        tasks = []
        for artifact_id in artifact_chunks.keys():
            task = workspace.get_artifact_chunks_head_and_tail(artifact_id, top_k)
            tasks.append(task)
        
        if tasks:
            head_tail_chunks = await asyncio.gather(*tasks)
            for artifact_id, (head_chunks, tail_chunks) in zip(artifact_chunks.keys(), head_tail_chunks):
                if head_chunks or tail_chunks:
                    knowledge_chunk_context += f"\n📄 Artifact {artifact_id} content:\n"
                    
                    # Add head chunks
                    if head_chunks:
                        knowledge_chunk_context += f"🔝 head chunks ({len(head_chunks)} chunks):\n"
                        for chunk in head_chunks:
                            knowledge_chunk_context += self._format_chunk_content(chunk)
                    
                    # Add tail chunks
                    if tail_chunks:
                        knowledge_chunk_context += f"🔚 tail chunks ({len(tail_chunks)} chunks):\n"
                        for chunk in tail_chunks:
                            knowledge_chunk_context += self._format_chunk_content(chunk)
        
        return knowledge_chunk_context
    
    async def _load_artifact_chunks_by_workspace(self, search_filter: Dict[str, Any], namespace: str = "default", top_k: int = 20) -> str:
        """Load artifact chunks from workspace."""
        knowledge_chunk_context = ""
        knowledge_chunks = await self.search_knowledge(
            user_query=self._context.task_input,
            namespace=namespace,
            search_filter=search_filter,
            top_k=top_k
        )
        if not knowledge_chunks:
            return knowledge_chunk_context
        
        from aworld.core.context.amni.retrieval.embeddings import EmbeddingsMetadata
        
        for item in knowledge_chunks.docs:
            metadata: EmbeddingsMetadata = item.metadata
            knowledge_chunk_context += (
                f"<knowledge_chunk>\n"
                f"<chunk_id>{item.id}</chunk_id>\n"
                f"<chunk_index>{metadata.chunk_index}</chunk_id>\n"
                f"<relevant_score>{item.score:.3f}</relevant_score>\n"
                f"<origin_knowledge_id>{metadata.artifact_id}</origin_knowledge_id>\n"
                f"<origin_knowledge_type>{metadata.artifact_type}</origin_knowledge_type>\n"
                f"<chunk_content>{item.content}</chunk_content>\n"
                f"</knowledge_chunk>\n"
            )
        return knowledge_chunk_context
    
    async def offload_by_workspace(self, artifacts: List[Artifact], namespace: str = "default", biz_id: str = None) -> str:
        """Offload artifacts to workspace (context offloading)."""
        import uuid
        import asyncio
        from aworld.logs.util import logger
        
        if not artifacts:
            return ""
        
        use_index = self._need_index(artifacts[0])
        # 1. add knowledge to workspace
        if not biz_id:
            biz_id = str(uuid.uuid4())
        for artifact in artifacts:
            artifact.metadata.update({
                "biz_id": biz_id
            })
        await self.add_knowledge_list(artifacts, namespace=namespace, build_index=use_index)
        
        # Add a strategy: single page should not exceed 40K
        if len(artifacts) == 1 and len(artifacts[0].content) < 40_000:
            logger.info(f"directly return artifacts content: {len(artifacts[0].content)}")
            return f"{artifacts[0].content}"
        
        logger.info(f"add artifacts to context: {[artifact.artifact_id for artifact in artifacts]}")
        artifact_context = "This is cur action result: a list of knowledge artifacts:"
        artifact_context += "\n<knowledge_list>\n"
        search_tasks = []
        for artifact in artifacts:
            search_tasks.append(self._get_knowledge_index_context(artifact, load_chunk_content_size=5))
        search_task_results = await asyncio.gather(*search_tasks)
        artifact_context += "\n".join(search_task_results)
        artifact_context += "</knowledge_list>"
        return f"{artifact_context}"
    
    async def load_context_by_workspace(self, search_filter: Dict[str, Any] = None, namespace: str = "default", 
                                        top_k: int = 20, load_content: bool = True, load_index: bool = True, 
                                        search_by_index: bool = True) -> str:
        """Load knowledge context from workspace."""
        import time
        from aworld.logs.util import logger
        from aworld.core.context.amni.prompt.prompts import AMNI_CONTEXT_PROMPT
        
        # Ensure workspace is initialized
        workspace = await self._context._ensure_workspace()
        
        if not search_filter:
            search_filter = {}
        
        # 1. Get knowledge_chunk_index with biz_id
        knowledge_index_context = ""
        knowledge_chunk_context = ""
        if search_by_index:
            if load_index:
                artifacts_indicis = await workspace.search_artifact_chunks_index(
                    self._context.task_input,
                    search_filter=search_filter,
                    top_k=top_k * 3
                )
                if artifacts_indicis:
                    for item in artifacts_indicis:
                        knowledge_index_context += f"{item.model_dump()}\n"
            if load_content:
                knowledge_chunk_context = await self._load_artifact_chunks_by_workspace(
                    search_filter=search_filter,
                    namespace=namespace,
                    top_k=top_k
                )
        else:
            start_time = time.time()
            artifacts_indicis = await workspace.async_query_artifact_index(search_filter=search_filter)
            logger.info(f"📊 artifacts_indicis loaded successfully in {time.time() - start_time:.3f} seconds")
            
            if artifacts_indicis:
                # 1. Get artifact statistics info
                artifact_stats = await self._get_artifact_statistics(artifacts_indicis)
                if artifact_stats:
                    knowledge_index_context += artifact_stats
                
                # 2. Process load_index logic - each artifact read the index from topk to 2*topk
                if load_index:
                    knowledge_index_context += await self._load_artifact_index_context(
                        artifact_chunk_indicis=artifacts_indicis,
                        top_k=top_k
                    )
                
                # 3. Process load_content logic - each artifact keep head-topk and tail-topk chunks
                if load_content:
                    knowledge_chunk_context += await self._load_artifact_content_context(
                        chunk_indicis=artifacts_indicis,
                        top_k=top_k
                    )
        
        # 3. Format context
        knowledge_context = AMNI_CONTEXT_PROMPT["KNOWLEDGE_PART"].format(
            knowledge_index=knowledge_index_context,
            knowledge_chunks=knowledge_chunk_context
        )
        
        return knowledge_context
    
    async def build_knowledge_context(self, namespace: str = "default", search_filter: Dict[str, Any] = None, top_k: int = 20) -> str:
        """Build knowledge context string."""
        return await self.load_context_by_workspace(search_filter, namespace=namespace, top_k=top_k)
    
    async def get_todo_info(self) -> str:
        """Get todo information from workspace."""
        from aworld.logs.util import logger
        
        workspace = await self._context._ensure_workspace()
        workspace._load_workspace_data()
        todo_info = (
            "Below is the global task execute todo information, explaining the current progress:\n"
        )
        artifact = workspace.get_artifact(f"session_{self._context.session_id}_todo")
        if not artifact:
            return "Todo is Empty"
        todo_info += f"{artifact.content}"
        return todo_info
    
    async def get_actions_info(self, namespace: str = "default") -> str:
        """Get actions information from workspace."""
        from aworld.logs.util import logger
        
        workspace = await self._context._ensure_workspace()
        workspace._load_workspace_data()
        artifacts = await workspace.query_artifacts(search_filter={
            "context_type": "actions_info",
            "task_id": self._context.task_id
        })
        logger.info(f"get_actions_info: {len(artifacts)}")
        actions_info = (
            "\nBelow is the actions information, including both successful and failed experiences, "
            "as well as key knowledge and insights obtained during the process. "
            "\nMake full use of this information:\n"
            "<knowledge_list>"
        )
        for artifact in artifacts:
            actions_info += f"  <knowledge id='{artifact.artifact_id}' summary='{artifact.summary}<'>: </knowledge>\n"
        actions_info += f"\n</knowledge_list>\n\n<tips>\n"
        actions_info += f"you can use get_knowledge(knowledge_id_xxx) to got detail content\n"
        actions_info += f"</tips>\n"
        return actions_info

