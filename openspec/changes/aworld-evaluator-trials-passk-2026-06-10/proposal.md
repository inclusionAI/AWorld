## Why

`aworld-evaluator-runtime-composition-2026-06-10` added rollout-owning harnesses, outcome/state-check grading, step rewards, retry wrappers, and an adoption suite. It deliberately kept retry/fallback separate from independent trial evaluation.

Complete agent evaluation still needs a first-class way to measure nondeterministic target behavior:

- run the same case multiple independent times
- preserve per-trial rollout state and metrics
- compute pass@k and pass^k without confusing retry attempts for trials
- report trial distributions without changing existing single-shot suite behavior

Without this layer, users can run a deterministic regression suite, but cannot answer "does this agent solve the task at least once in k attempts?" or "does it solve the task every time across k attempts?".

## What Changes

- Add suite-level trial configuration for independent repeated evaluation.
- Add trial-aware execution/report structures that retain per-trial case results.
- Add pass@k and pass^k aggregate metrics computed from independent trial outcomes.
- Keep retry/fallback attempts inside a trial and explicitly exclude them from pass@k/pass^k calculation.
- Add one opt-in adoption suite or test fixture proving trials work with runtime-composed suites and existing single-shot suites remain compatible.
- Defer clean-environment reset/sandbox orchestration to a dedicated environment-isolation change.

## Capabilities

### Modified Capabilities

- `evaluation-substrate`: add independent trial execution, trial reports, and pass@k/pass^k aggregation for suite-backed evaluation flows.

## Impact

- Affected code: `aworld/evaluations/**`, especially suite definitions, flow compilation, evaluator orchestration, report assembly, and runtime-composition integration.
- Affected APIs: additive trial configuration on suite-backed evaluator APIs; existing callers default to one trial.
- Affected tests: add focused coverage for trial expansion, pass@k/pass^k math, retry/trial separation, report shape, and compatibility.
- Affected docs: clarify trial semantics and how they differ from retry/fallback wrappers.
