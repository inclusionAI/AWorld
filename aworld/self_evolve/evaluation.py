from __future__ import annotations

import inspect
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aworld.config.conf import EvaluationConfig
from aworld.runners.evaluate_runner import EvaluateRunner
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary


@dataclass(frozen=True)
class EvaluationRequest:
    variant_id: str
    candidate: CandidateVariant | None
    dataset: SelfEvolveDataset
    eval_config: EvaluationConfig | None = None
    dataset_split: str = "all"


@dataclass(frozen=True)
class ReplayCostEstimate:
    passed: bool
    reason: str
    baseline_replay_count: int
    candidate_replay_count: int
    total_replay_count: int
    verification_command_count: int
    judge_call_count: int
    estimated_tokens: int
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class CandidateConfidenceDecision:
    confidence: str
    reason: str
    selection_split: str | None
    verification_split: str | None
    deterministic_signal_present: bool
    held_out_case_count: int


class EvaluationBackend(Protocol):
    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        """Evaluate a baseline or candidate variant."""


class CommandVerificationBackend:
    """Evaluate objective verification commands attached to eval cases."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.timeout_seconds = timeout_seconds

    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        started_at = time.monotonic()
        case_results: list[Mapping[str, Any]] = []

        for case in request.dataset.cases:
            command = case.verification_command
            if not command:
                continue
            case_started_at = time.monotonic()
            completed = subprocess.run(
                command,
                cwd=self.workspace_root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
            case_results.append(
                {
                    "case_id": case.case_id,
                    "command": command,
                    "passed": completed.returncode == 0,
                    "returncode": completed.returncode,
                    "stdout": _bounded_text(completed.stdout),
                    "stderr": _bounded_text(completed.stderr),
                    "latency_ms": _elapsed_ms(case_started_at),
                }
            )

        pass_count = sum(1 for result in case_results if result["passed"])
        failure_count = len(case_results) - pass_count
        pass_rate = pass_count / len(case_results) if case_results else None
        return EvaluationSummary(
            variant_id=request.variant_id,
            dataset_split=request.dataset_split,
            metrics={
                "deterministic_signal": bool(case_results),
                "command_case_count": len(case_results),
                "command_pass_count": pass_count,
                "command_failure_count": failure_count,
                "command_pass_rate": pass_rate,
                "case_results": case_results,
                "latency_ms": _elapsed_ms(started_at),
            },
        )


class EvaluateRunnerBackend:
    """Adapter that lets self-evolve call the existing evaluation runner."""

    def __init__(
        self,
        *,
        runner_factory: Callable[[EvaluationConfig], Any] = EvaluateRunner,
    ) -> None:
        self.runner_factory = runner_factory

    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        eval_config = request.eval_config or EvaluationConfig()
        runner = self.runner_factory(eval_config)
        result = runner.run()
        if inspect.isawaitable(result):
            result = await result
        return EvaluationSummary(
            variant_id=request.variant_id,
            dataset_split=request.dataset_split,
            metrics=_summary_metrics(result),
        )


async def evaluate_baseline_and_candidate(
    backend: EvaluationBackend,
    *,
    dataset: SelfEvolveDataset,
    candidate: CandidateVariant,
    eval_config: EvaluationConfig | None = None,
    dataset_split: str = "validation",
    baseline_variant_id: str = "baseline",
) -> tuple[EvaluationSummary, EvaluationSummary]:
    baseline_summary = await backend.evaluate_variant(
        EvaluationRequest(
            variant_id=baseline_variant_id,
            candidate=None,
            dataset=dataset,
            eval_config=eval_config,
            dataset_split=dataset_split,
        )
    )
    candidate_summary = await backend.evaluate_variant(
        EvaluationRequest(
            variant_id=candidate.candidate_id,
            candidate=candidate,
            dataset=dataset,
            eval_config=eval_config,
            dataset_split=dataset_split,
        )
    )
    return baseline_summary, candidate_summary


def estimate_replay_cost(
    *,
    dataset: SelfEvolveDataset,
    candidate_count: int,
    judge_repetitions: int,
    estimated_tokens_per_replay: int = 0,
    estimated_cost_usd_per_replay: float | None = None,
    max_run_tokens: int | None = None,
    max_run_cost_usd: float | None = None,
) -> ReplayCostEstimate:
    if candidate_count < 0:
        raise ValueError("candidate_count must be non-negative")
    if judge_repetitions < 0:
        raise ValueError("judge_repetitions must be non-negative")

    case_count = len(dataset.cases)
    baseline_replay_count = case_count
    candidate_replay_count = candidate_count * case_count
    total_replay_count = baseline_replay_count + candidate_replay_count
    verification_case_count = sum(
        1 for case in dataset.cases if case.verification_command
    )
    verification_command_count = verification_case_count * (1 + candidate_count)
    judge_call_count = case_count * candidate_count * judge_repetitions
    estimated_tokens = total_replay_count * estimated_tokens_per_replay
    estimated_cost_usd = (
        total_replay_count * estimated_cost_usd_per_replay
        if estimated_cost_usd_per_replay is not None
        else None
    )

    passed = True
    reason = "within budget"
    if max_run_tokens is not None and estimated_tokens > max_run_tokens:
        passed = False
        reason = "estimated replay tokens exceed max_run_tokens"
    elif (
        max_run_cost_usd is not None
        and estimated_cost_usd is not None
        and estimated_cost_usd > max_run_cost_usd
    ):
        passed = False
        reason = "estimated replay cost exceeds max_run_cost_usd"

    return ReplayCostEstimate(
        passed=passed,
        reason=reason,
        baseline_replay_count=baseline_replay_count,
        candidate_replay_count=candidate_replay_count,
        total_replay_count=total_replay_count,
        verification_command_count=verification_command_count,
        judge_call_count=judge_call_count,
        estimated_tokens=estimated_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )


def determine_candidate_confidence(
    *,
    dataset: SelfEvolveDataset,
    validation_summary: EvaluationSummary,
    held_out_summary: EvaluationSummary | None,
    min_eval_cases: int,
) -> CandidateConfidenceDecision:
    held_out_case_count = len(dataset.recipe.held_out_case_ids)
    deterministic_signal_present = _has_deterministic_signal(
        validation_summary,
        held_out_summary,
    )
    selection_split = validation_summary.dataset_split
    verification_split = held_out_summary.dataset_split if held_out_summary is not None else None

    if held_out_case_count < min_eval_cases or held_out_summary is None:
        return CandidateConfidenceDecision(
            confidence="limited",
            reason="insufficient held-out eval cases for verified confidence",
            selection_split=selection_split,
            verification_split=None,
            deterministic_signal_present=deterministic_signal_present,
            held_out_case_count=held_out_case_count,
        )

    if not deterministic_signal_present:
        return CandidateConfidenceDecision(
            confidence="limited",
            reason="verified confidence requires a deterministic signal",
            selection_split=selection_split,
            verification_split=verification_split,
            deterministic_signal_present=False,
            held_out_case_count=held_out_case_count,
        )

    return CandidateConfidenceDecision(
        confidence="verified",
        reason="held-out deterministic evaluation is sufficient",
        selection_split=selection_split,
        verification_split=verification_split,
        deterministic_signal_present=True,
        held_out_case_count=held_out_case_count,
    )


def _summary_metrics(result: Any) -> Mapping[str, Any]:
    summary = getattr(result, "summary", None)
    if isinstance(summary, Mapping):
        return summary
    if isinstance(result, Mapping):
        result_summary = result.get("summary")
        if isinstance(result_summary, Mapping):
            return result_summary
        return result
    return {"result": str(result)}


def _elapsed_ms(started_at: float) -> float:
    return (time.monotonic() - started_at) * 1000


def _has_deterministic_signal(
    validation_summary: EvaluationSummary,
    held_out_summary: EvaluationSummary | None,
) -> bool:
    summaries = (validation_summary,) if held_out_summary is None else (validation_summary, held_out_summary)
    return any(summary.metrics.get("deterministic_signal") is True for summary in summaries)


def _bounded_text(value: str, *, max_chars: int = 2000) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."
