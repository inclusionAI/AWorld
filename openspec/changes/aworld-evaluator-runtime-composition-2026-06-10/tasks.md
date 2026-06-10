## 1. Runtime Harness Model

- [ ] 1.1 Add a rollout-owning runtime harness definition separate from lightweight `EvalHarnessDef`.
- [ ] 1.2 Add a `RuntimeHarness` protocol or base class that executes one case and returns rollout state.
- [ ] 1.3 Preserve existing single-shot static/agent/task/program flows unchanged.

## 2. Rollout State

- [ ] 2.1 Add a serializable rollout state model with turns, messages, trajectory, tool calls, usage, timing, errors, metadata, and child/attempt state.
- [ ] 2.2 Bridge rollout state into existing `EvalState` so current scorer helpers and report summaries keep working.
- [ ] 2.3 Add tests proving rollout state does not store live runtime handles.

## 3. User Simulation

- [ ] 3.1 Add a deterministic user simulator contract.
- [ ] 3.2 Add a scripted simulator that reads turns from case input.
- [ ] 3.3 Add a single-prompt simulator for compatibility with current one-shot cases.

## 4. Step-Level Rewards

- [ ] 4.1 Add step reward records with metric name, step index, value, reason, and metadata.
- [ ] 4.2 Add rewarder interfaces that inspect rollout state without mutating it.
- [ ] 4.3 Aggregate step rewards into normal evaluator metrics and gate inputs.

## 5. Runtime Composition

- [ ] 5.1 Add one runtime wrapper style, preferably retry, around a base runtime harness.
- [ ] 5.2 Preserve child/attempt state for composed runs.
- [ ] 5.3 Add tests for retry/fallback state, terminal status, and report visibility.

## 6. Adoption Suite

- [ ] 6.1 Add one builtin or framework-registered adoption suite that uses the runtime-composition path.
- [ ] 6.2 The adoption suite uses typed judge schema, composite gate, trajectory scorer, step-level reward, and scripted simulator.
- [ ] 6.3 Keep `app-evaluator` behavior unchanged unless explicitly selected for migration later.

## 7. Verification

- [ ] 7.1 Add focused tests for harness rollout, user simulator, reward aggregation, runtime wrapper composition, and adoption suite execution.
- [ ] 7.2 Run the evaluator regression suite.
- [ ] 7.3 Validate this OpenSpec change with `openspec validate aworld-evaluator-runtime-composition-2026-06-10 --strict`.
