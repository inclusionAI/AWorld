# AWorld-Native Self-Evolve Execution Design

## Status

Approved architecture for implementation planning. This document supersedes the
lightweight direct-invocation portion of the earlier model-backed candidate
generation design. It does not change the replay-capability ownership boundary:
domain adapters remain skill-owned.

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

## AWorld Framework Changes

### 1. Prompt budget contract

Add an opt-in `PromptBudgetConfig` to `AgentConfig`. Existing
`max_input_tokens` remains the agent input ceiling; it is not duplicated in the
new config. The new config controls activation, overflow behavior, and section
reduction:

```python
class PromptBudgetConfig(BaseModel):
    enabled: bool = False
    overflow_strategy: Literal["compact_then_error", "error"] = "compact_then_error"
    minimum_remaining_tokens: int = 0
```

Candidate-generation agents enable the feature. Existing agents remain unchanged
until they opt in.

The effective budget is resolved after prompt assembly and before the provider
call:

```text
reserved_output_tokens = effective max_tokens or max_completion_tokens
model_input_capacity = ModelConfig.max_model_len - reserved_output_tokens
input_budget = min(AgentConfig.max_input_tokens, model_input_capacity)
```

The calculation includes messages, tool schemas, and model/provider message
overhead. Invalid configurations where the output reservation consumes the
model context window fail before provider invocation.

The output-token parameter is resolved once and shared by the budget processor
and provider request, preventing the two calculations from diverging.

### 2. Structured prompt sections

Extend `PromptSection`/`PromptAssemblyPlan` with budget metadata:

```python
@dataclass
class PromptSection:
    name: str
    kind: str
    stability: str
    content: Any
    priority: int = 50
    required: bool = False
    compressible: bool = True
    reducer: str | None = None
    original_tokens: int | None = None
    final_tokens: int | None = None
```

The default assembly provider assigns safe generic policies:

- system instructions and the current user turn are required;
- tool schemas are required unless the owning Agent explicitly marks tools
  optional;
- conversation history, summaries, retrieved knowledge, and tool results are
  compressible;
- stable-prefix metadata and provider cache behavior are preserved after
  reduction.

Callers may supply section hints through the existing prompt-assembly metadata.
Hints describe generic properties only. A reducer never imports or branches on
self-evolve types.

### 3. Prompt budget processor

Add a generic `PromptBudgetProcessor` at the Agent request boundary. The standard
`Agent.async_policy()` flow becomes:

```text
build_llm_input
  -> prompt assembly plan
  -> prompt budget processor
  -> record request observability
  -> invoke_model
```

The processor performs these steps in a stable order:

1. Count the assembled request.
2. Return unchanged when it fits.
3. Emit `BEFORE_CONTEXT_COMPACT` when reduction is required.
4. Reduce optional sections by ascending priority and stable section order.
5. Recount after each reduction phase.
6. Emit `AFTER_CONTEXT_COMPACT` with bounded diagnostics.
7. Raise `PromptBudgetExceededError` if required sections still do not fit.

The initial generic reducer registry contains only reusable policies:

- conversation-history trimming by complete turn;
- existing Memory summary reuse;
- existing tool-result compaction/offload;
- bounded head/tail reduction with an explicit omission marker;
- optional-section removal.

Reducers return a new plan and diagnostics; they do not mutate shared context or
the source dataset. The processor records section names, token counts, reducer
names, and omission counts, but never records full prompt content in its
diagnostic event.

`Agent.invoke_model()` also performs a final budget assertion. This is a guard
against custom Agents bypassing `async_policy()`, not an alternative processing
path. It never performs a second reduction.

### 4. Local Context runtime resources

Add `ContextRuntimeResources` as an optional runtime-only property of `Context`:

```python
@dataclass
class ContextRuntimeResources:
    memory: MemoryBase | None = None
    checkpoint_repository: CheckpointRepository | None = None
    workspace: WorkSpace | None = None
    owned: bool = False
```

These resources are not serialized into distributed task payloads. They are
supported only by the local runtime in this change.

Add a declarative local resource configuration to `AmniContextConfig`:

```python
class LocalContextResourceConfig(BaseModel):
    memory_scope: Literal["global", "task_local"] = "global"
    checkpoint_scope: Literal["default", "task_local"] = "default"
    workspace_mode: Literal["default", "disabled"] = "default"
```

When `memory_scope="task_local"`, the local Context factory constructs a
`MemoryBase` using the existing `InMemoryMemoryStore`. It does not call
`MemoryFactory.init()` and therefore does not replace the main agent's global
memory. Child contexts fork owned task-local resources when isolation is
requested; they never reuse another candidate worker's memory instance.

### 5. Context-aware memory resolution

Add one framework resolver:

```python
def resolve_memory(context: Context | None) -> MemoryBase:
    if context and context.runtime_resources.memory is not None:
        return context.runtime_resources.memory
    return MemoryFactory.instance()
```

Replace direct global-memory access along the standard Agent/Runner path with
this resolver, including:

- Agent message transformation and history reads;
- Agent memory writes and cleanup;
- `DefaultMemoryHandler`;
- `ApplicationContext.MemoryService`.

The fallback preserves current behavior for every Context without a local
resource. Existing Memory implementations, schemas, filters, summary logic, and
retrieval code remain unchanged.

For a local candidate worker, the selected policy is:

```text
memory scope: task_local
store: existing InMemoryMemoryStore
history scope: task
summary: disabled for the initial one-shot candidate task
checkpoint: task-local in-memory
workspace persistence: disabled
```

Schema repair belongs to the same population slot. The invalid response and
repair instruction are passed explicitly in the repair Task input, so correctness
does not depend on hidden conversation history.

### 6. Hook applicability

Add an execution-scope marker to Context and an optional applicability method to
hooks. The initial scopes are `cli_interactive`, `self_evolve`, and `generic`.
Hooks without a predicate retain current behavior.

`PreLlmCostHook` applies only to `cli_interactive` contexts with a CLI history
session. Candidate tasks use the AWorld prompt budget processor and therefore do
not emit empty CLI-history reports. Generic before/after LLM hooks continue to
run for candidate tasks.

### 7. Local bounded task execution

The local runtime already accepts a list of Tasks and executes coroutine runners
concurrently. Extend its local batch policy with:

- `max_concurrency`;
- stable input-order result collection;
- cancellation of queued and in-flight higher-index tasks on fail-fast;
- structured per-task failure results;
- no process-global environment mutation.

The default behavior remains compatible. Self-evolve opts into bounded
concurrency and indexed fail-fast semantics.

## Self-Evolve Execution Architecture

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

The CLI or Python caller creates `SelfEvolveRunner` directly. It constructs the
fixed candidate worker execution plan and runs it on AWorld's local runtime. No
main agent or CLI conversation Context is required.

### Main-agent subtask mode

The aworld-cli main agent delegates an entire self-evolve request to a
`SelfEvolveCoordinatorAgent` or equivalent registered subtask entry. The entry
validates the request and invokes the same `SelfEvolveRunner`; it does not perform
candidate selection itself. The main agent may start, cancel, and inspect the run
but cannot modify stage ordering or gate outcomes.

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

1. AWorld framework layer:
   prompt budget, structured section policies, local Context runtime resources,
   context-aware memory resolution, hook applicability, and bounded local task
   execution.
2. Self-evolve layer:
   replace direct candidate invocation with standard Tasks, build the fixed worker
   plan, add structured prompt hints, and integrate ordered fail-fast results.

Compatibility rules:

- prompt budgeting is initially opt-in;
- global memory remains the default;
- non-local runtimes reject `task_local` resource configuration with a clear
  configuration error rather than silently sharing or serializing local objects;
- existing agents, swarms, and CLI sessions retain current behavior;
- the earlier no-model deterministic proposal path remains available.

The temporary direct-invocation implementation and its manual Context creation
are removed only after the standard Runner path passes equivalent failure,
security, and performance tests.

## Verification

### AWorld framework tests

- budget resolution reserves the effective output limit and includes tools;
- required sections are never silently removed;
- reducers execute in stable priority/order and produce bounded diagnostics;
- over-budget required content fails before provider invocation;
- before/after context-compaction hooks fire exactly once when reduction occurs;
- task-local memory uses distinct existing `InMemoryMemoryStore` instances;
- a task-local candidate does not read or mutate global main-agent memory;
- child candidate contexts do not share memory with sibling contexts;
- global-memory agents remain backward compatible;
- `PreLlmCostHook` skips non-interactive self-evolve contexts;
- local batch results are ordered by input slot and cancellation is bounded;
- non-local runtimes reject task-local resources.

### Self-evolve tests

- every model-backed mutation is executed through `Runners.run_task`;
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
