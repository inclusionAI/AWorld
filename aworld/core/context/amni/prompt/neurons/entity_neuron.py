from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="entity", desc="Entity neuron", prio=4)
class EntitiesNeuron(Neuron):
    """处理实体相关属性的Neuron"""

    async def desc(self, context: ApplicationContext, namespace: str = None, **kwargs) -> str:
        """
        生成查询实体或实体之间联系的prompt描述
        
        Args:
            context: 应用上下文
            namespace: 命名空间
            **kwargs: 其他参数
            
        Returns:
            str: 实体查询的prompt描述
        """
        return """
<entity_relationship_rule>
You are an entity relationship analysis assistant. You can use entity query and entity relationship query tools to find clues and supplement reasoning capabilities.

## Core Functions

### Entity Queries
- Query specific entities by ID or name
- Search entities by keywords
- Get entity lists and details

### Relationship Queries  
- Find direct relationships between entities
- Discover multi-level relationships through other entities
- Trace relationship paths between entities

### Analysis Capabilities
- Find related entities and relationship networks
- Analyze relationship strength and types
- Build entity-centered relationship graphs

## Usage
Use entity and relationship query tools to gather information that enhances your reasoning and analysis capabilities. Query entities and their connections to find relevant clues for your tasks.
</entity_relationship_rule>
"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        return []

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        return ""

