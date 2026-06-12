from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable, Any
from pathlib import Path
from typing import Mapping, Iterable

from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.evaluation import (
    EvaluationBackend,
    evaluate_baseline_and_candidate,
)
from aworld.self_evolve.gates import (
    CostLatencyRegressionGate,
    ScoreImprovementGate,
    StoppingConditionGate,
    StoppingConditionState,
)
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget, SkillTextTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    SelfEvolveRun,
    SelfEvolveRunStatus,
)


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
    ) -> None:
        self.store = store
        self.optimizer = optimizer
        self.post_apply_evaluator = post_apply_evaluator
        self.evaluation_backend = evaluation_backend
        self.min_score_delta = min_score_delta
        self.pending_duplicate = pending_duplicate
        self.max_iterations = max_iterations

    async def run_explicit_target(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        trace_packs: tuple[TracePack, ...],
        apply_policy: str = "proposal",
    ) -> SelfEvolveRunnerResult:
        if apply_policy not in {"proposal", "auto_verified"}:
            raise ValueError(f"unsupported apply policy: {apply_policy}")

        run = SelfEvolveRun(run_id=run_id, target=target.identity, status=SelfEvolveRunStatus.RUNNING)
        self.store.create_run(run)
        self.store.write_dataset_recipe(run_id, dataset.recipe)

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
                "stopping_condition": {
                    "gate_name": stopping_result.gate_name,
                    "passed": stopping_result.passed,
                    "reason": stopping_result.reason,
                    "details": stopping_result.details,
                },
            }
            self.store.write_report(run_id, report)
            completed_run = SelfEvolveRun(
                run_id=run_id,
                target=target.identity,
                status=SelfEvolveRunStatus.REJECTED,
                gate_results=(stopping_result,),
            )
            self.store.create_run(completed_run)
            return SelfEvolveRunnerResult(run=completed_run, selected_candidate=None)

        optimizer_result = await self.optimizer.propose(
            OptimizerRequest.from_dataset(
                target=target.identity,
                current_content=target.load_current_content(),
                target_fingerprint=target.fingerprint_current_content(),
                trace_packs=trace_packs,
                validation_feedback=(),
                dataset=dataset,
            )
        )

        selected_candidate = optimizer_result.candidates[0] if optimizer_result.candidates else None
        for candidate in optimizer_result.candidates:
            target.preserve_proposal(self.store, run_id, candidate)
        for lineage in optimizer_result.lineage:
            self.store.write_optimizer_lineage(run_id, lineage)

        baseline_summary: EvaluationSummary | None = None
        candidate_summary: EvaluationSummary | None = None
        gate_results = []
        if self.evaluation_backend is not None and selected_candidate is not None:
            baseline_summary, candidate_summary = await evaluate_baseline_and_candidate(
                self.evaluation_backend,
                dataset=dataset,
                candidate=selected_candidate,
                dataset_split="validation",
            )
            score_gate = ScoreImprovementGate(min_delta=self.min_score_delta).evaluate(
                baseline=baseline_summary,
                candidate=candidate_summary,
            )
            cost_latency_gate = CostLatencyRegressionGate(
                max_cost_regression_ratio=0.25,
                max_latency_regression_ratio=0.5,
            ).evaluate(baseline=baseline_summary, candidate=candidate_summary)
            gate_results.extend([score_gate, cost_latency_gate])

        post_apply: dict[str, object] | None = None
        final_status = SelfEvolveRunStatus.SUCCEEDED
        if apply_policy == "auto_verified" and selected_candidate is not None:
            post_apply = await self._apply_auto_verified(target, selected_candidate)
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
                candidate.candidate_id for candidate in optimizer_result.candidates
            ],
            "selected_candidate_id": (
                selected_candidate.candidate_id if selected_candidate is not None else None
            ),
            "optimizer_diagnostics": optimizer_result.diagnostics,
        }
        if post_apply is not None:
            report["post_apply"] = post_apply
        if baseline_summary is not None:
            report["baseline_metrics"] = dict(baseline_summary.metrics)
        if candidate_summary is not None:
            report["candidate_metrics"] = dict(candidate_summary.metrics)
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

    async def _apply_auto_verified(
        self,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
    ) -> dict[str, object]:
        if self.post_apply_evaluator is None:
            raise ValueError("auto_verified apply policy requires post_apply_evaluator")
        target.apply_candidate(candidate.content)
        summary = self.post_apply_evaluator(candidate)
        if inspect.isawaitable(summary):
            summary = await summary
        if not isinstance(summary, EvaluationSummary):
            raise ValueError("post_apply_evaluator must return EvaluationSummary")
        if summary.metrics.get("post_apply_passed") is True:
            return {
                "status": "accepted",
                "metrics": dict(summary.metrics),
                "dataset_split": summary.dataset_split,
            }

        target.rollback()
        return {
            "status": "rolled_back",
            "metrics": dict(summary.metrics),
            "dataset_split": summary.dataset_split,
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
    iterations: int | None = None,
    apply_policy: str = "proposal",
    infer_target: bool = False,
) -> Mapping[str, Any]:
    if apply_policy not in {"proposal", "auto_verified"}:
        raise ValueError(f"unsupported apply policy: {apply_policy}")
    if infer_target:
        raise NotImplementedError("framework target inference requires trajectory credit assignment wiring")
    if not target:
        raise ValueError("target is required unless target inference is enabled")
    if not dataset and not from_session and not from_trajectory and not batch_config:
        raise ValueError("an eval source is required")

    target_adapter = _target_from_cli_ref(target, workspace_root=workspace_root)
    source_config = _source_config_from_cli_request(
        dataset=dataset,
        from_session=from_session,
        from_trajectory=from_trajectory,
        batch_config=batch_config,
        workspace_root=workspace_root,
    )
    built_dataset = build_dataset_from_source(source_config, task_id=task)
    trace_packs = tuple(
        case.trace_pack for case in built_dataset.cases if case.trace_pack is not None
    )
    run_id = f"cli-{abs(hash((target, dataset, from_session, from_trajectory, batch_config, iterations))) % 10**12:012d}"

    async def _noop_mutation(prompt: str) -> dict[str, str]:
        return {
            "content": target_adapter.load_current_content(),
            "rationale": "No CLI optimizer configured; preserved proposal-only baseline.",
        }

    import asyncio

    result = asyncio.run(
        SelfEvolveRunner(
            store=FilesystemSelfEvolveStore(workspace_root),
            optimizer=TraceReflectiveLLMMutator(mutate_text=_noop_mutation),
        ).run_explicit_target(
            run_id=run_id,
            target=target_adapter,
            dataset=built_dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
        )
    )
    report_path = FilesystemSelfEvolveStore(workspace_root).run_path(run_id) / "report.json"
    return {
        "report_path": str(report_path),
        "best_candidate_id": (
            result.selected_candidate.candidate_id
            if result.selected_candidate is not None
            else None
        ),
        "run_id": result.run.run_id,
        "status": result.run.status.value,
    }


def _target_from_cli_ref(target: str, *, workspace_root: str | Path) -> SelfEvolveTarget:
    target_type, _, target_id = target.partition(":")
    if target_type != "skill" or not target_id:
        raise NotImplementedError(f"CLI target adapter is not implemented for {target!r}")
    workspace = Path(workspace_root)
    candidates = (
        workspace / "aworld-skills" / target_id / "SKILL.md",
        workspace / "skills" / target_id / "SKILL.md",
    )
    for path in candidates:
        if path.exists():
            return SkillTextTarget(path)
    raise FileNotFoundError(f"skill target not found: {target}")


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
