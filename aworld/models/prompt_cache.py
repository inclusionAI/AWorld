# coding: utf-8

from dataclasses import dataclass, field
from typing import Any, Dict

from aworld.core.context.amni.prompt.assembly import PromptAssemblyPlan
from aworld.utils.serialized_util import to_serializable


@dataclass(frozen=True)
class PromptCacheCapabilities:
    provider_name: str
    supports_native_prompt_cache: bool = False
    supports_automatic_caching: bool = False
    supports_explicit_breakpoints: bool = False


PROMPT_CACHE_CAPABILITIES: Dict[str, PromptCacheCapabilities] = {
    "openai": PromptCacheCapabilities(
        provider_name="openai",
        supports_native_prompt_cache=True,
    ),
    "anthropic": PromptCacheCapabilities(
        provider_name="anthropic",
        supports_native_prompt_cache=True,
        supports_automatic_caching=True,
    ),
}


def get_prompt_cache_capabilities(provider_name: str) -> PromptCacheCapabilities:
    return PROMPT_CACHE_CAPABILITIES.get(
        provider_name,
        PromptCacheCapabilities(provider_name=provider_name or "unknown"),
    )


@dataclass
class PromptAssemblyLoweringResult:
    messages: Any
    request_kwargs: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class DefaultPromptAssemblyLowerer:
    def lower(
        self,
        *,
        plan: PromptAssemblyPlan,
        request_kwargs: Dict[str, Any] | None = None,
        enable_native_cache: bool = False,
    ) -> PromptAssemblyLoweringResult:
        _ = enable_native_cache
        return PromptAssemblyLoweringResult(
            messages=plan.to_model_messages(),
            request_kwargs=dict(request_kwargs or {}),
            metadata={
                "provider_native_cache": False,
                "stable_prefix_hash": getattr(plan, "stable_hash", ""),
            },
        )


class AnthropicPromptAssemblyLowerer(DefaultPromptAssemblyLowerer):
    def lower(
        self,
        *,
        plan: PromptAssemblyPlan,
        request_kwargs: Dict[str, Any] | None = None,
        enable_native_cache: bool = False,
    ) -> PromptAssemblyLoweringResult:
        result = super().lower(
            plan=plan,
            request_kwargs=request_kwargs,
            enable_native_cache=enable_native_cache,
        )
        result.metadata["tool_section_stable"] = bool(
            getattr(getattr(plan, "tool_section", None), "stable", False)
        )
        if enable_native_cache:
            result.request_kwargs.setdefault("cache_control", {"type": "ephemeral"})
            result.metadata["provider_native_cache"] = True
        result.messages = to_serializable(result.messages)
        return result
