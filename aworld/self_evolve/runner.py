from __future__ import annotations

import inspect
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Callable, Any
from pathlib import Path
from typing import Mapping, Iterable

from aworld.config.conf import SelfEvolveJudgeConfig
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
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayBackend,
    CandidateReplayResult,
    build_paired_replay_dataset,
    build_replay_request,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget, SkillTextTarget
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
        replay_candidate_limit: int = 1,
        baseline_replay_repetitions: int = 1,
        candidate_replay_repetitions: int = 1,
        replay_stability_margin: float = 0.0,
        replay_agent: str | None = None,
        runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
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

        for iteration_index in range(self.max_iterations):
            optimizer_result = await self.optimizer.propose(
                OptimizerRequest.from_dataset(
                    target=target.identity,
                    current_content=target.load_current_content(),
                    target_fingerprint=target.fingerprint_current_content(),
                    trace_packs=trace_packs,
                    validation_feedback=validation_feedback,
                    dataset=dataset,
                )
            )
            optimizer_diagnostics.append(
                {
                    "iteration": iteration_index + 1,
                    "candidate_ids": [
                        candidate.candidate_id for candidate in optimizer_result.candidates
                    ],
                    "diagnostics": dict(optimizer_result.diagnostics),
                }
            )
            for candidate in optimizer_result.candidates:
                all_candidates.append(candidate)
                target.preserve_proposal(self.store, run_id, candidate)
            for lineage in optimizer_result.lineage:
                self.store.write_optimizer_lineage(run_id, lineage)

            iteration_candidate = (
                optimizer_result.candidates[0] if optimizer_result.candidates else None
            )
            iteration_baseline_summary: EvaluationSummary | None = None
            iteration_candidate_summary: EvaluationSummary | None = None
            iteration_held_out_summary: EvaluationSummary | None = None
            iteration_replay_result: CandidateReplayResult | None = None
            iteration_replay_dataset: SelfEvolveDataset | None = None
            iteration_gate_results: list[GateResult] = []
            if iteration_candidate is None:
                iteration_reports.append(
                    {
                        "iteration": iteration_index + 1,
                        "candidate_id": None,
                        "status": "no_candidate",
                        "failed_gates": [],
                    }
                )
                continue

            current_content = target.load_current_content()
            iteration_gate_results.extend(
                _candidate_gate_results(
                    iteration_candidate,
                    current_content=current_content,
                    workspace_root=self.store.workspace_root,
                    max_chars=self.max_run_tokens,
                    target_provenance=target_provenance,
                )
            )
            iteration_gate_results.append(
                BudgetGate().evaluate(
                    estimate_replay_cost(
                        dataset=dataset,
                        candidate_count=len(optimizer_result.candidates),
                        judge_repetitions=self.judge_repetitions,
                        baseline_repetitions=self.baseline_replay_repetitions,
                        candidate_repetitions=self.candidate_replay_repetitions,
                        replay_candidate_limit=self.replay_candidate_limit,
                        max_run_tokens=self.max_run_tokens,
                    )
                )
            )
            (
                iteration_replay_result,
                iteration_replay_dataset,
                replay_gate,
            ) = await self._replay_selected_candidate(
                run_id=run_id,
                target=target,
                dataset=dataset,
                selected_candidate=iteration_candidate,
                apply_policy=apply_policy,
            )
            if replay_gate is not None:
                iteration_gate_results.append(replay_gate)
            replay_confidence_gate = _replay_confidence_gate(
                iteration_replay_result,
                apply_policy=apply_policy,
            )
            if replay_confidence_gate is not None:
                iteration_gate_results.append(replay_confidence_gate)

            evaluation_dataset = iteration_replay_dataset or dataset
            if self.evaluation_backend is not None:
                replay_blocked_verified_apply = (
                    apply_policy == "auto_verified"
                    and self.replay_enabled
                    and iteration_candidate.target.target_type == "skill"
                    and iteration_replay_dataset is None
                )
                if not replay_blocked_verified_apply:
                    try:
                        (
                            iteration_baseline_summary,
                            iteration_candidate_summary,
                        ) = await evaluate_baseline_and_candidate(
                            self.evaluation_backend,
                            dataset=evaluation_dataset,
                            candidate=iteration_candidate,
                            dataset_split="validation",
                        )
                        score_gate = ScoreImprovementGate(
                            min_delta=self.min_score_delta
                        ).evaluate(
                            baseline=iteration_baseline_summary,
                            candidate=iteration_candidate_summary,
                        )
                        cost_latency_gate = CostLatencyRegressionGate(
                            max_cost_regression_ratio=0.25,
                            max_latency_regression_ratio=0.5,
                        ).evaluate(
                            baseline=iteration_baseline_summary,
                            candidate=iteration_candidate_summary,
                        )
                        iteration_gate_results.extend([score_gate, cost_latency_gate])
                        replay_stability_gate = _replay_stability_gate(
                            baseline_summary=iteration_baseline_summary,
                            candidate_summary=iteration_candidate_summary,
                            min_score_delta=self.min_score_delta,
                            replay_stability_margin=self.replay_stability_margin,
                            replay_used=iteration_replay_dataset is not None,
                        )
                        if replay_stability_gate is not None:
                            iteration_gate_results.append(replay_stability_gate)
                        if apply_policy == "auto_verified":
                            iteration_held_out_summary = await self.evaluation_backend.evaluate_variant(
                                EvaluationRequest(
                                    variant_id=iteration_candidate.candidate_id,
                                    candidate=iteration_candidate,
                                    dataset=evaluation_dataset,
                                    dataset_split="held_out",
                                )
                            )
                            confidence = determine_candidate_confidence(
                                dataset=evaluation_dataset,
                                validation_summary=iteration_candidate_summary,
                                held_out_summary=iteration_held_out_summary,
                                min_eval_cases=self.min_eval_cases,
                            )
                            iteration_gate_results.extend(
                                [
                                    RequiredVerificationGate().evaluate(
                                        iteration_held_out_summary
                                    ),
                                    HeldOutVerificationGate(
                                        min_eval_cases=self.min_eval_cases
                                    ).evaluate(confidence),
                                    JudgeOnlySignalGate().evaluate(confidence),
                                    GlobalRegressionBenchmarkGate().evaluate(
                                        iteration_candidate,
                                        iteration_held_out_summary,
                                    ),
                                ]
                            )
                    except Exception as exc:
                        iteration_gate_results.append(
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
                iteration_gate_results.append(
                    GateResult(
                        gate_name="auto_verified_evaluation",
                        passed=False,
                        reason="auto_verified apply policy requires evaluation backend",
                    )
                )

            if apply_policy == "auto_verified":
                iteration_gate_results.append(
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

            failed_gates = [gate for gate in iteration_gate_results if not gate.passed]
            iteration_status = (
                "accepted"
                if apply_policy != "auto_verified" or not failed_gates
                else "rejected"
            )
            iteration_reports.append(
                {
                    "iteration": iteration_index + 1,
                    "candidate_id": iteration_candidate.candidate_id,
                    "status": iteration_status,
                    "baseline_metrics": (
                        dict(iteration_baseline_summary.metrics)
                        if iteration_baseline_summary is not None
                        else None
                    ),
                    "candidate_metrics": (
                        dict(iteration_candidate_summary.metrics)
                        if iteration_candidate_summary is not None
                        else None
                    ),
                    "held_out_metrics": (
                        dict(iteration_held_out_summary.metrics)
                        if iteration_held_out_summary is not None
                        else None
                    ),
                    "failed_gates": [gate.gate_name for gate in failed_gates],
                }
            )
            iteration_states.append(
                {
                    "candidate": iteration_candidate,
                    "baseline_summary": iteration_baseline_summary,
                    "candidate_summary": iteration_candidate_summary,
                    "held_out_summary": iteration_held_out_summary,
                    "replay_result": iteration_replay_result,
                    "replay_dataset": iteration_replay_dataset,
                    "gate_results": iteration_gate_results,
                    "status": iteration_status,
                }
            )
            validation_feedback = _iteration_validation_feedback(
                candidate=iteration_candidate,
                candidate_summary=iteration_candidate_summary,
                held_out_summary=iteration_held_out_summary,
                failed_gates=failed_gates,
            )
            if iteration_status == "accepted":
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
        if apply_policy == "auto_verified" and selected_candidate is not None:
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
        return SelfEvolveRunnerResult(run=completed_run, selected_candidate=selected_candidate)

    async def _replay_selected_candidate(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        selected_candidate: CandidateVariant,
        apply_policy: str,
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

        overlay = create_candidate_skill_overlay(
            workspace_root=self.store.workspace_root,
            run_id=run_id,
            candidate=selected_candidate,
            target_skill_path=target.identity.path,
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
            target_path=target.identity.path,
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
    batch_config: str | None = None,
    current_trajectory: Iterable[Mapping[str, Any]] | None = None,
    iterations: int | None = None,
    apply_policy: str = "proposal",
    infer_target: bool = False,
    evaluation_backend: EvaluationBackend | None = None,
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
    min_eval_cases: int = 30,
    judge_repetitions: int = 3,
    max_run_tokens: int = 500_000,
    min_score_delta: float = 0.0,
    auto_apply_target_types: tuple[str, ...] = ("skill",),
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None = None,
    replay_enabled: bool = False,
    candidate_replay_backend: CandidateReplayBackend | None = None,
    replay_timeout_seconds: int = 600,
    replay_max_steps: int | None = 1,
    replay_candidate_limit: int = 1,
    baseline_replay_repetitions: int = 1,
    candidate_replay_repetitions: int = 1,
    replay_stability_margin: float = 0.0,
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
) -> Mapping[str, Any]:
    if apply_policy not in {"proposal", "auto_verified"}:
        raise ValueError(f"unsupported apply policy: {apply_policy}")
    if (
        not dataset
        and not from_session
        and not from_trajectory
        and not batch_config
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
        )
    if config.mode == "custom_agent":
        if not config.agent_id:
            raise ValueError("custom_agent self-evolve evaluator requires agent_id")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_agent_name=config.agent_id,
            judge_repetitions=judge_repetitions,
        )
    if config.mode == "backend_ref":
        if not config.backend_ref:
            raise ValueError("backend_ref self-evolve evaluator requires backend_ref")
        return AWorldTrajectoryEvaluatorBackend(
            workspace_root=workspace_root,
            judge_backend_ref=config.backend_ref,
            judge_repetitions=judge_repetitions,
        )
    if config.mode == "disabled":
        raise ValueError("auto_verified self-evolve requires an evaluation backend")
    raise ValueError(f"unsupported judge mode: {config.mode}")


def _default_post_apply_evaluator(
    target: SelfEvolveTarget,
) -> Callable[[CandidateVariant], EvaluationSummary]:
    def evaluate(candidate: CandidateVariant) -> EvaluationSummary:
        target_path = Path(target.identity.path).resolve() if target.identity.path else None
        loaded_skill_path: str | None = None
        runtime_skill_found = False
        loaded_from_real_path = False
        runtime_content_matches = False
        content_matches_target_file = target.load_current_content() == candidate.content

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


def _content_fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _replay_report(replay_result: CandidateReplayResult) -> dict[str, object]:
    return {
        "request": {
            "run_id": replay_result.request.run_id,
            "task_id": replay_result.request.task_id,
            "candidate_id": replay_result.request.candidate_id,
            "overlay_skill_root": replay_result.request.overlay_skill_root,
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
    return GateResult(
        gate_name="replay_confidence",
        passed=True,
        reason="replay comparison has sufficient confidence for policy",
        details={
            "baseline_replay_source": baseline_source,
            "candidate_repetition_count": candidate_repetitions,
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
    candidate_summary: EvaluationSummary | None,
    held_out_summary: EvaluationSummary | None,
    failed_gates: list[GateResult],
) -> tuple[EvaluationSummary, ...]:
    feedback: list[EvaluationSummary] = []
    if candidate_summary is not None:
        feedback.append(
            EvaluationSummary(
                variant_id=candidate_summary.variant_id,
                metrics={
                    **dict(candidate_summary.metrics),
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
                "failed_gates": [gate.gate_name for gate in failed_gates],
                "candidate_status": "rejected" if failed_gates else "accepted",
            },
            dataset_split="validation",
        ),
    )


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
    feedback_items = payload.get("validation_feedback") if isinstance(payload, Mapping) else None
    if not isinstance(feedback_items, list):
        return []

    guidance: list[str] = []
    for item in feedback_items[-3:]:
        if not isinstance(item, Mapping):
            continue
        metrics = item.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        parts = []
        score = metrics.get("score")
        if isinstance(score, (int, float)):
            parts.append(f"score={score}")
        failed_gates = metrics.get("failed_gates")
        if isinstance(failed_gates, list) and failed_gates:
            parts.append(
                "failed_gates="
                + ",".join(str(gate) for gate in failed_gates if gate)
            )
        if not parts:
            continue
        split = item.get("dataset_split") or "validation"
        variant_id = item.get("variant_id") or "candidate"
        guidance.append(f"{variant_id} on {split}: {'; '.join(parts)}")
    return guidance


def _default_cli_skill_candidate(
    *,
    current_content: str,
    trace_packs: tuple[TracePack, ...],
    mutation_prompt: str | None = None,
) -> str:
    feedback_guidance = _feedback_guidance_from_mutation_prompt(mutation_prompt)
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
) -> SkillTextTarget:
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
        ),
    )
    if best_report.selected_target is not None:
        return best_report, inventory.find(
            best_report.selected_target.target_type,
            best_report.selected_target.target_id,
        )
    return best_report, None


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
    batch_config: str | None,
    iterations: int | None,
) -> str:
    return (
        "cli-"
        f"{abs(hash((target_key, dataset, from_session, from_trajectory, batch_config, iterations))) % 10**12:012d}"
    )


def _source_config_from_cli_request(
    *,
    dataset: str | None,
    from_session: str | None,
    from_trajectory: str | None,
    batch_config: str | None,
    workspace_root: str | Path,
) -> SelfEvolveEvalSourceConfig:
    if dataset:
        return SelfEvolveEvalSourceConfig(kind="jsonl", path=dataset)
    if from_trajectory:
        return SelfEvolveEvalSourceConfig(kind="trajectory_log", path=from_trajectory)
    if from_session:
        return SelfEvolveEvalSourceConfig(
            kind="session",
            path=str(workspace_root),
            session_id=from_session,
        )
    if batch_config:
        return SelfEvolveEvalSourceConfig(kind="batch_config", path=batch_config)
    raise ValueError("an eval source is required")
