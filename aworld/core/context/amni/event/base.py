# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional

from aworld.core.context.base import Context
from aworld.output import Artifact


@dataclass
class BaseMessagePayload:
    """基础事件定义"""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = None
    timestamp: datetime = field(default_factory=datetime.now)
    namespace: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "namespace": self.namespace,
            "status": self.status
        }

    def deep_copy(self) -> 'BaseMessagePayload':
        """创建事件的深拷贝"""
        import copy
        return copy.deepcopy(self)


@dataclass
class ContextMessagePayload(BaseMessagePayload):
    context: Optional[Context] = None

    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "context": str(self.context) if self.context else None
        })
        return base_dict


@dataclass
class SystemPromptMessagePayload(ContextMessagePayload):
    system_prompt: Optional[str] = None
    user_query: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None

    def deep_copy(self) -> 'SystemPromptMessagePayload':
        """创建事件的深拷贝，memory字段直接引用"""
        import copy

        new_event = SystemPromptMessagePayload()
        for key, value in self.__dict__.items():
            if key == 'memory':
                # Reference memory field directly
                setattr(new_event, key, value)
            elif key == 'context' and value is not None:
                # Special handling for context field
                if hasattr(value, 'deep_copy'):
                    setattr(new_event, key, value.deep_copy())
                else:
                    setattr(new_event, key, copy.deepcopy(value))
            else:
                # Deep copy other fields
                setattr(new_event, key, copy.deepcopy(value))

        return new_event


@dataclass
class ArtifactMessagePayload(ContextMessagePayload):
    """contains artifact"""
    artifact: Optional[Artifact] = None

    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "artifact_id": self.artifact.artifact_id if self.artifact else None,
        })
        return base_dict


@dataclass
class ToolResultMessagePayload(ContextMessagePayload):
    """包含工具结果的事件"""
    tool_result: Optional[Any] = None
    tool_call_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None

    def deep_copy(self) -> 'ToolResultMessagePayload':
        """创建事件的深拷贝"""
        import copy

        new_event = ToolResultMessagePayload()
        for key, value in self.__dict__.items():
            if key == 'context' and value is not None:
                # Special handling for context field
                if hasattr(value, 'deep_copy'):
                    setattr(new_event, key, value.deep_copy())
                else:
                    setattr(new_event, key, copy.deepcopy(value))
            else:
                # Deep copy other fields
                setattr(new_event, key, copy.deepcopy(value))

        return new_event
