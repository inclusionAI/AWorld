from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aworld.core.common import TaskStatusValue
from aworld.core.task import Task, TaskResponse
from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationInfrastructureError,
)
from aworld.self_evolve.candidate_protocol import (
    CandidateProtocolError,
    merge_candidate_repair_output,
    normalize_candidate_output,
)
from aworld.self_evolve.concurrency import (
    AWorldCandidatePopulationExecutor,
    CandidatePopulationResult,
    CandidatePopulationSlotResult,
    SelfEvolveConcurrencyPolicy,
)
from aworld.self_evolve.datasets import EvalCase
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.optimizers.base import (
    CandidateSemanticValidationError,
)
from aworld.self_evolve.optimizers import llm_mutator as llm_mutator_module
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.repair_conformance import RepairConformanceResult
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.types import EvaluationSummary, SelfEvolveTargetRef


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


def test_replay_generation_prompts_publish_runtime_launch_abi() -> None:
    replay_request = replace(
        _request(max_candidates=1),
        replay_requirements=(
            ReplayCapabilityRequirement(
                requirement_id="requirement-http",
                kind="http_resource",
                identifier="https://example.invalid/resource",
                case_ids=("train-1",),
                evidence_refs=("evidence-1",),
                status="runtime_required",
            ),
        ),
    )
    initial_prompt = llm_mutator_module._build_mutation_prompt(
        replay_request,
        candidate_index=0,
    )
    focused_prompt = llm_mutator_module._focused_repair_prompt_instructions(
        {"repair_conformance": {}}
    )

    for prompt in (initial_prompt, focused_prompt):
        assert "--port <int> --fixture <path> --scratch <path>" in prompt
        assert "AWORLD_REPLAY_PORT" in prompt
        assert (
            "not supplied" in prompt
            or "not be used" in prompt
            or "Do not replace" in prompt
        )
        assert "exact path selected from typed HTTP" in prompt
        assert "exactly one non-root HTTP task entry" in prompt


def test_focused_prompt_keeps_satisfied_source_proof_out_of_latest_failure_focus() -> None:
    prompt = llm_mutator_module._focused_repair_prompt_instructions(
        {
            "repair_conformance": {
                "schema_field_constraints": [
                    {
                        "schema_layer": "runtime",
                        "field_path": (
                            "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
                        ),
                        "rule": "enum",
                        "expected": ["json_sidecar_record_value_projector"],
                        "value_domain": "source_behavior",
                    }
                ]
            },
            "validation_feedback": [
                {
                    "diagnostics": [
                        {
                            "code": (
                                "replay_service_process_exited_before_readiness"
                            )
                        }
                    ]
                },
                {"diagnostics": [{"code": "source_behavior_proof_failed"}]},
            ],
        }
    )

    assert "cumulative preservation invariant" in prompt
    assert "already satisfied source proof" in prompt
    assert "Use this supported topology" not in prompt


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
async def test_representation_repair_preserves_valid_initial_candidate_files() -> None:
    async def run_task(task: Task):
        if task.id.endswith("-repair"):
            answer = json.dumps(
                {
                    "content": "# Demo\n\nUse the recorded replay runtime.\n",
                    "rationale": "repair the invalid candidate rationale",
                }
            )
        else:
            answer = json.dumps(
                {
                    "content": "# Demo\n\nOld guidance.\n",
                    "rationale": 7,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "operation": "upsert",
                            "content": "def respond():\n    return {'recorded': True}\n",
                        }
                    ],
                }
            )
        return {task.id: TaskResponse(id=task.id, success=True, answer=answer)}

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=lambda raw: normalize_candidate_output(
            raw,
            current_content="# Demo\n\nOld guidance.\n",
        ),
        repair_prompt_builder=lambda invalid, error: f"repair: {error}",
        repair_output_merger=merge_candidate_repair_output,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    population = await executor.run(("generate",), max_concurrency=1)

    assert population.slots[0].status == "succeeded"
    assert population.slots[0].repaired is True
    assert population.slots[0].output is not None
    assert population.slots[0].output["files"] == [
        {
            "path": "replay/runtime.py",
            "operation": "upsert",
            "content": "def respond():\n    return {'recorded': True}\n",
            "executable": False,
        }
    ]


@pytest.mark.asyncio
async def test_contextual_semantic_repair_preserves_valid_candidate_package() -> None:
    async def run_task(task: Task):
        if task.id.endswith("-repair"):
            answer = json.dumps(
                {
                    "addressed_improvement_signal_ids": [],
                }
            )
        else:
            answer = json.dumps(
                {
                    "content": "# Demo\n\nUse the reusable workflow.\n",
                    "rationale": "preserve the valid package",
                    "addressed_improvement_signal_ids": ["signal-unexposed"],
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "operation": "upsert",
                            "content": "def respond():\n    return {'ok': True}\n",
                        }
                    ],
                }
            )
        return {task.id: TaskResponse(id=task.id, success=True, answer=answer)}

    def validate_output(index: int, output):
        del index
        if output.get("addressed_improvement_signal_ids"):
            raise CandidateSemanticValidationError(
                "unexposed_improvement_signal_ids",
                "candidate addressed an improvement signal that was not exposed",
                field_path="addressed_improvement_signal_ids",
                allowed_improvement_signal_ids=(),
            )
        return output

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=lambda raw: normalize_candidate_output(
            raw,
            current_content="# Demo\n\nOld guidance.\n",
        ),
        repair_prompt_builder=lambda invalid, error: f"repair: {error}",
        repair_output_merger=merge_candidate_repair_output,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    population = await executor.run(
        ("generate",),
        max_concurrency=1,
        validate_output=validate_output,
    )

    assert population.slots[0].status == "succeeded"
    assert population.slots[0].repaired is True
    assert population.slots[0].output == {
        "schema_version": "aworld.self_evolve.candidate.v1",
        "content": "# Demo\n\nUse the reusable workflow.\n",
        "rationale": "preserve the valid package",
        "addressed_improvement_signal_ids": [],
        "files": [
            {
                "path": "replay/runtime.py",
                "operation": "upsert",
                "content": "def respond():\n    return {'ok': True}\n",
                "executable": False,
            }
        ],
    }
    assert population.diagnostics["repair_attempt_count"] == 1
    assert population.diagnostics["repair_success_count"] == 1


@pytest.mark.asyncio
async def test_llm_mutator_routes_unexposed_signal_through_same_slot_repair() -> None:
    async def run_task(task: Task):
        answer = (
            json.dumps({"addressed_improvement_signal_ids": []})
            if task.id.endswith("-repair")
            else json.dumps(
                {
                    "content": "# Demo\n\nUse the reusable workflow.\n",
                    "rationale": "repair only the contextual signal claim",
                    "addressed_improvement_signal_ids": ["signal-unexposed"],
                }
            )
        )
        return {task.id: TaskResponse(id=task.id, success=True, answer=answer)}

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=lambda raw: normalize_candidate_output(
            raw,
            current_content="# Demo\n\nOld guidance.\n",
        ),
        repair_prompt_builder=lambda invalid, error: f"repair: {error}",
        repair_output_merger=merge_candidate_repair_output,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    async def contextual_population(
        prompts,
        max_concurrency,
        *,
        validate_output=None,
    ):
        return await executor.run(
            prompts,
            max_concurrency=max_concurrency,
            validate_output=validate_output,
        )

    result = await TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=contextual_population,
    ).propose(_request(max_candidates=1))

    assert len(result.candidates) == 1
    assert result.lineage[0].addressed_improvement_signal_ids == ()
    assert result.diagnostics["candidate_materialization_failures"] == []
    assert result.diagnostics["candidate_population_execution"][
        "repair_attempt_count"
    ] == 1


@pytest.mark.asyncio
async def test_llm_mutator_repairs_source_behavior_proof_in_same_slot() -> None:
    base_runtime = (
        "import json, os\n"
        "class Runtime:\n"
        "    path = None\n"
        "def respond(path):\n"
        "    with open(path) as stream:\n"
        "        index = json.load(stream)\n"
        "    return index['records'][0]['value']\n"
        "def main():\n"
        "    path = os.getenv('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "    Runtime.path = path\n"
        "    return respond(Runtime.path)\n"
    )
    invalid_runtime = base_runtime.replace("Runtime", "Handler")
    valid_runtime = (
        "import json, os\n"
        "def respond():\n"
        "    path = os.getenv('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "    with open(path) as stream:\n"
        "        index = json.load(stream)\n"
        "    return index['records'][0]['value']\n"
    )
    constraint = {
        "schema_layer": "runtime",
        "field_path": "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer",
        "rule": "enum",
        "expected": ["json_sidecar_record_value_projector"],
        "value_domain": "source_behavior",
        "required_operations": [
            "read_environment_binding_as_path",
            "bind_environment_path_to_json_file_reader",
            "access_records_array",
            "project_record_value_field_directly",
        ],
    }
    feedback = EvaluationSummary(
        variant_id="candidate-failed",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_repair_conformance"],
            "failure_class": "candidate",
            "repairable": True,
            "repair_candidate_package": {
                "candidate_id": "candidate-failed",
                "content": "# Demo\n\nUse the reusable workflow.\n",
                "files": [
                    {
                        "path": "replay/capability.json",
                        "operation": "upsert",
                        "content": json.dumps(
                            {
                                "schema_version": (
                                    "aworld.skill.replay_capability.v1"
                                ),
                                "capability_id": "generic.replay",
                                "protocol": "aworld.replay.subprocess.v1",
                                "entrypoint": "replay/compiler.py",
                                "handles": ["local_endpoint"],
                                "runtime_files": ["replay/runtime.py"],
                            }
                        ),
                    },
                    {
                        "path": "replay/compiler.py",
                        "operation": "upsert",
                        "content": "def compile_request():\n    return None\n",
                    },
                    {
                        "path": "replay/runtime.py",
                        "operation": "upsert",
                        "content": base_runtime,
                    },
                ],
            },
            "candidate_validation_diagnostics": [
                {
                    "code": "invalid_replay_capability_compile",
                    "capability_error_code": "schema_field_validation_failed",
                    "schema_field_constraints": [constraint],
                }
            ],
        },
    )
    request = OptimizerRequest(
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path="/tmp/demo/SKILL.md",
        ),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(),
        validation_feedback=(feedback,),
        trainable_cases=(EvalCase(case_id="train-1", input="task"),),
        max_candidates=1,
    )
    repair_diagnostics: list[dict[str, object]] = []

    async def run_task(task: Task):
        output = {
            "addressed_improvement_signal_ids": [],
            "files": [
                {
                    "path": "replay/runtime.py",
                    "operation": "upsert",
                    "content": (
                        valid_runtime
                        if task.id.endswith("-repair")
                        else invalid_runtime
                    ),
                }
            ],
        }
        if not task.id.endswith("-repair"):
            output.update(
                {
                    "content": "# Demo\n\nUse the reusable workflow.\n",
                    "rationale": "repair response index consumption",
                }
            )
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=json.dumps(output),
            )
        }

    def repair_prompt_builder(invalid_output: str, error: ValueError) -> str:
        del invalid_output
        assert isinstance(error, CandidateSemanticValidationError)
        repair_diagnostics.append(error.to_diagnostic())
        return "repair the typed source behavior proof"

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=lambda raw: normalize_candidate_output(
            raw,
            current_content=request.current_content,
        ),
        repair_prompt_builder=repair_prompt_builder,
        repair_output_merger=merge_candidate_repair_output,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    async def contextual_population(
        prompts,
        max_concurrency,
        *,
        validate_output=None,
    ):
        return await executor.run(
            prompts,
            max_concurrency=max_concurrency,
            validate_output=validate_output,
        )

    result = await TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=contextual_population,
    ).propose(request)

    assert len(result.candidates) == 1
    runtime_file = next(
        item
        for item in result.candidates[0].files
        if item.path == "replay/runtime.py"
    )
    assert runtime_file.content == valid_runtime
    assert result.diagnostics["candidate_population_execution"][
        "repair_success_count"
    ] == 1
    proof_failure = repair_diagnostics[0]["details"]["repair_conformance"]
    assert proof_failure["code"] == "source_behavior_proof_failed"
    assert proof_failure["details"]["missing_operations"] == [
        "bind_environment_path_to_json_file_reader"
    ]


def test_response_index_reader_chain_is_canonicalized_before_model_repair() -> None:
    source = (
        "import json\n"
        "import os\n"
        "\n"
        "def read_environment_path(env_name):\n"
        "    return os.environ.get(env_name, '')\n"
        "\n"
        "def read_json_file(file_path):\n"
        "    if not file_path or not os.path.exists(file_path):\n"
        "        return None\n"
        "    with open(file_path, 'r', encoding='utf-8') as stream:\n"
        "        return json.load(stream)\n"
        "\n"
        "def response_for(operation):\n"
        "    file_path = read_environment_path(\n"
        "        'AWORLD_REPLAY_RESPONSE_INDEX'\n"
        "    )\n"
        "    index_data = read_json_file(file_path)\n"
        "    records = index_data.get('records', []) if index_data else []\n"
        "    for record in records:\n"
        "        if record.get('operation') == operation:\n"
        "            return record['value']\n"
        "    return None\n"
        "\n"
        "def main():\n"
        "    return response_for('AWORLD_REPLAY_RESPONSE_INDEX')\n"
    )

    initial = llm_mutator_module.recorded_response_index_source_behavior_proof(
        source
    )
    assert initial["missing_operations"] == [
        "read_environment_binding_as_path",
        "bind_environment_path_to_json_file_reader",
    ]

    rewritten = (
        llm_mutator_module._canonicalize_recorded_response_index_reader_chain(
            source
        )
    )

    assert rewritten is not None
    assert (
        'file_path = os.environ.get("AWORLD_REPLAY_RESPONSE_INDEX", "")'
        in rewritten
    )
    assert "index_data = read_json_file(file_path)" in rewritten
    assert llm_mutator_module.recorded_response_index_source_behavior_proof(
        rewritten
    )["proven"] is True


def test_response_index_reader_chain_allows_only_a_null_path_guard() -> None:
    guarded_source = (
        "import json\n"
        "import os\n"
        "def environment_path(env_name):\n"
        "    return os.environ.get(env_name, '')\n"
        "def json_reader(path):\n"
        "    with open(path, encoding='utf-8') as stream:\n"
        "        return json.load(stream)\n"
        "def response(env_name):\n"
        "    path = environment_path(env_name)\n"
        "    if not path:\n"
        "        return None\n"
        "    index = json_reader(path)\n"
        "    return index['records'][0]['value']\n"
        "def main():\n"
        "    return response('AWORLD_REPLAY_RESPONSE_INDEX')\n"
    )

    rewritten = (
        llm_mutator_module._canonicalize_recorded_response_index_reader_chain(
            guarded_source
        )
    )

    assert rewritten is not None
    assert "if not path:\n        return None" in rewritten
    assert llm_mutator_module.recorded_response_index_source_behavior_proof(
        rewritten
    )["proven"] is True


def test_response_index_reader_chain_rejects_ambiguous_producers() -> None:
    ambiguous_source = (
        "import json\n"
        "import os\n"
        "def environment_path(env_name):\n"
        "    return os.environ.get(env_name, '')\n"
        "def json_reader(path):\n"
        "    with open(path, encoding='utf-8') as stream:\n"
        "        return json.load(stream)\n"
        "def first(env_name):\n"
        "    path = environment_path(env_name)\n"
        "    index = json_reader(path)\n"
        "    return index['records'][0]['value']\n"
        "def second(env_name):\n"
        "    path = environment_path(env_name)\n"
        "    index = json_reader(path)\n"
        "    return index['records'][0]['value']\n"
        "def main():\n"
        "    first('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "    return second('AWORLD_REPLAY_RESPONSE_INDEX')\n"
    )

    assert (
        llm_mutator_module._canonicalize_recorded_response_index_reader_chain(
            ambiguous_source
        )
        is None
    )


def test_fixture_source_selector_prefers_authoritative_record_coverage() -> None:
    rewritten = llm_mutator_module._canonicalize_fixture_source_selector(
        llm_mutator_module._MINIMUM_BYTE_SOURCE_SELECTOR
    )

    assert rewritten is not None
    namespace: dict[str, object] = {}
    exec(compile(rewritten, "<selector>", "exec"), namespace)
    select_source = namespace["_select_source"]
    derivations = {
        "evidence": [
            {"path": "tiny", "byte_length": 10},
            {
                "path": "partial",
                "byte_length": 100,
                "response_index_path": "partial.responses.json",
                "response_record_count": 2,
            },
            {
                "path": "complete",
                "byte_length": 1000,
                "response_index_path": "complete.responses.json",
                "response_record_count": 7,
            },
        ],
        "no-response": [
            {"path": "large", "byte_length": 20},
            {"path": "small", "byte_length": 5},
        ],
    }

    assert select_source("evidence", derivations)["path"] == "complete"
    assert select_source("no-response", derivations)["path"] == "small"

    untyped = llm_mutator_module._MINIMUM_BYTE_SOURCE_SELECTOR.replace(
        "evidence_ref: str, derivations: dict) -> dict | None",
        "evidence_ref, derivations)",
    )
    assert llm_mutator_module._canonicalize_fixture_source_selector(untyped) is not None


def test_fixture_source_selector_normalization_uses_typed_probe_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        repair_focus_for_candidate=lambda **_kwargs: {"typed": True}
    )
    contract = SimpleNamespace(
        fixture_probe_constraints=(object(),),
        schema_field_constraints=(),
        requires_compiler_fixture_reconstruction=True,
        compiler_path="replay/compiler.py",
        required_branch_paths=("replay/compiler.py",),
    )
    monkeypatch.setattr(
        llm_mutator_module,
        "compile_evolution_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        llm_mutator_module,
        "compile_repair_conformance_contract",
        lambda _focus: contract,
    )
    output = {
        "content": "# Demo\n\nUse the reusable workflow.\n",
        "rationale": "repair fixture source selection",
        "addressed_improvement_signal_ids": [],
        "files": [
            {
                "path": "replay/compiler.py",
                "operation": "upsert",
                "content": llm_mutator_module._MINIMUM_BYTE_SOURCE_SELECTOR,
            }
        ],
    }

    normalized = (
        llm_mutator_module._canonicalize_fixture_source_selector_output(
            output,
            request=_request(max_candidates=1),
            candidate_index=0,
        )
    )

    compiler = normalized["files"][0]["content"]
    assert "response_index_path" in compiler
    assert "response_record_count" in compiler
    assert "return max(" in compiler


def test_fixture_source_selector_normalization_uses_compiler_source_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        repair_focus_for_candidate=lambda **_kwargs: {"typed": True}
    )
    contract = SimpleNamespace(
        fixture_probe_constraints=(),
        schema_field_constraints=(
            SimpleNamespace(
                schema_layer="compiler",
                field_path="evidence_derivations[*].response_index_path",
                expected=("recorded_response_source",),
            ),
        ),
        requires_compiler_fixture_reconstruction=False,
        compiler_path="replay/compiler.py",
        required_branch_paths=("replay/compiler.py",),
    )
    monkeypatch.setattr(
        llm_mutator_module,
        "compile_evolution_context",
        lambda _request: context,
    )
    monkeypatch.setattr(
        llm_mutator_module,
        "compile_repair_conformance_contract",
        lambda _focus: contract,
    )
    untyped = llm_mutator_module._MINIMUM_BYTE_SOURCE_SELECTOR.replace(
        "evidence_ref: str, derivations: dict) -> dict | None",
        "evidence_ref, derivations)",
    )

    normalized = llm_mutator_module._canonicalize_fixture_source_selector_output(
        {
            "content": "# Demo\n",
            "rationale": "repair recorded-response source selection",
            "addressed_improvement_signal_ids": [],
            "files": [
                {
                    "path": "replay/compiler.py",
                    "operation": "upsert",
                    "content": untyped,
                }
            ],
        },
        request=_request(max_candidates=1),
        candidate_index=0,
    )

    compiler = normalized["files"][0]["content"]
    assert "response_index_path" in compiler
    assert "response_record_count" in compiler
    compile(compiler, "<normalized-compiler>", "exec")


@pytest.mark.asyncio
async def test_llm_mutator_canonicalizes_cycle_007_reader_chain_without_repair() -> None:
    helper_runtime = (
        "import json\n"
        "import os\n"
        "def _read_environment_binding_as_path(env_name):\n"
        "    return os.environ.get(env_name, '')\n"
        "def _bind_environment_path_to_json_file_reader(path):\n"
        "    with open(path, encoding='utf-8') as stream:\n"
        "        return json.load(stream)\n"
        "def _load_response_index_value(env_name, operation):\n"
        "    path = _read_environment_binding_as_path(env_name)\n"
        "    if not path:\n"
        "        return None\n"
        "    index = _bind_environment_path_to_json_file_reader(path)\n"
        "    for record in index['records']:\n"
        "        if record.get('operation') == operation:\n"
        "            return record['value']\n"
        "    return None\n"
        "def main():\n"
        "    return _load_response_index_value(\n"
        "        'AWORLD_REPLAY_RESPONSE_INDEX', '/data'\n"
        "    )\n"
    )
    constraint = {
        "schema_layer": "runtime",
        "field_path": "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer",
        "rule": "enum",
        "expected": ["json_sidecar_record_value_projector"],
        "value_domain": "source_behavior",
        "required_operations": [
            "read_environment_binding_as_path",
            "bind_environment_path_to_json_file_reader",
            "access_records_array",
            "project_record_value_field_directly",
        ],
    }
    feedback = EvaluationSummary(
        variant_id="candidate-failed",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_repair_conformance"],
            "failure_class": "candidate",
            "repairable": True,
            "repair_candidate_package": {
                "candidate_id": "candidate-failed",
                "content": "# Demo\n\nUse the reusable workflow.\n",
                "files": [
                    {
                        "path": "replay/capability.json",
                        "operation": "upsert",
                        "content": json.dumps(
                            {
                                "schema_version": (
                                    "aworld.skill.replay_capability.v1"
                                ),
                                "capability_id": "generic.replay",
                                "protocol": "aworld.replay.subprocess.v1",
                                "entrypoint": "replay/compiler.py",
                                "handles": ["local_endpoint"],
                                "runtime_files": ["replay/runtime.py"],
                            }
                        ),
                    },
                    {
                        "path": "replay/compiler.py",
                        "operation": "upsert",
                        "content": "def compile_request():\n    return None\n",
                    },
                    {
                        "path": "replay/runtime.py",
                        "operation": "upsert",
                        "content": "# prior runtime\n",
                    },
                ],
            },
            "candidate_validation_diagnostics": [
                {
                    "code": "invalid_replay_capability_compile",
                    "capability_error_code": "schema_field_validation_failed",
                    "schema_field_constraints": [constraint],
                }
            ],
        },
    )
    request = OptimizerRequest(
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path="/tmp/demo/SKILL.md",
        ),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(),
        validation_feedback=(feedback,),
        trainable_cases=(EvalCase(case_id="train-1", input="task"),),
        max_candidates=1,
    )

    async def run_task(task: Task):
        assert not task.id.endswith("-repair")
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=json.dumps(
                    {
                        "content": "# Demo\n\nUse the reusable workflow.\n",
                        "rationale": "repair response index consumption",
                        "addressed_improvement_signal_ids": [],
                        "files": [
                            {
                                "path": "replay/runtime.py",
                                "operation": "upsert",
                                "content": helper_runtime,
                            }
                        ],
                    }
                ),
            )
        }

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=lambda raw: normalize_candidate_output(
            raw,
            current_content=request.current_content,
        ),
        repair_prompt_builder=lambda *_args, **_kwargs: pytest.fail(
            "deterministic canonicalization must avoid a model repair"
        ),
        repair_output_merger=merge_candidate_repair_output,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    async def contextual_population(
        prompts,
        max_concurrency,
        *,
        validate_output=None,
    ):
        return await executor.run(
            prompts,
            max_concurrency=max_concurrency,
            validate_output=validate_output,
        )

    result = await TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=contextual_population,
    ).propose(request)

    assert len(result.candidates) == 1
    runtime = next(
        item.content
        for item in result.candidates[0].files
        if item.path == "replay/runtime.py"
    )
    assert runtime is not None
    assert llm_mutator_module.recorded_response_index_source_behavior_proof(
        runtime
    )["proven"] is True
    execution = result.diagnostics["candidate_population_execution"]
    assert execution["repair_attempt_count"] == 0
    assert execution["repair_success_count"] == 0


@pytest.mark.asyncio
async def test_llm_mutator_repairs_any_candidate_owned_conformance_failure(
    monkeypatch,
) -> None:
    feedback = EvaluationSummary(
        variant_id="candidate-failed",
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_repair_conformance"],
            "failure_class": "candidate",
            "repairable": True,
            "repair_candidate_package": {
                "candidate_id": "candidate-failed",
                "content": "# Demo\n\nUse the reusable workflow.\n",
                "files": [
                    {
                        "path": "replay/runtime.py",
                        "operation": "upsert",
                        "content": "def select_fixture_value(value):\n    return value\n",
                    }
                ],
            },
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        },
    )
    request = OptimizerRequest(
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path="/tmp/demo/SKILL.md",
        ),
        current_content="# Demo\n\nOld guidance.\n",
        target_fingerprint="sha256:old",
        trace_packs=(),
        validation_feedback=(feedback,),
        trainable_cases=(EvalCase(case_id="train-1", input="task"),),
        max_candidates=1,
    )
    repair_diagnostics: list[dict[str, object]] = []

    def source_conformance(candidate, contract):
        del contract
        runtime = next(
            item.content
            for item in candidate.files
            if item.path == "replay/runtime.py"
        )
        if "bool_guard" in runtime:
            return RepairConformanceResult(
                passed=True,
                code="repair_branch_changed",
                reason="candidate repaired the typed violation",
                details={},
            )
        return RepairConformanceResult(
            passed=False,
            code="forbidden_fixture_probe_derivation",
            reason="candidate includes boolean metadata in fixture output",
            details={
                "violations": [
                    {
                        "construct": "boolean_metadata_not_excluded",
                        "function": "select_fixture_value",
                        "line": 2,
                        "path": "replay/runtime.py",
                    }
                ],
                "required_change": "reject bool before int or float",
            },
        )

    monkeypatch.setattr(
        llm_mutator_module,
        "evaluate_candidate_source_conformance",
        source_conformance,
    )

    async def run_task(task: Task):
        output = {
            "addressed_improvement_signal_ids": [],
            "files": [
                {
                    "path": "replay/runtime.py",
                    "operation": "upsert",
                    "content": (
                        "def bool_guard(value):\n"
                        "    return None if isinstance(value, bool) else value\n"
                        if task.id.endswith("-repair")
                        else (
                            "def select_fixture_value(value):\n"
                            "    return str(value)\n"
                        )
                    ),
                }
            ],
        }
        if not task.id.endswith("-repair"):
            output.update(
                {
                    "content": "# Demo\n\nUse the reusable workflow.\n",
                    "rationale": "repair fixture selection",
                }
            )
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=json.dumps(output),
            )
        }

    def repair_prompt_builder(invalid_output: str, error: ValueError) -> str:
        del invalid_output
        assert isinstance(error, CandidateSemanticValidationError)
        repair_diagnostics.append(error.to_diagnostic())
        return "repair every typed conformance violation"

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=lambda raw: normalize_candidate_output(
            raw,
            current_content=request.current_content,
        ),
        repair_prompt_builder=repair_prompt_builder,
        repair_output_merger=merge_candidate_repair_output,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    async def contextual_population(
        prompts,
        max_concurrency,
        *,
        validate_output=None,
    ):
        return await executor.run(
            prompts,
            max_concurrency=max_concurrency,
            validate_output=validate_output,
        )

    result = await TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=contextual_population,
    ).propose(request)

    assert len(result.candidates) == 1
    assert result.diagnostics["candidate_population_execution"][
        "repair_success_count"
    ] == 1
    conformance = repair_diagnostics[0]["details"]["repair_conformance"]
    assert conformance["code"] == "forbidden_fixture_probe_derivation"
    assert conformance["repairable"] is True
    assert conformance["failure_fingerprint"].startswith("sha256:")


@pytest.mark.asyncio
async def test_repair_telemetry_counts_attempt_success_and_tokens() -> None:
    async def run_task(task: Task):
        if task.id.endswith("-repair"):
            answer = json.dumps(
                {
                    "content": "# Demo\n\nRepaired candidate.\n",
                    "rationale": "repaired",
                }
            )
            usage = {"prompt_tokens": 25, "completion_tokens": 15, "total_tokens": 40}
        else:
            answer = "not-json"
            usage = {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20}
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=answer,
                usage=usage,
            )
        }

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=json.loads,
        repair_prompt_builder=lambda invalid, error: f"repair: {invalid}: {error}",
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    diagnostics = (await executor.run(["candidate prompt"], max_concurrency=1)).diagnostics

    assert diagnostics["repair_attempt_count"] == 1
    assert diagnostics["repair_success_count"] == 1
    assert diagnostics["repair_protocol_invalid_count"] == 0
    assert diagnostics["repair_infrastructure_failure_count"] == 0
    assert diagnostics["initial_token_usage"]["total_tokens"] == 20
    assert diagnostics["repair_token_usage"]["total_tokens"] == 40
    assert diagnostics["token_usage"]["total_tokens"] == 60
    assert diagnostics["initial_execution_seconds"] >= 0
    assert diagnostics["repair_execution_seconds"] >= 0


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
async def test_same_slot_repair_receives_original_generation_context() -> None:
    captured_original_prompts: list[str] = []

    async def run_task(task: Task):
        answer = "{}" if task.id.endswith("-repair") else "invalid json"
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=answer,
            )
        }

    def repair_prompt_builder(
        invalid_output: str,
        error: ValueError,
        *,
        original_prompt: str,
    ) -> str:
        del invalid_output, error
        captured_original_prompts.append(original_prompt)
        return "repair using the original source-complete context"

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=json.loads,
        repair_prompt_builder=repair_prompt_builder,
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    result = await executor.run(
        ["original source-complete generation context"],
        max_concurrency=1,
    )

    assert result.slots[0].status == "succeeded"
    assert captured_original_prompts == [
        "original source-complete generation context"
    ]


@pytest.mark.asyncio
async def test_non_repairable_protocol_failure_skips_repair_task() -> None:
    task_ids: list[str] = []

    async def run_task(task: Task):
        task_ids.append(task.id)
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer="invalid candidate",
            )
        }

    def parse_output(raw_output: str):
        raise CandidateProtocolError(
            "multiple_json_objects",
            "candidate response must contain exactly one JSON object",
            repairable=False,
        )

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=parse_output,
        repair_prompt_builder=lambda invalid, error: "must-not-run",
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    result = await executor.run(["candidate prompt"], max_concurrency=1)

    assert len(task_ids) == 1
    assert not any(task_id.endswith("-repair") for task_id in task_ids)
    assert result.slots[0].status == "protocol_invalid"
    assert result.diagnostics["repair_attempt_count"] == 0
    assert result.diagnostics["protocol_invalid_count"] == 1


@pytest.mark.asyncio
async def test_multiple_json_objects_receive_one_bounded_repair() -> None:
    task_ids: list[str] = []

    async def run_task(task: Task):
        task_ids.append(task.id)
        answer = (
            json.dumps(
                {
                    "content": "# Demo\n\nRepaired candidate.\n",
                    "rationale": "return one candidate object",
                    "files": [],
                }
            )
            if task.id.endswith("-repair")
            else (
                '{"content":"# Demo\\nFirst"} '
                '{"content":"# Demo\\nSecond"}'
            )
        )
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                answer=answer,
            )
        }

    executor = AWorldCandidatePopulationExecutor(
        agent_factory=_FakeCandidateAgent,
        parse_output=lambda raw: normalize_candidate_output(
            raw,
            current_content="# Demo\n\nOld guidance.\n",
        ),
        repair_prompt_builder=lambda invalid, error: (
            f"Return exactly one candidate JSON object. Error: {error}. "
            f"Invalid output: {invalid}"
        ),
        task_batch_executor=DeterministicTaskBatchExecutor(run_task=run_task),
    )

    result = await executor.run(["candidate prompt"], max_concurrency=1)

    assert len(task_ids) == 2
    assert task_ids[1].endswith("-repair")
    assert result.slots[0].status == "succeeded"
    assert result.slots[0].repaired is True
    assert result.diagnostics["repair_attempt_count"] == 1
    assert result.diagnostics["repair_success_count"] == 1


@pytest.mark.asyncio
async def test_llm_mutator_preserves_non_repairable_protocol_disposition() -> None:
    async def population_callable(
        prompts,
        max_concurrency,
        *,
        validate_output=None,
    ):
        del prompts, max_concurrency, validate_output
        return CandidatePopulationResult(
            slots=(
                CandidatePopulationSlotResult(
                    index=0,
                    status="protocol_invalid",
                    failure={
                        "code": "multiple_json_objects",
                        "stage": "candidate_protocol",
                        "failure_class": "candidate",
                        "repairable": False,
                    },
                ),
            ),
            diagnostics={"protocol_invalid_count": 1},
        )

    result = await TraceReflectiveLLMMutator(
        mutate_text=lambda prompt: None,
        population_callable=population_callable,
    ).propose(_request(max_candidates=1))

    assert len(result.generation_outcomes) == 1
    assert result.generation_outcomes[0].repairable is False
    assert result.generation_outcomes[0].reason_codes == (
        "multiple_json_objects",
    )


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
