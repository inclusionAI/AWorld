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


# Framework-owned prompt hints are consumed at assembly time and must never be
# forwarded to a model provider.  Keeping them on the message until this
# boundary lets producers describe per-occurrence stability without changing
# the OpenAI-compatible wire schema or relying on mutable text markers.
PROMPT_SECTION_NAME_HINT_KEY = "__aworld_prompt_section_name"
PROMPT_STABILITY_HINT_KEY = "__aworld_prompt_stability"


def _consume_message_hints(
    messages: Any,
) -> tuple[List[Any], List[Optional[Dict[str, Any]]]]:
    """Return provider-safe messages and aligned framework-owned hints."""

    sanitized: List[Any] = []
    hints: List[Optional[Dict[str, Any]]] = []
    for value in to_serializable(messages):
        if not isinstance(value, dict):
            sanitized.append(value)
            hints.append(None)
            continue
        message = dict(value)
        name = message.pop(PROMPT_SECTION_NAME_HINT_KEY, None)
        stability = message.pop(PROMPT_STABILITY_HINT_KEY, None)
        hint: Dict[str, Any] = {}
        if isinstance(name, str) and name.strip():
            hint["name"] = name.strip()
        if stability in {"stable", "dynamic"}:
            hint["stability"] = stability
        sanitized.append(message)
        hints.append(hint or None)
    return sanitized, hints


def sanitize_prompt_messages(messages: Any) -> List[Any]:
    """Strip framework-only assembly hints at the universal provider boundary."""

    sanitized, _ = _consume_message_hints(messages)
    return sanitized


def _tool_catalog_fingerprint(tools: Any) -> str:
    """Fingerprint the current catalog separately from cache routing identity."""

    return compute_stable_prefix_hash({"tools": to_serializable(tools or [])})


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
        serializable_messages, _ = _consume_message_hints(messages)
        serializable_tools = to_serializable(tools or [])

        stable_messages = []
        conversation_messages = []
        dynamic_sections = []
        for message in serializable_messages:
            if isinstance(message, dict) and message.get("role") == "system":
                stable_messages.append(message)
            else:
                conversation_messages.append(message)

        # A provider cache key is a routing hint, not a semantic digest of the
        # whole request.  Tool catalogs can intentionally contract for a
        # checkpoint while the reusable system prefix remains byte-identical.
        # Keep the two identities separate so such a turn does not strand the
        # following request in a new cache namespace.  Providers still compare
        # the actual request prefix before reusing cached computation.
        stable_payload = {"system_messages": stable_messages}
        stable_hash = compute_stable_prefix_hash(stable_payload)
        tool_fingerprint = _tool_catalog_fingerprint(serializable_tools)

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
        ordered_system_sections = [
            PromptSection(
                name=f"system_message_{index}",
                kind="system",
                stability="stable",
                content=message,
                hash=stable_hash,
            )
            for index, message in enumerate(stable_messages)
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
            "tool_catalog_hash": tool_fingerprint,
        }
        if "cache_aware_assembly" in plan_metadata:
            observability["cache_aware_assembly"] = plan_metadata["cache_aware_assembly"]

        return PromptAssemblyPlan(
            messages=serializable_messages,
            system_sections=ordered_system_sections,
            stable_system_sections=stable_sections,
            dynamic_system_sections=dynamic_sections,
            conversation_messages=conversation_messages,
            tool_section=ToolSectionHint(
                stable=True,
                tool_names=tool_names,
                tool_fingerprint=tool_fingerprint if tool_names else "",
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
        serializable_messages, embedded_hints = _consume_message_hints(messages)
        serializable_tools = to_serializable(tools or [])
        plan_metadata = dict(metadata or {})

        hints = self._normalize_system_section_hints(plan_metadata.get("system_section_hints"))
        stable_sections: List[PromptSection] = []
        dynamic_sections: List[PromptSection] = []
        ordered_system_sections: List[PromptSection] = []
        conversation_messages = []
        system_index = 0

        for message_index, message in enumerate(serializable_messages):
            if isinstance(message, dict) and message.get("role") == "system":
                explicit_hint = (
                    hints[system_index] if system_index < len(hints) else None
                )
                embedded_hint = embedded_hints[message_index]
                hint = dict(explicit_hint or {})
                if embedded_hint:
                    hint.update(embedded_hint)
                hint = hint or None
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
                ordered_system_sections.append(section)
            else:
                conversation_messages.append(message)

        stable_payload = {
            "stable_system_sections": [section.content for section in stable_sections],
        }
        stable_hash = compute_stable_prefix_hash(stable_payload)
        tool_fingerprint = _tool_catalog_fingerprint(serializable_tools)

        for section in stable_sections:
            section.hash = stable_hash

        tool_names = []
        for tool in serializable_tools:
            if isinstance(tool, dict):
                function = tool.get("function", {})
                if isinstance(function, dict) and function.get("name"):
                    tool_names.append(function["name"])

        runtime_observation = self.runtime_state.observe(
            stable_hash=stable_hash,
            tool_fingerprint=tool_fingerprint,
        )
        stable_prefix_reused = runtime_observation["stable_prefix_reused"]
        plan_metadata["stable_prefix_reused"] = stable_prefix_reused
        plan_metadata["tool_catalog_reused"] = runtime_observation[
            "tool_catalog_reused"
        ]

        observability = {
            "assembly_provider": self.__class__.__name__,
            "stable_prefix_hash": stable_hash,
            "stable_prefix_reused": stable_prefix_reused,
            "stable_prefix_changed": runtime_observation[
                "stable_prefix_changed"
            ],
            "tool_catalog_hash": tool_fingerprint,
            "tool_catalog_reused": runtime_observation["tool_catalog_reused"],
            "tool_catalog_changed": runtime_observation["tool_catalog_changed"],
            "cache_aware_assembly": True,
        }

        return PromptAssemblyPlan(
            messages=serializable_messages,
            system_sections=ordered_system_sections,
            stable_system_sections=stable_sections,
            dynamic_system_sections=dynamic_sections,
            conversation_messages=conversation_messages,
            tool_section=ToolSectionHint(
                stable=True,
                tool_names=tool_names,
                tool_fingerprint=tool_fingerprint if tool_names else "",
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
        # Unknown augment sources may contain retrieval, memory, task, or user
        # data. They stay dynamic until their owner declares stable semantics.
        return "dynamic"
