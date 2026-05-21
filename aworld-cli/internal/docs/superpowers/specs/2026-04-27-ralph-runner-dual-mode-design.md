# RalphRunner Dual-Mode Design

## Context

`RalphRunner` should remain a framework-level Ralph capability for agents built on AWorld. It should not be treated as a CLI-only feature, and it should not absorb process-level orchestration concerns that belong to `aworld-cli` plugins or future external orchestrators.

The current implementation already has useful foundations:

- a runner-level Ralph loop
- `CompletionCriteria`-based stop detection
- `LoopContext` as loop-scoped state
- workspace/artifact persistence
- sandbox-backed filesystem access

However, it currently exposes only one practical execution style and leaves several intended Ralph capabilities only partially connected:

- `reuse_context` is a boolean instead of an explicit execution mode
- loop memory is implicit and spread across files and artifacts
- verification is not a first-class part of the loop
- reflection/planning configs exist but are not cleanly integrated into the main loop path

At the same time, the `deepagents/examples/ralph_mode` reference highlights an important Ralph idea worth adopting at the framework layer: each iteration can start from fresh task context while the filesystem and persisted artifacts carry memory across iterations.

## Goals

- Keep `RalphRunner` as a framework-level execution capability.
- Support two explicit Ralph execution modes:
  - `reuse_context`
  - `fresh_context`
- Preserve backward compatibility for existing `RalphRunner` and `aworld.runner.ralph_run(...)` callers.
- Reuse existing AWorld persistence primitives instead of inventing a separate storage system.
- Make loop memory, iteration input construction, and verification explicit framework concepts.
- Leave CLI session looping and fresh-process orchestration outside `RalphRunner`.

## Non-Goals

- Do not make `RalphRunner` depend on the `ralph-session-loop` CLI plugin.
- Do not make `RalphRunner` responsible for fresh-process or fresh-session orchestration.
- Do not make the framework-level Ralph capability available only through `aworld-cli`.
- Do not replace existing workspace/artifact/sandbox file infrastructure with a new storage subsystem.
- Do not force an immediate breaking change on current `reuse_context` callers.

## Design Summary

The recommended design is to keep the public `RalphRunner` surface compatible while introducing explicit internal loop policy and execution mode abstractions.

The framework should treat Ralph as two related but distinct concerns:

- **outer orchestration**
  This includes interactive session continuation, fresh process spawning, and operator-driven loop control. This stays outside `RalphRunner`.

- **inner framework loop**
  This includes iteration execution, loop memory, verification, reflection, and stop detection inside a single AWorld runtime. This remains the responsibility of `RalphRunner`.

Under this design, `RalphRunner` becomes a dual-mode framework loop:

- `reuse_context`: continue iterating with reused task/application context
- `fresh_context`: rebuild task context each iteration and inject only persisted loop memory

Both modes share the same completion criteria, loop memory contract, verification pipeline, and stop detector.

## API Shape

### Backward-compatible public entrypoints

The following existing surfaces remain valid:

- `RalphRunner(task, completion_criteria=..., **kwargs)`
- `aworld.runner.ralph_run(task, completion_criteria)`

### RalphConfig changes

`RalphConfig` should become the main place for mode and loop behavior.

Recommended additions:

- `execution_mode: Literal["reuse_context", "fresh_context"] = "reuse_context"`
- `memory_mode: Literal["artifacts_only", "artifacts_plus_history"] = "artifacts_only"`
- `iteration_prompt_template: Optional[str] = None`
- `verify: RalphVerifyConfig`
- `summary: RalphSummaryConfig`

Compatibility behavior:

- keep `reuse_context: bool` during a migration window
- if `execution_mode` is explicitly set, it wins
- otherwise map `reuse_context=True` to `execution_mode="reuse_context"`
- map `reuse_context=False` to `execution_mode="fresh_context"`
- mark `reuse_context` as compatibility-only in docs before later deprecation

### CompletionCriteria stays narrow

`CompletionCriteria` should continue to mean stop policy only:

- max iterations
- timeout
- max cost
- max failures
- answer/custom stop criteria

It should not be overloaded with verification, memory, or prompt-shaping behavior.

## Internal Architecture

### 1. RalphLoopPolicy

`RalphLoopPolicy` becomes the internal source of truth for how a Ralph iteration should behave.

Responsibilities:

- resolve effective `execution_mode`
- resolve memory behavior
- resolve verify behavior
- resolve summary/reflection behavior
- provide iteration prompt shaping instructions

`RalphRunner` should depend on this object instead of scattering mode decisions across the loop body.

### 2. IterationInputBuilder

`IterationInputBuilder` builds the effective input for each iteration.

For `reuse_context`:

- read structured memory from loop storage
- append prior answer/reflection/verify feedback to the ongoing context
- keep the existing application/task context alive

For `fresh_context`:

- create a fresh sub-context for the iteration
- do not reuse the previous conversational/runtime context
- inject only structured loop memory into the new iteration input

Recommended normalized iteration input sections:

- original task
- current iteration number
- previous answer summary
- reflection or failure feedback
- verification requirements
- execution rule for the next step

This makes framework-side Ralph behavior more predictable than the current string concatenation path.

### 3. LoopMemoryStore

`LoopMemoryStore` should be introduced as a thin Ralph abstraction over existing AWorld persistence primitives.

It should not invent a new storage backend. It should reuse:

- `WorkSpace.add_artifact(...)` for small structured artifacts
- `workspace.get_artifact_data(...)` for retrieval
- `sandbox.file.write_file(...)` and `sandbox.file.read_file(...)` for file-based intermediate state

Recommended storage split:

- **artifact-first**
  Store structured, relatively small loop memory here:
  - `iteration_summary`
  - `reflection_feedback`
  - `verify_result`
  - `iteration_metadata`

- **file-assisted**
  Store larger or naturally file-shaped content here:
  - full answer payloads
  - generated summaries when large
  - command outputs/logs from verification
  - any operator-inspectable loop artifacts

This matches current AWorld capabilities and avoids parallel file management systems.

### 4. IterationEvaluator

`IterationEvaluator` should run after each task execution and produce structured post-iteration memory.

Responsibilities:

- evaluate verification
- produce summary
- produce reflection feedback
- emit completion-related signals for the next stop check

This is the point where currently disconnected config concepts become real runtime behavior.

## Loop Memory Contract

The loop should use a fixed memory contract across both modes.

Recommended fields:

- `previous_answer`
- `iteration_summary`
- `reflection_feedback`
- `verify_result`
- `iteration_metadata`

Recommended minimum `iteration_metadata`:

- iteration number
- start/end timestamp
- execution mode
- task id
- success/failure flag
- failure category if known

This contract matters most for `fresh_context`, where loop continuity depends on persisted memory rather than runtime context reuse.

## Verification Design

Framework-level Ralph should support real verification, not only prompt-declared verification.

Recommended `RalphVerifyConfig`:

- `enabled: bool = False`
- `commands: list[str] = []`
- `run_on_each_iteration: bool = False`
- `run_before_completion: bool = True`
- `success_policy: Literal["all", "any"] = "all"`
- `max_output_chars: int = 12000`

Verification execution model:

- run commands in the Ralph workspace
- capture stdout/stderr/exit status
- persist result via `LoopMemoryStore`
- if verification fails, feed a structured failure summary into the next iteration input

This is a framework-level upgrade beyond the CLI plugin's phase-1 declarative verify model.

## Execution Mode Semantics

### reuse_context

Use when the agent benefits from runtime continuity.

Behavior:

- preserve the active task/application context
- inject prior memory as additional guidance
- use persisted memory mainly as reinforcement and traceability

Best for:

- agent workflows that benefit from growing conversational state
- toolchains where context reconstruction is expensive

### fresh_context

Use when each iteration should restart cleanly from persisted outputs.

Behavior:

- build a fresh task context per iteration
- do not carry forward full runtime/conversation state
- reconstruct continuity from persisted loop memory and workspace files

Best for:

- long-running Ralph loops
- tasks sensitive to context drift
- agent implementations that should behave more like external Ralph fresh-run patterns without becoming process orchestrators

## Data Flow

### Common loop flow

1. `RalphRunner` resolves effective config and builds `RalphLoopPolicy`.
2. Stop detector checks `CompletionCriteria`.
3. `IterationInputBuilder` builds the next iteration input.
4. Runner executes the task.
5. Runner stores primary output via `LoopMemoryStore`.
6. `IterationEvaluator` runs verification/summary/reflection.
7. Evaluator writes structured loop memory.
8. Next stop check uses `CompletionCriteria` and evaluator-produced signals.

### fresh_context-specific difference

The critical difference is step 3:

- `reuse_context` updates the running context
- `fresh_context` creates a new sub-context and repopulates it from memory

The rest of the loop stays consistent.

## Error Handling

The dual-mode design should make failures explicit instead of hiding them in prompt text.

Recommended handling:

- task execution failure increments loop failure metrics
- verification failure is stored distinctly from execution failure
- corrupted or unreadable loop memory should degrade gracefully by:
  - logging the issue
  - falling back to minimal memory reconstruction
  - continuing if safe
- only `CompletionCriteria` and fatal runtime errors should terminate the framework loop

This keeps the loop resilient while preserving debuggability.

## Migration Strategy

### Phase A: structural compatibility

- add `execution_mode`
- keep `reuse_context`
- introduce `RalphLoopPolicy`
- wrap existing answer/reflection persistence behind `LoopMemoryStore`

### Phase B: real dual-mode behavior

- make `fresh_context` a first-class path
- move iteration input building out of ad hoc `read_to_task_context(...)`
- normalize iteration input structure

### Phase C: verification integration

- add `RalphVerifyConfig`
- run real verification commands
- persist verification outputs
- use verification failures as structured feedback

### Phase D: reflection and summary integration

- connect `ReflectionConfig` and summary generation into the main post-iteration path
- preserve planning as optional future work rather than mandatory first-slice scope

## Testing Strategy

The upgrade should be covered at three levels.

### Unit tests

- config compatibility mapping from `reuse_context` to `execution_mode`
- `IterationInputBuilder` behavior in both modes
- `LoopMemoryStore` read/write behavior for artifacts and files
- `IterationEvaluator` verify result shaping

### Runner integration tests

- `reuse_context` mode preserves context and loops correctly
- `fresh_context` mode rebuilds context while preserving memory continuity
- stop conditions behave identically across both modes
- verification failures feed the next iteration correctly

### Regression tests

- existing `RalphRunner` callers continue to work without configuration changes
- `aworld.runner.ralph_run(...)` remains valid
- workspace file outputs and reflection artifacts remain readable across versions

## Recommendation

Implement this as a framework-focused internal refactor with explicit dual-mode semantics, not as a CLI-inspired orchestration layer.

The key idea to borrow from external Ralph implementations is:

- fresh context can be a first-class iteration mode
- persisted workspace state is the loop memory

The key idea not to borrow is:

- process-level orchestration as the runner's responsibility

That separation keeps AWorld with two clean Ralph adoption paths:

- framework/programmatic Ralph via `RalphRunner`
- CLI session Ralph via plugin-hosted session looping
