from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Callable, Any
from pathlib import Path
from typing import Mapping, Iterable

from aworld.config.conf import ModelConfig, SelfEvolveJudgeConfig
from aworld.logs.util import logger
from aworld.runner import Runners
from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    TargetSelectionReport,
    TrajectoryCreditAssigner,
    build_target_selection_decision,
    build_default_target_inventory,
)
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_recipe,
    build_dataset_from_source,
)
from aworld.self_evolve.diagnostics import extract_harness_diagnostics
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.evaluation import (
    AWorldTrajectoryEvaluatorBackend,
    EvaluationBackend,
    EvaluationRequest,
    SkillCandidateOverlayBackend,
    determine_candidate_confidence,
    estimate_replay_cost,
    evaluate_baseline_and_candidate,
    evaluate_variant_task,
)
from aworld.self_evolve.gates import (
    BudgetGate,
    CandidatePackageGate,
    CostLatencyRegressionGate,
    EvidenceQualityGate,
    ExternalCodeEvolutionGate,
    GlobalRegressionBenchmarkGate,
    HeldOutVerificationGate,
    JudgeOnlySignalGate,
    MalformedCandidateGate,
    NoopCandidateGate,
    ProtectedPathGate,
    RequiredVerificationGate,
    ReplayAdaptationGate,
    ScoreImprovementGate,
    SkillMarkdownGate,
    StoppingConditionGate,
    StoppingConditionState,
    TokenLimitGate,
    TrustProvenanceGate,
)
from aworld.self_evolve.lifecycle import cleanup_self_evolve_artifacts
from aworld.self_evolve.lessons import LessonRecord, extract_lesson_records
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.candidate_protocol import (
    CANDIDATE_OUTPUT_CONTRACT,
    CandidateProtocolError,
    merge_candidate_repair_output,
    normalize_candidate_output,
)
from aworld.self_evolve.capability_contracts import (
    validate_applicable_capabilities,
)
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationAgent,
    CandidateGenerationInfrastructureError,
)
from aworld.self_evolve.concurrency import (
    AWorldCandidatePopulationExecutor,
    SelfEvolveConcurrencyPolicy,
    SelfEvolveExecutionTelemetry,
)
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest, OptimizerResult
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.provenance import (
    TargetProvenance,
    TargetProvenanceResolution,
    TargetProvenanceStatus,
    TargetSelectionOrigin,
    load_target_provenance_payload,
    resolve_target_provenance,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayBackend,
    CandidateReplayRequest,
    CandidateReplayResult,
    ReplayVariantResult,
    build_paired_replay_dataset,
    build_replay_request,
    candidate_replay_is_comparable,
    candidate_replay_pair_coverage,
    load_candidate_replay_result,
    preflight_frozen_replay_capability,
    replay_capability_fixture_leaf_values,
    replay_capability_fixture_response_leaf_values,
    replay_capability_fixture_summaries,
    replay_dataset_fingerprint,
    _distributed_member_repetitions,
    _load_variant_result_from_dir,
    _is_replayable_user_task_case,
    _select_replay_case,
)
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    RepairConformanceResult,
    evaluate_candidate_source_conformance,
    evaluate_compiled_probe_conformance,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    ReplayAdaptationCompiler,
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_PROTOCOL_VERSION,
    REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS,
    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS,
    FrozenReplayCapabilityAdapter,
    ReplayCapabilityCompileRequest,
    ReplayCapabilityError,
    compile_and_freeze_capability,
    discover_replay_capability,
    materialize_replay_evidence_derivations,
)
from aworld.self_evolve.release_checks import (
    build_content_quality_diagnostics,
    build_release_checklist,
)
from aworld.self_evolve.sanitization import (
    sanitize_metric_value,
    sanitize_path_ref,
    sanitize_source_text,
    sanitize_text,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import DraftSkillTextTarget, SelfEvolveTarget, SkillTextTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
    to_json_dict,
)
from aworld.skills.compat_provider import build_compat_registry
from aworld.skills.release import normalize_verified_skill_release


_MAX_PROGRESS_REPAIR_EXTENSION_ITERATIONS = 6
_MAX_CONSECUTIVE_DUPLICATE_POPULATION_STALLS = 1


@dataclass(frozen=True)
class SelfEvolveRunnerResult:
    run: SelfEvolveRun
    selected_candidate: CandidateVariant | None


@dataclass(frozen=True)
class _FixedCandidateOptimizer:
    candidate: CandidateVariant
    source_run_id: str

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        return OptimizerResult(
            candidates=(self.candidate,),
            diagnostics={
                "source": "stored_self_evolve_run",
                "source_run_id": self.source_run_id,
                "candidate_id": self.candidate.candidate_id,
            },
        )


@dataclass(frozen=True)
class _StoredCandidateReplayBackend:
    replay_result: CandidateReplayResult
    source_replay_path: str

    async def replay_candidate(
        self,
        request,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
        if candidate.candidate_id != self.replay_result.request.candidate_id:
            raise ValueError(
                "stored replay candidate does not match selected candidate: "
                f"{self.replay_result.request.candidate_id} != {candidate.candidate_id}"
            )
        return self.replay_result


def _emit_progress(
    progress_callback: Callable[[str, str], Any] | None,
    stage: str,
    message: str,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(stage, message)
    except Exception as exc:
        logger.debug(f"self_evolve.progress_callback_failed stage={stage} error={exc}")


def _execution_usage_report(
    *,
    optimizer_diagnostics: list[dict[str, object]],
    iteration_states: list[dict[str, object]],
    stages: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, int]]:
    candidate_tokens: dict[str, int] = {}
    for iteration in optimizer_diagnostics:
        diagnostics = iteration.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        population = diagnostics.get("candidate_population_execution")
        if not isinstance(population, Mapping):
            continue
        usage = population.get("token_usage")
        if not isinstance(usage, Mapping):
            continue
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                candidate_tokens[str(key)] = candidate_tokens.get(str(key), 0) + value

    judge_attempt_count = 0
    judge_estimated_input_tokens = 0
    for state in iteration_states:
        for key in ("baseline_summary", "candidate_summary", "held_out_summary"):
            summary = state.get(key)
            if not isinstance(summary, EvaluationSummary):
                continue
            attempts = summary.metrics.get("judge_attempt_count")
            if isinstance(attempts, int) and not isinstance(attempts, bool):
                judge_attempt_count += max(0, attempts)
            estimated = summary.metrics.get("judge_estimated_input_tokens_total")
            if isinstance(estimated, (int, float)) and not isinstance(estimated, bool):
                judge_estimated_input_tokens += max(0, int(estimated))

    replay_stage = stages.get("replay", {})
    evaluation_stage = stages.get("evaluation", {})
    candidate_stage = stages.get("candidate_generation", {})
    return {
        "token_usage": {
            **candidate_tokens,
            "judge_estimated_input_tokens": judge_estimated_input_tokens,
        },
        "replay_usage": {
            "scheduled_repetition_tasks": _non_negative_int(
                replay_stage.get("item_count")
            ),
        },
        "evaluation_usage": {
            "scheduled_tasks": _non_negative_int(
                evaluation_stage.get("item_count")
            ),
            "judge_attempt_count": judge_attempt_count,
        },
        "candidate_generation_usage": {
            "scheduled_slots": _non_negative_int(
                candidate_stage.get("item_count")
            ),
        },
    }


def _non_negative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _default_iteration_budget(
    *,
    apply_policy: str,
    explicit_iterations: int | None,
) -> int:
    if explicit_iterations is not None:
        if isinstance(explicit_iterations, bool) or explicit_iterations <= 0:
            raise ValueError("iterations must be positive")
        return explicit_iterations
    return 10 if apply_policy == "auto_verified" else 1


_DEFAULT_CANDIDATE_SCREENING_TIMEOUT_SECONDS = 240


def _candidate_screening_timeout(authoritative_timeout_seconds: int) -> int:
    """Bound representative screening without weakening authoritative replay."""
    return min(
        authoritative_timeout_seconds,
        _DEFAULT_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    )


def _optimizer_iteration_diagnostics(
    optimizer_diagnostics: Iterable[Mapping[str, object]],
) -> Iterable[Mapping[str, object]]:
    for item in optimizer_diagnostics:
        diagnostics = item.get("diagnostics")
        if isinstance(diagnostics, Mapping):
            yield diagnostics


def _status_without_selected_candidate(
    optimizer_diagnostics: list[dict[str, object]],
) -> SelfEvolveRunStatus:
    infrastructure_failure = False
    candidate_owned_outcome = False
    candidate_outcome_keys = {
        "candidate_protocol_invalid_count",
        "filtered_invalid_patch_candidates",
        "filtered_noop_candidates",
        "filtered_high_baseline_regression_candidates",
        "filtered_duplicate_candidates",
        "filtered_known_duplicate_candidates",
        "filtered_semantic_lesson_duplicate_candidates",
    }
    for diagnostics in _optimizer_iteration_diagnostics(optimizer_diagnostics):
        if isinstance(diagnostics.get("candidate_generation_failure"), Mapping):
            infrastructure_failure = True
        if any(
            _non_negative_int(diagnostics.get(key)) > 0
            for key in candidate_outcome_keys
        ):
            candidate_owned_outcome = True
    if infrastructure_failure and not candidate_owned_outcome:
        return SelfEvolveRunStatus.FAILED
    return SelfEvolveRunStatus.REJECTED


def _retryable_candidate_generation_failure(
    failure: Mapping[str, object],
) -> bool:
    error_type = str(failure.get("error_type") or "").strip().casefold()
    stage = str(failure.get("stage") or "").strip().casefold()
    if stage not in {"model_provider", "model_response"}:
        return False
    return error_type in {
        "apiconnectionerror",
        "apitimeouterror",
        "connectionerror",
        "llmresponseerror",
        "ratelimiterror",
        "timeouterror",
    }


def _infrastructure_prevented_comparable_evaluation(
    failed_gates: Iterable[GateResult],
    *,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
) -> bool:
    if baseline_summary is not None and candidate_summary is not None:
        return False
    gates = tuple(failed_gates)
    has_infrastructure_failure = any(
        isinstance(gate.details, Mapping)
        and gate.details.get("failure_class") == "infrastructure"
        for gate in gates
    )
    has_candidate_owned_failure = any(
        not isinstance(gate.details, Mapping)
        or gate.details.get("failure_class") != "infrastructure"
        for gate in gates
    )
    return has_infrastructure_failure and not has_candidate_owned_failure


def _replay_adaptation_exception_details(
    exc: Exception,
    *,
    candidate_capability: bool,
) -> dict[str, object]:
    reason = sanitize_text(str(exc), max_chars=240)
    if candidate_capability and isinstance(exc, ReplayCapabilityError):
        diagnostic = {
            "code": "invalid_replay_capability_compile",
            "stage": "capability_compile",
            "failure_class": "candidate",
            "repairable": True,
            "reason": reason,
            "required_manifest_contract": {
                "schema_version": REPLAY_CAPABILITY_SCHEMA_VERSION,
                "protocol": REPLAY_CAPABILITY_PROTOCOL_VERSION,
                "handles_values": list(
                    REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS
                ),
                "entrypoint_role": (
                    "relative compiler entrypoint that writes output/result.json"
                ),
                "runtime_files_role": (
                    "candidate-owned files available to result service "
                    "runtime_entrypoint"
                ),
            },
            "required_compile_result_contract": {
                "schema_version": REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
                "service_transport_values": list(
                    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS
                ),
                "runtime_service_transport": "skill_runtime",
                "requirement_classification": (
                    "classify every request requirement_id exactly once as "
                    "handled or unhandled"
                ),
            },
            "layering_rules": [
                (
                    "manifest protocol is always the subprocess compiler "
                    "protocol, never a service transport"
                ),
                (
                    "manifest handles contains request requirement kinds, "
                    "never readiness states or service transports"
                ),
                (
                    "runtime_required is a requirement status and must not "
                    "appear in handles"
                ),
                (
                    "skill_runtime is a compile-result service transport and "
                    "must not appear as manifest protocol or handles"
                ),
            ],
        }
        return {
            "failure_class": "candidate",
            "repairable": True,
            "diagnostics": [diagnostic],
        }
    return {
        "failure_class": "infrastructure",
        "repairable": False,
        "code": "replay_adaptation_infrastructure_error",
    }


def _terminal_cause(
    *,
    final_status: SelfEvolveRunStatus,
    optimizer_diagnostics: list[dict[str, object]],
    gate_results: Iterable[GateResult],
) -> dict[str, object] | None:
    if final_status is not SelfEvolveRunStatus.FAILED:
        return None
    for diagnostics in reversed(
        list(_optimizer_iteration_diagnostics(optimizer_diagnostics))
    ):
        failure = diagnostics.get("candidate_generation_failure")
        if not isinstance(failure, Mapping):
            continue
        cause: dict[str, object] = {
            "failure_class": "infrastructure",
            "stage": "candidate_generation",
            "code": str(
                failure.get("code")
                or "candidate_generation_infrastructure_error"
            ),
        }
        error_type = failure.get("error_type")
        if isinstance(error_type, str) and error_type:
            cause["error_type"] = error_type
        return cause
    for gate in gate_results:
        details = gate.details
        if (
            gate.passed
            or not isinstance(details, Mapping)
            or details.get("failure_class") != "infrastructure"
        ):
            continue
        cause = {
            "failure_class": "infrastructure",
            "stage": gate.gate_name,
            "code": str(details.get("code") or "infrastructure_error"),
        }
        error_type = details.get("type")
        if isinstance(error_type, str) and error_type:
            cause["error_type"] = error_type
        return cause
    return {
        "failure_class": "infrastructure",
        "stage": "self_evolve",
        "code": "infrastructure_error",
    }


class SelfEvolveRunner:
    def __init__(
        self,
        *,
        store: FilesystemSelfEvolveStore,
        optimizer: CandidateOptimizer,
        post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
        evaluation_backend: EvaluationBackend | None = None,
        min_score_delta: float = 0.0,
        pending_duplicate: bool = False,
        max_iterations: int = 1,
        min_eval_cases: int = 30,
        judge_repetitions: int = 3,
        max_run_tokens: int = 500_000,
        auto_apply_target_types: tuple[str, ...] = ("skill",),
        allow_generated_target_mutation: bool = False,
        allow_external_target_mutation: bool = False,
        replay_enabled: bool = False,
        candidate_replay_backend: CandidateReplayBackend | None = None,
        replay_timeout_seconds: int = 600,
        replay_max_steps: int | None = 1,
        replay_candidate_limit: int = 2,
        baseline_replay_repetitions: int = 1,
        candidate_replay_repetitions: int = 1,
        replay_stability_margin: float = 0.0,
        replay_agent: str | None = None,
        runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
        progress_callback: Callable[[str, str], Any] | None = None,
        skip_duplicate_rejected_candidate_gate: bool = False,
        replay_adaptation_compiler: ReplayAdaptationCompiler | None = None,
        concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
        task_batch_executor: DeterministicTaskBatchExecutor | None = None,
    ) -> None:
        self.store = store
        self.optimizer = optimizer
        self.post_apply_evaluator = post_apply_evaluator
        self.evaluation_backend = evaluation_backend
        self.min_score_delta = min_score_delta
        self.pending_duplicate = pending_duplicate
        self.max_iterations = max_iterations
        self.min_eval_cases = min_eval_cases
        self.judge_repetitions = judge_repetitions
        self.max_run_tokens = max_run_tokens
        self.auto_apply_target_types = tuple(auto_apply_target_types)
        self.allow_generated_target_mutation = allow_generated_target_mutation
        self.allow_external_target_mutation = allow_external_target_mutation
        self.replay_enabled = replay_enabled
        self.candidate_replay_backend = candidate_replay_backend
        self.replay_timeout_seconds = replay_timeout_seconds
        self.replay_max_steps = replay_max_steps
        self.replay_candidate_limit = replay_candidate_limit
        self.baseline_replay_repetitions = baseline_replay_repetitions
        self.candidate_replay_repetitions = candidate_replay_repetitions
        self.replay_stability_margin = replay_stability_margin
        self.replay_agent = replay_agent
        self.runtime_registry_refresher = runtime_registry_refresher
        self.runtime_skill_activator = runtime_skill_activator
        self.progress_callback = progress_callback
        self.skip_duplicate_rejected_candidate_gate = skip_duplicate_rejected_candidate_gate
        self.replay_adaptation_compiler = (
            replay_adaptation_compiler or ReplayAdaptationCompiler()
        )
        self.concurrency_policy = concurrency_policy or SelfEvolveConcurrencyPolicy()
        self.task_batch_executor = (
            task_batch_executor or DeterministicTaskBatchExecutor()
        )
        self.execution_telemetry = SelfEvolveExecutionTelemetry()
        self._replay_adaptation_cache: dict[
            tuple[str, str, str],
            tuple[ReplayAdaptationBundle | None, GateResult],
        ] = {}

    async def run_explicit_target(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        trace_packs: tuple[TracePack, ...],
        apply_policy: str = "proposal",
        target_selection_report: TargetSelectionReport | None = None,
        target_provenance: TargetProvenance | None = None,
        target_selection_decision: TargetSelectionDecision | None = None,
    ) -> SelfEvolveRunnerResult:
        self.execution_telemetry = SelfEvolveExecutionTelemetry()
        if apply_policy not in {"proposal", "auto_verified"}:
            raise ValueError(f"unsupported apply policy: {apply_policy}")
        supplied_provenance = target_provenance
        supplied_decision = target_selection_decision
        if target_selection_decision is not None:
            target_selection_report = target_selection_decision.report
            selection_origin = target_selection_decision.selection_origin
        elif (
            target_selection_report is not None
            and target_selection_report.selection_origin is not None
        ):
            selection_origin = target_selection_report.selection_origin
        elif target_selection_report is not None:
            selection_origin = TargetSelectionOrigin.UNKNOWN
        else:
            selection_origin = TargetSelectionOrigin.OPERATOR_EXPLICIT

        inventory = build_default_target_inventory(self.store.workspace_root)
        if target_selection_report is not None:
            selected_target = target_selection_report.selected_target
            if selected_target != target.identity:
                provenance_resolution = TargetProvenanceResolution(
                    status=TargetProvenanceStatus.UNRESOLVED,
                    provenance=None,
                    reason="target selection does not match the executable target",
                )
                target_selection_decision = TargetSelectionDecision(
                    report=replace(
                        target_selection_report,
                        provenance_status=provenance_resolution.status,
                        provenance_reason=provenance_resolution.reason,
                        selection_origin=selection_origin,
                    ),
                    provenance_resolution=provenance_resolution,
                    selection_origin=selection_origin,
                )
            else:
                target_selection_decision = build_target_selection_decision(
                    target_selection_report,
                    inventory=inventory,
                    selection_origin=selection_origin,
                    workspace_root=self.store.workspace_root,
                )
            target_selection_report = target_selection_decision.report
            provenance_resolution = target_selection_decision.provenance_resolution
        else:
            inventory_entries = inventory.find_all(
                target.identity.target_type,
                target.identity.target_id,
            )
            if len(inventory_entries) > 1:
                provenance_resolution = TargetProvenanceResolution(
                    status=TargetProvenanceStatus.UNRESOLVED,
                    provenance=None,
                    reason="inventory contains duplicate target identity",
                )
            else:
                provenance_resolution = resolve_target_provenance(
                    target.identity,
                    selection_origin=selection_origin,
                    inventory_provenance=(
                        inventory_entries[0].provenance
                        if inventory_entries
                        else None
                    ),
                    workspace_root=self.store.workspace_root,
                )

        authoritative_resolution = provenance_resolution
        if (
            supplied_decision is not None
            and supplied_decision.provenance_resolution
            != authoritative_resolution
        ):
            provenance_resolution = TargetProvenanceResolution(
                status=TargetProvenanceStatus.UNRESOLVED,
                provenance=None,
                reason=(
                    "supplied target decision does not match authoritative resolution"
                ),
            )

        if supplied_provenance is not None:
            if (
                not authoritative_resolution.resolved
                or authoritative_resolution.provenance != supplied_provenance
            ):
                provenance_resolution = TargetProvenanceResolution(
                    status=TargetProvenanceStatus.UNRESOLVED,
                    provenance=None,
                    reason="supplied provenance does not match authoritative resolution",
                )
            if target_selection_report is not None:
                target_selection_report = replace(
                    target_selection_report,
                    provenance_status=provenance_resolution.status,
                    provenance_reason=provenance_resolution.reason,
                )

        target_provenance = (
            provenance_resolution.provenance
            if provenance_resolution.resolved
            else None
        )
        target_provenance_unresolved_reason = (
            None if provenance_resolution.resolved else provenance_resolution.reason
        )
        if target_selection_report is not None and (
            target_selection_report.provenance_status != provenance_resolution.status
            or target_selection_report.provenance_reason != provenance_resolution.reason
        ):
            target_selection_report = replace(
                target_selection_report,
                provenance_status=provenance_resolution.status,
                provenance_reason=provenance_resolution.reason,
            )
        _emit_progress(
            self.progress_callback,
            "start",
            f"Starting self-evolve run {run_id}",
        )
        startup_artifact_retention = _artifact_retention_report(
            self.store,
            run_id,
        )
        _emit_progress(
            self.progress_callback,
            "trajectory_set_loading",
            (
                "Loaded self-evolve trajectory set "
                f"with {len(dataset.cases)} case(s)"
            ),
        )

        run = SelfEvolveRun(run_id=run_id, target=target.identity, status=SelfEvolveRunStatus.RUNNING)
        self.store.create_run(run)
        self.store.write_dataset_recipe(run_id, dataset.recipe)
        if target_selection_report is not None:
            self.store.write_target_selection_report(run_id, target_selection_report)
        target_provenance_path: Path | None = None
        if target_provenance is not None:
            target_provenance_path = self.store.write_target_provenance(
                run_id,
                target_provenance,
            )
        target_provenance_report = {
            "status": provenance_resolution.status,
            "path": (
                str(target_provenance_path)
                if target_provenance_path is not None
                else None
            ),
            "reason": provenance_resolution.reason,
        }

        stopping_gate = StoppingConditionGate(
            max_iterations=self.max_iterations,
            max_stalled_iterations=1,
            max_repeated_gate_failures=1,
        )
        stopping_result = stopping_gate.evaluate(
            StoppingConditionState(iteration=0, pending_duplicate=self.pending_duplicate)
        )
        if not stopping_result.passed:
            report = {
                "run_id": run_id,
                "target": {
                    "target_type": target.identity.target_type,
                    "target_id": target.identity.target_id,
                    "path": target.identity.path,
                },
                "apply_policy": apply_policy,
                "candidate_ids": [],
                "selected_candidate_id": None,
                "status": SelfEvolveRunStatus.REJECTED.value,
                "target_provenance": target_provenance_report,
                "stopping_condition": {
                    "gate_name": stopping_result.gate_name,
                    "passed": stopping_result.passed,
                    "reason": stopping_result.reason,
                    "details": stopping_result.details,
                },
            }
            if target_selection_report is not None:
                report["target_selection"] = to_json_dict(target_selection_report)
            report["execution"] = {
                "stages": {},
                "total_usage": _execution_usage_report(
                    optimizer_diagnostics=[],
                    iteration_states=[],
                    stages={},
                ),
            }
            report["artifact_retention"] = _artifact_retention_report(
                self.store,
                run_id,
                previous=startup_artifact_retention,
            )
            self.store.write_report(run_id, report)
            completed_run = SelfEvolveRun(
                run_id=run_id,
                target=target.identity,
                status=SelfEvolveRunStatus.REJECTED,
                gate_results=(stopping_result,),
            )
            self.store.create_run(completed_run)
            _emit_progress(
                self.progress_callback,
                "completed",
                f"Self-evolve run {run_id} finished with status {completed_run.status.value}",
            )
            return SelfEvolveRunnerResult(run=completed_run, selected_candidate=None)

        selected_candidate: CandidateVariant | None = None
        validation_feedback: tuple[EvaluationSummary, ...] = ()
        all_candidates: list[CandidateVariant] = []
        optimizer_diagnostics: list[dict[str, object]] = []
        optimizer_lineage_paths: list[str] = []
        optimizer_lineage_paths_by_candidate: dict[str, str] = {}
        iteration_reports: list[dict[str, object]] = []
        iteration_states: list[dict[str, object]] = []
        population_screening_reports: list[dict[str, object]] = []
        baseline_summary: EvaluationSummary | None = None
        candidate_summary: EvaluationSummary | None = None
        held_out_summary: EvaluationSummary | None = None
        replay_result: CandidateReplayResult | None = None
        replay_dataset: SelfEvolveDataset | None = None
        gate_results: list[GateResult] = []
        prior_feedback = _load_prior_rejected_feedback(
            self.store,
            target.identity,
            current_run_id=run_id,
        )
        generation_lesson_records = extract_lesson_records(
            prior_feedback,
            target_scope={
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
            },
            trace_packs=trace_packs,
        )
        rejected_candidate_ids = {
            feedback.variant_id
            for feedback in prior_feedback
            if feedback.metrics.get("candidate_status") == "rejected"
            and not _non_authoritative_candidate_rejection(feedback.metrics)
        }
        accepted_candidate_ids = {
            feedback.variant_id
            for feedback in prior_feedback
            if feedback.metrics.get("candidate_status") == "accepted"
        }
        current_run_attempted_candidate_ids: set[str] = set()
        rejected_semantic_lesson_fingerprints = (
            _load_prior_rejected_semantic_lesson_fingerprints(
                self.store,
                target.identity,
                current_run_id=run_id,
            )
        )
        replay_preflight = self.replay_adaptation_compiler.preflight(
            dataset=_replayable_user_task_dataset(dataset),
            workspace_root=self.store.workspace_root,
        )
        self.store.write_replay_requirements(run_id, replay_preflight)
        target_package_inventory = _target_package_inventory(target)

        baseline_preflight_blocked = False
        infrastructure_blocked = False
        progress_repair_families: set[str] = set()
        duplicate_population_stalls = 0
        candidate_generation_infrastructure_retries = 0
        iteration_budget = (
            self.max_iterations + _MAX_PROGRESS_REPAIR_EXTENSION_ITERATIONS
        )
        for iteration_index in range(iteration_budget):
            if iteration_index >= self.max_iterations:
                repair_family = _next_progress_repair_extension_family(
                    validation_feedback,
                    consumed_families=progress_repair_families,
                )
                if repair_family is None:
                    break
                progress_repair_families.add(repair_family)
            _emit_progress(
                self.progress_callback,
                "candidate_generation",
                f"Generating candidate iteration {iteration_index + 1}/{iteration_budget}",
            )
            iteration_lesson_records = generation_lesson_records
            if validation_feedback:
                iteration_lesson_records = extract_lesson_records(
                    (*prior_feedback, *validation_feedback),
                    target_scope={
                        "target_type": target.identity.target_type,
                        "target_id": target.identity.target_id,
                    },
                    trace_packs=trace_packs,
                )
            optimizer_request = OptimizerRequest.from_dataset(
                target=target.identity,
                current_content=target.load_current_content(),
                target_fingerprint=target.fingerprint_current_content(),
                trace_packs=trace_packs,
                validation_feedback=validation_feedback,
                prior_feedback=prior_feedback,
                lesson_records=iteration_lesson_records,
                dataset=dataset,
                max_candidates=_candidate_generation_limit(
                    replay_candidate_limit=self.replay_candidate_limit,
                ),
                replay_requirements=replay_preflight.requirements,
                target_package_inventory=target_package_inventory,
            )
            optimizer_request = replace(
                optimizer_request,
                evolution_context=compile_evolution_context(optimizer_request),
            )
            optimizer_result = await self.optimizer.propose(optimizer_request)
            population_execution = optimizer_result.diagnostics.get(
                "candidate_population_execution"
            )
            if isinstance(population_execution, Mapping):
                self.execution_telemetry.record(
                    "candidate_generation",
                    population_execution,
                )
            filtered_known_duplicates = _known_duplicate_candidate_count(
                optimizer_result.candidates,
                rejected_candidate_ids=rejected_candidate_ids,
                accepted_candidate_ids=accepted_candidate_ids,
            )
            current_lineage_fingerprints = _lineage_semantic_lesson_fingerprints(
                optimizer_result.lineage
            )
            filtered_semantic_lesson_duplicates = _semantic_lesson_duplicate_count(
                optimizer_result.candidates,
                lineage_fingerprints=current_lineage_fingerprints,
                rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
            )
            optimizer_diagnostics.append(
                {
                    "iteration": iteration_index + 1,
                    "candidate_ids": [
                        candidate.candidate_id for candidate in optimizer_result.candidates
                    ],
                    "diagnostics": {
                        **dict(optimizer_result.diagnostics),
                        "filtered_known_duplicate_candidates": filtered_known_duplicates,
                        "filtered_semantic_lesson_duplicate_candidates": (
                            filtered_semantic_lesson_duplicates
                        ),
                    },
                }
            )
            for candidate in optimizer_result.candidates:
                all_candidates.append(candidate)
                target.preserve_proposal(self.store, run_id, candidate)
            for lineage in optimizer_result.lineage:
                lineage_path = self.store.write_optimizer_lineage(run_id, lineage)
                optimizer_lineage_paths.append(str(lineage_path))
                optimizer_lineage_paths_by_candidate[lineage.candidate_id] = str(
                    lineage_path
                )

            candidate_population = _rank_candidate_population(
                tuple(
                    candidate
                    for candidate in optimizer_result.candidates
                    if candidate.candidate_id not in rejected_candidate_ids
                    and candidate.candidate_id not in accepted_candidate_ids
                    and not _is_semantic_lesson_duplicate(
                        candidate.candidate_id,
                        lineage_fingerprints=current_lineage_fingerprints,
                        rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
                    )
                ),
                optimizer_diagnostics=optimizer_result.diagnostics,
                current_content=target.load_current_content(),
            )[: max(1, self.replay_candidate_limit)]
            _emit_progress(
                self.progress_callback,
                "population_generation",
                (
                    "Prepared candidate population "
                    f"({len(candidate_population)} replay candidate(s), "
                    f"{len(optimizer_result.candidates)} generated)"
                ),
            )
            if not candidate_population:
                generation_failure = optimizer_result.diagnostics.get(
                    "candidate_generation_failure"
                )
                if isinstance(generation_failure, Mapping):
                    iteration_reports.append(
                        {
                            "iteration": iteration_index + 1,
                            "candidate_id": None,
                            "status": "infrastructure_failed",
                            "failed_gates": ["candidate_generation"],
                        }
                    )
                    if (
                        candidate_generation_infrastructure_retries < 2
                        and _retryable_candidate_generation_failure(
                            generation_failure
                        )
                    ):
                        candidate_generation_infrastructure_retries += 1
                        continue
                    break
                protocol_invalid_count = _non_negative_int(
                    optimizer_result.diagnostics.get(
                        "candidate_protocol_invalid_count"
                    )
                )
                if protocol_invalid_count:
                    validation_feedback = _merge_validation_feedback(
                        validation_feedback,
                        (
                            EvaluationSummary(
                                variant_id=(
                                    f"candidate-protocol-{iteration_index + 1}"
                                ),
                                dataset_split="validation",
                                metrics={
                                    "failed_gates": ["candidate_protocol"],
                                    "candidate_status": "rejected",
                                    "failure_class": "candidate",
                                    "repairable": True,
                                    "candidate_protocol_invalid_count": (
                                        protocol_invalid_count
                                    ),
                                },
                            ),
                        ),
                    )
                    iteration_reports.append(
                        {
                            "iteration": iteration_index + 1,
                            "candidate_id": None,
                            "status": "protocol_invalid",
                            "failed_gates": ["candidate_protocol"],
                        }
                    )
                    continue
                skipped_feedback: list[EvaluationSummary] = []
                skipped_duplicates = [
                    candidate
                    for candidate in optimizer_result.candidates
                    if candidate.candidate_id in rejected_candidate_ids
                    or candidate.candidate_id in accepted_candidate_ids
                ]
                for candidate_index, skipped_candidate in enumerate(
                    skipped_duplicates[: max(1, self.replay_candidate_limit)]
                ):
                    duplicate_gates: list[GateResult] = []
                    accepted_gate = _duplicate_accepted_candidate_gate(
                        skipped_candidate,
                        accepted_candidate_ids=accepted_candidate_ids,
                        apply_policy=apply_policy,
                    )
                    if accepted_gate is not None:
                        duplicate_gates.append(accepted_gate)
                    rejected_gate = _duplicate_rejected_candidate_gate(
                        skipped_candidate,
                        rejected_candidate_ids=rejected_candidate_ids,
                        apply_policy=apply_policy,
                    )
                    if rejected_gate is not None:
                        duplicate_gates.append(rejected_gate)
                    failed_duplicate_gates = [
                        gate for gate in duplicate_gates if not gate.passed
                    ]
                    duplicate_feedback = EvaluationSummary(
                        variant_id=skipped_candidate.candidate_id,
                        metrics={
                            "failed_gates": [
                                gate.gate_name for gate in failed_duplicate_gates
                            ],
                            "candidate_status": "rejected",
                        },
                        dataset_split="validation",
                    )
                    iteration_reports.append(
                        _iteration_report_item(
                            iteration_number=iteration_index + 1,
                            candidate_number=candidate_index + 1,
                            candidate_count=len(skipped_duplicates),
                            candidate=skipped_candidate,
                            status="rejected",
                            baseline_summary=None,
                            candidate_summary=None,
                            held_out_summary=None,
                            failed_gates=failed_duplicate_gates,
                        )
                    )
                    iteration_states.append(
                        _iteration_state(
                            candidate=skipped_candidate,
                            baseline_summary=None,
                            candidate_summary=None,
                            held_out_summary=None,
                            replay_result=None,
                            replay_dataset=None,
                            gate_results=duplicate_gates,
                            feedback=(duplicate_feedback,),
                            status="rejected",
                        )
                    )
                    skipped_feedback.append(duplicate_feedback)
                if skipped_feedback:
                    validation_feedback = _merge_validation_feedback(
                        validation_feedback,
                        tuple(skipped_feedback),
                    )
                    if all(
                        candidate.candidate_id
                        in current_run_attempted_candidate_ids
                        for candidate in skipped_duplicates
                    ):
                        duplicate_population_stalls += 1
                        if (
                            duplicate_population_stalls
                            >= _MAX_CONSECUTIVE_DUPLICATE_POPULATION_STALLS
                        ):
                            break
                    continue
                iteration_reports.append(
                    {
                        "iteration": iteration_index + 1,
                        "candidate_id": None,
                        "status": "no_candidate",
                        "failed_gates": [],
                    }
                )
                continue

            duplicate_population_stalls = 0
            candidate_generation_infrastructure_retries = 0

            screening_candidates = candidate_population
            repair_conformance_contracts = (
                _candidate_repair_conformance_contracts(
                    optimizer_result.diagnostics
                )
            )
            candidate_population, screening_report = await self._screen_candidate_population(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidates=candidate_population,
                apply_policy=apply_policy,
                capability_requirements=replay_preflight.requirements,
                repair_conformance_contracts=repair_conformance_contracts,
            )
            if screening_report is not None:
                population_screening_reports.append(screening_report)
            screening_feedback = _candidate_screening_repair_feedback(
                screening_candidates,
                screening_report,
            )
            if screening_feedback:
                validation_feedback = _merge_validation_feedback(
                    validation_feedback,
                    screening_feedback,
                )
                rejected_candidate_ids.update(
                    item.variant_id for item in screening_feedback
                )
                current_run_attempted_candidate_ids.update(
                    item.variant_id for item in screening_feedback
                )
                iteration_reports.extend(
                    {
                        "iteration": iteration_index + 1,
                        "candidate_id": item.variant_id,
                        "status": "screening_rejected",
                        "failed_gates": list(
                            item.metrics.get("failed_gates", [])
                        ),
                    }
                    for item in screening_feedback
                )
            if screening_feedback and not candidate_population:
                continue

            accepted_in_iteration = False
            reusable_baseline_replay_dir: str | None = None
            if (
                self.replay_enabled
                and target.identity.target_type == "skill"
                and self.candidate_replay_backend is not None
            ):
                replay_adaptation, replay_adaptation_gate = (
                    self._prepare_replay_adaptation(
                        run_id=run_id,
                        dataset=dataset,
                        emit_progress=False,
                    )
                )
                if replay_adaptation_gate.passed and replay_adaptation is not None:
                    reusable_baseline_replay_dir = _find_reusable_baseline_replay_dir(
                        store=self.store,
                        run_id=run_id,
                        target=target.identity,
                        dataset=dataset,
                        baseline_repetitions=self.baseline_replay_repetitions,
                        baseline_skill_fingerprint=target.fingerprint_current_content(),
                        dataset_fingerprint=replay_dataset_fingerprint(dataset),
                        adaptation_fingerprint=(
                            replay_adaptation.adaptation_fingerprint
                        ),
                        workspace_seed_fingerprint=(
                            replay_adaptation.workspace_seed_fingerprint
                        ),
                    )
            for candidate_index, iteration_candidate in enumerate(candidate_population):
                state, report_item, candidate_feedback = await self._evaluate_iteration_candidate(
                    run_id=run_id,
                    target=target,
                    dataset=dataset,
                    candidate=iteration_candidate,
                    apply_policy=apply_policy,
                    target_provenance=target_provenance,
                    target_provenance_unresolved_reason=(
                        target_provenance_unresolved_reason
                    ),
                    iteration_number=iteration_index + 1,
                    candidate_number=candidate_index + 1,
                    candidate_count=len(candidate_population),
                    rejected_candidate_ids=rejected_candidate_ids,
                    accepted_candidate_ids=accepted_candidate_ids,
                    baseline_replay_dir=reusable_baseline_replay_dir,
                    capability_requirements=replay_preflight.requirements,
                )
                validation_feedback = _merge_validation_feedback(
                    validation_feedback,
                    candidate_feedback,
                )
                current_run_attempted_candidate_ids.add(
                    iteration_candidate.candidate_id
                )
                iteration_reports.append(report_item)
                iteration_states.append(state)
                replay_state = state.get("replay_result")
                if isinstance(replay_state, CandidateReplayResult) and (
                    replay_state.member_results or replay_state.baseline.succeeded
                ):
                    reusable_baseline_replay_dir = _baseline_replay_artifact_dir(
                        replay_state
                    )
                failed_gates = [
                    gate for gate in state["gate_results"] if not gate.passed
                ]
                if failed_gates:
                    rejected_candidate_ids.add(iteration_candidate.candidate_id)
                if (
                    isinstance(replay_state, CandidateReplayResult)
                    and _baseline_preflight_blocks_population(replay_state)
                ):
                    baseline_preflight_blocked = True
                    break
                failed_state_gates = [
                    gate for gate in state["gate_results"] if not gate.passed
                ]
                if _infrastructure_prevented_comparable_evaluation(
                    failed_state_gates,
                    baseline_summary=state.get("baseline_summary"),
                    candidate_summary=state.get("candidate_summary"),
                ):
                    infrastructure_blocked = True
                    break
                if state["status"] == "accepted":
                    accepted_in_iteration = True
                    break
            if (
                accepted_in_iteration
                or baseline_preflight_blocked
                or infrastructure_blocked
            ):
                break

        selected_state = _select_iteration_state(iteration_states)
        if selected_state is not None:
            selected_candidate = selected_state["candidate"]  # type: ignore[assignment]
            baseline_summary = selected_state["baseline_summary"]  # type: ignore[assignment]
            candidate_summary = selected_state["candidate_summary"]  # type: ignore[assignment]
            held_out_summary = selected_state["held_out_summary"]  # type: ignore[assignment]
            replay_result = selected_state["replay_result"]  # type: ignore[assignment]
            replay_dataset = selected_state["replay_dataset"]  # type: ignore[assignment]
            gate_results = list(selected_state["gate_results"])  # type: ignore[arg-type]
        else:
            gate_results.append(
                GateResult(
                    gate_name=(
                        "candidate_generation"
                        if apply_policy == "auto_verified"
                        else "no_candidate"
                    ),
                    passed=False,
                    reason=(
                        "optimizer did not produce a replayable candidate"
                        if apply_policy == "auto_verified"
                        else "optimizer did not produce a candidate"
                    ),
                    details=(
                        {
                            "generated_candidate_count": len(all_candidates),
                            "iterations": len(optimizer_diagnostics),
                        }
                        if apply_policy == "auto_verified"
                        else None
                    ),
                )
            )

        post_apply: dict[str, object] | None = None
        final_status = SelfEvolveRunStatus.SUCCEEDED
        if selected_candidate is None:
            final_status = _status_without_selected_candidate(
                optimizer_diagnostics
            )
        elif apply_policy == "auto_verified":
            failed_gates = [gate for gate in gate_results if not gate.passed]
            if failed_gates:
                final_status = (
                    SelfEvolveRunStatus.FAILED
                    if _infrastructure_prevented_comparable_evaluation(
                        failed_gates,
                        baseline_summary=baseline_summary,
                        candidate_summary=candidate_summary,
                    )
                    else SelfEvolveRunStatus.REJECTED
                )
            else:
                post_apply = await self._apply_auto_verified(
                    run_id,
                    target,
                    selected_candidate,
                    expected_package_fingerprint=(
                        replay_result.request.verified_candidate_package_fingerprint
                        if replay_result is not None
                        else None
                    ),
                    addressed_lesson_ids=_lineage_addressed_lesson_ids(
                        optimizer_lineage_paths_by_candidate.get(
                            selected_candidate.candidate_id
                        )
                    ),
                )
                if post_apply["status"] != "accepted":
                    final_status = SelfEvolveRunStatus.REJECTED

        if optimizer_lineage_paths_by_candidate:
            _persist_lineage_lifecycle(
                optimizer_lineage_paths_by_candidate,
                iteration_states=iteration_states,
                selected_candidate_id=(
                    selected_candidate.candidate_id if selected_candidate is not None else None
                ),
                post_apply=post_apply,
            )

        execution_stages = self.execution_telemetry.to_report()
        report = {
            "run_id": run_id,
            "target": {
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
                "path": target.identity.path,
            },
            "apply_policy": apply_policy,
            "candidate_ids": [
                candidate.candidate_id for candidate in all_candidates
            ],
            "selected_candidate_id": (
                selected_candidate.candidate_id if selected_candidate is not None else None
            ),
            "status": final_status.value,
            "target_provenance": target_provenance_report,
            "optimizer_diagnostics": (
                optimizer_diagnostics[0]["diagnostics"]
                if len(optimizer_diagnostics) == 1
                else {"iterations": optimizer_diagnostics}
            ),
            "prior_feedback_count": len(prior_feedback),
            "iterations": iteration_reports,
            "execution": {
                "stages": execution_stages,
                "total_usage": _execution_usage_report(
                    optimizer_diagnostics=optimizer_diagnostics,
                    iteration_states=iteration_states,
                    stages=execution_stages,
                ),
            },
        }
        terminal_cause = _terminal_cause(
            final_status=final_status,
            optimizer_diagnostics=optimizer_diagnostics,
            gate_results=gate_results,
        )
        if terminal_cause is not None:
            report["terminal_cause"] = terminal_cause
        trajectory_set_report = _trajectory_set_report(dataset)
        if trajectory_set_report is not None:
            report["trajectory_set"] = trajectory_set_report
        population_report = _population_report(
            all_candidates=all_candidates,
            iteration_reports=iteration_reports,
            replay_candidate_limit=self.replay_candidate_limit,
            optimizer_diagnostics=optimizer_diagnostics,
            screening_reports=population_screening_reports,
        )
        if population_report is not None:
            report["population"] = population_report
        no_op_report = _no_op_report(gate_results, iteration_reports)
        if no_op_report is not None:
            report["no_op"] = no_op_report
        if optimizer_lineage_paths:
            report["optimizer_lineage"] = {
                "count": len(optimizer_lineage_paths),
                "paths": optimizer_lineage_paths,
            }
        if target_selection_report is not None:
            report["target_selection"] = to_json_dict(target_selection_report)
        if post_apply is not None:
            report["post_apply"] = post_apply
            release_normalization = _release_normalization_report(post_apply)
            if release_normalization is not None:
                report["release_normalization"] = release_normalization
        if baseline_summary is not None:
            report["baseline_metrics"] = dict(baseline_summary.metrics)
        if candidate_summary is not None:
            report["candidate_metrics"] = dict(candidate_summary.metrics)
        if held_out_summary is not None:
            report["held_out_metrics"] = dict(held_out_summary.metrics)
        if replay_result is not None:
            report["replay"] = _replay_report(replay_result)
            report["replay_path"] = _replay_artifact_path(replay_result)
            replay_capability_report = _replay_capability_report(replay_result)
            if replay_capability_report is not None:
                report["replay_capability"] = replay_capability_report
        evaluator_report_paths = _evaluator_report_paths(
            baseline_summary,
            candidate_summary,
            held_out_summary,
        )
        if evaluator_report_paths:
            report["evaluator_report_paths"] = evaluator_report_paths
        if gate_results:
            report["gate_results"] = [
                {
                    "gate_name": gate_result.gate_name,
                    "passed": gate_result.passed,
                    "reason": gate_result.reason,
                    "details": gate_result.details,
                }
                for gate_result in gate_results
            ]
            acceptance_confidence = _acceptance_confidence_report(gate_results)
            if acceptance_confidence is not None:
                report["acceptance_confidence"] = acceptance_confidence
            report["release_checklist"] = build_release_checklist(
                apply_policy=apply_policy,
                gate_results=report["gate_results"],
            )
        _emit_progress(
            self.progress_callback,
            "lesson_extraction",
            "Extracting lesson memory and harness diagnostics",
        )
        lesson_records = extract_lesson_records(
            tuple(
                feedback_item
                for state in iteration_states
                for feedback_item in state.get("feedback", ())
            ),
            target_scope={
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
            },
            trace_packs=trace_packs,
        )
        if lesson_records:
            lessons_path = self.store.write_lesson_records(run_id, lesson_records)
            report["lessons"] = {
                "path": str(lessons_path),
                "count": len(lesson_records),
                "types": _lesson_type_counts(lesson_records),
            }
            report["lesson_extraction"] = {
                "path": str(lessons_path),
                "count": len(lesson_records),
                "types": _lesson_type_counts(lesson_records),
            }
        harness_diagnostics = extract_harness_diagnostics(
            gate_results=gate_results,
            summaries=(baseline_summary, candidate_summary, held_out_summary),
            replay_result=replay_result,
        )
        if harness_diagnostics:
            diagnostics_path = self.store.write_harness_diagnostics(
                run_id,
                harness_diagnostics,
            )
            report["harness_diagnostics"] = {
                "path": str(diagnostics_path),
                "count": len(harness_diagnostics),
                "types": _harness_diagnostic_type_counts(harness_diagnostics),
                "promotion_statuses": _harness_diagnostic_promotion_counts(
                    harness_diagnostics
                ),
            }
        content_quality_metrics = (
            dict(held_out_summary.metrics)
            if held_out_summary is not None
            else dict(candidate_summary.metrics)
            if candidate_summary is not None
            else {}
        )
        report["content_quality_diagnostics"] = build_content_quality_diagnostics(
            content_quality_metrics
        )
        report["artifact_retention"] = _artifact_retention_report(
            self.store,
            run_id,
            previous=startup_artifact_retention,
        )
        self.store.write_report(run_id, report)

        completed_run = SelfEvolveRun(
            run_id=run_id,
            target=target.identity,
            status=final_status,
            selected_candidate_id=(
                selected_candidate.candidate_id if selected_candidate is not None else None
            ),
            metrics=tuple(item for item in (baseline_summary, candidate_summary) if item is not None),
            gate_results=tuple(gate_results),
        )
        self.store.create_run(completed_run)
        _emit_progress(
            self.progress_callback,
            "completed",
            f"Self-evolve run {run_id} finished with status {completed_run.status.value}",
        )
        return SelfEvolveRunnerResult(run=completed_run, selected_candidate=selected_candidate)

    async def _screen_candidate_population(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidates: tuple[CandidateVariant, ...],
        apply_policy: str,
        capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
        repair_conformance_contracts: Mapping[
            str, RepairConformanceContract
        ] | None = None,
    ) -> tuple[tuple[CandidateVariant, ...], dict[str, object] | None]:
        repair_conformance_contracts = repair_conformance_contracts or {}
        screening_dataset = _candidate_screening_dataset(
            dataset,
            capability_requirements=capability_requirements,
        )
        if (
            apply_policy != "auto_verified"
            or not candidates
            or (
                len(candidates) == 1
                and not capability_requirements
                and not repair_conformance_contracts
            )
            or screening_dataset is None
            or not self.replay_enabled
            or self.candidate_replay_backend is None
        ):
            return candidates, None

        representative_case_id = screening_dataset.cases[0].case_id
        _emit_progress(
            self.progress_callback,
            "candidate_screening",
            (
                "Screening candidate population on representative case "
                f"{representative_case_id} ({len(candidates)} candidate(s))"
            ),
        )
        attempts: list[dict[str, object]] = []
        selected_candidate: CandidateVariant | None = None
        screening_baseline_replay_dir = _find_reusable_baseline_replay_dir(
            store=self.store,
            run_id=run_id,
            target=target.identity,
            dataset=screening_dataset,
            baseline_repetitions=1,
            **self._baseline_reuse_provenance(
                run_id=run_id,
                target=target,
                dataset=screening_dataset,
            ),
        )
        for candidate in candidates:
            conformance_contract = repair_conformance_contracts.get(
                candidate.candidate_id
            )
            if conformance_contract is not None:
                source_conformance = evaluate_candidate_source_conformance(
                    candidate,
                    conformance_contract,
                )
                if not source_conformance.passed:
                    attempts.append(
                        _repair_conformance_screening_attempt(
                            candidate,
                            source_conformance,
                            contract=conformance_contract,
                        )
                    )
                    continue
                conformance_gate = (
                    await self._preflight_candidate_repair_conformance(
                        run_id=run_id,
                        target=target,
                        # Conformance probes are bounded and must cover every
                        # fixture shape before the expensive representative
                        # task rollout. A single-case preflight can otherwise
                        # approve a runtime that fails immediately when the
                        # authoritative dataset introduces another recorded
                        # response shape.
                        dataset=dataset,
                        candidate=candidate,
                        contract=conformance_contract,
                    )
                )
                if not conformance_gate.passed:
                    attempts.append(
                        {
                            "candidate_id": candidate.candidate_id,
                            "screening_candidate_id": None,
                            "passed": False,
                            "reason": conformance_gate.reason,
                            "details": conformance_gate.details,
                        }
                    )
                    continue
            screening_candidate = replace(
                candidate,
                candidate_id=f"{candidate.candidate_id}--screening",
            )
            replay_result, replay_dataset, replay_gate = (
                await self._replay_selected_candidate(
                    run_id=run_id,
                    target=target,
                    dataset=screening_dataset,
                    selected_candidate=screening_candidate,
                    apply_policy=apply_policy,
                    baseline_replay_dir=screening_baseline_replay_dir,
                    baseline_repetitions=1,
                    candidate_repetitions=1,
                    progress_stage="candidate_screening",
                    timeout_seconds=_candidate_screening_timeout(
                        self.replay_timeout_seconds
                    ),
                )
            )
            if (
                conformance_contract is not None
                and replay_gate is not None
                and not replay_gate.passed
            ):
                replay_gate = replace(
                    replay_gate,
                    details={
                        **dict(replay_gate.details or {}),
                        "repair_conformance": conformance_contract.to_dict(),
                    },
                )
            if replay_result is not None and (
                replay_result.member_results or replay_result.baseline.succeeded
            ):
                screening_baseline_replay_dir = _baseline_replay_artifact_dir(
                    replay_result
                )
            passed = bool(
                replay_dataset is not None
                and replay_gate is not None
                and replay_gate.passed
            )
            attempts.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "screening_candidate_id": screening_candidate.candidate_id,
                    "passed": passed,
                    "reason": (
                        replay_gate.reason
                        if replay_gate is not None
                        else "screening replay was unavailable"
                    ),
                    "details": replay_gate.details if replay_gate is not None else None,
                }
            )
            if passed:
                selected_candidate = candidate
                break
            if (
                replay_result is not None
                and _baseline_preflight_blocks_population(replay_result)
                and not _screening_attempt_requires_candidate_repair(attempts[-1])
            ):
                break

        selection_reason = "representative replay produced a comparable pair"
        if selected_candidate is None:
            if any(_screening_attempt_requires_candidate_repair(item) for item in attempts):
                selection_reason = (
                    "screening isolated a repairable candidate replay capability "
                    "failure; authoritative replay deferred to candidate repair"
                )
                selected_candidates = ()
            else:
                # Screening is a bounded cost filter, not an acceptance gate. An
                # unavailable or non-comparable baseline contains no evidence that
                # can distinguish candidates, so retain the complete ranked
                # population for authoritative replay instead of discarding viable
                # alternatives.
                selection_reason = (
                    "screening was inconclusive; authoritative full replay preserved "
                    "the ranked population"
                )
                selected_candidates = candidates
        else:
            selected_candidates = (selected_candidate,)
        return (
            selected_candidates,
            {
                "representative_case_id": representative_case_id,
                "generated_candidate_count": len(candidates),
                "attempted_candidate_count": len(attempts),
                "selected_candidate_id": (
                    selected_candidate.candidate_id
                    if selected_candidate is not None
                    else None
                ),
                "selected_candidate_ids": [
                    candidate.candidate_id for candidate in selected_candidates
                ],
                "selection_reason": selection_reason,
                "baseline_repetitions": 1,
                "candidate_repetitions": 1,
                "attempts": attempts,
            },
        )

    async def _preflight_candidate_repair_conformance(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        contract: RepairConformanceContract,
    ) -> GateResult:
        if target.identity.path is None:
            return _repair_conformance_gate(
                RepairConformanceResult(
                    passed=False,
                    code="repair_target_path_missing",
                    reason="repair conformance requires a filesystem skill target",
                    details={},
                ),
                contract=contract,
            )
        overlay = create_candidate_skill_overlay(
            workspace_root=self.store.workspace_root,
            run_id=run_id,
            candidate=candidate,
            target_skill_path=target.identity.path,
            baseline_skill_roots=getattr(target, "baseline_skill_roots", ()),
        )
        adaptation, adaptation_gate = self._prepare_replay_adaptation(
            run_id=run_id,
            dataset=dataset,
            capability_skill_root=overlay.candidate_skill_path.parent,
            candidate_package_fingerprint=candidate_package_fingerprint(candidate),
            emit_progress=False,
        )
        if adaptation is None or not adaptation_gate.passed:
            return GateResult(
                gate_name="candidate_repair_conformance",
                passed=False,
                reason=adaptation_gate.reason,
                details={
                    **dict(adaptation_gate.details or {}),
                    "failure_class": "candidate",
                    "repairable": True,
                    "stage": "repair_conformance_compile",
                    "code": "repair_capability_compile_failed",
                    "repair_conformance": contract.to_dict(),
                },
            )
        capability = adaptation.replay_capability
        if capability is None:
            return _repair_conformance_gate(
                RepairConformanceResult(
                    passed=False,
                    code="repair_capability_missing",
                    reason=(
                        "repair candidate did not compile a frozen replay capability"
                    ),
                    details={"focus_candidate_id": contract.focus_candidate_id},
                ),
                contract=contract,
            )
        probe_conformance = evaluate_compiled_probe_conformance(
            capability.services,
            contract,
            fixture_leaf_values=replay_capability_fixture_leaf_values(
                capability
            ),
            fixture_response_leaf_values=(
                replay_capability_fixture_response_leaf_values(capability)
            ),
        )
        if not probe_conformance.passed:
            return _repair_conformance_gate(
                probe_conformance,
                contract=contract,
            )
        artifact_dir = (
            self.store.run_path(run_id)
            / "repair_conformance"
            / _safe_artifact_name(candidate.candidate_id)
        )
        try:
            await preflight_frozen_replay_capability(
                capability,
                artifact_dir=artifact_dir,
                required_nonempty_probe_operations=(
                    _repair_conformance_required_nonempty_operations(contract)
                ),
                required_recorded_probe_operations=(
                    (
                        contract.required_fixture_probe_operations
                        or contract.late_observed_operations[-1:]
                    )
                    if contract.requires_fixture_derived_probe
                    else ()
                ),
            )
        except Exception as exc:
            return _repair_conformance_gate(
                RepairConformanceResult(
                    passed=False,
                    code="repair_probe_execution_failed",
                    reason=(
                        "candidate declared repair probe failed before task rollout"
                    ),
                    details={
                        "type": type(exc).__name__,
                        "reason": sanitize_text(str(exc), max_chars=400),
                        "artifact_root": str(artifact_dir),
                        "diagnostics": _repair_conformance_failure_diagnostics(
                            capability,
                            artifact_dir=artifact_dir,
                        ),
                    },
                ),
                contract=contract,
            )
        return _repair_conformance_gate(
            RepairConformanceResult(
                passed=True,
                code="repair_conformance_passed",
                reason=(
                    "candidate changed the failed branch and passed declared probes"
                ),
                details={
                    "focus_candidate_id": contract.focus_candidate_id,
                    "artifact_root": str(artifact_dir),
                },
            ),
            contract=contract,
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
        iteration_number: int,
        candidate_number: int,
        candidate_count: int,
        rejected_candidate_ids: set[str],
        accepted_candidate_ids: set[str],
        baseline_replay_dir: str | None = None,
        capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
    ) -> tuple[dict[str, object], dict[str, object], tuple[EvaluationSummary, ...]]:
        baseline_summary: EvaluationSummary | None = None
        candidate_summary: EvaluationSummary | None = None
        held_out_summary: EvaluationSummary | None = None
        replay_result: CandidateReplayResult | None = None
        replay_dataset: SelfEvolveDataset | None = None
        gate_results: list[GateResult] = []

        current_content = target.load_current_content()
        gate_results.extend(
            _candidate_gate_results(
                candidate,
                current_content=current_content,
                workspace_root=self.store.workspace_root,
                max_chars=self.max_run_tokens,
                target_provenance=target_provenance,
                target_provenance_unresolved_reason=(
                    target_provenance_unresolved_reason
                ),
                allow_generated_target_mutation=(
                    self.allow_generated_target_mutation
                ),
                allow_external_target_mutation=(
                    self.allow_external_target_mutation
                ),
            )
        )
        if not self.skip_duplicate_rejected_candidate_gate:
            duplicate_accepted_gate = _duplicate_accepted_candidate_gate(
                candidate,
                accepted_candidate_ids=accepted_candidate_ids,
                apply_policy=apply_policy,
            )
            if duplicate_accepted_gate is not None:
                gate_results.append(duplicate_accepted_gate)
            duplicate_gate = _duplicate_rejected_candidate_gate(
                candidate,
                rejected_candidate_ids=rejected_candidate_ids,
                apply_policy=apply_policy,
            )
            if duplicate_gate is not None:
                gate_results.append(duplicate_gate)
        accepted_duplicate_blocked = any(
            gate.gate_name == "duplicate_accepted_candidate" and not gate.passed
            for gate in gate_results
        )
        rejected_duplicate_blocked = any(
            gate.gate_name == "duplicate_rejected_candidate" and not gate.passed
            for gate in gate_results
        )
        if accepted_duplicate_blocked or rejected_duplicate_blocked:
            failed_gates = [gate for gate in gate_results if not gate.passed]
            feedback = (
                EvaluationSummary(
                    variant_id=candidate.candidate_id,
                    metrics={
                        "failed_gates": [gate.gate_name for gate in failed_gates],
                        "candidate_status": "rejected",
                    },
                    dataset_split="validation",
                ),
            )
            report_item = _iteration_report_item(
                iteration_number=iteration_number,
                candidate_number=candidate_number,
                candidate_count=candidate_count,
                candidate=candidate,
                status="rejected",
                baseline_summary=None,
                candidate_summary=None,
                held_out_summary=None,
                failed_gates=failed_gates,
            )
            state = _iteration_state(
                candidate=candidate,
                baseline_summary=None,
                candidate_summary=None,
                held_out_summary=None,
                replay_result=None,
                replay_dataset=None,
                gate_results=gate_results,
                feedback=feedback,
                status="rejected",
            )
            return state, report_item, feedback

        gate_results.append(
            BudgetGate().evaluate(
                estimate_replay_cost(
                    dataset=dataset,
                    candidate_count=candidate_count,
                    judge_repetitions=self.judge_repetitions,
                    baseline_repetitions=self.baseline_replay_repetitions,
                    candidate_repetitions=self.candidate_replay_repetitions,
                    replay_candidate_limit=self.replay_candidate_limit,
                    max_run_tokens=self.max_run_tokens,
                )
            )
        )
        capability_gates = self._validate_candidate_capabilities(
            run_id=run_id,
            target=target,
            dataset=dataset,
            candidate=candidate,
            requirements=capability_requirements,
        )
        gate_results.extend(capability_gates)
        capability_blocked = any(not gate.passed for gate in capability_gates)
        if not capability_blocked:
            replay_result, replay_dataset, replay_gate = (
                await self._replay_selected_candidate(
                    run_id=run_id,
                    target=target,
                    dataset=dataset,
                    selected_candidate=candidate,
                    apply_policy=apply_policy,
                    baseline_replay_dir=baseline_replay_dir,
                )
            )
            if replay_gate is not None:
                gate_results.append(replay_gate)
        replay_confidence_gate = _replay_confidence_gate(
            replay_result,
            dataset=dataset,
            apply_policy=apply_policy,
        )
        if replay_confidence_gate is not None:
            gate_results.append(replay_confidence_gate)

        evaluation_dataset = replay_dataset or dataset
        if self.evaluation_backend is not None:
            replay_blocked_verified_apply = (
                apply_policy == "auto_verified"
                and self.replay_enabled
                and candidate.target.target_type == "skill"
                and replay_dataset is None
            )
            if not replay_blocked_verified_apply:
                try:
                    _emit_progress(
                        self.progress_callback,
                        "evaluation",
                        (
                            "Evaluating baseline and candidate "
                            f"for iteration {iteration_number}/{self.max_iterations} "
                            f"candidate {candidate_number}/{candidate_count}"
                        ),
                    )
                    baseline_summary, candidate_summary = await evaluate_baseline_and_candidate(
                        self.evaluation_backend,
                        dataset=evaluation_dataset,
                        candidate=candidate,
                        dataset_split="validation",
                        artifact_namespace=run_id,
                        task_batch_executor=self.task_batch_executor,
                        max_concurrency=self.concurrency_policy.effective_limit(
                            "evaluation",
                            item_count=2,
                        ),
                        execution_telemetry=self.execution_telemetry,
                    )
                    if replay_result is not None:
                        baseline_summary = _summary_with_replay_evidence_metrics(
                            baseline_summary,
                            replay_result.baseline,
                        )
                        candidate_summary = _summary_with_replay_evidence_metrics(
                            candidate_summary,
                            replay_result.candidate,
                        )
                    score_gate = ScoreImprovementGate(
                        min_delta=self.min_score_delta
                    ).evaluate(
                        baseline=baseline_summary,
                        candidate=candidate_summary,
                    )
                    cost_latency_gate = CostLatencyRegressionGate(
                        max_cost_regression_ratio=0.25,
                        max_latency_regression_ratio=0.5,
                    ).evaluate(
                        baseline=baseline_summary,
                        candidate=candidate_summary,
                    )
                    gate_results.extend([score_gate, cost_latency_gate])
                    replay_stability_gate = _replay_stability_gate(
                        baseline_summary=baseline_summary,
                        candidate_summary=candidate_summary,
                        min_score_delta=self.min_score_delta,
                        replay_stability_margin=self.replay_stability_margin,
                        replay_used=replay_dataset is not None,
                    )
                    if replay_stability_gate is not None:
                        gate_results.append(replay_stability_gate)
                    if apply_policy == "auto_verified":
                        if _can_reuse_single_case_replay_validation(evaluation_dataset):
                            logger.info(
                                "self_evolve.evaluator.held_out.skip "
                                f"run_id={run_id} candidate_id={candidate.candidate_id} "
                                "reason=single_case_replay_validation_reused"
                            )
                            held_out_summary = replace(
                                candidate_summary,
                                dataset_split="single_case_replay",
                            )
                        else:
                            held_out_summary = await evaluate_variant_task(
                                self.evaluation_backend,
                                request=EvaluationRequest(
                                    variant_id=candidate.candidate_id,
                                    candidate=candidate,
                                    dataset=evaluation_dataset,
                                    dataset_split="held_out",
                                    artifact_namespace=run_id,
                                ),
                                task_batch_executor=self.task_batch_executor,
                                execution_telemetry=self.execution_telemetry,
                            )
                            if replay_result is not None:
                                held_out_summary = _summary_with_replay_evidence_metrics(
                                    held_out_summary,
                                    replay_result.candidate,
                                )
                        confidence = determine_candidate_confidence(
                            dataset=evaluation_dataset,
                            validation_summary=candidate_summary,
                            held_out_summary=held_out_summary,
                            min_eval_cases=self.min_eval_cases,
                        )
                        evidence_quality_gates = [
                            gate
                            for gate in (
                                _evidence_quality_gate(candidate_summary),
                                _evidence_quality_gate(held_out_summary),
                            )
                            if gate is not None
                        ]
                        gate_results.extend(
                            [
                                *evidence_quality_gates,
                                RequiredVerificationGate().evaluate(held_out_summary),
                                HeldOutVerificationGate(
                                    min_eval_cases=self.min_eval_cases
                                ).evaluate(confidence),
                                JudgeOnlySignalGate().evaluate(confidence),
                                GlobalRegressionBenchmarkGate().evaluate(
                                    candidate,
                                    held_out_summary,
                                ),
                            ]
                        )
                except Exception as exc:
                    gate_results.append(
                        GateResult(
                            gate_name="evaluation",
                            passed=False,
                            reason="evaluation backend failed",
                            details={
                                "failure_class": "infrastructure",
                                "code": "evaluation_infrastructure_error",
                                "type": type(exc).__name__,
                                "reason": str(exc),
                            },
                        )
                    )
        elif apply_policy == "auto_verified":
            gate_results.append(
                GateResult(
                    gate_name="auto_verified_evaluation",
                    passed=False,
                    reason="auto_verified apply policy requires evaluation backend",
                    details={
                        "failure_class": "infrastructure",
                        "code": "evaluation_backend_missing",
                    },
                )
            )

        if apply_policy == "auto_verified":
            gate_results.append(
                GateResult(
                    gate_name="auto_apply_target_type",
                    passed=target.identity.target_type in self.auto_apply_target_types,
                    reason=(
                        "target type is allowlisted for auto apply"
                        if target.identity.target_type in self.auto_apply_target_types
                        else "target type is not allowlisted for auto apply"
                    ),
                    details={
                        "target_type": target.identity.target_type,
                        "auto_apply_target_types": list(self.auto_apply_target_types),
                    },
                )
            )

        failed_gates = [gate for gate in gate_results if not gate.passed]
        status = (
            "accepted"
            if apply_policy != "auto_verified" or not failed_gates
            else "rejected"
        )
        report_item = _iteration_report_item(
            iteration_number=iteration_number,
            candidate_number=candidate_number,
            candidate_count=candidate_count,
            candidate=candidate,
            status=status,
            baseline_summary=baseline_summary,
            candidate_summary=candidate_summary,
            held_out_summary=held_out_summary,
            failed_gates=failed_gates,
        )
        feedback = _iteration_validation_feedback(
            candidate=candidate,
            baseline_summary=baseline_summary,
            candidate_summary=candidate_summary,
            held_out_summary=held_out_summary,
            failed_gates=failed_gates,
        )
        state = _iteration_state(
            candidate=candidate,
            baseline_summary=baseline_summary,
            candidate_summary=candidate_summary,
            held_out_summary=held_out_summary,
            replay_result=replay_result,
            replay_dataset=replay_dataset,
            gate_results=gate_results,
            feedback=feedback,
            status=status,
        )
        return state, report_item, feedback

    def _validate_candidate_capabilities(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        requirements: tuple[ReplayCapabilityRequirement, ...],
    ) -> list[GateResult]:
        if (
            not self.replay_enabled
            or not requirements
            or target.identity.path is None
        ):
            return []
        framework_adaptation, framework_gate = self._prepare_replay_adaptation(
            run_id=run_id,
            dataset=dataset,
            emit_progress=False,
        )
        if framework_gate.passed and framework_adaptation is not None:
            return []
        overlay = create_candidate_skill_overlay(
            workspace_root=self.store.workspace_root,
            run_id=run_id,
            candidate=candidate,
            target_skill_path=target.identity.path,
            baseline_skill_roots=getattr(target, "baseline_skill_roots", ()),
        )
        results = validate_applicable_capabilities(
            requirements=requirements,
            candidate=candidate,
            skill_root=overlay.candidate_skill_path.parent,
        )
        gates: list[GateResult] = []
        for result in results:
            diagnostics = [item.to_dict() for item in result.diagnostics]
            gates.append(
                GateResult(
                    gate_name=f"candidate_capability_{result.capability_type}",
                    passed=result.passed,
                    reason=(
                        "candidate package satisfies registered capability contract"
                        if result.passed
                        else "candidate package violates registered capability contract"
                    ),
                    details={
                        "capability_type": result.capability_type,
                        "failure_class": (
                            diagnostics[0]["failure_class"]
                            if diagnostics
                            else None
                        ),
                        "repairable": (
                            all(bool(item.get("repairable")) for item in diagnostics)
                            if diagnostics
                            else False
                        ),
                        "diagnostics": diagnostics,
                    },
                )
            )
        return gates

    def _prepare_replay_adaptation(
        self,
        *,
        run_id: str,
        dataset: SelfEvolveDataset,
        capability_skill_root: str | Path | None = None,
        candidate_package_fingerprint: str | None = None,
        emit_progress: bool = True,
    ) -> tuple[ReplayAdaptationBundle | None, GateResult]:
        dataset_fingerprint = replay_dataset_fingerprint(dataset)
        requested_package_fingerprint = (
            candidate_package_fingerprint or "framework-only"
        )
        capability = None
        discovery_error: Exception | None = None
        try:
            capability = (
                discover_replay_capability(capability_skill_root)
                if capability_skill_root is not None
                else None
            )
        except Exception as exc:
            discovery_error = exc
        discovered_package_fingerprint = (
            capability.package_fingerprint if capability is not None else "none"
        )
        capability_cache_key = (
            f"{requested_package_fingerprint}:{discovered_package_fingerprint}"
        )
        cache_key = (run_id, dataset_fingerprint, capability_cache_key)
        cached = self._replay_adaptation_cache.get(cache_key)
        if cached is not None:
            return cached
        if emit_progress:
            _emit_progress(
                self.progress_callback,
                "replay_adaptation",
                "Compiling replay paths, workspace seed, and dependency bindings",
            )
        replayable_dataset = _replayable_user_task_dataset(dataset)
        artifact_root = (
            self.store.run_path(run_id)
            / "replay_adaptation"
            / dataset_fingerprint.removeprefix("sha256:")[:16]
            / hashlib.sha256(capability_cache_key.encode("utf-8")).hexdigest()[:16]
        )
        try:
            if discovery_error is not None:
                raise discovery_error
            preflight = self.replay_adaptation_compiler.preflight(
                dataset=replayable_dataset,
                workspace_root=self.store.workspace_root,
            )
            if (
                preflight.requirements
                and capability is None
                and not self.replay_adaptation_compiler.adapters
            ):
                result = (
                    None,
                    GateResult(
                        gate_name="replay_capability",
                        passed=False,
                        reason=(
                            "replay requirements exist but the selected skill candidate "
                            "does not provide a skill-owned replay capability"
                        ),
                        details={
                            "requirement_count": len(preflight.requirements),
                            "requirement_kinds": sorted(
                                {item.kind for item in preflight.requirements}
                            ),
                            "preflight_fingerprint": preflight.fingerprint,
                            "artifact_root": str(artifact_root),
                        },
                    ),
                )
                self._replay_adaptation_cache[cache_key] = result
                return result
            frozen_capability = None
            additional_adapters = ()
            if capability is not None and preflight.requirements:
                context_root = artifact_root / "trajectory_context"
                context_root.mkdir(parents=True, exist_ok=True)
                context_snapshots: dict[str, str] = {}
                context_fingerprints: list[str] = []
                for case in replayable_dataset.cases:
                    if case.context_snapshot is None:
                        continue
                    snapshot_path = context_root / f"{_safe_artifact_name(case.case_id)}.json"
                    snapshot_path.write_text(
                        json.dumps(
                            asdict(case.context_snapshot),
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    context_snapshots[case.case_id] = str(snapshot_path)
                    context_fingerprints.append(case.context_snapshot.fingerprint)
                context_fingerprint = _stable_json_fingerprint(
                    {
                        "dataset_fingerprint": dataset_fingerprint,
                        "context_fingerprints": sorted(context_fingerprints),
                        "preflight_fingerprint": preflight.fingerprint,
                    }
                )
                compile_request = ReplayCapabilityCompileRequest.create(
                    requirements=preflight.requirements,
                    context_snapshots=context_snapshots,
                    task_inputs={
                        case.case_id: case.input for case in replayable_dataset.cases
                    },
                    capability_root=capability.skill_root,
                    capability_package_fingerprint=capability.package_fingerprint,
                    context_fingerprint=context_fingerprint,
                )
                evidence_derivations = materialize_replay_evidence_derivations(
                    compile_request,
                    context_root / "evidence_derivations",
                )
                compile_request = ReplayCapabilityCompileRequest.create(
                    requirements=preflight.requirements,
                    context_snapshots=context_snapshots,
                    task_inputs={
                        case.case_id: case.input
                        for case in replayable_dataset.cases
                    },
                    capability_root=capability.skill_root,
                    capability_package_fingerprint=capability.package_fingerprint,
                    context_fingerprint=context_fingerprint,
                    evidence_derivations=evidence_derivations,
                )
                frozen_capability = compile_and_freeze_capability(
                    capability,
                    compile_request,
                    artifact_root / "skill_replay_capability",
                )
                additional_adapters = (
                    FrozenReplayCapabilityAdapter(
                        capability=frozen_capability,
                        requirements=preflight.requirements,
                    ),
                )
            bundle = self.replay_adaptation_compiler.compile(
                dataset=replayable_dataset,
                workspace_root=self.store.workspace_root,
                artifact_root=artifact_root,
                additional_adapters=additional_adapters,
                replay_capability=frozen_capability,
            )
        except Exception as exc:
            failure_details = _replay_adaptation_exception_details(
                exc,
                candidate_capability=capability_skill_root is not None,
            )
            result = (
                None,
                GateResult(
                    gate_name="replay_adaptation",
                    passed=False,
                    reason="replay adaptation compilation failed",
                    details={
                        **failure_details,
                        "type": type(exc).__name__,
                        "reason": sanitize_text(str(exc), max_chars=240),
                        "artifact_root": str(artifact_root),
                    },
                ),
            )
            self._replay_adaptation_cache[cache_key] = result
            return result
        base_gate = ReplayAdaptationGate().evaluate(bundle)
        readiness = str((base_gate.details or {}).get("readiness") or "unresolved")
        gate = replace(
            base_gate,
            details={
                **dict(base_gate.details or {}),
                **_replay_adaptation_details(
                    bundle,
                    readiness=readiness,
                    artifact_root=artifact_root,
                ),
            },
        )
        result = (bundle, gate)
        self._replay_adaptation_cache[cache_key] = result
        return result

    def _baseline_reuse_provenance(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
    ) -> dict[str, str | None]:
        bundle, gate = self._prepare_replay_adaptation(
            run_id=run_id,
            dataset=dataset,
            emit_progress=False,
        )
        if bundle is None or not gate.passed:
            return {
                "baseline_skill_fingerprint": None,
                "dataset_fingerprint": None,
                "adaptation_fingerprint": None,
                "workspace_seed_fingerprint": None,
            }
        return {
            "baseline_skill_fingerprint": target.fingerprint_current_content(),
            "dataset_fingerprint": replay_dataset_fingerprint(dataset),
            "adaptation_fingerprint": bundle.adaptation_fingerprint,
            "workspace_seed_fingerprint": bundle.workspace_seed_fingerprint,
        }

    async def _replay_selected_candidate(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        selected_candidate: CandidateVariant,
        apply_policy: str,
        baseline_replay_dir: str | None = None,
        baseline_repetitions: int | None = None,
        candidate_repetitions: int | None = None,
        progress_stage: str = "candidate_replay",
        timeout_seconds: int | None = None,
    ) -> tuple[CandidateReplayResult | None, SelfEvolveDataset | None, GateResult | None]:
        if not self.replay_enabled or selected_candidate.target.target_type != "skill":
            return None, None, None
        if self.candidate_replay_backend is None:
            if apply_policy != "auto_verified":
                return None, None, None
            return (
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason="auto_verified skill apply requires candidate replay backend",
                ),
            )
        if target.identity.path is None:
            return (
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason="skill replay requires target filesystem path",
                ),
            )
        if not any(_is_replayable_user_task_case(case) for case in dataset.cases):
            return (
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=(
                        "candidate replay requires at least one user task eval case; "
                        "framework-generated evaluation contracts are not replayable"
                    ),
                ),
            )
        effective_baseline_repetitions = (
            baseline_repetitions
            if baseline_repetitions is not None
            else self.baseline_replay_repetitions
        )
        effective_candidate_repetitions = (
            candidate_repetitions
            if candidate_repetitions is not None
            else self.candidate_replay_repetitions
        )
        overlay = create_candidate_skill_overlay(
            workspace_root=self.store.workspace_root,
            run_id=run_id,
            candidate=selected_candidate,
            target_skill_path=target.identity.path,
            baseline_skill_roots=getattr(target, "baseline_skill_roots", ()),
        )
        replay_adaptation, adaptation_gate = self._prepare_replay_adaptation(
            run_id=run_id,
            dataset=dataset,
            capability_skill_root=overlay.candidate_skill_path.parent,
            candidate_package_fingerprint=candidate_package_fingerprint(
                selected_candidate
            ),
        )
        if replay_adaptation is None or not adaptation_gate.passed:
            return None, None, adaptation_gate
        _emit_progress(
            self.progress_callback,
            progress_stage,
            (
                "Running paired replay "
                f"(baseline x{effective_baseline_repetitions}, "
                f"candidate x{effective_candidate_repetitions})"
            ),
        )
        effective_timeout_seconds = (
            self.replay_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            request = build_replay_request(
                run_id=run_id,
                workspace_root=self.store.workspace_root,
                target=target.identity,
                candidate=selected_candidate,
                overlay_skill_root=overlay.shadow_root,
                dataset=dataset,
                agent=self.replay_agent,
                timeout_seconds=effective_timeout_seconds,
                max_steps=self.replay_max_steps,
                max_tokens=self.max_run_tokens,
                baseline_repetitions=effective_baseline_repetitions,
                candidate_repetitions=effective_candidate_repetitions,
                baseline_replay_dir=baseline_replay_dir,
                replay_adaptation=replay_adaptation,
                verified_candidate_package_fingerprint=(
                    overlay.candidate_skill_package_fingerprint
                ),
            )
        except ValueError as exc:
            return (
                None,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=str(exc),
                ),
            )
        replay_history = getattr(
            self.candidate_replay_backend,
            "replay_batch_observability",
            None,
        )
        replay_history_start = (
            len(replay_history) if isinstance(replay_history, list) else 0
        )
        try:
            replay_result = await self.candidate_replay_backend.replay_candidate(
                request,
                candidate=selected_candidate,
                dataset=dataset,
            )
        finally:
            if isinstance(replay_history, list):
                for observability in replay_history[replay_history_start:]:
                    if isinstance(observability, Mapping):
                        self.execution_telemetry.record("replay", observability)
        if not candidate_replay_is_comparable(
            dataset=dataset,
            replay_result=replay_result,
            require_adapted=True,
        ):
            return (
                replay_result,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason="candidate replay did not produce comparable paired outcomes",
                    details=_replay_gate_details(replay_result, dataset=dataset),
                ),
            )
        replay_dataset = build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay_result,
            candidate=selected_candidate,
        )
        return (
            replay_result,
            replay_dataset,
            GateResult(
                gate_name="candidate_replay",
                passed=True,
                reason="candidate replay produced comparable paired outcomes",
                details=_replay_gate_details(replay_result, dataset=dataset),
            ),
        )

    async def _apply_auto_verified(
        self,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
        expected_package_fingerprint: str | None = None,
        addressed_lesson_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if self.post_apply_evaluator is None:
            raise ValueError("auto_verified apply policy requires post_apply_evaluator")
        original_content = target.load_current_content()
        backup_path, journal_path = self.store.write_apply_backup(
            run_id,
            candidate=candidate,
            original_content=original_content,
            target_path=(
                str(_target_runtime_skill_path(target))
                if _target_runtime_skill_path(target) is not None
                else target.identity.path
            ),
        )
        self.store.update_apply_journal(
            journal_path,
            status="applying",
            details={
                "candidate_id": candidate.candidate_id,
                "verified_candidate_package_fingerprint": (
                    expected_package_fingerprint
                ),
            },
        )
        applied_candidate = candidate
        normalization_metrics: Mapping[str, Any] = {}
        if target.identity.target_type == "skill":
            _emit_progress(
                self.progress_callback,
                "release_normalization",
                "Normalizing verified skill content before apply",
            )
            normalized_content, normalization_metrics = normalize_verified_skill_release(
                candidate.content,
                run_id=run_id,
                candidate_id=candidate.candidate_id,
            )
            normalization_metrics = _with_release_lesson_mapping(
                normalization_metrics,
                addressed_lesson_ids=addressed_lesson_ids,
            )
            if not normalization_metrics.get("normalization_equivalence_passed"):
                self.store.update_apply_journal(
                    journal_path,
                    status="rejected",
                    details={
                        "post_apply_passed": False,
                        "release_state": "rejected",
                        **dict(normalization_metrics),
                    },
                )
                return {
                    "status": "rejected",
                    "metrics": {
                        "post_apply_passed": False,
                        **dict(normalization_metrics),
                    },
                    "dataset_split": "post_apply",
                    "backup_path": str(backup_path),
                    "journal_path": str(journal_path),
                    "release_state": "rejected",
                }
            applied_candidate = replace(
                candidate,
                content=normalized_content,
            )
        try:
            if (
                applied_candidate.target.target_type == "skill"
                and hasattr(target, "apply_candidate_variant")
            ):
                target.apply_candidate_variant(
                    applied_candidate,
                    expected_package_fingerprint=expected_package_fingerprint,
                    verified_content=candidate.content,
                )
            else:
                target.apply_candidate(applied_candidate.content)
        except Exception as exc:
            self.store.update_apply_journal(
                journal_path,
                status="rolled_back",
                details={
                    "post_apply_passed": False,
                    "apply_error": str(exc),
                },
            )
            return {
                "status": "rolled_back",
                "metrics": {
                    "post_apply_passed": False,
                    "apply_error": str(exc),
                },
                "dataset_split": "post_apply",
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
            }
        try:
            summary = self.post_apply_evaluator(applied_candidate)
            if inspect.isawaitable(summary):
                summary = await summary
            if not isinstance(summary, EvaluationSummary):
                raise ValueError(
                    "post_apply_evaluator must return EvaluationSummary"
                )
        except Exception as exc:
            target.rollback()
            self.store.update_apply_journal(
                journal_path,
                status="rolled_back",
                details={
                    "post_apply_passed": False,
                    "post_apply_error": str(exc),
                },
            )
            return {
                "status": "rolled_back",
                "metrics": {
                    "post_apply_passed": False,
                    "post_apply_error": str(exc),
                },
                "dataset_split": "post_apply",
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
            }
        if summary.metrics.get("post_apply_passed") is True:
            activation_result: Any = None
            if self.runtime_skill_activator is not None:
                try:
                    activation_result = self.runtime_skill_activator(applied_candidate)
                    if inspect.isawaitable(activation_result):
                        activation_result = await activation_result
                except Exception as exc:
                    target.rollback()
                    self.store.update_apply_journal(
                        journal_path,
                        status="rolled_back",
                        details={
                            "post_apply_passed": True,
                            "activation_passed": False,
                            "activation_error": str(exc),
                        },
                    )
                    metrics = dict(summary.metrics)
                    metrics.update(
                        {
                            "activation_passed": False,
                            "activation_error": str(exc),
                        }
                    )
                    return {
                        "status": "rolled_back",
                        "metrics": metrics,
                        "dataset_split": summary.dataset_split,
                        "backup_path": str(backup_path),
                        "journal_path": str(journal_path),
                    }
            refresh_result: Any = None
            if self.runtime_registry_refresher is not None:
                try:
                    refresh_result = self.runtime_registry_refresher(applied_candidate)
                    if inspect.isawaitable(refresh_result):
                        refresh_result = await refresh_result
                except Exception as exc:
                    target.rollback()
                    self.store.update_apply_journal(
                        journal_path,
                        status="rolled_back",
                        details={
                            "post_apply_passed": True,
                            "registry_refresh_passed": False,
                            "registry_refresh_error": str(exc),
                        },
                    )
                    metrics = dict(summary.metrics)
                    metrics.update(
                        {
                            "registry_refresh_passed": False,
                            "registry_refresh_error": str(exc),
                        }
                    )
                    return {
                        "status": "rolled_back",
                        "metrics": metrics,
                        "dataset_split": summary.dataset_split,
                        "backup_path": str(backup_path),
                        "journal_path": str(journal_path),
                    }
            try:
                self.store.update_apply_journal(
                    journal_path,
                    status="accepted",
                    details={
                        "post_apply_passed": True,
                        "release_state": "verified",
                    },
                )
            except Exception:
                target.rollback()
                raise
            package_cleanup_error: str | None = None
            if hasattr(target, "commit_candidate_variant"):
                try:
                    target.commit_candidate_variant()
                except Exception as exc:
                    package_cleanup_error = str(exc)
            result = {
                "status": "accepted",
                "metrics": {**dict(summary.metrics), **dict(normalization_metrics)},
                "dataset_split": summary.dataset_split,
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
                "release_state": "verified",
            }
            if package_cleanup_error is not None:
                result["package_cleanup_error"] = package_cleanup_error
            if activation_result is not None:
                result["activation"] = (
                    dict(activation_result)
                    if isinstance(activation_result, Mapping)
                    else {"result": activation_result}
                )
            if refresh_result is not None:
                result["refresh"] = (
                    dict(refresh_result)
                    if isinstance(refresh_result, Mapping)
                    else {"result": refresh_result}
                )
            return result

        target.rollback()
        self.store.update_apply_journal(
            journal_path,
            status="rolled_back",
            details={"post_apply_passed": False},
        )
        return {
            "status": "rolled_back",
            "metrics": dict(summary.metrics),
            "dataset_split": summary.dataset_split,
            "backup_path": str(backup_path),
            "journal_path": str(journal_path),
        }


async def optimize_explicit_target(
    *,
    workspace_root: str | Path,
    run_id: str,
    target: SelfEvolveTarget,
    current_trajectory: Iterable[Mapping[str, Any]],
    task_id: str,
    optimizer: CandidateOptimizer,
    apply_policy: str = "proposal",
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
) -> SelfEvolveRunnerResult:
    trajectory = list(current_trajectory)
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id=task_id,
    )
    trace_pack = dataset.cases[0].trace_pack
    if trace_pack is None:
        raise ValueError("current trajectory dataset did not produce a trace pack")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(workspace_root),
        optimizer=optimizer,
        post_apply_evaluator=post_apply_evaluator,
    )
    return await runner.run_explicit_target(
        run_id=run_id,
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy=apply_policy,
    )


def optimize_from_cli_request(
    *,
    workspace_root: str | Path,
    agent: str | None = None,
    task: str | None = None,
    target: str | None = None,
    dataset: str | None = None,
    from_session: str | None = None,
    from_trajectory: str | None = None,
    from_trajectory_set: str | None = None,
    include_prior_runs: bool = False,
    batch_config: str | None = None,
    from_run: str | None = None,
    rerun_evaluator: bool = False,
    current_trajectory: Iterable[Mapping[str, Any]] | None = None,
    iterations: int | None = None,
    apply_policy: str = "proposal",
    infer_target: bool = False,
    evaluation_backend: EvaluationBackend | None = None,
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
    min_eval_cases: int = 30,
    judge_repetitions: int = 3,
    judge_timeout_seconds: float | None = 300.0,
    max_run_tokens: int = 500_000,
    min_score_delta: float = 0.0,
    auto_apply_target_types: tuple[str, ...] = ("skill",),
    allow_generated_target_mutation: bool = False,
    allow_external_target_mutation: bool = False,
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None = None,
    mutation_model_config: ModelConfig | None = None,
    replay_enabled: bool = False,
    candidate_replay_backend: CandidateReplayBackend | None = None,
    replay_timeout_seconds: int = 600,
    replay_max_steps: int | None = 1,
    replay_candidate_limit: int = 2,
    baseline_replay_repetitions: int = 1,
    candidate_replay_repetitions: int = 1,
    replay_stability_margin: float = 0.0,
    replay_adaptation_compiler: ReplayAdaptationCompiler | None = None,
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
    progress_callback: Callable[[str, str], Any] | None = None,
    concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
) -> Mapping[str, Any]:
    effective_concurrency_policy = concurrency_policy or SelfEvolveConcurrencyPolicy()
    if apply_policy not in {"proposal", "auto_verified"}:
        raise ValueError(f"unsupported apply policy: {apply_policy}")
    effective_iteration_budget = _default_iteration_budget(
        apply_policy=apply_policy,
        explicit_iterations=iterations,
    )
    if rerun_evaluator:
        if not from_run:
            raise ValueError("--rerun-evaluator requires --from-run")
        return _rerun_evaluator_from_stored_run(
            workspace_root=workspace_root,
            from_run=from_run,
            agent=agent,
            task=task,
            apply_policy=apply_policy,
            evaluation_backend=evaluation_backend,
            post_apply_evaluator=post_apply_evaluator,
            min_eval_cases=min_eval_cases,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
            max_run_tokens=max_run_tokens,
            min_score_delta=min_score_delta,
            auto_apply_target_types=auto_apply_target_types,
            allow_generated_target_mutation=allow_generated_target_mutation,
            allow_external_target_mutation=allow_external_target_mutation,
            judge_config=judge_config,
            replay_timeout_seconds=replay_timeout_seconds,
            replay_max_steps=replay_max_steps,
            replay_candidate_limit=replay_candidate_limit,
            baseline_replay_repetitions=baseline_replay_repetitions,
            candidate_replay_repetitions=candidate_replay_repetitions,
            replay_stability_margin=replay_stability_margin,
            runtime_registry_refresher=runtime_registry_refresher,
            runtime_skill_activator=runtime_skill_activator,
            progress_callback=progress_callback,
            concurrency_policy=effective_concurrency_policy,
        )
    if (
        not dataset
        and not from_session
        and not from_trajectory
        and not from_trajectory_set
        and not batch_config
        and not from_run
        and current_trajectory is None
    ):
        raise ValueError("an eval source is required")

    _emit_progress(
        progress_callback,
        "trajectory_set_loading",
        "Loading self-evolve trajectory source",
    )
    source_config = (
        SelfEvolveEvalSourceConfig(kind="current_trajectory")
        if current_trajectory is not None
        else _source_config_from_cli_request(
            dataset=dataset,
            from_session=from_session,
            from_trajectory=from_trajectory,
            from_trajectory_set=from_trajectory_set,
            batch_config=batch_config,
            workspace_root=workspace_root,
        )
    )
    built_dataset = build_dataset_from_source(
        source_config,
        current_trajectory=current_trajectory,
        task_id=task,
    )
    _emit_progress(
        progress_callback,
        "trajectory_set_loading",
        f"Loaded self-evolve trajectory source with {len(built_dataset.cases)} case(s)",
    )
    trace_packs = tuple(
        case.trace_pack for case in built_dataset.cases if case.trace_pack is not None
    )
    if (
        infer_target
        and target is None
        and source_config.kind == "trajectory_log"
        and len(trace_packs) > 1
    ):
        built_dataset, trace_packs, _ = _auto_group_trajectory_log_dataset(
            built_dataset,
            trace_packs,
            source_config=source_config,
            workspace_root=workspace_root,
        )
    store = FilesystemSelfEvolveStore(workspace_root)
    target_selection_report: TargetSelectionReport | None = None
    target_selection_decision: TargetSelectionDecision | None = None
    target_provenance: TargetProvenance | None = None
    target_selection_path: Path | None = None
    target_provenance_path: Path | None = None

    if infer_target:
        if not trace_packs:
            target_selection_report = _no_evidence_target_selection_report(source_config.kind)
            run_id = _cli_run_id(
                "no_evidence",
                dataset,
                from_session,
                from_trajectory,
                from_trajectory_set,
                batch_config,
                iterations,
            )
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
            )
        target_selection_decision = _infer_target_from_trace_packs(
            trace_packs,
            workspace_root=workspace_root,
        )
        target_selection_report = target_selection_decision.report
        target_provenance = target_selection_decision.provenance
        target_selection_key = (
            f"{target_selection_report.selected_target.target_type}:"
            f"{target_selection_report.selected_target.target_id}"
            if target_selection_report.selected_target is not None
            else "no_target"
        )
        run_id = _cli_run_id(
            target_selection_key,
            dataset,
            from_session,
            from_trajectory,
            from_trajectory_set,
            batch_config,
            iterations,
        )
        if target_selection_report.selected_target is None:
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
            )
        if apply_policy == "auto_verified" and not _inferred_target_confident_for_auto_apply(
            target_selection_report
        ):
            target_selection_report = _blocked_low_confidence_target_selection_report(
                target_selection_report
            )
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
            )
        try:
            target_adapter = _target_from_ref(
                target_selection_report.selected_target,
                workspace_root=workspace_root,
                allow_auto_apply=(
                    apply_policy == "auto_verified"
                    and target_selection_report.selected_target.target_type
                    in auto_apply_target_types
                ),
            )
        except NotImplementedError as exc:
            return _persist_unsupported_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                target_provenance=target_provenance,
                apply_policy=apply_policy,
                reason=str(exc),
            )
    else:
        if not target:
            raise ValueError("target is required unless target inference is enabled")
        run_id = _cli_run_id(
            target,
            dataset,
            from_session,
            from_trajectory,
            from_trajectory_set,
            batch_config,
            iterations,
        )
        target_type, _, _target_id = target.partition(":")
        target_adapter = _target_from_cli_ref(
            target,
            workspace_root=workspace_root,
            allow_auto_apply=(
                apply_policy == "auto_verified" and target_type in auto_apply_target_types
            ),
        )
        target_selection_report = _explicit_target_selection_report(
            target_adapter.identity,
            trace_packs,
        )
        explicit_inventory = build_default_target_inventory(workspace_root)
        explicit_decision = build_target_selection_decision(
            target_selection_report,
            inventory=explicit_inventory,
            selection_origin=TargetSelectionOrigin.OPERATOR_EXPLICIT,
            workspace_root=workspace_root,
        )
        target_selection_report = explicit_decision.report
        target_provenance = explicit_decision.provenance
        target_selection_decision = explicit_decision

    if include_prior_runs:
        built_dataset = _include_prior_run_cases(
            built_dataset,
            store=store,
            target=target_adapter.identity,
            current_run_id=run_id,
        )

    async def _cli_default_mutation(prompt: str) -> Mapping[str, Any]:
        current_content = target_adapter.load_current_content()
        candidate_content = _default_cli_skill_candidate(
            current_content=current_content,
            trace_packs=trace_packs,
            mutation_prompt=prompt,
        )
        return {
            "content": candidate_content,
            "rationale": (
                "Generated a trajectory-backed skill proposal through the default "
                "CLI self-evolve mutator."
                if candidate_content != current_content
                else "No trajectory evidence available; preserved proposal-only baseline."
            ),
        }

    candidate_population_executor = (
        AWorldCandidatePopulationExecutor(
            agent_factory=lambda _slot: CandidateGenerationAgent(
                model_config=mutation_model_config
            ),
            parse_output=lambda raw_output: _parse_candidate_mutation_model_output(
                raw_output,
                current_content=target_adapter.load_current_content(),
            ),
            repair_prompt_builder=_candidate_mutation_repair_prompt,
            repair_output_merger=merge_candidate_repair_output,
        )
        if mutation_model_config is not None
        else None
    )

    async def _cli_candidate_population(prompts, max_concurrency):
        if candidate_population_executor is None:
            raise RuntimeError("candidate population executor is not configured")
        return await candidate_population_executor.run(
            prompts,
            max_concurrency=max_concurrency,
        )

    if apply_policy == "auto_verified" and evaluation_backend is None:
        evaluation_backend = _evaluation_backend_from_judge_config(
            judge_config,
            workspace_root=workspace_root,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if apply_policy == "auto_verified" and post_apply_evaluator is None:
        post_apply_evaluator = _default_post_apply_evaluator(target_adapter)
    if replay_enabled and candidate_replay_backend is None:
        candidate_replay_backend = AWorldCliCandidateReplayBackend()
        if hasattr(candidate_replay_backend, "concurrency_policy"):
            candidate_replay_backend.concurrency_policy = (
                effective_concurrency_policy
            )

    self_evolve_runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(
            mutate_text=_cli_default_mutation,
            population_callable=(
                _cli_candidate_population
                if candidate_population_executor is not None
                else None
            ),
            concurrency_policy=effective_concurrency_policy,
        ),
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply_evaluator,
        min_score_delta=min_score_delta,
        max_iterations=effective_iteration_budget,
        min_eval_cases=min_eval_cases,
        judge_repetitions=judge_repetitions,
        max_run_tokens=max_run_tokens,
        auto_apply_target_types=auto_apply_target_types,
        allow_generated_target_mutation=allow_generated_target_mutation,
        allow_external_target_mutation=allow_external_target_mutation,
        replay_enabled=replay_enabled,
        candidate_replay_backend=candidate_replay_backend,
        replay_timeout_seconds=replay_timeout_seconds,
        replay_max_steps=replay_max_steps,
        replay_candidate_limit=replay_candidate_limit,
        baseline_replay_repetitions=baseline_replay_repetitions,
        candidate_replay_repetitions=candidate_replay_repetitions,
        replay_stability_margin=replay_stability_margin,
        replay_adaptation_compiler=replay_adaptation_compiler,
        replay_agent=agent,
        runtime_registry_refresher=runtime_registry_refresher,
        runtime_skill_activator=runtime_skill_activator,
        progress_callback=progress_callback,
        concurrency_policy=effective_concurrency_policy,
    )
    from aworld.self_evolve.runtime import (
        SelfEvolveTaskRequest,
        build_self_evolve_task,
    )

    outer_task = build_self_evolve_task(
        SelfEvolveTaskRequest(
            runner=self_evolve_runner,
            run_kwargs={
                "run_id": run_id,
                "target": target_adapter,
                "dataset": built_dataset,
                "trace_packs": trace_packs,
                "apply_policy": apply_policy,
                "target_selection_report": target_selection_report,
                "target_provenance": target_provenance,
                "target_selection_decision": target_selection_decision,
            },
        ),
        task_id=f"{run_id}-self-evolve",
    )
    outer_responses = Runners.sync_run_task(outer_task)
    outer_response = outer_responses.get(outer_task.id)
    if outer_response is None or not outer_response.success:
        raise RuntimeError("self-evolve outer Task did not complete successfully")
    result = outer_response.answer
    run_path = store.run_path(run_id)
    if target_selection_report is not None:
        target_selection_path = run_path / "target_selection.json"
    if target_provenance is not None:
        target_provenance_path = run_path / "target_provenance.json"

    report_path = run_path / "report.json"
    selected_candidate_id = (
        result.selected_candidate.candidate_id
        if result.selected_candidate is not None
        else None
    )
    summary = {
        "report_path": str(report_path),
        "best_candidate_id": (
            selected_candidate_id
            if result.run.status.value == "succeeded" and apply_policy == "auto_verified"
            else None
        ),
        "selected_candidate_id": selected_candidate_id,
        "run_id": result.run.run_id,
        "status": result.run.status.value,
    }
    if target_selection_path is not None:
        summary["target_selection_path"] = str(target_selection_path)
    if target_provenance_path is not None:
        summary["target_provenance_path"] = str(target_provenance_path)
    elif (
        target_selection_report is not None
        and target_selection_report.selected_target is not None
    ):
        summary["target_provenance"] = {
            "status": target_selection_report.provenance_status or "unresolved",
            "reason": target_selection_report.provenance_reason,
        }
    if report_path.exists():
        try:
            report_payload = _load_json_mapping(report_path)
        except ValueError:
            report_payload = {}
        replay_path = report_payload.get("replay_path")
        if isinstance(replay_path, str):
            summary["replay_path"] = replay_path
        evaluator_report_paths = report_payload.get("evaluator_report_paths")
        if isinstance(evaluator_report_paths, list):
            summary["evaluator_report_paths"] = [
                item for item in evaluator_report_paths if isinstance(item, str)
            ]
        gate_results = report_payload.get("gate_results")
        if isinstance(gate_results, list):
            summary["gate_results"] = [
                item for item in gate_results if isinstance(item, Mapping)
            ]
    return summary


async def _run_candidate_generation_agent(
    agent: CandidateGenerationAgent,
    prompt: str,
) -> str:
    """Run one request through the optimize-scoped AWorld candidate agent."""

    return await agent.generate(prompt)


def _candidate_mutation_repair_prompt(
    invalid_output: str,
    error: ValueError,
) -> str:
    diagnostic = (
        error.to_diagnostic()
        if isinstance(error, CandidateProtocolError)
        else {
            "code": "candidate_protocol_invalid",
            "stage": "candidate_protocol",
            "failure_class": "candidate",
            "repairable": True,
        }
    )
    payload = {
        "candidate_schema": dict(CANDIDATE_OUTPUT_CONTRACT),
        "diagnostics": [diagnostic],
        # Candidate packages commonly contain complete compiler/runtime sources.
        # Preserve a bounded full package when possible so representation repair
        # does not reconstruct missing file tails from a small prefix.
        "invalid_response": sanitize_text(invalid_output, max_chars=64_000),
    }
    return (
        "Repair representation only using the supplied schema and diagnostic. "
        "Do not invent new task evidence. Return exactly one candidate JSON object.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _parse_candidate_mutation_model_output(
    raw_output: Any,
    *,
    current_content: str,
) -> Mapping[str, Any]:
    return normalize_candidate_output(
        raw_output,
        current_content=current_content,
    )


def _replayable_user_task_dataset(dataset: SelfEvolveDataset) -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=tuple(
            case for case in dataset.cases if _is_replayable_user_task_case(case)
        ),
        recipe=dataset.recipe,
    )


def _evaluation_backend_from_judge_config(
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None,
    *,
    workspace_root: str | Path,
    judge_repetitions: int = 1,
    judge_timeout_seconds: float | None = 300.0,
) -> EvaluationBackend:
    if judge_config is None:
        return SkillCandidateOverlayBackend()
    config = (
        SelfEvolveJudgeConfig.model_validate(judge_config)
        if isinstance(judge_config, Mapping)
        else judge_config
    )
    if config.mode == "trajectory":
        return SkillCandidateOverlayBackend()
    if config.mode == "agent_md":
        if not config.agent_path:
            raise ValueError("agent_md self-evolve evaluator requires agent_path")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_agent=config.agent_path,
            judge_model_profile=config.model_profile,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if config.mode == "custom_agent":
        if not config.agent_id:
            raise ValueError("custom_agent self-evolve evaluator requires agent_id")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_agent_name=config.agent_id,
            judge_model_profile=config.model_profile,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if config.mode == "backend_ref":
        if not config.backend_ref:
            raise ValueError("backend_ref self-evolve evaluator requires backend_ref")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_backend_ref=config.backend_ref,
            judge_model_profile=config.model_profile,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if config.mode == "disabled":
        raise ValueError("auto_verified self-evolve requires an evaluation backend")
    raise ValueError(f"unsupported judge mode: {config.mode}")


def _default_post_apply_evaluator(
    target: SelfEvolveTarget,
) -> Callable[[CandidateVariant], EvaluationSummary]:
    def evaluate(candidate: CandidateVariant) -> EvaluationSummary:
        target_path = _target_runtime_skill_path(target)
        loaded_skill_path: str | None = None
        runtime_skill_found = False
        loaded_from_real_path = False
        runtime_content_matches = False
        content_matches_target_file = (
            target_path.read_text(encoding="utf-8") == candidate.content
            if target_path is not None and target_path.exists()
            else False
        )

        if target_path is not None:
            registry = build_compat_registry(target_path.parent.parent)
            descriptor = next(
                (
                    item
                    for item in registry.list_descriptors()
                    if item.skill_name == target.identity.target_id
                ),
                None,
            )
            if descriptor is not None:
                runtime_skill_found = True
                loaded_skill_path = descriptor.skill_file
                loaded_from_real_path = Path(descriptor.skill_file).resolve() == target_path
                loaded_content = Path(descriptor.skill_file).read_text(encoding="utf-8")
                runtime_content_matches = (
                    _content_fingerprint(loaded_content)
                    == _content_fingerprint(candidate.content)
                )

        post_apply_passed = (
            content_matches_target_file
            and runtime_skill_found
            and loaded_from_real_path
            and runtime_content_matches
        )
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            dataset_split="post_apply",
            metrics={
                "post_apply_passed": post_apply_passed,
                "deterministic_signal": True,
                "evaluator_mode": "post_apply_runtime_loader",
                "content_matches_target_file": content_matches_target_file,
                "runtime_skill_found": runtime_skill_found,
                "loaded_from_real_path": loaded_from_real_path,
                "runtime_content_matches": runtime_content_matches,
                "loaded_skill_path": loaded_skill_path,
                "expected_skill_path": str(target_path) if target_path is not None else None,
            },
        )

    return evaluate


def _target_runtime_skill_path(target: SelfEvolveTarget) -> Path | None:
    runtime_path = getattr(target, "runtime_skill_path", None)
    if runtime_path is not None:
        return Path(runtime_path).resolve()
    return Path(target.identity.path).resolve() if target.identity.path else None


def _target_package_inventory(target: SelfEvolveTarget) -> tuple[str, ...]:
    target_path = _target_runtime_skill_path(target)
    if target_path is None or not target_path.exists():
        return ()
    root = target_path.parent
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    )


def _safe_artifact_name(value: str) -> str:
    readable = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in value
    ).strip("-")[:48] or "case"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{suffix}"


def _stable_json_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _artifact_retention_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    previous: Mapping[str, object] | None = None,
) -> dict[str, object]:
    try:
        cleanup: dict[str, object] = cleanup_self_evolve_artifacts(
            store.workspace_root,
            artifact_root=store.artifact_root,
            current_run_id=run_id,
        )
    except Exception as exc:
        current: dict[str, object] = {
            "status": "failed",
            "error": str(exc),
        }
        if previous is None:
            return current
        return _merge_artifact_retention_reports(previous, current)
    current: dict[str, object] = {
        "status": "completed",
        **cleanup,
    }
    if previous is None:
        return current
    return _merge_artifact_retention_reports(previous, current)


def _merge_artifact_retention_reports(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> dict[str, object]:
    removed_run_ids = sorted(
        {
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("removed_run_ids"))
            if isinstance(value, str) and value
        }
    )
    removed_paths = list(
        dict.fromkeys(
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("removed_paths"))
            if isinstance(value, str) and value
        )
    )
    final_state = current if current.get("status") == "completed" else previous
    skipped_runs = [
        value
        for value in _retention_sequence(final_state.get("skipped_runs"))
        if isinstance(value, Mapping)
    ]
    protected_run_ids = sorted(
        {
            str(value)
            for value in _retention_sequence(final_state.get("protected_run_ids"))
            if isinstance(value, str) and value
        }
    )
    statuses = tuple(report.get("status") for report in (previous, current))
    merged: dict[str, object] = {
        "status": (
            "completed" if statuses == ("completed", "completed") else "failed"
        ),
        "policy": current.get("policy", previous.get("policy", {})),
        "removed_run_count": len(removed_run_ids),
        "removed_run_ids": removed_run_ids,
        "removed_path_count": len(removed_paths),
        "removed_paths": removed_paths,
        "skipped_runs": skipped_runs,
        "protected_run_ids": protected_run_ids,
    }
    errors = [
        report.get("error")
        for report in (previous, current)
        if isinstance(report.get("error"), str)
    ]
    if errors:
        merged["errors"] = errors
    return merged


def _retention_sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(value)


def _rerun_evaluator_from_stored_run(
    *,
    workspace_root: str | Path,
    from_run: str,
    agent: str | None,
    task: str | None,
    apply_policy: str,
    evaluation_backend: EvaluationBackend | None,
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None,
    min_eval_cases: int,
    judge_repetitions: int,
    judge_timeout_seconds: float | None,
    max_run_tokens: int,
    min_score_delta: float,
    auto_apply_target_types: tuple[str, ...],
    allow_generated_target_mutation: bool,
    allow_external_target_mutation: bool,
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None,
    replay_timeout_seconds: int,
    replay_max_steps: int | None,
    replay_candidate_limit: int,
    baseline_replay_repetitions: int,
    candidate_replay_repetitions: int,
    replay_stability_margin: float,
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None,
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None,
    progress_callback: Callable[[str, str], Any] | None,
    concurrency_policy: SelfEvolveConcurrencyPolicy,
) -> Mapping[str, Any]:
    store = FilesystemSelfEvolveStore(workspace_root)
    source_run_path = _resolve_stored_run_path(store, from_run)
    source_run_id = source_run_path.name
    source_report = _load_json_mapping(source_run_path / "report.json")
    candidate_id = _stored_selected_candidate_id(source_report)
    candidate = _load_candidate_variant(source_run_path / "candidates" / f"{candidate_id}.json")
    replay_path = source_run_path / "replay" / candidate.candidate_id
    replay_result = load_candidate_replay_result(replay_path)

    source_config, split_seed = _source_config_from_stored_dataset_recipe(
        source_run_path / "dataset_recipe.json"
    )
    built_dataset = build_dataset_from_source(
        source_config,
        current_trajectory=None,
        task_id=task,
        split_seed=split_seed,
    )
    if not candidate_replay_is_comparable(
        dataset=built_dataset,
        replay_result=replay_result,
        require_adapted=True,
    ):
        raise ValueError(
            "stored replay did not produce comparable paired outcomes; "
            "rerun the full optimize flow instead"
        )
    trace_packs = tuple(
        case.trace_pack for case in built_dataset.cases if case.trace_pack is not None
    )
    target_adapter = _target_from_ref(
        candidate.target,
        workspace_root=workspace_root,
        allow_auto_apply=(
            apply_policy == "auto_verified"
            and candidate.target.target_type in auto_apply_target_types
        ),
    )
    target_selection_report = _load_target_selection_report(
        source_run_path / "target_selection.json"
    )
    stored_provenance_resolution = _load_target_provenance(
        source_run_path / "target_provenance.json"
    )
    if target_selection_report is None:
        target_selection_report = TargetSelectionReport(
            selected_target=candidate.target,
            confidence=0.0,
            evidence_step_ids=(),
            failure_category="stored_target",
            no_target_reason=None,
            selection_origin=TargetSelectionOrigin.UNKNOWN,
        )
    selection_origin = (
        target_selection_report.selection_origin
        or TargetSelectionOrigin.UNKNOWN
    )
    if target_selection_report.selected_target != candidate.target:
        authoritative_resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason="stored target selection does not match candidate target",
        )
    else:
        authoritative_resolution = build_target_selection_decision(
            target_selection_report,
            inventory=build_default_target_inventory(workspace_root),
            selection_origin=selection_origin,
            workspace_root=workspace_root,
        ).provenance_resolution
    if not stored_provenance_resolution.resolved:
        authoritative_resolution = stored_provenance_resolution
    elif (
        authoritative_resolution.provenance
        != stored_provenance_resolution.provenance
    ):
        authoritative_resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason="stored provenance does not match authoritative resolution",
        )
    target_selection_report = replace(
        target_selection_report,
        provenance_status=authoritative_resolution.status,
        provenance_reason=authoritative_resolution.reason,
        selection_origin=selection_origin,
    )
    target_selection_decision = TargetSelectionDecision(
        report=target_selection_report,
        provenance_resolution=authoritative_resolution,
        selection_origin=selection_origin,
    )
    target_provenance = (
        authoritative_resolution.provenance
        if authoritative_resolution.resolved
        else None
    )
    if apply_policy == "auto_verified" and evaluation_backend is None:
        evaluation_backend = _evaluation_backend_from_judge_config(
            judge_config,
            workspace_root=workspace_root,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if apply_policy == "auto_verified" and post_apply_evaluator is None:
        post_apply_evaluator = _default_post_apply_evaluator(target_adapter)

    run_id = _rerun_cli_run_id(source_run_id, candidate.candidate_id)
    _emit_progress(
        progress_callback,
        "resume",
        f"Reusing replay artifacts from {source_run_id} for candidate {candidate.candidate_id}",
    )

    self_evolve_runner = SelfEvolveRunner(
        store=store,
        optimizer=_FixedCandidateOptimizer(
            candidate=candidate,
            source_run_id=source_run_id,
        ),
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply_evaluator,
        min_score_delta=min_score_delta,
        max_iterations=1,
        min_eval_cases=min_eval_cases,
        judge_repetitions=judge_repetitions,
        max_run_tokens=max_run_tokens,
        auto_apply_target_types=auto_apply_target_types,
        allow_generated_target_mutation=allow_generated_target_mutation,
        allow_external_target_mutation=allow_external_target_mutation,
        replay_enabled=True,
        candidate_replay_backend=_StoredCandidateReplayBackend(
            replay_result=replay_result,
            source_replay_path=str(replay_path),
        ),
        replay_timeout_seconds=replay_timeout_seconds,
        replay_max_steps=replay_max_steps,
        replay_candidate_limit=replay_candidate_limit,
        baseline_replay_repetitions=baseline_replay_repetitions,
        candidate_replay_repetitions=candidate_replay_repetitions,
        replay_stability_margin=replay_stability_margin,
        replay_agent=agent,
        runtime_registry_refresher=runtime_registry_refresher,
        runtime_skill_activator=runtime_skill_activator,
        progress_callback=progress_callback,
        skip_duplicate_rejected_candidate_gate=True,
        concurrency_policy=concurrency_policy,
    )
    from aworld.self_evolve.runtime import (
        SelfEvolveTaskRequest,
        build_self_evolve_task,
    )

    outer_task = build_self_evolve_task(
        SelfEvolveTaskRequest(
            runner=self_evolve_runner,
            run_kwargs={
                "run_id": run_id,
                "target": target_adapter,
                "dataset": built_dataset,
                "trace_packs": trace_packs,
                "apply_policy": apply_policy,
                "target_selection_report": target_selection_report,
                "target_provenance": target_provenance,
                "target_selection_decision": target_selection_decision,
            },
        ),
        task_id=f"{run_id}-self-evolve",
    )
    outer_responses = Runners.sync_run_task(outer_task)
    outer_response = outer_responses.get(outer_task.id)
    if outer_response is None or not outer_response.success:
        raise RuntimeError("self-evolve outer Task did not complete successfully")
    result = outer_response.answer
    run_path = store.run_path(run_id)
    report_path = run_path / "report.json"
    selected_candidate_id = (
        result.selected_candidate.candidate_id
        if result.selected_candidate is not None
        else None
    )
    report = _load_json_mapping(report_path)
    summary = {
        "report_path": str(report_path),
        "best_candidate_id": (
            selected_candidate_id
            if result.run.status.value == "succeeded" and apply_policy == "auto_verified"
            else None
        ),
        "selected_candidate_id": selected_candidate_id,
        "run_id": result.run.run_id,
        "status": result.run.status.value,
        "source_run_id": source_run_id,
        "replay_path": str(replay_path),
    }
    target_selection_path = run_path / "target_selection.json"
    if target_selection_path.exists():
        summary["target_selection_path"] = str(target_selection_path)
    target_provenance_path = run_path / "target_provenance.json"
    if target_provenance_path.exists():
        summary["target_provenance_path"] = str(target_provenance_path)
    evaluator_report_paths = report.get("evaluator_report_paths")
    if isinstance(evaluator_report_paths, list):
        summary["evaluator_report_paths"] = evaluator_report_paths
    gate_results = report.get("gate_results")
    if isinstance(gate_results, list):
        summary["gate_results"] = gate_results
    return summary


def _content_fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _resolve_stored_run_path(store: FilesystemSelfEvolveStore, from_run: str) -> Path:
    raw = Path(from_run).expanduser()
    if raw.exists():
        run_path = raw
    else:
        run_path = store.run_path(from_run)
    if not run_path.exists() or not run_path.is_dir():
        raise FileNotFoundError(f"self-evolve run not found: {from_run}")
    if not (run_path / "report.json").exists():
        raise FileNotFoundError(f"self-evolve report not found under run: {run_path}")
    return run_path


def _stored_selected_candidate_id(report: Mapping[str, Any]) -> str:
    for key in ("selected_candidate_id", "best_candidate_id"):
        value = report.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    candidate_ids = report.get("candidate_ids")
    if isinstance(candidate_ids, list):
        for value in candidate_ids:
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise ValueError("stored run report does not identify a selected candidate")


def _load_candidate_variant(path: Path) -> CandidateVariant:
    payload = _load_json_mapping(path)
    target_payload = payload.get("target")
    if not isinstance(target_payload, Mapping):
        raise ValueError(f"candidate JSON is missing target: {path}")
    return CandidateVariant(
        candidate_id=str(payload.get("candidate_id") or ""),
        target=SelfEvolveTargetRef(
            target_type=str(target_payload.get("target_type") or ""),
            target_id=str(target_payload.get("target_id") or ""),
            path=(
                str(target_payload.get("path"))
                if target_payload.get("path") is not None
                else None
            ),
        ),
        content=str(payload.get("content") or ""),
        rationale=str(payload.get("rationale") or ""),
        parent_candidate_ids=tuple(
            str(item)
            for item in payload.get("parent_candidate_ids", ())
            if isinstance(item, str)
        ),
        target_fingerprint=(
            str(payload.get("target_fingerprint"))
            if payload.get("target_fingerprint") is not None
            else None
        ),
        files=tuple(
            CandidateFileDelta(
                path=str(item.get("path") or ""),
                operation=str(item.get("operation") or "upsert"),
                content=(
                    str(item.get("content"))
                    if item.get("content") is not None
                    else None
                ),
                executable=item.get("executable") is True,
            )
            for item in payload.get("files", ())
            if isinstance(item, Mapping)
        ),
    )


def _source_config_from_stored_dataset_recipe(
    path: Path,
) -> tuple[SelfEvolveEvalSourceConfig, str]:
    payload = _load_json_mapping(path)
    source = payload.get("source")
    if not isinstance(source, Mapping):
        raise ValueError(f"dataset recipe is missing source: {path}")
    kind = str(source.get("kind") or "")
    if kind not in {"trajectory_log", "jsonl", "session", "batch_config"}:
        raise ValueError(f"stored dataset source cannot be rebuilt for rerun: {kind}")
    task_ids_payload = source.get("task_ids")
    task_ids = tuple(
        str(item)
        for item in task_ids_payload
        if isinstance(item, str)
    ) if isinstance(task_ids_payload, list) else ()
    if not task_ids:
        auto_grouping = source.get("auto_grouping")
        selected_case_ids = (
            auto_grouping.get("selected_case_ids")
            if isinstance(auto_grouping, Mapping)
            else None
        )
        if isinstance(selected_case_ids, list):
            task_ids = tuple(
                str(item) for item in selected_case_ids if isinstance(item, str)
            )
    source_config = SelfEvolveEvalSourceConfig(
        kind=kind,
        path=(str(source.get("path")) if source.get("path") is not None else None),
        session_id=(
            str(source.get("session_id"))
            if source.get("session_id") is not None
            else None
        ),
        task_ids=task_ids,
    )
    split_seed = str(payload.get("split_seed") or "self-evolve-default-split")
    return source_config, split_seed


def _load_target_selection_report(path: Path) -> TargetSelectionReport | None:
    if not path.exists():
        return None
    payload = _load_json_mapping(path)
    target_payload = payload.get("selected_target")
    selected_target: SelfEvolveTargetRef | None = None
    if isinstance(target_payload, Mapping):
        selected_target = SelfEvolveTargetRef(
            target_type=str(target_payload.get("target_type") or ""),
            target_id=str(target_payload.get("target_id") or ""),
            path=(
                str(target_payload.get("path"))
                if target_payload.get("path") is not None
                else None
            ),
        )
    selection_origin_payload = payload.get("selection_origin")
    try:
        selection_origin = (
            TargetSelectionOrigin(selection_origin_payload)
            if isinstance(selection_origin_payload, str)
            else None
        )
    except ValueError:
        selection_origin = None
    return TargetSelectionReport(
        selected_target=selected_target,
        confidence=float(payload.get("confidence") or 0.0),
        evidence_step_ids=tuple(
            str(item)
            for item in payload.get("evidence_step_ids", ())
            if isinstance(item, str)
        ),
        failure_category=str(payload.get("failure_category") or "unknown"),
        signals=tuple(
            str(item)
            for item in payload.get("signals", ())
            if isinstance(item, str)
        ),
        no_target_reason=(
            str(payload.get("no_target_reason"))
            if payload.get("no_target_reason") is not None
            else None
        ),
        diagnostics=(
            dict(payload.get("diagnostics"))
            if isinstance(payload.get("diagnostics"), Mapping)
            else None
        ),
        provenance_status=(
            str(payload.get("provenance_status"))
            if payload.get("provenance_status") is not None
            else None
        ),
        provenance_reason=(
            str(payload.get("provenance_reason"))
            if payload.get("provenance_reason") is not None
            else None
        ),
        selection_origin=selection_origin,
    )


def _load_target_provenance(path: Path) -> TargetProvenanceResolution:
    if not path.exists():
        return TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason="target provenance sidecar is missing",
        )
    try:
        payload = _load_json_mapping(path)
    except ValueError as exc:
        return TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason=f"target provenance sidecar is unreadable: {exc}",
        )
    return load_target_provenance_payload(payload)


def _rerun_cli_run_id(source_run_id: str, candidate_id: str) -> str:
    return (
        "cli-rerun-"
        f"{abs(hash((source_run_id, candidate_id, 'evaluator'))) % 10**12:012d}"
    )


def _replay_report(replay_result: CandidateReplayResult) -> dict[str, object]:
    report: dict[str, object] = {
        "request": {
            "run_id": replay_result.request.run_id,
            "task_id": replay_result.request.task_id,
            "candidate_id": replay_result.request.candidate_id,
            "overlay_skill_root": replay_result.request.overlay_skill_root,
            "baseline_replay_dir": replay_result.request.baseline_replay_dir,
            "timeout_seconds": replay_result.request.timeout_seconds,
            "max_steps": replay_result.request.max_steps,
            "max_tokens": replay_result.request.max_tokens,
            "dataset_fingerprint": replay_result.request.dataset_fingerprint,
            "baseline_skill_fingerprint": (
                replay_result.request.baseline_skill_fingerprint
            ),
            "adaptation_fingerprint": (
                replay_result.request.adaptation_fingerprint
            ),
            "workspace_seed_fingerprint": (
                replay_result.request.workspace_seed_fingerprint
            ),
        },
        "overlay_skill_root": replay_result.request.overlay_skill_root,
        "baseline": {
            "variant_id": replay_result.baseline.variant_id,
            "status": replay_result.baseline.status,
            "metrics": dict(replay_result.baseline.metrics),
            "stdout_path": replay_result.baseline.stdout_path,
            "stderr_path": replay_result.baseline.stderr_path,
            "failure": replay_result.baseline.failure,
        },
        "candidate": {
            "variant_id": replay_result.candidate.variant_id,
            "status": replay_result.candidate.status,
            "metrics": dict(replay_result.candidate.metrics),
            "stdout_path": replay_result.candidate.stdout_path,
            "stderr_path": replay_result.candidate.stderr_path,
            "failure": replay_result.candidate.failure,
        },
    }
    if replay_result.request.replay_adaptation is not None:
        adaptation = replay_result.request.replay_adaptation
        report["adaptation"] = {
            "schema_version": adaptation.schema_version,
            "ready": adaptation.ready,
            "adaptation_fingerprint": adaptation.adaptation_fingerprint,
            "workspace_seed_fingerprint": adaptation.workspace_seed_fingerprint,
            "environment_fingerprint": adaptation.environment_fingerprint,
            "manifest_path": adaptation.manifest_path,
            "environment_snapshot_path": adaptation.environment_snapshot_path,
            "cases": [
                {
                    "case_id": case.case_id,
                    "readiness": case.readiness,
                    "task_input_fingerprint": case.task_input_fingerprint,
                }
                for case in adaptation.cases
            ],
        }
        capability_report = _replay_capability_report(replay_result)
        if capability_report is not None:
            report["replay_capability"] = capability_report
    if replay_result.member_results:
        report["members"] = [
            {
                "case_id": member.case_id,
                "baseline_status": member.baseline.status,
                "candidate_status": member.candidate.status,
                "baseline_metrics": dict(member.baseline.metrics),
                "candidate_metrics": dict(member.candidate.metrics),
                "baseline_failure": member.baseline.failure,
                "candidate_failure": member.candidate.failure,
            }
            for member in replay_result.member_results
        ]
    return report


def _replay_capability_report(
    replay_result: CandidateReplayResult,
) -> dict[str, object] | None:
    adaptation = replay_result.request.replay_adaptation
    capability = adaptation.replay_capability if adaptation is not None else None
    if capability is None:
        return None
    frozen_root = Path(capability.frozen_root)
    return {
        "source": "candidate",
        "capability_id": capability.capability_id,
        "capability_package_fingerprint": (
            capability.capability_package_fingerprint
        ),
        "request_fingerprint": capability.request_fingerprint,
        "frozen_capability_fingerprint": capability.fingerprint,
        "deterministic": capability.deterministic,
        "ready": capability.ready,
        "handled_requirements": list(capability.handled_requirements),
        "unhandled_requirements": list(capability.unhandled_requirements),
        "frozen_root": capability.frozen_root,
        "compile_a_path": str(frozen_root.parent / "compile-a"),
        "compile_b_path": str(frozen_root.parent / "compile-b"),
        "frozen_manifest_path": str(frozen_root / "frozen_manifest.json"),
        "fixtures": [
            {"path": item.path, "sha256": item.sha256, "size": item.size}
            for item in capability.fixtures
        ],
        "runtime_files": [
            {"path": item.path, "sha256": item.sha256, "size": item.size}
            for item in capability.runtime_files
        ],
        "service_ids": [item.service_id for item in capability.services],
    }


def _replay_artifact_path(replay_result: CandidateReplayResult) -> str:
    return str(
        Path(replay_result.request.workspace_root)
        / ".aworld"
        / "self_evolve"
        / replay_result.request.run_id
        / "replay"
        / replay_result.request.candidate_id
    )


def _baseline_replay_artifact_dir(replay_result: CandidateReplayResult) -> str:
    if replay_result.member_results:
        return str(Path(_replay_artifact_path(replay_result)) / "members")
    if replay_result.request.baseline_replay_dir:
        return replay_result.request.baseline_replay_dir
    return str(Path(_replay_artifact_path(replay_result)) / "baseline")


def _find_reusable_baseline_replay_dir(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    target: SelfEvolveTargetRef,
    dataset: SelfEvolveDataset,
    baseline_repetitions: int,
    baseline_skill_fingerprint: str | None = None,
    dataset_fingerprint: str | None = None,
    adaptation_fingerprint: str | None = None,
    workspace_seed_fingerprint: str | None = None,
) -> str | None:
    expected_provenance = {
        "baseline_skill_fingerprint": baseline_skill_fingerprint,
        "dataset_fingerprint": dataset_fingerprint,
        "adaptation_fingerprint": adaptation_fingerprint,
        "workspace_seed_fingerprint": workspace_seed_fingerprint,
    }
    if any(value is None for value in expected_provenance.values()):
        return None
    root = store.artifact_root
    if not root.exists():
        return None
    case_ids = tuple(
        case.case_id for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    if not case_ids:
        case_ids = tuple(case.case_id for case in dataset.cases)
    run_dirs = [
        path
        for path in root.iterdir()
        if path.is_dir() and path.name != run_id
    ]
    for prior_run_dir in sorted(run_dirs, key=lambda path: path.stat().st_mtime, reverse=True):
        replay_root = prior_run_dir / "replay"
        if not replay_root.exists():
            continue
        replay_dirs = [path for path in replay_root.iterdir() if path.is_dir()]
        for replay_dir in sorted(replay_dirs, key=lambda path: path.stat().st_mtime, reverse=True):
            try:
                replay_result = load_candidate_replay_result(replay_dir)
            except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
                continue
            if not _replay_target_matches(replay_result.request.target, target):
                continue
            if not _replay_request_provenance_matches(
                replay_result.request,
                expected=expected_provenance,
            ):
                continue
            if replay_result.request.baseline_repetitions != baseline_repetitions:
                continue
            if replay_result.member_results:
                member_case_ids = tuple(member.case_id for member in replay_result.member_results)
                if set(member_case_ids) != set(case_ids):
                    continue
                member_repetitions = _distributed_member_repetitions(
                    baseline_repetitions,
                    member_count=len(case_ids),
                )
                if all(
                    member.baseline.succeeded
                    and _successful_replay_count(member.baseline) == member_repetitions
                    for member in replay_result.member_results
                ):
                    members_dir = replay_dir / "members"
                    if (members_dir / "manifest.json").exists():
                        return str(members_dir)
                continue
            if len(case_ids) != 1 or replay_result.request.task_id != case_ids[0]:
                continue
            if (
                replay_result.baseline.succeeded
                and _successful_replay_count(replay_result.baseline) == baseline_repetitions
            ):
                baseline_dir = replay_dir / "baseline"
                if baseline_dir.exists():
                    return str(baseline_dir)
    return None


def _replay_request_provenance_matches(
    request: CandidateReplayRequest,
    *,
    expected: Mapping[str, str | None],
) -> bool:
    return all(
        value is not None and getattr(request, key, None) == value
        for key, value in expected.items()
    )


def _legacy_member_baseline_replay_dir(
    *,
    replay_dir: Path,
    target: SelfEvolveTargetRef,
    case_ids: tuple[str, ...],
    baseline_repetitions: int,
) -> str | None:
    members_root = replay_dir / "members"
    if not members_root.exists():
        return None
    reusable_by_case: dict[str, Path] = {}
    for member_dir in sorted(members_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not member_dir.is_dir():
            continue
        request_path = member_dir / "request.json"
        if not request_path.exists():
            continue
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                continue
            target_payload = payload.get("target")
            if not isinstance(target_payload, Mapping):
                continue
            stored_target = SelfEvolveTargetRef(
                target_type=str(target_payload.get("target_type") or ""),
                target_id=str(target_payload.get("target_id") or ""),
                path=(
                    str(target_payload.get("path"))
                    if target_payload.get("path") is not None
                    else None
                ),
            )
            task_id = str(payload.get("task_id") or "")
            if task_id not in case_ids:
                continue
            if not _replay_target_matches(stored_target, target):
                continue
            baseline = _load_variant_result_from_dir(
                member_dir / "baseline",
                base_variant_id="baseline",
            )
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
            continue
        if baseline.succeeded and _successful_replay_count(baseline) >= baseline_repetitions:
            reusable_by_case[task_id] = member_dir / "baseline"
    if len(case_ids) == 1:
        baseline_dir = reusable_by_case.get(case_ids[0])
        return str(baseline_dir) if baseline_dir is not None else None
    if all(case_id in reusable_by_case for case_id in case_ids):
        return str(members_root)
    return None


def _replay_target_matches(stored: SelfEvolveTargetRef, current: SelfEvolveTargetRef) -> bool:
    if stored.target_type != current.target_type or stored.target_id != current.target_id:
        return False
    if stored.path is None or current.path is None:
        return True
    return Path(stored.path).expanduser() == Path(current.path).expanduser()


def _successful_replay_count(result: ReplayVariantResult) -> int:
    count = result.metrics.get("successful_repetition_count")
    if isinstance(count, (int, float)):
        return int(count)
    if result.repetition_results:
        return sum(1 for repetition in result.repetition_results if repetition.succeeded)
    return 1 if result.succeeded else 0


def _evaluator_report_paths(
    *summaries: EvaluationSummary | None,
) -> list[str]:
    paths: list[str] = []
    for summary in summaries:
        if summary is None:
            continue
        path = summary.metrics.get("report_path")
        if isinstance(path, str) and path not in paths:
            paths.append(path)
    return paths


def _duplicate_rejected_candidate_gate(
    candidate: CandidateVariant,
    *,
    rejected_candidate_ids: set[str],
    apply_policy: str,
) -> GateResult | None:
    if apply_policy != "auto_verified":
        return None
    duplicated = candidate.candidate_id in rejected_candidate_ids
    if not duplicated:
        return GateResult(
            gate_name="duplicate_rejected_candidate",
            passed=True,
            reason="candidate has not been previously rejected for this target",
        )
    return GateResult(
        gate_name="duplicate_rejected_candidate",
        passed=False,
        reason="candidate repeats a previously rejected candidate for this target",
        details={"candidate_id": candidate.candidate_id},
    )


def _duplicate_accepted_candidate_gate(
    candidate: CandidateVariant,
    *,
    accepted_candidate_ids: set[str],
    apply_policy: str,
) -> GateResult | None:
    if apply_policy != "auto_verified":
        return None
    duplicated = candidate.candidate_id in accepted_candidate_ids
    if not duplicated:
        return GateResult(
            gate_name="duplicate_accepted_candidate",
            passed=True,
            reason="candidate has not been previously accepted for this target",
        )
    return GateResult(
        gate_name="duplicate_accepted_candidate",
        passed=False,
        reason="candidate repeats a previously accepted candidate for this target",
        details={"candidate_id": candidate.candidate_id},
    )


def _load_prior_rejected_feedback(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    limit: int = 12,
) -> tuple[EvaluationSummary, ...]:
    root = store.artifact_root
    if not root.exists():
        return ()
    feedback: list[EvaluationSummary] = []
    report_paths = sorted(
        root.glob("*/report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for report_path in report_paths:
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(report, target):
            continue
        for item in _feedback_from_report(report, report_path=report_path):
            feedback.append(item)
            if len(feedback) >= limit:
                return tuple(feedback)
    return tuple(feedback)


def _load_prior_rejected_semantic_lesson_fingerprints(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    limit: int = 64,
) -> set[tuple[str, str]]:
    root = store.artifact_root
    if not root.exists():
        return set()
    fingerprints: set[tuple[str, str]] = set()
    report_paths = sorted(
        root.glob("*/report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for report_path in report_paths:
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(report, target):
            continue
        rejected_ids = _rejected_candidate_ids_from_report(report)
        if not rejected_ids and str(report.get("status")) != "rejected":
            continue
        for lineage in _lineage_records_from_report(
            report,
            report_path=report_path,
            import_missing=True,
        ):
            candidate_id = lineage.get("candidate_id")
            if rejected_ids and candidate_id not in rejected_ids:
                continue
            semantic = lineage.get("semantic_fingerprint")
            lesson_set = lineage.get("lesson_set_fingerprint")
            if isinstance(semantic, str) and isinstance(lesson_set, str):
                fingerprints.add((semantic, lesson_set))
                if len(fingerprints) >= limit:
                    return fingerprints
    return fingerprints


def _rejected_candidate_ids_from_report(report: Mapping[str, Any]) -> set[str]:
    rejected: set[str] = set()
    retryable_infra_rejections: set[str] = set()
    iterations = report.get("iterations")
    if isinstance(iterations, list):
        for item in iterations:
            if not isinstance(item, Mapping):
                continue
            if item.get("status") != "rejected":
                continue
            candidate_id = item.get("candidate_id")
            if isinstance(candidate_id, str) and candidate_id:
                if _non_authoritative_candidate_rejection(
                    _historical_feedback_metrics(item)
                ):
                    retryable_infra_rejections.add(candidate_id)
                    continue
                rejected.add(candidate_id)
    selected = report.get("selected_candidate_id")
    if (
        str(report.get("status")) == "rejected"
        and isinstance(selected, str)
        and selected
        and selected not in retryable_infra_rejections
    ):
        rejected.add(selected)
    return rejected


def _lineage_records_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
    import_missing: bool = False,
) -> tuple[Mapping[str, Any], ...]:
    run_root = report_path.parent.resolve()
    lineage_paths: list[Path] = []
    optimizer_lineage = report.get("optimizer_lineage")
    if isinstance(optimizer_lineage, Mapping):
        raw_paths = optimizer_lineage.get("paths")
        if isinstance(raw_paths, list):
            for raw_path in raw_paths:
                if isinstance(raw_path, str) and raw_path:
                    lineage_paths.append(Path(raw_path))
    default_dir = run_root / "optimizer_lineage"
    if default_dir.exists():
        lineage_paths.extend(default_dir.glob("*.json"))

    records: list[Mapping[str, Any]] = []
    seen_paths: set[Path] = set()
    for lineage_path in lineage_paths:
        candidate_path = lineage_path
        if not candidate_path.is_absolute():
            candidate_path = run_root / candidate_path
        try:
            resolved = candidate_path.resolve()
        except OSError:
            continue
        if resolved in seen_paths or not _path_is_relative_to(resolved, run_root):
            continue
        seen_paths.add(resolved)
        try:
            payload = _load_json_mapping(resolved)
        except Exception:
            continue
        records.append(payload)
    if import_missing:
        records.extend(
            _lazy_import_lineage_records_from_report(
                report,
                report_path=report_path,
                existing_candidate_ids={
                    str(record.get("candidate_id"))
                    for record in records
                    if isinstance(record.get("candidate_id"), str)
                },
            )
        )
    return tuple(records)


def _lazy_import_lineage_records_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
    existing_candidate_ids: set[str],
) -> tuple[Mapping[str, Any], ...]:
    run_root = report_path.parent.resolve()
    lineage_dir = run_root / "optimizer_lineage"
    records: list[Mapping[str, Any]] = []
    for iteration in _lineage_importable_iterations(report):
        candidate_id = iteration.get("candidate_id")
        semantic = iteration.get("semantic_fingerprint")
        lesson_set = iteration.get("lesson_set_fingerprint")
        if not (
            isinstance(candidate_id, str)
            and candidate_id
            and isinstance(semantic, str)
            and semantic
            and isinstance(lesson_set, str)
            and lesson_set
        ):
            continue
        if candidate_id in existing_candidate_ids:
            continue
        file_stem = _safe_lineage_file_stem(candidate_id)
        if file_stem is None:
            continue
        payload: dict[str, Any] = {
            "candidate_id": candidate_id,
            "optimizer_name": "prior-report-import",
            "optimizer_version": "1",
            "semantic_fingerprint": semantic,
            "lesson_set_fingerprint": lesson_set,
            "rationale": "Imported lazily from prior self-evolve report.",
        }
        trainable_case_ids = iteration.get("trainable_case_ids")
        if isinstance(trainable_case_ids, list):
            payload["trainable_case_ids"] = [
                str(case_id) for case_id in trainable_case_ids if case_id
            ]
        addressed_lesson_ids = iteration.get("addressed_lesson_ids")
        if isinstance(addressed_lesson_ids, list):
            payload["addressed_lesson_ids"] = [
                str(lesson_id) for lesson_id in addressed_lesson_ids if lesson_id
            ]
        try:
            lineage_dir.mkdir(parents=True, exist_ok=True)
            lineage_path = lineage_dir / f"{file_stem}.json"
            if not lineage_path.exists():
                lineage_path.write_text(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
        except OSError:
            pass
        existing_candidate_ids.add(candidate_id)
        records.append(payload)
    return tuple(records)


def _lineage_importable_iterations(
    report: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    iterations = report.get("iterations")
    if not isinstance(iterations, list):
        return ()
    records: list[Mapping[str, Any]] = []
    for item in iterations:
        if not isinstance(item, Mapping):
            continue
        if item.get("status") != "rejected":
            continue
        records.append(item)
    return tuple(records)


def _safe_lineage_file_stem(candidate_id: str) -> str | None:
    safe_chars = []
    for char in candidate_id:
        if char.isalnum() or char in ("-", "_", "."):
            safe_chars.append(char)
        else:
            safe_chars.append("_")
    stem = "".join(safe_chars).strip("._")
    if not stem:
        return None
    return stem[:120]


def _persist_lineage_lifecycle(
    lineage_paths_by_candidate: Mapping[str, str],
    *,
    iteration_states: list[dict[str, object]],
    selected_candidate_id: str | None,
    post_apply: Mapping[str, object] | None,
) -> None:
    states_by_candidate: dict[str, dict[str, object]] = {}
    for state in iteration_states:
        candidate = state.get("candidate")
        candidate_id = getattr(candidate, "candidate_id", None)
        if isinstance(candidate_id, str) and candidate_id:
            states_by_candidate[candidate_id] = state

    for candidate_id, raw_path in lineage_paths_by_candidate.items():
        path = Path(raw_path)
        try:
            payload = dict(_load_json_mapping(path))
        except Exception:
            continue
        state = states_by_candidate.get(candidate_id)
        if state is None:
            payload.setdefault("lifecycle_status", "generated")
            payload.setdefault("replayed", False)
        else:
            status = state.get("status")
            payload["lifecycle_status"] = str(status or "generated")
            payload["replayed"] = state.get("replay_result") is not None
            gate_results = state.get("gate_results")
            if isinstance(gate_results, list):
                payload["failed_gates"] = [
                    gate.gate_name
                    for gate in gate_results
                    if isinstance(gate, GateResult) and not gate.passed
                ]
            replay_result = state.get("replay_result")
            if isinstance(replay_result, CandidateReplayResult):
                payload["baseline_replay_status"] = replay_result.baseline.status
                payload["candidate_replay_status"] = replay_result.candidate.status
            candidate_summary = state.get("candidate_summary")
            if isinstance(candidate_summary, EvaluationSummary):
                payload["candidate_score"] = candidate_summary.metrics.get("score")
        if candidate_id == selected_candidate_id and post_apply is not None:
            payload["post_apply_status"] = post_apply.get("status")
            payload["release_state"] = post_apply.get("release_state")
            if post_apply.get("status") == "accepted":
                payload["lifecycle_status"] = "accepted"
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            continue


def _lineage_addressed_lesson_ids(raw_path: str | None) -> tuple[str, ...]:
    if not raw_path:
        return ()
    try:
        payload = _load_json_mapping(Path(raw_path))
    except Exception:
        return ()
    value = payload.get("addressed_lesson_ids")
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)


def _with_release_lesson_mapping(
    normalization_metrics: Mapping[str, Any],
    *,
    addressed_lesson_ids: tuple[str, ...],
) -> Mapping[str, Any]:
    if not addressed_lesson_ids:
        return normalization_metrics
    metrics = dict(normalization_metrics)
    metrics["addressed_lesson_ids"] = list(addressed_lesson_ids)
    preserved_constraints = metrics.get("preserved_runtime_constraints")
    if isinstance(preserved_constraints, list):
        metrics["runtime_constraint_lesson_map"] = [
            {
                "constraint": str(constraint),
                "lesson_ids": list(addressed_lesson_ids),
            }
            for constraint in preserved_constraints
            if str(constraint).strip()
        ]
    return metrics


def _include_prior_run_cases(
    dataset: SelfEvolveDataset,
    *,
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    current_run_id: str,
    limit: int = 12,
) -> SelfEvolveDataset:
    prior_cases = _prior_run_eval_cases(
        store,
        target,
        current_run_id=current_run_id,
        limit=limit,
    )
    if not prior_cases:
        return dataset
    existing_case_ids = {case.case_id for case in dataset.cases}
    unique_prior_cases = tuple(
        case for case in prior_cases if case.case_id not in existing_case_ids
    )
    if not unique_prior_cases:
        return dataset
    source = dict(dataset.recipe.source)
    source["include_prior_runs"] = True
    source["prior_run_case_count"] = len(unique_prior_cases)
    source["prior_run_case_ids"] = [case.case_id for case in unique_prior_cases]
    trainable_case_ids = tuple(
        dict.fromkeys(
            [
                *dataset.recipe.trainable_case_ids,
                *(case.case_id for case in unique_prior_cases),
            ]
        )
    )
    splits = {
        key: list(value)
        for key, value in dataset.recipe.splits.items()
    }
    train_split = list(splits.get("train", []))
    train_split.extend(
        case.case_id for case in unique_prior_cases if case.case_id not in train_split
    )
    splits["train"] = train_split
    return SelfEvolveDataset(
        cases=(*dataset.cases, *unique_prior_cases),
        recipe=DatasetRecipe(
            source=source,
            split_seed=dataset.recipe.split_seed,
            splits=splits,
            synthetic_generation_policy=dataset.recipe.synthetic_generation_policy,
            trainable_case_ids=trainable_case_ids,
            held_out_case_ids=dataset.recipe.held_out_case_ids,
        ),
    )


def _prior_run_eval_cases(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    limit: int,
) -> tuple[EvalCase, ...]:
    root = store.artifact_root
    if not root.exists():
        return ()
    cases: list[EvalCase] = []
    report_paths = sorted(
        root.glob("*/report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for report_path in report_paths:
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(report, target):
            continue
        candidate_id = report.get("selected_candidate_id") or report.get("best_candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        status = str(report.get("status") or "unknown")
        case_id = f"prior-run:{report_path.parent.name}:{candidate_id}"
        cases.append(
            EvalCase(
                case_id=case_id,
                input=_prior_run_case_input(report, report_path=report_path),
                expected_output=None,
                metadata={
                    "trajectory_set": {
                        "set_id": "prior_self_evolve_runs",
                        "target": {
                            "target_type": target.target_type,
                            "target_id": target.target_id,
                            "path": target.path,
                        },
                        "member": {
                            "member_id": case_id,
                            "role": (
                                "accepted_followup"
                                if status == "succeeded"
                                else "rejected_candidate"
                            ),
                            "source_run_id": str(report.get("run_id") or report_path.parent.name),
                            "candidate_id": candidate_id,
                        },
                    }
                },
                source={
                    "kind": "prior_self_evolve_run",
                    "path": str(report_path),
                    "source_run_id": str(report.get("run_id") or report_path.parent.name),
                    "candidate_id": candidate_id,
                    "status": status,
                    "role": (
                        "accepted_followup"
                        if status == "succeeded"
                        else "rejected_candidate"
                    ),
                },
            )
        )
        if len(cases) >= limit:
            break
    return tuple(cases)


def _prior_run_case_input(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> Mapping[str, Any]:
    gate_results = report.get("gate_results")
    failed_gates = [
        sanitize_text(gate.get("gate_name"), max_chars=80)
        for gate in gate_results
        if isinstance(gate, Mapping) and gate.get("passed") is False and gate.get("gate_name")
    ] if isinstance(gate_results, list) else []
    post_apply = report.get("post_apply")
    return {
        "source": "prior_self_evolve_run",
        "run_id": sanitize_text(report.get("run_id") or report_path.parent.name, max_chars=120),
        "status": sanitize_text(report.get("status"), max_chars=80),
        "selected_candidate_id": sanitize_text(
            report.get("selected_candidate_id"),
            max_chars=160,
        ),
        "failed_gates": failed_gates[:12],
        "replay_path": sanitize_path_ref(report.get("replay_path")),
        "evaluator_report_paths": _sanitized_path_list(
            report.get("evaluator_report_paths")
        ),
        "post_apply_status": (
            sanitize_text(post_apply.get("status"), max_chars=80)
            if isinstance(post_apply, Mapping)
            else None
        ),
        "post_apply_release_state": (
            sanitize_text(post_apply.get("release_state"), max_chars=80)
            if isinstance(post_apply, Mapping)
            else None
        ),
        "baseline_metrics": _prior_run_metric_summary(report.get("baseline_metrics")),
        "candidate_metrics": _prior_run_metric_summary(report.get("candidate_metrics")),
        "acceptance_confidence": report.get("acceptance_confidence")
        if isinstance(report.get("acceptance_confidence"), Mapping)
        else None,
        "report_path": sanitize_path_ref(report_path),
    }


def _sanitized_path_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        sanitize_path_ref(item)
        for item in value[:8]
        if isinstance(item, str) and item
    ]


def _prior_run_metric_summary(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    keys = (
        "score",
        "A1_groundedness",
        "A2_completeness",
        "B1_tool_use",
        "B2_efficiency",
        "evidence_compacted",
        "evidence_incomplete",
        "evidence_bundle_valid",
        "latency_ms",
    )
    payload = {
        key: value[key]
        for key in keys
        if isinstance(value.get(key), bool) or isinstance(value.get(key), (int, float, str))
    }
    return {
        key: sanitize_metric_value(item)
        for key, item in payload.items()
    }


def _report_matches_target(
    report: Mapping[str, Any],
    target: SelfEvolveTargetRef,
) -> bool:
    payload = report.get("target")
    if not isinstance(payload, Mapping):
        return False
    return (
        payload.get("target_type") == target.target_type
        and payload.get("target_id") == target.target_id
        and (
            target.path is None
            or payload.get("path") is None
            or str(payload.get("path")) == str(target.path)
        )
    )


def _feedback_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    items: list[EvaluationSummary] = []
    repair_feedback = (
        *_repair_feedback_from_selected_candidate(
            report,
            report_path=report_path,
        ),
        *_repair_feedback_from_screening_report(
            report,
            report_path=report_path,
        ),
    )
    seen_repair_candidates: set[str] = set()
    for feedback in repair_feedback:
        if feedback.variant_id in seen_repair_candidates:
            continue
        seen_repair_candidates.add(feedback.variant_id)
        items.append(feedback)
    items.extend(_lesson_feedback_from_report(report, report_path=report_path))
    iterations = report.get("iterations")
    if isinstance(iterations, list):
        for iteration in iterations:
            if not isinstance(iteration, Mapping):
                continue
            if iteration.get("status") not in {"rejected", "accepted"}:
                continue
            candidate_id = iteration.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                continue
            metrics = _historical_feedback_metrics(iteration)
            metrics["candidate_status"] = str(iteration.get("status"))
            metrics["run_id"] = report.get("run_id")
            metrics["report_path"] = str(report_path)
            items.append(
                EvaluationSummary(
                    variant_id=candidate_id,
                    metrics=metrics,
                    dataset_split="historical",
                )
            )
    return tuple(items)


def _repair_feedback_from_selected_candidate(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    candidate_id = report.get("selected_candidate_id")
    raw_gates = report.get("gate_results")
    if (
        not isinstance(candidate_id, str)
        or not candidate_id
        or not isinstance(raw_gates, list)
    ):
        return ()
    package = _stored_repair_candidate_package(
        report_path=report_path,
        candidate_id=candidate_id,
    )
    if package is None:
        return ()

    judge_metrics, judge_split = _selected_candidate_judge_metrics(
        report,
        candidate_id=candidate_id,
    )
    judge_repair_gates = {
        "evidence_quality",
        "required_verification",
        "held_out_verification",
        "judge_only_signal",
        "global_regression_benchmark",
        "score_improvement",
        "cost_latency",
        "replay_stability",
    }

    gates: list[GateResult] = []
    for item in raw_gates:
        if not isinstance(item, Mapping) or item.get("passed") is not False:
            continue
        details = item.get("details")
        gate_name = item.get("gate_name")
        if not isinstance(gate_name, str) or not gate_name:
            continue
        candidate_repair = (
            isinstance(details, Mapping)
            and details.get("failure_class") == "candidate"
            and details.get("repairable") is True
        )
        judge_repair = bool(judge_metrics) and gate_name in judge_repair_gates
        if not candidate_repair and not judge_repair:
            continue
        bounded_details = dict(details) if isinstance(details, Mapping) else {}
        if judge_repair:
            bounded_details.setdefault("failure_class", "candidate")
            bounded_details.setdefault("repairable", True)
            bounded_details.setdefault("failure_stage", "judge_evaluation")
        failure_artifacts = _historical_failure_artifact_excerpts(
            report_path=report_path,
            artifact_root=bounded_details.get("artifact_root"),
        )
        if failure_artifacts:
            bounded_details["failure_artifacts"] = list(failure_artifacts)
        gates.append(
            GateResult(
                gate_name=gate_name,
                passed=False,
                reason=sanitize_text(item.get("reason"), max_chars=320),
                details=bounded_details,
            )
        )
    if not gates:
        return ()
    metrics = _typed_gate_feedback_metrics(gates)
    metrics.update(judge_metrics)
    metrics.update(
        {
            "failed_gates": [gate.gate_name for gate in gates],
            "candidate_status": "repairable",
            "authoritative_replay_failure": True,
            "run_id": report.get("run_id") or report_path.parent.name,
            "report_path": str(report_path),
            "repair_candidate_package": package,
        }
    )
    return (
        EvaluationSummary(
            variant_id=candidate_id,
            metrics=metrics,
            dataset_split=judge_split or "historical_repair",
        ),
    )


def _selected_candidate_judge_metrics(
    report: Mapping[str, Any],
    *,
    candidate_id: str,
) -> tuple[dict[str, Any], str | None]:
    """Rehydrate judge metrics onto the selected candidate repair package.

    Iteration history stores evaluated metrics separately from the candidate
    source package.  Joining them here preserves the deepest repair frontier
    when a later optimize run learns from a rejected report.
    """

    iterations = report.get("iterations")
    if not isinstance(iterations, list):
        return {}, None
    for iteration in reversed(iterations):
        if (
            not isinstance(iteration, Mapping)
            or iteration.get("candidate_id") != candidate_id
        ):
            continue
        candidate_metrics = iteration.get("candidate_metrics")
        held_out_metrics = iteration.get("held_out_metrics")
        selected_metrics: Mapping[str, Any] | None = None
        selected_split: str | None = None
        if isinstance(held_out_metrics, Mapping) and any(
            key in held_out_metrics
            for key in (
                "score",
                "A1_groundedness",
                "A2_completeness",
                "evidence_incomplete",
                "veto_triggered",
            )
        ):
            selected_metrics = held_out_metrics
            selected_split = "held_out"
        elif isinstance(candidate_metrics, Mapping) and any(
            key in candidate_metrics
            for key in (
                "score",
                "A1_groundedness",
                "A2_completeness",
                "evidence_incomplete",
                "veto_triggered",
            )
        ):
            selected_metrics = candidate_metrics
            selected_split = "validation"
        if selected_metrics is None:
            return {}, None
        metrics = dict(selected_metrics)
        failed_gates = iteration.get("failed_gates")
        if isinstance(failed_gates, list):
            metrics["failed_gates"] = [
                str(gate) for gate in failed_gates if str(gate)
            ]
        return metrics, selected_split
    return {}, None


def _historical_failure_artifact_excerpts(
    *,
    report_path: Path,
    artifact_root: Any,
) -> tuple[Mapping[str, str], ...]:
    if not isinstance(artifact_root, str) or not artifact_root:
        return ()
    run_root = report_path.parent.resolve()
    try:
        root = Path(artifact_root).expanduser().resolve()
    except OSError:
        return ()
    if not _path_is_relative_to(root, run_root) or not root.is_dir():
        return ()

    excerpts: list[Mapping[str, str]] = []
    inspected = 0
    try:
        paths = root.rglob("*")
        for path in paths:
            inspected += 1
            if inspected > 512 or len(excerpts) >= 4:
                break
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name.lower()
            is_diagnostic = (
                name.endswith((".stderr.txt", ".stdout.txt"))
                or name == "failure.json"
                or (
                    "diagnostic" in name
                    and path.suffix.lower() in {".json", ".txt", ".log"}
                )
            )
            if not is_diagnostic:
                continue
            try:
                with path.open("rb") as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - 4_096))
                    tail = handle.read(4_096).decode("utf-8", errors="replace")
            except OSError:
                continue
            # Preserve the terminal exception rather than the beginning of a
            # traceback; downstream metric compaction intentionally bounds each
            # diagnostic string to roughly one prompt paragraph.
            excerpt = sanitize_text(tail[-360:], max_chars=360)
            if not excerpt:
                continue
            excerpts.append(
                {
                    "path": sanitize_path_ref(
                        path.relative_to(run_root).as_posix()
                    ),
                    "tail": excerpt,
                }
            )
    except OSError:
        return tuple(excerpts)
    return tuple(excerpts)


def _repair_feedback_from_screening_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    population = report.get("population")
    if not isinstance(population, Mapping):
        return ()

    screenings: list[Mapping[str, Any]] = []
    screening_iterations = population.get("screening_iterations")
    if isinstance(screening_iterations, list):
        screenings.extend(
            item for item in screening_iterations if isinstance(item, Mapping)
        )
    screening = population.get("screening")
    if isinstance(screening, Mapping):
        screenings.append(screening)
    if not screenings:
        return ()

    feedback: list[EvaluationSummary] = []
    seen_candidate_ids: set[str] = set()
    attempts: list[Any] = []
    for screening_item in reversed(screenings):
        screening_attempts = screening_item.get("attempts")
        if isinstance(screening_attempts, list):
            attempts.extend(reversed(screening_attempts))
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or attempt.get("passed") is not False:
            continue
        candidate_id = attempt.get("candidate_id")
        details = attempt.get("details")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or not isinstance(details, Mapping)
            or details.get("failure_class") != "candidate"
            or details.get("repairable") is not True
            or candidate_id in seen_candidate_ids
        ):
            continue
        package = _stored_repair_candidate_package(
            report_path=report_path,
            candidate_id=candidate_id,
        )
        if package is None:
            continue
        seen_candidate_ids.add(candidate_id)
        gate = GateResult(
            gate_name="candidate_replay",
            passed=False,
            reason=sanitize_text(attempt.get("reason"), max_chars=320),
            details=details,
        )
        metrics = _typed_gate_feedback_metrics([gate])
        metrics.update(
            {
                "failed_gates": ["candidate_replay"],
                "candidate_status": "repairable",
                "run_id": report.get("run_id") or report_path.parent.name,
                "report_path": str(report_path),
                "repair_candidate_package": package,
            }
        )
        feedback.append(
            EvaluationSummary(
                variant_id=candidate_id,
                metrics=metrics,
                dataset_split="historical_repair",
            )
        )
        if len(feedback) >= _MAX_HISTORICAL_REPAIR_CANDIDATES:
            break
    return tuple(feedback)


def _stored_repair_candidate_package(
    *,
    report_path: Path,
    candidate_id: str,
) -> Mapping[str, object] | None:
    run_root = report_path.parent.resolve()
    payload: Mapping[str, Any] | None = None
    for candidate_path in (
        run_root / "candidates" / candidate_id / "candidate.json",
        run_root / "candidates" / f"{candidate_id}.json",
    ):
        try:
            resolved = candidate_path.resolve()
        except OSError:
            continue
        if not _path_is_relative_to(resolved, run_root) or not resolved.is_file():
            continue
        try:
            payload = _load_json_mapping(resolved)
        except Exception:
            continue
        break
    if payload is None:
        return None
    if payload.get("candidate_id") != candidate_id:
        return None
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        return None

    remaining_chars = _MAX_REPAIR_CANDIDATE_PACKAGE_CHARS
    files: list[dict[str, object]] = []
    for item in raw_files[:8]:
        if not isinstance(item, Mapping):
            continue
        path = item.get("path")
        operation = item.get("operation")
        if not isinstance(path, str) or not path or not isinstance(operation, str):
            continue
        file_payload: dict[str, object] = {
            "path": sanitize_text(path, max_chars=240),
            "operation": sanitize_text(operation, max_chars=40),
            "executable": item.get("executable") is True,
        }
        content = item.get("content")
        if isinstance(content, str) and remaining_chars > 0:
            content_limit = min(
                remaining_chars,
                _MAX_REPAIR_CANDIDATE_FILE_CHARS,
            )
            sanitized_content = sanitize_source_text(
                content,
                max_chars=content_limit,
            )
            file_payload["content"] = sanitized_content
            remaining_chars -= len(sanitized_content)
        files.append(file_payload)
    if not files:
        return None
    package = {
        "candidate_id": sanitize_text(candidate_id, max_chars=160),
        "rationale": sanitize_text(payload.get("rationale"), max_chars=1_000),
        "files": files,
    }
    raw_content = payload.get("content")
    if isinstance(raw_content, str) and raw_content.strip():
        package["content"] = sanitize_source_text(raw_content, max_chars=8_000)
    return package


def _lesson_feedback_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    lessons_path = _lessons_path_from_report(report, report_path=report_path)
    if lessons_path is None or not lessons_path.exists():
        return ()
    items: list[EvaluationSummary] = []
    try:
        raw_lines = lessons_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for raw_line in raw_lines:
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        lesson_id = payload.get("lesson_id")
        if not isinstance(lesson_id, str) or not lesson_id:
            continue
        lesson_metrics = payload.get("metrics")
        metrics: dict[str, Any] = dict(lesson_metrics) if isinstance(lesson_metrics, Mapping) else {}
        metrics.update(
            {
                "lesson_id": lesson_id,
                "lesson_type": str(payload.get("lesson_type") or ""),
                "lesson_title": _bounded_text(payload.get("title"), max_chars=160),
                "lesson_summary": _bounded_text(payload.get("summary"), max_chars=320),
                "run_id": report.get("run_id"),
                "report_path": str(report_path),
            }
        )
        source_run_ids = _string_list(payload.get("source_run_ids"))
        if source_run_ids:
            metrics["source_run_ids"] = source_run_ids
        source_task_ids = _string_list(payload.get("source_task_ids"))
        if source_task_ids:
            metrics["source_task_ids"] = source_task_ids
        items.append(
            EvaluationSummary(
                variant_id=lesson_id,
                metrics=metrics,
                dataset_split="lesson_memory",
            )
        )
    return tuple(items)


def _lessons_path_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> Path | None:
    run_root = report_path.parent.resolve()
    lessons = report.get("lessons")
    raw_path: str | None = None
    if isinstance(lessons, Mapping):
        path_value = lessons.get("path")
        if isinstance(path_value, str) and path_value:
            raw_path = path_value
    candidate_path = Path(raw_path) if raw_path is not None else run_root / "lessons" / "lessons.jsonl"
    if not candidate_path.is_absolute():
        candidate_path = run_root / candidate_path
    try:
        resolved = candidate_path.resolve()
    except OSError:
        return None
    if not _path_is_relative_to(resolved, run_root):
        return None
    return resolved


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _bounded_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _historical_feedback_metrics(iteration: Mapping[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    baseline_metrics = iteration.get("baseline_metrics")
    candidate_metrics = iteration.get("candidate_metrics")
    if isinstance(candidate_metrics, Mapping):
        metrics.update(dict(candidate_metrics))
    if isinstance(baseline_metrics, Mapping) and isinstance(candidate_metrics, Mapping):
        metrics.update(
            _baseline_comparison_feedback_metrics(
                baseline_summary=EvaluationSummary(
                    variant_id="baseline",
                    metrics=baseline_metrics,
                    dataset_split="historical",
                ),
                candidate_summary=EvaluationSummary(
                    variant_id=str(iteration.get("candidate_id") or "candidate"),
                    metrics=candidate_metrics,
                    dataset_split="historical",
                ),
            )
        )
    held_out_metrics = iteration.get("held_out_metrics")
    if isinstance(held_out_metrics, Mapping):
        for key, value in held_out_metrics.items():
            metrics.setdefault(f"held_out_{key}", value)
    failed_gates = iteration.get("failed_gates")
    if isinstance(failed_gates, list):
        metrics["failed_gates"] = [str(gate) for gate in failed_gates if gate]
    return metrics


def _retryable_infrastructure_rejection(metrics: Mapping[str, Any]) -> bool:
    if _has_missing_model_profile_judge_failure(metrics):
        return True
    failed_gates = {
        str(gate)
        for gate in metrics.get("failed_gates", ())
        if str(gate)
    }
    return bool(failed_gates) and failed_gates <= {
        "candidate_replay",
        "replay_confidence",
    } and not any(
        key in metrics
        for key in (
            "score",
            "candidate_score",
            "evaluator_gate_passed",
            "judge_attempt_count",
            "A1_groundedness",
            "A2_completeness",
        )
    )


def _non_authoritative_candidate_rejection(metrics: Mapping[str, Any]) -> bool:
    if _retryable_infrastructure_rejection(metrics):
        return True
    failed_gates = {
        str(gate)
        for gate in metrics.get("failed_gates", ())
        if str(gate)
    }
    return failed_gates == {"duplicate_rejected_candidate"}


def _has_missing_model_profile_judge_failure(metrics: Mapping[str, Any]) -> bool:
    for key, value in metrics.items():
        if not str(key).endswith("judge_failures"):
            continue
        if not isinstance(value, list):
            continue
        for failure in value:
            if not isinstance(failure, Mapping):
                continue
            reason = str(failure.get("reason") or "")
            if "model profile not found or incomplete" in reason:
                return True
    return False


def _evidence_quality_gate(summary: EvaluationSummary) -> GateResult | None:
    metrics = summary.metrics
    requires_evidence_quality = (
        metrics.get("evaluator_mode") == "aworld_trajectory_evaluator"
        or metrics.get("evaluator_source_kind") == "trajectory"
        or any(
            key in metrics
            for key in (
                "has_evidence",
                "evidence_block_count",
                "evidence_compacted",
                "evidence_incomplete",
            )
        )
    )
    if not requires_evidence_quality:
        return None
    return EvidenceQualityGate().evaluate(summary)


def _summary_with_replay_evidence_metrics(
    summary: EvaluationSummary,
    replay_variant: ReplayVariantResult,
) -> EvaluationSummary:
    replay_metrics = replay_variant.metrics or {}
    evidence_metric_names = (
        "evidence_strategy_passed",
        "evidence_manifest_entry_count",
        "evidence_manifest_invalid_entry_count",
        "evidence_manifest_present",
        "evidence_manifest_valid",
        "evidence_compaction_signals",
        "evidence_bundle_path",
        "evidence_bundle_present",
        "evidence_bundle_valid",
        "evidence_bundle_entry_count",
        "failed_repetition_count",
        "repetition_failures",
    )
    merged_metrics = dict(summary.metrics)
    for metric_name in evidence_metric_names:
        if metric_name in replay_metrics:
            merged_metrics.setdefault(metric_name, replay_metrics[metric_name])
            merged_metrics[f"replay_{metric_name}"] = replay_metrics[metric_name]
    failure_summary = _replay_failure_summary(replay_metrics.get("repetition_failures"))
    merged_metrics.update(failure_summary)
    return replace(summary, metrics=merged_metrics)


def _replay_failure_summary(value: object) -> dict[str, object]:
    if not isinstance(value, list):
        return {}
    reasons: list[str] = []
    types: list[str] = []
    evidence_manifest_invalid_entry_count = 0
    for item in value:
        if not isinstance(item, Mapping):
            continue
        reason = item.get("reason")
        if isinstance(reason, str) and reason and reason not in reasons:
            reasons.append(reason)
        failure_type = item.get("type") or item.get("reason")
        if isinstance(failure_type, str) and failure_type and failure_type not in types:
            types.append(failure_type)
        invalid_count = item.get("evidence_manifest_invalid_entry_count")
        if isinstance(invalid_count, (int, float)):
            evidence_manifest_invalid_entry_count += int(invalid_count)
    summary: dict[str, object] = {}
    if reasons:
        summary["replay_failure_reasons"] = reasons
    if types:
        summary["replay_failure_types"] = types
    if evidence_manifest_invalid_entry_count:
        summary["replay_evidence_manifest_invalid_entry_count"] = (
            evidence_manifest_invalid_entry_count
        )
    return summary


def _can_reuse_single_case_replay_validation(dataset: SelfEvolveDataset) -> bool:
    return (
        bool(dataset.recipe.source.get("paired_replay"))
        and dataset.recipe.source.get("original_case_count") == 1
        and not dataset.recipe.held_out_case_ids
    )


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _replay_gate_details(
    replay_result: CandidateReplayResult,
    *,
    dataset: SelfEvolveDataset,
) -> dict[str, object]:
    details: dict[str, object] = {
        "baseline_status": replay_result.baseline.status,
        "candidate_status": replay_result.candidate.status,
        "baseline_failure": replay_result.baseline.failure,
        "candidate_failure": replay_result.candidate.failure,
        **candidate_replay_pair_coverage(
            dataset=dataset,
            replay_result=replay_result,
        ),
        "adaptation_fingerprint": replay_result.request.adaptation_fingerprint,
        "workspace_seed_fingerprint": (
            replay_result.request.workspace_seed_fingerprint
        ),
        "dataset_fingerprint": replay_result.request.dataset_fingerprint,
        "baseline_skill_fingerprint": (
            replay_result.request.baseline_skill_fingerprint
        ),
    }
    if replay_result.member_results:
        details["member_count"] = len(replay_result.member_results)
        details["failed_members"] = [
            {
                "case_id": member.case_id,
                "baseline_status": member.baseline.status,
                "candidate_status": member.candidate.status,
                "baseline_failure": member.baseline.failure,
                "candidate_failure": member.candidate.failure,
            }
            for member in replay_result.member_results
            if not member.succeeded
        ]
    if _candidate_replay_has_repairable_capability_failure(replay_result):
        details["failure_class"] = "candidate"
        details["repairable"] = True
        details["failure_stage"] = "replay_capability"
    return details


def _candidate_replay_has_repairable_capability_failure(
    replay_result: CandidateReplayResult,
) -> bool:
    failures: list[Mapping[str, Any] | None] = [
        replay_result.baseline.failure,
        replay_result.candidate.failure,
    ]
    for member in replay_result.member_results:
        failures.extend((member.baseline.failure, member.candidate.failure))
    return any(_repairable_capability_failure(failure) for failure in failures)


def _repairable_capability_failure(failure: Mapping[str, Any] | None) -> bool:
    if not isinstance(failure, Mapping):
        return False
    if failure.get("outcome") == "candidate_failure":
        return True
    if (
        failure.get("failure_class") == "candidate_replay_capability"
        and failure.get("repairable") is True
    ):
        return True
    for key in ("failures", "repetition_failures"):
        nested = failure.get(key)
        if isinstance(nested, list) and any(
            _repairable_capability_failure(item)
            for item in nested
            if isinstance(item, Mapping)
        ):
            return True
    return False


def _screening_attempt_requires_candidate_repair(
    attempt: Mapping[str, object],
) -> bool:
    details = attempt.get("details")
    return bool(
        isinstance(details, Mapping)
        and details.get("failure_class") == "candidate"
        and details.get("repairable") is True
    )


def _candidate_repair_conformance_contracts(
    optimizer_diagnostics: Mapping[str, object],
) -> dict[str, RepairConformanceContract]:
    raw_strategies = optimizer_diagnostics.get("candidate_strategies")
    if not isinstance(raw_strategies, (list, tuple)):
        return {}
    contracts: dict[str, RepairConformanceContract] = {}
    for strategy in raw_strategies:
        if not isinstance(strategy, Mapping):
            continue
        candidate_id = strategy.get("candidate_id")
        raw_contract = strategy.get("repair_conformance")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or not isinstance(raw_contract, Mapping)
        ):
            continue
        contract = RepairConformanceContract.from_dict(raw_contract)
        if contract.focus_candidate_id and contract.required_branch_paths:
            contracts[candidate_id] = contract
    return contracts


def _repair_conformance_gate(
    result: RepairConformanceResult,
    *,
    contract: RepairConformanceContract | None = None,
) -> GateResult:
    details = {
        "failure_class": "candidate",
        "repairable": not result.passed,
        "stage": "repair_conformance",
        "code": result.code,
        **dict(result.details),
    }
    if contract is not None:
        details["repair_conformance"] = contract.to_dict()
    return GateResult(
        gate_name="candidate_repair_conformance",
        passed=result.passed,
        reason=result.reason,
        details=details,
    )


def _repair_conformance_required_nonempty_operations(
    contract: RepairConformanceContract,
) -> tuple[str, ...]:
    """Select operations whose exact probes must prove result-plane content.

    A task-plane contract always needs this validation. An exact repair probe
    also needs it when its diagnostic captured an observed request operation;
    otherwise a candidate can satisfy substring matching by echoing a mapping
    key or unrelated envelope metadata instead of returning recorded content.
    """

    if not contract.late_observed_operations:
        return ()
    if contract.requires_fixture_derived_probe or contract.exact_probe is not None:
        return (
            contract.required_fixture_probe_operations
            or contract.late_observed_operations[-1:]
        )
    return ()


def _repair_conformance_screening_attempt(
    candidate: CandidateVariant,
    result: RepairConformanceResult,
    *,
    contract: RepairConformanceContract,
) -> dict[str, object]:
    gate = _repair_conformance_gate(result, contract=contract)
    return {
        "candidate_id": candidate.candidate_id,
        "screening_candidate_id": None,
        "passed": False,
        "reason": gate.reason,
        "details": gate.details,
    }


def _repair_conformance_failure_diagnostics(
    capability: Any,
    *,
    artifact_dir: Path,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    fixture_summaries = replay_capability_fixture_summaries(capability)
    if fixture_summaries:
        diagnostics["replay_fixture_summaries"] = fixture_summaries

    trace_excerpts: list[dict[str, str]] = []
    if artifact_dir.is_dir():
        inspected = 0
        for path in artifact_dir.rglob("*"):
            inspected += 1
            if inspected > 128 or len(trace_excerpts) >= 8:
                break
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name.lower()
            if "protocol_trace" not in name and name not in {
                "stderr.txt",
                "stdout.txt",
            }:
                continue
            try:
                with path.open("rb") as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - 4_096))
                    tail = handle.read(4_096).decode(
                        "utf-8", errors="replace"
                    )
            except OSError:
                continue
            bounded_tail = sanitize_text(tail, max_chars=4_000).strip()
            if not bounded_tail:
                continue
            trace_excerpts.append(
                {
                    "path": sanitize_path_ref(
                        path.relative_to(artifact_dir).as_posix()
                    ),
                    "tail": bounded_tail,
                }
            )
    if trace_excerpts:
        diagnostics["replay_service_protocol_traces"] = trace_excerpts
    return diagnostics


def _candidate_screening_repair_feedback(
    candidates: Iterable[CandidateVariant],
    report: Mapping[str, object] | None,
) -> tuple[EvaluationSummary, ...]:
    if not isinstance(report, Mapping):
        return ()
    attempts = report.get("attempts")
    if not isinstance(attempts, list):
        return ()
    candidates_by_id = {
        candidate.candidate_id: candidate for candidate in candidates
    }
    feedback: list[EvaluationSummary] = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        if not _screening_attempt_requires_candidate_repair(attempt):
            continue
        candidate_id = attempt.get("candidate_id")
        candidate = candidates_by_id.get(str(candidate_id))
        if candidate is None:
            continue
        details = attempt.get("details")
        gate = GateResult(
            gate_name="candidate_replay",
            passed=False,
            reason=str(
                attempt.get("reason")
                or "screening replay requires candidate capability repair"
            ),
            details=(dict(details) if isinstance(details, Mapping) else None),
        )
        feedback.extend(
            _iteration_validation_feedback(
                candidate=candidate,
                baseline_summary=None,
                candidate_summary=None,
                held_out_summary=None,
                failed_gates=[gate],
            )
        )
    return tuple(feedback)


def _replay_adaptation_details(
    bundle: ReplayAdaptationBundle,
    *,
    readiness: str,
    artifact_root: Path,
) -> dict[str, object]:
    details: dict[str, object] = {
        "schema_version": bundle.schema_version,
        "readiness": readiness,
        "ready": bundle.ready,
        "adaptation_fingerprint": bundle.adaptation_fingerprint,
        "workspace_seed_fingerprint": bundle.workspace_seed_fingerprint,
        "environment_fingerprint": bundle.environment_fingerprint,
        "bundle_path": str(artifact_root / "bundle.json"),
        "manifest_path": bundle.manifest_path,
        "environment_snapshot_path": bundle.environment_snapshot_path,
        "cases": [
            {
                "case_id": case.case_id,
                "readiness": case.readiness,
                "task_input_fingerprint": case.task_input_fingerprint,
                "dependencies": [
                    {
                        "kind": dependency.kind,
                        "identifier": dependency.identifier,
                        "status": dependency.status,
                        "deterministic": dependency.deterministic,
                        "adapter_id": dependency.adapter_id,
                        "detail": dependency.detail,
                    }
                    for dependency in case.dependencies
                ],
                "tool_names": list(case.tool_names),
                "diagnostics": list(case.diagnostics),
            }
            for case in bundle.cases
        ],
    }
    if bundle.replay_capability is not None:
        capability = bundle.replay_capability
        details["replay_capability"] = {
            "source": "candidate",
            "capability_id": capability.capability_id,
            "capability_package_fingerprint": (
                capability.capability_package_fingerprint
            ),
            "frozen_capability_fingerprint": capability.fingerprint,
            "ready": capability.ready,
            "handled_requirements": list(capability.handled_requirements),
            "unhandled_requirements": list(capability.unhandled_requirements),
        }
    return details


def _baseline_preflight_blocks_population(
    replay_result: CandidateReplayResult,
) -> bool:
    candidates = (
        [member.candidate for member in replay_result.member_results]
        if replay_result.member_results
        else [replay_result.candidate]
    )
    return any(
        isinstance(candidate.failure, Mapping)
        and candidate.failure.get("reason") == "baseline_preflight_failed"
        for candidate in candidates
    )


def _replay_confidence_gate(
    replay_result: CandidateReplayResult | None,
    *,
    dataset: SelfEvolveDataset,
    apply_policy: str,
) -> GateResult | None:
    if replay_result is None or apply_policy != "auto_verified":
        return None
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
    )
    baseline_source = replay_result.baseline.metrics.get("replay_source")
    candidate_repetitions = replay_result.candidate.metrics.get("repetition_count")
    candidate_successful_repetitions = replay_result.candidate.metrics.get(
        "successful_repetition_count"
    )
    candidate_failed_repetitions = replay_result.candidate.metrics.get(
        "failed_repetition_count"
    )
    base_details: dict[str, object] = {
        **coverage,
        "baseline_replay_source": baseline_source,
        "candidate_repetition_count": candidate_repetitions,
        "candidate_successful_repetition_count": candidate_successful_repetitions,
        "candidate_failed_repetition_count": candidate_failed_repetitions,
    }
    if coverage["incomparable_pair_count"] > 0:
        return GateResult(
            gate_name="replay_confidence",
            passed=False,
            reason="replay comparison contains incomparable member outcomes",
            details=base_details,
        )
    if (
        baseline_source == "historical"
        and isinstance(candidate_repetitions, (int, float))
        and int(candidate_repetitions) <= 1
    ):
        return GateResult(
            gate_name="replay_confidence",
            passed=False,
            reason="fixed historical baseline plus one candidate rerun is limited confidence",
            details={
                **base_details,
                "candidate_repetition_count": int(candidate_repetitions),
            },
        )
    if (
        isinstance(candidate_repetitions, (int, float))
        and int(candidate_repetitions) >= 3
        and isinstance(candidate_successful_repetitions, (int, float))
        and int(candidate_successful_repetitions) < 3
    ):
        return GateResult(
            gate_name="replay_confidence",
            passed=False,
            reason="candidate replay successful repetitions are insufficient",
            details={
                **base_details,
                "candidate_repetition_count": int(candidate_repetitions),
                "candidate_successful_repetition_count": int(candidate_successful_repetitions),
                "candidate_failed_repetition_count": (
                    int(candidate_failed_repetitions)
                    if isinstance(candidate_failed_repetitions, (int, float))
                    else None
                ),
            },
        )
    return GateResult(
        gate_name="replay_confidence",
        passed=True,
        reason="replay comparison has sufficient confidence for policy",
        details=base_details,
    )


def _replay_stability_gate(
    *,
    baseline_summary: EvaluationSummary,
    candidate_summary: EvaluationSummary,
    min_score_delta: float,
    replay_stability_margin: float,
    replay_used: bool,
) -> GateResult | None:
    if not replay_used or replay_stability_margin <= 0:
        return None
    baseline_score = _metric_number(baseline_summary.metrics, "score")
    candidate_score = _metric_number(candidate_summary.metrics, "score")
    if baseline_score is None or candidate_score is None:
        return GateResult(
            gate_name="replay_stability_margin",
            passed=False,
            reason="score metric missing for replay stability margin",
        )
    delta = candidate_score - baseline_score
    required_delta = min_score_delta + replay_stability_margin
    return GateResult(
        gate_name="replay_stability_margin",
        passed=delta >= required_delta,
        reason=(
            "replay score delta clears stability margin"
            if delta >= required_delta
            else "replay score delta is below stability margin"
        ),
        details={
            "baseline": baseline_score,
            "candidate": candidate_score,
            "delta": round(delta, 10),
            "required_delta": round(required_delta, 10),
            "replay_stability_margin": replay_stability_margin,
        },
    )


def _metric_number(metrics: Mapping[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _iteration_validation_feedback(
    *,
    candidate: CandidateVariant,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
    held_out_summary: EvaluationSummary | None,
    failed_gates: list[GateResult],
) -> tuple[EvaluationSummary, ...]:
    feedback: list[EvaluationSummary] = []
    typed_gate_metrics = _typed_gate_feedback_metrics(failed_gates)
    repair_candidate_package = _repair_candidate_package_feedback(
        candidate,
        failed_gates=failed_gates,
    )
    if repair_candidate_package is not None:
        typed_gate_metrics["repair_candidate_package"] = repair_candidate_package
        # This helper is called only from the full candidate evaluation path.
        # Mark its repair frontier explicitly so bounded representative screening
        # or historical task-rollout feedback cannot outrank a later failure
        # discovered across the authoritative dataset.
        typed_gate_metrics["authoritative_replay_failure"] = True
    comparison_metrics = _baseline_comparison_feedback_metrics(
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
    )
    if candidate_summary is not None:
        feedback.append(
            EvaluationSummary(
                variant_id=candidate_summary.variant_id,
                metrics={
                    **dict(candidate_summary.metrics),
                    **comparison_metrics,
                    **typed_gate_metrics,
                    "failed_gates": [gate.gate_name for gate in failed_gates],
                },
                dataset_split=candidate_summary.dataset_split,
            )
        )
    if held_out_summary is not None:
        feedback.append(
            EvaluationSummary(
                variant_id=held_out_summary.variant_id,
                metrics={
                    **dict(held_out_summary.metrics),
                    **typed_gate_metrics,
                    "failed_gates": [gate.gate_name for gate in failed_gates],
                },
                dataset_split=held_out_summary.dataset_split,
            )
        )
    if feedback:
        return tuple(feedback)
    return (
        EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={
                **comparison_metrics,
                **typed_gate_metrics,
                "failed_gates": [gate.gate_name for gate in failed_gates],
                "candidate_status": "rejected" if failed_gates else "accepted",
            },
            dataset_split="validation",
        ),
    )


_MAX_CURRENT_RUN_VALIDATION_FEEDBACK = 16


def _merge_validation_feedback(
    existing: Iterable[EvaluationSummary],
    new: Iterable[EvaluationSummary],
) -> tuple[EvaluationSummary, ...]:
    merged: list[EvaluationSummary] = []
    seen: set[str] = set()
    for item in (*tuple(existing), *tuple(new)):
        fingerprint = hashlib.sha256(
            json.dumps(
                to_json_dict(item),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged.append(item)
    best_family_index: dict[str, int] = {}
    best_family_progress: dict[str, int] = {}
    for index, item in enumerate(merged):
        family = _validation_feedback_failure_family(item)
        if family is not None:
            progress = _feedback_interaction_progress(item)
            if progress >= best_family_progress.get(family, -1):
                best_family_progress[family] = progress
                best_family_index[family] = index
    compacted = [
        item
        for index, item in enumerate(merged)
        if (
            (family := _validation_feedback_failure_family(item)) is None
            or best_family_index.get(family) == index
        )
    ]
    return tuple(compacted[-_MAX_CURRENT_RUN_VALIDATION_FEEDBACK:])


def _feedback_interaction_progress(feedback: EvaluationSummary) -> int:
    value = feedback.metrics.get("interaction_progress")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _next_progress_repair_extension_family(
    feedback_items: Iterable[EvaluationSummary],
    *,
    consumed_families: set[str],
) -> str | None:
    for feedback in reversed(tuple(feedback_items)):
        metrics = feedback.metrics
        if metrics.get("failure_class") != "candidate":
            continue
        if metrics.get("repairable") is not True:
            continue
        if not isinstance(metrics.get("repair_candidate_package"), Mapping):
            continue
        family = _validation_feedback_failure_family(feedback)
        if family is not None and family not in consumed_families:
            return family
    return None


def _validation_feedback_failure_family(
    feedback: EvaluationSummary,
) -> str | None:
    metrics = feedback.metrics
    if not isinstance(metrics.get("repair_candidate_package"), Mapping):
        return None
    signature = {
        "failed_gates": sorted(
            str(item) for item in metrics.get("failed_gates", [])
        ),
        "failure_class": metrics.get("failure_class"),
        "diagnostics": _failure_signature_values(
            metrics.get("candidate_validation_diagnostics")
        ),
    }
    return hashlib.sha256(
        json.dumps(
            signature,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _failure_signature_values(value: Any) -> list[tuple[str, str]]:
    selected_keys = {
        "code",
        "failure_class",
        "reason",
        "repairable",
        "stage",
        "type",
    }
    values: list[tuple[str, str]] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                key_text = str(key)
                if key_text in selected_keys and not isinstance(
                    nested, (Mapping, list, tuple)
                ):
                    values.append((key_text, str(nested)))
                else:
                    visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return sorted(values)


def _diagnostic_classification_text(value: Any) -> str:
    """Extract bounded diagnostic tails for type classification only."""

    selected_keys = {
        "detail",
        "error",
        "message",
        "reason",
        "stderr_tail",
        "stdout_tail",
        "tail",
    }
    values: list[str] = []

    def visit(item: Any) -> None:
        if len(values) >= 32:
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if len(values) >= 32:
                    break
                if str(key) in selected_keys and not isinstance(
                    nested, (Mapping, list, tuple)
                ):
                    values.append(str(nested)[-2_000:])
                else:
                    visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:32]:
                visit(nested)

    visit(value)
    return "\n".join(values)


def _candidate_repair_diagnostic_view(
    details: Mapping[str, object],
) -> Mapping[str, object]:
    """Select candidate-side evidence from a paired replay gate.

    Baseline failures remain in the persisted gate details for comparison, but
    they must not redirect repair attribution away from the candidate variant.
    """

    candidate_failures: list[Mapping[str, object]] = []
    direct = details.get("candidate_failure")
    if isinstance(direct, Mapping):
        candidate_failures.append(direct)
    failed_members = details.get("failed_members")
    if isinstance(failed_members, list):
        for member in failed_members[:64]:
            if not isinstance(member, Mapping):
                continue
            failure = member.get("candidate_failure")
            if isinstance(failure, Mapping):
                candidate_failures.append(failure)
    if not candidate_failures:
        return details
    return {
        "failure_class": details.get("failure_class"),
        "repairable": details.get("repairable"),
        "candidate_failures": candidate_failures,
    }


def _typed_gate_feedback_metrics(
    failed_gates: Iterable[GateResult],
) -> dict[str, object]:
    failed_gate_items = tuple(failed_gates)
    diagnostics: list[Mapping[str, object]] = []
    classification_fragments: list[str] = []
    classification_views: list[Mapping[str, object]] = []
    failure_classes: set[str] = set()
    repairable_values: list[bool] = []
    for gate in failed_gate_items:
        details = gate.details
        gate_diagnostic: dict[str, object] = {
            "code": "failed_gate",
            "stage": sanitize_text(gate.gate_name, max_chars=120),
            "reason": sanitize_text(gate.reason, max_chars=400),
        }
        if not isinstance(details, Mapping):
            diagnostics.append(gate_diagnostic)
            continue
        classification_view = _candidate_repair_diagnostic_view(details)
        classification_views.append(classification_view)
        classification_fragments.append(
            _diagnostic_classification_text(classification_view)
        )
        bounded_details = sanitize_metric_value(details, max_chars=400)
        if isinstance(bounded_details, Mapping):
            gate_diagnostic["details"] = dict(bounded_details)
        diagnostics.append(gate_diagnostic)
        failure_class = details.get("failure_class")
        if isinstance(failure_class, str) and failure_class:
            failure_classes.add(failure_class)
        repairable = details.get("repairable")
        if isinstance(repairable, bool):
            repairable_values.append(repairable)
        raw_diagnostics = details.get("diagnostics")
        if isinstance(raw_diagnostics, list):
            diagnostics.extend(
                dict(item)
                for item in raw_diagnostics[:16]
                if isinstance(item, Mapping)
            )
    result: dict[str, object] = {}
    if len(failure_classes) == 1:
        result["failure_class"] = next(iter(failure_classes))
    if repairable_values:
        result["repairable"] = all(repairable_values)
    interaction_progress = _diagnostic_interaction_progress(
        tuple(classification_views)
    )
    routing_continuity_gaps = _diagnostic_routing_continuity_gaps(
        tuple(classification_views)
    )
    fixture_root_types = _diagnostic_fixture_root_types(
        tuple(classification_views)
    )
    observed_request_operations = _diagnostic_observed_request_operations(
        tuple(classification_views)
    )
    protocol_probe_mismatch = _diagnostic_protocol_probe_mismatch(
        tuple(classification_views)
    )
    completed_data_plane_operations = (
        _diagnostic_completed_data_plane_operations(
            tuple(classification_views)
        )
    )
    if interaction_progress:
        result["interaction_progress"] = interaction_progress
    diagnostic_text = json.dumps(
        classification_views,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower() + "\n" + "\n".join(classification_fragments).lower()
    # A timeout with observed protocol operations is already evidence that the
    # task reached the stateful/data plane, even when the trace did not expose
    # a numeric progress counter.  Preserve a minimum task-plane marker so the
    # next repair contract requires operation-aware fixture reconstruction and
    # the framework response-index binding instead of another readiness-only
    # candidate.
    if (
        "replay timed out" in diagnostic_text
        and observed_request_operations
        and interaction_progress < 4
    ):
        interaction_progress = 4
        result["interaction_progress"] = interaction_progress
    if (
        "permissionerror" in diagnostic_text
        or "permission denied" in diagnostic_text
    ):
        diagnostics.insert(
            0,
            {
                "code": "repair_candidate_output_permission_collision",
                "stage": "capability_compile",
                "failure_class": "candidate",
                "repairable": True,
                "reason": (
                    "The candidate compiler attempted to overwrite a generated output "
                    "whose mode was inherited from a read-only evidence source. Preserve "
                    "the recorded source bytes, but use a unique output path for each "
                    "handled requirement (including requirements that share an evidence "
                    "reference), or write without preserving source permissions. Ensure "
                    "every declared fixture path matches the file actually written."
                ),
            },
        )
    elif (
        "protocol_trace.jsonl" in diagnostic_text
        and any(
            marker in diagnostic_text
            for marker in (
                "missing required summary fields",
                "fields must be a list",
                "correlation must be an object",
                "direction must describe",
                "must record both received and emitted",
                "must contain one json object per line",
                "records must be json objects",
                "wrote an empty",
                "did not write",
            )
        )
    ):
        diagnostics.insert(
            0,
            {
                "code": "repair_protocol_trace_contract",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "reason": (
                    "Repair the candidate runtime's bounded protocol_trace.jsonl "
                    "writer. Every interaction record must be one JSON object with "
                    "direction, sequence, kind, fields, and correlation. fields must "
                    "be a list, correlation must be an object, and direction must "
                    "describe only a received/inbound or emitted/outbound interaction. "
                    "Record both sides of readiness and data-plane exchanges; do not "
                    "write lifecycle-only directions such as system. Keep payload "
                    "bodies and credentials out of the trace."
                ),
            },
        )
    elif "classification=recorded_response_selector_drift" in diagnostic_text:
        diagnostics.insert(
            0,
            {
                "code": "align_compiler_runtime_recorded_response_selection",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                **protocol_probe_mismatch,
                "reason": (
                    "The runtime response already contains immutable recorded-response "
                    "evidence, but the compiler-declared response_contains assertion "
                    "comes from a different fixture selection path. Change both the "
                    "compiler probe builder and the runtime selector. They must share "
                    "one deterministic gateway, payload, decoding, ordering, and "
                    "fallback algorithm so the declared scalar is a descendant of the "
                    "exact recorded container returned by the runtime. Do not replace "
                    "the runtime's response-index projection with the mismatched "
                    "diagnostic preview, and do not hard-code either preview."
                ),
            },
        )
    elif "protocol probe response mismatch" in diagnostic_text:
        expected_preview = str(
            protocol_probe_mismatch.get("expected_preview") or "unknown"
        )
        diagnostics.insert(
            0,
            {
                "code": "verify_declared_protocol_probe_branch",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                **protocol_probe_mismatch,
                "observed_request_operations": list(
                    observed_request_operations
                ),
                "reason": (
                    "Execute the declared request_text against the exact handler branch "
                    "in the returned candidate source and verify semantic containment: "
                    "its decoded response must contain response_contains while preserving "
                    "the protocol envelope. Compare the bounded expected_preview with the "
                    "response_preview; response length does not need to equal expected length. "
                    f"For this failure the exact bounded expected_preview is "
                    f"{expected_preview!r}; use it only as bounded diagnostic evidence, "
                    "not as a value to hard-code. A differing fixture-derived value in the "
                    "runtime response indicates that compiler and runtime selected different "
                    "leaves. Use one canonical deterministic selector in compiler and runtime "
                    "with identical JSON/JSONL parsing, recursive traversal, filtering, ordering, "
                    "deduplication, and fallback semantics. Prefer sharing the selector source "
                    "or passing the compiled selected value through generated configuration so "
                    "the two sides cannot drift. A single selected leaf may be reused by multiple "
                    "probes; do not invent mapping-key or raw-token fallbacks merely to make each "
                    "probe token unique. Place the derived value in the exact declared "
                    "response branch and self-check the serialized bytes before returning. "
                    "Every declared probe is executed. Do not copy one assertion onto every "
                    "observed operation: remove redundant probes for branches not required by "
                    "exact_probe or the final late_observed_operation, while still implementing "
                    "those operations for the real task. "
                    "A rationale claim is not a repair unless that returned candidate "
                    "source branch actually changes."
                ),
            },
        )
    elif (
        "websocket frame is incomplete" in diagnostic_text
        or "connection closed before" in diagnostic_text
    ):
        observed_roots = (
            ", ".join(fixture_root_types) if fixture_root_types else "unknown"
        )
        diagnostics.insert(
            0,
            {
                "code": "diagnose_protocol_handler_abort",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "observed_fixture_root_types": list(fixture_root_types),
                "reason": (
                    "The protocol trace records an inbound request but no complete "
                    "outbound frame, so the candidate handler closed or raised before "
                    "serializing its response. Re-run the exact declared probe against "
                    "the returned source and surface a bounded sanitized exception in "
                    "stderr or protocol_trace.jsonl; do not swallow handler exceptions. "
                    f"Observed frozen fixture root types: {observed_roots}. "
                    "Treat fixture payloads as arbitrary JSON root types (object, array, "
                    "scalar, or invalid JSON) and normalize the root before mapping-only "
                    "operations such as .get(). Preserve the working handshake and frame "
                    "helpers while repairing the request handler branch."
                ),
            },
        )
    elif routing_continuity_gaps:
        diagnostics.insert(
            0,
            {
                "code": "preserve_protocol_routing_continuity",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "routing_fields": list(routing_continuity_gaps),
                "reason": (
                    "The protocol trace shows that opaque routing fields present on "
                    "an inbound request were dropped from its outbound interaction "
                    f"envelope: {', '.join(routing_continuity_gaps)}. Preserve each "
                    "field byte-for-byte on every response and follow-up event emitted "
                    "for that request. Keep payload bodies redacted in protocol_trace.jsonl, "
                    "but include these routing field names and opaque values in the "
                    "correlation summary so continuity is directly verifiable."
                ),
            },
        )
    elif (
        "replay timed out" in diagnostic_text
        and completed_data_plane_operations
        and "candidate_task_behavior" in diagnostic_text
    ):
        result["required_behaviors"] = [
            "persist_first_successful_structured_evidence",
            "write_manifest_before_additional_collection",
            "verify_task_semantic_sufficiency_before_finalizing",
            "do_not_treat_transport_success_as_task_completion",
            "continue_bounded_acquisition_when_payload_is_only_metadata_or_execution_summary",
            "stop_after_sufficient_evidence",
            "return_bounded_evidence_ledger",
        ]
        diagnostics.insert(
            0,
            {
                "code": "finalize_after_successful_endpoint_interaction",
                "stage": "candidate_task_behavior",
                "failure_class": "candidate",
                "repairable": True,
                "completed_data_plane_operations": list(
                    completed_data_plane_operations
                ),
                "reason": (
                    "The supplied replay service completed a bidirectional "
                    "non-control interaction, but the candidate continued until "
                    "the outer task timeout instead of returning a bounded result. "
                    "Preserve the verified replay runtime. Repair the reusable target "
                    "instructions so the first successful structured extraction is "
                    "persisted immediately, a valid evidence manifest is written before "
                    "additional collection, and the saved payload is checked for direct "
                    "semantic support of the requested claims. A handshake, HTTP success, "
                    "structured envelope, metadata record, or execution summary is a delivery "
                    "signal rather than task completion. If the payload is insufficient, use "
                    "one materially different bounded artifact-backed source or report that "
                    "insufficiency; stop only once sufficient evidence exists. Return only "
                    "the requested bounded result and evidence ledger. "
                    "Do not hard-code an operation, endpoint, task, or fixture value."
                ),
            },
        )
    elif (
        "discovery methods failed" in diagnostic_text
        or "failed to deserialize" in diagnostic_text
        or "missing field" in diagnostic_text
        or "doesn't implement the expected protocol" in diagnostic_text
        or "does not implement the expected protocol" in diagnostic_text
        or "websocket protocol error" in diagnostic_text
        or (
            "not a " in diagnostic_text
            and " endpoint" in diagnostic_text
        )
        or (
            "replay timed out" in diagnostic_text
            and bool(observed_request_operations)
        )
    ):
        diagnostics.insert(
            0,
            {
                "code": "implement_observed_endpoint_interactions",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "observed_request_operations": list(observed_request_operations),
                "reason": (
                    "The candidate's declared probes passed but the real task still "
                    "rejected the supplied endpoint protocol. Use bounded task diagnostics "
                    "and trace interaction summaries to implement the actual observed "
                    "task-plane stateful interactions and add representative probes for "
                    "them. The late observed request operations are: "
                    + (", ".join(observed_request_operations) or "unknown")
                    + ". Recursively traverse arbitrary fixture objects and arrays, select "
                    "the recorded evidence needed by those operations, and make at least one "
                    "representative probe assert non-empty fixture-derived response content. "
                    "Do not return placeholder tokens or empty schemas, and do not preserve "
                    "a readiness-only runtime merely because its self-declared probes pass."
                ),
            },
        )
    elif (
        "hung during navigation" in diagnostic_text
        or "still navigating" in diagnostic_text
        or "waiting for the page to load" in diagnostic_text
        or "正在导航" in diagnostic_text
        or "仍在导航" in diagnostic_text
        or "等待页面加载" in diagnostic_text
    ):
        diagnostics.insert(
            0,
            {
                "code": "implement_async_endpoint_completion",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "reason": (
                    "The candidate handled synchronous endpoint requests but the real "
                    "task remained blocked waiting for completion. Preserve the working "
                    "request/response branches, then implement the observed stateful "
                    "interactions, including asynchronous completion or lifecycle "
                    "notifications required after a synchronous response. Preserve opaque "
                    "request correlation and routing metadata on both the response and its "
                    "follow-up events when the protocol multiplexes sessions or channels; "
                    "echoing only the numeric request id can leave the client waiting. Add a bounded "
                    "probe that verifies the completion notification rather than only "
                    "readiness or the initial response."
                ),
            },
        )
    if diagnostics:
        result["candidate_validation_diagnostics"] = diagnostics[:16]
    return result


def _diagnostic_completed_data_plane_operations(value: Any) -> tuple[str, ...]:
    operations: list[str] = []

    def visit(item: Any, *, depth: int = 0) -> None:
        if depth > 8 or len(operations) >= 32:
            return
        if isinstance(item, Mapping):
            raw = item.get("completed_data_plane_operations")
            if isinstance(raw, (list, tuple)):
                for operation in raw[:32]:
                    text = str(operation or "").strip()
                    if text and text not in operations:
                        operations.append(text)
            for nested in item.values():
                if isinstance(nested, (Mapping, list, tuple)):
                    visit(nested, depth=depth + 1)
        elif isinstance(item, (list, tuple)):
            for nested in item[:128]:
                visit(nested, depth=depth + 1)

    visit(value)
    return tuple(operations)


def _diagnostic_protocol_probe_mismatch(value: Any) -> dict[str, str]:
    """Parse bounded expected/actual probe evidence from candidate diagnostics."""

    messages: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in list(item.items())[:128]:
                if (
                    str(key) in {"detail", "error", "message", "reason"}
                    and isinstance(nested, str)
                    and "protocol probe response mismatch" in nested.lower()
                ):
                    messages.append(nested)
                else:
                    collect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect(nested)

    collect(value)
    for message in reversed(messages[-16:]):
        kind_match = re.search(r"\bkind=([^\s]+)", message)
        path_match = re.search(r"\bpath=([^\s]+)", message)
        expected_match = re.search(
            r"\bexpected_preview=(.*?)\s+response_bytes=\d+",
            message,
            flags=re.DOTALL,
        )
        response_match = re.search(
            r"\bresponse_preview=(.*)$",
            message,
            flags=re.DOTALL,
        )
        parsed: dict[str, str] = {}
        if kind_match:
            parsed["probe_kind"] = sanitize_text(
                kind_match.group(1), max_chars=40
            )
        if path_match:
            parsed["probe_path"] = sanitize_text(
                path_match.group(1), max_chars=160
            )
        if expected_match:
            parsed["expected_preview"] = sanitize_text(
                expected_match.group(1).strip(), max_chars=160
            )
        if response_match:
            parsed["response_preview"] = sanitize_text(
                response_match.group(1).strip(), max_chars=400
            )
        if parsed:
            return parsed
    return {}


def _diagnostic_observed_request_operations(value: Any) -> tuple[str, ...]:
    """Extract bounded, payload-free operation names from protocol trace tails."""

    trace_tails: list[str] = []

    def collect_tails(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in list(item.items())[:128]:
                if str(key) == "tail" and isinstance(nested, str):
                    trace_tails.append(nested)
                else:
                    collect_tails(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect_tails(nested)

    collect_tails(value)
    observed: list[tuple[int, str]] = []
    operation_keys = {"action", "command", "method", "operation", "path", "route"}
    inbound_directions = {"in", "inbound", "receive", "received", "recv"}
    transport_methods = {
        "CONNECT",
        "DELETE",
        "GET",
        "HEAD",
        "OPTIONS",
        "PATCH",
        "POST",
        "PUT",
        "TRACE",
    }

    def record_operation(raw_operation: str, *, sequence_number: int) -> None:
        operation = sanitize_text(raw_operation, max_chars=120).strip()
        if not operation or operation.upper() in transport_methods:
            return
        observed.append((sequence_number, operation))

    def collect_operations(
        source: Any,
        *,
        sequence_number: int,
        depth: int = 0,
    ) -> None:
        if depth > 4:
            return
        if isinstance(source, Mapping):
            for key, nested in list(source.items())[:64]:
                normalized_key = str(key).strip().lower()
                if normalized_key in operation_keys and isinstance(nested, str):
                    record_operation(
                        nested,
                        sequence_number=sequence_number,
                    )
                    continue
                if isinstance(nested, (Mapping, list, tuple)):
                    collect_operations(
                        nested,
                        sequence_number=sequence_number,
                        depth=depth + 1,
                    )
            return
        if isinstance(source, (list, tuple)):
            for nested in source[:64]:
                collect_operations(
                    nested,
                    sequence_number=sequence_number,
                    depth=depth + 1,
                )
            return
        if not isinstance(source, str):
            return
        field_name, separator, field_value = source.partition(":")
        if not separator:
            field_name, separator, field_value = source.partition("=")
        if field_name.strip().lower() not in operation_keys:
            return
        record_operation(
            field_value,
            sequence_number=sequence_number,
        )

    for tail in trace_tails[:16]:
        for raw_line in tail.splitlines()[-256:]:
            try:
                record = json.loads(raw_line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(record, Mapping):
                continue
            direction = str(record.get("direction") or "").strip().lower()
            kind = str(record.get("kind") or "").strip().lower()
            if direction not in inbound_directions and "request" not in kind:
                continue
            sequence = record.get("sequence")
            sequence_number = (
                int(sequence)
                if isinstance(sequence, (int, float)) and not isinstance(sequence, bool)
                else 0
            )
            collect_operations(
                record,
                sequence_number=sequence_number,
            )

    ordered: list[str] = []
    for _, operation in sorted(observed, key=lambda item: item[0]):
        if operation in ordered:
            ordered.remove(operation)
        ordered.append(operation)
    return tuple(ordered[-8:])


def _diagnostic_fixture_root_types(value: Any) -> tuple[str, ...]:
    """Collect bounded non-content fixture shape metadata from failure details."""

    root_types: set[str] = set()

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in list(item.items())[:128]:
                if str(key) == "json_root_type" and isinstance(nested, str):
                    normalized = nested.strip().lower()
                    if normalized:
                        root_types.add(normalized)
                else:
                    collect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect(nested)

    collect(value)
    return tuple(sorted(root_types))


def _diagnostic_routing_continuity_gaps(value: Any) -> tuple[str, ...]:
    """Infer dropped opaque routing fields from candidate-owned trace summaries."""

    trace_tails: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if str(key) == "tail" and isinstance(nested, str):
                    trace_tails.append(nested)
                else:
                    collect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect(nested)

    collect(value)
    gaps: set[str] = set()
    for tail in trace_tails[:16]:
        pending_routing_fields: set[str] = set()
        for line in tail.splitlines()[-256:]:
            try:
                record = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(record, Mapping):
                continue
            direction = str(record.get("direction") or "").strip().lower()
            raw_fields = record.get("fields")
            fields = {
                str(field)
                for field in raw_fields
                if isinstance(field, str) and field
            } if isinstance(raw_fields, list) else set()
            correlation = record.get("correlation")
            correlation_fields = (
                {
                    str(key)
                    for key, nested in correlation.items()
                    if nested is not None and nested != ""
                }
                if isinstance(correlation, Mapping)
                else set()
            )
            if direction in {"in", "inbound", "received", "receive", "recv"}:
                pending_routing_fields = {
                    field
                    for field in fields | correlation_fields
                    if _looks_like_protocol_routing_field(field)
                }
                continue
            if direction not in {
                "out",
                "outbound",
                "emitted",
                "emit",
                "send",
                "sent",
            }:
                continue
            if pending_routing_fields:
                gaps.update(
                    pending_routing_fields.difference(fields | correlation_fields)
                )
    return tuple(sorted(gaps))


def _looks_like_protocol_routing_field(field_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", field_name.lower())
    if not normalized or normalized == "id":
        return False
    return any(
        marker in normalized
        for marker in (
            "session",
            "channel",
            "route",
            "routing",
            "stream",
            "correlation",
            "connection",
        )
    )


def _diagnostic_interaction_progress(value: Any) -> int:
    max_sequence = 0

    def visit(item: Any) -> None:
        nonlocal max_sequence
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if (
                    str(key) in {"sequence", "seq"}
                    and not isinstance(nested, bool)
                    and isinstance(nested, (int, float))
                ):
                    max_sequence = max(max_sequence, int(nested))
                elif str(key) == "tail" and isinstance(nested, str):
                    for line in nested.splitlines()[-256:]:
                        try:
                            parsed = json.loads(line)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        visit(parsed)
                else:
                    visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                visit(nested)

    visit(value)
    return max_sequence


_MAX_REPAIR_CANDIDATE_PACKAGE_CHARS = 64_000
_MAX_REPAIR_CANDIDATE_FILE_CHARS = 32_000
_MAX_HISTORICAL_REPAIR_CANDIDATES = 8


def _repair_candidate_package_feedback(
    candidate: CandidateVariant,
    *,
    failed_gates: Iterable[GateResult],
) -> dict[str, object] | None:
    if not candidate.files or not any(not gate.passed for gate in failed_gates):
        return None
    remaining_chars = _MAX_REPAIR_CANDIDATE_PACKAGE_CHARS
    files: list[dict[str, object]] = []
    for item in candidate.files[:8]:
        file_payload: dict[str, object] = {
            "path": sanitize_text(item.path, max_chars=240),
            "operation": sanitize_text(item.operation, max_chars=40),
            "executable": item.executable,
        }
        if item.content is not None and remaining_chars > 0:
            content_limit = min(
                remaining_chars,
                _MAX_REPAIR_CANDIDATE_FILE_CHARS,
            )
            content = sanitize_source_text(item.content, max_chars=content_limit)
            file_payload["content"] = content
            remaining_chars -= len(content)
        files.append(file_payload)
    return {
        "candidate_id": sanitize_text(candidate.candidate_id, max_chars=160),
        "rationale": sanitize_text(candidate.rationale, max_chars=1_000),
        "content": sanitize_source_text(candidate.content, max_chars=8_000),
        "files": files,
    }


def _iteration_report_item(
    *,
    iteration_number: int,
    candidate_number: int,
    candidate_count: int,
    candidate: CandidateVariant,
    status: str,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
    held_out_summary: EvaluationSummary | None,
    failed_gates: list[GateResult],
) -> dict[str, object]:
    return {
        "iteration": iteration_number,
        "candidate_number": candidate_number,
        "candidate_count": candidate_count,
        "candidate_id": candidate.candidate_id,
        "status": status,
        "baseline_metrics": (
            dict(baseline_summary.metrics)
            if baseline_summary is not None
            else None
        ),
        "candidate_metrics": (
            dict(candidate_summary.metrics)
            if candidate_summary is not None
            else None
        ),
        "held_out_metrics": (
            dict(held_out_summary.metrics)
            if held_out_summary is not None
            else None
        ),
        "failed_gates": [gate.gate_name for gate in failed_gates],
    }


def _iteration_state(
    *,
    candidate: CandidateVariant,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
    held_out_summary: EvaluationSummary | None,
    replay_result: CandidateReplayResult | None,
    replay_dataset: SelfEvolveDataset | None,
    gate_results: list[GateResult],
    feedback: tuple[EvaluationSummary, ...],
    status: str,
) -> dict[str, object]:
    return {
        "candidate": candidate,
        "baseline_summary": baseline_summary,
        "candidate_summary": candidate_summary,
        "held_out_summary": held_out_summary,
        "replay_result": replay_result,
        "replay_dataset": replay_dataset,
        "gate_results": gate_results,
        "feedback": feedback,
        "status": status,
    }


def _lesson_type_counts(lessons: tuple[LessonRecord, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for lesson in lessons:
        counts[lesson.lesson_type] = counts.get(lesson.lesson_type, 0) + 1
    return counts


def _harness_diagnostic_type_counts(diagnostics: tuple[object, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        kind = getattr(diagnostic, "kind", None)
        kind_value = getattr(kind, "value", kind)
        if isinstance(kind_value, str) and kind_value:
            counts[kind_value] = counts.get(kind_value, 0) + 1
    return counts


def _harness_diagnostic_promotion_counts(
    diagnostics: tuple[object, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        status = getattr(diagnostic, "promotion_status", None)
        status_value = getattr(status, "value", status)
        if isinstance(status_value, str) and status_value:
            counts[status_value] = counts.get(status_value, 0) + 1
    return counts


def _release_normalization_report(post_apply: Mapping[str, object]) -> dict[str, object] | None:
    metrics = post_apply.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    pre_fingerprint = metrics.get("pre_normalization_fingerprint")
    normalized_fingerprint = metrics.get("normalized_release_fingerprint")
    equivalence_passed = metrics.get("normalization_equivalence_passed")
    preserved_constraints = metrics.get("preserved_runtime_constraints")
    runtime_constraint_lesson_map = metrics.get("runtime_constraint_lesson_map")
    addressed_lesson_ids = metrics.get("addressed_lesson_ids")
    if (
        pre_fingerprint is None
        and normalized_fingerprint is None
        and equivalence_passed is None
    ):
        return None
    return {
        "pre_normalization_fingerprint": pre_fingerprint,
        "normalized_release_fingerprint": normalized_fingerprint,
        "normalization_verification_passed": equivalence_passed,
        "preserved_runtime_constraints": (
            list(preserved_constraints)
            if isinstance(preserved_constraints, list)
            else []
        ),
        "runtime_constraint_lesson_map": (
            list(runtime_constraint_lesson_map)
            if isinstance(runtime_constraint_lesson_map, list)
            else []
        ),
        "addressed_lesson_ids": (
            list(addressed_lesson_ids)
            if isinstance(addressed_lesson_ids, list)
            else []
        ),
        "removed_internal_line_count": metrics.get("removed_internal_line_count"),
        "status": post_apply.get("status"),
    }


def _trajectory_set_report(dataset: SelfEvolveDataset) -> dict[str, object] | None:
    source = dict(dataset.recipe.source)
    has_trajectory_set_source = source.get("kind") == "trajectory_set"
    auto_grouping = source.get("auto_grouping")
    prior_case_ids = [
        case.case_id
        for case in dataset.cases
        if case.source.get("kind") == "prior_self_evolve_run"
    ]
    member_roles: dict[str, int] = {}
    set_ids: set[str] = set()
    for case in dataset.cases:
        metadata = case.metadata.get("trajectory_set")
        if not isinstance(metadata, Mapping):
            continue
        set_id = metadata.get("set_id")
        if isinstance(set_id, str) and set_id:
            set_ids.add(set_id)
        member = metadata.get("member")
        if isinstance(member, Mapping):
            role = member.get("role")
            if isinstance(role, str) and role:
                member_roles[role] = member_roles.get(role, 0) + 1
    if not has_trajectory_set_source and not prior_case_ids and not set_ids and not auto_grouping:
        return None
    report: dict[str, object] = {
        "source_kind": source.get("kind"),
        "set_ids": sorted(set_ids),
        "case_count": len(dataset.cases),
        "member_roles": member_roles,
        "include_prior_runs": bool(source.get("include_prior_runs")),
        "prior_run_case_count": len(prior_case_ids),
        "prior_run_case_ids": prior_case_ids,
    }
    if isinstance(auto_grouping, Mapping):
        report["auto_grouping"] = dict(auto_grouping)
    return report


def _population_report(
    *,
    all_candidates: list[CandidateVariant],
    iteration_reports: list[dict[str, object]],
    replay_candidate_limit: int,
    optimizer_diagnostics: list[dict[str, object]] | None = None,
    screening_reports: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    if not all_candidates and not iteration_reports:
        return None
    replayed_candidate_ids = [
        str(item.get("candidate_id"))
        for item in iteration_reports
        if isinstance(item.get("candidate_id"), str)
    ]
    report: dict[str, object] = {
        "generated_candidate_count": len(all_candidates),
        "generated_candidate_ids": [candidate.candidate_id for candidate in all_candidates],
        "replayed_candidate_count": len(replayed_candidate_ids),
        "replayed_candidate_ids": replayed_candidate_ids,
        "replay_candidate_limit": replay_candidate_limit,
        "non_replayed_candidate_count": max(
            0,
            len(all_candidates) - len(set(replayed_candidate_ids)),
        ),
    }
    strategy_records = _candidate_strategy_records(optimizer_diagnostics or ())
    if strategy_records:
        replayed_set = set(replayed_candidate_ids)
        non_replayed = [
            {
                **record,
                "not_replayed_reason": "not_replayed_due_to_budget",
            }
            for record in strategy_records
            if str(record.get("candidate_id")) not in replayed_set
        ]
        if non_replayed:
            report["non_replayed_candidate_strategies"] = non_replayed
    if screening_reports:
        report["screening"] = screening_reports[-1]
        if len(screening_reports) > 1:
            report["screening_iterations"] = screening_reports
    return report


def _candidate_screening_dataset(
    dataset: SelfEvolveDataset,
    *,
    capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
) -> SelfEvolveDataset | None:
    replayable_cases = tuple(
        case for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    if len(replayable_cases) <= 1:
        return None

    replayable_by_id = {case.case_id: case for case in replayable_cases}
    held_out_case_ids = set(dataset.recipe.held_out_case_ids)
    held_out_case_ids.update(dataset.recipe.splits.get("held_out", ()))
    preferred_case_ids = (
        *dataset.recipe.trainable_case_ids,
        *dataset.recipe.splits.get("train", ()),
        *dataset.recipe.splits.get("validation", ()),
        *(case.case_id for case in replayable_cases),
    )
    ordered_candidates: list[EvalCase] = []
    seen_case_ids: set[str] = set()
    for case_id in preferred_case_ids:
        if (
            case_id in replayable_by_id
            and case_id not in held_out_case_ids
            and case_id not in seen_case_ids
        ):
            ordered_candidates.append(replayable_by_id[case_id])
            seen_case_ids.add(case_id)
    if not ordered_candidates:
        ordered_candidates = list(replayable_cases)
    requirement_counts: dict[str, int] = {}
    for requirement in capability_requirements:
        for case_id in requirement.case_ids:
            requirement_counts[case_id] = requirement_counts.get(case_id, 0) + 1
    representative = max(
        enumerate(ordered_candidates),
        key=lambda item: (
            requirement_counts.get(item[1].case_id, 0),
            -item[0],
        ),
    )[1]
    return SelfEvolveDataset(
        cases=(representative,),
        recipe=replace(
            dataset.recipe,
            source={
                **dict(dataset.recipe.source),
                "candidate_screening": True,
                "screening_case_id": representative.case_id,
                "original_case_count": len(dataset.cases),
            },
            splits={
                "train": [representative.case_id],
                "validation": [],
                "held_out": [],
            },
            trainable_case_ids=(representative.case_id,),
            held_out_case_ids=(),
        ),
    )


def _candidate_strategy_records(
    optimizer_diagnostics: list[dict[str, object]] | tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for item in optimizer_diagnostics:
        diagnostics = item.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        strategies = diagnostics.get("candidate_strategies")
        if not isinstance(strategies, list):
            continue
        for strategy in strategies:
            if isinstance(strategy, Mapping) and isinstance(strategy.get("candidate_id"), str):
                records.append(dict(strategy))
    return records


def _rank_candidate_population(
    candidates: tuple[CandidateVariant, ...],
    *,
    optimizer_diagnostics: Mapping[str, object],
    current_content: str,
) -> tuple[CandidateVariant, ...]:
    if len(candidates) <= 1:
        return candidates
    strategy_by_candidate = {
        str(record.get("candidate_id")): record
        for record in _candidate_strategy_records(
            ({"diagnostics": optimizer_diagnostics},)
        )
        if isinstance(record.get("candidate_id"), str)
    }
    if not strategy_by_candidate:
        return candidates
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: _candidate_population_rank_key(
                candidate,
                strategy=strategy_by_candidate.get(candidate.candidate_id) or {},
                current_content=current_content,
            ),
        )
    )


def _candidate_population_rank_key(
    candidate: CandidateVariant,
    *,
    strategy: Mapping[str, object],
    current_content: str,
) -> tuple[int, int, int, int, str]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}.get(
        str(strategy.get("replay_priority") or "low"),
        2,
    )
    addressed_count = _sequence_length(strategy.get("addressed_lessons"))
    preserve_count = _sequence_length(strategy.get("preserved_success_behaviors"))
    char_growth = max(0, len(candidate.content) - len(current_content))
    line_growth = max(
        0,
        len(candidate.content.splitlines()) - len(current_content.splitlines()),
    )
    # Prefer candidates that explicitly address lessons and preserve successful
    # behavior, then keep replay cost bounded by favoring smaller deltas.
    return (
        priority_rank,
        -addressed_count,
        -preserve_count,
        char_growth + (line_growth * 80),
        candidate.candidate_id,
    )


def _sequence_length(value: object) -> int:
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


def _no_op_report(
    gate_results: list[GateResult],
    iteration_reports: list[dict[str, object]],
) -> dict[str, object] | None:
    no_candidate_gate = next(
        (gate for gate in gate_results if gate.gate_name == "no_candidate"),
        None,
    )
    no_candidate_iteration = next(
        (
            item
            for item in iteration_reports
            if item.get("status") == "no_candidate"
        ),
        None,
    )
    if no_candidate_gate is None and no_candidate_iteration is None:
        return None
    return {
        "status": "no_candidate",
        "reason": (
            no_candidate_gate.reason
            if no_candidate_gate is not None
            else "optimizer did not produce a candidate"
        ),
        "iterations": [
            item
            for item in iteration_reports
            if item.get("status") == "no_candidate"
        ],
    }


def _acceptance_confidence_report(gate_results: list[GateResult]) -> dict[str, object] | None:
    for gate in gate_results:
        if gate.gate_name != "held_out_verification" or not isinstance(gate.details, Mapping):
            continue
        details = gate.details
        verification_mode = details.get("verification_mode")
        verification_split = details.get("verification_split")
        if not isinstance(verification_mode, str) and isinstance(verification_split, str):
            verification_mode = verification_split
        if not isinstance(verification_mode, str):
            verification_mode = "unknown"
        return {
            "confidence": details.get("confidence"),
            "verification_mode": verification_mode,
            "verification_split": verification_split,
            "held_out_case_count": details.get("held_out_case_count"),
            "min_eval_cases": details.get("min_eval_cases"),
            "baseline_replay_count": details.get("baseline_replay_count"),
            "candidate_replay_count": details.get("candidate_replay_count"),
            "passed": gate.passed,
        }
    return None


def _baseline_comparison_feedback_metrics(
    *,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
) -> dict[str, float]:
    if baseline_summary is None or candidate_summary is None:
        return {}
    comparison: dict[str, float] = {}
    for metric_key in (
        "score",
        "A1_groundedness",
        "A2_completeness",
        "A3_relevance",
        "A4_readability",
        "B1_tool_use",
        "B2_efficiency",
        "B3_compliance",
        "B4_robustness",
        "evidence_block_count",
        "evidence_incomplete",
        "latency_ms",
    ):
        baseline_value = _metric_number(baseline_summary.metrics, metric_key)
        candidate_value = _metric_number(candidate_summary.metrics, metric_key)
        if baseline_value is None or candidate_value is None:
            continue
        comparison[f"baseline_{metric_key}"] = baseline_value
        comparison[f"candidate_{metric_key}"] = candidate_value
        comparison[f"{metric_key}_delta"] = candidate_value - baseline_value
    return comparison


def _select_iteration_state(
    iteration_states: list[dict[str, object]],
) -> dict[str, object] | None:
    if not iteration_states:
        return None
    for state in iteration_states:
        if state.get("status") == "accepted":
            return state
    return max(
        enumerate(iteration_states),
        key=lambda item: (_iteration_candidate_score(item[1]), item[0]),
    )[1]


def _iteration_candidate_score(
    state: Mapping[str, object],
) -> tuple[int, int, float, int, int]:
    summary = state.get("candidate_summary")
    score = float("-inf")
    if isinstance(summary, EvaluationSummary):
        candidate_score = _metric_number(summary.metrics, "score")
        if candidate_score is not None:
            score = candidate_score
    gate_results = state.get("gate_results")
    gates = (
        tuple(gate_results)
        if isinstance(gate_results, (list, tuple))
        else ()
    )
    failed_count = sum(
        1 for gate in gates if isinstance(gate, GateResult) and not gate.passed
    )
    passed_count = sum(
        1 for gate in gates if isinstance(gate, GateResult) and gate.passed
    )
    failed_gate_names = {
        gate.gate_name
        for gate in gates
        if isinstance(gate, GateResult) and not gate.passed
    }
    substantive_evaluation = failed_gate_names != {
        "duplicate_rejected_candidate"
    }
    reached_replay = bool(
        failed_gate_names & {"candidate_replay", "replay_confidence"}
    )
    adaptation_compiled = "replay_adaptation" not in failed_gate_names
    progress_rank = 2 if reached_replay else 1 if adaptation_compiled else 0
    return (
        int(substantive_evaluation),
        progress_rank,
        score,
        -failed_count,
        passed_count,
    )


def _candidate_generation_limit(
    *,
    replay_candidate_limit: int,
) -> int:
    return max(1, replay_candidate_limit)


def _known_duplicate_candidate_count(
    candidates: tuple[CandidateVariant, ...],
    *,
    rejected_candidate_ids: set[str],
    accepted_candidate_ids: set[str],
) -> int:
    return sum(
        1
        for candidate in candidates
        if candidate.candidate_id in rejected_candidate_ids
        or candidate.candidate_id in accepted_candidate_ids
    )


def _lineage_semantic_lesson_fingerprints(
    lineage_items: tuple[OptimizerLineage, ...],
) -> dict[str, tuple[str, str]]:
    fingerprints: dict[str, tuple[str, str]] = {}
    for lineage in lineage_items:
        if lineage.semantic_fingerprint and lineage.lesson_set_fingerprint:
            fingerprints[lineage.candidate_id] = (
                lineage.semantic_fingerprint,
                lineage.lesson_set_fingerprint,
            )
    return fingerprints


def _semantic_lesson_duplicate_count(
    candidates: tuple[CandidateVariant, ...],
    *,
    lineage_fingerprints: Mapping[str, tuple[str, str]],
    rejected_semantic_lesson_fingerprints: set[tuple[str, str]],
) -> int:
    return sum(
        1
        for candidate in candidates
        if _is_semantic_lesson_duplicate(
            candidate.candidate_id,
            lineage_fingerprints=lineage_fingerprints,
            rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
        )
    )


def _is_semantic_lesson_duplicate(
    candidate_id: str,
    *,
    lineage_fingerprints: Mapping[str, tuple[str, str]],
    rejected_semantic_lesson_fingerprints: set[tuple[str, str]],
) -> bool:
    fingerprint = lineage_fingerprints.get(candidate_id)
    return fingerprint is not None and fingerprint in rejected_semantic_lesson_fingerprints


def _feedback_required_behaviors_from_mutation_prompt(prompt: str | None) -> set[str]:
    if not prompt:
        return set()
    start = prompt.find("{")
    if start < 0:
        return set()
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, Mapping):
        return set()

    feedback_items: list[object] = []
    for key in ("prior_feedback", "validation_feedback"):
        value = payload.get(key)
        if isinstance(value, list):
            feedback_items.extend(value)

    behaviors: set[str] = set()
    for item in feedback_items:
        if not isinstance(item, Mapping):
            continue
        summary = item.get("feedback_summary")
        summary = summary if isinstance(summary, Mapping) else item
        required_behaviors = summary.get("required_behaviors")
        if not isinstance(required_behaviors, list):
            continue
        behaviors.update(str(behavior) for behavior in required_behaviors if str(behavior).strip())
    return behaviors


def _feedback_repair_plan_from_mutation_prompt(prompt: str | None) -> dict[str, set[str]]:
    if not prompt:
        return {"issues": set(), "actions": set(), "acceptance_criteria": set()}
    start = prompt.find("{")
    if start < 0:
        return {"issues": set(), "actions": set(), "acceptance_criteria": set()}
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return {"issues": set(), "actions": set(), "acceptance_criteria": set()}
    if not isinstance(payload, Mapping):
        return {"issues": set(), "actions": set(), "acceptance_criteria": set()}

    feedback_items: list[object] = []
    for key in ("prior_feedback", "validation_feedback"):
        value = payload.get(key)
        if isinstance(value, list):
            feedback_items.extend(value)

    result = {"issues": set(), "actions": set(), "acceptance_criteria": set()}
    for item in feedback_items:
        if not isinstance(item, Mapping):
            continue
        summary = item.get("feedback_summary")
        summary = summary if isinstance(summary, Mapping) else item
        repair_plan = summary.get("repair_plan")
        if not isinstance(repair_plan, Mapping):
            continue
        for key in result:
            values = repair_plan.get(key)
            if isinstance(values, list):
                result[key].update(str(value) for value in values if str(value).strip())
    return result


def _population_strategy_from_mutation_prompt(prompt: str | None) -> str:
    if not prompt:
        return "conservative_preserve_then_delta"
    start = prompt.find("{")
    if start < 0:
        return "conservative_preserve_then_delta"
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return "conservative_preserve_then_delta"
    if not isinstance(payload, Mapping):
        return "conservative_preserve_then_delta"
    strategy = payload.get("population_strategy")
    if isinstance(strategy, Mapping):
        name = strategy.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return "conservative_preserve_then_delta"


def _feedback_metrics_from_mutation_prompt(prompt: str | None) -> list[Mapping[str, Any]]:
    if not prompt:
        return []
    start = prompt.find("{")
    if start < 0:
        return []
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, Mapping):
        return []
    feedback_items: list[object] = []
    for key in ("prior_feedback", "validation_feedback"):
        value = payload.get(key)
        if isinstance(value, list):
            feedback_items.extend(value)
    metrics_items: list[Mapping[str, Any]] = []
    for item in feedback_items:
        if not isinstance(item, Mapping):
            continue
        summary = item.get("feedback_summary")
        summary = summary if isinstance(summary, Mapping) else item
        metrics = summary.get("metrics")
        if isinstance(metrics, Mapping):
            metrics_items.append(metrics)
    return metrics_items


def _feedback_has_scope_or_cost_issue(prompt: str | None) -> bool:
    behaviors = _feedback_required_behaviors_from_mutation_prompt(prompt)
    return bool(
        behaviors
        & {
            "reduce_answer_scope_to_verified_claims",
            "prefer_fewer_verified_claims_over_broad_synthesis",
            "optimize_verifiability_per_evidence_block",
            "avoid_collecting_more_evidence_without_verifiability_gain",
            "cap_evidence_acquisition_and_summarization_cost",
        }
    )


def _feedback_has_high_baseline_regression_issue(prompt: str | None) -> bool:
    behaviors = _feedback_required_behaviors_from_mutation_prompt(prompt)
    if behaviors & {
        "differentiate_from_high_scoring_baseline",
        "preserve_baseline_strengths",
        "define_behavior_delta_before_tools",
        "prefer_targeted_changes_over_broad_rewrites",
        }:
        return True
    repair_plan = _feedback_repair_plan_from_mutation_prompt(prompt)
    if repair_plan["actions"] & {
        "preserve_high_scoring_baseline_strengths",
        "define_candidate_behavior_delta",
        "prefer_targeted_change_over_broad_rewrite",
    }:
        return True
    for metrics in _feedback_metrics_from_mutation_prompt(prompt):
        baseline_score = _metric_number(metrics, "baseline_score")
        candidate_score = _metric_number(metrics, "candidate_score")
        score_delta = _metric_number(metrics, "score_delta")
        if baseline_score is None or baseline_score < 85.0:
            continue
        if score_delta is not None and score_delta <= 0:
            return True
        if candidate_score is not None and candidate_score <= baseline_score:
            return True
    return False


def _feedback_has_evidence_preservation_issue(prompt: str | None) -> bool:
    if not prompt:
        return False
    start = prompt.find("{")
    if start < 0:
        return False
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, Mapping):
        return False

    feedback_items: list[object] = []
    for key in ("prior_feedback", "validation_feedback"):
        value = payload.get(key)
        if isinstance(value, list):
            feedback_items.extend(value)

    for item in feedback_items:
        if not isinstance(item, Mapping):
            continue
        summary = item.get("feedback_summary")
        summary = summary if isinstance(summary, Mapping) else item
        metrics = summary.get("metrics")
        if not isinstance(metrics, Mapping):
            metrics = {}
        evidence = summary.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else metrics
        failed_gates = summary.get("failed_gates")
        if not isinstance(failed_gates, list):
            failed_gates = metrics.get("failed_gates")
        if isinstance(failed_gates, list) and "evidence_quality" in {
            str(gate) for gate in failed_gates
        }:
            return True
        if evidence.get("evidence_compacted") is True:
            return True
        if evidence.get("evidence_incomplete") is True:
            return True
    return False


def _default_cli_skill_candidate(
    *,
    current_content: str,
    trace_packs: tuple[TracePack, ...],
    mutation_prompt: str | None = None,
) -> str:
    runtime_rules = _runtime_behavior_rules_from_mutation_prompt(
        mutation_prompt,
        trace_packs=trace_packs,
    )
    if not runtime_rules:
        return current_content
    prefix = _candidate_runtime_prefix(current_content)
    section = ["## Runtime Behavior Delta", ""]
    section.extend(f"- {rule}" for rule in runtime_rules[:6])
    return prefix + "\n\n" + "\n".join(section) + "\n"


def _candidate_runtime_prefix(current_content: str) -> str:
    prefix = current_content.rstrip()
    for heading in (
        "\n## Self-Evolve Trace Guidance\n",
        "\n## Self-Evolve Targeted Delta\n",
        "\n## Runtime Behavior Delta\n",
    ):
        if heading in prefix:
            prefix = prefix.split(heading, 1)[0].rstrip()
    return prefix


def _runtime_behavior_rules_from_mutation_prompt(
    prompt: str | None,
    *,
    trace_packs: tuple[TracePack, ...],
) -> list[str]:
    repair_plan = _feedback_repair_plan_from_mutation_prompt(prompt)
    required_behaviors = _feedback_required_behaviors_from_mutation_prompt(prompt)
    population_strategy = _population_strategy_from_mutation_prompt(prompt)
    rules: list[str] = []

    def add(rule: str) -> None:
        if rule not in rules:
            rules.append(rule)

    if _feedback_has_evidence_preservation_issue(prompt) or required_behaviors & {
        "artifact_first",
        "bounded_structured_summary",
        "non_compacted_evidence",
        "claim_evidence_ledger",
        "claim_by_claim_verification",
        "support_every_claim_with_artifact_reference",
    }:
        add(
            "Persist large or unknown-size evidence to an artifact before inspecting "
            "or summarizing it."
        )
        add(
            "Use bounded structured extracts with source locations for the final answer; "
            "do not place full pages, documents, logs, or large JSON in the conversation."
        )
        add(
            "When output is compacted, truncated, or schema-invalid and no valid "
            "artifact-backed evidence bundle, manifest entry, or bounded extract exists, "
            "retry with a narrower extraction; otherwise use the artifact-backed evidence "
            "and retain only claims it directly supports."
        )

    if required_behaviors & {
        "verify_task_semantic_sufficiency_before_finalizing",
        "do_not_treat_transport_success_as_task_completion",
        "continue_bounded_acquisition_when_payload_is_only_metadata_or_execution_summary",
    } or repair_plan["issues"] & {"semantically_insufficient_evidence"}:
        add(
            "Treat transport success and structured envelopes as delivery signals, not task "
            "completion: stop only when the saved payload directly supports the requested "
            "claims; otherwise try one materially different bounded artifact-backed source "
            "or report the insufficiency explicitly."
        )

    if _feedback_has_scope_or_cost_issue(prompt) or required_behaviors & {
        "plan_before_tools",
        "prefer_direct_structured_extraction",
        "minimize_failed_attempts",
        "avoid_repeated_paths",
        "stop_after_sufficient_evidence",
        "cap_evidence_acquisition_and_summarization_cost",
    }:
        add(
            "Plan the shortest viable evidence path, avoid repeating a failed or low-yield "
            "path, and stop once the requested claims have sufficient support."
        )
        add(
            "Limit the final answer to requested claims with direct support; do not broaden "
            "the synthesis or collect more evidence without a verifiability gain."
        )

    if repair_plan["actions"] & {
        "write_valid_bounded_evidence_manifest",
        "persist_evidence_before_inspection",
    } or required_behaviors & {
        "manifest_schema_compliance",
    }:
        add(
            "Validate each evidence manifest entry before finalizing: identify its source "
            "and include a bounded excerpt, structured extract, or source span."
        )

    if repair_plan["issues"] & {
        "replay_timeout",
        "replay_evidence_quality_failure",
        "replay_trajectory_capture_failure",
    } or _has_failed_trace_lesson(prompt, trace_packs=trace_packs):
        add(
            "After one failed tool or evidence path, record the observed failure and change "
            "strategy before retrying; do not finalize without a captured result."
        )

    if (
        "compacted_tool_argument_replay" in repair_plan["issues"]
        or repair_plan["actions"]
        & {
            "regenerate_compacted_tool_arguments",
            "switch_to_artifact_read_after_invalid_tool_argument",
            "stop_repeating_invalid_tool_calls",
        }
        or required_behaviors
        & {
            "avoid_compacted_tool_arguments",
            "regenerate_schema_valid_tool_arguments",
            "stop_repeating_invalid_tool_calls",
            "switch_to_artifact_read_after_invalid_tool_argument",
        }
    ):
        add(
            "Before retrying a tool, regenerate the smallest schema-valid arguments from "
            "the current task or a saved artifact; never execute compacted placeholders."
        )

    if _feedback_has_high_baseline_regression_issue(prompt):
        add(
            "Preserve the existing successful workflow and add only the smallest repair "
            "required by current evidence; avoid extra collection or verification passes."
        )

    if (
        "high_baseline_without_efficiency_gain" in repair_plan["issues"]
        or "replace_broad_validation_with_efficiency_delta" in repair_plan["actions"]
        or "candidate_uses_no_more_steps_than_baseline"
        in repair_plan["acceptance_criteria"]
    ):
        add(
            "Preserve the supported claim set, answer structure, and source references while "
            "using no more tool or evidence steps; do not add broad comparison passes."
        )

    if rules and population_strategy == "evidence_integrity_delta":
        add(
            "Make evidence integrity the only changed behavior: repair bounded source payloads "
            "without changing supported answer content."
        )
    elif rules and population_strategy == "score_dimension_repair_delta":
        add(
            "Restore grounded and complete supported claims first, and skip any additional "
            "step that does not repair answer quality or execution efficiency."
        )

    return rules


def _has_failed_trace_lesson(
    prompt: str | None,
    *,
    trace_packs: tuple[TracePack, ...],
) -> bool:
    if any(
        str(pack.steps[-1].reward.get("status", "")).strip().lower()
        in {"failed", "error", "timeout", "cancelled", "rejected"}
        for pack in trace_packs
        if pack.steps
    ):
        return True
    if not prompt:
        return False
    start = prompt.find("{")
    if start < 0:
        return False
    try:
        payload = json.loads(prompt[start:])
    except json.JSONDecodeError:
        return False
    lessons = payload.get("lesson_records") if isinstance(payload, Mapping) else None
    return isinstance(lessons, list) and any(
        isinstance(lesson, Mapping)
        and str(lesson.get("lesson_type", "")).endswith("failure_memory")
        for lesson in lessons
    )


def _target_from_cli_ref(
    target: str,
    *,
    workspace_root: str | Path,
    allow_auto_apply: bool = False,
) -> SelfEvolveTarget:
    target_type, _, target_id = target.partition(":")
    if not target_type or not target_id:
        raise NotImplementedError(f"CLI target adapter is not implemented for {target!r}")
    return _target_from_ref(
        SelfEvolveTargetRef(target_type=target_type, target_id=target_id),
        workspace_root=workspace_root,
        allow_auto_apply=allow_auto_apply,
    )


def _candidate_gate_results(
    candidate: CandidateVariant,
    *,
    current_content: str,
    workspace_root: str | Path,
    max_chars: int,
    target_provenance: TargetProvenance | None,
    target_provenance_unresolved_reason: str | None = None,
    allow_generated_target_mutation: bool = False,
    allow_external_target_mutation: bool = False,
) -> list[GateResult]:
    results = [
        NoopCandidateGate().evaluate(current_content=current_content, candidate=candidate),
        MalformedCandidateGate().evaluate(candidate),
        CandidatePackageGate().evaluate(candidate),
        TokenLimitGate(max_chars=max_chars).evaluate(candidate),
        ProtectedPathGate(workspace_root=workspace_root).evaluate(candidate),
        ExternalCodeEvolutionGate().evaluate(candidate),
    ]
    if candidate.target.target_type == "skill":
        results.append(SkillMarkdownGate().evaluate(candidate))
    results.append(
        TrustProvenanceGate(
            allow_generated=allow_generated_target_mutation,
            allow_external=allow_external_target_mutation,
        ).evaluate(
            target_provenance,
            unresolved_reason=target_provenance_unresolved_reason,
        )
    )
    return results


def _target_from_ref(
    target_ref: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path,
    allow_auto_apply: bool = False,
) -> SelfEvolveTarget:
    adapter_factory = _CLI_TARGET_ADAPTER_FACTORIES.get(target_ref.target_type)
    if adapter_factory is None:
        raise NotImplementedError(
            "target inference selected "
            f"{target_ref.target_type}:{target_ref.target_id}, but that target adapter "
            "is not implemented for phase 1 CLI runs"
        )
    return adapter_factory(
        target_ref,
        workspace_root=workspace_root,
        allow_auto_apply=allow_auto_apply,
    )


def _skill_target_adapter(
    target_ref: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path,
    allow_auto_apply: bool = False,
) -> SelfEvolveTarget:
    if target_ref.path:
        path = Path(target_ref.path)
        if path.exists():
            return SkillTextTarget(
                path,
                target_id=target_ref.target_id,
                allow_auto_apply=allow_auto_apply,
            )
        return DraftSkillTextTarget(
            path,
            target_id=target_ref.target_id,
            release_path=(
                Path(workspace_root)
                / "aworld-skills"
                / target_ref.target_id
                / "SKILL.md"
            ),
            allow_auto_apply=allow_auto_apply,
        )
    return _skill_target_from_id(
        target_ref.target_id,
        workspace_root=workspace_root,
        allow_auto_apply=allow_auto_apply,
    )


def _skill_target_from_id(
    target_id: str,
    *,
    workspace_root: str | Path,
    allow_auto_apply: bool = False,
) -> SelfEvolveTarget:
    workspace = Path(workspace_root)
    candidates = (
        workspace / "aworld-skills" / target_id / "SKILL.md",
        workspace / "skills" / target_id / "SKILL.md",
    )
    for path in candidates:
        if path.exists():
            return SkillTextTarget(path, allow_auto_apply=allow_auto_apply)
    raise FileNotFoundError(f"skill target not found: skill:{target_id}")


_CLI_TARGET_ADAPTER_FACTORIES: Mapping[
    str,
    Callable[..., SelfEvolveTarget],
] = {
    "skill": _skill_target_adapter,
}


def _infer_target_from_trace_packs(
    trace_packs: tuple[TracePack, ...],
    *,
    workspace_root: str | Path,
) -> TargetSelectionDecision:
    if not trace_packs:
        raise ValueError("target inference requires trajectory evidence")

    inventory = build_default_target_inventory(workspace_root).only_target_types(
        _CLI_TARGET_ADAPTER_FACTORIES
    )
    assigner = TrajectoryCreditAssigner(inventory=inventory)
    decisions = [assigner.assign_decision(trace_pack) for trace_pack in trace_packs]
    return _aggregate_target_selection_decisions(tuple(decisions))


def _aggregate_target_selection_decisions(
    decisions: tuple[TargetSelectionDecision, ...],
) -> TargetSelectionDecision:
    if not decisions:
        raise ValueError("target decision aggregation requires at least one decision")
    best_decision = max(
        decisions,
        key=lambda item: (
            item.report.selected_target is not None,
            item.report.confidence,
            _target_selection_priority(item.report),
        ),
    )
    best_report = best_decision.report
    if best_report.selected_target is None:
        return best_decision

    selected_key = (
        best_report.selected_target.target_type,
        best_report.selected_target.target_id,
    )
    contributing_reports = tuple(
        decision.report
        for decision in decisions
        if decision.report.selected_target is not None
        and (
            decision.report.selected_target.target_type,
            decision.report.selected_target.target_id,
        )
        == selected_key
    )
    contributing_decisions = tuple(
        decision
        for decision in decisions
        if decision.report in contributing_reports
    )
    diagnostics = dict(best_report.diagnostics or {})
    diagnostics["contributing_pack_ids"] = [
        pack_id
        for report in contributing_reports
        for pack_id in _target_selection_pack_ids(report)
    ]
    aggregated_report = replace(
        best_report,
        evidence_step_ids=tuple(
            dict.fromkeys(
                evidence_id
                for report in contributing_reports
                for evidence_id in report.evidence_step_ids
            )
        ),
        signals=tuple(
            dict.fromkeys(
                signal
                for report in contributing_reports
                for signal in report.signals
            )
        ),
        diagnostics=diagnostics,
    )
    consistent_authorization = all(
        decision.selection_origin == best_decision.selection_origin
        and decision.provenance_resolution == best_decision.provenance_resolution
        for decision in contributing_decisions
    )
    if consistent_authorization:
        provenance_resolution = best_decision.provenance_resolution
        selection_origin = best_decision.selection_origin
    else:
        provenance_resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason="aggregated target decisions disagree on authorization",
        )
        selection_origin = TargetSelectionOrigin.UNKNOWN
        aggregated_report = replace(
            aggregated_report,
            provenance_status=provenance_resolution.status,
            provenance_reason=provenance_resolution.reason,
            selection_origin=selection_origin,
        )
    return TargetSelectionDecision(
        report=aggregated_report,
        provenance_resolution=provenance_resolution,
        selection_origin=selection_origin,
    )


def _target_selection_pack_ids(report: TargetSelectionReport) -> tuple[str, ...]:
    diagnostics = report.diagnostics
    if not isinstance(diagnostics, Mapping):
        return ()
    pack_id = diagnostics.get("pack_id")
    if isinstance(pack_id, str) and pack_id:
        return (pack_id,)
    pack_ids = diagnostics.get("pack_ids")
    if isinstance(pack_ids, (list, tuple)):
        return tuple(
            str(item) for item in pack_ids if isinstance(item, str) and item
        )
    return ()


def _auto_group_trajectory_log_dataset(
    dataset: SelfEvolveDataset,
    trace_packs: tuple[TracePack, ...],
    *,
    source_config: SelfEvolveEvalSourceConfig,
    workspace_root: str | Path,
    infer_target: Callable[
        [tuple[TracePack, ...]],
        TargetSelectionDecision,
    ]
    | None = None,
) -> tuple[SelfEvolveDataset, tuple[TracePack, ...], dict[str, object]]:
    if source_config.kind != "trajectory_log" or len(trace_packs) <= 1:
        return dataset, trace_packs, {
            "auto_grouped": False,
            "reason": "trajectory log has fewer than two trace packs",
        }

    infer = infer_target or (
        lambda packs, *, workspace_root=workspace_root: _infer_target_from_trace_packs(
            packs,
            workspace_root=workspace_root,
        )
    )
    groups: dict[str, dict[str, object]] = {}
    for trace_pack in trace_packs:
        report = infer((trace_pack,), workspace_root=workspace_root).report
        group_id = _target_group_id(report, fallback=trace_pack.task_id)
        group = groups.setdefault(
            group_id,
            {
                "group_id": group_id,
                "target": (
                    to_json_dict(report.selected_target)
                    if report.selected_target is not None
                    else None
                ),
                "confidence_sum": 0.0,
                "reports": [],
                "case_ids": [],
                "pack_ids": [],
                "trace_packs": [],
                "has_target": report.selected_target is not None,
                "target_priority": _target_selection_priority(report),
            },
        )
        group["confidence_sum"] = float(group["confidence_sum"]) + report.confidence
        cast_reports = group["reports"]
        if isinstance(cast_reports, list):
            cast_reports.append(to_json_dict(report))
        case_ids = group["case_ids"]
        if isinstance(case_ids, list):
            case_ids.append(trace_pack.task_id)
        pack_ids = group["pack_ids"]
        if isinstance(pack_ids, list):
            pack_ids.append(trace_pack.pack_id)
        packs = group["trace_packs"]
        if isinstance(packs, list):
            packs.append(trace_pack)

    ranked_groups = sorted(
        groups.values(),
        key=lambda item: (
            bool(item.get("has_target")),
            _group_confidence_bucket(item),
            len(item.get("case_ids") or ()),
            int(item.get("target_priority") or 0),
            _group_average_confidence(item),
            str(item.get("group_id") or ""),
        ),
        reverse=True,
    )
    selected_group = ranked_groups[0]
    selected_case_ids = tuple(
        str(case_id) for case_id in selected_group.get("case_ids", ()) if case_id
    )
    selected_case_id_set = set(selected_case_ids)
    selected_cases = tuple(
        case for case in dataset.cases if case.case_id in selected_case_id_set
    )
    selected_trace_packs = tuple(
        pack for pack in trace_packs if pack.task_id in selected_case_id_set
    )
    grouping_report = _trajectory_log_grouping_report(
        ranked_groups,
        selected_group_id=str(selected_group["group_id"]),
    )
    recipe = build_dataset_recipe(
        selected_cases,
        source_config=source_config,
        split_seed=dataset.recipe.split_seed,
        synthetic_generation_policy=dataset.recipe.synthetic_generation_policy,
    )
    source = dict(recipe.source)
    source["auto_grouping"] = grouping_report
    grouped_dataset = SelfEvolveDataset(
        cases=selected_cases,
        recipe=replace(recipe, source=source),
    )
    return grouped_dataset, selected_trace_packs, grouping_report


def _target_group_id(report: TargetSelectionReport, *, fallback: str) -> str:
    if report.selected_target is None:
        return f"no_target:{fallback}"
    return f"{report.selected_target.target_type}:{report.selected_target.target_id}"


def _group_average_confidence(group: Mapping[str, object]) -> float:
    case_ids = group.get("case_ids")
    count = len(case_ids) if isinstance(case_ids, list) else 0
    if count <= 0:
        return 0.0
    return float(group.get("confidence_sum") or 0.0) / count


def _group_confidence_bucket(group: Mapping[str, object]) -> float:
    return round(_group_average_confidence(group), 2)


def _trajectory_log_grouping_report(
    ranked_groups: list[dict[str, object]],
    *,
    selected_group_id: str,
) -> dict[str, object]:
    group_summaries: list[dict[str, object]] = []
    for group in ranked_groups:
        group_summaries.append(
            {
                "group_id": group.get("group_id"),
                "target": group.get("target"),
                "case_ids": list(group.get("case_ids") or ()),
                "pack_ids": list(group.get("pack_ids") or ()),
                "confidence": _group_average_confidence(group),
                "selected": group.get("group_id") == selected_group_id,
            }
        )
    selected_group = next(
        group for group in group_summaries if group["group_id"] == selected_group_id
    )
    largest_group_size = max(
        (len(group.get("case_ids") or ()) for group in group_summaries),
        default=0,
    )
    selected_case_count = len(selected_group.get("case_ids") or ())
    low_dataset_support = selected_case_count <= 1 and largest_group_size > selected_case_count
    return {
        "auto_grouped": True,
        "strategy": "inferred_target",
        "group_count": len(group_summaries),
        "selected_group_id": selected_group_id,
        "selected_case_ids": list(selected_group["case_ids"]),
        "selected_case_count": selected_case_count,
        "largest_group_case_count": largest_group_size,
        "low_dataset_support": low_dataset_support,
        "skipped_group_count": max(0, len(group_summaries) - 1),
        "groups": group_summaries,
    }


def _target_selection_priority(report: TargetSelectionReport) -> int:
    if report.selected_target is None:
        return 0
    priorities = {
        "prompt-section": 30,
        "tool-description": 25,
        "skill": 20,
        "config": 10,
        "workspace-artifact": 5,
    }
    return priorities.get(report.selected_target.target_type, 1)


def _explicit_target_selection_report(
    target: SelfEvolveTargetRef,
    trace_packs: tuple[TracePack, ...],
) -> TargetSelectionReport:
    evidence_step_ids = tuple(
        step.evidence_id
        for trace_pack in trace_packs
        for step in trace_pack.steps
    )
    return TargetSelectionReport(
        selected_target=target,
        confidence=1.0,
        evidence_step_ids=evidence_step_ids,
        failure_category="explicit_target",
        signals=("explicit_target",),
        diagnostics={
            "pack_ids": [trace_pack.pack_id for trace_pack in trace_packs],
            "target_inference": "bypassed",
        },
        selection_origin=TargetSelectionOrigin.OPERATOR_EXPLICIT,
    )


def _no_evidence_target_selection_report(source_kind: str) -> TargetSelectionReport:
    return TargetSelectionReport(
        selected_target=None,
        confidence=0.0,
        evidence_step_ids=(),
        failure_category="no_target",
        signals=("missing_trajectory_evidence",),
        no_target_reason="target inference requires trajectory evidence",
        diagnostics={"source_kind": source_kind},
        selection_origin=TargetSelectionOrigin.INFERRED,
    )


def _inferred_target_confident_for_auto_apply(report: TargetSelectionReport) -> bool:
    return report.confidence >= 0.9 and "low_confidence" not in report.signals


def _blocked_low_confidence_target_selection_report(
    report: TargetSelectionReport,
) -> TargetSelectionReport:
    diagnostics = dict(report.diagnostics or {})
    if report.selected_target is not None:
        diagnostics["blocked_selected_target"] = to_json_dict(report.selected_target)
    return TargetSelectionReport(
        selected_target=None,
        confidence=report.confidence,
        evidence_step_ids=report.evidence_step_ids,
        failure_category=report.failure_category,
        signals=tuple(report.signals) + ("auto_verified_low_confidence_blocked",),
        no_target_reason=(
            "auto_verified target inference requires confidence >= 0.9 without low_confidence signal"
        ),
        diagnostics=diagnostics,
        provenance_status=report.provenance_status,
        provenance_reason=report.provenance_reason,
        selection_origin=report.selection_origin,
    )


def _persist_no_target_cli_result(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    dataset: SelfEvolveDataset,
    target_selection_report: TargetSelectionReport,
    apply_policy: str,
) -> Mapping[str, Any]:
    target = SelfEvolveTargetRef(target_type="no_target", target_id="no_target")
    run = SelfEvolveRun(run_id=run_id, target=target, status=SelfEvolveRunStatus.REJECTED)
    store.create_run(run)
    store.write_dataset_recipe(run_id, dataset.recipe)
    target_selection_path = store.write_target_selection_report(run_id, target_selection_report)
    report = {
        "run_id": run_id,
        "target": {
            "target_type": target.target_type,
            "target_id": target.target_id,
            "path": target.path,
        },
        "apply_policy": apply_policy,
        "candidate_ids": [],
        "selected_candidate_id": None,
        "status": run.status.value,
        "target_selection": to_json_dict(target_selection_report),
    }
    report["artifact_retention"] = _artifact_retention_report(store, run_id)
    report_path = store.write_report(run_id, report)
    return {
        "report_path": str(report_path),
        "target_selection_path": str(target_selection_path),
        "best_candidate_id": None,
        "run_id": run_id,
        "status": run.status.value,
    }


def _persist_unsupported_target_cli_result(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    dataset: SelfEvolveDataset,
    target_selection_report: TargetSelectionReport,
    target_provenance: TargetProvenance | None,
    apply_policy: str,
    reason: str,
) -> Mapping[str, Any]:
    if target_selection_report.selected_target is None:
        return _persist_no_target_cli_result(
            store=store,
            run_id=run_id,
            dataset=dataset,
            target_selection_report=target_selection_report,
            apply_policy=apply_policy,
        )

    target = target_selection_report.selected_target
    run = SelfEvolveRun(run_id=run_id, target=target, status=SelfEvolveRunStatus.REJECTED)
    store.create_run(run)
    store.write_dataset_recipe(run_id, dataset.recipe)
    target_selection_path = store.write_target_selection_report(run_id, target_selection_report)
    target_provenance_path = (
        store.write_target_provenance(run_id, target_provenance)
        if target_provenance is not None
        else None
    )
    report = {
        "run_id": run_id,
        "target": {
            "target_type": target.target_type,
            "target_id": target.target_id,
            "path": target.path,
        },
        "apply_policy": apply_policy,
        "candidate_ids": [],
        "selected_candidate_id": None,
        "status": run.status.value,
        "target_selection": to_json_dict(target_selection_report),
        "target_provenance": {
            "status": (
                "resolved" if target_provenance_path is not None else "unresolved"
            ),
            "path": (
                str(target_provenance_path)
                if target_provenance_path is not None
                else None
            ),
            "reason": target_selection_report.provenance_reason,
        },
        "unsupported_target": {
            "target_ref": _target_ref_text(target),
            "reason": reason,
        },
    }
    report["artifact_retention"] = _artifact_retention_report(store, run_id)
    report_path = store.write_report(run_id, report)
    summary = {
        "report_path": str(report_path),
        "target_selection_path": str(target_selection_path),
        "best_candidate_id": None,
        "run_id": run_id,
        "status": run.status.value,
    }
    if target_provenance_path is not None:
        summary["target_provenance_path"] = str(target_provenance_path)
    else:
        summary["target_provenance"] = {
            "status": "unresolved",
            "reason": target_selection_report.provenance_reason,
        }
    return summary


def _target_ref_text(target: SelfEvolveTargetRef) -> str:
    return f"{target.target_type}:{target.target_id}"


def _cli_run_id(
    target_key: str | None,
    dataset: str | None,
    from_session: str | None,
    from_trajectory: str | None,
    from_trajectory_set: str | None,
    batch_config: str | None,
    iterations: int | None,
) -> str:
    return (
        "cli-"
        f"{abs(hash((target_key, dataset, from_session, from_trajectory, from_trajectory_set, batch_config, iterations))) % 10**12:012d}"
    )


def _source_config_from_cli_request(
    *,
    dataset: str | None,
    from_session: str | None,
    from_trajectory: str | None,
    from_trajectory_set: str | None,
    batch_config: str | None,
    workspace_root: str | Path,
) -> SelfEvolveEvalSourceConfig:
    if dataset:
        return SelfEvolveEvalSourceConfig(kind="jsonl", path=dataset)
    if from_trajectory:
        return SelfEvolveEvalSourceConfig(kind="trajectory_log", path=from_trajectory)
    if from_trajectory_set:
        return SelfEvolveEvalSourceConfig(kind="trajectory_set", path=from_trajectory_set)
    if from_session:
        return SelfEvolveEvalSourceConfig(
            kind="session",
            path=str(workspace_root),
            session_id=from_session,
        )
    if batch_config:
        return SelfEvolveEvalSourceConfig(kind="batch_config", path=batch_config)
    raise ValueError("an eval source is required")
