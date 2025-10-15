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
    Operation to extract link memories from tool facts
    
    Based on link_related_memories logic, extract key information
    from tool results that can establish links with other memories
    """

    def __init__(self, name: str = "extract_link_memory", **kwargs):
        # Call parent constructor
        super().__init__(name=name, **kwargs)

    async def _prepare_extraction_text(self, context: ApplicationContext, info: Dict[str, Any] = None, agent_id: str = None,
                                 event: ToolResultEvent = None) -> str:
        # # Current round extraction results
        # memory_commands = info.get("memory_commands", [])
        # if not memory_commands:
        #     return None
        # memory_items = [i.item for i in memory_commands]

        # Historical extraction
        graph_db = graph_db_factory.get_graph_db()
        if not graph_db:
            logger.info(f"⏭️ skip extract_tool_memory_node because graph_db is None")
            return None
        nodes = await graph_db.get_all_nodes(namespace=context.session_id)
        memory_items = [GraphMemoryNode(id=item.get('id', ''), user_id='', label=item.get('label', ''), properties=item)
                             for item in nodes]

        # Format entity information, one entity per line
        formatted_items = []
        for item in memory_items:
            formatted_item = f"Entity ID: {item.id}, Label: {item.label}, Properties: {item.properties}"
            formatted_items.append(formatted_item)
        
        return f"Extracted Entity Information:\n" + "\n".join(formatted_items)

    def _build_extraction_prompt_template(self) -> str:
        """Build prompt template for link memory extraction"""
        return """You are a link memory analyzer, specialized in extracting key information from tool execution results that can establish links with other memories. Your main role is to identify fact fragments with linking value from tool results, which will help agents establish meaningful relationships in the memory system.

Types of link memories to extract:

1. **entity_mentions**: Entity mentions, person names, place names, organization names, product names, and other linkable entities
2. **temporal_references**: Time references, dates, time periods, historical events, and other time-related information
3. **conceptual_connections**: Conceptual connections, themes, categories, domains, and other abstract concepts
4. **causal_relationships**: Causal relationships, impact relationships, dependency relationships, and other logical connections
5. **spatial_relationships**: Spatial relationships, geographic locations, regions, distances, and other geographical connections
6. **numerical_relationships**: Numerical relationships, statistical data, comparative data, and other quantitative connections
7. **functional_relationships**: Functional relationships, purposes, roles, effects, and other functional connections
8. **hierarchical_relationships**: Hierarchical relationships, classifications, ranks, structures, and other organizational connections
9. **sequential_relationships**: Sequential relationships, processes, steps, orders, and other procedural connections
10. **contextual_relationships**: Contextual relationships, backgrounds, environments, conditions, and other situational connections

Please remember:
- Focus on extracting information with linking value that can establish meaningful relationships with other memories
- Prioritize extracting entities, concepts, and relationships that can be shared across memories
- Return empty list if tool results contain no linkable information
- Ensure extracted information is clear, specific, and linkable
- Maintain objectivity and accuracy of information
- Detect the language of user input and record link information in the same language
- Prioritize extracting key elements with cross-memory connection potential
- For entity information, extract complete names and context
- For relationship information, extract relationship types and directions
- Must provide reason for extracting each relationship

Output format requirements:
- Do not add ```json or ``` markers before or after the output string
- Output string must be properly deserializable to a JSON object
- Output format must be a JSON object containing extracted link information
- Each link type serves as a key in the JSON object, with corresponding link content as value

Output example:
```json
[
    {
        "label": "entity_mentions",
        "name": "produces",
        "source_id": "1",
        "target_id": "2",
        "properties": {
            "reason": "Apple Inc., iPhone, Tim Cook"
        }
    },
    {
        "label": "entity_mentions",
        "name": "produces",
        "source_id": "1",
        "target_id": "2",
        "properties": {
            "reason": "Apple Inc., iPhone, Tim Cook"
        }
    }
]
```

List of nodes for relationship extraction
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
        """Get supported link types"""
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
