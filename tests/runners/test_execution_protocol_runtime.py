from __future__ import annotations

import json
import pytest

import aworld.runners.execution_protocol as execution_protocol_module

from aworld.core.context.base import Context
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.event.base import Message
from aworld.core.context.compiler import (
    ArtifactRequirement,
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
    ExecutionProtocolState,
    ExecutionProtocolStore,
    ProtocolMode,
    ProtocolPhase,
)
from aworld.core.task import Task
from aworld.runners.execution_protocol import (
    bind_pending_next_action_call,
    build_execution_protocol_telemetry,
    configure_execution_protocol,
    consume_execution_protocol_guidance,
    execution_protocol_accepts_model_profile,
    execution_protocol_model_decision_boundary,
    execution_protocol_policy,
    execution_protocol_requires_tool_free_finalization,
    final_review_guidance,
    framework_observable_validation_kind,
    load_candidate_fallback,
    load_execution_protocol_state,
    load_model_plan_update,
    load_public_probe_receipts,
    model_owned_review_active,
    mutation_gate_interception,
    project_execution_protocol_telemetry,
    record_candidate_final,
    record_acceptance_probe_plan,
    record_model_execution_profile,
    record_model_decision_attempt_failure,
    record_model_decision_boundary,
    record_model_plan_update,
    record_pre_generation_delivery_decision,
    record_public_probe_plan,
    record_review_repair_decision,
    record_review_tool_action,
    record_tool_protocol_event,
    store_candidate_fallback,
)
from aworld.runners.hook.agent_hooks import MutationGatePreToolHook
from aworld.runners.post_tool_progress import record_semantic_tool_progress
from aworld.sandbox.terminal_receipt import (
    TERMINAL_EXECUTION_RECEIPT_KEY,
    build_terminal_execution_receipt,
    plan_terminal_execution,
)
from aworld.sandbox.tool_observation import SandboxToolObservationRuntime


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


def _declare_mutation_required(context: Context, agent_id: str = "agent") -> None:
    transition = record_model_execution_profile(
        context,
        agent_id,
        {
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 3,
            "expected_tool_actions": 8,
            "verification_required": True,
            "workspace_mutation_required": True,
        },
    )
    assert transition is not None


def _activate_produce_convergence(context: Context, agent_id: str = "agent") -> None:
    """Reach the real typed boundary through two unapplied replans."""

    for sequence in (1, 2):
        transition = record_tool_protocol_event(
            context,
            agent_id,
            _semantic_state(
                repetition_count=1,
                current_agent_step=sequence,
                operation_hash=f"sha256:produce-operation-{sequence}",
                result_hash=f"sha256:produce-result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_attempt_failure(
            context, agent_id, boundary="replan"
        )
        assert not record_model_decision_attempt_failure(
            context, agent_id, boundary="replan"
        )
    state = load_execution_protocol_state(context, agent_id)
    assert state.convergence_constraint_active is True
    assert state.convergence_stage.value == "produce_candidate"


@pytest.mark.parametrize(
    ("task_timeout", "expected_review_timeout"),
    [(600, 60.0), (7_200, 180.0)],
)
def test_runtime_derives_finite_review_episode_timeout_from_task_budget(
    task_timeout,
    expected_review_timeout,
) -> None:
    context = Context(task_id=f"review-timeout-{task_timeout}")
    context.set_task(
        Task(
            id=f"review-timeout-{task_timeout}",
            input="complete the public request",
            timeout=task_timeout,
        )
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            independent_acceptance_enabled=False,
        ),
    )

    policy = execution_protocol_policy(context, "agent")
    assert policy.final_review_timeout_seconds == expected_review_timeout
    assert policy.max_final_reviews == 2
    assert policy.max_repairs == 1


def test_explicit_review_episode_timeout_is_preserved() -> None:
    context = _context("explicit-review-timeout")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            independent_acceptance_enabled=False,
            final_review_timeout_seconds=90,
        ),
    )

    assert execution_protocol_policy(
        context, "agent"
    ).final_review_timeout_seconds == 90


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
        "schema_version": "aworld.execution-protocol-telemetry/v2",
        "mode": "guide",
        "phase": "execute",
        "armed": False,
        "legacy_activation_fields_ignored": False,
        "event_count": 1,
        "tool_observation_count": 1,
        "stagnant_observations": 0,
        "replan_count": 0,
        "replan_requested_count": 0,
        "replan_applied_count": 0,
        "decision_checkpoint_pending": False,
        "candidate_decision_recorded": False,
        "candidate_epoch_advanced": False,
        "candidate_checkpoint_recorded": False,
        "public_deliverable_declared": False,
        "public_candidate_mutated": False,
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
        "consecutive_unapplied_replans": 0,
        "suppressed_replan_boundaries": 0,
        "candidate_final_count": 0,
        "final_review_count": 0,
        "repair_count": 0,
        "finalization_entered": False,
        "mutation_gate_active": False,
        "mutation_gate_validation_window_open": False,
        "mutation_gate_activation_count": 0,
        "mutation_gate_blocked_read_only_call_count": 0,
        "convergence_gate_blocked_call_count": 0,
        "consecutive_read_only_observations": 0,
        "post_candidate_read_only_observations": 0,
        "post_candidate_no_delivery_progress_observations": 0,
        "convergence_constraint_active": False,
        "convergence_constraint_activation_count": 0,
    }


def test_telemetry_v2_is_explicit_and_new_reader_still_accepts_v1() -> None:
    legacy = {
        "schema_version": "aworld.execution-protocol-telemetry/v1",
        "mode": "guide",
        "phase": "execute",
        "armed": False,
        "event_count": 1,
    }
    assert project_execution_protocol_telemetry(legacy) == legacy

    mislabeled = {
        **legacy,
        "convergence_constraint_active": True,
        "convergence_stage": "produce_candidate",
    }
    assert project_execution_protocol_telemetry(mislabeled) is None

    current = {
        **mislabeled,
        "schema_version": "aworld.execution-protocol-telemetry/v2",
    }
    assert project_execution_protocol_telemetry(current) == current


def test_short_estimate_arms_only_after_strict_double_action_overrun() -> None:
    context = _context("observed-long-horizon")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=3,
        model_activation_min_tool_actions=3,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
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

    for step in range(1, 5):
        record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(current_agent_step=step),
        )

    state = load_execution_protocol_state(context, "agent")
    assert state.long_horizon_armed is False

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(current_agent_step=5),
    )

    state = load_execution_protocol_state(context, "agent")
    assert state.long_horizon_armed is True
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["long_horizon_activation_source"] == "model_estimate_overrun"
    assert telemetry["model_expected_tool_actions"] == 2
    assert telemetry["model_expected_tool_actions_overrun_count"] == 3


def test_large_short_estimate_is_not_overridden_by_generic_threshold() -> None:
    context = _context("large-short-estimate")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=3,
        model_activation_min_tool_actions=3,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    configure_execution_protocol(context, "agent", policy)
    record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "short",
            "confidence": 0.9,
            "milestone_count": 1,
            "expected_tool_actions": 20,
            "verification_required": True,
        },
    )

    for step in range(1, 7):
        record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(current_agent_step=step),
        )

    state = load_execution_protocol_state(context, "agent")
    assert state.long_horizon_armed is False
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert "long_horizon_activation_source" not in telemetry
    assert telemetry["model_expected_tool_actions_overrun_count"] == 0


def test_unknown_horizon_uses_generic_observation_threshold_and_stays_armed() -> None:
    context = _context("unknown-observed-horizon")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=3,
        model_activation_min_tool_actions=3,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    configure_execution_protocol(context, "agent", policy)
    record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "unknown",
            "confidence": 0.2,
            "milestone_count": 1,
            "expected_tool_actions": 20,
            "verification_required": True,
        },
    )

    for step in range(1, 4):
        record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(current_agent_step=step),
        )

    state = load_execution_protocol_state(context, "agent")
    assert state.long_horizon_armed is True
    restored = ExecutionProtocolState.from_dict(state.to_dict())
    assert restored.long_horizon_armed is True
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert (
        telemetry["long_horizon_activation_source"]
        == "generic_observation_threshold"
    )


def test_two_unapplied_replans_activate_one_executable_convergence_phase() -> None:
    context = _context("bounded-replan-decisions")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        model_activation_min_tool_actions=1,
        repetition_threshold=1,
        stagnation_event_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)
    _declare_long_horizon(context)

    def acknowledge_without_replan(sequence: int) -> None:
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                repetition_count=1,
                current_agent_step=sequence,
                operation_hash=f"sha256:operation-{sequence}",
                result_hash=f"sha256:result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert execution_protocol_model_decision_boundary(context, "agent") == "replan"
        assert record_model_decision_boundary(
            context,
            "agent",
            boundary="replan",
            execution_profile=None,
            plan_update={
                "decision": "continue",
                "horizon": "long",
                "milestone": f"continue-{sequence}",
                "next_action": "run one bounded check",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": '{"command":"true"}',
                "verification_plan": "inspect the result",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "one more check may add evidence",
                "assumptions": [],
                "retired_approaches": [],
                "evidence_refs": [],
                "selected_candidate_id": None,
            },
            available_tool_names=frozenset({"terminal__execute"}),
        )

    acknowledge_without_replan(1)
    acknowledge_without_replan(2)
    third = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            repetition_count=1,
            current_agent_step=3,
            operation_hash="sha256:operation-3",
            result_hash="sha256:result-3",
        ),
    )

    assert third.decision.action is ControllerAction.CONTINUE
    assert third.decision.reason is DecisionReason.CONVERGENCE_CONSTRAINT_ACTIVE
    assert execution_protocol_model_decision_boundary(context, "agent") is None
    state = load_execution_protocol_state(context, "agent")
    assert state.decision_checkpoint_pending is False
    assert state.replan_requested_count == 2
    assert state.replan_applied_count == 0
    assert state.convergence_constraint_active is True
    assert state.convergence_stage.value == "produce_candidate"
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert guidance.startswith("AWorld convergence constraint:")
    assert "smallest honest inspectable candidate" in guidance
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["consecutive_unapplied_replans"] == 2
    assert telemetry["suppressed_replan_boundaries"] == 0
    assert telemetry["convergence_constraint_active"] is True
    assert telemetry["convergence_stage"] == "produce_candidate"
    assert telemetry["convergence_constraint_activation_count"] == 1

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=4,
            candidate_present=True,
            candidate_advanced=True,
            workspace_mutated=True,
        ),
    )
    state = load_execution_protocol_state(context, "agent")
    assert state.replan_requested_count == 2
    assert state.convergence_stage.value == "validate_repair_or_submit"
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "an inspectable candidate exists" in guidance


def test_two_unacknowledged_replan_boundaries_stop_request_counter_growth() -> None:
    context = _context("unacknowledged-replan-convergence")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            stagnation_event_threshold=1,
        ),
    )
    _declare_long_horizon(context)

    for sequence in (1, 2):
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                repetition_count=1,
                current_agent_step=sequence,
                operation_hash=f"sha256:unack-operation-{sequence}",
                result_hash=f"sha256:unack-result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )
        assert not record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )

    state = load_execution_protocol_state(context, "agent")
    assert state.replan_requested_count == 2
    assert state.replan_applied_count == 0
    assert state.convergence_constraint_active is True
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["replan_decision_status"] == "convergence_constraint"
    assert telemetry["consecutive_unapplied_replans"] == 2

    third = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            repetition_count=1,
            current_agent_step=3,
            operation_hash="sha256:unack-operation-3",
            result_hash="sha256:unack-result-3",
        ),
    )
    assert third.decision.action is ControllerAction.CONTINUE
    assert third.decision.reason is DecisionReason.CONVERGENCE_CONSTRAINT_ACTIVE
    assert load_execution_protocol_state(
        context, "agent"
    ).replan_requested_count == 2


def test_deadline_guidance_moves_from_candidate_to_delivery_only() -> None:
    context = _context("deadline-convergence")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        candidate_decision_reserve_seconds=0,
        delivery_debt_observation_threshold=99,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    configure_execution_protocol(context, "agent", policy)
    _declare_long_horizon(context)
    task = context.get_task()

    task.remaining_seconds = lambda: 350
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=1,
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
        ),
    )
    assert "40%" in consume_execution_protocol_guidance(context, "agent")

    task.remaining_seconds = lambda: 200
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(current_agent_step=2),
    )
    assert "65%" in consume_execution_protocol_guidance(context, "agent")

    task.remaining_seconds = lambda: 100
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(current_agent_step=3),
    )
    assert "80%" in consume_execution_protocol_guidance(context, "agent")
    assert consume_execution_protocol_guidance(context, "agent") is None


def test_read_only_heuristic_is_advisory_before_typed_convergence() -> None:
    context = _context("mutation-gate")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_mutation_required(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )

    assert consume_execution_protocol_guidance(context, "agent") is None
    assert build_execution_protocol_telemetry(context, "agent")[
        "mutation_gate_active"
    ] is False


def test_known_mutation_does_not_mint_validation_or_repair_authority(
) -> None:
    context = _context("mutation-validation-window")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_mutation_required(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )
    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat candidate.txt"},
        tool_call_id="call-validation",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [read]) is None

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=0,
            workspace_mutation_observed=False,
            workspace_mutated=False,
            known_mutation_executed=True,
            candidate_present=False,
        ),
    )
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["mutation_gate_active"] is False
    assert telemetry["mutation_gate_validation_window_open"] is False
    assert mutation_gate_interception(context, [read]) is None
    assert consume_execution_protocol_guidance(context, "agent") is None

    # If validation still cannot observe an actual mutation/candidate, the
    # already-armed pre-candidate gate closes again immediately.
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=1,
            read_only_observed=True,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )
    assert mutation_gate_interception(context, [read]) is None

    # Actual mutation evidence resolves the gate; it is distinct from the
    # successful mutating execution receipt used only for validation liveness.
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=0,
            workspace_mutation_observed=True,
            workspace_mutated=True,
            candidate_present=False,
        ),
    )
    assert mutation_gate_interception(context, [read]) is None


def test_mutation_gate_does_not_intercept_in_observe_mode() -> None:
    context = _context("mutation-gate-observe")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.OBSERVE,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_mutation_required(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=20,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )
    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat README.md"},
        tool_call_id="call-read",
        agent_name="agent",
    )

    assert mutation_gate_interception(context, [read]) is None
    assert consume_execution_protocol_guidance(context, "agent") is None


def test_named_deliverable_gate_ignores_unrelated_workspace_mutation(
    tmp_path,
) -> None:
    context = _context("mutation-gate-deliverable")
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
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            workspace_mutation_observed=True,
            candidate_present=False,
        ),
    )

    assert consume_execution_protocol_guidance(context, "agent") is None


def test_named_deliverable_gate_reopens_reads_after_candidate_changes(
    tmp_path,
) -> None:
    context = _context("mutation-gate-candidate")
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
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            public_candidate_mutated=False,
        ),
    )
    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat result.json"},
        tool_call_id="call-read",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [read]) is None

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=0,
            public_candidate_mutated=True,
            candidate_present=True,
        ),
    )

    assert mutation_gate_interception(context, [read]) is None
    assert build_execution_protocol_telemetry(context, "agent")[
        "mutation_gate_blocked_read_only_call_count"
    ] == 0


@pytest.mark.asyncio
async def test_post_candidate_read_only_loop_converges_to_validate_repair_or_submit(
    tmp_path,
) -> None:
    candidate = tmp_path / "result.json"
    candidate.write_text("{}")
    context = _context("post-candidate-convergence")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(candidate),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            post_candidate_read_only_threshold=3,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_long_horizon(context)

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=1,
            public_deliverable_declared=True,
            candidate_present=True,
            candidate_advanced=True,
            public_candidate_mutated=True,
            workspace_mutated=True,
        ),
    )

    for step in range(2, 5):
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=step,
                public_deliverable_declared=True,
                candidate_present=True,
                public_candidate_mutated=True,
                read_only_observed=True,
                workspace_mutated=False,
                validation_observed=False,
            ),
        )

    assert transition.decision.action is ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    assert transition.decision.reason is DecisionReason.POST_CANDIDATE_STAGNATION
    state = load_execution_protocol_state(context, "agent")
    assert state.post_candidate_read_only_observations == 3
    assert state.convergence_constraint_active is True
    assert state.convergence_stage.value == "validate_repair_or_submit"
    assert state.replan_requested_count == 0

    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat result.json"},
        tool_call_id="call-post-candidate-read",
        agent_name="agent",
    )
    receipt = mutation_gate_interception(context, [read])
    assert receipt is not None
    assert receipt["kind"] == "candidate_convergence_required"
    assert receipt["post_candidate_read_only_observations"] == 3
    assert receipt["blocked_read_only_call_count"] == 1

    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "validation against the public contract" in guidance
    assert "Do not return to broad" in guidance

    validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat result.json", "cwd": str(tmp_path)},
        tool_call_id="call-post-candidate-validation",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "validate the current result",
            "next_action": "read the declared result exactly once",
            "next_action_tool": "terminal__run_code",
            "next_action_arguments": json.dumps(validation.params),
            "verification_plan": "inspect the exact candidate bytes",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "validate_candidate",
            "delivery_rationale": "the current candidate needs bounded validation",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "result-json-v1",
        },
    ) is not None
    assert bind_pending_next_action_call(context, "agent", [validation]) is True

    # The exact typed validation crosses the real pre-Tool Hook while the
    # unrelated read remains blocked by the same still-active gate.
    hook = MutationGatePreToolHook()
    unrelated_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat README.md"},
        tool_call_id="call-post-candidate-broad-read",
        agent_name="agent",
    )
    assert await hook.exec(
        Message(category="tool_call", payload=[validation], sender="agent"),
        context,
    ) is None
    assert await hook.exec(
        Message(category="tool_call", payload=[unrelated_read], sender="agent"),
        context,
    ) is not None

    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[validation],
        observation=Observation(
            content="{}",
            action_result=[
                ActionResult(
                    tool_call_id=validation.tool_call_id,
                    content="{}",
                    success=True,
                    metadata={
                        "sandbox_observation": {
                            "effect": "read_only",
                            "workspace_mutated": False,
                            "workspace_generation": 1,
                        }
                    },
                )
            ],
        ),
    )
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["post_candidate_read_only_observations"] == 0
    assert telemetry["mutation_gate_blocked_read_only_call_count"] == 2
    # This synthetic Tool result predates Sandbox semantic receipts.  Exact
    # arguments remain compatibility telemetry and cannot manufacture a match.
    assert load_execution_protocol_state(context, "agent").last_action_alignment.value == (
        "unobservable"
    )


@pytest.mark.asyncio
async def test_post_candidate_gate_requires_evidence_before_contractless_repair(
) -> None:
    context = _context("contractless-post-candidate-convergence")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            post_candidate_read_only_threshold=2,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_long_horizon(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=1,
            candidate_present=True,
            candidate_advanced=True,
            workspace_mutated=True,
        ),
    )
    for step in range(2, 4):
        record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=step,
                candidate_present=True,
                read_only_observed=True,
            ),
        )

    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["convergence_constraint_active"] is True
    assert telemetry["mutation_gate_active"] is True

    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat candidate.txt"},
        tool_call_id="call-read",
        agent_name="agent",
    )
    repair = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "candidate.txt", "content": "repair"},
        tool_call_id="call-repair",
        agent_name="agent",
    )
    hook = MutationGatePreToolHook()
    assert await hook.exec(
        Message(category="tool_call", payload=[read], sender="agent"), context
    ) is not None
    assert await hook.exec(
        Message(category="tool_call", payload=[repair], sender="agent"), context
    ) is not None

    # A successful mutation claim cannot mint its own repair authority.
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=4,
            candidate_present=True,
            known_mutation_executed=True,
            workspace_mutated=False,
            workspace_mutation_observed=False,
        ),
    )
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["mutation_gate_active"] is True
    assert telemetry["mutation_gate_validation_window_open"] is False
    assert await hook.exec(
        Message(category="tool_call", payload=[read], sender="agent"), context
    ) is not None

def test_preexisting_named_file_does_not_arm_post_candidate_convergence(
    tmp_path,
) -> None:
    candidate = tmp_path / "existing.txt"
    candidate.write_text("pre-task baseline")
    context = _context("preexisting-candidate")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(candidate),
                "display_path": "existing.txt",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            post_candidate_read_only_threshold=2,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )

    for step in range(1, 4):
        record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=step,
                public_deliverable_declared=True,
                candidate_present=True,
                candidate_advanced=False,
                public_candidate_mutated=False,
                read_only_observed=True,
            ),
        )

    state = load_execution_protocol_state(context, "agent")
    assert state.candidate_present is True
    assert state.candidate_epoch_advanced is False
    assert state.candidate_checkpoint_recorded is False
    assert state.post_candidate_read_only_observations == 0
    assert state.convergence_constraint_active is False
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["mutation_gate_active"] is False


@pytest.mark.asyncio
async def test_pre_tool_hook_is_fail_open_before_typed_convergence() -> None:
    context = _context("mutation-gate-hook")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_mutation_required(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )
    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat README.md"},
        tool_call_id="call-read",
        agent_name="agent",
    )
    write = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "result.txt", "content": "candidate"},
        tool_call_id="call-write",
        agent_name="agent",
    )
    hook = MutationGatePreToolHook()

    intercepted = await hook.exec(
        Message(category="tool_call", payload=[read], sender="agent"),
        context,
    )
    allowed = await hook.exec(
        Message(category="tool_call", payload=[write], sender="agent"),
        context,
    )

    assert intercepted is None
    assert allowed is None
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["mutation_gate_active"] is False
    assert telemetry["mutation_gate_activation_count"] == 0
    assert telemetry["mutation_gate_blocked_read_only_call_count"] == 0
    assert telemetry["consecutive_read_only_observations"] == 8


@pytest.mark.asyncio
async def test_produce_convergence_admits_only_exact_declared_target(tmp_path) -> None:
    target = tmp_path / "result.json"
    context = _context("produce-semantic-admission")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(target),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            stagnation_event_threshold=1,
        ),
    )
    _declare_long_horizon(context)
    _activate_produce_convergence(context)

    deliverable = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf '{{}}' > {target}"},
        tool_call_id="declared-write",
        agent_name="agent",
    )
    helper = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf helper > {tmp_path / 'helper.py'}"},
        tool_call_id="helper-write",
        agent_name="agent",
    )
    deliverable_and_helper = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={
            "code": (
                f"printf '{{}}' > {target}; "
                f"printf helper > {tmp_path / 'helper.py'}"
            )
        },
        tool_call_id="mixed-target-write",
        agent_name="agent",
    )
    unknown = ActionModel(
        tool_name="custom",
        action_name="opaque",
        params={"value": "x"},
        tool_call_id="unknown-call",
        agent_name="agent",
    )

    assert mutation_gate_interception(context, [deliverable]) is None
    receipt = mutation_gate_interception(
        context,
        [deliverable, helper, deliverable_and_helper, unknown],
    )
    assert receipt is not None
    assert receipt["tool_call_ids"] == [
        "helper-write",
        "mixed-target-write",
        "unknown-call",
    ]
    assert receipt["convergence_stage"] == "produce_candidate"

    anonymous = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf '{{}}' > {target}"},
        agent_name="agent",
    )
    anonymous_receipt = mutation_gate_interception(context, [anonymous])
    assert anonymous_receipt is not None
    assert anonymous_receipt["block_all"] is True
    assert anonymous_receipt["tool_call_ids"] == []


def test_pre_convergence_unknown_action_remains_fail_open() -> None:
    context = _context("pre-convergence-fail-open")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    unknown = ActionModel(
        tool_name="custom",
        action_name="opaque",
        params={"value": "x"},
        tool_call_id="unknown-call",
        agent_name="agent",
    )

    assert mutation_gate_interception(context, [unknown]) is None


@pytest.mark.parametrize(
    "stale_field",
    ("task_id", "task_epoch", "agent_id"),
)
def test_legacy_gate_migrates_only_for_exact_current_scope(stale_field) -> None:
    context = _context("legacy-gate-scope")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat README.md"},
        tool_call_id="legacy-read",
        agent_name="agent",
    )
    current_gate = {
        "schema_version": "aworld.mutation-gate/v2",
        "task_id": context.task_id,
        "task_epoch": context.task_epoch,
        "agent_id": "agent",
        "active": True,
        "convergence_stage": "produce_candidate",
        "blocked_read_only_call_count": 0,
    }
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", current_gate
    )
    context.context_info["execution_protocol_mutation_gate:agent"] = current_gate
    assert mutation_gate_interception(context, [action]) is not None

    stale_gate = dict(current_gate)
    stale_gate[stale_field] = (
        "other" if stale_field != "task_epoch" else context.task_epoch + 1
    )
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", stale_gate
    )
    context.context_info["execution_protocol_mutation_gate:agent"] = stale_gate
    assert mutation_gate_interception(context, [action]) is None


def test_stale_v3_gate_cannot_poison_new_scope_projection() -> None:
    context = _context("fresh-gate-projection")
    configure_execution_protocol(
        context, "agent", ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    )
    stale = {
        "schema_version": "aworld.mutation-gate/v3",
        "scope_hash": "sha256:" + "a" * 64,
        "agent_id": "agent",
        "active": True,
        "activation_count": 99,
        "blocked_call_count": 99,
        "blocked_read_only_call_count": 99,
        "repair_failure_evidence_high_water": "f" * 128,
        "convergence_stage": "validate_repair_or_submit",
    }
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", stale
    )

    record_tool_protocol_event(context, "agent", _semantic_state())

    projected = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert projected["active"] is False
    assert projected["activation_count"] == 0
    assert projected["blocked_call_count"] == 0
    assert projected["repair_failure_evidence_high_water"] == "0" * 128


@pytest.mark.parametrize("schema", ("aworld.mutation-gate/v2", "aworld.mutation-gate/v3"))
def test_constraint_activation_ignores_stale_gate_evidence(schema) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context(f"stale-activation-{schema[-2:]}")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            stagnation_event_threshold=1,
        ),
    )
    _declare_long_horizon(context)

    for sequence in (1, 2):
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                repetition_count=1,
                current_agent_step=sequence,
                operation_hash=f"sha256:stale-operation-{sequence}",
                result_hash=f"sha256:stale-result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )
        if sequence == 2:
            stale = {
                "schema_version": schema,
                "task_id": "other-task",
                "task_epoch": context.task_epoch + 1,
                "scope_hash": semantic_fingerprint({"stale": True}),
                "agent_id": "agent",
                "active": True,
                "public_candidate_mutated": True,
                "workspace_mutation_observed": True,
                "consecutive_read_only_observations": 99,
                "blocked_call_count": 99,
                "convergence_stage": "validate_repair_or_submit",
            }
            context.write_task_runtime_state(
                "agent", "execution_protocol_mutation_gate", stale
            )
        assert not record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )

    projected = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert projected["active"] is True
    assert projected["convergence_stage"] == "produce_candidate"
    assert projected["public_candidate_mutated"] is False
    assert projected["workspace_mutation_observed"] is False
    assert projected["consecutive_read_only_observations"] == 0
    assert projected["blocked_call_count"] == 0


def test_contractless_produce_requires_exact_model_bound_semantics_and_call_id() -> None:
    context = _context("contractless-produce-admission")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            stagnation_event_threshold=1,
        ),
    )
    _declare_long_horizon(context)
    _activate_produce_convergence(context)
    candidate = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "candidate.txt", "content": "candidate"},
        tool_call_id="model-bound-candidate",
        agent_name="agent",
    )
    unbound = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "helper.txt", "content": "helper"},
        tool_call_id="unbound-helper",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "produce the contractless candidate",
            "next_action": "write the exact candidate",
            "next_action_tool": "filesystem__write_file",
            "next_action_arguments": json.dumps(candidate.params),
            "verification_plan": "inspect the resulting state",
            "completion_assessment": "in_progress",
            "delivery_intent": "produce_candidate",
            "delivery_rationale": "the task has no trusted named artifact",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        },
    ) is not None
    assert bind_pending_next_action_call(context, "agent", [candidate]) is True

    assert mutation_gate_interception(context, [candidate]) is None
    blocked = mutation_gate_interception(context, [unbound])
    assert blocked is not None
    assert blocked["tool_call_ids"] == ["unbound-helper"]


def test_contractless_empty_targets_fall_back_to_exact_bound_signature() -> None:
    context = _context("contractless-empty-target-admission")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            stagnation_event_threshold=1,
        ),
    )
    _declare_long_horizon(context)
    _activate_produce_convergence(context)
    planned = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "touch $TARGET"},
        tool_call_id="opaque-target",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "produce an environment-bound candidate",
            "next_action": "touch the exact bound target",
            "next_action_tool": "terminal__run_code",
            "next_action_arguments": json.dumps(planned.params),
            "verification_plan": "inspect the resulting service state",
            "completion_assessment": "in_progress",
            "delivery_intent": "produce_candidate",
            "delivery_rationale": "the target is supplied by the trusted environment",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        },
    ) is not None
    assert bind_pending_next_action_call(context, "agent", [planned]) is True
    substituted = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "touch $OTHER"},
        tool_call_id="opaque-target",
        agent_name="agent",
    )

    blocked = mutation_gate_interception(context, [substituted])
    assert blocked is not None
    assert blocked["tool_call_ids"] == ["opaque-target"]
    assert mutation_gate_interception(context, [planned]) is None


def test_registered_validation_binds_canonical_invocation_cwd(tmp_path) -> None:
    context = _context("validation-cwd-binding")
    context.workspace_path = str(tmp_path)
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="cwd-check",
                    argv=("sh", "-c", "cat result.json"),
                    cwd="checks",
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context, "agent", ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    )
    correct = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat result.json", "cwd": str(tmp_path / "checks")},
        tool_call_id="cwd-correct",
        agent_name="agent",
    )
    wrong = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat result.json", "cwd": str(tmp_path / "other")},
        tool_call_id="cwd-wrong",
        agent_name="agent",
    )

    assert framework_observable_validation_kind(
        context, "agent", correct
    ) == "registered_completion_validation"
    assert framework_observable_validation_kind(context, "agent", wrong) is None


@pytest.mark.parametrize("ambiguous_kind", ("mixed", "missing"))
def test_active_convergence_blocks_ambiguous_agent_batches(ambiguous_kind) -> None:
    context = _context(f"ambiguous-agent-{ambiguous_kind}")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            stagnation_event_threshold=1,
        ),
    )
    _declare_long_horizon(context)
    _activate_produce_convergence(context)
    first = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat README.md"},
        tool_call_id="ambiguous-1",
        agent_name="agent" if ambiguous_kind == "mixed" else None,
    )
    second = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat pyproject.toml"},
        tool_call_id="ambiguous-2",
        agent_name="other" if ambiguous_kind == "mixed" else None,
    )

    receipt = mutation_gate_interception(context, [first, second])
    assert receipt is not None
    assert receipt["kind"] == "convergence_scope_ambiguous"
    assert receipt["block_all"] is True
    assert receipt["tool_call_ids"] == ["ambiguous-1", "ambiguous-2"]


def test_missing_agent_uses_all_active_gates_not_latest_inactive_helper() -> None:
    context = _context("missing-agent-active-index")
    configure_execution_protocol(
        context,
        "solver",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            stagnation_event_threshold=1,
        ),
    )
    _declare_long_horizon(context, "solver")
    _activate_produce_convergence(context, "solver")

    configure_execution_protocol(
        context, "helper", ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    )
    record_tool_protocol_event(
        context,
        "helper",
        _semantic_state(current_agent_step=1),
    )
    assert context.context_info["execution_protocol_mutation_gate"][
        "agent_id"
    ] == "helper"
    actions = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": "cat README.md"},
            tool_call_id="missing-agent",
            agent_name=None,
        )
    ]

    receipt = mutation_gate_interception(context, actions)
    assert receipt is not None
    assert receipt["agent_id"] == "solver"
    assert receipt["block_all"] is True


def test_active_gate_index_overflow_remains_fail_closed_after_retained_deactivation(
) -> None:
    context = _context("active-gate-index-overflow")
    agent_ids = [f"agent-{index:02d}" for index in range(33)]
    for agent_id in agent_ids:
        execution_protocol_module._update_active_gate_index(
            context, agent_id, active=True
        )
    for agent_id in agent_ids[:32]:
        execution_protocol_module._update_active_gate_index(
            context, agent_id, active=False
        )

    index = execution_protocol_module._active_gate_index(context)
    assert index["active_agent_ids"] == []
    assert index["overflow_active"] is True
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat README.md"},
        tool_call_id="overflow-missing-agent",
        agent_name=None,
    )

    receipt = mutation_gate_interception(context, [action])
    assert receipt is not None
    assert receipt["block_all"] is True
    assert receipt["reason"] == "active_gate_index_overflow"


@pytest.mark.asyncio
async def test_validate_convergence_requires_typed_validation_or_one_bound_repair(
    tmp_path,
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    target = tmp_path / "result.json"
    target.write_text("{}")
    context = _context("validate-semantic-admission")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(target),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    validation_code = f"cat {target}"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="validate-result",
                    argv=("sh", "-c", validation_code),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            post_candidate_read_only_threshold=1,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_long_horizon(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=1,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=semantic_fingerprint("candidate-a"),
        ),
    )
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=2,
            candidate_present=True,
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=semantic_fingerprint("candidate-a"),
        ),
    )
    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_stage.value == "validate_repair_or_submit"

    validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="registered-validation",
        agent_name="agent",
    )
    helper = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf helper > {tmp_path / 'helper.py'}"},
        tool_call_id="helper-write",
        agent_name="agent",
    )
    mixed = mutation_gate_interception(context, [validation, helper])
    assert mixed is not None
    assert mixed["tool_call_ids"] == ["helper-write"]

    first_validation = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[validation],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id=validation.tool_call_id,
                    content="{}",
                    success=True,
                )
            ]
        ),
    )
    repeated_validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="registered-validation-repeat",
        agent_name="agent",
    )
    repeated_state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[repeated_validation],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id=repeated_validation.tool_call_id,
                    content="{}",
                    success=True,
                )
            ]
        ),
    )
    assert first_validation["delivery_progress_advanced"] is True
    assert repeated_state["delivery_progress_advanced"] is False

    failed_states = []
    for index, error in enumerate(("missing-result", "still-missing"), start=1):
        failed_action = ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": validation_code},
            tool_call_id=f"registered-validation-failed-{index}",
            agent_name="agent",
        )
        failed_states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[failed_action],
                observation=Observation(
                    action_result=[
                        ActionResult(
                            tool_call_id=failed_action.tool_call_id,
                            content=error,
                            success=False,
                            error=error,
                        )
                    ]
                ),
            )
        )
    assert all(state["validation_observed"] is True for state in failed_states)
    assert all(
        state["validation_evidence_advanced"] is False for state in failed_states
    )
    assert all(
        state["delivery_progress_advanced"] is False for state in failed_states
    )

    failed_validation = {
        "schema_version": "aworld.action-semantic-receipt/v1",
        "capability_aliases": ["workspace.validate"],
        "effect": "validation",
        "target_ids": [],
        "executed": True,
        "succeeded": False,
        "timed_out": False,
        "validation_kind": "registered:test",
        "declared_deliverable_targeted": False,
        "tool_call_id": "failed-validation",
    }
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=3,
            candidate_present=True,
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=semantic_fingerprint("candidate-a"),
            failure_signature=semantic_fingerprint("failed-validation"),
            observed_action_semantics=(failed_validation,),
        ),
    )
    repair = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf repaired > {target}"},
        tool_call_id="repair-once",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [repair]) is None
    # Replaying the identical failed validation receipt cannot replenish the
    # already-consumed one-use repair authorization.
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=4,
            candidate_present=True,
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=semantic_fingerprint("candidate-a"),
            failure_signature=semantic_fingerprint("failed-validation"),
            observed_action_semantics=(failed_validation,),
        ),
    )
    blocked_second = mutation_gate_interception(context, [repair])
    assert blocked_second is not None
    assert blocked_second["tool_call_ids"] == ["repair-once"]


def test_repair_authorization_becomes_stale_after_candidate_change(tmp_path) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    target = tmp_path / "result.json"
    target.write_text("{}")
    context = _context("stale-repair-authorization")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(target),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            post_candidate_read_only_threshold=1,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_long_horizon(context)
    first_hash = semantic_fingerprint("candidate-a")
    for step, progress in ((1, True), (2, False)):
        record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=step,
                candidate_present=True,
                candidate_advanced=progress,
                delivery_progress_advanced=progress,
                public_deliverable_declared=True,
                public_candidate_mutated=True,
                public_delivery_fingerprint=first_hash,
            ),
        )
    failed_validation = {
        "schema_version": "aworld.action-semantic-receipt/v1",
        "capability_aliases": ["workspace.validate"],
        "effect": "validation",
        "target_ids": [],
        "executed": True,
        "succeeded": False,
        "timed_out": False,
        "validation_kind": "registered:test",
        "declared_deliverable_targeted": False,
        "tool_call_id": "failed-validation",
    }
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=3,
            candidate_present=True,
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=first_hash,
            failure_signature=semantic_fingerprint("failure"),
            observed_action_semantics=(failed_validation,),
        ),
    )
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=4,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=semantic_fingerprint("candidate-b"),
        ),
    )
    repair = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf repaired > {target}"},
        tool_call_id="stale-repair",
        agent_name="agent",
    )
    receipt = mutation_gate_interception(context, [repair])
    assert receipt is not None
    assert receipt["tool_call_ids"] == ["stale-repair"]


@pytest.mark.asyncio
async def test_pre_convergence_registered_validation_and_reads_remain_fail_open(
    tmp_path,
) -> None:
    context = _context("mutation-gate-registered-validation")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="read-result",
                    argv=("cat", str(tmp_path / "result.json")),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            independent_acceptance_enabled=False,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_mutation_required(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )
    validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"cat {tmp_path / 'result.json'}"},
        tool_call_id="registered-validation",
        agent_name="agent",
    )
    broad = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat README.md"},
        tool_call_id="broad-read",
        agent_name="agent",
    )
    spoofed_validation = ActionModel(
        tool_name="filesystem",
        action_name="read_file",
        params={
            "path": "README.md",
            "code": f"cat {tmp_path / 'result.json'}",
        },
        tool_call_id="spoofed-validation",
        agent_name="agent",
    )
    hook = MutationGatePreToolHook()

    assert await hook.exec(
        Message(category="tool_call", payload=[validation], sender="agent"), context
    ) is None
    intercepted = await hook.exec(
        Message(
            category="tool_call",
            payload=[validation, broad],
            sender="agent",
        ),
        context,
    )

    assert intercepted is None
    spoofed = await hook.exec(
        Message(
            category="tool_call",
            payload=[spoofed_validation],
            sender="agent",
        ),
        context,
    )
    assert spoofed is None


@pytest.mark.asyncio
async def test_mutation_gate_public_probe_executes_through_hook_and_records_receipt(
) -> None:
    context = _context("mutation-gate-public-probe")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            independent_acceptance_enabled=False,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_mutation_required(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )
    validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat result.json"},
        tool_call_id="public-validation",
        agent_name="agent",
    )
    assert record_public_probe_plan(
        context,
        "agent",
        tool_call_id=validation.tool_call_id,
        tool_identity="terminal:run_code",
        arguments_projection=validation.params,
        value={
            "hypothesis_id": "result-readable",
            "highest_risk_counterexample": "the result is missing or unreadable",
            "probe_kind": "smoke",
        },
    )
    hook = MutationGatePreToolHook()
    assert await hook.exec(
        Message(category="tool_call", payload=[validation], sender="agent"), context
    ) is None

    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[validation],
        observation=Observation(
            content="{}",
            action_result=[
                ActionResult(
                    tool_call_id=validation.tool_call_id,
                    content="{}",
                    success=True,
                    metadata={
                        "sandbox_observation": {
                            "effect": "read_only",
                            "workspace_mutated": False,
                            "workspace_generation": 0,
                        }
                    },
                )
            ],
        ),
    )

    receipts = load_public_probe_receipts(context, "agent")
    assert len(receipts) == 1
    assert receipts[0]["hypothesis_id"] == "result-readable"
    assert receipts[0]["tool_execution_succeeded"] is True


@pytest.mark.asyncio
async def test_mutation_gate_allows_exact_pending_acceptance_probe(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "true")
    context = _context("mutation-gate-acceptance-probe")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(command_id="read-result", argv=("cat", "result.json")),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            review_unarmed_candidates=True,
            independent_acceptance_enabled=True,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_mutation_required(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            consecutive_read_only_observations=8,
            workspace_mutation_observed=False,
            candidate_present=False,
        ),
    )
    review = record_candidate_final(
        context,
        "agent",
        actions=[ActionModel(agent_name="agent", policy_info="candidate")],
    )
    assert review is not None
    assert review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat result.json"},
        tool_call_id="acceptance-validation",
        agent_name="agent",
    )
    assert record_acceptance_probe_plan(
        context,
        "agent",
        tool_call_id=validation.tool_call_id,
        hypothesis_id="result-readable",
        highest_risk_counterexample="the result is unreadable",
        tool_identity="terminal:run_code",
        arguments_projection=validation.params,
        probe_kind="independent_cross_check",
    )

    assert framework_observable_validation_kind(
        context, "agent", validation
    ) == "pending_acceptance_critic_probe"
    assert await MutationGatePreToolHook().exec(
        Message(category="tool_call", payload=[validation], sender="agent"), context
    ) is None


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


def test_runtime_aligns_alternate_terminal_commands_by_authoritative_semantics() -> None:
    context = _context("semantic-action-alignment")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(ArtifactRequirement("result", "/app/result.txt"),),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    assert (
        record_model_plan_update(
            context,
            "agent",
            {
                "decision": "continue",
                "horizon": "long",
                "milestone": "inspect the declared result",
                "next_action": "read the result with Python",
                "next_action_tool": "terminal__run_code",
                "next_action_arguments": (
                    '{"code":"from pathlib import Path; '
                    "Path('/app/result.txt').read_text()\","
                    '"language":"python"}'
                ),
                "verification_plan": "inspect the resulting artifact",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "one declared-artifact fact remains unknown",
                "assumptions": [],
                "retired_approaches": [],
                "evidence_refs": [],
                "selected_candidate_id": None,
            },
        )
        is not None
    )

    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat /app/result.txt"},
        tool_call_id="call-semantic-alignment",
        agent_name="agent",
    )
    terminal_receipt = build_terminal_execution_receipt(
        code=action.params["code"],
        plan=plan_terminal_execution(action.params["code"]),
        executed=True,
        exit_code=0,
        timed_out=False,
        mutation_observed=False,
    )
    result = SandboxToolObservationRuntime().record(
        action,
        ActionResult(
            tool_call_id=action.tool_call_id,
            content="done",
            success=True,
            parameter=action.params,
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: terminal_receipt},
        ),
        context=context,
    )

    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(content="done", action_result=[result]),
    )

    state = load_execution_protocol_state(context, "agent")
    assert state.last_action_alignment.value == "matched"
    assert state.action_alignment_match_count == 1
    persisted = state.to_dict()
    assert "/app/result.txt" not in str(persisted)


def test_runtime_treats_malformed_semantic_receipt_as_unobservable() -> None:
    context = _context("malformed-semantic-action")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    assert (
        record_model_plan_update(
            context,
            "agent",
            {
                "decision": "continue",
                "horizon": "long",
                "milestone": "inspect one file",
                "next_action": "read the file",
                "next_action_tool": "terminal__run_code",
                "next_action_arguments": '{"code":"cat input.txt"}',
                "verification_plan": "use the bounded observation",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "one fact remains unknown",
                "assumptions": [],
                "retired_approaches": [],
                "evidence_refs": [],
                "selected_candidate_id": None,
            },
        )
        is not None
    )

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            observed_action_semantics=(
                {
                    "schema_version": "aworld.action-semantic-receipt/v1",
                    "capability_aliases": ["workspace.execute"],
                    "effect": "read_only",
                    "target_ids": ["/raw/path/must-not-persist"],
                },
            ),
        ),
    )

    state = load_execution_protocol_state(context, "agent")
    assert state.last_action_alignment.value == "unobservable"
    assert "/raw/path/must-not-persist" not in str(state.to_dict())


def test_friendly_run_code_mapping_binds_real_dispatched_call() -> None:
    context = _context("friendly-run-code-alignment")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    assert record_model_decision_boundary(
        context,
        "agent",
        boundary="initial",
        execution_profile={
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 2,
            "expected_tool_actions": 4,
            "verification_required": True,
        },
        plan_update={
            "decision": "continue",
            "horizon": "long",
            "milestone": "inspect the input",
            "next_action": "read the input once",
            "next_action_tool": "run_code",
            "next_action_arguments": '{"code":"cat /app/input.txt"}',
            "verification_plan": "use the observed bytes",
            "completion_assessment": "in_progress",
            "delivery_intent": "continue_exploration",
            "delivery_rationale": "the input format is unknown",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        },
        available_tool_names=frozenset({"run_code"}),
        available_tool_aliases={"run_code": "docker__run_code"},
        decision_tool_call_id="call-control",
    )
    update = load_execution_protocol_state(context, "agent").model_plan_update
    assert update is not None
    assert update.decision_call_id == "call-control"
    assert "workspace.execute" in update.next_action_semantics.capability_aliases

    action = ActionModel(
        tool_name="mcp",
        action_name="docker__run_code",
        model_visible_tool_name="run_code",
        params={"code": "cat /app/input.txt"},
        tool_call_id="call-intended",
        agent_name="agent",
    )
    assert bind_pending_next_action_call(context, "agent", [action]) is True
    receipt = build_terminal_execution_receipt(
        code=action.params["code"],
        plan=plan_terminal_execution(action.params["code"]),
        executed=True,
        exit_code=0,
        timed_out=False,
        mutation_observed=False,
    )
    result = SandboxToolObservationRuntime().record(
        action,
        ActionResult(
            tool_call_id=action.tool_call_id,
            content="input",
            success=True,
            parameter=action.params,
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )
    record_semantic_tool_progress(
        context,
        tool_name="mcp",
        agent_id="agent",
        actions=[action],
        observation=Observation(content="input", action_result=[result]),
    )

    state = load_execution_protocol_state(context, "agent")
    assert state.last_action_alignment.value == "matched"
    assert state.pending_next_action_call_id is None


def test_typed_validation_preflight_uses_semantics_before_exact_signature() -> None:
    context = _context("semantic-validation-preflight")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(
                ArtifactRequirement("result", "/app/result.txt"),
            ),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "validate the result",
            "next_action": "read the first result line",
            "next_action_tool": "terminal__run_code",
            "next_action_arguments": '{"code":"cat /app/result.txt"}',
            "verification_plan": "inspect the declared artifact",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "validate_candidate",
            "delivery_rationale": "the candidate needs bounded validation",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "result-v1",
        },
    ) is not None
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        model_visible_tool_name="terminal__run_code",
        params={"code": "head -n 1 /app/result.txt"},
        tool_call_id="call-semantic-validation",
        agent_name="agent",
    )
    assert bind_pending_next_action_call(context, "agent", [action]) is True

    state = load_execution_protocol_state(context, "agent")
    assert state.model_plan_update.next_action_signature != action_signature(
        "terminal__run_code", action.params
    )
    assert (
        framework_observable_validation_kind(context, "agent", action)
        == "typed_validation_plan"
    )


def test_unmodeled_nested_cd_fails_semantic_validation_preflight() -> None:
    context = _context("nested-cd-validation-preflight")
    context.workspace_path = "/app"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(
                ArtifactRequirement("result", "/app/sub/nested/result.txt"),
            ),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    command = "cd sub && ! cd nested; cat result.txt"
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "validate the nested result",
            "next_action": "read the nested result",
            "next_action_tool": "terminal__run_code",
            "next_action_arguments": json.dumps({"code": command, "cwd": "/app"}),
            "verification_plan": "inspect the declared nested artifact",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "validate_candidate",
            "delivery_rationale": "the nested candidate needs validation",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "nested-result-v1",
        },
    ) is not None
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        model_visible_tool_name="terminal__run_code",
        params={"code": command, "cwd": "/app"},
        tool_call_id="call-nested-cd-validation",
        agent_name="agent",
    )
    assert bind_pending_next_action_call(context, "agent", [action]) is True

    state = load_execution_protocol_state(context, "agent")
    assert state.model_plan_update.next_action_semantics.effect == "unknown"
    assert framework_observable_validation_kind(context, "agent", action) is None


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


def test_final_review_is_requested_once_and_completed_review_submits() -> None:
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


def test_runtime_stops_review_loop_when_candidate_and_evidence_are_unchanged() -> None:
    context = _context("unchanged-review-basis")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            review_unarmed_candidates=True,
            independent_acceptance_enabled=False,
        ),
    )
    candidate = ActionModel(agent_name="agent", policy_info="same candidate")
    first_review = record_candidate_final(
        context, "agent", actions=[candidate]
    )
    assert first_review is not None
    repair = record_review_repair_decision(
        context,
        "agent",
        {"decision": "repair", "reason": "recheck the material gap"},
    )
    assert repair is not None

    unchanged = record_candidate_final(context, "agent", actions=[candidate])

    assert unchanged is not None
    assert unchanged.decision.action is ControllerAction.STOP_INCOMPLETE
    assert unchanged.decision.reason is DecisionReason.REVIEW_BASIS_UNCHANGED
    assert unchanged.state.final_review_count == 1
    assert unchanged.state.phase is ProtocolPhase.COMPLETE


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
