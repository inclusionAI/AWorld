from aworld.core.context.amni.prompt.assembly import PromptAssemblyPlan, ToolSectionHint
from aworld.models.anthropic_provider import AnthropicProvider
from aworld.models.prompt_cache import (
    AnthropicPromptAssemblyLowerer,
    DefaultPromptAssemblyLowerer,
    should_request_provider_native_cache,
    supports_provider_native_prompt_cache,
)


def _build_plan():
    return PromptAssemblyPlan(
        messages=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ],
        stable_hash="stable-hash-1",
        tool_section=ToolSectionHint(
            stable=True,
            tool_names=["search"],
            tool_fingerprint="tool-hash-1",
        ),
        observability={"assembly_provider": "DefaultPromptAssemblyProvider"},
    )


def test_provider_native_cache_support_is_declared_by_helper():
    assert supports_provider_native_prompt_cache("anthropic") is True
    assert supports_provider_native_prompt_cache("openai") is True
    assert supports_provider_native_prompt_cache("unknown-provider") is False


def test_provider_native_cache_request_rules_are_provider_specific():
    assert should_request_provider_native_cache("anthropic", {}) is True
    assert should_request_provider_native_cache("openai", {}) is False
    assert should_request_provider_native_cache("openai", {"prompt_cache_key": "cache-key-1"}) is True
    assert should_request_provider_native_cache(
        "openai",
        {"extra_body": {"prompt_cache_key": "cache-key-2"}},
    ) is True


def test_default_prompt_assembly_lowerer_preserves_plain_messages():
    plan = _build_plan()

    result = DefaultPromptAssemblyLowerer().lower(plan=plan)

    assert result.messages == plan.messages
    assert result.request_kwargs == {}
    assert result.metadata["provider_native_cache"] is False


def test_anthropic_prompt_assembly_lowerer_adds_top_level_cache_control():
    plan = _build_plan()

    result = AnthropicPromptAssemblyLowerer().lower(
        plan=plan,
        request_kwargs={"metadata": {"request_id": "req-1"}},
        enable_native_cache=True,
    )

    assert result.messages == plan.messages
    assert result.request_kwargs["metadata"] == {"request_id": "req-1"}
    assert result.request_kwargs["cache_control"] == {"type": "ephemeral"}
    assert result.metadata["provider_native_cache"] is True
    assert result.metadata["tool_section_stable"] is True


def test_anthropic_provider_uses_prompt_assembly_lowerer_for_native_cache():
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model_name = "claude-3-5-sonnet-20241022"
    provider.kwargs = {}

    params = provider.get_anthropic_params(
        messages=[{"role": "user", "content": "hello"}],
        system="rules",
        prompt_assembly_plan=_build_plan(),
        provider_native_prompt_cache=True,
    )

    assert params["cache_control"] == {"type": "ephemeral"}


def test_anthropic_provider_skips_native_cache_lowering_when_disabled():
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model_name = "claude-3-5-sonnet-20241022"
    provider.kwargs = {}

    params = provider.get_anthropic_params(
        messages=[{"role": "user", "content": "hello"}],
        system="rules",
        prompt_assembly_plan=_build_plan(),
        provider_native_prompt_cache=False,
    )

    assert "cache_control" not in params
