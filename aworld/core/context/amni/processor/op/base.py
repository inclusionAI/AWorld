from abc import ABC, abstractmethod
from typing import Any, Dict, Literal, TypeVar, Generic, Optional

from pydantic import BaseModel, Field

from amnicontext import ApplicationContext
from aworld.memory.models import MemoryItem

MEMORY_ITEM = TypeVar("MEMORY_ITEM", bound=MemoryItem)

class MemoryCommand(BaseModel, Generic[MEMORY_ITEM]):
    memory_id: Optional[str] = Field(default=None)
    type: Literal["ADD","DELETE","KEEP", "ADD_NODE", "ADD_EDGE", "DELETE_NODE", "DELETE_EDGE"]
    item: MEMORY_ITEM

class BaseOp(ABC):
    """基础操作类，所有 pipeline 操作都应该继承此类"""
    
    def __init__(self, name: str, **kwargs):
        self.op_name = name
        self.kwargs = kwargs

    def get_name(self) -> str:
        """获取操作名称"""
        return self.op_name

    @abstractmethod
    async def execute(self, context: ApplicationContext, **kwargs) -> Dict[str, Any]:
        """执行核心逻辑"""
        pass
