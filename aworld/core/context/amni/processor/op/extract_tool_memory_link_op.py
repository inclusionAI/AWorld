from typing import Any, Dict, List

from . import MemoryCommand
from ...event import ToolResultEvent
from ...retrieval.graph.base import GraphMemoryEdge, GraphMemoryNode
from ...retrieval.graph.factory import graph_db_factory


from aworld.logs.util import logger
from .llm_extract_op import LlmExtractOp
from .op_factory import memory_op
from ... import ApplicationContext


@memory_op("extract_tool_memory_link")
class ExtractToolMemoryLinkOp(LlmExtractOp):
    """
    从工具事实中提取链接记忆的操作
    
    基于link_related_memories的逻辑，从工具结果中
    提取可以与其他记忆建立链接的关键信息
    """

    def __init__(self, name: str = "extract_link_memory", **kwargs):
        # 调用父类构造函数
        super().__init__(name=name, **kwargs)

    async def _prepare_extraction_text(self, context: ApplicationContext, info: Dict[str, Any] = None, agent_id: str = None,
                                 event: ToolResultEvent = None) -> str:
        # # 当前这一轮的抽取结果
        # memory_commands = info.get("memory_commands", [])
        # if not memory_commands:
        #     return None
        # memory_items = [i.item for i in memory_commands]

        # 历史抽取
        graph_db = graph_db_factory.get_graph_db()
        if not graph_db:
            logger.info(f"skip extract_tool_memory_node because graph_db is None")
            return None
        nodes = await graph_db.get_all_nodes(namespace=context.session_id)
        memory_items = [GraphMemoryNode(id=item.get('id', ''), user_id='', label=item.get('label', ''), properties=item)
                             for item in nodes]

        # 格式化实体信息为中文，每行一个实体
        formatted_items = []
        for item in memory_items:
            formatted_item = f"实体ID: {item.id}, 标签: {item.label}, 属性: {item.properties}"
            formatted_items.append(formatted_item)
        
        return f"已抽取实体信息：\n" + "\n".join(formatted_items)

    def _build_extraction_prompt_template(self) -> str:
        """构建链接记忆提取的提示模板"""
        return """你是一个链接记忆分析器，专门从工具执行结果中提取可以与其他记忆建立链接的关键信息。你的主要作用是从工具结果中识别出具有链接价值的事实片段，这些事实将帮助agent在记忆系统中建立有意义的关系。

需要提取的链接记忆类型：

1. **entity_mentions**：实体提及、人名、地名、机构名、产品名等可链接的实体
2. **temporal_references**：时间引用、日期、时间段、历史事件等时间相关信息
3. **conceptual_connections**：概念连接、主题、类别、领域等抽象概念
4. **causal_relationships**：因果关系、影响关系、依赖关系等逻辑连接
5. **spatial_relationships**：空间关系、地理位置、区域、距离等地理连接
6. **numerical_relationships**：数值关系、统计数据、比较数据等量化连接
7. **functional_relationships**：功能关系、用途、作用、效果等功能连接
8. **hierarchical_relationships**：层级关系、分类、等级、结构等组织连接
9. **sequential_relationships**：序列关系、流程、步骤、顺序等过程连接
10. **contextual_relationships**：上下文关系、背景、环境、条件等情境连接

请记住以下几点：
- 专注于提取具有链接价值的信息，这些信息可以与其他记忆建立有意义的关系
- 优先提取实体、概念、关系等可以跨记忆共享的元素
- 如果工具结果中没有可链接的信息，返回空列表
- 确保提取的信息清晰、具体、可链接
- 保持信息的客观性和准确性
- 你应该检测用户输入的语言，并用相同的语言记录链接信息
- 优先提取具有跨记忆连接潜力的关键要素
- 对于实体信息，注意提取完整的名称和上下文
- 对于关系信息，注意提取关系的类型和方向
- 必须给出提取该关系的原因reason

输出格式要求：
- 注意输出字符串前后不要加任何 ```json 或 ``` 这样的标记
- 输出字符串必须可以被正常反序列化为json对象
- 输出格式必须是JSON对象，包含提取的链接信息
- 每个链接类型作为JSON对象的键，对应的值是该类型的链接内容

输出示例：
```json
[
    {
        "label": "entity_mentions",
        "name": "生产",
        "source_id": "1",
        "target_id": "2",
        "properties": {
            "reason": "苹果公司、iPhone、蒂姆·库克"
        }
    },
    {
        "label": "entity_mentions",
        "name": "生产",
        "source_id": "1",
        "target_id": "2",
        "properties": {
            "reason": "苹果公司、iPhone、蒂姆·库克"
        }
    }
]
```

待抽取关系的节点列表
{{text}}
"""


    def _convert_extraction_to_memory_commands(self, extraction_result: Dict[str, Any], context: ApplicationContext, agent_id: str) -> List[MemoryCommand]:
        memory_commands = []

        try:
            if not extraction_result or not isinstance(extraction_result, list):
                return []

            extraction_result = filter(lambda e: 'label' in e and e.get('label') is not None
                                                 and 'source_id' in e and e.get('source_id') is not None
                                                 and 'target_id' in e and e.get('target_id') is not None,
                                       extraction_result)
            extraction_result = list(extraction_result)

            # 处理LLM提取结果，直接使用JSON格式
            for content in extraction_result:
                # 构建图边的MemoryCommand
                graph_commands = self._build_graph_commands(content, context, agent_id, "ADD")
                memory_commands.extend(graph_commands)
                    
        except Exception as e:
            logger.error(f"❌ Error converting extraction to memory commands: {e}")

        return memory_commands

    def _get_link_types(self) -> List[str]:
        """获取支持的链接类型"""
        return [
            "entity_mentions",
            "temporal_references", 
            "conceptual_connections",
            "causal_relationships",
            "spatial_relationships",
            "numerical_relationships",
            "functional_relationships",
            "hierarchical_relationships",
            "sequential_relationships",
            "contextual_relationships"
        ]

    def _build_graph_commands(self, extract_data: Dict[str, Any], context: ApplicationContext, 
                             agent_id: str, operation_type: str) -> List[MemoryCommand]:
        commands = []
        
        try:
            if not extract_data or not isinstance(extract_data, dict):
                return []
            
            # 处理LLM提取结果格式 {key: value}
            command = MemoryCommand(
                type="ADD_EDGE",
                item=GraphMemoryEdge(
                    id=extract_data.get("id"),
                    label=extract_data.get("label"),
                    source_id=extract_data.get("source_id"),
                    target_id=extract_data.get("target_id"),
                    properties=extract_data.get("properties")
                )
            )
            commands.append(command)
        except Exception as e:
            logger.error(f"❌ Error building graph commands: {e}")

        return commands
