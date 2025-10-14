import traceback
from typing import Any, Dict

from aworld.logs.util import logger
from ... import ApplicationContext
from .base import BaseOp, MemoryCommand
from .op_factory import memory_op
from ...retrieval.graph.base import GraphMemoryNode, GraphMemoryEdge
from ...retrieval.graph.factory import graph_db_factory

@memory_op("save_graph_memory")
class SaveGraphMemoryOp(BaseOp):
    """SaveMemory"""

    def __init__(self, name: str = "save_graph_memory", **kwargs):
        super().__init__(name, **kwargs)

    async def execute(self, context: ApplicationContext, info: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]:
        memory_commands: list[MemoryCommand] = info.get("memory_commands")
        if not memory_commands:
            return {}

        for memory_command in memory_commands:
            try:
                if memory_command.type == "ADD_NODE":
                    await self._add_graph_node(context, memory_command.item)
                    logger.info(f"🔗 add graph node -> {memory_command.item}")
                elif memory_command.type == "ADD_EDGE":
                    await self._add_graph_edge(context, memory_command.item)
                    logger.info(f"🔗 add graph edge -> {memory_command.item}")
                else:
                    logger.info("⚠️ unprocess")
            except Exception as e:
                logger.error(f"❌ Error processing memory command {memory_command}: {e}")

        logger.info(f"✅ succeed save graph memory {memory_commands}")
        return {'memory_commands': []}

    async def _add_graph_node(self, context, node_data: GraphMemoryNode):
        """添加图节点"""
        try:
            graph_store = graph_db_factory.get_graph_db()
            if graph_store:
                node_id = node_data.id
                properties = node_data.properties
                await graph_store.upsert_node(namespace=context.session_id, node_id=node_id, node_data=properties)
        except Exception as e:
            logger.error(f"Error adding graph node: {e} {traceback.format_exc()}")
            # 确保在错误情况下也能继续处理其他节点
            pass

    async def _add_graph_edge(self, context, edge_data: GraphMemoryEdge):
        """添加图边"""
        try:
            graph_store = graph_db_factory.get_graph_db()
            if graph_store:
                source_id = edge_data.source_id
                target_id = edge_data.target_id
                properties = edge_data.properties
                await graph_store.upsert_edge(namespace=context.session_id, source_node_id=source_id, target_node_id=target_id, properties=properties)
        except Exception as e:
            logger.error(f"Error adding graph edge: {edge_data} reason: {e}")
            # 确保在错误情况下也能继续处理其他边
            pass
