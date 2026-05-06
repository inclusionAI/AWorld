# coding: utf-8

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from aworld.core.context.amni.prompt.assembly.plan import (
    PromptAssemblyPlan,
    PromptSection,
    ToolSectionHint,
)
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
        stable_hash = hashlib.sha256(
            json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

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
        if "provider_name" in plan_metadata:
            observability["provider_name"] = plan_metadata["provider_name"]
        if "context_cache_enabled" in plan_metadata:
            observability["context_cache_enabled"] = plan_metadata["context_cache_enabled"]
        if "cache_aware_assembly" in plan_metadata:
            observability["cache_aware_assembly"] = plan_metadata["cache_aware_assembly"]
        if "provider_native_cache" in plan_metadata:
            observability["provider_native_cache"] = plan_metadata["provider_native_cache"]

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
