from __future__ import annotations

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
CandidatePopulationStatus = Literal["succeeded", "failed", "discarded"]


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


@dataclass(frozen=True)
class CandidatePopulationSlotResult:
    index: int
    status: CandidatePopulationStatus
    output: Mapping[str, Any] | None = None
    failure: Mapping[str, str] | None = None
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
        if not prompts:
            return CandidatePopulationResult(
                slots=(),
                diagnostics={
                    "mode": "model_task_batch",
                    "configured_concurrency": max_concurrency,
                    "effective_concurrency": 0,
                    "max_observed_concurrency": 0,
                    "failure_cutoff_index": None,
                    "statuses": [],
                    "repair_count": 0,
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
                repair_inputs[result.index] = (prompts[result.index], exc)

        repaired_indexes: set[int] = set()
        repair_observability: Mapping[str, Any] = {}
        eligible_repairs = {
            index: value
            for index, value in repair_inputs.items()
            if failure_cutoff is None or index < failure_cutoff
        }
        if eligible_repairs:
            repair_tasks = {
                index: agents[index].build_task(
                    self._repair_prompt_builder(original_prompt, error),
                    task_id=(
                        f"self-evolve-candidate-{population_id}-{index}-repair"
                    ),
                )
                for index, (original_prompt, error) in eligible_repairs.items()
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
                    failure_cutoff = _minimum_index(failure_cutoff, index)
                    continue
                repaired_indexes.add(index)
                try:
                    outputs[index] = self._parse_output(raw_output)
                except ValueError:
                    # A second schema violation is a protocol-invalid candidate, not an
                    # infrastructure failure. The mutator will count this bounded empty
                    # payload as an invalid candidate.
                    outputs[index] = {}

        if failure_cutoff is not None:
            for index in range(failure_cutoff + 1, len(prompts)):
                discarded.add(index)
                outputs.pop(index, None)
                failures.pop(index, None)

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
        }
        return CandidatePopulationResult(
            slots=tuple(slots),
            diagnostics=diagnostics,
        )


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
