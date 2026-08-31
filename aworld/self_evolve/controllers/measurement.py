"""Controlled-measurement materialization and promotion policy."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.campaign_policy import (
    rebase_measurement_experiment_for_materialization,
)
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    ComponentIdentity,
    ExperimentBudget,
    FrozenIdentities,
    MeasurementEarlyStopPolicy,
    MeasurementObservation,
    MeasurementPolicyMode,
    MeasurementSummary,
    MeasurementUsage,
    OutcomePlan,
    SamplingPlan,
    SwapAxis,
    TargetResolutionConfidence,
    build_attribution_report,
    observations_from_evaluation,
    observations_from_replay,
    observations_with_evaluation_metric,
    observations_with_usage_fallback,
    stable_measurement_fingerprint,
)
from aworld.self_evolve.replay import (
    CandidateReplayRequest,
    CandidateReplayResult,
    _candidate_replay_request_from_mapping,
    _is_replayable_user_task_case,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
)


_rebase_measurement_experiment_for_materialization = (
    rebase_measurement_experiment_for_materialization
)


@dataclass(frozen=True)
class MeasurementPlanningIdentities:
    """Static component identities frozen for every experiment in one runner."""

    task_model: str
    generator: str
    scheduler: str
    evaluator: str
    runtime: str


@dataclass(frozen=True)
class MeasurementPlanningConfig:
    """Validated static policy consumed by measurement planning and resume."""

    mode: MeasurementPolicyMode
    identities: MeasurementPlanningIdentities
    resume_run_id: str | None
    replay_resume_dir: str | None
    replay_enabled: bool
    replay_backend_available: bool
    baseline_replay_repetitions: int
    candidate_replay_repetitions: int
    replay_repetitions_explicit: bool
    judge_repetitions: int
    evaluation_backend_available: bool
    minimum_independent_cases: int
    primary_metric: str
    minimum_effect: float
    confidence_level: float
    bootstrap_samples: int
    early_stop_policy: MeasurementEarlyStopPolicy
    total_run_token_budget: int | None
    per_attempt_replay_token_limit: int | None
    max_run_cost_usd: Decimal | None
    max_run_wall_seconds: Decimal | None
    replay_timeout_seconds: int

    def __post_init__(self) -> None:
        if self.resume_run_id is not None and self.replay_resume_dir is None:
            raise ValueError(
                "measurement authority resume requires its replay directory"
            )
        if not self.primary_metric.strip():
            raise ValueError("measurement primary metric must be non-empty")
        object.__setattr__(self, "primary_metric", self.primary_metric.strip())


@dataclass(frozen=True)
class MeasurementPlanningRequest:
    """Dynamic inputs for one candidate's frozen experiment authority."""

    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidate: CandidateVariant
    candidate_count: int
    experiment_key: object | None = None
    selection_protocol: str = "predeclared_authoritative_candidate"
    repetitions: int | None = None
    minimum_independent_cases: int | None = None
    environment_fingerprint: str | None = None
    target_intent: str | None = None
    allow_resume: bool = True

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("measurement planning run_id must be non-empty")
        if not self.selection_protocol.strip():
            raise ValueError(
                "measurement selection protocol must be non-empty"
            )
        object.__setattr__(self, "run_id", self.run_id.strip())
        object.__setattr__(
            self,
            "selection_protocol",
            self.selection_protocol.strip(),
        )


@dataclass
class MeasurementPlanningRuntime:
    """Mutable experiment registry owned by the calling run facade."""

    experiments: dict[object, ControlledExperimentSpec]


@dataclass(frozen=True)
class MeasurementPlanningResult:
    """Frozen experiment plus whether its authority came from a prior run."""

    experiment: ControlledExperimentSpec | None
    resumed: bool = False


@dataclass(frozen=True)
class MeasurementPlanningController:
    """Plans or resumes controlled experiments without reading Runner state."""

    store: FilesystemSelfEvolveStore
    config: MeasurementPlanningConfig

    def load_resume_request(
        self,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayRequest | None:
        """Load and validate the original frozen measurement authority."""

        resume_run_id = self.config.resume_run_id
        if resume_run_id is None:
            return None
        replay_resume_dir = self.config.replay_resume_dir
        if replay_resume_dir is None:
            raise ValueError("measurement resume replay directory is missing")
        request_path = Path(replay_resume_dir) / "request.json"
        request = _candidate_replay_request_from_mapping(
            _load_measurement_json_mapping(request_path)
        )
        if request.run_id != resume_run_id:
            raise ValueError("measurement resume request belongs to another run")
        if request.candidate_id != candidate.candidate_id:
            raise ValueError("measurement resume request candidate changed")
        if request.target != candidate.target:
            raise ValueError("measurement resume request target changed")
        if (
            request.measurement_plan is None
            or request.measurement_isolation_decision is None
            or request.measurement_evidence_policy_profile is None
        ):
            raise ValueError(
                "measurement resume request has no frozen v2 authority"
            )
        if (
            request.dataset_fingerprint
            != request.measurement_plan.dataset_fingerprint
        ):
            raise ValueError(
                "measurement resume request dataset authority drifted"
            )
        available_case_ids = {case.case_id for case in dataset.cases}
        missing_case_ids = (
            set(request.measurement_plan.case_ids) - available_case_ids
        )
        if missing_case_ids:
            raise ValueError(
                "measurement resume view no longer covers frozen plan cases: "
                + ",".join(sorted(missing_case_ids))
            )
        if (
            request.measurement_plan.candidate_fingerprint
            != candidate_package_fingerprint(candidate)
        ):
            raise ValueError("measurement resume candidate package changed")
        return request

    def plan(
        self,
        request: MeasurementPlanningRequest,
        runtime: MeasurementPlanningRuntime,
    ) -> MeasurementPlanningResult:
        config = self.config
        if config.mode is MeasurementPolicyMode.OFF:
            return MeasurementPlanningResult(experiment=None)
        key = (
            (request.run_id, request.candidate.candidate_id)
            if request.experiment_key is None
            else request.experiment_key
        )
        existing = runtime.experiments.get(key)
        if existing is not None:
            return MeasurementPlanningResult(experiment=existing)
        if request.allow_resume and config.resume_run_id is not None:
            source_request = self.load_resume_request(
                candidate=request.candidate,
                dataset=request.dataset,
            )
            assert source_request is not None
            assert source_request.measurement_plan is not None
            experiment = self.store.read_measurement_experiment(
                config.resume_run_id,
                source_request.measurement_plan.experiment_id,
            )
            if experiment.run_id != config.resume_run_id:
                raise ValueError(
                    "measurement resume experiment authority changed"
                )
            if (
                experiment.treatment.fingerprint
                != candidate_package_fingerprint(request.candidate)
            ):
                raise ValueError(
                    "measurement resume treatment identity changed"
                )
            runtime.experiments[key] = experiment
            runtime.experiments[
                (experiment.run_id, request.candidate.candidate_id)
            ] = experiment
            return MeasurementPlanningResult(
                experiment=experiment,
                resumed=True,
            )
        replay_planned = bool(
            config.replay_enabled
            and request.candidate.target.target_type == "skill"
            and config.replay_backend_available
        )
        measurement_case_ids = tuple(
            case.case_id
            for case in request.dataset.cases
            if not replay_planned or _is_replayable_user_task_case(case)
        )
        if not measurement_case_ids:
            if config.mode is MeasurementPolicyMode.REQUIRED:
                raise ValueError(
                    "required measurement has no executable user-task cases"
                )
            return MeasurementPlanningResult(experiment=None)
        configured_repetitions = (
            request.repetitions
            if request.repetitions is not None
            else (
                max(
                    config.baseline_replay_repetitions,
                    config.candidate_replay_repetitions,
                )
                if replay_planned
                else config.judge_repetitions
                if config.evaluation_backend_available
                else 1
            )
        )
        effective_repetitions = (
            1
            if (
                request.repetitions is None
                and replay_planned
                and not config.replay_repetitions_explicit
                and len(measurement_case_ids)
                >= max(2, config.minimum_independent_cases)
            )
            else configured_repetitions
        )
        dataset_fingerprint = replay_dataset_fingerprint(request.dataset)
        budget_identity = {
            "total_run_token_budget": config.total_run_token_budget,
            "per_attempt_replay_token_limit": (
                config.per_attempt_replay_token_limit
            ),
            "max_run_cost_usd": (
                str(config.max_run_cost_usd)
                if config.max_run_cost_usd is not None
                else None
            ),
            "max_run_wall_seconds": (
                str(config.max_run_wall_seconds)
                if config.max_run_wall_seconds is not None
                else None
            ),
            "candidate_opportunities": request.candidate_count,
        }
        experiment = ControlledExperimentSpec.create(
            run_id=request.run_id,
            mode=config.mode,
            swap_axis=SwapAxis.ARTIFACT,
            control=ComponentIdentity(
                component_id="artifact-control",
                fingerprint=request.target.fingerprint_current_content(),
            ),
            treatment=ComponentIdentity(
                component_id="artifact-treatment",
                fingerprint=candidate_package_fingerprint(request.candidate),
            ),
            frozen_identities=FrozenIdentities(
                task_model=config.identities.task_model,
                generator=config.identities.generator,
                scheduler=config.identities.scheduler,
                evaluator=config.identities.evaluator,
                dataset=dataset_fingerprint,
                environment=(
                    request.environment_fingerprint
                    or stable_measurement_fingerprint(
                        {
                            "workspace_contract": "aworld-local-workspace",
                            "replay_enabled": config.replay_enabled,
                        }
                    )
                ),
                runtime=config.identities.runtime,
                prompt_context=stable_measurement_fingerprint(
                    {
                        "target_type": request.target.identity.target_type,
                        "target_id": request.target.identity.target_id,
                        "target_intent": request.target_intent,
                        "dataset": dataset_fingerprint,
                    }
                ),
                budget=stable_measurement_fingerprint(budget_identity),
            ),
            sampling=SamplingPlan(
                independent_case_ids=measurement_case_ids,
                repetitions_per_case=effective_repetitions,
                seeds=tuple(range(1, effective_repetitions + 1)),
            ),
            outcomes=OutcomePlan(
                primary_metric=config.primary_metric,
                secondary_metrics=tuple(
                    metric
                    for metric in (
                        "task_success",
                        "score",
                        "latency_ms",
                        "total_tokens",
                    )
                    if metric != config.primary_metric
                ),
                minimum_effect=config.minimum_effect,
                non_regression_threshold=0.0,
                confidence_level=config.confidence_level,
                minimum_independent_cases=(
                    config.minimum_independent_cases
                    if request.minimum_independent_cases is None
                    else request.minimum_independent_cases
                ),
                bootstrap_samples=config.bootstrap_samples,
            ),
            budgets=ExperimentBudget(
                search=MeasurementUsage(
                    tokens=config.total_run_token_budget,
                    cost_usd=(
                        float(config.max_run_cost_usd)
                        if config.max_run_cost_usd is not None
                        else None
                    ),
                    wall_seconds=(
                        float(config.max_run_wall_seconds)
                        if config.max_run_wall_seconds is not None
                        else None
                    ),
                    candidate_opportunities=request.candidate_count,
                ),
                measurement=MeasurementUsage(
                    tokens=config.per_attempt_replay_token_limit,
                    wall_seconds=(
                        float(config.replay_timeout_seconds)
                        if replay_planned
                        else None
                    ),
                ),
            ),
            search_visible_case_ids=tuple(
                case_id
                for case_id in request.dataset.recipe.trainable_case_ids
                if case_id in measurement_case_ids
            ),
            selection_protocol=request.selection_protocol,
            stopping_policy=config.early_stop_policy,
        )
        self.store.write_measurement_experiment(experiment)
        runtime.experiments[key] = experiment
        return MeasurementPlanningResult(experiment=experiment)


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


def measurement_component_identity(
    component: object | None,
) -> dict[str, object]:
    """Project a stable, serialization-safe component identity."""

    if component is None:
        return {"kind": "disabled"}
    identity: dict[str, object] = {
        "module": type(component).__module__,
        "class": type(component).__qualname__,
    }
    for name in (
        "optimizer_name",
        "optimizer_version",
        "model_profile",
        "backend_ref",
        "version",
    ):
        value = getattr(component, name, None)
        if value is None or isinstance(value, (str, int, float, bool)):
            if value is not None:
                identity[name] = value
    return identity


def _load_measurement_json_mapping(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected JSON object in {path}")
    return payload


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
