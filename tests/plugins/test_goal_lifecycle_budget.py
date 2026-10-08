from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from aworld_cli.builtin_plugins.goal_session.common import parse_goal_args
from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    apply_turn_outcome,
    handle_event,
    new_goal_contract_state,
)
from aworld_cli.executors.local import LocalAgentExecutor, _GoalContinuation


def test_goal_has_no_default_turn_or_time_cap():
    state = new_goal_contract_state("finish the work")
    for _ in range(5000):
        state, again = apply_turn_outcome(
            state, {"semantic_status": "incomplete", "recoverable": True}
        )
        assert again
    assert state["max_turns"] is None
    assert "deadline_epoch_seconds" not in state


def test_explicit_goal_attempt_limit_is_preserved_on_continuation():
    state = new_goal_contract_state("work", max_turns=2)
    state, again = apply_turn_outcome(
        state, {"semantic_status": "budget_exhausted", "recoverable": True}
    )
    assert again and state["max_turns"] == 2
    state, again = apply_turn_outcome(
        state, {"semantic_status": "incomplete", "recoverable": True}
    )
    assert not again and state["status"] == "budget_limited"


def test_incomplete_promise_does_not_complete_goal_and_old_deadline_is_ignored():
    state = new_goal_contract_state("work", completion_promise="DONE")
    state, again = apply_turn_outcome(
        state,
        {
            "semantic_status": "incomplete",
            "recoverable": True,
            "final_answer": "<promise>DONE</promise>",
        },
    )
    assert again and not state["completion_promise_satisfied"]
    state["deadline_epoch_seconds"] = 1
    state, again = apply_turn_outcome(
        state,
        {"semantic_status": "succeeded", "final_answer": "<promise>DONE</promise>"},
    )
    assert not again and state["status"] == "complete"
    assert "deadline_epoch_seconds" not in state


def test_goal_without_promise_stops_on_typed_success():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work"), {"semantic_status": "succeeded"}
    )
    assert not again and state["status"] == "complete"
    assert state["last_attempt_receipt"]["disposition"] == "complete"
    assert state["last_attempt_receipt"]["acceptance_satisfied"] is True


def test_goal_persists_structured_verification_receipt_for_next_attempt():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work", verification_commands=["pytest -q"]),
        {
            "task_id": "attempt-1",
            "semantic_status": "incomplete",
            "completion_reason": "completion_contract_unsatisfied",
            "recoverable": True,
            "final_answer": "I implemented the change.",
        },
    )

    assert again is True
    assert state["last_attempt_receipt"] == {
        "schema_version": "aworld.goal.attempt-receipt/v1",
        "attempt": 1,
        "task_id": "attempt-1",
        "semantic_status": "incomplete",
        "completion_reason": "completion_contract_unsatisfied",
        "recoverable": True,
        "completion_claimed": True,
        "verification_required": True,
        "verification_passed": False,
        "acceptance_reason_codes": [],
        "acceptance_satisfied": False,
        "disposition": "continue",
        "decision_reason": "semantic_incomplete",
    }

    prompt = __import__(
        "aworld_cli.builtin_plugins.goal_session.hooks.task_completed",
        fromlist=["build_goal_context_prompt"],
    ).build_goal_context_prompt(state)
    assert "Last attempt receipt:" in prompt
    assert "Verification: failed" in prompt
    assert "Decision: continue (semantic_incomplete)" in prompt


def test_goal_receipt_carries_bounded_typed_acceptance_reasons():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work", verification_commands=["pytest -q"]),
        {
            "semantic_status": "incomplete",
            "completion_reason": "completion_contract_unsatisfied",
            "completion_assessment": {
                "mode": "enforce",
                "status": "repair_required",
                "reason_codes": [
                    "self_check_failed",
                    "required_artifact_missing",
                    *[f"extra-{index}" for index in range(10)],
                ],
            },
        },
    )

    assert again is True
    receipt = state["last_attempt_receipt"]
    assert receipt["acceptance_reason_codes"] == [
        "self_check_failed",
        "required_artifact_missing",
        "extra-0",
        "extra-1",
        "extra-2",
        "extra-3",
        "extra-4",
        "extra-5",
    ]
    prompt = __import__(
        "aworld_cli.builtin_plugins.goal_session.hooks.task_completed",
        fromlist=["build_goal_context_prompt"],
    ).build_goal_context_prompt(state)
    assert (
        "Unsatisfied evidence: self_check_failed, required_artifact_missing" in prompt
    )
    assert "extra-6" not in prompt


def test_goal_attempt_limit_is_persisted_as_a_non_successful_halt_receipt():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work", max_turns=1),
        {"semantic_status": "incomplete", "completion_reason": "needs_more_work"},
    )

    assert again is False
    assert state["status"] == "budget_limited"
    assert state["last_attempt_receipt"]["disposition"] == "limit_reached"
    assert state["last_attempt_receipt"]["acceptance_satisfied"] is False


def test_untyped_text_promise_is_not_completion_evidence():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work", completion_promise="DONE"),
        {"final_answer": "<promise>DONE</promise>", "task_status": "completed"},
    )
    assert again and state["status"] == "active"


def test_no_progress_does_not_stop_attempts_or_invent_a_budget():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work"),
        {
            "semantic_status": "incomplete",
            "completion_reason": "no_new_evidence",
            "recoverable": False,
        },
    )
    assert again and state["status"] == "active"
    assert "deadline_epoch_seconds" not in state and state["max_turns"] is None


def test_pause_wins_race_with_old_completion_hook():
    stale = new_goal_contract_state("work")
    writes = []
    handle = SimpleNamespace(
        read=lambda: {**stale, "active": False, "status": "paused"}, write=writes.append
    )
    assert handle_event(
        {"semantic_status": "incomplete"}, {**stale, "__plugin_state__": handle}
    ) == {"action": "allow"}
    assert writes == []


@pytest.mark.parametrize(
    "args",
    [
        "work --max-turns 0",
        "work --timeout-seconds 0",
        "work --timeout-seconds nan",
        "work --deadline-epoch-seconds inf",
    ],
)
def test_invalid_attempt_limits_and_unsupported_time_options_rejected(args):
    with pytest.raises(ValueError):
        parse_goal_args(args)


@pytest.mark.asyncio
async def test_cli_continues_thousands_of_goal_turns_without_recursion_or_time_limit(
    monkeypatch,
):
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    seen = []

    async def turn(
        message, requested_skill_names=None, _previous_goal_context=None, **_kwargs
    ):
        seen.append(message)
        return _GoalContinuation("continue") if len(seen) < 1500 else "done"

    executor._chat_turn = turn
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1")
    assert await executor.chat("work") == "done"
    assert len(seen) == 1500


@pytest.mark.asyncio
async def test_internal_continuation_keeps_original_request_and_logical_scope():
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor._session_mode = "direct"
    calls = []

    async def turn(message, **kwargs):
        calls.append((message, kwargs))
        if len(calls) == 1:
            return _GoalContinuation(
                "structured repair prompt",
                None,
                kwargs["_origin_user_input"],
                kwargs["_logical_task_state"],
                {"attempt": 1},
                "direct_acceptance",
            )
        return "done"

    executor._chat_turn = turn

    assert await executor.chat("original public request") == "done"
    assert [message for message, _ in calls] == [
        "original public request",
        "structured repair prompt",
    ]
    assert calls[1][1]["_origin_user_input"] == "original public request"
    assert (
        calls[0][1]["_logical_task_state"]["workspace_id"]
        == calls[1][1]["_logical_task_state"]["workspace_id"]
    )
    assert calls[1][1]["_direct_acceptance_state"] == {"attempt": 1}


def test_direct_long_horizon_acceptance_continues_without_replaying_prompt():
    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import ExecutionProtocolPolicy
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import (
        configure_execution_protocol,
        record_model_plan_update,
        record_tool_protocol_event,
    )

    context = Context(task_id="segment-1")
    task = Task(
        id="segment-1",
        input="build the requested project",
        context=context,
        timeout=600,
        completion_reserve_seconds=30,
    )
    context.set_task(task)
    configure_execution_protocol(
        context,
        "root-agent",
        ExecutionProtocolPolicy(mode="guide"),
    )
    assert (
        record_model_plan_update(
            context,
            "root-agent",
            {
                "decision": "continue",
                "horizon": "long",
                "milestone": "complete the requested project",
                "next_action": "continue implementation",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": '{"command":"continue implementation"}',
                "verification_plan": "inspect and test the candidate",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "implementation still needs bounded work",
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
        "root-agent",
        {
            "current_agent_step": 1,
            "completion_advanced": True,
            "goal_progress": True,
        },
    )

    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    logical_state = new_goal_contract_state(
        "build the requested project", source="direct_invocation"
    )
    response = TaskResponse(
        success=True,
        answer="",
        semantic_status="succeeded",
    )
    event = {
        "task_id": task.id,
        "semantic_status": "succeeded",
        "recoverable": None,
        "final_answer": "",
        "completion_assessment": None,
    }

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer="",
        event=event,
        origin_user_input="build the requested project",
        logical_task_state=logical_state,
        acceptance_state=None,
    )

    assert isinstance(continuation, _GoalContinuation)
    assert continuation.source == "direct_acceptance"
    assert continuation.prompt != "build the requested project"
    assert "Last attempt receipt:" in continuation.prompt
    assert (
        continuation.acceptance_state["workspace_id"] == logical_state["workspace_id"]
    )
    assert continuation.acceptance_state["max_turns"] == 2
    assert response.execution_protocol["armed"] is True
    assert response.execution_protocol["acceptance_continuation_count"] == 1


def test_direct_acceptance_never_reopens_protocol_finalization() -> None:
    from dataclasses import replace

    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import (
        ExecutionProtocolPolicy,
        ExecutionProtocolStore,
        ProtocolPhase,
    )
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import (
        configure_execution_protocol,
        project_execution_protocol_telemetry,
    )

    context = Context(task_id="finalized-segment")
    task = Task(
        id="finalized-segment",
        input="finish the requested work",
        context=context,
        timeout=600,
    )
    context.set_task(task)
    policy = ExecutionProtocolPolicy(mode="guide")
    configure_execution_protocol(context, "root-agent", policy)
    store = ExecutionProtocolStore(context, "root-agent", policy)
    store.save(
        replace(
            store.load(),
            long_horizon_armed=True,
            phase=ProtocolPhase.FINALIZE,
            finalization_entered=True,
        )
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    response = TaskResponse(
        success=False,
        answer="verified work remains incomplete",
        semantic_status="incomplete",
        recoverable=True,
    )

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer=response.answer,
        event={
            "task_id": task.id,
            "semantic_status": "incomplete",
            "recoverable": True,
            "final_answer": response.answer,
        },
        origin_user_input="finish the requested work",
        logical_task_state=new_goal_contract_state("finish the requested work"),
        acceptance_state=None,
    )

    assert continuation is None
    assert response.execution_protocol["finalization_entered"] is True
    assert response.execution_protocol["acceptance_continuation_suppressed"] == (
        "protocol_finalization"
    )
    projected = project_execution_protocol_telemetry(response.execution_protocol)
    assert projected is not None
    assert projected["acceptance_continuation_suppressed"] == (
        "protocol_finalization"
    )
    assert response.semantic_status == "incomplete"
    assert response.recoverable is True


def test_observe_mode_finalization_does_not_suppress_direct_acceptance() -> None:
    from dataclasses import replace

    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import (
        ExecutionProtocolPolicy,
        ExecutionProtocolStore,
        ProtocolPhase,
    )
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import configure_execution_protocol

    context = Context(task_id="observed-finalized-segment")
    task = Task(
        id="observed-finalized-segment",
        input="finish the requested work",
        context=context,
        timeout=600,
    )
    context.set_task(task)
    policy = ExecutionProtocolPolicy(mode="observe")
    configure_execution_protocol(context, "root-agent", policy)
    store = ExecutionProtocolStore(context, "root-agent", policy)
    store.save(
        replace(
            store.load(),
            long_horizon_armed=True,
            phase=ProtocolPhase.FINALIZE,
            finalization_entered=True,
        )
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    response = TaskResponse(
        success=False,
        answer="observed work remains incomplete",
        semantic_status="incomplete",
        recoverable=True,
    )

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer=response.answer,
        event={
            "task_id": task.id,
            "semantic_status": "incomplete",
            "recoverable": True,
            "final_answer": response.answer,
        },
        origin_user_input="finish the requested work",
        logical_task_state=new_goal_contract_state("finish the requested work"),
        acceptance_state=None,
    )

    assert isinstance(continuation, _GoalContinuation)
    assert response.execution_protocol["acceptance_continuation_count"] == 1
    assert "acceptance_continuation_suppressed" not in response.execution_protocol


def test_direct_acceptance_never_reopens_active_protocol_convergence() -> None:
    from dataclasses import replace

    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import (
        ConvergenceStage,
        ExecutionProtocolPolicy,
        ExecutionProtocolStore,
    )
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import (
        configure_execution_protocol,
        project_execution_protocol_telemetry,
    )

    context = Context(task_id="converging-segment")
    task = Task(
        id="converging-segment",
        input="finish the requested work",
        context=context,
        timeout=600,
    )
    context.set_task(task)
    policy = ExecutionProtocolPolicy(mode="guide")
    configure_execution_protocol(context, "root-agent", policy)
    store = ExecutionProtocolStore(context, "root-agent", policy)
    store.save(
        replace(
            store.load(),
            long_horizon_armed=True,
            event_count=3,
            tool_observation_count=2,
            convergence_constraint_active=True,
            convergence_stage=ConvergenceStage.PRODUCE_CANDIDATE,
        )
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    response = TaskResponse(
        success=False,
        answer="candidate is still missing",
        semantic_status="incomplete",
        recoverable=True,
    )
    acceptance_state = new_goal_contract_state(
        "finish the requested work",
        max_turns=2,
        source="direct_long_horizon",
    )
    acceptance_state["turn_count"] = 2
    acceptance_state["protocol_telemetry"] = {
        "schema_version": "aworld.execution-protocol-telemetry/v2",
        "mode": "guide",
        "phase": "execute",
        "armed": True,
        "event_count": 7,
        "tool_observation_count": 5,
        "initial_decision_unavailable_count": 2,
        "action_alignment_match_count": 4,
        "mutation_gate_activation_count": 3,
        "consecutive_unapplied_replans": 2,
        "implicit_acceptance_created": True,
        "acceptance_attempt": 1,
        "acceptance_continuation_count": 1,
    }

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer=response.answer,
        event={
            "task_id": task.id,
            "semantic_status": "incomplete",
            "recoverable": True,
            "final_answer": response.answer,
        },
        origin_user_input="finish the requested work",
        logical_task_state=new_goal_contract_state("finish the requested work"),
        acceptance_state=acceptance_state,
    )

    assert continuation is None
    assert response.execution_protocol["convergence_constraint_active"] is True
    assert response.execution_protocol["event_count"] == 10
    assert response.execution_protocol["tool_observation_count"] == 7
    assert response.execution_protocol["initial_decision_unavailable_count"] == 2
    assert response.execution_protocol["action_alignment_match_count"] == 4
    assert response.execution_protocol["mutation_gate_activation_count"] == 3
    assert response.execution_protocol["consecutive_unapplied_replans"] == 2
    assert response.execution_protocol["implicit_acceptance_created"] is True
    assert response.execution_protocol["acceptance_attempt"] == 1
    assert response.execution_protocol["acceptance_continuation_count"] == 1
    assert response.execution_protocol["acceptance_continuation_suppressed"] == (
        "protocol_convergence"
    )
    projected = project_execution_protocol_telemetry(response.execution_protocol)
    assert projected is not None
    assert projected["acceptance_continuation_suppressed"] == (
        "protocol_convergence"
    )
    assert response.semantic_status == "incomplete"
    assert response.recoverable is True


def test_direct_acceptance_first_segment_convergence_is_terminal() -> None:
    from dataclasses import replace

    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import (
        ConvergenceStage,
        ExecutionProtocolPolicy,
        ExecutionProtocolStore,
    )
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import configure_execution_protocol

    context = Context(task_id="first-converging-segment")
    task = Task(
        id="first-converging-segment",
        input="finish the requested work",
        context=context,
        timeout=600,
    )
    context.set_task(task)
    policy = ExecutionProtocolPolicy(mode="guide")
    configure_execution_protocol(context, "root-agent", policy)
    store = ExecutionProtocolStore(context, "root-agent", policy)
    store.save(
        replace(
            store.load(),
            long_horizon_armed=True,
            convergence_constraint_active=True,
            convergence_stage=ConvergenceStage.PRODUCE_CANDIDATE,
        )
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    response = TaskResponse(
        success=False,
        answer="candidate is still missing",
        semantic_status="incomplete",
        recoverable=True,
    )

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer=response.answer,
        event={
            "task_id": task.id,
            "semantic_status": "incomplete",
            "recoverable": True,
            "final_answer": response.answer,
        },
        origin_user_input="finish the requested work",
        logical_task_state=new_goal_contract_state("finish the requested work"),
        acceptance_state=None,
    )

    assert continuation is None
    assert response.execution_protocol["acceptance_continuation_suppressed"] == (
        "protocol_convergence"
    )
    assert "implicit_acceptance_created" not in response.execution_protocol


def test_observe_mode_convergence_does_not_suppress_direct_acceptance() -> None:
    from dataclasses import replace

    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import (
        ConvergenceStage,
        ExecutionProtocolPolicy,
        ExecutionProtocolStore,
    )
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import configure_execution_protocol

    context = Context(task_id="observed-converging-segment")
    task = Task(
        id="observed-converging-segment",
        input="finish the requested work",
        context=context,
        timeout=600,
    )
    context.set_task(task)
    policy = ExecutionProtocolPolicy(mode="observe")
    configure_execution_protocol(context, "root-agent", policy)
    store = ExecutionProtocolStore(context, "root-agent", policy)
    store.save(
        replace(
            store.load(),
            long_horizon_armed=True,
            convergence_constraint_active=True,
            convergence_stage=ConvergenceStage.PRODUCE_CANDIDATE,
        )
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    response = TaskResponse(
        success=False,
        answer="observed candidate gap",
        semantic_status="incomplete",
        recoverable=True,
    )

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer=response.answer,
        event={
            "task_id": task.id,
            "semantic_status": "incomplete",
            "recoverable": True,
            "final_answer": response.answer,
        },
        origin_user_input="finish the requested work",
        logical_task_state=new_goal_contract_state("finish the requested work"),
        acceptance_state=None,
    )

    assert isinstance(continuation, _GoalContinuation)
    assert response.execution_protocol["acceptance_continuation_count"] == 1
    assert "acceptance_continuation_suppressed" not in response.execution_protocol


def test_direct_acceptance_records_repair_success_without_another_segment():
    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import ExecutionProtocolPolicy
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import configure_execution_protocol

    context = Context(task_id="segment-2")
    task = Task(id="segment-2", input="continue", context=context, timeout=600)
    context.set_task(task)
    configure_execution_protocol(
        context,
        "root-agent",
        ExecutionProtocolPolicy(mode="guide"),
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    state = new_goal_contract_state(
        "build the requested project",
        max_turns=2,
        source="direct_long_horizon",
    )
    state["turn_count"] = 2
    state["protocol_telemetry"] = {
        "schema_version": "aworld.execution-protocol-telemetry/v1",
        "armed": True,
    }
    response = TaskResponse(
        success=True,
        answer="verified result",
        semantic_status="succeeded",
    )

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer="verified result",
        event={
            "task_id": task.id,
            "semantic_status": "succeeded",
            "final_answer": "verified result",
        },
        origin_user_input="build the requested project",
        logical_task_state=state,
        acceptance_state=state,
    )

    assert continuation is None
    assert response.execution_protocol["acceptance_disposition"] == "complete"
    assert response.execution_protocol["acceptance_satisfied"] is True


def test_direct_acceptance_bypasses_unarmed_short_task():
    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import ExecutionProtocolPolicy
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import configure_execution_protocol

    context = Context(task_id="short")
    task = Task(id="short", input="answer directly", context=context)
    context.set_task(task)
    configure_execution_protocol(
        context,
        "root-agent",
        ExecutionProtocolPolicy(mode="guide"),
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    response = TaskResponse(
        success=True,
        answer="",
        semantic_status="succeeded",
    )

    assert (
        executor._direct_acceptance_continuation(
            task=task,
            response=response,
            answer="",
            event={"task_id": task.id, "semantic_status": "succeeded"},
            origin_user_input="answer directly",
            logical_task_state=new_goal_contract_state("answer directly"),
            acceptance_state=None,
        )
        is None
    )
    assert response.execution_protocol["armed"] is False
    assert "implicit_acceptance_created" not in response.execution_protocol


def test_direct_acceptance_respects_deadline_reserve():
    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import ExecutionProtocolPolicy
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import (
        configure_execution_protocol,
        record_model_plan_update,
        record_tool_protocol_event,
    )

    context = Context(task_id="deadline")
    task = Task(
        id="deadline",
        input="long work",
        context=context,
        timeout=120,
        completion_reserve_seconds=120,
    )
    context.set_task(task)
    configure_execution_protocol(
        context,
        "root-agent",
        ExecutionProtocolPolicy(mode="guide"),
    )
    assert (
        record_model_plan_update(
            context,
            "root-agent",
            {
                "decision": "continue",
                "horizon": "long",
                "milestone": "complete long work",
                "next_action": "continue bounded execution",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": '{"command":"continue bounded execution"}',
                "verification_plan": "inspect the final candidate",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "the candidate still needs bounded work",
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
        "root-agent",
        {"current_agent_step": 1, "completion_advanced": True},
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    response = TaskResponse(
        success=False,
        answer="partial",
        semantic_status="incomplete",
        recoverable=True,
    )

    continuation = executor._direct_acceptance_continuation(
        task=task,
        response=response,
        answer="partial",
        event={
            "task_id": task.id,
            "semantic_status": "incomplete",
            "recoverable": True,
            "final_answer": "partial",
        },
        origin_user_input="long work",
        logical_task_state=new_goal_contract_state("long work"),
        acceptance_state=None,
    )

    assert continuation is None
    assert response.execution_protocol["acceptance_continuation_suppressed"] == (
        "deadline_reserve"
    )
    assert response.success is False
    assert response.semantic_status == "budget_exhausted"
    assert response.failure_origin == "task"


def test_explicit_goal_owned_segment_cannot_start_implicit_acceptance():
    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import ExecutionProtocolPolicy
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import (
        configure_execution_protocol,
        record_tool_protocol_event,
    )

    context = Context(task_id="explicit-limit")
    task = Task(id="explicit-limit", input="work", context=context)
    context.set_task(task)
    configure_execution_protocol(
        context,
        "root-agent",
        ExecutionProtocolPolicy(mode="guide", activation_event_threshold=1),
    )
    record_tool_protocol_event(
        context,
        "root-agent",
        {"current_agent_step": 1, "completion_advanced": True},
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )

    assert (
        executor._direct_acceptance_continuation(
            task=task,
            response=TaskResponse(
                success=False,
                answer="partial",
                semantic_status="incomplete",
                recoverable=True,
            ),
            answer="partial",
            event={"semantic_status": "incomplete", "recoverable": True},
            origin_user_input="work",
            logical_task_state=new_goal_contract_state("work"),
            acceptance_state=None,
            explicit_goal_owned_segment=True,
        )
        is None
    )


def test_direct_acceptance_limit_is_exported_as_unsatisfied():
    from aworld.core.context.base import Context
    from aworld.core.execution_protocol import ExecutionProtocolPolicy
    from aworld.core.task import Task, TaskResponse
    from aworld.runners.execution_protocol import configure_execution_protocol

    context = Context(task_id="repair-limit")
    task = Task(id="repair-limit", input="continue", context=context)
    context.set_task(task)
    configure_execution_protocol(
        context,
        "root-agent",
        ExecutionProtocolPolicy(mode="guide"),
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._session_mode = "direct"
    executor._base_runtime = None
    executor.swarm = SimpleNamespace(
        communicate_agent=SimpleNamespace(id=lambda: "root-agent")
    )
    state = new_goal_contract_state("work", max_turns=2)
    state["turn_count"] = 2
    state["protocol_telemetry"] = {
        "schema_version": "aworld.execution-protocol-telemetry/v1",
        "mode": "guide",
        "phase": "execute",
        "armed": True,
    }
    response = TaskResponse(
        success=False,
        answer="still partial",
        semantic_status="incomplete",
        recoverable=True,
    )

    assert (
        executor._direct_acceptance_continuation(
            task=task,
            response=response,
            answer="still partial",
            event={"semantic_status": "incomplete", "recoverable": True},
            origin_user_input="work",
            logical_task_state=state,
            acceptance_state=state,
        )
        is None
    )
    assert response.success is False
    assert response.semantic_status == "budget_exhausted"
    assert response.execution_protocol["acceptance_disposition"] == "limit_reached"


@pytest.mark.asyncio
async def test_later_segment_error_retains_prior_evidence_without_receipt_mismatch():
    from aworld.core.task import TaskResponse

    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor._session_mode = "direct"
    executor._run_plugin_task_hook = AsyncMock(return_value=[])
    calls = 0
    final_response = TaskResponse(
        id="segment-2",
        success=False,
        semantic_status="incomplete",
        llm_calls=[{"request_id": "call-2"}],
        trajectory=[{"meta": {"task_id": "segment-2"}, "action": {}}],
    )

    async def turn(_message, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            executor.last_task_response = TaskResponse(
                id="segment-1",
                success=False,
                semantic_status="incomplete",
                llm_calls=[{"request_id": "call-1"}],
                trajectory=[{"meta": {"task_id": "segment-1"}, "action": {}}],
            )
            return _GoalContinuation(
                "repair",
                None,
                kwargs["_origin_user_input"],
                kwargs["_logical_task_state"],
                {"attempt": 1},
                "direct_acceptance",
            )
        executor.last_task_response = final_response
        raise RuntimeError("second segment failed")

    executor._chat_turn = turn
    with pytest.raises(RuntimeError, match="second segment failed"):
        await executor.chat("work")

    assert [call["request_id"] for call in final_response.llm_calls] == [
        "call-1",
        "call-2",
    ]
    assert [item["meta"]["task_id"] for item in final_response.trajectory] == [
        "segment-2"
    ]
    assert final_response.execution_segments[0]["task_id"] == "segment-1"


@pytest.mark.asyncio
async def test_pre_run_failure_creates_typed_envelope_for_prior_evidence():
    from aworld.core.task import TaskResponse

    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor._session_mode = "direct"
    executor._run_plugin_task_hook = AsyncMock(return_value=[])
    calls = 0

    async def turn(_message, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            executor.last_task_response = TaskResponse(
                id="segment-1",
                success=False,
                semantic_status="incomplete",
                llm_calls=[{"request_id": "call-1"}],
                trajectory=[{"meta": {"task_id": "segment-1"}, "action": {}}],
            )
            return _GoalContinuation(
                "repair",
                None,
                kwargs["_origin_user_input"],
                kwargs["_logical_task_state"],
                {"attempt": 1},
                "direct_acceptance",
            )
        executor.last_task_response = None
        raise RuntimeError("context setup failed")

    executor._chat_turn = turn
    with pytest.raises(RuntimeError, match="context setup failed"):
        await executor.chat("work")

    response = executor.last_task_response
    assert response.failure_origin == "infrastructure"
    assert response.failure_code == "internal_segment_setup_error"
    assert response.llm_calls == [{"request_id": "call-1"}]
    assert response.execution_segments[0]["task_id"] == "segment-1"


@pytest.mark.asyncio
async def test_explicit_goal_continuations_do_not_build_unbounded_run_envelope():
    from aworld.core.task import TaskResponse

    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor._session_mode = "direct"
    calls = 0

    async def turn(_message, **kwargs):
        nonlocal calls
        calls += 1
        executor.last_task_response = TaskResponse(
            id=f"goal-{calls}",
            success=calls == 3,
            llm_calls=[{"request_id": f"call-{calls}"}],
            trajectory=[{"meta": {"task_id": f"goal-{calls}"}, "action": {}}],
        )
        if calls < 3:
            return _GoalContinuation(
                "continue goal",
                None,
                kwargs["_origin_user_input"],
                kwargs["_logical_task_state"],
                None,
                "goal_hook",
            )
        return "done"

    executor._chat_turn = turn
    assert await executor.chat("goal") == "done"
    assert executor.last_task_response.llm_calls == [{"request_id": "call-3"}]
    assert executor.last_task_response.execution_segments == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,maximum",
    [("paused", None), ("budget_limited", None), ("budget_limited", 5)],
)
async def test_goal_resume_keeps_work_scope_and_discards_old_time_limit(
    status, maximum
):
    from aworld_cli.builtin_plugins.goal_session.hooks.stop import GoalCommand
    from aworld_cli.core.command_system import CommandContext

    state = {
        **new_goal_contract_state("work", max_turns=maximum),
        "active": False,
        "status": status,
        "last_task_id": "prior-task",
        "last_task_epoch": 7,
        "deadline_epoch_seconds": 1,
    }

    def update(values):
        state.update(values)
        return dict(state)

    def write(values):
        state.clear()
        state.update(values)

    handle = SimpleNamespace(read=lambda: dict(state), update=update, write=write)
    command = object.__new__(GoalCommand)
    command.get_state_handle = lambda _: handle
    executor = SimpleNamespace()
    context = CommandContext(
        cwd="/tmp", user_args="resume", executor=executor, session_id="same-session"
    )
    assert await command.pre_execute(context) is None
    assert command.should_start_new_session(context) is False
    await command.get_prompt(context)
    assert state["status"] == "active" and "deadline_epoch_seconds" not in state
    assert executor._resume_context_checkpoint_once
    assert executor._resume_goal_work_scope_once == {
        "source_task_id": "prior-task",
        "source_task_epoch": 7,
    }


@pytest.mark.asyncio
async def test_user_pause_cancels_context_construction_without_a_timer(monkeypatch):
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor.session_id = "session"
    executor._run_plugin_task_hook = AsyncMock(return_value=[])
    entered = asyncio.Event()
    cancelled = []

    async def blocked_turn(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    executor._chat_turn = blocked_turn
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1")
    running = asyncio.create_task(executor.chat("work"))
    await entered.wait()
    assert not running.done()
    executor.request_goal_pause()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert cancelled == [True]
    assert executor._active_chat_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["complete", "paused", "budget_limited"])
async def test_ordinary_cli_work_does_not_inherit_inactive_goal_limits(
    monkeypatch, status
):
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = SimpleNamespace(
        build_plugin_hook_state=lambda *args: {
            "active": False,
            "status": status,
            "deadline_epoch_seconds": 1,
        }
    )

    async def turn(*args, **kwargs):
        assert "_lifetime" not in kwargs
        return "ordinary CLI work"

    executor._chat_turn = turn
    monkeypatch.delenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", raising=False)
    assert await executor.chat("new request") == "ordinary CLI work"


def test_goal_resume_maps_only_unique_configured_agent_names():
    executor = object.__new__(LocalAgentExecutor)
    executor.swarm = SimpleNamespace(
        agents={
            "new-a": SimpleNamespace(name=lambda: "worker", id=lambda: "new-a"),
            "new-b": SimpleNamespace(name=lambda: "duplicate", id=lambda: "new-b"),
            "new-c": SimpleNamespace(name=lambda: "duplicate", id=lambda: "new-c"),
        }
    )
    assert executor._goal_agent_ids() == {"worker": "new-a"}
