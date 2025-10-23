"""
Abstract ChunkStore interface and factory for AmniContext.

This module defines the contract for chunk storage implementations.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from ..base import Chunk


class ChunkStoreConfig(BaseModel):
    provider: str = "sqlite"
    config: Optional[dict] = Field(default_factory=dict)


class ChunkStore(ABC):
    """
    Abstract base class for chunk storage implementations.
    
    This interface defines the contract that all chunk storage implementations
    must follow, providing methods for storing, retrieving, and searching chunks.
    """
    
    @abstractmethod
    async def upsert_chunk(self, chunk: Chunk) -> None:
        """
        Upsert a chunk to storage. If chunk with same chunk_id exists, update it; otherwise insert new one.
        
        Args:
            chunk: Chunk object to be upserted
        """
        pass
    
    @abstractmethod
    async def upsert_chunks_batch(self, chunks: List[Chunk]) -> None:
        """
        Upsert multiple chunks to storage in a batch operation.
        
        Args:
            chunks: List of Chunk objects to be upserted
        """
        pass
    
    @abstractmethod
    async def get_chunk(self, chunk_id: str) -> Optional[Chunk]:
        """
        Retrieve a chunk by its ID.
        
        Args:
            chunk_id: ID of the chunk to retrieve
            
        Returns:
            Optional[Chunk]: The chunk if found, None otherwise
        """
        pass
    
    @abstractmethod
    async def check_chunk_exists(self, chunk_id: str) -> bool:
        """
        Check if a chunk with given chunk_id exists in storage.
        
        Args:
            chunk_id: ID of the chunk to check
            
        Returns:
            bool: True if chunk exists, False otherwise
        """
        pass
    
    @abstractmethod
    async def search_chunks(self, search_filter: Dict[str, Any]) -> Optional[List[Chunk]]:
        """
        Search for chunks based on filter conditions.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            Optional[List[Chunk]]: List of matching chunks or None if no chunks found
        """
        pass
    
    @abstractmethod
    async def delete_chunk(self, chunk_id: str) -> bool:
        """
        Delete a chunk by its ID.
        
        Args:
            chunk_id: ID of the chunk to delete
            
        Returns:
            bool: True if chunk was deleted, False if not found
        """
        pass
    
    @abstractmethod
    async def get_all_chunks(self) -> List[Chunk]:
        """
        Retrieve all chunks from storage.
        
        Returns:
            List[Chunk]: List of all chunks
        """
        pass
    
    @abstractmethod
    async def clear(self) -> None:
        """
        Clear all chunks from storage.
        """
        pass
    
    @abstractmethod
    async def get_chunk_count(self) -> int:
        """
        Get the total number of chunks in storage.
        
        Returns:
            int: Total number of chunks
        """
        pass
    
    @abstractmethod
    async def get_artifact_chunk_counts(self, search_filter: Dict[str, Any]) -> Dict[str, int]:
        """
        Get chunk counts grouped by artifact_id directly from database.
        
        This method performs a GROUP BY query at the database level to efficiently
        count chunks per artifact without loading all chunks into memory.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            Dict[str, int]: Dictionary mapping artifact_id to chunk count
        """
        pass
    
    @abstractmethod
    async def get_artifact_chunks_by_range(self, artifact_id: str, start_index: int, end_index: int) -> List[Chunk]:
        """
        Get chunks for a specific artifact within a range of chunk indices.
        
        This method efficiently retrieves only the chunks within the specified range
        without loading all chunks for the artifact.
        
        Args:
            artifact_id: ID of the artifact
            start_index: Start chunk index (inclusive)
            end_index: End chunk index (exclusive)
            
        Returns:
            List[Chunk]: List of chunks within the specified range, sorted by chunk_index
        """
        pass


class ChunkStoreFactory:
    """
    Factory class for creating ChunkStore instances.
    
    This factory provides a centralized way to create different types of chunk storage
    implementations based on configuration.
    """
    
    _stores = {}
    
    @classmethod
    def register_store(cls, store_type: str, store_class: type) -> None:
        """
        Register a new chunk store implementation.
        
        Args:
            store_type: String identifier for the store type
            store_class: Class implementing the ChunkStore interface
        """
        cls._stores[store_type] = store_class
    
    @classmethod
    def get_store(cls, store_type: str, **kwargs) -> ChunkStore:
        """
        Create and return a ChunkStore instance of the specified type.
        
        Args:
            store_type: String identifier for the store type
            **kwargs: Additional arguments to pass to the store constructor
            
        Returns:
            ChunkStore: Instance of the requested store type
            
        Raises:
            ValueError: If store_type is not registered
        """
        if store_type not in cls._stores:
            raise ValueError(f"Unknown store type: {store_type}. Available types: {list(cls._stores.keys())}")
        
        store_class = cls._stores[store_type]
        return store_class(**kwargs)
    
    @classmethod
    def get_available_stores(cls) -> List[str]:
        """
        Get list of available store types.
        
        Returns:
            List[str]: List of registered store type names
        """
        return list(cls._stores.keys()) 