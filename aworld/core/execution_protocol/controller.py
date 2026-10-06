"""Pure deterministic transitions for the long-horizon execution protocol."""

from __future__ import annotations

from dataclasses import replace

from .models import (
    ControllerAction,
    ControllerDecision,
    DeliveryIntent,
    DecisionReason,
    EventKind,
    ExecutionHorizon,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    NextActionAlignment,
    PlanUpdateDecision,
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


def _model_horizon(state: ExecutionProtocolState) -> ExecutionHorizon:
    """Return only the model's latest explicit horizon classification."""
    if state.model_plan_update is not None:
        return state.model_plan_update.horizon
    if state.model_execution_profile is not None:
        return state.model_execution_profile.horizon
    return ExecutionHorizon.UNKNOWN


def _next_action_alignment(
    state: ExecutionProtocolState,
    event: ExecutionProtocolEvent,
) -> NextActionAlignment | None:
    """Compare one observed outcome with the model's typed next-action intent.

    Natural-language plan text is deliberately not parsed.  When AWorld has no
    public observation capable of distinguishing a match, the receipt is
    explicitly unobservable rather than guessed.
    """

    if not state.next_action_alignment_pending or state.model_plan_update is None:
        return None
    intent = state.model_plan_update.delivery_intent
    if intent is DeliveryIntent.UNKNOWN:
        return None
    expected_tool = state.model_plan_update.next_action_tool
    expected_signature = state.model_plan_update.next_action_signature
    if intent is DeliveryIntent.SUBMIT_UNCERTAIN:
        # Any Tool observation contradicts the model's declared terminal step.
        return NextActionAlignment.MISMATCHED
    if (
        expected_tool is None
        or expected_signature is None
        or not event.observed_action_names
        or not event.observed_action_signatures
    ):
        return NextActionAlignment.UNOBSERVABLE
    if expected_tool not in event.observed_action_names:
        return NextActionAlignment.MISMATCHED
    if expected_signature not in event.observed_action_signatures:
        return NextActionAlignment.MISMATCHED
    if intent is DeliveryIntent.CONTINUE_EXPLORATION:
        matched = event.new_information_observed
    elif intent is DeliveryIntent.PRODUCE_CANDIDATE:
        if event.candidate_present is None:
            return NextActionAlignment.UNOBSERVABLE
        matched = event.candidate_advanced
    elif intent is DeliveryIntent.VALIDATE_CANDIDATE:
        # An ordinary Tool result may be a valid model-authored check without
        # carrying AWorld's optional public-probe receipt. Absence of that
        # receipt is not evidence that validation failed.
        if not event.validation_observed:
            return NextActionAlignment.UNOBSERVABLE
        matched = True
    else:
        return NextActionAlignment.UNOBSERVABLE
    return NextActionAlignment.MATCHED if matched else NextActionAlignment.MISMATCHED


def _checkpoint_transition(
    state: ExecutionProtocolState,
    policy: ExecutionProtocolPolicy,
    *,
    reason: DecisionReason,
    candidate_present: bool | None = None,
) -> ProtocolTransition:
    if policy.max_replans is not None and state.replan_count >= policy.max_replans:
        return ProtocolTransition(
            state,
            _decision(
                ControllerAction.CONTINUE,
                DecisionReason.REPLAN_LIMIT_REACHED,
            ),
        )
    if reason is DecisionReason.DELIVERY_DEBT_DETECTED:
        state = replace(
            state,
            delivery_checkpoint_count=state.delivery_checkpoint_count + 1,
            last_delivery_debt_attempt_epoch=state.attempt_epoch,
        )
    elif reason is DecisionReason.CANDIDATE_DECISION_RESERVE:
        state = replace(
            state,
            candidate_decision_count=state.candidate_decision_count + 1,
        )
    next_state = replace(
        state,
        replan_count=state.replan_count + 1,
        replan_requested_count=(
            state.replan_requested_count + 1
            if policy.mode is ProtocolMode.GUIDE
            else state.replan_requested_count
        ),
        decision_checkpoint_pending=policy.mode is ProtocolMode.GUIDE,
        decision_checkpoint_reason=(
            reason if policy.mode is ProtocolMode.GUIDE else None
        ),
        decision_checkpoint_candidate_present=(
            candidate_present if policy.mode is ProtocolMode.GUIDE else None
        ),
        last_replan_attempt_epoch=state.attempt_epoch,
    )
    action = _observed_action(
        policy.mode,
        guide=ControllerAction.REQUEST_REPLAN,
        observe=ControllerAction.WOULD_REQUEST_REPLAN,
    )
    return ProtocolTransition(next_state, _decision(action, reason))


def transition_execution_protocol(
    state: ExecutionProtocolState,
    event: ExecutionProtocolEvent,
    policy: ExecutionProtocolPolicy,
) -> ProtocolTransition:
    """Apply one event without side effects.

    Controller decisions never declare the user's task correct or failed.
    A Tool observation during review remains an observation; only an explicit
    REVIEW_RESULT event can enter repair. Uncertain/error critic results never
    become acceptance.
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
        # The model owns the semantic workflow.  A typed long-horizon
        # declaration arms the protocol directly; confidence, milestone, and
        # action estimates remain telemetry rather than a second framework
        # decision that can silently override the model.
        credible_long = profile is not None and profile.horizon is ExecutionHorizon.LONG
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

    if event.kind is EventKind.MODEL_PLAN_UPDATE:
        if state.phase not in {ProtocolPhase.EXECUTE, ProtocolPhase.REPAIR} or (
            state.review_pending
        ):
            return ProtocolTransition(
                next_state,
                _decision(ControllerAction.CONTINUE, DecisionReason.INVALID_EVENT),
            )
        update = event.model_plan_update
        requested_replan_pending = state.decision_checkpoint_pending
        candidate_decision_acknowledged = (
            state.decision_checkpoint_reason
            is DecisionReason.CANDIDATE_DECISION_RESERVE
        )
        terminal_intent = bool(
            update is not None
            and update.delivery_intent
            in {
                DeliveryIntent.SUBMIT_CURRENT,
                DeliveryIntent.SUBMIT_UNCERTAIN,
            }
        )
        alignment_pending = bool(
            update is not None
            and update.delivery_intent is not DeliveryIntent.UNKNOWN
            and not terminal_intent
        )
        next_state = replace(
            next_state,
            model_plan_update=update,
            long_horizon_armed=(
                update is not None and update.horizon is ExecutionHorizon.LONG
            ),
            phase=(
                ProtocolPhase.FINALIZE if terminal_intent else ProtocolPhase.EXECUTE
            ),
            finalization_entered=(next_state.finalization_entered or terminal_intent),
            attempt_epoch=next_state.attempt_epoch + 1,
            stagnant_observations=0,
            decision_checkpoint_pending=False,
            decision_checkpoint_reason=None,
            decision_checkpoint_candidate_present=None,
            candidate_decision_recorded=(
                next_state.candidate_decision_recorded
                or candidate_decision_acknowledged
            ),
            next_action_alignment_pending=alignment_pending,
            pending_next_action_plan_sequence=(
                next_state.event_count if alignment_pending else None
            ),
            replan_applied_count=(
                next_state.replan_applied_count + 1
                if update is not None
                and update.decision is PlanUpdateDecision.REPLAN
                and requested_replan_pending
                else next_state.replan_applied_count
            ),
        )
        reason = (
            DecisionReason.MODEL_REPLAN_APPLIED
            if update is not None
            and update.decision is PlanUpdateDecision.REPLAN
            and requested_replan_pending
            else DecisionReason.MODEL_PLAN_CHECKPOINT
        )
        return ProtocolTransition(
            next_state,
            _decision(ControllerAction.CONTINUE, reason),
        )

    if event.kind in {EventKind.TOOL_OBSERVATION, EventKind.DELIVERY_STATUS}:
        if event.kind is EventKind.DELIVERY_STATUS:
            reason = DecisionReason.OBSERVATION_RECORDED
        else:
            candidate_missing = bool(
                event.public_deliverable_declared and event.candidate_present is False
            )
            next_state = replace(
                next_state,
                delivery_debt_observations=(
                    next_state.delivery_debt_observations + 1
                    if candidate_missing
                    else 0
                ),
                workspace_mutation_absent_observations=(
                    next_state.workspace_mutation_absent_observations + 1
                    if candidate_missing and not event.workspace_mutated
                    else 0
                ),
            )

            alignment = _next_action_alignment(state, event)
            if alignment is not None:
                next_state = replace(
                    next_state,
                    next_action_alignment_pending=False,
                    pending_next_action_plan_sequence=None,
                    last_action_alignment=alignment,
                    last_action_alignment_plan_sequence=(
                        state.pending_next_action_plan_sequence
                    ),
                    last_action_alignment_observation_sequence=(next_state.event_count),
                    action_alignment_match_count=(
                        next_state.action_alignment_match_count + 1
                        if alignment is NextActionAlignment.MATCHED
                        else next_state.action_alignment_match_count
                    ),
                    action_alignment_mismatch_count=(
                        next_state.action_alignment_mismatch_count + 1
                        if alignment is NextActionAlignment.MISMATCHED
                        else next_state.action_alignment_mismatch_count
                    ),
                )

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

        stagnant = (
            _is_stagnant(next_state, event, policy)
            if event.kind is EventKind.TOOL_OBSERVATION
            else False
        )
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

        model_long = next_state.long_horizon_armed
        # A named public deliverable is an observable delivery obligation even
        # when the model classified the task as short.  Activate the existing
        # delivery checkpoints without changing the model-owned horizon or
        # introducing task- or benchmark-specific validation rules.
        delivery_protocol_active = bool(
            model_long or event.public_deliverable_declared
        )
        candidate_decision_due = bool(
            delivery_protocol_active
            and next_state.candidate_decision_count == 0
            and not next_state.decision_checkpoint_pending
            and not next_state.next_action_alignment_pending
            and event.remaining_seconds is not None
            and event.remaining_seconds <= policy.candidate_decision_reserve_seconds
            and event.remaining_seconds > policy.finalization_reserve_seconds
        )
        if candidate_decision_due:
            return _checkpoint_transition(
                next_state,
                policy,
                reason=DecisionReason.CANDIDATE_DECISION_RESERVE,
                candidate_present=event.candidate_present,
            )

        if event.kind is EventKind.DELIVERY_STATUS:
            return ProtocolTransition(
                next_state,
                _decision(ControllerAction.CONTINUE, reason),
            )

        alignment_mismatched = (
            next_state.last_action_alignment is NextActionAlignment.MISMATCHED
            and next_state.last_action_alignment_observation_sequence
            == next_state.event_count
        )
        if (
            alignment_mismatched
            and model_long
            and not next_state.decision_checkpoint_pending
        ):
            return _checkpoint_transition(
                next_state,
                policy,
                reason=DecisionReason.NEXT_ACTION_MISMATCH,
                candidate_present=event.candidate_present,
            )

        delivery_debt = bool(
            delivery_protocol_active
            and not (
                next_state.last_action_alignment is NextActionAlignment.MATCHED
                and next_state.last_action_alignment_observation_sequence
                == next_state.event_count
            )
            and event.public_deliverable_declared
            and event.candidate_present is False
            and (
                next_state.delivery_debt_observations
                >= policy.delivery_debt_observation_threshold
                or next_state.workspace_mutation_absent_observations
                >= policy.delivery_debt_observation_threshold
            )
        )
        if (
            delivery_debt
            and not next_state.decision_checkpoint_pending
            and next_state.last_delivery_debt_attempt_epoch != next_state.attempt_epoch
        ):
            return _checkpoint_transition(
                next_state,
                policy,
                reason=DecisionReason.DELIVERY_DEBT_DETECTED,
                candidate_present=False,
            )

        if stagnant:
            if (
                policy.max_replans is not None
                and next_state.replan_count >= policy.max_replans
            ):
                # Exhausting the bounded advisory budget only suppresses more
                # checkpoint injection.  The model retains authority to decide
                # whether to continue or change approach.  This signal is not
                # evidence that the task is complete and cannot revoke tools;
                # tool-free finalization is reserved for the caller-owned
                # deadline window handled above.
                return ProtocolTransition(
                    next_state,
                    _decision(
                        ControllerAction.CONTINUE,
                        DecisionReason.REPLAN_LIMIT_REACHED,
                    ),
                )
            if next_state.last_replan_attempt_epoch != next_state.attempt_epoch:
                return _checkpoint_transition(
                    next_state,
                    policy,
                    reason=DecisionReason.STAGNATION_DETECTED,
                    candidate_present=event.candidate_present,
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
            decision_checkpoint_pending=False,
            decision_checkpoint_reason=None,
            decision_checkpoint_candidate_present=None,
            replan_applied_count=(
                next_state.replan_applied_count + 1
                if state.decision_checkpoint_pending
                else next_state.replan_applied_count
            ),
        )
        return ProtocolTransition(
            next_state,
            _decision(ControllerAction.CONTINUE, DecisionReason.REPLAN_APPLIED),
        )

    if event.kind is EventKind.REPLAN_UNACKNOWLEDGED:
        next_state = replace(
            next_state,
            phase=ProtocolPhase.EXECUTE,
            stagnant_observations=0,
            decision_checkpoint_pending=False,
            decision_checkpoint_reason=None,
            decision_checkpoint_candidate_present=None,
            last_replan_attempt_epoch=None,
        )
        return ProtocolTransition(
            next_state,
            _decision(
                ControllerAction.CONTINUE,
                DecisionReason.MODEL_REPLAN_UNACKNOWLEDGED,
            ),
        )

    if event.kind is EventKind.CANDIDATE_FINAL:
        next_state = replace(
            next_state,
            candidate_final_count=next_state.candidate_final_count + 1,
            finalization_entered=True,
        )
        if next_state.phase is ProtocolPhase.FINALIZE:
            if policy.independent_acceptance_enabled:
                next_state = replace(
                    next_state,
                    phase=ProtocolPhase.COMPLETE,
                    review_pending=False,
                )
                return ProtocolTransition(
                    next_state,
                    _decision(
                        ControllerAction.STOP_INCOMPLETE,
                        DecisionReason.ACCEPTANCE_EVIDENCE_MISSING,
                    ),
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
                    DecisionReason.FINALIZATION_RESERVE,
                ),
            )
        if (
            not next_state.long_horizon_armed
            and not policy.review_unarmed_candidates
            and not policy.independent_acceptance_enabled
            and _model_horizon(next_state) is ExecutionHorizon.SHORT
        ):
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
        # Review and repair limits are independent compatibility controls.
        # ``None`` means caller-deadline bounded; an explicit review limit must
        # never be widened by the repair setting.
        review_budget = policy.max_final_reviews
        if review_budget is None or next_state.final_review_count < review_budget:
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
        if policy.independent_acceptance_enabled:
            return ProtocolTransition(
                next_state,
                _decision(
                    ControllerAction.STOP_INCOMPLETE,
                    DecisionReason.ACCEPTANCE_EVIDENCE_MISSING,
                ),
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
            action = (
                ControllerAction.STOP_INCOMPLETE
                if policy.independent_acceptance_enabled
                else ControllerAction.SUBMIT_CURRENT_RESULT
            )
            return ProtocolTransition(
                next_state,
                _decision(
                    action,
                    DecisionReason.INVALID_EVENT,
                ),
            )
        next_state = replace(next_state, review_pending=False)
        if event.review_outcome is ReviewOutcome.REPAIR or (
            policy.independent_acceptance_enabled
            and event.review_outcome
            in {
                ReviewOutcome.UNCERTAIN,
                ReviewOutcome.UNKNOWN,
                ReviewOutcome.ERROR,
            }
        ):
            if (
                policy.max_repairs is None
                or next_state.repair_count < policy.max_repairs
            ):
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
                reason = (
                    DecisionReason.REVIEW_REPAIR_REQUESTED
                    if event.review_outcome is ReviewOutcome.REPAIR
                    else DecisionReason.REVIEW_ERROR
                    if event.review_outcome is ReviewOutcome.ERROR
                    else DecisionReason.REVIEW_UNCERTAIN
                )
                return ProtocolTransition(next_state, _decision(action, reason))
            reason = DecisionReason.REPAIR_LIMIT_REACHED
        elif event.review_outcome is ReviewOutcome.ACCEPT:
            next_state = replace(next_state, acceptance_confirmed=True)
            reason = DecisionReason.REVIEW_ACCEPTED
        elif event.review_outcome is ReviewOutcome.ERROR:
            reason = DecisionReason.REVIEW_ERROR
        else:
            reason = DecisionReason.REVIEW_UNCERTAIN
        next_state = replace(next_state, phase=ProtocolPhase.COMPLETE)
        action = (
            ControllerAction.SUBMIT_CURRENT_RESULT
            if event.review_outcome is ReviewOutcome.ACCEPT
            or not policy.independent_acceptance_enabled
            else ControllerAction.STOP_INCOMPLETE
        )
        return ProtocolTransition(next_state, _decision(action, reason))

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
        action = ControllerAction.CONTINUE
        if final_boundary:
            action = (
                ControllerAction.STOP_INCOMPLETE
                if isinstance(policy, ExecutionProtocolPolicy)
                and policy.independent_acceptance_enabled
                else ControllerAction.SUBMIT_CURRENT_RESULT
            )
        return ProtocolTransition(
            state=state,
            decision=_decision(action, DecisionReason.CONTROLLER_ERROR),
        )


__all__ = ["safe_transition_execution_protocol", "transition_execution_protocol"]
