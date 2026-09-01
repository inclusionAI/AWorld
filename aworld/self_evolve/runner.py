from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
import math
import re
import shutil
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Any
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from aworld.config.conf import ModelConfig, SelfEvolveJudgeConfig
from aworld.agents.prompt_budgeted_agent import PromptBudgetedAgent
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.context.amni.prompt.assembly.budget import PromptBudgetPolicy
from aworld.core.task import Task
from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
from aworld.logs.util import logger
from aworld.models.usage import normalize_usage
from aworld.runner import Runners
from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    TargetSelectionReport,
    TrajectoryCreditAssigner,
    build_target_selection_decision,
    build_default_target_inventory,
)
from aworld.self_evolve.counterexamples import (
    candidate_failure_counterexample,
    normalize_counterexample,
)
from aworld.self_evolve.causal_admission import (
    causal_admission_prerequisite_blocker,
)
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_recipe,
    build_dataset_from_source,
)
from aworld.self_evolve.dataset_snapshot import (
    CAMPAIGN_DATASET_SNAPSHOT_SCHEMA_VERSION,
    campaign_dataset_snapshot_supported,
    load_campaign_dataset_snapshot,
    load_campaign_dataset_snapshot_manifest,
    write_campaign_dataset_snapshot,
)
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.skill_evolution_contract import (
    SkillEvolutionContract,
    evaluate_skill_evolution_replay,
)
from aworld.self_evolve.evaluation import (
    AWorldTrajectoryEvaluatorBackend,
    EvaluationBackend,
    EvaluationRequest,
    SkillCandidateOverlayBackend,
    evaluate_baseline_and_candidate,
    evaluate_variant_task,
)
from aworld.self_evolve.gates import (
    CandidatePackageGate,
    CostLatencyRegressionGate,
    EvidenceQualityGate,
    EvaluationRuntimeHealthGate,
    ExternalCodeEvolutionGate,
    MalformedCandidateGate,
    NewSkillPromotionGate,
    NoopCandidateGate,
    ProtectedPathGate,
    ReplayAdaptationGate,
    ScoreImprovementGate,
    SkillMarkdownGate,
    SkillReleaseFidelityGate,
    StoppingConditionGate,
    StoppingConditionState,
    TokenLimitGate,
    TrustProvenanceGate,
)
from aworld.self_evolve.controllers.retention import (
    ArtifactRetentionController,
    acknowledge_reported_artifact_retention as _acknowledge_reported_artifact_retention,
    merge_artifact_retention_reports as _merge_artifact_retention_reports,
    recover_artifact_retention_transactions as _recover_artifact_retention_transactions,
)
from aworld.self_evolve.lifecycle import cleanup_self_evolve_artifacts
from aworld.self_evolve.measurement import (
    AttributionReport,
    BudgetLedger,
    ControlledExperimentSpec,
    MeasurementEarlyStopPolicy,
    MeasurementPolicyMode,
    MeasurementSummary,
    MeasurementUsage,
    SearchCandidateResult,
    build_search_performance,
    evaluate_measurement_stopping,
    stable_measurement_fingerprint,
)
from aworld.self_evolve.measurement_control import (
    MeasurementPlanV2,
    estimate_measurement_feasibility,
)
from aworld.self_evolve.measurement_checkpoint import (
    MeasurementResumeCheckpointV1,
    PairedReplayResumeCheckpointV1,
    discover_measurement_resume_checkpoint,
    discover_paired_replay_resume_checkpoint,
    load_measurement_resume_checkpoint,
    load_paired_replay_resume_checkpoint,
)
from aworld.self_evolve.measurement_planner import (
    measurement_preflight_projection,
)
from aworld.self_evolve.ingestion import (
    DEFAULT_INGESTION_REGISTRY,
    AgenticDatasetIngestor,
    DatasetIngestionRequest,
    FrozenIngestionSnapshot,
    IngestionMode,
    IngestionRegistry,
    IngestionVerifier,
    IngestorTrustLevel,
    SourceScanner,
    build_quality_report,
    evaluate_ingestion_gate,
    fingerprint_json as ingestion_fingerprint_json,
    load_source_manifest,
    parse_source_manifest,
    validate_frozen_snapshot_quality,
)
from aworld.self_evolve.ingestion.types import (
    IngestionManifestOrigin,
)
from aworld.self_evolve.ingestion.semantic_workflow import (
    SemanticProviderResponseV1,
)
from aworld.self_evolve.ingestion.semantic_snapshot import (
    FrozenSemanticIngestionSnapshotV2,
)
from aworld.self_evolve.ingestion.semantic_ingestor import (
    promote_frozen_semantic_ingestion,
)
from aworld.self_evolve.ingestion.semantic_verifier import (
    evaluate_semantic_quality_gate,
)
from aworld.self_evolve.failure_events import (
    AggregatedReplayFailure,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
    aggregate_replay_failures,
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
)
from aworld.self_evolve.evidence_diagnostics import (
    EvidenceRepairConstraint,
    merge_evidence_repair_constraints,
)
from aworld.self_evolve.evaluation_plan import (
    HumanEvidenceApprovalV1,
    SemanticModelQualificationReportV1,
    SemanticQualificationRegistryV1,
)
from aworld.self_evolve.semantic_qualification import (
    load_semantic_model_qualification_report,
    load_semantic_qualification_registry,
)
from aworld.self_evolve.lessons import extract_lesson_records
from aworld.self_evolve.candidate_package import (
    CandidateMutationKind,
    candidate_content_semantic_fingerprint,
    candidate_file_semantic_fingerprint,
    candidate_package_fingerprint,
    candidate_package_reference_report,
    candidate_semantic_package_fingerprint,
    classify_candidate_mutation,
    validate_candidate_files,
)
from aworld.self_evolve.candidate_errors import (
    candidate_materialization_requirement_id,
    normalize_candidate_contract_fingerprint,
    normalize_candidate_failure_field,
    normalize_candidate_materialization_code,
    normalize_candidate_representation,
)
from aworld.self_evolve.candidate_protocol import (
    CANDIDATE_OUTPUT_CONTRACT,
    CandidateProtocolError,
    build_candidate_output_contract,
    merge_candidate_repair_output,
    normalize_candidate_output,
)
from aworld.self_evolve.capability_contracts import (
    validate_applicable_capabilities,
)
from aworld.self_evolve.campaign_policy import (
    CANDIDATE_REPAIRABLE_GATE_STAGES as _CANDIDATE_REPAIRABLE_GATE_STAGES,
    FRAMEWORK_SHARED_GATE_STAGES as _FRAMEWORK_SHARED_GATE_STAGES,
    campaign_measurement_outcome_for_replay as _campaign_measurement_outcome_for_replay,
    effective_cli_measurement_mode as _effective_cli_measurement_mode,
    effective_replay_repetitions as _effective_replay_repetitions,
    gate_has_candidate_owned_repair as _gate_has_candidate_owned_repair,
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationAgent,
    CandidateGenerationInfrastructureError,
    _effective_candidate_output_token_limit,
)
from aworld.self_evolve.controllers.generation import (
    CandidateGenerationController,
    candidate_generation_request_derived_tokens as _candidate_generation_request_derived_tokens,
)
from aworld.self_evolve.controllers.run_generation_execution import (
    GenerationExecutionDisposition,
    GenerationExecutionPolicy,
    GenerationExecutionRequest,
    GenerationExecutionRuntime,
    GenerationExecutionState,
    execute_generation_iteration,
)
from aworld.self_evolve.controllers.run_generation_helpers import (
    _MAX_CONSECUTIVE_DUPLICATE_POPULATION_STALLS,
    _MAX_CONSECUTIVE_MATERIALIZATION_STALLS,
    _MAX_CONSECUTIVE_POLICY_FILTER_STALLS,
    _SEMANTIC_DEDUP_IDENTITY_VERSION,
    _VERIFICATION_CONTRACT_VERSION,
    _SemanticLessonFingerprint,
    _causal_event_drives_repair_frontier,
    _candidate_attempt_placeholder,
    _candidate_generation_actual_usage,
    _candidate_materialization_failure_events,
    _candidate_materialization_failure_event,
    _candidate_materialization_failures,
    _candidate_materialization_stall_signature,
    _candidate_policy_filter_event,
    _candidate_policy_filter_signature,
    _candidate_population_rank_key,
    _candidate_mutation_rank,
    _candidate_strategy_records,
    _canonicalize_verified_prerequisite_files,
    _is_semantic_lesson_duplicate,
    _known_duplicate_candidate_count,
    _lineage_semantic_lesson_fingerprints,
    _nonnegative_int_or_default,
    _optimizer_stored_candidate_admission_reason,
    _rank_candidate_population,
    _retryable_candidate_generation_failure,
    _scheduler_state_with_mutation_families,
    _sequence_length,
    _semantic_lesson_duplicate_count,
    _semantic_lesson_duplicate_feedback,
    _positive_int_or_default,
    _string_list,
    _typed_repair_frontiers,
    _verification_contract_fingerprint as _generation_verification_contract_fingerprint,
    _verified_prerequisite_files,
    _with_versioned_semantic_lineage,
)
from aworld.self_evolve.controllers.run_iteration_execution import (
    IterationExecutionPolicy,
    IterationExecutionRequest,
    IterationExecutionRuntime,
    execute_iteration_lifecycle,
)
from aworld.self_evolve.controllers.run_iteration_helpers import (
    _authoritative_attempt_consumed,
    _baseline_comparison_feedback_metrics,
    _bounded_repair_candidate_target_content,
    _candidate_conformance_counterexample_ids,
    _candidate_conformance_counterexample_stages,
    _candidate_conformance_failure_signatures,
    _candidate_conformance_repair_topologies,
    _candidate_conformance_stall_signature,
    _candidate_conformance_strategy_switch_feedback,
    _candidate_gate_results,
    _candidate_repair_conformance_contracts,
    _candidate_screening_repair_failures,
    _candidate_screening_repair_feedback,
    _candidate_validation_shared_failure_gate,
    _candidate_validation_stopped_by_shared_infrastructure,
    _feedback_failure_reference,
    _gate_is_replay_execution_infrastructure_failure,
    _infrastructure_prevented_comparable_evaluation,
    _iteration_validation_feedback,
    _record_authoritative_replay_observations,
    _repair_candidate_package_feedback,
    _repair_contract_fingerprint,
    _source_control_flow_shape,
)
from aworld.self_evolve.cli_ingestion import (
    _IngestionMappingModelProvider,
    _IngestionSemanticModelProvider,
    _dataset_ingestion_summary,
    _ingestion_mode,
    _ingestor_for_request,
    _load_human_evidence_approval,
    _load_or_build_campaign_dataset as _cli_load_or_build_campaign_dataset,
    _load_semantic_trust_artifacts,
    _persist_ingestion_rejection,
    _reject_workspace_trust_symlink_components,
    _resolve_ingestion_artifact_path,
    _source_config_from_cli_request,
    _validate_eval_source_request,
    _validate_frozen_semantic_runtime_admission,
    _verify_trusted_registered_snapshot,
    _with_campaign_dataset_snapshot_reference,
    _write_run_ingestion_gate,
    prepare_ingestion_from_cli_request,
    promote_ingestion_from_cli_request,
)
from aworld.self_evolve.cli_rerun import (
    _load_candidate_variant,
    _load_structural_edit_intent,
    _load_target_provenance,
    _load_target_selection_report,
    _rerun_cli_run_id,
    _resolve_stored_run_path,
    _source_config_from_stored_dataset_recipe,
    _stored_selected_candidate_id,
    _validate_agentic_rerun_ingestion_ref,
    _validate_rerun_source_runtime_admission,
)
from aworld.self_evolve.cli_orchestration import (
    CliOrchestrationRuntime,
    execute_cli_optimization,
    _FixedCandidateOptimizer,
    _MeasurementResumeThenRepairOptimizer,
    _StoredCandidateReplayBackend,
    _add_post_apply_summary,
    _aggregate_target_selection_decisions,
    _auto_group_trajectory_log_dataset,
    _blocked_inferred_target_selection_decision,
    _blocked_low_confidence_target_selection_report,
    _campaign_target_matches,
    _candidate_mutation_repair_prompt,
    _candidate_runtime_prefix,
    _cli_run_id,
    _content_fingerprint,
    _default_cli_skill_candidate,
    _default_iteration_budget,
    _default_new_skill_registry_refresher,
    _default_post_apply_evaluator,
    _empty_run_budget_report as _cli_empty_run_budget_report,
    _evaluation_backend_from_judge_config,
    _explicit_target_selection_report,
    _feedback_has_evidence_preservation_issue,
    _feedback_has_high_baseline_regression_issue,
    _feedback_has_scope_or_cost_issue,
    _feedback_metrics_from_mutation_prompt,
    _feedback_repair_plan_from_mutation_prompt,
    _feedback_required_behaviors_from_mutation_prompt,
    _gate_has_candidate_prerequisite_failure,
    _group_average_confidence,
    _group_confidence_bucket,
    _group_context_completeness_bucket,
    _group_context_completeness_rate,
    _has_failed_trace_lesson,
    _include_prior_run_cases,
    _infer_target_from_trace_packs,
    _inferred_target_admitted_for_auto_apply,
    _materialize_run_owned_draft_decision,
    _measurement_pending_candidate_checkpoint,
    _metric_number,
    _no_evidence_target_selection_report,
    _paired_replay_pending_candidate_checkpoint,
    _parse_candidate_mutation_model_output,
    _path_has_symlink_component,
    _persist_no_target_cli_result,
    _persist_unsupported_target_cli_result,
    _population_strategy_from_mutation_prompt,
    _prior_run_case_input,
    _prior_run_eval_cases,
    _prior_run_metric_summary,
    _report_has_candidate_prerequisite_failure,
    _report_has_shared_measurement_failure,
    _report_matches_target,
    _rerun_evaluator_from_stored_run,
    _runtime_behavior_rules_from_mutation_prompt,
    _sanitized_path_list,
    _target_from_cli_ref,
    _target_from_ref,
    _target_group_id,
    _target_ref_text,
    _target_runtime_skill_path,
    _skill_target_adapter,
    _skill_target_from_id,
    _validated_run_owned_draft_paths,
    _CLI_TARGET_ADAPTER_FACTORIES,
    _target_selection_pack_ids,
    _target_selection_priority,
    _trajectory_case_context_status,
    _trajectory_group_rank_key,
    _trajectory_log_grouping_report,
)
from aworld.self_evolve.feedback_diagnostics import (
    _candidate_repair_diagnostic_view,
    _diagnostic_classification_text,
    _diagnostic_completed_data_plane_operations,
    _diagnostic_fixture_root_types,
    _diagnostic_interaction_progress,
    _diagnostic_observed_request_operations,
    _diagnostic_protocol_probe_mismatch,
    _diagnostic_routing_continuity_gaps,
    _failure_signature_values,
    _feedback_constraint_recovery_frontier,
    _feedback_contract_schema_constraint_ids,
    _feedback_has_candidate_repair_conformance,
    _feedback_interaction_progress,
    _feedback_recovery_frontier,
    _feedback_requires_counterexample_screening,
    _feedback_violated_schema_constraint_ids,
    _looks_like_protocol_routing_field,
    _merge_validation_feedback,
    _next_progress_repair_extension_family,
    _public_replay_counterexample,
    _public_replay_counterexamples,
    _recovery_trace_frontier,
    _typed_gate_feedback_metrics,
    _validation_feedback_failure_family,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
    CandidateLocalAdmissionPolicy,
    CandidateReplayAdmissionPolicy,
    CandidateReplayAdmissionRuntime,
    ExplicitTargetRunRequest,
    duplicate_accepted_candidate_gate as _duplicate_accepted_candidate_gate,
    duplicate_rejected_candidate_gate as _duplicate_rejected_candidate_gate,
    execute_candidate_local_admission,
    execute_candidate_replay_admission,
    iteration_report_item as _iteration_report_item,
    iteration_state as _iteration_state,
    terminal_candidate_evaluation_result as _controller_terminal_candidate_result,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionRequest,
    CandidateReplayExecutionRuntime,
    execute_candidate_replay,
)
from aworld.self_evolve.controllers.run_evaluation_admission import (
    CandidateEvaluationAdmissionPolicy,
    CandidateEvaluationAdmissionRequest,
    CandidateEvaluationAdmissionRuntime,
    plan_candidate_evaluation_admission,
)
from aworld.self_evolve.controllers.run_evaluation_execution import (
    CandidateEvaluationExecutionPolicy,
    CandidateEvaluationExecutionRequest,
    CandidateEvaluationExecutionRuntime,
    execute_candidate_evaluation,
)
from aworld.self_evolve.controllers.run_evaluation_finalization import (
    CandidateEvaluationFinalizationPolicy,
    CandidateEvaluationFinalizationRequest,
    CandidateEvaluationFinalizationRuntime,
    finalize_candidate_evaluation,
)
from aworld.self_evolve.controllers.run_state import (
    ExplicitRunStateAccumulator,
    VerificationFunnelRequest,
)
from aworld.self_evolve.controllers.run_terminal import (
    InferredDraftPromotionRequest,
    MeasurementReportRequest,
    TerminalPromotionRequest,
    TerminalPromotionRuntime,
    TerminalSelectionRequest,
    TerminalSelectionRuntime,
    plan_terminal_promotion,
    project_inferred_draft_promotion,
    project_measurement_report,
    project_target_selection_promotion_diagnostics,
    project_terminal_selection,
    release_normalization_report as _release_normalization_report,
    settle_post_apply_status,
)
from aworld.self_evolve.controllers.run_terminal_finalization import (
    TerminalFinalizationRequest,
    TerminalFinalizationRuntime,
    final_replay_causal_events as _final_replay_causal_events,
    finalize_terminal_run,
    harness_diagnostic_promotion_counts as _harness_diagnostic_promotion_counts,
    harness_diagnostic_type_counts as _harness_diagnostic_type_counts,
    lesson_extraction_counts as _lesson_extraction_counts,
    lesson_type_counts as _lesson_type_counts,
)
from aworld.self_evolve.controllers.measurement_execution_admission import (
    _candidate_intervention_unobserved,
    _candidate_intervention_unobserved_failure_event,
    _candidate_recovery_failure_event,
    _failure_completed_data_plane_operations,
    _paired_candidate_completion_failure,
    _paired_candidate_screening_deadline_failure,
    _replay_decision_failure_events,
    _replay_gate_details,
    _screening_budget_censor_basis,
    _system_owned_repetition_failures,
    _variant_blocked_by_invalid_control,
    _variant_has_progressing_task_timeout,
    _variant_is_screening_baseline_deadline,
    _variant_is_screening_timeout,
)
from aworld.self_evolve.controllers.measurement_execution_datasets import (
    _authoritative_control_should_defer,
    _authoritative_replay_dataset,
    _compact_authoritative_case_context,
    _control_qualification_identity as _measurement_control_identity,
    _control_qualification_identity_from_request,
    _legacy_path_sensitive_support_fingerprint,
    _partial_replay_evaluator_dataset as _measurement_partial_dataset,
    _prioritize_candidate_intervention_cases,
    _task_input_content,
)
from aworld.self_evolve.controllers.measurement_execution_progress import (
    _replay_member_hard_deadline_seconds,
    _replay_member_progress_message,
    _replay_timeout_checkpoint_details,
)
from aworld.self_evolve.controllers.measurement import (
    CandidateMeasurementController,
    MeasurementPlanningConfig,
    MeasurementPlanningController,
    MeasurementPlanningIdentities,
    MeasurementPlanningRequest,
    MeasurementPlanningRuntime,
    _rebase_measurement_experiment_for_materialization,
    measurement_component_identity as _measurement_component_identity,
    measurement_promotion_gate as _measurement_promotion_gate,
)
from aworld.self_evolve.controllers.measurement_authority import (
    AuthoritativeMeasurementConfig,
    AuthoritativeMeasurementController,
    AuthoritativeMeasurementRequest,
    AuthoritativeMeasurementRuntime,
    _authoritative_evidence_finalization_timeout_seconds,
    _legacy_retryable_measurement_task_failed_work_unit_ids,
)
from aworld.self_evolve.controllers.measurement_execution import (
    PairedReplayExecutionConfig,
    PairedReplayExecutionController,
    PairedReplayExecutionRequest,
    PairedReplayExecutionRuntime,
)
from aworld.self_evolve.controllers.screening import (
    SCREENING_BUDGET_CENSORED_CODE as _SCREENING_BUDGET_CENSORED_CODE,
    CandidateScreeningController,
    ScreeningPopulationRequest,
    ScreeningPopulationRuntime,
    budget_decision_wall_limit_seconds as _budget_decision_wall_limit_seconds,
    screening_attempt_is_budget_censored as _screening_attempt_is_budget_censored,
    screening_gate_is_budget_censored as _screening_gate_is_budget_censored,
    screening_stage_budget_censor_gate as _screening_stage_budget_censor_gate,
    support_specific_control_circuit_breaker_gate as _support_specific_control_circuit_breaker_gate,
)
from aworld.self_evolve.controllers.screening_helpers import (
    _candidate_artifact_lifecycle_observations,
    _candidate_changes_target_behavior,
    _candidate_replay_has_repairable_capability_failure,
    _candidate_requires_task_plane_intervention,
    _candidate_support_baseline_incompatibility_gate,
    _candidate_task_plane_intervention_case_ids,
    _candidate_task_plane_intervention_observation,
    _candidate_task_plane_intervention_observed,
    _candidate_validation_report_for_persistence,
    _combined_candidate_validation_report,
    _deduplicate_conformance_phenotypes,
    _framework_phase_timeout,
    _repairable_capability_failure,
    _replay_backend_provides_skill_activation_attestation,
    _screening_attempt_has_artifact_lifecycle_proof,
    _screening_attempt_is_candidate_failure,
    _screening_attempt_requires_artifact_lifecycle_proof,
    _screening_attempt_requires_candidate_repair,
    _screening_baseline_failure_case_ids,
    _screening_control_infeasible_before_candidate_observation,
    _screening_gate_has_baseline_execution_failure,
    _screening_gate_has_invalid_control,
    _screening_invalid_control_case_ids,
    _screening_invalid_control_is_timeout,
    _screening_required_intervention_unobserved,
    _candidate_screening_rank,
    _candidate_screening_rank_details,
    _control_qualification_identity as _screening_control_qualification_identity,
    _record_support_specific_control_observation,
    _candidate_screening_dataset,
    _screening_case_has_feasible_baseline,
    _screening_case_has_only_invalid_baselines,
    _candidate_screening_dataset_for_case_ids,
    _candidate_screening_case_cost,
    _record_candidate_screening_observation,
    _screening_attempt_termination_axes,
    _screening_termination_axis_counts,
    _non_negative_screening_float,
    _candidate_screening_qualification_case_limit,
    _candidate_screening_case_distance,
    _DEFAULT_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    _DEFAULT_CANDIDATE_SCREENING_TOOL_CALL_LIMIT,
    _DEFAULT_CANDIDATE_SCREENING_TRACE_HORIZON,
    _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    _SCREENING_STEP_TIMEOUT_SECONDS,
)
from aworld.self_evolve.controllers.screening_execution import (
    _TelemetryUsageDelta,
    _TelemetryUsageSnapshot,
    _decimal_metric,
    _sanitized_telemetry_usage_batch,
    _stage_telemetry_usage_snapshot,
    _canonical_batch_token_usage,
    _canonical_batch_decimal_usage,
    _stage_telemetry_usage_delta,
    _telemetry_usage_with_observed_wall,
    _budget_usage_for_attempt_event,
    _emit_progress,
    _non_negative_int,
    _candidate_screening_timeout,
    _candidate_screening_escalated_timeout,
    _candidate_screening_max_steps,
    _schema_field_contract_fingerprint,
    _with_typed_gate_failure_event,
    _replay_artifact_path,
    _replay_request_artifact_path,
    _baseline_replay_artifact_dir,
    _replay_result_has_reusable_baseline,
    find_reusable_baseline_replay_dir as _find_reusable_baseline_replay_dir,
    _incremental_baseline_cache_dir,
    _replay_request_provenance_matches,
    _replay_target_matches,
    _load_json_mapping,
    _gate_has_typed_shared_infrastructure_failure,
    _gate_has_typed_shared_measurement_failure,
    _repair_conformance_gate,
    _shared_replay_failure_blocks_population,
    _replay_evaluator_admission_gate,
    _typed_causal_feedback_event,
    execute_screen_candidate_population,
)
from aworld.self_evolve.challenger import (
    DEFAULT_CHALLENGE_CASES,
    MAX_CHALLENGE_CASES,
    ChallengeProposalBatch,
    ChallengeReport,
    ChallengerBackend,
    ChallengerRequest,
    DeterministicInvariantChallenger,
    admit_challenge_proposals,
)
from aworld.self_evolve.handbook import load_handbook_slice_for_target
from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetDecision,
    BudgetEstimateConfidence,
    BudgetEstimateSource,
    BudgetStage,
    BudgetUsage,
    BudgetUsageCompleteness,
    BudgetUsageObservation,
    CandidateAttemptEvent,
    CandidateAttemptKey,
    CandidateAttemptStage,
    RepairFrontier,
    RunBudgetLedger,
    ScheduledCandidateSlot,
    ScheduledSlotRole,
    SchedulerDecision,
    SchedulerState,
    StageAwareCandidateScheduler,
    TERMINAL_ATTEMPT_STAGES,
    ZeroBudgetUsageProofProvider,
    aggregate_candidate_attempts,
)
from aworld.self_evolve.concurrency import (
    AWorldCandidatePopulationExecutor,
    SelfEvolveConcurrencyPolicy,
    SelfEvolveExecutionTelemetry,
)
from aworld.self_evolve.optimizers.base import (
    CandidateGenerationOutcome,
    CandidateGenerationOutcomeKind,
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
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
    TargetProvenance,
    TargetProvenanceResolution,
    TargetProvenanceStatus,
    TargetSelectionOrigin,
    load_target_provenance_payload,
    resolve_target_provenance,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    AWorldCliReplayExecutor,
    CandidateReplayBackend,
    CandidateReplayEvidenceReuseBackend,
    CandidateReplayRequest,
    CandidateReplayResult,
    NormalizedReplayMembers,
    ReplayVariantResult,
    ReplayEvidenceDispositionKind,
    ReplayEvidenceReuseDisposition,
    ReplayServiceProcessExitedError,
    ReplayServiceReadinessTimeout,
    baseline_control_fingerprint,
    build_paired_replay_dataset,
    build_replay_request,
    candidate_replay_artifact_directory,
    candidate_replay_is_comparable,
    candidate_replay_pair_coverage,
    normalize_replay_members,
    load_candidate_replay_result,
    preflight_frozen_replay_capability,
    replay_capability_fixture_leaf_values,
    replay_capability_fixture_response_leaf_values,
    replay_capability_fixture_summaries,
    replay_dataset_fingerprint,
    replay_support_fingerprint,
    replay_timeout_envelope_fingerprint,
    _baseline_invalid_for_measurement,
    _baseline_replay_is_reusable,
    _candidate_replay_request_from_mapping,
    _distributed_member_repetitions,
    _load_variant_result_from_dir,
    _member_baseline_replay_dir,
    _member_artifact_name,
    _replay_member_pair_is_comparable,
    _replay_service_start_failure_details,
    _is_replayable_user_task_case,
    _select_replay_case,
)
from aworld.self_evolve.recovery_trace import (
    RECOVERY_TRACE_SCHEMA_VERSION,
    replay_recovery_trace,
    trace_pack_recovery_opportunity,
    update_constraint_recovery_trace,
    validate_public_constraint_recovery_trace,
    validate_public_recovery_trace,
)
from aworld.self_evolve.regression import (
    RegressionEvidence,
    RegressionSuiteResult,
    ResolvedRegressionSuite,
    dataset_case_fingerprints,
    evaluation_backend_identity,
    regression_execution_id,
    resolve_regression_suites,
    resolve_target_contract_regression_suite,
)
from aworld.self_evolve.schema_diagnostics import SchemaFieldRepairConstraint
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    RepairConformanceResult,
    build_repair_conformance_probe_plan,
    evaluate_artifact_lifecycle_conformance,
    evaluate_candidate_source_conformance,
    evaluate_compiled_probe_conformance,
    merge_repair_conformance_constraint_context,
    project_replay_capability_for_probe_group,
    repair_conformance_contract_identity,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    ReplayAdaptationBundle,
    ReplayAdaptationCompiler,
    ReplayCapabilityRequirement,
    ReplayPreflightReport,
    replay_adaptation_semantic_fingerprint,
)
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_PROTOCOL_VERSION,
    REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS,
    REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS,
    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS,
    FrozenReplayCapabilityAdapter,
    ReplayCapabilityCompileRequest,
    ReplayCapabilityError,
    compile_and_freeze_capability,
    discover_replay_capability,
    frozen_replay_fixture_shape_fingerprints,
    materialize_replay_evidence_derivations,
    replay_capability_semantic_fingerprint,
)
from aworld.self_evolve.sanitization import (
    public_diagnostic_projection,
    sanitize_metric_value,
    sanitize_path_ref,
    sanitize_source_text,
    sanitize_text,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import (
    DraftSkillTextTarget,
    SelfEvolveTarget,
    SkillTextTarget,
    TargetSnapshotStaleError,
)
from aworld.self_evolve.trace_pack import TracePack, build_trace_pack
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    OptimizerLineage,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
    SkillStructuralEditAction,
    SkillStructuralEditIntent,
    to_json_dict,
)
from aworld.skills.compat_provider import build_compat_registry
from aworld.skills.release import normalize_verified_skill_release


def _control_qualification_identity(
    *,
    case_id: str,
    baseline_skill_fingerprint: str,
    replay_adaptation: ReplayAdaptationBundle,
    timeout_seconds: float,
    max_steps: int | None,
    max_tool_calls: int | None,
) -> dict[str, object]:
    """Preserve Runner's historical fingerprint monkeypatch seams."""

    return _measurement_control_identity(
        case_id=case_id,
        baseline_skill_fingerprint=baseline_skill_fingerprint,
        replay_adaptation=replay_adaptation,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        capability_fingerprint=replay_capability_semantic_fingerprint,
        adaptation_fingerprint=replay_adaptation_semantic_fingerprint,
        support_fingerprint=replay_support_fingerprint,
    )


def _partial_replay_evaluator_dataset(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    candidate: CandidateVariant,
    normalized: NormalizedReplayMembers,
    minimum_independent_cases: int,
) -> tuple[SelfEvolveDataset | None, tuple[str, ...]]:
    """Preserve Runner's historical replay-comparability monkeypatch seam."""

    return _measurement_partial_dataset(
        dataset=dataset,
        replay_result=replay_result,
        candidate=candidate,
        normalized=normalized,
        minimum_independent_cases=minimum_independent_cases,
        replay_comparable=candidate_replay_is_comparable,
    )


_MAX_PROGRESS_REPAIR_EXTENSION_ITERATIONS = 6
_MAX_CONFORMANCE_STRATEGY_SWITCH_ATTEMPTS = 2
_SAFE_VERIFIED_TARGET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _verification_contract_fingerprint(**kwargs: object) -> str:
    """Compatibility seam for verification contract version upgrades."""

    return _generation_verification_contract_fingerprint(
        **kwargs,
        verification_contract_version=_VERIFICATION_CONTRACT_VERSION,
    )


@dataclass
class _RunBudgetContext:
    ledger: RunBudgetLedger
    cold_start_by_stage: Mapping[BudgetStage, BudgetUsage | None]
    backend_proven_zero_by_stage: Mapping[BudgetStage, bool] = field(
        default_factory=dict
    )
    decisions: list[dict[str, object]] = field(default_factory=list)
    debits: list[dict[str, object]] = field(default_factory=list)
    releases: list[dict[str, object]] = field(default_factory=list)

    def estimate(
        self,
        stage: BudgetStage,
        item_id: str,
        *,
        units: int = 1,
        backend_proven_zero: bool | None = None,
    ):
        return self.ledger.estimate_next(
            stage=stage,
            item_id=item_id,
            units=units,
            cold_start_per_unit=self.cold_start_by_stage.get(stage),
            backend_proven_zero=(
                backend_proven_zero
                if backend_proven_zero is not None
                else self.backend_proven_zero_by_stage.get(stage) is True
            ),
        )

    def can_fit(self, stage: BudgetStage, item_id: str, *, units: int = 1) -> bool:
        if self.ledger.ceilings.is_unbounded:
            return True
        estimate = self.estimate(stage, item_id, units=units)
        usage = estimate.resolved_usage()
        if usage is None:
            return False
        remaining = self.ledger.remaining()
        return bool(
            (remaining.tokens is None or usage.tokens <= remaining.tokens)
            and (remaining.cost_usd is None or usage.cost_usd <= remaining.cost_usd)
            and (
                remaining.wall_seconds is None
                or usage.wall_seconds <= remaining.wall_seconds
            )
        )

    def can_fit_workflow(
        self,
        work: Iterable[tuple[BudgetStage, str, int]],
    ) -> bool:
        if self.ledger.ceilings.is_unbounded:
            return True
        required = BudgetUsage()
        for stage, item_id, units in work:
            usage = self.estimate(stage, item_id, units=units).resolved_usage()
            if usage is None:
                return False
            required += usage
        remaining = self.ledger.remaining()
        return bool(
            (remaining.tokens is None or required.tokens <= remaining.tokens)
            and (remaining.cost_usd is None or required.cost_usd <= remaining.cost_usd)
            and (
                remaining.wall_seconds is None
                or required.wall_seconds <= remaining.wall_seconds
            )
        )

    def reserve(
        self,
        stage: BudgetStage,
        item_id: str,
        *,
        units: int = 1,
        backend_proven_zero: bool | None = None,
        request_derived_tokens: int | None = None,
    ) -> BudgetDecision:
        estimate = self.estimate(
            stage,
            item_id,
            units=units,
            backend_proven_zero=backend_proven_zero,
        )
        if request_derived_tokens is not None:
            if isinstance(request_derived_tokens, bool) or request_derived_tokens < 0:
                raise ValueError("request_derived_tokens must be non-negative")
            observed_estimate = estimate.source in {
                BudgetEstimateSource.OBSERVED_ROBUST,
                BudgetEstimateSource.OBSERVED_LOWER_BOUND,
            }
            resolved_tokens = (
                max(request_derived_tokens, estimate.tokens or 0)
                if observed_estimate
                else request_derived_tokens
            )
            estimate = replace(
                estimate,
                tokens=resolved_tokens,
                source=(
                    estimate.source
                    if observed_estimate
                    and (estimate.tokens or 0) >= request_derived_tokens
                    else BudgetEstimateSource.REQUEST_DERIVED
                ),
                confidence=(
                    estimate.confidence
                    if observed_estimate
                    and (estimate.tokens or 0) >= request_derived_tokens
                    else BudgetEstimateConfidence.MEDIUM
                ),
                backend_proven_zero=False,
            )
        decision = self.ledger.reserve(estimate)
        self.decisions.append(decision.to_dict())
        return decision

    def debit(
        self,
        decision: BudgetDecision,
        *,
        tokens: int | None = None,
        cost_usd: Decimal | None = None,
        wall_seconds: Decimal | None = None,
        usage_observation: BudgetUsageObservation | None = None,
        actual_source: str,
    ) -> None:
        if not decision.allowed or decision.reservation_id is None:
            return
        if usage_observation is not None and any(
            value is not None for value in (tokens, cost_usd, wall_seconds)
        ):
            raise ValueError(
                "usage_observation cannot be combined with dimension arguments"
            )
        observation = usage_observation or BudgetUsageObservation(
            known_lower_bound=BudgetUsage(
                tokens=0 if tokens is None else tokens,
                cost_usd=Decimal("0") if cost_usd is None else cost_usd,
                wall_seconds=(Decimal("0") if wall_seconds is None else wall_seconds),
            ),
            completeness=BudgetUsageCompleteness(
                tokens=tokens is not None,
                cost_usd=cost_usd is not None,
                wall_seconds=wall_seconds is not None,
            ),
        )
        result = self.ledger.debit_actual(
            decision.reservation_id,
            observation.known_lower_bound,
            actual_completeness=observation.completeness,
        )
        self.debits.append({**result.to_dict(), "actual_source": actual_source})

    def release(self, decision: BudgetDecision, *, reason_code: str) -> None:
        if not decision.allowed or decision.reservation_id is None:
            return
        reservation = self.ledger.release(decision.reservation_id)
        self.releases.append({**reservation.to_dict(), "reason_code": reason_code})

    def release_all(self, *, reason_code: str) -> None:
        for reservation in tuple(self.ledger.outstanding_reservations):
            released = self.ledger.release(reservation.reservation_id)
            self.releases.append({**released.to_dict(), "reason_code": reason_code})

    def release_all_best_effort(self, *, reason_code: str) -> None:
        """Release every surviving reservation without masking a run exception."""

        for reservation in tuple(self.ledger.outstanding_reservations):
            try:
                released = self.ledger.release(reservation.reservation_id)
            except BaseException:
                continue
            self.releases.append({**released.to_dict(), "reason_code": reason_code})

    def to_dict(self) -> dict[str, object]:
        return {
            "budget_mode": self.ledger.ceilings.budget_mode,
            "ledger": self.ledger.to_dict(),
            "decisions": list(self.decisions),
            "debits": list(self.debits),
            "releases": list(self.releases),
        }


def _remaining_measurement_budget(
    context: _RunBudgetContext,
) -> MeasurementUsage:
    remaining = context.ledger.remaining()
    return MeasurementUsage(
        tokens=remaining.tokens,
        cost_usd=(
            float(remaining.cost_usd) if remaining.cost_usd is not None else None
        ),
        wall_seconds=(
            float(remaining.wall_seconds)
            if remaining.wall_seconds is not None
            else None
        ),
    )


@dataclass
class _CandidateAttemptTracker:
    store: FilesystemSelfEvolveStore
    run_id: str
    _events: dict[CandidateAttemptKey, list[CandidateAttemptEvent]] = field(
        default_factory=dict
    )
    _candidate_keys: dict[str, CandidateAttemptKey] = field(default_factory=dict)

    def start(
        self,
        *,
        iteration: int,
        slot: int,
        candidate_id: str,
        usage: BudgetUsage | None = None,
    ) -> CandidateAttemptKey:
        key = CandidateAttemptKey(self.run_id, iteration, slot)
        self._append(
            key,
            CandidateAttemptStage.GENERATED,
            candidate_id=candidate_id,
            usage=usage,
        )
        self._candidate_keys.setdefault(candidate_id, key)
        return key

    def key_for_candidate(self, candidate_id: str) -> CandidateAttemptKey | None:
        return self._candidate_keys.get(candidate_id)

    def last_stage(self, key: CandidateAttemptKey) -> CandidateAttemptStage:
        return self._events[key][-1].stage

    def terminal(self, key: CandidateAttemptKey) -> bool:
        return self._events[key][-1].terminal

    def has_stage(
        self,
        key: CandidateAttemptKey,
        *stages: CandidateAttemptStage,
    ) -> bool:
        expected = set(stages)
        return any(event.stage in expected for event in self._events.get(key, ()))

    def finalize_open(self, *, reason_code: str) -> None:
        for key in sorted(self._events):
            if not self.terminal(key):
                self.emit(
                    key,
                    CandidateAttemptStage.NOT_RUN,
                    reason_code=reason_code,
                )

    def block_open_best_effort(self, *, reason_code: str) -> None:
        """Fail closed after an unhandled run error while preserving that error."""

        for key in sorted(self._events):
            try:
                if self.terminal(key):
                    continue
                self.emit(
                    key,
                    CandidateAttemptStage.BLOCKED,
                    reason_code=reason_code,
                )
            except BaseException:
                continue

    def emit(
        self,
        key: CandidateAttemptKey,
        stage: CandidateAttemptStage,
        *,
        reason_code: str | None = None,
        failure_event_id: str | None = None,
        semantic_failure_key: str | None = None,
        usage: BudgetUsage | None = None,
        case_count: int | None = None,
        distinct_conformance_shape_count: int | None = None,
    ) -> CandidateAttemptEvent:
        candidate_id = self._events[key][0].candidate_id
        return self._append(
            key,
            stage,
            candidate_id=candidate_id,
            reason_code=reason_code,
            failure_event_id=failure_event_id,
            semantic_failure_key=semantic_failure_key,
            usage=usage,
            case_count=case_count,
            distinct_conformance_shape_count=distinct_conformance_shape_count,
        )

    def _append(
        self,
        key: CandidateAttemptKey,
        stage: CandidateAttemptStage,
        *,
        candidate_id: str,
        reason_code: str | None = None,
        failure_event_id: str | None = None,
        semantic_failure_key: str | None = None,
        usage: BudgetUsage | None = None,
        case_count: int | None = None,
        distinct_conformance_shape_count: int | None = None,
    ) -> CandidateAttemptEvent:
        values = self._events.get(key, ())
        event = CandidateAttemptEvent(
            key=key,
            sequence=len(values),
            stage=stage,
            candidate_id=candidate_id,
            reason_code=reason_code,
            failure_event_id=failure_event_id,
            semantic_failure_key=semantic_failure_key,
            usage=usage or BudgetUsage(),
            case_count=case_count,
            distinct_conformance_shape_count=distinct_conformance_shape_count,
        )
        try:
            self.store.append_candidate_attempt_event(event)
        except BaseException:
            # An fsync/rename boundary can raise after the event became
            # durable. Reconcile only the exact deterministic event id, then
            # preserve the storage exception for the caller.
            try:
                persisted = self.store.read_candidate_attempt_events(key)
            except BaseException:
                persisted = ()
            if any(item.event_id == event.event_id for item in persisted):
                self._events[key] = list(persisted)
            raise
        self._events.setdefault(key, []).append(event)
        return event


@dataclass
class _RunFailureCleanup:
    """Bindings used by the public runner boundary for fail-closed cleanup."""

    budget_context: _RunBudgetContext | None = None
    attempt_tracker: _CandidateAttemptTracker | None = None

    def cleanup(self) -> None:
        if self.attempt_tracker is not None:
            try:
                self.attempt_tracker.block_open_best_effort(
                    reason_code="run_unhandled_exception"
                )
            except BaseException:
                pass
        if self.budget_context is not None:
            try:
                self.budget_context.release_all_best_effort(
                    reason_code="run_unhandled_exception_cleanup"
                )
            except BaseException:
                pass


def _configured_budget_usage(
    *,
    tokens: int | None,
    cost_usd: float | Decimal | None,
    wall_seconds: float | Decimal | None,
    token_ceiling: int | None,
    cost_ceiling: Decimal | None,
    wall_ceiling: Decimal | None,
) -> BudgetUsage | None:
    """Resolve a complete configured estimate without confusing zero with unknown."""

    if (
        (token_ceiling is not None and tokens is None)
        or (cost_usd is None and cost_ceiling is not None)
        or (wall_seconds is None and wall_ceiling is not None)
    ):
        return None
    usage = BudgetUsage(
        tokens=0 if tokens is None else tokens,
        cost_usd=(Decimal("0") if cost_usd is None else Decimal(str(cost_usd))),
        wall_seconds=(
            Decimal("0") if wall_seconds is None else Decimal(str(wall_seconds))
        ),
    )
    # A wholly zero configured estimate still requires an explicit backend
    # proof. A mixed estimate (for example local conformance: zero model
    # tokens/cost but bounded wall time) is complete and safe to reserve.
    return None if usage == BudgetUsage() else usage


def _judge_actual_token_usage(
    *summaries: EvaluationSummary | None,
    expected_summary_count: int | None = None,
) -> tuple[int | None, str]:
    """Return complete usage or the strongest observed token lower bound."""

    total = 0
    sources: set[str] = set()
    executed = _unique_evaluation_summaries(
        summary
        for summary in summaries
        if summary is not None
        and summary.dataset_split != "single_case_replay"
        and summary.metrics.get("evaluation_fresh_execution") is not False
    )
    expected = (
        len(executed) if expected_summary_count is None else expected_summary_count
    )
    if isinstance(expected, bool) or expected < 0:
        raise ValueError("expected_summary_count must be non-negative")
    complete = len(executed) == expected
    for summary in executed:
        metrics = summary.metrics
        raw_total = metrics.get("judge_total_tokens")
        if (
            isinstance(raw_total, int)
            and not isinstance(raw_total, bool)
            and raw_total >= 0
        ):
            total += raw_total
            sources.add("judge_total_tokens")
            continue
        raw_input = metrics.get("judge_input_tokens_total")
        raw_output = metrics.get("judge_output_tokens_total")
        if all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (raw_input, raw_output)
        ):
            total += int(raw_input) + int(raw_output)
            sources.add("judge_input_output_tokens")
            continue
        estimated_input = metrics.get("judge_estimated_input_tokens_total")
        if (
            isinstance(estimated_input, (int, float))
            and not isinstance(estimated_input, bool)
            and estimated_input >= 0
        ):
            total += int(estimated_input)
            sources.add("judge_estimated_input_tokens_lower_bound")
            complete = False
            continue
        complete = False
    if not executed:
        return None, "reserved_fallback_missing_judge_telemetry"
    if not complete:
        return (
            total,
            "known_lower_bound_incomplete_judge_telemetry:"
            + ("+".join(sorted(sources)) or "missing_dimensions"),
        )
    return total, "+".join(sorted(sources))


def _unique_evaluation_summaries(
    summaries: Iterable[EvaluationSummary],
) -> tuple[EvaluationSummary, ...]:
    unique: list[EvaluationSummary] = []
    seen: set[str] = set()
    for index, summary in enumerate(summaries):
        metrics = summary.metrics
        execution_id = metrics.get("evaluation_alias_of_execution_id") or metrics.get(
            "evaluation_execution_id"
        )
        if not isinstance(execution_id, str) or not execution_id:
            if summary.dataset_split == "single_case_replay":
                continue
            execution_id = (
                f"legacy:{index}:{summary.variant_id}:{summary.dataset_split}"
            )
        if execution_id in seen:
            continue
        seen.add(execution_id)
        unique.append(summary)
    return tuple(unique)


def _same_evaluation_execution(
    first: EvaluationSummary,
    second: EvaluationSummary,
) -> bool:
    def execution_id(summary: EvaluationSummary) -> object:
        return summary.metrics.get(
            "evaluation_alias_of_execution_id"
        ) or summary.metrics.get("evaluation_execution_id")

    first_id = execution_id(first)
    second_id = execution_id(second)
    return isinstance(first_id, str) and bool(first_id) and first_id == second_id


@dataclass(frozen=True)
class SelfEvolveRunnerResult:
    run: SelfEvolveRun
    selected_candidate: CandidateVariant | None


def _terminal_candidate_evaluation_result(
    *,
    candidate: CandidateVariant,
    iteration_number: int,
    candidate_number: int,
    candidate_count: int,
    gate_results: Iterable[GateResult],
    status: str = "rejected",
    replay_result: CandidateReplayResult | None = None,
    replay_dataset: SelfEvolveDataset | None = None,
) -> tuple[dict[str, object], dict[str, object], tuple[EvaluationSummary, ...]]:
    return _controller_terminal_candidate_result(
        candidate=candidate,
        iteration_number=iteration_number,
        candidate_number=candidate_number,
        candidate_count=candidate_count,
        gate_results=gate_results,
        feedback_builder=_iteration_validation_feedback,
        status=status,
        replay_result=replay_result,
        replay_dataset=replay_dataset,
    ).as_tuple()


def _typed_terminal_candidate_evaluation_result(
    *,
    candidate: CandidateVariant,
    iteration_number: int,
    candidate_number: int,
    candidate_count: int,
    gate_results: Iterable[GateResult],
    status: str = "rejected",
    replay_result: CandidateReplayResult | None = None,
    replay_dataset: SelfEvolveDataset | None = None,
) -> CandidateEvaluationResult:
    """Adapt the historical terminal tuple to the typed evaluation result."""

    return _controller_terminal_candidate_result(
        candidate=candidate,
        iteration_number=iteration_number,
        candidate_number=candidate_number,
        candidate_count=candidate_count,
        gate_results=gate_results,
        feedback_builder=_iteration_validation_feedback,
        status=status,
        replay_result=replay_result,
        replay_dataset=replay_dataset,
    )


def _backend_proves_zero_budget_usage(
    backend: object | None,
    stage: BudgetStage,
) -> bool:
    """Accept only an explicit stage-scoped backend proof of zero usage."""

    if not isinstance(backend, ZeroBudgetUsageProofProvider):
        return False
    try:
        return backend.proves_zero_budget_usage(stage) is True
    except Exception:
        # A broken optional capability must fail closed to normal reservation.
        return False


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
    judge_summaries: list[EvaluationSummary] = []
    for state in iteration_states:
        summaries = [
            state.get(key)
            for key in (
                "baseline_summary",
                "candidate_summary",
                "held_out_summary",
            )
        ]
        evidence = state.get("regression_evidence")
        if isinstance(evidence, RegressionEvidence):
            summaries.extend(
                summary
                for result in evidence.suite_results
                for summary in (
                    result.baseline_summary,
                    result.candidate_summary,
                )
                if result.fresh_execution
            )
        judge_summaries.extend(
            summary for summary in summaries if isinstance(summary, EvaluationSummary)
        )
    for summary in _unique_evaluation_summaries(judge_summaries):
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
            "scheduled_tasks": _non_negative_int(evaluation_stage.get("item_count")),
            "judge_attempt_count": judge_attempt_count,
        },
        "candidate_generation_usage": {
            "scheduled_slots": _non_negative_int(candidate_stage.get("item_count")),
        },
    }


_DEFAULT_CANDIDATE_GENERATION_ESTIMATED_TOKENS = 65_536
_SCREENING_CONTROL_HARNESS_ID = "aworld.self_evolve.screening_harness.v2"


def _screening_control_harness_fingerprint() -> str:
    return (
        "sha256:"
        + hashlib.sha256(_SCREENING_CONTROL_HARNESS_ID.encode("utf-8")).hexdigest()
    )


def _accumulate_score_evidence(
    initial: EvaluationSummary,
    additional: EvaluationSummary,
) -> EvaluationSummary:
    """Pool compatible ordered judge samples without discarding prior evidence."""

    initial_metrics = dict(initial.metrics)
    additional_metrics = dict(additional.metrics)
    initial_plan = initial_metrics.get("comparison_plan_fingerprint")
    additional_plan = additional_metrics.get("comparison_plan_fingerprint")
    compatible = (
        initial.variant_id == additional.variant_id
        and initial.dataset_split == additional.dataset_split
        and isinstance(initial_plan, str)
        and bool(initial_plan)
        and initial_plan == additional_plan
    )
    initial_samples = _finite_score_samples(initial_metrics.get("score_samples"))
    additional_samples = _finite_score_samples(additional_metrics.get("score_samples"))
    if not compatible or not initial_samples or not additional_samples:
        additional_metrics["score_evidence_accumulation"] = {
            "status": "incompatible",
            "initial_sample_count": len(initial_samples),
            "additional_sample_count": len(additional_samples),
        }
        return replace(additional, metrics=additional_metrics)

    samples = (*initial_samples, *additional_samples)
    additional_metrics.update(
        {
            "score": statistics.mean(samples),
            "score_samples": list(samples),
            "score_sample_count": len(samples),
            "score_std": statistics.stdev(samples) if len(samples) >= 2 else 0.0,
            "score_evidence_round_count": (
                _positive_metric_count(
                    initial_metrics.get("score_evidence_round_count")
                )
                or 1
            )
            + 1,
            "score_evidence_accumulation": {
                "status": "pooled",
                "initial_sample_count": len(initial_samples),
                "additional_sample_count": len(additional_samples),
                "pooled_sample_count": len(samples),
                "execution_ids": list(
                    dict.fromkeys(
                        str(value)
                        for value in (
                            initial_metrics.get("evaluation_execution_id"),
                            additional_metrics.get("evaluation_execution_id"),
                        )
                        if isinstance(value, str) and value
                    )
                ),
            },
        }
    )
    for key in (
        "judge_attempt_count",
        "judge_success_count",
        "judge_failure_count",
        "judge_timeout_count",
    ):
        initial_count = _nonnegative_numeric_count(initial_metrics.get(key))
        additional_count = _nonnegative_numeric_count(additional_metrics.get(key))
        if initial_count is not None or additional_count is not None:
            additional_metrics[key] = (initial_count or 0) + (additional_count or 0)
    return replace(additional, metrics=additional_metrics)


def _finite_score_samples(value: object) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    samples: list[float] = []
    for item in value:
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            return ()
        samples.append(float(item))
    return tuple(samples)


def _nonnegative_numeric_count(value: object) -> int | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    ):
        return int(value)
    return None


def _positive_metric_count(value: object) -> int:
    count = _nonnegative_numeric_count(value)
    return count if count is not None and count > 0 else 0


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


def _repair_conformance_validation_surface_changed(
    current: RepairConformanceContract,
    evolved: Mapping[str, object],
) -> bool:
    """Return whether a failed sibling discovered a shared new constraint.

    Contract identity also includes candidate lineage, failure codes, and base
    source fingerprints. Those fields legitimately change when one sibling has
    a local compile bug (for example a duplicate service id), but they do not
    invalidate another sibling that already passed the shared probes. Only a
    change to the executable validation surface may supersede passed/stale
    siblings.
    """

    try:
        evolved_contract = RepairConformanceContract.from_public_dict(evolved)
    except (TypeError, ValueError):
        # Fail closed for an unparseable evolved contract.
        return True

    def surface(contract: RepairConformanceContract) -> tuple[object, ...]:
        return (
            contract.required_branch_paths,
            contract.manifest_path,
            contract.compiler_path,
            contract.runtime_paths,
            contract.exact_probe,
            contract.late_observed_operations,
            contract.requires_compiler_fixture_reconstruction,
            contract.requires_fixture_derived_probe,
            contract.required_fixture_probe_operations,
            contract.fixture_probe_constraints,
            contract.schema_field_constraints,
            contract.runtime_response_constraints,
            contract.runtime_artifact_constraints,
            contract.required_runtime_transitions,
            contract.artifact_lifecycle_constraint,
        )

    return surface(current) != surface(evolved_contract)


def _candidate_generation_failure_events(
    optimizer_diagnostics: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    failures: list[dict[str, object]] = []
    policy_events: list[dict[str, object]] = []
    protocol_events: list[dict[str, object]] = []
    for item in _optimizer_iteration_diagnostics(optimizer_diagnostics):
        failures.extend(_candidate_materialization_failures(item))
        policy_events.extend(_candidate_policy_filter_events(item))
        protocol_events.extend(_candidate_protocol_failure_events(item))
    materialization_events = _candidate_materialization_failure_events(failures)
    events: list[dict[str, object]] = []
    seen_semantic_keys: set[str] = set()
    for event in (*materialization_events, *policy_events, *protocol_events):
        semantic_key = str(event["semantic_key"])
        if semantic_key in seen_semantic_keys:
            continue
        seen_semantic_keys.add(semantic_key)
        events.append(event)
    return tuple(events)


def _candidate_protocol_failure_events(
    diagnostics: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    raw_outcomes = diagnostics.get("candidate_generation_outcomes")
    if not isinstance(raw_outcomes, (list, tuple)):
        return ()
    events: list[dict[str, object]] = []
    seen_semantic_keys: set[str] = set()
    for item in raw_outcomes[:64]:
        if not isinstance(item, Mapping):
            continue
        try:
            outcome = CandidateGenerationOutcome.from_dict(item)
        except (TypeError, ValueError):
            continue
        if outcome.kind is not CandidateGenerationOutcomeKind.PROTOCOL_INVALID:
            continue
        code = (
            outcome.reason_codes[0]
            if outcome.reason_codes
            else ("candidate_protocol_invalid")
        )
        event = ReplayFailureEvent(
            code=code,
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CANDIDATE_GENERATION,
            scope=FailureScope.CANDIDATE,
            repairable=outcome.repairable,
            category="candidate_generation",
            summary="candidate response violated the generation protocol",
            diagnostics={
                "candidate_index": outcome.candidate_index,
                "active_frontier_key": outcome.active_frontier_key,
            },
            requirement_id=f"candidate-protocol/{code}",
        ).to_dict()
        semantic_key = str(event["semantic_key"])
        if semantic_key in seen_semantic_keys:
            continue
        seen_semantic_keys.add(semantic_key)
        events.append(event)
    return tuple(events)


def _candidate_policy_filter_outcomes(
    diagnostics: Mapping[str, object],
) -> tuple[CandidateGenerationOutcome, ...]:
    raw_outcomes = diagnostics.get("candidate_generation_outcomes")
    if not isinstance(raw_outcomes, (list, tuple)):
        return ()
    outcomes: list[CandidateGenerationOutcome] = []
    for item in raw_outcomes[:64]:
        if not isinstance(item, Mapping):
            continue
        try:
            outcome = CandidateGenerationOutcome.from_dict(item)
        except (TypeError, ValueError):
            continue
        if outcome.kind is CandidateGenerationOutcomeKind.POLICY_FILTERED:
            outcomes.append(outcome)
    return tuple(outcomes)


def _candidate_policy_frontier_stalled_event(
    outcomes: Sequence[CandidateGenerationOutcome],
) -> dict[str, object]:
    policy_ids = tuple(
        sorted({str(outcome.policy_id) for outcome in outcomes if outcome.policy_id})
    )
    constraint_ids = tuple(
        sorted(
            {
                constraint_id
                for outcome in outcomes
                for constraint_id in outcome.constraint_ids
            }
        )
    )
    signature = _candidate_policy_filter_signature(outcomes) or "unknown"
    event = ReplayFailureEvent(
        code="candidate_generation_policy_frontier_stalled",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CANDIDATE_GENERATION,
        scope=FailureScope.CANDIDATE,
        repairable=False,
        category="candidate_generation_policy",
        summary="generation policy frontier repeated without structural progress",
        diagnostics={
            "policy_ids": list(policy_ids),
            "constraint_ids": list(constraint_ids),
            "filter_signature": signature,
            "consecutive_stall_limit": _MAX_CONSECUTIVE_POLICY_FILTER_STALLS,
        },
        requirement_id=signature,
    )
    return event.to_dict()


def _candidate_policy_filter_events(
    diagnostics: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    seen_semantic_keys: set[str] = set()
    for outcome in _candidate_policy_filter_outcomes(diagnostics):
        event = _candidate_policy_filter_event(outcome)
        semantic_key = str(event["semantic_key"])
        if semantic_key in seen_semantic_keys:
            continue
        seen_semantic_keys.add(semantic_key)
        events.append(event)
    return tuple(events)


def _candidate_generation_failure_event(
    optimizer_diagnostics: Iterable[Mapping[str, object]],
) -> dict[str, object] | None:
    events = _candidate_generation_failure_events(optimizer_diagnostics)
    return events[0] if events else None


def _replay_adaptation_exception_details(
    exc: Exception,
    *,
    candidate_capability: bool,
) -> dict[str, object]:
    reason = sanitize_text(str(exc), max_chars=240)
    if candidate_capability:
        diagnostic = {
            "code": "invalid_replay_capability_compile",
            "stage": "capability_compile",
            "failure_class": "candidate",
            "repairable": True,
            "reason": reason,
            "required_manifest_contract": {
                "schema_version": REPLAY_CAPABILITY_SCHEMA_VERSION,
                "protocol": REPLAY_CAPABILITY_PROTOCOL_VERSION,
                "handles_values": list(REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS),
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
                "capability_identity": (
                    "copy request.capability_id exactly into result.capability_id"
                ),
                "service_transport_values": list(
                    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS
                ),
                "service_readiness_contract": (
                    "every services[*] item must emit readiness.kind; the "
                    "requirement applies to every wildcard-selected service, "
                    "not only skill_runtime or runtime_required branches"
                ),
                "service_readiness_kind_values": list(
                    REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS
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
        if isinstance(exc, ReplayCapabilityError):
            if exc.code:
                diagnostic["capability_error_code"] = exc.code
            diagnostic.update(exc.details)
        details: dict[str, object] = {
            "failure_class": "candidate",
            "failure_owner": FailureOwner.CANDIDATE.value,
            "failure_scope": FailureScope.CANDIDATE.value,
            "failure_source": FailureEventSource.NATIVE.value,
            "repairable": True,
            "diagnostics": [diagnostic],
        }
        if isinstance(exc, ReplayCapabilityError):
            if exc.code:
                details["capability_error_code"] = exc.code
            details.update(exc.details)
        failure_event = ReplayFailureEvent(
            code=(
                exc.code
                if isinstance(exc, ReplayCapabilityError) and exc.code
                else "invalid_replay_capability_compile"
            ),
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CAPABILITY_COMPILE,
            scope=FailureScope.CANDIDATE,
            repairable=True,
            category="replay_capability",
            summary=reason,
            contract_fingerprint=_schema_field_contract_fingerprint(details),
        )
        details["failure_event"] = failure_event.to_dict()
        details["causal_failure_events"] = [failure_event.to_dict()]
        return details
    return {
        "failure_class": "infrastructure",
        "failure_owner": FailureOwner.INFRASTRUCTURE.value,
        "failure_scope": FailureScope.SHARED_RUN.value,
        "failure_source": FailureEventSource.NATIVE.value,
        "repairable": False,
        "code": "replay_adaptation_infrastructure_error",
    }


def _repair_contract_fingerprints(
    details: Mapping[str, object],
) -> tuple[str, ...]:
    """Return full and component identities for frontier-resolution matching."""

    fingerprints: set[str] = set()
    direct = _schema_field_contract_fingerprint(details)
    if direct is not None:
        fingerprints.add(direct)
    projected = details.get("repair_conformance")
    if isinstance(projected, Mapping):
        combined = _schema_field_contract_fingerprint(projected)
        if combined is not None:
            fingerprints.add(combined)
        for field_name in (
            "schema_field_constraints",
            "runtime_response_constraints",
            "runtime_artifact_constraints",
        ):
            component = _schema_field_contract_fingerprint(
                {field_name: projected.get(field_name)}
            )
            if component is not None:
                fingerprints.add(component)
    return tuple(sorted(fingerprints))


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
                failure.get("code") or "candidate_generation_infrastructure_error"
            ),
            "retryable": _retryable_candidate_generation_failure(failure),
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
            "retryable": _retryable_infrastructure_details(details),
        }
        error_type = details.get("type")
        if isinstance(error_type, str) and error_type:
            cause["error_type"] = error_type
        return cause
    return {
        "failure_class": "infrastructure",
        "stage": "self_evolve",
        "code": "infrastructure_error",
        "retryable": False,
    }


def _retryable_infrastructure_details(details: Mapping[str, object]) -> bool:
    if details.get("retryable") is True or details.get("repairable") is True:
        return True
    error_type = (
        str(details.get("error_type") or details.get("type") or "").strip().casefold()
    )
    return error_type in {
        "apiconnectionerror",
        "apitimeouterror",
        "connectionerror",
        "llmresponseerror",
        "ratelimiterror",
        "timeouterror",
    }


def _rejection_attribution(
    *,
    final_status: SelfEvolveRunStatus,
    selected_candidate_id: str | None,
    gate_results: Iterable[GateResult],
    scheduler_decisions: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    if final_status is not SelfEvolveRunStatus.REJECTED:
        return None
    failed = [gate for gate in gate_results if not gate.passed]
    if not failed:
        return None
    substantive = [
        gate
        for gate in failed
        if gate.gate_name
        not in {
            "duplicate_accepted_candidate",
            "duplicate_rejected_candidate",
            "candidate_generation_exhausted_by_semantic_dedup",
        }
    ]
    # Gate execution order must not decide campaign ownership.  Prefer an
    # actionable candidate repair over a simultaneous framework uncertainty
    # signal (for example evidence_quality plus noisy score evidence).
    actionable_candidate_failures = [
        gate for gate in substantive if _gate_has_candidate_owned_repair(gate)
    ]
    primary = (
        actionable_candidate_failures[0]
        if actionable_candidate_failures
        else substantive[0]
        if substantive
        else failed[0]
    )
    details = primary.details if isinstance(primary.details, Mapping) else {}
    attribution: dict[str, object] = {
        "candidate_id": selected_candidate_id,
        "primary_gate": primary.gate_name,
        "primary_reason": sanitize_text(primary.reason, max_chars=400),
        "failure_class": str(details.get("failure_class") or "candidate"),
        "code": str(details.get("code") or primary.gate_name),
        "duplicate_only": not substantive,
    }
    for key in (
        "failure_owner",
        "failure_scope",
        "failure_stage",
        "repairable",
        "next_action",
        "resume_safe",
        "resume_candidate_id",
        "resume_candidate_package_fingerprint",
        "completed_baseline_case_count",
        "completed_candidate_case_count",
        "completed_comparable_pair_count",
        "pending_case_count",
    ):
        if details.get(key) is not None:
            attribution[key] = details[key]
    diagnostic_refs = _attribution_diagnostic_refs(details)
    if diagnostic_refs:
        attribution["diagnostic_refs"] = list(diagnostic_refs)
    capability_error_code = details.get("capability_error_code")
    if isinstance(capability_error_code, str) and capability_error_code:
        attribution["capability_error_code"] = capability_error_code
    if scheduler_decisions:
        terminal_decision = scheduler_decisions[-1]
        scheduler_reason_code = str(terminal_decision.get("reason_code") or "unknown")
        attribution["scheduler_reason_code"] = scheduler_reason_code
        attribution["scheduler_stop"] = terminal_decision.get("stop") is True
        if (
            attribution["scheduler_stop"] is True
            and scheduler_reason_code == "shared_run_blocked"
        ):
            attribution["failure_class"] = "framework"
            attribution["code"] = "shared_run_blocked"
        if (
            attribution["scheduler_stop"] is True
            and scheduler_reason_code == "repair_frontier_stalled"
            and primary.gate_name in {"candidate_generation", "no_candidate"}
        ):
            attribution["code"] = "candidate_repair_frontier_stalled"
    return attribution


def _campaign_failure_attribution(
    iteration_states: Iterable[Mapping[str, object]],
    *,
    generation_stop_reason: str | None,
    terminal_gates: Iterable[GateResult] = (),
    resolved_contract_fingerprints: Iterable[str] = (),
) -> dict[str, object] | None:
    """Attribute a rejected search to its dominant typed failure frontier.

    ``rejection_attribution`` explains the selected representative candidate.
    A campaign can reject many candidates before that selection, so using only
    the representative can surface an incidental Markdown error while hiding a
    repeated compiler/runtime frontier.  This aggregate is candidate-deduped and
    keeps the two concepts separate.
    """

    resolved_contracts = set(resolved_contract_fingerprints)
    for gate in terminal_gates:
        if gate.passed or not isinstance(gate.details, Mapping):
            continue
        details = gate.details
        owner = str(details.get("failure_owner") or "")
        scope = str(details.get("failure_scope") or "")
        failure_class = str(details.get("failure_class") or "")
        if (
            owner in {"framework", "infrastructure"}
            and scope == "shared_run"
            and failure_class in {"framework", "infrastructure", "measurement"}
        ):
            result: dict[str, object] = {
                "primary_gate": gate.gate_name,
                "code": str(details.get("code") or gate.gate_name),
                "failure_class": failure_class,
                "failure_owner": owner,
                "failure_scope": scope,
                "primary_reason": sanitize_text(gate.reason, max_chars=400),
                "occurrence_count": 1,
                "affected_candidate_count": 0,
                "affected_candidate_ids": [],
                "resolved_failure_count": len(resolved_contracts),
            }
            for key in (
                "next_action",
                "repairable",
                "failure_stage",
                "resume_safe",
                "resume_candidate_id",
                "resume_candidate_package_fingerprint",
                "completed_baseline_case_count",
                "completed_candidate_case_count",
                "completed_comparable_pair_count",
                "pending_case_count",
            ):
                if details.get(key) is not None:
                    result[key] = details[key]
            diagnostic_refs = _attribution_diagnostic_refs(details)
            if diagnostic_refs:
                result["diagnostic_refs"] = list(diagnostic_refs)
            if generation_stop_reason is not None:
                result["generation_stop_reason"] = generation_stop_reason
            return result

    groups: dict[
        tuple[str, str, str, str | None],
        dict[str, object],
    ] = {}
    seen_attempts: set[tuple[str, str, str, str | None]] = set()
    for state in iteration_states:
        # A verified evaluation-support package is an intermediate prerequisite,
        # not a rejected campaign frontier.  Its target_behavior_delta gate is
        # intentionally deferred until the composed behavior candidate exists.
        # Counting it as a terminal failure can hide the later authoritative
        # candidate's real replay failure when both occur once.
        if state.get("status") == "prerequisite":
            continue
        candidate = state.get("candidate")
        candidate_id = (
            candidate.candidate_id if isinstance(candidate, CandidateVariant) else None
        )
        raw_gates = state.get("gate_results")
        if not isinstance(raw_gates, (list, tuple)):
            continue
        for gate in raw_gates:
            if not isinstance(gate, GateResult) or gate.passed:
                continue
            if gate.gate_name in {
                "duplicate_accepted_candidate",
                "duplicate_rejected_candidate",
            }:
                continue
            details = gate.details if isinstance(gate.details, Mapping) else {}
            code = str(details.get("code") or gate.gate_name)
            contract_fingerprint = _repair_contract_fingerprint(details)
            if (
                gate.gate_name == "candidate_repair_conformance"
                and contract_fingerprint in resolved_contracts
            ):
                continue
            attempt_identity = (
                candidate_id or "<none>",
                gate.gate_name,
                code,
                contract_fingerprint,
            )
            if attempt_identity in seen_attempts:
                continue
            seen_attempts.add(attempt_identity)
            key = (
                gate.gate_name,
                code,
                str(details.get("failure_class") or "candidate"),
                contract_fingerprint,
            )
            group = groups.setdefault(
                key,
                {
                    "primary_gate": gate.gate_name,
                    "code": code,
                    "failure_class": str(details.get("failure_class") or "candidate"),
                    "primary_reason": sanitize_text(gate.reason, max_chars=400),
                    "occurrence_count": 0,
                    "candidate_ids": set(),
                    "contract_fingerprint": contract_fingerprint,
                    "failure_owner": details.get("failure_owner"),
                    "failure_scope": details.get("failure_scope"),
                    "failure_stage": details.get("failure_stage"),
                    "repairable": details.get("repairable"),
                    "next_action": details.get("next_action"),
                    "diagnostic_refs": set(_attribution_diagnostic_refs(details)),
                },
            )
            refs = group.get("diagnostic_refs")
            if isinstance(refs, set):
                refs.update(_attribution_diagnostic_refs(details))
            group["occurrence_count"] = int(group["occurrence_count"]) + 1
            if candidate_id is not None:
                candidate_ids = group["candidate_ids"]
                assert isinstance(candidate_ids, set)
                candidate_ids.add(candidate_id)
    if not groups:
        return None
    primary = dict(
        max(
            groups.values(),
            key=lambda item: (
                len(item["candidate_ids"]),
                int(item["occurrence_count"]),
                item["failure_class"] == "candidate",
                str(item["primary_gate"]),
            ),
        )
    )
    candidate_ids = primary.pop("candidate_ids")
    diagnostic_refs = primary.pop("diagnostic_refs", set())
    assert isinstance(candidate_ids, set)
    result = {
        **primary,
        "affected_candidate_count": len(candidate_ids),
        "affected_candidate_ids": sorted(candidate_ids)[:16],
        "resolved_failure_count": len(resolved_contracts),
    }
    if isinstance(diagnostic_refs, set) and diagnostic_refs:
        result["diagnostic_refs"] = sorted(diagnostic_refs)[:16]
    for optional_key in (
        "failure_owner",
        "failure_scope",
        "failure_stage",
        "repairable",
        "next_action",
    ):
        if result.get(optional_key) is None:
            result.pop(optional_key, None)
    if result.get("contract_fingerprint") is None:
        result.pop("contract_fingerprint", None)
    if generation_stop_reason is not None:
        result["generation_stop_reason"] = generation_stop_reason
    return result


def _attribution_diagnostic_refs(
    value: object,
) -> tuple[str, ...]:
    """Collect bounded artifact references from typed failure details."""

    refs: set[str] = set()
    pending: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 512 and len(refs) < 16:
        current, depth = pending.pop()
        visited += 1
        if depth > 8:
            continue
        if isinstance(current, Mapping):
            for key in ("artifact_refs", "diagnostic_refs", "evidence_refs"):
                raw = current.get(key)
                if not isinstance(raw, (list, tuple)):
                    continue
                for item in raw[:16]:
                    text = str(item).strip()
                    if text and "\n" not in text and "\r" not in text:
                        refs.add(text[:500])
            for nested in current.values():
                if isinstance(nested, (Mapping, list, tuple)):
                    pending.append((nested, depth + 1))
        elif isinstance(current, (list, tuple)):
            for nested in current[:128]:
                if isinstance(nested, (Mapping, list, tuple)):
                    pending.append((nested, depth + 1))
    return tuple(sorted(refs))[:16]


def _resolved_conformance_contract_fingerprints(
    validation_reports: Iterable[Mapping[str, object]],
) -> tuple[str, ...]:
    """Return typed repair frontiers closed by a later conformance success."""

    resolved: set[str] = set()
    for report in validation_reports:
        conformance = report.get("conformance")
        attempts = (
            conformance.get("attempts") if isinstance(conformance, Mapping) else None
        )
        if not isinstance(attempts, (list, tuple)):
            continue
        for attempt in attempts:
            if not isinstance(attempt, Mapping) or attempt.get("passed") is not True:
                continue
            details = attempt.get("details")
            if not isinstance(details, Mapping):
                continue
            resolved.update(_repair_contract_fingerprints(details))
    return tuple(sorted(resolved))


_DEFAULT_CANDIDATE_CONTENT_MAX_CHARS = 500_000


class SelfEvolveRunner:
    def __init__(
        self,
        *,
        store: FilesystemSelfEvolveStore,
        optimizer: CandidateOptimizer,
        post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
        evaluation_backend: EvaluationBackend | None = None,
        regression_backend: EvaluationBackend | None = None,
        regression_suites: tuple[ResolvedRegressionSuite, ...] = (),
        challenger_backend: ChallengerBackend | None = None,
        challenger_enabled: bool = True,
        challenger_max_cases: int = DEFAULT_CHALLENGE_CASES,
        min_score_delta: float = 0.0,
        pending_duplicate: bool = False,
        max_iterations: int = 1,
        min_eval_cases: int = 30,
        judge_repetitions: int = 3,
        max_run_tokens: int | None = None,
        total_run_token_budget: int | None = None,
        per_attempt_replay_token_limit: int | None = None,
        max_run_cost_usd: float | Decimal | None = None,
        max_run_wall_seconds: float | Decimal | None = None,
        candidate_generation_tokens_per_unit: int | None = (
            _DEFAULT_CANDIDATE_GENERATION_ESTIMATED_TOKENS
        ),
        candidate_generation_output_tokens_per_unit: int = 16_000,
        candidate_generation_model_name: str = "gpt-4o",
        candidate_generation_cost_usd_per_unit: float | Decimal | None = Decimal(
            "0.05"
        ),
        candidate_generation_wall_seconds_per_unit: float | Decimal | None = Decimal(
            "120"
        ),
        candidate_screening_tokens_per_unit: int | None = 4_096,
        candidate_screening_cost_usd_per_unit: float | Decimal | None = Decimal("0.05"),
        candidate_screening_wall_seconds_per_unit: float | Decimal | None = (
            Decimal("210")
        ),
        replay_tokens_per_unit: int | None = 4_096,
        replay_cost_usd_per_unit: float | Decimal | None = Decimal("0.05"),
        replay_wall_seconds_per_unit: float | Decimal | None = Decimal("600"),
        evaluation_tokens_per_unit: int | None = 2_048,
        evaluation_cost_usd_per_unit: float | Decimal | None = Decimal("0.02"),
        evaluation_wall_seconds_per_unit: float | Decimal | None = Decimal("60"),
        deprecated_config_mappings: Iterable[str] | Mapping[str, str] | None = None,
        auto_apply_target_types: tuple[str, ...] = ("skill",),
        allow_generated_target_mutation: bool = False,
        allow_external_target_mutation: bool = False,
        inferred_new_skill_policy: InferredNewSkillPolicy
        | str = InferredNewSkillPolicy.AUTO_VERIFIED,
        replay_enabled: bool = False,
        candidate_replay_backend: CandidateReplayBackend | None = None,
        regression_replay_backend: CandidateReplayBackend | None = None,
        replay_timeout_seconds: int = 600,
        replay_total_timeout_seconds: int | None = None,
        replay_resume_dir: str | Path | None = None,
        measurement_resume_run_id: str | None = None,
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
        measurement_mode: MeasurementPolicyMode | str = MeasurementPolicyMode.OFF,
        measurement_primary_metric: str = "task_success",
        measurement_minimum_effect: float = 0.0,
        measurement_confidence_level: float = 0.95,
        measurement_min_independent_cases: int = 2,
        measurement_bootstrap_samples: int = 2_000,
        measurement_zero_yield_patience: int = 2,
        measurement_invalid_control_patience: int = 2,
        measurement_maximum_interval_width: float | None = None,
        replay_agent: str | None = None,
        runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
        progress_callback: Callable[[str, str], Any] | None = None,
        skip_duplicate_rejected_candidate_gate: bool = False,
        replay_adaptation_compiler: ReplayAdaptationCompiler | None = None,
        concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
        task_batch_executor: DeterministicTaskBatchExecutor | None = None,
        ingestion_model_call_count: int = 0,
        skill_evolution_contract: SkillEvolutionContract | None = None,
    ) -> None:
        self.store = store
        self.optimizer = optimizer
        self.post_apply_evaluator = post_apply_evaluator
        self.evaluation_backend = evaluation_backend
        self.regression_backend = regression_backend or evaluation_backend
        self.regression_suites = tuple(regression_suites)
        self.challenger_enabled = challenger_enabled
        self.challenger_backend = (
            (challenger_backend or DeterministicInvariantChallenger())
            if challenger_enabled
            else None
        )
        if (
            isinstance(challenger_max_cases, bool)
            or not 0 < challenger_max_cases <= MAX_CHALLENGE_CASES
        ):
            raise ValueError(
                f"challenger_max_cases must be between 1 and {MAX_CHALLENGE_CASES}"
            )
        self.challenger_max_cases = challenger_max_cases
        self.min_score_delta = min_score_delta
        self.pending_duplicate = pending_duplicate
        self.max_iterations = max_iterations
        self.min_eval_cases = min_eval_cases
        self.judge_repetitions = judge_repetitions
        if (
            isinstance(ingestion_model_call_count, bool)
            or not isinstance(ingestion_model_call_count, int)
            or ingestion_model_call_count < 0
        ):
            raise ValueError(
                "ingestion_model_call_count must be a non-negative integer"
            )
        self.ingestion_model_call_count = ingestion_model_call_count
        self.skill_evolution_contract = skill_evolution_contract
        if (
            isinstance(candidate_generation_output_tokens_per_unit, bool)
            or candidate_generation_output_tokens_per_unit <= 0
        ):
            raise ValueError(
                "candidate_generation_output_tokens_per_unit must be positive"
            )
        self.candidate_generation_output_tokens_per_unit = (
            candidate_generation_output_tokens_per_unit
        )
        if not isinstance(candidate_generation_model_name, str) or not (
            candidate_generation_model_name.strip()
        ):
            raise ValueError("candidate_generation_model_name must be non-empty")
        self.candidate_generation_model_name = candidate_generation_model_name.strip()
        self._generation_controller = CandidateGenerationController(
            output_tokens_per_candidate=(
                self.candidate_generation_output_tokens_per_unit
            ),
            model_name=self.candidate_generation_model_name,
        )
        self.max_run_tokens = max_run_tokens
        legacy_total_budget_mapping = (
            total_run_token_budget is None and max_run_tokens is not None
        )
        self.total_run_token_budget = (
            max_run_tokens if legacy_total_budget_mapping else total_run_token_budget
        )
        legacy_per_attempt_budget_mapping = (
            per_attempt_replay_token_limit is None and max_run_tokens is not None
        )
        self.per_attempt_replay_token_limit = (
            max_run_tokens
            if legacy_per_attempt_budget_mapping
            else per_attempt_replay_token_limit
        )
        self.max_run_cost_usd = (
            Decimal(str(max_run_cost_usd)) if max_run_cost_usd is not None else None
        )
        self.max_run_wall_seconds = (
            Decimal(str(max_run_wall_seconds))
            if max_run_wall_seconds is not None
            else None
        )
        self.deprecated_config_mappings = (
            dict(deprecated_config_mappings)
            if isinstance(deprecated_config_mappings, Mapping)
            else tuple(deprecated_config_mappings or ())
        )
        legacy_budget_mappings = {
            name: target
            for enabled, name, target in (
                (
                    legacy_total_budget_mapping,
                    "max_run_tokens_to_total_run_token_budget",
                    "total_run_token_budget",
                ),
                (
                    legacy_per_attempt_budget_mapping,
                    "max_run_tokens_to_per_attempt_replay_token_limit",
                    "per_attempt_replay_token_limit",
                ),
            )
            if enabled
        }
        if legacy_budget_mappings:
            if isinstance(self.deprecated_config_mappings, Mapping):
                self.deprecated_config_mappings = {
                    **dict(self.deprecated_config_mappings),
                    **legacy_budget_mappings,
                }
            else:
                self.deprecated_config_mappings = tuple(
                    dict.fromkeys(
                        (
                            *self.deprecated_config_mappings,
                            *legacy_budget_mappings,
                        )
                    )
                )
        candidate_generation_tokens_per_unit = (
            _DEFAULT_CANDIDATE_GENERATION_ESTIMATED_TOKENS
            if candidate_generation_tokens_per_unit is None
            else candidate_generation_tokens_per_unit
        )
        candidate_generation_cost_usd_per_unit = (
            Decimal("0.05")
            if candidate_generation_cost_usd_per_unit is None
            else candidate_generation_cost_usd_per_unit
        )
        candidate_generation_wall_seconds_per_unit = (
            Decimal("120")
            if candidate_generation_wall_seconds_per_unit is None
            else candidate_generation_wall_seconds_per_unit
        )
        candidate_screening_tokens_per_unit = (
            4_096
            if candidate_screening_tokens_per_unit is None
            else candidate_screening_tokens_per_unit
        )
        candidate_screening_cost_usd_per_unit = (
            Decimal("0.05")
            if candidate_screening_cost_usd_per_unit is None
            else candidate_screening_cost_usd_per_unit
        )
        candidate_screening_wall_seconds_per_unit = (
            Decimal("210")
            if candidate_screening_wall_seconds_per_unit is None
            else candidate_screening_wall_seconds_per_unit
        )
        replay_tokens_per_unit = (
            4_096 if replay_tokens_per_unit is None else replay_tokens_per_unit
        )
        replay_cost_usd_per_unit = (
            Decimal("0.05")
            if replay_cost_usd_per_unit is None
            else replay_cost_usd_per_unit
        )
        replay_wall_seconds_per_unit = (
            Decimal("600")
            if replay_wall_seconds_per_unit is None
            else replay_wall_seconds_per_unit
        )
        evaluation_tokens_per_unit = (
            2_048 if evaluation_tokens_per_unit is None else evaluation_tokens_per_unit
        )
        evaluation_cost_usd_per_unit = (
            Decimal("0.02")
            if evaluation_cost_usd_per_unit is None
            else evaluation_cost_usd_per_unit
        )
        evaluation_wall_seconds_per_unit = (
            Decimal("60")
            if evaluation_wall_seconds_per_unit is None
            else evaluation_wall_seconds_per_unit
        )
        self.candidate_generation_tokens_per_unit = candidate_generation_tokens_per_unit
        self.candidate_screening_tokens_per_unit = candidate_screening_tokens_per_unit
        self.replay_tokens_per_unit = replay_tokens_per_unit
        self.evaluation_tokens_per_unit = evaluation_tokens_per_unit
        self._budget_cold_start_by_stage = {
            BudgetStage.CANDIDATE_GENERATION: _configured_budget_usage(
                tokens=candidate_generation_tokens_per_unit,
                cost_usd=candidate_generation_cost_usd_per_unit,
                wall_seconds=candidate_generation_wall_seconds_per_unit,
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
            BudgetStage.CHALLENGER: _configured_budget_usage(
                tokens=candidate_generation_tokens_per_unit,
                cost_usd=candidate_generation_cost_usd_per_unit,
                wall_seconds=candidate_generation_wall_seconds_per_unit,
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
            BudgetStage.CONFORMANCE: _configured_budget_usage(
                tokens=0,
                cost_usd=Decimal("0"),
                wall_seconds=Decimal("30"),
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
            BudgetStage.SCREENING: _configured_budget_usage(
                tokens=candidate_screening_tokens_per_unit,
                cost_usd=candidate_screening_cost_usd_per_unit,
                wall_seconds=candidate_screening_wall_seconds_per_unit,
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
            BudgetStage.PAIRED_REPLAY: _configured_budget_usage(
                tokens=replay_tokens_per_unit,
                cost_usd=replay_cost_usd_per_unit,
                wall_seconds=replay_wall_seconds_per_unit,
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
            BudgetStage.REGRESSION_REPLAY: _configured_budget_usage(
                tokens=replay_tokens_per_unit,
                cost_usd=replay_cost_usd_per_unit,
                wall_seconds=replay_wall_seconds_per_unit,
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
            BudgetStage.EVALUATION: _configured_budget_usage(
                tokens=evaluation_tokens_per_unit,
                cost_usd=evaluation_cost_usd_per_unit,
                wall_seconds=evaluation_wall_seconds_per_unit,
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
            BudgetStage.JUDGE: _configured_budget_usage(
                tokens=evaluation_tokens_per_unit,
                cost_usd=evaluation_cost_usd_per_unit,
                wall_seconds=evaluation_wall_seconds_per_unit,
                token_ceiling=self.total_run_token_budget,
                cost_ceiling=self.max_run_cost_usd,
                wall_ceiling=self.max_run_wall_seconds,
            ),
        }
        self.auto_apply_target_types = tuple(auto_apply_target_types)
        self.allow_generated_target_mutation = allow_generated_target_mutation
        self.allow_external_target_mutation = allow_external_target_mutation
        self.inferred_new_skill_policy = InferredNewSkillPolicy(
            inferred_new_skill_policy
        )
        self._active_target_intent: TargetMutationIntent | None = None
        self.replay_enabled = replay_enabled
        self.candidate_replay_backend = candidate_replay_backend
        self.regression_replay_backend = (
            regression_replay_backend
            if regression_replay_backend is not None
            else candidate_replay_backend
        )
        self.replay_timeout_seconds = replay_timeout_seconds
        if (
            replay_total_timeout_seconds is not None
            and replay_total_timeout_seconds <= 0
        ):
            raise ValueError("replay_total_timeout_seconds must be positive")
        self.replay_total_timeout_seconds = replay_total_timeout_seconds
        self.replay_resume_dir = (
            str(Path(replay_resume_dir)) if replay_resume_dir is not None else None
        )
        self.measurement_resume_run_id = (
            str(measurement_resume_run_id).strip()
            if measurement_resume_run_id is not None
            else None
        )
        if self.measurement_resume_run_id is not None:
            if not self.measurement_resume_run_id:
                raise ValueError("measurement_resume_run_id must be non-empty")
            if self.replay_resume_dir is None:
                raise ValueError(
                    "measurement authority resume requires its replay directory"
                )
        self.replay_max_steps = replay_max_steps
        self.replay_candidate_limit = replay_candidate_limit
        if candidate_screening_max_cases <= 0:
            raise ValueError("candidate_screening_max_cases must be positive")
        if max_generated_candidates <= 0:
            raise ValueError("max_generated_candidates must be positive")
        if max_full_evaluation_candidates <= 0:
            raise ValueError("max_full_evaluation_candidates must be positive")
        if max_score_tiebreak_candidates < 0:
            raise ValueError("max_score_tiebreak_candidates must be non-negative")
        self.candidate_screening_max_cases = candidate_screening_max_cases
        self._screening_controller = CandidateScreeningController()
        self.max_generated_candidates = max_generated_candidates
        self.max_full_evaluation_candidates = max_full_evaluation_candidates
        self.max_score_tiebreak_candidates = max_score_tiebreak_candidates
        self.baseline_replay_repetitions = baseline_replay_repetitions
        self.candidate_replay_repetitions = candidate_replay_repetitions
        self.replay_repetitions_explicit = replay_repetitions_explicit
        self.replay_stability_margin = replay_stability_margin
        self.measurement_mode = MeasurementPolicyMode(measurement_mode)
        self.measurement_primary_metric = measurement_primary_metric.strip()
        if not self.measurement_primary_metric:
            raise ValueError("measurement_primary_metric must be non-empty")
        self.measurement_minimum_effect = float(measurement_minimum_effect)
        if not math.isfinite(self.measurement_minimum_effect):
            raise ValueError("measurement_minimum_effect must be finite")
        self.measurement_confidence_level = float(measurement_confidence_level)
        if not 0 < self.measurement_confidence_level < 1:
            raise ValueError("measurement_confidence_level must be between 0 and 1")
        if measurement_min_independent_cases <= 0:
            raise ValueError("measurement_min_independent_cases must be positive")
        if not 200 <= measurement_bootstrap_samples <= 100_000:
            raise ValueError(
                "measurement_bootstrap_samples must be between 200 and 100000"
            )
        self.measurement_min_independent_cases = measurement_min_independent_cases
        self.measurement_bootstrap_samples = measurement_bootstrap_samples
        self.measurement_early_stop_policy = MeasurementEarlyStopPolicy(
            zero_yield_patience=measurement_zero_yield_patience,
            invalid_control_patience=measurement_invalid_control_patience,
            maximum_interval_width=measurement_maximum_interval_width,
        )
        self._measurement_experiments: dict[
            tuple[str, str], ControlledExperimentSpec
        ] = {}
        self._screening_measurement_experiments: dict[
            tuple[str, str, str], ControlledExperimentSpec
        ] = {}
        self._measurement_summaries: dict[tuple[str, str], MeasurementSummary] = {}
        self._measurement_controller = CandidateMeasurementController(
            store=self.store,
            primary_metric=self.measurement_primary_metric,
            summaries=self._measurement_summaries,
        )
        self._measurement_planning_controller = MeasurementPlanningController(
            store=self.store,
            config=MeasurementPlanningConfig(
                mode=self.measurement_mode,
                identities=MeasurementPlanningIdentities(
                    task_model=stable_measurement_fingerprint(
                        {
                            "replay_agent": replay_agent,
                            "execution_backend": (
                                _measurement_component_identity(
                                    self.candidate_replay_backend
                                )
                            ),
                        }
                    ),
                    generator=stable_measurement_fingerprint(
                        _measurement_component_identity(self.optimizer)
                    ),
                    scheduler=stable_measurement_fingerprint(
                        {
                            "kind": "StageAwareCandidateScheduler",
                            "max_generated_candidates": (self.max_generated_candidates),
                            "max_authoritative_candidates": (
                                self.max_full_evaluation_candidates
                            ),
                            "replay_candidate_limit": (self.replay_candidate_limit),
                        }
                    ),
                    evaluator=stable_measurement_fingerprint(
                        {
                            "evaluation": _measurement_component_identity(
                                self.evaluation_backend
                            ),
                            "regression": _measurement_component_identity(
                                self.regression_backend
                            ),
                            "judge_repetitions": self.judge_repetitions,
                        }
                    ),
                    runtime=stable_measurement_fingerprint(
                        {
                            "python": list(sys.version_info[:3]),
                            "platform": sys.platform,
                            "runner": type(self).__name__,
                        }
                    ),
                ),
                resume_run_id=self.measurement_resume_run_id,
                replay_resume_dir=self.replay_resume_dir,
                replay_enabled=self.replay_enabled,
                replay_backend_available=(self.candidate_replay_backend is not None),
                baseline_replay_repetitions=(self.baseline_replay_repetitions),
                candidate_replay_repetitions=(self.candidate_replay_repetitions),
                replay_repetitions_explicit=(self.replay_repetitions_explicit),
                judge_repetitions=self.judge_repetitions,
                evaluation_backend_available=(self.evaluation_backend is not None),
                minimum_independent_cases=(self.measurement_min_independent_cases),
                primary_metric=self.measurement_primary_metric,
                minimum_effect=self.measurement_minimum_effect,
                confidence_level=self.measurement_confidence_level,
                bootstrap_samples=self.measurement_bootstrap_samples,
                early_stop_policy=self.measurement_early_stop_policy,
                total_run_token_budget=self.total_run_token_budget,
                per_attempt_replay_token_limit=(self.per_attempt_replay_token_limit),
                max_run_cost_usd=self.max_run_cost_usd,
                max_run_wall_seconds=self.max_run_wall_seconds,
                replay_timeout_seconds=self.replay_timeout_seconds,
            ),
        )
        self._authoritative_measurement_controller = AuthoritativeMeasurementController(
            store=self.store,
            config=AuthoritativeMeasurementConfig(
                mode=self.measurement_mode,
                resume_run_id=self.measurement_resume_run_id,
                campaign_wall_deadline_seconds=(
                    float(self.replay_total_timeout_seconds)
                    if self.replay_total_timeout_seconds is not None
                    else None
                ),
            ),
        )
        self.replay_agent = replay_agent
        self.runtime_registry_refresher = runtime_registry_refresher
        self.runtime_skill_activator = runtime_skill_activator
        self.progress_callback = progress_callback
        self.skip_duplicate_rejected_candidate_gate = (
            skip_duplicate_rejected_candidate_gate
        )
        self.replay_adaptation_compiler = (
            replay_adaptation_compiler or ReplayAdaptationCompiler()
        )
        self.concurrency_policy = concurrency_policy or SelfEvolveConcurrencyPolicy()
        self.task_batch_executor = (
            task_batch_executor or DeterministicTaskBatchExecutor()
        )
        self.execution_telemetry = SelfEvolveExecutionTelemetry()
        self._paired_replay_execution_controller = PairedReplayExecutionController(
            store=self.store,
            config=PairedReplayExecutionConfig(
                replay_enabled=self.replay_enabled,
                replay_backend=self.candidate_replay_backend,
                replay_agent=self.replay_agent,
                baseline_repetitions=self.baseline_replay_repetitions,
                candidate_repetitions=self.candidate_replay_repetitions,
                repetitions_explicit=self.replay_repetitions_explicit,
                minimum_independent_cases=(self.measurement_min_independent_cases),
                timeout_seconds=self.replay_timeout_seconds,
                total_timeout_seconds=self.replay_total_timeout_seconds,
                max_steps=self.replay_max_steps,
                max_tokens=self.per_attempt_replay_token_limit,
                resume_replay_dir=self.replay_resume_dir,
                invalid_control_patience=(
                    self.measurement_early_stop_policy.invalid_control_patience
                ),
                measurement_mode=self.measurement_mode,
            ),
        )
        self._replay_adaptation_cache: dict[
            tuple[str, str, str],
            tuple[ReplayAdaptationBundle | None, GateResult],
        ] = {}
        self._replay_dataset_preflight_cache: dict[str, ReplayPreflightReport] = {}
        self._run_environment_fingerprints: dict[str, str] = {}
        # Screening is a comparative ranking plane.  Keep bounded empirical
        # observations so later batches in the same campaign do not repeatedly
        # select a representative case that already proved expensive and
        # non-discriminative (for example, both paired members timed out).
        self._candidate_screening_case_observations: dict[
            str, dict[str, float | int]
        ] = {}
        # Exact qualification health is never keyed by case alone.  The
        # case-only projection above is retained strictly as an advisory
        # ordering/cost signal for cold-start panel selection.
        self._candidate_screening_control_observations: dict[
            str, dict[str, object]
        ] = {}
        self._candidate_screening_observation_dataset_fingerprint: str | None = None
        self._candidate_screening_loaded_run_ids: set[str] = set()
        # A control that fails before candidate execution is a run-level
        # framework observation. Keep it quarantined across optimizer batches
        # and candidate variants so one unstable task cannot consume the same
        # physical baseline horizon repeatedly within a run.
        self._candidate_screening_run_invalid_control_case_ids: dict[str, set[str]] = {}
        self._current_run_authoritative_case_observations: dict[
            str, dict[str, int]
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
        campaign_prior_run_ids: tuple[str, ...] | None = None,
        campaign_scheduler_checkpoint_run_ids: tuple[str, ...] | None = None,
        campaign_id: str | None = None,
        campaign_cycle: int | None = None,
    ) -> SelfEvolveRunnerResult:
        failure_cleanup = _RunFailureCleanup()
        request = ExplicitTargetRunRequest(
            run_id=run_id,
            target=target,
            dataset=dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
            target_selection_report=target_selection_report,
            target_provenance=target_provenance,
            target_selection_decision=target_selection_decision,
            campaign_prior_run_ids=campaign_prior_run_ids,
            campaign_scheduler_checkpoint_run_ids=(
                campaign_scheduler_checkpoint_run_ids
            ),
            campaign_id=campaign_id,
            campaign_cycle=campaign_cycle,
        )
        try:
            return await self._run_explicit_target(
                request=request,
                failure_cleanup=failure_cleanup,
            )
        except BaseException:
            failure_cleanup.cleanup()
            raise
        finally:
            self._run_environment_fingerprints.pop(run_id, None)
            self._candidate_screening_run_invalid_control_case_ids.pop(
                run_id,
                None,
            )

    async def _run_explicit_target(
        self,
        *,
        request: ExplicitTargetRunRequest,
        failure_cleanup: _RunFailureCleanup,
    ) -> SelfEvolveRunnerResult:
        run_id = request.run_id
        target = request.target
        dataset = request.dataset
        trace_packs = request.trace_packs
        apply_policy = request.apply_policy
        target_selection_report = request.target_selection_report
        target_provenance = request.target_provenance
        target_selection_decision = request.target_selection_decision
        campaign_prior_run_ids = request.campaign_prior_run_ids
        campaign_scheduler_checkpoint_run_ids = (
            request.campaign_scheduler_checkpoint_run_ids
        )
        campaign_id = request.campaign_id
        campaign_cycle = request.campaign_cycle

        self.execution_telemetry = SelfEvolveExecutionTelemetry()
        self._current_run_authoritative_case_observations.clear()
        screening_dataset_fingerprint = _screening_observation_scope_fingerprint(
            dataset=dataset,
            target=target,
        )
        if (
            self._candidate_screening_observation_dataset_fingerprint
            != screening_dataset_fingerprint
        ):
            self._candidate_screening_case_observations.clear()
            self._candidate_screening_control_observations.clear()
            self._candidate_screening_loaded_run_ids.clear()
            self._candidate_screening_observation_dataset_fingerprint = (
                screening_dataset_fingerprint
            )
        _restore_campaign_screening_case_observations(
            self._candidate_screening_case_observations,
            store=self.store,
            prior_run_ids=tuple(campaign_prior_run_ids or ()),
            loaded_run_ids=self._candidate_screening_loaded_run_ids,
            control_observations=(self._candidate_screening_control_observations),
            harness_fingerprint=_screening_control_harness_fingerprint(),
        )
        _restore_historical_screening_lifecycle_observations(
            self._candidate_screening_case_observations,
            store=self.store,
            target=target.identity,
            dataset=dataset,
            current_run_id=run_id,
            control_observations=(self._candidate_screening_control_observations),
            loaded_run_ids=self._candidate_screening_loaded_run_ids,
            harness_fingerprint=_screening_control_harness_fingerprint(),
        )
        screening_control_preflight = _screening_control_preflight(
            dataset,
            observations=self._candidate_screening_case_observations,
            timeout_ceiling_seconds=min(
                self.replay_timeout_seconds,
                _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
            ),
            harness_fingerprint=_screening_control_harness_fingerprint(),
        )
        budget_context = _RunBudgetContext(
            ledger=RunBudgetLedger(
                BudgetCeilings(
                    total_tokens=self.total_run_token_budget,
                    total_cost_usd=self.max_run_cost_usd,
                    wall_seconds=self.max_run_wall_seconds,
                )
            ),
            cold_start_by_stage=self._budget_cold_start_by_stage,
            backend_proven_zero_by_stage={
                BudgetStage.CANDIDATE_GENERATION: (
                    _backend_proves_zero_budget_usage(
                        self.optimizer,
                        BudgetStage.CANDIDATE_GENERATION,
                    )
                ),
                BudgetStage.CHALLENGER: _backend_proves_zero_budget_usage(
                    self.challenger_backend,
                    BudgetStage.CHALLENGER,
                ),
                BudgetStage.SCREENING: _backend_proves_zero_budget_usage(
                    self.candidate_replay_backend,
                    BudgetStage.SCREENING,
                ),
                BudgetStage.PAIRED_REPLAY: _backend_proves_zero_budget_usage(
                    self.candidate_replay_backend,
                    BudgetStage.PAIRED_REPLAY,
                ),
                BudgetStage.REGRESSION_REPLAY: (
                    _backend_proves_zero_budget_usage(
                        self.regression_replay_backend,
                        BudgetStage.REGRESSION_REPLAY,
                    )
                ),
                BudgetStage.EVALUATION: _backend_proves_zero_budget_usage(
                    self.evaluation_backend,
                    BudgetStage.EVALUATION,
                ),
                BudgetStage.JUDGE: _backend_proves_zero_budget_usage(
                    self.evaluation_backend,
                    BudgetStage.JUDGE,
                ),
            },
        )
        failure_cleanup.budget_context = budget_context
        self.run_budget_ledger = budget_context.ledger
        if self.ingestion_model_call_count:
            ingestion_budget = budget_context.reserve(
                BudgetStage.CANDIDATE_GENERATION,
                "frozen-dataset-ingestion",
                units=self.ingestion_model_call_count,
            )
            if not ingestion_budget.allowed:
                raise ValueError("dataset ingestion model usage exceeds the run budget")
            budget_context.debit(
                ingestion_budget,
                usage_observation=BudgetUsageObservation(
                    known_lower_bound=BudgetUsage(),
                    completeness=BudgetUsageCompleteness.incomplete(),
                ),
                actual_source=("reserved_fallback_pre_run_ingestion_model_usage"),
            )
        scheduler = StageAwareCandidateScheduler(
            exploration_population=_candidate_generation_limit(
                replay_candidate_limit=self.replay_candidate_limit,
            )
        )
        scheduler_state = _load_prior_scheduler_state(
            self.store,
            target.identity,
            current_run_id=run_id,
            allowed_run_ids=(
                campaign_scheduler_checkpoint_run_ids
                if campaign_scheduler_checkpoint_run_ids is not None
                else campaign_prior_run_ids
            ),
        )
        scheduler_decisions: list[dict[str, object]] = []
        if apply_policy not in {"proposal", "auto_verified", "verified_only"}:
            raise ValueError(f"unsupported apply policy: {apply_policy}")
        if self.skill_evolution_contract is not None:
            if not _is_verified_apply_policy(apply_policy):
                raise ValueError(
                    "skill evolution contract requires a verified apply policy"
                )
            self.skill_evolution_contract.validate_run(
                target_type=target.identity.target_type,
                target_id=target.identity.target_id,
                dataset_case_ids=tuple(case.case_id for case in dataset.cases),
            )
        supplied_provenance = target_provenance
        supplied_decision = target_selection_decision
        if target_selection_decision is None and target_selection_report is None:
            target_selection_report = _explicit_target_selection_report(
                target.identity,
                trace_packs,
            )
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
                    target_intent=target_selection_report.target_intent,
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
                        inventory_entries[0].provenance if inventory_entries else None
                    ),
                    workspace_root=self.store.workspace_root,
                )

        authoritative_resolution = provenance_resolution
        if (
            supplied_decision is not None
            and supplied_decision.provenance_resolution != authoritative_resolution
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
            provenance_resolution.provenance if provenance_resolution.resolved else None
        )
        target_provenance_unresolved_reason = (
            None if provenance_resolution.resolved else provenance_resolution.reason
        )
        self._active_target_intent = (
            target_selection_decision.target_intent
            if target_selection_decision is not None
            else None
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
            (f"Loaded self-evolve trajectory set with {len(dataset.cases)} case(s)"),
        )

        run = SelfEvolveRun(
            run_id=run_id, target=target.identity, status=SelfEvolveRunStatus.RUNNING
        )
        self.store.create_run(run)
        self.store.write_screening_control_preflight(
            run_id,
            screening_control_preflight,
        )
        _emit_progress(
            self.progress_callback,
            "candidate_screening_preflight",
            (
                "Control preflight before candidate generation: "
                f"{screening_control_preflight.get('status')}; "
                f"feasible {len(screening_control_preflight.get('feasible_case_ids', []))}; "
                f"infeasible {len(screening_control_preflight.get('infeasible_case_ids', []))}; "
                f"unknown {len(screening_control_preflight.get('unknown_case_ids', []))}"
            ),
        )
        attempt_tracker = _CandidateAttemptTracker(self.store, run_id)
        failure_cleanup.attempt_tracker = attempt_tracker
        self.store.write_dataset_recipe(run_id, dataset.recipe)
        if self.regression_suites:
            self.store.write_regression_suite_manifest(
                run_id,
                tuple(suite.spec for suite in self.regression_suites),
            )
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
            StoppingConditionState(
                iteration=0, pending_duplicate=self.pending_duplicate
            )
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
            completed_run = SelfEvolveRun(
                run_id=run_id,
                target=target.identity,
                status=SelfEvolveRunStatus.REJECTED,
                gate_results=(stopping_result,),
            )
            _finalize_run_report(
                self.store,
                run_id,
                report=report,
                completed_run=completed_run,
                previous_artifact_retention=startup_artifact_retention,
            )
            _emit_progress(
                self.progress_callback,
                "completed",
                f"Self-evolve run {run_id} finished with status {completed_run.status.value}",
            )
            return SelfEvolveRunnerResult(run=completed_run, selected_candidate=None)

        selected_candidate: CandidateVariant | None = None
        validation_feedback: tuple[EvaluationSummary, ...] = ()
        all_candidates: list[CandidateVariant] = []
        candidate_source_dispositions: dict[str, CandidateSourceDisposition] = {}
        fresh_evaluation_required = False
        optimizer_diagnostics: list[dict[str, object]] = []
        optimizer_lineage_paths: list[str] = []
        optimizer_lineage_paths_by_candidate: dict[str, str] = {}
        iteration_reports: list[dict[str, object]] = []
        iteration_states: list[dict[str, object]] = []
        population_screening_reports: list[dict[str, object]] = []
        baseline_summary: EvaluationSummary | None = None
        candidate_summary: EvaluationSummary | None = None
        held_out_summary: EvaluationSummary | None = None
        regression_evidence: RegressionEvidence | None = None
        challenge_report: ChallengeReport | None = None
        measurement_summary: MeasurementSummary | None = None
        latest_handbook_slice: Mapping[str, object] | None = None
        replay_result: CandidateReplayResult | None = None
        replay_dataset: SelfEvolveDataset | None = None
        gate_results: list[GateResult] = []
        prior_feedback = _load_prior_rejected_feedback(
            self.store,
            target.identity,
            current_run_id=run_id,
            allowed_run_ids=campaign_prior_run_ids,
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
            and feedback.metrics.get("publication_completed") is True
        }
        current_run_attempted_candidate_ids: set[str] = set()
        (
            canonical_candidate_id_by_package,
            package_fingerprint_by_candidate_id,
        ) = _load_prior_candidate_package_index(
            self.store,
            target.identity,
            current_run_id=run_id,
            candidate_ids=(rejected_candidate_ids | accepted_candidate_ids),
            allowed_run_ids=campaign_prior_run_ids,
        )
        current_run_candidate_id_by_package: dict[str, str] = {}
        current_run_package_fingerprint_by_candidate_id: dict[str, str] = {}
        current_run_candidate_id_by_semantic_package: dict[str, str] = {}
        attempt_key_by_candidate_id: dict[str, CandidateAttemptKey] = {}
        rejected_semantic_lesson_fingerprints = (
            _load_prior_rejected_semantic_lesson_fingerprints(
                self.store,
                target.identity,
                current_run_id=run_id,
                allowed_run_ids=campaign_prior_run_ids,
            )
        )
        replay_preflight = self.replay_adaptation_compiler.preflight(
            dataset=_replayable_user_task_dataset(dataset),
            workspace_root=self.store.workspace_root,
        )
        self.store.write_replay_requirements(run_id, replay_preflight)
        target_package_inventory = _target_package_inventory(target)
        target_package_sources = _target_package_sources(
            target,
            inventory=target_package_inventory,
        )
        verification_settings: dict[str, object] = {
            "min_score_delta": self.min_score_delta,
            "min_eval_cases": self.min_eval_cases,
            "judge_repetitions": self.judge_repetitions,
            "candidate_screening_max_cases": self.candidate_screening_max_cases,
            "max_generated_candidates": self.max_generated_candidates,
            "max_full_evaluation_candidates": (self.max_full_evaluation_candidates),
            "max_score_tiebreak_candidates": (self.max_score_tiebreak_candidates),
            "replay_enabled": self.replay_enabled,
            "baseline_replay_repetitions": self.baseline_replay_repetitions,
            "candidate_replay_repetitions": self.candidate_replay_repetitions,
            "replay_stability_margin": self.replay_stability_margin,
            "replay_timeout_seconds": self.replay_timeout_seconds,
            "replay_total_timeout_seconds": self.replay_total_timeout_seconds,
            "measurement_mode": self.measurement_mode.value,
            "measurement_primary_metric": self.measurement_primary_metric,
            "measurement_minimum_effect": self.measurement_minimum_effect,
            "measurement_confidence_level": (self.measurement_confidence_level),
            "measurement_min_independent_cases": (
                self.measurement_min_independent_cases
            ),
            "measurement_invalid_control_patience": (
                self.measurement_early_stop_policy.invalid_control_patience
            ),
        }

        baseline_preflight_blocked = False
        infrastructure_blocked = False
        shared_validation_gate: GateResult | None = None
        if screening_control_preflight.get("candidate_generation_allowed") is False:
            baseline_preflight_blocked = True
            gate_results.append(
                GateResult(
                    gate_name="evolvability_preflight",
                    passed=False,
                    reason=(
                        "known baseline controls cannot execute reliably; repair "
                        "the shared replay harness before mutating the skill"
                    ),
                    details=dict(screening_control_preflight),
                )
            )
        repair_reserved_slot_count = (
            1
            if _is_verified_apply_policy(apply_policy)
            and self.max_generated_candidates > 1
            else 0
        )
        run_state = ExplicitRunStateAccumulator(
            validation_feedback=validation_feedback,
            iteration_reports=iteration_reports,
            iteration_states=iteration_states,
            current_run_attempted_candidate_ids=(current_run_attempted_candidate_ids),
            rejected_candidate_ids=rejected_candidate_ids,
            accepted_candidate_ids=accepted_candidate_ids,
            baseline_preflight_blocked=baseline_preflight_blocked,
            infrastructure_blocked=infrastructure_blocked,
        )
        iteration_budget = (
            self.max_iterations + _MAX_PROGRESS_REPAIR_EXTENSION_ITERATIONS
        )
        estimated_replay_case_count = len(_replayable_user_task_dataset(dataset).cases)
        (
            estimated_baseline_repetitions,
            estimated_candidate_repetitions,
            _,
        ) = _effective_replay_repetitions(
            apply_policy=apply_policy,
            repetitions_explicit=self.replay_repetitions_explicit,
            replay_case_count=estimated_replay_case_count,
            measurement_min_independent_cases=(self.measurement_min_independent_cases),
            baseline_repetitions=self.baseline_replay_repetitions,
            candidate_repetitions=self.candidate_replay_repetitions,
        )
        estimated_replay_units = (
            estimated_replay_case_count
            * (estimated_baseline_repetitions + estimated_candidate_repetitions)
            if self.replay_enabled and self.candidate_replay_backend is not None
            else 0
        )
        estimated_evaluation_case_count = max(
            len(dataset.cases),
            estimated_replay_case_count * estimated_candidate_repetitions,
        )
        estimated_evaluation_variants = (
            5 if _is_verified_apply_policy(apply_policy) else 2
        )
        estimated_regression_units = (
            sum(
                max(1, len(suite.dataset.cases)) * 2 for suite in self.regression_suites
            )
            if _is_verified_apply_policy(apply_policy)
            else 0
        )
        estimated_evaluation_units = max(
            1,
            estimated_evaluation_case_count * estimated_evaluation_variants
            + estimated_regression_units,
        )

        def repair_workflow_budget_items(
            *,
            iteration: int,
            candidate_count: int,
        ) -> tuple[tuple[BudgetStage, str, int], ...]:
            items: list[tuple[BudgetStage, str, int]] = [
                (
                    BudgetStage.CANDIDATE_GENERATION,
                    f"iteration-{iteration}-workflow-generation",
                    candidate_count,
                )
            ]
            if estimated_replay_units > 0:
                items.append(
                    (
                        BudgetStage.PAIRED_REPLAY,
                        f"iteration-{iteration}-workflow-replay",
                        estimated_replay_units * candidate_count,
                    )
                )
            if self.evaluation_backend is not None:
                evaluation_units = estimated_evaluation_units * candidate_count
                items.extend(
                    (
                        (
                            BudgetStage.EVALUATION,
                            f"iteration-{iteration}-workflow-evaluation",
                            evaluation_units,
                        ),
                        (
                            BudgetStage.JUDGE,
                            f"iteration-{iteration}-workflow-judge",
                            max(1, evaluation_units * self.judge_repetitions),
                        ),
                    )
                )
            return tuple(items)

        generation_execution_state = GenerationExecutionState(
            scheduler_state=scheduler_state,
            validation_feedback=validation_feedback,
            fresh_evaluation_required=fresh_evaluation_required,
            latest_handbook_slice=latest_handbook_slice,
            all_candidates=all_candidates,
            candidate_source_dispositions=candidate_source_dispositions,
            optimizer_diagnostics=optimizer_diagnostics,
            optimizer_lineage_paths=optimizer_lineage_paths,
            optimizer_lineage_paths_by_candidate=(optimizer_lineage_paths_by_candidate),
            scheduler_decisions=scheduler_decisions,
            iteration_reports=iteration_reports,
            iteration_states=iteration_states,
            gate_results=gate_results,
            canonical_candidate_id_by_package=(canonical_candidate_id_by_package),
            package_fingerprint_by_candidate_id=(package_fingerprint_by_candidate_id),
            current_run_candidate_id_by_package=(current_run_candidate_id_by_package),
            current_run_package_fingerprint_by_candidate_id=(
                current_run_package_fingerprint_by_candidate_id
            ),
            current_run_candidate_id_by_semantic_package=(
                current_run_candidate_id_by_semantic_package
            ),
            attempt_key_by_candidate_id=attempt_key_by_candidate_id,
        )
        generation_execution_policy = GenerationExecutionPolicy(
            max_iterations=self.max_iterations,
            max_generated_candidates=self.max_generated_candidates,
            max_full_evaluation_candidates=(self.max_full_evaluation_candidates),
            replay_candidate_limit=self.replay_candidate_limit,
            replay_enabled=self.replay_enabled,
            candidate_screening_max_cases=self.candidate_screening_max_cases,
        )
        generation_execution_runtime = GenerationExecutionRuntime(
            store=self.store,
            optimizer=self.optimizer,
            generation_controller=self._generation_controller,
            execution_telemetry=self.execution_telemetry,
            scheduler=scheduler,
            budget_context=budget_context,
            attempt_tracker=attempt_tracker,
            repair_workflow_budget_items=repair_workflow_budget_items,
            progress_callback=self.progress_callback,
            skill_evolution_contract=self.skill_evolution_contract,
            candidate_replay_backend=self.candidate_replay_backend,
            verification_contract_fingerprint=(_verification_contract_fingerprint),
        )

        iteration_execution = await execute_iteration_lifecycle(
            IterationExecutionRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                trace_packs=trace_packs,
                apply_policy=apply_policy,
                target_provenance=target_provenance,
                target_provenance_unresolved_reason=(
                    target_provenance_unresolved_reason
                ),
                target_selection_report=target_selection_report,
                prior_feedback=prior_feedback,
                generation_lesson_records=generation_lesson_records,
                rejected_candidate_ids=rejected_candidate_ids,
                accepted_candidate_ids=accepted_candidate_ids,
                current_run_attempted_candidate_ids=(
                    current_run_attempted_candidate_ids
                ),
                rejected_semantic_lesson_fingerprints=(
                    rejected_semantic_lesson_fingerprints
                ),
                repair_reserved_slot_count=repair_reserved_slot_count,
                replay_preflight=replay_preflight,
                target_package_inventory=target_package_inventory,
                target_package_sources=target_package_sources,
                verification_settings=verification_settings,
                estimated_baseline_repetitions=(estimated_baseline_repetitions),
                iteration_budget=iteration_budget,
                run_state=run_state,
                attempt_tracker=attempt_tracker,
                budget_context=budget_context,
                generation_execution_policy=generation_execution_policy,
                generation_execution_runtime=generation_execution_runtime,
                generation_execution_state=generation_execution_state,
                population_screening_reports=population_screening_reports,
            ),
            IterationExecutionRuntime(
                store=self.store,
                optimizer=self.optimizer,
                candidate_replay_backend=self.candidate_replay_backend,
                progress_callback=self.progress_callback,
                _plan_candidate_measurement=self._plan_candidate_measurement,
                _screen_candidate_population=(self._screen_candidate_population),
                _prepare_replay_adaptation=self._prepare_replay_adaptation,
                _execute_iteration_candidate=(self._execute_iteration_candidate),
                _baseline_reuse_provenance=self._baseline_reuse_provenance,
                _candidate_screening_case_observations=(
                    self._candidate_screening_case_observations
                ),
                _candidate_screening_control_observations=(
                    self._candidate_screening_control_observations
                ),
                _current_run_authoritative_case_observations=(
                    self._current_run_authoritative_case_observations
                ),
                _measurement_experiments=self._measurement_experiments,
                _measurement_summaries=self._measurement_summaries,
            ),
            IterationExecutionPolicy(
                allow_external_target_mutation=(self.allow_external_target_mutation),
                allow_generated_target_mutation=(self.allow_generated_target_mutation),
                inferred_new_skill_policy=self.inferred_new_skill_policy,
                max_full_evaluation_candidates=(self.max_full_evaluation_candidates),
                max_score_tiebreak_candidates=(self.max_score_tiebreak_candidates),
                measurement_early_stop_policy=(self.measurement_early_stop_policy),
                measurement_mode=self.measurement_mode,
                replay_enabled=self.replay_enabled,
                _active_target_intent=self._active_target_intent,
            ),
        )
        validation_feedback = iteration_execution.validation_feedback
        fresh_evaluation_required = iteration_execution.fresh_evaluation_required
        latest_handbook_slice = iteration_execution.latest_handbook_slice
        scheduler_state = iteration_execution.scheduler_state
        shared_validation_gate = iteration_execution.shared_validation_gate
        attempt_tracker.finalize_open(reason_code="run_terminated_before_candidate")
        budget_context.release_all(reason_code="run_terminal_cleanup")
        selected_projection = (
            None
            if shared_validation_gate is not None
            else run_state.select_iteration_evidence(
                fresh_evaluation_required=fresh_evaluation_required,
                selector=_select_iteration_state,
            )
        )
        selected_state = (
            selected_projection.state if selected_projection is not None else None
        )
        if shared_validation_gate is not None:
            gate_results.append(shared_validation_gate)
        elif selected_projection is not None:
            baseline_summary = selected_projection.baseline_summary
            candidate_summary = selected_projection.candidate_summary
            held_out_summary = selected_projection.held_out_summary
            regression_evidence = selected_projection.regression_evidence
            challenge_report = selected_projection.challenge_report
            replay_result = selected_projection.replay_result
            replay_dataset = selected_projection.replay_dataset
            gate_results = list(selected_projection.gate_results)
            selected_candidate = selected_projection.selected_candidate
        else:
            semantic_dedup_exhausted = (
                run_state.generation.semantic_lesson_duplicate_attempt_count > 0
                and run_state.generation.semantic_lesson_duplicate_attempt_count
                == run_state.generation.raw_generation_attempt_count
                and not all_candidates
            )
            candidate_generation_failure_events = (
                (
                    _candidate_policy_frontier_stalled_event(
                        run_state.generation.last_policy_filter_outcomes
                    ),
                )
                if run_state.generation.policy_frontier_exhausted
                else _candidate_generation_failure_events(optimizer_diagnostics)
            )
            candidate_generation_failure_event = (
                candidate_generation_failure_events[0]
                if candidate_generation_failure_events
                else None
            )
            candidate_generation_details: dict[str, object] = {
                "generated_candidate_count": len(all_candidates),
                "iterations": len(optimizer_diagnostics),
            }
            if run_state.generation.raw_generation_attempt_count:
                candidate_generation_details["generation_attempt_count"] = (
                    run_state.generation.raw_generation_attempt_count
                )
            if run_state.generation.policy_frontier_exhausted:
                candidate_generation_details["generation_policy_frontier_exhausted"] = (
                    True
                )
            if run_state.generation.materialization_frontier_exhausted:
                candidate_generation_details[
                    "generation_materialization_frontier_exhausted"
                ] = True
            if run_state.generation.protocol_frontier_exhausted:
                candidate_generation_details[
                    "generation_protocol_frontier_exhausted"
                ] = True
            if candidate_generation_failure_event is not None:
                candidate_generation_details.update(
                    {
                        "failure_class": "candidate",
                        "code": candidate_generation_failure_event["code"],
                        "failure_event": candidate_generation_failure_event,
                        "causal_failure_events": list(
                            candidate_generation_failure_events
                        ),
                    }
                )
            gate_results.append(
                GateResult(
                    gate_name=(
                        "candidate_generation_exhausted_by_semantic_dedup"
                        if semantic_dedup_exhausted
                        else "candidate_generation"
                        if _is_verified_apply_policy(apply_policy)
                        else "no_candidate"
                    ),
                    passed=False,
                    reason=(
                        (
                            "all generated candidates repeated historically rejected "
                            "complete semantic packages under the active verification "
                            "contract"
                        )
                        if semantic_dedup_exhausted
                        else (
                            "candidate generation policy frontier repeated without "
                            "structural progress"
                        )
                        if run_state.generation.policy_frontier_exhausted
                        else (
                            "candidate generation repeated the same typed "
                            "materialization failure without repair progress"
                        )
                        if run_state.generation.materialization_frontier_exhausted
                        else (
                            "candidate generation produced a non-repairable "
                            "protocol failure"
                        )
                        if run_state.generation.protocol_frontier_exhausted
                        else "optimizer did not produce a replayable candidate"
                        if _is_verified_apply_policy(apply_policy)
                        else "optimizer did not produce a candidate"
                    ),
                    details=(
                        {
                            "failure_class": "candidate",
                            "code": "candidate_generation_exhausted_by_semantic_dedup",
                            "generation_attempt_count": (
                                run_state.generation.raw_generation_attempt_count
                            ),
                            "canonical_unique_candidate_count": len(all_candidates),
                            "semantic_lesson_duplicate_attempt_count": (
                                run_state.generation.semantic_lesson_duplicate_attempt_count
                            ),
                            "semantic_identity_version": (
                                _SEMANTIC_DEDUP_IDENTITY_VERSION
                            ),
                            "verification_contract_version": (
                                _VERIFICATION_CONTRACT_VERSION
                            ),
                            "iterations": len(optimizer_diagnostics),
                        }
                        if semantic_dedup_exhausted
                        else candidate_generation_details
                        if _is_verified_apply_policy(apply_policy)
                        else None
                    ),
                )
            )

        terminal_selection = project_terminal_selection(
            TerminalSelectionRequest(
                selected_candidate=selected_candidate,
                gate_results=tuple(gate_results),
            ),
            runtime=TerminalSelectionRuntime(
                candidate_prerequisite_failure=(
                    _gate_has_candidate_prerequisite_failure
                ),
                measurement_materialization_blocked=(
                    _gate_blocks_measurement_materialization
                ),
            ),
        )
        gate_results = list(terminal_selection.gate_results)
        candidate_prerequisite_blocked = (
            terminal_selection.candidate_prerequisite_blocked
        )
        repair_focus_candidate = terminal_selection.repair_focus_candidate
        reported_selected_candidate = terminal_selection.reported_selected_candidate
        measurement_prerequisite_blocked = (
            terminal_selection.measurement_prerequisite_blocked
        )
        if selected_state is not None and not candidate_prerequisite_blocked:
            raw_measurement_summary = selected_state.get("measurement_summary")
            if isinstance(raw_measurement_summary, MeasurementSummary):
                measurement_summary = raw_measurement_summary
            elif (
                self.measurement_mode is not MeasurementPolicyMode.OFF
                and not measurement_prerequisite_blocked
            ):
                state_candidate = selected_state.get("candidate")
                if isinstance(state_candidate, CandidateVariant):
                    experiment = self._measurement_experiments.get(
                        (run_id, state_candidate.candidate_id)
                    )
                    if experiment is not None:
                        try:
                            measurement_summary = (
                                self._materialize_candidate_measurement(
                                    experiment=experiment,
                                    materialization_run_id=run_id,
                                    candidate=state_candidate,
                                    dataset=dataset,
                                    replay_result=(
                                        selected_state.get("replay_result")
                                        if isinstance(
                                            selected_state.get("replay_result"),
                                            CandidateReplayResult,
                                        )
                                        else None
                                    ),
                                    replay_dataset=(
                                        selected_state.get("replay_dataset")
                                        if isinstance(
                                            selected_state.get("replay_dataset"),
                                            SelfEvolveDataset,
                                        )
                                        else None
                                    ),
                                    baseline_summary=(
                                        selected_state.get("baseline_summary")
                                        if isinstance(
                                            selected_state.get("baseline_summary"),
                                            EvaluationSummary,
                                        )
                                        else None
                                    ),
                                    candidate_summary=(
                                        selected_state.get("candidate_summary")
                                        if isinstance(
                                            selected_state.get("candidate_summary"),
                                            EvaluationSummary,
                                        )
                                        else None
                                    ),
                                    candidate_count=max(1, len(all_candidates)),
                                    authoritative_candidate_count=1,
                                    target_selection_report=(target_selection_report),
                                )
                            )
                            selected_state["measurement_summary"] = measurement_summary
                            if (
                                self.measurement_mode is MeasurementPolicyMode.REQUIRED
                                and not any(
                                    gate.gate_name == "trusted_improvement_measurement"
                                    for gate in gate_results
                                )
                            ):
                                gate_results.append(
                                    _measurement_promotion_gate(measurement_summary)
                                )
                        except (OSError, TypeError, ValueError):
                            if (
                                self.measurement_mode is MeasurementPolicyMode.REQUIRED
                                and not any(
                                    gate.gate_name == "trusted_improvement_measurement"
                                    for gate in gate_results
                                )
                            ):
                                gate_results.append(
                                    GateResult(
                                        gate_name=("trusted_improvement_measurement"),
                                        passed=False,
                                        reason=(
                                            "controlled measurement could not "
                                            "be finalized"
                                        ),
                                        details={
                                            "failure_class": "measurement",
                                            "code": (
                                                "measurement_materialization_failed"
                                            ),
                                        },
                                    )
                                )

        elif (
            not candidate_prerequisite_blocked
            and not measurement_prerequisite_blocked
            and self.measurement_mode is not MeasurementPolicyMode.OFF
            and all_candidates
        ):
            fallback_candidate = all_candidates[-1]
            fallback_experiment = self._measurement_experiments.get(
                (run_id, fallback_candidate.candidate_id)
            )
            if fallback_experiment is not None:
                try:
                    measurement_summary = self._materialize_candidate_measurement(
                        experiment=fallback_experiment,
                        materialization_run_id=run_id,
                        candidate=fallback_candidate,
                        dataset=dataset,
                        replay_result=None,
                        replay_dataset=None,
                        baseline_summary=None,
                        candidate_summary=None,
                        candidate_count=max(1, len(all_candidates)),
                        authoritative_candidate_count=0,
                        target_selection_report=target_selection_report,
                    )
                    if self.measurement_mode is MeasurementPolicyMode.REQUIRED:
                        gate_results.append(
                            _measurement_promotion_gate(measurement_summary)
                        )
                except (OSError, TypeError, ValueError):
                    if self.measurement_mode is MeasurementPolicyMode.REQUIRED:
                        gate_results.append(
                            GateResult(
                                gate_name="trusted_improvement_measurement",
                                passed=False,
                                reason=(
                                    "controlled measurement could not be finalized"
                                ),
                                details={
                                    "failure_class": "measurement",
                                    "code": "measurement_materialization_failed",
                                },
                            )
                        )

        if measurement_summary is not None:
            try:
                measurement_summary = self._attach_measurement_search_performance(
                    run_id=run_id,
                    summary=measurement_summary,
                    candidates=all_candidates,
                    iteration_reports=iteration_reports,
                )
            except (OSError, TypeError, ValueError):
                # Search curves are diagnostic. The controlled effect report
                # remains authoritative and already persisted.
                pass

        skill_evolution_progress: dict[str, object] | None = None
        if self.skill_evolution_contract is not None and replay_result is not None:
            intervention_observed = any(
                gate.gate_name == "candidate_replay"
                and isinstance(gate.details, Mapping)
                and gate.details.get("candidate_intervention_observed") is True
                for gate in gate_results
            )
            skill_evolution_progress = evaluate_skill_evolution_replay(
                self.skill_evolution_contract,
                replay_result,
                candidate_intervention_observed=intervention_observed,
            )
            coverage_satisfied = skill_evolution_progress["coverage_satisfied"] is True
            gate_results.append(
                GateResult(
                    gate_name="skill_evolution_contract",
                    passed=coverage_satisfied,
                    reason=(
                        "target Skill capability coverage is satisfied"
                        if coverage_satisfied
                        else "target Skill capability coverage is incomplete"
                    ),
                    details={
                        **skill_evolution_progress,
                        "failure_class": (None if coverage_satisfied else "candidate"),
                        "failure_owner": (None if coverage_satisfied else "candidate"),
                        "failure_scope": (None if coverage_satisfied else "candidate"),
                        "repairable": not coverage_satisfied,
                        "code": (
                            "skill_contract_coverage_satisfied"
                            if coverage_satisfied
                            else "skill_contract_coverage_incomplete"
                        ),
                    },
                )
            )

        post_apply: dict[str, object] | None = None
        inferred_draft_creation = (
            self._active_target_intent == TargetMutationIntent.INFERRED_DRAFT_CREATION
        )
        promotion_plan = plan_terminal_promotion(
            TerminalPromotionRequest(
                selected_candidate=selected_candidate,
                gate_results=tuple(gate_results),
                apply_policy=apply_policy,
                measurement_mode=self.measurement_mode,
                measurement_summary=measurement_summary,
                fresh_evaluation_required=fresh_evaluation_required,
                optimizer_diagnostics=tuple(optimizer_diagnostics),
                baseline_summary=baseline_summary,
                candidate_summary=candidate_summary,
                inferred_draft_creation=inferred_draft_creation,
                inferred_new_skill_policy=self.inferred_new_skill_policy,
            ),
            runtime=TerminalPromotionRuntime(
                verified_apply_policy=_is_verified_apply_policy,
                infrastructure_prevented_comparable_evaluation=(
                    _infrastructure_prevented_comparable_evaluation
                ),
                status_without_selected_candidate=(_status_without_selected_candidate),
            ),
        )
        final_status = promotion_plan.final_status
        promotion = (
            dict(promotion_plan.promotion)
            if promotion_plan.promotion is not None
            else None
        )
        if promotion_plan.should_apply:
            assert selected_candidate is not None
            apply_kwargs = {
                "expected_package_fingerprint": (
                    replay_result.request.verified_candidate_package_fingerprint
                    if replay_result is not None
                    else None
                ),
                "addressed_lesson_ids": _lineage_addressed_lesson_ids(
                    optimizer_lineage_paths_by_candidate.get(
                        selected_candidate.candidate_id
                    )
                ),
            }
            if apply_policy == "verified_only":
                post_apply = await self._apply_verified_only(
                    run_id,
                    target,
                    selected_candidate,
                    **apply_kwargs,
                )
            else:
                post_apply = await self._apply_auto_verified(
                    run_id,
                    target,
                    selected_candidate,
                    **apply_kwargs,
                )
            final_status = settle_post_apply_status(final_status, post_apply)

        if inferred_draft_creation:
            published = (
                apply_policy == "auto_verified"
                and post_apply is not None
                and post_apply.get("status") == "accepted"
            )
            if selected_candidate is not None and not published:
                try:
                    if isinstance(target, DraftSkillTextTarget):
                        target.preserve_selected_draft(selected_candidate.content)
                except (FileExistsError, OSError, ValueError) as exc:
                    gate_results.append(
                        GateResult(
                            gate_name="draft_persistence",
                            passed=False,
                            reason="selected inferred skill draft could not be persisted",
                            details={
                                "failure_class": "infrastructure",
                                "code": "draft_persistence_failed",
                                "type": type(exc).__name__,
                                "reason": str(exc),
                            },
                        )
                    )
                    final_status = SelfEvolveRunStatus.FAILED
            runtime_skill_path = _target_runtime_skill_path(target)
            promotion = project_inferred_draft_promotion(
                InferredDraftPromotionRequest(
                    policy=self.inferred_new_skill_policy,
                    apply_policy=apply_policy,
                    selected_candidate=selected_candidate,
                    post_apply=post_apply,
                    draft_path=target.identity.path,
                    release_path=(
                        str(runtime_skill_path)
                        if runtime_skill_path is not None
                        else None
                    ),
                    runtime_registry_refresh_configured=(
                        self.runtime_registry_refresher is not None
                    ),
                    initial_promotion=promotion,
                )
            )
            if target_selection_report is not None:
                target_selection_report = replace(
                    target_selection_report,
                    diagnostics=project_target_selection_promotion_diagnostics(
                        target_selection_report.diagnostics,
                        promotion,
                    ),
                )
                self.store.write_target_selection_report(
                    run_id,
                    target_selection_report,
                )

        if optimizer_lineage_paths_by_candidate:
            _persist_lineage_lifecycle(
                optimizer_lineage_paths_by_candidate,
                iteration_states=iteration_states,
                attempt_events=self.store.read_all_candidate_attempt_events(run_id),
                selected_candidate_id=(
                    reported_selected_candidate.candidate_id
                    if reported_selected_candidate is not None
                    else None
                ),
                post_apply=post_apply,
            )

        execution_stages = self.execution_telemetry.to_report()
        generation_stop_reason = run_state.generation.stop_reason()
        report = {
            "run_id": run_id,
            "target": {
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
                "path": target.identity.path,
            },
            "apply_policy": apply_policy,
            "candidate_ids": [candidate.candidate_id for candidate in all_candidates],
            "selected_candidate_id": (
                reported_selected_candidate.candidate_id
                if reported_selected_candidate is not None
                else None
            ),
            "repair_focus_candidate_id": (
                repair_focus_candidate.candidate_id
                if repair_focus_candidate is not None
                else None
            ),
            "status": final_status.value,
            "target_provenance": target_provenance_report,
            "optimizer_diagnostics": (
                optimizer_diagnostics[0]["diagnostics"]
                if len(optimizer_diagnostics) == 1
                else {"iterations": optimizer_diagnostics}
            ),
            "prior_feedback_count": len(prior_feedback),
            "screening_control_preflight": screening_control_preflight,
            "support_specific_control_health": {
                "schema_version": (
                    "aworld.self_evolve.support_specific_control_health.v1"
                ),
                "identity_fields": [
                    "case_id",
                    "baseline_skill_fingerprint",
                    "capability_package_fingerprint",
                    "replay_capability_fingerprint",
                    "adaptation_fingerprint",
                    "timeout_envelope_fingerprint",
                ],
                "observations": [
                    dict(observation)
                    for observation in list(
                        self._candidate_screening_control_observations.values()
                    )[-128:]
                ],
            },
            "iterations": iteration_reports,
            "execution": {
                "stages": execution_stages,
                "total_usage": _execution_usage_report(
                    optimizer_diagnostics=optimizer_diagnostics,
                    iteration_states=iteration_states,
                    stages=execution_stages,
                ),
            },
            "budget": budget_context.to_dict(),
            "regression_evidence": (
                regression_evidence.to_dict()
                if regression_evidence is not None
                else None
            ),
            "challenge_report": (
                challenge_report.to_dict() if challenge_report is not None else None
            ),
            "composition_prerequisites": [
                {
                    "candidate_id": candidate_id,
                    "status": "verified_support",
                    "next_stage": "target_behavior_composition",
                    "inherit_candidate_package": True,
                    "evidence": "all_applicable_prerequisite_gates_passed",
                }
                for candidate_id in dict.fromkeys(run_state.prerequisite_candidate_ids)
            ],
            "verification_funnel": run_state.verification_funnel_report(
                VerificationFunnelRequest(
                    screening_max_cases=self.candidate_screening_max_cases,
                    repair_iteration_horizon=iteration_budget,
                    candidate_generation_batch_count=len(optimizer_diagnostics),
                    max_generated_candidates=self.max_generated_candidates,
                    repair_reserved_slot_count=repair_reserved_slot_count,
                    unique_generated_candidate_count=len(all_candidates),
                    policy_filtered_candidate_count=sum(
                        len(_candidate_policy_filter_outcomes(diagnostics))
                        for diagnostics in _optimizer_iteration_diagnostics(
                            optimizer_diagnostics
                        )
                    ),
                    max_authoritative_candidates=(self.max_full_evaluation_candidates),
                    max_score_tiebreak_candidates=(self.max_score_tiebreak_candidates),
                    authoritative_case_observations=(
                        self._current_run_authoritative_case_observations
                    ),
                )
            ),
            "handbook_slice": latest_handbook_slice,
            "repair_frontier_state": _repair_frontier_state_report(
                store=self.store,
                target=target.identity,
                current_run_id=run_id,
                allowed_run_ids=campaign_prior_run_ids,
                observed_frontiers=_typed_repair_frontiers(validation_feedback),
                scheduler_state=scheduler_state,
                selected_candidate_id=(
                    selected_candidate.candidate_id
                    if selected_candidate is not None
                    else None
                ),
                run_succeeded=final_status is SelfEvolveRunStatus.SUCCEEDED,
                campaign_id=campaign_id,
                campaign_cycle=campaign_cycle,
            ),
            "regression_suites": [
                suite.spec.to_dict()
                for suite in (
                    *self.regression_suites,
                    *(challenge_report.suites if challenge_report is not None else ()),
                )
            ],
        }
        measurement_report = project_measurement_report(
            MeasurementReportRequest(
                summary=measurement_summary,
                mode=self.measurement_mode,
                candidate_prerequisite_blocked=(candidate_prerequisite_blocked),
                measurement_prerequisite_blocked=(measurement_prerequisite_blocked),
                gate_results=tuple(gate_results),
            ),
            candidate_prerequisite_failure=(_gate_has_candidate_prerequisite_failure),
            measurement_materialization_blocked=(
                _gate_blocks_measurement_materialization
            ),
        )
        if measurement_report is not None:
            report["measurement"] = measurement_report
        _emit_progress(
            self.progress_callback,
            "lesson_extraction",
            "Extracting lesson memory and harness diagnostics",
        )
        finalization = finalize_terminal_run(
            TerminalFinalizationRequest(
                run_id=run_id,
                target=target.identity,
                final_status=final_status,
                reported_selected_candidate=reported_selected_candidate,
                repair_focus_candidate=repair_focus_candidate,
                apply_policy=apply_policy,
                base_report=report,
                optimizer_diagnostics=tuple(optimizer_diagnostics),
                gate_results=tuple(gate_results),
                scheduler_decisions=tuple(scheduler_decisions),
                population_screening_reports=tuple(population_screening_reports),
                iteration_states=tuple(iteration_states),
                iteration_reports=tuple(iteration_reports),
                generation_stop_reason=generation_stop_reason,
                dataset=dataset,
                all_candidates=tuple(all_candidates),
                replay_candidate_limit=self.replay_candidate_limit,
                budget_report=budget_context.to_dict(),
                optimizer_lineage_paths=tuple(optimizer_lineage_paths),
                target_selection_report=target_selection_report,
                post_apply=post_apply,
                promotion=promotion,
                baseline_summary=baseline_summary,
                candidate_summary=candidate_summary,
                held_out_summary=held_out_summary,
                replay_result=replay_result,
                replay_dataset=replay_dataset,
                skill_evolution_progress=skill_evolution_progress,
                trace_packs=tuple(trace_packs),
                candidate_source_dispositions=(candidate_source_dispositions),
                deprecated_config_mappings=(self.deprecated_config_mappings),
                previous_artifact_retention=startup_artifact_retention,
            ),
            runtime=TerminalFinalizationRuntime(
                store=self.store,
                terminal_cause=_terminal_cause,
                rejection_attribution=_rejection_attribution,
                resolved_contract_fingerprints=(
                    _resolved_conformance_contract_fingerprints
                ),
                campaign_failure_attribution=(_campaign_failure_attribution),
                trajectory_set_report=_trajectory_set_report,
                population_report=_population_report,
                no_op_report=_no_op_report,
                replay_report=_replay_report,
                replay_artifact_path=_replay_artifact_path,
                campaign_measurement_outcome=(_campaign_measurement_outcome_for_replay),
                replay_capability_report=_replay_capability_report,
                evaluator_report_paths=_evaluator_report_paths,
                acceptance_confidence_report=(_acceptance_confidence_report),
                finalize_run_report=_finalize_run_report,
            ),
        )
        completed_run = finalization.completed_run
        self._candidate_screening_loaded_run_ids.add(run_id)
        _emit_progress(
            self.progress_callback,
            "completed",
            f"Self-evolve run {run_id} finished with status {completed_run.status.value}",
        )
        return SelfEvolveRunnerResult(
            run=completed_run,
            selected_candidate=reported_selected_candidate,
        )

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
        stored_measurement_resume: bool = False,
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
            stored_measurement_resume=stored_measurement_resume,
        )
        result = await self._screening_controller.screen_population(
            request,
            execute=self._execute_screen_candidate_population,
            runtime=ScreeningPopulationRuntime(
                store=self.store,
                execution_telemetry=self.execution_telemetry,
                replay_enabled=self.replay_enabled,
                replay_backend=self.candidate_replay_backend,
                candidate_screening_max_cases=(self.candidate_screening_max_cases),
                replay_max_steps=self.replay_max_steps,
                replay_timeout_seconds=self.replay_timeout_seconds,
                baseline_replay_repetitions=(self.baseline_replay_repetitions),
                candidate_replay_repetitions=(self.candidate_replay_repetitions),
                progress_callback=self.progress_callback,
                case_observations=(self._candidate_screening_case_observations),
                control_observations=(self._candidate_screening_control_observations),
                invalid_control_case_ids_by_run=(
                    self._candidate_screening_run_invalid_control_case_ids
                ),
                measurement_experiments=(self._screening_measurement_experiments),
                validate_conformance_population=(
                    self._validate_candidate_repair_conformance_population
                ),
                plan_measurement=self._plan_candidate_measurement,
                prepare_adaptation=self._prepare_replay_adaptation,
                replay_candidate=self._replay_selected_candidate,
                baseline_reuse_provenance=(self._baseline_reuse_provenance),
                policy=self._screening_controller,
                control_qualification_identity=(_control_qualification_identity),
            ),
        )
        return result.candidates, result.report

    _execute_screen_candidate_population = staticmethod(
        execute_screen_candidate_population
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
        applicable = tuple(
            candidate
            for candidate in candidates
            if candidate.candidate_id in repair_conformance_contracts
        )
        if not applicable:
            return candidates, None

        _emit_progress(
            self.progress_callback,
            "candidate_conformance",
            (
                "Validating candidate repair conformance across "
                f"{len(dataset.cases)} dataset case(s)"
            ),
        )
        attempts: list[dict[str, object]] = []
        passed_candidates: list[CandidateVariant] = []
        stopped_by_shared_infrastructure = False
        superseded_candidate_ids: list[str] = []
        superseding_contract_identity: str | None = None
        for candidate_index, candidate in enumerate(candidates):
            contract = repair_conformance_contracts.get(candidate.candidate_id)
            if contract is None:
                passed_candidates.append(candidate)
                continue
            attempt_key = (
                attempt_keys.get(candidate.candidate_id)
                if attempt_keys is not None
                else None
            )
            source_conformance = evaluate_candidate_source_conformance(
                candidate,
                contract,
            )
            if not source_conformance.passed:
                if attempt_tracker is not None and attempt_key is not None:
                    attempt_tracker.emit(
                        attempt_key,
                        CandidateAttemptStage.REJECTED,
                        reason_code="source_conformance_rejected",
                    )
                attempts.append(
                    _repair_conformance_screening_attempt(
                        candidate,
                        source_conformance,
                        contract=contract,
                    )
                )
                if source_conformance.failure_class in {
                    "framework",
                    "infrastructure",
                }:
                    stopped_by_shared_infrastructure = True
                    passed_candidates.clear()
                    break
                continue
            gate = await self._preflight_candidate_repair_conformance(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=candidate,
                contract=contract,
                capability_requirements=capability_requirements,
                budget_context=budget_context,
            )
            if attempt_tracker is not None and attempt_key is not None:
                if (
                    attempt_tracker.last_stage(attempt_key)
                    is CandidateAttemptStage.LOCAL_GATES
                ):
                    attempt_tracker.emit(
                        attempt_key,
                        CandidateAttemptStage.ADAPTATION,
                        case_count=len(dataset.cases),
                    )
                gate_code = (
                    str(gate.details.get("code") or "")
                    if isinstance(gate.details, Mapping)
                    else ""
                )
                probe_plan_payload = (
                    gate.details.get("probe_plan")
                    if isinstance(gate.details, Mapping)
                    else None
                )
                probe_groups = (
                    probe_plan_payload.get("groups")
                    if isinstance(probe_plan_payload, Mapping)
                    else None
                )
                counterexample_contracts = (
                    gate.details.get("counterexample_contracts")
                    if isinstance(gate.details, Mapping)
                    else None
                )
                violations = (
                    gate.details.get("violations")
                    if isinstance(gate.details, Mapping)
                    else None
                )
                shape_count = max(
                    len(probe_groups) if isinstance(probe_groups, (list, tuple)) else 0,
                    len(counterexample_contracts)
                    if isinstance(counterexample_contracts, (list, tuple))
                    else 0,
                    len(violations) if isinstance(violations, (list, tuple)) else 0,
                )
                if gate_code == "conformance_budget_denied":
                    attempt_tracker.emit(
                        attempt_key,
                        CandidateAttemptStage.NOT_RUN,
                        reason_code="conformance_budget_denied",
                    )
                elif gate_code != "repair_capability_compile_failed":
                    attempt_tracker.emit(
                        attempt_key,
                        CandidateAttemptStage.CONFORMANCE,
                        case_count=len(dataset.cases),
                        distinct_conformance_shape_count=shape_count,
                    )
            attempt = {
                "candidate_id": candidate.candidate_id,
                "screening_candidate_id": None,
                "stage": "conformance",
                "gate_name": gate.gate_name,
                "passed": gate.passed,
                "reason": gate.reason,
                "details": gate.details,
            }
            attempts.append(attempt)
            if gate.passed:
                passed_candidates.append(candidate)
                continue
            evolved_contract = (
                gate.details.get("repair_conformance")
                if isinstance(gate.details, Mapping)
                else None
            )
            if isinstance(evolved_contract, Mapping):
                evolved_identity = repair_conformance_contract_identity(
                    evolved_contract
                )
                if (
                    evolved_identity != contract.contract_identity
                    and _repair_conformance_validation_surface_changed(
                        contract,
                        evolved_contract,
                    )
                ):
                    # The first member has discovered a deeper cumulative
                    # contract. Remaining siblings were leased against stale
                    # evidence and must not spend compile/replay budget or be
                    # mistaken for independent repair attempts.
                    superseding_contract_identity = evolved_identity
                    superseded = tuple(candidates[candidate_index + 1 :])
                    superseded_candidate_ids.extend(
                        item.candidate_id for item in superseded
                    )
                    for stale_candidate in superseded:
                        stale_key = (
                            attempt_keys.get(stale_candidate.candidate_id)
                            if attempt_keys is not None
                            else None
                        )
                        if (
                            attempt_tracker is not None
                            and stale_key is not None
                            and not attempt_tracker.terminal(stale_key)
                        ):
                            attempt_tracker.emit(
                                stale_key,
                                CandidateAttemptStage.NOT_RUN,
                                reason_code="repair_contract_superseded",
                            )
                    passed_candidates.clear()
                    break
            if _conformance_gate_blocks_population(gate):
                stopped_by_shared_infrastructure = True
                passed_candidates.clear()
                break

        return (
            tuple(passed_candidates),
            {
                "generated_candidate_count": len(candidates),
                "applicable_candidate_count": len(applicable),
                "attempted_candidate_count": len(attempts),
                "passed_candidate_ids": [
                    candidate.candidate_id for candidate in passed_candidates
                ],
                "stopped_by_shared_infrastructure": (stopped_by_shared_infrastructure),
                "superseded_candidate_ids": superseded_candidate_ids,
                "superseding_contract_identity": superseding_contract_identity,
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
        capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
        budget_context: _RunBudgetContext | None = None,
    ) -> GateResult:
        if target.identity.path is None:
            return _repair_conformance_gate(
                RepairConformanceResult(
                    passed=False,
                    code="repair_target_path_missing",
                    reason="repair conformance requires a filesystem skill target",
                    details={},
                    failure_class="framework",
                    repairable=False,
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
            adaptation_details = dict(adaptation_gate.details or {})
            # This compilation is performed against the candidate overlay.
            # It is candidate-owned unless the adaptation layer explicitly
            # supplies a native shared-run infrastructure/framework event.
            declared_owner = str(adaptation_details.get("failure_owner") or "")
            declared_scope = str(adaptation_details.get("failure_scope") or "")
            declared_source = str(adaptation_details.get("failure_source") or "")
            proven_shared = bool(
                declared_owner
                in {
                    FailureOwner.INFRASTRUCTURE.value,
                    FailureOwner.FRAMEWORK.value,
                }
                and declared_scope == FailureScope.SHARED_RUN.value
                and declared_source == FailureEventSource.NATIVE.value
            )
            candidate_owned = not proven_shared
            capability_error_code = str(
                adaptation_details.get("capability_error_code") or ""
            ).strip()
            repair_conformance = (
                merge_repair_conformance_constraint_context(
                    contract.to_public_dict(),
                    adaptation_details,
                )
                or contract.to_public_dict()
            )
            if proven_shared:
                return replace(
                    adaptation_gate,
                    details={
                        **adaptation_details,
                        "stage": (
                            adaptation_details.get("stage")
                            or "repair_conformance_compile"
                        ),
                        "repair_conformance": repair_conformance,
                        "source_gate_name": adaptation_gate.gate_name,
                    },
                )
            failure_event = ReplayFailureEvent(
                code=(capability_error_code or "repair_capability_compile_failed"),
                owner=(
                    FailureOwner.CANDIDATE
                    if candidate_owned
                    else FailureOwner(declared_owner)
                ),
                stage=FailureStage.CAPABILITY_COMPILE,
                scope=(
                    FailureScope.CANDIDATE
                    if candidate_owned
                    else FailureScope.SHARED_RUN
                ),
                repairable=candidate_owned,
                category="repair_conformance",
                contract_fingerprint=_schema_field_contract_fingerprint(
                    repair_conformance
                ),
                summary=adaptation_gate.reason,
                diagnostics={
                    "gate_name": adaptation_gate.gate_name,
                    "outer_code": adaptation_details.get("code"),
                    "capability_error_code": capability_error_code or None,
                },
            )
            return GateResult(
                gate_name="candidate_repair_conformance",
                passed=False,
                reason=adaptation_gate.reason,
                details={
                    **adaptation_details,
                    "failure_class": (
                        "candidate" if candidate_owned else "infrastructure"
                    ),
                    "repairable": candidate_owned,
                    "stage": "repair_conformance_compile",
                    "code": "repair_capability_compile_failed",
                    "capability_error_code": capability_error_code or None,
                    "repair_conformance": repair_conformance,
                    "failure_event": failure_event.to_dict(),
                    "causal_failure_events": [failure_event.to_dict()],
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
            fixture_leaf_values=replay_capability_fixture_leaf_values(capability),
            fixture_response_leaf_values=(
                replay_capability_fixture_response_leaf_values(capability)
            ),
        )
        if not probe_conformance.passed:
            return _repair_conformance_gate(
                probe_conformance,
                contract=contract,
            )
        probe_plan = build_repair_conformance_probe_plan(
            capability_id=capability.capability_id,
            services=capability.services,
            requirements=capability_requirements,
            fixture_shape_fingerprints=(
                frozen_replay_fixture_shape_fingerprints(capability)
            ),
            contract=contract,
            dataset_case_ids=tuple(
                case.case_id
                for case in dataset.cases
                if _is_replayable_user_task_case(case)
            ),
        )
        artifact_root = (
            self.store.run_path(run_id)
            / "repair_conformance"
            / _safe_artifact_name(candidate.candidate_id)
        )
        group_results: list[dict[str, object]] = []
        groups = probe_plan.groups
        conformance_budget: BudgetDecision | None = None
        if groups and budget_context is not None:
            conformance_budget = budget_context.reserve(
                BudgetStage.CONFORMANCE,
                f"{candidate.candidate_id}-conformance",
                units=len(groups),
            )
            if not conformance_budget.allowed:
                return GateResult(
                    gate_name="candidate_repair_conformance",
                    passed=False,
                    reason="repair conformance was not run because budget was denied",
                    details={
                        "failure_class": "budget",
                        "repairable": False,
                        "stage": "repair_conformance",
                        "code": "conformance_budget_denied",
                        "probe_plan": probe_plan.to_dict(),
                        "distinct_conformance_shape_count": len(groups),
                        "budget_decision": conformance_budget.to_dict(),
                    },
                )
        for group_index, group in enumerate(groups):
            fingerprint = group.fingerprint
            artifact_dir = artifact_root / (
                f"group-{group_index + 1:03d}-"
                f"{fingerprint.removeprefix('sha256:')[:12]}"
            )
            try:
                projected_capability = project_replay_capability_for_probe_group(
                    capability,
                    group,
                )
                required_nonempty_operations = tuple(
                    operation
                    for operation in _repair_conformance_required_nonempty_operations(
                        contract
                    )
                    if operation == group.operation
                )
                required_recorded_operations = tuple(
                    operation
                    for operation in (
                        (
                            contract.required_fixture_probe_operations
                            or contract.late_observed_operations[-1:]
                        )
                        if contract.requires_fixture_derived_probe
                        else ()
                    )
                    if operation == group.operation
                )
                await preflight_frozen_replay_capability(
                    projected_capability,
                    artifact_dir=artifact_dir,
                    required_nonempty_probe_operations=required_nonempty_operations,
                    required_recorded_probe_operations=required_recorded_operations,
                    integrity_capability=capability,
                )
            except Exception as exc:
                artifact_ref = sanitize_path_ref(
                    artifact_dir.relative_to(self.store.workspace_root).as_posix()
                    if artifact_dir.is_relative_to(self.store.workspace_root)
                    else artifact_dir.name
                )
                error_reason = sanitize_text(str(exc), max_chars=512)
                error_code = _repair_probe_root_cause_code(exc)
                raw_error_details = getattr(exc, "details", None)
                typed_error_details = {
                    key: value
                    for key, value in (
                        dict(raw_error_details).items()
                        if isinstance(raw_error_details, Mapping)
                        else ()
                    )
                    if key
                    in {
                        "runtime_artifact_constraints",
                        "runtime_response_constraints",
                        "runtime_response_observation",
                        "schema_field_constraints",
                        "schema_field_violations",
                        "schema_field_violation_count",
                        "counterexample_contracts",
                    }
                }
                failure_event = ReplayFailureEvent(
                    code=error_code,
                    owner=FailureOwner.CANDIDATE,
                    stage=FailureStage.CAPABILITY_PREFLIGHT,
                    scope=FailureScope.CANDIDATE,
                    repairable=True,
                    category="repair_conformance",
                    summary="candidate conformance probe group failed",
                    diagnostics={
                        "affected_case_ids": list(group.case_ids)[:100],
                        "error_type": type(exc).__name__,
                        "root_cause_code": error_code,
                        "reason": error_reason,
                        **typed_error_details,
                    },
                    artifact_refs=(artifact_ref,),
                    capability_id=capability.capability_id,
                    requirement_id=(
                        None
                        if typed_error_details.get("runtime_artifact_constraints")
                        else group.requirement_id
                    ),
                    contract_fingerprint=(
                        _schema_field_contract_fingerprint(typed_error_details)
                        or fingerprint
                    ),
                )
                group_observations = tuple(
                    ReplayFailureObservation(
                        event=failure_event,
                        case_id=case_id,
                        run_id=run_id,
                        candidate_id=candidate.candidate_id,
                    )
                    for case_id in group.case_ids
                ) or (
                    ReplayFailureObservation(
                        event=failure_event,
                        run_id=run_id,
                        candidate_id=candidate.candidate_id,
                    ),
                )
                failure_aggregate = aggregate_replay_failure_observations(
                    group_observations
                )[0]
                group_results.append(
                    {
                        "fingerprint": fingerprint,
                        "passed": False,
                        "code": error_code,
                        "root_cause_code": error_code,
                        "requirement_id": group.requirement_id,
                        "case_ids": list(group.case_ids),
                        "artifact_ref": artifact_ref,
                        "error_type": type(exc).__name__,
                        "reason": error_reason,
                        **typed_error_details,
                        "failure_event": failure_aggregate.to_dict(),
                    }
                )
                continue
            group_results.append(
                {
                    "fingerprint": fingerprint,
                    "passed": True,
                    "code": "repair_probe_group_passed",
                    "requirement_id": group.requirement_id,
                    "case_ids": list(group.case_ids),
                    "artifact_ref": sanitize_path_ref(
                        artifact_dir.relative_to(self.store.workspace_root).as_posix()
                        if artifact_dir.is_relative_to(self.store.workspace_root)
                        else artifact_dir.name
                    ),
                }
            )
        if conformance_budget is not None:
            budget_context.debit(
                conformance_budget,
                actual_source="reserved_fallback_local_conformance",
            )
        failed_groups = tuple(
            result for result in group_results if result.get("passed") is False
        )
        if failed_groups:
            return _repair_conformance_gate(
                RepairConformanceResult(
                    passed=False,
                    code="repair_probe_execution_failed",
                    reason=(
                        "candidate declared repair probe failed before task rollout"
                    ),
                    details={
                        "artifact_root": str(artifact_root),
                        "probe_plan": probe_plan.to_dict(),
                        "probe_group_results": group_results[:32],
                        "failed_probe_group_count": len(failed_groups),
                        "failed_case_ids": list(
                            dict.fromkeys(
                                case_id
                                for result in failed_groups
                                for case_id in result.get("case_ids", [])
                                if isinstance(case_id, str)
                            )
                        )[:100],
                        "causal_failure_events": [
                            result["failure_event"]
                            for result in failed_groups
                            if isinstance(result.get("failure_event"), Mapping)
                        ],
                        **_failed_probe_typed_feedback(failed_groups),
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
                    "artifact_root": str(artifact_root),
                    "probe_plan": probe_plan.to_dict(),
                    "probe_group_results": group_results[:32],
                },
            ),
            contract=contract,
        )

    def _load_measurement_resume_request(
        self,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayRequest | None:
        return self._measurement_planning_controller.load_resume_request(
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
            self._measurement_experiments
            if experiment_registry is None
            else experiment_registry
        )
        result = self._measurement_planning_controller.plan(
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
                    self._run_environment_fingerprints.get(run_id)
                ),
                target_intent=(
                    self._active_target_intent.value
                    if self._active_target_intent is not None
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
        return self._measurement_controller.materialize_candidate(
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

    def _attach_measurement_search_performance(
        self,
        *,
        run_id: str,
        summary: MeasurementSummary,
        candidates: Sequence[CandidateVariant],
        iteration_reports: Sequence[Mapping[str, object]],
    ) -> MeasurementSummary:
        reports_by_candidate = {
            str(item.get("candidate_id")): item
            for item in iteration_reports
            if isinstance(item.get("candidate_id"), str)
        }
        results: list[SearchCandidateResult] = []
        baseline_scores: list[float] = []
        for candidate in candidates:
            item = reports_by_candidate.get(candidate.candidate_id, {})
            metrics = (
                item.get("candidate_metrics")
                if isinstance(item.get("candidate_metrics"), Mapping)
                else {}
            )
            score = _finite_measurement_metric(metrics.get("score"))
            baseline_metrics = (
                item.get("baseline_metrics")
                if isinstance(item.get("baseline_metrics"), Mapping)
                else {}
            )
            baseline_score = _finite_measurement_metric(baseline_metrics.get("score"))
            if baseline_score is not None:
                baseline_scores.append(baseline_score)
            held_out_metrics = (
                item.get("held_out_metrics")
                if isinstance(item.get("held_out_metrics"), Mapping)
                else {}
            )
            regression_passed = _optional_measurement_bool(
                held_out_metrics.get(
                    "global_regression_passed",
                    metrics.get("global_regression_passed"),
                )
            )
            status = str(item.get("status") or "not_run")
            tokens = _non_negative_measurement_int(
                metrics.get("search_total_tokens", metrics.get("total_tokens"))
            )
            wall_seconds = _non_negative_measurement_float(
                metrics.get("search_wall_seconds", metrics.get("wall_seconds"))
            )
            results.append(
                SearchCandidateResult(
                    candidate_id=candidate.candidate_id,
                    score=score,
                    passed=status == "accepted",
                    valid=status
                    not in {
                        "local_gate_rejected",
                        "screening_rejected",
                        "not_run",
                    },
                    authoritative=(
                        item.get("baseline_metrics") is not None
                        or item.get("lifecycle_stage") == "authoritative_replay"
                    ),
                    tokens=tokens,
                    wall_seconds=wall_seconds,
                    cost_usd=_non_negative_measurement_float(
                        metrics.get("search_cost_usd", metrics.get("cost_usd"))
                    ),
                    regression_passed=regression_passed,
                )
            )
        token_total = sum(item.tokens or 0 for item in results)
        wall_total = sum(item.wall_seconds or 0.0 for item in results)
        search_performance = build_search_performance(
            results,
            k_values=(1, 2, 4, 8),
            token_budget_points=(
                _budget_curve_points(token_total)
                if results and all(item.tokens is not None for item in results)
                else ()
            ),
            wall_time_budget_points=(
                _budget_curve_points(wall_total)
                if results and all(item.wall_seconds is not None for item in results)
                else ()
            ),
            selection_protocol="generation_order_authoritative_funnel",
            quality_threshold=(
                baseline_scores[0] + self.min_score_delta
                if baseline_scores
                and all(
                    math.isclose(value, baseline_scores[0])
                    for value in baseline_scores[1:]
                )
                else None
            ),
        )
        measurement_authority_run_id = next(
            (
                experiment.run_id
                for experiment in self._measurement_experiments.values()
                if experiment.experiment_id == summary.experiment_id
            ),
            run_id,
        )
        attribution = self.store.read_measurement_attribution_report(
            measurement_authority_run_id,
            summary.experiment_id,
        )
        search_usage = MeasurementUsage(
            tokens=(
                token_total
                if results and all(item.tokens is not None for item in results)
                else None
            ),
            cost_usd=(
                sum(item.cost_usd or 0.0 for item in results)
                if results and all(item.cost_usd is not None for item in results)
                else None
            ),
            wall_seconds=(
                wall_total
                if results and all(item.wall_seconds is not None for item in results)
                else None
            ),
            candidate_opportunities=len(results),
        )
        updated = replace(
            attribution,
            search_performance=search_performance,
            budget_ledger=BudgetLedger(
                search=search_usage,
                measurement=attribution.budget_ledger.measurement,
            ),
            measurement_yield=replace(
                attribution.measurement_yield,
                search_tokens=search_usage.tokens,
                authoritative_candidate_count=sum(
                    1 for item in results if item.authoritative
                ),
            ),
        )
        self.store.write_measurement_attribution_report(updated)
        refreshed_summary = updated.summary(
            attribution_report_path=self.store.measurement_attribution_ref(
                measurement_authority_run_id,
                summary.experiment_id,
            )
        )
        candidate_key = next(
            (
                key
                for key, cached in self._measurement_summaries.items()
                if key[0] == measurement_authority_run_id
                and cached.experiment_id == summary.experiment_id
            ),
            None,
        )
        if candidate_key is not None:
            self._measurement_summaries[candidate_key] = refreshed_summary
        return refreshed_summary

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

        result = await self._execute_iteration_candidate(
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

    async def _execute_iteration_candidate(
        self,
        request: CandidateEvaluationRequest,
    ) -> CandidateEvaluationResult:
        """Execute one candidate through the typed evaluation boundary."""

        run_id = request.run_id
        target = request.target
        dataset = request.dataset
        candidate = request.candidate
        candidate_count = request.candidate_count

        local_admission = execute_candidate_local_admission(
            request,
            CandidateLocalAdmissionPolicy(
                workspace_root=self.store.workspace_root,
                max_candidate_chars=_DEFAULT_CANDIDATE_CONTENT_MAX_CHARS,
                allow_generated_target_mutation=(self.allow_generated_target_mutation),
                allow_external_target_mutation=(self.allow_external_target_mutation),
                target_intent=self._active_target_intent,
                inferred_new_skill_policy=self.inferred_new_skill_policy,
                skip_duplicate_rejected_candidate_gate=(
                    self.skip_duplicate_rejected_candidate_gate
                ),
                gate_evaluator=_candidate_gate_results,
            ),
        )
        gate_results = list(local_admission.gate_results)
        if local_admission.terminal_result is not None:
            return local_admission.terminal_result

        measurement_experiment: ControlledExperimentSpec | None = None
        if self.measurement_mode is not MeasurementPolicyMode.OFF:
            try:
                measurement_experiment = self._plan_candidate_measurement(
                    run_id=run_id,
                    target=target,
                    dataset=dataset,
                    candidate=candidate,
                    candidate_count=candidate_count,
                )
            except (OSError, TypeError, ValueError) as exc:
                if self.measurement_mode is MeasurementPolicyMode.REQUIRED:
                    gate_results.append(
                        GateResult(
                            gate_name="trusted_improvement_measurement",
                            passed=False,
                            reason="controlled experiment contract could not be frozen",
                            details={
                                "failure_class": "measurement",
                                "code": "measurement_contract_invalid",
                                "type": type(exc).__name__,
                                "reason": str(exc),
                            },
                        )
                    )

        replay_admission = await execute_candidate_replay_admission(
            request,
            CandidateReplayAdmissionPolicy(
                replay_enabled=self.replay_enabled,
                replay_backend=self.candidate_replay_backend,
                repetitions_explicit=self.replay_repetitions_explicit,
                measurement_min_independent_cases=(
                    self.measurement_min_independent_cases
                ),
                baseline_repetitions=self.baseline_replay_repetitions,
                candidate_repetitions=self.candidate_replay_repetitions,
                judge_repetitions=self.judge_repetitions,
                replay_candidate_limit=self.replay_candidate_limit,
                per_attempt_replay_token_limit=(self.per_attempt_replay_token_limit),
                replay_tokens_per_unit=self.replay_tokens_per_unit,
            ),
            CandidateReplayAdmissionRuntime(
                reusable_baseline_case_count=_reusable_baseline_case_count,
                validate_capabilities=self._validate_candidate_capabilities,
                typed_gate_failure=_with_typed_gate_failure_event,
                feedback_builder=_iteration_validation_feedback,
            ),
            initial_gate_results=gate_results,
        )
        gate_results = list(replay_admission.gate_results)
        if replay_admission.terminal_result is not None:
            return replay_admission.terminal_result
        replay_execution = await execute_candidate_replay(
            CandidateReplayExecutionRequest(
                evaluation=request,
                admission=replay_admission,
            ),
            CandidateReplayExecutionRuntime(
                replay_candidate=self._replay_selected_candidate,
                execution_telemetry=self.execution_telemetry,
                replay_confidence_gate=_replay_confidence_gate,
                replay_evaluator_admission_gate=(_replay_evaluator_admission_gate),
                typed_gate_failure=_with_typed_gate_failure_event,
                feedback_builder=_iteration_validation_feedback,
            ),
        )
        if replay_execution.terminal_result is not None:
            return replay_execution.terminal_result
        evaluation_admission = plan_candidate_evaluation_admission(
            CandidateEvaluationAdmissionRequest(
                evaluation=request,
                replay=replay_execution,
            ),
            CandidateEvaluationAdmissionPolicy(
                replay_enabled=self.replay_enabled,
                evaluation_backend=self.evaluation_backend,
                judge_repetitions=self.judge_repetitions,
                regression_suite_case_counts=tuple(
                    len(suite.dataset.cases) for suite in self.regression_suites
                ),
                challenger_enabled=self.challenger_enabled,
                challenger_max_cases=self.challenger_max_cases,
            ),
            CandidateEvaluationAdmissionRuntime(
                typed_gate_failure=_with_typed_gate_failure_event,
                feedback_builder=_iteration_validation_feedback,
            ),
        )
        if evaluation_admission.terminal_result is not None:
            return evaluation_admission.terminal_result
        evaluation_execution = await execute_candidate_evaluation(
            CandidateEvaluationExecutionRequest(
                evaluation=request,
                replay=replay_execution,
                admission=evaluation_admission,
            ),
            CandidateEvaluationExecutionPolicy(
                evaluation_backend=self.evaluation_backend,
                max_iterations=self.max_iterations,
                min_score_delta=self.min_score_delta,
                replay_stability_margin=self.replay_stability_margin,
                min_eval_cases=self.min_eval_cases,
                require_resource_evidence=(
                    isinstance(
                        self.evaluation_backend,
                        AWorldTrajectoryEvaluatorBackend,
                    )
                    or getattr(
                        self.evaluation_backend,
                        "resource_accounting_required",
                        False,
                    )
                    is True
                ),
            ),
            CandidateEvaluationExecutionRuntime(
                task_batch_executor=self.task_batch_executor,
                max_concurrency=self.concurrency_policy.effective_limit(
                    "evaluation",
                    item_count=2,
                ),
                execution_telemetry=self.execution_telemetry,
                progress_callback=self.progress_callback,
                evaluate_pair=evaluate_baseline_and_candidate,
                evaluate_variant=evaluate_variant_task,
                merge_replay_evidence=(_summary_with_replay_evidence_metrics),
                evidence_quality_gate=_evidence_quality_gate,
                accumulate_score_evidence=_accumulate_score_evidence,
                replay_stability_gate=_replay_stability_gate,
                same_evaluation_execution=_same_evaluation_execution,
                judge_actual_token_usage=_judge_actual_token_usage,
                evaluate_independent_regression=(self._evaluate_independent_regression),
                gate_is_replay_infrastructure_failure=(
                    _gate_is_replay_execution_infrastructure_failure
                ),
            ),
        )
        return finalize_candidate_evaluation(
            CandidateEvaluationFinalizationRequest(
                evaluation=request,
                replay=replay_execution,
                execution=evaluation_execution,
                measurement_experiment=measurement_experiment,
            ),
            CandidateEvaluationFinalizationPolicy(
                measurement_mode=self.measurement_mode,
                auto_apply_target_types=self.auto_apply_target_types,
            ),
            CandidateEvaluationFinalizationRuntime(
                materialize_measurement=(self._materialize_candidate_measurement),
                typed_gate_failure=_with_typed_gate_failure_event,
                feedback_builder=_iteration_validation_feedback,
            ),
        )

    async def _validate_candidate_capabilities(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        requirements: tuple[ReplayCapabilityRequirement, ...],
    ) -> list[GateResult]:
        if not self.replay_enabled or not requirements or target.identity.path is None:
            return []
        framework_adaptation, framework_gate = self._prepare_replay_adaptation(
            run_id=run_id,
            dataset=dataset,
            emit_progress=False,
        )
        if framework_gate.passed and framework_adaptation is not None:
            # Framework-owned adapters provide the replay environment without
            # executing candidate-owned capability code. Operational candidate
            # preflight is only required when replay depends on that code.
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
            diagnostic_events = [
                event
                for item in diagnostics
                for event in (
                    item.get("failure_event"),
                    *(item.get("causal_failure_events") or ()),
                )
                if isinstance(event, Mapping)
            ]
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
                        "code": (diagnostics[0].get("code") if diagnostics else None),
                        "failure_class": (
                            diagnostics[0]["failure_class"] if diagnostics else None
                        ),
                        "repairable": (
                            all(bool(item.get("repairable")) for item in diagnostics)
                            if diagnostics
                            else False
                        ),
                        "diagnostics": diagnostics,
                        **(
                            {
                                "failure_event": dict(diagnostic_events[0]),
                                "causal_failure_events": [
                                    dict(event) for event in diagnostic_events
                                ],
                            }
                            if diagnostic_events
                            else {}
                        ),
                    },
                )
            )
        if any(not gate.passed for gate in gates):
            return gates

        replay_gate_index = next(
            (
                index
                for index, gate in enumerate(gates)
                if gate.gate_name == "candidate_capability_replay"
            ),
            None,
        )
        if replay_gate_index is None:
            return gates

        adaptation, adaptation_gate = self._prepare_replay_adaptation(
            run_id=run_id,
            dataset=dataset,
            capability_skill_root=overlay.candidate_skill_path.parent,
            candidate_package_fingerprint=candidate_package_fingerprint(candidate),
            emit_progress=False,
        )
        if adaptation is None or not adaptation_gate.passed:
            details = dict(adaptation_gate.details or {})
            proven_shared = bool(
                details.get("failure_owner")
                in {
                    FailureOwner.INFRASTRUCTURE.value,
                    FailureOwner.FRAMEWORK.value,
                }
                and details.get("failure_scope") == FailureScope.SHARED_RUN.value
                and details.get("failure_source") == FailureEventSource.NATIVE.value
            )
            owner = (
                FailureOwner.INFRASTRUCTURE if proven_shared else FailureOwner.CANDIDATE
            )
            event = ReplayFailureEvent(
                code=str(
                    details.get("capability_error_code")
                    or details.get("code")
                    or "candidate_capability_compile_failed"
                ),
                owner=owner,
                stage=FailureStage.CAPABILITY_COMPILE,
                scope=(
                    FailureScope.SHARED_RUN if proven_shared else FailureScope.CANDIDATE
                ),
                repairable=not proven_shared,
                category="candidate_capability_preflight",
                summary=adaptation_gate.reason,
                diagnostics={
                    "gate_name": adaptation_gate.gate_name,
                    "candidate_id": candidate.candidate_id,
                },
            )
            gates[replay_gate_index] = GateResult(
                gate_name="candidate_capability_replay",
                passed=False,
                reason=(
                    "candidate replay capability could not be compiled for "
                    "operational preflight"
                ),
                details={
                    **details,
                    "failure_class": (
                        "infrastructure" if proven_shared else "candidate"
                    ),
                    "repairable": not proven_shared,
                    "stage": "capability_compile",
                    "code": "candidate_capability_compile_failed",
                    "failure_event": event.to_dict(),
                    "causal_failure_events": [event.to_dict()],
                },
            )
            return gates

        capability = adaptation.replay_capability
        if capability is None:
            event = ReplayFailureEvent(
                code="candidate_replay_capability_missing_after_compile",
                owner=FailureOwner.CANDIDATE,
                stage=FailureStage.CAPABILITY_COMPILE,
                scope=FailureScope.CANDIDATE,
                repairable=True,
                category="candidate_capability_preflight",
                summary="candidate replay adaptation did not freeze a capability",
            )
            gates[replay_gate_index] = GateResult(
                gate_name="candidate_capability_replay",
                passed=False,
                reason="candidate replay capability was not frozen",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "stage": "capability_compile",
                    "code": event.code,
                    "failure_event": event.to_dict(),
                    "causal_failure_events": [event.to_dict()],
                },
            )
            return gates

        artifact_dir = (
            self.store.run_path(run_id)
            / "capability_preflight"
            / _safe_artifact_name(candidate.candidate_id)
        )
        try:
            await preflight_frozen_replay_capability(
                capability,
                artifact_dir=artifact_dir,
            )
        except Exception as exc:
            failure_details = _replay_service_start_failure_details(
                exc,
                replay_capability=capability,
            )
            candidate_owned = failure_details.get("outcome") == "candidate_failure"
            repairable = failure_details.get("repairable") is True
            diagnostic_details = dict(
                failure_details.get("diagnostics")
                if isinstance(failure_details.get("diagnostics"), Mapping)
                else {}
            )
            error_code = str(
                failure_details.get("code")
                or "candidate_capability_operational_preflight_failed"
            )
            owner = (
                FailureOwner.CANDIDATE
                if candidate_owned
                else FailureOwner.INFRASTRUCTURE
            )
            event = ReplayFailureEvent(
                code=error_code,
                owner=owner,
                stage=FailureStage.CAPABILITY_PREFLIGHT,
                scope=(
                    FailureScope.CANDIDATE
                    if candidate_owned
                    else FailureScope.SHARED_RUN
                ),
                repairable=repairable,
                category="candidate_capability_preflight",
                summary="candidate replay capability failed operational preflight",
                diagnostics={
                    "error_type": type(exc).__name__,
                    "reason": sanitize_text(str(exc), max_chars=512),
                    **diagnostic_details,
                },
                artifact_refs=(
                    sanitize_path_ref(
                        artifact_dir.relative_to(self.store.workspace_root).as_posix()
                        if artifact_dir.is_relative_to(self.store.workspace_root)
                        else artifact_dir.name
                    ),
                ),
                capability_id=capability.capability_id,
            )
            gates[replay_gate_index] = GateResult(
                gate_name="candidate_capability_replay",
                passed=False,
                reason="candidate replay capability failed operational preflight",
                details={
                    "capability_type": "replay",
                    "failure_class": (
                        "candidate" if candidate_owned else "infrastructure"
                    ),
                    "repairable": repairable,
                    "stage": "capability_preflight",
                    "code": error_code,
                    "error_type": type(exc).__name__,
                    "artifact_root": str(artifact_dir),
                    **diagnostic_details,
                    "failure_event": event.to_dict(),
                    "causal_failure_events": [event.to_dict()],
                },
            )
            return gates

        gates[replay_gate_index] = GateResult(
            gate_name="candidate_capability_replay",
            passed=True,
            reason=(
                "candidate package satisfies the replay capability contract and "
                "operational preflight"
            ),
            details={
                "capability_type": "replay",
                "failure_class": None,
                "repairable": False,
                "diagnostics": [],
                "operational_preflight": True,
                "capability_id": capability.capability_id,
                "frozen_capability_fingerprint": capability.fingerprint,
                "artifact_root": str(artifact_dir),
            },
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
        if discovery_error is not None:
            # Invalid candidate packages must retain distinct diagnostics.  A
            # successfully discovered capability, however, is keyed only by its
            # executable surface so behavior-only siblings can share adaptation.
            capability_cache_key = (
                f"candidate-discovery-error:{requested_package_fingerprint}"
            )
        elif capability is not None:
            capability_cache_key = f"replay-capability:{discovered_package_fingerprint}"
        elif capability_skill_root is not None:
            capability_cache_key = "candidate-without-replay-capability"
        else:
            capability_cache_key = "framework-only"
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
            preflight_cache_key = replay_dataset_fingerprint(replayable_dataset)
            preflight = self._replay_dataset_preflight_cache.get(preflight_cache_key)
            preflight_cache_hit = preflight is not None
            if preflight is None:
                preflight = self.replay_adaptation_compiler.preflight(
                    dataset=replayable_dataset,
                    workspace_root=self.store.workspace_root,
                )
                self._replay_dataset_preflight_cache[preflight_cache_key] = preflight
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
                            "failure_class": (
                                "candidate"
                                if capability_skill_root is not None
                                else "infrastructure"
                            ),
                            "failure_owner": (
                                FailureOwner.CANDIDATE.value
                                if capability_skill_root is not None
                                else FailureOwner.INFRASTRUCTURE.value
                            ),
                            "failure_scope": (
                                FailureScope.CANDIDATE.value
                                if capability_skill_root is not None
                                else FailureScope.SHARED_RUN.value
                            ),
                            "failure_source": FailureEventSource.NATIVE.value,
                            "repairable": capability_skill_root is not None,
                            "code": "candidate_replay_capability_missing",
                            "requirement_count": len(preflight.requirements),
                            "requirement_kinds": sorted(
                                {item.kind for item in preflight.requirements}
                            ),
                            "preflight_fingerprint": preflight.fingerprint,
                            "preflight_cache_hit": preflight_cache_hit,
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
                    snapshot_path = (
                        context_root / f"{_safe_artifact_name(case.case_id)}.json"
                    )
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
                        case.case_id: case.input for case in replayable_dataset.cases
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
            expected_environment_fingerprint = self._run_environment_fingerprints.get(
                run_id
            )
            if expected_environment_fingerprint is None:
                self._run_environment_fingerprints[run_id] = (
                    bundle.environment_fingerprint
                )
            else:
                environment_drift_gate = _environment_fingerprint_drift_gate(
                    expected_environment_fingerprint,
                    bundle.environment_fingerprint,
                )
            if (
                expected_environment_fingerprint is not None
                and environment_drift_gate is not None
            ):
                result = (
                    None,
                    environment_drift_gate,
                )
                self._replay_adaptation_cache[cache_key] = result
                return result
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
                **(
                    {
                        "failure_class": "candidate",
                        "failure_owner": FailureOwner.CANDIDATE.value,
                        "failure_scope": FailureScope.CANDIDATE.value,
                        "failure_source": FailureEventSource.NATIVE.value,
                        "repairable": True,
                    }
                    if capability_skill_root is not None and not base_gate.passed
                    else {}
                ),
                **_replay_adaptation_details(
                    bundle,
                    readiness=readiness,
                    artifact_root=artifact_root,
                ),
                "preflight_cache_hit": preflight_cache_hit,
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
        replay_adaptation: ReplayAdaptationBundle | None = None,
        timeout_seconds: float | None = None,
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
    ) -> dict[str, str | None]:
        bundle = replay_adaptation
        gate: GateResult | None = None
        if bundle is None:
            bundle, gate = self._prepare_replay_adaptation(
                run_id=run_id,
                dataset=dataset,
                emit_progress=False,
            )
        if bundle is None or (gate is not None and not gate.passed):
            return {
                "baseline_skill_fingerprint": None,
                "dataset_fingerprint": None,
                "adaptation_fingerprint": None,
                "workspace_seed_fingerprint": None,
                "support_fingerprint": None,
                "timeout_envelope_fingerprint": None,
            }
        if not isinstance(bundle, ReplayAdaptationBundle):
            return {
                "baseline_skill_fingerprint": None,
                "dataset_fingerprint": None,
                "adaptation_fingerprint": None,
                "workspace_seed_fingerprint": None,
                "support_fingerprint": None,
                "timeout_envelope_fingerprint": None,
            }
        return {
            "baseline_skill_fingerprint": target.fingerprint_current_content(),
            "dataset_fingerprint": replay_dataset_fingerprint(dataset),
            "adaptation_fingerprint": bundle.adaptation_fingerprint,
            "workspace_seed_fingerprint": bundle.workspace_seed_fingerprint,
            "support_fingerprint": replay_support_fingerprint(bundle),
            "timeout_envelope_fingerprint": (
                replay_timeout_envelope_fingerprint(
                    timeout_seconds=timeout_seconds,
                    max_steps=max_steps,
                    max_tool_calls=max_tool_calls,
                )
                if timeout_seconds is not None
                else None
            ),
        }

    def _compile_authoritative_measurement_plan(
        self,
        *,
        run_id: str,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        replay_adaptation: ReplayAdaptationBundle,
        replay_backend: CandidateReplayBackend,
        member_timeout_seconds: float,
        artifact_namespace: str | None = None,
        target_adapter: SelfEvolveTarget | None = None,
        experiment: ControlledExperimentSpec | None = None,
        measurement_stage: str = "authoritative",
    ) -> (
        tuple[
            MeasurementPlanV2,
            IsolationDecision,
            EvidencePolicyProfileV2,
        ]
        | None
    ):
        result = self._authoritative_measurement_controller.compile(
            AuthoritativeMeasurementRequest(
                run_id=run_id,
                dataset=dataset,
                candidate=candidate,
                replay_adaptation=replay_adaptation,
                replay_backend_identity=(
                    _measurement_component_identity(replay_backend)
                ),
                member_timeout_seconds=member_timeout_seconds,
                artifact_namespace=artifact_namespace,
                target_adapter_identity=(
                    _measurement_component_identity(target_adapter)
                    if target_adapter is not None
                    else None
                ),
                experiment=experiment,
                measurement_stage=measurement_stage,
            ),
            AuthoritativeMeasurementRuntime(
                experiments=self._measurement_experiments,
                load_resume_request=self._load_measurement_resume_request,
                progress_callback=self.progress_callback,
            ),
        )
        return result.execution_bundle

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
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
        lifecycle_callback: Callable[[str, Mapping[str, object]], None] | None = None,
        source_disposition: CandidateSourceDisposition = CandidateSourceDisposition(),
        artifact_namespace: str | None = None,
        replay_backend: CandidateReplayBackend | None = None,
        measurement_experiment: ControlledExperimentSpec | None = None,
        measurement_stage: str = "authoritative",
    ) -> tuple[
        CandidateReplayResult | None, SelfEvolveDataset | None, GateResult | None
    ]:
        result = await self._paired_replay_execution_controller.execute(
            PairedReplayExecutionRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=selected_candidate,
                apply_policy=apply_policy,
                baseline_replay_dir=baseline_replay_dir,
                baseline_repetitions=baseline_repetitions,
                candidate_repetitions=candidate_repetitions,
                progress_stage=progress_stage,
                timeout_seconds=timeout_seconds,
                max_steps=max_steps,
                max_tool_calls=max_tool_calls,
                lifecycle_callback=lifecycle_callback,
                source_disposition=source_disposition,
                artifact_namespace=artifact_namespace,
                replay_backend=replay_backend,
                measurement_experiment=measurement_experiment,
                measurement_stage=measurement_stage,
            ),
            PairedReplayExecutionRuntime(
                progress_callback=self.progress_callback,
                execution_telemetry=self.execution_telemetry,
                screening_case_observations=(
                    self._candidate_screening_case_observations
                ),
                screening_control_observations=(
                    self._candidate_screening_control_observations
                ),
                measurement_experiments=self._measurement_experiments,
                prepare_replay_adaptation=self._prepare_replay_adaptation,
                baseline_reuse_provenance=self._baseline_reuse_provenance,
                compile_measurement_plan=(self._compile_authoritative_measurement_plan),
                load_measurement_resume_request=(self._load_measurement_resume_request),
            ),
        )
        return result.as_tuple()

    async def _evaluate_independent_regression(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        selection_dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        apply_policy: str,
        budget_context: _RunBudgetContext | None,
    ) -> tuple[RegressionEvidence | None, ChallengeReport | None, GateResult]:
        challenge_report, challenge_gate = await self._prepare_challenge_suites(
            run_id=run_id,
            target=target,
            candidate=candidate,
            budget_context=budget_context,
        )
        if not challenge_gate.passed:
            return None, challenge_report, challenge_gate
        if not self.regression_suites or self.regression_backend is None:
            return None, challenge_report, challenge_gate

        challenge_suites = (
            challenge_report.suites if challenge_report is not None else ()
        )
        regression_suites = (*self.regression_suites, *challenge_suites)
        if challenge_suites:
            self.store.write_regression_suite_manifest(
                run_id,
                tuple(suite.spec for suite in regression_suites),
            )

        suite_results: list[RegressionSuiteResult] = []
        for suite in regression_suites:
            started_at = time.monotonic()
            execution_id = regression_execution_id(suite.spec.suite_id)
            _emit_progress(
                self.progress_callback,
                "regression",
                f"Running independent regression suite {suite.spec.suite_id}",
            )
            regression_dataset = suite.dataset
            suite_gates: list[GateResult] = []
            fresh_execution = False
            baseline_summary: EvaluationSummary | None = None
            candidate_summary: EvaluationSummary | None = None
            replay_budget: BudgetDecision | None = None
            replay_started = False
            try:
                if self.replay_enabled and candidate.target.target_type == "skill":
                    if isinstance(
                        self.regression_replay_backend,
                        CandidateReplayEvidenceReuseBackend,
                    ):
                        raise RuntimeError(
                            "stored selection replay evidence cannot approve an "
                            "independent regression suite"
                        )
                    if budget_context is not None:
                        replay_units = max(1, len(suite.dataset.cases)) * (
                            self.baseline_replay_repetitions
                            + self.candidate_replay_repetitions
                        )
                        replay_budget = budget_context.reserve(
                            BudgetStage.REGRESSION_REPLAY,
                            (
                                f"{candidate.candidate_id}-regression-"
                                f"{suite.spec.suite_id}"
                            ),
                            units=replay_units,
                        )
                        if not replay_budget.allowed:
                            suite_gates.append(
                                GateResult(
                                    gate_name="run_budget_regression_replay",
                                    passed=False,
                                    reason=(
                                        "independent regression replay was not run "
                                        "because budget was denied"
                                    ),
                                    details={
                                        "failure_class": "budget",
                                        "failure_owner": FailureOwner.FRAMEWORK.value,
                                        "repairable": False,
                                        "code": "regression_replay_budget_denied",
                                        "suite_id": suite.spec.suite_id,
                                        "budget_decision": replay_budget.to_dict(),
                                    },
                                )
                            )
                            raise RuntimeError("regression replay budget denied")
                    replay_telemetry_before = _stage_telemetry_usage_snapshot(
                        self.execution_telemetry,
                        "replay",
                    )

                    def regression_replay_lifecycle(
                        stage: str,
                        _payload: Mapping[str, object],
                    ) -> None:
                        nonlocal replay_started
                        if stage == "replay_started":
                            replay_started = True

                    (
                        _,
                        paired_dataset,
                        replay_gate,
                    ) = await self._replay_selected_candidate(
                        run_id=run_id,
                        target=target,
                        dataset=suite.dataset,
                        selected_candidate=candidate,
                        apply_policy=apply_policy,
                        baseline_replay_dir=None,
                        progress_stage="regression_replay",
                        artifact_namespace=(f"regression/{suite.spec.suite_id}"),
                        lifecycle_callback=regression_replay_lifecycle,
                        replay_backend=self.regression_replay_backend,
                    )
                    if replay_budget is not None:
                        if replay_started:
                            replay_telemetry_after = _stage_telemetry_usage_snapshot(
                                self.execution_telemetry,
                                "replay",
                            )
                            replay_usage = _stage_telemetry_usage_delta(
                                replay_telemetry_before,
                                replay_telemetry_after,
                            )
                            budget_context.debit(
                                replay_budget,
                                usage_observation=replay_usage.observation,
                                actual_source=replay_usage.source,
                            )
                        else:
                            budget_context.release(
                                replay_budget,
                                reason_code="regression_replay_not_started",
                            )
                        replay_budget = None
                    if replay_gate is not None:
                        suite_gates.append(replay_gate)
                    if paired_dataset is None or (
                        replay_gate is not None and not replay_gate.passed
                    ):
                        raise RuntimeError(
                            "regression paired replay did not produce comparable evidence"
                        )
                    regression_dataset = paired_dataset

                (
                    baseline_summary,
                    candidate_summary,
                ) = await evaluate_baseline_and_candidate(
                    self.regression_backend,
                    dataset=regression_dataset,
                    candidate=candidate,
                    dataset_split="regression",
                    artifact_namespace=(f"{run_id}-regression-{suite.spec.suite_id}"),
                    task_batch_executor=self.task_batch_executor,
                    max_concurrency=self.concurrency_policy.effective_limit(
                        "evaluation",
                        item_count=2,
                    ),
                    execution_telemetry=self.execution_telemetry,
                )
                fresh_execution = True
                suite_gates.extend(
                    [
                        EvaluationRuntimeHealthGate().evaluate(
                            (baseline_summary, candidate_summary)
                        ),
                        ScoreImprovementGate(min_delta=0.0).evaluate(
                            baseline=baseline_summary,
                            candidate=candidate_summary,
                        ),
                        CostLatencyRegressionGate(
                            max_cost_regression_ratio=0.25,
                            max_latency_regression_ratio=0.5,
                        ).evaluate(
                            baseline=baseline_summary,
                            candidate=candidate_summary,
                        ),
                    ]
                )
            except Exception as exc:
                if replay_budget is not None and replay_budget.allowed:
                    if replay_started:
                        replay_telemetry_after = _stage_telemetry_usage_snapshot(
                            self.execution_telemetry,
                            "replay",
                        )
                        replay_usage = _stage_telemetry_usage_delta(
                            replay_telemetry_before,
                            replay_telemetry_after,
                        )
                        budget_context.debit(
                            replay_budget,
                            usage_observation=replay_usage.observation,
                            actual_source=replay_usage.source,
                        )
                    else:
                        budget_context.release(
                            replay_budget,
                            reason_code="regression_replay_failed_before_start",
                        )
                    replay_budget = None
                if not any(
                    gate.gate_name == "run_budget_regression_replay"
                    for gate in suite_gates
                ):
                    suite_gates.append(
                        GateResult(
                            gate_name="independent_regression_execution",
                            passed=False,
                            reason="independent regression suite execution failed",
                            details={
                                "failure_class": "infrastructure",
                                "failure_owner": FailureOwner.FRAMEWORK.value,
                                "repairable": False,
                                "code": "independent_regression_execution_failed",
                                "suite_id": suite.spec.suite_id,
                                "type": type(exc).__name__,
                                "reason": sanitize_text(str(exc), max_chars=240),
                            },
                        )
                    )
            if baseline_summary is None:
                baseline_summary = EvaluationSummary(
                    variant_id="baseline",
                    dataset_split="regression",
                    metrics={"regression_execution_available": False},
                )
            if candidate_summary is None:
                candidate_summary = EvaluationSummary(
                    variant_id=candidate.candidate_id,
                    dataset_split="regression",
                    metrics={"regression_execution_available": False},
                )
            suite_results.append(
                RegressionSuiteResult(
                    spec=suite.spec,
                    baseline_summary=baseline_summary,
                    candidate_summary=candidate_summary,
                    gate_results=tuple(suite_gates),
                    execution_id=execution_id,
                    duration_ms=max(
                        0,
                        int((time.monotonic() - started_at) * 1000),
                    ),
                    fresh_execution=fresh_execution,
                )
            )

        evidence = RegressionEvidence(
            candidate_id=candidate.candidate_id,
            selection_dataset_fingerprint=replay_dataset_fingerprint(selection_dataset),
            selection_case_fingerprints=dataset_case_fingerprints(selection_dataset),
            selection_backend_id=evaluation_backend_identity(self.evaluation_backend),
            regression_backend_id=evaluation_backend_identity(self.regression_backend),
            suite_results=tuple(suite_results),
        )
        self.store.write_regression_evidence(run_id, evidence)
        return evidence, challenge_report, challenge_gate

    async def _prepare_challenge_suites(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
        budget_context: _RunBudgetContext | None,
    ) -> tuple[ChallengeReport | None, GateResult]:
        if not self.challenger_enabled:
            return None, GateResult(
                gate_name="challenger_admission",
                passed=True,
                reason="challenger plane is disabled by explicit configuration",
                details={
                    "enabled": False,
                    "approval_authority": False,
                    "admitted_count": 0,
                },
            )
        if not self.regression_suites:
            return None, GateResult(
                gate_name="challenger_admission",
                passed=False,
                reason="challenger requires independent regression source suites",
                details={
                    "enabled": True,
                    "approval_authority": False,
                    "failure_class": "infrastructure",
                    "failure_owner": FailureOwner.FRAMEWORK.value,
                    "failure_scope": FailureScope.SHARED_RUN.value,
                    "repairable": False,
                    "code": "challenger_source_suites_missing",
                },
            )
        if self.challenger_backend is None:
            return None, GateResult(
                gate_name="challenger_admission",
                passed=False,
                reason="challenger backend is unavailable",
                details={
                    "enabled": True,
                    "approval_authority": False,
                    "failure_class": "infrastructure",
                    "failure_owner": FailureOwner.FRAMEWORK.value,
                    "failure_scope": FailureScope.SHARED_RUN.value,
                    "repairable": False,
                    "code": "challenger_backend_missing",
                },
            )

        challenge_budget: BudgetDecision | None = None
        if budget_context is not None and not _backend_proves_zero_budget_usage(
            self.challenger_backend,
            BudgetStage.CHALLENGER,
        ):
            challenge_budget = budget_context.reserve(
                BudgetStage.CHALLENGER,
                f"{candidate.candidate_id}-challenger",
            )
            if not challenge_budget.allowed:
                diagnostic = {
                    "schema_version": "aworld.self_evolve.challenger_failure.v1",
                    "candidate_id": candidate.candidate_id,
                    "status": "failed",
                    "approval_authority": False,
                    "code": "challenger_budget_denied",
                    "budget_decision": challenge_budget.to_dict(),
                }
                self.store.write_challenge_report(
                    run_id,
                    candidate.candidate_id,
                    diagnostic,
                )
                return None, GateResult(
                    gate_name="challenger_admission",
                    passed=False,
                    reason="challenger proposal generation budget was denied",
                    details={
                        "enabled": True,
                        "approval_authority": False,
                        "failure_class": "budget",
                        "failure_owner": FailureOwner.FRAMEWORK.value,
                        "failure_scope": FailureScope.SHARED_RUN.value,
                        "repairable": False,
                        **diagnostic,
                    },
                )
        try:
            request = ChallengerRequest(
                candidate=candidate,
                current_content=target.load_current_content(),
                regression_suites=self.regression_suites,
                max_cases=self.challenger_max_cases,
            )
            batch = await self.challenger_backend.propose(request)
            if not isinstance(batch, ChallengeProposalBatch):
                raise TypeError("challenger backend must return ChallengeProposalBatch")
            report = admit_challenge_proposals(
                batch,
                candidate=candidate,
                current_content=request.current_content,
                regression_suites=self.regression_suites,
            )
            self.store.write_challenge_report(
                run_id,
                candidate.candidate_id,
                report,
            )
            rejected = [
                admission for admission in report.admissions if not admission.admitted
            ]
            return report, GateResult(
                gate_name="challenger_admission",
                passed=not rejected,
                reason=(
                    "challenger proposals were admitted as independent tests"
                    if report.admitted_count
                    else "challenger found no applicable independent probe"
                    if not rejected
                    else "challenger proposals failed deterministic admission"
                ),
                details={
                    "enabled": True,
                    "approval_authority": False,
                    "challenger_id": report.batch.challenger_id,
                    "batch_fingerprint": report.batch.fingerprint,
                    "proposal_count": len(report.admissions),
                    "admitted_count": report.admitted_count,
                    "rejected_count": len(rejected),
                    "rejection_codes": [item.reason_code for item in rejected],
                    **(
                        {}
                        if not rejected
                        else {
                            "failure_class": "infrastructure",
                            "failure_owner": FailureOwner.FRAMEWORK.value,
                            "failure_scope": FailureScope.SHARED_RUN.value,
                            "repairable": False,
                            "code": "challenger_admission_failed",
                        }
                    ),
                },
            )
        except Exception as exc:
            diagnostic = {
                "schema_version": "aworld.self_evolve.challenger_failure.v1",
                "candidate_id": candidate.candidate_id,
                "status": "failed",
                "approval_authority": False,
                "code": "challenger_generation_failed",
                "type": type(exc).__name__,
                "reason": sanitize_text(str(exc), max_chars=240),
            }
            self.store.write_challenge_report(
                run_id,
                candidate.candidate_id,
                diagnostic,
            )
            return None, GateResult(
                gate_name="challenger_admission",
                passed=False,
                reason="challenger proposal generation failed",
                details={
                    "enabled": True,
                    "approval_authority": False,
                    "failure_class": "infrastructure",
                    "failure_owner": FailureOwner.FRAMEWORK.value,
                    "failure_scope": FailureScope.SHARED_RUN.value,
                    "repairable": False,
                    **diagnostic,
                },
            )
        finally:
            if challenge_budget is not None and challenge_budget.allowed:
                budget_context.debit(
                    challenge_budget,
                    actual_source="reserved_fallback_challenger_generation",
                )

    async def _apply_auto_verified(
        self,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
        expected_package_fingerprint: str | None = None,
        addressed_lesson_ids: tuple[str, ...] = (),
        *,
        post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
        runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
        release_state: str = "verified",
        published: bool = True,
    ) -> dict[str, object]:
        effective_post_apply_evaluator = (
            post_apply_evaluator or self.post_apply_evaluator
        )
        effective_runtime_skill_activator = (
            runtime_skill_activator
            if runtime_skill_activator is not None
            else self.runtime_skill_activator
        )
        effective_runtime_registry_refresher = (
            runtime_registry_refresher
            if runtime_registry_refresher is not None
            else self.runtime_registry_refresher
        )
        if effective_post_apply_evaluator is None:
            raise ValueError("auto_verified apply policy requires post_apply_evaluator")
        evaluated_target_fingerprint = candidate.target_fingerprint
        try:
            apply_target_fingerprint = target.fingerprint_current_content()
        except Exception as exc:
            return {
                "status": "rejected",
                "metrics": {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "target_snapshot_unavailable",
                    "failure_class": "infrastructure",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": False,
                    "error_type": type(exc).__name__,
                },
                "dataset_split": "post_apply",
                "backup_path": None,
                "journal_path": None,
                "release_state": "rejected",
            }
        if (
            not evaluated_target_fingerprint
            or apply_target_fingerprint != evaluated_target_fingerprint
        ):
            return {
                "status": "rejected",
                "metrics": {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "target_snapshot_stale",
                    "failure_class": "infrastructure",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": False,
                    "evaluated_target_fingerprint": (evaluated_target_fingerprint),
                    "apply_target_fingerprint": (apply_target_fingerprint),
                },
                "dataset_split": "post_apply",
                "backup_path": None,
                "journal_path": None,
                "release_state": "rejected",
            }
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
            normalized_content, normalization_metrics = (
                normalize_verified_skill_release(
                    candidate.content,
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    original_content=original_content,
                    structural_edit_intent=(candidate.structural_edit_intent),
                    require_exact_deletion_intent=True,
                )
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
            latest_target_fingerprint = target.fingerprint_current_content()
        except Exception as exc:
            latest_target_fingerprint = None
            fingerprint_error_type = type(exc).__name__
        else:
            fingerprint_error_type = None
        if latest_target_fingerprint != evaluated_target_fingerprint:
            drift_metrics = {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "target_snapshot_stale",
                "failure_class": "infrastructure",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "repairable": False,
                "evaluated_target_fingerprint": (evaluated_target_fingerprint),
                "apply_target_fingerprint": (latest_target_fingerprint),
                "fingerprint_error_type": fingerprint_error_type,
                **dict(normalization_metrics),
            }
            self.store.update_apply_journal(
                journal_path,
                status="rejected",
                details=drift_metrics,
            )
            return {
                "status": "rejected",
                "metrics": drift_metrics,
                "dataset_split": "post_apply",
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
                "release_state": "rejected",
            }
        try:
            if applied_candidate.target.target_type == "skill" and hasattr(
                target, "apply_candidate_variant"
            ):
                target.apply_candidate_variant(
                    applied_candidate,
                    expected_package_fingerprint=expected_package_fingerprint,
                    verified_content=candidate.content,
                    expected_target_fingerprint=(evaluated_target_fingerprint),
                )
            else:
                if target.fingerprint_current_content() != evaluated_target_fingerprint:
                    raise TargetSnapshotStaleError(
                        "target snapshot changed before candidate mutation"
                    )
                target.apply_candidate(applied_candidate.content)
        except TargetSnapshotStaleError:
            drift_metrics = {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "target_snapshot_stale",
                "failure_class": "infrastructure",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "repairable": False,
                "evaluated_target_fingerprint": (evaluated_target_fingerprint),
            }
            self.store.update_apply_journal(
                journal_path,
                status="rejected",
                details=drift_metrics,
            )
            return {
                "status": "rejected",
                "metrics": drift_metrics,
                "dataset_split": "post_apply",
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
                "release_state": "rejected",
            }
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
            summary = effective_post_apply_evaluator(applied_candidate)
            if inspect.isawaitable(summary):
                summary = await summary
            if not isinstance(summary, EvaluationSummary):
                raise ValueError("post_apply_evaluator must return EvaluationSummary")
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
            if effective_runtime_skill_activator is not None:
                try:
                    activation_result = effective_runtime_skill_activator(
                        applied_candidate
                    )
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
            if effective_runtime_registry_refresher is not None:
                try:
                    refresh_result = effective_runtime_registry_refresher(
                        applied_candidate
                    )
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
                        "release_state": release_state,
                        "published": published,
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
                "release_state": release_state,
                "published": published,
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

    async def _apply_verified_only(
        self,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
        expected_package_fingerprint: str | None = None,
        addressed_lesson_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        """Apply a verified skill package to a run-owned shadow registry only."""
        if target.identity.target_type != "skill":
            return {
                "status": "rejected",
                "metrics": {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "verified_only_target_type_unsupported",
                    "failure_class": "candidate",
                    "target_type": target.identity.target_type,
                },
                "dataset_split": "post_apply",
                "backup_path": None,
                "journal_path": None,
                "release_state": "rejected",
                "published": False,
            }

        try:
            source_fingerprint_before = target.fingerprint_current_content()
        except Exception as exc:
            return {
                "status": "rejected",
                "metrics": {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "target_snapshot_unavailable",
                    "failure_class": "infrastructure",
                    "error_type": type(exc).__name__,
                },
                "dataset_split": "post_apply",
                "backup_path": None,
                "journal_path": None,
                "release_state": "rejected",
                "published": False,
            }

        if _SAFE_VERIFIED_TARGET_ID.fullmatch(target.identity.target_id) is None:
            return {
                "status": "rejected",
                "metrics": {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "verified_target_id_unsafe",
                    "failure_class": "candidate",
                },
                "dataset_split": "post_apply",
                "backup_path": None,
                "journal_path": None,
                "release_state": "rejected",
                "published": False,
            }

        isolated_registry_root = self.store.run_path(run_id) / "verified_targets"
        isolated_package_root = isolated_registry_root / target.identity.target_id
        isolated_skill_path = isolated_package_root / "SKILL.md"
        if isolated_package_root.exists() or isolated_package_root.is_symlink():
            return {
                "status": "rejected",
                "metrics": {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "verified_target_collision",
                    "failure_class": "infrastructure",
                },
                "dataset_split": "post_apply",
                "backup_path": None,
                "journal_path": None,
                "release_state": "rejected",
                "published": False,
                "verified_target_path": str(isolated_skill_path),
            }

        source_skill_path = (
            Path(target.identity.path).resolve() if target.identity.path else None
        )
        try:
            isolated_registry_root.mkdir(parents=True, exist_ok=True)
            if source_skill_path is not None and source_skill_path.is_file():
                shutil.copytree(
                    source_skill_path.parent,
                    isolated_package_root,
                    symlinks=True,
                )
            else:
                isolated_package_root.mkdir(parents=True, exist_ok=False)
                isolated_skill_path.write_text(
                    target.load_current_content(),
                    encoding="utf-8",
                )
        except Exception as exc:
            return {
                "status": "rejected",
                "metrics": {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "verified_target_materialization_failed",
                    "failure_class": "infrastructure",
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                },
                "dataset_split": "post_apply",
                "backup_path": None,
                "journal_path": None,
                "release_state": "rejected",
                "published": False,
                "verified_target_path": str(isolated_skill_path),
            }

        isolated_target = SkillTextTarget(
            isolated_skill_path,
            target_id=target.identity.target_id,
            allow_auto_apply=True,
        )
        isolated_candidate = replace(
            candidate,
            target=isolated_target.identity,
            target_fingerprint=isolated_target.fingerprint_current_content(),
        )
        result = await self._apply_auto_verified(
            run_id,
            isolated_target,
            isolated_candidate,
            expected_package_fingerprint=expected_package_fingerprint,
            addressed_lesson_ids=addressed_lesson_ids,
            post_apply_evaluator=_default_post_apply_evaluator(isolated_target),
            runtime_skill_activator=lambda _candidate: {
                "status": "skipped",
                "reason": "verified_only does not mutate runtime skill state",
            },
            runtime_registry_refresher=_default_new_skill_registry_refresher(
                isolated_target
            ),
            release_state="verified_only",
            published=False,
        )
        try:
            source_fingerprint_after = target.fingerprint_current_content()
        except Exception:
            source_fingerprint_after = None
        source_unchanged = source_fingerprint_after == source_fingerprint_before
        result.update(
            {
                "published": False,
                "verified_target_path": str(isolated_skill_path),
                "source_target_path": target.identity.path,
                "source_target_fingerprint_before": source_fingerprint_before,
                "source_target_fingerprint_after": source_fingerprint_after,
                "source_target_unchanged": source_unchanged,
            }
        )
        if not source_unchanged and result.get("status") == "accepted":
            result["status"] = "rejected"
            result["release_state"] = "rejected"
            metrics = dict(result.get("metrics") or {})
            metrics.update(
                {
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    "code": "source_target_changed_during_verified_only",
                    "failure_class": "infrastructure",
                }
            )
            result["metrics"] = metrics
        return result


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


def _load_or_build_campaign_dataset(
    *,
    store: FilesystemSelfEvolveStore,
    campaign_id: str | None,
    campaign_cycle: int | None,
    source_config: SelfEvolveEvalSourceConfig,
    current_trajectory: Iterable[Mapping[str, Any]] | None,
    task_id: str | None,
    progress_callback: Callable[[str, str], Any] | None,
) -> tuple[SelfEvolveDataset, Path | None, bool]:
    """Keep Runner's dataset-builder patch seam at the CLI boundary."""

    return _cli_load_or_build_campaign_dataset(
        store=store,
        campaign_id=campaign_id,
        campaign_cycle=campaign_cycle,
        source_config=source_config,
        current_trajectory=current_trajectory,
        task_id=task_id,
        progress_callback=progress_callback,
        dataset_builder=build_dataset_from_source,
    )


def _empty_run_budget_report(
    *,
    max_run_tokens: int | None,
    total_run_token_budget: int | None,
    max_run_cost_usd: float | Decimal | None,
    max_run_wall_seconds: float | Decimal | None,
) -> dict[str, object]:
    """Keep the legacy zero-usage budget-report entry point."""

    return _cli_empty_run_budget_report(
        max_run_tokens=max_run_tokens,
        total_run_token_budget=total_run_token_budget,
        max_run_cost_usd=max_run_cost_usd,
        max_run_wall_seconds=max_run_wall_seconds,
        run_budget_context_type=_RunBudgetContext,
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
    inferred_new_skill_policy: InferredNewSkillPolicy
    | str = InferredNewSkillPolicy.AUTO_VERIFIED,
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
) -> Mapping[str, Any]:
    request = locals()
    return execute_cli_optimization(
        **request,
        runtime=CliOrchestrationRuntime(
            runner_type=SelfEvolveRunner,
            run_budget_context_type=_RunBudgetContext,
            load_or_build_campaign_dataset=_load_or_build_campaign_dataset,
            default_cli_skill_candidate=_default_cli_skill_candidate,
            auto_group_trajectory_log_dataset=_auto_group_trajectory_log_dataset,
            infer_target_from_trace_packs=_infer_target_from_trace_packs,
            target_from_ref=_target_from_ref,
            replay_backend_type=AWorldCliCandidateReplayBackend,
        ),
    )


async def _run_candidate_generation_agent(
    agent: CandidateGenerationAgent,
    prompt: str,
) -> str:
    """Run one request through the optimize-scoped AWorld candidate agent."""

    return await agent.generate(prompt)


def _replayable_user_task_dataset(dataset: SelfEvolveDataset) -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=tuple(
            case for case in dataset.cases if _is_replayable_user_task_case(case)
        ),
        recipe=dataset.recipe,
    )


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


def _target_package_sources(
    target: SelfEvolveTarget,
    *,
    inventory: Sequence[str],
    max_file_chars: int = 128_000,
    max_total_chars: int = 512_000,
) -> dict[str, Mapping[str, object]]:
    """Load a bounded source inventory for later focused-repair closure.

    The mapping remains private until a conformance contract names a required
    branch path. Binary, oversized, symlinked, and out-of-package files are
    excluded so focused repair cannot broaden its mutation surface implicitly.
    """

    target_path = _target_runtime_skill_path(target)
    if target_path is None or not target_path.exists():
        return {}
    root = target_path.parent.resolve()
    remaining_chars = max_total_chars
    sources: dict[str, Mapping[str, object]] = {}
    for relative_path in inventory:
        if remaining_chars <= 0:
            break
        candidate = root.joinpath(*Path(relative_path).parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if (
            not resolved.is_relative_to(root)
            or candidate.is_symlink()
            or not resolved.is_file()
        ):
            continue
        try:
            if resolved.stat().st_size > max_file_chars * 4:
                continue
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(content) > max_file_chars or len(content) > remaining_chars:
            continue
        sources[relative_path] = {
            "content": content,
            "executable": bool(resolved.stat().st_mode & 0o111),
        }
        remaining_chars -= len(content)
    return sources


def _safe_artifact_name(value: str) -> str:
    readable = (
        "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in value
        ).strip("-")[:48]
        or "case"
    )
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


def _retention_controller(
    store: FilesystemSelfEvolveStore,
) -> ArtifactRetentionController:
    """Build retention with Runner's historical cleanup patch seam."""

    return ArtifactRetentionController(
        store=store,
        cleanup=cleanup_self_evolve_artifacts,
    )


def _artifact_retention_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    previous: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return _retention_controller(store).build_report(
        run_id,
        previous=previous,
    )


def _finalize_run_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    report: dict[str, Any],
    completed_run: SelfEvolveRun,
    previous_artifact_retention: Mapping[str, object] | None = None,
) -> Path:
    return _retention_controller(store).finalize_run_report(
        run_id,
        report=report,
        completed_run=completed_run,
        previous_artifact_retention=previous_artifact_retention,
    )


def _replay_report(replay_result: CandidateReplayResult) -> dict[str, object]:
    def lifecycle(variant: ReplayVariantResult) -> dict[str, object]:
        return {
            "variant_id": variant.variant_id,
            "status": variant.status,
            "metrics": public_diagnostic_projection(dict(variant.metrics)),
            "stdout_path": variant.stdout_path,
            "stderr_path": variant.stderr_path,
            # Retained for readers of v1 reports.
            "failure": public_diagnostic_projection(
                variant.failure.compatibility_dict()
                if isinstance(variant.failure, ReplayFailureEvent)
                else variant.failure
            ),
            "failure_event": public_diagnostic_projection(
                variant.failure.to_dict()
                if isinstance(variant.failure, ReplayFailureEvent)
                else None
            ),
            "blocked_by": public_diagnostic_projection(
                [event.to_dict() for event in variant.blocked_by]
            ),
        }

    report: dict[str, object] = {
        "request": {
            "run_id": replay_result.request.run_id,
            "task_id": replay_result.request.task_id,
            "candidate_id": replay_result.request.candidate_id,
            "overlay_skill_root": replay_result.request.overlay_skill_root,
            "baseline_replay_dir": replay_result.request.baseline_replay_dir,
            "resume_replay_dir": replay_result.request.resume_replay_dir,
            "timeout_seconds": replay_result.request.timeout_seconds,
            "max_steps": replay_result.request.max_steps,
            "max_tokens": replay_result.request.max_tokens,
            "dataset_fingerprint": replay_result.request.dataset_fingerprint,
            "baseline_skill_fingerprint": (
                replay_result.request.baseline_skill_fingerprint
            ),
            "adaptation_fingerprint": (replay_result.request.adaptation_fingerprint),
            "support_fingerprint": replay_result.request.support_fingerprint,
            "timeout_envelope_fingerprint": (
                replay_result.request.timeout_envelope_fingerprint
            ),
            "workspace_seed_fingerprint": (
                replay_result.request.workspace_seed_fingerprint
            ),
        },
        "overlay_skill_root": replay_result.request.overlay_skill_root,
        "baseline": lifecycle(replay_result.baseline),
        "candidate": lifecycle(replay_result.candidate),
    }
    if replay_result.request.measurement_plan is not None:
        assert replay_result.request.measurement_isolation_decision is not None
        feasibility = estimate_measurement_feasibility(
            replay_result.request.measurement_plan
        )
        report["measurement_control"] = {
            "measurement_plan_fingerprint": (
                replay_result.request.measurement_plan.measurement_plan_fingerprint
            ),
            "isolation_decision_fingerprint": (
                replay_result.request.measurement_plan.isolation_decision_fingerprint
            ),
            "evidence_policy_fingerprint": (
                replay_result.request.measurement_plan.evidence_policy_fingerprint
            ),
            "decision": public_diagnostic_projection(
                dict(replay_result.measurement_decision or {})
            ),
            "preflight": public_diagnostic_projection(
                measurement_preflight_projection(
                    plan=replay_result.request.measurement_plan,
                    feasibility=feasibility,
                    isolation_decision=(
                        replay_result.request.measurement_isolation_decision
                    ),
                )
            ),
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
                "baseline_metrics": public_diagnostic_projection(
                    dict(member.baseline.metrics)
                ),
                "candidate_metrics": public_diagnostic_projection(
                    dict(member.candidate.metrics)
                ),
                "baseline_failure": lifecycle(member.baseline)["failure"],
                "candidate_failure": lifecycle(member.candidate)["failure"],
                "baseline_lifecycle": lifecycle(member.baseline),
                "candidate_lifecycle": lifecycle(member.candidate),
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
        "capability_package_fingerprint": (capability.capability_package_fingerprint),
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


def _reusable_baseline_case_count(
    *,
    dataset: SelfEvolveDataset,
    baseline_replay_dir: str | None,
    baseline_repetitions: int,
) -> int:
    """Count only validated cached controls when reserving replay work."""

    if baseline_replay_dir is None:
        return 0
    reusable = 0
    for case in dataset.cases:
        if not _is_replayable_user_task_case(case):
            continue
        try:
            stored_dir = _member_baseline_replay_dir(
                baseline_replay_dir,
                case.case_id,
            )
            if stored_dir is None:
                continue
            baseline = _load_variant_result_from_dir(
                Path(stored_dir),
                base_variant_id="baseline",
            )
        except (
            FileNotFoundError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            OSError,
        ):
            continue
        if _baseline_replay_is_reusable(
            baseline,
            requested_repetitions=baseline_repetitions,
        ):
            reusable += 1
    return reusable


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
    for member_dir in sorted(
        members_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True
    ):
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
        if _baseline_replay_is_reusable(
            baseline,
            requested_repetitions=baseline_repetitions,
        ):
            reusable_by_case[task_id] = member_dir / "baseline"
    if len(case_ids) == 1:
        baseline_dir = reusable_by_case.get(case_ids[0])
        return str(baseline_dir) if baseline_dir is not None else None
    if all(case_id in reusable_by_case for case_id in case_ids):
        return str(members_root)
    return None


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


def _load_prior_rejected_feedback(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    limit: int = 12,
    allowed_run_ids: Iterable[str] | None = None,
) -> tuple[EvaluationSummary, ...]:
    root = store.artifact_root
    if not root.exists():
        return ()
    feedback: list[EvaluationSummary] = []
    report_paths = _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    )
    for report_path in report_paths:
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(
            report,
            target,
            require_path=allowed_run_ids is None,
        ):
            continue
        for item in _feedback_from_report(report, report_path=report_path):
            feedback.append(item)
            if len(feedback) >= limit:
                return tuple(feedback)
    return tuple(feedback)


def _load_prior_scheduler_state(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    allowed_run_ids: Iterable[str] | None,
) -> SchedulerState:
    """Restore the latest Campaign scheduler checkpoint without heuristics."""

    if not allowed_run_ids:
        return SchedulerState()
    for report_path in _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    ):
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(report, target, require_path=False):
            continue
        repair_state = report.get("repair_frontier_state")
        raw_state = (
            repair_state.get("scheduler_state")
            if isinstance(repair_state, Mapping)
            else None
        )
        if not isinstance(raw_state, Mapping):
            population = report.get("population")
            decisions = (
                population.get("scheduler_decisions")
                if isinstance(population, Mapping)
                else None
            )
            if isinstance(decisions, list) and decisions:
                latest = decisions[-1]
                raw_state = latest.get("state") if isinstance(latest, Mapping) else None
        if isinstance(raw_state, Mapping):
            try:
                return SchedulerState.from_dict(raw_state)
            except (TypeError, ValueError):
                continue
    return SchedulerState()


def _repair_frontier_state_report(
    *,
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    current_run_id: str,
    allowed_run_ids: Iterable[str] | None,
    observed_frontiers: tuple[RepairFrontier, ...],
    scheduler_state: SchedulerState,
    selected_candidate_id: str | None,
    run_succeeded: bool,
    campaign_id: str | None,
    campaign_cycle: int | None,
) -> dict[str, object]:
    previous_records: dict[str, Mapping[str, object]] = {}
    if allowed_run_ids:
        for report_path in _prior_report_paths(
            store,
            current_run_id=current_run_id,
            allowed_run_ids=allowed_run_ids,
        ):
            try:
                report = _load_json_mapping(report_path)
            except Exception:
                continue
            if not _report_matches_target(report, target, require_path=False):
                continue
            previous_state = report.get("repair_frontier_state")
            records = (
                previous_state.get("records")
                if isinstance(previous_state, Mapping)
                else None
            )
            if isinstance(records, list):
                previous_records = {
                    str(item["semantic_key"]): item
                    for item in records
                    if isinstance(item, Mapping)
                    and isinstance(item.get("semantic_key"), str)
                }
                break

    observed = {item.semantic_key: item for item in observed_frontiers}
    records: list[dict[str, object]] = []
    for semantic_key in sorted({*previous_records, *observed}):
        previous = previous_records.get(semantic_key, {})
        frontier = observed.get(semantic_key)
        previous_status = str(previous.get("status") or "active")
        if previous_status not in {"active", "dormant", "resolved", "regressed"}:
            previous_status = "active"
        previous_progress = _non_negative_int(previous.get("current_progress"))
        previous_best = max(
            previous_progress,
            _non_negative_int(previous.get("best_progress")),
        )
        if frontier is None:
            if run_succeeded:
                status = "resolved"
            elif previous_status in {"active", "regressed"}:
                status = "dormant"
            else:
                status = previous_status
            current_progress = previous_progress
            best_progress = previous_best
            owner = str(previous.get("owner") or "candidate")
            scope = str(previous.get("scope") or "candidate")
            repairable = previous.get("repairable") is True
        else:
            current_progress = frontier.progress
            best_progress = max(previous_best, current_progress)
            status = (
                "regressed"
                if previous_status == "resolved"
                or (previous_best > 0 and current_progress < previous_best)
                else "active"
            )
            owner = frontier.owner.value
            scope = frontier.scope.value
            repairable = frontier.repairable
        champion_candidate_id = previous.get("champion_candidate_id")
        if (
            frontier is not None
            and selected_candidate_id is not None
            and current_progress >= previous_best
        ):
            champion_candidate_id = selected_candidate_id
        previous_mutation_families = previous.get("mutation_families")
        if not isinstance(previous_mutation_families, (list, tuple)):
            previous_mutation_families = ()
        records.append(
            {
                "semantic_key": semantic_key,
                "status": status,
                "owner": owner,
                "scope": scope,
                "repairable": repairable,
                "current_progress": current_progress,
                "best_progress": best_progress,
                "first_seen_run_id": str(
                    previous.get("first_seen_run_id") or current_run_id
                ),
                "last_seen_run_id": (
                    current_run_id
                    if frontier is not None
                    else previous.get("last_seen_run_id")
                ),
                "champion_candidate_id": champion_candidate_id,
                "mutation_families": list(
                    scheduler_state.frontier_mutation_families.get(
                        semantic_key,
                        tuple(
                            str(item)
                            for item in previous_mutation_families
                            if isinstance(item, str) and item
                        ),
                    )
                ),
                "regression_count": _non_negative_int(previous.get("regression_count"))
                + (
                    1 if status == "regressed" and previous_status != "regressed" else 0
                ),
            }
        )
    return {
        "schema_version": "aworld.self_evolve.repair_frontier_state.v1",
        "campaign_id": campaign_id,
        "campaign_cycle": campaign_cycle,
        "run_id": current_run_id,
        "records": records,
        "active_count": sum(item["status"] == "active" for item in records),
        "dormant_count": sum(item["status"] == "dormant" for item in records),
        "resolved_count": sum(item["status"] == "resolved" for item in records),
        "regressed_count": sum(item["status"] == "regressed" for item in records),
        "scheduler_state": scheduler_state.to_dict(),
    }


def _load_prior_candidate_package_index(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    candidate_ids: set[str],
    allowed_run_ids: Iterable[str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Index canonical prior packages without mutating any prior artifact."""

    package_to_candidate: dict[str, str] = {}
    package_by_candidate: dict[str, str] = {}
    if not store.artifact_root.exists() or not candidate_ids:
        return package_to_candidate, package_by_candidate
    for report_path in _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    ):
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(
            report,
            target,
            require_path=allowed_run_ids is None,
        ):
            continue
        candidate_root = report_path.parent / "candidates"
        for candidate_id in sorted(candidate_ids):
            candidate_path = candidate_root / f"{candidate_id}.json"
            if not candidate_path.is_file() or candidate_path.is_symlink():
                continue
            try:
                candidate = _load_candidate_variant(candidate_path)
            except Exception:
                continue
            if (
                candidate.target.target_type != target.target_type
                or candidate.target.target_id != target.target_id
                or (
                    allowed_run_ids is None
                    and candidate.target.path is not None
                    and target.path is not None
                    and str(candidate.target.path) != str(target.path)
                )
            ):
                continue
            fingerprint = candidate_package_fingerprint(candidate)
            package_to_candidate.setdefault(fingerprint, candidate_id)
            package_by_candidate.setdefault(candidate_id, fingerprint)
    return package_to_candidate, package_by_candidate


def _load_prior_rejected_semantic_lesson_fingerprints(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    limit: int = 64,
    allowed_run_ids: Iterable[str] | None = None,
) -> set[_SemanticLessonFingerprint]:
    root = store.artifact_root
    if not root.exists():
        return set()
    fingerprints: set[_SemanticLessonFingerprint] = set()
    report_paths = _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    )
    for report_path in report_paths:
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(
            report,
            target,
            require_path=allowed_run_ids is None,
        ):
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
            identity_version = lineage.get("semantic_identity_version")
            semantic_package = lineage.get("semantic_package_fingerprint")
            lesson_set = lineage.get("lesson_set_fingerprint")
            verification_contract = lineage.get("verification_contract_fingerprint")
            # Legacy two-field lineage remains importable for audit and lesson
            # extraction, but it cannot prove that candidate-owned files or the
            # active verifier contract are equivalent and therefore cannot hard
            # filter a new candidate.
            if (
                identity_version == _SEMANTIC_DEDUP_IDENTITY_VERSION
                and isinstance(semantic_package, str)
                and semantic_package
                and isinstance(lesson_set, str)
                and lesson_set
                and isinstance(verification_contract, str)
                and verification_contract
            ):
                fingerprints.add(
                    _SemanticLessonFingerprint(
                        semantic_package_fingerprint=semantic_package,
                        lesson_set_fingerprint=lesson_set,
                        verification_contract_fingerprint=(verification_contract),
                    )
                )
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
    attempt_events: tuple[CandidateAttemptEvent, ...] = (),
    selected_candidate_id: str | None,
    post_apply: Mapping[str, object] | None,
) -> None:
    states_by_candidate: dict[str, dict[str, object]] = {}
    for state in iteration_states:
        candidate = state.get("candidate")
        candidate_id = getattr(candidate, "candidate_id", None)
        if isinstance(candidate_id, str) and candidate_id:
            states_by_candidate[candidate_id] = state
    events_by_candidate: dict[str, list[CandidateAttemptEvent]] = {}
    for event in attempt_events:
        events_by_candidate.setdefault(event.candidate_id, []).append(event)

    for candidate_id, raw_path in lineage_paths_by_candidate.items():
        path = Path(raw_path)
        try:
            payload = dict(_load_json_mapping(path))
        except Exception:
            continue
        state = states_by_candidate.get(candidate_id)
        candidate_events = sorted(
            events_by_candidate.get(candidate_id, ()),
            key=lambda event: (event.key.iteration, event.key.slot, event.sequence),
        )
        payload["screened"] = any(
            event.stage is CandidateAttemptStage.SCREENING for event in candidate_events
        )
        if state is None:
            terminal_event = next(
                (
                    event
                    for event in reversed(candidate_events)
                    if event.stage in TERMINAL_ATTEMPT_STAGES
                ),
                None,
            )
            payload["lifecycle_status"] = (
                terminal_event.stage.value
                if terminal_event is not None
                else "generated"
            )
            payload["replayed"] = any(
                event.stage
                in {
                    CandidateAttemptStage.REPLAY_EVIDENCE_REUSED,
                    CandidateAttemptStage.PAIRED_REPLAY_STARTED,
                    CandidateAttemptStage.PAIRED_REPLAY_COMPLETED,
                    CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE,
                }
                for event in candidate_events
            )
            if terminal_event is not None and terminal_event.reason_code:
                payload["lifecycle_reason_code"] = terminal_event.reason_code
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
                payload["lifecycle_status"] = (
                    "verified"
                    if post_apply.get("release_state") == "verified_only"
                    else "accepted"
                )
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


def _report_matches_screening_harness(
    report: Mapping[str, Any],
    expected_fingerprint: str | None,
) -> bool:
    """Reject stale control evidence produced by another harness identity."""

    if expected_fingerprint is None:
        return True
    preflight = report.get("screening_control_preflight")
    return bool(
        isinstance(preflight, Mapping)
        and preflight.get("harness_fingerprint") == expected_fingerprint
    )


def _prior_report_paths(
    store: FilesystemSelfEvolveStore,
    *,
    current_run_id: str,
    allowed_run_ids: Iterable[str] | None,
) -> list[Path]:
    if allowed_run_ids is None:
        return sorted(
            store.artifact_root.glob("*/report.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    paths: list[Path] = []
    for run_id in reversed(tuple(dict.fromkeys(str(item) for item in allowed_run_ids))):
        if run_id == current_run_id:
            continue
        try:
            path = store.run_path(run_id) / "report.json"
        except ValueError:
            continue
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return paths


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
    if _report_has_shared_measurement_failure(report):
        # A broken control plane is not general candidate training data.  Keep
        # only independently attributed candidate-owned conformance/screening
        # feedback gathered before the shared stop; omit lessons and raw
        # iteration summaries whose candidate effect was never observed.
        return tuple(items)
    items.extend(_lesson_feedback_from_report(report, report_path=report_path))
    iterations = report.get("iterations")
    if isinstance(iterations, list):
        for iteration in iterations:
            if not isinstance(iteration, Mapping):
                continue
            if iteration.get("status") not in {
                "rejected",
                "accepted",
                "prerequisite",
            }:
                continue
            candidate_id = iteration.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                continue
            metrics = _historical_feedback_metrics(iteration)
            metrics["candidate_status"] = str(iteration.get("status"))
            post_apply = (
                report.get("post_apply")
                if isinstance(report.get("post_apply"), Mapping)
                else {}
            )
            legacy_accepted_report = (
                report.get("apply_policy") is None and not post_apply
            )
            metrics["publication_completed"] = legacy_accepted_report or (
                report.get("apply_policy") == "auto_verified"
                and post_apply.get("status") == "accepted"
                and post_apply.get("release_state") == "verified"
                and post_apply.get("published") is not False
            )
            metrics["historical_apply_policy"] = report.get("apply_policy")
            metrics["historical_release_state"] = post_apply.get("release_state")
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


def _gate_results_have_candidate_prerequisite_failure(
    gates: Iterable[GateResult],
) -> bool:
    return any(_gate_has_candidate_prerequisite_failure(gate) for gate in gates)


def _repair_feedback_from_selected_candidate(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    candidate_id = report.get("repair_focus_candidate_id") or report.get(
        "selected_candidate_id"
    )
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
        "replay_evaluator_admission",
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
    candidate_status = (
        "prerequisite"
        if any(
            isinstance(gate.details, Mapping)
            and gate.details.get("candidate_status") == "prerequisite"
            for gate in gates
        )
        else "repairable"
    )
    metrics = _typed_gate_feedback_metrics(gates)
    metrics.update(judge_metrics)
    metrics.update(
        {
            "failed_gates": [gate.gate_name for gate in gates],
            "candidate_status": candidate_status,
            "authoritative_replay_failure": candidate_status != "prerequisite",
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
            metrics["failed_gates"] = [str(gate) for gate in failed_gates if str(gate)]
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
                    "path": sanitize_path_ref(path.relative_to(run_root).as_posix()),
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
    conformance_iterations = population.get("conformance_iterations")
    if isinstance(conformance_iterations, list):
        screenings.extend(
            item for item in conformance_iterations if isinstance(item, Mapping)
        )
    conformance = population.get("conformance")
    if isinstance(conformance, Mapping):
        screenings.append(conformance)
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
        gate_name = (
            "candidate_repair_conformance"
            if attempt.get("stage") == "conformance"
            else "candidate_replay"
        )
        gate = GateResult(
            gate_name=gate_name,
            passed=False,
            reason=sanitize_text(attempt.get("reason"), max_chars=320),
            details=details,
        )
        metrics = _typed_gate_feedback_metrics([gate])
        metrics.update(
            {
                "failed_gates": [gate_name],
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
    if not isinstance(raw_files, list):
        return None

    raw_content = payload.get("content")
    has_target_content = isinstance(raw_content, str) and bool(raw_content.strip())
    bounded_target_content = (
        _bounded_repair_candidate_target_content(
            raw_content,
            has_files=bool(raw_files),
        )
        if has_target_content
        else None
    )
    remaining_chars = _MAX_REPAIR_CANDIDATE_PACKAGE_CHARS
    if bounded_target_content is not None:
        remaining_chars -= len(bounded_target_content)
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
                preserve_format=True,
            )
            file_payload["content"] = sanitized_content
            remaining_chars -= len(sanitized_content)
        files.append(file_payload)
    if raw_files and not files:
        return None
    if not files and not has_target_content:
        return None
    package = {
        "candidate_id": sanitize_text(candidate_id, max_chars=160),
        "rationale": sanitize_text(payload.get("rationale"), max_chars=1_000),
        "files": files,
    }
    if bounded_target_content is not None:
        package["content"] = bounded_target_content
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
        metrics: dict[str, Any] = (
            dict(lesson_metrics) if isinstance(lesson_metrics, Mapping) else {}
        )
        metrics.update(
            {
                "lesson_id": lesson_id,
                "lesson_type": str(payload.get("lesson_type") or ""),
                "lesson_title": _bounded_text(payload.get("title"), max_chars=160),
                "lesson_summary": _bounded_text(payload.get("summary"), max_chars=320),
                # Additive backward compatibility: legacy lesson rows predate
                # occurrence aggregation and therefore represent one event.
                "occurrence_count": _positive_int_or_default(
                    payload.get("occurrence_count"), default=1
                ),
                "distinct_source_count": _nonnegative_int_or_default(
                    payload.get("distinct_source_count"), default=0
                ),
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
        source_candidate_ids = _string_list(payload.get("source_candidate_ids"))
        if source_candidate_ids:
            metrics["source_candidate_ids"] = source_candidate_ids
        affected_case_ids = _string_list(payload.get("affected_case_ids"))
        if affected_case_ids:
            metrics["affected_case_ids"] = affected_case_ids
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
    candidate_path = (
        Path(raw_path)
        if raw_path is not None
        else run_root / "lessons" / "lessons.jsonl"
    )
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
    failed_gates = {str(gate) for gate in metrics.get("failed_gates", ()) if str(gate)}
    return (
        bool(failed_gates)
        and failed_gates
        <= {
            "candidate_replay",
            "replay_confidence",
        }
        and not any(
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
    )


def _non_authoritative_candidate_rejection(metrics: Mapping[str, Any]) -> bool:
    if _retryable_infrastructure_rejection(metrics):
        return True
    failed_gates = {str(gate) for gate in metrics.get("failed_gates", ()) if str(gate)}
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


def _evidence_quality_gate(
    summary: EvaluationSummary,
    *,
    baseline: EvaluationSummary | None = None,
) -> GateResult | None:
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
    return EvidenceQualityGate().evaluate(summary, baseline=baseline)


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
        "evidence_artifact_reference_count",
        "evidence_manifested_artifact_reference_count",
        "evidence_unmanifested_artifact_reference_count",
        "evidence_unmanifested_artifact_reference_identity_digests",
        "evidence_runtime_policy_active",
        "evidence_runtime_policy_passed",
        "evidence_runtime_policy_authoritative_passed",
        "evidence_runtime_policy_authority",
        "evidence_runtime_policy_mode",
        "evidence_runtime_policy_advisory_violation_count",
        "evidence_runtime_policy_violation_count",
        "evidence_runtime_policy_phase",
        "evidence_runtime_policy_tool_call_attempt_count",
        "evidence_runtime_policy_artifact_file_count",
        "evidence_runtime_policy_artifact_bytes",
        "evidence_runtime_policy_consecutive_failed_action_count",
        "evidence_runtime_policy_max_consecutive_failed_actions",
        "evidence_runtime_policy_allowed_loopback_endpoint_count",
        "evidence_runtime_policy_allowed_control_action_count",
        "task_completion_established",
        "timeout_evidence_recovered",
        "replay_counterexamples",
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


def _conformance_gate_blocks_population(gate: GateResult) -> bool:
    return _gate_has_typed_shared_infrastructure_failure(gate)


def _gate_blocks_measurement_materialization(gate: GateResult) -> bool:
    """Prevent derived statistical gates from hiding an execution blocker."""

    return bool(
        _gate_has_typed_shared_measurement_failure(gate)
        or _gate_is_replay_execution_infrastructure_failure(gate)
    )


def _failed_probe_typed_feedback(
    failed_groups: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Merge payload-free exception diagnostics across every failed probe shape."""

    constraints: dict[str, dict[str, object]] = {}
    runtime_artifact_constraints: dict[str, dict[str, object]] = {}
    runtime_response_constraints: dict[str, dict[str, object]] = {}
    runtime_response_observations: list[dict[str, object]] = []
    counterexample_contracts: dict[str, dict[str, object]] = {}
    violations: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    violation_count = 0
    for result in failed_groups:
        diagnostic: dict[str, object] = {
            "code": str(result.get("code") or "repair_probe_execution_failed"),
            "root_cause_code": str(
                result.get("root_cause_code")
                or result.get("code")
                or "repair_probe_execution_failed"
            ),
            "error_type": str(result.get("error_type") or "Exception"),
            "reason": str(result.get("reason") or "candidate probe failed"),
        }
        raw_constraints = result.get("schema_field_constraints")
        if isinstance(raw_constraints, (list, tuple)):
            projected: list[dict[str, object]] = []
            for item in raw_constraints:
                if not isinstance(item, Mapping):
                    continue
                value = dict(item)
                identity = json.dumps(
                    value,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                )
                constraints[identity] = value
                projected.append(value)
            if projected:
                diagnostic["schema_field_constraints"] = projected
        raw_violations = result.get("schema_field_violations")
        if isinstance(raw_violations, (list, tuple)):
            projected_violations = [
                dict(item) for item in raw_violations if isinstance(item, Mapping)
            ][:100]
            violations.extend(projected_violations)
            if projected_violations:
                diagnostic["schema_field_violations"] = projected_violations
        raw_count = result.get("schema_field_violation_count")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool):
            violation_count += max(0, raw_count)
        raw_counterexamples = result.get("counterexample_contracts")
        if isinstance(raw_counterexamples, (list, tuple)):
            projected_counterexamples: list[dict[str, object]] = []
            for item in raw_counterexamples[:100]:
                if not isinstance(item, Mapping):
                    continue
                counterexample_id = item.get("counterexample_id")
                if not isinstance(counterexample_id, str) or not counterexample_id:
                    continue
                value = dict(item)
                counterexample_contracts[counterexample_id] = value
                projected_counterexamples.append(value)
            if projected_counterexamples:
                diagnostic["counterexample_contracts"] = projected_counterexamples
        raw_runtime_constraints = result.get("runtime_response_constraints")
        if isinstance(raw_runtime_constraints, (list, tuple)):
            projected_runtime_constraints: list[dict[str, object]] = []
            for item in raw_runtime_constraints[:64]:
                if not isinstance(item, Mapping):
                    continue
                value = dict(item)
                identity = json.dumps(
                    value,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                )
                runtime_response_constraints[identity] = value
                projected_runtime_constraints.append(value)
            if projected_runtime_constraints:
                diagnostic["runtime_response_constraints"] = (
                    projected_runtime_constraints
                )
        raw_runtime_observation = result.get("runtime_response_observation")
        if isinstance(raw_runtime_observation, Mapping):
            observation = dict(raw_runtime_observation)
            runtime_response_observations.append(observation)
            diagnostic["runtime_response_observation"] = observation
        raw_runtime_artifacts = result.get("runtime_artifact_constraints")
        if isinstance(raw_runtime_artifacts, (list, tuple)):
            projected_runtime_artifacts: list[dict[str, object]] = []
            for item in raw_runtime_artifacts[:64]:
                if not isinstance(item, Mapping):
                    continue
                value = dict(item)
                identity = json.dumps(
                    value,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                )
                runtime_artifact_constraints[identity] = value
                projected_runtime_artifacts.append(value)
            if projected_runtime_artifacts:
                diagnostic["runtime_artifact_constraints"] = projected_runtime_artifacts
        diagnostics.append(diagnostic)
    feedback: dict[str, object] = {"diagnostics": diagnostics[:32]}
    if constraints:
        feedback["schema_field_constraints"] = [
            constraints[key] for key in sorted(constraints)
        ]
    if violations:
        feedback["schema_field_violations"] = violations[:100]
        feedback["schema_field_violation_count"] = (
            violation_count if violation_count else len(violations)
        )
    if counterexample_contracts:
        feedback["counterexample_contracts"] = [
            counterexample_contracts[key] for key in sorted(counterexample_contracts)
        ]
    if runtime_response_constraints:
        feedback["runtime_response_constraints"] = [
            runtime_response_constraints[key]
            for key in sorted(runtime_response_constraints)
        ]
    if runtime_artifact_constraints:
        feedback["runtime_artifact_constraints"] = [
            runtime_artifact_constraints[key]
            for key in sorted(runtime_artifact_constraints)
        ]
    if runtime_response_observations:
        feedback["runtime_response_observations"] = runtime_response_observations[:32]
    return feedback


def _repair_probe_root_cause_code(exc: Exception) -> str:
    declared = getattr(exc, "code", None)
    if isinstance(declared, str) and declared:
        return declared
    if isinstance(exc, ReplayServiceReadinessTimeout):
        if exc.phase == "protocol_probe":
            return "replay_service_protocol_probe_timeout"
        return "replay_service_readiness_failed"
    if isinstance(exc, ReplayServiceProcessExitedError):
        return "replay_service_process_exited_before_readiness"
    return "repair_probe_execution_failed"


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
        "stage": "conformance",
        "gate_name": gate.gate_name,
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
                    tail = handle.read(4_096).decode("utf-8", errors="replace")
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


def _environment_fingerprint_drift_gate(
    expected_fingerprint: str,
    observed_fingerprint: str,
) -> GateResult | None:
    """Return a shared-run blocker when immutable environment identity drifts."""

    if expected_fingerprint == observed_fingerprint:
        return None
    failure_event = ReplayFailureEvent(
        code="environment_fingerprint_drift",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.ADAPTATION,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
        category="environment_health",
        summary="replay environment changed during one self-evolve run",
        diagnostics={
            "expected_environment_fingerprint": expected_fingerprint,
            "observed_environment_fingerprint": observed_fingerprint,
        },
    )
    event_payload = failure_event.to_dict()
    return GateResult(
        gate_name="replay_environment_health",
        passed=False,
        reason=("replay environment fingerprint changed during the active run"),
        details={
            "failure_class": FailureOwner.INFRASTRUCTURE.value,
            "failure_owner": FailureOwner.INFRASTRUCTURE.value,
            "failure_scope": FailureScope.SHARED_RUN.value,
            "failure_source": FailureEventSource.NATIVE.value,
            "repairable": False,
            "code": "environment_fingerprint_drift",
            "expected_environment_fingerprint": expected_fingerprint,
            "observed_environment_fingerprint": observed_fingerprint,
            "failure_event": event_payload,
            "causal_failure_events": [event_payload],
        },
    )


def _replay_confidence_gate(
    replay_result: CandidateReplayResult | None,
    *,
    dataset: SelfEvolveDataset,
    apply_policy: str,
) -> GateResult | None:
    if replay_result is None or not _is_verified_apply_policy(apply_policy):
        return None
    normalized = normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    )
    if coverage["candidate_executed_count"] == 0:
        return None
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
    causal_failures = aggregate_replay_failures(
        replay_result,
        normalized=normalized,
    )
    invalid_control = any(
        event.owner is FailureOwner.FRAMEWORK
        and event.code
        in {
            "authoritative_replay_invalid_control",
            "trusted_measurement_invalid_control_frontier",
        }
        for event in causal_failures
    )
    zero_comparable_pairs = coverage["comparable_pair_count"] == 0
    if invalid_control or zero_comparable_pairs:
        measurement_event = ReplayFailureEvent(
            code="control_not_comparable",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.SHARED_RUN,
            repairable=True,
            category="measurement_validity",
            summary=(
                "paired replay produced no comparable task-level evidence"
                if zero_comparable_pairs
                else "paired replay control was not comparable"
            ),
            diagnostics={
                "comparable_pair_count": coverage["comparable_pair_count"],
                "incomparable_pair_count": coverage["incomparable_pair_count"],
                "candidate_executed_count": coverage["candidate_executed_count"],
            },
        )
        measurement_payload = measurement_event.to_dict()
        primary_failure = next(
            (
                event
                for event in causal_failures
                if event.stage is not FailureStage.EVALUATION
                and FailureEventSource.NATIVE.value
                in getattr(event, "source_kinds", ())
            ),
            measurement_event,
        )
        primary_payload = primary_failure.to_dict()
        primary_class = (
            "measurement"
            if primary_failure is measurement_event
            else primary_failure.owner.value
        )
        primary_scope = (
            FailureScope.SHARED_RUN
            if primary_failure.owner
            in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
            else primary_failure.scope
        )
        primary_next_action = (
            "repair_measurement"
            if primary_failure.owner
            in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
            else "repair_candidate"
            if primary_failure.owner is FailureOwner.CANDIDATE
            else "repair_task_completion"
        )
        base_details.update(
            {
                "code": primary_failure.code,
                "failure_class": primary_class,
                "failure_owner": primary_failure.owner.value,
                "failure_scope": primary_scope.value,
                "failure_stage": primary_failure.stage.value,
                "repairable": primary_failure.repairable,
                "next_action": primary_next_action,
                "effect": None,
                "failure_event": primary_payload,
                "derived_failure_event": measurement_payload,
                "causal_failure_events": [
                    *(event.to_dict() for event in causal_failures),
                    measurement_payload,
                ],
                "observed_replay_failure_events": [
                    event.to_dict() for event in causal_failures
                ],
            }
        )
    actionable_incomparable_pair_count = max(
        0,
        int(coverage["incomparable_pair_count"])
        - int(coverage.get("intentionally_unadmitted_member_count", 0)),
    )
    base_details["actionable_incomparable_pair_count"] = (
        actionable_incomparable_pair_count
    )
    if actionable_incomparable_pair_count > 0:
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
        candidate_variants = tuple(
            member.candidate for member in normalized.members
        ) or (replay_result.candidate,)
        system_failures = _system_owned_repetition_failures(*candidate_variants)
        if system_failures:
            failure_owner = (
                FailureOwner.INFRASTRUCTURE
                if any(
                    event.owner is FailureOwner.INFRASTRUCTURE
                    for event in system_failures
                )
                else FailureOwner.FRAMEWORK
            )
            failure_scope = (
                FailureScope.SHARED_RUN
                if failure_owner is FailureOwner.INFRASTRUCTURE
                else FailureScope.MEMBER
            )
            event_payloads = [event.to_dict() for event in system_failures]
            return GateResult(
                gate_name="replay_confidence",
                passed=False,
                reason=(
                    "replay confidence is unavailable because system-owned "
                    "repetitions failed"
                ),
                details={
                    **base_details,
                    "candidate_repetition_count": int(candidate_repetitions),
                    "candidate_successful_repetition_count": int(
                        candidate_successful_repetitions
                    ),
                    "candidate_failed_repetition_count": (
                        int(candidate_failed_repetitions)
                        if isinstance(
                            candidate_failed_repetitions,
                            (int, float),
                        )
                        else None
                    ),
                    "failure_class": failure_owner.value,
                    "failure_owner": failure_owner.value,
                    "failure_scope": failure_scope.value,
                    "repairable": any(event.repairable for event in system_failures),
                    "failure_event": event_payloads[0],
                    "causal_failure_events": event_payloads,
                },
            )
        return GateResult(
            gate_name="replay_confidence",
            passed=False,
            reason="candidate replay successful repetitions are insufficient",
            details={
                **base_details,
                "candidate_repetition_count": int(candidate_repetitions),
                "candidate_successful_repetition_count": int(
                    candidate_successful_repetitions
                ),
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


_MAX_REPAIR_CANDIDATE_PACKAGE_CHARS = 64_000
_MAX_REPAIR_CANDIDATE_FILE_CHARS = 32_000
_MAX_MIXED_REPAIR_TARGET_CHARS = 32_000
_MAX_HISTORICAL_REPAIR_CANDIDATES = 8


def _finite_measurement_metric(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ):
        return float(value)
    return None


def _optional_measurement_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _non_negative_measurement_int(value: object) -> int | None:
    numeric = _finite_measurement_metric(value)
    if numeric is None or numeric < 0 or not numeric.is_integer():
        return None
    return int(numeric)


def _non_negative_measurement_float(value: object) -> float | None:
    numeric = _finite_measurement_metric(value)
    return numeric if numeric is not None and numeric >= 0 else None


def _budget_curve_points(total: int | float) -> tuple[int | float, ...]:
    if total <= 0:
        return (0,)
    return tuple(
        dict.fromkeys(
            (
                total * 0.25,
                total * 0.5,
                total * 0.75,
                total,
            )
        )
    )


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
    if (
        not has_trajectory_set_source
        and not prior_case_ids
        and not set_ids
        and not auto_grouping
    ):
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
    attempt_events: Iterable[CandidateAttemptEvent] = (),
    budget_report: Mapping[str, object] | None = None,
    scheduler_decisions: Iterable[Mapping[str, object]] = (),
) -> dict[str, object] | None:
    attempt_events = tuple(attempt_events)
    if not all_candidates and not iteration_reports and not attempt_events:
        return None
    replayed_candidate_ids = [
        str(item.get("candidate_id"))
        for item in iteration_reports
        if isinstance(item.get("candidate_id"), str)
        and item.get("lifecycle_stage") == "authoritative_replay"
    ]
    report: dict[str, object] = {
        "generated_candidate_count": len(all_candidates),
        "generated_candidate_ids": [
            candidate.candidate_id for candidate in all_candidates
        ],
        "replayed_candidate_count": len(replayed_candidate_ids),
        "replayed_candidate_ids": replayed_candidate_ids,
        "replay_candidate_limit": replay_candidate_limit,
        "non_replayed_candidate_count": max(
            0,
            len(all_candidates) - len(set(replayed_candidate_ids)),
        ),
    }
    lifecycle: dict[str, object] = {
        "generated": {
            "candidate_count": len(all_candidates),
            "candidate_ids": [candidate.candidate_id for candidate in all_candidates],
        },
        "conformance": {
            "attempted_candidate_count": 0,
            "rejected_candidate_count": 0,
            "attempted_candidate_ids": [],
            "rejected_candidate_ids": [],
        },
        "screening": {
            "attempted_candidate_count": 0,
            "rejected_candidate_count": 0,
            "attempted_candidate_ids": [],
            "rejected_candidate_ids": [],
        },
        "authoritative_replay": {
            "attempted_candidate_count": len(replayed_candidate_ids),
            "attempted_candidate_ids": replayed_candidate_ids,
        },
    }
    if screening_reports:
        latest_validation = screening_reports[-1]
        latest_conformance = latest_validation.get("conformance")
        latest_screening = latest_validation.get("screening")
        if isinstance(latest_conformance, Mapping):
            report["conformance"] = _candidate_validation_report_for_persistence(
                latest_conformance
            )
        if isinstance(latest_screening, Mapping):
            report["screening"] = _candidate_validation_report_for_persistence(
                latest_screening
            )
        elif "conformance" not in latest_validation:
            report["screening"] = latest_validation
        if len(screening_reports) > 1:
            conformance_iterations = [
                _candidate_validation_report_for_persistence(item["conformance"])
                for item in screening_reports
                if isinstance(item.get("conformance"), Mapping)
            ]
            task_screening_iterations = [
                _candidate_validation_report_for_persistence(item["screening"])
                for item in screening_reports
                if isinstance(item.get("screening"), Mapping)
            ]
            if conformance_iterations:
                report["conformance_iterations"] = conformance_iterations
            if task_screening_iterations:
                report["screening_iterations"] = task_screening_iterations
        conformance_attempts = [
            attempt
            for validation in screening_reports
            for conformance in (validation.get("conformance"),)
            if isinstance(conformance, Mapping)
            for attempt in conformance.get("attempts", ())
            if isinstance(attempt, Mapping)
        ]
        screening_attempts = [
            attempt
            for validation in screening_reports
            for screening in (validation.get("screening"),)
            if isinstance(screening, Mapping)
            for attempt in screening.get("attempts", ())
            if isinstance(attempt, Mapping)
        ]
        screening_stage_reports = [
            screening
            for validation in screening_reports
            for screening in (validation.get("screening"),)
            if isinstance(screening, Mapping)
        ]
        termination_axis_counts: dict[str, int] = {}
        for screening in screening_stage_reports:
            raw_counts = screening.get("termination_budget_axis_counts")
            if not isinstance(raw_counts, Mapping):
                continue
            for axis, count in raw_counts.items():
                if not isinstance(axis, str):
                    continue
                termination_axis_counts[axis] = termination_axis_counts.get(
                    axis, 0
                ) + _non_negative_int(count)
        report["screening_execution"] = {
            "physical_pair_execution_count": sum(
                _non_negative_int(screening.get("physical_pair_execution_count"))
                for screening in screening_stage_reports
            ),
            "wall_seconds": sum(
                _non_negative_screening_float(screening.get("screening_wall_seconds"))
                for screening in screening_stage_reports
            ),
            "right_censored_batch_count": sum(
                int(screening.get("stopped_after_budget_censor") is True)
                for screening in screening_stage_reports
            ),
            "termination_budget_axis_counts": termination_axis_counts,
            "strategy_counts": dict(
                Counter(
                    str(screening.get("screening_strategy") or "unknown")
                    for screening in screening_stage_reports
                )
            ),
        }
        for stage_name, attempts in (
            ("conformance", conformance_attempts),
            ("screening", screening_attempts),
        ):
            attempted_ids = list(
                dict.fromkeys(
                    str(attempt.get("candidate_id"))
                    for attempt in attempts
                    if isinstance(attempt.get("candidate_id"), str)
                )
            )
            rejected_ids = list(
                dict.fromkeys(
                    str(attempt.get("candidate_id"))
                    for attempt in attempts
                    if isinstance(attempt.get("candidate_id"), str)
                    and attempt.get("passed") is False
                )
            )
            stage = lifecycle[stage_name]
            assert isinstance(stage, dict)
            stage.update(
                {
                    "attempted_candidate_count": len(attempted_ids),
                    "rejected_candidate_count": len(rejected_ids),
                    "attempted_candidate_ids": attempted_ids,
                    "rejected_candidate_ids": rejected_ids,
                }
            )
    stored_events = attempt_events
    terminal_reason_by_candidate: dict[str, str] = {}
    if stored_events:
        compatibility_lifecycle = lifecycle
        aggregate = aggregate_candidate_attempts(stored_events)
        grouped_events: dict[CandidateAttemptKey, list[CandidateAttemptEvent]] = {}
        for event in stored_events:
            grouped_events.setdefault(event.key, []).append(event)
        replayed_candidate_ids = list(
            dict.fromkeys(
                event.candidate_id
                for event in stored_events
                if event.stage is CandidateAttemptStage.PAIRED_REPLAY_STARTED
            )
        )
        for events in grouped_events.values():
            terminal = sorted(events, key=lambda item: item.sequence)[-1]
            if terminal.terminal and terminal.reason_code is not None:
                terminal_reason_by_candidate[terminal.candidate_id] = (
                    terminal.reason_code
                )
        report.update(
            {
                "generation_attempt_count": aggregate.attempt_count,
                "unique_candidate_count": aggregate.unique_candidate_count,
                "duplicate_attempt_count": aggregate.duplicate_attempt_count,
                "terminal_attempt_count": aggregate.terminal_attempt_count,
                "replayed_candidate_count": aggregate.paired_replay_started_count,
                "replayed_candidate_ids": replayed_candidate_ids,
                "paired_replay_started_count": (aggregate.paired_replay_started_count),
                "paired_replay_completed_count": (
                    aggregate.paired_replay_completed_count
                ),
                "paired_replay_comparable_count": (
                    aggregate.paired_replay_comparable_count
                ),
                "non_replayed_candidate_count": max(
                    0,
                    aggregate.unique_candidate_count - len(set(replayed_candidate_ids)),
                ),
            }
        )
        lifecycle = aggregate.to_dict()
        report["compatibility_aliases"] = {
            "generated_candidate_count": {
                "value": len(all_candidates),
                "semantic": "canonical_unique_candidates_persisted",
            },
            "replayed_candidate_count": {
                "value": aggregate.paired_replay_started_count,
                "semantic": "paired_replay_started_attempts",
            },
            "legacy_stage_details": compatibility_lifecycle,
        }
    strategy_records = _candidate_strategy_records(optimizer_diagnostics or ())
    if strategy_records:
        replayed_set = set(replayed_candidate_ids)
        non_replayed: list[dict[str, object]] = []
        for record in strategy_records:
            candidate_id = str(record.get("candidate_id"))
            if candidate_id in replayed_set:
                continue
            terminal_reason = terminal_reason_by_candidate.get(candidate_id)
            item = dict(record)
            if terminal_reason is not None:
                item["terminal_reason_code"] = terminal_reason
                if "budget_denied" in terminal_reason:
                    item["not_replayed_reason"] = "not_replayed_due_to_budget"
            non_replayed.append(item)
        if non_replayed:
            report["non_replayed_candidate_strategies"] = non_replayed
    report["lifecycle"] = lifecycle
    if budget_report is not None:
        report["budget"] = dict(budget_report)
    scheduler_payload = [dict(item) for item in scheduler_decisions]
    if scheduler_payload:
        report["scheduler_decisions"] = scheduler_payload
    return report


def _screening_observation_scope_fingerprint(
    *,
    dataset: SelfEvolveDataset,
    target: SelfEvolveTarget,
) -> str:
    payload = {
        "dataset_fingerprint": replay_dataset_fingerprint(dataset),
        "target_type": target.identity.target_type,
        "target_id": target.identity.target_id,
        "target_path": target.identity.path,
        "baseline_skill_fingerprint": target.fingerprint_current_content(),
        "harness_fingerprint": _screening_control_harness_fingerprint(),
    }
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )


def _restore_campaign_screening_case_observations(
    observations: dict[str, dict[str, float | int]],
    *,
    store: FilesystemSelfEvolveStore,
    prior_run_ids: tuple[str, ...],
    loaded_run_ids: set[str],
    control_observations: dict[str, dict[str, object]] | None = None,
    harness_fingerprint: str | None = None,
) -> None:
    """Restore payload-free control health across Campaign cycles/restarts."""

    for prior_run_id in prior_run_ids:
        if prior_run_id in loaded_run_ids:
            continue
        try:
            report = store.read_report(prior_run_id)
        except (FileNotFoundError, OSError, TypeError, ValueError):
            continue
        if not _report_matches_screening_harness(
            report,
            harness_fingerprint,
        ):
            continue
        verification_funnel = report.get("verification_funnel")
        authoritative_observations = (
            verification_funnel.get("authoritative_case_observations")
            if isinstance(verification_funnel, Mapping)
            else None
        )
        if isinstance(authoritative_observations, Mapping):
            for case_id, raw_observation in authoritative_observations.items():
                if not isinstance(case_id, str) or not isinstance(
                    raw_observation, Mapping
                ):
                    continue
                current = observations.setdefault(case_id, {})
                for field_name in (
                    "attempt_count",
                    "invalid_control_count",
                    "passed_count",
                    "authoritative_failure_count",
                ):
                    count = _non_negative_int(raw_observation.get(field_name))
                    if count <= 0:
                        continue
                    current[field_name] = (
                        _non_negative_int(current.get(field_name)) + count
                    )
                if not current:
                    observations.pop(case_id, None)
        population = report.get("population")
        screening = (
            population.get("screening") if isinstance(population, Mapping) else None
        )
        attempts = screening.get("attempts") if isinstance(screening, Mapping) else None
        if isinstance(attempts, (list, tuple)):
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    continue
                raw_control_attempts = attempt.get("control_case_attempts")
                if not isinstance(raw_control_attempts, (list, tuple)):
                    continue
                for control_attempt in raw_control_attempts:
                    if not isinstance(control_attempt, Mapping):
                        continue
                    raw_case_ids = control_attempt.get("case_ids")
                    if control_attempt.get("invalid_control") is True:
                        raw_invalid_case_ids = control_attempt.get(
                            "invalid_control_case_ids"
                        )
                        if isinstance(raw_invalid_case_ids, (list, tuple)):
                            raw_case_ids = raw_invalid_case_ids
                    case_ids = tuple(
                        str(case_id)
                        for case_id in (
                            raw_case_ids
                            if isinstance(raw_case_ids, (list, tuple))
                            else ()
                        )
                        if isinstance(case_id, str) and case_id
                    )
                    if not case_ids:
                        continue
                    raw_identities = control_attempt.get("control_identities")
                    identities = (
                        tuple(
                            item for item in raw_identities if isinstance(item, Mapping)
                        )
                        if isinstance(raw_identities, (list, tuple))
                        else (
                            (control_attempt["control_identity"],)
                            if isinstance(
                                control_attempt.get("control_identity"),
                                Mapping,
                            )
                            else ()
                        )
                    )
                    if control_observations is not None:
                        for identity in identities:
                            _record_support_specific_control_observation(
                                control_observations,
                                identity=identity,
                                attempt={
                                    "passed": (control_attempt.get("passed") is True),
                                    "wall_seconds": (
                                        _non_negative_screening_float(
                                            control_attempt.get("wall_seconds")
                                        )
                                        / max(1, len(identities))
                                    ),
                                    "details": {
                                        "baseline_status": control_attempt.get(
                                            "baseline_status"
                                        ),
                                        "candidate_status": control_attempt.get(
                                            "candidate_status"
                                        ),
                                        "baseline_failure": control_attempt.get(
                                            "baseline_failure"
                                        ),
                                        "candidate_failure": control_attempt.get(
                                            "candidate_failure"
                                        ),
                                    },
                                },
                            )
                    for case_id in case_ids:
                        current = observations.setdefault(case_id, {})
                        current["attempt_count"] = (
                            _non_negative_int(current.get("attempt_count")) + 1
                        )
                        current["invalid_control_count"] = _non_negative_int(
                            current.get("invalid_control_count")
                        ) + int(control_attempt.get("invalid_control") is True)
                        current["passed_count"] = _non_negative_int(
                            current.get("passed_count")
                        ) + int(control_attempt.get("passed") is True)
                        wall_seconds = _non_negative_screening_float(
                            control_attempt.get("wall_seconds")
                        ) / max(1, len(case_ids))
                        current["total_wall_seconds"] = (
                            _non_negative_screening_float(
                                current.get("total_wall_seconds")
                            )
                            + wall_seconds
                        )
        _restore_authoritative_member_lifecycle_observations(
            observations,
            control_observations=control_observations,
            run_dir=store.run_path(prior_run_id),
        )
        loaded_run_ids.add(prior_run_id)


def _restore_historical_screening_lifecycle_observations(
    observations: dict[str, dict[str, float | int]],
    *,
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    dataset: SelfEvolveDataset,
    current_run_id: str,
    control_observations: dict[str, dict[str, object]] | None = None,
    loaded_run_ids: set[str] | None = None,
    harness_fingerprint: str | None = None,
) -> None:
    """Build a candidate-independent control profile from prior lifecycles."""

    eligible_case_ids = {case.case_id for case in dataset.cases}
    report_paths = sorted(
        store.artifact_root.glob("*/report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:128]
    for report_path in report_paths:
        run_dir = report_path.parent
        if run_dir.name == current_run_id or run_dir.is_symlink():
            continue
        if loaded_run_ids is not None and run_dir.name in loaded_run_ids:
            continue
        try:
            report = _load_json_mapping(report_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not _report_matches_target(report, target):
            continue
        if not _report_matches_screening_harness(
            report,
            harness_fingerprint,
        ):
            continue
        restored_authoritative = _restore_authoritative_member_lifecycle_observations(
            observations,
            control_observations=control_observations,
            run_dir=run_dir,
            eligible_case_ids=eligible_case_ids,
        )
        if control_observations is not None:
            health = report.get("support_specific_control_health")
            raw_health_observations = (
                health.get("observations") if isinstance(health, Mapping) else None
            )
            if not restored_authoritative and isinstance(
                raw_health_observations, (list, tuple)
            ):
                for raw_observation in raw_health_observations[:128]:
                    if not isinstance(raw_observation, Mapping):
                        continue
                    identity = raw_observation.get("identity")
                    fingerprint = (
                        identity.get("control_identity_fingerprint")
                        if isinstance(identity, Mapping)
                        else None
                    )
                    if isinstance(fingerprint, str) and fingerprint:
                        control_observations.setdefault(
                            fingerprint,
                            dict(raw_observation),
                        )
        screening_root = run_dir / "screening"
        if not screening_root.is_dir() or screening_root.is_symlink():
            continue
        for case_dir in screening_root.iterdir():
            if not case_dir.is_dir() or case_dir.is_symlink():
                continue
            replay_root = case_dir / "replay"
            if not replay_root.is_dir() or replay_root.is_symlink():
                continue
            for replay_dir in replay_root.iterdir():
                if not replay_dir.is_dir() or replay_dir.is_symlink():
                    continue
                try:
                    stored_request = _candidate_replay_request_from_mapping(
                        _load_json_mapping(replay_dir / "request.json")
                    )
                except (
                    FileNotFoundError,
                    OSError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    stored_request = None
                case_id = (
                    stored_request.task_id
                    if stored_request is not None
                    and stored_request.task_id in eligible_case_ids
                    else case_dir.name
                    if case_dir.name in eligible_case_ids
                    else None
                )
                if case_id is None:
                    continue
                current = observations.setdefault(case_id, {})
                _merge_screening_variant_lifecycle_observation(
                    current,
                    variant_dir=replay_dir / "baseline",
                    phase="baseline",
                )
                candidate_dir = replay_dir / replay_dir.name
                _merge_screening_variant_lifecycle_observation(
                    current,
                    variant_dir=candidate_dir,
                    phase="candidate",
                )
                if control_observations is not None:
                    identity = (
                        _control_qualification_identity_from_request(stored_request)
                        if stored_request is not None
                        else None
                    )
                    if identity is not None:
                        fingerprint = identity.get("control_identity_fingerprint")
                        if fingerprint not in control_observations:
                            _merge_support_specific_lifecycle_observation(
                                control_observations,
                                identity=identity,
                                variant_dir=replay_dir / "baseline",
                            )
                if not current:
                    observations.pop(case_id, None)


def _restore_authoritative_member_lifecycle_observations(
    observations: dict[str, dict[str, float | int]],
    *,
    control_observations: dict[str, dict[str, object]] | None,
    run_dir: Path,
    eligible_case_ids: set[str] | None = None,
) -> bool:
    """Recover completed member controls even when a run timed out pre-report."""

    replay_root = run_dir / "replay"
    if not replay_root.is_dir() or replay_root.is_symlink():
        return False
    restored = False
    candidate_dirs = sorted(replay_root.iterdir(), key=lambda path: path.name)[:32]
    for candidate_dir in candidate_dirs:
        members_root = candidate_dir / "members"
        if (
            not candidate_dir.is_dir()
            or candidate_dir.is_symlink()
            or not members_root.is_dir()
            or members_root.is_symlink()
        ):
            continue
        member_dirs = sorted(members_root.iterdir(), key=lambda path: path.name)[:256]
        for member_dir in member_dirs:
            if not member_dir.is_dir() or member_dir.is_symlink():
                continue
            try:
                stored_request = _candidate_replay_request_from_mapping(
                    _load_json_mapping(member_dir / "request.json")
                )
            except (
                FileNotFoundError,
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue
            case_id = stored_request.task_id
            if eligible_case_ids is not None and case_id not in eligible_case_ids:
                continue
            identity = _control_qualification_identity_from_request(stored_request)
            if identity is None:
                continue
            baseline_dir = member_dir / "baseline"
            lifecycle_path = baseline_dir / "lifecycle.json"
            if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
                continue
            restored = True
            current = observations.setdefault(case_id, {})
            _merge_screening_variant_lifecycle_observation(
                current,
                variant_dir=baseline_dir,
                phase="baseline",
            )
            if control_observations is not None:
                _merge_support_specific_lifecycle_observation(
                    control_observations,
                    identity=identity,
                    variant_dir=baseline_dir,
                )
            if not current:
                observations.pop(case_id, None)
    return restored


def _merge_screening_variant_lifecycle_observation(
    observation: dict[str, float | int],
    *,
    variant_dir: Path,
    phase: str,
) -> None:
    lifecycle_path = variant_dir / "lifecycle.json"
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        return
    try:
        lifecycle = _load_json_mapping(lifecycle_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    status = str(lifecycle.get("status") or "")
    if status in {"", "blocked", "not_run"}:
        return
    attempt_key = f"{phase}_attempt_count"
    success_key = f"{phase}_success_count"
    timeout_key = f"{phase}_timeout_count"
    wall_key = f"{phase}_total_wall_seconds"
    observation[attempt_key] = _non_negative_int(observation.get(attempt_key)) + 1
    observation[success_key] = _non_negative_int(observation.get(success_key)) + int(
        status == ReplayExecutionStatus.SUCCEEDED.value
    )
    failure = lifecycle.get("failure")
    phase_timeout = bool(
        isinstance(failure, Mapping)
        and failure.get("code") == "replay_member_phase_timeout"
    )
    observation[timeout_key] = _non_negative_int(observation.get(timeout_key)) + int(
        phase_timeout
    )
    metrics_path = variant_dir / "aggregate_metrics.json"
    try:
        metrics = _load_json_mapping(metrics_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        metrics = {}
    latency_ms = metrics.get("latency_ms")
    wall_seconds = (
        float(latency_ms) / 1000.0
        if isinstance(latency_ms, (int, float))
        and not isinstance(latency_ms, bool)
        and math.isfinite(float(latency_ms))
        and float(latency_ms) >= 0
        else 0.0
    )
    if wall_seconds <= 0 and phase_timeout and isinstance(failure, Mapping):
        diagnostics = failure.get("diagnostics")
        timeout = (
            diagnostics.get("timeout_seconds")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if (
            isinstance(timeout, (int, float))
            and not isinstance(timeout, bool)
            and math.isfinite(float(timeout))
            and float(timeout) > 0
        ):
            wall_seconds = float(timeout)
    if phase_timeout and isinstance(failure, Mapping):
        diagnostics = failure.get("diagnostics")
        timeout = (
            diagnostics.get("timeout_seconds")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if (
            isinstance(timeout, (int, float))
            and not isinstance(timeout, bool)
            and math.isfinite(float(timeout))
            and float(timeout) > 0
        ):
            observation[f"{phase}_timeout_max_seconds"] = max(
                _non_negative_screening_float(
                    observation.get(f"{phase}_timeout_max_seconds")
                ),
                float(timeout),
            )
    observation[wall_key] = (
        _non_negative_screening_float(observation.get(wall_key)) + wall_seconds
    )
    if status == ReplayExecutionStatus.SUCCEEDED.value:
        success_wall_key = f"{phase}_success_wall_seconds"
        observation[success_wall_key] = (
            _non_negative_screening_float(observation.get(success_wall_key))
            + wall_seconds
        )


def _merge_support_specific_lifecycle_observation(
    observations: dict[str, dict[str, object]],
    *,
    identity: Mapping[str, object],
    variant_dir: Path,
) -> None:
    lifecycle_path = variant_dir / "lifecycle.json"
    if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
        return
    try:
        lifecycle = _load_json_mapping(lifecycle_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    status = str(lifecycle.get("status") or "")
    if status in {"", "blocked", "not_run"}:
        return
    failure = lifecycle.get("failure")
    metrics_path = variant_dir / "aggregate_metrics.json"
    try:
        metrics = _load_json_mapping(metrics_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        metrics = {}
    latency_ms = metrics.get("latency_ms")
    wall_seconds = (
        float(latency_ms) / 1000.0
        if isinstance(latency_ms, (int, float))
        and not isinstance(latency_ms, bool)
        and math.isfinite(float(latency_ms))
        and float(latency_ms) >= 0
        else 0.0
    )
    _record_support_specific_control_observation(
        observations,
        identity=identity,
        attempt={
            "passed": status == ReplayExecutionStatus.SUCCEEDED.value,
            "wall_seconds": wall_seconds,
            "details": {
                "baseline_status": status,
                "baseline_failure": failure,
            },
        },
    )


def _screening_control_preflight(
    dataset: SelfEvolveDataset,
    *,
    observations: Mapping[str, Mapping[str, float | int]],
    timeout_ceiling_seconds: float = _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    harness_fingerprint: str | None = None,
) -> dict[str, object]:
    """Classify baseline feasibility before any candidate generation call."""

    case_ids = tuple(
        case.case_id for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    feasible: list[str] = []
    infeasible: list[str] = []
    unknown: list[str] = []
    for case_id in case_ids:
        observation = observations.get(case_id, {})
        attempts = _non_negative_int(observation.get("baseline_attempt_count"))
        successes = _non_negative_int(observation.get("baseline_success_count"))
        timeouts = _non_negative_int(observation.get("baseline_timeout_count"))
        if successes > 0:
            feasible.append(case_id)
        elif (
            attempts > 0
            and timeouts >= attempts
            and _non_negative_screening_float(
                observation.get("baseline_timeout_max_seconds")
            )
            >= timeout_ceiling_seconds
        ):
            infeasible.append(case_id)
        else:
            unknown.append(case_id)
    status = (
        "feasible"
        if feasible
        else "infeasible"
        if case_ids and not unknown and len(infeasible) == len(case_ids)
        else "unknown"
    )
    generation_allowed = status != "infeasible"
    return {
        "schema_version": "aworld.self_evolve.screening_control_preflight.v1",
        "status": status,
        "case_count": len(case_ids),
        "feasible_case_ids": feasible,
        "infeasible_case_ids": infeasible,
        "unknown_case_ids": unknown,
        "candidate_generation_allowed": generation_allowed,
        "advisory_only": generation_allowed,
        "advisory_role": ("candidate_control_ordering" if generation_allowed else None),
        "failure_class": None if generation_allowed else "framework",
        "failure_owner": None if generation_allowed else "framework",
        "failure_scope": None if generation_allowed else "shared_run",
        "repairable": not generation_allowed,
        "code": None if generation_allowed else "baseline_controls_infeasible",
        "next_action": (
            None if generation_allowed else "repair_or_build_shared_replay_harness"
        ),
        "support_specific_qualification_required": True,
        "source": "same_harness_historical_baseline_lifecycle",
        "harness_fingerprint": (
            harness_fingerprint or _screening_control_harness_fingerprint()
        ),
        "timeout_ceiling_seconds": timeout_ceiling_seconds,
        "case_observations": {
            case_id: dict(observations.get(case_id, {}))
            for case_id in case_ids
            if observations.get(case_id)
        },
    }


def _no_op_report(
    gate_results: list[GateResult],
    iteration_reports: list[dict[str, object]],
) -> dict[str, object] | None:
    no_candidate_gate = next(
        (gate for gate in gate_results if gate.gate_name == "no_candidate"),
        None,
    )
    no_candidate_iteration = next(
        (item for item in iteration_reports if item.get("status") == "no_candidate"),
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
            item for item in iteration_reports if item.get("status") == "no_candidate"
        ],
    }


def _acceptance_confidence_report(
    gate_results: list[GateResult],
) -> dict[str, object] | None:
    for gate in gate_results:
        if gate.gate_name != "held_out_verification" or not isinstance(
            gate.details, Mapping
        ):
            continue
        details = gate.details
        verification_mode = details.get("verification_mode")
        verification_split = details.get("verification_split")
        if not isinstance(verification_mode, str) and isinstance(
            verification_split, str
        ):
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
) -> tuple[int, int, int, float, float, int, int]:
    summary = state.get("candidate_summary")
    score = float("-inf")
    if isinstance(summary, EvaluationSummary):
        candidate_score = _metric_number(summary.metrics, "score")
        if candidate_score is not None:
            score = candidate_score
    gate_results = state.get("gate_results")
    gates = tuple(gate_results) if isinstance(gate_results, (list, tuple)) else ()
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
    gate_names = {gate.gate_name for gate in gates if isinstance(gate, GateResult)}
    substantive_evaluation = failed_gate_names != {"duplicate_rejected_candidate"}
    reached_evaluation = isinstance(summary, EvaluationSummary)
    reached_replay = (
        state.get("replay_result") is not None
        or bool(gate_names & {"candidate_replay", "replay_confidence"})
        or reached_evaluation
    )
    adaptation_compiled = reached_replay or any(
        isinstance(gate, GateResult)
        and gate.gate_name == "replay_adaptation"
        and gate.passed
        for gate in gates
    )
    progress_rank = (
        3
        if reached_evaluation
        else 2
        if reached_replay
        else 1
        if adaptation_compiled
        else 0
    )
    paired_delta = _iteration_candidate_paired_delta(state, gates=gates)
    return (
        int(substantive_evaluation),
        progress_rank,
        int(paired_delta is not None),
        paired_delta if paired_delta is not None else float("-inf"),
        score,
        -failed_count,
        passed_count,
    )


def _iteration_candidate_paired_delta(
    state: Mapping[str, object],
    *,
    gates: Sequence[object],
) -> float | None:
    """Return a comparable baseline-to-candidate score effect when available.

    Candidate evaluations can use different judge baselines. Absolute score is
    therefore not a causal ranking signal across rejected candidates: a proven
    regression against a high baseline must not displace a positive paired
    effect against a lower baseline and become the Campaign checkpoint.
    """

    for gate in gates:
        if not isinstance(gate, GateResult) or gate.gate_name != "score_improvement":
            continue
        details = gate.details
        if not isinstance(details, Mapping):
            continue
        comparability = details.get("comparability")
        if (
            isinstance(comparability, Mapping)
            and comparability.get("comparable") is False
        ):
            return None
        delta = details.get("delta")
        if (
            isinstance(delta, (int, float))
            and not isinstance(delta, bool)
            and math.isfinite(float(delta))
        ):
            return float(delta)

    baseline = state.get("baseline_summary")
    candidate = state.get("candidate_summary")
    if not isinstance(baseline, EvaluationSummary) or not isinstance(
        candidate, EvaluationSummary
    ):
        return None
    baseline_score = _metric_number(baseline.metrics, "score")
    candidate_score = _metric_number(candidate.metrics, "score")
    if baseline_score is None or candidate_score is None:
        return None
    delta = candidate_score - baseline_score
    return delta if math.isfinite(delta) else None


def _candidate_generation_limit(
    *,
    replay_candidate_limit: int,
) -> int:
    return max(1, replay_candidate_limit)
