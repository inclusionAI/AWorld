"""
Database implementations for memory storage.
"""

from .sqlite import SQLiteMemoryStore

# PostgresMemoryStore is optional and requires SQLAlchemy
__all__ = ["SQLiteMemoryStore"]

try:
    from .postgres import PostgresMemoryStore
    __all__.append("PostgresMemoryStore")
except ImportError:
    # SQLAlchemy not installed, PostgresMemoryStore will not be available
    pass
