from __future__ import annotations

import pytest

from aworld.evaluations.runtime_composition import RetryRuntimeHarness, RolloutState
from aworld.evaluations.substrate import (
    EvalCaseDef,
    EvalSuiteDef,
    EvaluationFlowDef,
    GateMetricCondition,
    GatePolicyDef,
    TrialPolicyDef,
    compile_evaluation_flow,
    run_evaluation_flow,
)
from aworld.evaluations.report import validate_evaluator_report


def test_trial_policy_rejects_invalid_k_values():
    with pytest.raises(ValueError, match="k values"):
        TrialPolicyDef(num_trials=2, pass_at_k=(3,)).validate()


def test_build_eval_dataset_expands_trial_cases():
    suite = EvalSuiteDef(
        suite_id="trial-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        trial_policy=TrialPolicyDef(num_trials=3),
    )

    compiled = compile_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    ids = [case.eval_case_id for case in compiled.dataset.eval_cases]
    assert ids == ["case-1::trial-1", "case-1::trial-2", "case-1::trial-3"]
    assert compiled.dataset.eval_cases[0].case_data["_trial"]["original_case_id"] == "case-1"
    assert compiled.dataset.eval_cases[0].case_data["_trial"]["trial_index"] == 1


@pytest.mark.asyncio
async def test_run_evaluation_flow_reports_pass_at_k_and_pass_caret_k():
    async def fake_judge(case_input, target):
        trial_index = case_input["_trial"]["trial_index"]
        return {"score": 1.0 if trial_index == 2 else 0.0}

    suite = EvalSuiteDef(
        suite_id="trial-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        trial_policy=TrialPolicyDef(
            num_trials=3,
            pass_at_k=(2,),
            pass_caret_k=(2,),
            success_metric="score",
        ),
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name="score_pass@2", op=">=", threshold=1.0),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    assert report["metrics"]["score_pass@2"]["mean"] == pytest.approx(1.0)
    assert report["metrics"]["score_pass^2"]["mean"] == pytest.approx(0.0)
    assert report["gate"]["status"] == "pass"


@pytest.mark.asyncio
async def test_trial_success_metric_defaults_from_trial_gate_metric_base_name():
    async def fake_judge(case_input, target):
        return {"score": 1.0 if case_input["_trial"]["trial_index"] == 2 else 0.0}

    suite = EvalSuiteDef(
        suite_id="trial-default-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        trial_policy=TrialPolicyDef(num_trials=2, pass_at_k=(2,)),
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name="score_pass@2", op=">=", threshold=1.0),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    assert report["metrics"]["score_pass@2"]["mean"] == pytest.approx(1.0)
    assert report["gate"]["status"] == "pass"


@pytest.mark.asyncio
async def test_retry_attempts_do_not_count_as_trials():
    class RetryInsideTrialHarness:
        def __init__(self):
            self.calls = 0

        async def run_rollout(self, *, case, target):
            self.calls += 1
            if self.calls % 2 == 1:
                return RolloutState(case_id=case.case_id, status="failed", answer="failed-attempt")
            return RolloutState(case_id=case.case_id, status="success", answer="passed-trial")

    async def fake_judge(case_input, target):
        return {"score": 1.0 if target.get("answer") == "passed-trial" else 0.0}

    suite = EvalSuiteDef(
        suite_id="retry-trial-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        runtime_harness=RetryRuntimeHarness(
            base_harness=RetryInsideTrialHarness(),
            max_attempts=2,
        ),
        judge=fake_judge,
        trial_policy=TrialPolicyDef(
            num_trials=2,
            pass_at_k=(2,),
            success_metric="score",
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    assert report["trial_counts"]["trials_total"] == 2
    assert report["metrics"]["score_pass@2"]["mean"] == pytest.approx(1.0)
    assert len(report["results"][0]["artifacts"]["attempts"]) == 2


@pytest.mark.asyncio
async def test_multi_trial_report_exposes_trial_metadata():
    async def fake_judge(case_input, target):
        return {"score": 1.0}

    suite = EvalSuiteDef(
        suite_id="trial-report-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        trial_policy=TrialPolicyDef(num_trials=2, pass_at_k=(2,), success_metric="score"),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    assert report["trial_policy"]["num_trials"] == 2
    assert report["trial_counts"] == {"original_cases": 1, "trials_total": 2}
    assert report["results"][0]["trial"]["original_case_id"] == "case-1"
    assert report["results"][0]["trial"]["trial_index"] == 1
    validate_evaluator_report(report.to_dict())
