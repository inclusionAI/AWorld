# AWorld Evaluator Environment Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add trusted per-rollout environment reset/cleanup lifecycle support for runtime-composed evaluator suites.

**Architecture:** Extend `runtime_composition.py` with serializable environment snapshots, a fixture protocol, and a wrapper harness. The wrapper resets before one base rollout, injects environment metadata into case/target context, cleans up afterward, and records lifecycle metadata in `RolloutState` without exposing live handles.

**Tech Stack:** Python dataclasses/protocols, existing runtime harness and trial substrate, pytest, OpenSpec.

---

## File Structure

- Modify: `aworld/evaluations/runtime_composition.py`
  Add environment snapshot, fixture protocol, wrapper harness, context injection, and cleanup semantics.
- Test: `tests/evaluations/test_environment_isolation.py`
  Focused TDD coverage for reset/cleanup, trial integration, retry composition, failure cleanup, and report metadata.
- Validate: `openspec/changes/aworld-evaluator-environment-isolation-2026-06-10`
  Keep tasks/spec/design aligned with implementation.

## Task 1: Environment Snapshot And Fixture

- [x] **Step 1: Write failing snapshot serialization test**

Create `tests/evaluations/test_environment_isolation.py`:

```python
from aworld.evaluations.runtime_composition import EnvironmentSnapshot


def test_environment_snapshot_excludes_live_handles():
    snapshot = EnvironmentSnapshot(
        environment_id="env-1",
        trial_id="case-1::trial-1",
        metadata={"workspace": "/tmp/demo", "client": object()},
    )

    assert snapshot.to_dict() == {
        "environment_id": "env-1",
        "trial_id": "case-1::trial-1",
        "metadata": {"workspace": "/tmp/demo"},
    }
```

- [x] **Step 2: Run test and confirm failure**

Run: `pytest tests/evaluations/test_environment_isolation.py::test_environment_snapshot_excludes_live_handles -q`

Expected: FAIL because `EnvironmentSnapshot` does not exist.

- [x] **Step 3: Implement `EnvironmentSnapshot`**

Add a frozen dataclass in `aworld/evaluations/runtime_composition.py` with `to_dict()` that uses `_serializable_dict()`.

- [x] **Step 4: Run snapshot test until green**

Run: `pytest tests/evaluations/test_environment_isolation.py::test_environment_snapshot_excludes_live_handles -q`

Expected: PASS.

## Task 2: Reset/Cleanup Wrapper

- [x] **Step 1: Write failing reset and cleanup test**

Add a test where a fixture records `reset` and `cleanup`, and the base harness asserts `_environment` exists in both case and target.

- [x] **Step 2: Run wrapper test and confirm failure**

Run: `pytest tests/evaluations/test_environment_isolation.py::test_environment_isolated_harness_resets_and_cleans_up -q`

Expected: FAIL because `EnvironmentIsolatedRuntimeHarness` does not exist.

- [x] **Step 3: Implement wrapper harness**

Add `EnvironmentFixture` protocol and `EnvironmentIsolatedRuntimeHarness`. Use `_maybe_await()` for sync/async fixture hooks. Inject snapshot dictionaries into copied case input/metadata and copied target.

- [x] **Step 4: Run wrapper tests until green**

Run: `pytest tests/evaluations/test_environment_isolation.py -q`

Expected: PASS for initial wrapper tests.

## Task 3: Trial And Retry Semantics

- [x] **Step 1: Write failing trial reset count test**

Use `EvalSuiteDef(trial_policy=TrialPolicyDef(num_trials=2))` with `EnvironmentIsolatedRuntimeHarness`. Assert two resets, two cleanups, and distinct trial ids.

- [x] **Step 2: Run test and confirm failure**

Run: `pytest tests/evaluations/test_environment_isolation.py::test_environment_isolation_resets_once_per_trial -q`

Expected: FAIL until wrapper metadata flows through expanded trial cases.

- [x] **Step 3: Write retry-inside-isolation test**

Compose `EnvironmentIsolatedRuntimeHarness(base_harness=RetryRuntimeHarness(...))`. Assert reset count equals trial count, not retry attempt count.

- [x] **Step 4: Run trial/retry tests until green**

Run: `pytest tests/evaluations/test_environment_isolation.py tests/evaluations/test_evaluator_trials.py -q`

Expected: PASS.

## Task 4: Failure Cleanup

- [x] **Step 1: Write failing cleanup-on-rollout-error test**

Create a base harness that raises after reset. Assert cleanup is attempted and the original rollout exception is raised.

- [x] **Step 2: Implement failure cleanup path**

Wrap base rollout execution in `try/except/finally`. Preserve original exception when cleanup also fails.

- [x] **Step 3: Write cleanup-failure-after-success test**

Create a cleanup hook that raises after a successful rollout. Assert the returned state has `status == "failed"` and environment cleanup error metadata.

- [x] **Step 4: Run failure tests until green**

Run: `pytest tests/evaluations/test_environment_isolation.py -q`

Expected: PASS.

## Task 5: Verification And Commit

- [x] **Step 1: Run evaluator regression suite**

Run:

```bash
pytest tests/evaluations/test_environment_isolation.py tests/evaluations/test_runtime_composition.py tests/evaluations/test_evaluator_trials.py tests/evaluations/test_evaluation_substrate.py tests/core/test_evaluator_runtime.py -q
```

Expected: PASS.

- [x] **Step 2: Validate OpenSpec**

Run: `openspec validate aworld-evaluator-environment-isolation-2026-06-10 --strict`

Expected: `Change 'aworld-evaluator-environment-isolation-2026-06-10' is valid`

- [x] **Step 3: Commit**

```bash
git add aworld/evaluations/runtime_composition.py tests/evaluations/test_environment_isolation.py
git add -f openspec/changes/aworld-evaluator-environment-isolation-2026-06-10
git commit -m "feat: add evaluator environment isolation"
```
