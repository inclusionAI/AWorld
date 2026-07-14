# AWorld-Native Self-Evolve Execution Design

## Status

Architecture approved; the extension-class and `Task.runner_cls` revision is
pending written-design review. This document supersedes the lightweight
direct-invocation portion of the earlier model-backed candidate generation
design. It does not change the replay-capability ownership boundary: domain
adapters remain skill-owned.

## Problem

Self-evolve currently owns a deterministic domain workflow for dataset loading,
replay adaptation, candidate generation, rollout, evaluation, gates, lineage,
and apply policy. That domain workflow is valid, but candidate generation was
integrated with AWorld at too low a level: it manually created a basic `Context`,
assembled messages, and called `Agent.invoke_model()` directly.

That path reused the AWorld model/provider call, hooks, retries, and accounting,
but bypassed the standard Agent/Runner task lifecycle and its context and memory
handling. Registering the agent in `AgentFactory` fixes discovery by hooks but
does not restore the missing lifecycle. The CLI `PreLlmCostHook` is a
history-based guard for interactive CLI sessions and cannot enforce the budget of
a newly constructed one-shot candidate prompt.

AWorld also exposes configuration fields such as `AgentConfig.max_input_tokens`,
`ModelConfig.max_model_len`, and memory compression settings, but the current
generic Agent path does not consistently enforce a current-request input budget.
Memory store implementations already support in-memory operation, while many
Agent and Runner call sites still resolve memory through the process-global
`MemoryFactory.instance()`.

The correction must reuse AWorld as the execution framework without turning the
self-evolve domain state machine into an autonomous LLM workflow.

## Goals

1. Execute every model-backed candidate generation request as a standard AWorld
   `Task` through the Agent/Runner lifecycle.
2. Enforce a model-aware input budget before the provider is called.
3. Reuse AWorld prompt assembly and context reducers instead of adding a private
   self-evolve compression stack.
4. Support isolated, ephemeral memory for local parallel candidate workers
   without redesigning the Memory system.
5. Preserve the deterministic ordering and gate semantics of `SelfEvolveRunner`.
6. Support two entry modes with the same execution semantics:
   - an independent dataset/offline self-evolve run;
   - an aworld-cli main agent delegating an entire self-evolve run as a subtask.
7. Preserve backward compatibility for existing agents that use the global
   memory factory and do not opt into prompt budgeting.
8. Deliver AWorld capabilities as opt-in extension classes and additive APIs;
   self-evolve is their first and only consumer in this change.

## Non-Goals

- Replacing AWorld Memory storage, retrieval, embedding, or summary algorithms.
- Providing context-local Memory for distributed Spark, Ray, or Kubernetes
  runtimes in this change. Context-local resource ownership is local-runtime
  only.
- Letting an LLM coordinator choose self-evolve stages, reorder gates, or decide
  whether a candidate is accepted.
- Guaranteeing byte-identical remote model outputs. The design guarantees
  deterministic orchestration and result ordering.
- Moving Browser/CDP or other domain replay adapters into `aworld/self_evolve`.
- Making the CLI history hook responsible for candidate prompt budgeting.
- Changing the default behavior of `Agent`, `ApplicationContext`,
  `Runners.run_task()`, or `LocalRuntime.execute()`.
- Migrating unrelated AWorld agents to the new extension classes.

## Ownership Boundary

### AWorld framework owns

- Agent and Task lifecycle;
- Context construction and child-context isolation;
- context-local runtime resource resolution in local mode;
- memory access, prompt assembly, prompt budgeting, and generic reduction;
- hooks, model invocation, retries, token usage, cancellation, and bounded
  parallel task execution;
- generic observability for prompt-budget decisions.

### Self-evolve owns

- dataset and trajectory-context reconstruction;
- Replay Adaptation Compiler and capability requirements;
- the candidate input/output protocol and package validation;
- population slot definitions and candidate lineage;
- baseline/candidate rollout isolation;
- evaluation, gates, selection, apply policy, and run reports.

Self-evolve may label prompt sections with generic priority and reduction policy,
but it must not implement token counting, summary generation, truncation, or
memory-store selection itself.

## AWorld Additive Extension APIs

The implementation must use extension classes and additive APIs. Base classes
retain their current default behavior. A minimal compatibility seam may be added
to a base factory only when existing callers receive exactly the same object and
semantics when no extension scope is active.

### 1. `PromptBudgetedAgent`

Add a generic `PromptBudgetedAgent(Agent)` extension and a
`PromptBudgetPolicy`. Existing `max_input_tokens` remains the agent input
ceiling; the policy controls overflow handling and section reduction without
adding behavior to the base `Agent`:

```python
class PromptBudgetPolicy(BaseModel):
    overflow_strategy: Literal["compact_then_error", "error"] = "compact_then_error"
    minimum_remaining_tokens: int = 0


class PromptBudgetedAgent(Agent):
    def __init__(self, *, prompt_budget_policy: PromptBudgetPolicy, **kwargs): ...
```

`CandidateGenerationAgent` inherits from `PromptBudgetedAgent`. Unrelated agents
continue inheriting directly from `Agent` and do no additional token counting or
reduction.

The extension resolves the effective budget after prompt assembly and before the
provider call:

```text
reserved_output_tokens = effective max_tokens or max_completion_tokens
model_input_capacity = ModelConfig.max_model_len - reserved_output_tokens
input_budget = min(AgentConfig.max_input_tokens, model_input_capacity)
```

Messages, tool schemas, and model/provider overhead count toward the budget. The
output-token parameter is resolved once and shared by budgeting and the provider
request. Invalid configurations fail before provider invocation.

The extension performs a final assertion in its `invoke_model()` override. It
never changes base `Agent.invoke_model()` behavior.

### 2. `BudgetedPromptAssemblyProvider`

Add a provider decorator rather than modifying the existing assembly providers:

```python
class BudgetedPromptAssemblyProvider(PromptAssemblyProvider):
    def __init__(self, delegate: PromptAssemblyProvider, policy: PromptBudgetPolicy): ...
```

`PromptBudgetedAgent` wraps the provider returned by the existing Context/Agent
resolution path. The decorator builds the normal plan first, applies generic
budget sections, and returns a compatible `PromptAssemblyPlan` with reduced
messages and bounded observability.

Budget metadata is represented by extension types such as
`BudgetedPromptSection`/`BudgetedPromptAssemblyPlan`; existing `PromptSection`
and `PromptAssemblyPlan` constructors do not change. Candidate callers supply
generic section hints through plan metadata:

- `priority`;
- `required`;
- `compressible`;
- reducer name.

System instructions and the current task are required. History, summaries,
retrieved knowledge, tool results, and explicitly optional evidence may be
reduced. No reducer imports or branches on self-evolve types.

The decorator applies reducers by ascending priority and stable section order.
The initial reusable reducers are complete-turn history trimming, reuse of an
existing Memory summary, existing tool-result compaction/offload, bounded
head/tail reduction with an omission marker, and optional-section removal.
Reducers do not mutate source Context or dataset objects.

After reduction, the decorator rebuilds the plan through the delegate so stable
hashes and provider prompt-cache keys describe the final request rather than the
pre-reduction request. Diagnostics contain token counts, section names, reducer
names, and omission counts, never section contents.

### 3. `LocalMemoryScope`

Reuse the existing `MemoryBase`, `MemoryFactory.from_config()`, and
`InMemoryMemoryStore`. Add a local async-task scope backed by `contextvars`:

```python
class LocalMemoryScope:
    def __init__(self, memory: MemoryBase): ...
    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc, tb): ...
    @classmethod
    def current(cls) -> MemoryBase | None: ...
```

The only compatibility seam in `MemoryFactory.instance()` is:

```python
scoped = LocalMemoryScope.current()
if scoped is not None:
    return scoped
```

When no scope is active, the existing cached/global path executes unchanged.
This avoids replacing all Agent memory call sites and avoids calling
`MemoryFactory.init()` for a candidate. Python `contextvars` isolate concurrent
local Tasks and are inherited by their child async tasks.

This scoped override is runtime-only, is not serialized, and is rejected by the
self-evolve execution adapter for non-local runtimes. It does not change Memory
storage, filters, summaries, retrieval, or provider implementations.

### 4. `LocalIsolatedApplicationContext`

Add an `ApplicationContext` extension that owns one local resource bundle:

```python
class LocalIsolatedApplicationContext(ApplicationContext):
    memory: MemoryBase
    checkpoint_repository: InMemoryCheckpointRepository
    execution_scope: str
```

Its factory constructs memory with the existing `InMemoryMemoryStore`, disables
workspace persistence unless explicitly supplied, and creates child contexts
with new owned memory when sibling isolation is requested. It never changes
`ApplicationContext.create()` defaults.

For a candidate worker:

```text
memory: existing MemoryBase + InMemoryMemoryStore
history scope: task
summary: disabled
checkpoint: in-memory
workspace persistence: disabled
execution scope: self_evolve
```

Schema repair remains in the same population slot, but its invalid response and
repair instruction are explicit Task input. Correctness does not depend on
hidden history.

### 5. `ExecutionScopedHook`

Add an optional hook extension whose `exec()` method first checks an execution
scope predicate. Existing hooks continue inheriting from `Hook` and therefore run
unchanged.

`PreLlmCostHook` adopts the extension and applies only to `cli_interactive`
contexts with a CLI history session. Candidate Tasks use
`execution_scope="self_evolve"` and therefore do not emit empty CLI-history
reports. Generic before/after LLM hooks continue to run.

### 6. `DeterministicTaskBatchExecutor`

Add an independent local batch API instead of changing `LocalRuntime.execute()`
or `Runners.run_task()`:

```python
class DeterministicTaskBatchExecutor:
    async def run(
        self,
        tasks: list[Task],
        *,
        max_concurrency: int,
        failure_policy: Literal["indexed_fail_fast", "collect_all"],
    ) -> list[TaskBatchResult]: ...
```

The executor invokes the existing `Runners.run_task()` for each Task, uses a
local semaphore for bounded concurrency, preserves input-order results, and
cancels queued/in-flight higher-index Tasks under indexed fail-fast. Existing
runtime and Runner APIs are not modified. Self-evolve is the only consumer in
this change.

## Self-Evolve Execution Architecture

### `Task.runner_cls` adapters

Self-evolve uses the existing `Task.runner_cls` extension point at two levels.
Neither class changes default Runner selection.

`SelfEvolveTaskRunner(TaskRunner)` is the outer AWorld adapter for one complete
self-evolve run. Its constructor accepts the `run_conf` keyword supplied by
`choose_runners()`, stores it for local-runtime validation, and calls
`TaskRunner.__init__(task, agent_oriented=False)`. Its `do_run()` delegates
domain execution to the existing deterministic `SelfEvolveRunner` and maps
progress, cancellation, artifacts, and the final run report into
`TaskResponse`. It implements the required `streaming()` contract by forwarding
the self-evolve progress stream when streaming is enabled. It does not
reimplement dataset, replay, evaluation, gate, or apply logic.

Both entry modes submit the same outer Task:

```python
Task(
    input=self_evolve_request,
    runner_cls="aworld.self_evolve.runtime.SelfEvolveTaskRunner",
)
```

`SelfEvolveCandidateTaskRunner(TaskEventRunner)` is the inner adapter for one
candidate-generation slot. It reuses the standard event-driven Agent lifecycle,
enters the slot Context's `LocalMemoryScope`, and releases owned resources in
`post_run()`. The candidate Task selects it explicitly through `runner_cls`.
Its constructor also accepts and removes `run_conf` before delegating to
`TaskEventRunner`, because the base event Runner does not currently accept that
keyword.

The construction contract is therefore explicit:

```python
class SelfEvolveTaskRunner(TaskRunner):
    def __init__(self, task: Task, *, run_conf: RunConfig | None = None):
        self.run_conf = run_conf
        super().__init__(task, agent_oriented=False)


class SelfEvolveCandidateTaskRunner(TaskEventRunner):
    def __init__(self, task: Task, *, run_conf: RunConfig | None = None):
        self.run_conf = run_conf
        super().__init__(task)
```

This matches the existing `choose_runners()` call
`new_instance(task.runner_cls, task=task, run_conf=run_conf)` without widening
the constructor of any default Runner.

The inner Runner waits for or cancels owned background tasks before leaving the
scope. Candidate summary is disabled initially, so no summary task should
outlive the worker Task. Schema parsing and repair scheduling remain in the
self-evolve coordinator; one inner Task performs one standard Agent execution.

The Runner adapters live under `aworld/self_evolve` because they encode
self-evolve Task/result semantics. They compose AWorld's generic extension APIs
(`PromptBudgetedAgent`, `LocalMemoryScope`,
`LocalIsolatedApplicationContext`, and `DeterministicTaskBatchExecutor`) rather
than adding self-evolve branches to the default Runner.

### Deterministic control plane

`SelfEvolveRunner` remains the only authority for stage transitions:

```text
dataset/context reconstruction
  -> replay adaptation preflight
  -> candidate generation execution plan
  -> candidate package validation
  -> rollout
  -> evaluation
  -> gates
  -> selection/apply/report
```

No coordinator LLM may add, skip, or reorder these stages.

### Candidate worker topology

For each population iteration, self-evolve creates an immutable execution plan
with slots `0..N-1`. Each slot receives:

- the same target and dataset snapshot fingerprints;
- its explicitly assigned mutation strategy/input;
- a distinct `CandidateGenerationAgent` instance;
- a distinct local `ApplicationContext` and task-local in-memory resource set;
- a stable task id derived from run id, iteration, and slot;
- the injected mutation `ModelConfig`.

Each agent is run through `Runners.run_task(Task(...))`. Candidate generation no
longer directly calls `invoke_model()` or manually constructs a base Context.
Each candidate Task explicitly selects `SelfEvolveCandidateTaskRunner` through
`Task.runner_cls`.

The worker Task uses structured prompt sections for the output contract, target
content, replay requirements, trajectory evidence, lessons, and prior feedback.
Section policies are generic prompt-budget metadata; the prompt budget processor
decides whether reduction is needed.

### Deterministic parallel semantics

Parallel completion order must not influence accepted results:

1. Slots are ordered before submission.
2. Results are collected and reported in slot order.
3. Schema repair is contained within its slot and has one bounded retry.
4. Candidate ids and tie-breakers use existing stable fingerprints and slot
   metadata, not completion timestamps.
5. On infrastructure failure, the lowest failing slot defines the population
   cutoff. Successful results from lower slots remain eligible; results from the
   failing slot and all higher slots are discarded, even if a higher slot
   completed earlier.
6. Queued and in-flight higher slots are cancelled, and no later batch is
   submitted.

With concurrency `1`, this is identical to current sequential fail-fast behavior.
With concurrency greater than `1`, it provides parallel execution without making
the resulting population depend on network timing.

Model outputs may still differ across remote provider calls. The run report
records model-profile fingerprint, request fingerprint, supported seed value,
effective budgets, and response usage so model nondeterminism remains auditable.

## Dual Entry Mode

### Independent/offline mode

The CLI or Python caller submits an outer Task selecting
`SelfEvolveTaskRunner`. That adapter invokes the existing `SelfEvolveRunner`,
constructs the fixed candidate worker execution plan, and runs it on AWorld's
local runtime. No main agent or CLI conversation Context is required.

### Main-agent subtask mode

The aworld-cli main agent delegates an entire self-evolve request to a
`SelfEvolveCoordinatorAgent` or equivalent registered subtask entry. The entry
validates the request and submits the same outer Task selecting
`SelfEvolveTaskRunner`; it does not perform candidate selection itself. The main
agent may start, cancel, and inspect the run but cannot modify stage ordering or
gate outcomes.

Both entry modes produce the same execution plan, artifacts, and reports for the
same explicit inputs.

## Error Handling

- Prompt overflow after allowed reduction becomes
  `PromptBudgetExceededError` and is classified as candidate-generation
  infrastructure failure.
- Context resource construction failure occurs before the worker Task begins and
  contains no prompt content.
- Provider and Agent runtime failures remain bounded typed diagnostics.
- Schema violations remain candidate protocol failures and receive one repair
  Task.
- Cancellation is explicit in Task status and lineage; cancelled higher slots do
  not become candidates.
- No raw trajectory prompt is written to generic failed-request artifacts by the
  specialized candidate agent.

## Observability

Every candidate Task records bounded metadata:

- run, iteration, slot, task, agent, and context ids;
- model-profile and request fingerprints;
- original input tokens, input budget, reserved output tokens, and final input
  tokens;
- section-level token counts and reducer names without section contents;
- task status, repair count, cancellation reason, and provider usage;
- memory scope and runtime mode, but no memory contents.

The self-evolve report references these Task records and retains its existing
lineage and gate artifacts.

## Compatibility and Rollout

Implementation proceeds in two layers:

1. AWorld additive extension layer:
   `PromptBudgetedAgent`, `BudgetedPromptAssemblyProvider`,
   `LocalMemoryScope`, `LocalIsolatedApplicationContext`,
   `ExecutionScopedHook`, and `DeterministicTaskBatchExecutor`.
2. Self-evolve layer:
   add the two `Task.runner_cls` adapters, replace direct candidate invocation
   with standard Tasks, build the fixed worker plan, add structured prompt hints,
   and integrate ordered fail-fast results.

Compatibility rules:

- only agents inheriting `PromptBudgetedAgent` receive prompt-budget behavior;
- `MemoryFactory.instance()` returns the exact existing global instance whenever
  no `LocalMemoryScope` is active;
- non-local self-evolve execution rejects `LocalMemoryScope` with a clear
  configuration error rather than silently sharing or serializing local objects;
- existing `Agent`, `ApplicationContext`, `TaskRunner`, `Runners.run_task()`, and
  `LocalRuntime.execute()` defaults remain unchanged;
- existing agents, swarms, and CLI sessions retain current behavior;
- the earlier no-model deterministic proposal path remains available.

The temporary direct-invocation implementation and its manual Context creation
are removed only after the standard Runner path passes equivalent failure,
security, and performance tests.

## Verification

### AWorld framework tests

- a base `Agent` never invokes prompt-budget extension code;
- budget resolution reserves the effective output limit and includes tools;
- required sections are never silently removed;
- reducers execute in stable priority/order and produce bounded diagnostics;
- over-budget required content fails before provider invocation;
- reduction rebuilds stable hashes and provider cache keys from final messages;
- `MemoryFactory.instance()` is object-identical to its prior global result when
  no local scope is active;
- task-local memory uses distinct existing `InMemoryMemoryStore` instances;
- a task-local candidate does not read or mutate global main-agent memory;
- child candidate contexts do not share memory with sibling contexts;
- global-memory agents remain backward compatible;
- `PreLlmCostHook` skips non-interactive self-evolve contexts;
- local batch results are ordered by input slot and cancellation is bounded;
- existing `Runners.run_task()` and `LocalRuntime.execute()` behavior is unchanged;
- non-local self-evolve execution rejects local memory scope;
- both custom Runner constructors accept the `run_conf` keyword supplied by
  `choose_runners()` without forwarding it to default Runner constructors;
- the outer Runner fulfills both `do_run()` and `streaming()` contracts while
  retaining `TaskRunner.run()` lifecycle and cleanup behavior.

### Self-evolve tests

- every model-backed mutation is executed through `Runners.run_task`;
- outer offline and main-agent requests select `SelfEvolveTaskRunner` through
  `Task.runner_cls`;
- each candidate slot selects `SelfEvolveCandidateTaskRunner` through
  `Task.runner_cls`;
- candidate prompts pass through prompt-budget processing before the fake
  provider;
- candidate slots use isolated local Context resources;
- sequential and parallel execution produce the same ordered population for the
  same mocked slot results;
- a failure at slot `k` keeps only valid lower slots and cancels/discards higher
  slots;
- schema repair stays within its slot and does not pollute sibling memory;
- offline and main-agent-subtask entry modes produce equivalent plans and reports;
- replay, evaluation, gates, lineage, apply policy, and skill-owned replay
  capability behavior remain unchanged;
- no trajectory content or credentials appear in budget diagnostics, reports, or
  generic failure artifacts.

## Acceptance Criteria

The design is complete when:

1. Candidate generation contains no direct `invoke_model()` orchestration.
2. A candidate provider call cannot occur without a resolved and verified input
   budget.
3. Local candidate workers can run concurrently without sharing memory with each
   other or the main agent.
4. The solution reuses the existing in-memory Memory implementation and does not
   change Memory storage/retrieval semantics.
5. Self-evolve stage and gate ordering remains controlled solely by
   `SelfEvolveRunner`.
6. Offline and main-agent-subtask modes execute the same deterministic plan.
7. Existing non-opt-in AWorld agents remain behaviorally compatible.
8. Default Runner and LocalRuntime execution paths contain no self-evolve branch.
