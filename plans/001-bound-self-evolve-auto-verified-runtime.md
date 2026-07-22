# Plan 001: Bound auto_verified self-evolve runtime

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in "STOP conditions" occurs, stop and report instead
> of improvising.
>
> **Drift check (run first)**:
> `git diff --stat 98d769f5..HEAD -- aworld/self_evolve aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py tests/self_evolve`
> If any in-scope code differs from the excerpts below, inspect the live code
> and update the implementation path before editing.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: perf
- **Planned at**: commit `98d769f5`, 2026-07-10
- **Issue**: https://github.com/inclusionAI/AWorld/issues/931

## Why this matters

`aworld-cli optimize --from-trajectory ... --apply auto_verified --judge-agent ... --judge-timeout 600` can take hours because verification is multiplicative and mostly serial. The current defaults replay baseline twice and candidate three times for `auto_verified`, may evaluate up to two candidates, and can run judge attempts with long per-attempt timeouts. This plan keeps verification trustworthy while adding early rejection, bounded concurrency, and explicit wall-clock/runtime budget controls.

## Current State

- `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py:8-11` defaults `auto_verified` to `judge_repetitions=1`, `baseline_replay_repetitions=2`, and `candidate_replay_repetitions=3`.
- `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py:275-339` applies those defaults and passes replay/judge settings into `optimize_from_cli_request`.
- `aworld/self_evolve/runner.py:503-535` evaluates candidate population sequentially. It reuses a successful baseline replay for later candidates, but the candidates themselves are still serialized.
- `aworld/self_evolve/runner.py:791-879` runs local candidate gates, then proceeds into budget/replay even if non-duplicate gates such as `noop_candidate`, `malformed_candidate`, `token_limit`, `protected_path`, `external_code_evolution`, or `skill_markdown` already failed.
- `aworld/self_evolve/replay.py:262-281` replays multi-case members sequentially.
- `aworld/self_evolve/replay.py:379-394` runs baseline/candidate repetitions sequentially.
- `aworld/self_evolve/replay.py:423-457` allows one evidence-quality retry per variant, so a bad evidence run can double replay wall-clock cost.
- `aworld/self_evolve/replay.py:542-572` invokes each replay through `python -m aworld_cli.main run ...` with the configured timeout.
- `aworld/self_evolve/evaluation.py:339-405` runs judge attempts sequentially; `max_attempts = judge_repetitions + judge_failure_retries`, and `judge_failure_retries` defaults to 2.
- `aworld/self_evolve/evaluation.py:503-533` evaluates baseline then candidate sequentially.
- `aworld/self_evolve/evaluation.py:461-463` only enforces `judge_timeout_seconds` when a custom injected evaluator is used; the default CLI evaluator path passes the timeout into the evaluator runner but does not wrap the outer `to_thread` call.

With one replayable case, two replay candidates, the current `auto_verified` defaults, `--judge-timeout 600`, and the default replay timeout of 600 seconds, worst-case wall time can be measured in hours: replay alone can require baseline x2 plus candidate x3 per candidate, doubled by evidence retries; judge evaluation can spend up to three 600-second attempts per evaluated variant.

## Commands You Will Need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `pytest tests/self_evolve/test_runner.py tests/self_evolve/test_cli_trajectory_case_script.py tests/self_evolve/test_optimizer_contract.py` | exit 0 |
| Replay/evaluation tests | `pytest tests/self_evolve/test_runner.py -k "replay or auto_verified or evaluator or population"` | exit 0 |
| Full self-evolve tests | `pytest tests/self_evolve` | exit 0 |

## Scope

**In scope**
- `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- `aworld/self_evolve/runner.py`
- `aworld/self_evolve/replay.py`
- `aworld/self_evolve/evaluation.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_cli_trajectory_case_script.py`
- new focused tests under `tests/self_evolve/` if needed

**Out of scope**
- Changing evaluator scoring semantics.
- Lowering verification quality by silently reducing explicit user-provided repetitions or timeouts.
- Mutating public skill content or `.aworld/self_evolve` artifacts as part of the implementation.

## Steps

### Step 1: Short-circuit candidates that fail local gates

In `SelfEvolveRunner._evaluate_iteration_candidate`, after `_candidate_gate_results(...)` and duplicate checks, return a rejected iteration immediately when any local preflight gate failed. Treat these gates as pre-replay blockers: `noop_candidate`, `malformed_candidate`, `token_limit`, `protected_path`, `external_code_evolution`, `skill_markdown`, and `trust_provenance`.

Preserve the existing duplicate-specific feedback behavior, but extract a small helper if needed so preflight and duplicate rejections produce the same report/state shape.

Add a test with a replay backend that raises if called, then pass a candidate that fails one preflight gate. The test should assert the candidate is rejected and replay/evaluation are not invoked.

**Verify**: `pytest tests/self_evolve/test_runner.py -k "preflight or noop or malformed or protected"` -> exit 0.

### Step 2: Add explicit runtime budget and concurrency knobs to the CLI

Add CLI flags and plumbing:
- `--wall-clock-budget` or `--max-optimize-seconds`: total best-effort budget for one optimize run.
- `--replay-concurrency`: bounded concurrency for independent replay subprocesses, default `1` to preserve current behavior.
- `--judge-concurrency`: bounded concurrency for independent evaluator calls, default `1` until the evaluator environment handling is concurrency-safe.
- `--judge-failure-retries`: expose the current implicit default of `2`, allowing users to choose `0` for faster exploratory runs.
- Optional: `--fast-verified` as a shorthand for lower non-explicit caps, for example replay candidate limit 1, judge failure retries 0, replay concurrency 2, and a smaller wall-clock budget. Do not override explicitly provided flags.

Propagate these options from `run_optimize_cli` into `optimize_from_cli_request`, `SelfEvolveRunner`, `AWorldCliCandidateReplayBackend`, and `AWorldTrajectoryEvaluatorBackend` as appropriate.

**Verify**: `pytest tests/self_evolve/test_cli_trajectory_case_script.py tests/self_evolve/test_runner.py -k "optimize_cli or auto_verified"` -> exit 0.

### Step 3: Parallelize replay safely

In `AWorldCliCandidateReplayBackend`, add bounded `asyncio.Semaphore` concurrency for independent replay work:
- Multi-case `_replay_member` calls can run concurrently because each member uses a distinct artifact directory.
- Repetitions inside `_run_repetitions` can run concurrently because each repetition has a distinct artifact directory.
- Keep baseline reuse behavior intact: when `baseline_replay_dir` is present, load it instead of running baseline.

Do not share mutable artifact paths between concurrent tasks. Aggregate results in deterministic index order so report output remains stable.

Add tests with a fake async executor that sleeps briefly and records overlapping execution. Assert concurrency reduces elapsed time or proves overlap without making the test flaky.

**Verify**: `pytest tests/self_evolve/test_runner.py -k "replay_concurrency or reuses_successful_baseline_replay"` -> exit 0.

### Step 4: Make evaluator timeout and concurrency explicit

Before enabling `judge_concurrency > 1`, remove or isolate global environment mutation in `AWorldTrajectoryEvaluatorBackend._self_evolve_runtime_log_env`. The current default path runs evaluator work in a thread while temporarily changing `os.environ`, which is not safe for concurrent evaluator calls.

Then:
- Wrap the default evaluator `to_thread` path in `asyncio.wait_for` when `judge_timeout_seconds` is set.
- Allow `judge_failure_retries=0` from CLI.
- Parallelize baseline and candidate evaluation only after the environment handling is safe; otherwise keep `judge_concurrency` defaulted and documented as `1`.

Add tests for timeout enforcement on the default evaluator path and for `judge_failure_retries=0` limiting attempts.

**Verify**: `pytest tests/self_evolve/test_runner.py -k "judge_timeout or judge_failure_retries or evaluator"` -> exit 0.

### Step 5: Surface the cost estimate before expensive work

Before replay starts, emit a progress event and report field that includes:
- estimated replay subprocess count
- estimated judge call count
- configured replay timeout, judge timeout, and failure retry count
- estimated worst-case wall-clock range under current concurrency

Use `estimate_replay_cost(...)` as the base, then add wall-clock fields. This makes long runs explain themselves before they start.

**Verify**: `pytest tests/self_evolve/test_runner.py -k "progress_events_for_long_optimize_phases or replay_cost"` -> exit 0.

## Done Criteria

- [ ] Invalid local-gate candidates do not start replay or judge.
- [ ] Users can cap wall-clock behavior without weakening explicit verification settings unexpectedly.
- [ ] Replay concurrency is opt-in, bounded, deterministic in reports, and artifact-safe.
- [ ] Judge timeout applies to the default evaluator path, not only injected test evaluators.
- [ ] `--judge-failure-retries 0` is supported and tested.
- [ ] The optimize summary/report includes enough replay/judge cost context to explain slow runs.
- [ ] Focused and full `tests/self_evolve` suites pass.

## STOP Conditions

Stop and report if:
- Parallel evaluator work requires unsafe global `os.environ` mutation and cannot be isolated without changing evaluator runtime APIs.
- A proposed default would reduce verification when the user explicitly provided repetitions, timeouts, or `auto_verified` safety settings.
- Replay artifact directories cannot be made unique per concurrent subprocess.
- The implementation needs to change public evaluator scoring semantics.

## Maintenance Notes

Reviewers should scrutinize concurrency for artifact path isolation, deterministic report ordering, and preservation of baseline replay reuse. The most valuable immediate win is Step 1; it is low risk and should land even if evaluator parallelization is deferred.
