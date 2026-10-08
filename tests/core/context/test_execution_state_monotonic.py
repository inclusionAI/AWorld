from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from aworld.core.context.base import Context
from aworld.core.context.execution_state import (
    EXECUTION_STATE_SCHEMA,
    execution_resolution_observation,
    get_execution_state,
    reconcile_execution_states,
    record_execution_resolution,
    record_execution_state,
)


def _context(task_id: str = "task", *, epoch: int | None = None) -> Context:
    context = Context(task_id=task_id)
    if epoch is not None:
        context._context_lifecycle_state = type(
            "Lifecycle", (), {"task_epoch": epoch}
        )()
    return context


def test_generic_running_and_success_cannot_erase_truncated_action() -> None:
    context = _context("regex-like")

    blocked = record_execution_state(
        context,
        "solver",
        "incomplete",
        "model_output_truncated",
    )
    running = record_execution_state(
        context,
        "solver",
        "running",
        "model_response_recovery_scheduled",
    )
    succeeded = record_execution_state(
        context,
        "solver",
        "succeeded",
        "agent_final_response",
    )

    assert blocked["revision"] < running["revision"] < succeeded["revision"]
    assert succeeded["status"] == "incomplete"
    assert succeeded["reason"] == "model_output_truncated"
    assert succeeded["last_event"]["requested_status"] == "succeeded"
    assert len(succeeded["unresolved_blockers"]) == 1


def test_fresh_complete_provider_actions_resolve_only_model_response_blockers() -> None:
    for evidence_kind in (
        "complete_provider_tool_action",
        "complete_provider_final_action",
    ):
        context = _context(evidence_kind)
        record_execution_state(
            context,
            "solver",
            "incomplete",
            "model_output_truncated",
        )
        observation = execution_resolution_observation(context, "solver")

        recovered = record_execution_resolution(
            context,
            "solver",
            evidence_kind=evidence_kind,
            status="running",
            reason="model_response_accepted",
            observation=observation,
        )

        assert recovered["status"] == "running"
        assert recovered["unresolved_blockers"] == []
        assert recovered["resolution_evidence"][0]["evidence_kind"] == evidence_kind
        completed = record_execution_state(
            context, "solver", "succeeded", "agent_final_response"
        )
        assert completed["status"] == "succeeded"


def test_budget_stop_is_sticky_without_positive_budget_resolution() -> None:
    context = _context("budget")
    record_execution_state(
        context,
        "solver",
        "budget_exhausted",
        "long_horizon_generation_budget_exhausted",
    )
    observation = execution_resolution_observation(context, "solver")
    record_execution_resolution(
        context,
        "solver",
        evidence_kind="complete_provider_final_action",
        status="succeeded",
        reason="agent_final_response",
        observation=observation,
    )
    state = record_execution_state(context, "solver", "succeeded", "summary_fallback")

    assert state["status"] == "budget_exhausted"
    assert state["reason"] == "long_horizon_generation_budget_exhausted"


def test_uncertain_reviewer_requires_typed_critic_acceptance() -> None:
    context = _context("review")
    record_execution_state(
        context,
        "solver",
        "incomplete",
        "independent_acceptance_review_error",
    )
    provider_observation = execution_resolution_observation(context, "solver")
    still_blocked = record_execution_resolution(
        context,
        "solver",
        evidence_kind="complete_provider_final_action",
        status="succeeded",
        reason="agent_final_response",
        observation=provider_observation,
    )
    assert still_blocked["status"] == "incomplete"

    accepted = record_execution_resolution(
        context,
        "solver",
        evidence_kind="accepted_critic",
        status="succeeded",
        reason="independent_acceptance_accepted",
        observation=execution_resolution_observation(context, "solver"),
    )
    assert accepted["status"] == "succeeded"
    assert accepted["unresolved_blockers"] == []


def test_reconciliation_is_fail_closed_for_stale_and_tied_copies() -> None:
    blocked_context = _context("fan-in")
    blocked = record_execution_state(
        blocked_context,
        "solver",
        "incomplete",
        "model_output_truncated",
    )

    stale_context = _context("fan-in")
    stale = record_execution_state(
        stale_context, "solver", "succeeded", "agent_final_response"
    )
    for index in range(4):
        stale = record_execution_state(
            stale_context, "solver", "succeeded", f"generic_success_{index}"
        )

    reconciled = reconcile_execution_states(
        [stale, blocked],
        task_id="fan-in",
        task_epoch=blocked_context.task_epoch,
        agent_id="solver",
    )

    assert stale["revision"] > blocked["revision"]
    assert reconciled is not None
    assert reconciled["status"] == "incomplete"
    assert reconciled["reason"] == "model_output_truncated"

    tied_budget = dict(blocked)
    tied_budget["status"] = "budget_exhausted"
    tied_budget["reason"] = "agent_loop_budget_exhausted"
    tied_budget["unresolved_blockers"] = [
        {
            **blocked["unresolved_blockers"][0],
            "category": "budget",
            "status": "budget_exhausted",
            "reason": "agent_loop_budget_exhausted",
            "blocker_id": "budget-tie",
        }
    ]
    fail_closed = reconcile_execution_states(
        [blocked, tied_budget],
        task_id="fan-in",
        task_epoch=blocked_context.task_epoch,
        agent_id="solver",
    )
    assert fail_closed["status"] == "budget_exhausted"


def test_resolution_is_causal_and_does_not_clear_unobserved_tied_blocker() -> None:
    left = _context("causal")
    right = _context("causal")
    first = record_execution_state(
        left, "solver", "incomplete", "model_output_truncated"
    )
    resolution = record_execution_resolution(
        left,
        "solver",
        evidence_kind="complete_provider_tool_action",
        status="running",
        reason="model_response_accepted",
        observation=execution_resolution_observation(left, "solver"),
    )
    divergent = record_execution_state(
        right, "solver", "incomplete", "incomplete_tool_arguments"
    )

    assert first["revision"] == divergent["revision"]
    merged = reconcile_execution_states(
        [resolution, divergent],
        task_id="causal",
        task_epoch=left.task_epoch,
        agent_id="solver",
    )
    assert merged["status"] == "incomplete"
    assert merged["reason"] == "incomplete_tool_arguments"


def test_out_of_order_provider_response_cannot_clear_newer_blocker() -> None:
    context = _context("out-of-order")
    stale_request = execution_resolution_observation(context, "solver")
    record_execution_state(
        context,
        "solver",
        "incomplete",
        "model_output_truncated",
    )

    stale_response = record_execution_resolution(
        context,
        "solver",
        evidence_kind="complete_provider_tool_action",
        status="running",
        reason="model_response_accepted",
        observation=stale_request,
    )
    assert stale_response["status"] == "incomplete"
    assert stale_response["reason"] == "model_output_truncated"

    fresh_request = execution_resolution_observation(context, "solver")
    recovered = record_execution_resolution(
        context,
        "solver",
        evidence_kind="complete_provider_tool_action",
        status="running",
        reason="model_response_accepted",
        observation=fresh_request,
    )
    assert recovered["status"] == "running"


def test_newest_resolution_is_retained_after_bounded_ledger_fills() -> None:
    context = _context("resolution-ledger")
    state = None
    stale_first_blocker = None
    for index in range(24):
        blocked = record_execution_state(
            context,
            "solver",
            "incomplete",
            f"delivery_candidate_missing_{index}",
        )
        if stale_first_blocker is None:
            stale_first_blocker = json.loads(json.dumps(blocked))
        observation = execution_resolution_observation(context, "solver")
        state = record_execution_resolution(
            context,
            "solver",
            evidence_kind="candidate_advanced",
            reason="public_candidate_advanced",
            observation=observation,
        )
        assert state["status"] == "running"

    assert state is not None
    assert len(state["resolution_evidence"]) <= 16
    assert state["resolution_evidence"][-1]["revision"] == state["revision"]
    assert state["resolution_watermarks"]
    reconciled = reconcile_execution_states(
        [state, stale_first_blocker],
        task_id="resolution-ledger",
        task_epoch=context.task_epoch,
        agent_id="solver",
    )
    assert reconciled is not None
    assert reconciled["status"] == "running"


def test_same_category_blockers_remain_independent_until_each_is_resolved() -> None:
    context = _context("same-category")
    record_execution_state(context, "solver", "incomplete", "model_output_truncated")
    record_execution_state(context, "solver", "incomplete", "incomplete_tool_arguments")
    state = get_execution_state(context, agent_id="solver")
    assert state is not None
    assert len(state["unresolved_blockers"]) == 2
    blocker_by_reason = {
        blocker["reason"]: blocker for blocker in state["unresolved_blockers"]
    }
    assert (
        blocker_by_reason["incomplete_tool_arguments"]["revision"]
        > (blocker_by_reason["model_output_truncated"]["revision"])
    )

    partially_resolved = record_execution_resolution(
        context,
        "solver",
        evidence_kind="complete_provider_tool_action",
        reason="model_response_accepted",
        observation=execution_resolution_observation(context, "solver"),
    )

    assert partially_resolved["status"] == "incomplete"
    assert [
        blocker["reason"] for blocker in partially_resolved["unresolved_blockers"]
    ] == ["model_output_truncated"]


def test_independent_tied_events_have_distinct_stable_blocker_ids() -> None:
    left = _context("independent-events")
    right = _context("independent-events")
    left_state = record_execution_state(
        left, "solver", "incomplete", "model_output_truncated"
    )
    right_state = record_execution_state(
        right, "solver", "incomplete", "model_output_truncated"
    )
    left_id = left_state["unresolved_blockers"][0]["blocker_id"]
    right_id = right_state["unresolved_blockers"][0]["blocker_id"]

    assert left_state["revision"] == right_state["revision"]
    assert left_id != right_id
    transported_left = json.loads(json.dumps(left_state))
    merged = reconcile_execution_states(
        [left_state, transported_left, right_state],
        task_id="independent-events",
        task_epoch=left.task_epoch,
        agent_id="solver",
    )
    assert merged is not None
    assert {item["blocker_id"] for item in merged["unresolved_blockers"]} == {
        left_id,
        right_id,
    }


def test_candidate_evidence_does_not_resolve_unbound_external_dependency() -> None:
    context = _context("bound-resolution")
    record_execution_state(
        context, "solver", "incomplete", "external_dependency_missing"
    )
    observation = execution_resolution_observation(context, "solver")

    state = record_execution_resolution(
        context,
        "solver",
        evidence_kind="candidate_advanced",
        reason="public_candidate_advanced",
        observation=observation,
    )

    assert state["status"] == "incomplete"
    assert state["reason"] == "external_dependency_missing"


def test_resolution_api_requires_action_start_observation() -> None:
    context = _context("required-observation")
    with pytest.raises(TypeError):
        record_execution_resolution(
            context,
            "solver",
            evidence_kind="candidate_advanced",
            reason="public_candidate_advanced",
        )


def test_execution_state_is_task_scoped_and_migrates_v1() -> None:
    current = _context("current")
    current.context_info["agent_execution_state"] = {
        "schema_version": "aworld.agent.execution-state/v1",
        "task_id": "current",
        "task_epoch": current.task_epoch,
        "agent_id": "solver",
        "status": "incomplete",
        "reason": "model_output_truncated",
        "recoverable": True,
        "work_state_revision": 7,
    }
    migrated = get_execution_state(current, agent_id="solver")
    assert migrated is not None
    assert migrated["schema_version"] == EXECUTION_STATE_SCHEMA
    assert migrated["revision"] >= 7
    assert migrated["status"] == "incomplete"

    other = _context("other")
    other.context_info["agent_execution_state"] = dict(
        current.context_info["agent_execution_state"]
    )
    assert get_execution_state(other, agent_id="solver") is None


def test_execution_state_is_bounded_json_and_does_not_retain_raw_reason() -> None:
    context = _context("bounded")
    secret = "secret model output " * 500
    state = record_execution_state(context, "solver", "incomplete", secret)

    encoded = json.dumps(state, sort_keys=True)
    assert len(encoded) < 8_000
    assert secret[:100] not in encoded
    assert state["reason"] == "unclassified_execution_blocker"
    assert len(state["unresolved_blockers"]) <= 8

    for index in range(40):
        record_execution_state(
            context, "solver", "incomplete", f"delivery_pending_{index}"
        )
        state = record_execution_resolution(
            context,
            "solver",
            evidence_kind="candidate_advanced",
            reason="public_candidate_advanced",
            observation=execution_resolution_observation(context, "solver"),
        )
    assert len(json.dumps(state, sort_keys=True)) < 8_000
    assert len(state["resolution_evidence"]) <= 16


def test_shared_transport_copies_update_one_atomic_revision_stream() -> None:
    context = _context("atomic")
    transported = context.deep_copy()

    def write(index: int) -> int:
        target = context if index % 2 else transported
        state = record_execution_state(
            target,
            "solver",
            "budget_exhausted" if index == 3 else "running",
            "agent_loop_budget_exhausted" if index == 3 else f"running_{index}",
        )
        return state["revision"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        revisions = list(pool.map(write, range(12)))

    assert sorted(revisions) == list(range(1, 13))
    assert (
        get_execution_state(context, agent_id="solver")["status"] == "budget_exhausted"
    )
    assert (
        get_execution_state(transported, agent_id="solver")["status"]
        == "budget_exhausted"
    )
