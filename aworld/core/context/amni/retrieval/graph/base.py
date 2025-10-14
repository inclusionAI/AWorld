import time
from typing import Optional, Any, Dict

from pydantic import BaseModel

from aworld.memory.models import MemoryItem


class GraphDBConfig(BaseModel):
    provider: str = "pg"
    config: dict[str, Any] = {}

class GraphMemoryEdge(MemoryItem):
    label: Optional[str] = None
    id: Optional[str] = None
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    properties: Optional[dict] = None

    def __init__(self, user_id: str = None, id: str = None, label: str = None, source_id: str = None,
                 target_id: str = None,
                 metadata: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        meta = metadata.copy() if metadata else {}
        if user_id:
            meta['user_id'] = user_id
        elif metadata and metadata.get('user_id'):
            meta['user_id'] = metadata.get('user_id')

        if 'memory_type' in kwargs:
            kwargs.pop("memory_type")

        properties = kwargs.get('properties', {})

        # 先调用父类初始化，确保Pydantic属性正确设置
        super().__init__(content=properties, metadata=meta, memory_type="fact", **kwargs)

        # 然后设置子类属性
        self.label = label
        self.id = id
        self.source_id = source_id
        self.target_id = target_id
        self.properties = properties
        self.properties['id'] = self.id
        self.properties['source_id'] = self.source_id
        self.properties['target_id'] = self.target_id
        self.properties["label"] = self.label
        self.properties['updated'] = time.time()

    @property
    def key(self) -> str:
        return self.id

    @property
    def value(self) -> Any:
        return self.properties

    @property
    def embedding_text(self):
        return f"key:{self.key} value:{self.value}"

    def to_openai_message(self) -> dict:
        return {
            "role": "user",
            "content": self.content
        }


class GraphMemoryNode(MemoryItem):
    label: Optional[str] = None
    id: Optional[str] = None
    properties: Optional[dict] = None

    def __init__(self, user_id: str = None, id: str = None, label: str = None,
                 metadata: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        meta = metadata.copy() if metadata else {}
        if user_id:
            meta['user_id'] = user_id
        elif metadata and metadata.get('user_id'):
            meta['user_id'] = metadata.get('user_id')

        if 'memory_type' in kwargs:
            kwargs.pop("memory_type")

        properties = kwargs.get('properties', {})

        # 先调用父类初始化，确保Pydantic属性正确设置
        super().__init__(content=properties, metadata=meta, memory_type="fact", **kwargs)

        # 然后设置子类属性
        self.label = label
        self.id = id
        self.properties = properties
        self.properties['id'] = self.id
        self.properties["label"] = self.label
        self.properties['updated'] = time.time()

    @property
    def key(self) -> str:
        return self.id

    @property
    def value(self) -> Any:
        return self.properties

    @property
    def embedding_text(self):
        return f"key:{self.key} value:{self.value}"

    def to_openai_message(self) -> dict:
        return {
            "role": "user",
            "content": self.content
        }
