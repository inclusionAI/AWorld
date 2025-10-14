"""
PostgreSQL implementation of ChunkStore for AmniContext.

This module provides a scalable PostgreSQL-based storage solution for chunks.
"""

import asyncio
import json
import logging
from typing import Optional, List, Dict, Any
from ..base import Chunk, ChunkMetadata
from .chunk_store import ChunkStore

logger = logging.getLogger(__name__)

try:
    import asyncpg
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    logger.warning("⚠️ asyncpg not available. PostgreSQL storage will not work.")


class PostgreSQLChunkStore(ChunkStore):
    """
    PostgreSQL implementation of ChunkStore.
    
    This implementation stores chunks in a PostgreSQL database for high-performance
    and scalability. It's suitable for production environments.
    """
    
    def __init__(self, config: dict, ) -> None:
        """
        Initialize the PostgreSQL chunk store.
        
        Args:
            connection_string: PostgreSQL connection string
            table_name: Name of the table to store chunks
            pool_size: Size of the connection pool
            max_overflow: Maximum number of connections that can be created beyond pool_size
        """
        if not POSTGRES_AVAILABLE:
            raise RuntimeError("PostgreSQL storage requires asyncpg package. Install with: pip install asyncpg")
        
        self.connection_string = config.get("connection_string")
        self.table_name = config.get("table_name", "chunks")
        self.pool_size = config.get("pool_size", 5)
        self.max_overflow = config.get("max_overflow", 10)
        self._pool: Optional[asyncpg.Pool] = None
        self._lock = asyncio.Lock()
        logger.debug(f"🚀 PostgreSQLChunkStore initialized with table: {self.table_name}")
    
    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create the connection pool."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.connection_string,
                min_size=5,
                max_size=self.pool_size + self.max_overflow
            )
        return self._pool
    
    async def _init_database(self) -> None:
        """Initialize the database and create tables if they don't exist."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # Create chunks table
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        chunk_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        chunk_metadata JSONB NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)
                
                # Create index on chunk_id for faster lookups
                await conn.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_chunk_id 
                    ON {self.table_name}(chunk_id)
                """)
                
                # Create GIN index on metadata for JSON queries
                await conn.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_metadata 
                    ON {self.table_name} USING GIN (chunk_metadata)
                """)
                
                logger.debug(f"✅ PostgreSQL database initialized successfully")
                
        except Exception as e:
            logger.error(f"❌ Failed to initialize PostgreSQL database: {e}")
            raise
    
    async def upsert_chunk(self, chunk: Chunk) -> None:
        """
        Upsert a chunk to storage. If chunk with same chunk_id exists, update it; otherwise insert new one.
        
        Args:
            chunk: Chunk object to be upserted
        """
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    # Use PostgreSQL's ON CONFLICT for upsert
                    await conn.execute(f"""
                        INSERT INTO {self.table_name} (chunk_id, content, chunk_metadata, updated_at)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (chunk_id) 
                        DO UPDATE SET 
                            content = EXCLUDED.content,
                            chunk_metadata = EXCLUDED.chunk_metadata,
                            updated_at = NOW()
                    """, chunk.chunk_id, chunk.content, json.dumps(chunk.chunk_metadata.model_dump()))
                    
                    logger.debug(f"🔄 Upserted chunk: {chunk.chunk_id}")
                    
            except Exception as e:
                logger.error(f"❌ Failed to upsert chunk {chunk.chunk_id}: {e}")
                raise
    
    async def upsert_chunks_batch(self, chunks: List[Chunk]) -> None:
        """
        Upsert multiple chunks to storage in a batch operation.
        
        Args:
            chunks: List of Chunk objects to be upserted
        """
        if not chunks:
            return
            
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    # Prepare batch data
                    batch_data = [
                        (chunk.chunk_id, chunk.content, json.dumps(chunk.chunk_metadata.model_dump()))
                        for chunk in chunks
                    ]
                    
                    # Use PostgreSQL's ON CONFLICT for batch upsert
                    await conn.executemany(f"""
                        INSERT INTO {self.table_name} (chunk_id, content, chunk_metadata, updated_at)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (chunk_id) 
                        DO UPDATE SET 
                            content = EXCLUDED.content,
                            chunk_metadata = EXCLUDED.chunk_metadata,
                            updated_at = NOW()
                    """, batch_data)
                    
                    logger.debug(f"🔄 Batch upsert completed: {len(chunks)} chunks processed")
                    
            except Exception as e:
                logger.error(f"❌ Failed to batch upsert chunks: {e}")
                raise
    
    async def get_chunk(self, chunk_id: str) -> Optional[Chunk]:
        """
        Retrieve a chunk by its ID.
        
        Args:
            chunk_id: ID of the chunk to retrieve
            
        Returns:
            Optional[Chunk]: The chunk if found, None otherwise
        """
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(f"""
                        SELECT chunk_id, content, chunk_metadata 
                        FROM {self.table_name} 
                        WHERE chunk_id = $1
                    """, chunk_id)
                    
                    if row:
                        chunk_id, content, metadata_json = row
                        metadata = ChunkMetadata.model_validate(metadata_json)
                        return Chunk(chunk_id=chunk_id, content=content, chunk_metadata=metadata)
                    return None
                    
            except Exception as e:
                logger.error(f"❌ Failed to get chunk {chunk_id}: {e}")
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
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    exists = await conn.fetchval(f"""
                        SELECT EXISTS(SELECT 1 FROM {self.table_name} WHERE chunk_id = $1)
                    """, chunk_id)
                    return exists
                    
            except Exception as e:
                logger.error(f"❌ Failed to check chunk existence {chunk_id}: {e}")
                return False
    
    async def search_chunks(self, search_filter: Dict[str, Any]) -> List[Chunk]:
        """
        Search for chunks based on filter conditions using PostgreSQL JSONB queries.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            List[Chunk]: List of matching chunks
        """
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    # Build JSONB query based on filter
                    where_conditions = []
                    query_params = []
                    param_count = 1
                    
                    for key, value in search_filter.items():
                        where_conditions.append(f"chunk_metadata->>'{key}' = ${param_count}")
                        query_params.append(str(value))
                        param_count += 1
                    
                    where_clause = " AND ".join(where_conditions) if where_conditions else "TRUE"
                    
                    query = f"""
                        SELECT chunk_id, content, chunk_metadata 
                        FROM {self.table_name} 
                        WHERE {where_clause}
                    """
                    
                    rows = await conn.fetch(query, *query_params)
                    
                    chunks = []
                    for row in rows:
                        chunk_id, content, metadata_json = row
                        metadata = ChunkMetadata.model_validate(metadata_json)
                        chunk = Chunk(chunk_id=chunk_id, content=content, chunk_metadata=metadata)
                        chunks.append(chunk)
                    
                    return chunks
                    
            except Exception as e:
                logger.error(f"❌ Failed to search chunks: {e}")
                return []
    
    async def delete_chunk(self, chunk_id: str) -> bool:
        """
        Delete a chunk by its ID.
        
        Args:
            chunk_id: ID of the chunk to delete
            
        Returns:
            bool: True if chunk was deleted, False if not found
        """
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    result = await conn.execute(f"""
                        DELETE FROM {self.table_name} WHERE chunk_id = $1
                    """, chunk_id)
                    
                    deleted = result != "DELETE 0"
                    if deleted:
                        logger.debug(f"🗑️ Deleted chunk: {chunk_id}")
                    return deleted
                    
            except Exception as e:
                logger.error(f"❌ Failed to delete chunk {chunk_id}: {e}")
                return False
    
    async def get_all_chunks(self) -> List[Chunk]:
        """
        Retrieve all chunks from storage.
        
        Returns:
            List[Chunk]: List of all chunks
        """
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    rows = await conn.fetch(f"""
                        SELECT chunk_id, content, chunk_metadata 
                        FROM {self.table_name}
                    """)
                    
                    chunks = []
                    for row in rows:
                        chunk_id, content, metadata_json = row
                        metadata = ChunkMetadata.model_validate(metadata_json)
                        chunk = Chunk(chunk_id=chunk_id, content=content, chunk_metadata=metadata)
                        chunks.append(chunk)
                    
                    return chunks
                    
            except Exception as e:
                logger.error(f"❌ Failed to get all chunks: {e}")
                return []
    
    async def clear(self) -> None:
        """Clear all chunks from storage."""
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    await conn.execute(f"DELETE FROM {self.table_name}")
                    logger.debug("🧹 Cleared all chunks from storage")
                    
            except Exception as e:
                logger.error(f"❌ Failed to clear chunks: {e}")
                raise
    
    async def get_chunk_count(self) -> int:
        """
        Get the total number of chunks in storage.
        
        Returns:
            int: Total number of chunks
        """
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    count = await conn.fetchval(f"SELECT COUNT(*) FROM {self.table_name}")
                    return count
                    
            except Exception as e:
                logger.error(f"❌ Failed to get chunk count: {e}")
                return 0
    
    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.debug("🔒 PostgreSQL connection pool closed")
    
    async def get_artifact_chunk_counts(self, search_filter: Dict[str, Any]) -> Dict[str, int]:
        """
        Get chunk counts grouped by artifact_id directly from PostgreSQL database.
        
        This method performs a GROUP BY query at the database level to efficiently
        count chunks per artifact without loading all chunks into memory.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            Dict[str, int]: Dictionary mapping artifact_id to chunk count
        """
        async with self._lock:
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    # Build WHERE conditions for JSONB queries
                    where_conditions = []
                    query_params = []
                    param_count = 1
                    
                    for key, value in search_filter.items():
                        where_conditions.append(f"chunk_metadata->>'{key}' = ${param_count}")
                        query_params.append(str(value))
                        param_count += 1
                    
                    where_clause = " AND ".join(where_conditions) if where_conditions else "TRUE"
                    
                    # Use GROUP BY to count chunks per artifact_id
                    query = f"""
                        SELECT 
                            chunk_metadata->>'artifact_id' as artifact_id,
                            COUNT(*) as chunk_count
                        FROM {self.table_name} 
                        WHERE {where_clause}
                        GROUP BY chunk_metadata->>'artifact_id'
                        ORDER BY chunk_count DESC
                    """
                    
                    rows = await conn.fetch(query, *query_params)
                    
                    # Convert results to dictionary
                    artifact_counts = {}
                    for row in rows:
                        artifact_id, chunk_count = row
                        if artifact_id:  # Skip None values
                            artifact_counts[artifact_id] = chunk_count
                    
                    logger.debug(f"📊 PostgreSQL 查询完成: 找到 {len(artifact_counts)} 个 artifacts 的统计信息")
                    return artifact_counts
                    
            except Exception as e:
                logger.error(f"❌ Failed to get artifact chunk counts: {e}")
                return {}
    
    async def get_artifact_chunks_by_range(self, artifact_id: str, start_index: int, end_index: int) -> List[Chunk]:
        """
        Get chunks for a specific artifact within a range of chunk indices.
        
        This method efficiently retrieves only the chunks within the specified range
        using PostgreSQL range queries, avoiding loading all chunks for the artifact.
        
        Args:
            artifact_id: ID of the artifact
            start_index: Start chunk index (inclusive)
            end_index: End chunk index (exclusive)
            
        Returns:
            List[Chunk]: List of chunks within the specified range, sorted by chunk_index
        """
        async with self._lock:
            try:
                async with self.pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        # 🚀 Efficient range query using PostgreSQL
                        query = f"""
                            SELECT chunk_id, content, chunk_metadata 
                            FROM {self.table_name} 
                            WHERE chunk_metadata->>'artifact_id' = %s
                            AND (chunk_metadata->>'chunk_index')::int >= %s
                            AND (chunk_metadata->>'chunk_index')::int < %s
                            ORDER BY (chunk_metadata->>'chunk_index')::int
                        """
                        
                        await cursor.execute(query, (artifact_id, start_index, end_index))
                        rows = await cursor.fetchall()
                        
                        chunks = []
                        for row in rows:
                            chunk_id, content, metadata_json = row
                            metadata = ChunkMetadata.model_validate(json.loads(metadata_json))
                            chunk = Chunk(chunk_id=chunk_id, content=content, chunk_metadata=metadata)
                            chunks.append(chunk)
                        
                        logger.debug(f"🔍 Found {len(chunks)} chunks for artifact {artifact_id} in range [{start_index}, {end_index})")
                        return chunks
                        
            except Exception as e:
                logger.error(f"❌ Failed to get artifact chunks by range: {e}")
                return [] 