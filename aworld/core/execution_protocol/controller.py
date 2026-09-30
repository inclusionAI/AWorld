"""Pure deterministic transitions for the long-horizon execution protocol."""

from __future__ import annotations

from dataclasses import replace

from .models import (
    ControllerAction,
    ControllerDecision,
    DecisionReason,
    EventKind,
    ExecutionHorizon,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ProtocolMode,
    ProtocolPhase,
    ProtocolTransition,
    ReviewOutcome,
)


def _decision(action: ControllerAction, reason: DecisionReason) -> ControllerDecision:
    return ControllerDecision(action=action, reason=reason)


def _observed_action(
    mode: ProtocolMode,
    *,
    guide: ControllerAction,
    observe: ControllerAction,
) -> ControllerAction:
    return guide if mode is ProtocolMode.GUIDE else observe


def _is_progress(event: ExecutionProtocolEvent) -> bool:
    return event.evidence_advanced or event.goal_progress is True


def _has_stagnation_signal(
    event: ExecutionProtocolEvent,
    policy: ExecutionProtocolPolicy,
) -> bool:
    return any(
        (
            event.repetition_count >= policy.repetition_threshold,
            event.low_information_gain_count >= policy.low_information_gain_threshold,
            event.goal_progress_observable is True and event.goal_progress is not True,
        )
    )


def _is_stagnant(
    state: ExecutionProtocolState,
    event: ExecutionProtocolEvent,
    policy: ExecutionProtocolPolicy,
) -> bool:
    if _is_progress(event):
        return False
    return _has_stagnation_signal(event, policy) and any(
        (
            event.repetition_count >= policy.repetition_threshold,
            event.low_information_gain_count >= policy.low_information_gain_threshold,
            event.no_goal_progress_count >= policy.no_goal_progress_threshold,
            state.stagnant_observations >= policy.stagnation_event_threshold,
        )
    )


def transition_execution_protocol(
    state: ExecutionProtocolState,
    event: ExecutionProtocolEvent,
    policy: ExecutionProtocolPolicy,
) -> ProtocolTransition:
    """Apply one event without side effects.

    Controller decisions never declare the user's task correct or failed.
    Uncertain/error review results submit the current candidate (fail open).
    """
    if not isinstance(state, ExecutionProtocolState):
        raise TypeError("state must be ExecutionProtocolState")
    if not isinstance(event, ExecutionProtocolEvent):
        raise TypeError("event must be ExecutionProtocolEvent")
    if not isinstance(policy, ExecutionProtocolPolicy):
        raise TypeError("policy must be ExecutionProtocolPolicy")
    if policy.mode is ProtocolMode.OFF:
        return ProtocolTransition(
            state=state,
            decision=_decision(ControllerAction.CONTINUE, DecisionReason.DISABLED),
        )

    next_state = state.append_event(event, history_limit=policy.history_limit)

    if event.kind is EventKind.MODEL_EXECUTION_PROFILE:
        if state.model_execution_profile is not None:
            return ProtocolTransition(
                next_state,
                _decision(
                    ControllerAction.CONTINUE,
                    DecisionReason.MODEL_PROFILE_ALREADY_RECORDED,
                ),
            )
        profile = event.model_execution_profile
        next_state = replace(next_state, model_execution_profile=profile)
        credible_long = (
            profile is not None
            and profile.horizon is ExecutionHorizon.LONG
            and profile.confidence
            >= policy.model_activation_confidence_threshold
            and (
                profile.milestone_count
                >= policy.model_activation_min_milestones
                or profile.expected_tool_actions
                >= policy.model_activation_min_tool_actions
            )
        )
        if credible_long:
            next_state = replace(next_state, long_horizon_armed=True)
            reason = DecisionReason.MODEL_LONG_HORIZON
        elif profile is not None and profile.horizon is ExecutionHorizon.SHORT:
            reason = DecisionReason.MODEL_SHORT_HORIZON
        else:
            reason = DecisionReason.MODEL_PROFILE_INSUFFICIENT
        return ProtocolTransition(
            next_state,
            _decision(ControllerAction.CONTINUE, reason),
        )

    if event.kind is EventKind.TOOL_OBSERVATION:
        if _is_progress(event):
            next_state = replace(next_state, stagnant_observations=0)
            reason = DecisionReason.PROGRESS_OBSERVED
        elif _has_stagnation_signal(event, policy):
            next_state = replace(
                next_state,
                stagnant_observations=next_state.stagnant_observations + 1,
            )
            reason = DecisionReason.OBSERVATION_RECORDED
        else:
            reason = DecisionReason.OBSERVATION_RECORDED

        stagnant = _is_stagnant(next_state, event, policy)
        should_arm = (
            next_state.tool_observation_count
            >= policy.activation_event_threshold
            or stagnant
        )
        if should_arm and not next_state.long_horizon_armed:
            next_state = replace(next_state, long_horizon_armed=True)

        reserve_reached = (
            event.remaining_seconds is not None
            and event.remaining_seconds <= policy.finalization_reserve_seconds
            and not next_state.finalization_entered
        )
        if reserve_reached:
            next_state = replace(
                next_state,
                phase=ProtocolPhase.FINALIZE,
                finalization_entered=True,
                long_horizon_armed=True,
            )
            action = _observed_action(
                policy.mode,
                guide=ControllerAction.ENTER_FINALIZATION,
                observe=ControllerAction.WOULD_ENTER_FINALIZATION,
            )
            return ProtocolTransition(
                next_state,
                _decision(action, DecisionReason.FINALIZATION_RESERVE),
            )

        if stagnant:
            if next_state.replan_count >= policy.max_replans:
                if next_state.finalization_entered:
                    return ProtocolTransition(
                        next_state,
                        _decision(
                            ControllerAction.CONTINUE,
                            DecisionReason.REPLAN_LIMIT_REACHED,
                        ),
                    )
                next_state = replace(
                    next_state,
                    phase=ProtocolPhase.FINALIZE,
                    finalization_entered=True,
                    long_horizon_armed=True,
                )
                action = _observed_action(
                    policy.mode,
                    guide=ControllerAction.ENTER_FINALIZATION,
                    observe=ControllerAction.WOULD_ENTER_FINALIZATION,
                )
                return ProtocolTransition(
                    next_state,
                    _decision(action, DecisionReason.REPLAN_LIMIT_REACHED),
                )
            if next_state.last_replan_attempt_epoch != next_state.attempt_epoch:
                next_state = replace(
                    next_state,
                    replan_count=next_state.replan_count + 1,
                    last_replan_attempt_epoch=next_state.attempt_epoch,
                )
                action = _observed_action(
                    policy.mode,
                    guide=ControllerAction.REQUEST_REPLAN,
                    observe=ControllerAction.WOULD_REQUEST_REPLAN,
                )
                return ProtocolTransition(
                    next_state,
                    _decision(action, DecisionReason.STAGNATION_DETECTED),
                )
        return ProtocolTransition(
            next_state,
            _decision(ControllerAction.CONTINUE, reason),
        )

    if event.kind is EventKind.REPLAN_APPLIED:
        next_state = replace(
            next_state,
            phase=ProtocolPhase.EXECUTE,
            attempt_epoch=next_state.attempt_epoch + 1,
            stagnant_observations=0,
        )
        return ProtocolTransition(
            next_state,
            _decision(ControllerAction.CONTINUE, DecisionReason.REPLAN_APPLIED),
        )

    if event.kind is EventKind.CANDIDATE_FINAL:
        next_state = replace(
            next_state,
            candidate_final_count=next_state.candidate_final_count + 1,
            finalization_entered=True,
        )
        if next_state.phase is ProtocolPhase.FINALIZE:
            next_state = replace(
                next_state,
                phase=ProtocolPhase.COMPLETE,
                review_pending=False,
            )
            return ProtocolTransition(
                next_state,
                _decision(
                    ControllerAction.SUBMIT_CURRENT_RESULT,
                    DecisionReason.FINALIZATION_RESERVE,
                ),
            )
        if not next_state.long_horizon_armed:
            next_state = replace(
                next_state,
                phase=ProtocolPhase.COMPLETE,
                review_pending=False,
            )
            return ProtocolTransition(
                next_state,
                _decision(
                    ControllerAction.SUBMIT_CURRENT_RESULT,
                    DecisionReason.SHORT_TASK_BYPASS,
                ),
            )
        if (
            next_state.final_review_count < policy.max_final_reviews
            and next_state.repair_count == 0
        ):
            next_state = replace(
                next_state,
                phase=ProtocolPhase.REVIEW,
                final_review_count=next_state.final_review_count + 1,
                review_pending=True,
            )
            action = _observed_action(
                policy.mode,
                guide=ControllerAction.REQUEST_FINAL_REVIEW,
                observe=ControllerAction.WOULD_REQUEST_FINAL_REVIEW,
            )
            return ProtocolTransition(
                next_state,
                _decision(action, DecisionReason.FINAL_REVIEW_REQUIRED),
            )
        next_state = replace(
            next_state,
            phase=ProtocolPhase.COMPLETE,
            review_pending=False,
        )
        return ProtocolTransition(
            next_state,
            _decision(
                ControllerAction.SUBMIT_CURRENT_RESULT,
                DecisionReason.FINAL_REVIEW_ALREADY_USED,
            ),
        )

    if event.kind is EventKind.REVIEW_RESULT:
        if not next_state.review_pending:
            next_state = replace(next_state, phase=ProtocolPhase.COMPLETE)
            return ProtocolTransition(
                next_state,
                _decision(
                    ControllerAction.SUBMIT_CURRENT_RESULT,
                    DecisionReason.INVALID_EVENT,
                ),
            )
        next_state = replace(next_state, review_pending=False)
        if event.review_outcome is ReviewOutcome.REPAIR:
            if next_state.repair_count < policy.max_repairs:
                next_state = replace(
                    next_state,
                    phase=ProtocolPhase.REPAIR,
                    repair_count=next_state.repair_count + 1,
                )
                action = _observed_action(
                    policy.mode,
                    guide=ControllerAction.REQUEST_REPAIR,
                    observe=ControllerAction.WOULD_REQUEST_REPAIR,
                )
                return ProtocolTransition(
                    next_state,
                    _decision(action, DecisionReason.REVIEW_REPAIR_REQUESTED),
                )
            reason = DecisionReason.REPAIR_LIMIT_REACHED
        elif event.review_outcome is ReviewOutcome.ACCEPT:
            reason = DecisionReason.REVIEW_ACCEPTED
        elif event.review_outcome is ReviewOutcome.ERROR:
            reason = DecisionReason.REVIEW_ERROR
        else:
            reason = DecisionReason.REVIEW_UNCERTAIN
        next_state = replace(next_state, phase=ProtocolPhase.COMPLETE)
        return ProtocolTransition(
            next_state,
            _decision(ControllerAction.SUBMIT_CURRENT_RESULT, reason),
        )

    raise ValueError("unsupported execution protocol event")


def safe_transition_execution_protocol(
    state: ExecutionProtocolState,
    event: ExecutionProtocolEvent,
    policy: ExecutionProtocolPolicy,
) -> ProtocolTransition:
    """Fail open if protocol control code cannot process an event."""
    try:
        return transition_execution_protocol(state, event, policy)
    except Exception:
        final_boundary = isinstance(event, ExecutionProtocolEvent) and event.kind in {
            EventKind.CANDIDATE_FINAL,
            EventKind.REVIEW_RESULT,
        }
        action = (
            ControllerAction.SUBMIT_CURRENT_RESULT
            if final_boundary
            else ControllerAction.CONTINUE
        )
        return ProtocolTransition(
            state=state,
            decision=_decision(action, DecisionReason.CONTROLLER_ERROR),
        )


__all__ = ["safe_transition_execution_protocol", "transition_execution_protocol"]
