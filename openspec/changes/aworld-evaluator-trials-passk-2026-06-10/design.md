## Context

Runtime composition now gives AWorld a rollout-owning harness and serializable rollout state. That solves multi-turn execution and outcome inspection for one evaluation attempt. It does not solve nondeterminism measurement: a model or agent can fail one rollout and pass another under the same case. Agent evaluation needs independent repeated trials and distribution-level metrics.

This change adds trial execution above the existing suite/harness layer. A trial is one independent evaluation of one case. A retry attempt is not a trial; retry is an execution strategy inside one trial.

## Goals / Non-Goals

**Goals:**

- Add trial configuration with a default of one trial.
- Execute each case for `num_trials` independent trials.
- Preserve trial index, trial id, terminal status, metrics, and state summary in reports.
- Compute pass@k and pass^k from independent trial outcomes.
- Keep retry/fallback attempts nested inside a trial and excluded from trial metrics.
- Support both single-shot suites and runtime-composed suites.
- Keep existing evaluator behavior unchanged when no trial configuration is supplied.

**Non-Goals:**

- Adding sandbox reset, filesystem/database isolation, or clean-environment orchestration.
- Adding LLM-backed adaptive user simulators.
- Adding training-loop or optimizer integration.
- Redesigning `EvaluateRunner` public API beyond additive evaluator-substrate wiring.
- Treating retry/fallback attempts as trials.

## Decisions

### 1. Model trials as evaluator-level repetition, not harness retries

Add a `TrialPolicyDef` on `EvalSuiteDef`:

- `num_trials`: positive integer, default `1`
- `pass_at_k`: tuple of k values to report, default empty
- `pass_caret_k`: tuple of k values to report, default empty
- `success_metric`: metric used to decide whether a trial passed, default derived from the gate primary metric or `score`

The framework should normalize invalid values at compile time: `num_trials >= 1`, k values between `1` and `num_trials`, and `success_metric` must be a declared or gate-referenced metric.

### 2. Preserve one trial as current behavior

If `TrialPolicyDef.num_trials == 1`, report shape and existing aggregate metrics should remain compatible. Trial-specific fields may be absent or present as additive metadata, but no existing required field should change.

### 3. Expand cases without changing case identity

The evaluator should execute `case_id` repeatedly with trial metadata:

- stable original case id
- `trial_index`
- `trial_id`
- optional deterministic seed metadata

Reports should group results by original case id while still exposing individual trial case results. A practical first implementation can expand dataset case ids to `case_id::trial-N` and retain `original_case_id` in case metadata.

### 4. Compute pass@k and pass^k from trial outcomes

For each original case and metric:

- pass@k is true if any of the first k independent trials passed
- pass^k is true if all of the first k independent trials passed

Aggregate report metrics should include rates across original cases:

- `<metric_name>_pass@k`
- `<metric_name>_pass^k`

These values are report-level metrics and may be referenced by composite gates.

### 5. Keep retry/fallback inside each trial

If a runtime harness uses retry, the selected terminal attempt determines that trial's metric outcome. Child attempts stay in rollout artifacts/metadata for inspection, but pass@k/pass^k must count the trial once.

### 6. Defer environment isolation

Trials need independence, but this change does not create sandboxes. The implementation should provide metadata hooks for later environment reset integration and document that true clean-state independence requires the follow-up environment-isolation change.

## Risks / Trade-offs

- [Retry confused with trials] -> Mitigation: explicit report fields and tests that retry child attempts do not increase trial count.
- [Report bloat] -> Mitigation: per-trial state summaries stay per case result; full child states remain artifacts or references.
- [Existing repeat_times behavior collision] -> Mitigation: keep suite trial policy framework-owned and avoid relying on legacy `Evaluator.repeat_times` pass@k behavior unless it can preserve required report semantics.
- [False independence without sandbox] -> Mitigation: document clean-state reset as out of scope and preserve hooks for later isolation.

## Migration Plan

1. Add `TrialPolicyDef` and compile-time validation.
2. Expand suite cases into trial cases with metadata while preserving original case id.
3. Add trial-aware result grouping and pass@k/pass^k aggregation.
4. Add report fields for trial policy, trial counts, and trial metrics.
5. Add tests proving retry attempts do not count as trials.
6. Keep existing one-trial suites and `app-evaluator` behavior compatible.

## Deferred Questions

- Environment reset semantics should be handled in an `evaluator-environment-isolation` change.
- LLM-backed adaptive user simulators should stay in a simulator-focused change.
- Training/optimizer integration should wait until trial metrics and environment isolation are stable.
