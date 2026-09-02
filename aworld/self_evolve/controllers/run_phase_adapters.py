"""Compatibility and signature adapters shared by composed run phases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aworld.self_evolve.controllers.measurement import (
    MeasurementPlanningController,
    measurement_component_identity,
)
from aworld.self_evolve.controllers.measurement_authority import (
    AuthoritativeMeasurementController,
    AuthoritativeMeasurementRequest,
    AuthoritativeMeasurementRuntime,
)
from aworld.self_evolve.controllers.run_challenge_execution import (
    ChallengeExecutionRequest,
    ChallengeExecutionResult,
)
from aworld.self_evolve.controllers.run_repair_conformance import (
    RepairConformancePreflightRequest,
    RepairConformancePreflightResult,
)
from aworld.self_evolve.controllers.run_replay_adaptation import (
    BaselineReuseProvenanceRequest,
    BaselineReuseProvenanceRuntime,
    ReplayAdaptationExecution,
    ReplayAdaptationRequest,
    ReplayAdaptationResult,
    baseline_reuse_provenance,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.measurement import ControlledExperimentSpec
from aworld.self_evolve.replay import CandidateReplayRequest
from aworld.self_evolve.replay_adaptation import ReplayAdaptationBundle
from aworld.self_evolve.types import CandidateVariant, GateResult


@dataclass(frozen=True)
class LegacyReplayAdaptationOverride:
    callback: Callable[..., tuple[ReplayAdaptationBundle | None, GateResult]]

    def __call__(self, request: ReplayAdaptationRequest) -> ReplayAdaptationResult:
        return ReplayAdaptationResult(
            *self.callback(
                run_id=request.run_id,
                dataset=request.dataset,
                capability_skill_root=request.capability_skill_root,
                candidate_package_fingerprint=request.candidate_package_fingerprint,
                emit_progress=request.emit_progress,
            )
        )


@dataclass(frozen=True)
class LegacyRepairConformancePreflightOverride:
    callback: Callable[..., Any]

    async def __call__(
        self,
        request: RepairConformancePreflightRequest,
    ) -> RepairConformancePreflightResult:
        gate = await self.callback(
            run_id=request.run_id,
            target=request.target,
            dataset=request.dataset,
            candidate=request.candidate,
            contract=request.contract,
            capability_requirements=request.capability_requirements,
            budget_context=request.budget_context,
        )
        return RepairConformancePreflightResult(gate)


@dataclass(frozen=True)
class LegacyChallengeExecutionOverride:
    callback: Callable[..., Any]

    async def __call__(
        self,
        request: ChallengeExecutionRequest,
    ) -> ChallengeExecutionResult:
        report, gate = await self.callback(
            run_id=request.run_id,
            target=request.target,
            candidate=request.candidate,
            budget_context=request.budget_context,
        )
        return ChallengeExecutionResult(report, gate)


@dataclass(frozen=True)
class PrepareReplayAdaptationAdapter:
    execution: ReplayAdaptationExecution

    def __call__(
        self, **kwargs: Any
    ) -> tuple[ReplayAdaptationBundle | None, GateResult]:
        return self.execution.execute(ReplayAdaptationRequest(**kwargs)).as_tuple()


@dataclass(frozen=True)
class BaselineReuseProvenanceAdapter:
    execution: ReplayAdaptationExecution

    def __call__(self, **kwargs: Any) -> Mapping[str, object]:
        return baseline_reuse_provenance(
            BaselineReuseProvenanceRequest(**kwargs),
            BaselineReuseProvenanceRuntime(replay_adaptation=self.execution),
        ).provenance


@dataclass(frozen=True)
class MeasurementResumeAdapter:
    controller: MeasurementPlanningController

    def __call__(
        self,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayRequest | None:
        return self.controller.load_resume_request(candidate=candidate, dataset=dataset)


@dataclass(frozen=True)
class MeasurementPlanCompilationAdapter:
    controller: AuthoritativeMeasurementController
    experiments: Mapping[object, ControlledExperimentSpec]
    load_resume_request: MeasurementResumeAdapter
    progress_callback: Callable[[str, str], Any] | None

    def __call__(self, **kwargs: Any) -> object:
        replay_backend = kwargs.pop("replay_backend")
        target_adapter = kwargs.pop("target_adapter", None)
        result = self.controller.compile(
            AuthoritativeMeasurementRequest(
                **kwargs,
                replay_backend_identity=measurement_component_identity(replay_backend),
                target_adapter_identity=(
                    measurement_component_identity(target_adapter)
                    if target_adapter is not None
                    else None
                ),
            ),
            AuthoritativeMeasurementRuntime(
                experiments=self.experiments,
                load_resume_request=self.load_resume_request,
                progress_callback=self.progress_callback,
            ),
        )
        return result.execution_bundle
