"""Memory stores, loaded only when a specific backend is requested."""

from importlib import import_module

_BACKENDS = {
    "InMemoryMemoryStore": ".inmemory",
    "SQLiteMemoryStore": ".sqlite",
    "FileSystemMemoryStore": ".filesystem",
    "PostgresMemoryStore": ".postgres",
    "MySQLMemoryStore": ".mysql",
}

__all__ = ["InMemoryMemoryStore", "SQLiteMemoryStore", "FileSystemMemoryStore"]


def __getattr__(name):
    if name not in _BACKENDS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    backend = getattr(import_module(_BACKENDS[name], __name__), name)
    globals()[name] = backend
    return backend
