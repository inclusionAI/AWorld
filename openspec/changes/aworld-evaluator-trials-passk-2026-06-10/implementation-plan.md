# AWorld Evaluator Trials and pass@k Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent evaluator trials with pass@k/pass^k metrics while keeping retry/fallback attempts distinct from trials.

**Architecture:** Add a small trial policy layer to the suite substrate. Expand cases into trial rows before evaluation, preserve original case metadata, then aggregate pass@k/pass^k from independent trial case results during report assembly.

**Tech Stack:** Python dataclasses, existing evaluator substrate/report/scorer infrastructure, pytest, OpenSpec.

---

## File Structure

- Modify: `aworld/evaluations/substrate.py`
  Add `TrialPolicyDef`, trial case expansion, trial aggregation, report metadata, and gate integration.
- Modify: `aworld/evaluations/report.py`
  Allow additive trial metadata fields if needed.
- Test: `tests/evaluations/test_evaluator_trials.py`
  Focused TDD coverage for trial policy, expansion, pass@k/pass^k, retry separation, and report shape.
- Test: existing evaluator regression tests
  Ensure one-trial behavior remains compatible.

## Task 1: Trial Policy

- [ ] **Step 1: Write failing trial policy tests**

Add tests in `tests/evaluations/test_evaluator_trials.py`:

```python
from aworld.evaluations.substrate import TrialPolicyDef


def test_trial_policy_rejects_invalid_k_values():
    with pytest.raises(ValueError, match="k values"):
        TrialPolicyDef(num_trials=2, pass_at_k=(3,)).validate()
```

- [ ] **Step 2: Run test and confirm failure**

Run: `pytest tests/evaluations/test_evaluator_trials.py::test_trial_policy_rejects_invalid_k_values -q`

Expected: FAIL because `TrialPolicyDef` does not exist.

- [ ] **Step 3: Implement `TrialPolicyDef`**

Add a frozen dataclass in `aworld/evaluations/substrate.py`:

```python
@dataclass(frozen=True)
class TrialPolicyDef:
    num_trials: int = 1
    pass_at_k: tuple[int, ...] = tuple()
    pass_caret_k: tuple[int, ...] = tuple()
    success_metric: str | None = None

    def validate(self) -> None:
        if self.num_trials < 1:
            raise ValueError("num_trials must be >= 1")
        invalid = [k for k in (*self.pass_at_k, *self.pass_caret_k) if k < 1 or k > self.num_trials]
        if invalid:
            raise ValueError("k values must be between 1 and num_trials")
```

- [ ] **Step 4: Run policy tests until green**

Run: `pytest tests/evaluations/test_evaluator_trials.py -q`

Expected: PASS for initial policy tests.

## Task 2: Trial Case Expansion

- [ ] **Step 1: Write failing expansion tests**

```python
def test_build_eval_dataset_expands_trial_cases():
    suite = EvalSuiteDef(
        suite_id="trial-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        trial_policy=TrialPolicyDef(num_trials=3),
    )
    compiled = compile_evaluation_flow(EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite))

    ids = [case.eval_case_id for case in compiled.dataset.eval_cases]
    assert ids == ["case-1::trial-1", "case-1::trial-2", "case-1::trial-3"]
    assert compiled.dataset.eval_cases[0].case_data["_trial"]["original_case_id"] == "case-1"
    assert compiled.dataset.eval_cases[0].case_data["_trial"]["trial_index"] == 1
```

- [ ] **Step 2: Run expansion test and confirm failure**

Run: `pytest tests/evaluations/test_evaluator_trials.py::test_build_eval_dataset_expands_trial_cases -q`

Expected: FAIL because `EvalSuiteDef` does not accept `trial_policy`.

- [ ] **Step 3: Implement trial expansion**

Add `trial_policy: TrialPolicyDef = field(default_factory=TrialPolicyDef)` to `EvalSuiteDef`. Update `compile_evaluation_flow()` to expand `flow.suite.cases` before `build_eval_dataset()`, preserving `_trial` metadata.

- [ ] **Step 4: Run expansion tests until green**

Run: `pytest tests/evaluations/test_evaluator_trials.py -q`

Expected: PASS.

## Task 3: pass@k/pass^k Aggregation

- [ ] **Step 1: Write failing aggregation tests**

Use a deterministic judge that passes trial 2 and fails trials 1/3. Assert `score_pass@2 == 1.0` and `score_pass^2 == 0.0`.

- [ ] **Step 2: Run aggregation test and confirm failure**

Run: `pytest tests/evaluations/test_evaluator_trials.py::test_run_evaluation_flow_reports_pass_at_k_and_pass_caret_k -q`

Expected: FAIL because trial aggregation does not exist.

- [ ] **Step 3: Implement trial aggregation**

In `run_evaluation_flow()`, group case results by `_trial.original_case_id`, derive each trial pass/fail from `TrialPolicyDef.success_metric` or gate primary metric, then add aggregate metrics named `<metric>_pass@k` and `<metric>_pass^k`.

- [ ] **Step 4: Run aggregation tests until green**

Run: `pytest tests/evaluations/test_evaluator_trials.py -q`

Expected: PASS.

## Task 4: Retry Separation

- [ ] **Step 1: Write failing retry/trial separation test**

Use a runtime harness wrapped in retry with `num_trials=2`. Assert report trial count is `2`, not the number of retry attempts, and pass@k counts terminal trial outcomes only.

- [ ] **Step 2: Run retry separation test and confirm failure**

Run: `pytest tests/evaluations/test_evaluator_trials.py::test_retry_attempts_do_not_count_as_trials -q`

Expected: FAIL until trial grouping ignores retry child attempts.

- [ ] **Step 3: Preserve retry attempts as artifacts only**

Ensure trial aggregation reads only top-level trial results and never inspects `artifacts.attempts` as independent outcomes.

- [ ] **Step 4: Run retry separation tests until green**

Run: `pytest tests/evaluations/test_evaluator_trials.py -q`

Expected: PASS.

## Task 5: Report Shape and Compatibility

- [ ] **Step 1: Write failing report compatibility tests**

Assert one-trial `app-evaluator` style suites keep current required report fields, while multi-trial reports include `trial_policy`, `trial_counts`, and per-result trial metadata.

- [ ] **Step 2: Run report tests and confirm failure**

Run: `pytest tests/evaluations/test_evaluator_trials.py::test_multi_trial_report_exposes_trial_metadata -q`

Expected: FAIL until report metadata is added.

- [ ] **Step 3: Add additive report fields**

Add report fields without changing existing required fields:

```python
report["trial_policy"] = {...}
report["trial_counts"] = {"original_cases": n, "trials_total": m}
```

- [ ] **Step 4: Run report tests until green**

Run: `pytest tests/evaluations/test_evaluator_trials.py tests/evaluations/test_evaluation_substrate.py -q`

Expected: PASS.

## Task 6: Verification and Commit

- [ ] **Step 1: Run evaluator regression suite**

Run:

```bash
pytest tests/evaluations/test_execution_state.py tests/evaluations/test_execution_adapters.py tests/evaluations/test_evaluation_substrate.py tests/evaluations/test_runtime_composition.py tests/evaluations/test_evaluator_trials.py tests/core/test_evaluator_runtime.py tests/core/test_evaluator_top_level_command.py tests/plugins/test_plugin_hooks.py tests/test_plugin_cli_entrypoint.py tests/docs/test_evaluator_report_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Validate OpenSpec**

Run: `openspec validate aworld-evaluator-trials-passk-2026-06-10 --strict`

Expected: `Change 'aworld-evaluator-trials-passk-2026-06-10' is valid`

- [ ] **Step 3: Commit**

```bash
git add aworld/evaluations/substrate.py aworld/evaluations/report.py tests/evaluations/test_evaluator_trials.py openspec/changes/aworld-evaluator-trials-passk-2026-06-10
git commit -m "feat: add evaluator trial pass metrics"
```
