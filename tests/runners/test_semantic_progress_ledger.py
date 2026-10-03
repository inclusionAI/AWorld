from datetime import datetime, timezone

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    ArtifactEvidence,
    ArtifactRequirement,
    CompletionContract,
    CompletionMode,
    SelfCheckEvidence,
)
from aworld.core.execution_protocol import (
    ControllerAction,
    ExecutionProtocolPolicy,
    ProtocolMode,
)
from aworld.runners.execution_protocol import (
    configure_execution_protocol,
    consume_execution_protocol_guidance,
    load_execution_protocol_state,
)
from aworld.runners.post_tool_progress import record_semantic_tool_progress


def _record_failure(context: Context, index: int):
    return record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="terminal",
                action_name="execute",
                tool_call_id=f"call-{index}",
                params={"command": f"candidate-{index} --retry"},
            )
        ],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=False,
                    error=f"AssertionError: row {index} at /tmp/run-{index}/check.py",
                )
            ]
        ),
    )


def test_repeated_failure_signature_offers_bounded_checkpoint():
    context = Context(task_id="semantic-ledger")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    baseline = _record_failure(context, 0)
    assert baseline["goal_progress_observable"] is None
    assert baseline["no_goal_progress_count"] == 0

    for index in range(1, 6):
        state = _record_failure(context, index)

    assert state["failure_signature"] == baseline["failure_signature"]
    assert state["no_goal_progress_count"] == 0
    assert state["durable_stagnation_count"] == 6
    metrics = context.context_info["execution_protocol_metrics"]
    assert metrics["last_action"] == ControllerAction.REQUEST_REPLAN.value


def test_changed_failure_signature_is_meaningful_progress():
    context = Context(task_id="semantic-failure-change")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    _record_failure(context, 1)
    repeated = _record_failure(context, 2)
    assert repeated["no_goal_progress_count"] == 0

    changed = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(success=False, error="PermissionError: denied")]
        ),
    )

    assert changed["failure_signature"] != repeated["failure_signature"]
    assert changed["semantic_progress_enabled"] is True
    assert changed["goal_progress"] is False
    assert changed["durable_milestone_advanced"] is False
    assert changed["no_goal_progress_count"] == 0
    assert changed["durable_stagnation_count"] == 3
    assert changed["last_meaningful_progress_at"] is not None


def test_alternating_known_failure_signatures_do_not_reset_progress():
    context = Context(task_id="semantic-failure-abab")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    states = []
    for index, error in enumerate(
        (
            "AssertionError: alpha",
            "PermissionError: beta",
            "AssertionError: alpha",
            "PermissionError: beta",
            "AssertionError: alpha",
            "PermissionError: beta",
        )
    ):
        states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[
                    ActionModel(
                        tool_name="terminal",
                        action_name="execute",
                        params={"command": f"attempt-{index}"},
                    )
                ],
                observation=Observation(
                    action_result=[ActionResult(success=False, error=error)]
                ),
            )
        )

    assert states[0]["goal_progress"] is False
    assert states[1]["goal_progress"] is False
    assert states[0]["durable_milestone_advanced"] is False
    assert states[1]["durable_milestone_advanced"] is False
    assert all(state["goal_progress"] is False for state in states[2:])
    assert states[-1]["no_goal_progress_count"] == 0
    assert states[-1]["durable_stagnation_count"] == 6
    assert len(states[-1]["recent_failure_signatures"]) == 6


def test_semantic_progress_ledger_env_opt_out(monkeypatch):
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "false")
    context = Context(task_id="semantic-ledger-off")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    state = _record_failure(context, 1)

    assert state["semantic_progress_enabled"] is False
    assert state["goal_progress_observable"] is False
    assert state["no_goal_progress_count"] == 0


def test_two_ineffective_replans_stop_injecting_without_finalizing():
    context = Context(task_id="semantic-replan-limit")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
            max_replans=2,
        ),
    )
    _record_failure(context, 0)
    next_index = 1
    for _ in range(2):
        for _ in range(6):
            _record_failure(context, next_index)
            next_index += 1
        guidance = consume_execution_protocol_guidance(context, "agent")
        assert guidance is not None and "checkpoint" in guidance

    for _ in range(6):
        _record_failure(context, next_index)
        next_index += 1

    metrics = context.context_info["execution_protocol_metrics"]
    assert metrics["last_action"] == ControllerAction.CONTINUE.value
    assert metrics["last_reason"] == "replan_limit_reached"
    assert consume_execution_protocol_guidance(context, "agent") is None
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 2
    assert protocol_state.finalization_entered is False


def test_distinct_successful_investigations_offer_durable_evidence_checkpoint():
    context = Context(task_id="semantic-progress-unknown")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
            no_goal_progress_threshold=2,
            stagnation_event_threshold=2,
        ),
    )

    for index in range(6):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"call-{index}",
                    params={"command": f"inspect-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel observation {index}", success=True)
                ]
            ),
        )
        if index < 5:
            assert load_execution_protocol_state(context, "agent").replan_count == 0

    assert state["goal_progress_observable"] is None
    assert state["goal_progress"] is False
    assert state["no_goal_progress_count"] == 0
    assert state["result_repetition_count"] == 1
    assert state["durable_stagnation_count"] == 6
    assert state["low_information_gain_count"] == 6
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 1
    assert protocol_state.phase.value == "execute"
    assert protocol_state.finalization_entered is False
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "bounded next action" in guidance
    assert "inspectable milestone evidence" in guidance
    assert "keeps all normal Tools available" in guidance
    assert consume_execution_protocol_guidance(context, "agent") is None
    reset = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="terminal",
                action_name="execute",
                tool_call_id="after-checkpoint",
                params={"command": "inspect-after-checkpoint"},
            )
        ],
        observation=Observation(
            action_result=[ActionResult(content="new observation", success=True)]
        ),
    )
    assert reset["durable_stagnation_count"] == 1
    assert reset["low_information_gain_count"] == 1


def test_unverified_artifact_advance_does_not_reset_durable_stagnation():
    context = Context(task_id="semantic-unverified-artifact")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    for index in range(5):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"inspect-{index}",
                    params={"command": f"inspect-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel observation {index}", success=True)
                ]
            ),
        )

    assert state["goal_progress_observable"] is None
    assert state["durable_stagnation_count"] == 5
    assert load_execution_protocol_state(context, "agent").replan_count == 0

    advanced = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="filesystem",
                action_name="write_file",
                tool_call_id="write-candidate",
                params={"path": "candidate.txt", "content": "candidate"},
            )
        ],
        observation=Observation(
            action_result=[
                ActionResult(
                    content="candidate written",
                    success=True,
                    metadata={
                        "context_management": {
                            "artifact_changed": True,
                            "artifact_fingerprint_after": "artifact-v2",
                        }
                    },
                )
            ]
        ),
    )

    assert advanced["artifact_advanced"] is True
    assert advanced["diagnostic_progress_observable"] is True
    assert advanced["durable_milestone_advanced"] is False
    assert advanced["goal_progress_observable"] is None
    assert advanced["goal_progress"] is False
    assert advanced["durable_stagnation_count"] == 6
    assert advanced["low_information_gain_count"] == 6
    assert load_execution_protocol_state(context, "agent").replan_count == 1


def test_contract_bound_completion_advance_resets_durable_stagnation():
    context = Context(task_id="semantic-contract-progress")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(
                ArtifactRequirement(
                    requirement_id="candidate",
                    path="/app/candidate.txt",
                ),
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
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    for index in range(5):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"inspect-contract-{index}",
                    params={"command": f"inspect-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel observation {index}", success=True)
                ]
            ),
        )

    assert state["durable_stagnation_count"] == 5
    context.record_completion_artifact(
        ArtifactEvidence(
            requirement_id="candidate",
            exists=True,
            content_hash="sha256:" + "a" * 64,
            observed_at=datetime.now(timezone.utc),
        )
    )
    advanced = record_semantic_tool_progress(
        context,
        tool_name="filesystem",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="filesystem",
                action_name="write_file",
                tool_call_id="write-required-candidate",
                params={"path": "/app/candidate.txt", "content": "candidate"},
            )
        ],
        observation=Observation(
            action_result=[ActionResult(content="candidate written", success=True)]
        ),
    )

    assert advanced["completion_advanced"] is True
    assert advanced["durable_milestone_advanced"] is True
    assert advanced["goal_progress"] is True
    assert advanced["durable_stagnation_count"] == 0
    assert load_execution_protocol_state(context, "agent").replan_count == 0


def test_repeated_identical_completion_evidence_is_not_new_goal_progress():
    context = Context(task_id="semantic-identical-completion-evidence")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("focused-check",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    evidence = SelfCheckEvidence(
        command_id="focused-check",
        exit_code=0,
        output_hash="sha256:passed",
        observed_at=datetime.now(timezone.utc),
    )
    context.record_completion_self_check(evidence)
    first = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="check passed", success=True)]
        ),
    )

    context.record_completion_self_check(evidence)
    repeated = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="check passed", success=True)]
        ),
    )

    assert first["completion_score"] == [1, 0]
    assert first["goal_progress"] is True
    assert repeated["completion_score"] == [1, 0]
    assert repeated["completion_advanced"] is False
    assert repeated["validation_evidence_advanced"] is False
    assert repeated["durable_milestone_advanced"] is False
    assert repeated["goal_progress"] is False
    assert repeated["durable_stagnation_count"] == 1


def test_undeclared_completion_evidence_cannot_reset_durable_stagnation():
    context = Context(task_id="semantic-undeclared-completion-evidence")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("required-check",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    for index in range(1, 4):
        context.record_completion_self_check(
            SelfCheckEvidence(
                command_id=f"diagnostic-{index}",
                exit_code=0,
                output_hash=f"sha256:diagnostic-{index}",
                observed_at=datetime.now(timezone.utc),
            )
        )
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[ActionModel(tool_name="terminal", action_name="execute")],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"diagnostic {index} passed", success=True)
                ]
            ),
        )
        assert state["completion_score"] == [0, -1]
        assert state["completion_advanced"] is False
        assert state["durable_milestone_advanced"] is False
        assert state["goal_progress"] is False

    assert state["durable_stagnation_count"] == 3


def test_completion_regression_cannot_replay_an_old_high_water_milestone():
    context = Context(task_id="semantic-completion-high-water")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("check-a", "check-b"),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    states = []
    for index, exit_code in enumerate((1, 0, 1, 0, 1, 0)):
        context.record_completion_self_check(
            SelfCheckEvidence(
                command_id="check-a",
                exit_code=exit_code,
                output_hash=f"sha256:check-a-{exit_code}",
                observed_at=datetime.now(timezone.utc),
            )
        )
        states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[
                    ActionModel(tool_name="terminal", action_name="execute")
                ],
                observation=Observation(
                    action_result=[
                        ActionResult(
                            content=f"check-a exit {exit_code}", success=True
                        )
                    ]
                ),
            )
        )

    assert [state["completion_score"] for state in states] == [
        [0, -2],
        [1, -1],
        [0, -2],
        [1, -1],
        [0, -2],
        [1, -1],
    ]
    assert [state["completion_advanced"] for state in states] == [
        False,
        True,
        False,
        False,
        False,
        False,
    ]
    assert states[-1]["completion_high_water_score"] == [1, -1]
    assert states[-1]["goal_progress"] is False
    assert states[-1]["durable_stagnation_count"] == 4


def test_completion_high_water_is_reset_for_a_new_task_epoch():
    context = Context(task_id="semantic-scope-task-1")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("check-a", "check-b"),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    for command_id in ("check-a", "check-b"):
        context.record_completion_self_check(
            SelfCheckEvidence(
                command_id=command_id,
                exit_code=0,
                output_hash=f"sha256:{command_id}",
                observed_at=datetime.now(timezone.utc),
            )
        )
    first = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="task one passed", success=True)]
        ),
    )
    assert first["completion_high_water_score"] == [2, 0]
    first_scope = first["scope"]

    context.task_id = "semantic-scope-task-2"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("check-c",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    context.record_completion_self_check(
        SelfCheckEvidence(
            command_id="check-c",
            exit_code=0,
            output_hash="sha256:check-c",
            observed_at=datetime.now(timezone.utc),
        )
    )
    second = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="task two passed", success=True)]
        ),
    )

    assert second["scope"] != first_scope
    assert second["scope"]["task_id"] == "semantic-scope-task-2"
    assert second["completion_score"] == [1, 0]
    assert second["completion_high_water_score"] == [1, 0]
    assert second["completion_advanced"] is True
    assert second["goal_progress"] is True


def test_opaque_workspace_churn_does_not_suppress_advisory_replan():
    context = Context(task_id="semantic-opaque-workspace-churn")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    for index in range(6):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id=f"opaque-{index}",
                    params={"code": f"download-or-install-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(
                        content=f"novel diagnostic {index}",
                        success=True,
                        metadata={
                            "context_management": {
                                "artifact_changed": True,
                                "artifact_fingerprint_after": f"workspace-{index}",
                            }
                        },
                    )
                ]
            ),
        )

    assert state["artifact_advanced"] is True
    assert state["diagnostic_progress_observable"] is True
    assert state["durable_milestone_advanced"] is False
    assert state["goal_progress"] is False
    assert state["durable_stagnation_count"] == 6
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 1
    assert protocol_state.finalization_entered is False
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "inspectable milestone evidence" in guidance


def test_r5_shaped_failure_and_workspace_churn_requests_replan():
    context = Context(task_id="semantic-r5-shape")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    states = []
    for index in range(10):
        failed = index in {1, 4, 7}
        states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[
                    ActionModel(
                        tool_name="terminal",
                        action_name="run_code",
                        tool_call_id=f"r5-{index}",
                        params={"code": f"explore-prerequisite-{index}"},
                    )
                ],
                observation=Observation(
                    action_result=[
                        ActionResult(
                            content=f"diagnostic {index}",
                            success=not failed,
                            error=(
                                f"network prerequisite {index} failed"
                                if failed
                                else None
                            ),
                            metadata={
                                "context_management": {
                                    "artifact_changed": index in {2, 5, 8},
                                    "artifact_fingerprint_after": f"workspace-{index}",
                                }
                            },
                        )
                    ]
                ),
            )
        )

    state = states[-1]
    assert any(item["semantic_progress"] is True for item in states)
    assert state["diagnostic_progress_observable"] is True
    assert state["goal_progress_observable"] is None
    assert state["goal_progress"] is False
    assert state["durable_stagnation_count"] == 10
    assert state["low_information_gain_count"] == 10
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 1
    assert protocol_state.finalization_entered is False


def test_repeated_failures_remain_diagnostic_without_goal_channel():
    context = Context(task_id="semantic-failure-observable")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    first = _record_failure(context, 0)
    repeated = _record_failure(context, 1)

    assert first["diagnostic_progress_observable"] is True
    assert repeated["diagnostic_progress_observable"] is True
    assert first["goal_progress_observable"] is None
    assert repeated["goal_progress_observable"] is None
    assert repeated["no_goal_progress_count"] == 0
