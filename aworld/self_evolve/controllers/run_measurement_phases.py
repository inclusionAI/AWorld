"""Measurement planning and search-projection phase factory."""

from __future__ import annotations

from aworld.self_evolve.controllers.run_phase_context import RunPhaseContext

from typing import Mapping, Sequence

from aworld.self_evolve.credit_assignment import (
    TargetSelectionReport,
)
from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
)
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementSummary,
)
from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
)
from aworld.self_evolve.controllers.run_resources import (
    CandidateAttemptTracker as _CandidateAttemptTracker,
    RunBudgetContext as _RunBudgetContext,
)
from aworld.self_evolve.controllers.measurement import (
    MeasurementPlanningRequest,
    MeasurementPlanningRuntime,
    MeasurementSearchProjectionExecution,
    MeasurementSearchProjectionRequest,
    MeasurementSearchProjectionRuntime,
)
from aworld.self_evolve.budget import (
    CandidateAttemptKey,
)
from aworld.self_evolve.optimizers.base import (
    CandidateSourceDisposition,
)
from aworld.self_evolve.provenance import (
    TargetProvenance,
)
from aworld.self_evolve.replay import (
    CandidateReplayRequest,
    CandidateReplayResult,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.targets import (
    SelfEvolveTarget,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
)


class MeasurementPhaseFactory:
    def __init__(self, context: RunPhaseContext) -> None:
        self.context = context

    def _load_measurement_resume_request(
        self,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayRequest | None:
        return self.context.construction.controllers.measurement_planning.load_resume_request(
            candidate=candidate,
            dataset=dataset,
        )

    def _plan_candidate_measurement(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        candidate_count: int,
        experiment_registry: dict[object, ControlledExperimentSpec] | None = None,
        experiment_key: object | None = None,
        selection_protocol: str = "predeclared_authoritative_candidate",
        repetitions: int | None = None,
        minimum_independent_cases: int | None = None,
    ) -> ControlledExperimentSpec | None:
        registry = (
            self.context.construction.measurement.experiments
            if experiment_registry is None
            else experiment_registry
        )
        result = self.context.construction.controllers.measurement_planning.plan(
            MeasurementPlanningRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=candidate,
                candidate_count=candidate_count,
                experiment_key=experiment_key,
                selection_protocol=selection_protocol,
                repetitions=repetitions,
                minimum_independent_cases=minimum_independent_cases,
                environment_fingerprint=(
                    self.context.construction.mutable.replay_adaptation.environment_fingerprints.get(run_id)
                ),
                target_intent=(
                    self.context.state.active_target_intent.value
                    if self.context.state.active_target_intent is not None
                    else None
                ),
                allow_resume=experiment_registry is None,
            ),
            MeasurementPlanningRuntime(experiments=registry),
        )
        return result.experiment

    def _materialize_candidate_measurement(
        self,
        *,
        experiment: ControlledExperimentSpec,
        materialization_run_id: str,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
        replay_result: CandidateReplayResult | None,
        replay_dataset: SelfEvolveDataset | None,
        baseline_summary: EvaluationSummary | None,
        candidate_summary: EvaluationSummary | None,
        candidate_count: int,
        authoritative_candidate_count: int,
        target_selection_report: TargetSelectionReport | None,
    ) -> MeasurementSummary:
        return self.context.construction.controllers.measurement.materialize_candidate(
            experiment=experiment,
            materialization_run_id=materialization_run_id,
            candidate=candidate,
            dataset=dataset,
            replay_result=replay_result,
            replay_dataset=replay_dataset,
            baseline_summary=baseline_summary,
            candidate_summary=candidate_summary,
            candidate_count=candidate_count,
            authoritative_candidate_count=authoritative_candidate_count,
            target_selection_report=target_selection_report,
        )

    def _measurement_search_projection_execution(
        self,
    ) -> MeasurementSearchProjectionExecution:
        return MeasurementSearchProjectionExecution(
            MeasurementSearchProjectionRuntime(
                store=self.context.construction.runtime.store,
                experiments=self.context.construction.measurement.experiments,
                summaries=self.context.construction.measurement.summaries,
                min_score_delta=self.context.construction.policy.min_score_delta,
            )
        )

    def _attach_measurement_search_performance(
        self,
        *,
        run_id: str,
        summary: MeasurementSummary,
        candidates: Sequence[CandidateVariant],
        iteration_reports: Sequence[Mapping[str, object]],
    ) -> MeasurementSummary:
        return (
            self.context.require_operations().measurement_search_projection_execution()
            .execute(
                MeasurementSearchProjectionRequest(
                    run_id=run_id,
                    summary=summary,
                    candidates=tuple(candidates),
                    iteration_reports=tuple(iteration_reports),
                )
            )
            .summary
        )

    async def _evaluate_iteration_candidate(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        apply_policy: str,
        target_provenance: TargetProvenance | None,
        target_provenance_unresolved_reason: str | None = None,
        target_selection_report: TargetSelectionReport | None = None,
        iteration_number: int,
        candidate_number: int,
        candidate_count: int,
        rejected_candidate_ids: set[str],
        accepted_candidate_ids: set[str],
        baseline_replay_dir: str | None = None,
        capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
        attempt_key: CandidateAttemptKey | None = None,
        attempt_tracker: _CandidateAttemptTracker | None = None,
        budget_context: _RunBudgetContext | None = None,
        precomputed_gate_results: tuple[GateResult, ...] = (),
        source_disposition: CandidateSourceDisposition = CandidateSourceDisposition(),
        baseline_evaluation_cache: dict[str, EvaluationSummary] | None = None,
        allow_score_tiebreak: bool = True,
    ) -> tuple[dict[str, object], dict[str, object], tuple[EvaluationSummary, ...]]:
        """Compatibility adapter for the historical keyword-only boundary."""

        result = await self.context.require_operations().execute_iteration_candidate(
            CandidateEvaluationRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=candidate,
                apply_policy=apply_policy,
                target_provenance=target_provenance,
                target_provenance_unresolved_reason=(
                    target_provenance_unresolved_reason
                ),
                target_selection_report=target_selection_report,
                iteration_number=iteration_number,
                candidate_number=candidate_number,
                candidate_count=candidate_count,
                rejected_candidate_ids=rejected_candidate_ids,
                accepted_candidate_ids=accepted_candidate_ids,
                baseline_replay_dir=baseline_replay_dir,
                capability_requirements=capability_requirements,
                attempt_key=attempt_key,
                attempt_tracker=attempt_tracker,
                budget_context=budget_context,
                precomputed_gate_results=precomputed_gate_results,
                source_disposition=source_disposition,
                baseline_evaluation_cache=baseline_evaluation_cache,
                allow_score_tiebreak=allow_score_tiebreak,
            )
        )
        return result.as_tuple()
