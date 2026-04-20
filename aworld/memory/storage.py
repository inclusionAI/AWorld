"""
Memory storage module using SQLite.
Handles database schema, indexing, and querying.
"""

import sqlite3
import json
import hashlib
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class MemoryStorage:
    """SQLite-based storage for memory chunks and embeddings."""

    def __init__(self, db_path: str, cache_enabled: bool = True, fts_enabled: bool = True):
        """Initialize storage with database path."""
        self.db_path = db_path
        self.cache_enabled = cache_enabled
        self.fts_enabled = fts_enabled
        self.conn: Optional[sqlite3.Connection] = None
        self._ensure_database()

    def _ensure_database(self):
        """Ensure database exists and has correct schema."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self):
        """Create database schema."""
        cursor = self.conn.cursor()

        # Meta table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Files table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'memory',
                hash TEXT NOT NULL,
                mtime INTEGER NOT NULL,
                size INTEGER NOT NULL
            )
        """)

        # Chunks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'memory',
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                hash TEXT NOT NULL,
                model TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)")

        # Embedding cache table
        if self.cache_enabled:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider_key TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    dims INTEGER,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (provider, model, provider_key, hash)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_embedding_cache_updated_at ON embedding_cache(updated_at)"
            )

        # FTS5 full-text search table
        if self.fts_enabled:
            try:
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                        text,
                        id UNINDEXED,
                        path UNINDEXED,
                        source UNINDEXED,
                        model UNINDEXED,
                        start_line UNINDEXED,
                        end_line UNINDEXED
                    )
                """)
                logger.info("FTS5 full-text search enabled")
            except sqlite3.OperationalError as e:
                logger.warning(f"FTS5 not available: {e}")
                self.fts_enabled = False

        self.conn.commit()

    def get_file_hash(self, path: str, source: str = "memory") -> Optional[str]:
        """Get stored hash for a file."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT hash FROM files WHERE path = ? AND source = ?", (path, source))
        row = cursor.fetchone()
        return row["hash"] if row else None

    def upsert_file(self, path: str, file_hash: str, mtime: int, size: int, source: str = "memory"):
        """Insert or update file metadata."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO files (path, source, hash, mtime, size)
            VALUES (?, ?, ?, ?, ?)
            """,
            (path, source, file_hash, mtime, size),
        )
        self.conn.commit()

    def delete_file_chunks(self, path: str, source: str = "memory"):
        """Delete all chunks for a file."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM chunks WHERE path = ? AND source = ?", (path, source))
        if self.fts_enabled:
            try:
                cursor.execute("DELETE FROM chunks_fts WHERE path = ? AND source = ?", (path, source))
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def insert_chunk(
        self,
        chunk_id: str,
        path: str,
        source: str,
        start_line: int,
        end_line: int,
        chunk_hash: str,
        model: str,
        text: str,
        embedding: List[float],
    ):
        """Insert a chunk with its embedding."""
        cursor = self.conn.cursor()
        embedding_json = json.dumps(embedding)
        updated_at = int(time.time())

        # Insert into chunks table
        cursor.execute(
            """
            INSERT OR REPLACE INTO chunks 
            (id, path, source, start_line, end_line, hash, model, text, embedding, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chunk_id, path, source, start_line, end_line, chunk_hash, model, text, embedding_json, updated_at),
        )

        # Insert into FTS table
        if self.fts_enabled:
            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO chunks_fts 
                    (text, id, path, source, model, start_line, end_line)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (text, chunk_id, path, source, model, start_line, end_line),
                )
            except sqlite3.OperationalError:
                pass

        self.conn.commit()

    def search_vector(
        self, query_embedding: List[float], limit: int = 10, source_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search using vector similarity (cosine similarity)."""
        cursor = self.conn.cursor()
        
        # Build source filter
        source_clause = ""
        params: List[Any] = []
        if source_filter:
            source_clause = "WHERE source = ?"
            params.append(source_filter)
        
        # Fetch all chunks
        cursor.execute(f"SELECT * FROM chunks {source_clause}", params)
        rows = cursor.fetchall()

        # Calculate cosine similarity
        results = []
        for row in rows:
            embedding = json.loads(row["embedding"])
            score = self._cosine_similarity(query_embedding, embedding)
            results.append({
                "id": row["id"],
                "path": row["path"],
                "source": row["source"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "text": row["text"],
                "score": score,
            })

        # Sort by score and return top results
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def search_keyword(
        self, query: str, limit: int = 10, source_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search using full-text search."""
        if not self.fts_enabled:
            return []

        cursor = self.conn.cursor()
        
        # Build FTS query
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        # Build source filter
        source_clause = ""
        params: List[Any] = [fts_query]
        if source_filter:
            source_clause = "AND source = ?"
            params.append(source_filter)

        try:
            cursor.execute(
                f"""
                SELECT id, path, source, start_line, end_line, text, 
                       bm25(chunks_fts) as text_score
                FROM chunks_fts
                WHERE chunks_fts MATCH ? {source_clause}
                ORDER BY text_score
                LIMIT ?
                """,
                params + [limit],
            )
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                # Convert BM25 rank to score (0-1 range)
                text_score = self._bm25_rank_to_score(row["text_score"])
                results.append({
                    "id": row["id"],
                    "path": row["path"],
                    "source": row["source"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "text": row["text"],
                    "score": text_score,
                    "text_score": text_score,
                })
            
            return results
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS search failed: {e}")
            return []

    def get_cached_embedding(
        self, provider: str, model: str, provider_key: str, text_hash: str
    ) -> Optional[List[float]]:
        """Get cached embedding if available."""
        if not self.cache_enabled:
            return None

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT embedding FROM embedding_cache
            WHERE provider = ? AND model = ? AND provider_key = ? AND hash = ?
            """,
            (provider, model, provider_key, text_hash),
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row["embedding"])
        return None

    def cache_embedding(
        self,
        provider: str,
        model: str,
        provider_key: str,
        text_hash: str,
        embedding: List[float],
        dims: int,
    ):
        """Cache an embedding."""
        if not self.cache_enabled:
            return

        cursor = self.conn.cursor()
        embedding_json = json.dumps(embedding)
        updated_at = int(time.time())
        cursor.execute(
            """
            INSERT OR REPLACE INTO embedding_cache
            (provider, model, provider_key, hash, embedding, dims, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (provider, model, provider_key, text_hash, embedding_json, dims, updated_at),
        )
        self.conn.commit()

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    def _build_fts_query(self, query: str) -> Optional[str]:
        """Build FTS5 query from user query."""
        # Simple implementation: quote the query
        cleaned = query.strip()
        if not cleaned:
            return None
        # Escape quotes
        escaped = cleaned.replace('"', '""')
        return f'"{escaped}"'

    def _bm25_rank_to_score(self, rank: float) -> float:
        """Convert BM25 rank to 0-1 score."""
        # BM25 returns negative values, lower is better
        # Convert to 0-1 range where 1 is best
        return 1.0 / (1.0 + abs(rank))

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
