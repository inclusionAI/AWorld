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
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
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
    ) -> None:
        self.store = store
        self.optimizer = optimizer
        self.post_apply_evaluator = post_apply_evaluator

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
        self.store.write_report(run_id, report)

        completed_run = SelfEvolveRun(
            run_id=run_id,
            target=target.identity,
            status=final_status,
            selected_candidate_id=(
                selected_candidate.candidate_id if selected_candidate is not None else None
            ),
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
        raise NotImplementedError("framework target inference is not implemented in phase 1a")
    if not target:
        raise ValueError("target is required unless target inference is enabled")
    if not dataset and not from_session and not from_trajectory and not batch_config:
        raise ValueError("an eval source is required")

    raise NotImplementedError(
        "framework CLI optimize request handling is available, but construction "
        "of target adapters and optimizers is not implemented in phase 1a"
    )
