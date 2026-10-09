from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Barrier, BrokenBarrierError, Event, get_ident
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
    ConvergenceStage,
    ControllerDecision,
    ControllerAction,
    DecisionReason,
    EventKind,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ExecutionProtocolStore,
    ProtocolMode,
    ProtocolPhase,
    ProtocolTransition,
)
from aworld.core.task import Task
from aworld.runners.execution_protocol import (
    bind_pending_next_action_call,
    build_execution_protocol_telemetry,
    configure_execution_protocol,
    constrain_candidate_convergence_tool_catalog,
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
    MutationGateRejectionReason,
    mutation_gate_interception,
    project_execution_protocol_telemetry,
    record_candidate_final,
    record_acceptance_critic_decision,
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
from aworld.sandbox.tool_observation import (
    SandboxToolObservationRuntime,
    classify_tool_effect,
)


def _sandbox_receipt(action: ActionModel, generation: int, **values):
    effect = classify_tool_effect(action)
    return {
        "schema_version": "aworld.sandbox-tool-observation/v1",
        "tool_call_id": action.tool_call_id,
        "canonical_tool": effect.identity,
        "operation_hash": effect.operation_hash,
        "workspace_generation": generation,
        **values,
    }


@pytest.fixture(autouse=True)
def _legacy_protocol_features(monkeypatch):
    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "false")
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "false")


def _context(task_id: str = "long") -> Context:
    context = Context(task_id=task_id)
    context.set_task(Task(id=task_id, input="complete the public request", timeout=600))
    return context


def _set_deadline_progress(context: Context, consumed_fraction: float) -> None:
    task = context.get_task()
    total = float(task.timeout)
    remaining = total * (1.0 - consumed_fraction)
    task.remaining_seconds = lambda: remaining


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


def test_protocol_persistence_failure_cannot_activate_runtime_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context("protocol-persistence-gate")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        post_candidate_read_only_threshold=1,
    )
    configure_execution_protocol(context, "agent", policy)
    store = ExecutionProtocolStore(context, "agent", policy)
    store.save(
        replace(
            store.load(),
            long_horizon_armed=True,
            candidate_present=True,
            candidate_checkpoint_recorded=True,
        )
    )

    def fail_persistence(*_args, **_kwargs):
        raise OSError("runtime registry unavailable")

    monkeypatch.setattr(
        context, "update_and_project_task_runtime_state", fail_persistence
    )
    monkeypatch.setattr(context, "update_task_runtime_state", fail_persistence)
    monkeypatch.setattr(context, "write_task_runtime_state", fail_persistence)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=9,
            candidate_present=True,
            read_only_observed=True,
        ),
    )

    assert transition.decision.reason is DecisionReason.PERSISTENCE_ERROR
    state = load_execution_protocol_state(context, "agent")
    assert state.event_count == 0
    assert state.convergence_constraint_active is False
    assert execution_protocol_module.MUTATION_GATE_STATE_KEY not in context.context_info
    assert (
        f"{execution_protocol_module.EXECUTION_PROTOCOL_DEADLINE_GUIDANCE_KEY}:agent"
        not in context.context_info
    )


def test_telemetry_distinguishes_active_review_from_terminal_unverified() -> None:
    context = _context("review-telemetry")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    configure_execution_protocol(context, "agent", policy)
    store = ExecutionProtocolStore(context, "agent", policy)
    store.save(
        replace(
            store.load(),
            phase=ProtocolPhase.REVIEW,
            review_pending=True,
            terminal_incomplete=False,
        )
    )

    active = build_execution_protocol_telemetry(context, "agent")
    assert active["phase"] == "review"
    assert active["terminal_incomplete"] is False
    assert project_execution_protocol_telemetry(active) == active

    store.save(
        replace(
            store.load(),
            phase=ProtocolPhase.REVIEW,
            review_pending=False,
            terminal_incomplete=True,
        )
    )
    terminal = build_execution_protocol_telemetry(context, "agent")
    assert terminal["phase"] == "review"
    assert terminal["terminal_incomplete"] is True
    assert project_execution_protocol_telemetry(terminal) == terminal

    legacy_v1 = {
        **terminal,
        "schema_version": "aworld.execution-protocol-telemetry/v1",
    }
    assert project_execution_protocol_telemetry(legacy_v1) is None


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

    _set_deadline_progress(context, 0.40)
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


def _activate_validate_repair_convergence(
    context: Context,
    agent_id: str = "agent",
) -> str:
    from aworld.core.context.compiler import semantic_fingerprint

    configure_execution_protocol(
        context,
        agent_id,
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            post_candidate_read_only_threshold=1,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_long_horizon(context, agent_id)
    _set_deadline_progress(context, 0.40)
    candidate_fingerprint = semantic_fingerprint("diagnostic-candidate")
    record_tool_protocol_event(
        context,
        agent_id,
        _semantic_state(
            current_agent_step=1,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_fingerprint,
        ),
    )
    record_tool_protocol_event(
        context,
        agent_id,
        _semantic_state(
            current_agent_step=2,
            candidate_present=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_fingerprint,
        ),
    )
    assert load_execution_protocol_state(
        context, agent_id
    ).convergence_stage.value == "validate_repair_or_submit"
    return candidate_fingerprint


def _record_failed_candidate_validation(
    context: Context,
    candidate_fingerprint: str,
    *,
    marker: str,
    step: int,
    agent_id: str = "agent",
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

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
        "tool_call_id": f"failed-validation-{marker}",
    }
    record_tool_protocol_event(
        context,
        agent_id,
        _semantic_state(
            current_agent_step=step,
            candidate_present=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_fingerprint,
            result_hash=semantic_fingerprint(f"failed-result-{marker}"),
            failure_signature=semantic_fingerprint(f"failure-{marker}"),
            observed_action_semantics=(failed_validation,),
        ),
    )


def _activate_acceptance_repair_convergence(context: Context) -> str:
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="acceptance-check",
                    argv=("pytest", "-q", "tests/test_contract.py"),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    return _activate_validate_repair_convergence(context)


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
        "terminal_incomplete": False,
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
        "analysis_runway_open": False,
        "analysis_progress_count": 0,
        "analysis_stagnation_count": 0,
        "analysis_runway_reset_count": 0,
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


def test_repeated_unapplied_continues_activate_one_convergence_phase() -> None:
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
    _set_deadline_progress(context, 0.40)

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

    # The first executable semantic is genuine planning progress. Repeating
    # that same semantic twice (even with new prose/arguments) still converges.
    acknowledge_without_replan(1)
    acknowledge_without_replan(2)
    acknowledge_without_replan(3)
    third = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            repetition_count=1,
            current_agent_step=4,
            operation_hash="sha256:operation-4",
            result_hash="sha256:result-4",
        ),
    )

    assert third.decision.action is ControllerAction.CONTINUE
    assert third.decision.reason is DecisionReason.CONVERGENCE_CONSTRAINT_ACTIVE
    assert execution_protocol_model_decision_boundary(context, "agent") is None
    state = load_execution_protocol_state(context, "agent")
    assert state.decision_checkpoint_pending is False
    assert state.replan_requested_count == 3
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
            current_agent_step=5,
            candidate_present=True,
            candidate_advanced=True,
            workspace_mutated=True,
        ),
    )
    state = load_execution_protocol_state(context, "agent")
    assert state.replan_requested_count == 3
    assert state.convergence_stage.value == "validate_repair_or_submit"
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "an inspectable candidate exists" in guidance


def test_unapplied_replans_wait_until_caller_deadline_is_40_percent_consumed() -> None:
    context = _context("deadline-aligned-replan-convergence")
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
    _set_deadline_progress(context, 0.148)

    for sequence in (1, 2):
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                repetition_count=1,
                current_agent_step=sequence,
                operation_hash=f"sha256:early-operation-{sequence}",
                result_hash=f"sha256:early-result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )
        assert not record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )

    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_constraint_active is False
    assert build_execution_protocol_telemetry(context, "agent")[
        "consecutive_unapplied_replans"
    ] == 2

    _set_deadline_progress(context, 0.40)
    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(current_agent_step=3),
    )
    assert transition.decision.reason is DecisionReason.CONVERGENCE_CONSTRAINT_ACTIVE
    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_constraint_active is True


def test_deadline_40_percent_requires_candidate_without_replan_counter() -> None:
    context = _context("deadline-candidate-required")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_long_horizon(context)
    _set_deadline_progress(context, 0.39)
    before = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=1,
            candidate_present=False,
            public_deliverable_declared=True,
        ),
    )
    assert before.state.convergence_constraint_active is False

    _set_deadline_progress(context, 0.40)
    due = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=2,
            candidate_present=False,
            public_deliverable_declared=True,
        ),
    )

    assert due.state.convergence_constraint_active is True
    assert due.state.convergence_stage.value == "produce_candidate"
    assert build_execution_protocol_telemetry(context, "agent")[
        "consecutive_unapplied_replans"
    ] == 0


def test_unapplied_replans_without_typed_deadline_preserve_compatibility() -> None:
    context = Context(task_id="deadline-unavailable-replan-convergence")
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
                operation_hash=f"sha256:no-deadline-operation-{sequence}",
                result_hash=f"sha256:no-deadline-result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )
        assert not record_model_decision_attempt_failure(
            context, "agent", boundary="replan"
        )

    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_constraint_active is True


def test_new_executable_plan_semantic_is_progress_but_argument_churn_is_not() -> None:
    context = _context("semantic-plan-progress")
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
    _set_deadline_progress(context, 0.40)

    def continue_with(sequence: int, command: str) -> None:
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                repetition_count=1,
                current_agent_step=sequence,
                operation_hash=f"sha256:semantic-operation-{sequence}",
                result_hash=f"sha256:semantic-result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_boundary(
            context,
            "agent",
            boundary="replan",
            execution_profile=None,
            plan_update={
                "decision": "continue",
                "horizon": "long",
                "milestone": f"bounded inspection {sequence}",
                "next_action": "inspect one bounded input region",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": json.dumps({"command": command}),
                "verification_plan": "use only the new bounded evidence",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "a bounded input fact is still missing",
                "assumptions": [],
                "retired_approaches": [],
                "evidence_refs": [],
                "selected_candidate_id": None,
            },
            available_tool_names=frozenset({"terminal__execute"}),
        )

    continue_with(1, "cat /app/input-a.txt")
    continue_with(2, "cat /app/input-b.txt")
    assert build_execution_protocol_telemetry(context, "agent")[
        "consecutive_unapplied_replans"
    ] == 0
    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_constraint_active is False

    # Head/tail change raw arguments, but retain the same executable semantic
    # shape (capability, effect, and input-b target), so they cannot evade the
    # bounded convergence counter.
    continue_with(3, "head -n 1 /app/input-b.txt")
    assert build_execution_protocol_telemetry(context, "agent")[
        "consecutive_unapplied_replans"
    ] == 1
    continue_with(4, "tail -n 1 /app/input-b.txt")
    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_constraint_active is True


def test_repeated_identical_replan_semantic_cannot_reset_convergence() -> None:
    context = _context("replayed-replan-semantic")
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
    _set_deadline_progress(context, 0.40)

    for sequence in (1, 2, 3):
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                repetition_count=1,
                current_agent_step=sequence,
                operation_hash=f"sha256:replan-operation-{sequence}",
                result_hash=f"sha256:replan-result-{sequence}",
            ),
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_boundary(
            context,
            "agent",
            boundary="replan",
            execution_profile=None,
            plan_update={
                "decision": "replan",
                "horizon": "long",
                "milestone": "inspect the same bounded input",
                "next_action": "read the same input",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": json.dumps(
                    {"command": "cat /app/input.txt"}
                ),
                "verification_plan": "use the bounded observation",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "one input fact remains unknown",
                "assumptions": [],
                "retired_approaches": [],
                "evidence_refs": [],
                "selected_candidate_id": None,
            },
            available_tool_names=frozenset({"terminal__execute"}),
        )

    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["consecutive_unapplied_replans"] == 2
    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_constraint_active is True


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
    _set_deadline_progress(context, 0.40)

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


def test_40_percent_validate_guidance_never_requests_another_candidate() -> None:
    context = _context("validate-deadline-guidance")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    configure_execution_protocol(context, "agent", policy)
    store = ExecutionProtocolStore(context, "agent", policy)
    store.save(
        replace(
            store.load(),
            long_horizon_armed=True,
            candidate_present=True,
            candidate_checkpoint_recorded=True,
            convergence_constraint_active=True,
            convergence_stage=ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT,
            convergence_constraint_activation_count=1,
        )
    )
    _set_deadline_progress(context, 0.40)

    guidance = consume_execution_protocol_guidance(context, "agent")

    assert guidance is not None
    assert "validate, repair, or submit the current candidate" in guidance
    assert "produce the smallest honest candidate" not in guidance


def test_validate_mutation_window_tracks_40_65_80_deadline_stages() -> None:
    context = _context("validate-mutation-window-deadlines")
    _activate_validate_repair_convergence(context)

    _set_deadline_progress(context, 0.40)
    at_40 = consume_execution_protocol_guidance(context, "agent")
    _set_deadline_progress(context, 0.65)
    at_65 = consume_execution_protocol_guidance(context, "agent")
    _set_deadline_progress(context, 0.80)
    at_80 = consume_execution_protocol_guidance(context, "agent")

    assert "mutation validation window" in at_40
    assert "40% convergence checkpoint" in at_40
    assert "65% validation checkpoint" in at_65
    assert "80% delivery-only checkpoint" in at_80
    assert all("produce the smallest honest candidate" not in value for value in (
        at_40,
        at_65,
        at_80,
    ))


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


def test_present_unmutated_candidate_uses_existing_convergence_threshold(
    tmp_path,
) -> None:
    candidate = tmp_path / "result.json"
    candidate.write_text("baseline", encoding="utf-8")
    context = _context("present-unmutated-convergence")
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
            post_candidate_read_only_threshold=2,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    _declare_long_horizon(context)
    _set_deadline_progress(context, 0.40)

    for step in (1, 2):
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=step,
                public_deliverable_declared=True,
                candidate_present=True,
                public_candidate_mutated=False,
                read_only_observed=True,
                workspace_mutated=False,
            ),
        )

    assert transition.decision.action is ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    assert transition.decision.reason is DecisionReason.POST_CANDIDATE_STAGNATION
    state = load_execution_protocol_state(context, "agent")
    assert state.convergence_stage is ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
    assert state.candidate_checkpoint_recorded is False
    assert state.acceptance_confirmed is False


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
    _set_deadline_progress(context, 0.40)

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
    assert (
        "Each candidate-bound declared-revision signature is admitted once"
        in guidance
    )
    assert "semantically unknown mutations remain blocked" in guidance
    assert "up to three mechanically read-only diagnostic Tool calls" in guidance
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
    intercepted = await hook.exec(
        Message(category="tool_call", payload=[unrelated_read], sender="agent"),
        context,
    )
    assert intercepted is not None
    hook_message = intercepted.headers["tool_interception"]["message"]
    assert "without verifier or repair authorization" in hook_message
    assert "up to three" in hook_message
    assert "one per Tool batch" in hook_message
    assert "new for the current candidate" in hook_message
    assert "unknown mutations remain blocked" in hook_message

    from aworld.sandbox.tool_observation import (
        build_preflight_action_semantic_receipt,
    )

    validation_receipt = build_preflight_action_semantic_receipt(
        context=context,
        action=validation,
        delivery_intent="validate_candidate",
    ).to_dict()
    validation_receipt.update(
        {
            "executed": True,
            "succeeded": True,
            "timed_out": False,
            "tool_call_id": validation.tool_call_id,
        }
    )

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
                        "sandbox_observation": _sandbox_receipt(
                            validation,
                            1,
                            effect="read_only",
                            workspace_mutated=False,
                            cache_hit=False,
                            action_semantic_receipt=validation_receipt,
                        )
                    },
                )
            ],
        ),
    )
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["post_candidate_read_only_observations"] == 0
    assert telemetry["mutation_gate_blocked_read_only_call_count"] == 2
    # A fresh Sandbox-authenticated validation is both progress and an exact
    # match for the bound model plan.
    assert load_execution_protocol_state(context, "agent").last_action_alignment.value == (
        "matched"
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
    _set_deadline_progress(context, 0.40)
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


def test_produce_convergence_keeps_required_delivery_open_after_rejections(
    tmp_path,
) -> None:
    target = tmp_path / "result.json"
    context = _context("produce-rejection-finalization")
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

    for index in range(2):
        blocked = mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": f"cat diagnostic-{index}.log"},
                    tool_call_id=f"rejected-before-candidate-{index}",
                    agent_name="agent",
                )
            ],
        )
        assert blocked is not None
        assert blocked["convergence_stage"] == "produce_candidate"
        assert blocked["blocked_call_count"] == index + 1
        assert blocked["pre_candidate_rejected_batch_count"] == index + 1
        assert blocked["pre_candidate_tool_free_latched"] is False
        assert not execution_protocol_requires_tool_free_finalization(
            context, "agent"
        )

    state = load_execution_protocol_state(context, "agent")
    assert state.phase is ProtocolPhase.EXECUTE
    assert state.finalization_entered is False
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["convergence_gate_blocked_call_count"] == 2

    # The rejection counter is bounded telemetry, not authority to abandon a
    # required output.  A later exact candidate write must remain admissible.
    deliverable = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf '{{}}' > {target}"},
        tool_call_id="recovered-candidate-write",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [deliverable]) is None


def test_produce_rejection_latch_rechecks_live_candidate_before_finalizing(
    tmp_path,
) -> None:
    target = tmp_path / "result.json"
    context = _context("produce-live-candidate-recheck")
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

    admitted = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf '{{}}' > {target}"},
        tool_call_id="admitted-candidate-write",
        agent_name="agent",
    )
    blocked = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat diagnostic-{index}.log"},
            tool_call_id=f"blocked-helper-{index}",
            agent_name="agent",
        )
        for index in range(2)
    ]
    receipt = mutation_gate_interception(context, [admitted, *blocked])
    assert receipt is not None
    assert receipt["blocked_call_count"] == 2
    assert receipt["pre_candidate_rejected_batch_count"] == 0
    assert receipt["pre_candidate_tool_free_latched"] is False
    # Simulate the admitted call completing while post-tool projection is
    # delayed or unavailable. The durable gate is intentionally still stale.
    target.write_text("{}", encoding="utf-8")

    assert not execution_protocol_requires_tool_free_finalization(
        context, "agent"
    )
    state = load_execution_protocol_state(context, "agent")
    assert state.phase is ProtocolPhase.EXECUTE
    assert state.finalization_entered is False


def test_produce_rejection_latch_ignores_baseline_file_presence(tmp_path) -> None:
    target = tmp_path / "result.json"
    target.write_text("baseline", encoding="utf-8")
    context = _context("produce-baseline-candidate")
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
    assert load_execution_protocol_state(
        context, "agent"
    ).convergence_stage is ConvergenceStage.PRODUCE_CANDIDATE

    for index in range(2):
        receipt = mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": f"cat diagnostic-{index}.log"},
                    tool_call_id=f"baseline-rejected-{index}",
                    agent_name="agent",
                )
            ],
        )
        assert receipt is not None

    assert not execution_protocol_requires_tool_free_finalization(context, "agent")
    assert load_execution_protocol_state(
        context, "agent"
    ).phase is ProtocolPhase.EXECUTE


def test_produce_convergence_requires_isolated_python_import_authority() -> None:
    from aworld.sandbox.tool_observation import (
        build_preflight_action_semantic_receipt,
    )

    context = _context("isolated-python-candidate-admission")
    context.workspace_path = "/app"
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "result",
                "path": "/app/out.txt",
                "display_path": "out.txt",
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
    body = """import math, re
rows = open('text.gcode', errors='replace').read().splitlines()
values = [math.hypot(1, 1) for row in rows if re.search(r'G1', row)]
open('/app/out.txt', 'w').write(str(len(values)))
PY
"""
    nonisolated = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cd /app && python3 - <<'PY'\n" + body},
        tool_call_id="nonisolated-candidate",
        agent_name="agent",
    )
    isolated = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cd /app && python3 -I - <<'PY'\n" + body},
        tool_call_id="isolated-candidate",
        agent_name="agent",
    )

    nonisolated_semantic = build_preflight_action_semantic_receipt(
        context=context,
        action=nonisolated,
        delivery_intent="produce_candidate",
    )
    isolated_semantic = build_preflight_action_semantic_receipt(
        context=context,
        action=isolated,
        delivery_intent="produce_candidate",
    )

    assert nonisolated_semantic.effect == "unknown"
    assert nonisolated_semantic.declared_deliverable_targeted is True
    assert isolated_semantic.effect == "mutating"
    assert isolated_semantic.declared_deliverable_targeted is True
    blocked = mutation_gate_interception(context, [nonisolated])
    assert blocked is not None
    assert blocked["tool_call_ids"] == ["nonisolated-candidate"]
    assert mutation_gate_interception(context, [isolated]) is None


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


def test_active_v3_gate_is_scope_matched_but_diagnostics_fail_closed(
    tmp_path,
) -> None:
    target = tmp_path / "result.txt"
    target.write_text("candidate")
    validation_code = f"cat {target}"
    context = _context("active-v3-gate")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(target),
                "display_path": "result.txt",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="validate-v3-candidate",
                    argv=("sh", "-c", validation_code),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    gate["schema_version"] = "aworld.mutation-gate/v3"
    gate.pop("candidate_diagnostic_read_count")
    gate.pop("candidate_diagnostic_high_water")
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )
    registered_validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="v3-registered-validation",
        agent_name="agent",
    )
    declared_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf revised > {target}"},
        tool_call_id="v3-declared-revision",
        agent_name="agent",
    )
    diagnostic = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat broad-v3-diagnostic.log"},
        tool_call_id="v3-candidate-diagnostic",
        agent_name="agent",
    )

    assert mutation_gate_interception(context, [registered_validation]) is None
    assert mutation_gate_interception(context, [declared_revision]) is None
    blocked = mutation_gate_interception(context, [diagnostic])
    assert blocked is not None
    assert blocked["tool_call_ids"] == ["v3-candidate-diagnostic"]

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=3,
            candidate_present=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_fingerprint,
        ),
    )
    migrated = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert migrated["schema_version"] == "aworld.mutation-gate/v4"
    assert migrated["candidate_diagnostic_read_count"] == 3
    assert migrated["candidate_diagnostic_high_water"] == "f" * 128


@pytest.mark.parametrize(
    ("stored", "expected"),
    (
        ("-1", "0" * 128),
        (-1, "0" * 128),
        ("f" * 129, "0" * 128),
        ("", "0" * 128),
        ("8" + "0" * 127, "8" + "0" * 127),
        ("A", "0" * 127 + "a"),
    ),
)
def test_declared_mutation_attempt_high_water_is_strict_and_canonical(
    stored,
    expected,
) -> None:
    context = _context("declared-mutation-high-water")
    configure_execution_protocol(
        context, "agent", ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    )
    _declare_long_horizon(context)
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(current_agent_step=1),
    )
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    gate["declared_mutation_attempt_high_water"] = stored
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(current_agent_step=2),
    )

    projected = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert projected["declared_mutation_attempt_high_water"] == expected
    assert len(projected["declared_mutation_attempt_high_water"]) == 128


@pytest.mark.parametrize(
    "schema",
    (
        "aworld.mutation-gate/v2",
        "aworld.mutation-gate/v3",
        "aworld.mutation-gate/v4",
    ),
)
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
    _set_deadline_progress(context, 0.40)

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
    mixed = mutation_gate_interception(
        context,
        [
            candidate,
            ActionModel(
                tool_name="filesystem",
                action_name="write_file",
                params={"path": "helper-2.txt", "content": "helper"},
                tool_call_id="unbound-helper-2",
                agent_name="agent",
            ),
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                params={"code": "cat README.md"},
                tool_call_id="unbound-read",
                agent_name="agent",
            ),
        ],
    )
    assert mixed is not None
    assert mixed["pre_candidate_rejected_batch_count"] == 0
    assert mixed["pre_candidate_tool_free_latched"] is False
    assert not execution_protocol_requires_tool_free_finalization(
        context, "agent"
    )


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

    arbitrary_unknown = ActionModel(
        tool_name="custom",
        action_name="opaque",
        params={"value": "candidate"},
        tool_call_id="arbitrary-unknown",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "produce an opaque candidate",
            "next_action": "invoke the exact opaque action",
            "next_action_tool": "custom__opaque",
            "next_action_arguments": json.dumps(arbitrary_unknown.params),
            "verification_plan": "inspect the resulting service state",
            "completion_assessment": "in_progress",
            "delivery_intent": "produce_candidate",
            "delivery_rationale": "the action is exact but mechanically unknown",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        },
    ) is not None
    assert bind_pending_next_action_call(
        context, "agent", [arbitrary_unknown]
    ) is True
    unknown_blocked = mutation_gate_interception(context, [arbitrary_unknown])
    assert unknown_blocked is not None
    assert unknown_blocked["tool_call_ids"] == ["arbitrary-unknown"]


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
async def test_validate_convergence_admits_bounded_declared_revision_and_validation(
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
    _set_deadline_progress(context, 0.40)
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

    mixed_target_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={
            "code": (
                f"printf revised > {target}; "
                f"printf helper > {tmp_path / 'helper.py'}"
            )
        },
        tool_call_id="mixed-target-revision",
        agent_name="agent",
    )
    unknown_revision = ActionModel(
        tool_name="custom",
        action_name="opaque",
        params={"value": "revision"},
        tool_call_id="unknown-revision",
        agent_name="agent",
    )
    ineligible_revisions = mutation_gate_interception(
        context, [mixed_target_revision, unknown_revision]
    )
    assert ineligible_revisions is not None
    assert ineligible_revisions["tool_call_ids"] == [
        "mixed-target-revision",
        "unknown-revision",
    ]

    direct_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf revised > {target}"},
        tool_call_id="direct-declared-revision",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [direct_revision]) is None
    replayed_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params=dict(direct_revision.params),
        tool_call_id="direct-declared-revision-replayed",
        agent_name="agent",
    )
    repeated_revision = mutation_gate_interception(context, [replayed_revision])
    assert repeated_revision is not None
    assert repeated_revision["tool_call_ids"] == [
        "direct-declared-revision-replayed"
    ]

    candidate_a_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    candidate_a_fingerprint = candidate_a_gate["candidate_fingerprint"]
    candidate_b_gate = dict(candidate_a_gate)
    candidate_b_gate["candidate_fingerprint"] = semantic_fingerprint("candidate-b")
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", candidate_b_gate
    )
    candidate_b_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params=dict(direct_revision.params),
        tool_call_id="direct-declared-revision-candidate-b",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [candidate_b_revision]) is None

    returned_candidate_a_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    returned_candidate_a_gate["candidate_fingerprint"] = candidate_a_fingerprint
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", returned_candidate_a_gate
    )
    returned_candidate_a_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params=dict(direct_revision.params),
        tool_call_id="direct-declared-revision-candidate-a-returned",
        agent_name="agent",
    )
    replayed_candidate_a = mutation_gate_interception(
        context, [returned_candidate_a_revision]
    )
    assert replayed_candidate_a is not None
    assert replayed_candidate_a["tool_call_ids"] == [
        "direct-declared-revision-candidate-a-returned"
    ]

    large_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf '{'x' * 5000}' > {target}"},
        tool_call_id="large-declared-revision",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [large_revision]) is None
    replayed_large_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params=dict(large_revision.params),
        tool_call_id="large-declared-revision-replayed",
        agent_name="agent",
    )
    repeated_large_revision = mutation_gate_interception(
        context, [replayed_large_revision]
    )
    assert repeated_large_revision is not None
    assert repeated_large_revision["tool_call_ids"] == [
        "large-declared-revision-replayed"
    ]

    def validation_result(
        action: ActionModel,
        *,
        executed: bool,
        cache_hit: bool,
        workspace_generation: int,
    ) -> ActionResult:
        from aworld.sandbox.tool_observation import (
            build_preflight_action_semantic_receipt,
        )

        semantic_receipt = build_preflight_action_semantic_receipt(
            context=context,
            action=action,
            delivery_intent="validate_candidate",
        ).to_dict()
        semantic_receipt.update(
            {
                "executed": executed,
                "succeeded": True,
                "timed_out": False,
                "tool_call_id": action.tool_call_id,
            }
        )
        return ActionResult(
            tool_call_id=action.tool_call_id,
            content="{}",
            success=True,
            metadata={
                "sandbox_observation": _sandbox_receipt(
                    action,
                    workspace_generation,
                    cache_hit=cache_hit,
                    effect="read_only",
                    workspace_mutated=False,
                    action_semantic_receipt=semantic_receipt,
                )
            },
        )

    first_validation = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[validation],
        observation=Observation(
            action_result=[
                validation_result(
                    validation,
                    executed=True,
                    cache_hit=False,
                    workspace_generation=1,
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
                validation_result(
                    repeated_validation,
                    executed=False,
                    cache_hit=True,
                    workspace_generation=2,
                )
            ]
        ),
    )
    assert first_validation["delivery_progress_advanced"] is True
    assert repeated_state["delivery_progress_advanced"] is False

    fresh_repeat = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="registered-validation-fresh-repeat",
        agent_name="agent",
    )
    fresh_repeat_state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[fresh_repeat],
        observation=Observation(
            action_result=[
                validation_result(
                    fresh_repeat,
                    executed=True,
                    cache_hit=False,
                    workspace_generation=3,
                )
            ]
        ),
    )
    assert fresh_repeat_state["validation_evidence_advanced"] is False
    assert fresh_repeat_state["delivery_progress_advanced"] is False

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
    poisoned_repair_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    poisoned_repair_gate["repair_failure_evidence_high_water"] = "-1"
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", poisoned_repair_gate
    )
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
    repaired_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert len(repaired_gate["repair_failure_evidence_high_water"]) == 128
    assert repaired_gate["repair_failure_evidence_high_water"] != "0" * 128
    assert repaired_gate["repair_authorization"] is not None
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


def test_candidate_allows_three_diagnostic_reads_then_repair(
    tmp_path,
) -> None:
    context = _context("failed-validation-diagnostic-window")
    validation_code = "cat candidate.txt"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="validate-candidate",
                    argv=("sh", "-c", validation_code),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="first",
        step=3,
    )
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_diagnostic_read_count"] == 0
    assert gate["repair_authorization"]["source"] == "validation_failure"
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "No verifier or repair authorization is required" in guidance
    assert "up to three bounded" in guidance
    assert "3 diagnostic call(s) remain" in guidance
    assert "Registered validation remains admitted" in guidance

    first_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat diagnostic-one.log"},
        tool_call_id="diagnostic-read-one",
        agent_name="agent",
    )
    second_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat diagnostic-two.log"},
        tool_call_id="diagnostic-read-two",
        agent_name="agent",
    )
    third_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat diagnostic-three.log"},
        tool_call_id="diagnostic-read-three",
        agent_name="agent",
    )
    fourth_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat diagnostic-four.log"},
        tool_call_id="diagnostic-read-four",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [first_read]) is None
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_diagnostic_read_count"] == 1
    first_authorization_hash = gate["repair_authorization"][
        "failure_evidence_hash"
    ]

    registered_validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="registered-validation-no-diagnostic-charge",
        agent_name="agent",
    )
    assert framework_observable_validation_kind(
        context, "agent", registered_validation
    ) == "registered_completion_validation"
    assert mutation_gate_interception(context, [registered_validation]) is None
    assert context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )["candidate_diagnostic_read_count"] == 1

    # Distinct failed evidence for the same candidate refreshes repair evidence,
    # but must not refresh the candidate-bound diagnostic allowance.
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="second",
        step=4,
    )
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["repair_authorization"]["failure_evidence_hash"] != (
        first_authorization_hash
    )
    assert gate["candidate_diagnostic_read_count"] == 1

    assert mutation_gate_interception(context, [second_read]) is None
    assert mutation_gate_interception(context, [third_read]) is None
    blocked_fourth = mutation_gate_interception(context, [fourth_read])
    assert blocked_fourth is not None
    assert blocked_fourth["tool_call_ids"] == ["diagnostic-read-four"]
    assert blocked_fourth["candidate_diagnostic_read_count"] == 3
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_diagnostic_read_count"] == 3

    repair = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": str(tmp_path / "candidate.txt"), "content": "repaired"},
        tool_call_id="evidence-backed-repair",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "repair the current candidate",
            "next_action": "apply the evidence-backed candidate repair",
            "next_action_tool": "filesystem__write_file",
            "next_action_arguments": json.dumps(repair.params),
            "verification_plan": "run the registered validation again",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "produce_candidate",
            "delivery_rationale": "fresh failed validation supports this repair",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "diagnostic-candidate",
        },
    ) is not None
    assert bind_pending_next_action_call(context, "agent", [repair]) is True
    assert mutation_gate_interception(context, [repair]) is None
    repaired_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert repaired_gate["repair_authorization"] is None
    assert repaired_gate["candidate_diagnostic_read_count"] == 3


def _exhaust_candidate_diagnostics(context: Context) -> str:
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    for index in range(3):
        assert mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": f"cat diagnostic-{index}.log"},
                    tool_call_id=f"diagnostic-{index}",
                    agent_name="agent",
                )
            ],
        ) is None
    return candidate_fingerprint


def test_post_candidate_rejection_budget_is_independent_of_diagnostic_budget():
    context = _context("independent-post-candidate-rejection-budget")
    _activate_validate_repair_convergence(context)

    for index in range(4):
        blocked = mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": f"unmodeled_command_{index}"},
                    tool_call_id=f"pure-rejection-{index}",
                    agent_name="agent",
                )
            ],
        )
        assert blocked is not None
        assert blocked["candidate_diagnostic_read_count"] == 0
        assert blocked["candidate_exhausted_rejection_count"] == 0
        assert blocked["candidate_pure_rejection_count"] == index + 1
        assert blocked["candidate_pure_rejection_latched"] is (index == 3)

    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    assert load_execution_protocol_state(context, "agent").phase is (
        ProtocolPhase.FINALIZE
    )


def test_structured_quality_debt_guides_one_bounded_repair_then_uncertainty():
    context = _context("structured-quality-guidance")
    _activate_validate_repair_convergence(context)
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    gate.update(
        structured_quality_open=True,
        structured_quality_required_table_count=2,
        structured_quality_usable_table_count=0,
        structured_quality_repair_attempt_count=0,
        structured_quality_reason_codes=["structured_table_unusable"],
    )
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )

    repair_guidance = consume_execution_protocol_guidance(context, "agent")
    assert "one bounded repair" in repair_guidance
    assert "JSON cell grid" in repair_guidance

    gate["structured_quality_repair_attempt_count"] = 1
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )
    exhausted_guidance = consume_execution_protocol_guidance(context, "agent")
    assert "remains after the bounded repair attempt" in exhausted_guidance
    assert "submit the limitation accurately" in exhausted_guidance


def test_exhausted_candidate_hides_exploration_and_latches_after_two_rejections():
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context("exhausted-candidate-catalog")
    candidate_fingerprint = _exhaust_candidate_diagnostics(context)
    tools = [
        {
            "type": "function",
            "function": {"name": "run_code", "parameters": {"type": "object"}},
        },
        {
            "type": "function",
            "function": {"name": "read_file", "parameters": {"type": "object"}},
        },
        {
            "type": "function",
            "function": {"name": "write_file", "parameters": {"type": "object"}},
        },
        {
            "type": "function",
            "function": {
                "name": "CONTEXT_TOOL__list_sessions",
                "parameters": {"type": "object"},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "AWORLD_ADVISORY_VERIFIER__review_candidate",
                "parameters": {"type": "object"},
            },
        },
    ]
    mapping = {
        "run_code": "terminal__run_code",
        "read_file": "filesystem__read_file",
        "write_file": "filesystem__write_file",
    }

    constrained = constrain_candidate_convergence_tool_catalog(
        context,
        "agent",
        tools,
        tool_identity_mapping=mapping,
    )

    # Schema names alone cannot prove whether a custom Tool is a registered
    # validation. Exact pre-Tool semantics remain authoritative until the
    # persistent latch enters FINALIZE.
    assert constrained == tools
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")

    assert (
        constrain_candidate_convergence_tool_catalog(
            context,
            "agent",
            None,
            tool_identity_mapping=mapping,
        )
        is None
    )

    for index in range(2):
        blocked = mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": f"unmodeled_command_{index}"},
                    tool_call_id=f"rejected-after-quota-{index}",
                    agent_name="agent",
                )
            ],
        )
        assert blocked is not None
        gate = context.read_task_runtime_state(
            "agent", "execution_protocol_mutation_gate"
        )
        assert gate["candidate_exhausted_rejection_fingerprint"] == (
            candidate_fingerprint
        )
        assert gate["candidate_exhausted_rejection_count"] == index + 1
        assert gate["candidate_tool_free_latched"] is (index == 1)

    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    state = load_execution_protocol_state(context, "agent")
    assert state.phase is ProtocolPhase.FINALIZE
    assert state.finalization_entered is True
    assert (
        constrain_candidate_convergence_tool_catalog(
            context,
            "agent",
            tools,
            tool_identity_mapping=mapping,
        )
        == []
    )
    submitted = record_candidate_final(context, "agent")
    assert submitted is not None
    assert submitted.decision.action is ControllerAction.STOP_INCOMPLETE
    assert submitted.state.phase is ProtocolPhase.REVIEW
    assert submitted.state.review_pending is False
    assert submitted.state.terminal_incomplete is True

    # Simulate a legitimate new candidate epoch from a fresh execution state;
    # the latch binding must not survive the fingerprint change.
    store = ExecutionProtocolStore(
        context,
        "agent",
        execution_protocol_policy(context, "agent"),
    )
    store.save(
        replace(
            store.load(),
            phase=ProtocolPhase.EXECUTE,
            terminal_incomplete=False,
            finalization_entered=False,
        )
    )
    new_fingerprint = semantic_fingerprint("diagnostic-candidate-revised")
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=7,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=new_fingerprint,
        ),
    )
    refreshed = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert refreshed["candidate_exhausted_rejection_count"] == 0
    assert refreshed["candidate_tool_free_latched"] is False
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")


def test_late_catalog_observes_but_never_transitions_new_latch() -> None:
    context = _context("late-catalog-latch-race")
    _exhaust_candidate_diagnostics(context)
    tools = [
        {
            "type": "function",
            "function": {"name": "run_code", "parameters": {"type": "object"}},
        }
    ]

    for index in range(2):
        blocked = mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": f"unmodeled_late_catalog_{index}"},
                    tool_call_id=f"late-catalog-rejection-{index}",
                    agent_name="agent",
                )
            ],
        )
        assert blocked is not None
        assert constrain_candidate_convergence_tool_catalog(
            context, "agent", tools
        ) == tools
        assert load_execution_protocol_state(context, "agent").phase is not (
            ProtocolPhase.FINALIZE
        )

    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_tool_free_latched"] is True
    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    assert constrain_candidate_convergence_tool_catalog(
        context, "agent", tools
    ) == []


def test_legal_declared_revision_only_resets_after_candidate_changes(tmp_path):
    context = _context("declared-revision-resets-rejection-latch")
    target = tmp_path / "candidate.txt"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(ArtifactRequirement("candidate", str(target)),),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    _exhaust_candidate_diagnostics(context)
    blocked = mutation_gate_interception(
        context,
        [
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                params={"code": "unmodeled_first_attempt"},
                tool_call_id="first-rejected-attempt",
                agent_name="agent",
            )
        ],
    )
    assert blocked is not None
    assert blocked["candidate_exhausted_rejection_count"] == 1

    revision = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": str(target), "content": "revised"},
        tool_call_id="declared-revision",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [revision]) is None
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    # Preflight admission is not proof that the Tool ran or changed the
    # candidate, so it cannot erase the existing strike.
    assert gate["candidate_exhausted_rejection_count"] == 1
    assert gate["candidate_tool_free_latched"] is False

    current_fingerprint = gate["candidate_fingerprint"]
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=8,
            candidate_present=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=current_fingerprint,
            workspace_mutated=False,
        ),
    )
    unchanged = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert unchanged["candidate_exhausted_rejection_count"] == 1

    from aworld.core.context.compiler import semantic_fingerprint

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=9,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=semantic_fingerprint(
                "declared-revision-candidate"
            ),
            workspace_mutated=True,
        ),
    )
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_exhausted_rejection_count"] == 0
    assert gate["candidate_tool_free_latched"] is False
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")


def test_registered_validation_does_not_erase_exhausted_rejection_strike():
    context = _context("validation-preserves-rejection-strike")
    validation_code = "cat candidate.txt"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="validate-candidate",
                    argv=("sh", "-c", validation_code),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    _exhaust_candidate_diagnostics(context)

    def rejected(call_id: str):
        return mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": "unmodeled_after_quota"},
                    tool_call_id=call_id,
                    agent_name="agent",
                )
            ],
        )

    assert rejected("first-rejection") is not None
    validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="registered-validation",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [validation]) is None
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_exhausted_rejection_count"] == 1
    assert gate["candidate_tool_free_latched"] is False

    assert rejected("second-rejection") is not None
    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    assert load_execution_protocol_state(context, "agent").phase is (
        ProtocolPhase.FINALIZE
    )


def test_mixed_registered_validation_and_blocked_read_converges_after_two_batches():
    context = _context("mixed-validation-and-blocked-read-converges")
    validation_code = "cat candidate.txt"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="validate-candidate",
                    argv=("sh", "-c", validation_code),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    _exhaust_candidate_diagnostics(context)

    for index in range(2):
        validation_call_id = f"registered-validation-{index}"
        blocked_call_id = f"blocked-read-{index}"
        interception = mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": validation_code},
                    tool_call_id=validation_call_id,
                    agent_name="agent",
                ),
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": "cat unrelated-after-quota.log"},
                    tool_call_id=blocked_call_id,
                    agent_name="agent",
                ),
            ],
        )
        assert interception is not None
        assert interception["block_all"] is False
        assert interception["tool_call_ids"] == [blocked_call_id]
        assert interception["candidate_exhausted_rejection_count"] == index + 1
        assert interception["candidate_tool_free_latched"] is (index == 1)

    # A mixed batch still has an admitted validation that may project fresh
    # candidate evidence after execution, so preflight records the latch but
    # defers the typed transition until the next pre-generation boundary.
    assert load_execution_protocol_state(context, "agent").phase is not (
        ProtocolPhase.FINALIZE
    )
    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    assert load_execution_protocol_state(context, "agent").phase is (
        ProtocolPhase.FINALIZE
    )


@pytest.mark.parametrize("candidate_advances", (False, True))
def test_mixed_declared_revision_resolves_latch_after_execution(
    tmp_path,
    candidate_advances: bool,
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context(f"mixed-revision-advance-{candidate_advances}")
    target = tmp_path / "candidate.txt"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(ArtifactRequirement("candidate", str(target)),),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    _exhaust_candidate_diagnostics(context)

    first = mutation_gate_interception(
        context,
        [
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                params={"code": "unmodeled_first_attempt"},
                tool_call_id="first-rejection",
                agent_name="agent",
            )
        ],
    )
    assert first is not None
    current = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    old_fingerprint = current["candidate_fingerprint"]

    blocked_call_id = "blocked-helper-read"
    mixed = mutation_gate_interception(
        context,
        [
            ActionModel(
                tool_name="filesystem",
                action_name="write_file",
                params={"path": str(target), "content": "revised"},
                tool_call_id="declared-revision",
                agent_name="agent",
            ),
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                params={"code": "cat unrelated-helper.log"},
                tool_call_id=blocked_call_id,
                agent_name="agent",
            ),
        ],
    )
    assert mixed is not None
    assert mixed["block_all"] is False
    assert mixed["tool_call_ids"] == [blocked_call_id]
    assert mixed["candidate_tool_free_latched"] is True
    assert load_execution_protocol_state(context, "agent").phase is not (
        ProtocolPhase.FINALIZE
    )

    projected_fingerprint = (
        semantic_fingerprint("advanced-declared-revision")
        if candidate_advances
        else old_fingerprint
    )
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=9,
            candidate_present=True,
            candidate_advanced=candidate_advances,
            delivery_progress_advanced=candidate_advances,
            public_candidate_mutated=candidate_advances,
            public_delivery_fingerprint=projected_fingerprint,
            workspace_mutated=candidate_advances,
        ),
    )

    if candidate_advances:
        gate = context.read_task_runtime_state(
            "agent", "execution_protocol_mutation_gate"
        )
        assert gate["candidate_fingerprint"] == projected_fingerprint
        assert gate["candidate_exhausted_rejection_count"] == 0
        assert gate["candidate_tool_free_latched"] is False
        assert not execution_protocol_requires_tool_free_finalization(
            context, "agent"
        )
        assert load_execution_protocol_state(context, "agent").phase is not (
            ProtocolPhase.FINALIZE
        )
    else:
        assert execution_protocol_requires_tool_free_finalization(context, "agent")
        assert load_execution_protocol_state(context, "agent").phase is (
            ProtocolPhase.FINALIZE
        )


def test_malformed_latch_requires_durable_typed_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context("malformed-latch-finalization")
    _exhaust_candidate_diagnostics(context)
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    gate["candidate_exhausted_rejection_count"] = "corrupt"
    execution_protocol_module._write_runtime_value(
        context,
        "agent",
        execution_protocol_module.MUTATION_GATE_STATE_KEY,
        gate,
    )
    tools = [
        {
            "type": "function",
            "function": {"name": "run_code", "parameters": {"type": "object"}},
        }
    ]
    original_apply_event = execution_protocol_module._apply_event

    def fail_finalization(context_arg, agent_id, event):
        assert event.kind is EventKind.CONVERGENCE_EXHAUSTED
        return ProtocolTransition(
            state=load_execution_protocol_state(context_arg, agent_id),
            decision=ControllerDecision(
                action=ControllerAction.CONTINUE,
                reason=DecisionReason.PERSISTENCE_ERROR,
            ),
        )

    monkeypatch.setattr(
        execution_protocol_module,
        "_apply_event",
        fail_finalization,
    )
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")
    assert constrain_candidate_convergence_tool_catalog(
        context, "agent", tools
    ) == tools
    assert load_execution_protocol_state(context, "agent").phase is not (
        ProtocolPhase.FINALIZE
    )

    monkeypatch.setattr(
        execution_protocol_module,
        "_apply_event",
        original_apply_event,
    )
    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    assert load_execution_protocol_state(context, "agent").phase is (
        ProtocolPhase.FINALIZE
    )
    submitted = record_candidate_final(context, "agent")
    assert submitted is not None
    assert submitted.decision.action is ControllerAction.STOP_INCOMPLETE
    assert submitted.state.review_pending is False
    assert submitted.state.terminal_incomplete is True


def test_convergence_latch_persistence_failure_preserves_pending_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context("convergence-latch-persistence-failure")
    _exhaust_candidate_diagnostics(context)

    def reject(call_id: str):
        return mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": "unmodeled_after_quota"},
                    tool_call_id=call_id,
                    agent_name="agent",
                )
            ],
        )

    assert reject("first-rejection") is not None
    before = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert before["candidate_exhausted_rejection_count"] == 1

    second = reject("second-rejection")
    assert second is not None
    pending = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert pending["candidate_exhausted_rejection_count"] == 2
    assert pending["candidate_tool_free_latched"] is True
    assert load_execution_protocol_state(context, "agent").phase is not (
        ProtocolPhase.FINALIZE
    )

    original_apply_event = execution_protocol_module._apply_event

    def fail_finalization(context_arg, agent_id, event):
        if event.kind is EventKind.CONVERGENCE_EXHAUSTED:
            return ProtocolTransition(
                state=load_execution_protocol_state(context_arg, agent_id),
                decision=ControllerDecision(
                    action=ControllerAction.CONTINUE,
                    reason=DecisionReason.PERSISTENCE_ERROR,
                ),
            )
        return original_apply_event(context_arg, agent_id, event)

    monkeypatch.setattr(
        execution_protocol_module,
        "_apply_event",
        fail_finalization,
    )
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")
    after = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert after["candidate_exhausted_rejection_count"] == 2
    assert after["candidate_rejection_high_water"] != before[
        "candidate_rejection_high_water"
    ]
    assert after["candidate_tool_free_latched"] is True
    assert load_execution_protocol_state(context, "agent").phase is not (
        ProtocolPhase.FINALIZE
    )

    monkeypatch.setattr(
        execution_protocol_module,
        "_apply_event",
        original_apply_event,
    )
    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    assert load_execution_protocol_state(context, "agent").phase is (
        ProtocolPhase.FINALIZE
    )


def test_candidate_advance_wins_cross_provider_rejection_race(tmp_path) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context("candidate-advance-rejection-race")
    target = tmp_path / "candidate.txt"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(ArtifactRequirement("candidate", str(target)),),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    _exhaust_candidate_diagnostics(context)
    assert mutation_gate_interception(
        context,
        [
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                params={"code": "unmodeled_first_attempt"},
                tool_call_id="first-rejection",
                agent_name="agent",
            )
        ],
    ) is not None
    # Provider A has an admitted declared revision in flight. Providers B/C
    # may concurrently consume the remaining rejected-batch runway, but no
    # pre-Tool path is allowed to enter FINALIZE before A projects its result.
    assert mutation_gate_interception(
        context,
        [
            ActionModel(
                tool_name="filesystem",
                action_name="write_file",
                params={"path": str(target), "content": "revised"},
                tool_call_id="in-flight-declared-revision",
                agent_name="agent",
            )
        ],
    ) is None

    barrier = Barrier(2)
    new_fingerprint = semantic_fingerprint("cross-provider-revision")

    def project_revision_result():
        barrier.wait(timeout=5)
        return record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=12,
                candidate_present=True,
                candidate_advanced=True,
                delivery_progress_advanced=True,
                public_candidate_mutated=True,
                public_delivery_fingerprint=new_fingerprint,
                workspace_mutated=True,
            ),
        )

    def intercept_other_provider():
        barrier.wait(timeout=5)
        return mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": "cat unrelated-helper.log"},
                    tool_call_id="cross-provider-rejection",
                    agent_name="agent",
                )
            ],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        projection = executor.submit(project_revision_result)
        interception = executor.submit(intercept_other_provider)
        projection.result(timeout=10)
        interception.result(timeout=10)

    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_fingerprint"] == new_fingerprint
    assert gate["candidate_exhausted_rejection_count"] == 0
    assert gate["candidate_tool_free_latched"] is False
    assert not execution_protocol_requires_tool_free_finalization(context, "agent")
    assert load_execution_protocol_state(context, "agent").phase is not (
        ProtocolPhase.FINALIZE
    )


def test_candidate_rejection_high_water_prevents_aba_refill() -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context("candidate-rejection-aba")
    candidate_a = _exhaust_candidate_diagnostics(context)

    def reject(call_id: str):
        return mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": "unmodeled_after_quota"},
                    tool_call_id=call_id,
                    agent_name="agent",
                )
            ],
        )

    assert reject("candidate-a-first-rejection") is not None
    gate_a = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate_a["candidate_exhausted_rejection_count"] == 1
    rejection_high_water = gate_a["candidate_rejection_high_water"]

    candidate_b = semantic_fingerprint("candidate-b")
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=10,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_b,
        ),
    )
    gate_b = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate_b["candidate_exhausted_rejection_count"] == 0
    assert gate_b["candidate_rejection_high_water"] == rejection_high_water

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=11,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_a,
        ),
    )
    returned_a = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert returned_a["candidate_diagnostic_read_count"] == 3
    assert returned_a["candidate_exhausted_rejection_count"] == 1
    assert returned_a["candidate_tool_free_latched"] is False

    assert reject("candidate-a-second-rejection") is not None
    assert execution_protocol_requires_tool_free_finalization(context, "agent")
    final_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert final_gate["candidate_exhausted_rejection_count"] == 2
    assert final_gate["candidate_tool_free_latched"] is True


def test_candidate_change_resets_diagnostic_reads_via_gate_projection() -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context("candidate-change-resets-diagnostics")
    first_candidate = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        first_candidate,
        marker="candidate-a",
        step=3,
    )
    first_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat candidate-a-diagnostic.log"},
        tool_call_id="candidate-a-diagnostic",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [first_read]) is None
    assert context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )["candidate_diagnostic_read_count"] == 1

    second_candidate = semantic_fingerprint("diagnostic-candidate-b")
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=4,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=second_candidate,
        ),
    )
    changed_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert changed_gate["candidate_fingerprint"] == second_candidate
    assert changed_gate["candidate_diagnostic_read_count"] == 0
    assert changed_gate["repair_authorization"] is None

    _record_failed_candidate_validation(
        context,
        second_candidate,
        marker="candidate-b",
        step=5,
    )
    refreshed_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert refreshed_gate["candidate_diagnostic_read_count"] == 0

    reads = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat candidate-b-diagnostic-{index}.log"},
            tool_call_id=f"candidate-b-diagnostic-{index}",
            agent_name="agent",
        )
        for index in range(1, 5)
    ]
    assert mutation_gate_interception(context, [reads[0]]) is None
    assert mutation_gate_interception(context, [reads[1]]) is None
    assert mutation_gate_interception(context, [reads[2]]) is None
    blocked_fourth = mutation_gate_interception(context, [reads[3]])
    assert blocked_fourth is not None
    assert blocked_fourth["tool_call_ids"] == ["candidate-b-diagnostic-4"]
    assert blocked_fourth["candidate_diagnostic_read_count"] == 3


def test_candidate_diagnostic_high_water_prevents_aba_refill() -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context("candidate-diagnostic-aba")
    candidate_a = _activate_validate_repair_convergence(context)

    def read(candidate: str, index: int) -> ActionModel:
        return ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat {candidate}-diagnostic-{index}.log"},
            tool_call_id=f"{candidate}-diagnostic-{index}",
            agent_name="agent",
        )

    for index in range(1, 4):
        assert mutation_gate_interception(context, [read("a", index)]) is None
    gate_a = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate_a["candidate_diagnostic_read_count"] == 3
    mask_a = int(gate_a["candidate_diagnostic_high_water"], 16)

    candidate_b = semantic_fingerprint("candidate-diagnostic-b")
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=3,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_b,
        ),
    )
    assert context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )["candidate_diagnostic_read_count"] == 0
    for index in range(1, 4):
        assert mutation_gate_interception(context, [read("b", index)]) is None
    gate_b = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate_b["candidate_diagnostic_read_count"] == 3
    mask_b = int(gate_b["candidate_diagnostic_high_water"], 16)
    assert mask_b & mask_a == mask_a

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=4,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_a,
        ),
    )
    returned_a = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert returned_a["candidate_diagnostic_read_count"] == 3
    assert int(returned_a["candidate_diagnostic_high_water"], 16) == mask_b
    blocked_a = mutation_gate_interception(context, [read("a", 4)])
    assert blocked_a is not None
    assert blocked_a["tool_call_ids"] == ["a-diagnostic-4"]


@pytest.mark.parametrize(
    "unresolved_fingerprint",
    (None, "not-a-canonical-fingerprint"),
    ids=("missing", "noncanonical"),
)
def test_advanced_candidate_without_fingerprint_fails_closed_then_recovers(
    unresolved_fingerprint,
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context(f"unresolved-advanced-candidate-{unresolved_fingerprint}")
    first_candidate = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        first_candidate,
        marker="unresolved-candidate-a",
        step=3,
    )
    first_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat unresolved-candidate-a.log"},
        tool_call_id="unresolved-candidate-a-diagnostic",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [first_read]) is None
    assert context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )["candidate_diagnostic_read_count"] == 1

    advanced_state = {
        "current_agent_step": 4,
        "candidate_present": True,
        "candidate_advanced": True,
        "delivery_progress_advanced": True,
        "public_candidate_mutated": True,
    }
    if unresolved_fingerprint is not None:
        advanced_state["public_delivery_fingerprint"] = unresolved_fingerprint
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(**advanced_state),
    )
    unresolved_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert unresolved_gate["candidate_fingerprint"] is None
    assert unresolved_gate["candidate_binding_unresolved"] is True
    assert unresolved_gate["repair_authorization"] is None
    assert unresolved_gate["candidate_diagnostic_read_count"] == 3
    blocked = mutation_gate_interception(context, [first_read])
    assert blocked is not None
    assert blocked["tool_call_ids"] == ["unresolved-candidate-a-diagnostic"]

    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=5,
            candidate_present=True,
            public_candidate_mutated=True,
        ),
    )
    still_unresolved = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert still_unresolved["candidate_fingerprint"] is None
    assert still_unresolved["candidate_binding_unresolved"] is True
    assert still_unresolved["candidate_diagnostic_read_count"] == 3

    second_candidate = semantic_fingerprint("resolved-candidate-b")
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=6,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=second_candidate,
        ),
    )
    resolved_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert resolved_gate["candidate_fingerprint"] == second_candidate
    assert resolved_gate["candidate_binding_unresolved"] is False
    assert resolved_gate["candidate_diagnostic_read_count"] == 0
    assert resolved_gate["repair_authorization"] is None

    _record_failed_candidate_validation(
        context,
        second_candidate,
        marker="resolved-candidate-b",
        step=7,
    )
    reads = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat resolved-candidate-b-{index}.log"},
            tool_call_id=f"resolved-candidate-b-diagnostic-{index}",
            agent_name="agent",
        )
        for index in (1, 2, 3)
    ]
    assert mutation_gate_interception(context, [reads[0]]) is None
    assert mutation_gate_interception(context, [reads[1]]) is None
    assert mutation_gate_interception(context, [reads[2]]) is None
    final_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert final_gate["candidate_diagnostic_read_count"] == 3


@pytest.mark.asyncio
async def test_mutation_gate_hook_enforces_candidate_diagnostic_budget() -> None:
    context = _context("hook-failed-validation-diagnostics")
    validation_code = "cat candidate.txt"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="validate-candidate",
                    argv=("sh", "-c", validation_code),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    _activate_validate_repair_convergence(context)
    diagnostics = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat hook-diagnostic-{index}.log"},
            tool_call_id=f"hook-diagnostic-{index}",
            agent_name="agent",
        )
        for index in range(1, 5)
    ]
    registered_validation = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="hook-registered-validation",
        agent_name="agent",
    )
    hook = MutationGatePreToolHook()

    assert await hook.exec(
        Message(category="tool_call", payload=[diagnostics[0]], sender="agent"),
        context,
    ) is None
    assert await hook.exec(
        Message(
            category="tool_call",
            payload=[registered_validation],
            sender="agent",
        ),
        context,
    ) is None
    assert context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )["candidate_diagnostic_read_count"] == 1
    assert await hook.exec(
        Message(category="tool_call", payload=[diagnostics[1]], sender="agent"),
        context,
    ) is None
    assert await hook.exec(
        Message(category="tool_call", payload=[diagnostics[2]], sender="agent"),
        context,
    ) is None

    intercepted = await hook.exec(
        Message(category="tool_call", payload=[diagnostics[3]], sender="agent"),
        context,
    )

    assert intercepted is not None
    interception = intercepted.headers["tool_interception"]
    assert interception["tool_call_ids"] == ["hook-diagnostic-4"]
    assert interception["block_all"] is False
    assert interception["source_receipt"]["candidate_diagnostic_read_count"] == 3


def test_concurrent_diagnostic_preflights_admit_at_most_three(monkeypatch) -> None:
    context = _context("concurrent-diagnostic-preflights")
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="concurrent",
        step=3,
    )
    transported_contexts = [context.deep_copy() for _ in range(4)]
    actions = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat concurrent-diagnostic-{index}.log"},
            tool_call_id=f"concurrent-diagnostic-{index}",
            agent_name="agent",
        )
        for index in range(4)
    ]
    start_barrier = Barrier(4)
    classification_barrier = Barrier(4)
    original_classifier = execution_protocol_module.actions_are_provably_read_only

    def synchronized_classifier(batch):
        classified = original_classifier(batch)
        try:
            classification_barrier.wait(timeout=0.5)
        except BrokenBarrierError:
            pass
        return classified

    monkeypatch.setattr(
        execution_protocol_module,
        "actions_are_provably_read_only",
        synchronized_classifier,
    )

    def preflight(index):
        start_barrier.wait(timeout=2)
        return mutation_gate_interception(
            transported_contexts[index], [actions[index]]
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(preflight, range(4)))

    assert sum(result is None for result in results) == 3
    blocked = [result for result in results if result is not None]
    assert len(blocked) == 1
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_diagnostic_read_count"] == 3


def test_gate_projection_cannot_overwrite_concurrent_diagnostic_count(
    monkeypatch,
) -> None:
    context = _context("projection-diagnostic-serialization")
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="projection-race",
        step=3,
    )
    projection_read = Event()
    release_projection = Event()
    interception_started = Event()
    interception_finished = Event()
    projection_thread_ids: set[int] = set()
    original_read = execution_protocol_module._read_runtime_value
    paused = False

    def pausing_read(runtime_context, agent_id, key):
        nonlocal paused
        value = original_read(runtime_context, agent_id, key)
        if (
            get_ident() in projection_thread_ids
            and key == "execution_protocol_mutation_gate"
            and not paused
        ):
            paused = True
            projection_read.set()
            assert release_projection.wait(timeout=2)
        return value

    monkeypatch.setattr(
        execution_protocol_module,
        "_read_runtime_value",
        pausing_read,
    )
    first_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat projection-race-one.log"},
        tool_call_id="projection-race-diagnostic-one",
        agent_name="agent",
    )

    def project_gate():
        projection_thread_ids.add(get_ident())
        return record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=4,
                candidate_present=True,
                public_candidate_mutated=True,
                public_delivery_fingerprint=candidate_fingerprint,
            ),
        )

    def intercept_first_read():
        interception_started.set()
        result = mutation_gate_interception(context, [first_read])
        interception_finished.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        projection_future = executor.submit(project_gate)
        assert projection_read.wait(timeout=2)
        interception_future = executor.submit(intercept_first_read)
        assert interception_started.wait(timeout=2)
        assert not interception_finished.wait(timeout=0.1)
        release_projection.set()
        projection_future.result(timeout=2)
        assert interception_future.result(timeout=2) is None

    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_diagnostic_read_count"] == 1

    second_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat projection-race-two.log"},
        tool_call_id="projection-race-diagnostic-two",
        agent_name="agent",
    )
    third_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat projection-race-three.log"},
        tool_call_id="projection-race-diagnostic-three",
        agent_name="agent",
    )
    fourth_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat projection-race-four.log"},
        tool_call_id="projection-race-diagnostic-four",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [second_read]) is None
    assert mutation_gate_interception(context, [third_read]) is None
    blocked_fourth = mutation_gate_interception(context, [fourth_read])
    assert blocked_fourth is not None
    assert blocked_fourth["tool_call_ids"] == [
        "projection-race-diagnostic-four"
    ]
    final_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert final_gate["candidate_diagnostic_read_count"] == 3


def test_candidate_change_apply_to_projection_gap_uses_new_candidate_quota(
    monkeypatch,
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context("candidate-change-apply-projection-gap")
    first_candidate = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        first_candidate,
        marker="candidate-gap-a",
        step=3,
    )
    second_candidate = semantic_fingerprint("candidate-gap-b")
    projection_entry = Event()
    release_projection = Event()
    interception_started = Event()
    interception_finished = Event()
    original_update_gate = execution_protocol_module._update_mutation_gate

    def pausing_update_gate(*args, **kwargs):
        projection_entry.set()
        assert release_projection.wait(timeout=2)
        return original_update_gate(*args, **kwargs)

    monkeypatch.setattr(
        execution_protocol_module,
        "_update_mutation_gate",
        pausing_update_gate,
    )
    stale_candidate_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat stale-candidate-a-diagnostic.log"},
        tool_call_id="stale-candidate-a-diagnostic",
        agent_name="agent",
    )

    def project_candidate_change():
        return record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=4,
                candidate_present=True,
                candidate_advanced=True,
                delivery_progress_advanced=True,
                public_candidate_mutated=True,
                public_delivery_fingerprint=second_candidate,
            ),
        )

    def intercept_stale_candidate_read():
        interception_started.set()
        result = mutation_gate_interception(context, [stale_candidate_read])
        interception_finished.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        projection_future = executor.submit(project_candidate_change)
        assert projection_entry.wait(timeout=2)
        interception_future = executor.submit(intercept_stale_candidate_read)
        assert interception_started.wait(timeout=2)
        assert not interception_finished.wait(timeout=0.1)
        release_projection.set()
        projection_future.result(timeout=2)
        interception = interception_future.result(timeout=2)

    assert interception is None
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_fingerprint"] == second_candidate
    assert gate["repair_authorization"] is None
    assert gate["candidate_diagnostic_read_count"] == 1


def test_mutation_gate_transaction_allows_nested_registry_updates() -> None:
    context = _context("nested-mutation-gate-transaction")
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="nested",
        step=3,
    )
    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat nested-transaction.log"},
        tool_call_id="nested-transaction-diagnostic",
        agent_name="agent",
    )

    with context.task_runtime_state_transaction():
        assert mutation_gate_interception(context, [read]) is None
        context.update_task_runtime_state(
            "agent",
            "nested-transaction-proof",
            lambda current: int(current or 0) + 1,
        )

    assert context.read_task_runtime_state(
        "agent", "nested-transaction-proof"
    ) == 1
    assert context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )["candidate_diagnostic_read_count"] == 1


def test_diagnostic_read_is_single_per_mixed_batch_and_block_all_rolls_back(
    tmp_path,
) -> None:
    context = _context("failed-validation-diagnostic-batches")
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="batch",
        step=3,
    )
    first_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat first.log"},
        tool_call_id="batch-diagnostic-one",
        agent_name="agent",
    )
    second_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat second.log"},
        tool_call_id="batch-diagnostic-two",
        agent_name="agent",
    )
    helper_mutation = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": str(tmp_path / "helper.txt"), "content": "helper"},
        tool_call_id="batch-helper-mutation",
        agent_name="agent",
    )
    unknown = ActionModel(
        tool_name="custom",
        action_name="opaque",
        params={"value": "unknown"},
        tool_call_id="batch-unknown",
        agent_name="agent",
    )
    mixed = mutation_gate_interception(
        context,
        [first_read, second_read, helper_mutation, unknown],
    )
    assert mixed is not None
    assert mixed["tool_call_ids"] == [
        "batch-diagnostic-two",
        "batch-helper-mutation",
        "batch-unknown",
    ]
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_diagnostic_read_count"] == 1
    high_water_after_mixed = gate["candidate_diagnostic_high_water"]

    block_all_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat block-all.log"},
        tool_call_id="block-all-diagnostic",
        agent_name="agent",
    )
    missing_call_id = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat missing-id.log"},
        tool_call_id=None,
        agent_name="agent",
    )
    blocked_batch = mutation_gate_interception(
        context, [block_all_read, missing_call_id]
    )
    assert blocked_batch is not None
    assert blocked_batch["block_all"] is True
    assert blocked_batch["tool_call_ids"] == ["block-all-diagnostic"]
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_diagnostic_read_count"] == 1
    assert gate["candidate_diagnostic_high_water"] == high_water_after_mixed


def test_duplicate_call_ids_block_all_before_any_admission(tmp_path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("candidate")
    context = _context("duplicate-call-id-preflight")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(target),
                "display_path": "result.txt",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="duplicate-call-id",
        step=3,
    )
    before = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    duplicate_call_id = "duplicate-batch-call"
    diagnostic = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat duplicate-diagnostic.log"},
        tool_call_id=duplicate_call_id,
        agent_name="agent",
    )
    declared_revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf revised > {target}"},
        tool_call_id=duplicate_call_id,
        agent_name="agent",
    )
    helper_mutation = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": str(tmp_path / "helper.txt"), "content": "helper"},
        tool_call_id=duplicate_call_id,
        agent_name="agent",
    )

    blocked = mutation_gate_interception(
        context,
        [diagnostic, declared_revision, helper_mutation],
    )

    assert blocked is not None
    assert blocked["block_all"] is True
    assert blocked["tool_call_ids"] == [duplicate_call_id]
    after = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert after["candidate_diagnostic_read_count"] == 0
    assert after["candidate_diagnostic_high_water"] == before[
        "candidate_diagnostic_high_water"
    ]
    assert after["repair_authorization"]["failure_evidence_hash"] == (
        before["repair_authorization"]["failure_evidence_hash"]
    )
    assert after["declared_mutation_attempt_high_water"] == (
        before["declared_mutation_attempt_high_water"]
    )


def test_candidate_diagnostics_do_not_require_typed_repair_authorization() -> None:
    context = _context("diagnostic-read-without-failure")
    _activate_validate_repair_convergence(context)
    reads = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat candidate-{index}.txt"},
            tool_call_id=f"diagnostic-without-authorization-{index}",
            agent_name="agent",
        )
        for index in range(1, 5)
    ]

    assert mutation_gate_interception(context, [reads[0]]) is None
    assert mutation_gate_interception(context, [reads[1]]) is None
    assert mutation_gate_interception(context, [reads[2]]) is None
    blocked = mutation_gate_interception(context, [reads[3]])

    assert blocked is not None
    assert blocked["tool_call_ids"] == ["diagnostic-without-authorization-4"]
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["repair_authorization"] is None
    assert gate["candidate_diagnostic_read_count"] == 3

    repair = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "candidate.txt", "content": "repair"},
        tool_call_id="repair-without-typed-authorization",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "repair the candidate",
            "next_action": "write the exact candidate repair",
            "next_action_tool": "filesystem__write_file",
            "next_action_arguments": json.dumps(repair.params),
            "verification_plan": "validate after repair",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "produce_candidate",
            "delivery_rationale": "attempt a repair without typed evidence",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "diagnostic-candidate",
        },
    ) is not None
    assert bind_pending_next_action_call(context, "agent", [repair]) is True

    blocked_repair = mutation_gate_interception(context, [repair])

    assert blocked_repair is not None
    assert blocked_repair["tool_call_ids"] == [
        "repair-without-typed-authorization"
    ]


@pytest.mark.parametrize(
    "stored_count",
    (None, "1", -1, False, 0.0, True, 3, 4),
)
def test_legacy_or_malformed_diagnostic_count_fails_closed(stored_count) -> None:
    context = _context(f"malformed-diagnostic-count-{stored_count!r}")
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker="malformed",
        step=3,
    )
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    if stored_count is None:
        gate.pop("candidate_diagnostic_read_count")
    else:
        gate["candidate_diagnostic_read_count"] = stored_count
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )
    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat diagnostic.log"},
        tool_call_id="malformed-count-diagnostic",
        agent_name="agent",
    )

    blocked = mutation_gate_interception(context, [read])

    assert blocked is not None
    assert blocked["tool_call_ids"] == ["malformed-count-diagnostic"]
    normalized = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert normalized["candidate_diagnostic_read_count"] == 3
    assert normalized["repair_authorization"] is not None
    assert normalized["validation_window_open"] is True


@pytest.mark.parametrize("stored_high_water", (None, "not-a-mask"))
def test_malformed_candidate_diagnostic_high_water_is_scope_fail_closed(
    stored_high_water,
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context(f"malformed-candidate-high-water-{stored_high_water}")
    candidate_a = _activate_validate_repair_convergence(context)
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    if stored_high_water is None:
        gate.pop("candidate_diagnostic_high_water")
    else:
        gate["candidate_diagnostic_high_water"] = stored_high_water
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )
    read_a = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat malformed-high-water-a.log"},
        tool_call_id="malformed-high-water-a",
        agent_name="agent",
    )
    blocked_a = mutation_gate_interception(context, [read_a])
    assert blocked_a is not None
    failed_closed = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert failed_closed["candidate_diagnostic_read_count"] == 3
    assert failed_closed["candidate_diagnostic_high_water"] == "f" * 128

    candidate_b = semantic_fingerprint("malformed-high-water-candidate-b")
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=3,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_b,
        ),
    )
    gate_b = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate_b["candidate_diagnostic_read_count"] == 3
    read_b = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat malformed-high-water-b.log"},
        tool_call_id="malformed-high-water-b",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [read_b]) is not None

    context.advance_context_lifecycle(LifecycleAction.NEW_TASK)
    fresh_candidate = _activate_validate_repair_convergence(context)
    assert fresh_candidate == candidate_a
    fresh_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert fresh_gate["candidate_diagnostic_read_count"] == 0
    assert fresh_gate["candidate_diagnostic_high_water"] == "0" * 128


def test_candidate_diagnostic_count_high_water_mismatch_fails_closed() -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    context = _context("candidate-diagnostic-count-mask-mismatch")
    candidate_a = _activate_validate_repair_convergence(context)
    first_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat mismatch-a.log"},
        tool_call_id="mismatch-a-diagnostic",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [first_read]) is None
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    gate["candidate_diagnostic_read_count"] = 0
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "0 diagnostic call(s) remain" in guidance
    blocked = mutation_gate_interception(context, [first_read])
    assert blocked is not None
    failed_closed = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert failed_closed["candidate_diagnostic_read_count"] == 3

    candidate_b = semantic_fingerprint("count-mask-mismatch-b")
    record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            current_agent_step=3,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_b,
        ),
    )
    gate_b = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate_b["candidate_diagnostic_read_count"] == 0
    assert gate_b["candidate_fingerprint"] != candidate_a


@pytest.mark.parametrize(
    "malformation",
    (
        "missing_schema",
        "legacy_schema",
        "missing_source",
        "missing_failure_hash",
        "noncanonical_failure_hash",
        "unknown_source",
        "unexpected_legacy_field",
    ),
)
def test_malformed_repair_authorization_cannot_diagnose_or_repair(
    malformation,
) -> None:
    context = _context(f"malformed-repair-authorization-{malformation}")
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    _record_failed_candidate_validation(
        context,
        candidate_fingerprint,
        marker=malformation,
        step=3,
    )
    repair = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "candidate.txt", "content": "repair"},
        tool_call_id=f"malformed-auth-repair-{malformation}",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "repair the current candidate",
            "next_action": "apply the exact evidence-backed repair",
            "next_action_tool": "filesystem__write_file",
            "next_action_arguments": json.dumps(repair.params),
            "verification_plan": "rerun validation",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "produce_candidate",
            "delivery_rationale": "repair the observed validation failure",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "diagnostic-candidate",
        },
    ) is not None
    assert bind_pending_next_action_call(context, "agent", [repair]) is True
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    authorization = gate["repair_authorization"]
    if malformation == "missing_schema":
        authorization.pop("schema_version")
    elif malformation == "legacy_schema":
        authorization["schema_version"] = "aworld.repair-authorization/v1"
    elif malformation == "missing_source":
        authorization.pop("source")
    elif malformation == "missing_failure_hash":
        authorization.pop("failure_evidence_hash")
    elif malformation == "noncanonical_failure_hash":
        authorization["failure_evidence_hash"] = "sha256:NOT-CANONICAL"
    elif malformation == "unknown_source":
        authorization["source"] = "untyped_repair"
    else:
        authorization["diagnostic_read_count"] = 0
    context.write_task_runtime_state(
        "agent", "execution_protocol_mutation_gate", gate
    )
    diagnostic = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat malformed-auth-diagnostic.log"},
        tool_call_id=f"malformed-auth-diagnostic-{malformation}",
        agent_name="agent",
    )

    blocked = mutation_gate_interception(context, [diagnostic, repair])

    assert blocked is not None
    assert blocked["tool_call_ids"] == [
        f"malformed-auth-repair-{malformation}",
    ]
    sanitized = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert sanitized["repair_authorization"] is None
    assert sanitized["validation_window_open"] is False
    assert sanitized["candidate_diagnostic_read_count"] == 1


def test_model_review_repair_mints_typed_source_and_diagnostic_window() -> None:
    context = _context("typed-model-review-repair")
    candidate_fingerprint = _activate_validate_repair_convergence(context)
    pre_review_read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat pre-review-diagnostic.log"},
        tool_call_id="pre-review-diagnostic",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [pre_review_read]) is None
    review = record_candidate_final(
        context,
        "agent",
        actions=[ActionModel(agent_name="agent", policy_info="candidate")],
        review_boundary_available=True,
    )
    assert review is not None
    assert load_execution_protocol_state(context, "agent").review_pending is True

    repair = record_review_repair_decision(
        context,
        "agent",
        {"decision": "repair", "reason": "typed review found a concrete gap"},
    )

    assert repair is not None
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    authorization = gate["repair_authorization"]
    assert authorization["schema_version"] == "aworld.repair-authorization/v2"
    assert authorization["source"] == "model_review_repair"
    assert authorization["candidate_fingerprint"] == candidate_fingerprint
    assert gate["candidate_diagnostic_read_count"] == 1
    read = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat typed-review-diagnostic.log"},
        tool_call_id="typed-review-diagnostic",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [read]) is None
    admitted_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert admitted_gate["candidate_diagnostic_read_count"] == 2
    assert admitted_gate["repair_authorization"]["source"] == (
        "model_review_repair"
    )


def test_stale_concurrent_model_review_cannot_mint_after_submission(
    monkeypatch,
) -> None:
    context = _context("stale-concurrent-model-review")
    _activate_validate_repair_convergence(context)
    review = record_candidate_final(
        context,
        "agent",
        actions=[ActionModel(agent_name="agent", policy_info="candidate")],
        review_boundary_available=True,
    )
    assert review is not None
    assert review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW

    # Exercise the compatibility path without transaction support so the
    # final REQUEST_REPAIR check is independently proven fail-closed.
    monkeypatch.setattr(context, "task_runtime_state_transaction", None)
    original_load = execution_protocol_module.ExecutionProtocolStore.load
    repair_thread_ids: set[int] = set()
    stale_precheck = Barrier(2)
    review_consumed = Barrier(2)
    paused = False

    def pausing_load(store):
        nonlocal paused
        state = original_load(store)
        if (
            get_ident() in repair_thread_ids
            and state.review_pending
            and not paused
        ):
            paused = True
            stale_precheck.wait(timeout=2)
            review_consumed.wait(timeout=2)
        return state

    monkeypatch.setattr(
        execution_protocol_module.ExecutionProtocolStore,
        "load",
        pausing_load,
    )

    def stale_repair_decision():
        repair_thread_ids.add(get_ident())
        return record_review_repair_decision(
            context,
            "agent",
            {"decision": "repair", "reason": "stale concurrent repair"},
        )

    def submit_review():
        stale_precheck.wait(timeout=2)
        submitted = record_candidate_final(
            context,
            "agent",
            actions=[ActionModel(agent_name="agent", policy_info="candidate")],
        )
        review_consumed.wait(timeout=2)
        return submitted

    with ThreadPoolExecutor(max_workers=2) as executor:
        stale_future = executor.submit(stale_repair_decision)
        submit_future = executor.submit(submit_review)
        submitted = submit_future.result(timeout=2)
        stale = stale_future.result(timeout=2)

    assert submitted is not None
    assert submitted.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert stale is not None
    assert stale.decision.action is not ControllerAction.REQUEST_REPAIR
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["repair_authorization"] is None
    unauthorized_repair = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "candidate.txt", "content": "stale repair"},
        tool_call_id="stale-model-review-repair",
        agent_name="agent",
    )
    blocked = mutation_gate_interception(context, [unauthorized_repair])
    assert blocked is not None
    assert blocked["tool_call_ids"] == ["stale-model-review-repair"]


def test_acceptance_critic_repair_without_pending_review_cannot_mint(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "true")
    context = _context("acceptance-repair-without-review")
    _activate_acceptance_repair_convergence(context)
    decision = {
        "decision": "repair",
        "highest_risk_counterexample": "the contract still fails",
        "hypothesis_id": "acceptance-repair",
        "reason": "typed critic requests a repair",
    }

    transition, _, accepted = record_acceptance_critic_decision(
        context, "agent", decision
    )

    assert accepted is False
    assert transition is not None
    assert transition.decision.action is not ControllerAction.REQUEST_REPAIR
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["repair_authorization"] is None
    unauthorized_repair = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "candidate.txt", "content": "unauthorized repair"},
        tool_call_id="no-pending-review-repair",
        agent_name="agent",
    )
    blocked = mutation_gate_interception(context, [unauthorized_repair])
    assert blocked is not None
    assert blocked["tool_call_ids"] == ["no-pending-review-repair"]


def test_acceptance_repair_mint_is_atomic_with_candidate_change(
    monkeypatch,
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "true")
    context = _context("acceptance-repair-candidate-gap")
    _activate_acceptance_repair_convergence(context)
    review = record_candidate_final(
        context,
        "agent",
        actions=[ActionModel(agent_name="agent", policy_info="candidate-a")],
        review_boundary_available=True,
    )
    assert review is not None
    assert review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    decision = {
        "decision": "repair",
        "highest_risk_counterexample": "the contract still fails",
        "hypothesis_id": "acceptance-repair",
        "reason": "typed critic requests a repair",
    }
    mint_entry = Event()
    release_mint = Event()
    projection_started = Event()
    projection_finished = Event()
    original_mint = execution_protocol_module._mint_review_repair_authorization

    def pausing_mint(*args, **kwargs):
        mint_entry.set()
        assert release_mint.wait(timeout=2)
        return original_mint(*args, **kwargs)

    monkeypatch.setattr(
        execution_protocol_module,
        "_mint_review_repair_authorization",
        pausing_mint,
    )
    second_candidate = semantic_fingerprint("acceptance-candidate-b")

    def decide_repair():
        return record_acceptance_critic_decision(context, "agent", decision)

    def project_candidate_change():
        projection_started.set()
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                current_agent_step=4,
                candidate_present=True,
                candidate_advanced=True,
                delivery_progress_advanced=True,
                public_candidate_mutated=True,
                public_delivery_fingerprint=second_candidate,
            ),
        )
        projection_finished.set()
        return transition

    with ThreadPoolExecutor(max_workers=2) as executor:
        decision_future = executor.submit(decide_repair)
        assert mint_entry.wait(timeout=2)
        projection_future = executor.submit(project_candidate_change)
        assert projection_started.wait(timeout=2)
        assert not projection_finished.wait(timeout=0.1)
        release_mint.set()
        decision_transition, _, _ = decision_future.result(timeout=2)
        projection_future.result(timeout=2)

    assert decision_transition is not None
    assert decision_transition.decision.action is ControllerAction.REQUEST_REPAIR
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["candidate_fingerprint"] == second_candidate
    assert gate["repair_authorization"] is None
    assert gate["candidate_diagnostic_read_count"] == 0


def test_consumed_acceptance_review_cannot_refill_repair_authorization(
    monkeypatch,
) -> None:
    from aworld.core.context.compiler import semantic_fingerprint

    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "true")
    context = _context("acceptance-repair-consumed-review")
    _activate_acceptance_repair_convergence(context)
    review = record_candidate_final(
        context,
        "agent",
        actions=[ActionModel(agent_name="agent", policy_info="candidate")],
        review_boundary_available=True,
    )
    assert review is not None
    assert review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    decision = {
        "decision": "repair",
        "highest_risk_counterexample": "the contract still fails",
        "hypothesis_id": "acceptance-repair",
        "reason": "typed critic requests a repair",
    }

    first, _, _ = record_acceptance_critic_decision(context, "agent", decision)

    assert first is not None
    assert first.decision.action is ControllerAction.REQUEST_REPAIR
    gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert gate["repair_authorization"]["source"] == (
        "acceptance_critic_repair"
    )
    diagnostic = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat acceptance-review-diagnostic.log"},
        tool_call_id="acceptance-review-diagnostic",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [diagnostic]) is None

    repair = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        params={"path": "candidate.txt", "content": "critic repair"},
        tool_call_id="acceptance-review-repair",
        agent_name="agent",
    )
    assert record_model_plan_update(
        context,
        "agent",
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "repair the critic finding",
            "next_action": "apply the exact critic repair",
            "next_action_tool": "filesystem__write_file",
            "next_action_arguments": json.dumps(repair.params),
            "verification_plan": "rerun the registered acceptance check",
            "completion_assessment": "candidate_ready",
            "delivery_intent": "produce_candidate",
            "delivery_rationale": "typed acceptance review supports the repair",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "diagnostic-candidate",
        },
    ) is not None
    assert bind_pending_next_action_call(context, "agent", [repair]) is True
    assert mutation_gate_interception(context, [repair]) is None
    assert context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )["repair_authorization"] is None

    # Distinct critic-state evidence would produce a fresh evidence hash if the
    # already-consumed review decision were incorrectly allowed to mint again.
    context.write_task_runtime_state(
        "agent",
        execution_protocol_module.EXECUTION_PROTOCOL_CRITIC_KEY,
        {
            "candidate_hash": semantic_fingerprint("repeat-critic-candidate"),
            "evidence_hash": semantic_fingerprint("repeat-critic-evidence"),
        },
    )
    repeated, _, _ = record_acceptance_critic_decision(
        context, "agent", {**decision, "reason": "repeat repair request"}
    )

    assert repeated is not None
    assert repeated.decision.action is not ControllerAction.REQUEST_REPAIR
    repeated_gate = context.read_task_runtime_state(
        "agent", "execution_protocol_mutation_gate"
    )
    assert repeated_gate["repair_authorization"] is None
    blocked_repeat = mutation_gate_interception(context, [repair])
    assert blocked_repeat is not None
    assert blocked_repeat["tool_call_ids"] == ["acceptance-review-repair"]


def test_stale_repair_authorization_does_not_block_novel_declared_revision(
    tmp_path,
) -> None:
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
    _set_deadline_progress(context, 0.40)
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
    assert receipt is None
    repeated = mutation_gate_interception(context, [repair])
    assert repeated is not None
    assert repeated["tool_call_ids"] == ["stale-repair"]


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
                        "sandbox_observation": _sandbox_receipt(
                            validation,
                            0,
                            effect="read_only",
                            workspace_mutated=False,
                        )
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


def test_short_profile_with_context_contract_requires_review(tmp_path) -> None:
    context = _context("short-public-contract")
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
            independent_acceptance_enabled=False,
        ),
    )
    record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "short",
            "confidence": 0.9,
            "milestone_count": 1,
            "expected_tool_actions": 1,
            "verification_required": True,
        },
    )

    transition = record_candidate_final(
        context,
        "agent",
        review_boundary_available=True,
    )

    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert transition.state.public_deliverable_declared is True
    assert transition.state.phase is ProtocolPhase.REVIEW


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
    assert unchanged.state.phase is ProtocolPhase.REVIEW
    assert unchanged.state.terminal_incomplete is True
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["phase"] == "review"
    assert project_execution_protocol_telemetry(telemetry) is not None


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


@pytest.mark.asyncio
async def test_mutation_gate_reports_bounded_path_free_rejection_reasons(
    tmp_path,
) -> None:
    target = tmp_path / "out.txt"
    context = _context("bounded-call-rejection-reasons")
    context.workspace_path = str(tmp_path)
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "result",
                "path": str(target),
                "display_path": "out.txt",
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
    actions = [
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={
                "code": (
                    "awk '{print $1}' input.txt | sort > "
                    f"{target}"
                )
            },
            tool_call_id="unknown-pipeline",
            agent_name="agent",
        ),
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"printf helper > {tmp_path / 'helper.txt'}"},
            tool_call_id="helper-write",
            agent_name="agent",
        ),
        ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": "cat README.md"},
            tool_call_id="unregistered-read",
            agent_name="agent",
        ),
    ]

    receipt = mutation_gate_interception(context, actions)

    assert receipt is not None
    assert receipt["call_rejections"] == [
        {"tool_call_id": "unknown-pipeline", "reason": "effect_unknown"},
        {"tool_call_id": "helper-write", "reason": "undeclared_helper"},
        {
            "tool_call_id": "unregistered-read",
            "reason": "validation_unregistered",
        },
    ]
    assert receipt["call_rejections_truncated"] == 0
    serialized = json.dumps(receipt, sort_keys=True)
    assert "awk '{print $1}'" not in serialized
    assert str(target) not in serialized
    assert str(tmp_path / "helper.txt") not in serialized

    hook_result = await MutationGatePreToolHook().exec(
        Message(category="tool_call", payload=[actions[0]], sender="agent"),
        context,
    )
    assert hook_result is not None
    message = hook_result.headers["tool_interception"]["message"]
    assert "effect_unknown" in message
    assert "pipelines" in message
    assert "python3 -I" in message
    assert "not sufficient" in message

    many_unknown = [
        ActionModel(
            tool_name="custom",
            action_name="opaque",
            params={"private_path": f"/private/secret-{index}"},
            tool_call_id=f"unknown-{index}",
            agent_name="agent",
        )
        for index in range(40)
    ]
    bounded = mutation_gate_interception(context, many_unknown)
    assert bounded is not None
    assert len(bounded["call_rejections"]) == 32
    assert bounded["call_rejections_truncated"] == 8
    assert "/private/secret" not in json.dumps(bounded, sort_keys=True)

    unsafe_call_id = "/private/secret\n" + "x" * 4096
    sanitized, truncated = execution_protocol_module._serialized_call_rejections(
        [(unsafe_call_id, MutationGateRejectionReason.EFFECT_UNKNOWN)]
    )
    assert sanitized == [{"tool_call_id": None, "reason": "effect_unknown"}]
    assert truncated == 0
    assert unsafe_call_id not in json.dumps(sanitized)

def test_validate_gate_distinguishes_replay_and_exhausted_diagnostic(
    tmp_path,
) -> None:
    target = tmp_path / "candidate.txt"
    target.write_text("candidate")
    validation_code = f"cat {target}"
    context = _context("validate-rejection-reasons")
    context.workspace_path = str(tmp_path)
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(ArtifactRequirement("candidate", str(target)),),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="validate-candidate",
                    argv=("sh", "-c", validation_code),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    _activate_validate_repair_convergence(context)
    revision = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": f"printf revised > {target}"},
        tool_call_id="declared-revision",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [revision]) is None
    replay = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params=dict(revision.params),
        tool_call_id="replayed-revision",
        agent_name="agent",
    )
    replay_receipt = mutation_gate_interception(context, [replay])
    assert replay_receipt is not None
    assert replay_receipt["call_rejections"] == [
        {"tool_call_id": "replayed-revision", "reason": "replayed_revision"}
    ]

    for index in range(3):
        diagnostic = ActionModel(
            tool_name="terminal",
            action_name="run_code",
            params={"code": f"cat diagnostic-{index}.log"},
            tool_call_id=f"diagnostic-{index}",
            agent_name="agent",
        )
        assert mutation_gate_interception(context, [diagnostic]) is None

    registered = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": validation_code},
        tool_call_id="registered-validation",
        agent_name="agent",
    )
    assert mutation_gate_interception(context, [registered]) is None
    exhausted = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat diagnostic-exhausted.log"},
        tool_call_id="diagnostic-exhausted",
        agent_name="agent",
    )
    exhausted_receipt = mutation_gate_interception(context, [exhausted])
    assert exhausted_receipt is not None
    assert exhausted_receipt["call_rejections"] == [
        {
            "tool_call_id": "diagnostic-exhausted",
            "reason": "diagnostic_quota_exhausted",
        }
    ]
    assert {
        MutationGateRejectionReason.EFFECT_UNKNOWN.value,
        MutationGateRejectionReason.DIAGNOSTIC_QUOTA_EXHAUSTED.value,
        MutationGateRejectionReason.UNDECLARED_HELPER.value,
        MutationGateRejectionReason.REPLAYED_REVISION.value,
        MutationGateRejectionReason.VALIDATION_UNREGISTERED.value,
    }.issubset({reason.value for reason in MutationGateRejectionReason})
