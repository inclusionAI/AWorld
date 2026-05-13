# coding: utf-8

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from aworld.core.context.amni.prompt.assembly.hashing import compute_stable_prefix_hash
from aworld.core.context.amni.prompt.assembly.plan import (
    PromptAssemblyPlan,
    PromptSection,
    ToolSectionHint,
)
from aworld.core.context.amni.prompt.assembly.state import PromptAssemblyRuntimeState
from aworld.utils.serialized_util import to_serializable


class PromptAssemblyProvider(ABC):
    @abstractmethod
    def build_plan(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PromptAssemblyPlan:
        pass


class DefaultPromptAssemblyProvider(PromptAssemblyProvider):
    """Preserve today's OpenAI-style message list while surfacing stable-prefix metadata."""

    def build_plan(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PromptAssemblyPlan:
        serializable_messages = to_serializable(messages)
        serializable_tools = to_serializable(tools or [])

        stable_messages = []
        conversation_messages = []
        dynamic_sections = []
        for message in serializable_messages:
            if isinstance(message, dict) and message.get("role") == "system":
                stable_messages.append(message)
            else:
                conversation_messages.append(message)

        stable_payload = {
            "system_messages": stable_messages,
            "tools": serializable_tools,
        }
        stable_hash = compute_stable_prefix_hash(stable_payload)

        tool_names = []
        for tool in serializable_tools:
            if isinstance(tool, dict):
                function = tool.get("function", {})
                if isinstance(function, dict) and function.get("name"):
                    tool_names.append(function["name"])

        stable_sections = [
            PromptSection(
                name="system_messages",
                kind="system",
                stability="stable",
                content=stable_messages,
                hash=stable_hash,
            )
        ]
        if conversation_messages:
            dynamic_sections.append(
                PromptSection(
                    name="conversation_messages",
                    kind="messages",
                    stability="dynamic",
                    content=conversation_messages,
                )
            )

        plan_metadata = dict(metadata or {})
        observability = {
            "assembly_provider": self.__class__.__name__,
            "stable_prefix_hash": stable_hash,
        }
        if "cache_aware_assembly" in plan_metadata:
            observability["cache_aware_assembly"] = plan_metadata["cache_aware_assembly"]

        return PromptAssemblyPlan(
            messages=serializable_messages,
            stable_system_sections=stable_sections,
            dynamic_system_sections=dynamic_sections,
            conversation_messages=conversation_messages,
            tool_section=ToolSectionHint(
                stable=True,
                tool_names=tool_names,
                tool_fingerprint=stable_hash if tool_names else "",
            ),
            stable_hash=stable_hash,
            observability=observability,
            metadata=plan_metadata,
        )


class CacheAwarePromptAssemblyProvider(PromptAssemblyProvider):
    """Classify stable vs dynamic prompt sections while preserving request payload order."""

    STABLE_SECTION_NAMES = {
        "base_rules",
        "system_prompt",
        "aworld_file",
        "workspace_instruction",
        "skill",
        "policy",
    }

    DYNAMIC_SECTION_NAMES = {
        "relevant_memory",
        "history",
        "conversation_history",
        "summaries",
        "summary",
        "task",
        "todo",
        "action_info",
        "current_task",
    }

    def __init__(self, runtime_state: PromptAssemblyRuntimeState | None = None):
        self.runtime_state = runtime_state or PromptAssemblyRuntimeState()

    def build_plan(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PromptAssemblyPlan:
        serializable_messages = to_serializable(messages)
        serializable_tools = to_serializable(tools or [])
        plan_metadata = dict(metadata or {})

        hints = self._normalize_system_section_hints(plan_metadata.get("system_section_hints"))
        stable_sections: List[PromptSection] = []
        dynamic_sections: List[PromptSection] = []
        conversation_messages = []
        system_index = 0

        for message in serializable_messages:
            if isinstance(message, dict) and message.get("role") == "system":
                hint = hints[system_index] if system_index < len(hints) else None
                system_index += 1
                section = PromptSection(
                    name=(hint or {}).get("name") or "system_message",
                    kind="system",
                    stability=self._classify_system_stability(hint),
                    content=message,
                )
                if section.stability == "stable":
                    stable_sections.append(section)
                else:
                    dynamic_sections.append(section)
            else:
                conversation_messages.append(message)

        stable_payload = {
            "stable_system_sections": [section.content for section in stable_sections],
            "tools": serializable_tools,
        }
        stable_hash = compute_stable_prefix_hash(stable_payload)

        for section in stable_sections:
            section.hash = stable_hash

        tool_names = []
        for tool in serializable_tools:
            if isinstance(tool, dict):
                function = tool.get("function", {})
                if isinstance(function, dict) and function.get("name"):
                    tool_names.append(function["name"])

        stable_prefix_reused = self.runtime_state.mark_stable_prefix(stable_hash)
        plan_metadata["stable_prefix_reused"] = stable_prefix_reused

        observability = {
            "assembly_provider": self.__class__.__name__,
            "stable_prefix_hash": stable_hash,
            "stable_prefix_reused": stable_prefix_reused,
            "cache_aware_assembly": True,
        }

        return PromptAssemblyPlan(
            messages=serializable_messages,
            stable_system_sections=stable_sections,
            dynamic_system_sections=dynamic_sections,
            conversation_messages=conversation_messages,
            tool_section=ToolSectionHint(
                stable=True,
                tool_names=tool_names,
                tool_fingerprint=stable_hash if tool_names else "",
            ),
            stable_hash=stable_hash,
            observability=observability,
            metadata=plan_metadata,
        )

    @classmethod
    def _normalize_system_section_hints(cls, hints: Any) -> List[Dict[str, Any]]:
        normalized = []
        if not isinstance(hints, list):
            return normalized
        for hint in hints:
            if isinstance(hint, dict):
                normalized.append(dict(hint))
            elif isinstance(hint, str):
                normalized.append({"name": hint})
        return normalized

    @classmethod
    def _classify_system_stability(cls, hint: Dict[str, Any] | None) -> str:
        if not hint:
            return "stable"
        explicit = hint.get("stability")
        if explicit in {"stable", "dynamic"}:
            return explicit

        name = str(hint.get("name") or "").strip().lower()
        if name in cls.DYNAMIC_SECTION_NAMES:
            return "dynamic"
        if name in cls.STABLE_SECTION_NAMES:
            return "stable"
        return "stable"
