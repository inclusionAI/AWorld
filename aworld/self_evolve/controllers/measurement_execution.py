"""Typed paired-replay execution boundary for candidate measurement."""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from aworld.logs.util import logger
from aworld.self_evolve.campaign_policy import (
    effective_replay_repetitions,
    is_verified_apply_policy,
)
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers.measurement_authority import (
    MeasurementAuthorityBundle,
)
from aworld.self_evolve.controllers.measurement_execution_admission import (
    _candidate_intervention_unobserved,
    _replay_gate_details,
)
from aworld.self_evolve.controllers.measurement_execution_datasets import (
    _authoritative_replay_dataset,
    _control_qualification_identity_from_request,
    _partial_replay_evaluator_dataset,
    _prioritize_candidate_intervention_cases,
)
from aworld.self_evolve.controllers.measurement_execution_progress import (
    _replay_member_hard_deadline_seconds,
    _replay_member_progress_message,
    _replay_timeout_checkpoint_details,
)
from aworld.self_evolve.controllers.screening_execution import (
    find_reusable_baseline_replay_dir,
)
from aworld.self_evolve.controllers.screening_helpers import (
    _candidate_changes_target_behavior,
    _candidate_requires_task_plane_intervention,
    _candidate_support_baseline_incompatibility_gate,
    _replay_backend_provides_skill_activation_attestation,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
)
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementPolicyMode,
)
from aworld.self_evolve.optimizers.base import (
    CandidateSourceDisposition,
    CandidateSourceKind,
)
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.replay import (
    CandidateReplayBackend,
    CandidateReplayEvidenceReuseBackend,
    CandidateReplayRequest,
    CandidateReplayResult,
    _baseline_invalid_for_measurement,
    _is_replayable_user_task_case,
    build_paired_replay_dataset,
    build_replay_request,
    candidate_replay_is_comparable,
    normalize_replay_members,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.replay_adaptation import ReplayAdaptationBundle
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import CandidateVariant, GateResult


_DEFAULT_AUTHORITATIVE_REPLAY_MAX_STEPS = 12
_DEFAULT_AUTHORITATIVE_REPLAY_TOOL_CALL_LIMIT = 16
_REPLAY_PROGRESS_HEARTBEAT_SECONDS = 30.0


@dataclass(frozen=True)
class PairedReplayExecutionConfig:
    """Static execution policy frozen with one Runner instance."""

    replay_enabled: bool
    replay_backend: CandidateReplayBackend | None
    replay_agent: object | None
    baseline_repetitions: int
    candidate_repetitions: int
    repetitions_explicit: bool
    minimum_independent_cases: int
    timeout_seconds: int
    total_timeout_seconds: float | None
    max_steps: int | None
    max_tokens: int | None
    resume_replay_dir: str | None
    invalid_control_patience: int
    measurement_mode: MeasurementPolicyMode


@dataclass(frozen=True)
class PairedReplayExecutionRequest:
    """Dynamic inputs for one paired replay execution."""

    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidate: CandidateVariant
    apply_policy: str
    baseline_replay_dir: str | None = None
    baseline_repetitions: int | None = None
    candidate_repetitions: int | None = None
    progress_stage: str = "candidate_replay"
    timeout_seconds: int | None = None
    max_steps: int | None = None
    max_tool_calls: int | None = None
    lifecycle_callback: (
        Callable[[str, Mapping[str, object]], None] | None
    ) = None
    source_disposition: CandidateSourceDisposition = CandidateSourceDisposition()
    artifact_namespace: str | None = None
    replay_backend: CandidateReplayBackend | None = None
    measurement_experiment: ControlledExperimentSpec | None = None
    measurement_stage: str = "authoritative"


@dataclass
class PairedReplayExecutionRuntime:
    """Mutable services and compatibility policies injected by Runner."""

    progress_callback: Callable[[str, str], object] | None
    execution_telemetry: SelfEvolveExecutionTelemetry
    screening_case_observations: Mapping[str, Mapping[str, float | int]]
    screening_control_observations: Mapping[str, Mapping[str, object]]
    measurement_experiments: Mapping[
        tuple[str, str], ControlledExperimentSpec
    ]
    prepare_replay_adaptation: Callable[
        ..., tuple[ReplayAdaptationBundle | None, GateResult]
    ]
    baseline_reuse_provenance: Callable[..., Mapping[str, object]]
    compile_measurement_plan: Callable[..., MeasurementAuthorityBundle | None]
    load_measurement_resume_request: Callable[..., CandidateReplayRequest | None]


@dataclass(frozen=True)
class PairedReplayExecutionResult:
    """Paired replay evidence and its admission decision."""

    replay_result: CandidateReplayResult | None
    replay_dataset: SelfEvolveDataset | None
    gate: GateResult | None

    def as_tuple(
        self,
    ) -> tuple[
        CandidateReplayResult | None,
        SelfEvolveDataset | None,
        GateResult | None,
    ]:
        return self.replay_result, self.replay_dataset, self.gate


@dataclass(frozen=True)
class PairedReplayExecutionController:
    """Executes paired replay without reading Runner state."""

    store: FilesystemSelfEvolveStore
    config: PairedReplayExecutionConfig

    async def execute(
        self,
        request: PairedReplayExecutionRequest,
        runtime: PairedReplayExecutionRuntime,
    ) -> PairedReplayExecutionResult:
        config = self.config
        run_id = request.run_id
        target = request.target
        dataset = request.dataset
        selected_candidate = request.candidate
        apply_policy = request.apply_policy
        progress_stage = request.progress_stage
        lifecycle_callback = request.lifecycle_callback
        source_disposition = request.source_disposition
        artifact_namespace = request.artifact_namespace
        measurement_experiment = request.measurement_experiment
        measurement_stage = request.measurement_stage

        if (
            not config.replay_enabled
            or selected_candidate.target.target_type != "skill"
        ):
            return PairedReplayExecutionResult(None, None, None)
        effective_replay_backend = (
            request.replay_backend
            if request.replay_backend is not None
            else config.replay_backend
        )
        candidate_requires_service_intervention = (
            _candidate_requires_task_plane_intervention(selected_candidate)
        )
        candidate_requires_skill_activation = bool(
            _candidate_changes_target_behavior(selected_candidate)
            and _replay_backend_provides_skill_activation_attestation(
                effective_replay_backend
            )
        )
        candidate_requires_intervention_exposure = bool(
            candidate_requires_service_intervention
            or candidate_requires_skill_activation
        )
        if effective_replay_backend is None:
            if not is_verified_apply_policy(apply_policy):
                return PairedReplayExecutionResult(None, None, None)
            return PairedReplayExecutionResult(
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=(
                        "auto_verified skill apply requires candidate replay backend"
                    ),
                ),
            )
        if progress_stage == "candidate_replay":
            dataset = _authoritative_replay_dataset(
                dataset,
                empirical_observations=runtime.screening_case_observations,
            )
        if isinstance(
            effective_replay_backend,
            CandidateReplayEvidenceReuseBackend,
        ):
            return await self._reuse_stored_evidence(
                run_id=run_id,
                dataset=dataset,
                candidate=selected_candidate,
                source_disposition=source_disposition,
                backend=effective_replay_backend,
                lifecycle_callback=lifecycle_callback,
                progress_stage=progress_stage,
                candidate_requires_intervention_exposure=(
                    candidate_requires_intervention_exposure
                ),
                candidate_requires_service_intervention=(
                    candidate_requires_service_intervention
                ),
                candidate_requires_skill_activation=(
                    candidate_requires_skill_activation
                ),
                runtime=runtime,
            )
        if target.identity.path is None:
            return PairedReplayExecutionResult(
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason="skill replay requires target filesystem path",
                ),
            )
        if not any(_is_replayable_user_task_case(case) for case in dataset.cases):
            return PairedReplayExecutionResult(
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=(
                        "candidate replay requires at least one user task eval "
                        "case; framework-generated evaluation contracts are not "
                        "replayable"
                    ),
                ),
            )
        replay_case_count = sum(
            1 for case in dataset.cases if _is_replayable_user_task_case(case)
        )
        requested_baseline_repetitions = (
            request.baseline_repetitions
            if request.baseline_repetitions is not None
            else config.baseline_repetitions
        )
        requested_candidate_repetitions = (
            request.candidate_repetitions
            if request.candidate_repetitions is not None
            else config.candidate_repetitions
        )
        (
            effective_baseline_repetitions,
            effective_candidate_repetitions,
            repetition_policy,
        ) = effective_replay_repetitions(
            apply_policy=apply_policy,
            repetitions_explicit=(
                config.repetitions_explicit
                or request.baseline_repetitions is not None
                or request.candidate_repetitions is not None
            ),
            replay_case_count=replay_case_count,
            measurement_min_independent_cases=config.minimum_independent_cases,
            baseline_repetitions=requested_baseline_repetitions,
            candidate_repetitions=requested_candidate_repetitions,
        )
        overlay = create_candidate_skill_overlay(
            workspace_root=self.store.workspace_root,
            run_id=run_id,
            candidate=selected_candidate,
            target_skill_path=target.identity.path,
            baseline_skill_roots=getattr(target, "baseline_skill_roots", ()),
        )
        replay_adaptation, adaptation_gate = runtime.prepare_replay_adaptation(
            run_id=run_id,
            dataset=dataset,
            capability_skill_root=overlay.candidate_skill_path.parent,
            candidate_package_fingerprint=candidate_package_fingerprint(
                selected_candidate
            ),
        )
        if lifecycle_callback is not None:
            lifecycle_callback(
                "adaptation_completed",
                {"passed": adaptation_gate.passed},
            )
        if replay_adaptation is None or not adaptation_gate.passed:
            return PairedReplayExecutionResult(None, None, adaptation_gate)
        if candidate_requires_intervention_exposure:
            dataset = _prioritize_candidate_intervention_cases(
                dataset,
                replay_adaptation,
            )
        _emit_progress(
            runtime.progress_callback,
            progress_stage,
            (
                "Running paired replay "
                f"(baseline x{effective_baseline_repetitions}, "
                f"candidate x{effective_candidate_repetitions})"
            ),
        )
        effective_timeout_seconds = (
            config.timeout_seconds
            if request.timeout_seconds is None
            else request.timeout_seconds
        )
        effective_max_steps = (
            request.max_steps
            if request.max_steps is not None
            else (
                config.max_steps
                if config.max_steps is not None
                else _DEFAULT_AUTHORITATIVE_REPLAY_MAX_STEPS
            )
        )
        effective_max_tool_calls = (
            request.max_tool_calls
            if request.max_tool_calls is not None
            else min(
                _DEFAULT_AUTHORITATIVE_REPLAY_TOOL_CALL_LIMIT,
                max(8, effective_max_steps * 2),
            )
        )
        baseline_replay_dir = find_reusable_baseline_replay_dir(
            store=self.store,
            run_id=run_id,
            target=target.identity,
            dataset=dataset,
            baseline_repetitions=effective_baseline_repetitions,
            **runtime.baseline_reuse_provenance(
                run_id=run_id,
                target=target,
                dataset=dataset,
                replay_adaptation=replay_adaptation,
                timeout_seconds=effective_timeout_seconds,
                max_steps=effective_max_steps,
                max_tool_calls=effective_max_tool_calls,
            ),
        )
        try:
            measurement_bundle = runtime.compile_measurement_plan(
                run_id=run_id,
                dataset=dataset,
                candidate=selected_candidate,
                replay_adaptation=replay_adaptation,
                replay_backend=effective_replay_backend,
                member_timeout_seconds=effective_timeout_seconds,
                artifact_namespace=artifact_namespace,
                target_adapter=target,
                experiment=measurement_experiment,
                measurement_stage=measurement_stage,
            )
            if measurement_bundle is None:
                measurement_plan = None
                measurement_isolation_decision = None
                measurement_evidence_profile = None
            else:
                (
                    measurement_plan,
                    measurement_isolation_decision,
                    measurement_evidence_profile,
                ) = measurement_bundle
            replay_request = build_replay_request(
                run_id=run_id,
                workspace_root=self.store.workspace_root,
                target=target.identity,
                candidate=selected_candidate,
                overlay_skill_root=overlay.shadow_root,
                dataset=dataset,
                agent=config.replay_agent,
                timeout_seconds=effective_timeout_seconds,
                max_steps=effective_max_steps,
                max_tool_calls=effective_max_tool_calls,
                max_tokens=config.max_tokens,
                baseline_repetitions=effective_baseline_repetitions,
                candidate_repetitions=effective_candidate_repetitions,
                baseline_replay_dir=baseline_replay_dir,
                resume_replay_dir=(
                    config.resume_replay_dir
                    if progress_stage == "candidate_replay"
                    else None
                ),
                replay_adaptation=replay_adaptation,
                verified_candidate_package_fingerprint=(
                    overlay.candidate_skill_package_fingerprint
                ),
                artifact_namespace=artifact_namespace,
                invalid_control_patience=config.invalid_control_patience,
                measurement_early_stop_enabled=(
                    config.measurement_mode
                    in {
                        MeasurementPolicyMode.ADVISORY,
                        MeasurementPolicyMode.REQUIRED,
                    }
                    or (
                        is_verified_apply_policy(apply_policy)
                        and replay_case_count > 1
                    )
                ),
                stop_on_incomparable_member=(
                    is_verified_apply_policy(apply_policy)
                    and replay_case_count == 1
                ),
                repetition_policy=repetition_policy,
                evidence_policy_mode=(
                    "required"
                    if measurement_plan is not None
                    or is_verified_apply_policy(apply_policy)
                    else "legacy"
                ),
                measurement_plan=measurement_plan,
                measurement_isolation_decision=measurement_isolation_decision,
                measurement_evidence_policy_profile=measurement_evidence_profile,
            )
            authority_experiment = measurement_experiment
            if authority_experiment is None and measurement_plan is not None:
                authority_experiment = runtime.measurement_experiments.get(
                    (run_id, selected_candidate.candidate_id)
                )
            if (
                measurement_plan is not None
                and authority_experiment is not None
                and authority_experiment.run_id != run_id
            ):
                source_request = runtime.load_measurement_resume_request(
                    candidate=selected_candidate,
                    dataset=dataset,
                )
                assert source_request is not None
                if source_request.measurement_plan != measurement_plan:
                    raise ValueError(
                        "measurement resume plan changed during admission"
                    )
                replay_request = replace(
                    source_request,
                    measurement_lane_attestations={},
                )
        except ValueError as exc:
            return _measurement_plan_admission_failure(
                exc=exc,
                candidate=selected_candidate,
                measurement_stage=measurement_stage,
            )
        replay_result_or_failure = await self._execute_replay(
            replay_request=replay_request,
            dataset=dataset,
            candidate=selected_candidate,
            backend=effective_replay_backend,
            apply_policy=apply_policy,
            progress_stage=progress_stage,
            lifecycle_callback=lifecycle_callback,
            effective_baseline_repetitions=effective_baseline_repetitions,
            effective_candidate_repetitions=effective_candidate_repetitions,
            repetition_policy=repetition_policy,
            member_timeout_seconds=effective_timeout_seconds,
            runtime=runtime,
        )
        if isinstance(replay_result_or_failure, PairedReplayExecutionResult):
            return replay_result_or_failure
        replay_result = replay_result_or_failure
        if lifecycle_callback is not None:
            lifecycle_callback(
                "replay_completed",
                {"case_count": replay_case_count},
            )
        return self._validate_replay_result(
            replay_result=replay_result,
            replay_request=replay_request,
            dataset=dataset,
            candidate=selected_candidate,
            progress_stage=progress_stage,
            lifecycle_callback=lifecycle_callback,
            candidate_requires_intervention_exposure=(
                candidate_requires_intervention_exposure
            ),
            candidate_requires_service_intervention=(
                candidate_requires_service_intervention
            ),
            candidate_requires_skill_activation=(
                candidate_requires_skill_activation
            ),
            runtime=runtime,
        )

    async def _reuse_stored_evidence(
        self,
        *,
        run_id: str,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        source_disposition: CandidateSourceDisposition,
        backend: CandidateReplayEvidenceReuseBackend,
        lifecycle_callback: (
            Callable[[str, Mapping[str, object]], None] | None
        ),
        progress_stage: str,
        candidate_requires_intervention_exposure: bool,
        candidate_requires_service_intervention: bool,
        candidate_requires_skill_activation: bool,
        runtime: PairedReplayExecutionRuntime,
    ) -> PairedReplayExecutionResult:
        disposition = backend.replay_evidence_reuse_disposition()
        replay_result = await backend.reuse_replay_evidence(
            candidate=candidate,
            dataset=dataset,
        )
        normalized = normalize_replay_members(
            dataset=dataset,
            replay_result=replay_result,
        )
        current_dataset_fingerprint = replay_dataset_fingerprint(dataset)
        source_provenance_matches = bool(
            source_disposition.kind
            is CandidateSourceKind.STORED_EVIDENCE_RERUN
            and source_disposition.source_run_id == disposition.source_run_id
            and replay_result.request.run_id == disposition.source_run_id
        )
        dataset_fingerprint_matches = (
            replay_result.request.dataset_fingerprint
            == current_dataset_fingerprint
        )
        comparable = (
            source_provenance_matches
            and dataset_fingerprint_matches
            and candidate_replay_is_comparable(
                dataset=dataset,
                replay_result=replay_result,
                require_adapted=True,
                normalized=normalized,
            )
        )
        replay_case_count = sum(
            1 for case in dataset.cases if _is_replayable_user_task_case(case)
        )
        reuse_report = {
            "schema_version": "aworld.self_evolve.replay_evidence_reuse.v1",
            "disposition": disposition.to_dict(),
            "run_id": run_id,
            "candidate_id": candidate.candidate_id,
            "source_request_run_id": replay_result.request.run_id,
            "source_request_candidate_id": replay_result.request.candidate_id,
            "source_dataset_fingerprint": (
                replay_result.request.dataset_fingerprint
            ),
            "current_dataset_fingerprint": current_dataset_fingerprint,
            "source_provenance_matches": source_provenance_matches,
            "dataset_fingerprint_matches": dataset_fingerprint_matches,
            "replay_case_count": replay_case_count,
            "normalized_member_count": len(normalized.members),
            "comparable": comparable,
        }
        reuse_path = self.store.write_replay_evidence_reuse(
            run_id,
            candidate.candidate_id,
            reuse_report,
        )
        if lifecycle_callback is not None:
            lifecycle_callback(
                "replay_evidence_reused",
                {
                    "case_count": replay_case_count,
                    "disposition": disposition.to_dict(),
                    "provenance_path": str(reuse_path),
                    "comparable": comparable,
                },
            )
        reuse_details = {
            "disposition": disposition.to_dict(),
            "provenance_path": str(reuse_path),
            "source_request_run_id": replay_result.request.run_id,
            "source_request_candidate_id": replay_result.request.candidate_id,
            "replay_case_count": replay_case_count,
            "normalized_member_count": len(normalized.members),
            "source_provenance_matches": source_provenance_matches,
            "dataset_fingerprint_matches": dataset_fingerprint_matches,
            **_replay_gate_details(
                replay_result,
                dataset=dataset,
                normalized=normalized,
                candidate_requires_intervention_exposure=(
                    candidate_requires_intervention_exposure
                ),
                candidate_requires_service_intervention=(
                    candidate_requires_service_intervention
                ),
                candidate_requires_skill_activation=(
                    candidate_requires_skill_activation
                ),
                bounded_screening=(progress_stage == "candidate_screening"),
            ),
        }
        intervention_unobserved = _candidate_intervention_unobserved(
            reuse_details
        )
        if not comparable:
            return PairedReplayExecutionResult(
                replay_result,
                None,
                GateResult(
                    gate_name="candidate_replay_evidence_reuse",
                    passed=False,
                    reason=(
                        "stored replay evidence is not comparable for the "
                        "current trajectory set"
                    ),
                    details=reuse_details,
                ),
            )
        if intervention_unobserved:
            return PairedReplayExecutionResult(
                replay_result,
                None,
                GateResult(
                    gate_name="candidate_replay_evidence_reuse",
                    passed=False,
                    reason=(
                        "stored replay evidence did not exercise the "
                        "candidate-owned intervention"
                    ),
                    details=reuse_details,
                ),
            )
        replay_dataset = build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay_result,
            candidate=candidate,
            normalized=normalized,
        )
        return PairedReplayExecutionResult(
            replay_result,
            replay_dataset,
            GateResult(
                gate_name="candidate_replay_evidence_reuse",
                passed=True,
                reason=(
                    "stored source replay evidence was reused without replay "
                    "execution"
                ),
                details=reuse_details,
            ),
        )

    async def _execute_replay(
        self,
        *,
        replay_request: CandidateReplayRequest,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        backend: CandidateReplayBackend,
        apply_policy: str,
        progress_stage: str,
        lifecycle_callback: (
            Callable[[str, Mapping[str, object]], None] | None
        ),
        effective_baseline_repetitions: int,
        effective_candidate_repetitions: int,
        repetition_policy: str,
        member_timeout_seconds: int,
        runtime: PairedReplayExecutionRuntime,
    ) -> CandidateReplayResult | PairedReplayExecutionResult:
        config = self.config
        replay_history = getattr(
            backend,
            "replay_batch_observability",
            None,
        )
        replay_history_start = (
            len(replay_history) if isinstance(replay_history, list) else 0
        )
        effective_total_timeout_seconds = config.total_timeout_seconds
        if (
            effective_total_timeout_seconds is None
            and is_verified_apply_policy(apply_policy)
            and replay_request.measurement_plan is None
        ):
            effective_total_timeout_seconds = max(
                member_timeout_seconds,
                member_timeout_seconds * 6,
            )
        try:
            if lifecycle_callback is not None:
                lifecycle_callback(
                    "replay_started",
                    {
                        "case_count": sum(
                            1
                            for case in dataset.cases
                            if _is_replayable_user_task_case(case)
                        ),
                        "baseline_repetitions": (
                            effective_baseline_repetitions
                        ),
                        "candidate_repetitions": (
                            effective_candidate_repetitions
                        ),
                        "repetition_policy": repetition_policy,
                        "total_timeout_seconds": (
                            effective_total_timeout_seconds
                        ),
                    },
                )
            replay_progress: dict[str, object] = {
                "candidate_id": candidate.candidate_id,
                "case_index": 0,
                "case_count": sum(
                    1
                    for case in dataset.cases
                    if _is_replayable_user_task_case(case)
                ),
                "case_id": "pending",
                "phase": "preparing",
            }
            phase_started_at = time.monotonic()
            phase_scope: str | None = None
            completed_phase_durations: list[float] = []

            def replay_progress_callback(
                payload: Mapping[str, object],
            ) -> None:
                nonlocal phase_scope, phase_started_at
                now = time.monotonic()
                event = payload.get("event")
                completed_scope = (
                    "member"
                    if event == "member_phase_completed"
                    else "attempt"
                    if event == "replay_attempt_completed"
                    else None
                )
                if (
                    completed_scope is not None
                    and phase_scope == completed_scope
                ):
                    completed_phase_durations.append(now - phase_started_at)
                    phase_scope = None
                replay_progress.update(payload)
                started_scope = (
                    "member"
                    if event == "member_phase_started"
                    else "attempt"
                    if event == "replay_attempt_started"
                    else None
                )
                if started_scope is not None:
                    phase_started_at = now
                    phase_scope = started_scope
                _emit_progress(
                    runtime.progress_callback,
                    progress_stage,
                    _replay_member_progress_message(payload),
                )

            async def execute_replay() -> CandidateReplayResult:
                async def execute_backend() -> CandidateReplayResult:
                    if bool(
                        getattr(backend, "supports_member_progress", False)
                    ):
                        return await backend.replay_candidate(
                            replay_request,
                            candidate=candidate,
                            dataset=dataset,
                            progress_callback=replay_progress_callback,
                        )
                    return await backend.replay_candidate(
                        replay_request,
                        candidate=candidate,
                        dataset=dataset,
                    )

                if effective_total_timeout_seconds is None:
                    return await execute_backend()
                async with asyncio.timeout(effective_total_timeout_seconds):
                    return await execute_backend()

            if runtime.progress_callback is None:
                replay_result = await execute_replay()
            else:
                replay_started_at = time.monotonic()
                replay_task = asyncio.create_task(execute_replay())
                try:
                    while True:
                        done, _ = await asyncio.wait(
                            {replay_task},
                            timeout=_REPLAY_PROGRESS_HEARTBEAT_SECONDS,
                        )
                        if done:
                            replay_result = replay_task.result()
                            break
                        now = time.monotonic()
                        phase_elapsed = now - phase_started_at
                        attempt_timeout = replay_progress.get(
                            "attempt_timeout_seconds"
                        )
                        member_hard_deadline = (
                            _replay_member_hard_deadline_seconds(
                                replay_request,
                                replay_progress,
                            )
                        )
                        phase_remaining = (
                            max(
                                0,
                                int(
                                    float(member_hard_deadline)
                                    - phase_elapsed
                                ),
                            )
                            if isinstance(member_hard_deadline, (int, float))
                            else None
                        )
                        completed_phases = len(completed_phase_durations)
                        total_phases = (
                            int(replay_progress.get("case_count") or 0) * 2
                        )
                        estimated_remaining = (
                            int(
                                statistics.mean(completed_phase_durations)
                                * max(0, total_phases - completed_phases)
                            )
                            if completed_phase_durations and total_phases
                            else None
                        )
                        _emit_progress(
                            runtime.progress_callback,
                            progress_stage,
                            _replay_heartbeat_message(
                                replay_progress=replay_progress,
                                replay_started_at=replay_started_at,
                                phase_elapsed=phase_elapsed,
                                member_hard_deadline=member_hard_deadline,
                                phase_remaining=phase_remaining,
                                attempt_timeout=attempt_timeout,
                                estimated_remaining=estimated_remaining,
                                now=now,
                                progress_message=(
                                    _replay_member_progress_message(
                                        replay_progress
                                    )
                                ),
                            ),
                        )
                finally:
                    if not replay_task.done():
                        replay_task.cancel()
                        await asyncio.gather(
                            replay_task,
                            return_exceptions=True,
                        )
        except TimeoutError:
            if lifecycle_callback is not None:
                lifecycle_callback(
                    "replay_timed_out",
                    {
                        "timeout_seconds": config.total_timeout_seconds,
                        "effective_timeout_seconds": (
                            effective_total_timeout_seconds
                        ),
                    },
                )
            timeout_checkpoint = _replay_timeout_checkpoint_details(
                replay_request
            )
            return PairedReplayExecutionResult(
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=(
                        "candidate replay exceeded the total hard deadline"
                    ),
                    details={
                        "failure_class": "measurement",
                        "failure_owner": FailureOwner.FRAMEWORK.value,
                        "failure_scope": FailureScope.SHARED_RUN.value,
                        "failure_stage": FailureStage.EVALUATION.value,
                        "repairable": True,
                        "next_action": "continue_measurement",
                        "code": "replay_total_timeout",
                        "timeout_seconds": effective_total_timeout_seconds,
                        "timeout_source": (
                            "configured"
                            if config.total_timeout_seconds is not None
                            else "verified_default"
                        ),
                        "partial_baseline_cache_preserved": True,
                        **timeout_checkpoint,
                    },
                ),
            )
        finally:
            if isinstance(replay_history, list):
                for observability in replay_history[replay_history_start:]:
                    if isinstance(observability, Mapping):
                        runtime.execution_telemetry.record(
                            "replay",
                            observability,
                        )
        return replay_result

    def _validate_replay_result(
        self,
        *,
        replay_result: CandidateReplayResult,
        replay_request: CandidateReplayRequest,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        progress_stage: str,
        lifecycle_callback: (
            Callable[[str, Mapping[str, object]], None] | None
        ),
        candidate_requires_intervention_exposure: bool,
        candidate_requires_service_intervention: bool,
        candidate_requires_skill_activation: bool,
        runtime: PairedReplayExecutionRuntime,
    ) -> PairedReplayExecutionResult:
        measurement_decision = replay_result.measurement_decision
        measurement_decision_kind = (
            str(measurement_decision.get("kind"))
            if isinstance(measurement_decision, Mapping)
            else ""
        )
        if measurement_decision_kind in {
            "measurement_incomplete_checkpoint",
            "measurement_incomplete_campaign_deadline",
        }:
            return PairedReplayExecutionResult(
                replay_result,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=(
                        "authoritative replay stopped at a durable measurement "
                        "boundary and can resume without repeating terminal work"
                    ),
                    details={
                        "failure_class": "measurement",
                        "failure_owner": "measurement_scheduler",
                        "failure_scope": FailureScope.SHARED_RUN.value,
                        "failure_stage": FailureStage.EVALUATION.value,
                        "repairable": True,
                        "next_action": "continue_measurement",
                        "code": measurement_decision_kind,
                        "measurement_decision": dict(measurement_decision),
                        "measurement_plan_fingerprint": (
                            replay_request.measurement_plan.measurement_plan_fingerprint
                            if replay_request.measurement_plan is not None
                            else None
                        ),
                        "resume_safe": (
                            measurement_decision.get("resume_safe") is True
                        ),
                    },
                ),
            )
        replay_validation_dataset = _measurement_validation_dataset(
            dataset=dataset,
            replay_result=replay_result,
            replay_request=replay_request,
        )
        normalized = normalize_replay_members(
            dataset=replay_validation_dataset,
            replay_result=replay_result,
        )
        if not candidate_replay_is_comparable(
            dataset=replay_validation_dataset,
            replay_result=replay_result,
            require_adapted=True,
            normalized=normalized,
        ):
            replay_gate: GateResult | None = GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason=(
                    "candidate replay did not produce comparable paired outcomes"
                ),
                details=_replay_gate_details(
                    replay_result,
                    dataset=replay_validation_dataset,
                    normalized=normalized,
                    candidate_requires_intervention_exposure=(
                        candidate_requires_intervention_exposure
                    ),
                    candidate_requires_service_intervention=(
                        candidate_requires_service_intervention
                    ),
                    candidate_requires_skill_activation=(
                        candidate_requires_skill_activation
                    ),
                    bounded_screening=(
                        progress_stage == "candidate_screening"
                    ),
                ),
            )
            for member in normalized.members:
                if member.baseline.status is not ReplayExecutionStatus.FAILED:
                    continue
                replay_gate = _candidate_support_baseline_incompatibility_gate(
                    replay_gate,
                    control_identity=(
                        _control_qualification_identity_from_request(
                            member.request
                        )
                    ),
                    control_observations=(
                        runtime.screening_control_observations
                    ),
                )
            evaluator_dataset: SelfEvolveDataset | None = None
            evaluator_case_ids: tuple[str, ...] = ()
            if replay_request.measurement_plan is not None:
                evaluator_dataset, evaluator_case_ids = (
                    _partial_replay_evaluator_dataset(
                        dataset=replay_validation_dataset,
                        replay_result=replay_result,
                        candidate=candidate,
                        normalized=normalized,
                        minimum_independent_cases=(
                            replay_request.measurement_plan.decision_policy
                            .minimum_independent_cases
                        ),
                    )
                )
            if evaluator_dataset is not None:
                replay_gate = replace(
                    replay_gate,
                    details={
                        **dict(replay_gate.details or {}),
                        "evaluator_partial_panel_available": True,
                        "evaluator_partial_panel_role": "diagnostic_only",
                        "evaluator_partial_panel_case_count": len(
                            evaluator_case_ids
                        ),
                        "evaluator_partial_panel_case_ids": list(
                            evaluator_case_ids
                        ),
                        "verified_replay_gate_relaxed": False,
                    },
                )
            return PairedReplayExecutionResult(
                replay_result,
                evaluator_dataset,
                replay_gate,
            )
        replay_details = _replay_gate_details(
            replay_result,
            dataset=replay_validation_dataset,
            normalized=normalized,
            candidate_requires_intervention_exposure=(
                candidate_requires_intervention_exposure
            ),
            candidate_requires_service_intervention=(
                candidate_requires_service_intervention
            ),
            candidate_requires_skill_activation=(
                candidate_requires_skill_activation
            ),
            bounded_screening=(progress_stage == "candidate_screening"),
        )
        if _candidate_intervention_unobserved(replay_details):
            return PairedReplayExecutionResult(
                replay_result,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=(
                        "candidate replay did not exercise the candidate-owned "
                        "intervention"
                    ),
                    details=replay_details,
                ),
            )
        replay_dataset = build_paired_replay_dataset(
            dataset=replay_validation_dataset,
            replay_result=replay_result,
            candidate=candidate,
            normalized=normalized,
        )
        if lifecycle_callback is not None:
            lifecycle_callback(
                "replay_comparable",
                {
                    "case_count": sum(
                        1
                        for case in dataset.cases
                        if _is_replayable_user_task_case(case)
                    )
                },
            )
        return PairedReplayExecutionResult(
            replay_result,
            replay_dataset,
            GateResult(
                gate_name="candidate_replay",
                passed=True,
                reason="candidate replay produced comparable paired outcomes",
                details=replay_details,
            ),
        )


def _measurement_validation_dataset(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    replay_request: CandidateReplayRequest,
) -> SelfEvolveDataset:
    plan = replay_request.measurement_plan
    if plan is None:
        return dataset
    planned_case_ids = set(plan.case_ids)
    baseline_qualified_case_ids = {
        member.case_id
        for member in (replay_result.member_results or ())
        if not _baseline_invalid_for_measurement(member.baseline)
    }
    minimum_independent_cases = (
        plan.decision_policy.minimum_independent_cases
    )
    if len(baseline_qualified_case_ids) >= minimum_independent_cases:
        planned_case_ids = baseline_qualified_case_ids
    return replace(
        dataset,
        cases=tuple(
            case for case in dataset.cases if case.case_id in planned_case_ids
        ),
        recipe=replace(
            dataset.recipe,
            source={
                **dict(dataset.recipe.source),
                "measurement_plan_fingerprint": (
                    plan.measurement_plan_fingerprint
                ),
                "measurement_case_count": len(planned_case_ids),
                "measurement_invalid_control_case_ids": sorted(
                    set(plan.case_ids) - baseline_qualified_case_ids
                ),
            },
            splits={
                "train": [],
                "validation": [],
                "held_out": sorted(planned_case_ids),
            },
            trainable_case_ids=(),
            held_out_case_ids=tuple(sorted(planned_case_ids)),
        ),
    )


def _measurement_plan_admission_failure(
    *,
    exc: ValueError,
    candidate: CandidateVariant,
    measurement_stage: str,
) -> PairedReplayExecutionResult:
    failure_event = ReplayFailureEvent(
        code="measurement_plan_admission_failed",
        owner=FailureOwner.FRAMEWORK,
        stage=FailureStage.EVALUATION,
        scope=FailureScope.SHARED_RUN,
        repairable=True,
        category="measurement_control",
        summary="measurement plan admission failed before replay rollout",
        diagnostics={
            "error_type": type(exc).__name__,
            "measurement_stage": measurement_stage,
        },
    )
    payload = failure_event.to_dict()
    return PairedReplayExecutionResult(
        None,
        None,
        GateResult(
            gate_name="candidate_replay",
            passed=False,
            reason=str(exc),
            details={
                "failure_class": "measurement",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.SHARED_RUN.value,
                "failure_stage": FailureStage.EVALUATION.value,
                "repairable": True,
                "next_action": "repair_measurement",
                "resume_safe": True,
                "resume_candidate_id": candidate.candidate_id,
                "resume_candidate_package_fingerprint": (
                    candidate_package_fingerprint(candidate)
                ),
                "code": failure_event.code,
                "measurement_stage": measurement_stage,
                "failure_event": payload,
                "causal_failure_events": [payload],
            },
        ),
    )


def _replay_heartbeat_message(
    *,
    replay_progress: Mapping[str, object],
    replay_started_at: float,
    phase_elapsed: float,
    member_hard_deadline: float | None,
    phase_remaining: int | None,
    attempt_timeout: object,
    estimated_remaining: int | None,
    now: float,
    progress_message: str,
) -> str:
    attempt_index = replay_progress.get("attempt_index")
    attempt_limit = replay_progress.get("attempt_limit")
    return (
        progress_message
        + "; still running; total elapsed "
        f"{int(now - replay_started_at)}s; phase elapsed "
        f"{int(phase_elapsed)}s; member hard deadline "
        + (
            f"{member_hard_deadline}s"
            if member_hard_deadline is not None
            else "unknown"
        )
        + (
            f"; attempt timeout {attempt_timeout}s"
            if attempt_timeout is not None
            and attempt_timeout != member_hard_deadline
            else ""
        )
        + (
            f"; member remaining {phase_remaining}s"
            if phase_remaining is not None
            else ""
        )
        + (
            f"; attempt {attempt_index}/{attempt_limit}"
            if attempt_index is not None
            else ""
        )
        + (
            f"; estimated replay remaining {estimated_remaining}s"
            if estimated_remaining is not None
            else ""
        )
    )


def _emit_progress(
    progress_callback: Callable[[str, str], object] | None,
    stage: str,
    message: str,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(stage, message)
    except Exception as exc:
        logger.debug(
            "self_evolve.progress_callback_failed "
            f"stage={stage} error={exc}"
        )
