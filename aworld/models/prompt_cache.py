# coding: utf-8

from dataclasses import dataclass, field
from typing import Any, Dict

from aworld.core.context.amni.prompt.assembly import PromptAssemblyPlan
from aworld.utils.serialized_util import to_serializable

PROMPT_CACHE_CAPABLE_PROVIDERS = {"openai", "anthropic"}


def supports_provider_native_prompt_cache(provider_name: str) -> bool:
    return provider_name in PROMPT_CACHE_CAPABLE_PROVIDERS


def should_request_provider_native_cache(
    provider_name: str,
    request_kwargs: Dict[str, Any] | None = None,
) -> bool:
    request_kwargs = request_kwargs or {}
    if provider_name == "openai":
        if request_kwargs.get("prompt_cache_key"):
            return True
        extra_body = request_kwargs.get("extra_body")
        if isinstance(extra_body, dict) and extra_body.get("prompt_cache_key"):
            return True
        return False
    if provider_name == "anthropic":
        return True
    return False


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
