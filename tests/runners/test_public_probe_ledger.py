from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier, Event
from types import SimpleNamespace

import aworld.runners.execution_protocol as execution_protocol_module

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)
from aworld.core.execution_protocol import ExecutionProtocolPolicy, ProtocolMode
from aworld.core.task import Task
from aworld.runners.execution_protocol import (
    configure_execution_protocol,
    load_public_probe_receipts,
    record_model_plan_update,
    record_public_probe_observations,
    record_public_probe_plan,
    store_candidate_fallback,
)
from aworld.runners.post_tool_progress import record_semantic_tool_progress
from aworld.runners.post_tool_progress import semantic_progress_for_agent
from aworld.core.context.compiler import ADAPTIVE_WORK_STATE_KEY


def _context() -> Context:
    context = Context(task_id="public-probe")
    context.set_task(
        Task(id="public-probe", input="produce report.json and validate it")
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    return context


def _application_context() -> ApplicationContext:
    context = ApplicationContext(
        task_state=ApplicationTaskContextState(
            task_input=TaskInput(
                session_id="public-probe-session",
                task_id="public-probe-checkpoint",
                content="produce report.json and validate it",
            ),
            working_state=TaskWorkingState(messages=[], user_profiles=[], kv_store={}),
            task_output=TaskOutput(),
        )
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    return context


def _probe_value(**overrides):
    value = {
        "hypothesis_id": "report-parses",
        "highest_risk_counterexample": "report.json exists but is invalid JSON",
        "probe_kind": "counterexample",
    }
    value.update(overrides)
    return value


def _select_candidate(
    context: Context,
    candidate_id: str,
    *,
    next_command: str = "true",
) -> None:
    assert (
        record_model_plan_update(
            context,
            "agent",
            {
                "decision": "continue",
                "horizon": "long",
                "milestone": "validate the selected candidate",
                "next_action": "run the discriminating public probe",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": json.dumps({"command": next_command}),
                "verification_plan": "bind the observed probe to this candidate",
                "completion_assessment": "in_progress",
                "delivery_intent": "validate_candidate",
                "delivery_rationale": "the selected candidate needs a fresh probe",
                "assumptions": [],
                "retired_approaches": [],
                "evidence_refs": [],
                "selected_candidate_id": candidate_id,
            },
        )
        is not None
    )


def test_public_probe_receipt_binds_request_candidate_action_and_observation():
    context = _context()
    _select_candidate(
        context,
        "candidate-2",
        next_command="python -m json.tool report.json",
    )
    store_candidate_fallback(
        context,
        "agent",
        [ActionModel(agent_name="agent", policy_info="candidate response v1")],
    )
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="call-7",
        params={"command": "python -m json.tool report.json"},
    )

    assert record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-7",
        tool_identity="terminal:execute",
        arguments_projection=action.params,
        value=_probe_value(),
    )
    assert (
        record_public_probe_observations(
            context,
            "agent",
            actions=[action],
            result_projections=[
                {
                    "tool_call_id": "call-7",
                    "success": True,
                    "return_code": 0,
                    "content": "valid JSON",
                }
            ],
            artifact_after="sha256:artifact-v1",
        )
        == 1
    )

    receipts = load_public_probe_receipts(context, "agent")
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["authority"] == "agent_self_check"
    assert receipt["task_reward"] == "not_assessed"
    assert receipt["hypothesis_id"] == "report-parses"
    assert receipt["selected_candidate_id"] == "candidate-2"
    assert receipt["tool_execution_succeeded"] is True
    assert receipt["probe_assessment"] == "unassessed"
    assert receipt["candidate_current"] is True
    assert receipt["request_current"] is True
    assert receipt["artifact_current"] is False
    assert receipt["stale"] is True
    assert "candidate response v1" not in str(receipt)
    assert "produce report.json" not in str(receipt)


def test_public_probe_receipt_becomes_stale_when_candidate_changes():
    context = _context()
    _select_candidate(context, "candidate-1", next_command="pytest -q")
    store_candidate_fallback(
        context,
        "agent",
        [ActionModel(agent_name="agent", policy_info="candidate response v1")],
    )
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="call-8",
        params={"command": "pytest -q"},
    )
    assert record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-8",
        tool_identity="terminal:execute",
        arguments_projection=action.params,
        value=_probe_value(probe_kind="regression"),
    )
    assert (
        record_public_probe_observations(
            context,
            "agent",
            actions=[action],
            result_projections=[{"tool_call_id": "call-8", "success": True}],
            artifact_after="sha256:artifact-v1",
        )
        == 1
    )
    _select_candidate(context, "candidate-2")

    receipt = load_public_probe_receipts(context, "agent")[0]
    assert receipt["candidate_current"] is False
    assert receipt["stale"] is True


def test_candidate_change_refreshes_semantic_and_adaptive_probe_projection():
    context = _context()
    store_candidate_fallback(
        context,
        "agent",
        [ActionModel(agent_name="agent", policy_info="candidate response v1")],
    )
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="call-refresh",
        params={"command": "pytest -q"},
    )
    assert record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-refresh",
        tool_identity="terminal:execute",
        arguments_projection=action.params,
        value=_probe_value(probe_kind="regression"),
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(tool_call_id="call-refresh", content="ok", success=True)
            ]
        ),
    )
    assert (
        semantic_progress_for_agent(context, agent_id="agent")["public_probe_receipts"][
            0
        ]["stale"]
        is False
    )

    store_candidate_fallback(
        context,
        "agent",
        [ActionModel(agent_name="agent", policy_info="candidate response v2")],
    )

    semantic = semantic_progress_for_agent(context, agent_id="agent")
    assert semantic["public_probe_receipts"][0]["stale"] is True
    adaptive = context.read_task_runtime_state("agent", ADAPTIVE_WORK_STATE_KEY)
    assert adaptive["public_probe_receipts"][0]["stale"] is True


def test_public_probe_plan_is_strict_bounded_and_never_accepts_reward_claims():
    context = _context()

    assert not record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-9",
        tool_identity="terminal:execute",
        arguments_projection={"command": "true"},
        value={**_probe_value(), "task_reward": 1},
    )
    assert not record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-9",
        tool_identity="terminal:execute",
        arguments_projection={"command": "true"},
        value=_probe_value(highest_risk_counterexample="x" * 1025),
    )
    assert load_public_probe_receipts(context, "agent") == []


def test_public_probe_rejects_action_tail_collision_beyond_bounded_projection():
    context = _context()
    planned = "x" * 2200 + "planned"
    observed = "x" * 2200 + "different"
    assert record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-tail",
        tool_identity="terminal:execute",
        arguments_projection={"command": planned},
        value=_probe_value(),
    )
    mismatched_action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="call-tail",
        params={"command": observed},
    )

    assert (
        record_public_probe_observations(
            context,
            "agent",
            actions=[mismatched_action],
            result_projections=[{"tool_call_id": "call-tail", "success": True}],
        )
        == 0
    )
    assert load_public_probe_receipts(context, "agent") == []


def test_public_probe_receipt_is_projected_into_operational_evidence_ledger():
    context = _context()
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="call-ledger",
        params={"command": "python -m json.tool report.json"},
    )
    assert record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-ledger",
        tool_identity="terminal:execute",
        arguments_projection=action.params,
        value=_probe_value(),
    )

    state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id="call-ledger",
                    content="valid JSON",
                    success=True,
                )
            ]
        ),
    )

    assert state["public_probe_receipt_count"] == 1
    assert state["public_probe_receipts"][0]["hypothesis_id"] == "report-parses"
    assert state["public_probe_receipts"][0]["tool_execution_succeeded"] is True
    assert state["public_probe_receipts"][0]["probe_assessment"] == "unassessed"
    assert state["public_probe_receipts"][0]["stale"] is False


def test_public_probe_receipts_fan_in_across_transport_copies():
    context = _context()
    actions = [
        ActionModel(
            tool_name="terminal",
            action_name="execute",
            tool_call_id=f"call-{index}",
            params={"command": f"check-{index}"},
        )
        for index in (1, 2)
    ]
    for index, action in enumerate(actions, start=1):
        assert record_public_probe_plan(
            context,
            "agent",
            tool_call_id=action.tool_call_id,
            tool_identity="terminal:execute",
            arguments_projection=action.params,
            value=_probe_value(hypothesis_id=f"hypothesis-{index}"),
        )
    copies = [context.deep_copy(), context.deep_copy()]
    barrier = Barrier(2)

    def observe(copy: Context, action: ActionModel) -> int:
        barrier.wait(timeout=5)
        return record_public_probe_observations(
            copy,
            "agent",
            actions=[action],
            result_projections=[{"tool_call_id": action.tool_call_id, "success": True}],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(observe, copies, actions))

    assert counts == [1, 1]
    receipts = load_public_probe_receipts(context, "agent")
    assert {receipt["hypothesis_id"] for receipt in receipts} == {
        "hypothesis-1",
        "hypothesis-2",
    }


def test_public_probe_ledger_survives_application_context_checkpoint_round_trip():
    context = _application_context()
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="call-checkpoint",
        params={"command": "python -m json.tool report.json"},
    )
    assert record_public_probe_plan(
        context,
        "agent",
        tool_call_id="call-checkpoint",
        tool_identity="terminal:execute",
        arguments_projection=action.params,
        value=_probe_value(),
    )
    assert (
        record_public_probe_observations(
            context,
            "agent",
            actions=[action],
            result_projections=[{"tool_call_id": "call-checkpoint", "success": True}],
        )
        == 1
    )

    restored = ApplicationContext.from_dict(context.to_dict())

    receipts = load_public_probe_receipts(restored, "agent")
    assert len(receipts) == 1
    assert receipts[0]["hypothesis_id"] == "report-parses"
    assert receipts[0]["authority"] == "agent_self_check"
    assert receipts[0]["task_reward"] == "not_assessed"


def test_public_probe_checkpoint_rejects_wrong_task_scope():
    context = _application_context()
    scoped_key = "execution_protocol_public_probes:agent"
    context.put(
        scoped_key,
        {
            "schema_version": "aworld.public-probe-ledger/v1",
            "scope": {
                "task_id": "different-task",
                "task_epoch": context.task_epoch,
                "agent_id": "agent",
            },
            "plans": {},
            "receipts": [{"hypothesis_id": "must-not-leak"}],
        },
    )

    restored = ApplicationContext.from_dict(context.to_dict())

    assert load_public_probe_receipts(restored, "agent") == []


def test_concurrent_probe_fan_in_is_atomic_with_checkpoint_projection(
    monkeypatch,
):
    context = _application_context()
    actions = [
        ActionModel(
            tool_name="terminal",
            action_name="execute",
            tool_call_id=f"call-checkpoint-{index}",
            params={"command": f"check-{index}"},
        )
        for index in (1, 2)
    ]
    transport_copies = [context.deep_copy(), context.deep_copy()]
    for transport_copy in transport_copies:
        transport_copy._event_manager = SimpleNamespace(context=context)
    first_projection_entered = Event()
    release_first_projection = Event()
    original_project = execution_protocol_module._project_runtime_value

    def delayed_project(owner, agent_id, key, value):
        plans = value.get("plans") if isinstance(value, dict) else None
        if (
            key == "execution_protocol_public_probes"
            and isinstance(plans, dict)
            and len(plans) == 1
            and not first_projection_entered.is_set()
        ):
            first_projection_entered.set()
            assert release_first_projection.wait(timeout=5)
        return original_project(owner, agent_id, key, value)

    monkeypatch.setattr(
        execution_protocol_module,
        "_project_runtime_value",
        delayed_project,
    )

    def plan(copy: ApplicationContext, action: ActionModel, index: int) -> bool:
        return record_public_probe_plan(
            copy,
            "agent",
            tool_call_id=action.tool_call_id,
            tool_identity="terminal:execute",
            arguments_projection=action.params,
            value=_probe_value(hypothesis_id=f"checkpoint-{index}"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(plan, transport_copies[0], actions[0], 1)
        assert first_projection_entered.wait(timeout=5)
        second = pool.submit(plan, transport_copies[1], actions[1], 2)
        assert not second.done()
        release_first_projection.set()
        assert first.result(timeout=5) is True
        assert second.result(timeout=5) is True

    restored = ApplicationContext.from_dict(context.to_dict())
    assert (
        record_public_probe_observations(
            restored,
            "agent",
            actions=actions,
            result_projections=[
                {"tool_call_id": action.tool_call_id, "success": True}
                for action in actions
            ],
        )
        == 2
    )
    assert {
        receipt["hypothesis_id"]
        for receipt in load_public_probe_receipts(restored, "agent")
    } == {"checkpoint-1", "checkpoint-2"}
