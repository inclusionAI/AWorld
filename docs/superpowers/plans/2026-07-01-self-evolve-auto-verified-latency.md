# Self-Evolve Auto Verified Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CLI `optimize --apply auto_verified` complete a full self-evolve verification path while avoiding multi-hour runaway behavior.

**Architecture:** Keep the verified path complete by default: one iteration, one judge sample, and enough replay repetitions for single-case replay confidence (`baseline=2`, `candidate=3`). Low-confidence inferred targets still run the full `auto_verified` process when requested; they are not silently downgraded to proposal. Existing skill edits and newly generated skills are both treated as draft skill candidates in overlay, then promoted only after replay, evaluation, and post-apply verification pass. Single-case replay avoids redundant held-out judging, evaluator artifacts are scoped by run id, and progress is logged at each expensive phase.

**Tech Stack:** Python 3.12, pytest, existing `aworld.self_evolve` runner/replay/evaluation modules, existing CLI command tests.

---

### Task 1: Cheap CLI Defaults

**Files:**
- Modify: `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- Test: `tests/core/test_optimize_top_level_command.py`

- [ ] Update `test_run_optimize_cli_uses_stable_auto_verified_defaults` to expect `judge_repetitions=1`, `baseline_replay_repetitions=2`, `candidate_replay_repetitions=3`, and `iterations=1`.
- [ ] Run `PYTHONPATH=aworld-cli/src:. pytest tests/core/test_optimize_top_level_command.py::test_run_optimize_cli_uses_stable_auto_verified_defaults -q` and confirm it fails.
- [ ] Change the four auto-verified default constants/default calls to the new values.
- [ ] Run the same test and confirm it passes.

### Task 2: Low-Confidence Target Inference Does Not Short-Circuit Auto Verification

**Files:**
- Modify: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] Add a test that patches `_infer_target_from_trace_packs` to return `confidence=0.85` with `low_confidence`, calls `optimize_from_cli_request(..., infer_target=True, apply_policy="auto_verified")`, and asserts it still runs replay, evaluation, and post-apply verification when those gates pass.
- [ ] Run that test and confirm it fails.
- [ ] Remove the low-confidence downgrade path so `auto_verified` means full verification even for inferred targets.
- [ ] Preserve the target selection signals in the report so operators can see low-confidence provenance.
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

### Task 6: New Skill Draft Targets

**Files:**
- Modify: `aworld/self_evolve/targets.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/self_evolve/credit_assignment.py`
- Test: `tests/self_evolve/test_runner.py`
- Test: `tests/self_evolve/test_credit_assignment.py`

- [ ] Add a runner test proving an inferred missing skill path is evaluated in overlay and written as `release_state: verified` only after replay, evaluation, and post-apply pass.
- [ ] Add a credit assignment test proving podcast/web grounding failures generate `skill:web-content-grounding` instead of modifying `agent-browser` just because the trajectory used that tool.
- [ ] Implement a draft skill target with skeleton current content, diff/proposal support, auto-apply create semantics, and rollback delete semantics.
- [ ] Make `_target_from_ref` path-aware so missing inferred skill paths become draft targets while explicit `skill:<id>` lookup still requires an existing skill unless a path is supplied.
- [ ] Add a draft-skill inference path before generic skill alias fallback for reusable web evidence grounding gaps.
- [ ] Run the real `task_20260609193335.log` through target inference and confirm it selects `skill:web-content-grounding`.
