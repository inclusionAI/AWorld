from __future__ import annotations

import hashlib

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.execution_protocol import (
    ControllerAction,
    ExecutionProtocolPolicy,
    ProtocolMode,
)
from aworld.core.task import Task
from aworld.runners.execution_protocol import (
    build_execution_protocol_telemetry,
    configure_execution_protocol,
    load_execution_protocol_state,
    record_model_decision_attempt_failure,
    record_model_execution_profile,
    record_tool_protocol_event,
)
from aworld.runners.post_tool_progress import record_semantic_tool_progress
from aworld.sandbox.tool_observation import classify_tool_effect


def _context(task_id: str) -> Context:
    context = Context(task_id=task_id)
    context.set_task(Task(id=task_id, input="produce /app/out.txt", timeout=600))
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
            delivery_debt_observation_threshold=99,
        ),
    )
    assert record_model_execution_profile(
        context,
        "agent",
        {
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 3,
            "expected_tool_actions": 20,
            "verification_required": True,
            "workspace_mutation_required": True,
        },
    ) is not None
    return context


def _set_deadline_progress(context: Context, consumed_fraction: float) -> None:
    task = context.get_task()
    remaining = float(task.timeout) * (1.0 - consumed_fraction)
    task.remaining_seconds = lambda: remaining


def _semantic_state(
    *,
    progress_count: int,
    stagnation_count: int,
    step: int,
    reset_count: int = 0,
):
    return {
        "repetition_count": 0,
        "low_information_gain_count": 0,
        "no_goal_progress_count": 0,
        "goal_progress_observable": True,
        "goal_progress": False,
        "current_agent_step": step,
        "operation_hash": f"sha256:operation-{step}",
        "result_hash": f"sha256:result-{step}",
        "public_deliverable_declared": True,
        "missing_public_deliverable_count": 1,
        "candidate_present": False,
        "analysis_progress_advanced": stagnation_count == 0,
        "analysis_progress_count": progress_count,
        "analysis_stagnation_count": stagnation_count,
        "analysis_runway_reset_count": reset_count,
    }


def test_observed_known_write_is_intermediate_analysis_progress() -> None:
    context = _context("known-intermediate-write")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="analysis-write",
        params={"code": "python3 analysis.py"},
    )
    effect = classify_tool_effect(action)
    semantic = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id=action.tool_call_id,
                    content="rendered a new projection",
                    success=True,
                    metadata={
                        "sandbox_observation": {
                            "schema_version": "aworld.sandbox-tool-observation/v1",
                            "tool_call_id": action.tool_call_id,
                            "canonical_tool": effect.identity,
                            "operation_hash": effect.operation_hash,
                            "workspace_generation": 1,
                            "effect": "unknown",
                            "workspace_mutated": True,
                        }
                    },
                )
            ]
        ),
    )

    assert semantic["workspace_mutated"] is True
    assert semantic["workspace_mutation_observed"] is True
    assert semantic["intermediate_artifact_advanced"] is True
    assert semantic["analysis_progress_advanced"] is True
    assert semantic["analysis_progress_count"] == 1
    assert semantic["analysis_stagnation_count"] == 0
    # Intermediate evidence buys runway; it is not promoted to completion or
    # public-deliverable progress.
    assert semantic["goal_progress"] is False
    assert semantic["delivery_progress_advanced"] is False


def test_only_novel_successful_read_resets_analysis_runway() -> None:
    context = _context("novel-analysis-read")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="analysis-read",
        params={"code": "cat /app/text.gcode"},
    )
    effect = classify_tool_effect(action)

    def observe(content: str):
        return record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action],
            observation=Observation(
                action_result=[
                    ActionResult(
                        tool_call_id=action.tool_call_id,
                        content=content,
                        success=True,
                        metadata={
                            "sandbox_observation": {
                                "schema_version": (
                                    "aworld.sandbox-tool-observation/v1"
                                ),
                                "tool_call_id": action.tool_call_id,
                                "canonical_tool": effect.identity,
                                "operation_hash": effect.operation_hash,
                                "workspace_generation": 0,
                                "effect": "read_only",
                                "workspace_mutated": False,
                                "content_sha256": "sha256:"
                                + hashlib.sha256(content.encode()).hexdigest(),
                            }
                        },
                    )
                ]
            ),
        )

    first = observe("bounded new geometry")
    repeated = observe("bounded new geometry")

    assert first["new_information_observed"] is True
    assert first["analysis_information_advanced"] is True
    assert first["analysis_stagnation_count"] == 0
    assert repeated["new_information_observed"] is False
    assert repeated["analysis_information_advanced"] is False
    assert repeated["analysis_stagnation_count"] == 1

    for index in range(9):
        assert observe(f"distinct geometry {index}")[
            "analysis_information_advanced"
        ] is True
    replay_after_window = observe("bounded new geometry")
    assert replay_after_window["analysis_information_advanced"] is False


def test_analysis_progress_defers_40_percent_gate_for_three_stagnant_observations() -> None:
    context = _context("bounded-analysis-runway")
    _set_deadline_progress(context, 0.40)

    progressed = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(progress_count=1, stagnation_count=0, step=1),
    )
    assert progressed.state.convergence_constraint_active is False
    telemetry = build_execution_protocol_telemetry(context, "agent")
    assert telemetry["analysis_runway_open"] is True
    assert telemetry["analysis_progress_count"] == 1
    assert telemetry["analysis_stagnation_count"] == 0
    assert telemetry["analysis_runway_reset_count"] == 0

    for step, stagnation_count in ((2, 1), (3, 2)):
        transition = record_tool_protocol_event(
            context,
            "agent",
            _semantic_state(
                progress_count=1,
                stagnation_count=stagnation_count,
                step=step,
            ),
        )
        assert transition.state.convergence_constraint_active is False

    exhausted = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(progress_count=1, stagnation_count=3, step=4),
    )
    assert exhausted.state.convergence_constraint_active is True
    assert exhausted.state.convergence_stage.value == "produce_candidate"


def test_analysis_runway_has_unconditional_65_percent_ceiling() -> None:
    context = _context("analysis-runway-ceiling")
    _set_deadline_progress(context, 0.65)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(progress_count=8, stagnation_count=0, step=8),
    )

    assert transition.state.convergence_constraint_active is True
    assert transition.state.convergence_stage.value == "produce_candidate"


def test_analysis_runway_has_bounded_progress_reset_budget() -> None:
    context = _context("analysis-runway-reset-budget")
    _set_deadline_progress(context, 0.50)

    transition = record_tool_protocol_event(
        context,
        "agent",
        _semantic_state(
            progress_count=9,
            stagnation_count=0,
            reset_count=9,
            step=9,
        ),
    )

    assert transition.state.convergence_constraint_active is True
    assert transition.state.convergence_stage.value == "produce_candidate"


def test_recent_analysis_progress_defers_unapplied_replan_hard_gate() -> None:
    context = _context("analysis-runway-unapplied-replans")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            activation_event_threshold=1,
            model_activation_min_tool_actions=1,
            repetition_threshold=1,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=1,
            delivery_debt_observation_threshold=99,
        ),
    )
    _set_deadline_progress(context, 0.40)

    for step in (1, 2):
        semantic = _semantic_state(
            progress_count=step,
            stagnation_count=0,
            step=step,
        )
        semantic["repetition_count"] = 1
        context.write_task_runtime_state("agent", "semantic_progress", semantic)
        transition = record_tool_protocol_event(context, "agent", semantic)
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert record_model_decision_attempt_failure(
            context,
            "agent",
            boundary="replan",
        )
        assert not record_model_decision_attempt_failure(
            context,
            "agent",
            boundary="replan",
        )

    assert load_execution_protocol_state(
        context,
        "agent",
    ).convergence_constraint_active is False

    exhausted = _semantic_state(
        progress_count=2,
        stagnation_count=3,
        step=3,
    )
    context.write_task_runtime_state("agent", "semantic_progress", exhausted)
    record_tool_protocol_event(context, "agent", exhausted)

    assert load_execution_protocol_state(
        context,
        "agent",
    ).convergence_constraint_active is True
