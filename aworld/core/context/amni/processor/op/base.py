from abc import ABC, abstractmethod
from typing import Any, Dict, Literal, TypeVar, Generic, Optional

from pydantic import BaseModel, Field

from ... import ApplicationContext
from aworld.memory.models import MemoryItem

MEMORY_ITEM = TypeVar("MEMORY_ITEM", bound=MemoryItem)

class MemoryCommand(BaseModel, Generic[MEMORY_ITEM]):
    memory_id: Optional[str] = Field(default=None)
    type: Literal["ADD","DELETE","KEEP", "ADD_NODE", "ADD_EDGE", "DELETE_NODE", "DELETE_EDGE"]
    item: MEMORY_ITEM

class BaseOp(ABC):
    """Base operation class, all pipeline operations should inherit from this class"""
    
    def __init__(self, name: str, **kwargs):
        self.op_name = name
        self.kwargs = kwargs

    def get_name(self) -> str:
        """Get operation name"""
        return self.op_name

    @abstractmethod
    async def execute(self, context: ApplicationContext, **kwargs) -> Dict[str, Any]:
        """Execute core logic"""
        pass
