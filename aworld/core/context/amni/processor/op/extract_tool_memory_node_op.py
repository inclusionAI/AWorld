import traceback
from typing import Dict, Any, List, Optional

from aworld.logs.util import logger
from . import memory_op, ExtractToolFactOp
from ... import ApplicationContext, ContextEvent
from .base import MemoryCommand
from ...retrieval.graph.base import GraphMemoryNode
from ...retrieval.graph.factory import graph_db_factory


@memory_op("extract_tool_memory_node")
class ExtractToolMemoryNodeOp(ExtractToolFactOp[GraphMemoryNode]):

    def _prepare_extraction_text(self, context: ApplicationContext, agent_id: str, event: ContextEvent = None) -> str:
        graph_db = graph_db_factory.get_graph_db()
        if not graph_db:
            logger.info(f"skip extract_tool_memory_node because graph_db is None")
            return None

    def _get_few_shot_examples(self) -> List[Dict]:
        return [
            {
                "type": "tool_fact",
                "input": "工具名称: web_search\n动作名称: search\n参数: {'search_term': '苹果公司2024年第三季度财报营收数据'}\n结果: 苹果公司2024年第三季度营收达到948.4亿美元，同比增长1.4%。iPhone营收为459.6亿美元，服务营收为212.1亿美元，Mac营收为74.0亿美元，iPad营收为57.9亿美元。净利润为236.4亿美元。",
                "output": [
                    {
                        "type": "economic_financial",
                        "item": {
                            "name": "Apple",
                            "properties": {
                                "desc": "苹果公司2024年第三季度营收948.4亿美元，净利润236.4亿美元"
                            }
                        }
                    }
                ]
            },
        ]

    def _build_memory_item(self, type: str, extract_data: Dict[str, Any], context: ApplicationContext, agent_id: str) -> Optional[
        GraphMemoryNode]:
        try:
            if not extract_data or not isinstance(extract_data, dict):
                return None

            name = extract_data.get("name", "")
            properties = extract_data.get("properties", {})
            properties["label"] = type

            if not type or not name:
                return None

            return GraphMemoryNode(
                id=name,
                user_id=context.user_id,
                label=type,
                properties=properties
            )
        except Exception as e:
            logger.error(f"❌ Error building fact from extract data: {e} {traceback.format_exc()}")
            return None

    def _convert_extractions_to_memory_commands(self, extractions_result: Any, context: ApplicationContext,
                                                agent_id: str) -> List[MemoryCommand[GraphMemoryNode]]:
        memory_commands = []

        try:
            if not extractions_result.extractions:
                return []

            # 处理提取结果
            for extraction in extractions_result.extractions:
                if extraction.extraction_class in self.extraction_classes:
                    # 提取属性
                    attributes = extraction.attributes
                    type = attributes.get("type")
                    extract_data = attributes.get("item")
                    memory_id = attributes.get("memory_id")

                    memory_item = self._build_memory_item(type=type, extract_data=extract_data, context=context, agent_id=agent_id)
                    if not memory_item:
                        continue
                    # 创建 MemoryCommand
                    command = MemoryCommand(
                        memory_id=memory_id,
                        type="ADD_NODE",
                        item=memory_item
                    )
                    memory_commands.append(command)
        except Exception as e:
            logger.error(f"❌ Error converting extractions to memory commands: {e} {traceback.format_exc()}")

        return memory_commands
