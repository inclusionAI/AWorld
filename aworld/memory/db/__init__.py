"""
Database implementations for memory storage.
"""

from .sqlite import SQLiteMemoryStore

# PostgresMemoryStore and MySQLMemoryStore are optional and require SQLAlchemy
__all__ = ["SQLiteMemoryStore"]

try:
    from .postgres import PostgresMemoryStore
    __all__.append("PostgresMemoryStore")
except ImportError:
    # SQLAlchemy not installed, PostgresMemoryStore will not be available
    pass

try:
    from .mysql import MySQLMemoryStore
    __all__.append("MySQLMemoryStore")
except ImportError:
    # SQLAlchemy not installed, MySQLMemoryStore will not be available
    pass
