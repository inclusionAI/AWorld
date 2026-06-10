## 1. Runtime Harness Model

- [x] 1.1 Add a rollout-owning runtime harness definition separate from lightweight `EvalHarnessDef`.
- [x] 1.2 Add a `RuntimeHarness` protocol or base class that executes one case and returns rollout state.
- [x] 1.3 Preserve existing single-shot static/agent/task/program flows unchanged.

## 2. Rollout State

- [x] 2.1 Add a serializable rollout state model with turns, messages, trajectory, tool calls, usage, timing, errors, metadata, and child/attempt state.
- [x] 2.2 Bridge rollout state into existing `EvalState` so current scorer helpers and report summaries keep working.
- [x] 2.3 Include outcome data and optional serializable environment/artifact snapshots in rollout state.
- [x] 2.4 Derive standard rollout metrics such as turn count, tool-call count, token usage, and duration.
- [x] 2.5 Add tests proving rollout state does not store live runtime handles.

## 3. Outcome / State-Check Grading

- [x] 3.1 Add deterministic outcome/state-check grader definitions.
- [x] 3.2 Emit outcome metrics separately from judge, trajectory, and reward metrics.
- [x] 3.3 Allow composite gates to reference outcome metrics.
- [x] 3.4 Explicitly reject state checks that require sandbox reset, command execution, or clean-environment isolation in this change.

## 4. User Simulation

- [x] 4.1 Add a deterministic user simulator contract.
- [x] 4.2 Add a scripted simulator that reads turns from case input.
- [x] 4.3 Add a single-prompt simulator for compatibility with current one-shot cases.
- [x] 4.4 Document that LLM-backed adaptive user simulation is deferred.

## 5. Step-Level Rewards

- [x] 5.1 Add step reward records with metric name, step index, value, weight, partial-credit marker, reason, and metadata.
- [x] 5.2 Add rewarder interfaces that inspect rollout state without mutating it.
- [x] 5.3 Aggregate step rewards into normal evaluator metrics and gate inputs, including weighted and partial-credit summaries.

## 6. Runtime Composition

- [x] 6.1 Add one runtime wrapper style, preferably retry, around a base runtime harness.
- [x] 6.2 Preserve child/attempt state for composed runs.
- [x] 6.3 Add tests for retry/fallback state, terminal status, and report visibility.
- [x] 6.4 Document and test that retry/fallback attempts are not independent trials and do not produce pass@k/pass^k metrics.

## 7. Adoption Suite

- [x] 7.1 Add one builtin or framework-registered adoption suite that uses the runtime-composition path.
- [x] 7.2 The adoption suite uses typed judge schema, composite gate, outcome/state-check grader, trajectory scorer, step-level reward, and scripted simulator.
- [x] 7.3 Mark the adoption suite with capability/regression purpose metadata.
- [x] 7.4 Keep `app-evaluator` behavior unchanged unless explicitly selected for migration later.

## 8. Verification

- [x] 8.1 Add focused tests for harness rollout, outcome grading, user simulator, reward aggregation, runtime wrapper composition, standard metrics, and adoption suite execution.
- [x] 8.2 Run the evaluator regression suite.
- [x] 8.3 Validate this OpenSpec change with `openspec validate aworld-evaluator-runtime-composition-2026-06-10 --strict`.
