# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional

from aworld.core.context.base import Context
from aworld.memory.main import Memory
from aworld.memory.models import MemoryMessage
from aworld.output import Artifact


class EventType:
    """事件类型"""
    ARTIFACT_ADDED = "artifact_added"
    CONTEXT_CONSOLIDATION = "context_consolidation"
    SYSTEM_PROMPT = "system_prompt"
    USER_INPUT = "user_input"
    AGENT_RESULT = "agent_result"
    TOOL_RESULT = "tool_result"

    @staticmethod
    def as_list() -> list[str]:
        return [EventType.ARTIFACT_ADDED, EventType.CONTEXT_CONSOLIDATION, EventType.SYSTEM_PROMPT,
                EventType.USER_INPUT, EventType.AGENT_RESULT, EventType.TOOL_RESULT]

class EventStatus:
    """事件状态"""
    INIT = "init"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"

@dataclass
class Event:
    """基础事件定义"""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = None
    timestamp: datetime = field(default_factory=datetime.now)
    namespace: str = ""
    status: str = field(default=EventStatus.INIT)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "namespace": self.namespace,
            "status": self.status
        }
    
    def deep_copy(self) -> 'Event':
        """创建事件的深拷贝"""
        import copy
        return copy.deepcopy(self)


@dataclass
class ContextEvent(Event):
    context: Optional[Context] = None

    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "context": str(self.context) if self.context else None
        })
        return base_dict


@dataclass
class SystemPromptEvent(ContextEvent):
    system_prompt: Optional[str] = None
    user_query: Optional[str] = None
    memory: Optional[Memory] = None
    agent_id: Optional[str] = None

    def deep_copy(self) -> 'SystemPromptEvent':
        """创建事件的深拷贝，memory字段直接引用"""
        import copy
        
        new_event = SystemPromptEvent()
        for key, value in self.__dict__.items():
            if key == 'memory':
                # memory字段直接引用
                setattr(new_event, key, value)
            elif key == 'context' and value is not None:
                # context字段特殊处理
                if hasattr(value, 'deep_copy'):
                    setattr(new_event, key, value.deep_copy())
                else:
                    setattr(new_event, key, copy.deepcopy(value))
            else:
                # 其他字段深拷贝
                setattr(new_event, key, copy.deepcopy(value))
        
        return new_event

@dataclass
class ArtifactEvent(Event):
    """包含Artifact的事件"""
    artifact: Optional[Artifact] = None
    context: Optional[Context] = None

    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "artifact_id": self.artifact.artifact_id if self.artifact else None,
            "context": str(self.context) if self.context else None
        })
        return base_dict


@dataclass
class MessageEvent(Event):
    """包含MemoryMessage的事件"""
    message: Optional[MemoryMessage] = None
    context: Optional[Context] = None
    
    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "message_id": self.message.id if self.message else None,
            "message_role": self.message.role if self.message else None,
            "context": str(self.context) if self.context else None
        })
        return base_dict


@dataclass
class ToolResultEvent(Event):
    """包含工具结果的事件"""
    tool_result: Optional[Any] = None
    context: Optional[Context] = None
    tool_call_id: Optional[str] = None
    memory: Optional[Memory] = None
    agent_id: Optional[str] = None
    
    def deep_copy(self) -> 'ToolResultEvent':
        """创建事件的深拷贝，memory字段直接引用"""
        import copy
        
        new_event = ToolResultEvent()
        for key, value in self.__dict__.items():
            if key == 'memory':
                # memory字段直接引用
                setattr(new_event, key, value)
            elif key == 'context' and value is not None:
                # context字段特殊处理
                if hasattr(value, 'deep_copy'):
                    setattr(new_event, key, value.deep_copy())
                else:
                    setattr(new_event, key, copy.deepcopy(value))
            else:
                # 其他字段深拷贝
                setattr(new_event, key, copy.deepcopy(value))
        
        return new_event
