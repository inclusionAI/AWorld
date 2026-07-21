from __future__ import annotations

import asyncio
import contextvars
import hashlib
import inspect
import json
import os
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aworld.config.conf import EvaluationConfig
from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.runners.batch import (
    DeterministicTaskBatchExecutor,
    TaskBatchItem,
    TaskResourceClaim,
)
from aworld.runners.evaluate_runner import EvaluateRunner
from aworld.self_evolve.budget import (
    BudgetEstimateConfidence,
    BudgetEstimateSource,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
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
    estimated_tokens: int | None
    estimated_cost_usd: float | None = None
    estimated_tokens_per_replay: int | None = None
    estimate_source: BudgetEstimateSource = BudgetEstimateSource.UNKNOWN
    estimate_confidence: BudgetEstimateConfidence = BudgetEstimateConfidence.UNKNOWN
    estimate_known: bool | None = None
    token_ceiling: int | None = None

    def __post_init__(self) -> None:
        known = (
            self.estimated_tokens is not None
            if self.estimate_known is None
            else self.estimate_known
        )
        if known != (self.estimated_tokens is not None):
            raise ValueError(
                "estimate_known must agree with estimated_tokens availability"
            )
        source = BudgetEstimateSource(self.estimate_source)
        confidence = BudgetEstimateConfidence(self.estimate_confidence)
        if known and source is BudgetEstimateSource.UNKNOWN:
            source = BudgetEstimateSource.CONFIGURED_COLD_START
        if known and confidence is BudgetEstimateConfidence.UNKNOWN:
            confidence = BudgetEstimateConfidence.LOW
        if not known and (
            source is not BudgetEstimateSource.UNKNOWN
            or confidence is not BudgetEstimateConfidence.UNKNOWN
        ):
            raise ValueError("unknown replay token estimate must use unknown metadata")
        if self.token_ceiling is not None and self.token_ceiling <= 0:
            raise ValueError("token_ceiling must be positive")
        if self.estimated_tokens_per_replay is not None and (
            self.estimated_tokens_per_replay < 0
            or isinstance(self.estimated_tokens_per_replay, bool)
        ):
            raise ValueError("estimated_tokens_per_replay must be non-negative")
        object.__setattr__(self, "estimate_known", known)
        object.__setattr__(self, "estimate_source", source)
        object.__setattr__(self, "estimate_confidence", confidence)


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

    task_local_runtime = True

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

        for case in _evaluation_cases_for_split(request):
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

    task_local_runtime = True

    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        case_count = 0
        failed_step_count = 0
        completed_case_count = 0
        total_step_count = 0

        for case in _evaluation_cases_for_split(request):
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

    task_local_runtime = True

    async def evaluate_variant(self, request: EvaluationRequest) -> EvaluationSummary:
        candidate = request.candidate
        candidate_changed = bool(
            candidate is not None
            and any(
                heading in candidate.content
                for heading in (
                    "## Runtime Behavior Delta",
                    "## Self-Evolve Trace Guidance",
                )
            )
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
        judge_model_profile: str | None = None,
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
        self.judge_model_profile = judge_model_profile
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

    @property
    def task_local_runtime(self) -> bool:
        return self.run_evaluator_source is None

    async def evaluate_variant_in_task(
        self,
        request: EvaluationRequest,
    ) -> EvaluationSummary:
        if not self.task_local_runtime:
            return await self.evaluate_variant(request)
        token = _ISOLATED_EVALUATOR_TASK.set(True)
        try:
            return await self.evaluate_variant(request)
        finally:
            _ISOLATED_EVALUATOR_TASK.reset(token)

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
        records = _aworld_trajectory_records_for_request(request)
        evaluation_cases = _evaluation_cases_for_split(request)
        original_case_count = len(evaluation_cases)
        effective_case_count = len(records)
        deduplicated_case_count = max(0, original_case_count - effective_case_count)
        log_path.write_text(
            "\n".join(repr(record) for record in records) + "\n",
            encoding="utf-8",
        )
        if not records:
            metrics = _failed_aworld_evaluator_metrics(
                failures=[],
                case_count=0,
                input_path=log_path,
                judge_repetitions=self.judge_repetitions,
            )
            metrics.update(
                {
                    "evaluation_case_count": 0,
                    "original_case_count": 0,
                    "effective_case_count": 0,
                    "deduplicated_case_count": 0,
                    "evaluation_skip_reason": "dataset split has no evaluation cases",
                }
            )
            return EvaluationSummary(
                variant_id=request.variant_id,
                dataset_split=request.dataset_split,
                metrics=metrics,
            )
        report_path = eval_dir / "report.json"
        runner = self.run_evaluator_source or _load_run_evaluator_source_cli()
        task_id = evaluation_cases[0].case_id if len(evaluation_cases) == 1 else None
        runner_kwargs = {
            "input": str(log_path),
            "kind": "trajectory",
            "judge_agent": self.judge_agent,
            "judge_agent_name": self.judge_agent_name,
            "judge_backend_ref": self.judge_backend_ref,
            "judge_model_profile": self.judge_model_profile,
            "out_dir": str(eval_dir / "extracted"),
            "output": str(report_path),
            "task_id": task_id,
            "agent": self.agent,
            "judge_timeout_seconds": self.judge_timeout_seconds,
        }
        runtime_log_path = eval_dir / "logs"
        reports: list[Mapping[str, Any]] = []
        failures: list[Mapping[str, Any]] = []
        max_attempts = self.judge_repetitions + self.judge_failure_retries
        fallback_model_profile: str | None = None
        logger.info(
            "self_evolve.evaluator.start "
            f"variant_id={request.variant_id} split={request.dataset_split} "
            f"cases={original_case_count} effective_cases={effective_case_count} "
            f"repetitions={self.judge_repetitions} "
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
                    log_path=runtime_log_path,
                )
            except asyncio.TimeoutError as exc:
                timeout_reason = str(exc).strip()
                if not timeout_reason:
                    timeout_reason = (
                        "AWorld trajectory judge timed out after "
                        f"{self.judge_timeout_seconds:g}s"
                        if self.judge_timeout_seconds is not None
                        else "AWorld trajectory judge timed out"
                    )
                failure: dict[str, Any] = {
                    "attempt": attempt_index,
                    "type": "TimeoutError",
                    "reason": timeout_reason,
                }
                diagnostics = _judge_diagnostics_from_exception(exc)
                if diagnostics:
                    failure["diagnostics"] = diagnostics
                    timeout_phase = diagnostics[-1].get("phase")
                    if isinstance(timeout_phase, str) and timeout_phase:
                        failure["timeout_phase"] = timeout_phase
                failures.append(failure)
                logger.info(
                    "self_evolve.evaluator.attempt.end "
                    f"variant_id={request.variant_id} split={request.dataset_split} "
                    f"attempt={attempt_index}/{max_attempts} status=timeout"
                )
                continue
            except Exception as exc:
                if (
                    fallback_model_profile is None
                    and runner_kwargs.get("judge_model_profile")
                    and _is_missing_model_profile_error(exc)
                ):
                    fallback_model_profile = str(runner_kwargs["judge_model_profile"])
                    runner_kwargs["judge_model_profile"] = None
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
                case_count=effective_case_count,
                input_path=log_path,
            )
            metrics.update(
                _aworld_evaluator_case_count_metrics(
                    original_case_count=original_case_count,
                    effective_case_count=effective_case_count,
                )
            )
            metrics["judge_attempt_count"] = len(reports) + len(failures)
            metrics["judge_success_count"] = len(reports)
            metrics["judge_failure_count"] = len(failures)
            metrics["judge_repetitions"] = self.judge_repetitions
            if failures:
                metrics["judge_failures"] = failures
            if fallback_model_profile is not None:
                metrics["judge_model_profile_fallback"] = fallback_model_profile
        else:
            metrics = _failed_aworld_evaluator_metrics(
                failures=failures,
                case_count=effective_case_count,
                input_path=log_path,
                judge_repetitions=self.judge_repetitions,
            )
            metrics.update(
                _aworld_evaluator_case_count_metrics(
                    original_case_count=original_case_count,
                    effective_case_count=effective_case_count,
                )
            )
            if fallback_model_profile is not None:
                metrics["judge_model_profile_fallback"] = fallback_model_profile
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
        log_path: Path,
    ) -> Mapping[str, Any]:
        call = self._run_evaluator_source(
            runner,
            runner_kwargs=runner_kwargs,
            log_path=log_path,
        )
        if self.judge_timeout_seconds is None or self.run_evaluator_source is None:
            return await call
        return await asyncio.wait_for(call, timeout=self.judge_timeout_seconds)

    async def _run_evaluator_source(
        self,
        runner: Callable[..., Any],
        *,
        runner_kwargs: Mapping[str, Any],
        log_path: Path,
    ) -> Mapping[str, Any]:
        if self.run_evaluator_source is None and _ISOLATED_EVALUATOR_TASK.get():
            report = await asyncio.to_thread(
                _run_evaluator_cli_subprocess,
                runner_kwargs=runner_kwargs,
                log_path=log_path,
                workspace_root=self.workspace_root,
            )
            if not isinstance(report, Mapping):  # pragma: no cover - helper validates
                raise ValueError("AWorld trajectory evaluator report must be a mapping")
            return report
        with _self_evolve_runtime_log_env(log_path):
            if self.run_evaluator_source is None:
                report = await asyncio.to_thread(runner, **runner_kwargs)
            else:
                report = runner(**runner_kwargs)
                if inspect.isawaitable(report):
                    report = await report
        if not isinstance(report, Mapping):
            raise ValueError("AWorld trajectory evaluator report must be a mapping")
        return report


_ISOLATED_EVALUATOR_TASK: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "aworld_self_evolve_isolated_evaluator_task",
    default=False,
)


def _run_evaluator_cli_subprocess(
    *,
    runner_kwargs: Mapping[str, Any],
    log_path: Path,
    workspace_root: Path,
) -> Mapping[str, Any]:
    command = [sys.executable, "-m", "aworld_cli.main", "evaluator"]
    option_names = {
        "input": "--input",
        "kind": "--kind",
        "judge_agent": "--judge-agent",
        "judge_agent_name": "--judge-agent-name",
        "judge_backend_ref": "--judge-backend-ref",
        "judge_model_profile": "--judge-model-profile",
        "out_dir": "--out-dir",
        "output": "--output",
        "task_id": "--task-id",
        "agent": "--agent",
        "judge_timeout_seconds": "--judge-timeout",
    }
    for key, option in option_names.items():
        value = runner_kwargs.get(key)
        if value is not None:
            command.extend([option, str(value)])
    output = runner_kwargs.get("output")
    if not isinstance(output, str) or not output:
        raise ValueError("isolated evaluator subprocess requires output path")
    environment = os.environ.copy()
    environment["AWORLD_LOG_PATH"] = str(log_path)
    environment["AWORLD_TRAJECTORY_LOG_DISABLED"] = "1"
    timeout = runner_kwargs.get("judge_timeout_seconds")
    process_timeout = (
        float(timeout) + 30.0
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
        else None
    )
    completed = subprocess.run(
        command,
        cwd=workspace_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=process_timeout,
    )
    report_path = Path(output)
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("isolated evaluator report is invalid") from exc
        if isinstance(report, Mapping):
            return report
    process_diagnostics = []
    for label, value in (("stderr", completed.stderr), ("stdout", completed.stdout)):
        bounded = _bounded_text(str(value or "").strip())
        if bounded:
            process_diagnostics.append(f"{label}={bounded}")
    diagnostic_suffix = (
        "; " + "; ".join(process_diagnostics)
        if process_diagnostics
        else ""
    )
    raise RuntimeError(
        "isolated evaluator subprocess did not produce a report "
        f"(exit={completed.returncode}){diagnostic_suffix}"
    )


@contextmanager
def _self_evolve_runtime_log_env(log_path: Path):
    previous_log_path = os.environ.get("AWORLD_LOG_PATH")
    previous_disabled = os.environ.get("AWORLD_TRAJECTORY_LOG_DISABLED")
    os.environ["AWORLD_LOG_PATH"] = str(log_path)
    os.environ["AWORLD_TRAJECTORY_LOG_DISABLED"] = "1"
    try:
        yield
    finally:
        if previous_log_path is None:
            os.environ.pop("AWORLD_LOG_PATH", None)
        else:
            os.environ["AWORLD_LOG_PATH"] = previous_log_path
        if previous_disabled is None:
            os.environ.pop("AWORLD_TRAJECTORY_LOG_DISABLED", None)
        else:
            os.environ["AWORLD_TRAJECTORY_LOG_DISABLED"] = previous_disabled


def _is_missing_model_profile_error(exc: Exception) -> bool:
    return "model profile not found or incomplete" in str(exc)


def _judge_diagnostics_from_exception(exc: BaseException) -> list[dict[str, Any]]:
    diagnostics = getattr(exc, "judge_diagnostics", None)
    if not isinstance(diagnostics, (list, tuple)):
        return []
    return [dict(item) for item in diagnostics if isinstance(item, Mapping)]


async def evaluate_baseline_and_candidate(
    backend: EvaluationBackend,
    *,
    dataset: SelfEvolveDataset,
    candidate: CandidateVariant,
    eval_config: EvaluationConfig | None = None,
    dataset_split: str = "validation",
    baseline_variant_id: str = "baseline",
    artifact_namespace: str | None = None,
    task_batch_executor: DeterministicTaskBatchExecutor | None = None,
    max_concurrency: int = 1,
    execution_telemetry: SelfEvolveExecutionTelemetry | None = None,
) -> tuple[EvaluationSummary, EvaluationSummary]:
    requests = (
        EvaluationRequest(
            variant_id=baseline_variant_id,
            candidate=None,
            dataset=dataset,
            eval_config=eval_config,
            dataset_split=dataset_split,
            artifact_namespace=artifact_namespace,
        ),
        EvaluationRequest(
            variant_id=candidate.candidate_id,
            candidate=candidate,
            dataset=dataset,
            eval_config=eval_config,
            dataset_split=dataset_split,
            artifact_namespace=artifact_namespace,
        ),
    )
    summaries = await _execute_evaluation_requests(
        backend,
        requests=requests,
        task_batch_executor=task_batch_executor,
        max_concurrency=max_concurrency,
        artifact_namespace=artifact_namespace,
        dataset_split=dataset_split,
        execution_telemetry=execution_telemetry,
    )
    return summaries[0], summaries[1]


async def evaluate_variant_task(
    backend: EvaluationBackend,
    *,
    request: EvaluationRequest,
    task_batch_executor: DeterministicTaskBatchExecutor | None = None,
    execution_telemetry: SelfEvolveExecutionTelemetry | None = None,
) -> EvaluationSummary:
    summaries = await _execute_evaluation_requests(
        backend,
        requests=(request,),
        task_batch_executor=task_batch_executor,
        max_concurrency=1,
        artifact_namespace=request.artifact_namespace,
        dataset_split=request.dataset_split,
        execution_telemetry=execution_telemetry,
    )
    return summaries[0]


async def _execute_evaluation_requests(
    backend: EvaluationBackend,
    *,
    requests: tuple[EvaluationRequest, ...],
    task_batch_executor: DeterministicTaskBatchExecutor | None,
    max_concurrency: int,
    artifact_namespace: str | None,
    dataset_split: str,
    execution_telemetry: SelfEvolveExecutionTelemetry | None,
) -> tuple[EvaluationSummary, ...]:
    executor = task_batch_executor or DeterministicTaskBatchExecutor()
    task_local_runtime = getattr(backend, "task_local_runtime", False) is True
    resource_claims = (
        ()
        if task_local_runtime
        else (
            TaskResourceClaim(
                key=f"self-evolve-evaluation-backend:{id(backend)}",
                exclusive=True,
            ),
        )
    )
    items: list[TaskBatchItem] = []
    for index, request in enumerate(requests):
        task_id = (
            "self-evolve-evaluation-"
            f"{_safe_path_component(artifact_namespace or 'run')}-"
            f"{_safe_path_component(request.variant_id)}-"
            f"{_safe_path_component(dataset_split)}"
        )
        items.append(
            TaskBatchItem(
                index=index,
                task=Task(
                    id=task_id,
                    session_id=task_id,
                    input=EvaluationTaskInput(backend=backend, request=request),
                    context=LocalIsolatedApplicationContext.create(
                        task_id=task_id,
                        session_id=task_id,
                        task_content="isolated self-evolve evaluation request",
                    ),
                    runner_cls=(
                        "aworld.self_evolve.runtime.SelfEvolveEvaluationTaskRunner"
                    ),
                ),
                resource_claims=resource_claims,
            )
        )
    results = await executor.run(
        items,
        max_concurrency=max_concurrency,
        failure_policy="collect_all",
    )
    if execution_telemetry is not None:
        execution_telemetry.record(
            "evaluation",
            executor.last_run_observability,
        )
    summaries: list[EvaluationSummary] = []
    for result in results:
        if (
            result.status != "succeeded"
            or result.response is None
            or not isinstance(result.response.answer, EvaluationSummary)
        ):
            if result.error_type == "ValueError":
                raise ValueError(
                    f"required evaluation Task failed at index {result.index}"
                )
            if result.error_type in {"TimeoutError", "TimeoutExpired"}:
                raise TimeoutError(
                    f"required evaluation Task timed out at index {result.index}"
                )
            raise RuntimeError(
                "required evaluation Task failed "
                f"at index {result.index} ({result.error_type or 'TaskFailed'})"
            )
        summaries.append(result.response.answer)
    return tuple(summaries)


@dataclass(frozen=True)
class EvaluationTaskInput:
    backend: EvaluationBackend
    request: EvaluationRequest

    async def execute(self) -> EvaluationSummary:
        task_method = getattr(self.backend, "evaluate_variant_in_task", None)
        if callable(task_method):
            result = task_method(self.request)
        else:
            result = self.backend.evaluate_variant(self.request)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, EvaluationSummary):
            raise TypeError("evaluation backend must return EvaluationSummary")
        return result


def estimate_replay_cost(
    *,
    dataset: SelfEvolveDataset,
    candidate_count: int,
    judge_repetitions: int,
    baseline_repetitions: int = 1,
    candidate_repetitions: int = 1,
    replay_candidate_limit: int | None = None,
    estimated_tokens_per_replay: int | None = None,
    backend_proven_zero: bool = False,
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
    if estimated_tokens_per_replay is not None and (
        isinstance(estimated_tokens_per_replay, bool)
        or estimated_tokens_per_replay < 0
    ):
        raise ValueError("estimated_tokens_per_replay must be non-negative")
    if backend_proven_zero:
        if estimated_tokens_per_replay not in (None, 0):
            raise ValueError(
                "backend_proven_zero conflicts with a non-zero replay estimate"
            )
        effective_tokens_per_replay: int | None = 0
        estimate_source = BudgetEstimateSource.BACKEND_PROVEN_ZERO
        estimate_confidence = BudgetEstimateConfidence.PROVEN
    elif estimated_tokens_per_replay == 0:
        raise ValueError(
            "zero replay token estimate requires backend_proven_zero=True"
        )
    elif estimated_tokens_per_replay is None:
        effective_tokens_per_replay = None
        estimate_source = BudgetEstimateSource.UNKNOWN
        estimate_confidence = BudgetEstimateConfidence.UNKNOWN
    else:
        effective_tokens_per_replay = estimated_tokens_per_replay
        estimate_source = BudgetEstimateSource.CONFIGURED_COLD_START
        estimate_confidence = BudgetEstimateConfidence.LOW

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
    estimated_tokens = (
        total_replay_count * effective_tokens_per_replay
        if effective_tokens_per_replay is not None
        else None
    )
    estimated_cost_usd = (
        total_replay_count * estimated_cost_usd_per_replay
        if estimated_cost_usd_per_replay is not None
        else None
    )

    passed = True
    reason = "within budget"
    if max_run_tokens is not None and estimated_tokens is None:
        passed = False
        reason = "estimated replay tokens are unknown under max_run_tokens"
    elif (
        max_run_tokens is not None
        and estimated_tokens is not None
        and estimated_tokens > max_run_tokens
    ):
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
        estimated_tokens_per_replay=effective_tokens_per_replay,
        estimate_source=estimate_source,
        estimate_confidence=estimate_confidence,
        estimate_known=estimated_tokens is not None,
        token_ceiling=max_run_tokens,
    )


def determine_candidate_confidence(
    *,
    dataset: SelfEvolveDataset,
    validation_summary: EvaluationSummary,
    held_out_summary: EvaluationSummary | None,
    min_eval_cases: int,
) -> CandidateConfidenceDecision:
    independent_held_out_count = dataset.recipe.source.get("held_out_member_count")
    held_out_case_count = (
        int(independent_held_out_count)
        if isinstance(independent_held_out_count, int)
        and not isinstance(independent_held_out_count, bool)
        and independent_held_out_count >= 0
        else len(dataset.recipe.held_out_case_ids)
    )
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

    if (
        held_out_summary is not None
        and held_out_case_count > 0
        and deterministic_signal_present
        and _has_trajectory_set_validation_source(dataset)
    ):
        return CandidateConfidenceDecision(
            confidence="verified",
            reason="trajectory-set validation is sufficient",
            selection_split=selection_split,
            verification_split="trajectory_set_validation",
            deterministic_signal_present=True,
            held_out_case_count=held_out_case_count,
            verification_mode="trajectory_set_validation",
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


def _has_trajectory_set_validation_source(dataset: SelfEvolveDataset) -> bool:
    source = dataset.recipe.source
    if source.get("kind") == "trajectory_set":
        return True
    auto_grouping = source.get("auto_grouping")
    if not isinstance(auto_grouping, Mapping):
        return False
    if auto_grouping.get("auto_grouped") is not True:
        return False
    selected_count = auto_grouping.get("selected_case_count")
    return isinstance(selected_count, int) and selected_count > 1


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


def _aworld_trajectory_records_for_request(
    request: EvaluationRequest,
) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    seen_replay_fingerprints: set[str] = set()
    for case in _evaluation_cases_for_split(request):
        record = _aworld_trajectory_record(case, request=request)
        if _case_uses_replay_variant(case):
            fingerprint = _aworld_trajectory_record_fingerprint(record)
            if fingerprint in seen_replay_fingerprints:
                continue
            seen_replay_fingerprints.add(fingerprint)
        records.append(record)
    return records


def _evaluation_cases_for_split(request: EvaluationRequest) -> tuple[Any, ...]:
    cases = request.dataset.cases
    split = request.dataset_split
    if split in {"", "all", "post_apply", "single_case_replay"}:
        return cases

    recipe = request.dataset.recipe
    if split == "validation":
        selected_ids = tuple(recipe.splits.get("validation", ()))
        if not selected_ids:
            selected_ids = recipe.trainable_case_ids or tuple(
                recipe.splits.get("train", ())
            )
        if not selected_ids:
            return cases
    elif split == "held_out":
        selected_ids = recipe.held_out_case_ids or tuple(
            recipe.splits.get("held_out", ())
        )
    else:
        selected_ids = tuple(recipe.splits.get(split, ()))

    selected = set(selected_ids)
    return tuple(case for case in cases if case.case_id in selected)


def _aworld_trajectory_record(
    case: Any,
    *,
    request: EvaluationRequest,
) -> Mapping[str, Any]:
    trajectory = _trajectory_for_variant(case, request=request)
    record: dict[str, Any] = {
        "task_id": case.case_id,
        "is_sub_task": False,
        "trajectory": json.dumps(trajectory, ensure_ascii=False),
    }
    evidence_bundle_path = _evidence_bundle_path_for_variant(case, request=request)
    if evidence_bundle_path:
        record["evidence_bundle_path"] = evidence_bundle_path
    return record


def _case_uses_replay_variant(case: Any) -> bool:
    metadata = case.metadata if isinstance(case.metadata, Mapping) else {}
    return any(
        key in metadata
        for key in (
            "variant_trajectories",
            "baseline_trajectory",
            "candidate_trajectory",
            "replay",
        )
    )


def _aworld_trajectory_record_fingerprint(record: Mapping[str, Any]) -> str:
    payload = {
        "trajectory": record.get("trajectory"),
        "evidence_bundle_path": record.get("evidence_bundle_path"),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _evidence_bundle_path_for_variant(case: Any, *, request: EvaluationRequest) -> str | None:
    metadata = case.metadata if isinstance(case.metadata, Mapping) else {}
    replay = metadata.get("replay")
    if not isinstance(replay, Mapping):
        return None
    variant_keys = [request.variant_id]
    if request.candidate is not None:
        variant_keys.extend([request.candidate.candidate_id, "candidate"])
    else:
        variant_keys.extend(["baseline"])
    for key in variant_keys:
        block = replay.get(key)
        if not isinstance(block, Mapping):
            continue
        metrics = block.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        path = metrics.get("evidence_bundle_path") or metrics.get("replay_evidence_bundle_path")
        if isinstance(path, str) and path.strip():
            return path
    return None


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
    metrics.update(_aworld_judge_diagnostic_metrics(report))

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


def _aworld_evaluator_case_count_metrics(
    *,
    original_case_count: int,
    effective_case_count: int,
) -> dict[str, int]:
    return {
        "original_case_count": original_case_count,
        "effective_case_count": effective_case_count,
        "deduplicated_case_count": max(0, original_case_count - effective_case_count),
    }


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
    combined_judge_diagnostics: list[dict[str, Any]] = []
    for key in sorted(keys):
        values = [metrics[key] for metrics in per_run if key in metrics]
        if not values:
            continue
        if key == "judge_call_diagnostics":
            for value in values:
                if not isinstance(value, list):
                    continue
                combined_judge_diagnostics.extend(
                    dict(item) for item in value if isinstance(item, Mapping)
                )
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

    if combined_judge_diagnostics:
        aggregated.update(_summarize_judge_diagnostics(combined_judge_diagnostics))

    gate_passed = bool(aggregated.get("evaluator_gate_passed"))
    aggregated["global_regression_passed"] = gate_passed
    aggregated["deterministic_signal"] = gate_passed
    aggregated["command_case_count"] = case_count
    aggregated["command_pass_count"] = case_count if gate_passed else 0
    aggregated["command_failure_count"] = 0 if gate_passed else case_count
    aggregated["command_pass_rate"] = 1.0 if gate_passed else 0.0
    return aggregated


def _aworld_judge_diagnostic_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    results = report.get("results")
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, Mapping):
                continue
            case_diagnostics = result.get("judge_diagnostics")
            if not isinstance(case_diagnostics, list):
                continue
            diagnostics.extend(
                dict(item) for item in case_diagnostics if isinstance(item, Mapping)
            )
    return _summarize_judge_diagnostics(diagnostics)


def _summarize_judge_diagnostics(
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    if not diagnostics:
        return {}

    def _number(item: Mapping[str, Any], key: str) -> float:
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return 0.0

    latencies = [_number(item, "latency_ms") for item in diagnostics]
    return {
        "judge_call_diagnostics": [dict(item) for item in diagnostics],
        "judge_call_count": len(diagnostics),
        "judge_artifact_read_round_count": sum(
            1 for item in diagnostics if str(item.get("phase", "")).startswith("artifact_read_round_")
        ),
        "judge_artifact_request_count": int(
            sum(_number(item, "artifact_request_count") for item in diagnostics)
        ),
        "judge_artifact_read_count": int(
            sum(_number(item, "artifact_read_count") for item in diagnostics)
        ),
        "judge_artifact_read_chars": int(
            sum(_number(item, "artifact_read_chars") for item in diagnostics)
        ),
        "judge_prompt_chars_total": int(
            sum(_number(item, "prompt_chars") for item in diagnostics)
        ),
        "judge_estimated_input_tokens_total": int(
            sum(_number(item, "estimated_input_tokens") for item in diagnostics)
        ),
        "judge_model_latency_ms_total": sum(latencies),
        "judge_model_latency_ms_max": max(latencies, default=0.0),
        "judge_timeout_count": sum(
            1 for item in diagnostics if item.get("status") == "timed_out"
        ),
    }


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
