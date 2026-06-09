## Why

`aworld-evaluation-substrate-2026-06-01` established the first execution-backed evaluator substrate and completed the v1 CLI flow, but it intentionally stopped short of several extensibility and contract-hardening steps:

- suite execution modes are still limited to `static`, `agent`, and `task`
- execution targets still couple directly to current runner entrypoints
- judge schemas are still lightweight required-field checks
- gate policies still only express single-metric threshold decisions

Those tradeoffs were acceptable for v1, but they limit AWorld's ability to expose evaluation as a broader framework capability for non-agent programs, stricter automation, and richer reusable evaluator definitions.

This change is an incremental hardening of the v1 single-shot evaluator substrate. It is not intended to claim verifiers v1 parity: multi-turn rollout ownership, user simulators, lifecycle hooks, child-state composition, and training reward semantics remain out of scope for a later runtime-composition change.

## What Changes

- Add a lightweight first-class `EvalHarnessDef` so suites have an explicit execution boundary in the suite/case/harness/state hierarchy without adopting a full rollout-owning harness object model.
- Extend the framework-owned evaluation substrate with a bounded `PROGRAM` execution mode for importable program-backed evaluators that do not use AWorld's agent/task runtime.
- Add an internal execution adapter layer under `aworld/evaluations/` so suite-backed evaluation no longer hardcodes runner invocation details into eval targets.
- Promote judge output contracts from required-field-only validation to typed model validation with JSON-schema-friendly structure and a compatibility bridge for existing suites.
- Expand gate policies from single-threshold checks into structured composite conditions with explicit comparison operators while preserving the current simple threshold shape as compatibility sugar.
- Add suite-declared trajectory scorers so result evaluation and normalized trajectory/process metric evaluation can be configured side by side in the current single-shot flow.
- Keep `aworld-cli evaluator` compatible as an assembly layer on top of the evolved framework substrate rather than introducing a second evaluator stack.

## Capabilities

### Modified Capabilities

- `evaluation-substrate`: add adapter-backed program execution, typed judge schemas, and richer composite gate policies without breaking the v1 evaluator substrate shape.

## Impact

- Affected code: `aworld/evaluations/**`, especially execution specification, substrate compilation, eval target execution, judge validation, and gate evaluation paths.
- Affected APIs: internal evaluation composition APIs gain additive extensions; existing suite-backed and legacy evaluation callers remain valid through compatibility paths.
- Affected systems: framework-owned evaluator execution and scoring; `aworld-cli evaluator` should inherit the new framework capabilities without owning their semantics.
