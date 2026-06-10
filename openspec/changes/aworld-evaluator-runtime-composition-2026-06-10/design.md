## Context

Evaluator v2 extensibility made the AWorld evaluator substrate more configurable, but it deliberately left verifiers-style runtime behavior out of scope. The current pipeline still compiles suites into the existing `EvalTarget -> Evaluator -> EvaluateRunner` skeleton and scores a single normalized state after execution. That is enough for post-hoc result and trajectory checks, but it cannot express:

- a harness that owns rollout lifecycle and produces state
- controlled multi-turn user simulation
- per-step reward/reason records
- outcome checks against final environment or artifact state
- retry/fallback/wrapper harness composition
- child-state borrowing or links between rollout attempts
- trial-based pass@k/pass^k metrics for nondeterministic agents
- an adoption suite that exercises these capabilities outside tests

This change introduces runtime composition as a framework-owned layer under `aworld/evaluations/` while preserving the v2 single-shot substrate. It also adds outcome/state-check grading because outcome verification is tightly coupled to rollout state and environment snapshots. Trial-based pass@k/pass^k execution is explicitly deferred because retry composition and independent trials have different semantics.

## Goals / Non-Goals

**Goals:**

- Add a rollout-owning harness contract that executes evaluation cases and returns normalized rollout state.
- Support multi-turn rollout state with turns, messages, tool calls, usage, timing, terminal outcome, child-state links, and step rewards.
- Add a user simulator abstraction that can drive controlled multi-turn interactions.
- Add outcome/state-check grader definitions that verify final environment or artifact state separately from final text answer and trajectory.
- Add step-level reward definitions and aggregation so process quality can participate in reports and gates.
- Add at least one runtime composition wrapper, such as retry or fallback, that composes around a base harness.
- Add one builtin/adoption suite that uses typed judge schema, composite gate, outcome grader, trajectory scorer, step reward, and rollout harness together.
- Add standard rollout metrics such as turn count, tool-call count, token usage, and timing/latency when the underlying runtime exposes them.
- Keep current static/agent/task/program single-shot flows compatible.

**Non-Goals:**

- Implementing a verifiers public API compatibility layer.
- Building a training optimizer, RL loop, or policy update system.
- Adding untrusted code execution, sandbox command execution, or package registry loading.
- Adding clean-environment isolation or sandbox reset semantics for each trial.
- Adding multi-trial execution, pass@k, pass^k, or trial-distribution metrics.
- Adding LLM-backed adaptive user simulation.
- Reworking `aworld-cli evaluator` UX or command syntax.
- Migrating every builtin suite in this change.
- Replacing `EvaluateRunner`; runtime composition should integrate with it through framework-owned targets/adapters.

## Ownership Model

| Concept | Owns | Must not own |
| --- | --- | --- |
| `EvalSuiteDef` / `EvalCaseDef` | Domain inputs, judge schema, gates, scorer declarations, runtime references | Live runtime handles in declarative manifests |
| `EvalRuntimeHarnessDef` | Rollout lifecycle configuration, simulator wiring, reward hooks, composition wrappers | Judge/scorer report assembly |
| `RuntimeHarness` | Executing one case through a rollout and returning rollout state | Gate policy decisions |
| `UserSimulator` | Producing user turns from case, rollout state, and previous assistant output | Agent execution internals |
| `OutcomeGrader` / `StateCheckGrader` | Checking final environment, artifact, or domain state | Driving rollout turns or replacing trajectory scoring |
| `StepRewarder` | Per-step reward values and reasons | Mutating rollout state or model behavior |
| `RolloutState` / `EvalState` bridge | Serializable rollout transcript and state normalization | Live clients, sandboxes, runners |

## Decisions

### 1. Add a rollout-owning harness layer

Introduce a framework-owned runtime harness abstraction separate from the lightweight `EvalHarnessDef` from v2 extensibility. The runtime harness owns the lifecycle of a rollout:

1. initialize state for one case
2. ask the user simulator or case input for the next user turn
3. execute the target runtime for one assistant/tool step
4. record messages, tool calls, observations, rewards, usage, and timing
5. decide whether the rollout is terminal
6. return a normalized rollout state

The first implementation should keep the public surface small:

- `EvalRuntimeHarnessDef`: immutable configuration object
- `RuntimeHarness`: protocol or base class for executing one case
- `run_rollout(case, target, harness) -> RolloutState`: internal framework entry point
- compatibility bridge from rollout state into existing `EvalState`

This is the first AWorld harness that owns rollout. The older `EvalHarnessDef` remains a compatibility holder for single-shot execution specs.

### 2. Model rollout state explicitly

Add a serializable rollout state model rather than overloading arbitrary trajectory dictionaries. It should include:

- `case_id`
- `status`
- `turns`: ordered user/assistant/tool records
- `messages`: normalized conversation messages when available
- `trajectory`: scorer-compatible trajectory view
- `tool_calls`
- `step_rewards`
- `outcome`: final answer plus optional environment/artifact snapshot references and state-check results
- `child_states` or `attempts` for composed runtimes
- `usage`
- `timing`
- `standard_metrics`: turn count, tool-call count, token counts, and latency/timing metrics derived from rollout state
- `error`
- `metadata`

The bridge into `EvalState` should preserve the existing state summary and scorer helpers. Existing trajectory scorers should work against the bridge without needing a report format fork.

### 3. Add outcome/state-check grading

Outcome evaluation must not be limited to terminal text. Add an outcome/state-check contract that can verify the final state produced by a rollout:

- file or artifact existence/content checks
- structured environment snapshot checks
- database or domain-state assertions when the harness provides a serializable snapshot
- test-command or sandbox checks only through a future trusted execution/sandbox change

The first implementation should keep state checks deterministic and in-process. A state-check grader receives the `RolloutState`, case, target, and optional serializable environment snapshot. It returns normal evaluator metric results plus structured details explaining which checks passed or failed.

Outcome graders are distinct from:

- typed judge output, which evaluates semantic result quality
- trajectory scorers, which evaluate process/transcript quality
- step rewarders, which evaluate individual rollout steps

Composite gates may reference all of these metric families side by side.

### 4. Add user simulator contracts

Add a user simulator interface that can be deterministic and testable:

- input: case, target, rollout state, last assistant output
- output: next user message, terminal signal, or simulator error

Built-in simulators should start small:

- scripted simulator over case-provided turns
- static single-prompt simulator for compatibility

LLM-backed simulators are a future extension. Scripted simulators are sufficient for this change's deterministic adoption suite, but they do not complete adaptive conversation-agent evaluation.

### 5. Add step-level rewards

Add reward records independent of final judge output:

- `metric_name`
- `step_index`
- `value`
- `weight`
- `partial_credit`
- `reason`
- `metadata`

Rewarders should be pure evaluators over rollout state or an individual step. They must not mutate state or call model execution. Aggregation should produce normal evaluator metrics, for example weighted mean reward, total reward, partial-credit rate, pass/fail threshold status, and report-level gate inputs.

### 6. Add runtime composition wrappers

Add one wrapper mechanism in this change so composition is real, not only a type hierarchy. The first wrapper should be retry or fallback:

- retry wrapper: reruns a base harness when terminal state is failed or a configured reward/gate condition is not met
- fallback wrapper: tries alternate harnesses when one fails

The wrapper must preserve child/attempt state so reports can explain which attempt passed or failed. The first implementation should support one wrapper style only if both would make the change too large.

Retry and fallback are not trials. Retry is an execution strategy that tries to produce one terminal rollout; trials are independent repeated evaluations used to estimate nondeterministic performance. This change must not label retry results as pass@k or pass^k.

### 7. Add suite purpose metadata and standard metrics

Suites should be able to describe whether they are intended for capability evaluation or regression evaluation. The first implementation can use suite metadata, for example:

- `evaluation_purpose`: `capability` or `regression`
- `expected_pass_rate`: optional descriptive threshold or range

Runtime-composed harnesses should derive standard transcript and latency metrics when data is available:

- `n_turns`
- `n_tool_calls`
- `n_tokens` or token usage fields
- wall-clock duration / time cost
- optional first-token or first-action latency when exposed by the runtime

Suites can still declare custom metrics, but these baseline metrics should not require every suite to hand-roll them.

### 8. Add one adoption suite

Add one builtin or framework-registered adoption suite that consumes the new runtime:

- typed judge schema
- composite gate
- outcome/state-check grader
- trajectory scorer
- step-level reward metric
- rollout-owning harness with scripted simulator
- suite metadata marking whether the suite is for capability or regression use

This suite can be narrow and deterministic. Its purpose is to prove that the substrate is active in production code paths, not only in isolated unit tests. It should not replace `app-evaluator` unless that public contract is ready to change.

### 9. Keep CLI additive

`aworld-cli evaluator` should discover and run the adoption suite through existing suite selection paths. Do not add CLI-only runtime syntax in this change. If CLI ergonomics are needed later, handle them in a product-focused change after the framework contract settles.

## Risks / Trade-offs

- [Scope growth] -> Mitigation: ship one simulator, one wrapper style, deterministic outcome checks, and one adoption suite; defer untrusted execution, LLM simulators, trials, and training loops.
- [Duplicate state models] -> Mitigation: rollout state must bridge into `EvalState` and reuse existing scorer/report helpers.
- [Hard-to-debug composed runs] -> Mitigation: preserve attempt/child state and reward reasons in serializable report metadata.
- [Adoption suite changes public behavior] -> Mitigation: add a new suite or opt-in registration rather than silently changing `app-evaluator`.
- [Runtime harness conflicts with existing adapter layer] -> Mitigation: keep adapters for single-shot execution; runtime harnesses own multi-turn rollout and may call adapters internally.
- [Retry metrics confused with pass@k] -> Mitigation: document retry as one composed rollout, not independent trials, and defer pass@k/pass^k to a separate multi-trial change.

## Migration Plan

1. Add rollout state and harness interfaces without changing existing suite behavior.
2. Add outcome/state-check grader contracts and deterministic state-check metrics.
3. Add scripted user simulator and reward records with weights and partial credit.
4. Add rollout target/adapter bridge into `EvalState`.
5. Add one runtime wrapper style with child-state reporting while keeping retry distinct from trials.
6. Add adoption suite that consumes typed schema, composite gate, outcome grader, trajectory scorer, rollout harness, and step rewards.
7. Keep existing evaluator regression suite green and add focused runtime-composition coverage.

Rollback strategy:

- runtime-composition suites are opt-in
- single-shot suite behavior and existing report fields remain compatible
- adoption suite can be unregistered or hidden without removing the underlying framework interfaces

## Deferred Questions

- LLM-backed user simulators should wait until deterministic scripted simulators are stable.
- Sandbox/command-backed harness execution should wait for a dedicated trusted execution change.
- Clean-environment reset semantics should wait for a sandbox/environment isolation change.
- Multi-trial execution, pass@k, and pass^k should be handled in a separate evaluator-trials change.
- Public API naming can be refined after the internal framework contract proves itself.
- Training reward integration should wait for a separate optimizer/training change.
