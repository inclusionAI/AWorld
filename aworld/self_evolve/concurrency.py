from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, PositiveInt

from aworld.core.task import Task, TaskResponse
from aworld.runners.batch import (
    DeterministicTaskBatchExecutor,
    TaskBatchItem,
    TaskBatchResult,
)
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationInfrastructureError,
)


SelfEvolveStage = Literal[
    "candidate_generation",
    "candidate_screening",
    "replay",
    "evaluation",
]
CandidatePopulationStatus = Literal[
    "succeeded",
    "protocol_invalid",
    "failed",
    "discarded",
]


class SelfEvolveConcurrencyPolicy(BaseModel):
    """Opt-in local concurrency limits for self-evolve stage batches."""

    model_config = ConfigDict(frozen=True)

    max_total_concurrency: PositiveInt = 2
    candidate_generation_concurrency: PositiveInt = 2
    replay_concurrency: PositiveInt = 2
    judge_concurrency: PositiveInt = 2
    candidate_screening_concurrency: PositiveInt = 1

    def effective_limit(self, stage: SelfEvolveStage, *, item_count: int) -> int:
        if isinstance(item_count, bool) or item_count < 0:
            raise ValueError("item_count must be non-negative")
        if item_count == 0:
            return 0
        stage_limit = {
            "candidate_generation": self.candidate_generation_concurrency,
            "candidate_screening": self.candidate_screening_concurrency,
            "replay": self.replay_concurrency,
            "evaluation": self.judge_concurrency,
        }[stage]
        return min(self.max_total_concurrency, stage_limit, item_count)


class SelfEvolveExecutionTelemetry:
    """Bounded, content-free observability for self-evolve stage batches."""

    def __init__(self) -> None:
        self._records: dict[str, list[dict[str, Any]]] = {}

    def record(self, stage: SelfEvolveStage, observability: Mapping[str, Any]) -> None:
        allowed = {
            "item_count",
            "configured_concurrency",
            "effective_concurrency",
            "max_observed_concurrency",
            "failure_cutoff_index",
            "resource_serialized_count",
            "queue_wait_seconds",
            "execution_seconds",
            "elapsed_seconds",
            "mode",
            "repair_count",
            "repair_attempt_count",
            "repair_success_count",
            "repair_protocol_invalid_count",
            "repair_infrastructure_failure_count",
            "initial_cancelled_count",
            "initial_discarded_count",
            "repair_cancelled_count",
            "repair_discarded_count",
            "initial_queue_wait_seconds",
            "initial_execution_seconds",
            "repair_queue_wait_seconds",
            "repair_execution_seconds",
        }
        record = {
            key: value
            for key, value in observability.items()
            if key in allowed
            and isinstance(value, (str, int, float, type(None)))
            and not isinstance(value, bool)
        }
        for key in ("token_usage", "initial_token_usage", "repair_token_usage"):
            usage = observability.get(key)
            if isinstance(usage, Mapping):
                record[key] = _bounded_token_usage(usage)
        self._records.setdefault(stage, []).append(record)

    def to_report(self) -> dict[str, Mapping[str, Any]]:
        report: dict[str, Mapping[str, Any]] = {}
        for stage, records in self._records.items():
            stage_report: dict[str, Any] = {
                "batch_count": len(records),
                "item_count": sum(_telemetry_int(item, "item_count") for item in records),
                "configured_concurrency": max(
                    (_telemetry_int(item, "configured_concurrency") for item in records),
                    default=0,
                ),
                "effective_concurrency": max(
                    (_telemetry_int(item, "effective_concurrency") for item in records),
                    default=0,
                ),
                "max_observed_concurrency": max(
                    (_telemetry_int(item, "max_observed_concurrency") for item in records),
                    default=0,
                ),
                "resource_serialized_count": sum(
                    _telemetry_int(item, "resource_serialized_count")
                    for item in records
                ),
                "queue_wait_seconds": sum(
                    _telemetry_float(item, "queue_wait_seconds") for item in records
                ),
                "execution_seconds": sum(
                    _telemetry_float(item, "execution_seconds") for item in records
                ),
                "elapsed_seconds": sum(
                    _telemetry_float(item, "elapsed_seconds") for item in records
                ),
                "failure_cutoff_indexes": [
                    item["failure_cutoff_index"]
                    for item in records
                    if isinstance(item.get("failure_cutoff_index"), int)
                ],
                "batches": records,
            }
            for key in _SUMMED_TELEMETRY_INT_KEYS:
                stage_report[key] = sum(_telemetry_int(item, key) for item in records)
            for key in _SUMMED_TELEMETRY_FLOAT_KEYS:
                stage_report[key] = sum(
                    _telemetry_float(item, key) for item in records
                )
            for key in ("token_usage", "initial_token_usage", "repair_token_usage"):
                usage = _aggregate_token_usage(records, key)
                if usage:
                    stage_report[key] = usage
            report[stage] = stage_report
        return report


_SUMMED_TELEMETRY_INT_KEYS = (
    "repair_count",
    "repair_attempt_count",
    "repair_success_count",
    "repair_protocol_invalid_count",
    "repair_infrastructure_failure_count",
    "initial_cancelled_count",
    "initial_discarded_count",
    "repair_cancelled_count",
    "repair_discarded_count",
)

_SUMMED_TELEMETRY_FLOAT_KEYS = (
    "initial_queue_wait_seconds",
    "initial_execution_seconds",
    "repair_queue_wait_seconds",
    "repair_execution_seconds",
)

_ALLOWED_TOKEN_USAGE_KEYS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
    }
)


def _bounded_token_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    return {
        key: value
        for key, value in sorted(usage.items())
        if key in _ALLOWED_TOKEN_USAGE_KEYS
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }


def _aggregate_token_usage(
    records: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for record in records:
        usage = record.get(key)
        if not isinstance(usage, Mapping):
            continue
        for usage_key, value in _bounded_token_usage(usage).items():
            totals[usage_key] = totals.get(usage_key, 0) + value
    return dict(sorted(totals.items()))


def _telemetry_int(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    return item if isinstance(item, int) and not isinstance(item, bool) else 0


def _telemetry_float(value: Mapping[str, Any], key: str) -> float:
    item = value.get(key)
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return float(item)
    return 0.0


@dataclass(frozen=True)
class CandidatePopulationSlotResult:
    index: int
    status: CandidatePopulationStatus
    output: Mapping[str, Any] | None = None
    failure: Mapping[str, Any] | None = None
    repaired: bool = False


@dataclass(frozen=True)
class CandidatePopulationResult:
    slots: tuple[CandidatePopulationSlotResult, ...]
    diagnostics: Mapping[str, Any]


class CandidateTaskAgent(Protocol):
    def build_task(self, prompt: str, *, task_id: str | None = None) -> Task: ...

    def candidate_response_from_task(
        self,
        task: Task,
        response: TaskResponse | None,
    ) -> str: ...

    def pop_task_failure(
        self,
        task: Task,
    ) -> CandidateGenerationInfrastructureError | None: ...


CandidateAgentFactory = Callable[[int], CandidateTaskAgent]
CandidateOutputParser = Callable[[str], Mapping[str, Any]]
CandidateRepairPromptBuilder = Callable[[str, ValueError], str]


class AWorldCandidatePopulationExecutor:
    """Execute model-backed candidate slots with standard AWorld Tasks."""

    def __init__(
        self,
        *,
        agent_factory: CandidateAgentFactory,
        parse_output: CandidateOutputParser,
        repair_prompt_builder: CandidateRepairPromptBuilder,
        task_batch_executor: DeterministicTaskBatchExecutor | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._parse_output = parse_output
        self._repair_prompt_builder = repair_prompt_builder
        self._task_batch_executor = (
            task_batch_executor or DeterministicTaskBatchExecutor()
        )

    async def run(
        self,
        prompts: Sequence[str],
        *,
        max_concurrency: int,
    ) -> CandidatePopulationResult:
        started_at = time.monotonic()
        if not prompts:
            return CandidatePopulationResult(
                slots=(),
                diagnostics={
                    "mode": "model_task_batch",
                    "item_count": 0,
                    "configured_concurrency": max_concurrency,
                    "effective_concurrency": 0,
                    "max_observed_concurrency": 0,
                    "failure_cutoff_index": None,
                    "statuses": [],
                    "repair_count": 0,
                    "repair_attempt_count": 0,
                    "repair_success_count": 0,
                    "repair_protocol_invalid_count": 0,
                    "repair_infrastructure_failure_count": 0,
                    "protocol_invalid_count": 0,
                    "queue_wait_seconds": 0.0,
                    "execution_seconds": 0.0,
                    "initial_queue_wait_seconds": 0.0,
                    "initial_execution_seconds": 0.0,
                    "repair_queue_wait_seconds": 0.0,
                    "repair_execution_seconds": 0.0,
                    "initial_cancelled_count": 0,
                    "initial_discarded_count": 0,
                    "repair_cancelled_count": 0,
                    "repair_discarded_count": 0,
                    "resource_serialized_count": 0,
                    "elapsed_seconds": 0.0,
                    "initial_token_usage": {},
                    "repair_token_usage": {},
                    "token_usage": {},
                },
            )

        population_id = uuid.uuid4().hex
        agents = [self._agent_factory(index) for index in range(len(prompts))]
        tasks = [
            agent.build_task(
                prompt,
                task_id=f"self-evolve-candidate-{population_id}-{index}",
            )
            for index, (agent, prompt) in enumerate(zip(agents, prompts))
        ]
        initial_results = await self._task_batch_executor.run(
            [TaskBatchItem(index=index, task=task) for index, task in enumerate(tasks)],
            max_concurrency=max_concurrency,
            failure_policy="indexed_fail_fast",
        )
        initial_observability = dict(
            self._task_batch_executor.last_run_observability
        )

        outputs: dict[int, Mapping[str, Any]] = {}
        failures: dict[int, Mapping[str, str]] = {}
        protocol_failures: dict[int, Mapping[str, Any]] = {}
        discarded: set[int] = {
            result.index for result in initial_results if result.status == "discarded"
        }
        repair_inputs: dict[int, tuple[str, ValueError]] = {}
        failure_cutoff = _failure_cutoff(initial_observability)

        for result in initial_results:
            if result.status == "discarded":
                continue
            agent = agents[result.index]
            task = tasks[result.index]
            try:
                raw_output = _read_candidate_task_result(agent, task, result)
            except CandidateGenerationInfrastructureError as exc:
                failures[result.index] = exc.to_diagnostic()
                failure_cutoff = _minimum_index(failure_cutoff, result.index)
                continue
            try:
                outputs[result.index] = self._parse_output(raw_output)
            except ValueError as exc:
                repair_inputs[result.index] = (raw_output, exc)

        repaired_indexes: set[int] = set()
        successful_repair_indexes: set[int] = set()
        repair_infrastructure_failures: set[int] = set()
        repair_results: list[TaskBatchResult] = []
        repair_observability: Mapping[str, Any] = {}
        eligible_repairs = {
            index: value
            for index, value in repair_inputs.items()
            if failure_cutoff is None or index < failure_cutoff
        }
        if eligible_repairs:
            repair_tasks = {
                index: agents[index].build_task(
                    self._repair_prompt_builder(invalid_output, error),
                    task_id=(
                        f"self-evolve-candidate-{population_id}-{index}-repair"
                    ),
                )
                for index, (invalid_output, error) in eligible_repairs.items()
            }
            repair_results = await self._task_batch_executor.run(
                [
                    TaskBatchItem(index=index, task=repair_tasks[index])
                    for index in sorted(repair_tasks)
                ],
                max_concurrency=max_concurrency,
                failure_policy="indexed_fail_fast",
            )
            repair_observability = dict(
                self._task_batch_executor.last_run_observability
            )
            repair_cutoff = _failure_cutoff(repair_observability)
            failure_cutoff = _minimum_index(failure_cutoff, repair_cutoff)
            for result in repair_results:
                index = result.index
                if result.status == "discarded":
                    discarded.add(index)
                    continue
                try:
                    raw_output = _read_candidate_task_result(
                        agents[index],
                        repair_tasks[index],
                        result,
                    )
                except CandidateGenerationInfrastructureError as exc:
                    failures[index] = exc.to_diagnostic()
                    repair_infrastructure_failures.add(index)
                    failure_cutoff = _minimum_index(failure_cutoff, index)
                    continue
                repaired_indexes.add(index)
                try:
                    outputs[index] = self._parse_output(raw_output)
                    successful_repair_indexes.add(index)
                except ValueError as exc:
                    protocol_failures[index] = _candidate_protocol_diagnostic(exc)

        if failure_cutoff is not None:
            for index in range(failure_cutoff + 1, len(prompts)):
                discarded.add(index)
                outputs.pop(index, None)
                failures.pop(index, None)
                protocol_failures.pop(index, None)

        slots: list[CandidatePopulationSlotResult] = []
        for index in range(len(prompts)):
            if index in discarded or (
                failure_cutoff is not None and index > failure_cutoff
            ):
                slots.append(
                    CandidatePopulationSlotResult(index=index, status="discarded")
                )
            elif index in failures:
                slots.append(
                    CandidatePopulationSlotResult(
                        index=index,
                        status="failed",
                        failure=failures[index],
                    )
                )
            elif index in protocol_failures:
                slots.append(
                    CandidatePopulationSlotResult(
                        index=index,
                        status="protocol_invalid",
                        failure=protocol_failures[index],
                        repaired=index in repaired_indexes,
                    )
                )
            else:
                slots.append(
                    CandidatePopulationSlotResult(
                        index=index,
                        status="succeeded",
                        output=outputs.get(index, {}),
                        repaired=index in repaired_indexes,
                    )
                )

        diagnostics = {
            "mode": "model_task_batch",
            "item_count": len(slots),
            "configured_concurrency": max_concurrency,
            "effective_concurrency": initial_observability.get(
                "effective_concurrency", 0
            ),
            "max_observed_concurrency": max(
                int(initial_observability.get("max_observed_concurrency") or 0),
                int(repair_observability.get("max_observed_concurrency") or 0),
            ),
            "failure_cutoff_index": failure_cutoff,
            "statuses": [slot.status for slot in slots],
            "repair_count": len(repaired_indexes),
            "repair_attempt_count": len(eligible_repairs),
            "repair_success_count": sum(
                1
                for index in successful_repair_indexes
                if index not in discarded
                and (failure_cutoff is None or index <= failure_cutoff)
            ),
            "repair_protocol_invalid_count": len(protocol_failures),
            "repair_infrastructure_failure_count": len(
                repair_infrastructure_failures
            ),
            "protocol_invalid_count": len(protocol_failures),
            "queue_wait_seconds": sum(
                result.queue_wait_seconds for result in initial_results
            ),
            "execution_seconds": sum(
                result.execution_seconds for result in initial_results
            ),
            "resource_serialized_count": sum(
                1 for result in initial_results if result.serialized_by_resource
            ),
            "initial_queue_wait_seconds": sum(
                result.queue_wait_seconds for result in initial_results
            ),
            "initial_execution_seconds": sum(
                result.execution_seconds for result in initial_results
            ),
            "repair_queue_wait_seconds": sum(
                result.queue_wait_seconds for result in repair_results
            ),
            "repair_execution_seconds": sum(
                result.execution_seconds for result in repair_results
            ),
            "initial_cancelled_count": _batch_status_count(
                initial_results, "cancelled"
            ),
            "initial_discarded_count": _batch_status_count(
                initial_results, "discarded"
            ),
            "repair_cancelled_count": _batch_status_count(
                repair_results, "cancelled"
            ),
            "repair_discarded_count": _batch_status_count(
                repair_results, "discarded"
            ),
            "elapsed_seconds": time.monotonic() - started_at,
            "initial_token_usage": _candidate_task_token_usage(initial_results),
            "repair_token_usage": _candidate_task_token_usage(repair_results),
            "token_usage": _candidate_task_token_usage([*initial_results, *repair_results]),
        }
        return CandidatePopulationResult(
            slots=tuple(slots),
            diagnostics=diagnostics,
        )


def _candidate_protocol_diagnostic(error: ValueError) -> Mapping[str, Any]:
    to_diagnostic = getattr(error, "to_diagnostic", None)
    if callable(to_diagnostic):
        diagnostic = to_diagnostic()
        if isinstance(diagnostic, Mapping):
            return dict(diagnostic)
    return {
        "code": "candidate_protocol_invalid",
        "stage": "candidate_protocol",
        "failure_class": "candidate",
        "repairable": True,
    }


def _read_candidate_task_result(
    agent: CandidateTaskAgent,
    task: Task,
    result: TaskBatchResult,
) -> str:
    if result.status != "succeeded":
        failure = agent.pop_task_failure(task)
        if failure is not None:
            raise failure
        raise CandidateGenerationInfrastructureError(
            stage="task_runner",
            error_type=result.error_type or "CandidateTaskFailed",
        )
    return agent.candidate_response_from_task(task, result.response)


def _failure_cutoff(observability: Mapping[str, Any]) -> int | None:
    value = observability.get("failure_cutoff_index")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _minimum_index(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _candidate_task_token_usage(
    results: Sequence[TaskBatchResult],
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for result in results:
        for key, value in result.usage_metadata.items():
            totals[key] = totals.get(key, 0) + value
    return dict(sorted(totals.items()))


def _batch_status_count(
    results: Sequence[TaskBatchResult],
    status: str,
) -> int:
    return sum(1 for result in results if result.status == status)
