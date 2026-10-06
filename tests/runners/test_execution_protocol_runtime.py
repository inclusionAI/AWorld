from __future__ import annotations

import pytest

from aworld.core.context.base import Context
from aworld.core.common import ActionModel
from aworld.core.context.compiler import (
    CompletionContract,
    CompletionMode,
    LifecycleAction,
    ValidationCommand,
)
from aworld.core.execution_protocol import (
    action_signature,
    ControllerAction,
    DecisionReason,
    ExecutionProtocolPolicy,
    ExecutionProtocolStore,
    ProtocolMode,
    ProtocolPhase,
)
from aworld.core.task import Task
from aworld.runners.execution_protocol import (
    build_execution_protocol_telemetry,
    configure_execution_protocol,
    consume_execution_protocol_guidance,
    execution_protocol_accepts_model_profile,
    execution_protocol_model_decision_boundary,
    execution_protocol_policy,
    execution_protocol_requires_tool_free_finalization,
    final_review_guidance,
    load_candidate_fallback,
    load_execution_protocol_state,
    load_model_plan_update,
    model_owned_review_active,
    record_candidate_final,
    record_model_execution_profile,
    record_model_decision_boundary,
    record_model_plan_update,
    record_pre_generation_delivery_decision,
    record_review_repair_decision,
    record_review_tool_action,
    record_tool_protocol_event,
    store_candidate_fallback,
)


@pytest.fixture(autouse=True)
def _legacy_protocol_features(monkeypatch):
    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "false")
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "false")


def _context(task_id: str = "long") -> Context:
    context = Context(task_id=task_id)
    context.set_task(Task(id=task_id, input="complete the public request", timeout=600))
    return context


def _semantic_state(**overrides):
    state = {
        "repetition_count": 0,
        "low_information_gain_count": 0,
        "no_goal_progress_count": 0,
        "goal_progress_observable": False,
        "goal_progress": False,
        "validation_evidence_advanced": False,
        "completion_advanced": False,
        "current_agent_step": 4,
        "operation_hash": "sha256:operation",
        "result_hash": "sha256:result",
    }
    state.update(overrides)
    return state


def _declare_long_horizon(context: Context, agent_id: str = "agent") -> None:
    transition = record_model_execution_profile(
        context,
        agent_id,
        {
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 3,
            "expected_tool_actions": 8,
            "verification_required": True,
        },
    )
    assert transition is not None


def test_guide_mode_delivers_each_replan_checkpoint_once() -> None:
    context = _context()
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        stagnation_event_threshold=1,
        repetition_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(repetition_count=1),
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_REPLAN
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "long-horizon checkpoint" in guidance
    assert "Continue it when warranted" in guidance
    assert "bounded next action" in guidance
    assert "inspectable milestone evidence" in guidance
    assert "keeps all normal Tools available" in guidance
    assert "checkpoint is advisory" in guidance
    assert consume_execution_protocol_guidance(context, "agent") is None
    state = ExecutionProtocolStore(context, "agent", policy).load()
    assert state.replan_count == 1
    assert state.attempt_epoch == 0

    applied = record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "short",
            "milestone": "bounded diagnosis",
            "next_action": "run a different public probe",
            "next_action_tool": "terminal__execute",
            "next_action_arguments": '{"command":"run public probe"}',
            "verification_plan": "compare the probe result with the request",
            "completion_assessment": "uncertain",
            "delivery_intent": "continue_exploration",
            "delivery_rationale": "a different public probe can add evidence",
            "assumptions": ["the probe is locally available"],
            "retired_approaches": [],
            "evidence_refs": ["tool:call-1"],
            "selected_candidate_id": None,
        },
    )
    assert applied is not None
    assert applied.state.attempt_epoch == 1
    assert applied.state.long_horizon_armed is False
    assert load_model_plan_update(context, "agent")["milestone"] == "bounded diagnosis"


def test_observe_mode_records_without_changing_the_prompt() -> None:
    context = _context("observe")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.OBSERVE,
        stagnation_event_threshold=1,
        repetition_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(repetition_count=1),
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.WOULD_REQUEST_REPLAN
    assert consume_execution_protocol_guidance(context, "agent") is None
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")


def test_replan_exhaustion_keeps_tools_available_for_model_judgment() -> None:
    context = _context("replan-finalize")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        stagnation_event_threshold=1,
        repetition_threshold=1,
        max_replans=0,
    )
    configure_execution_protocol(context, "agent", policy)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(repetition_count=1),
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.CONTINUE
    assert transition.state.phase is ProtocolPhase.EXECUTE
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")


def test_replan_prioritizes_missing_public_deliverable(tmp_path) -> None:
    context = _context("replan-public-delivery")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(tmp_path / "result.json"),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        stagnation_event_threshold=1,
        repetition_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)
    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(repetition_count=1),
    )
    assert transition.decision.action is ControllerAction.REQUEST_REPLAN

    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "result.json" in guidance
    assert "choose the next delivery intent" in guidance.lower()
    assert "continue_exploration" in guidance
    assert "does not force a command" in guidance


def test_pre_generation_candidate_decision_is_typed_and_one_shot(tmp_path) -> None:
    context = _context("candidate-reserve")
    output = tmp_path / "candidate.txt"
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "candidate.txt",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    configure_execution_protocol(context, "agent", policy)
    _declare_long_horizon(context)
    context.get_task().remaining_seconds = lambda: 150

    transition = record_pre_generation_delivery_decision(
        context, "agent", policy=policy
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_REPLAN
    assert transition.state.decision_checkpoint_candidate_present is False
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "produce_candidate" in guidance
    assert "validate_candidate" in guidance
    assert "submit_current" in guidance
    assert "submit_uncertain" in guidance
    assert "observed candidate state is absent" in guidance

    # The same reserve is not silently converted into repeated control turns.
    assert (
        record_pre_generation_delivery_decision(context, "agent", policy=policy) is None
    )


def test_short_profile_gets_candidate_decision_for_public_deliverable(tmp_path) -> None:
    context = _context("short-public-candidate-reserve")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(tmp_path / "result.json"),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    configure_execution_protocol(context, "agent", policy)
    record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "short",
            "confidence": 0.9,
            "milestone_count": 1,
            "expected_tool_actions": 2,
            "verification_required": True,
        },
    )
    context.get_task().remaining_seconds = lambda: 150

    transition = record_pre_generation_delivery_decision(
        context, "agent", policy=policy
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_REPLAN
    assert transition.decision.reason is DecisionReason.CANDIDATE_DECISION_RESERVE


def test_pre_generation_candidate_decision_fails_open_on_unavailable_provider() -> None:
    from aworld.runners.execution_protocol import (
        execution_protocol_model_decision_boundary,
        record_model_decision_unavailable,
    )

    context = _context("candidate-reserve-unavailable")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    configure_execution_protocol(context, "agent", policy)
    _declare_long_horizon(context)
    context.get_task().remaining_seconds = lambda: 150
    assert (
        record_pre_generation_delivery_decision(context, "agent", policy=policy)
        is not None
    )
    assert execution_protocol_model_decision_boundary(context, "agent") == "replan"

    assert (
        record_model_decision_unavailable(
            context,
            "agent",
            boundary="replan",
            reason="provider_unavailable",
        )
        is True
    )
    assert execution_protocol_model_decision_boundary(context, "agent") is None
    state = load_execution_protocol_state(context, "agent")
    assert state.phase is ProtocolPhase.EXECUTE
    assert state.decision_checkpoint_pending is False
    assert state.candidate_decision_count == 1
    assert state.candidate_decision_recorded is False
    assert (
        record_pre_generation_delivery_decision(context, "agent", policy=policy) is None
    )


def test_observe_replan_exhaustion_never_changes_tool_availability() -> None:
    context = _context("observe-finalize")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.OBSERVE,
        activation_event_threshold=1,
        stagnation_event_threshold=1,
        repetition_threshold=1,
        max_replans=0,
    )
    configure_execution_protocol(context, "agent", policy)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(repetition_count=1),
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.CONTINUE
    assert transition.state.phase is ProtocolPhase.EXECUTE
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")


def test_model_profile_arms_early_and_is_accepted_only_once() -> None:
    context = _context("model-profile")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=20,
    )
    configure_execution_protocol(context, "agent", policy)

    assert execution_protocol_accepts_model_profile(context, "agent") is True
    transition = record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 3,
            "expected_tool_actions": 9,
            "verification_required": True,
        },
    )

    assert transition is not None
    assert transition.state.long_horizon_armed is True
    assert execution_protocol_accepts_model_profile(context, "agent") is False
    assert record_model_execution_profile(context, "agent", {}) is None


def test_scoped_state_and_bounded_telemetry_are_public_read_only_views() -> None:
    context = _context("telemetry")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(completion_advanced=True, goal_progress=True),
    )

    state = load_execution_protocol_state(context, "agent")
    telemetry = build_execution_protocol_telemetry(context, "agent")

    assert state.scope.task_id == "telemetry"
    assert state.long_horizon_armed is False
    assert telemetry == {
        "schema_version": "aworld.execution-protocol-telemetry/v1",
        "mode": "guide",
        "phase": "execute",
        "armed": False,
        "legacy_activation_fields_ignored": True,
        "event_count": 1,
        "tool_observation_count": 1,
        "stagnant_observations": 0,
        "replan_count": 0,
        "replan_requested_count": 0,
        "replan_applied_count": 0,
        "decision_checkpoint_pending": False,
        "candidate_decision_recorded": False,
        "delivery_debt_observations": 0,
        "workspace_mutation_absent_observations": 0,
        "delivery_checkpoint_count": 0,
        "candidate_decision_count": 0,
        "action_alignment_match_count": 0,
        "action_alignment_mismatch_count": 0,
        "initial_decision_attempt_count": 0,
        "initial_decision_unavailable_count": 0,
        "replan_decision_attempt_count": 0,
        "replan_decision_unavailable_count": 0,
        "candidate_final_count": 0,
        "final_review_count": 0,
        "repair_count": 0,
        "finalization_entered": False,
    }


def test_delivery_intent_is_bounded_in_telemetry_and_transition_metrics() -> None:
    context = _context("delivery-intent-telemetry")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )

    transition = record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "inspect the candidate",
            "next_action": "run the exact public probe",
            "next_action_tool": "terminal__execute",
            "next_action_arguments": '{"command":"pytest -q"}',
            "verification_plan": "use the observed exit status",
            "completion_assessment": "in_progress",
            "delivery_intent": "validate_candidate",
            "delivery_rationale": "a candidate exists and needs a fresh check",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": ["tool:call-1"],
            "selected_candidate_id": "candidate-1",
        },
    )

    assert transition is not None
    assert (
        build_execution_protocol_telemetry(context, "agent")["last_delivery_intent"]
        == "validate_candidate"
    )
    assert (
        context.context_info["execution_protocol_metrics"]["last_delivery_intent"]
        == "validate_candidate"
    )


def test_invalid_model_plan_update_fails_open_without_acknowledging_checkpoint():
    context = _context("invalid-plan-update")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        repetition_threshold=1,
        stagnation_event_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)
    record_tool_protocol_event(context, "agent", _semantic_state(repetition_count=1))
    assert consume_execution_protocol_guidance(context, "agent") is not None

    assert record_model_plan_update(context, "agent", {"decision": "replan"}) is None
    state = load_execution_protocol_state(context, "agent")
    assert state.attempt_epoch == 0
    assert load_model_plan_update(context, "agent") == {}


def test_model_decision_boundary_rejects_a_forged_tool_call_signature():
    context = _context("forged-plan-signature")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    forged_signature = action_signature(
        "terminal__execute", {"command": "malicious-tool-call"}
    )

    acknowledged = record_model_decision_boundary(
        context,
        "agent",
        boundary="initial",
        execution_profile={
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 3,
            "expected_tool_actions": 8,
            "verification_required": True,
        },
        plan_update={
            "decision": "continue",
            "horizon": "long",
            "milestone": "claim a benign next action",
            "next_action": "run the declared probe",
            "next_action_tool": "terminal__execute",
            "next_action_signature": forged_signature,
            "verification_plan": "inspect the observed result",
            "completion_assessment": "in_progress",
            "delivery_intent": "validate_candidate",
            "delivery_rationale": "the candidate needs a public check",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "candidate-1",
        },
    )

    assert acknowledged is False
    assert execution_protocol_model_decision_boundary(context, "agent") == "initial"
    state = load_execution_protocol_state(context, "agent")
    assert state.model_execution_profile is None
    assert state.model_plan_update is None


def test_model_decision_boundary_rejects_tool_outside_decision_catalog():
    context = _context("out-of-catalog-plan-tool")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )

    acknowledged = record_model_decision_boundary(
        context,
        "agent",
        boundary="initial",
        execution_profile={
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 3,
            "expected_tool_actions": 8,
            "verification_required": True,
        },
        plan_update={
            "decision": "continue",
            "horizon": "long",
            "milestone": "attempt an undeclared action",
            "next_action": "invoke a Tool outside the offered catalog",
            "next_action_tool": "filesystem__delete",
            "next_action_arguments": '{"path":"result.json"}',
            "verification_plan": "inspect the result",
            "completion_assessment": "in_progress",
            "delivery_intent": "continue_exploration",
            "delivery_rationale": "the undeclared action might add evidence",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        },
        available_tool_names=frozenset({"terminal__execute"}),
    )

    assert acknowledged is False
    assert execution_protocol_model_decision_boundary(context, "agent") == "initial"
    state = load_execution_protocol_state(context, "agent")
    assert state.model_execution_profile is None
    assert state.model_plan_update is None


def test_initial_short_plan_can_submit_current_without_claiming_uncertainty():
    context = _context("short-submit-current")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )

    acknowledged = record_model_decision_boundary(
        context,
        "agent",
        boundary="initial",
        execution_profile={
            "horizon": "short",
            "confidence": 0.95,
            "milestone_count": 1,
            "expected_tool_actions": 0,
            "verification_required": False,
        },
        plan_update={
            "decision": "continue",
            "horizon": "short",
            "milestone": "answer is ready",
            "next_action": "submit the current result",
            "next_action_tool": None,
            "next_action_arguments": None,
            "verification_plan": "return the best current result",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "submit_current",
            "delivery_rationale": "the request is complete without a Tool call",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        },
        available_tool_names=frozenset({"terminal__execute"}),
    )

    assert acknowledged is True
    state = load_execution_protocol_state(context, "agent")
    assert state.phase is ProtocolPhase.FINALIZE
    assert state.finalization_entered is True
    assert state.long_horizon_armed is False
    assert state.acceptance_confirmed is False
    assert execution_protocol_requires_tool_free_finalization(context, "agent") is True
    assert (
        build_execution_protocol_telemetry(context, "agent")["last_delivery_intent"]
        == "submit_current"
    )


def test_evidence_fingerprint_change_alone_does_not_reset_stagnation() -> None:
    context = _context("evidence-change")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=20,
        stagnation_event_threshold=1,
        repetition_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            repetition_count=1,
            validation_evidence_advanced=True,
            completion_advanced=False,
            goal_progress=False,
        ),
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_REPLAN
    assert transition.state.stagnant_observations == 1


def test_invalid_model_profile_stays_unknown_and_can_be_reoffered() -> None:
    context = _context("invalid-model-profile")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    configure_execution_protocol(context, "agent", policy)

    assert (
        record_model_execution_profile(
            context,
            "agent",
            {"horizon": "long", "confidence": "certain"},
        )
        is None
    )
    assert execution_protocol_accepts_model_profile(context, "agent") is True
    state = ExecutionProtocolStore(context, "agent", policy).load()
    assert state.long_horizon_armed is False


def test_final_review_is_requested_once_and_unknown_submits_current_result() -> None:
    context = _context("final")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE, activation_event_threshold=1
    )
    configure_execution_protocol(context, "agent", policy)
    _declare_long_horizon(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(validation_evidence_advanced=True),
    )

    first = record_candidate_final(context, "agent")
    assert (
        final_review_guidance(first, independent_acceptance_enabled=False) is not None
    )
    assert first is not None
    assert first.decision.action is ControllerAction.REQUEST_FINAL_REVIEW

    second = record_candidate_final(context, "agent")
    assert second is not None
    assert second.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert second.state.phase is ProtocolPhase.COMPLETE
    assert second.state.final_review_count == 1


def test_short_task_candidate_final_bypasses_review() -> None:
    context = _context("short")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    configure_execution_protocol(context, "agent", policy)
    record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "short",
            "confidence": 0.9,
            "milestone_count": 1,
            "expected_tool_actions": 1,
            "verification_required": False,
        },
    )

    transition = record_candidate_final(context, "agent")

    assert transition is not None
    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.state.final_review_count == 0
    assert (
        final_review_guidance(transition, independent_acceptance_enabled=False) is None
    )


def test_missing_validation_contract_uses_model_owned_reflection(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context = _context("review-all")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
    )
    configure_execution_protocol(context, "agent", policy)

    transition = record_candidate_final(context, "agent")
    effective_policy = execution_protocol_policy(context, "agent")

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    guidance = final_review_guidance(
        transition,
        independent_acceptance_enabled=(
            effective_policy.independent_acceptance_enabled
        ),
    )
    assert effective_policy.independent_acceptance_enabled is False
    assert "model-owned completion reflection" in guidance
    assert "solver self-review" in guidance
    assert "No trusted independent validation contract is active" in guidance
    assert "framework probe receipt" not in guidance
    assert "accept requires" not in guidance
    assert model_owned_review_active(context, "agent") is True


def test_ordinary_review_tool_action_stays_in_review() -> None:
    context = _context("repair")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE, activation_event_threshold=1
    )
    configure_execution_protocol(context, "agent", policy)
    _declare_long_horizon(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(validation_evidence_advanced=True),
    )
    record_candidate_final(context, "agent")

    transition = record_review_tool_action(context, "agent")

    assert transition is None
    state = load_execution_protocol_state(context, "agent")
    assert state.phase is ProtocolPhase.REVIEW
    assert state.review_pending is True
    assert state.repair_count == 0
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")


def test_only_strict_structured_review_decision_enters_repair() -> None:
    context = _context("structured-repair")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        independent_acceptance_enabled=False,
    )
    configure_execution_protocol(context, "agent", policy)
    _declare_long_horizon(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(validation_evidence_advanced=True),
    )
    record_candidate_final(context, "agent")

    for invalid in (
        {"decision": "repair"},
        {"decision": "repair", "reason": ""},
        {"decision": "repair", "reason": "gap", "extra": True},
        {"decision": "accept", "reason": "looks good"},
        "repair",
    ):
        assert record_review_repair_decision(context, "agent", invalid) is None
        assert load_execution_protocol_state(context, "agent").review_pending is True

    transition = record_review_repair_decision(
        context,
        "agent",
        {"decision": "repair", "reason": "observed output is stale"},
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_REPAIR
    assert transition.state.phase is ProtocolPhase.REPAIR
    assert transition.state.review_pending is False
    assert transition.state.repair_count == 1
    assert model_owned_review_active(context, "agent") is False


def test_independent_review_rejects_noncritic_repair_marker(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context = _context("critic-marker")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="registered-check",
                    argv=("pytest", "-q", "tests/test_contract.py"),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
    )
    configure_execution_protocol(context, "agent", policy)
    review = record_candidate_final(context, "agent")

    assert (
        record_review_repair_decision(
            context,
            "agent",
            {"decision": "repair", "reason": "unverified gap"},
        )
        is None
    )
    guidance = final_review_guidance(
        review,
        independent_acceptance_enabled=True,
    )
    assert "independent acceptance" in guidance
    assert "successful framework probe receipt" in guidance
    assert "solver self-review" not in guidance
    state = load_execution_protocol_state(context, "agent")
    assert state.phase is ProtocolPhase.REVIEW
    assert state.review_pending is True
    assert state.repair_count == 0


def test_candidate_fallback_survives_context_transport_copy() -> None:
    context = _context("fallback")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    configure_execution_protocol(context, "agent", policy)
    store_candidate_fallback(
        context,
        "agent",
        [ActionModel(agent_name="agent", policy_info="best verified candidate")],
    )

    transported = context.deep_copy()
    fallback = load_candidate_fallback(transported, "agent")

    assert fallback is not None
    assert fallback[0].policy_info == "best verified candidate"


def test_candidate_fallback_does_not_cross_task_epoch() -> None:
    context = _context("epoch")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    configure_execution_protocol(context, "agent", policy)
    store_candidate_fallback(
        context,
        "agent",
        [ActionModel(agent_name="agent", policy_info="previous task candidate")],
    )

    context.advance_context_lifecycle(LifecycleAction.NEW_TASK)

    assert load_candidate_fallback(context, "agent") is None
