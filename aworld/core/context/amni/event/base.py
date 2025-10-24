# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional

from aworld.core.context.base import Context
from aworld.memory.models import MemoryMessage
from aworld.output import Artifact


class EventType:
    """Event types"""
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
    """Event status"""
    INIT = "init"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"

@dataclass
class Event:
    """Base event definition"""
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
        """Create a deep copy of the event"""
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
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None

    def deep_copy(self) -> 'SystemPromptEvent':
        """Create a deep copy of the event, with memory field referenced directly"""
        import copy
        
        new_event = SystemPromptEvent()
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
class ArtifactEvent(ContextEvent):
    """contains artifact"""
    artifact: Optional[Artifact] = None

    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "artifact_id": self.artifact.artifact_id if self.artifact else None,
        })
        return base_dict


@dataclass
class MessageEvent(ContextEvent):
    """contain MemoryMessage"""
    message: Optional[MemoryMessage] = None

    def to_dict(self) -> Dict[str, Any]:
        base_dict = super().to_dict()
        base_dict.update({
            "message_id": self.message.id if self.message else None,
            "message_role": self.message.role if self.message else None,
        })
        return base_dict


@dataclass
class ToolResultEvent(ContextEvent):
    """Event containing tool results"""
    tool_result: Optional[Any] = None
    tool_call_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None

    def deep_copy(self) -> 'ToolResultEvent':
        """Create a deep copy of the event"""
        import copy
        
        new_event = ToolResultEvent()
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
