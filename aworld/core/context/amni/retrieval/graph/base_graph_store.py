from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .base import GraphDBConfig


@dataclass
class KnowledgeGraphNode:
    """Knowledge graph node"""
    id: str
    labels: List[str]
    properties: Dict[str, Any]


@dataclass
class KnowledgeGraphEdge:
    """Knowledge graph edge"""
    id: str
    type: str
    source: str
    target: str
    properties: Dict[str, Any]


@dataclass
class KnowledgeGraph:
    """Knowledge graph"""
    nodes: List[KnowledgeGraphNode] = None
    edges: List[KnowledgeGraphEdge] = None
    is_truncated: bool = False
    
    def __post_init__(self):
        if self.nodes is None:
            self.nodes = []
        if self.edges is None:
            self.edges = []


class BaseGraphStore(ABC):
    """Graph storage base class"""
    
    def __init__(self, graph_db_config: GraphDBConfig, **kwargs):
        self.graph_db_config = graph_db_config
    
    async def initialize(self):
        """Initialize storage"""
        pass
    
    async def finalize(self):
        """Clean up storage resources"""
        pass
    
    @abstractmethod
    async def has_node(self, namespace, node_id: str) -> bool:
        """Check if node exists
        
        Args:
            node_id: Node ID
            
        Returns:
            True if node exists, otherwise False
        """
        pass
    
    @abstractmethod
    async def has_edge(self, namespace, source_node_id: str, target_node_id: str) -> bool:
        """Check if edge exists between two nodes
        
        Args:
            source_node_id: Source node ID
            target_node_id: Target node ID
            
        Returns:
            True if edge exists, otherwise False
        """
        pass
    
    @abstractmethod
    async def get_node(self, namespace, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node by ID
        
        Args:
            node_id: Node ID
            
        Returns:
            Node property dictionary, returns None if not exists
        """
        pass
    
    @abstractmethod
    async def get_edge(self, namespace, source_node_id: str, target_node_id: str) -> Optional[Dict[str, Any]]:
        """Get edge between two nodes
        
        Args:
            source_node_id: Source node ID
            target_node_id: Target node ID
            
        Returns:
            Edge property dictionary, returns None if not exists
        """
        pass
    
    @abstractmethod
    async def get_node_edges(self, namespace, node_id: str) -> Optional[List[Tuple[str, str]]]:
        """Get all edges of a node
        
        Args:
            node_id: Node ID
            
        Returns:
            List of (source node ID, target node ID) tuples, returns None if node not exists
        """
        pass
    
    @abstractmethod
    async def upsert_node(self, namespace, node_id: str, node_data: Dict[str, Any]) -> None:
        """Insert or update node
        
        Args:
            node_id: Node ID
            node_data: Node property dictionary
        """
        pass
    
    @abstractmethod
    async def upsert_edge(self, namespace, source_node_id: str, target_node_id: str, edge_data: Dict[str, Any]) -> None:
        """Insert or update edge
        
        Args:
            source_node_id: Source node ID
            target_node_id: Target node ID
            edge_data: Edge property dictionary
        """
        pass

    @abstractmethod
    async def remove_nodes(self, namespace, node_ids: List[str]) -> None:
        """Delete multiple nodes
        
        Args:
            node_ids: List of node IDs
        """
        pass
    
    @abstractmethod
    async def remove_edges(self, namespace, edges: List[Tuple[str, str]]) -> None:
        """Delete multiple edges
        
        Args:
            edges: List of edges, each edge is a (source node ID, target node ID) tuple
        """
        pass
    
    # Batch operation methods (optional implementation, base class provides default implementation)
    async def get_nodes_batch(self, namespace, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch get nodes
        
        Args:
            node_ids: List of node IDs
            
        Returns:
            Mapping from node ID to node property dictionary
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
        """Batch get edges
        
        Args:
            pairs: List of edge pairs, each element contains "src" and "tgt" keys
            
        Returns:
            Mapping from (source node ID, target node ID) to edge property dictionary
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
        """Batch get edges of nodes
        
        Args:
            node_ids: List of node IDs
            
        Returns:
            Mapping from node ID to edge list
        """
        result = {}
        for node_id in node_ids:
            edges = await self.get_node_edges(namespace=namespace, node_id=node_id)
            result[node_id] = edges if edges is not None else []
        return result

    async def get_related_nodes(self, namespace, node_id: str, max_depth: int = 2, limit: int = 10) -> List[str]:
        """Get list of node IDs related to specified node, supports multi-level queries
        
        Args:
            node_id: Node ID to query related nodes
            max_depth: Maximum query depth, defaults to 2
            limit: Result count limit, defaults to 10
            
        Returns:
            List[str]: List of related node IDs
        """
        # Default implementation: only get directly related nodes
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
        """Callback after indexing completion, used for data persistence"""
        pass

