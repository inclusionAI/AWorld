from dataclasses import replace

import pytest

from aworld.core.execution_protocol import (
    ControllerAction,
    DecisionReason,
    EventKind,
    ExecutionHorizon,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ModelExecutionProfile,
    ModelPlanUpdate,
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


def _tool(**kwargs):
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
    update = ModelPlanUpdate.from_mapping(
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
    update = ModelPlanUpdate.from_mapping(
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


def test_model_plan_update_cannot_escape_pending_review_phase():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=True,
        independent_acceptance_enabled=False,
    )
    review = transition_execution_protocol(
        _state(), ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL), policy
    )
    update = ModelPlanUpdate.from_mapping(
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
        ExecutionProtocolPolicy(
            mode="guide", review_unarmed_candidates=True
        ),
    )

    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert transition.decision.reason is DecisionReason.FINAL_REVIEW_REQUIRED
    assert transition.state.review_pending is True
    assert transition.state.long_horizon_armed is False


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
    first = transition_execution_protocol(_state(), _tool(repetition_count=2), policy)
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


def test_default_policy_allows_multiple_evidence_driven_review_repairs():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        review_unarmed_candidates=True,
        independent_acceptance_enabled=False,
    )
    state = _state()

    for expected_count in range(1, 4):
        review = transition_execution_protocol(
            state,
            ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
            policy,
        )
        assert review.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
        repair = transition_execution_protocol(
            review.state,
            ExecutionProtocolEvent(
                kind=EventKind.REVIEW_RESULT,
                review_outcome=ReviewOutcome.REPAIR,
            ),
            policy,
        )
        assert repair.decision.action is ControllerAction.REQUEST_REPAIR
        assert repair.state.repair_count == expected_count
        state = repair.state


def test_observed_progress_resets_stagnation_counter():
    state = replace(_state(), stagnant_observations=5)
    transition = transition_execution_protocol(
        state,
        _tool(goal_progress_observable=True, goal_progress=True),
        ExecutionProtocolPolicy(mode="guide"),
    )

    assert transition.state.stagnant_observations == 0
    assert transition.decision.reason is DecisionReason.PROGRESS_OBSERVED


def test_unknown_progress_does_not_accumulate_stagnation_or_request_replan():
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

    assert state.long_horizon_armed is False
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
        _state(), _tool(remaining_seconds=59.5), policy
    )
    second = transition_execution_protocol(
        first.state, _tool(remaining_seconds=20), policy
    )

    assert first.decision.action is ControllerAction.ENTER_FINALIZATION
    assert first.state.phase is ProtocolPhase.FINALIZE
    assert first.state.long_horizon_armed is False
    assert second.decision.action is ControllerAction.CONTINUE


def test_candidate_final_in_finalization_reserve_bypasses_review():
    policy = ExecutionProtocolPolicy(
        mode="guide",
        finalization_reserve_seconds=60,
        independent_acceptance_enabled=False,
    )
    finalizing = transition_execution_protocol(
        _state(), _tool(remaining_seconds=59.5), policy
    )

    submitted = transition_execution_protocol(
        finalizing.state,
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        policy,
    )

    assert submitted.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert submitted.decision.reason is DecisionReason.FINALIZATION_RESERVE
    assert submitted.state.final_review_count == 0
    assert submitted.state.phase is ProtocolPhase.COMPLETE


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


def test_default_threshold_does_not_classify_but_unknown_requires_review() -> None:
    policy = ExecutionProtocolPolicy(
        mode="guide", independent_acceptance_enabled=False
    )
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

    assert state.long_horizon_armed is False
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
    assert final.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert final.state.final_review_count == 1
    assert final.state.repair_count == 1


def test_ordinary_tool_observation_during_review_does_not_enter_repair():
    policy = ExecutionProtocolPolicy(
        mode="guide", independent_acceptance_enabled=False
    )
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


def test_uncertain_and_error_reviews_fail_open_to_current_result():
    policy = ExecutionProtocolPolicy(
        mode="guide", independent_acceptance_enabled=False
    )
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
        assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
        assert transition.decision.reason is reason


def test_unsolicited_review_result_cannot_force_a_repair():
    transition = transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(
            kind=EventKind.REVIEW_RESULT,
            review_outcome=ReviewOutcome.REPAIR,
        ),
        ExecutionProtocolPolicy(
            mode="guide", independent_acceptance_enabled=False
        ),
    )

    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.decision.reason is DecisionReason.INVALID_EVENT
    assert transition.state.repair_count == 0


def test_controller_exception_fails_open_at_candidate_boundary():
    transition = safe_transition_execution_protocol(
        _state(),
        ExecutionProtocolEvent(kind=EventKind.CANDIDATE_FINAL),
        None,
    )

    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.decision.reason is DecisionReason.CONTROLLER_ERROR
