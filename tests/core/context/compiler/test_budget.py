from __future__ import annotations

import pytest

from aworld.core.context.compiler import (
    AtomicGroupRef,
    Authority,
    BudgetAllocationTier,
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
    UnversionedTokenEstimator,
    plan_context_budget,
)


def _item(
    item_id: str,
    *,
    priority: int = 0,
    required: bool = False,
    kind: ContextKind = ContextKind.MEMORY,
    token_limit: int | None = None,
    authority: Authority = Authority.APPLICATION_AGENT,
    scope: ContextScope | None = None,
    trust: Trust = Trust.TRUSTED,
) -> ContextItem:
    return ContextItem(
        id=item_id,
        kind=kind,
        payload={"content": item_id},
        task_epoch=1,
        authority=authority,
        scope=scope or ContextScope.unknown(),
        lifetime=Lifetime.TASK,
        priority=priority,
        required=required,
        trust=trust,
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
    atomic_group: AtomicGroupRef | None = None,
    dependency_item_ids: tuple[str, ...] = (),
    allocation_tier: BudgetAllocationTier | None = None,
) -> BudgetCandidate:
    estimate = (
        TokenEstimate.unknown()
        if tokens is None
        else TokenEstimate(value=tokens, estimator="test-v1", exact=exact)
    )
    return BudgetCandidate(
        item=item,
        tokens=estimate,
        allocation_tier=allocation_tier or BudgetAllocationTier(0, "default"),
        atomic_group=atomic_group,
        dependency_item_ids=dependency_item_ids,
    )


def test_optional_dependency_closure_is_selected_or_excluded_atomically():
    skill = _item("skill")
    shared_tool = _item("tool", kind=ContextKind.TOOL_CATALOG)
    other_skill = _item("other-skill")
    candidates = (
        _candidate(
            skill, 4,
            dependency_item_ids=("tool",),
            allocation_tier=BudgetAllocationTier(0, "skill"),
        ),
        _candidate(
            other_skill, 3,
            dependency_item_ids=("tool",),
            allocation_tier=BudgetAllocationTier(0, "skill"),
        ),
        _candidate(
            shared_tool, 6,
            allocation_tier=BudgetAllocationTier(1, "tool"),
        ),
    )

    tight = plan_context_budget(candidates, ContextInputBudget(9, 0, 0, 0, 20))
    enough = plan_context_budget(candidates, ContextInputBudget(10, 0, 0, 0, 20))

    assert [item.id for item in tight.selected_items] == ["other-skill", "tool"]
    assert [item.id for item in enough.selected_items] == ["skill", "tool"]


def test_required_dependency_closure_fails_closed_when_budget_is_insufficient():
    skill = _item("required-skill", required=True)
    tool = _item("required-tool", kind=ContextKind.TOOL_CATALOG)

    with pytest.raises(RequiredContextBudgetExceeded):
        plan_context_budget(
            (
                _candidate(skill, 4, dependency_item_ids=("required-tool",)),
                _candidate(tool, 6),
            ),
            ContextInputBudget(9, 0, 0, 0, 20),
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
    assert plan.token_accounting.provider_protocol_reserve.value == 0
    assert plan.token_accounting.safety_margin.value == 0
    assert plan.token_accounting.available_input.value == 16


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
    tool_pair = AtomicGroupRef(
        "tool",
        "request",
        "tool-pair-1",
        selection_priority=10,
    )
    candidates = (
        _candidate(_item("background", priority=20), 6),
        _candidate(
            _item("tool-call", priority=10, kind=ContextKind.SYSTEM),
            3,
            atomic_group=tool_pair,
        ),
        _candidate(
            _item(
                "tool-result",
                priority=999,
                kind=ContextKind.TOOL_RESULT,
                authority=Authority.EXTERNAL_TOOL,
                trust=Trust.TOOL_UNTRUSTED,
            ),
            4,
            atomic_group=tool_pair,
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
            atomic_group=AtomicGroupRef("test", "request", "optional-group"),
        ),
        _candidate(
            _item("mate", priority=10),
            1,
            atomic_group=AtomicGroupRef("test", "request", "optional-group"),
        ),
        _candidate(_item("kept", priority=1), 2),
    )

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["kept"]
    assert [decision.reason for decision in plan.decisions] == [
        ResolutionReason.ITEM_TOKEN_LIMIT_EXCEEDED,
        ResolutionReason.ATOMIC_GROUP_ITEM_LIMIT_EXCEEDED,
        ResolutionReason.BUDGET_INCLUDED,
    ]


def test_caller_atomic_group_cannot_collide_with_internal_singleton_key() -> None:
    budget = ContextInputBudget(
        context_limit=2,
        reserved_output_tokens=0,
        provider_protocol_reserve=0,
        safety_margin_tokens=0,
        max_item_tokens=2,
    )
    candidates = (
        _candidate(_item("standalone", priority=20), 2),
        _candidate(
            _item("caller-group", priority=10),
            1,
            atomic_group=AtomicGroupRef("caller", "request", "__single__:0"),
        ),
    )

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["standalone"]
    assert plan.decisions[1].action is ResolutionAction.EXCLUDED


def test_same_group_id_from_different_owners_remains_independent() -> None:
    budget = ContextInputBudget(4, 0, 0, 0, 4)
    candidates = (
        _candidate(
            _item("owner-a", priority=20),
            4,
            atomic_group=AtomicGroupRef("owner-a", "request", "pair"),
        ),
        _candidate(
            _item("owner-b", priority=10),
            1,
            atomic_group=AtomicGroupRef("owner-b", "request", "pair"),
        ),
    )

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["owner-a"]


def test_allocation_tier_precedes_priority_across_authority_scope_domains() -> None:
    instruction_scope = ContextScope(kinds=("task",), task_id="task-1")
    external_scope = ContextScope(kinds=("session",), session_id="session-1")
    candidates = (
        _candidate(
            _item(
                "external-memory",
                priority=100,
                authority=Authority.EXTERNAL_TOOL,
                scope=external_scope,
            ),
            4,
            allocation_tier=BudgetAllocationTier(20, "optional-evidence"),
        ),
        _candidate(
            _item(
                "instruction",
                priority=1,
                kind=ContextKind.INSTRUCTION,
                authority=Authority.APPLICATION_AGENT,
                scope=instruction_scope,
            ),
            4,
            allocation_tier=BudgetAllocationTier(10, "control"),
        ),
    )
    budget = ContextInputBudget(4, 0, 0, 0, 4)

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["instruction"]


def test_priority_only_reorders_within_same_authority_scope_domain() -> None:
    scope_a = ContextScope(kinds=("task",), task_id="task-a")
    scope_b = ContextScope(kinds=("task",), task_id="task-b")
    tier = BudgetAllocationTier(10, "evidence")
    candidates = (
        _candidate(
            _item("a-low", priority=1, scope=scope_a),
            2,
            allocation_tier=tier,
        ),
        _candidate(
            _item("b-high", priority=100, scope=scope_b),
            2,
            allocation_tier=tier,
        ),
        _candidate(
            _item("a-high", priority=10, scope=scope_a),
            2,
            allocation_tier=tier,
        ),
    )
    budget = ContextInputBudget(2, 0, 0, 0, 2)

    plan = plan_context_budget(candidates, budget)

    assert [item.id for item in plan.selected_items] == ["a-high"]


def test_enforce_refuses_unversioned_token_estimator() -> None:
    budget = ContextInputBudget(10, 0, 0, 0, 10)
    candidate = BudgetCandidate(
        item=_item("unversioned"),
        tokens=TokenEstimate(value=1, estimator="approximate", exact=False),
        allocation_tier=BudgetAllocationTier(0, "default"),
    )

    with pytest.raises(UnversionedTokenEstimator) as captured:
        plan_context_budget((candidate,), budget)

    assert captured.value.code == "token_estimator_unversioned"
