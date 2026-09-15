# coding: utf-8

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aworld.utils.serialized_util import to_serializable


AMNI_SYSTEM_SECTIONS_SCHEMA_VERSION = "aworld.context.amni-system-sections.v1"


def validated_amni_system_sections(
    *, content: Any, metadata: Any
) -> List[Dict[str, Any]] | None:
    """Return exact structured sections only when they reproduce stored content."""
    ext_info = (
        metadata.get("ext_info")
        if isinstance(metadata, dict)
        else getattr(metadata, "ext_info", None)
    )
    payload = (
        ext_info.get("aworld_context_system_sections")
        if isinstance(ext_info, dict)
        else None
    )
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != AMNI_SYSTEM_SECTIONS_SCHEMA_VERSION
        or not isinstance(payload.get("sections"), list)
    ):
        return None
    normalized = []
    for section in payload["sections"]:
        if (
            not isinstance(section, dict)
            or section.get("stability") not in {"stable", "dynamic"}
            or not isinstance(section.get("content"), str)
            or not section["content"]
        ):
            return None
        normalized.append(dict(section))
    if "\n\n".join(section["content"] for section in normalized) != content:
        return None
    return normalized


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
    # Caller-ordered system occurrences. Grouped stable/dynamic views below are
    # retained for compatibility but cannot by themselves reconstruct order.
    system_sections: List[PromptSection] = field(default_factory=list)
    stable_system_sections: List[PromptSection] = field(default_factory=list)
    dynamic_system_sections: List[PromptSection] = field(default_factory=list)
    conversation_messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_section: Optional[ToolSectionHint] = None
    stable_hash: str = ""
    observability: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_model_messages(self) -> List[Dict[str, Any]]:
        return to_serializable(self.messages)
