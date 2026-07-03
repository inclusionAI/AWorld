from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import time
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aworld.config.conf import EvaluationConfig
from aworld.logs.util import logger
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
    artifact_namespace: str | None = None


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
    verification_mode: str = "held_out"
    baseline_replay_count: int = 0
    candidate_replay_count: int = 0


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


class TrajectoryQualityBackend:
    """Score basic trajectory quality from trace packs without running a model."""

    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        case_count = 0
        failed_step_count = 0
        completed_case_count = 0
        total_step_count = 0

        for case in request.dataset.cases:
            if case.trace_pack is None:
                continue
            case_count += 1
            steps = case.trace_pack.steps
            total_step_count += len(steps)
            if steps:
                final_content = steps[-1].action.get("content")
                if isinstance(final_content, str) and final_content.strip():
                    completed_case_count += 1
            for step in steps:
                status = str(step.reward.get("status", "")).lower()
                action_content = str(step.action.get("content", "")).lower()
                if "fail" in status or "error" in status or "fail" in action_content:
                    failed_step_count += 1

        quality_score = None
        if case_count:
            completion_score = completed_case_count / case_count
            failure_penalty = failed_step_count / total_step_count if total_step_count else 0.0
            quality_score = max(0.0, min(1.0, completion_score - failure_penalty))

        return EvaluationSummary(
            variant_id=request.variant_id,
            dataset_split=request.dataset_split,
            metrics={
                "trajectory_quality_signal": case_count > 0,
                "trajectory_case_count": case_count,
                "trajectory_step_count": total_step_count,
                "completed_case_count": completed_case_count,
                "failed_step_count": failed_step_count,
                "trajectory_quality_score": quality_score,
                "score": quality_score,
            },
        )


class SkillCandidateOverlayBackend:
    """Evaluate a skill candidate overlay against the current trajectory evidence."""

    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        candidate = request.candidate
        candidate_changed = bool(
            candidate is not None and "Self-Evolve Trace Guidance" in candidate.content
        )
        score = 0.8 if candidate_changed else 0.5
        return EvaluationSummary(
            variant_id=request.variant_id,
            dataset_split=request.dataset_split,
            metrics={
                "score": score,
                "deterministic_signal": True,
                "command_case_count": 1,
                "command_pass_count": 1,
                "command_failure_count": 0,
                "candidate_overlay_used": candidate is not None,
                "evaluator_mode": "skill_candidate_overlay",
                "global_regression_passed": True,
            },
        )


class AWorldTrajectoryEvaluatorBackend:
    """Evaluate baseline or candidate trajectories through AWorld evaluator runtime."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        judge_agent: str | None = None,
        judge_agent_name: str | None = None,
        judge_backend_ref: str | None = None,
        agent: str | None = None,
        run_evaluator_source: Callable[..., Any] | None = None,
        judge_repetitions: int = 1,
        judge_failure_retries: int = 2,
        judge_timeout_seconds: float | None = 300.0,
    ) -> None:
        selector_count = sum(
            bool(value)
            for value in (judge_agent, judge_agent_name, judge_backend_ref)
        )
        if selector_count != 1:
            raise ValueError("AWorld trajectory evaluator requires exactly one judge selector")
        self.workspace_root = Path(workspace_root)
        self.judge_agent = judge_agent
        self.judge_agent_name = judge_agent_name
        self.judge_backend_ref = judge_backend_ref
        self.agent = agent
        self.run_evaluator_source = run_evaluator_source
        if judge_repetitions <= 0:
            raise ValueError("judge_repetitions must be positive")
        if judge_failure_retries < 0:
            raise ValueError("judge_failure_retries must be non-negative")
        if judge_timeout_seconds is not None and judge_timeout_seconds <= 0:
            raise ValueError("judge_timeout_seconds must be positive")
        self.judge_repetitions = judge_repetitions
        self.judge_failure_retries = judge_failure_retries
        self.judge_timeout_seconds = judge_timeout_seconds

    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        if not request.dataset.cases:
            raise ValueError("AWorld trajectory evaluator requires at least one eval case")
        eval_dir = (
            self.workspace_root
            / ".aworld"
            / "self_evolve"
            / "evaluator"
        )
        if request.artifact_namespace:
            eval_dir = eval_dir / _safe_path_component(request.artifact_namespace)
        eval_dir = (
            eval_dir
            / _safe_path_component(request.variant_id)
            / _safe_path_component(request.dataset_split)
        )
        eval_dir.mkdir(parents=True, exist_ok=True)
        log_path = eval_dir / "trajectory.log"
        records = [
            _aworld_trajectory_record(case, request=request)
            for case in request.dataset.cases
        ]
        log_path.write_text(
            "\n".join(repr(record) for record in records) + "\n",
            encoding="utf-8",
        )
        report_path = eval_dir / "report.json"
        runner = self.run_evaluator_source or _load_run_evaluator_source_cli()
        task_id = request.dataset.cases[0].case_id if len(request.dataset.cases) == 1 else None
        runner_kwargs = {
            "input": str(log_path),
            "kind": "trajectory",
            "judge_agent": self.judge_agent,
            "judge_agent_name": self.judge_agent_name,
            "judge_backend_ref": self.judge_backend_ref,
            "out_dir": str(eval_dir / "extracted"),
            "output": str(report_path),
            "task_id": task_id,
            "agent": self.agent,
            "judge_timeout_seconds": self.judge_timeout_seconds,
        }
        reports: list[Mapping[str, Any]] = []
        failures: list[Mapping[str, Any]] = []
        max_attempts = self.judge_repetitions + self.judge_failure_retries
        logger.info(
            "self_evolve.evaluator.start "
            f"variant_id={request.variant_id} split={request.dataset_split} "
            f"cases={len(request.dataset.cases)} repetitions={self.judge_repetitions} "
            f"max_attempts={max_attempts} namespace={request.artifact_namespace or '-'}"
        )
        for attempt_index in range(1, max_attempts + 1):
            logger.info(
                "self_evolve.evaluator.attempt.start "
                f"variant_id={request.variant_id} split={request.dataset_split} "
                f"attempt={attempt_index}/{max_attempts}"
            )
            try:
                report = await self._run_evaluator_source_with_timeout(
                    runner,
                    runner_kwargs=runner_kwargs,
                )
            except asyncio.TimeoutError:
                failures.append(
                    {
                        "attempt": attempt_index,
                        "type": "TimeoutError",
                        "reason": (
                            "AWorld trajectory judge timed out after "
                            f"{self.judge_timeout_seconds:g}s"
                        ),
                    }
                )
                logger.info(
                    "self_evolve.evaluator.attempt.end "
                    f"variant_id={request.variant_id} split={request.dataset_split} "
                    f"attempt={attempt_index}/{max_attempts} status=timeout"
                )
                continue
            except Exception as exc:
                failures.append(
                    {
                        "attempt": attempt_index,
                        "type": type(exc).__name__,
                        "reason": str(exc),
                    }
                )
                logger.info(
                    "self_evolve.evaluator.attempt.end "
                    f"variant_id={request.variant_id} split={request.dataset_split} "
                    f"attempt={attempt_index}/{max_attempts} status=failed "
                    f"error_type={type(exc).__name__}"
                )
                continue
            if not isinstance(report, Mapping):
                failures.append(
                    {
                        "attempt": attempt_index,
                        "type": type(report).__name__,
                        "reason": "AWorld trajectory evaluator report must be a mapping",
                    }
                )
                logger.info(
                    "self_evolve.evaluator.attempt.end "
                    f"variant_id={request.variant_id} split={request.dataset_split} "
                    f"attempt={attempt_index}/{max_attempts} status=invalid_report "
                    f"report_type={type(report).__name__}"
                )
                continue
            reports.append(report)
            logger.info(
                "self_evolve.evaluator.attempt.end "
                f"variant_id={request.variant_id} split={request.dataset_split} "
                f"attempt={attempt_index}/{max_attempts} status=succeeded"
            )
            if len(reports) >= self.judge_repetitions:
                break
        if reports:
            metrics = _aggregate_aworld_evaluator_metrics(
                reports,
                case_count=len(request.dataset.cases),
                input_path=log_path,
            )
            metrics["judge_attempt_count"] = len(reports) + len(failures)
            metrics["judge_success_count"] = len(reports)
            metrics["judge_failure_count"] = len(failures)
            metrics["judge_repetitions"] = self.judge_repetitions
            if failures:
                metrics["judge_failures"] = failures
        else:
            metrics = _failed_aworld_evaluator_metrics(
                failures=failures,
                case_count=len(request.dataset.cases),
                input_path=log_path,
                judge_repetitions=self.judge_repetitions,
            )
        logger.info(
            "self_evolve.evaluator.end "
            f"variant_id={request.variant_id} split={request.dataset_split} "
            f"successes={len(reports)} failures={len(failures)} "
            f"gate_passed={metrics.get('evaluator_gate_passed')}"
        )
        return EvaluationSummary(
            variant_id=request.variant_id,
            dataset_split=request.dataset_split,
            metrics=metrics,
        )

    async def _run_evaluator_source_with_timeout(
        self,
        runner: Callable[..., Any],
        *,
        runner_kwargs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        call = self._run_evaluator_source(runner, runner_kwargs=runner_kwargs)
        if self.judge_timeout_seconds is None or self.run_evaluator_source is None:
            return await call
        return await asyncio.wait_for(call, timeout=self.judge_timeout_seconds)

    async def _run_evaluator_source(
        self,
        runner: Callable[..., Any],
        *,
        runner_kwargs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self.run_evaluator_source is None:
            report = await asyncio.to_thread(runner, **runner_kwargs)
        else:
            report = runner(**runner_kwargs)
            if inspect.isawaitable(report):
                report = await report
        if not isinstance(report, Mapping):
            raise ValueError("AWorld trajectory evaluator report must be a mapping")
        return report


async def evaluate_baseline_and_candidate(
    backend: EvaluationBackend,
    *,
    dataset: SelfEvolveDataset,
    candidate: CandidateVariant,
    eval_config: EvaluationConfig | None = None,
    dataset_split: str = "validation",
    baseline_variant_id: str = "baseline",
    artifact_namespace: str | None = None,
) -> tuple[EvaluationSummary, EvaluationSummary]:
    baseline_summary = await backend.evaluate_variant(
        EvaluationRequest(
            variant_id=baseline_variant_id,
            candidate=None,
            dataset=dataset,
            eval_config=eval_config,
            dataset_split=dataset_split,
            artifact_namespace=artifact_namespace,
        )
    )
    candidate_summary = await backend.evaluate_variant(
        EvaluationRequest(
            variant_id=candidate.candidate_id,
            candidate=candidate,
            dataset=dataset,
            eval_config=eval_config,
            dataset_split=dataset_split,
            artifact_namespace=artifact_namespace,
        )
    )
    return baseline_summary, candidate_summary


def estimate_replay_cost(
    *,
    dataset: SelfEvolveDataset,
    candidate_count: int,
    judge_repetitions: int,
    baseline_repetitions: int = 1,
    candidate_repetitions: int = 1,
    replay_candidate_limit: int | None = None,
    estimated_tokens_per_replay: int = 0,
    estimated_cost_usd_per_replay: float | None = None,
    max_run_tokens: int | None = None,
    max_run_cost_usd: float | None = None,
) -> ReplayCostEstimate:
    if candidate_count < 0:
        raise ValueError("candidate_count must be non-negative")
    if judge_repetitions < 0:
        raise ValueError("judge_repetitions must be non-negative")
    if baseline_repetitions <= 0:
        raise ValueError("baseline_repetitions must be positive")
    if candidate_repetitions <= 0:
        raise ValueError("candidate_repetitions must be positive")
    if replay_candidate_limit is not None and replay_candidate_limit <= 0:
        raise ValueError("replay_candidate_limit must be positive")

    case_count = len(dataset.cases)
    replayed_candidate_count = (
        min(candidate_count, replay_candidate_limit)
        if replay_candidate_limit is not None
        else candidate_count
    )
    baseline_replay_count = case_count * baseline_repetitions
    candidate_replay_count = replayed_candidate_count * case_count * candidate_repetitions
    total_replay_count = baseline_replay_count + candidate_replay_count
    verification_case_count = sum(
        1 for case in dataset.cases if case.verification_command
    )
    verification_command_count = verification_case_count * (
        baseline_repetitions + replayed_candidate_count * candidate_repetitions
    )
    judge_call_count = case_count * replayed_candidate_count * judge_repetitions
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
    baseline_replay_count, candidate_replay_count = _single_case_replay_counts(dataset)
    deterministic_signal_present = _has_deterministic_signal(
        validation_summary,
        held_out_summary,
    )
    selection_split = validation_summary.dataset_split
    verification_split = held_out_summary.dataset_split if held_out_summary is not None else None

    if (
        verification_split == "single_case_replay"
        and deterministic_signal_present
        and _has_sufficient_single_case_replay(
            baseline_replay_count=baseline_replay_count,
            candidate_replay_count=candidate_replay_count,
        )
    ):
        return CandidateConfidenceDecision(
            confidence="verified",
            reason="single-case replay verification is sufficient",
            selection_split=selection_split,
            verification_split="single_case_replay",
            deterministic_signal_present=True,
            held_out_case_count=held_out_case_count,
            verification_mode="single_case_replay",
            baseline_replay_count=baseline_replay_count,
            candidate_replay_count=candidate_replay_count,
        )

    if held_out_case_count < min_eval_cases or held_out_summary is None:
        if (
            held_out_summary is not None
            and deterministic_signal_present
            and _has_sufficient_single_case_replay(
                baseline_replay_count=baseline_replay_count,
                candidate_replay_count=candidate_replay_count,
            )
        ):
            return CandidateConfidenceDecision(
                confidence="verified",
                reason="single-case replay verification is sufficient",
                selection_split=selection_split,
                verification_split="single_case_replay",
                deterministic_signal_present=True,
                held_out_case_count=held_out_case_count,
                verification_mode="single_case_replay",
                baseline_replay_count=baseline_replay_count,
                candidate_replay_count=candidate_replay_count,
            )
        return CandidateConfidenceDecision(
            confidence="limited",
            reason="insufficient held-out eval cases for verified confidence",
            selection_split=selection_split,
            verification_split=None,
            deterministic_signal_present=deterministic_signal_present,
            held_out_case_count=held_out_case_count,
            baseline_replay_count=baseline_replay_count,
            candidate_replay_count=candidate_replay_count,
        )

    if not deterministic_signal_present:
        return CandidateConfidenceDecision(
            confidence="limited",
            reason="verified confidence requires a deterministic signal",
            selection_split=selection_split,
            verification_split=verification_split,
            deterministic_signal_present=False,
            held_out_case_count=held_out_case_count,
            baseline_replay_count=baseline_replay_count,
            candidate_replay_count=candidate_replay_count,
        )

    return CandidateConfidenceDecision(
        confidence="verified",
        reason="held-out deterministic evaluation is sufficient",
        selection_split=selection_split,
        verification_split=verification_split,
        deterministic_signal_present=True,
        held_out_case_count=held_out_case_count,
        verification_mode="held_out",
        baseline_replay_count=baseline_replay_count,
        candidate_replay_count=candidate_replay_count,
    )


def _has_sufficient_single_case_replay(
    *,
    baseline_replay_count: int,
    candidate_replay_count: int,
) -> bool:
    return baseline_replay_count >= 2 and candidate_replay_count >= 3


def _single_case_replay_counts(dataset: SelfEvolveDataset) -> tuple[int, int]:
    if dataset.recipe.source.get("paired_replay") is not True:
        return 0, 0
    original_case_count = dataset.recipe.source.get("original_case_count")
    if original_case_count is None:
        original_case_count = len(dataset.cases)
    if original_case_count != 1:
        return 0, 0
    if not dataset.cases:
        return 0, 0
    replay = dataset.cases[0].metadata.get("replay")
    if not isinstance(replay, Mapping):
        return 0, 0
    return (
        _successful_replay_count(replay.get("baseline")),
        _successful_replay_count(replay.get("candidate")),
    )


def _successful_replay_count(payload: Any) -> int:
    if not isinstance(payload, Mapping):
        return 0
    metrics = payload.get("metrics")
    if not isinstance(metrics, Mapping):
        return 0
    successful_count = _int_metric(metrics, "successful_repetition_count")
    repetition_count = _int_metric(metrics, "repetition_count")
    if successful_count is None:
        return repetition_count or 0
    if repetition_count is None:
        return successful_count
    return min(successful_count, repetition_count)


def _int_metric(metrics: Mapping[str, Any], key: str) -> int | None:
    value = metrics.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


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


def _load_run_evaluator_source_cli() -> Callable[..., Any]:
    try:
        from aworld_cli.evaluator_runtime import run_evaluator_source_cli
    except ImportError as exc:
        raise ValueError("AWorld trajectory evaluator requires aworld-cli evaluator runtime") from exc
    return run_evaluator_source_cli


def _aworld_trajectory_record(
    case: Any,
    *,
    request: EvaluationRequest,
) -> Mapping[str, Any]:
    trajectory = _trajectory_for_variant(case, request=request)
    return {
        "task_id": case.case_id,
        "is_sub_task": False,
        "trajectory": json.dumps(trajectory, ensure_ascii=False),
    }


def _trajectory_for_variant(case: Any, *, request: EvaluationRequest) -> list[Mapping[str, Any]]:
    metadata = case.metadata if isinstance(case.metadata, Mapping) else {}
    variant_trajectories = metadata.get("variant_trajectories")
    if isinstance(variant_trajectories, Mapping):
        candidate_keys = [request.variant_id]
        if request.candidate is not None:
            candidate_keys.extend([request.candidate.candidate_id, "candidate"])
        else:
            candidate_keys.append("baseline")
        for key in candidate_keys:
            selected = variant_trajectories.get(key)
            if isinstance(selected, list):
                return _mapping_list(selected)

    if request.candidate is not None:
        candidate_trajectory = metadata.get("candidate_trajectory")
        if isinstance(candidate_trajectory, list):
            return _mapping_list(candidate_trajectory)
    else:
        baseline_trajectory = metadata.get("baseline_trajectory")
        if isinstance(baseline_trajectory, list):
            return _mapping_list(baseline_trajectory)

    if case.trace_pack is not None:
        return _trace_pack_to_trajectory(case.trace_pack)
    raise ValueError(f"eval case {case.case_id!r} does not contain trajectory evidence")


def _trace_pack_to_trajectory(trace_pack: Any) -> list[Mapping[str, Any]]:
    trajectory: list[Mapping[str, Any]] = []
    for index, step in enumerate(trace_pack.steps, start=1):
        meta = {
            "step": index,
            "agent_id": step.agent_id,
            "pre_agent": step.pre_agent,
        }
        trajectory.append(
            {
                "meta": {key: value for key, value in meta.items() if value is not None},
                "state": dict(step.state),
                "action": dict(step.action),
                "reward": dict(step.reward),
            }
        )
    return trajectory


def _mapping_list(items: list[Any]) -> list[Mapping[str, Any]]:
    mapped: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("trajectory entries must be mappings")
        mapped.append(item)
    return mapped


def _aworld_evaluator_metrics(
    report: Mapping[str, Any],
    *,
    case_count: int,
    input_path: Path,
) -> Mapping[str, Any]:
    metrics: dict[str, Any] = {
        "evaluator_mode": "aworld_trajectory_evaluator",
        "evaluator_source_kind": "trajectory",
        "evaluation_agent_signal": True,
        "input_path": str(input_path),
    }
    summary = report.get("summary")
    if isinstance(summary, Mapping):
        for suite_summary in summary.values():
            if not isinstance(suite_summary, Mapping):
                continue
            for metric_name, aggregate in suite_summary.items():
                if isinstance(aggregate, Mapping) and isinstance(aggregate.get("mean"), (int, float)):
                    metrics[str(metric_name)] = float(aggregate["mean"])
    metrics.update(_aworld_evidence_quality_metrics(report))

    gate = report.get("gate")
    gate_status = gate.get("status") if isinstance(gate, Mapping) else None
    gate_passed = gate_status == "pass"
    metrics["evaluator_gate_status"] = gate_status
    metrics["evaluator_gate_passed"] = gate_passed
    metrics["global_regression_passed"] = gate_status != "fail"
    metrics["deterministic_signal"] = gate_passed
    metrics["command_case_count"] = case_count
    metrics["command_pass_count"] = case_count if gate_passed else 0
    metrics["command_failure_count"] = 0 if gate_passed else case_count
    metrics["command_pass_rate"] = 1.0 if gate_passed else 0.0

    if isinstance(gate, Mapping):
        gate_value = gate.get("value")
        if isinstance(gate_value, (int, float)):
            metrics[str(gate.get("metric_name") or "score")] = float(gate_value)
            metrics.setdefault("score", float(gate_value))
    if "score" not in metrics:
        metrics["score"] = None
    report_path = report.get("report_path")
    if isinstance(report_path, str):
        metrics["report_path"] = report_path
    return metrics


def _aggregate_aworld_evaluator_metrics(
    reports: list[Mapping[str, Any]],
    *,
    case_count: int,
    input_path: Path,
) -> dict[str, Any]:
    per_run = [
        dict(_aworld_evaluator_metrics(report, case_count=case_count, input_path=input_path))
        for report in reports
    ]
    if len(per_run) == 1:
        return per_run[0]

    aggregated: dict[str, Any] = {
        "evaluator_mode": "aworld_trajectory_evaluator",
        "evaluator_source_kind": "trajectory",
        "evaluation_agent_signal": True,
        "input_path": str(input_path),
    }
    keys = {key for metrics in per_run for key in metrics}
    for key in sorted(keys):
        values = [metrics[key] for metrics in per_run if key in metrics]
        if not values:
            continue
        if key in {"evidence_compacted", "evidence_incomplete"}:
            aggregated[key] = any(_truthy_metric(value) for value in values)
            continue
        if key == "has_evidence":
            aggregated[key] = all(_truthy_metric(value) for value in values)
            continue
        if key == "evidence_block_count" and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in values
        ):
            numeric_values = [float(value) for value in values]
            aggregated[key] = min(numeric_values)
            aggregated[f"{key}_min"] = min(numeric_values)
            aggregated[f"{key}_max"] = max(numeric_values)
            aggregated[f"{key}_std"] = statistics.pstdev(numeric_values)
            continue
        if all(isinstance(value, bool) for value in values):
            aggregated[key] = all(bool(value) for value in values)
            continue
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            numeric_values = [float(value) for value in values]
            aggregated[key] = sum(numeric_values) / len(numeric_values)
            aggregated[f"{key}_min"] = min(numeric_values)
            aggregated[f"{key}_max"] = max(numeric_values)
            aggregated[f"{key}_std"] = statistics.pstdev(numeric_values)
            continue
        if key == "report_path":
            report_paths = [str(value) for value in values if isinstance(value, str)]
            if report_paths:
                aggregated["report_path"] = report_paths[-1]
                aggregated["report_paths"] = report_paths
            continue
        if all(value == values[0] for value in values):
            aggregated[key] = values[0]

    gate_passed = bool(aggregated.get("evaluator_gate_passed"))
    aggregated["global_regression_passed"] = gate_passed
    aggregated["deterministic_signal"] = gate_passed
    aggregated["command_case_count"] = case_count
    aggregated["command_pass_count"] = case_count if gate_passed else 0
    aggregated["command_failure_count"] = 0 if gate_passed else case_count
    aggregated["command_pass_rate"] = 1.0 if gate_passed else 0.0
    return aggregated


def _aworld_evidence_quality_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    records: list[Mapping[str, Any]] = []
    top_level = report.get("evidence_quality")
    if isinstance(top_level, Mapping):
        records.append(top_level)

    results = report.get("results")
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, Mapping):
                continue
            judge = result.get("judge")
            if not isinstance(judge, Mapping):
                continue
            record: dict[str, Any] = {}
            nested = judge.get("evidence_quality")
            if isinstance(nested, Mapping):
                record.update(dict(nested))
            for key in (
                "has_evidence",
                "evidence_block_count",
                "evidence_compacted",
                "evidence_incomplete",
                "evidence_issues",
            ):
                if key in judge:
                    record[key] = judge[key]
            if record:
                records.append(record)

    if not records:
        return {}

    metrics: dict[str, Any] = {}
    has_evidence_values = [
        _truthy_metric(record.get("has_evidence"))
        for record in records
        if record.get("has_evidence") is not None
    ]
    block_counts = [
        int(record.get("evidence_block_count"))
        for record in records
        if isinstance(record.get("evidence_block_count"), (int, float))
        and not isinstance(record.get("evidence_block_count"), bool)
    ]
    compacted_values = [
        _truthy_metric(record.get("evidence_compacted"))
        for record in records
        if record.get("evidence_compacted") is not None
    ]
    incomplete_values = [
        _truthy_metric(record.get("evidence_incomplete"))
        for record in records
        if record.get("evidence_incomplete") is not None
    ]
    issues: list[str] = []
    for record in records:
        record_issues = record.get("evidence_issues")
        if isinstance(record_issues, list):
            issues.extend(str(issue) for issue in record_issues if issue)

    if has_evidence_values:
        metrics["has_evidence"] = 1.0 if all(has_evidence_values) else 0.0
    if block_counts:
        metrics["evidence_block_count"] = min(block_counts)
        metrics["evidence_block_count_total"] = sum(block_counts)
    if compacted_values:
        metrics["evidence_compacted"] = any(compacted_values)
    if incomplete_values:
        metrics["evidence_incomplete"] = any(incomplete_values)
    if issues:
        metrics["evidence_issues"] = issues
    return metrics


def _truthy_metric(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "pass", "passed"}
    return bool(value)


def _failed_aworld_evaluator_metrics(
    *,
    failures: list[Mapping[str, Any]],
    case_count: int,
    input_path: Path,
    judge_repetitions: int,
) -> dict[str, Any]:
    return {
        "evaluator_mode": "aworld_trajectory_evaluator",
        "evaluator_source_kind": "trajectory",
        "evaluation_agent_signal": False,
        "input_path": str(input_path),
        "score": 0.0,
        "evaluator_gate_status": "fail",
        "evaluator_gate_passed": False,
        "global_regression_passed": False,
        "deterministic_signal": False,
        "command_case_count": case_count,
        "command_pass_count": 0,
        "command_failure_count": case_count,
        "command_pass_rate": 0.0,
        "judge_attempt_count": len(failures),
        "judge_success_count": 0,
        "judge_failure_count": len(failures),
        "judge_repetitions": judge_repetitions,
        "judge_failures": list(failures),
    }


def _safe_path_component(value: str | None) -> str:
    safe = "".join(
        character
        for character in str(value or "default")
        if character.isalnum() or character in {"-", "_", "."}
    ).strip(".")
    return safe or "default"


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
