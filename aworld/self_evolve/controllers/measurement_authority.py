"""Authoritative measurement-plan compilation and resume-journal control."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
from aworld.logs.util import logger
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.controllers.screening_helpers import (
    dataset_case_strata,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementPolicyMode,
    stable_measurement_fingerprint,
)
from aworld.self_evolve.measurement_control import (
    DeadlinePolicy,
    MeasurementPlanV2,
)
from aworld.self_evolve.measurement_execution import (
    MeasurementExecutionJournal,
)
from aworld.self_evolve.measurement_planner import (
    compile_measurement_plan_v2,
    compile_screening_measurement_plan_v2,
    measurement_preflight_projection,
    persist_compiled_measurement_plan,
)
from aworld.self_evolve.replay import (
    CandidateReplayRequest,
    _load_variant_result_from_dir,
    _member_artifact_name,
    candidate_replay_artifact_directory,
    compile_authoritative_replay_evidence_policy_profile_v2,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    ReplayAdaptationBundle,
    compile_replay_adaptation_isolation_decision,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import CandidateVariant


MeasurementAuthorityBundle = tuple[
    MeasurementPlanV2,
    IsolationDecision,
    EvidencePolicyProfileV2,
]


@dataclass(frozen=True)
class AuthoritativeMeasurementConfig:
    """Static authority policy frozen with one Runner instance."""

    mode: MeasurementPolicyMode
    resume_run_id: str | None
    campaign_wall_deadline_seconds: float | None


@dataclass(frozen=True)
class AuthoritativeMeasurementRequest:
    """Dynamic inputs required to compile one replay work graph."""

    run_id: str
    dataset: SelfEvolveDataset
    candidate: CandidateVariant
    replay_adaptation: ReplayAdaptationBundle
    replay_backend_identity: Mapping[str, object]
    member_timeout_seconds: float
    artifact_namespace: str | None = None
    target_adapter_identity: Mapping[str, object] | None = None
    experiment: ControlledExperimentSpec | None = None
    measurement_stage: str = "authoritative"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class AuthoritativeMeasurementRuntime:
    """Mutable registries and injected effects used by authority compilation."""

    experiments: Mapping[object, ControlledExperimentSpec]
    load_resume_request: Callable[..., CandidateReplayRequest | None]
    progress_callback: Callable[[str, str], object] | None = None
    now: Callable[[], str] = field(default=_utc_now)


@dataclass(frozen=True)
class AuthoritativeMeasurementResult:
    """Executable authority bundle, or a persisted shadow/off outcome."""

    execution_bundle: MeasurementAuthorityBundle | None
    resumed: bool = False
    shadow_only: bool = False


@dataclass(frozen=True)
class AuthoritativeMeasurementController:
    """Compiles and persists authoritative measurement without Runner access."""

    store: FilesystemSelfEvolveStore
    config: AuthoritativeMeasurementConfig

    def compile(
        self,
        request: AuthoritativeMeasurementRequest,
        runtime: AuthoritativeMeasurementRuntime,
    ) -> AuthoritativeMeasurementResult:
        config = self.config
        if config.mode is MeasurementPolicyMode.OFF:
            return AuthoritativeMeasurementResult(execution_bundle=None)
        if request.measurement_stage not in {"authoritative", "screening"}:
            raise ValueError("unsupported measurement execution stage")
        experiment = request.experiment or runtime.experiments.get(
            (request.run_id, request.candidate.candidate_id)
        )
        if experiment is None:
            raise ValueError(
                "measurement experiment was not frozen before replay admission"
            )
        if (
            request.measurement_stage == "authoritative"
            and config.resume_run_id is not None
            and experiment.run_id == config.resume_run_id
        ):
            source_request = runtime.load_resume_request(
                candidate=request.candidate,
                dataset=request.dataset,
            )
            assert source_request is not None
            assert source_request.measurement_plan is not None
            assert source_request.measurement_isolation_decision is not None
            assert source_request.measurement_evidence_policy_profile is not None
            plan = source_request.measurement_plan
            if plan.experiment_id != experiment.experiment_id:
                raise ValueError("measurement resume plan experiment changed")
            journal = MeasurementExecutionJournal(
                store=self.store,
                run_id=config.resume_run_id,
                plan=plan,
            )
            resume_now = runtime.now()
            journal.recover_expired(now=resume_now)
            legacy_retryable_ids = (
                legacy_retryable_measurement_task_failed_work_unit_ids(
                    source_request
                )
            )
            retried = journal.schedule_infrastructure_retries(
                now=resume_now,
                maximum_attempts=2,
                retryable_task_failed_work_unit_ids=legacy_retryable_ids,
            )
            entries = journal.index_entries()
            pending = sum(not entry.state.terminal for entry in entries)
            completed = len(entries) - pending
            _emit_progress(
                runtime.progress_callback,
                "measurement_preflight",
                (
                    "Resumed authoritative measurement authority: "
                    f"{pending}/{len(entries)} work units pending; "
                    f"{completed} trusted terminal units reused; "
                    f"{len(retried)} infrastructure unit(s) scheduled for retry"
                ),
            )
            return AuthoritativeMeasurementResult(
                execution_bundle=(
                    plan,
                    source_request.measurement_isolation_decision,
                    source_request.measurement_evidence_policy_profile,
                ),
                resumed=True,
            )
        replay_dir = candidate_replay_artifact_directory(
            workspace_root=self.store.workspace_root,
            run_id=request.run_id,
            candidate_id=request.candidate.candidate_id,
            artifact_namespace=request.artifact_namespace,
        )
        isolation_decision = compile_replay_adaptation_isolation_decision(
            request.replay_adaptation,
            materialization_root=replay_dir / "measurement-lanes",
            requested_lane_count=2,
        )
        evidence_profile = (
            compile_authoritative_replay_evidence_policy_profile_v2(
                experiment=experiment,
                target=request.candidate.target,
                replay_adaptation=request.replay_adaptation,
                member_timeout_seconds=request.member_timeout_seconds,
                target_adapter_identity=request.target_adapter_identity,
            )
        )
        raw_deferred_control_case_ids = request.dataset.recipe.source.get(
            "authoritative_deferred_control_case_ids",
            (),
        )
        deferred_control_case_ids = tuple(
            case_id
            for case_id in (
                raw_deferred_control_case_ids
                if isinstance(raw_deferred_control_case_ids, (list, tuple))
                else ()
            )
            if isinstance(case_id, str)
        )
        try:
            compile_plan = (
                compile_screening_measurement_plan_v2
                if request.measurement_stage == "screening"
                else compile_measurement_plan_v2
            )
            compiled = compile_plan(
                experiment=experiment,
                dataset_fingerprint=replay_dataset_fingerprint(
                    request.dataset
                ),
                execution_contract_fingerprint=(
                    stable_measurement_fingerprint(
                        {
                            "schema_version": (
                                "aworld.self_evolve."
                                "replay_execution_contract.v2"
                            ),
                            "backend": dict(
                                request.replay_backend_identity
                            ),
                            "adaptation_fingerprint": (
                                request.replay_adaptation.adaptation_fingerprint
                            ),
                            "workspace_seed_fingerprint": (
                                request.replay_adaptation.workspace_seed_fingerprint
                            ),
                            "candidate_package_fingerprint": (
                                candidate_package_fingerprint(
                                    request.candidate
                                )
                            ),
                        }
                    )
                ),
                isolation_decision=isolation_decision,
                evidence_policy_profile=evidence_profile,
                deadlines=DeadlinePolicy(
                    attempt_timeout_seconds=float(
                        request.member_timeout_seconds
                    ),
                    member_hard_deadline_seconds=float(
                        request.member_timeout_seconds
                    ),
                    checkpoint_quantum_seconds=max(
                        float(request.member_timeout_seconds) * 2.0,
                        60.0,
                    ),
                    evidence_finalization_timeout_seconds=(
                        authoritative_evidence_finalization_timeout_seconds(
                            float(request.member_timeout_seconds)
                        )
                    ),
                    campaign_wall_deadline_seconds=(
                        config.campaign_wall_deadline_seconds
                    ),
                    resumable_chunked=True,
                ),
                **(
                    {
                        "case_strata": {
                            case.case_id: "|".join(
                                sorted(dataset_case_strata(case))
                            )
                            for case in request.dataset.cases
                            if case.case_id
                            in experiment.sampling.independent_case_ids
                        },
                        "repair_screening_case_ids": (
                            experiment.search_visible_case_ids
                        ),
                        "deferred_control_case_ids": (
                            deferred_control_case_ids
                        ),
                        "sentinel_case_count": (
                            experiment.outcomes.minimum_independent_cases
                        ),
                    }
                    if request.measurement_stage == "authoritative"
                    else {}
                ),
            )
        except ValueError:
            if config.mode is MeasurementPolicyMode.SHADOW:
                logger.warning(
                    "self_evolve.measurement.shadow_plan_unavailable",
                    exc_info=True,
                )
                return AuthoritativeMeasurementResult(
                    execution_bundle=None,
                    shadow_only=True,
                )
            raise
        persist_compiled_measurement_plan(
            self.store,
            run_id=request.run_id,
            compiled=compiled,
            isolation_decision=isolation_decision,
            evidence_policy_profile=evidence_profile,
        )
        preflight = measurement_preflight_projection(
            plan=compiled.plan,
            feasibility=compiled.feasibility,
            isolation_decision=isolation_decision,
        )
        measurement_plan_label = (
            "Screening measurement plan: "
            if request.measurement_stage == "screening"
            else "Authoritative measurement plan: "
        )
        _emit_progress(
            runtime.progress_callback,
            "measurement_preflight",
            _measurement_preflight_message(
                label=measurement_plan_label,
                preflight=preflight,
                deferred_unstable_case_ids=(
                    compiled.deferred_unstable_case_ids
                ),
            ),
        )
        if config.mode is MeasurementPolicyMode.SHADOW:
            return AuthoritativeMeasurementResult(
                execution_bundle=None,
                shadow_only=True,
            )
        return AuthoritativeMeasurementResult(
            execution_bundle=(
                compiled.plan,
                isolation_decision,
                evidence_profile,
            )
        )


def authoritative_evidence_finalization_timeout_seconds(
    member_timeout_seconds: float,
) -> float:
    """Reserve a bounded, latency-aware terminal synthesis window."""

    timeout = float(member_timeout_seconds)
    return min(timeout, min(max(timeout * 0.25, 45.0), 300.0))


def legacy_retryable_measurement_task_failed_work_unit_ids(
    request: CandidateReplayRequest,
) -> tuple[str, ...]:
    """Recover pre-fix framework failures persisted as task failures."""

    plan = request.measurement_plan
    if plan is None:
        return ()
    members_root = (
        candidate_replay_artifact_directory(
            workspace_root=request.workspace_root,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
            artifact_namespace=request.artifact_namespace,
        )
        / "members"
    )
    retryable: list[str] = []
    for unit in plan.work_units:
        variant_id = (
            "baseline"
            if unit.arm.value == "control"
            else request.candidate_id
        )
        artifact_dir = (
            members_root
            / _member_artifact_name(unit.case_id)
            / variant_id
            / str(unit.repetition_id)
        )
        if not (artifact_dir / "lifecycle.json").is_file():
            continue
        try:
            result = _load_variant_result_from_dir(
                artifact_dir,
                base_variant_id=variant_id,
            )
        except (OSError, TypeError, ValueError):
            continue
        events = tuple(
            event
            for event in (result.failure, *result.blocked_by)
            if isinstance(event, ReplayFailureEvent)
        )
        if any(
            event.repairable
            and event.owner
            in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
            and (
                event.stage is FailureStage.EVIDENCE_FINALIZATION
                or event.scope is FailureScope.SHARED_RUN
            )
            for event in events
        ):
            retryable.append(unit.work_unit_id)
    return tuple(retryable)


def _measurement_preflight_message(
    *,
    label: str,
    preflight: Mapping[str, object],
    deferred_unstable_case_ids: tuple[str, ...],
) -> str:
    isolation_fallback = preflight["isolation_fallback"]
    return (
        f"{label}"
        f"work {preflight['pending_work_units']}/"
        f"{preflight['planned_work_units']} pending; "
        f"decision units {preflight['decision_required_work_units']}; "
        f"safe lanes {preflight['safe_lane_count']}; "
        "P50/P90 decision ETA "
        f"{int(float(preflight['p50_time_to_decision_seconds']))}s/"
        f"{int(float(preflight['p90_time_to_decision_seconds']))}s"
        + (
            "; deferred unstable controls "
            + str(len(deferred_unstable_case_ids))
            if deferred_unstable_case_ids
            else ""
        )
        + (
            "; isolation fallback "
            + str(isolation_fallback["code"])
            + ":"
            + str(isolation_fallback["limiting_resource"])
            if isinstance(isolation_fallback, Mapping)
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


_authoritative_evidence_finalization_timeout_seconds = (
    authoritative_evidence_finalization_timeout_seconds
)
_legacy_retryable_measurement_task_failed_work_unit_ids = (
    legacy_retryable_measurement_task_failed_work_unit_ids
)
