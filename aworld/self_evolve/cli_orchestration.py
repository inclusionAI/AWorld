"""Typed CLI optimization orchestration outside the Runner facade."""

from __future__ import annotations

import hashlib
import json
import re

from aworld.config.conf import (
    ModelConfig,
    SelfEvolveJudgeConfig,
)
from aworld.runner import (
    Runners,
)
from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetStage,
    RunBudgetLedger,
)
from aworld.self_evolve.campaign_policy import (
    effective_cli_measurement_mode as _effective_cli_measurement_mode,
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.apply_runtime_support import (
    default_new_skill_registry_compensator,
    default_new_skill_registry_refresher,
    default_post_apply_evaluator,
)
from aworld.self_evolve.target_selection_support import explicit_target_selection_report
from aworld.self_evolve.target_package import (
    _target_runtime_skill_path as target_runtime_skill_path,
)
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationAgent,
    _effective_candidate_output_token_limit,
)
from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
)
from aworld.self_evolve.candidate_protocol import (
    CANDIDATE_OUTPUT_CONTRACT,
    CandidateProtocolError,
    build_candidate_output_contract,
    merge_candidate_repair_output,
    normalize_candidate_output,
)
from aworld.self_evolve.challenger import (
    ChallengerBackend,
    DEFAULT_CHALLENGE_CASES,
)
from aworld.self_evolve.cli_ingestion import (
    _dataset_ingestion_summary,
    _ingestion_mode,
    _persist_ingestion_rejection,
    _source_config_from_cli_request,
    _validate_eval_source_request,
    _validate_frozen_semantic_runtime_admission,
    _write_run_ingestion_gate,
    prepare_ingestion_from_cli_request,
    promote_ingestion_from_cli_request,
)
from aworld.self_evolve.cli_rerun import (
    _load_target_provenance,
    _load_target_selection_report,
    _load_stored_campaign_dataset,
    _rerun_cli_run_id,
    _resolve_stored_run_path,
    _source_config_from_stored_dataset_recipe,
    _stored_selected_candidate_id,
    _validate_agentic_rerun_ingestion_ref,
    _validate_rerun_source_runtime_admission,
)
from aworld.self_evolve.concurrency import (
    AWorldCandidatePopulationExecutor,
    SelfEvolveConcurrencyPolicy,
)
from aworld.self_evolve.controllers.retention import (
    _artifact_retention_report,
    acknowledge_reported_artifact_retention as _acknowledge_reported_artifact_retention,
)
from aworld.self_evolve.controllers.screening_execution import (
    _emit_progress,
)
from aworld.self_evolve.history_support import _load_json_mapping
from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    TargetSelectionReport,
    TrajectoryCreditAssigner,
    build_default_target_inventory,
    build_target_selection_decision,
)
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
    build_dataset_recipe,
)
from aworld.self_evolve.evaluation import (
    AWorldTrajectoryEvaluatorBackend,
    EvaluationBackend,
    SkillCandidateOverlayBackend,
)
from aworld.self_evolve.evaluation_reporting import _metric_number
from aworld.self_evolve.feedback_history import (
    _report_has_shared_measurement_failure,
)
from aworld.self_evolve.ingestion import (
    DEFAULT_INGESTION_REGISTRY,
    FrozenIngestionSnapshot,
    IngestionRegistry,
    IngestorTrustLevel,
    evaluate_ingestion_gate,
    fingerprint_json as ingestion_fingerprint_json,
    parse_source_manifest,
)
from aworld.self_evolve.ingestion.semantic_snapshot import (
    FrozenSemanticIngestionSnapshotV2,
)
from aworld.self_evolve.ingestion.semantic_verifier import (
    evaluate_semantic_quality_gate,
)
from aworld.self_evolve.measurement import (
    MeasurementPolicyMode,
)
from aworld.self_evolve.measurement_checkpoint import (
    MeasurementResumeCheckpointV1,
    PairedReplayResumeCheckpointV1,
    discover_measurement_resume_checkpoint,
    discover_paired_replay_resume_checkpoint,
    load_measurement_resume_checkpoint,
    load_paired_replay_resume_checkpoint,
)
from aworld.self_evolve.optimizers.base import (
    CandidateOptimizer,
    CandidateSemanticValidationError,
    CandidateSourceDisposition,
    CandidateSourceKind,
    OptimizerRequest,
    OptimizerResult,
)
from aworld.self_evolve.optimizers.llm_mutator import (
    TraceReflectiveLLMMutator,
)
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
    TargetProvenance,
    TargetProvenanceResolution,
    TargetProvenanceStatus,
    TargetSelectionOrigin,
)
from aworld.self_evolve.recovery_trace import (
    trace_pack_recovery_opportunity,
)
from aworld.self_evolve.regression import (
    resolve_regression_suites,
    resolve_target_contract_regression_suite,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayBackend,
    CandidateReplayResult,
    ReplayEvidenceDispositionKind,
    ReplayEvidenceReuseDisposition,
    candidate_replay_is_comparable,
    load_candidate_replay_result,
)
from aworld.self_evolve.run_history import (
    _load_candidate_variant,
    _report_matches_target,
)

from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationCompiler,
)
from aworld.self_evolve.sanitization import (
    public_diagnostic_projection,
    sanitize_path_ref,
    sanitize_source_text,
    sanitize_text,
)
from aworld.self_evolve.skill_evolution_contract import (
    SkillEvolutionContract,
)
from aworld.self_evolve.store import (
    FilesystemSelfEvolveStore,
)
from aworld.self_evolve.targets import (
    DraftSkillTextTarget,
    SelfEvolveTarget,
    SkillTextTarget,
)
from aworld.self_evolve.trace_pack import (
    TracePack,
    build_trace_pack,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
    to_json_dict,
)
from dataclasses import (
    dataclass,
    replace,
)
from decimal import (
    Decimal,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
)


@dataclass(frozen=True)
class CliOrchestrationRuntime:
    """Compatibility services injected by the Runner module boundary."""

    runner_type: Callable[..., Any]
    run_budget_context_type: Callable[..., Any]
    load_or_build_campaign_dataset: Callable[..., Any]
    default_cli_skill_candidate: Callable[..., Any]
    auto_group_trajectory_log_dataset: Callable[..., Any]
    infer_target_from_trace_packs: Callable[..., Any]
    target_from_ref: Callable[..., Any]
    replay_backend_type: Callable[..., CandidateReplayBackend]


@dataclass(frozen=True)
class _FixedCandidateOptimizer:
    candidate: CandidateVariant
    source_run_id: str
    admission_reason_code: str = "stored_candidate_fresh_evaluation"

    def __post_init__(self) -> None:
        if not self.admission_reason_code.strip():
            raise ValueError("stored candidate admission reason is required")

    def proves_zero_budget_usage(self, stage: BudgetStage) -> bool:
        return stage is BudgetStage.CANDIDATE_GENERATION

    def stored_candidate_admission_reason(self) -> str:
        """Declare why scheduler mutation-frontier discovery is not needed."""

        return self.admission_reason_code

    def opens_repair_frontier_after_stored_candidate(self) -> bool:
        """Evaluator-only reruns must never generate replacement candidates."""

        return False

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        return OptimizerResult(
            candidates=(self.candidate,),
            source_disposition=CandidateSourceDisposition(
                kind=CandidateSourceKind.STORED_EVIDENCE_RERUN,
                source_run_id=self.source_run_id,
            ),
            diagnostics={
                "source": "stored_self_evolve_run",
                "source_run_id": self.source_run_id,
                "candidate_id": self.candidate.candidate_id,
            },
        )


@dataclass
class _MeasurementResumeThenRepairOptimizer:
    """Replay one frozen candidate, then open the real mutation frontier."""

    candidate: CandidateVariant
    source_run_id: str
    delegate: CandidateOptimizer
    proposal_count: int = 0

    def proves_zero_budget_usage(self, stage: BudgetStage) -> bool:
        # The first proposal is free, but subsequent repair proposals are not.
        # Budget exemptions are configured for the optimizer as a whole, so a
        # mixed optimizer must conservatively decline a global exemption.
        return False

    def stored_candidate_admission_reason(self) -> str | None:
        if self.proposal_count <= 1:
            return "stored_candidate_measurement_resume"
        return None

    def opens_repair_frontier_after_stored_candidate(self) -> bool:
        return True

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        if self.proposal_count == 0:
            self.proposal_count = 1
            return OptimizerResult(
                candidates=(self.candidate,),
                source_disposition=CandidateSourceDisposition(
                    kind=CandidateSourceKind.STORED_EVIDENCE_RERUN,
                    source_run_id=self.source_run_id,
                ),
                diagnostics={
                    "source": "stored_self_evolve_run",
                    "source_run_id": self.source_run_id,
                    "candidate_id": self.candidate.candidate_id,
                    "repair_frontier_available_after_replay": True,
                },
            )
        self.proposal_count += 1
        return await self.delegate.propose(request)


@dataclass(frozen=True)
class _StoredCandidateReplayBackend:
    replay_result: CandidateReplayResult
    source_run_id: str
    source_replay_path: str
    source_dataset_snapshot_fingerprint: str | None = None

    def proves_zero_budget_usage(self, stage: BudgetStage) -> bool:
        return stage is BudgetStage.PAIRED_REPLAY

    def replay_evidence_reuse_disposition(
        self,
    ) -> ReplayEvidenceReuseDisposition:
        return ReplayEvidenceReuseDisposition(
            kind=ReplayEvidenceDispositionKind.STORED_SOURCE_REUSE,
            source_run_id=self.source_run_id,
            source_replay_path=self.source_replay_path,
            source_dataset_snapshot_fingerprint=(
                self.source_dataset_snapshot_fingerprint
            ),
        )

    async def reuse_replay_evidence(
        self,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
        return self._validated_result(candidate)

    async def replay_candidate(
        self,
        request,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
        return self._validated_result(candidate)

    def _validated_result(
        self,
        candidate: CandidateVariant,
    ) -> CandidateReplayResult:
        if candidate.candidate_id != self.replay_result.request.candidate_id:
            raise ValueError(
                "stored replay candidate does not match selected candidate: "
                f"{self.replay_result.request.candidate_id} != {candidate.candidate_id}"
            )
        return self.replay_result


def _default_iteration_budget(
    *,
    apply_policy: str,
    explicit_iterations: int | None,
) -> int:
    if explicit_iterations is not None:
        if isinstance(explicit_iterations, bool) or explicit_iterations <= 0:
            raise ValueError("iterations must be positive")
        return explicit_iterations
    return 10 if _is_verified_apply_policy(apply_policy) else 1


def _candidate_mutation_repair_prompt(
    invalid_output: str,
    error: ValueError,
    *,
    original_prompt: str | None = None,
) -> str:
    diagnostic = (
        error.to_diagnostic()
        if isinstance(
            error,
            (
                CandidateProtocolError,
                CandidateSemanticValidationError,
            ),
        )
        else {
            "code": "candidate_protocol_invalid",
            "stage": "candidate_protocol",
            "failure_class": "candidate",
            "repairable": True,
        }
    )
    allowed_signal_ids = getattr(
        error,
        "allowed_improvement_signal_ids",
        (),
    )
    candidate_schema = (
        build_candidate_output_contract(tuple(allowed_signal_ids))
        if isinstance(allowed_signal_ids, (list, tuple))
        else dict(CANDIDATE_OUTPUT_CONTRACT)
    )
    payload = {
        "candidate_schema": candidate_schema,
        "diagnostics": [diagnostic],
        # Candidate packages commonly contain complete compiler/runtime sources.
        # Preserve a bounded full package when possible so representation repair
        # does not reconstruct missing file tails from a small prefix.
        "invalid_response": sanitize_text(invalid_output, max_chars=64_000),
    }
    if isinstance(original_prompt, str) and original_prompt:
        payload["original_generation_context"] = sanitize_source_text(
            original_prompt,
            max_chars=96_000,
            preserve_format=True,
        )
    repair_instruction = (
        "Repair representation only using the supplied schema and diagnostic. "
        if isinstance(error, CandidateProtocolError)
        else (
            "Repair candidate contract conformance using the supplied schema and "
            "diagnostic. Preserve valid package fields outside the diagnosed repair "
            "field. "
        )
    )
    if isinstance(error, CandidateSemanticValidationError) and isinstance(
        diagnostic.get("details"),
        Mapping,
    ):
        diagnostic_details = diagnostic["details"]
        repair_instruction += (
            "The nested repair_conformance result is executable feedback: repair "
            "every reported violation and missing operation in the submitted source. "
            "Function names and line numbers are locations, not requirements; renaming "
            "or moving the same invalid construct does not change its failure fingerprint. "
        )
        required_change = diagnostic_details.get("required_change")
        if isinstance(required_change, str) and required_change.strip():
            repair_instruction += (
                "Apply this analyzer-required source change literally before making "
                "any optional refactor: "
                + required_change
                + ". "
            )
        violation_constructs = diagnostic_details.get(
            "violation_constructs"
        )
        if isinstance(violation_constructs, (list, tuple)) and any(
            isinstance(item, str) and item.strip()
            for item in violation_constructs
        ):
            repair_instruction += (
                "Remove every named invalid construct from its reported source "
                "location: "
                + ", ".join(
                    str(item)
                    for item in violation_constructs
                    if isinstance(item, str) and item.strip()
                )
                + ". "
            )
        if error.code == "forbidden_fixture_probe_derivation":
            repair_instruction += (
                "Do not repair a compiler-side fixture assertion by adding another raw "
                "fixture walk. Remove that selector and leave a non-empty protocol-shape "
                "placeholder for framework canonical binding. If the diagnosed branch is "
                "runtime-side, read AWORLD_REPLAY_RESPONSE_INDEX and project one correlated "
                "record value; never use mapping keys, boolean metadata, or a concatenation "
                "of multiple scalars. Check bool before int/float and reject it. "
            )
    return repair_instruction + (
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
        judge_agent_path = Path(config.agent_path).expanduser().resolve()
        if not judge_agent_path.is_file():
            raise FileNotFoundError(
                f"judge agent does not exist: {judge_agent_path}"
            )
        try:
            judge_agent_source = judge_agent_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"judge agent is not valid UTF-8: {judge_agent_path}"
            ) from exc
        if not judge_agent_source.strip():
            raise ValueError(f"judge agent is empty: {judge_agent_path}")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_agent=str(judge_agent_path),
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


def _rerun_evaluator_from_stored_run(
    *,
    workspace_root: str | Path,
    from_run: str,
    agent: str | None,
    task: str | None,
    apply_policy: str,
    inferred_new_skill_policy: InferredNewSkillPolicy,
    evaluation_backend: EvaluationBackend | None,
    regression_backend: EvaluationBackend | None,
    regression_benchmarks: Iterable[str],
    challenger_backend: ChallengerBackend | None,
    challenger_enabled: bool,
    challenger_max_cases: int,
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None,
    min_eval_cases: int,
    judge_repetitions: int,
    judge_timeout_seconds: float | None,
    max_run_tokens: int | None,
    total_run_token_budget: int | None,
    per_attempt_replay_token_limit: int | None,
    max_run_cost_usd: float | Decimal | None,
    max_run_wall_seconds: float | Decimal | None,
    min_score_delta: float,
    auto_apply_target_types: tuple[str, ...],
    allow_generated_target_mutation: bool,
    allow_external_target_mutation: bool,
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None,
    replay_timeout_seconds: int,
    replay_total_timeout_seconds: int | None,
    replay_max_steps: int | None,
    replay_candidate_limit: int,
    baseline_replay_repetitions: int,
    candidate_replay_repetitions: int,
    replay_stability_margin: float,
    measurement_mode: MeasurementPolicyMode | str,
    measurement_primary_metric: str,
    measurement_minimum_effect: float,
    measurement_confidence_level: float,
    measurement_min_independent_cases: int,
    measurement_bootstrap_samples: int,
    measurement_zero_yield_patience: int,
    measurement_invalid_control_patience: int,
    measurement_maximum_interval_width: float | None,
    regression_replay_backend: CandidateReplayBackend | None,
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None,
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None,
    runtime_registry_compensator: Callable[
        [CandidateVariant, object | None], Any
    ]
    | None,
    runtime_skill_compensator: Callable[[CandidateVariant, object | None], Any]
    | None,
    progress_callback: Callable[[str, str], Any] | None,
    concurrency_policy: SelfEvolveConcurrencyPolicy,
    runner_type: Callable[..., Any],
    replay_backend_type: Callable[..., CandidateReplayBackend] = (
        AWorldCliCandidateReplayBackend
    ),
) -> Mapping[str, Any]:
    store = FilesystemSelfEvolveStore(workspace_root)
    source_run_path = _resolve_stored_run_path(store, from_run)
    source_run_id = source_run_path.name
    source_report = _load_json_mapping(source_run_path / "report.json")
    candidate_id = _stored_selected_candidate_id(source_report)
    candidate = _load_candidate_variant(source_run_path / "candidates" / f"{candidate_id}.json")
    replay_path = source_run_path / "replay" / candidate.candidate_id
    replay_result = load_candidate_replay_result(replay_path)

    _validate_agentic_rerun_ingestion_ref(source_run_path)
    source_config, split_seed = _source_config_from_stored_dataset_recipe(
        source_run_path / "dataset_recipe.json"
    )
    _validate_rerun_source_runtime_admission(
        source_config,
        apply_policy=apply_policy,
    )
    built_dataset = _load_stored_campaign_dataset(
        store=store,
        source_run_path=source_run_path,
    )
    if built_dataset is None:
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
    snapshot_reference = built_dataset.recipe.source.get(
        "campaign_dataset_snapshot"
    )
    source_dataset_snapshot_fingerprint = (
        str(snapshot_reference.get("snapshot_fingerprint"))
        if isinstance(snapshot_reference, Mapping)
        and isinstance(snapshot_reference.get("snapshot_fingerprint"), str)
        else None
    )
    trace_packs = tuple(
        case.trace_pack for case in built_dataset.cases if case.trace_pack is not None
    )
    stored_target_selection_report = _load_target_selection_report(
        source_run_path / "target_selection.json"
    )
    if (
        stored_target_selection_report is not None
        and stored_target_selection_report.target_intent
        == TargetMutationIntent.INFERRED_DRAFT_CREATION
    ):
        raise ValueError(
            "evaluator-only rerun cannot rebind a verified candidate package to a "
            "different run-owned draft path; rerun the full optimize flow"
        )
    target_adapter = _target_from_ref(
        candidate.target,
        workspace_root=workspace_root,
        allow_auto_apply=(
            apply_policy == "auto_verified"
            and candidate.target.target_type in auto_apply_target_types
        ),
    )
    target_selection_report = stored_target_selection_report
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
        target_intent=target_selection_report.target_intent,
    )
    target_provenance = (
        authoritative_resolution.provenance
        if authoritative_resolution.resolved
        else None
    )
    if _is_verified_apply_policy(apply_policy) and evaluation_backend is None:
        evaluation_backend = _evaluation_backend_from_judge_config(
            judge_config,
            workspace_root=workspace_root,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if _is_verified_apply_policy(apply_policy) and post_apply_evaluator is None:
        post_apply_evaluator = _default_post_apply_evaluator(target_adapter)
    resolved_regression_suites = resolve_regression_suites(
        regression_benchmarks,
        selection_dataset=built_dataset,
        base_dir=workspace_root,
    )
    if (
        not resolved_regression_suites
        and _is_verified_apply_policy(apply_policy)
    ):
        resolved_regression_suites = resolve_target_contract_regression_suite(
            target_type=target_adapter.identity.target_type,
            target_id=target_adapter.identity.target_id,
            target_path=target_adapter.identity.path,
            current_content=target_adapter.load_current_content(),
            target_fingerprint=target_adapter.fingerprint_current_content(),
            selection_dataset=built_dataset,
        )
    if regression_replay_backend is None:
        regression_replay_backend = replay_backend_type(
            concurrency_policy=concurrency_policy,
        )

    run_id = _rerun_cli_run_id(source_run_id, candidate.candidate_id)
    _emit_progress(
        progress_callback,
        "resume",
        f"Reusing replay artifacts from {source_run_id} for candidate {candidate.candidate_id}",
    )

    self_evolve_runner = runner_type(
        store=store,
        optimizer=_FixedCandidateOptimizer(
            candidate=candidate,
            source_run_id=source_run_id,
        ),
        evaluation_backend=evaluation_backend,
        regression_backend=regression_backend,
        regression_suites=resolved_regression_suites,
        challenger_backend=challenger_backend,
        challenger_enabled=challenger_enabled,
        challenger_max_cases=challenger_max_cases,
        post_apply_evaluator=post_apply_evaluator,
        min_score_delta=min_score_delta,
        max_iterations=1,
        min_eval_cases=min_eval_cases,
        judge_repetitions=judge_repetitions,
        max_run_tokens=max_run_tokens,
        total_run_token_budget=total_run_token_budget,
        per_attempt_replay_token_limit=per_attempt_replay_token_limit,
        max_run_cost_usd=max_run_cost_usd,
        max_run_wall_seconds=max_run_wall_seconds,
        auto_apply_target_types=auto_apply_target_types,
        allow_generated_target_mutation=allow_generated_target_mutation,
        allow_external_target_mutation=allow_external_target_mutation,
        inferred_new_skill_policy=inferred_new_skill_policy,
        replay_enabled=True,
        candidate_replay_backend=_StoredCandidateReplayBackend(
            replay_result=replay_result,
            source_run_id=source_run_id,
            source_replay_path=str(replay_path),
            source_dataset_snapshot_fingerprint=(
                source_dataset_snapshot_fingerprint
            ),
        ),
        regression_replay_backend=regression_replay_backend,
        replay_timeout_seconds=replay_timeout_seconds,
        replay_total_timeout_seconds=replay_total_timeout_seconds,
        replay_max_steps=replay_max_steps,
        replay_candidate_limit=replay_candidate_limit,
        baseline_replay_repetitions=baseline_replay_repetitions,
        candidate_replay_repetitions=candidate_replay_repetitions,
        replay_stability_margin=replay_stability_margin,
        measurement_mode=measurement_mode,
        measurement_primary_metric=measurement_primary_metric,
        measurement_minimum_effect=measurement_minimum_effect,
        measurement_confidence_level=measurement_confidence_level,
        measurement_min_independent_cases=measurement_min_independent_cases,
        measurement_bootstrap_samples=measurement_bootstrap_samples,
        measurement_zero_yield_patience=measurement_zero_yield_patience,
        measurement_invalid_control_patience=(
            measurement_invalid_control_patience
        ),
        measurement_maximum_interval_width=(
            measurement_maximum_interval_width
        ),
        replay_agent=agent,
        runtime_registry_refresher=runtime_registry_refresher,
        runtime_skill_activator=runtime_skill_activator,
        runtime_registry_compensator=runtime_registry_compensator,
        runtime_skill_compensator=runtime_skill_compensator,
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
            if result.run.status.value == "succeeded"
            and _is_verified_apply_policy(apply_policy)
            else None
        ),
        "selected_candidate_id": selected_candidate_id,
        "run_id": result.run.run_id,
        "status": result.run.status.value,
        "source_run_id": source_run_id,
        "replay_path": str(replay_path),
    }
    _add_post_apply_summary(summary, report)
    target_selection_path = run_path / "target_selection.json"
    if target_selection_path.exists():
        summary["target_selection_path"] = str(target_selection_path)
    target_provenance_path = run_path / "target_provenance.json"
    if target_provenance_path.exists():
        summary["target_provenance_path"] = str(target_provenance_path)
    evaluator_report_paths = report.get("evaluator_report_paths")
    if isinstance(evaluator_report_paths, list):
        summary["evaluator_report_paths"] = evaluator_report_paths
    if selected_candidate_id is not None:
        regression_evidence_path = (
            run_path
            / "regression"
            / "evidence"
            / f"{selected_candidate_id}.json"
        )
        if regression_evidence_path.is_file():
            summary["regression_evidence_path"] = str(
                regression_evidence_path
            )
    gate_results = report.get("gate_results")
    if isinstance(gate_results, list):
        summary["gate_results"] = gate_results
    return summary


def _add_post_apply_summary(
    summary: dict[str, Any],
    report: Mapping[str, Any],
) -> None:
    post_apply = report.get("post_apply")
    if not isinstance(post_apply, Mapping):
        return
    summary["release_state"] = post_apply.get("release_state")
    summary["published"] = post_apply.get("published") is True
    verified_target_path = post_apply.get("verified_target_path")
    if isinstance(verified_target_path, str) and verified_target_path:
        summary["verified_target_path"] = verified_target_path


def _content_fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


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
        if _report_has_shared_measurement_failure(report):
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
                    # Prior-run cases are bounded optimizer feedback, not user
                    # tasks.  Keep them in the trainable context while routing
                    # every executable replay panel through the shared
                    # ``_is_replayable_user_task_case`` predicate.
                    "framework_generated": True,
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
        key: public_diagnostic_projection(item)
        for key, item in payload.items()
    }




def _campaign_target_matches(
    target: SelfEvolveTargetRef,
    expected: Mapping[str, Any] | None,
) -> bool:
    if expected is None:
        return True
    return (
        expected.get("target_type") == target.target_type
        and expected.get("target_id") == target.target_id
    )








def _measurement_pending_candidate_checkpoint(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    report: Mapping[str, Any],
) -> MeasurementResumeCheckpointV1 | None:
    """Admit only a complete authoritative v2 continuation checkpoint."""

    if not _report_has_shared_measurement_failure(report):
        return None
    run_path = store.run_path(run_id)
    candidate_ids: list[str] = []
    expected_fingerprints: dict[str, str] = {}
    for key in ("campaign_failure_attribution", "rejection_attribution"):
        attribution = report.get(key)
        if not isinstance(attribution, Mapping):
            continue
        candidate_id = attribution.get("resume_candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            candidate_ids.append(candidate_id)
            fingerprint = attribution.get(
                "resume_candidate_package_fingerprint"
            )
            if isinstance(fingerprint, str) and fingerprint:
                expected_fingerprints[candidate_id] = fingerprint
    selected_candidate_id = report.get("selected_candidate_id")
    if isinstance(selected_candidate_id, str) and selected_candidate_id:
        candidate_ids.append(selected_candidate_id)
    raw_candidate_ids = report.get("candidate_ids")
    if isinstance(raw_candidate_ids, (list, tuple)):
        normalized = [
            item for item in raw_candidate_ids if isinstance(item, str) and item
        ]
        if len(normalized) == 1:
            candidate_ids.extend(normalized)
    population = report.get("population")
    if isinstance(population, Mapping):
        screening_items: list[Mapping[str, Any]] = []
        for key in ("screening", "conformance"):
            item = population.get(key)
            if isinstance(item, Mapping):
                screening_items.append(item)
        for key in ("screening_iterations", "conformance_iterations"):
            raw_items = population.get(key)
            if isinstance(raw_items, list):
                screening_items.extend(
                    item for item in raw_items if isinstance(item, Mapping)
                )
        for item in screening_items:
            attempts = item.get("attempts")
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    continue
                details = attempt.get("details")
                if not isinstance(details, Mapping):
                    continue
                if not (
                    details.get("failure_class") == "measurement"
                    and details.get("failure_owner")
                    in {"framework", "infrastructure", "evaluation_harness"}
                    and details.get("failure_scope") == "shared_run"
                ):
                    continue
                candidate_id = details.get("resume_candidate_id")
                if not isinstance(candidate_id, str) or not candidate_id:
                    candidate_id = attempt.get("candidate_id")
                if not isinstance(candidate_id, str) or not candidate_id:
                    continue
                candidate_ids.append(candidate_id)
                fingerprint = details.get(
                    "resume_candidate_package_fingerprint"
                )
                if isinstance(fingerprint, str) and fingerprint:
                    expected_fingerprints[candidate_id] = fingerprint
    for candidate_id in dict.fromkeys(candidate_ids):
        candidate_path = run_path / "candidates" / f"{candidate_id}.json"
        if not candidate_path.is_file() or candidate_path.is_symlink():
            continue
        try:
            candidate = _load_candidate_variant(candidate_path)
            fingerprint = candidate_package_fingerprint(candidate)
        except (OSError, TypeError, ValueError):
            continue
        expected = expected_fingerprints.get(candidate_id)
        if expected is not None and expected != fingerprint:
            continue
        checkpoint = discover_measurement_resume_checkpoint(
            store,
            run_id=run_id,
            candidate_id=candidate_id,
            candidate_fingerprint=fingerprint,
        )
        if checkpoint is not None:
            return checkpoint
    return None


def _paired_replay_pending_candidate_checkpoint(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    report: Mapping[str, Any],
) -> PairedReplayResumeCheckpointV1 | None:
    """Admit a typed continuation only for a safe progressive replay timeout."""

    for key in ("campaign_failure_attribution", "rejection_attribution"):
        attribution = report.get(key)
        if not isinstance(attribution, Mapping):
            continue
        if not (
            attribution.get("code") == "replay_total_timeout"
            and attribution.get("failure_class") == "measurement"
            and attribution.get("failure_owner")
            in {"framework", "infrastructure", "evaluation_harness"}
            and attribution.get("failure_scope") == "shared_run"
            and attribution.get("repairable") is True
            and attribution.get("resume_safe") is True
            and attribution.get("next_action") == "continue_measurement"
        ):
            continue
        candidate_id = attribution.get("resume_candidate_id")
        fingerprint = attribution.get("resume_candidate_package_fingerprint")
        if not (
            isinstance(candidate_id, str)
            and candidate_id
            and isinstance(fingerprint, str)
            and fingerprint
        ):
            continue
        checkpoint = discover_paired_replay_resume_checkpoint(
            store,
            run_id=run_id,
            candidate_id=candidate_id,
            verified_candidate_package_fingerprint=fingerprint,
        )
        if checkpoint is not None:
            return checkpoint
    member_timeout = any(
        isinstance(report.get(key), Mapping)
        and report[key].get("code") == "replay_member_phase_timeout"
        and report[key].get("failure_owner") == "framework"
        and report[key].get("failure_scope") == "member"
        and report[key].get("repairable") is True
        for key in ("campaign_failure_attribution", "rejection_attribution")
    )
    if not member_timeout:
        return None
    raw_candidate_ids = report.get("candidate_ids")
    candidate_ids = tuple(
        dict.fromkeys(
            item
            for item in (
                raw_candidate_ids
                if isinstance(raw_candidate_ids, (list, tuple))
                else ()
            )
            if isinstance(item, str) and item
        )
    )
    if len(candidate_ids) != 1:
        return None
    candidate_id = candidate_ids[0]
    request_path = (
        store.run_path(run_id) / "replay" / candidate_id / "request.json"
    )
    try:
        request = _load_json_mapping(request_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    fingerprint = request.get("verified_candidate_package_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        return None
    return discover_paired_replay_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=candidate_id,
        verified_candidate_package_fingerprint=fingerprint,
    )




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
        if isinstance(failed_gates, list) and {
            "evidence_quality",
            "replay_evaluator_admission",
        }.intersection(str(gate) for gate in failed_gates):
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
        path, release_path = _validated_run_owned_draft_paths(
            target_ref,
            workspace_root=workspace_root,
        )
        return DraftSkillTextTarget(
            path,
            target_id=target_ref.target_id,
            release_path=release_path,
            allow_auto_apply=allow_auto_apply,
        )
    return _skill_target_from_id(
        target_ref.target_id,
        workspace_root=workspace_root,
        allow_auto_apply=allow_auto_apply,
    )


def _validated_run_owned_draft_paths(
    target_ref: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path,
) -> tuple[Path, Path]:
    if not target_ref.path:
        raise ValueError("inferred skill draft requires a path")
    workspace = Path(workspace_root).resolve()
    path = Path(target_ref.path).absolute()
    try:
        relative = path.relative_to(workspace)
        path.resolve(strict=False).relative_to(workspace)
    except ValueError as exc:
        raise ValueError("inferred skill draft escapes the workspace") from exc
    expected = ("draft_target", target_ref.target_id, "SKILL.md")
    if (
        len(relative.parts) != 6
        or relative.parts[:2] != (".aworld", "self_evolve")
        or relative.parts[3:] != expected
        or not relative.parts[2]
    ):
        raise ValueError("inferred skill draft is not owned by exactly one run")
    if _path_has_symlink_component(workspace, path):
        raise ValueError("inferred skill draft path traverses a symlink")
    if path.exists() or path.is_symlink():
        raise FileExistsError("stale inferred skill draft cannot be reused")
    release_path = (
        workspace / "aworld-skills" / target_ref.target_id / "SKILL.md"
    )
    if release_path.parent.exists() or release_path.parent.is_symlink():
        raise FileExistsError("new-skill release path already exists")
    return path, release_path


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
    selected_decisions = tuple(
        decision for decision in decisions if decision.report.selected_target is not None
    )
    selected_keys = {
        (
            decision.report.selected_target.target_type,
            decision.report.selected_target.target_id,
            decision.target_intent,
            decision.report.capability_fingerprint,
        )
        for decision in selected_decisions
        if decision.report.selected_target is not None
    }
    if len(selected_keys) > 1:
        evidence_step_ids = tuple(
            dict.fromkeys(
                evidence_id
                for decision in decisions
                for evidence_id in decision.report.evidence_step_ids
            )
        )
        report = TargetSelectionReport(
            selected_target=None,
            confidence=0.0,
            evidence_step_ids=evidence_step_ids,
            failure_category="no_target",
            signals=("conflicting_target_intents",),
            no_target_reason="trajectory members disagree on target capability intent",
            diagnostics={
                "conflicting_target_count": len(selected_keys),
                "pack_ids": [
                    pack_id
                    for decision in decisions
                    for pack_id in _target_selection_pack_ids(decision.report)
                ],
            },
            selection_origin=TargetSelectionOrigin.INFERRED,
        )
        resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason=report.no_target_reason or "conflicting target intents",
        )
        return TargetSelectionDecision(
            report=report,
            provenance_resolution=resolution,
            selection_origin=TargetSelectionOrigin.INFERRED,
            target_intent=None,
        )
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
        best_decision.target_intent,
        best_report.capability_fingerprint,
    )
    contributing_reports = tuple(
        decision.report
        for decision in decisions
        if decision.report.selected_target is not None
        and (
            decision.report.selected_target.target_type,
            decision.report.selected_target.target_id,
            decision.target_intent,
            decision.report.capability_fingerprint,
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
        and decision.target_intent == best_decision.target_intent
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
        target_intent=(
            best_decision.target_intent if consistent_authorization else None
        ),
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
    cases_by_id = {case.case_id: case for case in dataset.cases}
    groups: dict[str, dict[str, object]] = {}
    for trace_pack in trace_packs:
        report = infer((trace_pack,), workspace_root=workspace_root).report
        case = cases_by_id.get(trace_pack.task_id)
        context_status = _trajectory_case_context_status(case)
        recovery_opportunity = trace_pack_recovery_opportunity(trace_pack)
        opportunity_tier = int(recovery_opportunity["tier"])
        opportunity_kind = str(recovery_opportunity["kind"])
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
                "context_complete_case_ids": [],
                "context_incomplete_case_ids": [],
                "recovery_opportunity_case_count": 0,
                "max_recovery_opportunity_tier": 0,
                "recovery_opportunity_kinds": {},
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
        context_key = (
            "context_complete_case_ids"
            if context_status == "complete"
            else "context_incomplete_case_ids"
        )
        context_case_ids = group[context_key]
        if isinstance(context_case_ids, list):
            context_case_ids.append(trace_pack.task_id)
        # Only replayable members contribute recovery opportunity to ranking.
        # An unreplayable failure is an adaptation problem, not candidate work.
        if context_status == "complete" and opportunity_tier > 0:
            group["recovery_opportunity_case_count"] = (
                int(group["recovery_opportunity_case_count"]) + 1
            )
            group["max_recovery_opportunity_tier"] = max(
                int(group["max_recovery_opportunity_tier"]),
                opportunity_tier,
            )
            kinds = group["recovery_opportunity_kinds"]
            if isinstance(kinds, dict):
                kinds[opportunity_kind] = int(kinds.get(opportunity_kind, 0)) + 1

    ranked_groups = sorted(
        groups.values(),
        key=_trajectory_group_rank_key,
        reverse=True,
    )
    selected_group = ranked_groups[0]
    selected_group_case_ids = tuple(
        str(case_id) for case_id in selected_group.get("case_ids", ()) if case_id
    )
    context_complete_case_ids = tuple(
        str(case_id)
        for case_id in selected_group.get("context_complete_case_ids", ())
        if case_id
    )
    selected_case_ids = (
        context_complete_case_ids
        if context_complete_case_ids
        else selected_group_case_ids
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
        selected_case_ids=selected_case_ids,
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
    intent = report.target_intent.value if report.target_intent is not None else "unknown"
    fingerprint = report.capability_fingerprint or "none"
    return (
        f"{report.selected_target.target_type}:{report.selected_target.target_id}:"
        f"{intent}:{fingerprint}"
    )


def _group_average_confidence(group: Mapping[str, object]) -> float:
    case_ids = group.get("case_ids")
    count = len(case_ids) if isinstance(case_ids, list) else 0
    if count <= 0:
        return 0.0
    return float(group.get("confidence_sum") or 0.0) / count


def _group_confidence_bucket(group: Mapping[str, object]) -> float:
    return round(_group_average_confidence(group), 2)


def _trajectory_case_context_status(case: EvalCase | None) -> str:
    snapshot = case.context_snapshot if case is not None else None
    status = getattr(snapshot, "context_status", None)
    return "incomplete" if status == "incomplete" else "complete"


def _group_context_completeness_rate(group: Mapping[str, object]) -> float:
    complete = group.get("context_complete_case_ids")
    incomplete = group.get("context_incomplete_case_ids")
    complete_count = len(complete) if isinstance(complete, list) else 0
    incomplete_count = len(incomplete) if isinstance(incomplete, list) else 0
    total = complete_count + incomplete_count
    return round(complete_count / total, 6) if total else 0.0


def _group_context_completeness_bucket(group: Mapping[str, object]) -> float:
    return round(_group_context_completeness_rate(group), 2)


def _trajectory_group_rank_key(
    group: Mapping[str, object],
) -> tuple[object, ...]:
    """Rank replayable improvement value before target lookup convenience."""

    # Context completeness is an eligibility boundary. Within that frontier,
    # opportunity precedes target availability and diagnosis confidence so a
    # high-value new capability is not hidden by a shallow existing-target
    # match.
    return (
        bool(group.get("context_complete_case_ids")),
        int(group.get("max_recovery_opportunity_tier") or 0),
        int(group.get("recovery_opportunity_case_count") or 0),
        _group_context_completeness_bucket(group),
        bool(group.get("has_target")),
        _group_confidence_bucket(group),
        len(group.get("context_complete_case_ids") or ()),
        int(group.get("target_priority") or 0),
        _group_average_confidence(group),
        str(group.get("group_id") or ""),
    )


def _trajectory_log_grouping_report(
    ranked_groups: list[dict[str, object]],
    *,
    selected_group_id: str,
    selected_case_ids: tuple[str, ...],
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
                "context_complete_case_ids": list(
                    group.get("context_complete_case_ids") or ()
                ),
                "context_incomplete_case_ids": list(
                    group.get("context_incomplete_case_ids") or ()
                ),
                "context_completeness_rate": _group_context_completeness_rate(
                    group
                ),
                "recovery_opportunity_case_count": int(
                    group.get("recovery_opportunity_case_count") or 0
                ),
                "max_recovery_opportunity_tier": int(
                    group.get("max_recovery_opportunity_tier") or 0
                ),
                "recovery_opportunity_kinds": dict(
                    group.get("recovery_opportunity_kinds") or {}
                ),
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
    selected_group_case_ids = list(selected_group.get("case_ids") or ())
    selected_case_count = len(selected_case_ids)
    excluded_context_incomplete_case_ids = [
        case_id
        for case_id in selected_group.get("context_incomplete_case_ids") or ()
        if case_id not in selected_case_ids
    ]
    low_dataset_support = selected_case_count <= 1 and largest_group_size > selected_case_count
    return {
        "auto_grouped": True,
        "strategy": "inferred_target",
        "ranking_strategy": "recovery_opportunity_then_context_completeness",
        "group_count": len(group_summaries),
        "selected_group_id": selected_group_id,
        "selected_group_case_ids": selected_group_case_ids,
        "selected_case_ids": list(selected_case_ids),
        "selected_group_case_count": len(selected_group_case_ids),
        "selected_case_count": selected_case_count,
        "largest_group_case_count": largest_group_size,
        "low_dataset_support": low_dataset_support,
        "context_filtered": bool(excluded_context_incomplete_case_ids),
        "excluded_context_incomplete_case_ids": (
            excluded_context_incomplete_case_ids
        ),
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


# Historical import seams remain aliases while their implementation lives inward.
_default_post_apply_evaluator = default_post_apply_evaluator
_default_new_skill_registry_refresher = default_new_skill_registry_refresher
_default_new_skill_registry_compensator = default_new_skill_registry_compensator
_explicit_target_selection_report = explicit_target_selection_report
_target_runtime_skill_path = target_runtime_skill_path


def _no_evidence_target_selection_report(source_kind: str) -> TargetSelectionReport:
    return TargetSelectionReport(
        selected_target=None,
        confidence=0.0,
        evidence_step_ids=(),
        failure_category="no_target",
        signals=("missing_trajectory_evidence", "target_evidence_missing"),
        no_target_reason="target inference requires trajectory evidence",
        diagnostics={
            "source_kind": source_kind,
            "reason_code": "target_evidence_missing",
        },
        selection_origin=TargetSelectionOrigin.INFERRED,
    )


def _inferred_target_admitted_for_auto_apply(
    decision: TargetSelectionDecision,
) -> bool:
    if not decision.provenance_resolution.resolved:
        return False
    if decision.target_intent == TargetMutationIntent.INFERRED_DRAFT_CREATION:
        report = decision.report
        return bool(
            report.evidence_step_ids
            and isinstance(report.capability_fingerprint, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", report.capability_fingerprint)
        )
    report = decision.report
    return report.confidence >= 0.9 and "low_confidence" not in report.signals


def _materialize_run_owned_draft_decision(
    decision: TargetSelectionDecision,
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    workspace_root: str | Path,
    policy: InferredNewSkillPolicy,
) -> TargetSelectionDecision:
    report = decision.report
    target = report.selected_target
    if (
        decision.target_intent != TargetMutationIntent.INFERRED_DRAFT_CREATION
        or target is None
    ):
        return decision
    if target.path is not None:
        return _blocked_inferred_target_selection_decision(
            decision,
            reason="inferred new-skill intent must remain path-free until the run exists",
            signal="inferred_draft_preowned_path_blocked",
        )
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", target.target_id):
        return _blocked_inferred_target_selection_decision(
            decision,
            reason="inferred new-skill id is invalid",
            signal="inferred_draft_invalid_id",
        )
    inventory = build_default_target_inventory(workspace_root)
    if inventory.find_all("skill", target.target_id):
        return _blocked_inferred_target_selection_decision(
            decision,
            reason="an inventory target appeared after capability-gap inference",
            signal="inferred_draft_inventory_collision",
        )
    workspace = Path(workspace_root).resolve()
    run_root = store.run_path(run_id).absolute()
    draft_path = run_root / "draft_target" / target.target_id / "SKILL.md"
    release_root = workspace / "aworld-skills" / target.target_id
    if _path_has_symlink_component(workspace, draft_path):
        return _blocked_inferred_target_selection_decision(
            decision,
            reason="inferred skill draft path traverses a symlink",
            signal="inferred_draft_symlink_blocked",
        )
    try:
        run_root.relative_to(workspace)
        draft_path.resolve(strict=False).relative_to(run_root)
    except ValueError:
        return _blocked_inferred_target_selection_decision(
            decision,
            reason="inferred skill draft path escapes the current run",
            signal="inferred_draft_path_escape",
        )
    if draft_path.exists() or draft_path.is_symlink():
        return _blocked_inferred_target_selection_decision(
            decision,
            reason="a stale draft already exists for this run",
            signal="inferred_draft_stale_collision",
        )
    if release_root.exists() or release_root.is_symlink():
        return _blocked_inferred_target_selection_decision(
            decision,
            reason="new-skill release path already exists",
            signal="inferred_draft_release_collision",
        )
    materialized_target = replace(target, path=str(draft_path))
    diagnostics = dict(report.diagnostics or {})
    diagnostics.update(
        {
            "draft_path": str(draft_path),
            "draft_status": "run_owned",
            "promotion_policy": policy.value,
            "promotion_status": "pending",
            "release_path": str(release_root / "SKILL.md"),
        }
    )
    materialized_report = replace(
        report,
        selected_target=materialized_target,
        diagnostics=diagnostics,
    )
    return build_target_selection_decision(
        materialized_report,
        inventory=inventory,
        selection_origin=TargetSelectionOrigin.INFERRED,
        workspace_root=workspace,
        target_intent=TargetMutationIntent.INFERRED_DRAFT_CREATION,
    )


def _path_has_symlink_component(root: Path, path: Path) -> bool:
    lexical_root = root.absolute()
    lexical_path = path.absolute()
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError:
        return True
    current = lexical_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _blocked_inferred_target_selection_decision(
    decision: TargetSelectionDecision,
    *,
    reason: str,
    signal: str,
) -> TargetSelectionDecision:
    diagnostics = dict(decision.report.diagnostics or {})
    if decision.report.selected_target is not None:
        diagnostics["blocked_selected_target"] = to_json_dict(
            decision.report.selected_target
        )
    blocked_report = replace(
        decision.report,
        selected_target=None,
        signals=tuple(dict.fromkeys((*decision.report.signals, signal))),
        no_target_reason=reason,
        diagnostics=diagnostics,
        provenance_status=TargetProvenanceStatus.UNRESOLVED,
        provenance_reason=reason,
    )
    resolution = TargetProvenanceResolution(
        status=TargetProvenanceStatus.UNRESOLVED,
        provenance=None,
        reason=reason,
    )
    return TargetSelectionDecision(
        report=blocked_report,
        provenance_resolution=resolution,
        selection_origin=decision.selection_origin,
        target_intent=None,
    )


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
        target_intent=report.target_intent,
        capability_fingerprint=report.capability_fingerprint,
    )


def _empty_run_budget_report(
    *,
    max_run_tokens: int | None,
    total_run_token_budget: int | None,
    max_run_cost_usd: float | Decimal | None,
    max_run_wall_seconds: float | Decimal | None,
    run_budget_context_type: Callable[..., Any],
) -> dict[str, object]:
    """Build typed zero-usage telemetry for runs rejected before execution."""

    effective_token_budget = (
        max_run_tokens
        if total_run_token_budget is None and max_run_tokens is not None
        else total_run_token_budget
    )
    return run_budget_context_type(
        ledger=RunBudgetLedger(
            BudgetCeilings(
                total_tokens=effective_token_budget,
                total_cost_usd=max_run_cost_usd,
                wall_seconds=max_run_wall_seconds,
            )
        ),
        cold_start_by_stage={},
    ).to_dict()


def _persist_no_target_cli_result(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    dataset: SelfEvolveDataset,
    target_selection_report: TargetSelectionReport,
    apply_policy: str,
    budget_report: Mapping[str, object],
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
        "budget": dict(budget_report),
    }
    report["artifact_retention"] = _artifact_retention_report(store, run_id)
    report_path = store.write_report(run_id, report)
    _acknowledge_reported_artifact_retention(store, run_id, report)
    summary = {
        "report_path": str(report_path),
        "target_selection_path": str(target_selection_path),
        "best_candidate_id": None,
        "run_id": run_id,
        "status": run.status.value,
    }
    summary.update(_dataset_ingestion_summary(store, dataset))
    return summary


def _persist_unsupported_target_cli_result(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    dataset: SelfEvolveDataset,
    target_selection_report: TargetSelectionReport,
    target_provenance: TargetProvenance | None,
    apply_policy: str,
    reason: str,
    budget_report: Mapping[str, object],
) -> Mapping[str, Any]:
    if target_selection_report.selected_target is None:
        return _persist_no_target_cli_result(
            store=store,
            run_id=run_id,
            dataset=dataset,
            target_selection_report=target_selection_report,
            apply_policy=apply_policy,
            budget_report=budget_report,
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
        "budget": dict(budget_report),
    }
    report["artifact_retention"] = _artifact_retention_report(store, run_id)
    report_path = store.write_report(run_id, report)
    _acknowledge_reported_artifact_retention(store, run_id, report)
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
    summary.update(_dataset_ingestion_summary(store, dataset))
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
    *,
    campaign_id: str | None = None,
    campaign_cycle: int | None = None,
) -> str:
    if campaign_id is not None or campaign_cycle is not None:
        if (
            not isinstance(campaign_id, str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}", campaign_id)
            or isinstance(campaign_cycle, bool)
            or not isinstance(campaign_cycle, int)
            or campaign_cycle <= 0
        ):
            raise ValueError("campaign run identity is invalid")
        return f"{campaign_id}-cycle-{campaign_cycle:03d}"
    digest = hashlib.sha256(
        json.dumps(
            {
                "target_key": target_key,
                "dataset": dataset,
                "from_session": from_session,
                "from_trajectory": from_trajectory,
                "from_trajectory_set": from_trajectory_set,
                "batch_config": batch_config,
                "iterations": iterations,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        "cli-"
        + digest[:16]
    )


def execute_cli_optimization(
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
    inferred_new_skill_policy: InferredNewSkillPolicy | str = InferredNewSkillPolicy.AUTO_VERIFIED,
    evaluation_backend: EvaluationBackend | None = None,
    regression_backend: EvaluationBackend | None = None,
    regression_benchmarks: Iterable[str] = (),
    challenger_backend: ChallengerBackend | None = None,
    challenger_enabled: bool = True,
    challenger_max_cases: int = DEFAULT_CHALLENGE_CASES,
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
    min_eval_cases: int = 30,
    judge_repetitions: int = 3,
    judge_timeout_seconds: float | None = 300.0,
    max_run_tokens: int | None = None,
    total_run_token_budget: int | None = None,
    per_attempt_replay_token_limit: int | None = None,
    max_run_cost_usd: float | Decimal | None = None,
    max_run_wall_seconds: float | Decimal | None = None,
    candidate_generation_tokens_per_unit: int | None = None,
    candidate_generation_cost_usd_per_unit: float | Decimal | None = None,
    candidate_generation_wall_seconds_per_unit: float | Decimal | None = None,
    candidate_screening_tokens_per_unit: int | None = None,
    candidate_screening_cost_usd_per_unit: float | Decimal | None = None,
    candidate_screening_wall_seconds_per_unit: float | Decimal | None = None,
    replay_tokens_per_unit: int | None = None,
    replay_cost_usd_per_unit: float | Decimal | None = None,
    replay_wall_seconds_per_unit: float | Decimal | None = None,
    evaluation_tokens_per_unit: int | None = None,
    evaluation_cost_usd_per_unit: float | Decimal | None = None,
    evaluation_wall_seconds_per_unit: float | Decimal | None = None,
    deprecated_config_mappings: Iterable[str] | Mapping[str, str] | None = None,
    min_score_delta: float = 0.0,
    auto_apply_target_types: tuple[str, ...] = ("skill",),
    allow_generated_target_mutation: bool = False,
    allow_external_target_mutation: bool = False,
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None = None,
    mutation_model_config: ModelConfig | None = None,
    replay_enabled: bool = False,
    candidate_replay_backend: CandidateReplayBackend | None = None,
    regression_replay_backend: CandidateReplayBackend | None = None,
    replay_timeout_seconds: int = 600,
    replay_total_timeout_seconds: int | None = None,
    replay_max_steps: int | None = None,
    replay_candidate_limit: int = 2,
    candidate_screening_max_cases: int = 3,
    max_generated_candidates: int = 6,
    max_full_evaluation_candidates: int = 3,
    max_score_tiebreak_candidates: int = 1,
    baseline_replay_repetitions: int = 1,
    candidate_replay_repetitions: int = 1,
    replay_repetitions_explicit: bool = False,
    replay_stability_margin: float = 0.0,
    measurement_mode: MeasurementPolicyMode | str | None = None,
    measurement_primary_metric: str = "task_success",
    measurement_minimum_effect: float = 0.0,
    measurement_confidence_level: float = 0.95,
    measurement_min_independent_cases: int = 2,
    measurement_bootstrap_samples: int = 2_000,
    measurement_zero_yield_patience: int = 2,
    measurement_invalid_control_patience: int = 2,
    measurement_maximum_interval_width: float | None = None,
    replay_adaptation_compiler: ReplayAdaptationCompiler | None = None,
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
    runtime_registry_compensator: Callable[
        [CandidateVariant, object | None], Any
    ]
    | None = None,
    runtime_skill_compensator: Callable[[CandidateVariant, object | None], Any]
    | None = None,
    progress_callback: Callable[[str, str], Any] | None = None,
    concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
    campaign_id: str | None = None,
    campaign_cycle: int | None = None,
    campaign_prior_run_ids: Iterable[str] | None = None,
    campaign_scheduler_checkpoint_run_ids: Iterable[str] | None = None,
    campaign_expected_target: Mapping[str, Any] | None = None,
    campaign_measurement_pending_run_id: str | None = None,
    campaign_measurement_pending_candidate_id: str | None = None,
    from_source: str | None = None,
    source_ingestor: str | None = None,
    source_manifest: str | None = None,
    semantic_evidence_approval: str | None = None,
    semantic_qualification_report: str | None = None,
    ingestion_model_config: ModelConfig | None = None,
    ingestion_only: bool = False,
    frozen_ingestion_id: str | None = None,
    ingestion_registry: IngestionRegistry | None = None,
    skill_evolution_contract: (
        Mapping[str, object] | SkillEvolutionContract | None
    ) = None,
    runtime: CliOrchestrationRuntime,
) -> Mapping[str, Any]:
    effective_concurrency_policy = concurrency_policy or SelfEvolveConcurrencyPolicy()
    typed_skill_evolution_contract = (
        skill_evolution_contract
        if isinstance(skill_evolution_contract, SkillEvolutionContract)
        else SkillEvolutionContract.from_dict(skill_evolution_contract)
        if isinstance(skill_evolution_contract, Mapping)
        else None
    )
    if (
        typed_skill_evolution_contract is not None
        and typed_skill_evolution_contract.required_stable_cycles > 1
        and campaign_id is None
    ):
        raise ValueError(
            "multi-cycle Skill evolution contract requires a Campaign"
        )
    typed_new_skill_policy = InferredNewSkillPolicy(inferred_new_skill_policy)
    if apply_policy not in {"proposal", "auto_verified", "verified_only"}:
        raise ValueError(f"unsupported apply policy: {apply_policy}")
    measurement_mode = _effective_cli_measurement_mode(
        measurement_mode,
        apply_policy=apply_policy,
        replay_enabled=replay_enabled,
    )
    effective_iteration_budget = _default_iteration_budget(
        apply_policy=apply_policy,
        explicit_iterations=iterations,
    )
    if rerun_evaluator:
        if not from_run:
            raise ValueError("--rerun-evaluator requires --from-run")
        return _rerun_evaluator_from_stored_run(
            runner_type=runtime.runner_type,
            replay_backend_type=runtime.replay_backend_type,
            workspace_root=workspace_root,
            from_run=from_run,
            agent=agent,
            task=task,
            apply_policy=apply_policy,
            inferred_new_skill_policy=typed_new_skill_policy,
            evaluation_backend=evaluation_backend,
            regression_backend=regression_backend,
            regression_benchmarks=regression_benchmarks,
            challenger_backend=challenger_backend,
            challenger_enabled=challenger_enabled,
            challenger_max_cases=challenger_max_cases,
            post_apply_evaluator=post_apply_evaluator,
            min_eval_cases=min_eval_cases,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
            max_run_tokens=max_run_tokens,
            total_run_token_budget=total_run_token_budget,
            per_attempt_replay_token_limit=per_attempt_replay_token_limit,
            max_run_cost_usd=max_run_cost_usd,
            max_run_wall_seconds=max_run_wall_seconds,
            min_score_delta=min_score_delta,
            auto_apply_target_types=auto_apply_target_types,
            allow_generated_target_mutation=allow_generated_target_mutation,
            allow_external_target_mutation=allow_external_target_mutation,
            judge_config=judge_config,
            replay_timeout_seconds=replay_timeout_seconds,
            replay_total_timeout_seconds=replay_total_timeout_seconds,
            replay_max_steps=replay_max_steps,
            replay_candidate_limit=replay_candidate_limit,
            baseline_replay_repetitions=baseline_replay_repetitions,
            candidate_replay_repetitions=candidate_replay_repetitions,
            replay_stability_margin=replay_stability_margin,
            measurement_mode=measurement_mode,
            measurement_primary_metric=measurement_primary_metric,
            measurement_minimum_effect=measurement_minimum_effect,
            measurement_confidence_level=measurement_confidence_level,
            measurement_min_independent_cases=(
                measurement_min_independent_cases
            ),
            measurement_bootstrap_samples=measurement_bootstrap_samples,
            measurement_zero_yield_patience=measurement_zero_yield_patience,
            measurement_invalid_control_patience=(
                measurement_invalid_control_patience
            ),
            measurement_maximum_interval_width=(
                measurement_maximum_interval_width
            ),
            regression_replay_backend=regression_replay_backend,
            runtime_registry_refresher=runtime_registry_refresher,
            runtime_skill_activator=runtime_skill_activator,
            runtime_registry_compensator=runtime_registry_compensator,
            runtime_skill_compensator=runtime_skill_compensator,
            progress_callback=progress_callback,
            concurrency_policy=effective_concurrency_policy,
        )
    pre_execution_budget_report = _empty_run_budget_report(
        run_budget_context_type=runtime.run_budget_context_type,
        max_run_tokens=max_run_tokens,
        total_run_token_budget=total_run_token_budget,
        max_run_cost_usd=max_run_cost_usd,
        max_run_wall_seconds=max_run_wall_seconds,
    )
    _validate_eval_source_request(
        dataset=dataset,
        from_session=from_session,
        from_trajectory=from_trajectory,
        from_trajectory_set=from_trajectory_set,
        batch_config=batch_config,
        current_trajectory=current_trajectory,
        from_source=from_source,
        frozen_ingestion_id=frozen_ingestion_id,
        source_ingestor=source_ingestor,
        source_manifest=source_manifest,
        semantic_evidence_approval=semantic_evidence_approval,
        semantic_qualification_report=semantic_qualification_report,
        ingestion_only=ingestion_only,
    )

    store = FilesystemSelfEvolveStore(workspace_root)
    ingestion_snapshot: (
        FrozenIngestionSnapshot
        | FrozenSemanticIngestionSnapshotV2
        | None
    ) = None
    ingestion_gate = None
    ingestion_trust_level: IngestorTrustLevel | None = None
    if from_source is not None or frozen_ingestion_id is not None:
        effective_ingestor_name = source_ingestor or "auto"
        registry = ingestion_registry or DEFAULT_INGESTION_REGISTRY
        if frozen_ingestion_id is not None:
            if (
                semantic_evidence_approval is not None
                or semantic_qualification_report is not None
            ):
                ingestion_snapshot = promote_ingestion_from_cli_request(
                    workspace_root=workspace_root,
                    frozen_ingestion_id=frozen_ingestion_id,
                    semantic_evidence_approval=(
                        semantic_evidence_approval
                    ),
                    semantic_qualification_report=(
                        semantic_qualification_report
                    ),
                    apply_policy=apply_policy,
                    ingestion_only=ingestion_only,
                )
            else:
                ingestion_snapshot = store.read_ingestion(
                    frozen_ingestion_id
                )
        else:
            ingestion_snapshot = prepare_ingestion_from_cli_request(
                workspace_root=workspace_root,
                from_source=str(from_source),
                source_ingestor=effective_ingestor_name,
                source_manifest=source_manifest,
                semantic_evidence_approval=semantic_evidence_approval,
                semantic_qualification_report=(
                    semantic_qualification_report
                ),
                apply_policy=apply_policy,
                ingestion_only=ingestion_only,
                ingestion_model_config=ingestion_model_config,
                ingestion_registry=registry,
            )
        ingestion_trust_level = registry.effective_snapshot_trust_level(
            ingestion_snapshot,
            ingestor_name=effective_ingestor_name,
        )
        if isinstance(
            ingestion_snapshot,
            FrozenSemanticIngestionSnapshotV2,
        ):
            _validate_frozen_semantic_runtime_admission(
                ingestion_snapshot,
                mode=_ingestion_mode(
                    apply_policy=apply_policy,
                    ingestion_only=ingestion_only,
                ),
            )
        if (
            source_manifest is not None
            and ingestion_snapshot.manifest_fingerprint is None
        ):
            raise ValueError(
                "registered ingestor did not freeze the requested source manifest"
            )

    source_config = (
        SelfEvolveEvalSourceConfig(kind="current_trajectory")
        if current_trajectory is not None
        else SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=ingestion_snapshot,
            max_cases=len(ingestion_snapshot.normalized_cases),
        )
        if ingestion_snapshot is not None
        else _source_config_from_cli_request(
            dataset=dataset,
            from_session=from_session,
            from_trajectory=from_trajectory,
            from_trajectory_set=from_trajectory_set,
            batch_config=batch_config,
            workspace_root=workspace_root,
        )
    )
    (
        built_dataset,
        campaign_dataset_snapshot_path,
        campaign_dataset_snapshot_reused,
    ) = runtime.load_or_build_campaign_dataset(
        store=store,
        campaign_id=campaign_id,
        campaign_cycle=campaign_cycle,
        source_config=source_config,
        current_trajectory=current_trajectory,
        task_id=task,
        progress_callback=progress_callback,
    )
    if ingestion_snapshot is not None:
        split_fingerprint = ingestion_fingerprint_json(built_dataset.recipe.splits)
        if (
            ingestion_snapshot.split_fingerprint is not None
            and ingestion_snapshot.split_fingerprint != split_fingerprint
        ):
            raise ValueError("frozen ingestion split fingerprint changed")
        if ingestion_snapshot.split_fingerprint is None:
            ingestion_snapshot = replace(
                ingestion_snapshot,
                split_fingerprint=split_fingerprint,
            )
        store.write_ingestion(
            ingestion_snapshot,
            dataset_recipe=built_dataset.recipe,
        )
        assert ingestion_trust_level is not None
        if isinstance(
            ingestion_snapshot,
            FrozenSemanticIngestionSnapshotV2,
        ):
            ingestion_gate = evaluate_semantic_quality_gate(
                ingestion_snapshot.quality_report,
                mode=_ingestion_mode(
                    apply_policy=apply_policy,
                    ingestion_only=ingestion_only,
                ),
                consensus_threshold=(
                    ingestion_snapshot.semantic_consensus_threshold
                ),
            )
        else:
            ingestion_gate = evaluate_ingestion_gate(
                ingestion_snapshot.quality_report,
                mode=_ingestion_mode(
                    apply_policy=apply_policy,
                    ingestion_only=ingestion_only,
                ),
                trust_level=ingestion_trust_level,
                snapshot_frozen=True,
                split_frozen=True,
                manifest=(
                    parse_source_manifest(
                        ingestion_snapshot.source_manifest
                    )
                    if ingestion_snapshot.source_manifest is not None
                    else None
                ),
            )
        if ingestion_only:
            if isinstance(
                ingestion_snapshot,
                FrozenSemanticIngestionSnapshotV2,
            ):
                return {
                    "status": "ingested",
                    "normalization_kind": "semantic_evidence",
                    "ingestion_id": ingestion_snapshot.ingestion_id,
                    "ingestion_report_path": str(
                        store.ingestion_path(
                            ingestion_snapshot.ingestion_id
                        )
                        / "quality_report.json"
                    ),
                    "ingestion_status": ingestion_gate.reason_code,
                    "ingestion_case_count": len(
                        ingestion_snapshot.normalized_cases
                    ),
                    "semantic_entity_count": len(
                        ingestion_snapshot.evidence_graph.entities
                    ),
                    "semantic_claim_count": len(
                        ingestion_snapshot.evidence_graph.claims
                    ),
                    "semantic_signal_count": len(
                        ingestion_snapshot
                        .improvement_signal_set.signals
                    ),
                    "semantic_conflict_count": len(
                        ingestion_snapshot.evidence_graph.conflicts
                    ),
                    "semantic_unresolved_conflict_count": (
                        ingestion_snapshot.quality_report
                        .unresolved_semantic_conflict_count
                    ),
                    "semantic_stage_completion_rate": (
                        ingestion_snapshot.quality_report
                        .agentic_stage_completion_rate
                    ),
                    "semantic_source_disposition_coverage_rate": (
                        ingestion_snapshot.quality_report
                        .semantic_source_disposition_coverage_rate
                    ),
                    "semantic_entailment_coverage_rate": (
                        ingestion_snapshot.quality_report
                        .semantic_entailment_coverage_rate
                    ),
                    "evidence_graph_logical_fingerprint": (
                        ingestion_snapshot.evidence_graph
                        .logical_fingerprint
                    ),
                    "evidence_graph_provenance_fingerprint": (
                        ingestion_snapshot.evidence_graph
                        .provenance_fingerprint
                    ),
                    "manifest_origin": (
                        ingestion_snapshot.manifest_origin.value
                    ),
                    "semantic_evidence_approval_template_path": (
                        str(
                            store.ingestion_path(
                                ingestion_snapshot.ingestion_id
                            )
                            / "evidence_approval_template.json"
                        )
                        if ingestion_snapshot.manifest_origin.value
                        == "operator_explicit"
                        and ingestion_snapshot.resolution_evidence
                        .extraction_origin.value
                        != "deterministic_canonical"
                        else None
                    ),
                    "semantic_model_profile_qualified": (
                        ingestion_snapshot.quality_report
                        .semantic_model_profile_qualified
                    ),
                    "semantic_verified_eligible_plan_count": (
                        ingestion_snapshot.quality_report
                        .verified_eligible_plan_count
                    ),
                    "semantic_non_verified_trainable_plan_count": (
                        ingestion_snapshot.quality_report
                        .non_verified_trainable_plan_count
                    ),
                    "semantic_attested_trace_count": sum(
                        item.extraction_attestation is not None
                        for item in ingestion_snapshot.resolved_traces
                    ),
                    "ingestion_model_call_count": (
                        ingestion_snapshot.ingestion_model_call_count
                    ),
                    "gate_results": [ingestion_gate.to_dict()],
                }
            return {
                "status": "ingested",
                "ingestion_id": ingestion_snapshot.ingestion_id,
                "ingestion_report_path": str(
                    store.ingestion_path(ingestion_snapshot.ingestion_id)
                    / "quality_report.json"
                ),
                "ingestion_status": ingestion_gate.reason_code,
                "ingestion_case_count": (
                    ingestion_snapshot.quality_report.normalized_case_count
                ),
                "ingestion_record_coverage_rate": (
                    ingestion_snapshot.quality_report.record_coverage_rate
                ),
                "ingestion_rejected_record_count": (
                    ingestion_snapshot.quality_report.rejected_record_count
                ),
                "ingestion_model_call_count": (
                    ingestion_snapshot.ingestion_model_call_count
                ),
                "gate_results": [ingestion_gate.to_dict()],
            }
        if not ingestion_gate.passed:
            run_id = _cli_run_id(
                target or "ingestion_rejected",
                ingestion_snapshot.ingestion_id,
                from_session,
                from_trajectory,
                from_trajectory_set,
                batch_config,
                iterations,
                campaign_id=campaign_id,
                campaign_cycle=campaign_cycle,
            )
            return _persist_ingestion_rejection(
                store=store,
                run_id=run_id,
                target=target,
                dataset=built_dataset,
                apply_policy=apply_policy,
                ingestion_gate=ingestion_gate.to_dict(),
            )
    trace_packs = tuple(
        case.trace_pack for case in built_dataset.cases if case.trace_pack is not None
    )
    if isinstance(
        ingestion_snapshot,
        FrozenSemanticIngestionSnapshotV2,
    ):
        resolved_traces = {
            item.trace_ref: item
            for item in ingestion_snapshot.resolved_traces
        }
        trace_packs = tuple(
            build_trace_pack(
                resolved_traces[execution.trace_ref].trajectory["steps"],
                source_kind="agentic_semantic_source",
                task_id=execution.execution_entity_id,
            )
            for execution in (
                ingestion_snapshot
                .compiled_dataset.target_evidence_bundle.executions
            )
        )
    if (
        infer_target
        and target is None
        and source_config.kind == "trajectory_log"
        and len(trace_packs) > 1
    ):
        built_dataset, trace_packs, _ = runtime.auto_group_trajectory_log_dataset(
            built_dataset,
            trace_packs,
            source_config=source_config,
            workspace_root=workspace_root,
        )
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
                (
                    ingestion_snapshot.ingestion_id
                    if ingestion_snapshot is not None
                    else dataset
                ),
                from_session,
                from_trajectory,
                from_trajectory_set,
                batch_config,
                iterations,
                campaign_id=campaign_id,
                campaign_cycle=campaign_cycle,
            )
            _write_run_ingestion_gate(store, run_id, ingestion_gate)
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
                budget_report=pre_execution_budget_report,
            )
        target_selection_decision = runtime.infer_target_from_trace_packs(
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
            (
                ingestion_snapshot.ingestion_id
                if ingestion_snapshot is not None
                else dataset
            ),
            from_session,
            from_trajectory,
            from_trajectory_set,
            batch_config,
            iterations,
            campaign_id=campaign_id,
            campaign_cycle=campaign_cycle,
        )
        _write_run_ingestion_gate(store, run_id, ingestion_gate)
        if target_selection_report.selected_target is None:
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
                budget_report=pre_execution_budget_report,
            )
        if not _campaign_target_matches(
            target_selection_report.selected_target,
            campaign_expected_target,
        ):
            target_selection_decision = _blocked_inferred_target_selection_decision(
                target_selection_decision,
                reason="campaign target identity changed across improvement cycles",
                signal="campaign_target_identity_changed",
            )
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_decision.report,
                apply_policy=apply_policy,
                budget_report=pre_execution_budget_report,
            )
        if (
            target_selection_decision.target_intent
            == TargetMutationIntent.INFERRED_DRAFT_CREATION
            and typed_new_skill_policy == InferredNewSkillPolicy.DISABLED
        ):
            target_selection_decision = _blocked_inferred_target_selection_decision(
                target_selection_decision,
                reason="inferred new-skill creation is disabled by policy",
                signal="inferred_new_skill_policy_disabled",
            )
            target_selection_report = target_selection_decision.report
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
                budget_report=pre_execution_budget_report,
            )
        if (
            target_selection_decision.target_intent
            == TargetMutationIntent.INFERRED_DRAFT_CREATION
        ):
            target_selection_decision = _materialize_run_owned_draft_decision(
                target_selection_decision,
                store=store,
                run_id=run_id,
                workspace_root=workspace_root,
                policy=typed_new_skill_policy,
            )
            target_selection_report = target_selection_decision.report
            target_provenance = target_selection_decision.provenance
            if target_selection_report.selected_target is None:
                return _persist_no_target_cli_result(
                    store=store,
                    run_id=run_id,
                    dataset=built_dataset,
                    target_selection_report=target_selection_report,
                    apply_policy=apply_policy,
                    budget_report=pre_execution_budget_report,
                )
        if not target_selection_decision.provenance_resolution.resolved:
            target_selection_decision = _blocked_inferred_target_selection_decision(
                target_selection_decision,
                reason=target_selection_decision.provenance_resolution.reason,
                signal="target_authorization_unresolved",
            )
            target_selection_report = target_selection_decision.report
            return _persist_no_target_cli_result(
                store=store,
                run_id=run_id,
                dataset=built_dataset,
                target_selection_report=target_selection_report,
                apply_policy=apply_policy,
                budget_report=pre_execution_budget_report,
            )
        if (
            _is_verified_apply_policy(apply_policy)
            and not _inferred_target_admitted_for_auto_apply(
                target_selection_decision
            )
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
                budget_report=pre_execution_budget_report,
            )
        try:
            target_adapter = runtime.target_from_ref(
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
                budget_report=pre_execution_budget_report,
            )
    else:
        if not target:
            raise ValueError("target is required unless target inference is enabled")
        run_id = _cli_run_id(
            target,
            (
                ingestion_snapshot.ingestion_id
                if ingestion_snapshot is not None
                else dataset
            ),
            from_session,
            from_trajectory,
            from_trajectory_set,
            batch_config,
            iterations,
            campaign_id=campaign_id,
            campaign_cycle=campaign_cycle,
        )
        _write_run_ingestion_gate(store, run_id, ingestion_gate)
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

    if not _campaign_target_matches(
        target_adapter.identity,
        campaign_expected_target,
    ):
        raise ValueError("campaign target identity changed across improvement cycles")

    if include_prior_runs:
        built_dataset = _include_prior_run_cases(
            built_dataset,
            store=store,
            target=target_adapter.identity,
            current_run_id=run_id,
        )

    resolved_regression_suites = resolve_regression_suites(
        regression_benchmarks,
        selection_dataset=built_dataset,
        base_dir=workspace_root,
    )
    if (
        not resolved_regression_suites
        and _is_verified_apply_policy(apply_policy)
    ):
        resolved_regression_suites = resolve_target_contract_regression_suite(
            target_type=target_adapter.identity.target_type,
            target_id=target_adapter.identity.target_id,
            target_path=target_adapter.identity.path,
            current_content=target_adapter.load_current_content(),
            target_fingerprint=target_adapter.fingerprint_current_content(),
            selection_dataset=built_dataset,
        )

    async def _cli_default_mutation(prompt: str) -> Mapping[str, Any]:
        current_content = target_adapter.load_current_content()
        candidate_content = runtime.default_cli_skill_candidate(
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

    async def _cli_candidate_population(
        prompts,
        max_concurrency,
        *,
        validate_output=None,
    ):
        if candidate_population_executor is None:
            raise RuntimeError("candidate population executor is not configured")
        return await candidate_population_executor.run(
            prompts,
            max_concurrency=max_concurrency,
            validate_output=validate_output,
        )

    if _is_verified_apply_policy(apply_policy) and evaluation_backend is None:
        evaluation_backend = _evaluation_backend_from_judge_config(
            judge_config,
            workspace_root=workspace_root,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if _is_verified_apply_policy(apply_policy) and post_apply_evaluator is None:
        post_apply_evaluator = _default_post_apply_evaluator(target_adapter)
    if (
        apply_policy == "auto_verified"
        and typed_new_skill_policy == InferredNewSkillPolicy.AUTO_VERIFIED
        and target_selection_decision is not None
        and target_selection_decision.target_intent
        == TargetMutationIntent.INFERRED_DRAFT_CREATION
        and runtime_registry_refresher is None
    ):
        runtime_registry_refresher = _default_new_skill_registry_refresher(
            target_adapter
        )
        runtime_registry_compensator = _default_new_skill_registry_compensator(
            target_adapter
        )
    if replay_enabled and candidate_replay_backend is None:
        candidate_replay_backend = runtime.replay_backend_type()
        if hasattr(candidate_replay_backend, "concurrency_policy"):
            candidate_replay_backend.concurrency_policy = (
                effective_concurrency_policy
            )

    measurement_pending_candidate: CandidateVariant | None = None
    measurement_resume_replay_dir: Path | None = None
    authoritative_measurement_resume = False
    pending_measurement_values = (
        campaign_measurement_pending_run_id,
        campaign_measurement_pending_candidate_id,
    )
    if any(pending_measurement_values):
        if not all(pending_measurement_values):
            raise ValueError(
                "campaign measurement resume requires both run and candidate ids"
            )
        assert campaign_measurement_pending_run_id is not None
        assert campaign_measurement_pending_candidate_id is not None
        if campaign_measurement_pending_run_id not in set(
            campaign_prior_run_ids or ()
        ):
            raise ValueError(
                "campaign measurement resume source is outside campaign lineage"
            )
        pending_source_report = store.read_report(
            campaign_measurement_pending_run_id
        )
        resume_checkpoint = load_measurement_resume_checkpoint(
            store,
            run_id=campaign_measurement_pending_run_id,
            report=pending_source_report,
        )
        if resume_checkpoint is None:
            resume_checkpoint = load_paired_replay_resume_checkpoint(
                store,
                run_id=campaign_measurement_pending_run_id,
                report=pending_source_report,
            )
        if resume_checkpoint is None:
            raise ValueError(
                "campaign replay resume checkpoint is missing or invalid"
            )
        authoritative_measurement_resume = isinstance(
            resume_checkpoint, MeasurementResumeCheckpointV1
        )
        if (
            resume_checkpoint.candidate_id
            != campaign_measurement_pending_candidate_id
        ):
            raise ValueError("campaign measurement resume candidate changed")
        pending_candidate_path = (
            store.run_path(campaign_measurement_pending_run_id)
            / "candidates"
            / f"{campaign_measurement_pending_candidate_id}.json"
        )
        measurement_pending_candidate = _load_candidate_variant(
            pending_candidate_path
        )
        expected_pending_fingerprint = pending_source_report.get(
            "measurement_pending_candidate_fingerprint"
        )
        actual_pending_fingerprint = candidate_package_fingerprint(
            measurement_pending_candidate
        )
        if (
            isinstance(expected_pending_fingerprint, str)
            and expected_pending_fingerprint
            and expected_pending_fingerprint != actual_pending_fingerprint
        ):
            raise ValueError(
                "campaign measurement resume candidate checkpoint changed"
            )
        if resume_checkpoint.candidate_fingerprint != actual_pending_fingerprint:
            raise ValueError(
                "campaign measurement resume typed candidate checkpoint changed"
            )
        if measurement_pending_candidate.target != target_adapter.identity:
            raise ValueError(
                "campaign measurement resume candidate target changed"
            )
        measurement_resume_replay_dir = (
            store.run_path(campaign_measurement_pending_run_id)
            / resume_checkpoint.replay_dir
        )
        _emit_progress(
            progress_callback,
            "resume",
            (
                "Resuming measurement-pending candidate "
                f"{campaign_measurement_pending_candidate_id} from "
                f"{campaign_measurement_pending_run_id}"
            ),
        )

    mutation_optimizer = TraceReflectiveLLMMutator(
        mutate_text=_cli_default_mutation,
        population_callable=(
            _cli_candidate_population
            if candidate_population_executor is not None
            else None
        ),
        concurrency_policy=effective_concurrency_policy,
    )
    optimizer: CandidateOptimizer = (
        _MeasurementResumeThenRepairOptimizer(
            candidate=measurement_pending_candidate,
            source_run_id=str(campaign_measurement_pending_run_id),
            delegate=mutation_optimizer,
        )
        if measurement_pending_candidate is not None
        else mutation_optimizer
    )

    self_evolve_runner = runtime.runner_type(
        store=store,
        optimizer=optimizer,
        evaluation_backend=evaluation_backend,
        regression_backend=regression_backend,
        regression_suites=resolved_regression_suites,
        challenger_backend=challenger_backend,
        challenger_enabled=challenger_enabled,
        challenger_max_cases=challenger_max_cases,
        post_apply_evaluator=post_apply_evaluator,
        min_score_delta=min_score_delta,
        max_iterations=effective_iteration_budget,
        min_eval_cases=min_eval_cases,
        judge_repetitions=judge_repetitions,
        max_run_tokens=max_run_tokens,
        total_run_token_budget=total_run_token_budget,
        per_attempt_replay_token_limit=per_attempt_replay_token_limit,
        max_run_cost_usd=max_run_cost_usd,
        max_run_wall_seconds=max_run_wall_seconds,
        candidate_generation_tokens_per_unit=(
            candidate_generation_tokens_per_unit
        ),
        candidate_generation_output_tokens_per_unit=(
            _effective_candidate_output_token_limit(mutation_model_config)
            if mutation_model_config is not None
            else 16_000
        ),
        candidate_generation_model_name=(
            mutation_model_config.llm_model_name
            if mutation_model_config is not None
            and mutation_model_config.llm_model_name
            else "gpt-4o"
        ),
        candidate_generation_cost_usd_per_unit=(
            candidate_generation_cost_usd_per_unit
        ),
        candidate_generation_wall_seconds_per_unit=(
            candidate_generation_wall_seconds_per_unit
        ),
        candidate_screening_tokens_per_unit=(
            candidate_screening_tokens_per_unit
        ),
        candidate_screening_cost_usd_per_unit=(
            candidate_screening_cost_usd_per_unit
        ),
        candidate_screening_wall_seconds_per_unit=(
            candidate_screening_wall_seconds_per_unit
        ),
        replay_tokens_per_unit=replay_tokens_per_unit,
        replay_cost_usd_per_unit=replay_cost_usd_per_unit,
        replay_wall_seconds_per_unit=replay_wall_seconds_per_unit,
        evaluation_tokens_per_unit=evaluation_tokens_per_unit,
        evaluation_cost_usd_per_unit=evaluation_cost_usd_per_unit,
        evaluation_wall_seconds_per_unit=evaluation_wall_seconds_per_unit,
        deprecated_config_mappings=deprecated_config_mappings,
        auto_apply_target_types=auto_apply_target_types,
        allow_generated_target_mutation=allow_generated_target_mutation,
        allow_external_target_mutation=allow_external_target_mutation,
        inferred_new_skill_policy=typed_new_skill_policy,
        replay_enabled=replay_enabled,
        candidate_replay_backend=candidate_replay_backend,
        regression_replay_backend=regression_replay_backend,
        replay_timeout_seconds=replay_timeout_seconds,
        replay_total_timeout_seconds=replay_total_timeout_seconds,
        replay_resume_dir=measurement_resume_replay_dir,
        measurement_resume_run_id=(
            str(campaign_measurement_pending_run_id)
            if authoritative_measurement_resume
            else None
        ),
        replay_max_steps=replay_max_steps,
        replay_candidate_limit=replay_candidate_limit,
        candidate_screening_max_cases=candidate_screening_max_cases,
        max_generated_candidates=max_generated_candidates,
        max_full_evaluation_candidates=max_full_evaluation_candidates,
        max_score_tiebreak_candidates=max_score_tiebreak_candidates,
        baseline_replay_repetitions=baseline_replay_repetitions,
        candidate_replay_repetitions=candidate_replay_repetitions,
        replay_repetitions_explicit=replay_repetitions_explicit,
        replay_stability_margin=replay_stability_margin,
        measurement_mode=measurement_mode,
        measurement_primary_metric=measurement_primary_metric,
        measurement_minimum_effect=measurement_minimum_effect,
        measurement_confidence_level=measurement_confidence_level,
        measurement_min_independent_cases=measurement_min_independent_cases,
        measurement_bootstrap_samples=measurement_bootstrap_samples,
        measurement_zero_yield_patience=measurement_zero_yield_patience,
        measurement_invalid_control_patience=(
            measurement_invalid_control_patience
        ),
        measurement_maximum_interval_width=(
            measurement_maximum_interval_width
        ),
        replay_adaptation_compiler=replay_adaptation_compiler,
        replay_agent=agent,
        runtime_registry_refresher=runtime_registry_refresher,
        runtime_skill_activator=runtime_skill_activator,
        runtime_registry_compensator=runtime_registry_compensator,
        runtime_skill_compensator=runtime_skill_compensator,
        progress_callback=progress_callback,
        concurrency_policy=effective_concurrency_policy,
        ingestion_model_call_count=(
            ingestion_snapshot.ingestion_model_call_count
            if ingestion_snapshot is not None
            and (
                frozen_ingestion_id is None
                or campaign_cycle == 1
            )
            else 0
        ),
        skill_evolution_contract=typed_skill_evolution_contract,
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
                "campaign_prior_run_ids": tuple(campaign_prior_run_ids or ()),
                "campaign_scheduler_checkpoint_run_ids": tuple(
                    campaign_scheduler_checkpoint_run_ids or ()
                ),
                "campaign_id": campaign_id,
                "campaign_cycle": campaign_cycle,
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
    report = _load_json_mapping(report_path)
    measurement_checkpoint = _measurement_pending_candidate_checkpoint(
        store=store,
        run_id=run_id,
        report=report,
    )
    if measurement_checkpoint is not None:
        report["measurement_resume_checkpoint"] = measurement_checkpoint.to_dict()
        report["measurement_pending_candidate_id"] = (
            measurement_checkpoint.candidate_id
        )
        report["measurement_pending_candidate_fingerprint"] = (
            measurement_checkpoint.candidate_fingerprint
        )
    paired_replay_checkpoint = _paired_replay_pending_candidate_checkpoint(
        store=store,
        run_id=run_id,
        report=report,
    )
    if paired_replay_checkpoint is not None:
        report["paired_replay_resume_checkpoint"] = (
            paired_replay_checkpoint.to_dict()
        )
        report["measurement_pending_candidate_id"] = (
            paired_replay_checkpoint.candidate_id
        )
        report["measurement_pending_candidate_fingerprint"] = (
            paired_replay_checkpoint.candidate_fingerprint
        )
    if measurement_pending_candidate is not None:
        report["measurement_resume"] = {
            "schema_version": "aworld.self_evolve.measurement_resume.v1",
            "source_run_id": campaign_measurement_pending_run_id,
            "candidate_id": measurement_pending_candidate.candidate_id,
            "candidate_fingerprint": candidate_package_fingerprint(
                measurement_pending_candidate
            ),
            "generation_skipped": True,
            "resume_scope": (
                "authoritative_measurement"
                if authoritative_measurement_resume
                else "paired_replay"
            ),
            "source_replay_dir": (
                str(measurement_resume_replay_dir)
                if measurement_resume_replay_dir is not None
                else None
            ),
        }
    if (
        measurement_pending_candidate is not None
        or report.get("measurement_pending_candidate_id") is not None
    ):
        store.write_report(run_id, report)
    selected_candidate_id = report.get("selected_candidate_id")
    if not isinstance(selected_candidate_id, str) or not selected_candidate_id:
        selected_candidate_id = None
    repair_focus_candidate_id = report.get("repair_focus_candidate_id")
    if (
        not isinstance(repair_focus_candidate_id, str)
        or not repair_focus_candidate_id
    ):
        repair_focus_candidate_id = None
    summary = {
        "report_path": str(report_path),
        "best_candidate_id": (
            selected_candidate_id
            if result.run.status.value == "succeeded"
            and _is_verified_apply_policy(apply_policy)
            else None
        ),
        "selected_candidate_id": selected_candidate_id,
        "repair_focus_candidate_id": repair_focus_candidate_id,
        "run_id": result.run.run_id,
        "status": result.run.status.value,
    }
    if campaign_dataset_snapshot_path is not None:
        summary["campaign_dataset_snapshot_path"] = str(
            campaign_dataset_snapshot_path
        )
        summary["campaign_dataset_snapshot_reused"] = (
            campaign_dataset_snapshot_reused
        )
    _add_post_apply_summary(summary, report)
    if ingestion_snapshot is not None:
        summary.update(
            {
                "ingestion_id": ingestion_snapshot.ingestion_id,
                "ingestion_report_path": str(
                    store.ingestion_path(ingestion_snapshot.ingestion_id)
                    / "quality_report.json"
                ),
            }
        )
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
    if selected_candidate_id is not None:
        regression_evidence_path = (
            run_path
            / "regression"
            / "evidence"
            / f"{selected_candidate_id}.json"
        )
        if regression_evidence_path.is_file():
            summary["regression_evidence_path"] = str(
                regression_evidence_path
            )
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
        promotion = report_payload.get("promotion")
        if isinstance(promotion, Mapping):
            summary["promotion"] = dict(promotion)
        rejection_attribution = report_payload.get("rejection_attribution")
        if isinstance(rejection_attribution, Mapping):
            summary["rejection_attribution"] = dict(rejection_attribution)
        campaign_failure_attribution = report_payload.get(
            "campaign_failure_attribution"
        )
        if isinstance(campaign_failure_attribution, Mapping):
            summary["campaign_failure_attribution"] = dict(
                campaign_failure_attribution
            )
    return summary
