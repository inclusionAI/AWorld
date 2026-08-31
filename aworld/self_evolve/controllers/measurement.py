"""Controlled-measurement materialization and promotion policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aworld.self_evolve.campaign_policy import (
    rebase_measurement_experiment_for_materialization,
)
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementObservation,
    MeasurementSummary,
    MeasurementUsage,
    TargetResolutionConfidence,
    build_attribution_report,
    observations_from_evaluation,
    observations_from_replay,
    observations_with_evaluation_metric,
    observations_with_usage_fallback,
)
from aworld.self_evolve.replay import CandidateReplayResult
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
)


@dataclass(frozen=True)
class CandidateMeasurementController:
    """Materializes one frozen experiment into durable attribution evidence."""

    store: FilesystemSelfEvolveStore
    primary_metric: str
    summaries: dict[tuple[str, str], MeasurementSummary]

    def __post_init__(self) -> None:
        if not isinstance(self.primary_metric, str) or not self.primary_metric.strip():
            raise ValueError("primary_metric must be non-empty")
        object.__setattr__(self, "primary_metric", self.primary_metric.strip())

    def materialize_candidate(
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
        experiment = rebase_measurement_experiment_for_materialization(
            experiment,
            run_id=materialization_run_id,
        )
        self.store.write_measurement_experiment(experiment)
        key = (experiment.run_id, candidate.candidate_id)
        cached = self.summaries.get(key)
        if cached is not None:
            return cached
        replay_observations: tuple[MeasurementObservation, ...] = ()
        if replay_result is not None:
            replay_observations = observations_from_replay(
                experiment,
                dataset=dataset,
                replay_result=replay_result,
                run_root=self.store.run_path(experiment.run_id),
            )
        evaluation_observations = observations_from_evaluation(
            experiment,
            dataset=replay_dataset or dataset,
            baseline_summary=baseline_summary,
            candidate_summary=candidate_summary,
        )
        if evaluation_observations and replay_observations:
            evaluation_observations = observations_with_usage_fallback(
                evaluation_observations,
                replay_observations,
            )
        if (
            self.primary_metric != "task_success"
            and evaluation_observations
            and replay_observations
        ):
            observations = observations_with_evaluation_metric(
                replay_observations,
                evaluation_observations,
                metric=self.primary_metric,
            )
        elif self.primary_metric != "task_success" and evaluation_observations:
            observations = evaluation_observations
        else:
            observations = replay_observations or evaluation_observations
        if observations:
            self.store.append_measurement_observations(
                experiment.run_id,
                experiment.experiment_id,
                observations,
            )
        usage = complete_measurement_usage(
            observations,
            candidate_opportunities=candidate_count,
        )
        target_resolution = measurement_target_resolution(target_selection_report)
        admitted_primary_case_ids: tuple[str, ...] | None = None
        if replay_result is not None and isinstance(
            replay_result.measurement_decision,
            Mapping,
        ):
            raw_admitted = replay_result.measurement_decision.get(
                "baseline_qualified_case_ids"
            )
            if isinstance(raw_admitted, (list, tuple)):
                frozen_primary = set(experiment.sampling.independent_case_ids)
                admitted_primary_case_ids = tuple(
                    case_id
                    for case_id in raw_admitted
                    if isinstance(case_id, str) and case_id in frozen_primary
                )
        attribution = build_attribution_report(
            experiment,
            observations,
            target_resolution=target_resolution,
            total_usage=usage,
            generated_candidate_count=candidate_count,
            authoritative_candidate_count=authoritative_candidate_count,
            admitted_primary_case_ids=admitted_primary_case_ids,
        )
        self.store.write_measurement_attribution_report(attribution)
        summary = attribution.summary(
            attribution_report_path=self.store.measurement_attribution_ref(
                experiment.run_id,
                experiment.experiment_id,
            )
        )
        self.summaries[key] = summary
        return summary


def complete_measurement_usage(
    observations: Sequence[MeasurementObservation],
    *,
    candidate_opportunities: int,
) -> MeasurementUsage:
    if not observations or not all(item.usage.complete for item in observations):
        return MeasurementUsage(candidate_opportunities=candidate_opportunities)
    cost_complete = all(item.usage.cost_usd is not None for item in observations)
    return MeasurementUsage(
        tokens=sum(item.usage.tokens or 0 for item in observations),
        cost_usd=(
            sum(item.usage.cost_usd or 0.0 for item in observations)
            if cost_complete
            else None
        ),
        wall_seconds=sum(item.usage.wall_seconds or 0.0 for item in observations),
        candidate_opportunities=candidate_opportunities,
    )


def measurement_target_resolution(
    report: TargetSelectionReport | None,
) -> TargetResolutionConfidence:
    if report is None:
        return TargetResolutionConfidence(
            confidence=1.0,
            origin="direct_target_argument",
            inference_bypassed=True,
        )
    raw_origin = report.selection_origin
    origin = str(getattr(raw_origin, "value", raw_origin) or "unknown")
    causal_confidence: float | None = None
    diagnostics = report.diagnostics
    if isinstance(diagnostics, Mapping):
        raw_causal = diagnostics.get("causal_confidence")
        if (
            isinstance(raw_causal, (int, float))
            and not isinstance(raw_causal, bool)
            and 0 <= float(raw_causal) <= 1
        ):
            causal_confidence = float(raw_causal)
    return TargetResolutionConfidence(
        confidence=float(report.confidence),
        origin=origin,
        inference_bypassed=origin not in {"inferred", "model_inferred"},
        causal_confidence=causal_confidence,
    )


def measurement_promotion_gate(summary: MeasurementSummary) -> GateResult:
    return GateResult(
        gate_name="trusted_improvement_measurement",
        passed=summary.promotion_eligible,
        reason=(
            "controlled measurement supports candidate promotion"
            if summary.promotion_eligible
            else (
                "controlled measurement blocked promotion: "
                f"{summary.decision_reason}"
            )
        ),
        details={
            "failure_class": None if summary.promotion_eligible else "measurement",
            "code": (
                None
                if summary.promotion_eligible
                else "trusted_improvement_not_established"
            ),
            "experiment_id": summary.experiment_id,
            "validity_status": summary.validity_status.value,
            "effect_direction": summary.effect_direction.value,
            "confidence_lower_bound": summary.confidence_lower_bound,
            "budget_normalized": summary.budget_normalized,
            "next_action": summary.next_action.value,
        },
    )
