## Context

Evaluator v1 deliberately optimized for shipping a working framework substrate and official CLI flow first. The result is structurally sound, but its abstraction ceiling is still low in four places:

- execution can only target AWorld-native `agent` and `task` paths plus judge-only `static`
- eval targets still know too much about runner entrypoints
- judge contracts are validated structurally but not typed as first-class models
- gate policies are inspectable but too narrow for multi-metric release decisions

The goal of this follow-up is to raise that ceiling without discarding the v1 substrate. The evaluator remains an AWorld framework capability under `aworld/evaluations/`, and `aworld-cli` remains only an official consumer and assembly layer. This is a v1 extensibility increment, not a verifiers-parity runtime: rollout-owning harnesses, user simulators, lifecycle hooks, child-state composition, and training reward semantics are deferred.

## Goals / Non-Goals

**Goals:**

- Add a program-backed execution path that fits the existing suite/case/execution/state model.
- Add a lightweight harness definition so reusable execution behavior is explicit without adopting verifiers' broader object model.
- Isolate execution mechanics behind a framework-owned adapter boundary so runtime-specific invocation does not leak across evaluator code.
- Make typed judge-output models the primary contract for suite-backed evaluation.
- Support structured composite gate policies for multi-metric pass, fail, and approval decisions.
- Support suite-declared trajectory scoring alongside final-result scoring.
- Preserve compatibility for existing v1 suite-backed flows, reports, and CLI evaluator behavior.

**Non-Goals:**

- Replacing the existing `EvalTarget -> Evaluator -> EvaluateRunner` orchestration skeleton.
- Creating a public external evaluator API v2 in this change.
- Reworking the `aworld-cli evaluator` command shape beyond compatibility adjustments required by framework changes.
- Shipping baseline history, trend analysis, or evaluator comparison workflows.
- Converging AWorld onto verifiers' public API terminology or object model.
- Adding external harness package registries, lifecycle decorators, training reward semantics, sandbox command execution, or child-state composition.

## Ownership Model

| Concept | Owns | Must not own |
| --- | --- | --- |
| `EvalSuiteDef` / `EvalCaseDef` | Domain inputs, case metadata, judge contract, declared scorers, gates | Runtime handles in declarative or persisted suite definitions |
| `EvalHarnessDef` | Reusable execution selection and execution defaults for a suite flow | Scoring, judge validation, report assembly |
| `EvalExecutionSpec` | Typed execution configuration for one harness or suite execution path | Arbitrary workflow engines or command execution |
| `ExecutionAdapter` | Invocation and normalization into `EvalState` | Orchestration, score calculation, gate decisions |
| `Evaluator` / `EvaluateRunner` | Existing dataset, target, scorer orchestration | Suite-specific execution semantics |

Cases remain serializable input data. `EvalState` remains serializable rollout output containing answer, completion, artifacts, trajectory, usage, timing, errors, raw response, and metadata. Runtime clients, runners, sandboxes, program objects, and other live handles may be used transiently by adapters but must not be stored in `EvalState`.

In-memory framework callers may still pass live AWorld agent/task objects through `EvalExecutionSpec.target_config` for compatibility with existing agent/task evaluation APIs. That path is not a declarative or JSON-serializable suite contract. Declared JSON manifests intentionally do not accept `execution`, `target_ref`, `task_builder_ref`, or live runtime handles; they only layer safe suite metadata and simple gate overrides on supported builtin suites.

## Decisions

### 1. Add `PROGRAM` execution as an extension of the current execution model

`EvalExecutionMode` should gain a `PROGRAM` mode that lets a suite execute an importable callable without pretending every evaluation target is an AWorld agent or task.

`PROGRAM` is for evaluation targets that do not use AWorld's agent or task runtime, such as a third-party API client, local library evaluator, or custom callable harness. It is not for customizing AWorld agent behavior, preprocessing case inputs, replacing judge/scorer logic, command execution, sandbox placement, or general workflow engines.

The callable reference must be an import string (`module:attribute` or `module.attribute`) that resolves to a callable. `EvalExecutionSpec` validation should reject `PROGRAM` specs without `target_ref` and reject unsupported command or workflow forms in this change. TASK builder references use the same importable-callable validation.

Program callables receive `(case, spec, target)` and may be sync or async. They must return one of:

1. an `EvalState`
2. a mapping matching `EvalState` fields, including optional `status`, `answer`, `completion`, `trajectory`, `tool_calls`, `usage`, `timing`, `error`, and `metadata`
3. a `TaskResponse`
4. a bare value, treated as the final answer with success status

If custom normalization is needed, the program should return a mapping with all relevant `EvalState` fields set explicitly and document the mapping in suite metadata. Exceptions from the program should propagate as execution failures rather than being silently converted into judge payloads.

The program-backed path should still compile into the same evaluator substrate:

- case definitions still provide the task-level inputs
- execution specs still describe runtime wiring
- execution output must still normalize into `EvalState`
- scorers and gate policies remain agnostic to how execution happened

`PROGRAM` is a framework extensibility mechanism, not a new CLI product mode.

Importable callable execution is a trusted in-process extension point. Importing a module can execute module top-level code, so `PROGRAM` and TASK builder refs must only be used for evaluator code controlled by the runner or workspace owner. This change does not sandbox imported code, provide an allowlist, sanitize third-party program payloads, or make untrusted suite manifests executable.

### 2. Add a lightweight harness boundary over execution specs

AWorld should not adopt verifiers' `Taskset` / `Harness` / `Env` object model, but it should make the missing execution reuse boundary explicit.

`EvalHarnessDef` should be a small framework-owned dataclass that can be attached to a suite or flow:

- `harness_id`: stable reusable identifier
- `execution`: `EvalExecutionSpec`
- `metadata`: optional serializable harness metadata

Suites may continue to set `execution` directly for v1 compatibility. At compile time, direct `suite.execution` lowers into an equivalent harness so the substrate has one execution boundary. Harnesses own execution defaults and adapter selection; suites still own cases, judges, scorers, and gates.

This is intentionally not a BYO harness plugin system and not equivalent to verifiers' rollout-owning harness. External package loading, lifecycle decorators, retry/fallback composition, multi-turn rollout ownership, and runtime handle borrowing are deferred.

### 3. Route execution through adapters instead of hardcoded runner calls

The follow-up should introduce an internal adapter boundary in `aworld/evaluations/`, for example an `ExecutionAdapter` protocol plus concrete adapters for:

- static/judge-only execution
- AWorld agent execution
- AWorld task execution
- program-backed execution

This keeps runner coupling local. If runner invocation details change later, the evaluator substrate should only need adapter updates instead of cross-cutting target rewrites.

Adapters are a hard internal boundary: they must not replace the current `EvalTarget -> Evaluator -> EvaluateRunner` orchestration skeleton. They only execute one case through the configured runtime and normalize the result into `EvalState`.

### 4. Make typed judge models the primary schema contract

Judge output validation should move from required-field checks toward typed models using Pydantic, which already exists across the codebase.

The primary suite contract should become:

- a typed judge-output model for validation and documentation
- JSON schema derivation from that model for report and tooling integration
- a compatibility bridge so current `JudgeSchemaDef(required_fields=...)` style suites continue to work during migration

This change is about stronger framework contracts, not about forcing every existing scorer to migrate in one pass.

Legacy required-field definitions should lower through the same `JudgeSchemaDef` validation and schema-export API used by typed models. They should not create a parallel scoring path.

Judge schema metadata should be surfaced once at the top level of the evaluator report, not copied into every case result. Per-case judge metadata should continue to include judge payload fields and backend id.

### 5. Use structured composite gate conditions instead of a string DSL

The follow-up should expand gate expressiveness, but it should avoid introducing a loose string expression DSL as the first step.

The preferred direction is a structured gate model such as:

- condition objects over named metrics and comparison operators
- explicit combinators like `all` / `any`
- optional approval-stage conditions separate from pass/fail conditions
- compatibility lowering from the current single-threshold policy into the new structure

Supported operators should include `>=`, `<=`, `>`, `<`, `==`, and `!=` from the first implementation so adding strict bounds or categorical metrics later does not require an API break.

This keeps gate logic inspectable, serializable, and consistent with AWorld's existing preference for explicit typed configuration objects.

Legacy threshold gates should lower into structured conditions at substrate boundaries. They should not keep a separate gate evaluation path.

### 6. Add suite-declared trajectory scoring

The substrate already preserves trajectory in `EvalState`, and existing scorer extractors can inspect it. This change should make trajectory evaluation explicit in suite definitions so final-result scoring and process scoring can be configured together.

`EvalSuiteDef` should gain a `trajectory_scorers` tuple of structured scorer definitions. The first implementation should lower these definitions into normal `EvalCriteria` entries for existing trajectory scorer classes, preserving the current scorer registry and report metric shapes.

Trajectory scorers evaluate `EvalState.trajectory` and related state fields produced by the current single-shot execution flow. They should not mutate state, replace the judge layer, introduce step-level reward semantics, run multi-turn user simulation, or introduce a separate report format.

### 7. Keep CLI changes additive and framework-driven

The official `aworld-cli evaluator` command should inherit these improvements through framework compilation and execution, not through CLI-owned evaluator semantics.

That means:

- no second evaluator stack inside `aworld-cli`
- no CLI-only gate language
- no CLI-only program execution abstraction

If follow-up CLI work becomes necessary later, it should be a separate product-focused change.

## Risks / Trade-offs

- [Program execution shape too generic] -> Mitigation: keep `PROGRAM` scoped to trusted importable callable refs plus normalized `EvalState` output, not arbitrary workflow engines or untrusted manifest execution.
- [Typed judge model migration friction] -> Mitigation: provide compatibility bridging from current `JudgeSchemaDef`; builtin typed-model migration may be staged after the substrate lands.
- [Composite gate policies become overdesigned] -> Mitigation: prefer structured operators and combinators over a general-purpose DSL.
- [Adapter layer duplicates existing target abstractions] -> Mitigation: keep adapters narrowly focused on execution invocation and normalization, not on replacing the orchestration skeleton.
- [Harness concept expands into a second framework] -> Mitigation: keep `EvalHarnessDef` as a lightweight typed holder for execution specs and defer lifecycle/composition/package features.

## Migration Plan

1. Add the harness, execution, and adapter abstractions behind compatibility paths so current suites still resolve.
2. Introduce typed judge models and bridge legacy schema definitions.
3. Expand gate evaluation logic while preserving current threshold-style gate definitions.
4. Add suite-declared trajectory scorer lowering.
5. Keep builtin suites compatible and exercise the richer substrate through focused tests; migrate builtin suites to typed models only when their public output contract is ready to change.
6. Keep CLI evaluator behavior stable while letting it consume the new framework-owned capabilities.

Rollback strategy:

- retain current `static` / `agent` / `task` suite behavior through compatibility lowering
- keep legacy threshold gate definitions and lightweight judge schemas valid until follow-up migrations are complete

## Deferred Questions

- Rich harness lifecycle hooks, retry/fallback composition, and child-state borrowing should wait for a later runtime-composition change.
- Command-backed or sandbox-backed program execution should wait for a dedicated execution-runtime change.
- Manifest exposure for every structured gate and trajectory scorer field may be staged after the core substrate supports the model.
