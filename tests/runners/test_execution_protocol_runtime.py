from __future__ import annotations

from aworld.core.context.base import Context
from aworld.core.common import ActionModel
from aworld.core.context.compiler import LifecycleAction
from aworld.core.execution_protocol import (
    ControllerAction,
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
    execution_protocol_requires_tool_free_finalization,
    final_review_guidance,
    load_candidate_fallback,
    load_execution_protocol_state,
    record_candidate_final,
    record_model_execution_profile,
    record_review_tool_action,
    record_tool_protocol_event,
    store_candidate_fallback,
)


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
    assert consume_execution_protocol_guidance(context, "agent") is None
    state = ExecutionProtocolStore(context, "agent", policy).load()
    assert state.replan_count == 1
    assert state.attempt_epoch == 1


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


def test_replan_exhaustion_reserves_a_tool_free_final_turn() -> None:
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
    assert transition.decision.action is ControllerAction.ENTER_FINALIZATION
    assert transition.state.phase is ProtocolPhase.FINALIZE
    assert execution_protocol_requires_tool_free_finalization(context, "agent")


def test_observe_finalization_never_changes_tool_availability() -> None:
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
    assert transition.decision.action is ControllerAction.WOULD_ENTER_FINALIZATION
    assert transition.state.phase is ProtocolPhase.FINALIZE
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
    assert state.long_horizon_armed is True
    assert telemetry == {
        "schema_version": "aworld.execution-protocol-telemetry/v1",
        "mode": "guide",
        "phase": "execute",
        "armed": True,
        "event_count": 1,
        "tool_observation_count": 1,
        "stagnant_observations": 0,
        "replan_count": 0,
        "candidate_final_count": 0,
        "final_review_count": 0,
        "repair_count": 0,
        "finalization_entered": False,
    }


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


def test_invalid_model_profile_fails_open_and_does_not_repeat() -> None:
    context = _context("invalid-model-profile")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    configure_execution_protocol(context, "agent", policy)

    assert record_model_execution_profile(
        context,
        "agent",
        {"horizon": "long", "confidence": "certain"},
    ) is None
    assert execution_protocol_accepts_model_profile(context, "agent") is False
    state = ExecutionProtocolStore(context, "agent", policy).load()
    assert state.long_horizon_armed is False


def test_final_review_is_requested_once_and_unknown_submits_current_result() -> None:
    context = _context("final")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE, activation_event_threshold=1
    )
    configure_execution_protocol(context, "agent", policy)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(validation_evidence_advanced=True),
    )

    first = record_candidate_final(context, "agent")
    assert final_review_guidance(first) is not None
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

    transition = record_candidate_final(context, "agent")

    assert transition is not None
    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.state.final_review_count == 0
    assert final_review_guidance(transition) is None


def test_runtime_policy_can_send_short_candidate_to_model_review() -> None:
    context = _context("review-all")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
    )
    configure_execution_protocol(context, "agent", policy)

    transition = record_candidate_final(context, "agent")

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert "model-owned completion review" in final_review_guidance(transition)


def test_review_tool_action_opens_one_normal_execution_repair() -> None:
    context = _context("repair")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE, activation_event_threshold=1
    )
    configure_execution_protocol(context, "agent", policy)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(validation_evidence_advanced=True),
    )
    record_candidate_final(context, "agent")

    transition = record_review_tool_action(context, "agent")

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_REPAIR
    assert transition.state.repair_count == 1
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")
    assert record_review_tool_action(context, "agent") is None


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
