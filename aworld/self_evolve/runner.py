from __future__ import annotations

import inspect
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Callable, Any
from pathlib import Path
from typing import Mapping, Iterable

from aworld.config.conf import SelfEvolveJudgeConfig
from aworld.logs.util import logger
from aworld.self_evolve.credit_assignment import (
    TargetInventoryEntry,
    TargetSelectionReport,
    TrajectoryCreditAssigner,
    build_default_target_inventory,
)
from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.evaluation import (
    AWorldTrajectoryEvaluatorBackend,
    EvaluationBackend,
    EvaluationRequest,
    SkillCandidateOverlayBackend,
    determine_candidate_confidence,
    estimate_replay_cost,
    evaluate_baseline_and_candidate,
)
from aworld.self_evolve.gates import (
    BudgetGate,
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
    ScoreImprovementGate,
    SkillMarkdownGate,
    StoppingConditionGate,
    StoppingConditionState,
    TokenLimitGate,
    TrustProvenanceGate,
)
from aworld.self_evolve.lessons import LessonRecord, extract_lesson_records
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest, OptimizerResult
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayBackend,
    CandidateReplayResult,
    ReplayVariantResult,
    build_paired_replay_dataset,
    build_replay_request,
    load_candidate_replay_result,
)
from aworld.self_evolve.release_checks import (
    build_content_quality_diagnostics,
    build_release_checklist,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import DraftSkillTextTarget, SelfEvolveTarget, SkillTextTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
    to_json_dict,
)
from aworld.skills.compat_provider import build_compat_registry
from aworld.skills.release import mark_skill_content_verified


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
    ) -> SelfEvolveRunnerResult:
        if apply_policy not in {"proposal", "auto_verified"}:
            raise ValueError(f"unsupported apply policy: {apply_policy}")
        _emit_progress(
            self.progress_callback,
            "start",
            f"Starting self-evolve run {run_id}",
        )

        run = SelfEvolveRun(run_id=run_id, target=target.identity, status=SelfEvolveRunStatus.RUNNING)
        self.store.create_run(run)
        self.store.write_dataset_recipe(run_id, dataset.recipe)
        if target_selection_report is not None:
            self.store.write_target_selection_report(run_id, target_selection_report)
        if target_provenance is not None:
            self.store.write_target_provenance(run_id, target_provenance)

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
                "stopping_condition": {
                    "gate_name": stopping_result.gate_name,
                    "passed": stopping_result.passed,
                    "reason": stopping_result.reason,
                    "details": stopping_result.details,
                },
            }
            if target_selection_report is not None:
                report["target_selection"] = to_json_dict(target_selection_report)
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
        iteration_reports: list[dict[str, object]] = []
        iteration_states: list[dict[str, object]] = []
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
        rejected_candidate_ids = {
            feedback.variant_id
            for feedback in prior_feedback
            if feedback.metrics.get("candidate_status") == "rejected"
        }
        accepted_candidate_ids = {
            feedback.variant_id
            for feedback in prior_feedback
            if feedback.metrics.get("candidate_status") == "accepted"
        }

        for iteration_index in range(self.max_iterations):
            _emit_progress(
                self.progress_callback,
                "candidate_generation",
                f"Generating candidate iteration {iteration_index + 1}/{self.max_iterations}",
            )
            optimizer_result = await self.optimizer.propose(
                OptimizerRequest.from_dataset(
                    target=target.identity,
                    current_content=target.load_current_content(),
                    target_fingerprint=target.fingerprint_current_content(),
                    trace_packs=trace_packs,
                    validation_feedback=validation_feedback,
                    prior_feedback=prior_feedback,
                    dataset=dataset,
                    max_candidates=_candidate_generation_limit(
                        replay_candidate_limit=self.replay_candidate_limit,
                        rejected_candidate_ids=rejected_candidate_ids,
                        accepted_candidate_ids=accepted_candidate_ids,
                    ),
                )
            )
            filtered_known_duplicates = _known_duplicate_candidate_count(
                optimizer_result.candidates,
                rejected_candidate_ids=rejected_candidate_ids,
                accepted_candidate_ids=accepted_candidate_ids,
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
                    },
                }
            )
            for candidate in optimizer_result.candidates:
                all_candidates.append(candidate)
                target.preserve_proposal(self.store, run_id, candidate)
            for lineage in optimizer_result.lineage:
                self.store.write_optimizer_lineage(run_id, lineage)

            candidate_population = tuple(
                candidate
                for candidate in optimizer_result.candidates
                if candidate.candidate_id not in rejected_candidate_ids
                and candidate.candidate_id not in accepted_candidate_ids
            )[: max(1, self.replay_candidate_limit)]
            if not candidate_population:
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
                    validation_feedback = tuple(skipped_feedback)
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

            accepted_in_iteration = False
            reusable_baseline_replay_dir: str | None = None
            for candidate_index, iteration_candidate in enumerate(candidate_population):
                state, report_item, validation_feedback = await self._evaluate_iteration_candidate(
                    run_id=run_id,
                    target=target,
                    dataset=dataset,
                    candidate=iteration_candidate,
                    apply_policy=apply_policy,
                    target_provenance=target_provenance,
                    iteration_number=iteration_index + 1,
                    candidate_number=candidate_index + 1,
                    candidate_count=len(candidate_population),
                    rejected_candidate_ids=rejected_candidate_ids,
                    accepted_candidate_ids=accepted_candidate_ids,
                    baseline_replay_dir=reusable_baseline_replay_dir,
                )
                iteration_reports.append(report_item)
                iteration_states.append(state)
                if reusable_baseline_replay_dir is None:
                    replay_state = state.get("replay_result")
                    if isinstance(replay_state, CandidateReplayResult) and replay_state.baseline.succeeded:
                        reusable_baseline_replay_dir = _baseline_replay_artifact_dir(
                            replay_state
                        )
                failed_gates = [
                    gate for gate in state["gate_results"] if not gate.passed
                ]
                if failed_gates:
                    rejected_candidate_ids.add(iteration_candidate.candidate_id)
                if state["status"] == "accepted":
                    accepted_in_iteration = True
                    break
            if accepted_in_iteration:
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
        elif apply_policy == "auto_verified":
            gate_results.append(
                GateResult(
                    gate_name="auto_verified_evaluation",
                    passed=False,
                    reason="auto_verified apply policy requires a candidate",
                )
            )

        post_apply: dict[str, object] | None = None
        final_status = SelfEvolveRunStatus.SUCCEEDED
        if apply_policy == "auto_verified":
            if selected_candidate is None:
                final_status = SelfEvolveRunStatus.REJECTED
            else:
                failed_gates = [gate for gate in gate_results if not gate.passed]
                if failed_gates:
                    final_status = SelfEvolveRunStatus.REJECTED
                else:
                    post_apply = await self._apply_auto_verified(run_id, target, selected_candidate)
                    if post_apply["status"] != "accepted":
                        final_status = SelfEvolveRunStatus.REJECTED

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
            "optimizer_diagnostics": (
                optimizer_diagnostics[0]["diagnostics"]
                if len(optimizer_diagnostics) == 1
                else {"iterations": optimizer_diagnostics}
            ),
            "prior_feedback_count": len(prior_feedback),
            "iterations": iteration_reports,
        }
        if target_selection_report is not None:
            report["target_selection"] = to_json_dict(target_selection_report)
        if post_apply is not None:
            report["post_apply"] = post_apply
        if baseline_summary is not None:
            report["baseline_metrics"] = dict(baseline_summary.metrics)
        if candidate_summary is not None:
            report["candidate_metrics"] = dict(candidate_summary.metrics)
        if held_out_summary is not None:
            report["held_out_metrics"] = dict(held_out_summary.metrics)
        if replay_result is not None:
            report["replay"] = _replay_report(replay_result)
            report["replay_path"] = _replay_artifact_path(replay_result)
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
            report["release_checklist"] = build_release_checklist(
                apply_policy=apply_policy,
                gate_results=report["gate_results"],
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
        )
        if lesson_records:
            lessons_path = self.store.write_lesson_records(run_id, lesson_records)
            report["lessons"] = {
                "path": str(lessons_path),
                "count": len(lesson_records),
                "types": _lesson_type_counts(lesson_records),
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

    async def _evaluate_iteration_candidate(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        apply_policy: str,
        target_provenance: TargetProvenance | None,
        iteration_number: int,
        candidate_number: int,
        candidate_count: int,
        rejected_candidate_ids: set[str],
        accepted_candidate_ids: set[str],
        baseline_replay_dir: str | None = None,
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
                status="rejected",
            )
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
        replay_result, replay_dataset, replay_gate = await self._replay_selected_candidate(
            run_id=run_id,
            target=target,
            dataset=dataset,
            selected_candidate=candidate,
            apply_policy=apply_policy,
            baseline_replay_dir=baseline_replay_dir,
        )
        if replay_gate is not None:
            gate_results.append(replay_gate)
        replay_confidence_gate = _replay_confidence_gate(
            replay_result,
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
                            held_out_summary = await self.evaluation_backend.evaluate_variant(
                                EvaluationRequest(
                                    variant_id=candidate.candidate_id,
                                    candidate=candidate,
                                    dataset=evaluation_dataset,
                                    dataset_split="held_out",
                                    artifact_namespace=run_id,
                                )
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

    async def _replay_selected_candidate(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        selected_candidate: CandidateVariant,
        apply_policy: str,
        baseline_replay_dir: str | None = None,
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
        _emit_progress(
            self.progress_callback,
            "candidate_replay",
            (
                "Running paired replay "
                f"(baseline x{self.baseline_replay_repetitions}, "
                f"candidate x{self.candidate_replay_repetitions})"
            ),
        )

        overlay = create_candidate_skill_overlay(
            workspace_root=self.store.workspace_root,
            run_id=run_id,
            candidate=selected_candidate,
            target_skill_path=target.identity.path,
            baseline_skill_roots=getattr(target, "baseline_skill_roots", ()),
        )
        request = build_replay_request(
            run_id=run_id,
            workspace_root=self.store.workspace_root,
            target=target.identity,
            candidate=selected_candidate,
            overlay_skill_root=overlay.shadow_root,
            dataset=dataset,
            agent=self.replay_agent,
            timeout_seconds=self.replay_timeout_seconds,
            max_steps=self.replay_max_steps,
            max_tokens=self.max_run_tokens,
            baseline_repetitions=self.baseline_replay_repetitions,
            candidate_repetitions=self.candidate_replay_repetitions,
            baseline_replay_dir=baseline_replay_dir,
        )
        replay_result = await self.candidate_replay_backend.replay_candidate(
            request,
            candidate=selected_candidate,
            dataset=dataset,
        )
        if not replay_result.succeeded:
            return (
                replay_result,
                None,
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason="candidate replay did not produce successful paired trajectories",
                    details=_replay_gate_details(replay_result),
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
                reason="candidate replay produced successful paired trajectories",
                details=_replay_gate_details(replay_result),
            ),
        )

    async def _apply_auto_verified(
        self,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
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
            details={"candidate_id": candidate.candidate_id},
        )
        applied_candidate = candidate
        if target.identity.target_type == "skill":
            applied_candidate = replace(
                candidate,
                content=mark_skill_content_verified(
                    candidate.content,
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                ),
            )
        target.apply_candidate(applied_candidate.content)
        summary = self.post_apply_evaluator(applied_candidate)
        if inspect.isawaitable(summary):
            summary = await summary
        if not isinstance(summary, EvaluationSummary):
            raise ValueError("post_apply_evaluator must return EvaluationSummary")
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
                refresh_result = self.runtime_registry_refresher(applied_candidate)
                if inspect.isawaitable(refresh_result):
                    refresh_result = await refresh_result
            self.store.update_apply_journal(
                journal_path,
                status="accepted",
                details={"post_apply_passed": True, "release_state": "verified"},
            )
            result = {
                "status": "accepted",
                "metrics": dict(summary.metrics),
                "dataset_split": summary.dataset_split,
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
                "release_state": "verified",
            }
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
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None = None,
    replay_enabled: bool = False,
    candidate_replay_backend: CandidateReplayBackend | None = None,
    replay_timeout_seconds: int = 600,
    replay_max_steps: int | None = 1,
    replay_candidate_limit: int = 2,
    baseline_replay_repetitions: int = 1,
    candidate_replay_repetitions: int = 1,
    replay_stability_margin: float = 0.0,
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
    progress_callback: Callable[[str, str], Any] | None = None,
) -> Mapping[str, Any]:
    if apply_policy not in {"proposal", "auto_verified"}:
        raise ValueError(f"unsupported apply policy: {apply_policy}")
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
    trace_packs = tuple(
        case.trace_pack for case in built_dataset.cases if case.trace_pack is not None
    )
    store = FilesystemSelfEvolveStore(workspace_root)
    target_selection_report: TargetSelectionReport | None = None
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
        target_selection_report, inventory_entry = _infer_target_from_trace_packs(
            trace_packs,
            workspace_root=workspace_root,
        )
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
        if inventory_entry is not None:
            target_provenance = inventory_entry.provenance
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

    async def _cli_default_mutation(prompt: str) -> dict[str, str]:
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

    import asyncio

    result = asyncio.run(
        SelfEvolveRunner(
            store=store,
            optimizer=TraceReflectiveLLMMutator(mutate_text=_cli_default_mutation),
            evaluation_backend=evaluation_backend,
            post_apply_evaluator=post_apply_evaluator,
            min_score_delta=min_score_delta,
            max_iterations=iterations or 1,
            min_eval_cases=min_eval_cases,
            judge_repetitions=judge_repetitions,
            max_run_tokens=max_run_tokens,
            auto_apply_target_types=auto_apply_target_types,
            replay_enabled=replay_enabled,
            candidate_replay_backend=candidate_replay_backend,
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
        ).run_explicit_target(
            run_id=run_id,
            target=target_adapter,
            dataset=built_dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
            target_selection_report=target_selection_report,
            target_provenance=target_provenance,
        )
    )
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
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if config.mode == "custom_agent":
        if not config.agent_id:
            raise ValueError("custom_agent self-evolve evaluator requires agent_id")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_agent_name=config.agent_id,
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        )
    if config.mode == "backend_ref":
        if not config.backend_ref:
            raise ValueError("backend_ref self-evolve evaluator requires backend_ref")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_backend_ref=config.backend_ref,
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
) -> Mapping[str, Any]:
    store = FilesystemSelfEvolveStore(workspace_root)
    source_run_path = _resolve_stored_run_path(store, from_run)
    source_run_id = source_run_path.name
    source_report = _load_json_mapping(source_run_path / "report.json")
    candidate_id = _stored_selected_candidate_id(source_report)
    candidate = _load_candidate_variant(source_run_path / "candidates" / f"{candidate_id}.json")
    replay_path = source_run_path / "replay" / candidate.candidate_id
    replay_result = load_candidate_replay_result(replay_path)
    if not replay_result.succeeded:
        raise ValueError(
            "stored replay did not produce successful paired trajectories; "
            "rerun the full optimize flow instead"
        )

    source_config, split_seed = _source_config_from_stored_dataset_recipe(
        source_run_path / "dataset_recipe.json"
    )
    built_dataset = build_dataset_from_source(
        source_config,
        current_trajectory=None,
        task_id=task,
        split_seed=split_seed,
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

    import asyncio

    result = asyncio.run(
        SelfEvolveRunner(
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
        ).run_explicit_target(
            run_id=run_id,
            target=target_adapter,
            dataset=built_dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
            target_selection_report=target_selection_report,
        )
    )
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
    )


def _rerun_cli_run_id(source_run_id: str, candidate_id: str) -> str:
    return (
        "cli-rerun-"
        f"{abs(hash((source_run_id, candidate_id, 'evaluator'))) % 10**12:012d}"
    )


def _replay_report(replay_result: CandidateReplayResult) -> dict[str, object]:
    return {
        "request": {
            "run_id": replay_result.request.run_id,
            "task_id": replay_result.request.task_id,
            "candidate_id": replay_result.request.candidate_id,
            "overlay_skill_root": replay_result.request.overlay_skill_root,
            "baseline_replay_dir": replay_result.request.baseline_replay_dir,
            "timeout_seconds": replay_result.request.timeout_seconds,
            "max_steps": replay_result.request.max_steps,
            "max_tokens": replay_result.request.max_tokens,
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
    if replay_result.request.baseline_replay_dir:
        return replay_result.request.baseline_replay_dir
    return str(Path(_replay_artifact_path(replay_result)) / "baseline")


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
            merged_metrics[metric_name] = replay_metrics[metric_name]
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


def _replay_gate_details(replay_result: CandidateReplayResult) -> dict[str, object]:
    return {
        "baseline_status": replay_result.baseline.status,
        "candidate_status": replay_result.candidate.status,
        "baseline_failure": replay_result.baseline.failure,
        "candidate_failure": replay_result.candidate.failure,
    }


def _replay_confidence_gate(
    replay_result: CandidateReplayResult | None,
    *,
    apply_policy: str,
) -> GateResult | None:
    if replay_result is None or apply_policy != "auto_verified":
        return None
    baseline_source = replay_result.baseline.metrics.get("replay_source")
    candidate_repetitions = replay_result.candidate.metrics.get("repetition_count")
    candidate_successful_repetitions = replay_result.candidate.metrics.get(
        "successful_repetition_count"
    )
    candidate_failed_repetitions = replay_result.candidate.metrics.get(
        "failed_repetition_count"
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
                "baseline_replay_source": baseline_source,
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
                "baseline_replay_source": baseline_source,
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
        details={
            "baseline_replay_source": baseline_source,
            "candidate_repetition_count": candidate_repetitions,
            "candidate_successful_repetition_count": candidate_successful_repetitions,
        },
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
                    **comparison_metrics,
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
                "failed_gates": [gate.gate_name for gate in failed_gates],
                "candidate_status": "rejected" if failed_gates else "accepted",
            },
            dataset_split="validation",
        ),
    )


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
    return max(iteration_states, key=_iteration_candidate_score)


def _iteration_candidate_score(state: Mapping[str, object]) -> float:
    summary = state.get("candidate_summary")
    if isinstance(summary, EvaluationSummary):
        score = _metric_number(summary.metrics, "score")
        if score is not None:
            return score
    return float("-inf")


def _candidate_generation_limit(
    *,
    replay_candidate_limit: int,
    rejected_candidate_ids: set[str],
    accepted_candidate_ids: set[str],
) -> int:
    replay_limit = max(1, replay_candidate_limit)
    known_duplicate_count = len(rejected_candidate_ids) + len(accepted_candidate_ids)
    return min(max(replay_limit + known_duplicate_count, replay_limit), replay_limit * 3)


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


def _feedback_guidance_from_mutation_prompt(prompt: str | None) -> list[str]:
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
    prior_feedback = payload.get("prior_feedback")
    if isinstance(prior_feedback, list):
        feedback_items.extend(prior_feedback[:3])
    validation_feedback = payload.get("validation_feedback")
    if isinstance(validation_feedback, list):
        feedback_items.extend(validation_feedback[-3:])
    if not feedback_items:
        return []

    guidance: list[str] = []
    for item in feedback_items[:3]:
        if not isinstance(item, Mapping):
            continue
        summary = item.get("feedback_summary")
        summary = summary if isinstance(summary, Mapping) else item
        metrics = summary.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        evidence = summary.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else metrics
        parts = []
        score = metrics.get("score")
        if isinstance(score, (int, float)):
            parts.append(f"score={score}")
        failed_gates = summary.get("failed_gates")
        if not isinstance(failed_gates, list):
            failed_gates = metrics.get("failed_gates")
        if isinstance(failed_gates, list) and failed_gates:
            parts.append(
                "failed_gates="
                + ",".join(str(gate) for gate in failed_gates if gate)
            )
        if isinstance(evidence.get("evidence_compacted"), bool):
            parts.append(f"evidence_compacted={evidence['evidence_compacted']}")
        if isinstance(evidence.get("evidence_incomplete"), bool):
            parts.append(f"evidence_incomplete={evidence['evidence_incomplete']}")
        evidence_issues = evidence.get("issues")
        if not isinstance(evidence_issues, list):
            evidence_issues = metrics.get("evidence_issues")
        if isinstance(evidence_issues, list) and evidence_issues:
            issue_text = "; ".join(
                str(issue).strip()
                for issue in evidence_issues[:2]
                if str(issue).strip()
            )
            if issue_text:
                parts.append(f"evidence_issues={issue_text}")
        replay_failure_reasons = evidence.get("replay_failure_reasons")
        if not isinstance(replay_failure_reasons, list):
            replay_failure_reasons = metrics.get("replay_failure_reasons")
        if isinstance(replay_failure_reasons, list) and replay_failure_reasons:
            reason_text = ",".join(
                str(reason).strip()
                for reason in replay_failure_reasons[:3]
                if str(reason).strip()
            )
            if reason_text:
                parts.append(f"replay_failure_reasons={reason_text}")
        replay_failure_types = evidence.get("replay_failure_types")
        if not isinstance(replay_failure_types, list):
            replay_failure_types = metrics.get("replay_failure_types")
        if isinstance(replay_failure_types, list) and replay_failure_types:
            type_text = ",".join(
                str(failure_type).strip()
                for failure_type in replay_failure_types[:3]
                if str(failure_type).strip()
            )
            if type_text:
                parts.append(f"replay_failure_types={type_text}")
        required_behaviors = summary.get("required_behaviors")
        if isinstance(required_behaviors, list) and required_behaviors:
            behavior_text = ",".join(
                str(behavior)
                for behavior in required_behaviors[:5]
                if str(behavior).strip()
            )
            if behavior_text:
                parts.append(f"required_behaviors={behavior_text}")
        if not parts:
            continue
        split = summary.get("dataset_split") or item.get("dataset_split") or "validation"
        variant_id = summary.get("variant_id") or item.get("variant_id") or "candidate"
        guidance.append(f"{variant_id} on {split}: {'; '.join(parts)}")
    return guidance


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
    feedback_guidance = _feedback_guidance_from_mutation_prompt(mutation_prompt)
    evidence_preservation_issue = _feedback_has_evidence_preservation_issue(mutation_prompt)
    scope_or_cost_issue = _feedback_has_scope_or_cost_issue(mutation_prompt)
    high_baseline_regression_issue = _feedback_has_high_baseline_regression_issue(
        mutation_prompt
    )
    repair_plan = _feedback_repair_plan_from_mutation_prompt(mutation_prompt)
    population_strategy = _population_strategy_from_mutation_prompt(mutation_prompt)
    if high_baseline_regression_issue:
        return _default_cli_high_baseline_delta_candidate(
            current_content=current_content,
            trace_packs=trace_packs,
            repair_plan=repair_plan,
            population_strategy=population_strategy,
        )
    if not trace_packs and not feedback_guidance:
        return current_content

    evidence_ids = [
        step.evidence_id
        for trace_pack in trace_packs[:3]
        for step in trace_pack.steps[:4]
    ]
    task_ids = [trace_pack.task_id for trace_pack in trace_packs[:3]]
    guidance = [
        "Use trajectory evidence before choosing or repeating tool actions.",
        (
            "When a tool path fails or repeats, record the observed failure and "
            "switch to an alternate evidence source before finalizing."
        ),
    ]
    if evidence_preservation_issue:
        guidance.extend(
            [
                "Evidence preservation requirements:",
                (
                    "Do not stream large raw pages, full HTML, large JSON, or long tool outputs "
                    "directly into the conversation."
                ),
                (
                    "For large or unknown-size sources, avoid raw dumps and line-based previews; "
                    "they can still emit huge single-line content and trigger compaction."
                ),
                (
                    "Persist raw evidence to a file or artifact first, then return only "
                    "small, verifiable extracts with source fields and offsets."
                ),
                (
                    "Emit a bounded structured summary instead of raw source material; include "
                    "only source identifiers, byte or line ranges when available, and short excerpts "
                    "needed to support the final answer."
                ),
                (
                    "If a tool result is compacted, truncated, schema-invalid, or too large to "
                    "inspect, treat that attempt as unusable evidence and switch to an artifact-first "
                    "or narrower extraction strategy before answering."
                ),
                (
                    "Maintain an evidence ledger mapping each important claim to a non-compacted "
                    "extract, source location, or artifact reference."
                ),
                (
                    "Before finalizing, verify that every concrete claim is supported by "
                    "non-compacted evidence captured in the trajectory; do a claim-by-claim check "
                    "and omit claims that cannot be verified."
                ),
            ]
        )
    if scope_or_cost_issue:
        guidance.extend(
            [
                "Scope and cost control requirements:",
                (
                    "Prefer fewer verified claims over broad synthesis when prior evaluation "
                    "shows lower score, lower verifiability, or higher cost."
                ),
                (
                    "Do not expand answer breadth until each concrete claim is tied to a "
                    "non-compacted source excerpt, artifact reference, or structured field."
                ),
                (
                    "Optimize verifiability per evidence block: each captured block should "
                    "support a specific final-answer claim or be omitted."
                ),
                (
                    "Avoid collecting more evidence without a verifiability gain; stop expanding "
                    "once the required claims are supported."
                ),
                (
                    "Cap evidence acquisition and summarization cost by using the smallest "
                    "bounded extracts that can support the requested answer."
                ),
                (
                    "Plan the shortest viable evidence path before tool use; choose the "
                    "fewest actions likely to produce verifiable bounded evidence."
                ),
                (
                    "Set a small evidence budget for attempts, sources, and final claims; "
                    "spend new calls only when they add support for an uncovered claim."
                ),
                (
                    "Do not repeat a failed or low-yield evidence path; switch strategy "
                    "after one failed, compacted, or unsupported attempt."
                ),
                (
                    "Stop after sufficient verified evidence is captured, then answer only "
                    "with claims covered by the evidence ledger."
                ),
            ]
        )
    if repair_plan["acceptance_criteria"]:
        guidance.extend(
            [
                "Verified evidence acceptance criteria:",
                (
                    "Every final factual claim must have non-compacted support in a "
                    "bounded extract, artifact reference, structured field, or source span."
                ),
                (
                    "The evidence manifest must have no invalid entries; each entry must "
                    "identify a source and include bounded evidence payload."
                ),
                (
                    "Do not finalize if these criteria are not met; instead narrow the "
                    "answer to only verified claims or report the missing evidence."
                ),
            ]
        )
    if repair_plan["issues"] & {
        "replay_timeout",
        "replay_evidence_quality_failure",
        "replay_trajectory_capture_failure",
    }:
        guidance.extend(
            [
                "Replay failure recovery requirements:",
                (
                    "After one failed replay evidence attempt, change the evidence strategy "
                    "instead of repeating the same path."
                ),
                (
                    "Do not finalize after a failed evidence retry; first produce a bounded "
                    "missing-evidence report or narrow the answer to verified claims only."
                ),
                (
                    "If replay succeeds but returns no trajectory evidence, treat the run "
                    "as unusable and change strategy."
                ),
                (
                    "Do not finalize without captured trajectory evidence that includes "
                    "tool evidence and final state."
                ),
                (
                    "The replay should complete without replay evidence failures before the "
                    "candidate can be considered ready for verified apply."
                ),
            ]
        )
    if (
        "compacted_tool_argument_replay" in repair_plan["issues"]
        or repair_plan["actions"]
        & {
            "regenerate_compacted_tool_arguments",
            "switch_to_artifact_read_after_invalid_tool_argument",
            "stop_repeating_invalid_tool_calls",
        }
    ):
        guidance.extend(
            [
                "Tool argument replay hygiene requirements:",
                (
                    "Do not execute replay placeholders, compacted string fields, or "
                    "schema-invalid argument objects as real tool inputs."
                ),
                (
                    "Regenerate the smallest schema-valid tool arguments from the current "
                    "task context before retrying a tool path."
                ),
                (
                    "If the original argument was compacted, read a saved artifact or use "
                    "a narrower extraction path instead of replaying the placeholder."
                ),
                (
                    "After one invalid tool-argument failure, stop repeating the same call "
                    "and switch strategy before continuing."
                ),
            ]
        )
    if high_baseline_regression_issue:
        guidance.extend(
            [
                "High-baseline improvement requirements:",
                (
                    "Preserve baseline strengths first: keep any existing behavior that already "
                    "earns high groundedness, relevance, completeness, and completion scores."
                ),
                (
                    "Define an explicit behavior delta before tool use: name the small "
                    "execution behavior that will differ from the baseline and why it should "
                    "raise score, compliance, efficiency, or robustness."
                ),
                (
                    "Prefer one targeted change over broad rewrites; do not rewrite broad "
                    "strategy when the baseline is already strong."
                ),
                (
                    "Use a pre-final acceptance check: candidate_score exceeds baseline_score "
                    "only if the answer is at least as grounded and complete while improving "
                    "one weaker dimension such as compliance, efficiency, or robustness."
                ),
                (
                    "If no concrete behavior delta is available, preserve the baseline strategy "
                    "and avoid adding extra instructions that only increase complexity."
                ),
            ]
        )

    section = [
        "## Self-Evolve Trace Guidance",
        "",
        f"- Source task ids: {', '.join(task_ids)}",
        f"- Evidence steps: {', '.join(evidence_ids)}",
    ]
    section.extend(f"- {item}" for item in guidance)
    if feedback_guidance:
        section.append("- Previous validation feedback:")
        section.extend(f"  - {item}" for item in feedback_guidance)

    heading = "\n## Self-Evolve Trace Guidance\n"
    prefix = current_content.rstrip()
    if heading in current_content:
        prefix = current_content.split(heading, 1)[0].rstrip()
    return prefix + "\n\n" + "\n".join(section) + "\n"


def _default_cli_high_baseline_delta_candidate(
    *,
    current_content: str,
    trace_packs: tuple[TracePack, ...],
    repair_plan: Mapping[str, set[str]],
    population_strategy: str = "conservative_preserve_then_delta",
) -> str:
    evidence_ids = [
        step.evidence_id
        for trace_pack in trace_packs[:2]
        for step in trace_pack.steps[:2]
    ]
    task_ids = [trace_pack.task_id for trace_pack in trace_packs[:2]]
    issues = repair_plan.get("issues", set())
    actions = repair_plan.get("actions", set())
    acceptance_criteria = repair_plan.get("acceptance_criteria", set())

    behavior_delta = (
        "Before adding new evidence paths or expanding the final answer, run one "
        "pre-final check over the already captured artifacts: every retained claim "
        "must map to a bounded source span or artifact reference, and any invalid "
        "manifest entry must be repaired with a bounded evidence payload or the "
        "unsupported claim must be omitted without reducing supported answer completeness."
    )
    strategy_focus = "preserve the high-scoring baseline and add only one minimal delta"
    if (
        "score_or_efficiency_regression" in issues
        or "stop_after_sufficient_verified_evidence" in actions
    ):
        behavior_delta = (
            "After the minimum evidence needed for the requested answer is captured, "
            "stop broad exploration and only add another evidence step when it covers "
            "a specific unsupported claim or repairs a concrete verification failure."
        )
        strategy_focus = "avoid extra steps unless they repair a named verification gap"
    if "invalid_evidence_manifest" in issues or "write_valid_bounded_evidence_manifest" in actions:
        behavior_delta = (
            "Before finalizing, validate the evidence manifest entries and repair only "
            "schema-invalid or unsupported references; do not broaden the answer or "
            "collect unrelated evidence while repairing the manifest. Each repaired "
            "entry must include a bounded evidence payload such as excerpt, "
            "structured_extract, or source_span; fields_used is only an index and "
            "cannot replace that payload. Do not narrow the answer or omit supported "
            "claims solely to make the manifest easier to validate."
        )
        strategy_focus = "repair evidence references without changing supported answer content"
    if (
        "compacted_tool_argument_replay" in issues
        or actions
        & {
            "regenerate_compacted_tool_arguments",
            "switch_to_artifact_read_after_invalid_tool_argument",
            "stop_repeating_invalid_tool_calls",
        }
    ):
        behavior_delta = (
            "Before retrying any failed tool path, verify that the tool arguments are "
            "schema-valid real inputs and not replay placeholders or compacted string "
            "fields. If a required argument was compacted, regenerate the smallest valid "
            "argument from the current task context or read the saved artifact; after one "
            "invalid-argument failure, stop repeating that call and switch strategy."
        )
        strategy_focus = "preserve baseline behavior while preventing invalid replay tool arguments"
    if population_strategy == "evidence_integrity_delta":
        behavior_delta = (
            "Use an artifact-first evidence contract before finalizing: persist source "
            "material as artifacts, write a manifest entry only when it has a bounded "
            "payload, and treat fields_used, artifact names, or source ids as indexes "
            "rather than evidence. If the bounded payload is missing or compacted, "
            "retry with a narrower extraction or mark the claim unsupported instead of "
            "answering from memory."
        )
        strategy_focus = "make evidence validity the only changed execution behavior"
    elif population_strategy == "score_dimension_repair_delta":
        regressed_dimensions = [
            action.removeprefix("restore_")
            for action in sorted(actions)
            if action.startswith("restore_")
        ]
        dimension_text = (
            ", ".join(regressed_dimensions)
            if regressed_dimensions
            else "A1_groundedness, A2_completeness, and B2_efficiency"
        )
        behavior_delta = (
            "Before finalizing, compare the draft answer against the baseline-strength "
            f"dimensions ({dimension_text}). Restore any baseline-supported claim whose "
            "removal would lower completeness, require a source span for claims that "
            "affect groundedness, and avoid any additional step that does not improve "
            "one of those regressed dimensions."
        )
        strategy_focus = f"restore {dimension_text} without broadening task scope"
    if (
        "high_baseline_without_efficiency_gain" in issues
        or "replace_broad_validation_with_efficiency_delta" in actions
        or "candidate_uses_no_more_steps_than_baseline" in acceptance_criteria
    ):
        behavior_delta = (
            "Use a high-baseline efficiency delta: preserve the same claim set, answer "
            "structure, and source references as the baseline, but complete with no more "
            "tool calls or evidence steps than the baseline. Do not add pre-final "
            "comparison passes, broad re-validation loops, or new external claims; only "
            "reuse already captured bounded artifacts and remove unsupported claims whose "
            "source links cannot be preserved."
        )
        strategy_focus = "improve high-baseline runs only through fewer steps at unchanged quality"

    acceptance_check = (
        "candidate_score exceeds baseline_score, A1_groundedness and A2_completeness "
        "stay no worse than baseline, answer completeness is preserved, "
        "evidence_manifest_invalid_entry_count == 0, and every manifest entry includes "
        "bounded evidence payload."
    )
    if "candidate_score_exceeds_baseline_score" in acceptance_criteria:
        acceptance_check = (
            "candidate_score exceeds baseline_score while preserving baseline "
            "A1_groundedness, A2_completeness, answer completeness, and relevance; "
            "evidence_manifest_invalid_entry_count == 0; otherwise keep the baseline behavior."
        )
    if "candidate_uses_no_more_steps_than_baseline" in acceptance_criteria:
        acceptance_check = (
            "candidate_score exceeds baseline_score; candidate_uses_no_more_steps_than_baseline; "
            "candidate_groundedness_is_no_worse_than_baseline; answer completeness and source "
            "references stay no worse than baseline; otherwise keep the baseline behavior."
        )

    section = [
        "## Self-Evolve Targeted Delta",
        "",
        f"### Population strategy: {population_strategy}",
        f"- Focus: {strategy_focus}.",
        "",
        "### Preserve",
        (
            "- Keep the existing high-scoring evidence acquisition, answer structure, "
            "and completion behavior unchanged."
        ),
        (
            "- Do not rewrite broad strategy or add extra evidence collection unless "
            "it addresses a concrete failed check."
        ),
        (
            "- Preserve A1_groundedness, A2_completeness, and answer completeness; "
            "do not narrow the answer, and do not omit supported claims unless a "
            "claim lacks non-compacted evidence."
        ),
        "",
        "### Behavior delta",
        f"- {behavior_delta}",
        (
            "- When writing evidence_manifest.jsonl, every entry must include bounded "
            "evidence payload: use excerpt, structured_extract, or source_span. "
            "fields_used can help describe selected fields, but it cannot replace "
            "the bounded evidence payload."
        ),
        "",
        "### Acceptance check",
        f"- {acceptance_check}",
    ]
    if task_ids:
        section.extend(
            ["", "### Trace scope", f"- Source task ids: {', '.join(task_ids)}"]
        )
    if evidence_ids:
        section.append(f"- Evidence steps: {', '.join(evidence_ids)}")

    heading = "\n## Self-Evolve Targeted Delta\n"
    prefix = current_content.rstrip()
    if heading in current_content:
        prefix = current_content.split(heading, 1)[0].rstrip()
    return prefix + "\n\n" + "\n".join(section) + "\n"


def _target_from_cli_ref(
    target: str,
    *,
    workspace_root: str | Path,
    allow_auto_apply: bool = False,
) -> SelfEvolveTarget:
    target_type, _, target_id = target.partition(":")
    if target_type != "skill" or not target_id:
        raise NotImplementedError(f"CLI target adapter is not implemented for {target!r}")
    return _skill_target_from_id(
        target_id,
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
) -> list[GateResult]:
    results = [
        NoopCandidateGate().evaluate(current_content=current_content, candidate=candidate),
        MalformedCandidateGate().evaluate(candidate),
        TokenLimitGate(max_chars=max_chars).evaluate(candidate),
        ProtectedPathGate(workspace_root=workspace_root).evaluate(candidate),
        ExternalCodeEvolutionGate().evaluate(candidate),
    ]
    if candidate.target.target_type == "skill":
        results.append(SkillMarkdownGate().evaluate(candidate))
    if target_provenance is not None:
        results.append(TrustProvenanceGate().evaluate(target_provenance))
    return results


def _target_from_ref(
    target_ref: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path,
    allow_auto_apply: bool = False,
) -> SelfEvolveTarget:
    if target_ref.target_type == "skill":
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
    raise NotImplementedError(
        "target inference selected "
        f"{target_ref.target_type}:{target_ref.target_id}, but that target adapter "
        "is not implemented for phase 1 CLI runs"
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


def _infer_target_from_trace_packs(
    trace_packs: tuple[TracePack, ...],
    *,
    workspace_root: str | Path,
) -> tuple[TargetSelectionReport, TargetInventoryEntry | None]:
    if not trace_packs:
        raise ValueError("target inference requires trajectory evidence")

    inventory = build_default_target_inventory(workspace_root)
    assigner = TrajectoryCreditAssigner(inventory=inventory)
    reports = [assigner.assign(trace_pack) for trace_pack in trace_packs]
    best_report = max(
        reports,
        key=lambda item: (
            item.selected_target is not None,
            item.confidence,
            _target_selection_priority(item),
        ),
    )
    if best_report.selected_target is not None:
        return best_report, inventory.find(
            best_report.selected_target.target_type,
            best_report.selected_target.target_id,
        )
    return best_report, None


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
) -> TargetSelectionReport | None:
    if not trace_packs:
        return None
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
    )


def _inferred_target_confident_for_auto_apply(report: TargetSelectionReport) -> bool:
    if "new_skill_candidate" in report.signals and report.selected_target is not None:
        return report.selected_target.target_type == "skill"
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
    report_path = store.write_report(
        run_id,
        {
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
        },
    )
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
    report_path = store.write_report(
        run_id,
        {
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
            "unsupported_target": {
                "target_ref": _target_ref_text(target),
                "reason": reason,
            },
        },
    )
    summary = {
        "report_path": str(report_path),
        "target_selection_path": str(target_selection_path),
        "best_candidate_id": None,
        "run_id": run_id,
        "status": run.status.value,
    }
    if target_provenance_path is not None:
        summary["target_provenance_path"] = str(target_provenance_path)
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
