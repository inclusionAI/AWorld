# AWorld Evaluator LLM User Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add provider-neutral adaptive user simulation for runtime-composed evaluator rollouts.

**Architecture:** Extend `CallableRuntimeHarness` to await simulator outputs, then add `LLMUserSimulator` as a thin adapter around an injected generator callable. The simulator normalizes string/mapping/turn/stop outputs into `RolloutTurn | None` and leaves provider clients outside serializable rollout state.

**Tech Stack:** Python protocols/dataclasses, existing runtime-composition harness, pytest, OpenSpec.

---

## File Structure

- Modify: `aworld/evaluations/runtime_composition.py`
  Add async simulator support and `LLMUserSimulator`.
- Test: `tests/evaluations/test_llm_user_simulator.py`
  Focused TDD coverage for adaptive generation and stop behavior.

## Task 1: Async Simulator Support

- [x] **Step 1: Write failing async simulator test**

Create `tests/evaluations/test_llm_user_simulator.py` with an async simulator whose `next_turn` returns a `RolloutTurn`.

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/evaluations/test_llm_user_simulator.py::test_callable_runtime_harness_awaits_async_simulator -q`

Expected: FAIL because `CallableRuntimeHarness` does not await simulator output.

- [x] **Step 3: Await simulator next turn**

Change `CallableRuntimeHarness.run_rollout()` to call `await _maybe_await(self.simulator.next_turn(...))`.

- [x] **Step 4: Run test until green**

Run: `pytest tests/evaluations/test_llm_user_simulator.py -q`

Expected: PASS.

## Task 2: LLMUserSimulator

- [x] **Step 1: Write failing adaptive generation tests**

Add tests for string output, mapping output with metadata, explicit stop output, and generator context arguments.

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/evaluations/test_llm_user_simulator.py -q`

Expected: FAIL because `LLMUserSimulator` does not exist.

- [x] **Step 3: Implement `LLMUserSimulator`**

Add a class accepting `turn_generator`. Normalize outputs:

- `None` -> `None`
- `{"stop": True}` -> `None`
- `str` -> `RolloutTurn(role="user", content=value)`
- `RolloutTurn` -> returned directly
- mapping -> `RolloutTurn(role=..., content=..., metadata=...)`

- [x] **Step 4: Run simulator tests until green**

Run: `pytest tests/evaluations/test_llm_user_simulator.py -q`

Expected: PASS.

## Task 3: Verification And Commit

- [x] **Step 1: Run runtime/evaluator regression**

Run:

```bash
pytest tests/evaluations/test_llm_user_simulator.py tests/evaluations/test_runtime_composition.py tests/evaluations/test_environment_isolation.py tests/evaluations/test_evaluator_trials.py -q
```

Expected: PASS.

- [x] **Step 2: Validate OpenSpec**

Run: `openspec validate aworld-evaluator-llm-user-simulator-2026-06-10 --strict`

Expected: `Change 'aworld-evaluator-llm-user-simulator-2026-06-10' is valid`

- [x] **Step 3: Commit**

```bash
git add aworld/evaluations/runtime_composition.py tests/evaluations/test_llm_user_simulator.py
git add -f openspec/changes/aworld-evaluator-llm-user-simulator-2026-06-10
git commit -m "feat: add adaptive evaluator user simulator"
```
