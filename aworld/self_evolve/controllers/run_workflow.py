"""Typed workflow-budget estimation for explicit-target runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aworld.self_evolve.budget import BudgetStage
from aworld.self_evolve.campaign_policy import (
    effective_replay_repetitions,
    is_verified_apply_policy,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.regression import ResolvedRegressionSuite


WorkflowBudgetItems = Callable[[int, int], tuple[tuple[BudgetStage, str, int], ...]]


@dataclass(frozen=True)
class WorkflowEstimationPolicy:
    max_iterations: int
    replay_enabled: bool
    replay_backend_available: bool
    repetitions_explicit: bool
    minimum_independent_cases: int
    baseline_repetitions: int
    candidate_repetitions: int
    evaluation_backend_available: bool
    judge_repetitions: int
    progress_repair_extension_iterations: int
    challenger_enabled: bool = False
    challenger_max_cases: int = 1


@dataclass(frozen=True)
class WorkflowEstimationRequest:
    dataset: SelfEvolveDataset
    apply_policy: str
    regression_suites: tuple[ResolvedRegressionSuite, ...]
    policy: WorkflowEstimationPolicy
    replayable_dataset: Callable[[SelfEvolveDataset], SelfEvolveDataset]


@dataclass(frozen=True)
class WorkflowEstimationResult:
    iteration_budget: int
    estimated_baseline_repetitions: int
    estimated_candidate_repetitions: int
    estimated_replay_units: int
    estimated_evaluation_units: int
    evaluation_backend_available: bool
    judge_repetitions: int

    def budget_items(
        self,
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
        if self.estimated_replay_units > 0:
            items.append(
                (
                    BudgetStage.PAIRED_REPLAY,
                    f"iteration-{iteration}-workflow-replay",
                    self.estimated_replay_units * candidate_count,
                )
            )
        if self.evaluation_backend_available:
            evaluation_units = self.estimated_evaluation_units * candidate_count
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


def estimate_run_workflow(
    request: WorkflowEstimationRequest,
) -> WorkflowEstimationResult:
    """Compile conservative run workflow units without reading Runner state."""

    policy = request.policy
    replay_case_count = len(request.replayable_dataset(request.dataset).cases)
    baseline_repetitions, candidate_repetitions, _ = effective_replay_repetitions(
        apply_policy=request.apply_policy,
        repetitions_explicit=policy.repetitions_explicit,
        replay_case_count=replay_case_count,
        measurement_min_independent_cases=policy.minimum_independent_cases,
        baseline_repetitions=policy.baseline_repetitions,
        candidate_repetitions=policy.candidate_repetitions,
    )
    replay_units = (
        replay_case_count * (baseline_repetitions + candidate_repetitions)
        if policy.replay_enabled and policy.replay_backend_available
        else 0
    )
    evaluation_case_count = max(
        len(request.dataset.cases), replay_case_count * candidate_repetitions
    )
    verified_apply = is_verified_apply_policy(request.apply_policy)
    reuse_single_case_validation = bool(
        replay_units > 0
        and replay_case_count == 1
        and not request.dataset.recipe.held_out_case_ids
    )
    # Validation, challenge, and held-out evaluation are all paired. Match
    # authoritative admission rather than under-budgeting a held-out arm.
    evaluation_variants = (
        (4 if reuse_single_case_validation else 6) if verified_apply else 2
    )
    regression_units = (
        sum(max(1, len(suite.dataset.cases)) * 2 for suite in request.regression_suites)
        if verified_apply
        else 0
    )
    if verified_apply and policy.challenger_enabled and request.regression_suites:
        regression_units += policy.challenger_max_cases * 2
    evaluation_units = max(
        1, evaluation_case_count * evaluation_variants + regression_units
    )
    return WorkflowEstimationResult(
        iteration_budget=(
            policy.max_iterations + policy.progress_repair_extension_iterations
        ),
        estimated_baseline_repetitions=baseline_repetitions,
        estimated_candidate_repetitions=candidate_repetitions,
        estimated_replay_units=replay_units,
        estimated_evaluation_units=evaluation_units,
        evaluation_backend_available=policy.evaluation_backend_available,
        judge_repetitions=policy.judge_repetitions,
    )


__all__ = [
    "WorkflowEstimationPolicy",
    "WorkflowEstimationRequest",
    "WorkflowEstimationResult",
    "estimate_run_workflow",
]
