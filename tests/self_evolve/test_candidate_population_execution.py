from __future__ import annotations

import asyncio
import json

import pytest

from aworld.core.common import TaskStatusValue
from aworld.core.task import Task, TaskResponse
from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationInfrastructureError,
)
from aworld.self_evolve.concurrency import (
    AWorldCandidatePopulationExecutor,
    SelfEvolveConcurrencyPolicy,
)
from aworld.self_evolve.datasets import EvalCase
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.types import SelfEvolveTargetRef


def _request(max_candidates: int = 4) -> OptimizerRequest:
    return OptimizerRequest(
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path="/tmp/demo/SKILL.md",
        ),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(),
        trainable_cases=(EvalCase(case_id="train-1", input="task"),),
        max_candidates=max_candidates,
    )


class _FakeCandidateAgent:
    def __init__(self, slot: int) -> None:
        self.slot = slot
        self.tasks: list[Task] = []
        self.failure: CandidateGenerationInfrastructureError | None = None

    def build_task(self, prompt: str, *, task_id: str | None = None) -> Task:
        task = Task(id=task_id, input=prompt, agent=self)
        self.tasks.append(task)
        return task

    def candidate_response_from_task(
        self,
        task: Task,
        response: TaskResponse | None,
    ) -> str:
        if response is None or not response.success:
            raise CandidateGenerationInfrastructureError(
                stage="task_runner",
                error_type="CandidateTaskFailed",
            )
        return str(response.answer)

    def pop_task_failure(
        self,
        task: Task,
    ) -> CandidateGenerationInfrastructureError | None:
        return self.failure


def _population_callable(executor: AWorldCandidatePopulationExecutor):
    async def run(prompts, max_concurrency):
        return await executor.run(prompts, max_concurrency=max_concurrency)

    return run


@pytest.mark.asyncio
async def test_model_backed_population_uses_aworld_tasks_and_stable_slot_order() -> None:
    active = 0
    max_active = 0

    async def run_task(task: Task):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        slot = task.agent.slot
        await asyncio.sleep(0.01 * (4 - slot))
        active -= 1
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                status=TaskStatusValue.SUCCESS,
                answer=json.dumps(
                    {
                        "content": f"# Demo\n\nCandidate slot {slot}.\n",
                        "rationale": f"slot-{slot}",
                    }
                ),
            )
        }

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=json.loads,
        repair_prompt_builder=lambda prompt, error: f"{prompt}\nrepair: {error}",
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )
    optimizer = TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=_population_callable(executor),
        concurrency_policy=SelfEvolveConcurrencyPolicy(
            max_total_concurrency=2,
            candidate_generation_concurrency=2,
        ),
    )

    result = await optimizer.propose(_request())

    assert max_active == 2
    assert [candidate.rationale for candidate in result.candidates] == [
        "slot-0",
        "slot-1",
        "slot-2",
        "slot-3",
    ]
    assert result.diagnostics["candidate_population_execution"][
        "max_observed_concurrency"
    ] == 2


@pytest.mark.asyncio
async def test_model_backed_population_discards_failure_slot_and_higher_results() -> None:
    agents: dict[int, _FakeCandidateAgent] = {}
    completed: list[int] = []

    def agent_factory(slot: int) -> _FakeCandidateAgent:
        agents[slot] = _FakeCandidateAgent(slot)
        return agents[slot]

    async def run_task(task: Task):
        slot = task.agent.slot
        if slot == 1:
            await asyncio.sleep(0.02)
            task.agent.failure = CandidateGenerationInfrastructureError(
                stage="model_provider",
                error_type="APIConnectionError",
            )
            raise task.agent.failure
        await asyncio.sleep(0.001 if slot == 2 else 0.005)
        completed.append(slot)
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=json.dumps(
                    {
                        "content": f"# Demo\n\nCandidate slot {slot}.\n",
                        "rationale": f"slot-{slot}",
                    }
                ),
            )
        }

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=agent_factory,
        parse_output=json.loads,
        repair_prompt_builder=lambda prompt, error: prompt,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )
    optimizer = TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=_population_callable(executor),
        concurrency_policy=SelfEvolveConcurrencyPolicy(
            max_total_concurrency=3,
            candidate_generation_concurrency=3,
        ),
    )

    result = await optimizer.propose(_request(max_candidates=4))

    assert 2 in completed
    assert [candidate.rationale for candidate in result.candidates] == ["slot-0"]
    assert result.diagnostics["candidate_generation_failure"]["error_type"] == (
        "APIConnectionError"
    )
    assert result.diagnostics["candidate_population_execution"][
        "failure_cutoff_index"
    ] == 1
    assert result.diagnostics["candidate_population_execution"]["statuses"] == [
        "succeeded",
        "failed",
        "discarded",
        "discarded",
    ]


@pytest.mark.asyncio
async def test_schema_repair_reuses_the_same_slot_agent() -> None:
    agents: dict[int, _FakeCandidateAgent] = {}

    def agent_factory(slot: int) -> _FakeCandidateAgent:
        agents[slot] = _FakeCandidateAgent(slot)
        return agents[slot]

    async def run_task(task: Task):
        if task.id.endswith("-repair"):
            answer = json.dumps(
                {
                    "content": "# Demo\n\nRepaired candidate.\n",
                    "rationale": "repaired",
                }
            )
        else:
            answer = "not-json"
        return {task.id: TaskResponse(id=task.id, success=True, answer=answer)}

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=agent_factory,
        parse_output=json.loads,
        repair_prompt_builder=lambda prompt, error: f"{prompt}\nrepair: {error}",
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )
    optimizer = TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=_population_callable(executor),
        concurrency_policy=SelfEvolveConcurrencyPolicy(
            max_total_concurrency=1,
            candidate_generation_concurrency=1,
        ),
    )

    result = await optimizer.propose(_request(max_candidates=1))

    assert [candidate.rationale for candidate in result.candidates] == ["repaired"]
    assert len(agents[0].tasks) == 2
    assert all(task.agent is agents[0] for task in agents[0].tasks)
    assert result.diagnostics["candidate_population_execution"]["repair_count"] == 1


@pytest.mark.asyncio
async def test_schema_repair_builder_receives_invalid_output_not_original_prompt() -> None:
    captured_repair_inputs: list[str] = []

    def repair_prompt_builder(invalid_output: str, error: ValueError) -> str:
        captured_repair_inputs.append(invalid_output)
        return f"repair only: {error}: {invalid_output}"

    async def run_task(task: Task):
        answer = (
            json.dumps(
                {
                    "content": "# Demo\n\nRepaired candidate.\n",
                    "rationale": "representation repaired",
                }
            )
            if task.id.endswith("-repair")
            else "invalid response sentinel"
        )
        return {task.id: TaskResponse(id=task.id, success=True, answer=answer)}

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=json.loads,
        repair_prompt_builder=repair_prompt_builder,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    result = await executor.run(
        ["original trajectory sentinel"],
        max_concurrency=1,
    )

    assert captured_repair_inputs == ["invalid response sentinel"]
    assert result.slots[0].repaired is True


@pytest.mark.asyncio
async def test_second_schema_violation_is_a_typed_candidate_outcome() -> None:
    async def run_task(task: Task):
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer="still not valid json",
            )
        }

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=json.loads,
        repair_prompt_builder=lambda invalid, error: f"repair: {invalid}: {error}",
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    result = await executor.run(["candidate prompt"], max_concurrency=1)

    assert result.slots[0].status == "protocol_invalid"
    assert result.slots[0].failure == {
        "code": "candidate_protocol_invalid",
        "stage": "candidate_protocol",
        "failure_class": "candidate",
        "repairable": True,
    }
    assert result.diagnostics["protocol_invalid_count"] == 1


@pytest.mark.asyncio
async def test_custom_mutator_remains_serial_without_population_callable() -> None:
    active = 0
    max_active = 0
    call_index = 0

    async def mutate(prompt: str):
        nonlocal active, max_active, call_index
        slot = call_index
        call_index += 1
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return {
            "content": f"# Demo\n\nCandidate slot {slot}.\n",
            "rationale": f"slot-{slot}",
        }

    optimizer = TraceReflectiveLLMMutator(
        mutate_text=mutate,
        concurrency_policy=SelfEvolveConcurrencyPolicy(
            max_total_concurrency=4,
            candidate_generation_concurrency=4,
        ),
    )

    result = await optimizer.propose(_request(max_candidates=3))

    assert max_active == 1
    assert [candidate.rationale for candidate in result.candidates] == [
        "slot-0",
        "slot-1",
        "slot-2",
    ]
    assert result.diagnostics["candidate_population_execution"]["mode"] == (
        "custom_serial"
    )


def test_self_evolve_concurrency_policy_uses_stage_and_global_minimum() -> None:
    policy = SelfEvolveConcurrencyPolicy(
        max_total_concurrency=3,
        candidate_generation_concurrency=5,
        replay_concurrency=2,
        judge_concurrency=4,
    )

    assert policy.effective_limit("candidate_generation", item_count=10) == 3
    assert policy.effective_limit("replay", item_count=10) == 2
    assert policy.effective_limit("evaluation", item_count=2) == 2
