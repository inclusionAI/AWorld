from typing import Optional

from amnicontext import get_amnicontext_config
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
        # 如果已经创建过实例，直接返回
        if self._graph_store is not None:
            return self._graph_store
            
        if graph_db_config is None:
            graph_db_config = get_amnicontext_config().graph_db_config
        if not graph_db_config:
            return None
            
        with self._lock:
            # 双重检查锁定模式
            if self._graph_store is not None:
                return self._graph_store
                
            if graph_db_config.provider == "pg":
                from .pg_graph_store import PGGraphStore
                try:
                    # 尝试创建连接并测试连通性
                    graph_store = PGGraphStore(graph_db_config.config)
                    # 测试连接是否可用
                    if hasattr(graph_store, 'test_connection'):
                        if not graph_store.test_connection():
                            return None
                    self._graph_store = graph_store
                    return self._graph_store
                except Exception:
                    # 连接失败时返回None
                    return None
            else:
                raise ValueError(f"Graph database {graph_db_config.provider} is not supported")

graph_db_factory = GraphDBFactory()