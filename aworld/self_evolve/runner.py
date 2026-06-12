from __future__ import annotations

from dataclasses import dataclass

from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.optimizers.base import CandidateOptimizer, OptimizerRequest
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
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
    ) -> None:
        self.store = store
        self.optimizer = optimizer

    async def run_explicit_target(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        trace_packs: tuple[TracePack, ...],
        apply_policy: str = "proposal",
    ) -> SelfEvolveRunnerResult:
        if apply_policy != "proposal":
            raise NotImplementedError("phase-1a runner only supports proposal apply policy")

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
        self.store.write_report(run_id, report)

        completed_run = SelfEvolveRun(
            run_id=run_id,
            target=target.identity,
            status=SelfEvolveRunStatus.SUCCEEDED,
            selected_candidate_id=(
                selected_candidate.candidate_id if selected_candidate is not None else None
            ),
        )
        self.store.create_run(completed_run)
        return SelfEvolveRunnerResult(run=completed_run, selected_candidate=selected_candidate)
