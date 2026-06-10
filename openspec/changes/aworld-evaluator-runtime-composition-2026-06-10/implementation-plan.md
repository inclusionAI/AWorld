# AWorld Evaluator Runtime Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rollout-owning evaluator runtime composition with multi-turn harnesses, outcome/state-check grading, user simulation, step-level rewards, and one adoption suite that actively consumes v2 evaluator capabilities.

**Architecture:** Keep the current single-shot evaluator substrate intact. Add a runtime-composition layer under `aworld/evaluations/` that can execute multi-turn rollouts, normalize them into `EvalState`, evaluate final outcome snapshots, aggregate weighted reward metrics, derive standard rollout metrics, and compose retry attempts while preserving child state. Retry remains an execution wrapper, not pass@k/pass^k trial evaluation.

**Tech Stack:** Python dataclasses/protocols, AWorld evaluator substrate, existing scorer/report infrastructure, Pydantic for typed judge outputs, pytest, OpenSpec.

---

## File Structure

- Create: `aworld/evaluations/runtime_composition.py`
  Rollout state, turn records, outcome check records, user simulator protocols, runtime harness protocols, reward records, and retry wrapper primitives.
- Modify: `aworld/evaluations/substrate.py`
  Compile opt-in runtime-composition suites and register the adoption suite.
- Modify: `aworld/evaluations/execution.py`
  Add rollout-state-to-`EvalState` normalization helpers if they do not fit cleanly in `runtime_composition.py`.
- Modify: `aworld/evaluations/report.py`
  Preserve attempt/reward/outcome metadata in existing report shape without breaking schema.
- Modify: `aworld/evaluations/scorers/**`
  Add outcome and reward aggregation scorers or reuse existing scorer infrastructure for those metrics.
- Test: `tests/evaluations/test_runtime_composition.py`
  Focused tests for rollout state, outcome grading, simulator, harness, retry wrapper, reward aggregation, standard metrics, and adoption suite.
- Test: existing evaluator regression tests
  Ensure single-shot behavior remains compatible.

## Task 1: Rollout State and Harness Contracts

- [x] **Step 1: Write failing rollout state tests**

Add tests in `tests/evaluations/test_runtime_composition.py` for:

```python
def test_rollout_state_to_eval_state_excludes_live_handles():
    live_agent = object()
    state = RolloutState(
        case_id="case-1",
        status="success",
        answer="done",
        turns=[RolloutTurn(role="user", content="hello")],
        outcome={"artifact_exists": True},
        metadata={"live_agent": live_agent, "safe": "ok"},
    )

    eval_state = state.to_eval_state(target={"target_kind": "inline"})

    assert eval_state.case_id == "case-1"
    assert eval_state.answer == "done"
    assert eval_state.trajectory
    assert eval_state.artifacts["outcome"]["artifact_exists"] is True
    assert "live_agent" not in eval_state.metadata
    assert eval_state.metadata["safe"] == "ok"
```

- [x] **Step 2: Run test and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_rollout_state_to_eval_state_excludes_live_handles -q`

Expected: FAIL because `runtime_composition.py` and `RolloutState` do not exist.

- [x] **Step 3: Add minimal rollout models**

Create `aworld/evaluations/runtime_composition.py` with serializable dataclasses for `RolloutTurn`, `OutcomeCheckResult`, `StepReward`, `RolloutState`, `EvalRuntimeHarnessDef`, `RuntimeHarness`, and `UserSimulator`.

- [x] **Step 4: Run rollout tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py -q`

Expected: PASS for initial rollout state tests.

## Task 2: Outcome / State-Check Grading

- [x] **Step 1: Write failing outcome grader tests**

Cover deterministic final-state checks:

```python
def test_state_check_grader_emits_outcome_metric():
    state = RolloutState(
        case_id="case-1",
        status="success",
        outcome={"ticket": {"status": "resolved"}},
    )
    grader = StateCheckGrader(
        metric_name="ticket_resolved",
        path=("ticket", "status"),
        expected="resolved",
    )

    result = grader.grade(state=state, case=None, target={})

    assert result.metric_name == "ticket_resolved"
    assert result.value == 1.0
    assert result.passed is True
```

- [x] **Step 2: Run outcome tests and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_state_check_grader_emits_outcome_metric -q`

Expected: FAIL because `StateCheckGrader` does not exist.

- [x] **Step 3: Implement deterministic state-check grader**

Add an in-process state-check grader that reads serializable rollout `outcome` data and emits normal metric-compatible results. Reject checks that require command execution, sandbox reset, or non-serializable live handles.

- [x] **Step 4: Run outcome tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py -q`

Expected: PASS.

## Task 3: Scripted User Simulator

- [x] **Step 1: Write failing simulator tests**

Cover scripted turns and single-prompt behavior:

```python
def test_scripted_user_simulator_emits_turns_in_order():
    simulator = ScriptedUserSimulator()
    state = RolloutState(case_id="case-1")
    case = EvalCaseDef(case_id="case-1", input={"turns": ["hi", "again"]})

    first = simulator.next_turn(case=case, target={}, state=state, last_output=None)
    state.turns.append(first)
    second = simulator.next_turn(case=case, target={}, state=state, last_output="ok")

    assert first.content == "hi"
    assert second.content == "again"
```

- [x] **Step 2: Run simulator tests and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_scripted_user_simulator_emits_turns_in_order -q`

Expected: FAIL because simulator implementation does not exist.

- [x] **Step 3: Implement scripted and single-prompt simulators**

Add `ScriptedUserSimulator` and `SinglePromptUserSimulator` to `runtime_composition.py`.

- [x] **Step 4: Run simulator tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py -q`

Expected: PASS.

## Task 4: Runtime Harness Execution

- [x] **Step 1: Write failing harness execution tests**

Add a deterministic harness test that consumes simulator turns and returns rollout state with assistant turns and trajectory.

- [x] **Step 2: Run harness test and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_runtime_harness_executes_multi_turn_rollout -q`

Expected: FAIL because harness implementation does not exist.

- [x] **Step 3: Implement a minimal scripted runtime harness**

Add a framework test harness or deterministic harness class that uses a simulator and a callable assistant step function.

- [x] **Step 4: Run harness tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py -q`

Expected: PASS.

## Task 5: Step Rewards and Aggregation

- [x] **Step 1: Write failing reward aggregation tests**

Cover reward records becoming weighted and partial-credit case/aggregate metrics.

- [x] **Step 2: Run reward tests and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_step_rewards_aggregate_into_metrics -q`

Expected: FAIL because reward aggregation is not wired.

- [x] **Step 3: Implement step reward records and aggregation scorer**

Use existing scorer/report metric shapes. Keep reward metrics distinct from judge and outcome metrics.

- [x] **Step 4: Run reward tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py -q`

Expected: PASS.

## Task 6: Retry Wrapper Composition

- [x] **Step 1: Write failing retry wrapper tests**

Cover failed first attempt, successful second attempt, preserved child/attempt state, and explicit absence of pass@k/pass^k labels.

- [x] **Step 2: Run retry tests and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_retry_wrapper_preserves_failed_attempts -q`

Expected: FAIL because retry wrapper does not exist.

- [x] **Step 3: Implement retry wrapper**

Add a retry wrapper around a base `RuntimeHarness` with max attempts and selected terminal attempt. Preserve attempts as child state and do not emit trial metrics.

- [x] **Step 4: Run retry tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py -q`

Expected: PASS.

## Task 7: Standard Metrics and Suite Purpose

- [x] **Step 1: Write failing standard metric tests**

Cover `n_turns`, `n_tool_calls`, token usage, and duration derivation from rollout state.

- [x] **Step 2: Run standard metric tests and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_rollout_standard_metrics_are_derived -q`

Expected: FAIL because standard metric derivation does not exist.

- [x] **Step 3: Implement standard metric derivation and purpose metadata preservation**

Add rollout standard metrics and preserve suite metadata such as `evaluation_purpose="capability"` or `evaluation_purpose="regression"` in report context.

- [x] **Step 4: Run standard metric tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py -q`

Expected: PASS.

## Task 8: Adoption Suite

- [x] **Step 1: Write failing adoption suite tests**

Assert the new suite is registered, uses typed judge schema, composite gate, outcome/state-check grader, trajectory scorer, step reward metric, scripted simulator, purpose metadata, and runtime harness.

- [x] **Step 2: Run adoption tests and confirm failure**

Run: `pytest tests/evaluations/test_runtime_composition.py::test_runtime_composition_adoption_suite_runs_end_to_end -q`

Expected: FAIL because suite does not exist.

- [x] **Step 3: Implement opt-in adoption suite**

Add a narrow deterministic suite without changing `app-evaluator` behavior.

- [x] **Step 4: Run adoption tests until green**

Run: `pytest tests/evaluations/test_runtime_composition.py tests/evaluations/test_evaluation_substrate.py -q`

Expected: PASS.

## Task 9: Verification and Commit

- [x] **Step 1: Run evaluator regression suite**

Run:

```bash
pytest tests/evaluations/test_execution_state.py tests/evaluations/test_execution_adapters.py tests/evaluations/test_evaluation_substrate.py tests/evaluations/test_runtime_composition.py tests/core/test_evaluator_runtime.py tests/core/test_evaluator_top_level_command.py tests/plugins/test_plugin_hooks.py tests/test_plugin_cli_entrypoint.py tests/docs/test_evaluator_report_docs.py -q
```

Expected: PASS.

- [x] **Step 2: Validate OpenSpec**

Run: `openspec validate aworld-evaluator-runtime-composition-2026-06-10 --strict`

Expected: `Change 'aworld-evaluator-runtime-composition-2026-06-10' is valid`

- [x] **Step 3: Commit**

```bash
git add aworld/evaluations/runtime_composition.py aworld/evaluations/substrate.py aworld/evaluations/execution.py aworld/evaluations/report.py aworld/evaluations/scorers tests/evaluations/test_runtime_composition.py openspec/changes/aworld-evaluator-runtime-composition-2026-06-10
git commit -m "feat: add evaluator runtime composition"
```
