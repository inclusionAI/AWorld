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
    execution_protocol_policy,
    execution_protocol_requires_tool_free_finalization,
    final_review_guidance,
    load_candidate_fallback,
    load_execution_protocol_state,
    model_owned_review_active,
    record_candidate_final,
    record_model_execution_profile,
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
    assert "next Tool action must create or update" in guidance


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
    assert (
        final_review_guidance(
            first, independent_acceptance_enabled=False
        )
        is not None
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

    transition = record_candidate_final(context, "agent")

    assert transition is not None
    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.state.final_review_count == 0
    assert (
        final_review_guidance(
            transition, independent_acceptance_enabled=False
        )
        is None
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
