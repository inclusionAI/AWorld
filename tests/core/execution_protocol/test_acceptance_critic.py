import json

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.execution_protocol import (
    AcceptanceCriticDecision,
    AcceptanceDecision,
    AcceptanceProbeReceipt,
    ControllerAction,
    ExecutionProtocolPolicy,
    ProtocolMode,
    fresh_acceptance_critic_messages,
)
from aworld.core.task import Task
from aworld.runners.execution_protocol import (
    configure_execution_protocol,
    record_acceptance_critic_decision,
    record_acceptance_probe_observation,
    record_acceptance_probe_plan,
    record_candidate_final,
    store_candidate_fallback,
)
from aworld.runners.post_tool_progress import record_semantic_tool_progress


def _context(name: str) -> Context:
    context = Context(task_id=name)
    context.set_task(Task(id=name, input="public task", timeout=600))
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            review_unarmed_candidates=True,
            independent_acceptance_enabled=True,
        ),
    )
    return context


def _decision(value: str = "accept") -> str:
    return json.dumps(
        {
            "decision": value,
            "highest_risk_counterexample": "real SIGINT interrupts all workers",
            "hypothesis_id": "signal-propagation",
            "reason": "fresh process probe completed",
        }
    )


def _successful_probe(context: Context) -> None:
    assert record_acceptance_probe_plan(
        context,
        "agent",
        tool_call_id="probe-1",
        hypothesis_id="signal-propagation",
        highest_risk_counterexample="real SIGINT interrupts all workers",
        tool_identity="terminal:execute",
        arguments_projection={"command": "python probe.py"},
        assertion={
            "kind": "exit_code_zero_and_output_contains",
            "expected_output": "SIGINT_OK",
        },
    )
    assert record_acceptance_probe_observation(
        context,
        "agent",
        actions=[ActionModel(tool_call_id="probe-1")],
        result_projection={
            "tool_call_id": "probe-1",
            "success": True,
            "return_code": 0,
            "stdout_tail": "SIGINT_OK",
            "stderr_tail": "",
            "content_tail": "",
            "failure_code": None,
        },
        success=True,
        failure_code=None,
        artifact_after="sha256:after",
    )


def test_new_protocol_features_are_default_on_in_config():
    policy = ExecutionProtocolPolicy()

    assert policy.independent_acceptance_enabled is True
    assert policy.semantic_progress_enabled is True


def test_typed_accept_requires_matching_successful_probe_receipt():
    context = _context("critic-accept")
    assert record_candidate_final(context, "agent").decision.action is (
        ControllerAction.REQUEST_FINAL_REVIEW
    )
    _successful_probe(context)

    transition, decision, accepted = record_acceptance_critic_decision(
        context, "agent", _decision()
    )

    assert isinstance(decision, AcceptanceCriticDecision)
    assert decision.decision is AcceptanceDecision.ACCEPT
    assert accepted is True
    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.state.acceptance_confirmed is True


def test_post_tool_boundary_builds_framework_probe_receipt():
    context = _context("critic-post-tool")
    record_candidate_final(context, "agent")
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="probe-boundary",
        params={"command": "python probe.py"},
    )
    assert record_acceptance_probe_plan(
        context,
        "agent",
        tool_call_id="probe-boundary",
        hypothesis_id="signal-propagation",
        highest_risk_counterexample="real SIGINT interrupts all workers",
        tool_identity="terminal:execute",
        arguments_projection=action.params,
        assertion={
            "kind": "exit_code_zero_and_output_contains",
            "expected_output": "SIGINT_OK",
        },
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id="probe-boundary",
                    success=True,
                    content="probe complete",
                    metadata={"return_code": 0, "stdout": "SIGINT_OK"},
                )
            ]
        ),
    )

    transition, _, accepted = record_acceptance_critic_decision(
        context, "agent", _decision()
    )

    assert accepted is True
    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT


def test_accept_without_probe_becomes_repair_not_submit():
    context = _context("critic-no-probe")
    record_candidate_final(context, "agent")

    transition, _, accepted = record_acceptance_critic_decision(
        context, "agent", _decision()
    )

    assert accepted is False
    assert transition.decision.action is ControllerAction.REQUEST_REPAIR
    assert transition.state.acceptance_confirmed is False


def test_second_uncertain_review_stops_incomplete_never_submits():
    context = _context("critic-uncertain")
    record_candidate_final(context, "agent")
    first, _, _ = record_acceptance_critic_decision(
        context, "agent", _decision("uncertain")
    )
    assert first.decision.action is ControllerAction.REQUEST_REPAIR

    second_review = record_candidate_final(context, "agent")
    assert second_review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    second, _, _ = record_acceptance_critic_decision(
        context, "agent", _decision("uncertain")
    )
    assert second.decision.action is ControllerAction.STOP_INCOMPLETE
    assert second.state.acceptance_confirmed is False


def test_fresh_critic_messages_exclude_solver_history_and_self_claims():
    receipt = AcceptanceProbeReceipt.build(
        hypothesis_id="h1",
        highest_risk_counterexample="counterexample",
        tool_identity="terminal:execute",
        arguments_projection={"command": "python probe.py"},
        candidate="artifact saved",
        evidence={"artifact_fingerprint": "sha256:artifact"},
        artifact_before="sha256:artifact",
        artifact_after="sha256:artifact",
        assertion={
            "kind": "exit_code_zero_and_output_contains",
            "expected_output": "OK",
        },
        assertion_satisfied=True,
        result_projection={"return_code": 0, "stdout_tail": "OK"},
        success=True,
    )
    messages = fresh_acceptance_critic_messages(
        public_request="write the artifact",
        candidate="artifact saved",
        evidence={
            "artifact_fingerprint": "sha256:artifact",
            "solver_reasoning": "private chain that must not be forwarded",
            "self_check_claim": "all tests pass",
        },
        probe_receipt=receipt,
        probe_hypothesis_id="h1",
        probe_counterexample="counterexample",
    )
    serialized = json.dumps(messages)

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "write the artifact" in serialized
    assert "artifact saved" in serialized
    assert "private chain" not in serialized
    assert "all tests pass" not in serialized
    assert "aworld.acceptance-probe-receipt/v2" in serialized
    assert "python probe.py" in serialized
    assert "stdout_tail" in serialized

    tampered = receipt.to_dict()
    tampered["arguments_projection"] = {"command": "true"}
    assert (
        AcceptanceProbeReceipt.from_dict(tampered).supports(
            AcceptanceCriticDecision.from_value(
                {
                    "decision": "accept",
                    "highest_risk_counterexample": "counterexample",
                    "hypothesis_id": "h1",
                    "reason": "probe passed",
                }
            )
        )
        is False
    )


def test_trivial_transport_success_cannot_support_accept():
    context = _context("critic-trivial-probe")
    record_candidate_final(context, "agent")
    assert record_acceptance_probe_plan(
        context,
        "agent",
        tool_call_id="probe-trivial",
        hypothesis_id="signal-propagation",
        highest_risk_counterexample="real SIGINT interrupts all workers",
        tool_identity="terminal:execute",
        arguments_projection={"command": "true"},
        assertion={
            "kind": "exit_code_zero_and_output_contains",
            "expected_output": "SIGINT_OK",
        },
    )
    assert record_acceptance_probe_observation(
        context,
        "agent",
        actions=[ActionModel(tool_call_id="probe-trivial")],
        result_projection={
            "tool_call_id": "probe-trivial",
            "success": True,
            "return_code": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "content_tail": "",
            "failure_code": None,
        },
        success=True,
        failure_code=None,
    )

    transition, _, accepted = record_acceptance_critic_decision(
        context, "agent", _decision()
    )

    assert accepted is False
    assert transition.decision.action is ControllerAction.REQUEST_REPAIR


def test_probe_cannot_echo_its_expected_assertion_marker():
    context = _context("critic-echo-probe")
    record_candidate_final(context, "agent")

    planned = record_acceptance_probe_plan(
        context,
        "agent",
        tool_call_id="probe-echo",
        hypothesis_id="signal-propagation",
        highest_risk_counterexample="real SIGINT interrupts all workers",
        tool_identity="terminal:execute",
        arguments_projection={"command": "echo SIGINT_OK"},
        assertion={
            "kind": "exit_code_zero_and_output_contains",
            "expected_output": "SIGINT_OK",
        },
    )

    assert planned is False


def test_probe_receipt_cannot_be_reused_for_changed_candidate():
    context = _context("critic-candidate-binding")
    store_candidate_fallback(
        context, "agent", [ActionModel(agent_name="agent", policy_info="candidate-a")]
    )
    record_candidate_final(context, "agent")
    _successful_probe(context)
    store_candidate_fallback(
        context, "agent", [ActionModel(agent_name="agent", policy_info="candidate-b")]
    )

    transition, _, accepted = record_acceptance_critic_decision(
        context, "agent", _decision()
    )

    assert accepted is False
    assert transition.decision.action is ControllerAction.REQUEST_REPAIR


def test_independent_acceptance_can_be_explicitly_disabled():
    context = Context(task_id="critic-off")
    context.set_task(Task(id="critic-off", input="small task", timeout=60))
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            independent_acceptance_enabled=False,
        ),
    )

    transition = record_candidate_final(context, "agent")

    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT


def test_independent_acceptance_env_opt_out_overrides_enabled_config(monkeypatch):
    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "false")
    context = Context(task_id="critic-env-off")
    context.set_task(Task(id="critic-env-off", input="small task", timeout=60))
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            independent_acceptance_enabled=True,
        ),
    )

    transition = record_candidate_final(context, "agent")

    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
