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

    # Every iteration either stops or admits at least one previously hidden
    # case, so the frozen case count is a strict loop bound.
    for _iteration in range(len(plan.case_ids) + len(plan.stages) + 1):
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
                return StagedMeasurementResult(
                    decision=boundary_decision,
                    schedules=tuple(schedules),
                    admitted_case_ids=tuple(
                        sorted(
                            case_id
                            for values in admitted_by_stage.values()
                            for case_id in values
                        )
                    ),
                    decision_history=tuple(
                        [*decision_history, boundary_decision]
                    ),
                )
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
    raise RuntimeError("adaptive measurement exceeded its frozen admission bound")


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
