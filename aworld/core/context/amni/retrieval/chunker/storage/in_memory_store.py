"""
In-memory implementation of ChunkStore for AmniContext.

This module provides a fast, in-memory storage solution for chunks.
"""

import asyncio
import logging
from typing import Optional, List, Dict, Any
from ..base import Chunk
from .chunk_store import ChunkStore

logger = logging.getLogger(__name__)


class InMemoryChunkStore(ChunkStore):
    """
    In-memory implementation of ChunkStore.
    
    This implementation stores chunks in memory using a Python list and dictionary
    for fast access. It's suitable for testing and small-scale applications.
    """
    
    def __init__(self, **kwargs) -> None:
        """Initialize the in-memory chunk store."""
        self._chunks: List[Chunk] = []
        self._chunk_index: Dict[str, int] = {}  # chunk_id -> index mapping
        self._lock = asyncio.Lock()
        logger.debug("🚀 InMemoryChunkStore initialized")
    
    async def upsert_chunk(self, chunk: Chunk) -> None:
        """
        Upsert a chunk to storage. If chunk with same chunk_id exists, update it; otherwise insert new one.
        
        Args:
            chunk: Chunk object to be upserted
        """
        async with self._lock:
            if chunk.chunk_id in self._chunk_index:
                # Update existing chunk
                index = self._chunk_index[chunk.chunk_id]
                self._chunks[index] = chunk
                logger.debug(f"🔄 Updated existing chunk: {chunk.chunk_id}")
            else:
                # Insert new chunk
                self._chunks.append(chunk)
                self._chunk_index[chunk.chunk_id] = len(self._chunks) - 1
                logger.debug(f"➕ Inserted new chunk: {chunk.chunk_id}")
    
    async def upsert_chunks_batch(self, chunks: List[Chunk]) -> None:
        """
        Upsert multiple chunks to storage in a batch operation.
        
        Args:
            chunks: List of Chunk objects to be upserted
        """
        if not chunks:
            return
            
        async with self._lock:
            updated_count = 0
            inserted_count = 0
            
            for chunk in chunks:
                if chunk.chunk_id in self._chunk_index:
                    # Update existing chunk
                    index = self._chunk_index[chunk.chunk_id]
                    self._chunks[index] = chunk
                    updated_count += 1
                else:
                    # Insert new chunk
                    self._chunks.append(chunk)
                    self._chunk_index[chunk.chunk_id] = len(self._chunks) - 1
                    inserted_count += 1
            
            logger.debug(f"🔄 Batch upsert completed: {updated_count} updated, {inserted_count} inserted")
    
    async def get_chunk(self, chunk_id: str) -> Optional[Chunk]:
        """
        Retrieve a chunk by its ID.
        
        Args:
            chunk_id: ID of the chunk to retrieve
            
        Returns:
            Optional[Chunk]: The chunk if found, None otherwise
        """
        async with self._lock:
            if chunk_id in self._chunk_index:
                index = self._chunk_index[chunk_id]
                return self._chunks[index]
            return None
    
    async def check_chunk_exists(self, chunk_id: str) -> bool:
        """
        Check if a chunk with given chunk_id exists in storage.
        
        Args:
            chunk_id: ID of the chunk to check
            
        Returns:
            bool: True if chunk exists, False otherwise
        """
        async with self._lock:
            return chunk_id in self._chunk_index
    
    async def search_chunks(self, search_filter: Dict[str, Any]) -> List[Chunk]:
        """
        Search for chunks based on filter conditions.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            List[Chunk]: List of matching chunks
        """
        async with self._lock:
            matching_chunks = []
            for chunk in self._chunks:
                if self._chunk_matches_filter(chunk, search_filter):
                    matching_chunks.append(chunk)
            return matching_chunks
    
    async def delete_chunk(self, chunk_id: str) -> bool:
        """
        Delete a chunk by its ID.
        
        Args:
            chunk_id: ID of the chunk to delete
            
        Returns:
            bool: True if chunk was deleted, False if not found
        """
        async with self._lock:
            if chunk_id not in self._chunk_index:
                return False
            
            index = self._chunk_index[chunk_id]
            del self._chunks[index]
            del self._chunk_index[chunk_id]
            
            # Rebuild index for chunks after the deleted one
            for i in range(index, len(self._chunks)):
                self._chunk_index[self._chunks[i].chunk_id] = i
            
            logger.debug(f"🗑️ Deleted chunk: {chunk_id}")
            return True
    
    async def get_all_chunks(self) -> List[Chunk]:
        """
        Retrieve all chunks from storage.
        
        Returns:
            List[Chunk]: List of all chunks
        """
        async with self._lock:
            return self._chunks.copy()
    
    async def clear(self) -> None:
        """Clear all chunks from storage."""
        async with self._lock:
            self._chunks.clear()
            self._chunk_index.clear()
            logger.debug("🧹 Cleared all chunks from storage")
    
    async def get_chunk_count(self) -> int:
        """
        Get the total number of chunks in storage.
        
        Returns:
            int: Total number of chunks
        """
        async with self._lock:
            return len(self._chunks)
    
    async def get_artifact_chunk_counts(self, search_filter: Dict[str, Any]) -> Dict[str, int]:
        """
        Get chunk counts grouped by artifact_id from in-memory storage.
        
        This method filters chunks in memory and groups them by artifact_id.
        For in-memory storage, this is still efficient as we avoid loading
        chunk content, only processing metadata.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            Dict[str, int]: Dictionary mapping artifact_id to chunk count
        """
        async with self._lock:
            try:
                from collections import Counter
                
                # Filter chunks that match the search criteria
                matching_chunks = [
                    chunk for chunk in self._chunks 
                    if self._chunk_matches_filter(chunk, search_filter)
                ]
                
                # Count chunks per artifact_id using Counter
                artifact_counts = Counter(chunk.chunk_metadata.artifact_id for chunk in matching_chunks)
                
                logger.debug(f"📊 内存存储查询完成: 找到 {len(artifact_counts)} 个 artifacts 的统计信息")
                return dict(artifact_counts)
                
            except Exception as e:
                logger.error(f"❌ Failed to get artifact chunk counts: {e}")
                return {}
    
    async def get_artifact_chunks_by_range(self, artifact_id: str, start_index: int, end_index: int) -> List[Chunk]:
        """
        Get chunks for a specific artifact within a range of chunk indices.
        
        This method efficiently retrieves only the chunks within the specified range
        from in-memory storage, avoiding loading all chunks for the artifact.
        
        Args:
            artifact_id: ID of the artifact
            start_index: Start chunk index (inclusive)
            end_index: End chunk index (exclusive)
            
        Returns:
            List[Chunk]: List of chunks within the specified range, sorted by chunk_index
        """
        try:
            # 🚀 Filter chunks by artifact_id and range in memory
            matching_chunks = []
            for chunk in self._chunks:
                if (chunk.chunk_metadata.artifact_id == artifact_id and 
                    start_index <= chunk.chunk_metadata.chunk_index < end_index):
                    matching_chunks.append(chunk)
            
            # Sort by chunk_index to maintain order
            matching_chunks.sort(key=lambda x: x.chunk_metadata.chunk_index)
            
            logger.debug(f"🔍 Found {len(matching_chunks)} chunks for artifact {artifact_id} in range [{start_index}, {end_index})")
            return matching_chunks
            
        except Exception as e:
            logger.error(f"❌ Failed to get artifact chunks by range: {e}")
            return []
    
    def _chunk_matches_filter(self, chunk: Chunk, search_filter: Dict[str, Any]) -> bool:
        """
        Check if a chunk matches the given filter conditions.
        
        Args:
            chunk: Chunk object to check
            search_filter: Dictionary containing filter conditions
            
        Returns:
            bool: True if chunk matches all filter conditions, False otherwise
        """
        try:
            for key, value in search_filter.items():
                # Get the corresponding value from chunk metadata
                if hasattr(chunk.chunk_metadata, key):
                    metadata_value = getattr(chunk.chunk_metadata, key)
                    # Compare values
                    if metadata_value != value:
                        return False
                else:
                    # If filter key doesn't exist in metadata, consider it a mismatch
                    return False
            return True
        except Exception as e:
            logger.warning(f"❌ Error checking chunk filter match: {e}")
            return False 