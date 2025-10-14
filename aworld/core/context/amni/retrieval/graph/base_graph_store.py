from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .base import GraphDBConfig


@dataclass
class KnowledgeGraphNode:
    """知识图谱节点"""
    id: str
    labels: List[str]
    properties: Dict[str, Any]


@dataclass
class KnowledgeGraphEdge:
    """知识图谱边"""
    id: str
    type: str
    source: str
    target: str
    properties: Dict[str, Any]


@dataclass
class KnowledgeGraph:
    """知识图谱"""
    nodes: List[KnowledgeGraphNode] = None
    edges: List[KnowledgeGraphEdge] = None
    is_truncated: bool = False
    
    def __post_init__(self):
        if self.nodes is None:
            self.nodes = []
        if self.edges is None:
            self.edges = []


class BaseGraphStore(ABC):
    """图存储基类"""
    
    def __init__(self, graph_db_config: GraphDBConfig, **kwargs):
        self.graph_db_config = graph_db_config
    
    async def initialize(self):
        """初始化存储"""
        pass
    
    async def finalize(self):
        """清理存储资源"""
        pass
    
    @abstractmethod
    async def has_node(self, namespace, node_id: str) -> bool:
        """检查节点是否存在
        
        Args:
            node_id: 节点ID
            
        Returns:
            True如果节点存在，否则False
        """
        pass
    
    @abstractmethod
    async def has_edge(self, namespace, source_node_id: str, target_node_id: str) -> bool:
        """检查两个节点之间是否存在边
        
        Args:
            source_node_id: 源节点ID
            target_node_id: 目标节点ID
            
        Returns:
            True如果边存在，否则False
        """
        pass
    
    @abstractmethod
    async def get_node(self, namespace, node_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取节点
        
        Args:
            node_id: 节点ID
            
        Returns:
            节点属性字典，如果不存在则返回None
        """
        pass
    
    @abstractmethod
    async def get_edge(self, namespace, source_node_id: str, target_node_id: str) -> Optional[Dict[str, Any]]:
        """获取两个节点之间的边
        
        Args:
            source_node_id: 源节点ID
            target_node_id: 目标节点ID
            
        Returns:
            边属性字典，如果不存在则返回None
        """
        pass
    
    @abstractmethod
    async def get_node_edges(self, namespace, node_id: str) -> Optional[List[Tuple[str, str]]]:
        """获取节点的所有边
        
        Args:
            node_id: 节点ID
            
        Returns:
            (源节点ID, 目标节点ID)元组列表，如果节点不存在则返回None
        """
        pass
    
    @abstractmethod
    async def upsert_node(self, namespace, node_id: str, node_data: Dict[str, Any]) -> None:
        """插入或更新节点
        
        Args:
            node_id: 节点ID
            node_data: 节点属性字典
        """
        pass
    
    @abstractmethod
    async def upsert_edge(self, namespace, source_node_id: str, target_node_id: str, edge_data: Dict[str, Any]) -> None:
        """插入或更新边
        
        Args:
            source_node_id: 源节点ID
            target_node_id: 目标节点ID
            edge_data: 边属性字典
        """
        pass

    @abstractmethod
    async def remove_nodes(self, namespace, node_ids: List[str]) -> None:
        """删除多个节点
        
        Args:
            node_ids: 节点ID列表
        """
        pass
    
    @abstractmethod
    async def remove_edges(self, namespace, edges: List[Tuple[str, str]]) -> None:
        """删除多个边
        
        Args:
            edges: 边列表，每个边是(源节点ID, 目标节点ID)元组
        """
        pass
    
    # 批量操作方法（可选实现，基类提供默认实现）
    async def get_nodes_batch(self, namespace, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取节点
        
        Args:
            node_ids: 节点ID列表
            
        Returns:
            节点ID到节点属性字典的映射
        """
        result = {}
        for node_id in node_ids:
            node = await self.get_node(node_id)
            if node is not None:
                result[node_id] = node
        return result

    async def get_all_nodes(self, namespace) -> list[dict]:
        pass

    async def get_edges_batch(self, namespace, pairs: List[Dict[str, str]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """批量获取边
        
        Args:
            pairs: 边对列表，每个元素包含"src"和"tgt"键
            
        Returns:
            (源节点ID, 目标节点ID)到边属性字典的映射
        """
        result = {}
        for pair in pairs:
            src_id = pair["src"]
            tgt_id = pair["tgt"]
            edge = await self.get_edge(src_id, tgt_id)
            if edge is not None:
                result[(src_id, tgt_id)] = edge
        return result
    
    async def get_nodes_edges_batch(self, namespace, node_ids: List[str]) -> Dict[str, List[Tuple[str, str]]]:
        """批量获取节点的边
        
        Args:
            node_ids: 节点ID列表
            
        Returns:
            节点ID到边列表的映射
        """
        result = {}
        for node_id in node_ids:
            edges = await self.get_node_edges(namespace=namespace, node_id=node_id)
            result[node_id] = edges if edges is not None else []
        return result

    async def get_related_nodes(self, namespace, node_id: str, max_depth: int = 2, limit: int = 10) -> List[str]:
        """获取与指定节点相关的节点ID列表，支持多层级查询
        
        Args:
            node_id: 要查询关联节点的节点ID
            max_depth: 最大查询深度，默认为2
            limit: 返回结果数量限制，默认为10
            
        Returns:
            List[str]: 关联节点ID列表
        """
        # 默认实现：只获取直接关联节点
        edges = await self.get_node_edges(namespace=namespace, node_id=node_id)
        if not edges:
            return []
        
        related_node_ids = set()
        for source_id, target_id in edges:
            if source_id != node_id:
                related_node_ids.add(source_id)
            if target_id != node_id:
                related_node_ids.add(target_id)
        
        return list(related_node_ids)[:limit]

    async def index_done_callback(self) -> None:
        """索引完成后的回调，用于持久化数据"""
        pass

