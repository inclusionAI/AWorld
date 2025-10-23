from typing import Optional

from .base import GraphDBConfig
from .base_graph_store import BaseGraphStore


class GraphDBFactory:
    _instance = None
    _graph_store = None
    _lock = None

    def __new__(cls):
        if cls._instance is None:
            import threading
            cls._lock = threading.Lock()
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(GraphDBFactory, cls).__new__(cls)
        return cls._instance

    def get_graph_db(self, graph_db_config: GraphDBConfig = None) -> Optional[BaseGraphStore]:
        # If instance has already been created, return directly
        if self._graph_store is not None:
            return self._graph_store

        if not graph_db_config:
            return None
            
        with self._lock:
            # Double-check locking pattern
            if self._graph_store is not None:
                return self._graph_store
                
            if graph_db_config.provider == "pg":
                from .pg_graph_store import PGGraphStore
                try:
                    # Try to create connection and test connectivity
                    graph_store = PGGraphStore(graph_db_config.config)
                    # Test if connection is available
                    if hasattr(graph_store, 'test_connection'):
                        if not graph_store.test_connection():
                            return None
                    self._graph_store = graph_store
                    return self._graph_store
                except Exception:
                    # Return None when connection fails
                    return None
            else:
                raise ValueError(f"Graph database {graph_db_config.provider} is not supported")

graph_db_factory = GraphDBFactory()