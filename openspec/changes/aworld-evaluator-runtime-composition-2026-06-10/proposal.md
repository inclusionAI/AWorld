## Why

`aworld-evaluator-v2-extensibility-2026-06-09` hardened the single-shot evaluator substrate with execution adapters, typed judge schemas, composite gates, bounded `PROGRAM` execution, and suite-declared trajectory scorers. That change intentionally stopped short of verifiers-style runtime composition:

- `EvalHarnessDef` is a lightweight execution-spec holder, not a rollout-owning runtime object
- trajectory evaluation inspects the already captured single-shot `EvalState.trajectory`
- there is no user simulator, lifecycle hook model, child-state composition, retry/fallback harness composition, or step-level reward contract
- there is no explicit outcome/environment-state grader for verifying final external state
- there is no multi-trial execution model for pass@k or pass^k style nondeterminism metrics
- no builtin or adoption suite currently exercises typed judge + composite gate + trajectory scorer + rollout runtime together

The result is useful framework substrate, but not yet a complete runtime-composition evaluation capability. This change adds the missing rollout/runtime layer, adds outcome/state-check grading, and proves it through one concrete adoption suite. Multi-trial pass@k/pass^k execution remains a separate follow-up because it cuts across execution scheduling and statistical aggregation rather than harness retry behavior.

## What Changes

- Add a rollout-owning evaluator runtime harness abstraction that can execute multi-turn cases and produce normalized rollout state.
- Add multi-turn rollout state with turns, messages, tool calls, terminal outcome, step rewards, and child-state links.
- Add a user simulator contract for controlled multi-turn agent/user evaluation.
- Add an outcome/state-check grader contract for verifying final environment or artifact state separately from text answer and trajectory.
- Add step-level reward definitions and aggregation into report metrics and gates.
- Add runtime composition wrappers, starting with retry/fallback or equivalent wrapper harness semantics.
- Add one builtin/adoption suite that actually uses typed judge output, composite gates, trajectory scoring, and the new rollout-owning harness.
- Explicitly document that retry/fallback wrappers are not trials and must not be used as pass@k/pass^k metrics.
- Keep existing single-shot evaluator flows compatible and avoid changing the `aworld-cli evaluator` command shape.

## Capabilities

### Modified Capabilities

- `evaluation-substrate`: add rollout-owning runtime composition, multi-turn harness execution, user simulation, step-level reward scoring, and one adoption suite that consumes the v2 substrate capabilities end to end.

## Impact

- Affected code: `aworld/evaluations/**`, especially substrate definitions, execution/runtime orchestration, scorer integration, report assembly, and builtin suite registration.
- Affected APIs: framework-owned evaluator APIs gain additive runtime-composition contracts; existing suite-backed and legacy evaluation callers remain valid.
- Affected tests: add focused coverage for harness rollout, user simulation, reward aggregation, runtime wrappers, report/gate integration, and adoption suite behavior.
- Affected docs: clarify the difference between single-shot evaluation and rollout-owning runtime composition.
