"""Screening, conformance, and replay-adaptation phase factory."""

from __future__ import annotations

from aworld.self_evolve.controllers.run_phase_context import RunPhaseContext
from aworld.self_evolve.controllers.run_phase_adapters import (
    LegacyReplayAdaptationOverride,
    LegacyRepairConformancePreflightOverride,
)

from typing import Mapping

from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
)
from aworld.self_evolve.capability_contracts import (
    validate_applicable_capabilities,
)
from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.controllers.run_replay_adaptation import (
    ReplayAdaptationExecution,
    ReplayAdaptationRuntime,
)
from aworld.self_evolve.controllers.run_repair_conformance import (
    RepairConformancePopulationRequest,
    RepairConformancePopulationRuntime,
    RepairConformancePreflightRequest,
    RepairConformancePreflightRuntime,
    preflight_candidate_repair_conformance,
    validate_repair_conformance_population,
)
from aworld.self_evolve.controllers.run_capability_validation import (
    CapabilityValidationRuntime,
)
from aworld.self_evolve.controllers.run_resources import (
    CandidateAttemptTracker as _CandidateAttemptTracker,
    RunBudgetContext as _RunBudgetContext,
)
from aworld.self_evolve.controllers.screening import (
    ScreeningPopulationRequest,
    ScreeningPopulationRuntime,
    StoredCandidateScreeningBypass,
)
from aworld.self_evolve.controllers.screening_execution import (
    _emit_progress,
)
from aworld.self_evolve.schema_diagnostics import _schema_field_contract_fingerprint
from aworld.self_evolve.budget import (
    CandidateAttemptKey,
)
from aworld.self_evolve.replay import (
    _replay_service_start_failure_details,
)
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.targets import (
    SelfEvolveTarget,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    GateResult,
)


class ScreeningPhaseFactory:
    def __init__(self, context: RunPhaseContext) -> None:
        self.context = context

    async def _screen_candidate_population(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidates: tuple[CandidateVariant, ...],
        apply_policy: str,
        capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
        repair_conformance_contracts: Mapping[str, RepairConformanceContract]
        | None = None,
        attempt_tracker: _CandidateAttemptTracker | None = None,
        attempt_keys: Mapping[str, CandidateAttemptKey] | None = None,
        budget_context: _RunBudgetContext | None = None,
        require_single_candidate_screening: bool = False,
        stored_candidate_bypass: StoredCandidateScreeningBypass | None = None,
    ) -> tuple[tuple[CandidateVariant, ...], dict[str, object] | None]:
        request = ScreeningPopulationRequest(
            run_id=run_id,
            target=target,
            dataset=dataset,
            candidates=candidates,
            apply_policy=apply_policy,
            capability_requirements=capability_requirements,
            repair_conformance_contracts=(repair_conformance_contracts or {}),
            attempt_tracker=attempt_tracker,
            attempt_keys=attempt_keys,
            budget_context=budget_context,
            require_single_candidate_screening=(require_single_candidate_screening),
            stored_candidate_bypass=stored_candidate_bypass,
        )
        result = await self.context.construction.controllers.screening.screen_population(
            request,
            execute=self.context.require_operations().execute_screen_candidate_population,
            runtime=ScreeningPopulationRuntime(
                store=self.context.construction.runtime.store,
                execution_telemetry=self.context.state.execution_telemetry,
                replay_enabled=self.context.construction.replay.replay_enabled,
                replay_backend=self.context.construction.runtime.candidate_replay_backend,
                candidate_screening_max_cases=(self.context.construction.replay.candidate_screening_max_cases),
                replay_max_steps=self.context.construction.replay.replay_max_steps,
                replay_timeout_seconds=self.context.construction.replay.replay_timeout_seconds,
                baseline_replay_repetitions=(self.context.construction.replay.baseline_replay_repetitions),
                candidate_replay_repetitions=(self.context.construction.replay.candidate_replay_repetitions),
                progress_callback=self.context.construction.runtime.progress_callback,
                case_observations=(self.context.construction.mutable.candidate_screening_case_observations),
                control_observations=(self.context.construction.mutable.candidate_screening_control_observations),
                invalid_control_case_ids_by_run=(
                    self.context.construction.mutable.candidate_screening_run_invalid_control_case_ids
                ),
                measurement_experiments=(self.context.construction.measurement.screening_experiments),
                validate_conformance_population=(
                    self.context.overrides.get(
                        "_validate_candidate_repair_conformance_population"
                    )
                    or self._validate_candidate_repair_conformance_population
                ),
                plan_measurement=self.context.require_operations().plan_candidate_measurement,
                prepare_adaptation=(
                    self.context.overrides.get("_prepare_replay_adaptation")
                    or self.context.require_operations().prepare_replay_adaptation
                ),
                replay_candidate=(
                    self.context.overrides.get("_replay_selected_candidate")
                    or self.context.require_operations().replay_selected_candidate
                ),
                baseline_reuse_provenance=(self.context.require_operations().baseline_reuse_provenance),
                policy=self.context.construction.controllers.screening,
                control_qualification_identity=(
                    self.context.services.control_qualification_identity
                ),
            ),
        )
        return result.candidates, result.report

    def _replay_adaptation_execution(
        self,
        *,
        preserve_instance_override: bool = True,
    ) -> ReplayAdaptationExecution:
        override = None
        if preserve_instance_override:
            callback = self.context.overrides.get("_prepare_replay_adaptation")
            if callback is not None:
                override = LegacyReplayAdaptationOverride(callback)
        return ReplayAdaptationExecution(
            runtime=ReplayAdaptationRuntime(
                store=self.context.construction.runtime.store,
                compiler=self.context.construction.runtime.replay_adaptation_compiler,
                progress_callback=self.context.construction.runtime.progress_callback,
                emit_progress=_emit_progress,
                schema_field_contract_fingerprint=(_schema_field_contract_fingerprint),
            ),
            state=self.context.construction.mutable.replay_adaptation,
            override=override,
        )

    def _repair_conformance_preflight_runtime(
        self,
    ) -> RepairConformancePreflightRuntime:
        return RepairConformancePreflightRuntime(
            store=self.context.construction.runtime.store,
            replay_adaptation=self.context.require_operations().replay_adaptation_execution(),
            create_candidate_skill_overlay=(
                self.context.services.create_candidate_skill_overlay
            ),
            evaluate_compiled_probe_conformance=(
                self.context.services.evaluate_compiled_probe_conformance
            ),
            replay_capability_fixture_leaf_values=(
                self.context.services.replay_capability_fixture_leaf_values
            ),
            replay_capability_fixture_response_leaf_values=(
                self.context.services.replay_capability_fixture_response_leaf_values
            ),
            frozen_replay_fixture_shape_fingerprints=(
                self.context.services.frozen_replay_fixture_shape_fingerprints
            ),
            preflight_frozen_replay_capability=(
                self.context.services.preflight_frozen_replay_capability
            ),
            schema_field_contract_fingerprint=_schema_field_contract_fingerprint,
        )

    def _repair_conformance_preflight_override(
        self,
    ) -> LegacyRepairConformancePreflightOverride | None:
        callback = self.context.overrides.get(
            "_preflight_candidate_repair_conformance"
        )
        return (
            LegacyRepairConformancePreflightOverride(callback)
            if callback is not None
            else None
        )

    def _repair_conformance_population_runtime(
        self,
    ) -> RepairConformancePopulationRuntime:
        return RepairConformancePopulationRuntime(
            progress_callback=self.context.construction.runtime.progress_callback,
            preflight_runtime=self.context.require_operations().repair_conformance_preflight_runtime(),
            emit_progress=_emit_progress,
            evaluate_candidate_source_conformance=(
                self.context.services.evaluate_candidate_source_conformance
            ),
            preflight_override=self.context.require_operations().repair_conformance_preflight_override(),
        )

    def _capability_validation_runtime(self) -> CapabilityValidationRuntime:
        return CapabilityValidationRuntime(
            store=self.context.construction.runtime.store,
            replay_adaptation=self.context.require_operations().replay_adaptation_execution(),
            create_candidate_skill_overlay=(
                self.context.services.create_candidate_skill_overlay
            ),
            validate_applicable_capabilities=validate_applicable_capabilities,
            preflight_frozen_replay_capability=(
                self.context.services.preflight_frozen_replay_capability
            ),
            replay_service_start_failure_details=(
                _replay_service_start_failure_details
            ),
        )

    async def _validate_candidate_repair_conformance_population(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidates: tuple[CandidateVariant, ...],
        capability_requirements: tuple[ReplayCapabilityRequirement, ...],
        repair_conformance_contracts: Mapping[str, RepairConformanceContract],
        attempt_tracker: _CandidateAttemptTracker | None = None,
        attempt_keys: Mapping[str, CandidateAttemptKey] | None = None,
        budget_context: _RunBudgetContext | None = None,
    ) -> tuple[tuple[CandidateVariant, ...], dict[str, object] | None]:
        result = await validate_repair_conformance_population(
            RepairConformancePopulationRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidates=candidates,
                capability_requirements=capability_requirements,
                repair_conformance_contracts=repair_conformance_contracts,
                attempt_tracker=attempt_tracker,
                attempt_keys=attempt_keys,
                budget_context=budget_context,
            ),
            self.context.require_operations().repair_conformance_population_runtime(),
        )
        return result.as_tuple()

    async def _preflight_candidate_repair_conformance(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        contract: RepairConformanceContract,
        capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
        budget_context: _RunBudgetContext | None = None,
    ) -> GateResult:
        result = await preflight_candidate_repair_conformance(
            RepairConformancePreflightRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=candidate,
                contract=contract,
                capability_requirements=capability_requirements,
                budget_context=budget_context,
            ),
            self.context.require_operations().repair_conformance_preflight_runtime(),
        )
        return result.gate
