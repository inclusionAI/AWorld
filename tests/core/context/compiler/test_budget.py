from __future__ import annotations

import pytest

from aworld.core.context.compiler import (
    Authority,
    BudgetCandidate,
    ContextInputBudget,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    ItemTokenLimitExceeded,
    Lifetime,
    RequiredContextBudgetExceeded,
    ResolutionAction,
    ResolutionReason,
    SourceKind,
    Stability,
    TokenEstimate,
    Trust,
    UnknownTokenEstimate,
    plan_context_budget,
)


def _item(
    item_id: str,
    *,
    priority: int = 0,
    required: bool = False,
    kind: ContextKind = ContextKind.MEMORY,
    token_limit: int | None = None,
) -> ContextItem:
    return ContextItem(
        id=item_id,
        kind=kind,
        payload={"content": item_id},
        task_epoch=1,
        authority=Authority.APPLICATION_AGENT,
        scope=ContextScope.unknown(),
        lifetime=Lifetime.TASK,
        priority=priority,
        required=required,
        trust=Trust.TRUSTED,
        stability=Stability.TURN_DYNAMIC,
        token_limit=token_limit,
        reducer=None,
        source=ContextSource(kind=SourceKind.AGENT),
        activation_reason="test",
    )


def _candidate(
    item: ContextItem,
    tokens: int | None,
    *,
    exact: bool = True,
    atomic_group: str | None = None,
) -> BudgetCandidate:
    estimate = (
        TokenEstimate.unknown()
        if tokens is None
        else TokenEstimate(value=tokens, estimator="test-v1", exact=exact)
    )
    return BudgetCandidate(
        item=item,
        tokens=estimate,
        atomic_group=atomic_group,
    )


def test_input_budget_uses_all_reserves_without_mutating_output_reserve() -> None:
    budget = ContextInputBudget(
        context_limit=100,
        reserved_output_tokens=20,
        provider_protocol_reserve=5,
        safety_margin_tokens=7,
        max_item_tokens=60,
    )

    assert budget.available_input_tokens == 68
    assert budget.reserved_output_tokens == 20

    with pytest.raises(ValueError, match="reserves exceed context limit"):
        ContextInputBudget(
            context_limit=10,
            reserved_output_tokens=6,
            provider_protocol_reserve=3,
            safety_margin_tokens=2,
            max_item_tokens=10,
        )


def test_budget_keeps_required_then_explicit_priority_but_preserves_request_order() -> None:
    candidates = (
        _candidate(_item("low", priority=1), 4),
        _candidate(_item("required", required=True), 5),
        _candidate(_item("high", priority=20), 6),
        _candidate(_item("middle", priority=10), 5),
    )
    budget = ContextInputBudget(
        context_limit=16,
        reserved_output_tokens=0,
        provider_protocol_reserve=0,
        safety_margin_tokens=0,
        max_item_tokens=16,
    )

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["required", "high", "middle"]
    assert [decision.item_id for decision in plan.decisions] == [
        "low",
        "required",
        "high",
        "middle",
    ]
    assert [decision.action for decision in plan.decisions] == [
        ResolutionAction.EXCLUDED,
        ResolutionAction.INCLUDED,
        ResolutionAction.INCLUDED,
        ResolutionAction.INCLUDED,
    ]
    assert plan.decisions[0].reason is ResolutionReason.BUDGET_EXCLUDED
    assert plan.decisions[1].reason is ResolutionReason.REQUIRED
    assert plan.token_accounting.total_before.value == 20
    assert plan.token_accounting.total_after.value == 16


def test_required_overflow_is_typed_and_never_reduces_reserved_output() -> None:
    budget = ContextInputBudget(
        context_limit=20,
        reserved_output_tokens=8,
        provider_protocol_reserve=2,
        safety_margin_tokens=2,
        max_item_tokens=20,
    )
    candidates = (
        _candidate(_item("required-a", required=True), 5),
        _candidate(_item("required-b", required=True), 4),
    )

    with pytest.raises(RequiredContextBudgetExceeded) as captured:
        plan_context_budget(candidates, budget)

    assert captured.value.code == "required_context_budget_exceeded"
    assert captured.value.required_tokens == 9
    assert captured.value.available_tokens == 8
    assert budget.reserved_output_tokens == 8


def test_atomic_tool_pair_is_selected_or_excluded_as_one_unit() -> None:
    candidates = (
        _candidate(_item("background", priority=20), 6),
        _candidate(
            _item("tool-call", priority=10, kind=ContextKind.SYSTEM),
            3,
            atomic_group="tool-pair-1",
        ),
        _candidate(
            _item("tool-result", priority=10, kind=ContextKind.TOOL_RESULT),
            4,
            atomic_group="tool-pair-1",
        ),
    )
    budget = ContextInputBudget(
        context_limit=10,
        reserved_output_tokens=0,
        provider_protocol_reserve=0,
        safety_margin_tokens=0,
        max_item_tokens=10,
    )

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["background"]
    pair_decisions = plan.decisions[1:]
    assert all(
        decision.action is ResolutionAction.EXCLUDED
        and decision.reason is ResolutionReason.BUDGET_EXCLUDED
        for decision in pair_decisions
    )


def test_unknown_estimate_and_required_item_limit_fail_before_enforce() -> None:
    budget = ContextInputBudget(
        context_limit=100,
        reserved_output_tokens=10,
        provider_protocol_reserve=5,
        safety_margin_tokens=5,
        max_item_tokens=30,
    )

    with pytest.raises(UnknownTokenEstimate) as unknown:
        plan_context_budget(
            (_candidate(_item("unknown", required=True), None),),
            budget,
        )
    assert unknown.value.code == "token_estimate_unknown"

    with pytest.raises(ItemTokenLimitExceeded) as exceeded:
        plan_context_budget(
            (_candidate(_item("too-large", required=True), 31),),
            budget,
        )
    assert exceeded.value.code == "required_item_token_limit_exceeded"
    assert exceeded.value.limit == 30


def test_optional_item_limit_excludes_the_whole_atomic_group() -> None:
    budget = ContextInputBudget(
        context_limit=100,
        reserved_output_tokens=0,
        provider_protocol_reserve=0,
        safety_margin_tokens=0,
        max_item_tokens=20,
    )
    candidates = (
        _candidate(
            _item("oversized", priority=10),
            21,
            atomic_group="optional-group",
        ),
        _candidate(
            _item("mate", priority=10),
            1,
            atomic_group="optional-group",
        ),
        _candidate(_item("kept", priority=1), 2),
    )

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["kept"]
    assert [decision.reason for decision in plan.decisions] == [
        ResolutionReason.ITEM_TOKEN_LIMIT_EXCEEDED,
        ResolutionReason.ITEM_TOKEN_LIMIT_EXCEEDED,
        ResolutionReason.BUDGET_INCLUDED,
    ]

