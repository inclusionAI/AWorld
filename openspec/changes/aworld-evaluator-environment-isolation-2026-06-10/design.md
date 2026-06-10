## Context

The evaluator roadmap now has three layers in place:

- rollout-owning runtime harnesses with serializable `RolloutState`
- deterministic outcome/state-check graders
- independent trials with pass@k/pass^k aggregation

The remaining correctness gap is trial independence. Re-running a case without resetting environment state can inflate pass rates or hide regressions. This change adds the environment lifecycle boundary that lets a suite declare reset semantics without embedding live handles in suite state or report state.

## Goals / Non-Goals

**Goals:**

- Add a trusted environment fixture protocol for reset and cleanup.
- Provide a runtime-harness wrapper that applies the fixture around each rollout.
- Ensure each expanded trial receives its own environment metadata.
- Record environment reset/cleanup metadata in serializable rollout/report state.
- Clean up after failed rollouts or raised exceptions where possible.
- Keep retry attempts inside a single environment reset unless the suite explicitly wraps retry differently.

**Non-Goals:**

- Running shell commands, test commands, or arbitrary workflow engines.
- Providing a production sandbox/container implementation.
- Managing external databases or filesystem snapshots directly.
- Supporting untrusted suite manifests for environment fixture references.
- Adding LLM-backed adaptive user simulators or training/optimizer integration.

## Decisions

### 1. Add a trusted fixture lifecycle

Define a small in-process contract:

- `reset(case, target) -> EnvironmentSnapshot`
- `cleanup(snapshot, case, target, state) -> EnvironmentSnapshot | None`

The fixture is trusted Python code supplied by the suite author, not a declared JSON manifest capability. Returned metadata must be serializable. Live clients, file handles, subprocesses, and credentials must not be retained in rollout state.

### 2. Represent reset output as serializable environment snapshot

Add `EnvironmentSnapshot` with:

- `environment_id`
- `trial_id`
- `metadata`

The snapshot is injected into:

- `case.input["_environment"]`
- `case.metadata["_environment"]`
- `target["_environment"]`
- `RolloutState.metadata["environment"]`

This lets the base harness find a workspace id, database schema id, or seed without coupling to a concrete sandbox implementation.

### 3. Use wrapper composition instead of changing every harness

Add `EnvironmentIsolatedRuntimeHarness(base_harness, fixture)`. The wrapper owns reset and cleanup around exactly one call to `base_harness.run_rollout`.

For multi-trial suites, case expansion already creates one case row per trial, so the wrapper naturally runs one reset per trial. For retry suites, the recommended composition is:

- `EnvironmentIsolatedRuntimeHarness(RetryRuntimeHarness(base))`: one environment per trial, retry attempts share that trial environment.

If a suite intentionally needs one environment per retry attempt, it can wrap in the opposite order:

- `RetryRuntimeHarness(EnvironmentIsolatedRuntimeHarness(base))`

### 4. Fail closed on lifecycle errors

If reset fails, the rollout should not run. If cleanup fails after a successful rollout, the terminal state should record cleanup failure metadata and mark the state failed only if the fixture declares cleanup failure as fatal. The first implementation keeps cleanup failure fatal by default to avoid silently reporting contaminated environments as clean.

If the base harness raises, the wrapper must still attempt cleanup and then re-raise the original error unless cleanup failure is the only error.

## Risks / Trade-offs

- [False sandbox confidence] -> Mitigation: name the feature environment fixture lifecycle, not production sandboxing, and document sandbox adapters as future work.
- [Live handle leakage] -> Mitigation: serialize snapshots through existing serializable filtering before storing state.
- [Retry/trial confusion] -> Mitigation: document wrapper order and add tests proving one reset per trial when retry is inside isolation.
- [Cleanup masking rollout errors] -> Mitigation: preserve original rollout exception when both rollout and cleanup fail.

## Migration Plan

1. Add environment snapshot and fixture protocol primitives.
2. Add environment-isolated runtime harness wrapper.
3. Inject serializable environment metadata into case, target, and rollout state.
4. Add trial integration tests proving one reset per trial.
5. Add failure-path tests for cleanup on raised rollout.
6. Keep existing suites unchanged unless they opt into the wrapper.

## Deferred Questions

- Concrete filesystem/database/container adapters should be handled in a later environment-adapter change.
- LLM-backed adaptive user simulation remains a simulator-focused follow-up.
- Training/optimizer integration should wait until environment isolation and trial metrics stabilize.
