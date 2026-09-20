"""Adaptive stage orchestration over the authoritative pair-lane scheduler."""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Generic, Sequence, TypeVar

from aworld.self_evolve.measurement_control import (
    AdaptiveDecision,
    AdaptiveDecisionKind,
    MeasurementPlanV2,
    MeasurementProgressSummary,
    SamplingStageKind,
    decide_staged_measurement,
)
from aworld.self_evolve.measurement_scheduler import (
    ControlAdmission,
    FrameworkFilesystemLaneMaterializer,
    PairExecutor,
    PairLaneScheduleResult,
    PairLaneStopKind,
    ReusedControlResolver,
    ScheduleObserver,
    StopDecision,
    TreatmentExecutor,
    load_measurement_schedule_bundle,
    schedule_pair_lanes,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


ControlT = TypeVar("ControlT")
TreatmentT = TypeVar("TreatmentT")

ProgressBuilder = Callable[
    [
        MeasurementPlanV2,
        str,
        tuple[PairLaneScheduleResult[ControlT, TreatmentT], ...],
    ],
    MeasurementProgressSummary | Awaitable[MeasurementProgressSummary],
]


@dataclass(frozen=True)
class StagedMeasurementResult(Generic[ControlT, TreatmentT]):
    decision: AdaptiveDecision
    schedules: tuple[PairLaneScheduleResult[ControlT, TreatmentT], ...]
    admitted_case_ids: tuple[str, ...]
    decision_history: tuple[AdaptiveDecision, ...] = ()


async def schedule_staged_measurement(
    store: FilesystemSelfEvolveStore,
    *,
    run_id: str,
    measurement_plan_fingerprint: str,
    run_control: PairExecutor[ControlT],
    run_treatment: TreatmentExecutor[ControlT, TreatmentT],
    progress_builder: ProgressBuilder[ControlT, TreatmentT],
    lane_materializer: FrameworkFilesystemLaneMaterializer,
    resolve_reused_control: ReusedControlResolver[ControlT] | None = None,
    control_allows_treatment: ControlAdmission[ControlT] | None = None,
    pair_should_stop: StopDecision[ControlT, TreatmentT] | None = None,
    pair_decisive_stop_kind: AdaptiveDecisionKind | None = None,
    observer: ScheduleObserver | None = None,
    checkpoint_quantum_seconds: float | None = None,
    campaign_deadline_monotonic: float | None = None,
    materialization_timeout_seconds: float | None = None,
    configured_lane_limit: int = 2,
) -> StagedMeasurementResult[ControlT, TreatmentT]:
    """Run only stages and case batches admitted by the frozen policy.

    Candidate-visible repair cases never enter this function because the plan
    compiler excludes them. Expansion is bounded by each stage's batch size;
    regression/transfer work is admitted only by a positive-effect decision.
    """

    bundle = load_measurement_schedule_bundle(
        store,
        run_id=run_id,
        measurement_plan_fingerprint=measurement_plan_fingerprint,
    )
    plan = bundle.plan
    if (pair_should_stop is None) != (pair_decisive_stop_kind is None):
        raise ValueError(
            "pair stop callback and typed adaptive stop kind must be supplied together"
        )
    if pair_decisive_stop_kind is not None:
        pair_decisive_stop_kind = AdaptiveDecisionKind(
            pair_decisive_stop_kind
        )
        if not pair_decisive_stop_kind.value.startswith("stop_"):
            raise ValueError("pair decisive stop kind must be terminal")
    sentinel = plan.stages[0]
    if sentinel.kind is not SamplingStageKind.SENTINEL:
        raise ValueError("measurement plan must start with a sentinel stage")
    current_stage_id = sentinel.stage_id
    current_batch_case_ids = set(sentinel.case_ids)
    admitted_by_stage: dict[str, set[str]] = {
        sentinel.stage_id: set(sentinel.case_ids)
    }
    schedules: list[PairLaneScheduleResult[ControlT, TreatmentT]] = []
    decision_history: list[AdaptiveDecision] = []
    orchestration_started = time.monotonic()
    prior_pending_by_scope: dict[
        tuple[str, tuple[str, ...]],
        frozenset[tuple[str, int]],
    ] = {}

    def finish(
        decision: AdaptiveDecision,
    ) -> StagedMeasurementResult[ControlT, TreatmentT]:
        return StagedMeasurementResult(
            decision=decision,
            schedules=tuple(schedules),
            admitted_case_ids=tuple(
                sorted(
                    case_id
                    for values in admitted_by_stage.values()
                    for case_id in values
                )
            ),
            decision_history=tuple(decision_history),
        )

    # A scheduler boundary advances one frozen case/repetition pair, not
    # necessarily a whole case.  Derive the safety horizon from the immutable
    # work graph so multi-repetition plans cannot exhaust a case-count bound.
    pair_coordinates = {
        (unit.stage_id, unit.case_id, unit.repetition_id)
        for unit in plan.work_units
    }
    maximum_transitions = len(pair_coordinates) + len(plan.stages) + 1
    for _iteration in range(maximum_transitions):
        remaining_quantum = checkpoint_quantum_seconds
        if checkpoint_quantum_seconds is not None:
            remaining_quantum = checkpoint_quantum_seconds - (
                time.monotonic() - orchestration_started
            )
            if remaining_quantum <= 0:
                boundary_decision = AdaptiveDecision(
                    kind=(
                        AdaptiveDecisionKind.MEASUREMENT_INCOMPLETE_CHECKPOINT
                    ),
                    reason_code="checkpoint_quantum_expired",
                    resume_safe=True,
                )
                decision_history.append(boundary_decision)
                return finish(boundary_decision)
        admitted_cases = tuple(sorted(current_batch_case_ids))
        schedule = await schedule_pair_lanes(
            store,
            run_id=run_id,
            measurement_plan_fingerprint=measurement_plan_fingerprint,
            run_control=run_control,
            run_treatment=run_treatment,
            lane_materializer=lane_materializer,
            resolve_reused_control=resolve_reused_control,
            control_allows_treatment=control_allows_treatment,
            should_stop=pair_should_stop,
            observer=observer,
            checkpoint_quantum_seconds=remaining_quantum,
            campaign_deadline_monotonic=campaign_deadline_monotonic,
            materialization_timeout_seconds=materialization_timeout_seconds,
            configured_lane_limit=configured_lane_limit,
            admitted_stage_ids=(current_stage_id,),
            admitted_case_ids=admitted_cases,
        )
        schedules.append(schedule)
        progress = progress_builder(plan, current_stage_id, tuple(schedules))
        if inspect.isawaitable(progress):
            progress = await progress
        if not isinstance(progress, MeasurementProgressSummary):
            raise TypeError("progress builder must return MeasurementProgressSummary")
        if schedule.stop_kind is PairLaneStopKind.CAMPAIGN_DEADLINE:
            progress = _with_boundary(progress, campaign=True)
        elif schedule.stop_kind is PairLaneStopKind.CHECKPOINT_QUANTUM:
            progress = _with_boundary(progress, checkpoint=True)
        elif schedule.stop_kind is PairLaneStopKind.DECISIVE_STOP:
            # Do not wait for every repetition of a case to finish before
            # propagating the scheduler's typed stop.  The caller must bind
            # the callback to an explicit adaptive terminal kind so a string
            # reason can never silently change causal ownership.
            assert pair_decisive_stop_kind is not None
            decision = AdaptiveDecision(
                kind=pair_decisive_stop_kind,
                reason_code=schedule.stop_reason,
                resume_safe=(
                    pair_decisive_stop_kind
                    is AdaptiveDecisionKind.STOP_FRAMEWORK_BLOCKED
                ),
            )
            decision_history.append(decision)
            return finish(decision)
        decision = decide_staged_measurement(plan, progress)
        decision_history.append(decision)
        if decision.kind in {
            AdaptiveDecisionKind.CONTINUE_CURRENT_STAGE,
            AdaptiveDecisionKind.ADMIT_EXPANSION,
            AdaptiveDecisionKind.ADMIT_TIE_BREAK,
            AdaptiveDecisionKind.ADMIT_REQUIRED_REGRESSION_TRANSFER,
        }:
            next_stage_id = decision.next_stage_id
            if next_stage_id is None:
                raise ValueError("adaptive admission omitted its next stage")
            newly_admitted = set(decision.admit_case_ids)
            if decision.kind is AdaptiveDecisionKind.CONTINUE_CURRENT_STAGE:
                if schedule.pending:
                    scope = (current_stage_id, admitted_cases)
                    pending_coordinates = frozenset(
                        item.coordinate for item in schedule.pending
                    )
                    prior_pending = prior_pending_by_scope.get(scope)
                    if (
                        prior_pending is not None
                        and not pending_coordinates < prior_pending
                    ):
                        blocked = AdaptiveDecision(
                            kind=AdaptiveDecisionKind.STOP_FRAMEWORK_BLOCKED,
                            reason_code="measurement_scheduler_no_progress",
                            resume_safe=True,
                        )
                        decision_history.append(blocked)
                        return finish(blocked)
                    prior_pending_by_scope[scope] = pending_coordinates
                    continue
                raise ValueError(
                    "adaptive policy requested current stage without pending work"
                )
            prior = admitted_by_stage.setdefault(next_stage_id, set())
            if not newly_admitted or newly_admitted.issubset(prior):
                raise ValueError("adaptive admission made no forward progress")
            prior.update(newly_admitted)
            current_stage_id = next_stage_id
            current_batch_case_ids = newly_admitted
            continue
        return finish(decision)
    blocked = AdaptiveDecision(
        kind=AdaptiveDecisionKind.STOP_FRAMEWORK_BLOCKED,
        reason_code="measurement_scheduler_admission_bound_inconsistent",
        resume_safe=True,
    )
    decision_history.append(blocked)
    return finish(blocked)


def _with_boundary(
    progress: MeasurementProgressSummary,
    *,
    checkpoint: bool = False,
    campaign: bool = False,
) -> MeasurementProgressSummary:
    return replace(
        progress,
        checkpoint_quantum_expired=checkpoint,
        campaign_wall_deadline_expired=campaign,
        resume_safe=True,
    )
