from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import aworld.runners.execution_protocol as protocol
from aworld.core.common import ActionModel
from aworld.core.context.base import Context
from aworld.core.execution_protocol import (
    ExecutionProtocolPolicy,
    ProtocolMode,
    ReviewerDirectiveSource,
    ReviewerLeaseClosureReason,
    ReviewerLeaseStatus,
)
from aworld.core.task import Task


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _context(tmp_path, *, armed: bool = True, candidate: bool = True) -> Context:
    context = Context(task_id="reviewer-value")
    context.set_task(
        Task(
            id="reviewer-value",
            input="Create report.json whose value is at least 10.",
            timeout=600,
        )
    )
    output = tmp_path / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if candidate:
        output.write_text('{"value": 12}', encoding="utf-8")
    request = context.get_task().input
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-deliverable-1",
                "path": str(output),
                "display_path": "report.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    excerpt = "value is at least 10"
    context.context_info["public_task_contract"] = {
        "schema_version": "aworld.public-task-contract/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "request_fingerprint": _fingerprint(request),
        "obligations": [
            {
                "obligation_id": "public-obligation-1",
                "kind": "deliverable_file",
                "subject": "report.json",
                "expectation": {"path": str(output), "required": True},
                "source_fingerprint": _fingerprint("report.json"),
                "authority": "public_task_advisory",
                "source": "public_task_text",
            },
            {
                "obligation_id": "public-obligation-2",
                "kind": "numeric_threshold",
                "subject": excerpt,
                "expectation": {"statement": excerpt},
                "source_fingerprint": _fingerprint(excerpt),
                "authority": "public_task_advisory",
                "source": "public_task_text",
            },
        ],
    }
    if candidate:
        context.context_info["context_semantic_progress"] = {
            "agent": {
                "candidate_present": True,
                "public_deliverable_declared": True,
                "public_delivery_fingerprint": _fingerprint(
                    output.read_text(encoding="utf-8")
                ),
            }
        }
    protocol.configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            terminal_review_gate_enabled=True,
            final_review_timeout_seconds=30,
            finalization_reserve_seconds=60,
        ),
    )
    if armed:
        transition = protocol.record_model_execution_profile(
            context,
            "agent",
            {
                "horizon": "long",
                "confidence": 0.95,
                "milestone_count": 3,
                "expected_tool_actions": 8,
                "verification_required": True,
            },
        )
        assert transition is not None
        if not transition.state.long_horizon_armed:
            for index in range(6):
                protocol.record_tool_protocol_event(
                    context,
                    "agent",
                    {
                        "current_agent_step": index + 1,
                        "operation_hash": _fingerprint(f"operation-{index}"),
                        "result_hash": _fingerprint(f"result-{index}"),
                    },
                )
    return context


def _claim_and_direct(context: Context):
    offered = protocol.ensure_reviewer_value_lease_offer(context, "agent")
    assert offered is not None
    claimed = protocol.claim_reviewer_value_lease_for_review(context, "agent")
    assert claimed is not None
    lease_id = protocol.reviewer_value_lease_id(claimed)
    directed = protocol.record_reviewer_value_directive(
        context,
        "agent",
        lease_id=lease_id,
        counterexample="assert the exact lower boundary independently",
        source=ReviewerDirectiveSource.PUBLIC_CONTRACT_FALLBACK,
        status="inconclusive",
        decision="uncertain",
        reason_code="reviewer_uncertain",
        review_elapsed_ms=800,
    )
    assert directed is not None
    return directed, lease_id


def _arm_validation(context: Context, lease_id: str) -> None:
    review = ActionModel(
        tool_name="AWORLD_ADVISORY_VERIFIER",
        action_name="review_candidate",
        tool_call_id="review-call",
        params={"candidate_claim": "report is ready"},
    )
    payload = {
        "schema_version": "aworld.advisory-review/v2",
        "authority": "advisory_public_self_check",
        "status": "inconclusive",
        "decision": "uncertain",
        "recommended_framework_action": "task_derived_validation_once",
        "task_derived_validation_scope": [
            {"obligation_id": "public-obligation-2"}
        ],
        "task_derived_counterexample": {
            "schema_version": "aworld.task-derived-counterexample/v1",
            "lease_id": lease_id,
            "counterexample": "assert the exact lower boundary independently",
            "obligation_ids": ["public-obligation-2"],
            "requirements": ["execute one assertion"],
        },
    }
    assert protocol.record_public_probe_observations(
        context,
        "agent",
        actions=[review],
        result_projections=[
            {
                "tool_call_id": review.tool_call_id,
                "success": True,
                "content": json.dumps(payload),
            }
        ],
    ) == 0


def _validate(
    context: Context,
    *,
    call_id: str,
    success: bool,
    return_code: int,
) -> int:
    validation = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id=call_id,
        params={"command": "python check_boundary.py"},
    )
    assert protocol.record_public_probe_plan(
        context,
        "agent",
        tool_call_id=call_id,
        tool_identity="terminal:execute",
        arguments_projection=validation.params,
        value={
            "hypothesis_id": "reviewer-value-boundary",
            "highest_risk_counterexample": (
                "assert the exact lower boundary independently"
            ),
            "probe_kind": "counterexample",
            "obligation_ids": ["public-obligation-2"],
        },
    )
    return protocol.record_public_probe_observations(
        context,
        "agent",
        actions=[validation],
        result_projections=[
            {
                "tool_call_id": call_id,
                "success": success,
                "return_code": return_code,
            }
        ],
    )


def test_active_reviewer_validation_exposes_exact_probe_binding(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)

    assert protocol.reviewer_validation_probe_binding(context, "agent") == {
        "hypothesis_id": "reviewer-value-validation",
        "highest_risk_counterexample": (
            "assert the exact lower boundary independently"
        ),
        "probe_kind": "counterexample",
        "obligation_ids": ["public-obligation-2"],
    }


def test_offer_requires_armed_candidate_contract_and_closure_reserve(tmp_path):
    assert (
        protocol.ensure_reviewer_value_lease_offer(
            _context(tmp_path / "unarmed", armed=False), "agent"
        )
        is None
    )
    assert (
        protocol.ensure_reviewer_value_lease_offer(
            _context(tmp_path / "missing", candidate=False), "agent"
        )
        is None
    )
    preexisting = _context(tmp_path / "preexisting")
    preexisting.context_info.pop("context_semantic_progress")
    assert (
        protocol.ensure_reviewer_value_lease_offer(preexisting, "agent")
        is None
    )
    context = _context(tmp_path / "late")
    context.get_task().remaining_seconds = lambda: 100
    assert protocol.ensure_reviewer_value_lease_offer(context, "agent") is None
    boundary_low = _context(tmp_path / "boundary-low")
    boundary_low.get_task().remaining_seconds = lambda: 239
    assert (
        protocol.ensure_reviewer_value_lease_offer(boundary_low, "agent")
        is None
    )
    boundary_high = _context(tmp_path / "boundary-high")
    boundary_high.get_task().remaining_seconds = lambda: 241
    assert (
        protocol.ensure_reviewer_value_lease_offer(boundary_high, "agent")
        is not None
    )


def test_no_contract_skips_reviewer_and_submits_to_external_evaluation(tmp_path):
    context = _context(tmp_path)
    context.context_info.pop("public_task_contract")
    assert protocol.ensure_reviewer_value_lease_offer(context, "agent") is None
    assert protocol.load_reviewer_value_lease(context, "agent") is None

    transition = protocol.record_candidate_final(
        context,
        "agent",
        actions=[ActionModel(agent_name="agent", policy_info="best candidate")],
        review_boundary_available=True,
    )

    assert transition is not None
    assert transition.decision.action.value == "submit_current_result"
    assert transition.decision.reason.value == "terminal_fallback_submit"
    assert transition.state.acceptance_confirmed is False


def test_oversized_public_expectation_cannot_admit_reviewer(tmp_path):
    context = _context(tmp_path)
    context.context_info["public_task_contract"]["obligations"][1][
        "expectation"
    ] = {"statement": "x" * 5000}

    assert protocol.ensure_reviewer_value_lease_offer(context, "agent") is None
    assert protocol.load_reviewer_value_lease(context, "agent") is None


def test_offer_selects_one_unresolved_assertion_and_guides_once(tmp_path):
    context = _context(tmp_path)

    offered = protocol.ensure_reviewer_value_lease_offer(context, "agent")

    assert offered is not None
    assert offered.status is ReviewerLeaseStatus.OFFERED
    assert offered.obligation_id == "public-obligation-2"
    assert "review_candidate" in (
        protocol.reviewer_value_lease_guidance(context, "agent") or ""
    )
    assert protocol.reviewer_value_lease_guidance(context, "agent") is None


def test_claim_is_checkpointed_and_cannot_repeat_after_factory_rebuild(tmp_path):
    context = _context(tmp_path)
    assert protocol.ensure_reviewer_value_lease_offer(context, "agent") is not None

    first = protocol.claim_reviewer_value_lease_for_review(context, "agent")
    second = protocol.claim_reviewer_value_lease_for_review(context, "agent")

    assert first is not None
    assert first.status is ReviewerLeaseStatus.DIRECTIVE_PENDING
    assert second is None
    assert protocol.load_reviewer_value_lease(context, "agent") == first


def test_concurrent_claim_has_exactly_one_winner(tmp_path):
    context = _context(tmp_path)
    assert protocol.ensure_reviewer_value_lease_offer(context, "agent") is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: protocol.claim_reviewer_value_lease_for_review(
                    context, "agent"
                ),
                range(2),
            )
        )

    assert sum(result is not None for result in results) == 1


def test_claim_return_requires_persisted_transition(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    context = _context(tmp_path)
    assert protocol.ensure_reviewer_value_lease_offer(context, "agent") is not None
    offered_state = protocol._reviewer_value_state(context, "agent")

    def rolled_back(_context, _agent_id, _key, update):
        update(offered_state)
        return offered_state

    monkeypatch.setattr(protocol, "_update_runtime_value", rolled_back)

    assert protocol.claim_reviewer_value_lease_for_review(
        context, "agent"
    ) is None


def test_candidate_change_during_review_closes_without_validation_scope(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    context = _context(tmp_path)
    offered = protocol.ensure_reviewer_value_lease_offer(context, "agent")
    claimed = protocol.claim_reviewer_value_lease_for_review(context, "agent")
    assert offered is not None and claimed is not None
    monkeypatch.setattr(
        protocol,
        "_public_candidate_binding",
        lambda _context, _agent_id: (_fingerprint("new-candidate"), None),
    )

    recorded = protocol.record_reviewer_value_directive(
        context,
        "agent",
        lease_id=protocol.reviewer_value_lease_id(claimed),
        counterexample="assert the selected boundary",
        source=ReviewerDirectiveSource.REVIEWER,
        status="completed",
        decision="repair",
        reason_code=None,
        review_elapsed_ms=100,
    )

    assert recorded is None
    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.DIRECTIVE_UNAVAILABLE
    )


def test_terminal_guidance_exhaustion_closes_consumed_lease(tmp_path):
    context = _context(tmp_path)
    _claim_and_direct(context)

    assert protocol.reviewer_value_lease_guidance(
        context, "agent", terminal=True
    )
    assert protocol.reviewer_value_lease_guidance(
        context, "agent", terminal=True
    )
    assert (
        protocol.reviewer_value_lease_guidance(
            context, "agent", terminal=True
        )
        is None
    )

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.DIRECTIVE_NOT_CONSUMED
    )
    assert protocol.reviewer_value_lease_requires_continuation(
        context, "agent"
    ) is False
    reopened = protocol.ensure_reviewer_value_lease_offer(context, "agent")
    assert reopened == closed
    assert reopened.status is ReviewerLeaseStatus.CLOSED


def test_unclaimed_guidance_exhaustion_is_a_same_epoch_tombstone(tmp_path):
    context = _context(tmp_path)
    assert protocol.ensure_reviewer_value_lease_offer(context, "agent") is not None
    assert protocol.reviewer_value_lease_guidance(
        context, "agent", terminal=True
    )
    assert protocol.reviewer_value_lease_guidance(
        context, "agent", terminal=True
    )
    assert (
        protocol.reviewer_value_lease_guidance(
            context, "agent", terminal=True
        )
        is None
    )

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.REVIEWER_NOT_INVOKED
    )
    same_epoch = protocol.ensure_reviewer_value_lease_offer(context, "agent")
    assert same_epoch == closed
    assert same_epoch.status is ReviewerLeaseStatus.CLOSED


def test_successful_assertion_closes_one_lease(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)

    assert _validate(
        context, call_id="validate", success=True, return_code=0
    ) == 1

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is ReviewerLeaseClosureReason.VALIDATION_PASSED
    assert protocol.reviewer_value_lease_terminal_ready(context, "agent") is True

    protocol.store_candidate_fallback(
        context,
        "agent",
        [ActionModel(agent_name="agent", policy_info="report is ready")],
    )
    transition = protocol.record_candidate_final(
        context,
        "agent",
        actions=[ActionModel(agent_name="agent", policy_info="report is ready")],
        review_boundary_available=True,
    )
    assert transition is not None
    assert transition.decision.action.value == "submit_current_result"
    assert transition.decision.reason.value == "terminal_fallback_submit"
    assert transition.state.acceptance_confirmed is False
    telemetry = protocol.build_execution_protocol_telemetry(context, "agent")
    assert telemetry["schema_version"] == (
        "aworld.execution-protocol-telemetry/v3"
    )
    assert telemetry["reviewer_lease_status"] == "closed"
    assert telemetry["reviewer_directive_source"] == (
        "public_contract_fallback"
    )
    assert telemetry["reviewer_closure_reason"] == "validation_passed"
    assert telemetry["reviewer_claim_count"] == 1
    assert telemetry["reviewer_validation_attempt_count"] == 1
    assert telemetry["reviewer_value_realized"] is True
    assert telemetry["reviewer_elapsed_ms"] == 800


def test_explicit_public_probe_metadata_consumes_the_same_lease(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    validation = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="metadata-validation",
        params={"command": "python check_boundary.py"},
    )
    assert protocol.record_public_probe_plan(
        context,
        "agent",
        tool_call_id=validation.tool_call_id,
        tool_identity="terminal:execute",
        arguments_projection=validation.params,
        value={
            "hypothesis_id": "reviewer-value-boundary",
            "highest_risk_counterexample": (
                "assert the exact lower boundary independently"
            ),
            "probe_kind": "counterexample",
            "obligation_ids": ["public-obligation-2"],
        },
    )

    assert protocol.record_public_probe_observations(
        context,
        "agent",
        actions=[validation],
        result_projections=[
            {
                "tool_call_id": validation.tool_call_id,
                "success": True,
                "return_code": 0,
            }
        ],
    ) == 1

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is ReviewerLeaseClosureReason.VALIDATION_PASSED


def test_candidate_change_after_validation_requires_one_revalidation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    assert _validate(
        context, call_id="initial-validation", success=True, return_code=0
    ) == 1
    changed_hash = _fingerprint("changed-after-validation")
    monkeypatch.setattr(
        protocol,
        "_public_candidate_binding",
        lambda _context, _agent_id: (changed_hash, "candidate-changed"),
    )

    guidance = protocol.reviewer_value_lease_guidance(
        context,
        "agent",
        terminal=True,
    )

    assert guidance is not None and "Revalidate" in guidance
    reopened = protocol.load_reviewer_value_lease(context, "agent")
    assert reopened is not None
    assert reopened.status is ReviewerLeaseStatus.REVALIDATION_PENDING
    assert _validate(
        context, call_id="changed-revalidation", success=True, return_code=0
    ) == 1
    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.REVALIDATION_PASSED
    )


def test_unbound_single_assertion_cannot_close_selected_obligation(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    validation = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="unbound-validation",
        params={"command": "python unrelated_check.py"},
    )

    assert protocol.record_public_probe_observations(
        context,
        "agent",
        actions=[validation],
        result_projections=[
            {
                "tool_call_id": validation.tool_call_id,
                "success": True,
                "return_code": 0,
            }
        ],
    ) == 0

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.VALIDATION_UNAVAILABLE
    )


def test_contradiction_allows_one_changed_repair_and_revalidation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    assert _validate(
        context, call_id="contradict", success=False, return_code=1
    ) == 1
    contradicted = protocol.load_reviewer_value_lease(context, "agent")
    assert contradicted is not None
    assert contradicted.status is ReviewerLeaseStatus.REPAIR_PENDING

    repaired_hash = _fingerprint("repaired-candidate")
    monkeypatch.setattr(
        protocol,
        "_public_candidate_binding",
        lambda _context, _agent_id: (repaired_hash, "candidate-2"),
    )
    repair = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="repair",
        params={"command": "python repair_report.py"},
    )
    protocol.record_public_probe_observations(
        context,
        "agent",
        actions=[repair],
        result_projections=[{"tool_call_id": "repair", "success": True}],
    )
    repaired = protocol.load_reviewer_value_lease(context, "agent")
    assert repaired is not None
    assert repaired.status is ReviewerLeaseStatus.REVALIDATION_PENDING

    assert _validate(
        context, call_id="revalidate", success=True, return_code=0
    ) == 1
    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.REVALIDATION_PASSED
    )


def test_ambiguous_validation_batch_closes_without_repair(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    actions = [
        ActionModel(
            tool_name="terminal",
            action_name="execute",
            tool_call_id=f"validation-{index}",
            params={"command": f"python check_{index}.py"},
        )
        for index in range(2)
    ]

    protocol.record_public_probe_observations(
        context,
        "agent",
        actions=actions,
        result_projections=[
            {"tool_call_id": action.tool_call_id, "success": True}
            for action in actions
        ],
    )

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.VALIDATION_AMBIGUOUS
    )


def test_multiple_matching_explicit_probes_close_as_ambiguous(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    actions = [
        ActionModel(
            tool_name="terminal",
            action_name="execute",
            tool_call_id=f"explicit-{index}",
            params={"command": f"python check_boundary.py --seed {index}"},
        )
        for index in range(2)
    ]
    for action in actions:
        assert protocol.record_public_probe_plan(
            context,
            "agent",
            tool_call_id=action.tool_call_id,
            tool_identity="terminal:execute",
            arguments_projection=action.params,
            value={
                "hypothesis_id": "reviewer-value-boundary",
                "highest_risk_counterexample": (
                    "assert the exact lower boundary independently"
                ),
                "probe_kind": "counterexample",
                "obligation_ids": ["public-obligation-2"],
            },
        )

    assert protocol.record_public_probe_observations(
        context,
        "agent",
        actions=actions,
        result_projections=[
            {
                "tool_call_id": actions[0].tool_call_id,
                "success": True,
                "return_code": 0,
            },
            {
                "tool_call_id": actions[1].tool_call_id,
                "success": False,
                "return_code": 1,
            },
        ],
    ) == 2

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.VALIDATION_AMBIGUOUS
    )
    lease_receipts = [
        receipt
        for receipt in protocol.load_public_probe_receipts(context, "agent")
        if receipt.get("reviewer_lease_id") == lease_id
    ]
    assert len(lease_receipts) == 2
    assert {item["probe_assessment"] for item in lease_receipts} == {
        "ambiguous_batch"
    }


def test_missing_validation_result_closes_unavailable(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="missing-result",
        params={"command": "python check_boundary.py"},
    )

    assert protocol.record_public_probe_observations(
        context,
        "agent",
        actions=[action],
        result_projections=[],
    ) == 0

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.VALIDATION_UNAVAILABLE
    )


def test_timeout_is_unavailable_not_a_counterexample(tmp_path):
    context = _context(tmp_path)
    _lease, lease_id = _claim_and_direct(context)
    _arm_validation(context, lease_id)
    validation = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="timed-out-validation",
        params={"command": "python check_boundary.py"},
    )
    assert protocol.record_public_probe_plan(
        context,
        "agent",
        tool_call_id=validation.tool_call_id,
        tool_identity="terminal:execute",
        arguments_projection=validation.params,
        value={
            "hypothesis_id": "reviewer-value-boundary",
            "highest_risk_counterexample": (
                "assert the exact lower boundary independently"
            ),
            "probe_kind": "counterexample",
            "obligation_ids": ["public-obligation-2"],
        },
    )

    protocol.record_public_probe_observations(
        context,
        "agent",
        actions=[validation],
        result_projections=[
            {
                "tool_call_id": validation.tool_call_id,
                "success": False,
                "return_code": -1,
                "metadata": {
                    "error_type": "timeout",
                    "terminal_execution_receipt": {
                        "timed_out": True,
                        "executed": True,
                    },
                },
            }
        ],
    )

    closed = protocol.load_reviewer_value_lease(context, "agent")
    assert closed is not None
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.VALIDATION_UNAVAILABLE
    )
    assert closed.repair_attempt_count == 0
