from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef


@dataclass(frozen=True)
class CandidateReplayRequest:
    run_id: str
    task_id: str
    workspace_root: str
    target: SelfEvolveTargetRef
    candidate_id: str
    overlay_skill_root: str
    task_input: Any
    agent: str | None = None
    timeout_seconds: float | None = None
    max_steps: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class ReplayVariantResult:
    variant_id: str
    status: str
    trajectory: list[Mapping[str, Any]]
    metrics: Mapping[str, Any] = field(default_factory=dict)
    stdout_path: str | None = None
    stderr_path: str | None = None
    failure: Mapping[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class CandidateReplayResult:
    request: CandidateReplayRequest
    baseline: ReplayVariantResult
    candidate: ReplayVariantResult

    @property
    def succeeded(self) -> bool:
        return self.baseline.succeeded and self.candidate.succeeded


class CandidateReplayBackend(Protocol):
    async def replay_candidate(
        self,
        request: CandidateReplayRequest,
        *,
        candidate: CandidateVariant,
        dataset: SelfEvolveDataset,
    ) -> CandidateReplayResult:
        """Replay baseline/candidate variants and return their trajectories."""


def build_replay_request(
    *,
    run_id: str,
    workspace_root: str | Path,
    target: SelfEvolveTargetRef,
    candidate: CandidateVariant,
    overlay_skill_root: str | Path,
    dataset: SelfEvolveDataset,
    agent: str | None = None,
    timeout_seconds: float | None = None,
    max_steps: int | None = None,
    max_tokens: int | None = None,
    max_cost_usd: float | None = None,
) -> CandidateReplayRequest:
    if not dataset.cases:
        raise ValueError("candidate replay requires at least one eval case")
    case = dataset.cases[0]
    return CandidateReplayRequest(
        run_id=run_id,
        task_id=case.case_id,
        workspace_root=str(Path(workspace_root)),
        target=target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(Path(overlay_skill_root)),
        task_input=case.input,
        agent=agent,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
    )


def build_paired_replay_dataset(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    candidate: CandidateVariant,
) -> SelfEvolveDataset:
    if not replay_result.candidate.succeeded:
        raise ValueError("candidate replay did not succeed")
    if not replay_result.baseline.succeeded:
        raise ValueError("baseline replay did not succeed")

    cases: list[EvalCase] = []
    for case in dataset.cases:
        metadata = dict(case.metadata)
        metadata["variant_trajectories"] = {
            "baseline": replay_result.baseline.trajectory,
            candidate.candidate_id: replay_result.candidate.trajectory,
        }
        metadata["replay"] = {
            "request": {
                "run_id": replay_result.request.run_id,
                "task_id": replay_result.request.task_id,
                "candidate_id": replay_result.request.candidate_id,
                "overlay_skill_root": replay_result.request.overlay_skill_root,
            },
            "baseline": {
                "status": replay_result.baseline.status,
                "metrics": dict(replay_result.baseline.metrics),
                "failure": replay_result.baseline.failure,
            },
            "candidate": {
                "status": replay_result.candidate.status,
                "metrics": dict(replay_result.candidate.metrics),
                "failure": replay_result.candidate.failure,
            },
        }
        cases.append(
            EvalCase(
                case_id=case.case_id,
                input=case.input,
                expected_output=case.expected_output,
                verification_command=case.verification_command,
                metadata=metadata,
                trace_pack=case.trace_pack,
                source=case.source,
            )
        )

    return SelfEvolveDataset(
        cases=tuple(cases),
        recipe=DatasetRecipe(
            source={
                **dict(dataset.recipe.source),
                "paired_replay": True,
                "candidate_id": candidate.candidate_id,
            },
            split_seed=dataset.recipe.split_seed,
            splits=dataset.recipe.splits,
            synthetic_generation_policy=dataset.recipe.synthetic_generation_policy,
            trainable_case_ids=dataset.recipe.trainable_case_ids,
            held_out_case_ids=dataset.recipe.held_out_case_ids,
        ),
    )
