# Self-Evolve Auto Verified Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CLI `optimize --apply auto_verified` fast enough for interactive use while preserving explicit stable-verification controls.

**Architecture:** Keep heavy verification available through explicit CLI flags, but change implicit defaults to one iteration, one replay per side, and one judge sample. Add runner-level guards so low-confidence inferred targets do not auto-apply, single-case replay avoids redundant held-out judging, evaluator artifacts are scoped by run id, and progress is logged at each expensive phase.

**Tech Stack:** Python 3.12, pytest, existing `aworld.self_evolve` runner/replay/evaluation modules, existing CLI command tests.

---

### Task 1: Cheap CLI Defaults

**Files:**
- Modify: `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- Test: `tests/core/test_optimize_top_level_command.py`

- [ ] Update `test_run_optimize_cli_uses_stable_auto_verified_defaults` to expect `judge_repetitions=1`, `baseline_replay_repetitions=1`, `candidate_replay_repetitions=1`, and `iterations=1`.
- [ ] Run `PYTHONPATH=aworld-cli/src:. pytest tests/core/test_optimize_top_level_command.py::test_run_optimize_cli_uses_stable_auto_verified_defaults -q` and confirm it fails.
- [ ] Change the four auto-verified default constants/default calls to the new values.
- [ ] Run the same test and confirm it passes.

### Task 2: Low-Confidence Target Inference Guard

**Files:**
- Modify: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] Add a test that patches `_infer_target_from_trace_packs` to return `confidence=0.85` with `low_confidence`, calls `optimize_from_cli_request(..., infer_target=True, apply_policy="auto_verified")`, and asserts it returns a rejected/no-target style summary without applying a target.
- [ ] Run that test and confirm it fails.
- [ ] Add a helper that blocks inferred auto-apply unless confidence is at least `0.9` and `low_confidence` is absent from signals.
- [ ] Persist the blocked result through the existing no-target path with a clear reason.
- [ ] Run the new test and related runner tests.

### Task 3: Skip Redundant Held-Out Judge For Stable Single-Case Replay

**Files:**
- Modify: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] Add a test using a recording evaluation backend where a single-case replay has baseline/candidate repetitions sufficient for `single_case_replay`, and assert evaluator calls are `baseline validation` and `candidate validation` only.
- [ ] Run the test and confirm it fails because `held_out` is called.
- [ ] Add a helper that detects stable single-case replay from replay metadata and reuses the candidate validation summary as the held-out summary with `dataset_split="single_case_replay"`.
- [ ] Run the new test plus existing single-case replay tests.

### Task 4: Scope Evaluator Artifacts By Run ID

**Files:**
- Modify: `aworld/self_evolve/evaluation.py`
- Modify: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_evaluation_backend.py`

- [ ] Add optional `artifact_namespace` to `EvaluationRequest`.
- [ ] Add a test that evaluates the same variant with namespaces `run-a` and `run-b`, asserting report paths are under separate directories.
- [ ] Run the test and confirm it fails.
- [ ] Pass `run_id` as the namespace from `SelfEvolveRunner` when calling evaluator backends.
- [ ] Include the namespace in `AWorldTrajectoryEvaluatorBackend` artifact path.
- [ ] Run evaluation backend and runner tests.

### Task 5: Phase Progress Logging

**Files:**
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/self_evolve/replay.py`
- Test: `tests/self_evolve/test_runner.py` or `tests/self_evolve/test_replay_overlay.py`

- [ ] Add tests with `caplog` or monkeypatched logger to assert replay/evaluation phase messages include run id, candidate id, phase, and repetition counts.
- [ ] Run tests and confirm they fail.
- [ ] Add `logger.info` calls before/after candidate proposal, replay, validation evaluation, single-case replay shortcut, held-out evaluation, and replay repetitions.
- [ ] Run focused tests and the self-evolve subset.
