# AWorld-Native Self-Evolve Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute self-evolve candidate generation, safe replay work, and judge evaluation through opt-in AWorld Tasks with isolated local Context resources, prompt budgets, deterministic barriers, and bounded parallelism.

**Architecture:** Add generic opt-in AWorld extensions for prompt budgeting, local memory scoping, and deterministic Task batches without changing default Agent/Runner behavior. Self-evolve supplies specialized `runner_cls` adapters and remains the deterministic planner; the CLI constructs an outer Task and model-backed candidate population Tasks. Replay and evaluation only fan out when their runtime resources are isolated, otherwise resource claims serialize them.

**Tech Stack:** Python 3.11+, asyncio, contextvars, Pydantic, AWorld Agent/Task/Runner, pytest/pytest-asyncio.

---

## File map

- Create `aworld/runners/batch.py`: generic deterministic bounded Task executor and resource claims.
- Create `aworld/memory/scope.py`: task-local `MemoryFactory` override using `contextvars`.
- Create `aworld/core/context/amni/local.py`: local isolated `ApplicationContext` factory.
- Create `aworld/core/context/amni/prompt/assembly/budget.py`: prompt budget policy, reduction plan, error, and provider decorator.
- Create `aworld/agents/prompt_budgeted_agent.py`: opt-in Agent request-budget enforcement.
- Create `aworld/runners/hook/scoped.py`: opt-in execution-scope hook base.
- Create `aworld/self_evolve/runtime.py`: outer, candidate, replay, and evaluation Runner adapters.
- Create `aworld/self_evolve/concurrency.py`: self-evolve concurrency policy and stage telemetry.
- Modify `aworld/memory/main.py`: one scoped-memory compatibility seam.
- Modify `aworld/self_evolve/candidate_generation.py`: use `PromptBudgetedAgent`, local Context, and standard Task lifecycle.
- Modify `aworld/self_evolve/optimizers/llm_mutator.py`: optional model-backed population executor while preserving serial callback behavior.
- Modify `aworld/self_evolve/replay_adaptation.py` and `replay_capability.py`: generic skill-owned concurrency metadata and resource keys.
- Modify `aworld/self_evolve/replay.py`: specialized replay Tasks and deterministic repetition/member reduction.
- Modify `aworld/self_evolve/evaluation.py`: specialized evaluation Tasks and safe concurrent baseline/candidate judge execution.
- Modify `aworld/self_evolve/runner.py`: inject batch executor/policy, stage barriers, outer Task entry, and report telemetry.
- Modify `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`: inject self-evolve policy independently from judge model selection.
- Modify `aworld-cli/src/aworld_cli/executors/pre_llm_cost_hook.py`: apply only to interactive CLI execution scope.

### Task 1: Generic deterministic Task batches

**Files:**
- Create: `aworld/runners/batch.py`
- Test: `tests/runners/test_deterministic_task_batch.py`

```python
@dataclass(frozen=True)
class TaskBatchItem:
    index: int
    task: Task
    resource_claims: tuple[TaskResourceClaim, ...] = ()
```

- [x] Write failing tests covering stable result order, concurrency cap, `indexed_fail_fast`, `collect_all`, and exclusive/shared resource claims. Use custom test `runner_cls` values so every work item goes through `Runners.run_task()`.
- [x] Run `conda run -n aworld_env python -m pytest tests/runners/test_deterministic_task_batch.py -q`; expect import failure for `aworld.runners.batch`.
- [x] Implement immutable `TaskResourceClaim`, `TaskBatchItem`, `TaskBatchResult`, and `DeterministicTaskBatchExecutor`. A conflict exists when keys match and either claim is exclusive. Return results by item index; cancel higher indexes after the lowest indexed fail-fast failure.
- [x] Re-run the test file and `tests/runners/test_tool_reset_compat.py`; expect all pass.
- [x] Commit `feat(runners): add deterministic task batch executor`.

### Task 2: Local memory and ApplicationContext isolation

**Files:**
- Create: `aworld/memory/scope.py`
- Create: `aworld/core/context/amni/local.py`
- Modify: `aworld/memory/main.py`
- Test: `tests/memory/test_local_memory_scope.py`
- Test: `tests/context/test_local_isolated_application_context.py`

```python
async with LocalMemoryScope(memory):
    assert MemoryFactory.instance() is memory

context = LocalIsolatedApplicationContext.create(
    task_id="self-evolve-slot-0",
    task_content="candidate request",
)
```

- [x] Write failing tests proving concurrent scopes resolve different memory objects, child async tasks inherit their scope, and no-scope `MemoryFactory.instance()` returns the exact prior global object.
- [x] Run both new test files; expect missing imports.
- [x] Implement `LocalMemoryScope` with a `ContextVar[MemoryBase | None]`, token-based reset, and nested-scope restoration. Add only `scoped = LocalMemoryScope.current()` to the start of `MemoryFactory.instance()`.
- [x] Implement `LocalIsolatedApplicationContext.create()` using `MemoryFactory.from_config(config=MemoryConfig(provider="aworld"), memory_store=InMemoryMemoryStore())`, `InMemoryCheckpointRepository`, summary-disabled Amni config, `execution_scope="self_evolve"`, and no persisted workspace.
- [x] Re-run both new test files plus existing memory/context tests selected by `rg --files tests | rg '(memory|context)'`; expect all selected tests pass.
- [x] Commit `feat(context): add isolated local memory scope`.

### Task 3: Opt-in prompt budgeting

**Files:**
- Create: `aworld/core/context/amni/prompt/assembly/budget.py`
- Create: `aworld/agents/prompt_budgeted_agent.py`
- Modify: `aworld/core/context/amni/prompt/assembly/__init__.py`
- Test: `tests/context/test_budgeted_prompt_assembly.py`
- Test: `tests/agents/test_prompt_budgeted_agent.py`

```python
policy = PromptBudgetPolicy(
    input_budget=4096,
    reserved_output_tokens=1024,
    section_hints={"prior_feedback": {"required": False, "priority": 10}},
)
plan = BudgetedPromptAssemblyProvider(delegate, policy).build_plan(
    messages=messages,
    tools=tools,
    metadata=metadata,
)
```

- [x] Write failing tests for `input_budget = min(max_input_tokens, max_model_len - reserved_output_tokens)`, tool-schema counting, stable optional-section reduction, required-content overflow, rebuilt stable hash, and unchanged base `Agent` behavior.
- [x] Run the two test files; expect missing imports.
- [x] Implement `PromptBudgetPolicy`, `PromptBudgetExceededError`, `BudgetedPromptSection`, `BudgetedPromptAssemblyPlan`, and `BudgetedPromptAssemblyProvider`. Count model messages plus serialized tools with `ModelUtils`; reduce complete optional messages, then bounded head/tail content, and never include content in diagnostics.
- [x] Implement `PromptBudgetedAgent` to wrap the normal provider, resolve one effective output limit, inject budget metadata, and assert the final plan before calling the inherited provider path. Do not alter `Agent.async_policy()` or `Agent.invoke_model()`.
- [x] Re-run both files plus prompt-cache tests; expect all pass and final cache hashes describe reduced messages.
- [x] Commit `feat(agents): add opt-in prompt budget processing`.

### Task 4: Execution-scoped hooks

**Files:**
- Create: `aworld/runners/hook/scoped.py`
- Modify: `aworld-cli/src/aworld_cli/executors/pre_llm_cost_hook.py`
- Test: `tests/hooks/test_execution_scoped_hook.py`
- Test: `tests/hooks/test_pre_llm_cost_hook.py`

```python
class PreLlmCostHook(ExecutionScopedHook, PreLLMCallHook):
    allowed_execution_scopes = frozenset({"cli_interactive"})
```

- [ ] Write a failing test showing a `self_evolve` Context returns before CLI history and console lookup, while `cli_interactive` preserves current behavior.
- [ ] Run the targeted hook tests; expect `ExecutionScopedHook` import failure.
- [ ] Implement `ExecutionScopedHook` as an opt-in mixin with `applies_to(context)` and a protected `_exec_scoped()` method; migrate only `PreLlmCostHook` and require `execution_scope == "cli_interactive"`.
- [ ] Re-run hook tests; expect existing non-opt-in hooks unchanged.
- [ ] Commit `feat(hooks): scope interactive CLI cost checks`.

### Task 5: Self-evolve Runner adapters and candidate Task lifecycle

**Files:**
- Create: `aworld/self_evolve/runtime.py`
- Modify: `aworld/self_evolve/candidate_generation.py`
- Modify: `aworld/self_evolve/__init__.py`
- Test: `tests/self_evolve/test_runtime_runners.py`
- Modify test: `tests/self_evolve/test_candidate_generation.py`

```python
task = Task(
    input=prompt,
    agent=agent,
    context=LocalIsolatedApplicationContext.create(
        task_id="self-evolve-candidate-0",
        task_content=prompt,
    ),
    runner_cls="aworld.self_evolve.runtime.SelfEvolveCandidateTaskRunner",
)
responses = await Runners.run_task(task)
```

- [ ] Write failing tests that `choose_runners()` constructs all four self-evolve adapters with `run_conf`, the outer adapter is non-agent-oriented, and candidate generation invokes `Runners.run_task()` with `SelfEvolveCandidateTaskRunner`.
- [ ] Run the targeted tests; expect missing Runner classes and the old direct `invoke_model()` path.
- [ ] Implement `SelfEvolveTaskRunner`, `SelfEvolveCandidateTaskRunner`, `SelfEvolveReplayTaskRunner`, and `SelfEvolveEvaluationTaskRunner`. Enter `LocalMemoryScope` before candidate `TaskEventRunner.pre_run()` and release it after `post_run()`.
- [ ] Change `CandidateGenerationAgent` to inherit `PromptBudgetedAgent`. Its `generate()` creates a fresh `LocalIsolatedApplicationContext`, a standard `Task` whose `runner_cls` is `aworld.self_evolve.runtime.SelfEvolveCandidateTaskRunner`, awaits `Runners.run_task()`, validates the `TaskResponse`, and preserves typed/sanitized failures.
- [ ] Re-run candidate/runtime tests; expect no direct manual `Context` or `invoke_model()` orchestration in `generate()`.
- [ ] Commit `feat(self-evolve): run candidate generation as AWorld task`.

### Task 6: Model-backed candidate population batches

**Files:**
- Create: `aworld/self_evolve/concurrency.py`
- Modify: `aworld/self_evolve/candidate_generation.py`
- Modify: `aworld/self_evolve/optimizers/llm_mutator.py`
- Modify: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_candidate_population_execution.py`
- Modify test: `tests/self_evolve/test_optimizer_contract.py`

```python
class SelfEvolveConcurrencyPolicy(BaseModel):
    max_total_concurrency: PositiveInt = 2
    candidate_generation_concurrency: PositiveInt = 2
    replay_concurrency: PositiveInt = 2
    judge_concurrency: PositiveInt = 2
    candidate_screening_concurrency: PositiveInt = 1
```

- [ ] Write failing tests proving model-backed slots overlap up to policy limit, results reduce by slot, a failure at slot `k` discards `k..N`, schema repair stays in the same slot, and direct custom `TraceReflectiveLLMMutator` remains serial by default.
- [ ] Run targeted tests; expect missing `SelfEvolveConcurrencyPolicy`/population executor.
- [ ] Implement `SelfEvolveConcurrencyPolicy` with defaults `(total=2, generation=2, replay=2, judge=2, screening=1)` and positive-value validation.
- [ ] Implement a CLI-only candidate population executor that creates one agent and Task per slot, uses `DeterministicTaskBatchExecutor(indexed_fail_fast)`, performs one bounded repair Task for invalid JSON in the same slot, and returns slot-indexed mutation payloads.
- [ ] Extend `TraceReflectiveLLMMutator` with an optional population callable; preserve its existing sequential loop when absent. Inject the callable from `optimize_from_cli_request()` only when a mutation `ModelConfig` exists.
- [ ] Re-run optimizer/population tests and the current candidate-generation suite.
- [ ] Commit `feat(self-evolve): batch candidate population tasks`.

### Task 7: Outer self-evolve Task entry and deterministic barriers

**Files:**
- Modify: `aworld/self_evolve/runtime.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- Test: `tests/self_evolve/test_outer_task_runner.py`
- Modify test: `tests/core/test_optimize_top_level_command.py`

```python
outer_task = Task(
    input=SelfEvolveTaskRequest(runner=runner, run_kwargs=run_kwargs),
    runner_cls="aworld.self_evolve.runtime.SelfEvolveTaskRunner",
)
response = Runners.sync_run_task(outer_task)[outer_task.id]
```

- [ ] Write failing tests showing both offline CLI and main-agent subtask inputs select `SelfEvolveTaskRunner`, and iteration 2 cannot start until iteration 1 feedback is reduced.
- [ ] Run targeted tests; expect `optimize_from_cli_request()` still directly constructs `SelfEvolveRunner` inside `asyncio.run()`.
- [ ] Add a local-only immutable outer request carrying the constructed deterministic runner and `run_explicit_target()` arguments. Make `SelfEvolveTaskRunner.do_run()` execute it and map the result to `TaskResponse`.
- [ ] Replace that direct call with `Runners.sync_run_task(outer_task)` where `outer_task.runner_cls` is `aworld.self_evolve.runtime.SelfEvolveTaskRunner`; preserve summary/report output and keep all stage planning inside `SelfEvolveRunner`.
- [ ] Re-run targeted CLI/runner tests.
- [ ] Commit `feat(self-evolve): add outer task runner entry`.

### Task 8: Skill-owned replay concurrency and replay Tasks

**Files:**
- Modify: `aworld/self_evolve/replay_adaptation.py`
- Modify: `aworld/self_evolve/replay_capability.py`
- Modify: `aworld/self_evolve/replay.py`
- Modify: `aworld/self_evolve/runtime.py`
- Test: `tests/self_evolve/test_replay_concurrency.py`
- Modify test: `tests/self_evolve/test_replay_capability.py`
- Modify test: `tests/self_evolve/test_replay_adaptation.py`

```python
@dataclass(frozen=True)
class ReplayAdapterBinding:
    adapter_id: str
    dependency_id: str
    deterministic: bool
    concurrency_mode: Literal["isolated", "shared_read_only", "exclusive"] = "exclusive"
    resource_key: str | None = None
    binding_fingerprint: str | None = None
```

- [ ] Write failing tests for manifest modes `isolated`, `shared_read_only`, and `exclusive`; absent metadata derives one capability-scoped exclusive key; contradictory isolated bindings fail preflight.
- [ ] Write failing tests that eligible replay repetitions use `SelfEvolveReplayTaskRunner`, share the initial snapshot fingerprint, use distinct materialized workspace paths, and aggregate by case/repetition index.
- [ ] Implement optional manifest/result binding fields `concurrency_mode`, opaque `resource_key`, and binding identity fingerprint with backward-compatible parsing/defaults.
- [ ] Implement replay Task inputs and fan-out in `_run_repetitions()`. Use effective concurrency `1` for exclusive claims and the policy limit for verified isolated/read-only bindings. Keep each evidence-retry chain inside one repetition Task.
- [ ] Re-run replay capability/adaptation/concurrency tests and existing replay integration tests.
- [ ] Commit `feat(self-evolve): run isolated replay tasks concurrently`.

### Task 9: Evaluation Tasks and safe judge concurrency

**Files:**
- Modify: `aworld/self_evolve/evaluation.py`
- Modify: `aworld/self_evolve/runtime.py`
- Modify: `aworld-cli/src/aworld_cli/top_level_commands/evaluator_cmd.py`
- Test: `tests/self_evolve/test_evaluation_concurrency.py`
- Modify test: `tests/self_evolve/test_evaluation.py`

```python
baseline, candidate = await evaluate_baseline_and_candidate(
    backend,
    dataset=dataset,
    candidate=variant,
    task_batch_executor=batch_executor,
    max_concurrency=2,
)
```

- [ ] Write failing tests that baseline/candidate judge Tasks overlap for a task-local backend, unsafe in-process backends serialize, and results remain `(baseline, candidate)` regardless of completion order.
- [ ] Write a failing subprocess-environment test proving concurrent CLI judges receive distinct `AWORLD_LOG_PATH` values without mutating the parent environment.
- [ ] Implement `SelfEvolveEvaluationTaskRunner` input execution and optional batch execution in `evaluate_baseline_and_candidate()`. Add an exclusive claim for custom in-process backends unless they expose `task_local_runtime = True`.
- [ ] For the default CLI evaluator, invoke `aworld-cli evaluator` in a child process with explicit environment and `--judge-timeout`; read the report artifact even when a failed gate produces a non-zero exit status. Never wrap concurrent CLI calls with the parent `_self_evolve_runtime_log_env` context manager.
- [ ] Re-run evaluation/concurrency tests and CLI evaluator tests.
- [ ] Commit `feat(self-evolve): batch isolated judge tasks`.

### Task 10: Telemetry, rollout, and full verification

**Files:**
- Modify: `aworld/self_evolve/concurrency.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/config/conf.py`
- Modify: `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- Modify: `tests/self_evolve/test_runner.py`
- Modify: `tests/core/test_optimize_top_level_command.py`

```python
report["execution"] = {
    "stages": telemetry.to_report(),
    "total_usage": total_usage,
}
```

- [ ] Write failing tests for per-stage elapsed time, queue wait, configured/effective/max-observed concurrency, resource serialization, and separate total token/replay usage.
- [ ] Add optional `SelfEvolveConcurrencyPolicy` injection without reading `judge_config.model_profile`; default optimize concurrency is `2`, and all-ones policy is the serial rollback.
- [ ] Add report telemetry and cancellation diagnostics without prompt, credential, or dependency contents.
- [ ] Run `conda run -n aworld_env python -m pytest tests/runners tests/memory tests/context tests/agents tests/hooks tests/self_evolve tests/core/test_optimize_top_level_command.py -q`.
- [ ] Run `git diff --check` and inspect `git status --short` to ensure unrelated workspace artifacts are not staged.
- [ ] Commit `feat(self-evolve): enable bounded AWorld-native execution`.
