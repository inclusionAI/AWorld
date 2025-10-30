"""
Storage module for AmniContext.

This module provides abstract storage interfaces and implementations for chunks and other data.
"""

from .chunk_store import ChunkStore, ChunkStoreFactory
from .in_memory_store import InMemoryChunkStore
from .sqlite_store import SQLiteChunkStore

# Try to import PostgreSQL store (optional dependency)
try:
    from .postgres_store import PostgreSQLChunkStore
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

# Register available store implementations
ChunkStoreFactory.register_store("memory", InMemoryChunkStore)
ChunkStoreFactory.register_store("sqlite", SQLiteChunkStore)

if POSTGRES_AVAILABLE:
    ChunkStoreFactory.register_store("postgres", PostgreSQLChunkStore)

__all__ = [
    "ChunkStore",
    "ChunkStoreFactory", 
    "InMemoryChunkStore",
    "SQLiteChunkStore",
]

if POSTGRES_AVAILABLE:
    __all__.append("PostgreSQLChunkStore") 