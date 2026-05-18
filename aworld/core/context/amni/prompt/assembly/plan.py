# coding: utf-8

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aworld.utils.serialized_util import to_serializable


@dataclass
class PromptSection:
    name: str
    kind: str
    stability: str
    content: Any = None
    hash: Optional[str] = None


@dataclass
class ToolSectionHint:
    stable: bool = True
    tool_names: List[str] = field(default_factory=list)
    tool_fingerprint: str = ""


@dataclass
class PromptAssemblyPlan:
    messages: List[Dict[str, Any]]
    stable_system_sections: List[PromptSection] = field(default_factory=list)
    dynamic_system_sections: List[PromptSection] = field(default_factory=list)
    conversation_messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_section: Optional[ToolSectionHint] = None
    stable_hash: str = ""
    observability: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_model_messages(self) -> List[Dict[str, Any]]:
        return to_serializable(self.messages)
