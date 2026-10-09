from dataclasses import replace
import json

import pytest

from aworld.core.execution_protocol import (
    ActionSemanticReceipt,
    action_signature,
    ConvergenceStage,
    ControllerAction,
    DeliveryIntent,
    DecisionReason,
    EventKind,
    ExecutionHorizon,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ModelExecutionProfile,
    ModelPlanUpdate,
    NextActionAlignment,
    ProtocolPhase,
    ProtocolScope,
    ReviewOutcome,
    safe_transition_execution_protocol,
    transition_execution_protocol,
)


def _state():
    return ExecutionProtocolState.initial(
        ProtocolScope(task_id="task", task_epoch=1, agent_id="agent")
    )


def _tool(
    *,
    action_tool: str = "terminal__execute",
    action_arguments: dict | None = None,
    **kwargs,
):
    if action_arguments is None:
        action_arguments = {"command": "build-candidate"}
    kwargs.setdefault(
        "observed_action_names",
        ("terminal", "execute", "terminal__execute"),
    )
    if kwargs["observed_action_names"] == ("terminal",):
        kwargs["observed_action_names"] = (
            "terminal",
            "execute",
            "terminal__execute",
        )
    kwargs.setdefault(
        "observed_action_signatures",
        (action_signature(action_tool, action_arguments),),
    )
    return ExecutionProtocolEvent(kind=EventKind.TOOL_OBSERVATION, **kwargs)


def _armed_state():
    return replace(_state(), long_horizon_armed=True)


def _profile(
    *,
    horizon: ExecutionHorizon = ExecutionHorizon.LONG,
    confidence: float = 0.9,
    milestone_count: int = 3,
    expected_tool_actions: int = 8,
):
    return ExecutionProtocolEvent(
        kind=EventKind.MODEL_EXECUTION_PROFILE,
        model_execution_profile=ModelExecutionProfile(
            horizon=horizon,
            confidence=confidence,
            milestone_count=milestone_count,
            expected_tool_actions=expected_tool_actions,
            verification_required=True,
        ),
    )


def _plan_update(
    *,
    intent: str,
    decision: str = "continue",
    horizon: str = "long",
    tool: str | None = "terminal__execute",
    arguments: dict | None = None,
):
    if arguments is None and intent not in {"submit_current", "submit_uncertain"}:
        arguments = {"command": "build-candidate"}
    return ModelPlanUpdate.from_mapping(
        {
            "decision": decision,
            "horizon": horizon,
            "milestone": "produce and check a candidate",
            "next_action": "take one bounded action consistent with the declared intent",
            "next_action_tool": tool,
            "next_action_arguments": (
                json.dumps(arguments, sort_keys=True) if arguments is not None else None
            ),
            "verification_plan": "inspect fresh public evidence",
            "completion_assessment": "in_progress",
            "delivery_intent": intent,
            "delivery_rationale": "chosen from current public evidence",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": ["tool:call-1"],
            "selected_candidate_id": None,
        }
    )


def _semantic_receipt(
    *,
    effect: str,
    target: str | None,
    capability: str = "workspace.execute",
    executed: bool | None = None,
    succeeded: bool | None = None,
    timed_out: bool | None = None,
    validation_kind: str | None = None,
    declared: bool | None = None,
    call_id: str | None = None,
) -> ActionSemanticReceipt:
    return ActionSemanticReceipt(
        capability_aliases=(capability,),
        effect=effect,
        target_ids=((target,) if target is not None else ()),
        executed=executed,
        succeeded=succeeded,
        timed_out=timed_out,
        validation_kind=validation_kind,
        declared_deliverable_targeted=declared,
        tool_call_id=call_id,
    )


def _semantic_plan(*, intent: str, receipt: ActionSemanticReceipt) -> ModelPlanUpdate:
    return replace(_plan_update(intent=intent), next_action_semantics=receipt)


def _semantic_tool(receipt: ActionSemanticReceipt, **kwargs) -> ExecutionProtocolEvent:
    kwargs.setdefault("observed_action_names", ())
    kwargs.setdefault("observed_action_signatures", ())
    kwargs["observed_action_semantics"] = (receipt,)
    return _tool(**kwargs)


def test_semantically_equivalent_commands_on_declared_target_align() -> None:
    target = "sha256:" + "1" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="produce_candidate",
                receipt=_semantic_receipt(
                    effect="mutating", target=target, declared=True
                ),
            ),
        ),
        policy,
    )

    observed = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="mutating",
                target=target,
                executed=True,
                succeeded=True,
                timed_out=False,
                declared=True,
            ),
            candidate_present=True,
            candidate_advanced=True,
        ),
        policy,
    )

    assert observed.state.last_action_alignment is NextActionAlignment.MATCHED
    assert observed.state.action_alignment_match_count == 1


@pytest.mark.parametrize(
    ("capability", "target"),
    [
        ("filesystem.write", "sha256:" + "1" * 64),
        ("workspace.execute", "sha256:" + "2" * 64),
    ],
)
def test_semantic_alignment_rejects_unrelated_capability_or_target(
    capability, target
) -> None:
    declared_target = "sha256:" + "1" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="produce_candidate",
                receipt=_semantic_receipt(
                    effect="mutating", target=declared_target, declared=True
                ),
            ),
        ),
        policy,
    )

    observed = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                capability=capability,
                effect="mutating",
                target=target,
                executed=True,
                succeeded=True,
                timed_out=False,
                declared=target == declared_target,
            ),
            candidate_present=False,
            candidate_advanced=False,
        ),
        policy,
    )

    assert observed.state.last_action_alignment is NextActionAlignment.MISMATCHED


@pytest.mark.parametrize("receipt", [None, "unknown"])
def test_missing_or_unknown_semantic_receipt_is_unobservable(receipt) -> None:
    target = "sha256:" + "3" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="continue_exploration",
                receipt=_semantic_receipt(effect="read_only", target=target),
            ),
        ),
        policy,
    )
    semantics = ()
    if receipt == "unknown":
        semantics = (
            _semantic_receipt(
                effect="unknown",
                target=target,
                executed=True,
                succeeded=True,
                timed_out=False,
            ),
        )

    observed = transition_execution_protocol(
        planned.state,
        _tool(
            observed_action_names=(),
            observed_action_signatures=(),
            observed_action_semantics=semantics,
            new_information_observed=True,
        ),
        policy,
    )

    assert observed.state.last_action_alignment is NextActionAlignment.UNOBSERVABLE
    assert observed.state.action_alignment_match_count == 0
    assert observed.state.action_alignment_mismatch_count == 0


@pytest.mark.parametrize("timed_out", [False, True])
def test_failed_or_timed_out_semantic_action_never_matches(timed_out) -> None:
    target = "sha256:" + "b" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="continue_exploration",
                receipt=_semantic_receipt(effect="read_only", target=target),
            ),
        ),
        policy,
    )

    observed = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="read_only",
                target=target,
                executed=True,
                succeeded=False,
                timed_out=timed_out,
            ),
            new_information_observed=True,
        ),
        policy,
    )

    assert observed.state.last_action_alignment is NextActionAlignment.MISMATCHED
    assert observed.state.action_alignment_match_count == 0


def test_exploration_semantics_require_fresh_information() -> None:
    target = "sha256:" + "c" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="continue_exploration",
                receipt=_semantic_receipt(effect="read_only", target=target),
            ),
        ),
        policy,
    )

    observed = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="read_only",
                target=target,
                executed=True,
                succeeded=True,
                timed_out=False,
            ),
            new_information_observed=False,
        ),
        policy,
    )

    assert observed.state.last_action_alignment is NextActionAlignment.MISMATCHED


def test_bound_intended_call_failure_is_not_masked_by_incidental_success() -> None:
    target = "sha256:" + "d" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="continue_exploration",
                receipt=_semantic_receipt(effect="read_only", target=target),
            ),
        ),
        policy,
    )
    bound = transition_execution_protocol(
        planned.state,
        ExecutionProtocolEvent(
            kind=EventKind.NEXT_ACTION_BOUND,
            bound_tool_call_id="call-intended",
        ),
        policy,
    )

    observed = transition_execution_protocol(
        bound.state,
        _tool(
            observed_action_names=(),
            observed_action_signatures=(),
            observed_action_semantics=(
                _semantic_receipt(
                    effect="read_only",
                    target=target,
                    executed=True,
                    succeeded=False,
                    timed_out=False,
                    call_id="call-intended",
                ),
                _semantic_receipt(
                    effect="read_only",
                    target=target,
                    executed=True,
                    succeeded=True,
                    timed_out=False,
                    call_id="call-incidental",
                ),
            ),
            new_information_observed=True,
        ),
        policy,
    )

    assert bound.state.pending_next_action_call_id == "call-intended"
    assert observed.state.last_action_alignment is NextActionAlignment.MISMATCHED
    assert observed.state.action_alignment_match_count == 0


def test_semantic_alignment_rejects_extra_observed_target() -> None:
    first = "sha256:" + "e" * 64
    second = "sha256:" + "f" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="continue_exploration",
                receipt=_semantic_receipt(effect="read_only", target=first),
            ),
        ),
        policy,
    )
    observed_receipt = ActionSemanticReceipt(
        capability_aliases=("workspace.execute",),
        effect="read_only",
        target_ids=(first, second),
        executed=True,
        succeeded=True,
        timed_out=False,
    )

    observed = transition_execution_protocol(
        planned.state,
        _semantic_tool(observed_receipt, new_information_observed=True),
        policy,
    )

    assert observed.state.last_action_alignment is NextActionAlignment.MISMATCHED


def test_missing_candidate_without_mutation_requests_model_owned_delivery_decision():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        delivery_debt_observation_threshold=2,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
    )
    first = transition_execution_protocol(
        _armed_state(),
        _tool(
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
            new_information_observed=True,
            observed_action_names=("terminal",),
        ),
        policy,
    )
    second = transition_execution_protocol(
        first.state,
        _tool(
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
            new_information_observed=True,
            observed_action_names=("terminal",),
        ),
        policy,
    )

    assert first.decision.action is ControllerAction.CONTINUE
    assert first.state.delivery_debt_observations == 1
    assert second.decision.action is ControllerAction.REQUEST_REPLAN
    assert second.decision.reason is DecisionReason.DELIVERY_DEBT_DETECTED
    assert second.state.delivery_debt_observations == 2
    assert second.state.delivery_checkpoint_count == 1
    assert second.state.decision_checkpoint_pending is True
    assert (
        second.state.decision_checkpoint_reason is DecisionReason.DELIVERY_DEBT_DETECTED
    )


def test_delivery_debt_allows_explicit_exploration_deferral_and_tracks_alignment():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        delivery_debt_observation_threshold=1,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
    )
    requested = transition_execution_protocol(
        _armed_state(),
        _tool(
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
        ),
        policy,
    )
    deferred = transition_execution_protocol(
        requested.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="continue_exploration",
                receipt=_semantic_receipt(
                    effect="read_only", target="sha256:" + "4" * 64
                ),
            ),
        ),
        policy,
    )
    observed = transition_execution_protocol(
        deferred.state,
        _semantic_tool(
            _semantic_receipt(
                effect="read_only",
                target="sha256:" + "4" * 64,
                executed=True,
                succeeded=True,
                timed_out=False,
            ),
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
            new_information_observed=True,
            observed_action_names=("terminal",),
        ),
        policy,
    )

    assert (
        deferred.state.model_plan_update.delivery_intent
        is DeliveryIntent.CONTINUE_EXPLORATION
    )
    assert deferred.state.next_action_alignment_pending is True
    assert observed.state.next_action_alignment_pending is False
    assert observed.state.last_action_alignment is NextActionAlignment.MATCHED
    assert observed.state.action_alignment_match_count == 1
    assert observed.decision.action is ControllerAction.CONTINUE


def test_declared_candidate_action_mismatch_requests_fresh_decision():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        delivery_debt_observation_threshold=99,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
    )
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="produce_candidate",
                receipt=_semantic_receipt(
                    effect="mutating",
                    target="sha256:" + "5" * 64,
                    declared=True,
                ),
            ),
        ),
        policy,
    )
    mismatched = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="mutating",
                target="sha256:" + "6" * 64,
                executed=True,
                succeeded=True,
                timed_out=False,
                declared=False,
            ),
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
            new_information_observed=True,
            observed_action_names=("terminal",),
        ),
        policy,
    )

    assert mismatched.decision.action is ControllerAction.REQUEST_REPLAN
    assert mismatched.decision.reason is DecisionReason.NEXT_ACTION_MISMATCH
    assert mismatched.state.last_action_alignment is NextActionAlignment.MISMATCHED
    assert mismatched.state.action_alignment_mismatch_count == 1
    assert mismatched.state.last_action_alignment_plan_sequence == 1
    assert mismatched.state.last_action_alignment_observation_sequence == 2
    assert mismatched.state.next_action_alignment_pending is False

    replanned = transition_execution_protocol(
        mismatched.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="produce_candidate",
                receipt=_semantic_receipt(
                    effect="mutating",
                    target="sha256:" + "5" * 64,
                    declared=True,
                ),
            ),
        ),
        policy,
    )
    repeated = transition_execution_protocol(
        replanned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="mutating",
                target="sha256:" + "6" * 64,
                executed=True,
                succeeded=True,
                timed_out=False,
                declared=False,
            ),
            candidate_present=False,
            candidate_advanced=False,
            observed_action_names=("terminal",),
        ),
        policy,
    )
    assert repeated.decision.reason is DecisionReason.NEXT_ACTION_MISMATCH
    assert repeated.state.action_alignment_mismatch_count == 2
    assert repeated.state.last_action_alignment_plan_sequence == 3
    assert repeated.state.last_action_alignment_observation_sequence == 4


def test_exploration_alignment_requires_the_declared_tool_identity():
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="continue_exploration",
                receipt=_semantic_receipt(
                    capability="filesystem.read",
                    effect="read_only",
                    target="sha256:" + "7" * 64,
                ),
            ),
        ),
        policy,
    )

    different_action = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                capability="workspace.execute",
                effect="read_only",
                target="sha256:" + "7" * 64,
                executed=True,
                succeeded=True,
                timed_out=False,
            ),
            new_information_observed=True,
            observed_action_names=("terminal", "terminal__run_code"),
        ),
        policy,
    )

    assert different_action.decision.reason is DecisionReason.NEXT_ACTION_MISMATCH
    assert (
        different_action.state.last_action_alignment is NextActionAlignment.MISMATCHED
    )


def test_exact_argument_signature_is_compatibility_telemetry_only():
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(
                intent="continue_exploration",
                arguments={"command": "inspect-first-candidate"},
            ),
        ),
        policy,
    )

    different_arguments = transition_execution_protocol(
        planned.state,
        _tool(
            action_arguments={"command": "inspect-second-candidate"},
            new_information_observed=True,
        ),
        policy,
    )

    assert different_arguments.decision.reason is DecisionReason.OBSERVATION_RECORDED
    assert (
        different_arguments.state.last_action_alignment
        is NextActionAlignment.UNOBSERVABLE
    )


def test_declared_candidate_action_requires_observed_candidate_progress():
    target = "sha256:" + "8" * 64
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="produce_candidate",
                receipt=_semantic_receipt(
                    effect="mutating", target=target, declared=True
                ),
            ),
        ),
        policy,
    )
    matched = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="mutating",
                target=target,
                executed=True,
                succeeded=True,
                timed_out=False,
                declared=True,
            ),
            workspace_mutated=True,
            candidate_present=False,
            observed_action_names=("terminal",),
        ),
        policy,
    )

    assert matched.decision.action is ControllerAction.REQUEST_REPLAN
    assert matched.state.last_action_alignment is NextActionAlignment.MISMATCHED
    assert matched.state.action_alignment_match_count == 0

    replanned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="produce_candidate",
                receipt=_semantic_receipt(
                    effect="mutating", target=target, declared=True
                ),
            ),
        ),
        policy,
    )
    candidate = transition_execution_protocol(
        replanned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="mutating",
                target=target,
                executed=True,
                succeeded=True,
                timed_out=False,
                declared=True,
            ),
            workspace_mutated=True,
            candidate_present=True,
            candidate_advanced=True,
            observed_action_names=("terminal",),
        ),
        policy,
    )
    assert candidate.decision.action is ControllerAction.CONTINUE
    assert candidate.state.last_action_alignment is NextActionAlignment.MATCHED
    assert candidate.state.action_alignment_match_count == 1


def test_candidate_decision_reserve_precedes_tool_free_finalization():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    checkpoint = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=150,
            public_deliverable_declared=True,
            candidate_present=False,
        ),
        policy,
    )

    assert checkpoint.decision.action is ControllerAction.REQUEST_REPLAN
    assert checkpoint.decision.reason is DecisionReason.CANDIDATE_DECISION_RESERVE
    assert checkpoint.state.phase is ProtocolPhase.EXECUTE
    assert checkpoint.state.candidate_decision_count == 1
    assert checkpoint.state.candidate_decision_recorded is False
    assert checkpoint.state.decision_checkpoint_candidate_present is False

    explicit_deferral = transition_execution_protocol(
        checkpoint.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(intent="continue_exploration"),
        ),
        policy,
    )
    assert explicit_deferral.decision.reason is DecisionReason.MODEL_PLAN_CHECKPOINT
    assert explicit_deferral.state.decision_checkpoint_pending is False
    assert explicit_deferral.state.candidate_decision_recorded is True
    assert explicit_deferral.state.next_action_alignment_pending is True


def test_observe_candidate_reserve_is_requested_but_never_recorded_as_acknowledged():
    transition = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=150,
            candidate_present=False,
        ),
        ExecutionProtocolPolicy(
            mode="observe",
            finalization_reserve_seconds=60,
            candidate_decision_reserve_seconds=180,
        ),
    )

    assert transition.decision.action is ControllerAction.WOULD_REQUEST_REPLAN
    assert transition.state.candidate_decision_count == 1
    assert transition.state.candidate_decision_recorded is False


def test_unacknowledged_candidate_reserve_is_not_recorded_or_requested_again():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    requested = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=150,
            candidate_present=False,
        ),
        policy,
    )
    unacknowledged = transition_execution_protocol(
        requested.state,
        ExecutionProtocolEvent(kind=EventKind.REPLAN_UNACKNOWLEDGED),
        policy,
    )
    repeated = transition_execution_protocol(
        unacknowledged.state,
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=140,
            candidate_present=False,
        ),
        policy,
    )

    assert unacknowledged.state.candidate_decision_count == 1
    assert unacknowledged.state.candidate_decision_recorded is False
    assert repeated.state.candidate_decision_count == 1
    assert repeated.decision.action is ControllerAction.CONTINUE


def test_candidate_reserve_does_not_replace_an_unobserved_declared_action():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(intent="produce_candidate"),
        ),
        policy,
    )

    reserve = transition_execution_protocol(
        planned.state,
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=150,
            public_deliverable_declared=True,
            candidate_present=False,
        ),
        policy,
    )

    assert reserve.decision.action is ControllerAction.CONTINUE
    assert reserve.state.next_action_alignment_pending is True
    assert reserve.state.candidate_decision_recorded is False


def test_candidate_decision_reserve_records_candidate_state_without_overriding_choice():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    missing = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=150,
            candidate_present=False,
        ),
        policy,
    )
    missing_choice = transition_execution_protocol(
        missing.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(intent="validate_candidate"),
        ),
        policy,
    )
    assert missing_choice.decision.reason is DecisionReason.MODEL_PLAN_CHECKPOINT
    assert (
        missing_choice.state.model_plan_update.delivery_intent
        is DeliveryIntent.VALIDATE_CANDIDATE
    )

    present = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=150,
            candidate_present=True,
        ),
        policy,
    )
    valid = transition_execution_protocol(
        present.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(intent="validate_candidate"),
        ),
        policy,
    )
    assert valid.decision.reason is DecisionReason.MODEL_PLAN_CHECKPOINT


def test_validation_intent_without_probe_receipt_is_unobservable_not_mismatched():
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(intent="validate_candidate"),
        ),
        policy,
    )

    unobserved = transition_execution_protocol(
        planned.state,
        _tool(
            candidate_present=True,
            validation_observed=False,
            observed_action_names=("terminal",),
        ),
        policy,
    )

    assert unobserved.decision.action is ControllerAction.CONTINUE
    assert unobserved.state.last_action_alignment is NextActionAlignment.UNOBSERVABLE
    assert unobserved.state.action_alignment_mismatch_count == 0


@pytest.mark.parametrize("intent", ["submit_current", "submit_uncertain"])
def test_terminal_delivery_intent_enters_tool_free_finalization(intent):
    policy = ExecutionProtocolPolicy(mode="guide")
    planned = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(intent=intent, tool=None),
        ),
        policy,
    )

    assert planned.state.phase is ProtocolPhase.FINALIZE
    assert planned.state.finalization_entered is True
    assert planned.state.next_action_alignment_pending is False
    assert planned.state.acceptance_confirmed is False
    assert planned.state.candidate_final_count == 0

    unexpected_tool = transition_execution_protocol(
        planned.state,
        _tool(observed_action_names=("terminal",)),
        policy,
    )
    assert unexpected_tool.state.phase is ProtocolPhase.FINALIZE
    assert unexpected_tool.state.last_action_alignment is None
    assert unexpected_tool.state.action_alignment_mismatch_count == 0


def test_explicit_short_horizon_still_protects_named_public_deliverable():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        delivery_debt_observation_threshold=1,
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
    )
    state = transition_execution_protocol(
        _state(), _profile(horizon=ExecutionHorizon.SHORT), policy
    ).state
    debt = transition_execution_protocol(
        state,
        _tool(
            remaining_seconds=150,
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
        ),
        policy,
    )

    assert debt.decision.action is ControllerAction.REQUEST_REPLAN
    assert debt.decision.reason is DecisionReason.CANDIDATE_DECISION_RESERVE
    assert debt.state.candidate_decision_count == 1


def test_short_profile_with_public_contract_cannot_bypass_review():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        independent_acceptance_enabled=False,
        candidate_decision_reserve_seconds=180,
        finalization_reserve_seconds=60,
    )
    profiled = transition_execution_protocol(
        _state(), _profile(horizon=ExecutionHorizon.SHORT), policy
    )
    reviewed = transition_execution_protocol(
        profiled.state,
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL,
            public_deliverable_declared=True,
        ),
        policy,
    )

    assert reviewed.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert reviewed.decision.reason is DecisionReason.FINAL_REVIEW_REQUIRED
    assert reviewed.state.phase is ProtocolPhase.REVIEW


def test_public_contract_without_model_profile_cannot_activate_delivery_control():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        delivery_debt_observation_threshold=1,
        post_candidate_read_only_threshold=1,
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    missing = transition_execution_protocol(
        _state(),
        _tool(
            remaining_seconds=150,
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
        ),
        policy,
    )
    candidate = transition_execution_protocol(
        missing.state,
        _tool(
            public_deliverable_declared=True,
            candidate_present=True,
            candidate_advanced=True,
            public_candidate_mutated=True,
        ),
        policy,
    )
    stagnant = transition_execution_protocol(
        candidate.state,
        _tool(
            public_deliverable_declared=True,
            candidate_present=True,
            public_candidate_mutated=True,
            read_only_observed=True,
        ),
        policy,
    )

    assert missing.decision.action is ControllerAction.CONTINUE
    assert missing.state.delivery_debt_observations == 0
    assert missing.state.candidate_decision_count == 0
    assert stagnant.decision.action is ControllerAction.CONTINUE
    assert stagnant.state.post_candidate_no_delivery_progress_observations == 0
    assert stagnant.state.convergence_constraint_active is False


def test_explicit_zero_replan_limit_suppresses_all_new_checkpoint_paths():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        max_replans=0,
        delivery_debt_observation_threshold=1,
        finalization_reserve_seconds=60,
        candidate_decision_reserve_seconds=180,
    )
    candidate_reserve = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.DELIVERY_STATUS,
            remaining_seconds=150,
            candidate_present=False,
        ),
        policy,
    )
    assert candidate_reserve.decision.reason is DecisionReason.REPLAN_LIMIT_REACHED
    assert candidate_reserve.state.replan_count == 0
    assert candidate_reserve.state.candidate_decision_count == 0
    assert candidate_reserve.state.candidate_decision_recorded is False

    debt = transition_execution_protocol(
        _armed_state(),
        _tool(
            public_deliverable_declared=True,
            candidate_present=False,
            workspace_mutated=False,
        ),
        policy,
    )
    assert debt.decision.reason is DecisionReason.REPLAN_LIMIT_REACHED
    assert debt.state.replan_count == 0
    assert debt.state.delivery_checkpoint_count == 0

    planned = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_semantic_plan(
                intent="produce_candidate",
                receipt=_semantic_receipt(
                    effect="mutating",
                    target="sha256:" + "9" * 64,
                    declared=True,
                ),
            ),
        ),
        policy,
    )
    mismatch = transition_execution_protocol(
        planned.state,
        _semantic_tool(
            _semantic_receipt(
                effect="mutating",
                target="sha256:" + "a" * 64,
                executed=True,
                succeeded=True,
                timed_out=False,
                declared=False,
            ),
            candidate_present=False,
            observed_action_names=("terminal",),
        ),
        policy,
    )
    assert mismatch.decision.reason is DecisionReason.REPLAN_LIMIT_REACHED
    assert mismatch.state.replan_count == 0


def test_off_mode_is_completely_inert():
    state = _state()
    transition = transition_execution_protocol(
        state,
        _tool(repetition_count=99, current_step=9),
        ExecutionProtocolPolicy(mode="off"),
    )

    assert transition.state is state
    assert transition.decision.action is ControllerAction.CONTINUE
    assert transition.decision.reason is DecisionReason.DISABLED


def test_high_confidence_model_profile_arms_before_tool_threshold():
    transition = transition_execution_protocol(
        _state(),
        _profile(),
        ExecutionProtocolPolicy(mode="guide", activation_event_threshold=20),
    )

    assert transition.state.long_horizon_armed is True
    assert transition.state.model_execution_profile is not None
    assert transition.state.tool_observation_count == 0
    assert transition.decision.reason is DecisionReason.MODEL_LONG_HORIZON


def test_short_model_profile_does_not_arm_long_horizon_protocol():
    transition = transition_execution_protocol(
        _state(),
        _profile(horizon=ExecutionHorizon.SHORT),
        ExecutionProtocolPolicy(mode="guide"),
    )

    assert transition.state.long_horizon_armed is False
    assert transition.state.model_execution_profile is not None


def test_unknown_model_profile_cannot_authorize_short_task_review_bypass():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=False,
        independent_acceptance_enabled=False,
    )
    profiled = transition_execution_protocol(
        _state(),
        _profile(horizon=ExecutionHorizon.UNKNOWN),
        policy,
    )

    candidate = transition_execution_protocol(
        profiled.state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert profiled.state.long_horizon_armed is False
    assert candidate.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert candidate.decision.reason is DecisionReason.FINAL_REVIEW_REQUIRED


def test_missing_model_profile_cannot_authorize_short_task_review_bypass():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=False,
        independent_acceptance_enabled=False,
    )

    candidate = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert candidate.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert candidate.decision.reason is DecisionReason.FINAL_REVIEW_REQUIRED


def test_explicit_short_model_profile_retains_fast_candidate_bypass():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=False,
        independent_acceptance_enabled=False,
    )
    profiled = transition_execution_protocol(
        _state(),
        _profile(horizon=ExecutionHorizon.SHORT),
        policy,
    )

    candidate = transition_execution_protocol(
        profiled.state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert candidate.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert candidate.decision.reason is DecisionReason.SHORT_TASK_BYPASS


@pytest.mark.parametrize(
    "event",
    [
        _profile(confidence=0.01),
        _profile(milestone_count=1, expected_tool_actions=0),
    ],
)
def test_typed_long_horizon_declaration_is_not_overridden_by_framework_estimates(
    event,
):
    transition = transition_execution_protocol(
        _state(), event, ExecutionProtocolPolicy(mode="guide")
    )

    assert transition.state.long_horizon_armed is True
    assert transition.decision.reason is DecisionReason.MODEL_LONG_HORIZON


def test_short_model_profile_remains_authoritative_after_framework_observations():
    policy = ExecutionProtocolPolicy(mode="guide", activation_event_threshold=3)
    state = transition_execution_protocol(
        _state(), _profile(horizon=ExecutionHorizon.SHORT), policy
    ).state
    for step in range(1, 4):
        state = transition_execution_protocol(
            state,
            _tool(current_step=step, evidence_advanced=True),
            policy,
        ).state

    assert state.tool_observation_count == 3
    assert state.long_horizon_armed is False


def test_framework_observations_do_not_classify_an_unprofiled_task_as_long():
    policy = ExecutionProtocolPolicy(mode="guide", activation_event_threshold=1)

    transition = transition_execution_protocol(
        _state(),
        _tool(current_step=1, repetition_count=3),
        policy,
    )

    assert transition.decision.action is ControllerAction.REQUEST_REPLAN
    assert transition.state.long_horizon_armed is False


def test_model_plan_update_can_reclassify_horizon_and_acknowledge_checkpoint():
    policy = ExecutionProtocolPolicy(mode="guide", repetition_threshold=1)
    checkpoint = transition_execution_protocol(
        _state(), _tool(current_step=1, repetition_count=1), policy
    )
    update = ModelPlanUpdate.from_persisted_mapping(
        {
            "decision": "replan",
            "horizon": "long",
            "milestone": "first candidate",
            "next_action": "implement the bounded fix",
            "verification_plan": "run the public regression test",
            "completion_assessment": "in_progress",
            "assumptions": [],
            "retired_approaches": ["repeat inspection"],
            "evidence_refs": ["tool:call-1"],
            "selected_candidate_id": None,
        }
    )

    applied = transition_execution_protocol(
        checkpoint.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=update,
        ),
        policy,
    )

    assert applied.state.long_horizon_armed is True
    assert applied.state.model_plan_update == update
    assert applied.state.attempt_epoch == 1
    assert applied.state.stagnant_observations == 0
    assert applied.state.replan_requested_count == 1
    assert applied.state.replan_applied_count == 1
    assert applied.state.decision_checkpoint_pending is False
    assert applied.decision.reason is DecisionReason.MODEL_REPLAN_APPLIED


def test_continue_checkpoint_acknowledges_request_without_claiming_replan_applied():
    policy = ExecutionProtocolPolicy(mode="guide", repetition_threshold=1)
    requested = transition_execution_protocol(
        _state(), _tool(current_step=1, repetition_count=1), policy
    )
    update = ModelPlanUpdate.from_persisted_mapping(
        {
            "decision": "continue",
            "horizon": "unknown",
            "milestone": "inspect current candidate",
            "next_action": "run one discriminating check",
            "verification_plan": "use the observed result to decide whether to replan",
            "completion_assessment": "in_progress",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        }
    )

    acknowledged = transition_execution_protocol(
        requested.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=update,
        ),
        policy,
    )

    assert requested.state.replan_requested_count == 1
    assert requested.state.replan_applied_count == 0
    assert requested.state.decision_checkpoint_pending is True
    assert acknowledged.state.replan_requested_count == 1
    assert acknowledged.state.replan_applied_count == 0
    assert acknowledged.state.decision_checkpoint_pending is False
    assert acknowledged.decision.reason is DecisionReason.MODEL_PLAN_CHECKPOINT


def test_convergence_constraint_stops_future_replan_requests_and_advances_stage():
    policy = ExecutionProtocolPolicy(mode="guide", repetition_threshold=1)
    requested = transition_execution_protocol(
        _armed_state(), _tool(current_step=1, repetition_count=1), policy
    )
    constrained = transition_execution_protocol(
        requested.state,
        ExecutionProtocolEvent(
            kind=EventKind.REPLAN_UNACKNOWLEDGED,
            convergence_stage=ConvergenceStage.PRODUCE_CANDIDATE,
        ),
        policy,
    )

    assert constrained.decision.action is ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    assert constrained.state.decision_checkpoint_pending is False
    assert constrained.state.convergence_constraint_active is True
    assert constrained.state.convergence_constraint_activation_count == 1
    assert constrained.state.history[-1].kind is EventKind.REPLAN_UNACKNOWLEDGED
    assert constrained.state.to_dict()["history"][-1]["kind"] == (
        "replan_unacknowledged"
    )

    repeated = transition_execution_protocol(
        constrained.state,
        _tool(current_step=2, repetition_count=2),
        policy,
    )
    assert repeated.decision.action is ControllerAction.CONTINUE
    assert repeated.decision.reason is DecisionReason.CONVERGENCE_CONSTRAINT_ACTIVE
    assert repeated.state.replan_requested_count == 1

    candidate = transition_execution_protocol(
        repeated.state,
        _tool(
            current_step=3,
            candidate_present=True,
            candidate_advanced=True,
            workspace_mutated=True,
        ),
        policy,
    )
    assert candidate.state.convergence_stage is (
        ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
    )


def test_unprofiled_constraint_event_cannot_activate_a_gate():
    transition = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.REPLAN_UNACKNOWLEDGED,
            convergence_stage=ConvergenceStage.PRODUCE_CANDIDATE,
        ),
        ExecutionProtocolPolicy(mode="guide"),
    )

    assert transition.decision.action is ControllerAction.CONTINUE
    assert transition.decision.reason is DecisionReason.MODEL_PROFILE_INSUFFICIENT
    assert transition.state.convergence_constraint_active is False


def test_repeated_post_candidate_reads_activate_convergence_without_replan():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=2,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    existing = transition_execution_protocol(
        _armed_state(),
        _tool(candidate_present=True, read_only_observed=True),
        policy,
    )
    assert existing.state.post_candidate_read_only_observations == 0
    assert existing.state.convergence_constraint_active is False

    advanced = transition_execution_protocol(
        existing.state,
        _tool(
            candidate_present=True,
            candidate_advanced=True,
            workspace_mutated=True,
        ),
        policy,
    )
    first = transition_execution_protocol(
        advanced.state,
        _tool(
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            candidate_present=True,
            read_only_observed=True,
        ),
        policy,
    )
    second = transition_execution_protocol(
        first.state,
        _tool(
            public_deliverable_declared=True,
            public_candidate_mutated=True,
            candidate_present=True,
            read_only_observed=True,
        ),
        policy,
    )

    assert second.decision.action is ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    assert second.decision.reason is DecisionReason.POST_CANDIDATE_STAGNATION
    assert second.state.convergence_stage is (
        ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
    )
    assert second.state.post_candidate_read_only_observations == 2
    assert second.state.replan_requested_count == 0


@pytest.mark.parametrize(
    ("consumed_fraction", "expected_active"),
    ((0.148, False), (0.40, True), (None, True)),
)
def test_post_candidate_hard_convergence_respects_deadline_checkpoint(
    consumed_fraction,
    expected_active,
):
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=1,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    candidate = transition_execution_protocol(
        _armed_state(),
        _tool(
            candidate_present=True,
            candidate_advanced=True,
            workspace_mutated=True,
            deadline_consumed_fraction=consumed_fraction,
        ),
        policy,
    )
    observed = transition_execution_protocol(
        candidate.state,
        _tool(
            candidate_present=True,
            read_only_observed=True,
            deadline_consumed_fraction=consumed_fraction,
        ),
        policy,
    )

    assert observed.state.convergence_constraint_active is expected_active
    assert (
        observed.decision.action
        is ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    ) is expected_active


@pytest.mark.parametrize(
    ("consumed_fraction", "expected_active"),
    ((0.148, False), (0.40, True), (None, True)),
)
def test_unapplied_replan_hard_convergence_respects_deadline_checkpoint(
    consumed_fraction,
    expected_active,
):
    transition = transition_execution_protocol(
        _armed_state(),
        ExecutionProtocolEvent(
            kind=EventKind.REPLAN_UNACKNOWLEDGED,
            convergence_stage=ConvergenceStage.PRODUCE_CANDIDATE,
            deadline_consumed_fraction=consumed_fraction,
        ),
        ExecutionProtocolPolicy(mode="guide"),
    )

    assert transition.state.convergence_constraint_active is expected_active
    assert (
        transition.decision.action
        is ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    ) is expected_active


def test_post_candidate_counter_tracks_all_no_delivery_progress_effects():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=2,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    candidate = transition_execution_protocol(
        _armed_state(),
        _tool(
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            workspace_mutated=True,
        ),
        policy,
    )
    scratch = transition_execution_protocol(
        candidate.state,
        _tool(
            candidate_present=True,
            workspace_mutated=True,
            known_mutation_executed=True,
        ),
        policy,
    )
    assert scratch.state.post_candidate_no_delivery_progress_observations == 1
    assert scratch.state.post_candidate_read_only_observations == 1

    unknown = transition_execution_protocol(
        scratch.state,
        _tool(candidate_present=True),
        policy,
    )
    assert unknown.decision.action is ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    assert unknown.state.post_candidate_no_delivery_progress_observations == 2


def test_only_new_delivery_high_water_resets_post_candidate_counter():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=4,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    first = transition_execution_protocol(
        _armed_state(),
        _tool(
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
        ),
        policy,
    )
    stagnant = transition_execution_protocol(
        first.state, _tool(candidate_present=True, workspace_mutated=True), policy
    )
    assert stagnant.state.post_candidate_no_delivery_progress_observations == 1

    new_hash = transition_execution_protocol(
        stagnant.state,
        _tool(
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
        ),
        policy,
    )
    assert new_hash.state.post_candidate_no_delivery_progress_observations == 0

    # A changed artifact that revisits an already-seen high-water fingerprint
    # is projected by the observation layer as candidate_advanced=False.
    oscillated = transition_execution_protocol(
        new_hash.state,
        _tool(
            candidate_present=True,
            candidate_advanced=False,
            delivery_progress_advanced=False,
            workspace_mutated=True,
        ),
        policy,
    )
    assert oscillated.state.post_candidate_no_delivery_progress_observations == 1


def test_explicit_candidate_checkpoint_enables_contractless_post_candidate_phase():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=1,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    checkpoint = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=_plan_update(intent="validate_candidate"),
        ),
        policy,
    )
    assert checkpoint.state.candidate_checkpoint_recorded is True

    constrained = transition_execution_protocol(
        checkpoint.state,
        _tool(read_only_observed=True),
        policy,
    )
    assert constrained.decision.action is (
        ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    )
    assert constrained.state.convergence_stage is (
        ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
    )

    removed = transition_execution_protocol(
        constrained.state,
        _tool(candidate_present=False, read_only_observed=True),
        policy,
    )
    assert removed.state.candidate_checkpoint_recorded is False
    assert removed.state.candidate_epoch_advanced is False
    assert removed.state.post_candidate_read_only_observations == 0
    assert removed.state.convergence_stage is ConvergenceStage.PRODUCE_CANDIDATE


def test_public_candidate_rollback_to_baseline_revokes_convergence_readiness():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=1,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    advanced = transition_execution_protocol(
        _state(),
        _tool(
            public_deliverable_declared=True,
            candidate_present=True,
            candidate_advanced=True,
            public_candidate_mutated=True,
            workspace_mutated=True,
        ),
        policy,
    )
    assert advanced.state.public_candidate_mutated is True

    rolled_back = transition_execution_protocol(
        advanced.state,
        _tool(
            public_deliverable_declared=True,
            candidate_present=True,
            candidate_advanced=True,
            public_candidate_mutated=False,
            workspace_mutated=True,
        ),
        policy,
    )
    assert rolled_back.state.candidate_epoch_advanced is True
    assert rolled_back.state.public_candidate_mutated is False
    assert rolled_back.state.candidate_checkpoint_recorded is False

    inspected = transition_execution_protocol(
        rolled_back.state,
        _tool(
            public_deliverable_declared=True,
            candidate_present=True,
            public_candidate_mutated=False,
            read_only_observed=True,
        ),
        policy,
    )
    assert inspected.state.post_candidate_read_only_observations == 0
    assert inspected.state.candidate_checkpoint_recorded is False
    assert inspected.state.acceptance_confirmed is False
    assert inspected.state.convergence_constraint_active is False


def test_present_unmutated_candidate_enters_bounded_validation_convergence():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=2,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )
    first = transition_execution_protocol(
        _armed_state(),
        _tool(
            public_deliverable_declared=True,
            candidate_present=True,
            public_candidate_mutated=False,
            read_only_observed=True,
            deadline_consumed_fraction=0.4,
        ),
        policy,
    )
    assert first.state.post_candidate_no_delivery_progress_observations == 1
    assert first.state.candidate_checkpoint_recorded is False
    assert first.state.convergence_constraint_active is False

    constrained = transition_execution_protocol(
        first.state,
        _tool(
            public_deliverable_declared=True,
            candidate_present=True,
            public_candidate_mutated=False,
            read_only_observed=True,
            deadline_consumed_fraction=0.4,
        ),
        policy,
    )

    assert constrained.decision.action is (
        ControllerAction.APPLY_CONVERGENCE_CONSTRAINT
    )
    assert constrained.state.convergence_stage is (
        ConvergenceStage.VALIDATE_REPAIR_OR_SUBMIT
    )
    assert constrained.state.candidate_checkpoint_recorded is False
    assert constrained.state.acceptance_confirmed is False


def test_contractless_presence_without_checkpoint_does_not_enter_convergence():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        post_candidate_read_only_threshold=1,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
    )

    observed = transition_execution_protocol(
        _armed_state(),
        _tool(
            candidate_present=True,
            candidate_advanced=False,
            read_only_observed=True,
            deadline_consumed_fraction=0.4,
        ),
        policy,
    )

    assert observed.state.post_candidate_no_delivery_progress_observations == 0
    assert observed.state.candidate_checkpoint_recorded is False
    assert observed.state.convergence_constraint_active is False


def test_model_plan_update_cannot_escape_pending_review_phase():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=True,
        independent_acceptance_enabled=False,
    )
    review = transition_execution_protocol(
        _state(), ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL), policy
    )
    update = ModelPlanUpdate.from_persisted_mapping(
        {
            "decision": "replan",
            "horizon": "long",
            "milestone": "repair candidate",
            "next_action": "change the output",
            "verification_plan": "rerun the public check",
            "completion_assessment": "in_progress",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": None,
        }
    )

    ignored = transition_execution_protocol(
        review.state,
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=update,
        ),
        policy,
    )

    assert ignored.decision.reason is DecisionReason.INVALID_EVENT
    assert ignored.state.phase is ProtocolPhase.REVIEW
    assert ignored.state.review_pending is True
    assert ignored.state.attempt_epoch == 0
    assert ignored.state.model_plan_update is None


def test_runtime_can_request_model_review_for_an_unarmed_candidate():
    transition = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        ExecutionProtocolPolicy(mode="guide", review_unarmed_candidates=True),
    )

    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert transition.decision.reason is DecisionReason.FINAL_REVIEW_REQUIRED
    assert transition.state.review_pending is True
    assert transition.state.long_horizon_armed is False


def test_structurally_unavailable_requested_review_is_unverified():
    transition = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL,
            result_hash="sha256:candidate",
            review_boundary_available=False,
        ),
        ExecutionProtocolPolicy(
            mode="guide",
            review_unarmed_candidates=True,
            independent_acceptance_enabled=False,
        ),
    )

    assert transition.decision.action is ControllerAction.STOP_INCOMPLETE
    assert transition.decision.reason is DecisionReason.REVIEW_BOUNDARY_UNAVAILABLE
    assert transition.state.phase is ProtocolPhase.REVIEW
    assert transition.state.terminal_incomplete is True
    assert transition.state.review_pending is False
    assert transition.state.final_review_count == 0


def test_strict_acceptance_never_submits_from_unavailable_review_claim():
    transition = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL,
            result_hash="sha256:candidate",
            review_boundary_available=False,
        ),
        ExecutionProtocolPolicy(
            mode="guide",
            review_unarmed_candidates=True,
            independent_acceptance_enabled=True,
        ),
    )

    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert transition.decision.action is not ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.state.phase is ProtocolPhase.REVIEW
    assert transition.state.review_pending is True


def test_observe_reports_would_replan_without_issuing_guidance():
    transition = transition_execution_protocol(
        _state(),
        _tool(repetition_count=3),
        ExecutionProtocolPolicy(mode="observe"),
    )

    assert transition.decision.action is ControllerAction.WOULD_REQUEST_REPLAN
    assert transition.state.replan_count == 1
    assert transition.state.long_horizon_armed is False


def test_guide_stops_requesting_replans_at_limit_without_revoking_tools():
    policy = ExecutionProtocolPolicy(
        mode="guide", repetition_threshold=2, max_replans=2
    )
    first = transition_execution_protocol(
        _armed_state(), _tool(repetition_count=2), policy
    )
    duplicate = transition_execution_protocol(
        first.state, _tool(repetition_count=4), policy
    )
    applied = transition_execution_protocol(
        duplicate.state,
        ExecutionProtocolEvent(kind=EventKind.REPLAN_APPLIED),
        policy,
    )
    second = transition_execution_protocol(
        applied.state, _tool(low_information_gain_count=3), policy
    )
    applied_again = transition_execution_protocol(
        second.state,
        ExecutionProtocolEvent(kind=EventKind.REPLAN_APPLIED),
        policy,
    )
    exhausted = transition_execution_protocol(
        applied_again.state, _tool(repetition_count=2), policy
    )

    assert first.decision.action is ControllerAction.REQUEST_REPLAN
    assert duplicate.decision.action is ControllerAction.CONTINUE
    assert second.decision.action is ControllerAction.REQUEST_REPLAN
    assert exhausted.decision.action is ControllerAction.CONTINUE
    assert exhausted.decision.reason is DecisionReason.REPLAN_LIMIT_REACHED
    assert exhausted.state.replan_count == 2
    assert exhausted.state.phase is ProtocolPhase.EXECUTE
    assert exhausted.state.finalization_entered is False

    repeated = transition_execution_protocol(
        exhausted.state, _tool(repetition_count=3), policy
    )
    assert repeated.decision.action is ControllerAction.CONTINUE
    assert repeated.decision.reason is DecisionReason.REPLAN_LIMIT_REACHED

    reserve = transition_execution_protocol(
        repeated.state,
        _tool(repetition_count=4, remaining_seconds=59.5),
        policy,
    )
    assert reserve.decision.action is ControllerAction.ENTER_FINALIZATION
    assert reserve.decision.reason is DecisionReason.FINALIZATION_RESERVE
    assert reserve.state.phase is ProtocolPhase.FINALIZE
    assert reserve.state.finalization_entered is True


def test_default_policy_keeps_replan_advice_available_until_deadline():
    policy = ExecutionProtocolPolicy(mode="guide", repetition_threshold=2)
    state = _state()

    for expected_count in range(1, 5):
        transition = transition_execution_protocol(
            state,
            _tool(repetition_count=2),
            policy,
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        assert transition.state.replan_count == expected_count
        state = transition_execution_protocol(
            transition.state,
            ExecutionProtocolEvent(kind=EventKind.REPLAN_APPLIED),
            policy,
        ).state


def test_default_policy_bounds_evidence_driven_review_repairs():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=True,
        independent_acceptance_enabled=False,
    )
    state = _state()

    first_review = transition_execution_protocol(
        state,
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL, result_hash="sha256:first"
        ),
        policy,
    )
    first_repair = transition_execution_protocol(
        first_review.state,
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        ),
        policy,
    )
    assert first_repair.decision.action is ControllerAction.REQUEST_REPAIR

    second_review = transition_execution_protocol(
        first_repair.state,
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL, result_hash="sha256:changed"
        ),
        policy,
    )
    second_repair = transition_execution_protocol(
        second_review.state,
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        ),
        policy,
    )

    assert second_review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert second_repair.decision.action is ControllerAction.STOP_INCOMPLETE
    assert second_repair.decision.reason is DecisionReason.REPAIR_LIMIT_REACHED
    assert second_repair.state.final_review_count == 2
    assert second_repair.state.repair_count == 1


@pytest.mark.parametrize("independent", [False, True])
def test_unchanged_candidate_evidence_basis_stops_before_second_review(
    independent,
):
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=True,
        independent_acceptance_enabled=independent,
    )
    first_review = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL, result_hash="sha256:unchanged"
        ),
        policy,
    )
    repair = transition_execution_protocol(
        first_review.state,
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        ),
        policy,
    )
    unchanged = transition_execution_protocol(
        repair.state,
        ExecutionProtocolEvent(
            kind=EventKind.CANDIDATE_FINAL, result_hash="sha256:unchanged"
        ),
        policy,
    )

    assert unchanged.decision.action is ControllerAction.STOP_INCOMPLETE
    assert unchanged.decision.reason is DecisionReason.REVIEW_BASIS_UNCHANGED
    assert unchanged.state.phase is ProtocolPhase.REVIEW
    assert unchanged.state.terminal_incomplete is True
    assert unchanged.state.final_review_count == 1


def test_observed_progress_resets_stagnation_counter():
    state = replace(_state(), stagnant_observations=5)
    transition = transition_execution_protocol(
        state,
        _tool(goal_progress_observable=True, goal_progress=True),
        ExecutionProtocolPolicy(mode="guide"),
    )

    assert transition.state.stagnant_observations == 0
    assert transition.decision.reason is DecisionReason.PROGRESS_OBSERVED


def test_unknown_progress_arms_observed_horizon_without_requesting_replan():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        activation_event_threshold=12,
        stagnation_event_threshold=3,
    )
    state = _state()
    decisions = []
    for step in range(1, 21):
        transition = transition_execution_protocol(
            state,
            _tool(
                current_step=step,
                goal_progress_observable=None,
                goal_progress=None,
            ),
            policy,
        )
        state = transition.state
        decisions.append(transition.decision.action)

    assert state.long_horizon_armed is True
    assert state.stagnant_observations == 0
    assert state.replan_count == 0
    assert ControllerAction.REQUEST_REPLAN not in decisions


def test_observable_no_progress_accumulates_and_requests_replan():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        activation_event_threshold=20,
        stagnation_event_threshold=3,
        no_goal_progress_threshold=20,
    )
    state = _state()
    for step in range(1, 4):
        transition = transition_execution_protocol(
            state,
            _tool(
                current_step=step,
                goal_progress_observable=True,
                goal_progress=False,
            ),
            policy,
        )
        state = transition.state

    assert transition.decision.action is ControllerAction.REQUEST_REPLAN
    assert state.stagnant_observations == 3
    assert state.long_horizon_armed is False


def test_finalization_reserve_is_generic_and_emitted_once():
    policy = ExecutionProtocolPolicy(mode="guide", finalization_reserve_seconds=60)
    first = transition_execution_protocol(
        _armed_state(), _tool(remaining_seconds=59.5), policy
    )
    second = transition_execution_protocol(
        first.state, _tool(remaining_seconds=20), policy
    )

    assert first.decision.action is ControllerAction.ENTER_FINALIZATION
    assert first.state.phase is ProtocolPhase.FINALIZE
    assert first.state.long_horizon_armed is True
    assert second.decision.action is ControllerAction.CONTINUE


def test_unprofiled_task_does_not_enter_finalization_reserve():
    transition = transition_execution_protocol(
        _state(),
        _tool(remaining_seconds=1),
        ExecutionProtocolPolicy(
            mode="guide",
            finalization_reserve_seconds=60,
        ),
    )

    assert transition.decision.action is ControllerAction.CONTINUE
    assert transition.state.phase is ProtocolPhase.EXECUTE
    assert transition.state.finalization_entered is False


def test_candidate_final_in_finalization_reserve_is_unverified():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        finalization_reserve_seconds=60,
        independent_acceptance_enabled=False,
    )
    finalizing = transition_execution_protocol(
        _armed_state(), _tool(remaining_seconds=59.5), policy
    )

    submitted = transition_execution_protocol(
        finalizing.state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert submitted.decision.action is ControllerAction.STOP_INCOMPLETE
    assert submitted.decision.reason is DecisionReason.FINALIZATION_RESERVE
    assert submitted.state.final_review_count == 0
    assert submitted.state.phase is ProtocolPhase.REVIEW
    assert submitted.state.terminal_incomplete is True

    forged_accept = transition_execution_protocol(
        submitted.state,
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.ACCEPT,
        ),
        policy,
    )
    assert forged_accept.decision.action is ControllerAction.STOP_INCOMPLETE
    assert forged_accept.decision.reason is DecisionReason.INVALID_EVENT
    assert forged_accept.state.phase is ProtocolPhase.REVIEW
    assert forged_accept.state.terminal_incomplete is True


def test_tool_event_threshold_never_overrides_model_horizon_ownership():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        activation_event_threshold=3,
        stagnation_event_threshold=9,
    )
    state = _state()
    for step in range(1, 4):
        state = transition_execution_protocol(
            state,
            _tool(current_step=step, evidence_advanced=True),
            policy,
        ).state

    assert state.long_horizon_armed is False


def test_default_threshold_arms_observed_horizon_and_requires_review() -> None:
    policy = ExecutionProtocolPolicy(mode="guide", independent_acceptance_enabled=False)
    state = _state()
    for step in range(1, 7):
        state = transition_execution_protocol(
            state,
            _tool(current_step=step, evidence_advanced=True),
            policy,
        ).state

    transition = transition_execution_protocol(
        state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert state.long_horizon_armed is True
    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW


def test_candidate_final_without_classification_cannot_consume_short_bypass():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        activation_event_threshold=12,
        independent_acceptance_enabled=False,
    )
    state = _state()
    for step in range(1, 12):
        state = transition_execution_protocol(
            state,
            _tool(current_step=step, evidence_advanced=True),
            policy,
        ).state

    transition = transition_execution_protocol(
        state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert transition.decision.reason is DecisionReason.FINAL_REVIEW_REQUIRED
    assert transition.state.long_horizon_armed is False
    assert transition.state.final_review_count == 1


def test_candidate_final_after_threshold_requests_review():
    policy = ExecutionProtocolPolicy(mode="guide", activation_event_threshold=2)
    state = _state()
    for step in range(1, 3):
        state = transition_execution_protocol(
            state,
            _tool(current_step=step, evidence_advanced=True),
            policy,
        ).state

    transition = transition_execution_protocol(
        state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert transition.state.final_review_count == 1


def test_history_is_bounded_independently_of_total_event_count():
    policy = ExecutionProtocolPolicy(mode="observe", history_limit=3)
    state = _state()
    for step in range(8):
        state = transition_execution_protocol(
            state,
            _tool(current_step=step, evidence_advanced=True),
            policy,
        ).state

    assert state.event_count == 8
    assert len(state.history) == 3
    assert [item.sequence for item in state.history] == [6, 7, 8]


def test_explicit_limits_keep_one_bounded_review_and_repair_compatibility():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        independent_acceptance_enabled=False,
        max_final_reviews=1,
        max_repairs=1,
    )
    review = transition_execution_protocol(
        _armed_state(), ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL), policy
    )
    repair = transition_execution_protocol(
        review.state,
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        ),
        policy,
    )
    final = transition_execution_protocol(
        repair.state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert repair.decision.action is ControllerAction.REQUEST_REPAIR
    assert final.decision.action is ControllerAction.STOP_INCOMPLETE
    assert final.state.final_review_count == 1
    assert final.state.repair_count == 1


def test_ordinary_tool_observation_during_review_does_not_enter_repair():
    policy = ExecutionProtocolPolicy(mode="guide", independent_acceptance_enabled=False)
    review = transition_execution_protocol(
        _armed_state(), ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL), policy
    )

    observed = transition_execution_protocol(
        review.state,
        _tool(current_step=9, evidence_advanced=True),
        policy,
    )

    assert observed.decision.action is ControllerAction.CONTINUE
    assert observed.state.phase is ProtocolPhase.REVIEW
    assert observed.state.review_pending is True
    assert observed.state.repair_count == 0


def test_uncertain_and_error_reviews_preserve_candidate_without_success():
    policy = ExecutionProtocolPolicy(mode="guide", independent_acceptance_enabled=False)
    for outcome, reason in (
        (ReviewOutcome.UNKNOWN, DecisionReason.REVIEW_UNCERTAIN),
        (ReviewOutcome.ERROR, DecisionReason.REVIEW_ERROR),
    ):
        pending = transition_execution_protocol(
            _armed_state(),
            ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
            policy,
        )
        transition = transition_execution_protocol(
            pending.state,
            ExecutionProtocolEvent(
                kind=EventKind.REVIEW_RESULT,
                review_outcome=outcome,
            ),
            policy,
        )
        assert transition.decision.action is ControllerAction.STOP_INCOMPLETE
        assert transition.decision.reason is reason


def test_unsolicited_review_result_cannot_force_a_repair():
    transition = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        ),
        ExecutionProtocolPolicy(mode="guide", independent_acceptance_enabled=False),
    )

    assert transition.decision.action is ControllerAction.STOP_INCOMPLETE
    assert transition.decision.reason is DecisionReason.INVALID_EVENT
    assert transition.state.repair_count == 0


def test_controller_exception_fails_closed_at_candidate_boundary():
    transition = safe_transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        None,
    )

    assert transition.decision.action is ControllerAction.STOP_INCOMPLETE
    assert transition.decision.reason is DecisionReason.CONTROLLER_ERROR
    assert transition.state.phase is ProtocolPhase.REVIEW
    assert transition.state.terminal_incomplete is True
