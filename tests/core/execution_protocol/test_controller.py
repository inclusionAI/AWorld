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


@pytest.mark.parametrize(
    "event",
    [
        _profile(horizon=ExecutionHorizon.SHORT),
        _profile(confidence=0.69),
        _profile(milestone_count=1, expected_tool_actions=5),
    ],
)
def test_model_profile_cannot_arm_without_credible_long_signal(event):
    transition = transition_execution_protocol(
        _state(), event, ExecutionProtocolPolicy(mode="guide")
    )

    assert transition.state.long_horizon_armed is False
    assert transition.state.model_execution_profile is not None


def test_short_model_profile_cannot_suppress_framework_tool_fallback():
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
    assert state.long_horizon_armed is True


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
    assert transition.state.long_horizon_armed is True


def test_guide_requests_one_replan_per_attempt_and_honors_limit():
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
    assert exhausted.decision.action is ControllerAction.ENTER_FINALIZATION
    assert exhausted.decision.reason is DecisionReason.REPLAN_LIMIT_REACHED
    assert exhausted.state.replan_count == 2
    assert exhausted.state.phase is ProtocolPhase.FINALIZE
    assert exhausted.state.finalization_entered is True

    repeated = transition_execution_protocol(
        exhausted.state, _tool(repetition_count=3), policy
    )
    assert repeated.decision.action is ControllerAction.CONTINUE
    assert repeated.decision.reason is DecisionReason.REPLAN_LIMIT_REACHED


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
    assert state.long_horizon_armed is True


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
    assert first.state.long_horizon_armed is True
    assert second.decision.action is ControllerAction.CONTINUE


def test_candidate_final_in_finalization_reserve_bypasses_review():
    policy = ExecutionProtocolPolicy(mode="guide", finalization_reserve_seconds=60)
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


def test_tool_event_threshold_arms_long_horizon_protocol():
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

    assert state.long_horizon_armed is True


def test_default_threshold_arms_after_six_tool_observations() -> None:
    policy = ExecutionProtocolPolicy(mode="guide")
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


def test_candidate_final_before_arming_bypasses_review_without_consuming_it():
    policy = ExecutionProtocolPolicy(mode="guide", activation_event_threshold=12)
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

    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.decision.reason is DecisionReason.SHORT_TASK_BYPASS
    assert transition.state.long_horizon_armed is False
    assert transition.state.final_review_count == 0


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


def test_first_candidate_final_gets_one_bounded_review_and_repair():
    policy = ExecutionProtocolPolicy(mode="guide")
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


def test_uncertain_and_error_reviews_fail_open_to_current_result():
    policy = ExecutionProtocolPolicy(mode="guide")
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
        ExecutionProtocolPolicy(mode="guide"),
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
