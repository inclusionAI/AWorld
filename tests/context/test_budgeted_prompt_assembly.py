from __future__ import annotations

import pytest

from aworld.core.context.amni.prompt.assembly import (
    CacheAwarePromptAssemblyProvider,
    DefaultPromptAssemblyProvider,
)
from aworld.core.context.amni.prompt.assembly.budget import (
    BudgetedPromptAssemblyPlan,
    BudgetedPromptAssemblyProvider,
    PromptBudgetExceededError,
    PromptBudgetPolicy,
)


def _estimate(
    messages: list[dict[str, str]],
    tools: list[dict] | None = None,
) -> int:
    return BudgetedPromptAssemblyProvider.estimate_request_tokens(
        messages=messages,
        tools=tools,
        model_name="gpt-4o",
    )["total"]


def test_tool_schemas_count_toward_the_input_budget() -> None:
    messages = [
        {"role": "system", "content": "required rules"},
        {"role": "user", "content": "required task"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "x" * 800,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    message_only_budget = _estimate(messages)
    provider = BudgetedPromptAssemblyProvider(
        DefaultPromptAssemblyProvider(),
        PromptBudgetPolicy(input_budget=message_only_budget),
    )

    with pytest.raises(PromptBudgetExceededError) as exc_info:
        provider.build_plan(messages=messages, tools=tools)

    assert exc_info.value.tool_tokens > 0
    assert exc_info.value.final_input_tokens > message_only_budget


def test_optional_sections_reduce_by_priority_then_stable_order() -> None:
    messages = [
        {"role": "system", "content": "required system rules"},
        {"role": "user", "content": "remove this prior feedback"},
        {"role": "user", "content": "keep this lesson"},
        {"role": "user", "content": "required current task"},
    ]
    expected_messages = [messages[0], messages[2], messages[3]]
    provider = BudgetedPromptAssemblyProvider(
        DefaultPromptAssemblyProvider(),
        PromptBudgetPolicy(input_budget=_estimate(expected_messages)),
    )

    plan = provider.build_plan(
        messages=messages,
        metadata={
            "budget_section_hints": [
                {"name": "system_prompt", "required": True},
                {
                    "name": "prior_feedback",
                    "required": False,
                    "priority": 10,
                    "reducer": "optional_remove",
                },
                {
                    "name": "lessons",
                    "required": False,
                    "priority": 20,
                    "reducer": "optional_remove",
                },
                {"name": "current_task", "required": True},
            ]
        },
    )

    assert isinstance(plan, BudgetedPromptAssemblyPlan)
    assert plan.messages == expected_messages
    assert plan.original_input_tokens > plan.final_input_tokens
    assert plan.final_input_tokens <= plan.input_budget
    assert plan.observability["reduced_sections"] == [
        {"name": "prior_feedback", "reducer": "optional_remove"}
    ]
    assert "remove this prior feedback" not in str(plan.observability)


def test_equal_priority_optional_sections_reduce_in_message_order() -> None:
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "first optional"},
        {"role": "user", "content": "second optional"},
        {"role": "user", "content": "current task"},
    ]
    expected_messages = [messages[0], messages[2], messages[3]]
    provider = BudgetedPromptAssemblyProvider(
        DefaultPromptAssemblyProvider(),
        PromptBudgetPolicy(input_budget=_estimate(expected_messages)),
    )

    plan = provider.build_plan(
        messages=messages,
        metadata={
            "budget_section_hints": [
                {"name": "system_prompt", "required": True},
                {"name": "first", "required": False, "priority": 5},
                {"name": "second", "required": False, "priority": 5},
                {"name": "current_task", "required": True},
            ]
        },
    )

    assert plan.messages == expected_messages
    assert plan.observability["reduced_sections"][0]["name"] == "first"


def test_required_compressible_section_uses_bounded_head_tail_reduction() -> None:
    long_content = "BEGIN-" + ("evidence " * 300) + "-END"
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": long_content},
        {"role": "user", "content": "current task"},
    ]
    minimum_messages = [messages[0], {"role": "user", "content": "\n...[omitted]...\n"}, messages[2]]
    provider = BudgetedPromptAssemblyProvider(
        DefaultPromptAssemblyProvider(),
        PromptBudgetPolicy(input_budget=_estimate(minimum_messages) + 20),
    )

    plan = provider.build_plan(
        messages=messages,
        metadata={
            "budget_section_hints": [
                {"name": "system_prompt", "required": True},
                {
                    "name": "evidence",
                    "required": True,
                    "compressible": True,
                    "reducer": "head_tail",
                },
                {"name": "current_task", "required": True},
            ]
        },
    )

    reduced = plan.messages[1]["content"]
    assert reduced.startswith("BEGIN-")
    assert reduced.endswith("-END")
    assert "...[omitted]..." in reduced
    assert plan.final_input_tokens <= plan.input_budget


def test_required_content_overflow_raises_bounded_diagnostic() -> None:
    secret = "trajectory-secret-that-must-not-leak"
    provider = BudgetedPromptAssemblyProvider(
        DefaultPromptAssemblyProvider(),
        PromptBudgetPolicy(input_budget=1),
    )

    with pytest.raises(PromptBudgetExceededError) as exc_info:
        provider.build_plan(
            messages=[
                {"role": "system", "content": secret * 20},
                {"role": "user", "content": "required task"},
            ]
        )

    diagnostic = exc_info.value.to_diagnostic()
    assert diagnostic["code"] == "prompt_budget_exceeded"
    assert diagnostic["input_budget"] == 1
    assert secret not in str(exc_info.value)
    assert secret not in str(diagnostic)


def test_reduction_rebuilds_delegate_stable_hash_from_final_messages() -> None:
    delegate = DefaultPromptAssemblyProvider()
    messages = [
        {"role": "system", "content": "required rules"},
        {"role": "system", "content": "optional volatile context"},
        {"role": "user", "content": "current task"},
    ]
    final_messages = [messages[0], messages[2]]
    original_hash = delegate.build_plan(messages=messages).stable_hash
    expected_hash = delegate.build_plan(messages=final_messages).stable_hash
    provider = BudgetedPromptAssemblyProvider(
        delegate,
        PromptBudgetPolicy(input_budget=_estimate(final_messages)),
    )

    plan = provider.build_plan(
        messages=messages,
        metadata={
            "budget_section_hints": [
                {"name": "system_prompt", "required": True},
                {"name": "optional_context", "required": False, "priority": 1},
                {"name": "current_task", "required": True},
            ]
        },
    )

    assert plan.stable_hash == expected_hash
    assert plan.stable_hash != original_hash


def test_budget_preview_does_not_pollute_cache_runtime_state() -> None:
    messages = [
        {"role": "system", "content": "stable rules"},
        {"role": "user", "content": "optional history " * 100},
        {"role": "user", "content": "current task"},
    ]
    final_messages = [messages[0], messages[2]]
    provider = BudgetedPromptAssemblyProvider(
        CacheAwarePromptAssemblyProvider(),
        PromptBudgetPolicy(input_budget=_estimate(final_messages)),
    )
    metadata = {
        "budget_section_hints": [
            {"name": "system_prompt", "required": True},
            {"name": "history", "required": False, "priority": 1},
            {"name": "current_task", "required": True},
        ]
    }

    first = provider.build_plan(messages=messages, metadata=metadata)
    second = provider.build_plan(messages=messages, metadata=metadata)

    assert first.observability["stable_prefix_reused"] is False
    assert second.observability["stable_prefix_reused"] is True
