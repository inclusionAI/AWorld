"""
SQLite implementation of ChunkStore for AmniContext.

This module provides a persistent SQLite-based storage solution for chunks.
"""

import asyncio
import json
import logging
import sqlite3
import time
from typing import Optional, List, Dict, Any
from ..base import Chunk, ChunkMetadata
from .chunk_store import ChunkStore

logger = logging.getLogger(__name__)


class SQLiteChunkStore(ChunkStore):
    """
    SQLite implementation of ChunkStore.
    
    This implementation stores chunks in a SQLite database for persistence.
    It's suitable for small to medium-scale applications.
    """
    
    def __init__(self, config: dict) -> None:
        """
        Initialize the SQLite chunk store.
        
        Args:
            db_path: Path to SQLite database file, use ":memory:" for in-memory database
            table_name: Name of the table to store chunks
        """
        self.db_path = config.get("db_path", "./data/amni_context.db")
        self.table_name = config.get("table_name", "chunks")
        self._lock = asyncio.Lock()
        self._init_database()
        logger.debug(f"🚀 SQLiteChunkStore initialized with database: {self.db_path}")
    
    def _init_database(self) -> None:
        """Initialize the database and create tables if they don't exist."""
        try:
            print(f"当前SQLite版本: {sqlite3.sqlite_version}")
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 🚀 启用WAL模式提高写入性能
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA cache_size=10000")
                cursor.execute("PRAGMA temp_store=MEMORY")
                
                # Create chunks table
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        chunk_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        chunk_metadata TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Create index on chunk_id for faster lookups
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_chunk_id 
                    ON {self.table_name}(chunk_id)
                """)
                
                # Create JSON expression indexes for common metadata fields
                # Index for artifact_id queries (most common)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_artifact_id 
                    ON {self.table_name}(JSON_EXTRACT(chunk_metadata, '$.artifact_id'))
                """)
                
                # Index for workspace_id queries
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_workspace_biz_id
                    ON {self.table_name}(
                    JSON_EXTRACT(chunk_metadata, '$.workspace_id'),
                    JSON_EXTRACT(chunk_metadata, '$.biz_id'))
                """)
                
                # Index for chunk_index queries
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_chunk_index 
                    ON {self.table_name}(JSON_EXTRACT(chunk_metadata, '$.chunk_index'))
                """)
                
                # Index for artifact_type queries
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_artifact_type 
                    ON {self.table_name}(JSON_EXTRACT(chunk_metadata, '$.artifact_type'))
                """)

                # Index for created_at timestamp (for time-based queries)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_created_at 
                    ON {self.table_name}(created_at)
                """)
                
                conn.commit()
                logger.debug(f"✅ Database initialized successfully")
                
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}")
            raise
    
    async def upsert_chunk(self, chunk: Chunk) -> None:
        """
        Upsert a chunk to storage. If chunk with same chunk_id exists, update it; otherwise insert new one.
        
        Args:
            chunk: Chunk object to be upserted
        """
        async with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # Check if chunk exists
                    cursor.execute(f"SELECT chunk_id FROM {self.table_name} WHERE chunk_id = ?", (chunk.chunk_id,))
                    exists = cursor.fetchone() is not None
                    
                    if exists:
                        # Update existing chunk
                        cursor.execute(f"""
                            UPDATE {self.table_name} 
                            SET content = ?, chunk_metadata = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE chunk_id = ?
                        """, (chunk.content, json.dumps(chunk.chunk_metadata.model_dump()), chunk.chunk_id))
                        logger.debug(f"🔄 Updated existing chunk: {chunk.chunk_id}")
                    else:
                        # Insert new chunk
                        cursor.execute(f"""
                            INSERT INTO {self.table_name} (chunk_id, content, chunk_metadata)
                            VALUES (?, ?, ?)
                        """, (chunk.chunk_id, chunk.content, json.dumps(chunk.chunk_metadata.model_dump())))
                        logger.debug(f"➕ Inserted new chunk: {chunk.chunk_id}")
                    
                    conn.commit()
                    
            except Exception as e:
                logger.error(f"❌ Failed to upsert chunk {chunk.chunk_id}: {e}")
                raise
    
    async def upsert_chunks_batch(self, chunks: List[Chunk], batch_size: int = 200) -> None:
        """
        Upsert multiple chunks to storage in a batch operation.
        
        Args:
            chunks: List of Chunk objects to be upserted
            batch_size: Number of chunks to process in each batch (default: 200)
        """
        if not chunks:
            return
            
        start_time = time.time()
        total_chunks = len(chunks)
        
        # 🚀 如果chunks数量超过batch_size，分批处理
        if total_chunks > batch_size:
            logger.info(f"📦 分批处理: {total_chunks} chunks, 批次大小: {batch_size}")
            for i in range(0, total_chunks, batch_size):
                batch_chunks = chunks[i:i + batch_size]
                await self._process_batch(batch_chunks, i // batch_size + 1)
            return
        
        # 单批次处理
        await self._process_batch(chunks, 1)
    
    async def _process_batch(self, chunks: List[Chunk], batch_num: int) -> None:
        """
        Process a single batch of chunks.
        
        Args:
            chunks: List of Chunk objects to process
            batch_num: Batch number for logging
        """
        batch_start_time = time.time()
        
        async with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # 🚀 批量查询所有chunk_id是否存在
                    query_start = time.time()
                    chunk_ids = [chunk.chunk_id for chunk in chunks]
                    placeholders = ','.join(['?' for _ in chunk_ids])
                    cursor.execute(f"SELECT chunk_id FROM {self.table_name} WHERE chunk_id IN ({placeholders})", chunk_ids)
                    existing_ids = {row[0] for row in cursor.fetchall()}
                    query_time = time.time() - query_start
                    
                    # 分离需要更新和插入的chunks
                    chunks_to_update = []
                    chunks_to_insert = []
                    
                    for chunk in chunks:
                        if chunk.chunk_id in existing_ids:
                            chunks_to_update.append(chunk)
                        else:
                            chunks_to_insert.append(chunk)
                    
                    # 🚀 批量更新
                    update_time = 0
                    if chunks_to_update:
                        update_start = time.time()
                        update_data = [
                            (chunk.content, json.dumps(chunk.chunk_metadata.model_dump()), chunk.chunk_id)
                            for chunk in chunks_to_update
                        ]
                        cursor.executemany(f"""
                            UPDATE {self.table_name} 
                            SET content = ?, chunk_metadata = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE chunk_id = ?
                        """, update_data)
                        update_time = time.time() - update_start
                    
                    # 🚀 批量插入
                    insert_time = 0
                    if chunks_to_insert:
                        insert_start = time.time()
                        
                        # 🚀 预序列化所有metadata，减少重复计算
                        serialization_start = time.time()
                        insert_data = []
                        for chunk in chunks_to_insert:
                            metadata_json = json.dumps(chunk.chunk_metadata.model_dump())
                            insert_data.append((chunk.chunk_id, chunk.content, metadata_json))
                        serialization_time = time.time() - serialization_start
                        
                        # 🚀 使用executemany进行批量插入
                        db_insert_start = time.time()
                        cursor.executemany(f"""
                            INSERT INTO {self.table_name} (chunk_id, content, chunk_metadata)
                            VALUES (?, ?, ?)
                        """, insert_data)
                        db_insert_time = time.time() - db_insert_start
                        
                        insert_time = time.time() - insert_start
                        
                        logger.debug(f"🔧 批次{batch_num}插入详情 - 序列化耗时: {serialization_time:.3f}s, 数据库插入耗时: {db_insert_time:.3f}s")
                    
                    conn.commit()
                    batch_time = time.time() - batch_start_time
                    
                    logger.debug(f"🔄 批次{batch_num}完成: {len(chunks_to_update)} 个更新, {len(chunks_to_insert)} 个插入")
                    logger.debug(f"⏱️ 批次{batch_num}性能 - 总chunks: {len(chunks)}, 总耗时: {batch_time:.3f}s, "
                              f"查询耗时: {query_time:.3f}s, 更新耗时: {update_time:.3f}s, "
                              f"插入耗时: {insert_time:.3f}s, 平均: {batch_time/len(chunks)*1000:.2f}ms/chunk")
                    
            except Exception as e:
                logger.error(f"❌ 批次{batch_num}处理失败: {e}")
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
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        SELECT chunk_id, content, chunk_metadata 
                        FROM {self.table_name} 
                        WHERE chunk_id = ?
                    """, (chunk_id,))
                    
                    row = cursor.fetchone()
                    if row:
                        chunk_id, content, metadata_json = row
                        metadata = ChunkMetadata.model_validate(json.loads(metadata_json))
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
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT 1 FROM {self.table_name} WHERE chunk_id = ?", (chunk_id,))
                    return cursor.fetchone() is not None
                    
            except Exception as e:
                logger.error(f"❌ Failed to check chunk existence {chunk_id}: {e}")
                return False
    
    async def search_chunks(self, search_filter: Dict[str, Any]) -> List[Chunk]:
        """
        Search for chunks based on filter conditions using optimized SQL queries.
        
        This method uses JSON_EXTRACT to leverage SQLite's JSON capabilities and
        avoid loading all chunks into memory for filtering. Optimized for common
        query patterns with proper indexing support.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            List[Chunk]: List of matching chunks
        """
        async with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # Build optimized SQL query using JSON_EXTRACT with proper type handling
                    where_conditions = []
                    query_params = []
                    
                    for key, value in search_filter.items():
                        # Handle different data types properly for JSON extraction
                        if isinstance(value, (int, float)):
                            where_conditions.append(f"JSON_EXTRACT(chunk_metadata, '$.{key}') = ?")
                            query_params.append(value)
                        elif isinstance(value, str):
                            where_conditions.append(f"JSON_EXTRACT(chunk_metadata, '$.{key}') = ?")
                            query_params.append(value)
                        elif isinstance(value, bool):
                            where_conditions.append(f"JSON_EXTRACT(chunk_metadata, '$.{key}') = ?")
                            query_params.append(1 if value else 0)
                        else:
                            # Fallback to string conversion
                            where_conditions.append(f"JSON_EXTRACT(chunk_metadata, '$.{key}') = ?")
                            query_params.append(str(value))
                    
                    where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                    
                    # Add ORDER BY for consistent results and better performance
                    query = f"""
                        SELECT chunk_id, content, chunk_metadata 
                        FROM {self.table_name} 
                        WHERE {where_clause}
                        ORDER BY created_at DESC, chunk_id
                    """
                    
                    cursor.execute(query, query_params)
                    rows = cursor.fetchall()
                    
                    matching_chunks = []
                    for row in rows:
                        chunk_id, content, metadata_json = row
                        metadata = ChunkMetadata.model_validate(json.loads(metadata_json))
                        chunk = Chunk(chunk_id=chunk_id, content=content, chunk_metadata=metadata)
                        matching_chunks.append(chunk)
                    
                    logger.debug(f"🔍 Found {len(matching_chunks)} chunks matching filter: {search_filter}")
                    return matching_chunks
                    
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
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"DELETE FROM {self.table_name} WHERE chunk_id = ?", (chunk_id,))
                    conn.commit()
                    
                    deleted = cursor.rowcount > 0
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
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT chunk_id, content, chunk_metadata FROM {self.table_name}")
                    
                    chunks = []
                    for row in cursor.fetchall():
                        chunk_id, content, metadata_json = row
                        metadata = ChunkMetadata.model_validate(json.loads(metadata_json))
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
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"DELETE FROM {self.table_name}")
                    conn.commit()
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
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
                    return cursor.fetchone()[0]
                    
            except Exception as e:
                logger.error(f"❌ Failed to get chunk count: {e}")
                return 0
    
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
    
    async def get_artifact_chunk_counts(self, search_filter: Dict[str, Any]) -> Dict[str, int]:
        """
        Get chunk counts grouped by artifact_id directly from SQLite database.
        
        This method performs a GROUP BY query at the database level to efficiently
        count chunks per artifact without loading all chunks into memory.
        
        Args:
            search_filter: Dictionary containing filter conditions to match against chunk_metadata
            
        Returns:
            Dict[str, int]: Dictionary mapping artifact_id to chunk count
        """
        async with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # Build WHERE conditions for JSON_EXTRACT queries
                    where_conditions = []
                    query_params = []
                    param_count = 1
                    
                    for key, value in search_filter.items():
                        where_conditions.append(f"JSON_EXTRACT(chunk_metadata, '$.{key}') = ?")
                        query_params.append(str(value))
                        param_count += 1
                    
                    where_clause = " AND ".join(where_conditions) if where_conditions else "TRUE"
                    
                    # Use GROUP BY to count chunks per artifact_id
                    query = f"""
                        SELECT 
                            JSON_EXTRACT(chunk_metadata, '$.artifact_id') as artifact_id,
                            COUNT(*) as chunk_count
                        FROM {self.table_name} 
                        WHERE {where_clause}
                        GROUP BY JSON_EXTRACT(chunk_metadata, '$.artifact_id')
                        ORDER BY chunk_count DESC
                    """
                    
                    cursor.execute(query, query_params)
                    rows = cursor.fetchall()
                    
                    # Convert results to dictionary
                    artifact_counts = {}
                    for artifact_id, chunk_count in rows:
                        if artifact_id:  # Skip None values
                            artifact_counts[artifact_id] = chunk_count
                    
                    logger.debug(f"📊 SQLite 查询完成: 找到 {len(artifact_counts)} 个 artifacts 的统计信息")
                    return artifact_counts
                    
            except Exception as e:
                logger.error(f"❌ Failed to get artifact chunk counts: {e}")
                return {}
    
    async def get_artifact_chunks_by_range(self, artifact_id: str, start_index: int, end_index: int) -> List[Chunk]:
        """
        Get chunks for a specific artifact within a range of chunk indices.
        
        This method efficiently retrieves only the chunks within the specified range
        using SQL range queries, avoiding loading all chunks for the artifact.
        
        Args:
            artifact_id: ID of the artifact
            start_index: Start chunk index (inclusive)
            end_index: End chunk index (exclusive)
            
        Returns:
            List[Chunk]: List of chunks within the specified range, sorted by chunk_index
        """
        async with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # 🚀 Efficient range query using SQL
                    query = f"""
                        SELECT chunk_id, content, chunk_metadata 
                        FROM {self.table_name} 
                        WHERE JSON_EXTRACT(chunk_metadata, '$.artifact_id') = ?
                        AND JSON_EXTRACT(chunk_metadata, '$.chunk_index') >= ?
                        AND JSON_EXTRACT(chunk_metadata, '$.chunk_index') < ?
                        ORDER BY JSON_EXTRACT(chunk_metadata, '$.chunk_index')
                    """
                    
                    cursor.execute(query, (artifact_id, start_index, end_index))
                    rows = cursor.fetchall()
                    
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