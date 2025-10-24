import asyncio
import time
import traceback
from itertools import chain
from typing import Optional, Dict, Tuple

from .chunker import ChunkerFactory, Chunk, ChunkIndex, Chunker, ArtifactStats,ChunkStoreFactory, ChunkStore
from aworld.logs.util import logger
from .embeddings.base import SearchResults, SearchResult
from .reranker import Reranker
from .reranker.factory import RerankerFactory
from .base import BaseRetriever, RetrieverConfig
from .index import RetrievalIndexPluginFactory
from .index.base import RetrievalIndexPlugin
from aworld.output import Artifact


class AmniRetriever(BaseRetriever):
    """
    AmniRetriever
    
    This class orchestrates the complete RAG pipeline, providing intelligent artifact
    processing, semantic chunking, vector indexing, and multi-source retrieval with
    intelligent reranking capabilities.
    
    Key Features:
    - 🔧 Intelligent artifact chunking with contextual awareness
    - 💾 Efficient chunk storage and management
    - 🔍 Multi-index plugin search capabilities (Vector, Full-Text, KG, etc.)
    - 🎯 Smart result deduplication and reranking
    - ⚡ Asynchronous processing for optimal performance
    - 🛡️ Workspace isolation for multi-tenant security
    
    Architecture:
    The retriever follows a pipeline pattern where artifacts flow through chunking,
    storage, indexing, and retrieval phases, with each phase optimized for
    specific performance characteristics.
    """
    chunker: Optional[Chunker]
    chunk_store: Optional[ChunkStore]
    reranker: Optional[Reranker]
    index_plugins: Optional[list[RetrievalIndexPlugin]]
    _chunk_cache: Dict[Tuple[str, int], Chunk]  # Cache for frequently accessed chunks

    def __init__(self, config: RetrieverConfig) -> None:
        """Initialize AmniRag with None values for components."""
        super().__init__(config)
        
        # Initialize chunk cache for performance optimization
        self._chunk_cache: Dict[Tuple[str, int], Chunk] = {}

        # Initialize chunker
        self.chunker = ChunkerFactory.get_chunker(config.chunk_config)
        if self.chunker is None:
            raise RuntimeError("Failed to initialize chunker")
        logger.debug(f"✅ Chunker initialized: {type(self.chunker).__name__}")

        self.chunk_store: Optional[ChunkStore] = ChunkStoreFactory.get_store(
            config.chunk_store_config.provider,
            config=config.chunk_store_config.config)
        if self.chunk_store is None:
            raise RuntimeError("Failed to initialize chunk store")
        logger.debug(
            f"✅ Chunk store initialized: {type(self.chunk_store).__name__}")

        self.index_plugins = []
        for index_plugin_config in config.index_plugin_configs:
            try:
                plugin = RetrievalIndexPluginFactory.get_index_plugin(
                    index_plugin_config=index_plugin_config)
                self.index_plugins.append(plugin)
            except Exception as e:
                logger.error(
                    f"❌ Failed to initialize index plugin ({getattr(index_plugin_config, 'type', 'unknown')}): {e}"
                )

        self.reranker = RerankerFactory.get_reranker(config.reranker_config)
        if self.reranker is None:
            raise RuntimeError("Failed to initialize reranker")
        logger.debug(f"✅ Reranker initialized: {type(self.reranker).__name__}")

        logger.debug("🚀 AmniRag instance initialized")

    @classmethod
    def create(cls, rag_config: RetrieverConfig,
                     **kwargs) -> 'AmniRetriever':
        """
        Async factory method to create and initialize AmniRag instance.
        
        Args:
            rag_config: Configuration object for RAG operations
            **kwargs: Additional keyword arguments
            
        Returns:
            AmniRetriever: Initialized AmniRag instance
            
        Raises:
            Exception: If any component fails to initialize
        """
        try:
            instance = cls(rag_config)
            logger.info("🔧 load AmniRag components...")

            logger.info("🎉 AmniRag load completed successfully")
            return instance

        except Exception as e:
            logger.error(f"❌ Failed to initialize AmniRag: {str(e)}")
            raise

    async def async_insert(self, workspace_id: str, artifact: Artifact, index:bool = True) -> None:
        """
        Asynchronously insert an artifact into the Retriever.
        
        This method processes an artifact by chunking it, storing the chunks, and building
        vector indices for efficient retrieval. The index building can be done either
        
        Processing Flow:
        +-------------+    chunk     +-----------+    store     +------------+    index     +------------+
        |   Artifact  |------------->|  Chunks   |------------>| ChunkStore  |------------>| Multi-Index |
        +-------------+              +-----------+              +------------+              +------------+
              |                           |                           |                           |
              |                           |                           |                           |
              v                           v                           v                           v
        [Input Validation]         [Semantic Chunking]        [Batch Storage]        [Parallel Indexing]
              |                           |                           |                           |
              |                           |                           |                           |
              |                           |                           |                           +---> [Vector Index]
              |                           |                           |                           +---> [Full-Text Index]  
              |                           |                           |                           +---> [Knowledge Graph]
              |                           |                           |                           +---> [Other Indexes]
        
        Args:
            artifact (Artifact): Artifact object to be processed and inserted
            async_build_index (bool): Whether to build vector index asynchronously. 
                                    Defaults to True for better performance.
        
        Raises:
            RuntimeError: If chunker is not properly initialized
            Exception: If any step in the insertion process fails
        """
        try:
            # Check if components are properly initialized
            if self.chunker is None:
                logger.error("❌ Chunker is not initialized")
                raise RuntimeError("Chunker is not initialized")

            if not isinstance(artifact.content, str):
                return # Skip non-string artifacts

            logger.debug(
                f"[AMNI_RETRIEVER]📥 Starting artifact insertion for workspace: {workspace_id}"
            )
            logger.debug(
                f"[AMNI_RETRIEVER]📋 Processing artifact: {getattr(artifact, 'id', 'unknown')}")

            chunks = await self.chunker.chunk(artifact)
            artifact.metadata["chunked"] = True
            artifact.metadata["chunks"] = len(chunks)
            if not chunks:
                return
            logger.debug(f"[AMNI_RETRIEVER]✂️ Artifact chunked into {len(chunks)} pieces")

            await self._add_chunks_to_store(workspace_id, chunks[:200])
            logger.debug(f"[AMNI_RETRIEVER]💾 Chunks added to store: {len(chunks)}")

            if index:
                await self._build_chunks_index(workspace_id, chunks[:200])
                logger.debug(f"[AMNI_RETRIEVER]🔍 Finished index build for {len(chunks)} chunks")

            # Clear cache for this artifact to ensure consistency
            self._clear_artifact_cache(artifact.artifact_id)
            
            logger.debug(
                f"[AMNI_RETRIEVER]✅ Artifact#{artifact.artifact_id} insertion completed successfully for workspace_id: {workspace_id}"
            )

        except Exception as e:
            logger.error(
                f"[AMNI_RETRIEVER]❌ Failed to insert artifact for workspace_id {workspace_id}: {str(e)}"
            )
            raise

    async def _add_chunks_to_store(self, workspace_id: str, chunks: list[Chunk]) -> None:
        """
        Add chunks to the chunk store with workspace ID.
        
        This helper method sets the workspace_id for each chunk and then
        upserts them to the chunk store in batch for efficiency.
        
        Args:
            chunks (list[Chunk]): List of chunks to be stored
        """
        for chunk in chunks:
            chunk.chunk_metadata.workspace_id = workspace_id
        await self.chunk_store.upsert_chunks_batch(chunks)

    async def _build_chunks_index(self, workspace_id: str, chunks: list[Chunk]) -> None:
        """
        Build multi-modal indices for chunks using all available index plugins concurrently.
        
        This method gathers all chunk indexing tasks from different index plugins (Vector, 
        Full-Text, Knowledge Graph, etc.) and executes them concurrently to improve 
        insertion efficiency. Each index plugin processes each chunk's content and metadata
        according to its specific indexing strategy.
        
        Args:
            chunks (list[Chunk]): List of chunks to build indices for
        """
        logger.debug("🚀🚀 Chunk Index Start  🚀")

        tasks = []
        for plugin in self.index_plugins:
            batch_data = [{
                "doc_id": chunk.chunk_id,
                "content": chunk.content,
                "meta": chunk.chunk_metadata.model_dump()
            } for chunk in chunks]
            index_task = plugin.build_index_batch(workspace_id, batch_data)
            if not plugin.wait_insert:
                asyncio.create_task(index_task)
            else:
                tasks.append(index_task)

        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, (chunk, result) in enumerate(zip(chunks, results), 1):
            if isinstance(result, Exception):
                logger.error(
                    f"❌ Failed to add chunk {chunk.chunk_id} to vector DB: {result}"
                )
            else:
                logger.debug(
                    f"💾 Chunk {i}/{len(chunks)} added to vector DB: {chunk.chunk_id}"
                )
        logger.debug("🎉🎉 Build Chunk Index Finished 🎉🎉")

    async def async_search_chunk_index(self, workspace_id: str, user_query: str, search_filter: dict = None, top_k: int = None) -> \
    Optional[list[ChunkIndex]]:
        if not search_filter:
            search_filter = {}
        search_filter["workspace_id"] = workspace_id
        results = await self.async_search(workspace_id, user_query, search_filter, top_k)
        chunk_indexes = []
        if results:
            for chunk in results.docs:
                chunk_indexes.append(
                    ChunkIndex(chunk_index=chunk.metadata.chunk_index,
                               artifact_id=chunk.metadata.artifact_id,
                               chunk_desc=chunk.metadata.chunk_desc))
        return chunk_indexes


    async def async_search(self,
                           workspace_id: str,
                           user_query: str,
                           search_filter: dict = None,
                           top_k: int = None,
                           **kwargs) -> Optional[SearchResults]:
        """
        Perform search across all index plugins and rerank results.
        
        This method executes search queries across all configured index plugins (Vector, 
        Full-Text, Knowledge Graph, etc.) concurrently, then applies reranking to merge 
        and sort the results from different index types.
        
        Processing Flow:
        +-------------+    search    +------------+    merge     +------------+    rerank    +------------+
        | User Query  |------------->|Index Plugins|------------>|  Results   |------------>|  Output   |
        +-------------+              +------------+              +------------+              +------------+
              |                           |                           |                           |
              |                           |                           |                           |
              v                           v                           v                           v
        [Query Parsing]            [Parallel Search]         [Result Collection]      [Smart Reranking]
              |                           |                           |                           |
              |                           |                           |                           |
              |                           +---> [Vector Search]        |                           |
              |                           +---> [Full-Text Search]    |                           |
              |                           +---> [KG Query]            |                           |
              |                           +---> [Other Indexes]       |                           |
              |                           |                           |                           |
              +---------------------------+---------------------------+---------------------------+
              |                    [Concurrent Execution]                                    |
              +------------------------------------------------------------------------------+
        
        Args:
            user_query (str): The search query string
            search_filter (dict, optional): Additional filters for the search. Defaults to None.
            threshold (float): Minimum similarity threshold for results. Defaults to 0.7.
            topk (int): Maximum number of results to return. Defaults to 50.
        
        Returns:
            Optional[SearchResults]: Merged and reranked search results, or None if search fails
        """
        start_time = time.time()
        if search_filter is None:
            search_filter = {}
        logger.debug(f"🔍 Start search: {user_query}")
        tasks = []
        for plugin in self.index_plugins:
            tasks.append(
                plugin.async_search(workspace_id, user_query,
                                    search_filter, top_k))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, (plugin, result) in enumerate(zip(self.index_plugins, results),
                                             1):
            if isinstance(result, Exception):
                logger.error(
                    f"❌ Failed to search plugin {plugin.name}: {result}")
            else:
                logger.debug(
                    f"💾 Search plugin {i}/{len(self.index_plugins)} completed successfully"
                )
        logger.info(f"🔍 Search finished: {user_query}, result size is {len(results)}")
        # rerank_result = await self.async_rerank(user_query, results, top_k)
        use_time = time.time() - start_time
        # logger.info(f"🔍 Rerank finished: {user_query[:20]}, use time {use_time:.3f} , result size is {len(rerank_result.docs) if rerank_result and rerank_result.docs else 0}")

        return results[0]

    async def async_rerank(self,
                           user_query: str,
                           search_results_list: list[SearchResults],
                           topk: int = 20) -> Optional[SearchResults]:
        """
        Deduplicate and rerank search results from multiple sources.
        
        This method takes search results from multiple plugins, removes duplicates
        based on document IDs, and applies reranking to produce a unified,
        sorted result set.
        
        Args:
            user_query (str): The original user query for reranking context
            search_results_list (list[SearchResults]): List of search results from different plugins
            topk (int): Maximum number of results to return after reranking. Defaults to 50.
        
        Returns:
            Optional[SearchResults]: Merged and reranked search results, or None if reranking fails
        
        Raises:
            RuntimeError: If reranker is not properly initialized
        """
        if self.reranker is None:
            logger.error("❌ Reranker is not initialized")
            raise RuntimeError("Reranker is not initialized")

        # unique docs
        doc_list = []
        seen_ids = set()
        seen_contents = set()
        for doc in chain.from_iterable(
            (sr.docs or []) for sr in search_results_list if sr and sr.docs):
            if doc.id not in seen_ids and doc.content not in seen_contents:
                doc_list.append(doc)
                seen_contents.add(doc.content)
                seen_ids.add(doc.id)

        docs = [doc.content for doc in doc_list]

        # reranker rerank docs
        rerank_results = await self.reranker.run(user_query, docs, topk)
        if not rerank_results:
            return None

        merged_results = []
        for item in rerank_results:
            idx = item.idx
            score = item.score
            doc = doc_list[idx]
            merged_results.append(
                SearchResult(id=doc.id,
                             content=doc.content,
                             metadata=doc.metadata,
                             score=score))

        merged_search_results = SearchResults(docs=merged_results,
                                              search_at=int(time.time()))
        return merged_search_results

    async def async_query_chunk_index(self,
                                      workspace_id: str,
                                      search_filter: dict = None
                                      ) -> Optional[list[ChunkIndex]]:
        """
        Query chunk indices based on search filters.
        
        This method searches for chunk indices that match the given filters,
        automatically adding the workspace_id filter for security isolation.
        
        Args:
            search_filter (dict, optional): Search criteria for chunks. Defaults to None.
        
        Returns:
            Optional[list[ChunkIndex]]: List of chunk indices matching the criteria, or None if no results
        """
        if not search_filter:
            search_filter = {}
        search_filter["workspace_id"] = workspace_id
        results = await self.chunk_store.search_chunks(search_filter)
        chunk_indexes = []
        if results:
            for chunk in results:
                chunk_indexes.append(
                    ChunkIndex(chunk_index=chunk.chunk_metadata.chunk_index,
                               artifact_id=chunk.chunk_metadata.artifact_id,
                               chunk_desc=chunk.chunk_metadata.chunk_desc))
        return chunk_indexes

    async def async_query_artifact_index(self,
                                         workspace_id: str,
                                          search_filter: dict = None
                                          ) -> Optional[list[ArtifactStats]]:
        """
        Query chunk count statistics for each artifact.

        This method queries all matching chunks based on the provided search filter, then groups by artifact_id
        to count the number of chunks contained in each artifact, returning a list of statistics.

        Processing flow:
        +-------------+    search    +------------+    group     +------------+    count     +------------+
        |Search Filter|------------->|   Chunks   |------------>| Group by   |------------>| Artifact   |
        |             |              |            |             |artifact_id |             |  Stats     |
        +-------------+              +------------+             +------------+             +------------+
              |                           |                           |                           |
              |                           |                           |                           |
              v                           v                           v                           v
        [Add Workspace]            [Query Chunks]            [Group & Count]        [Build Stats]
              |                           |                           |                           |
              |                           |                           |                           |
              +---------------------------+---------------------------+---------------------------+
              |                    [Concurrent Processing]                                      |
              +--------------------------------------------------------------------------------+
        
        Args:
            search_filter (dict, optional): Search condition dictionary for filtering chunks. Defaults to None.
                                         Automatically adds workspace_id to ensure security isolation.

        Returns:
            Optional[list[ArtifactStats]]: List containing statistics for each artifact, returns None if query fails.
                                         Each ArtifactStats contains artifact_id and corresponding chunk_count.

        Raises:
            Exception: If an error occurs during the query process
        """
        try:
            # ensure workspace_id 
            if not search_filter:
                search_filter = {}
            search_filter["workspace_id"] = workspace_id
            
            logger.debug(f"📊 Starting artifact statistics query, workspace_id: {workspace_id}")
            logger.debug(f"🔍 Search filter: {search_filter}")

            # Query statistics directly from database to avoid loading all chunks into memory
            artifact_chunk_counts = await self.chunk_store.get_artifact_chunk_counts(search_filter)
            
            if not artifact_chunk_counts:
                logger.debug("📊 No matching artifacts found")
                return []

            logger.debug(f"📊 Database query completed: found statistics for {len(artifact_chunk_counts)} artifacts")

            # Build ArtifactStats list - using list comprehension for performance optimization
            artifact_stats_list = [
                ArtifactStats(artifact_id=artifact_id, chunk_count=chunk_count)
                for artifact_id, chunk_count in artifact_chunk_counts.items()
            ]
            
            # Results are already sorted by chunk_count in descending order (completed in database query)
            total_chunks = sum(artifact_chunk_counts.values())
            logger.debug(f"📊 Statistics completed: found {len(artifact_stats_list)} artifacts, total {total_chunks} chunks")
            
            # Log detailed information for the first few artifacts (for debugging)
            if artifact_stats_list:
                top_artifacts = artifact_stats_list[:5]  # Show top 5
                top_info = ", ".join([f"{stats.artifact_id}: {stats.chunk_count} chunks" for stats in top_artifacts])
                logger.debug(f"📊 Top artifacts statistics: {top_info}")
            
            return artifact_stats_list
            
        except Exception as e:
            logger.error(f"❌ Failed to query artifact statistics: {str(e)}")
            logger.error(f"🔍 Search filter: {search_filter}")
            raise

    async def async_query_chunk(self,workspace_id:str, artifact_id: str,
                                chunk_index: int) -> Optional[Chunk]:
        """
        Retrieve a specific chunk by artifact ID and chunk index with caching.
        
        This method fetches a specific chunk from the chunk store using
        the artifact ID and chunk index as identifiers. It includes caching
        for frequently accessed chunks to improve performance.
        
        Args:
            artifact_id (str): The ID of the artifact containing the chunk
            chunk_index (int): The index of the chunk within the artifact
        
        Returns:
            Optional[Chunk]: The requested chunk if found, or None if not found or on error
        """
        start_time = time.time()
        cache_key = (artifact_id, chunk_index)
        
        try:
            # Check cache first for performance optimization
            if cache_key in self._chunk_cache:
                logger.debug(f"🚀 Cache hit for chunk: {artifact_id}:{chunk_index}")
                return self._chunk_cache[cache_key]
            
            logger.debug(
                f"🔍 Querying chunk: {workspace_id}-{artifact_id}:{chunk_index}"
            )

            # Query from database with optimized search
            chunks = await self.chunk_store.search_chunks(
                search_filter={
                    "artifact_id": artifact_id,
                    "chunk_index": chunk_index,
                    "workspace_id": workspace_id
                })
            
            if chunks:
                chunk = chunks[0]
                # Cache the result for future queries
                self._chunk_cache[cache_key] = chunk
                
                # Limit cache size to prevent memory issues
                if len(self._chunk_cache) > 1000:
                    # Remove oldest entries (simple FIFO)
                    oldest_key = next(iter(self._chunk_cache))
                    del self._chunk_cache[oldest_key]
                
                elapsed_time = time.time() - start_time
                logger.debug(f"✅ Chunk retrieved in {elapsed_time:.3f}s: {artifact_id}:{chunk_index}")
                return chunk
            
            logger.debug(f"❌ Chunk not found: {artifact_id}:{chunk_index}")
            return None
            
        except Exception as err:
            elapsed_time = time.time() - start_time
            logger.warning(
                f"❌ async_query_chunk[{workspace_id}-{artifact_id}:{chunk_index}] "
                f"failed after {elapsed_time:.3f}s, error: {err}\n"
                f"Trace: {traceback.format_exc()}"
            )
            return None

    async def get_artifact_chunks_by_range(self, workspace_id: str, artifact_id: str, start_index: int,
                                           end_index: int) -> list[Chunk]:
        return await self.chunk_store.get_artifact_chunks_by_range(artifact_id, start_index, end_index)

    def _clear_artifact_cache(self, artifact_id: str) -> None:
        """
        Clear cache entries for a specific artifact.
        
        This method removes all cached chunks for the given artifact_id
        to ensure cache consistency when artifacts are updated.
        
        Args:
            artifact_id (str): The ID of the artifact to clear from cache
        """
        keys_to_remove = [key for key in self._chunk_cache.keys() if key[0] == artifact_id]
        for key in keys_to_remove:
            del self._chunk_cache[key]
        
        if keys_to_remove:
            logger.debug(f"🧹 Cleared {len(keys_to_remove)} cache entries for artifact: {artifact_id}")
    
    def clear_cache(self) -> None:
        """
        Clear all cached chunks.
        
        This method removes all entries from the chunk cache, useful for
        memory management or when cache consistency is required.
        """
        cache_size = len(self._chunk_cache)
        self._chunk_cache.clear()
        logger.debug(f"🧹 Cleared all {cache_size} cache entries")
