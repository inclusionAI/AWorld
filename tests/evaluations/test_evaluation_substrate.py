from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import pytest
from pydantic import BaseModel, Field

from aworld.evaluations.base import EvaluationConfig
import aworld.evaluations.substrate as substrate_module
from aworld.evaluations.substrate import (
    AgentJudgeBackend,
    CallableJudgeBackend,
    EvalCaseDef,
    EvalHarnessDef,
    EvalSuiteDef,
    EvaluationFlowDef,
    FallbackJudgeBackend,
    GateMetricCondition,
    GatePolicyDef,
    JudgeSchemaDef,
    TrajectoryScorerDef,
    compile_evaluation_flow,
    get_builtin_eval_suite,
    list_eval_suites,
    list_matching_eval_suites,
    load_declared_eval_suites,
    register_eval_suite,
    resolve_eval_harness,
    resolve_eval_suite,
    resolve_eval_suite_selection,
    run_evaluation_flow,
)
from aworld.evaluations.execution import EvalExecutionMode, EvalExecutionSpec
from aworld.evaluations.manifests import validate_declared_eval_suite_manifest
from aworld.evaluations.report import validate_evaluator_report
from aworld.evaluations.types import MetricNames


class DemoJudgeOutput(BaseModel):
    score: float
    verdict: str


class AliasJudgeOutput(BaseModel):
    final_score: float = Field(alias="score")
    verdict: str


class GenericJudgeOutput(BaseModel):
    decision: str
    confidence: float


@pytest.fixture(autouse=True)
def _reset_eval_registry_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})
    monkeypatch.setattr(substrate_module, "_LOADED_EVAL_MANIFEST_PATHS", set())
    monkeypatch.setattr(substrate_module, "_DECLARED_EVAL_SUITE_IDS_BY_WORKSPACE", {})
    substrate_module.register_eval_suite(
        "app-evaluator",
        lambda target: get_builtin_eval_suite("app-evaluator"),
        matcher=lambda target: target.get("target_kind") in {"file", "directory", "image"},
        priority=10,
    )


def test_compile_evaluation_flow_builds_inline_dataset_and_gate_config() -> None:
    suite = EvalSuiteDef(
        suite_id="demo-suite",
        cases=[
            EvalCaseDef(
                case_id="case-1",
                input={"query": "hello world"},
            )
        ],
        judge_schema=JudgeSchemaDef(required_fields=("score", "rank")),
        gate_policy=GatePolicyDef(metric_name="score", pass_threshold=0.8),
    )
    flow = EvaluationFlowDef(
        target={"kind": "inline", "value": {"target_path": "demo.txt"}},
        suite=suite,
    )

    compiled = compile_evaluation_flow(flow)

    assert isinstance(compiled.eval_config, EvaluationConfig)
    assert compiled.eval_config.eval_dataset is compiled.dataset
    assert compiled.dataset.eval_cases[0].case_data["query"] == "hello world"
    assert compiled.dataset.eval_cases[0].case_data["_target"]["target_path"] == "demo.txt"
    assert compiled.dataset.eval_cases[0].case_data["_expected"] is None
    assert compiled.gate_policy.metric_name == "score"


def test_compile_evaluation_flow_lowers_trajectory_scorers_to_eval_criteria() -> None:
    suite = EvalSuiteDef(
        suite_id="trajectory-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello world"})],
        judge=lambda case_input, target: {"score": 1.0},
        trajectory_scorers=(
            TrajectoryScorerDef(
                metric_name=MetricNames.TRAJECTORY_TOOL_CALLS,
                threshold=1.0,
            ),
        ),
    )

    compiled = compile_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "inline", "value": {"target_path": "demo.txt"}},
            suite=suite,
        )
    )

    metric_names = [criteria["metric_name"] for criteria in compiled.eval_config.eval_criterias]
    assert metric_names == ["score", MetricNames.TRAJECTORY_TOOL_CALLS]


def test_compile_evaluation_flow_rejects_unknown_trajectory_metric() -> None:
    suite = EvalSuiteDef(
        suite_id="trajectory-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello world"})],
        judge=lambda case_input, target: {"score": 1.0},
        trajectory_scorers=(
            TrajectoryScorerDef(metric_name="trajectory_typo"),
        ),
    )

    with pytest.raises(ValueError, match="unknown trajectory metric"):
        compile_evaluation_flow(
            EvaluationFlowDef(
                target={"kind": "inline", "value": {"target_path": "demo.txt"}},
                suite=suite,
            )
        )


def test_compile_evaluation_flow_rejects_unsupported_trajectory_scorer_params() -> None:
    suite = EvalSuiteDef(
        suite_id="trajectory-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello world"})],
        judge=lambda case_input, target: {"score": 1.0},
        trajectory_scorers=(
            TrajectoryScorerDef(
                metric_name=MetricNames.TRAJECTORY_TOOL_CALLS,
                scorer_params={"minimum_calls": 2},
            ),
        ),
    )

    with pytest.raises(ValueError, match="unsupported trajectory scorer_params"):
        compile_evaluation_flow(
            EvaluationFlowDef(
                target={"kind": "inline", "value": {"target_path": "demo.txt"}},
                suite=suite,
            )
        )


def test_eval_case_def_supports_expected_and_runtime_overrides() -> None:
    case = EvalCaseDef(
        case_id="case-1",
        input={"query": "demo"},
        expected={"answer": "ok"},
        max_turns=3,
        timeout_seconds=5.0,
        metadata={"toolsets": ["search"]},
    )

    assert case.expected == {"answer": "ok"}
    assert case.max_turns == 3
    assert case.timeout_seconds == 5.0
    assert case.metadata["toolsets"] == ["search"]


def test_compile_evaluation_flow_uses_execution_backed_target_when_suite_declares_execution() -> None:
    suite = EvalSuiteDef(
        suite_id="task-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "demo"})],
        execution=EvalExecutionSpec(mode=EvalExecutionMode.TASK, task_builder_ref="tests.helpers:build_demo_task"),
    )
    flow = EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo.txt"}}, suite=suite)

    compiled = compile_evaluation_flow(flow)

    assert compiled.eval_config.eval_target.__class__.__name__ == "_ConfiguredTaskEvalTarget"


@pytest.mark.asyncio
async def test_task_execution_rejects_path_style_task_builder_ref() -> None:
    suite = EvalSuiteDef(
        suite_id="task-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "demo"})],
        execution=EvalExecutionSpec(mode=EvalExecutionMode.TASK, task_builder_ref="scripts/run.py:build_task"),
    )
    compiled = compile_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo.txt"}}, suite=suite)
    )

    with pytest.raises(ValueError, match="importable callable"):
        await compiled.eval_config.eval_target.build_task(0, compiled.dataset.eval_cases[0])


def test_compile_evaluation_flow_preserves_live_agent_target_config() -> None:
    live_agent = object()
    suite = EvalSuiteDef(
        suite_id="agent-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "demo"})],
        execution=EvalExecutionSpec(
            mode=EvalExecutionMode.AGENT,
            target_config={"agent": live_agent},
        ),
    )
    flow = EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo.txt"}}, suite=suite)

    compiled = compile_evaluation_flow(flow)

    assert compiled.eval_config.eval_target.agent is live_agent


@pytest.mark.asyncio
async def test_program_execution_receives_normalized_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def demo_program(case, spec, target):
        return {
            "status": "success",
            "answer": target["target_path"],
            "metadata": {"target_kind_seen": target["target_kind"]},
        }

    async def fake_judge(case_input, target):
        return {"score": 1.0, "answer": target["answer"]}

    monkeypatch.setattr(
        "aworld.evaluations.execution_adapters.load_program_callable",
        lambda ref: demo_program,
    )

    suite = EvalSuiteDef(
        suite_id="program-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "demo"})],
        execution=EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case"),
        judge=fake_judge,
    )
    flow = EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo.txt"}}, suite=suite)

    report = await run_evaluation_flow(flow)

    assert report["results"][0]["judge"]["answer"] == "demo.txt"
    assert report["results"][0]["state_summary"]["answer"] == "demo.txt"


@pytest.mark.asyncio
async def test_task_execution_uses_adapter_target_config_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = type("Task", (), {"id": "task-1"})()

    async def fake_run_task(*, task):
        return {task.id: {"status": "success", "answer": "task-ok"}}

    async def fake_judge(case_input, target):
        return {"score": 1.0, "answer": target["answer"]}

    monkeypatch.setattr("aworld.evaluations.execution_adapters.Runners.run_task", fake_run_task)

    suite = EvalSuiteDef(
        suite_id="task-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "demo"})],
        execution=EvalExecutionSpec(mode=EvalExecutionMode.TASK, target_config={"task": task}),
        judge=fake_judge,
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "file", "target_path": "artifact.txt"}, suite=suite)
    )

    assert report["results"][0]["judge"]["answer"] == "task-ok"


def test_resolve_eval_harness_lowers_direct_suite_execution() -> None:
    suite = EvalSuiteDef(
        suite_id="program-suite",
        execution=EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case"),
    )

    harness = resolve_eval_harness(suite)

    assert harness.harness_id == "program-suite-execution"
    assert harness.execution is suite.execution
    assert harness.metadata["lowered_from"] == "suite.execution"


def test_resolve_eval_harness_prefers_explicit_harness() -> None:
    harness = EvalHarnessDef(
        harness_id="shared-program",
        execution=EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case"),
    )
    suite = EvalSuiteDef(suite_id="program-suite", harness=harness)

    assert resolve_eval_harness(suite) is harness


def test_judge_schema_validation_rejects_missing_fields() -> None:
    schema = JudgeSchemaDef(required_fields=("score", "rank", "criticism"))

    with pytest.raises(ValueError, match="missing required judge fields"):
        schema.validate({"score": 0.8, "rank": "Good"})


def test_typed_judge_model_accepts_valid_payload() -> None:
    schema = JudgeSchemaDef(output_model=DemoJudgeOutput)

    payload = schema.validate_payload({"score": 0.8, "verdict": "ok"})

    assert payload["score"] == 0.8
    assert payload["verdict"] == "ok"


def test_typed_judge_model_rejects_invalid_payload() -> None:
    schema = JudgeSchemaDef(output_model=DemoJudgeOutput)

    with pytest.raises(ValueError, match="verdict"):
        schema.validate_payload({"score": 0.8})


def test_legacy_required_fields_schema_still_returns_payload() -> None:
    schema = JudgeSchemaDef(required_fields=("score", "rank"))

    payload = schema.validate_payload({"score": 0.9, "rank": 1})

    assert payload["rank"] == 1


def test_judge_schema_exports_json_schema_for_typed_model() -> None:
    schema = JudgeSchemaDef(output_model=DemoJudgeOutput)

    exported = schema.json_schema()

    assert exported["properties"]["score"]["type"] == "number"
    assert "verdict" in exported["required"]


def test_typed_judge_model_returns_alias_keys_to_match_exported_schema() -> None:
    schema = JudgeSchemaDef(output_model=AliasJudgeOutput)

    payload = schema.validate_payload({"score": 0.8, "verdict": "ok"})
    exported = schema.json_schema()

    assert payload["score"] == 0.8
    assert "final_score" not in payload
    assert "score" in exported["properties"]


def test_gate_policy_uses_pass_and_approval_thresholds() -> None:
    gate = GatePolicyDef(
        metric_name="score",
        pass_threshold=0.85,
        approval_threshold=0.6,
    )

    assert gate.evaluate({"score": 0.9}).status == "pass"
    assert gate.evaluate({"score": 0.7}).status == "needs_approval"
    assert gate.evaluate({"score": 0.5}).status == "fail"


def test_composite_gate_returns_pass_when_all_conditions_hold() -> None:
    policy = GatePolicyDef(
        pass_all=(
            GateMetricCondition(metric_name="score", op=">=", threshold=0.9),
            GateMetricCondition(metric_name="latency", op="<=", threshold=5.0),
        )
    )

    decision = policy.evaluate({"score": 0.95, "latency": 4.2})

    assert decision.status == "pass"
    assert decision.metric_name is None
    assert decision.value is None
    assert len(decision.matched_conditions) == 2


def test_composite_gate_returns_needs_approval_when_approval_conditions_hold() -> None:
    policy = GatePolicyDef(
        pass_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.9),),
        approval_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.75),),
    )

    decision = policy.evaluate({"score": 0.8})

    assert decision.status == "needs_approval"
    assert len(decision.failed_conditions) == 1
    assert len(decision.matched_conditions) == 1


def test_legacy_threshold_gate_lowers_to_structured_policy() -> None:
    policy = GatePolicyDef(metric_name="score", pass_threshold=0.9, approval_threshold=0.8)

    decision = policy.evaluate({"score": 0.85})

    assert decision.status == "needs_approval"
    assert decision.metric_name == "score"
    assert decision.value == pytest.approx(0.85)


@pytest.mark.parametrize(
    ("op", "threshold", "value"),
    [
        (">", 0.9, 0.91),
        ("<", 0.9, 0.89),
        (">=", 0.9, 0.9),
        ("<=", 0.9, 0.9),
        ("==", "approved", "approved"),
        ("!=", "blocked", "approved"),
    ],
)
def test_gate_metric_condition_supports_all_declared_operators(op, threshold, value) -> None:
    policy = GatePolicyDef(
        pass_all=(GateMetricCondition(metric_name="metric", op=op, threshold=threshold),)
    )

    assert policy.evaluate({"metric": value}).status == "pass"


def test_gate_policy_reports_missing_metric() -> None:
    policy = GatePolicyDef(
        pass_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.9),)
    )

    decision = policy.evaluate({})

    assert decision.status == "fail"
    assert decision.failed_conditions == [
        {"metric_name": "score", "op": ">=", "threshold": 0.9, "reason": "missing_metric"}
    ]


def test_gate_policy_missing_pass_metric_fails_even_when_approval_matches() -> None:
    policy = GatePolicyDef(
        pass_all=(GateMetricCondition(metric_name="trajectory_tool_calls", op=">=", threshold=1.0),),
        approval_all=(GateMetricCondition(metric_name="score", op=">=", threshold=0.7),),
    )

    decision = policy.evaluate({"score": 0.8})

    assert decision.status == "fail"
    assert decision.failed_conditions == [
        {"metric_name": "trajectory_tool_calls", "op": ">=", "threshold": 1.0, "reason": "missing_metric"}
    ]


def test_gate_policy_rejects_unsupported_operator() -> None:
    policy = GatePolicyDef(
        pass_all=(GateMetricCondition(metric_name="score", op="contains", threshold=0.9),)
    )

    with pytest.raises(ValueError, match="unsupported gate operator"):
        policy.evaluate({"score": 0.95})


@pytest.mark.asyncio
async def test_run_evaluation_flow_executes_suite_judge_and_returns_gate() -> None:
    async def fake_judge(case_input, target):
        assert case_input["query"] == "hello"
        assert target["target_path"] == "artifact.txt"
        return {
            "score": 0.7,
            "rank": "Good",
            "criticism": "Needs stronger hierarchy.",
            "praise": "The layout is clear.",
            "improvement_advice": "Increase contrast around the hero area.",
        }

    suite = EvalSuiteDef(
        suite_id="demo-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge_schema=JudgeSchemaDef(
            required_fields=(
                "score",
                "rank",
                "criticism",
                "praise",
                "improvement_advice",
            )
        ),
        gate_policy=GatePolicyDef(
            metric_name="score",
            pass_threshold=0.85,
            approval_threshold=0.6,
        ),
        judge=fake_judge,
    )
    flow = EvaluationFlowDef(
        target={"kind": "file", "target_path": "artifact.txt"},
        suite=suite,
    )

    report = await run_evaluation_flow(flow)

    assert report["suite_id"] == "demo-suite"
    assert report["report_format"]["id"] == "aworld.evaluator.report"
    assert report["report_format"]["version"] == 1
    assert report["generated_at"]
    assert report["gate"]["status"] == "needs_approval"
    assert report["results"][0]["judge"]["rank"] == "Good"
    assert report["results"][0]["metrics"]["score"]["value"] == pytest.approx(0.7)
    assert report["results"][0]["metrics"]["score"]["status"] == "FAILED"
    assert report["metrics"]["score"]["mean"] == pytest.approx(0.7)
    assert report["result_counts"]["cases_total"] == 1
    assert report["result_counts"]["cases_with_metrics"] == 1
    assert report["summary"]["demo-suite"]["score"]["mean"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_run_evaluation_flow_exposes_judge_schema_once() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.95, "verdict": "ok"}

    suite = EvalSuiteDef(
        suite_id="typed-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge_schema=JudgeSchemaDef(output_model=DemoJudgeOutput),
        gate_policy=GatePolicyDef(metric_name="score", pass_threshold=0.8),
        judge=fake_judge,
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["judge_schema"]["properties"]["score"]["type"] == "number"
    assert "_judge_schema" not in report["results"][0]["judge"]
    validate_evaluator_report(report.to_dict())


@pytest.mark.asyncio
async def test_run_evaluation_flow_evaluates_composite_gate_metrics() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.95, "latency": 4.2}

    suite = EvalSuiteDef(
        suite_id="composite-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(
                GateMetricCondition(metric_name="score", op=">=", threshold=0.9),
                GateMetricCondition(metric_name="latency", op="<=", threshold=5.0),
            )
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "pass"
    assert len(report["gate"]["matched_conditions"]) == 2
    assert report["metrics"]["latency"]["mean"] == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_run_evaluation_flow_failed_composite_gate_keeps_metric_status_and_matches() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.7, "latency": 4.2}

    suite = EvalSuiteDef(
        suite_id="composite-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(
                GateMetricCondition(metric_name="score", op=">=", threshold=0.9),
                GateMetricCondition(metric_name="latency", op="<=", threshold=5.0),
            )
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "fail"
    assert report["results"][0]["metrics"]["score"]["status"] == "FAILED"
    assert report["metrics"]["score"]["eval_status"] == "FAILED"
    assert report["gate"]["matched_conditions"] == [
        {"metric_name": "latency", "op": "<=", "threshold": 5.0}
    ]
    assert report["gate"]["failed_conditions"] == [
        {"metric_name": "score", "op": ">=", "threshold": 0.9}
    ]


@pytest.mark.asyncio
async def test_run_evaluation_flow_missing_gate_metric_fails_closed_and_keeps_results() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.95}

    suite = EvalSuiteDef(
        suite_id="composite-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name="trajectory_tool_calls", op=">=", threshold=1.0),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "fail"
    assert report["gate"]["failed_conditions"] == [
        {"metric_name": "trajectory_tool_calls", "op": ">=", "threshold": 1.0, "reason": "missing_metric"}
    ]
    assert report["results"][0]["case_id"] == "case-1"
    assert report["results"][0]["metrics"]["score"]["value"] == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_run_evaluation_flow_composite_gate_is_not_condition_order_sensitive() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.95, "latency": 4.2}

    suite = EvalSuiteDef(
        suite_id="composite-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(
                GateMetricCondition(metric_name="latency", op="<=", threshold=5.0),
                GateMetricCondition(metric_name="score", op=">=", threshold=0.9),
            )
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "pass"
    assert report["metrics"]["score"]["mean"] == pytest.approx(0.95)
    assert report["metrics"]["latency"]["mean"] == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_run_evaluation_flow_composite_gate_without_score_condition_still_runs_suite_judge() -> None:
    async def fake_judge(case_input, target):
        return {"score": 1.0, "latency": 4.2}

    suite = EvalSuiteDef(
        suite_id="latency-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name="latency", op="<=", threshold=5.0),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "pass"
    assert report["metrics"]["latency"]["mean"] == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_run_evaluation_flow_legacy_non_score_gate_does_not_set_score_threshold() -> None:
    async def fake_judge(case_input, target):
        return {"score": 1.0, "latency": 6.0}

    suite = EvalSuiteDef(
        suite_id="latency-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(metric_name="latency", pass_threshold=5.0),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "pass"
    assert report["results"][0]["metrics"]["score"]["status"] == "PASSED"
    assert report["metrics"]["score"]["eval_status"] == "PASSED"


@pytest.mark.asyncio
async def test_run_evaluation_flow_strict_gate_operator_keeps_metric_status_consistent() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.9}

    suite = EvalSuiteDef(
        suite_id="strict-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name="score", op=">", threshold=0.9),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "fail"
    assert report["results"][0]["metrics"]["score"]["status"] == "FAILED"
    assert report["metrics"]["score"]["eval_status"] == "FAILED"


@pytest.mark.asyncio
async def test_run_evaluation_flow_equality_gate_keeps_metric_status_consistent() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.7}

    suite = EvalSuiteDef(
        suite_id="equality-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name="score", op="==", threshold=0.9),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "fail"
    assert report["results"][0]["metrics"]["score"]["status"] == "FAILED"
    assert report["metrics"]["score"]["eval_status"] == "FAILED"


@pytest.mark.asyncio
async def test_run_evaluation_flow_categorical_gate_metric() -> None:
    async def fake_judge(case_input, target):
        return {"score": 1.0, "verdict": "approved"}

    suite = EvalSuiteDef(
        suite_id="categorical-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge=fake_judge,
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name="verdict", op="==", threshold="approved"),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "pass"
    assert report["gate"]["value"] is None
    assert report["results"][0]["metrics"]["verdict"]["value"] == "approved"
    assert report["metrics"]["verdict"]["value"] == "approved"


@pytest.mark.asyncio
async def test_run_evaluation_flow_reports_trajectory_scorer_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def demo_program(case, spec, target):
        return {
            "status": "success",
            "answer": "ok",
            "trajectory": [
                {"action": {"tool_calls": [{"id": "call-1", "function": {"name": "search", "arguments": "{}"}}]}}
            ],
        }

    async def fake_judge(case_input, target):
        return {"score": 1.0}

    monkeypatch.setattr(
        "aworld.evaluations.execution_adapters.load_program_callable",
        lambda ref: demo_program,
    )

    suite = EvalSuiteDef(
        suite_id="trajectory-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        execution=EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case"),
        judge=fake_judge,
        trajectory_scorers=(
            TrajectoryScorerDef(metric_name=MetricNames.TRAJECTORY_TOOL_CALLS, threshold=1.0),
        ),
        gate_policy=GatePolicyDef(
            pass_all=(
                GateMetricCondition(metric_name="score", op=">=", threshold=0.9),
                GateMetricCondition(metric_name=MetricNames.TRAJECTORY_TOOL_CALLS, op="==", threshold=1.0),
            )
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "pass"
    assert report["results"][0]["metrics"][MetricNames.TRAJECTORY_TOOL_CALLS]["value"] == pytest.approx(1.0)
    assert report["metrics"][MetricNames.TRAJECTORY_TOOL_CALLS]["mean"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_declared_trajectory_metric_takes_precedence_over_judge_payload_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def demo_program(case, spec, target):
        return {
            "status": "success",
            "answer": "ok",
            "trajectory": [
                {"action": {"tool_calls": [{"id": "call-1", "function": {"name": "search", "arguments": "{}"}}]}}
            ],
        }

    async def fake_judge(case_input, target):
        return {"score": 1.0, MetricNames.TRAJECTORY_TOOL_CALLS: 0.0}

    monkeypatch.setattr(
        "aworld.evaluations.execution_adapters.load_program_callable",
        lambda ref: demo_program,
    )

    suite = EvalSuiteDef(
        suite_id="trajectory-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        execution=EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case"),
        judge=fake_judge,
        trajectory_scorers=(
            TrajectoryScorerDef(metric_name=MetricNames.TRAJECTORY_TOOL_CALLS, threshold=1.0),
        ),
        gate_policy=GatePolicyDef(
            pass_all=(GateMetricCondition(metric_name=MetricNames.TRAJECTORY_TOOL_CALLS, op="==", threshold=1.0),)
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    assert report["gate"]["status"] == "pass"
    assert report["results"][0]["metrics"][MetricNames.TRAJECTORY_TOOL_CALLS]["value"] == pytest.approx(1.0)
    assert report["metrics"][MetricNames.TRAJECTORY_TOOL_CALLS]["mean"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_suite_judge_prefers_state_payload_over_static_case_target() -> None:
    async def fake_judge(case_input, target):
        return {"score": 1.0, "answer": target["answer"]}

    suite = EvalSuiteDef(
        suite_id="demo-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "demo"})],
        judge=fake_judge,
    )
    from aworld.evaluations.scorers.suite_judge import SuiteJudgeScorer

    scorer = SuiteJudgeScorer(suite=suite)
    input_case = type("Case", (), {"case_data": {"query": "demo", "_target": {"path": "legacy"}}})()
    output = {"state": {"answer": "from-state", "status": "success"}}

    result = await scorer.score(0, input_case, output)

    assert result.metric_results["score"]["metadata"]["answer"] == "from-state"


@pytest.mark.asyncio
async def test_report_keeps_full_judge_metadata_only_on_score_metric() -> None:
    async def fake_judge(case_input, target):
        return {
            "score": 0.5,
            "verdict": "Fail",
            "A1_groundedness": 1,
            "veto_triggered": True,
        }

    suite = EvalSuiteDef(
        suite_id="demo-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "demo"})],
        judge=fake_judge,
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "inline", "value": {"target_path": "demo"}},
            suite=suite,
        )
    )

    result = report["results"][0]
    assert result["judge"]["A1_groundedness"] == 1
    assert result["metrics"]["verdict"]["value"] == "Fail"
    assert set(result["metric_details"]) == {"score"}
    assert result["metric_details"]["score"]["veto_triggered"] is True


def test_builtin_app_evaluator_suite_has_required_schema_and_score_gate() -> None:
    suite = get_builtin_eval_suite("app-evaluator")

    assert suite.suite_id == "app-evaluator"
    assert suite.judge_schema.required_fields == (
        "score",
        "rank",
        "criticism",
        "praise",
        "improvement_advice",
    )
    assert suite.gate_policy.metric_name == "score"


def test_eval_suite_registry_resolves_explicit_and_target_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})

    def generic_factory(target):
        return EvalSuiteDef(suite_id="generic-review")

    def image_factory(target):
        return EvalSuiteDef(suite_id="image-review")

    register_eval_suite(
        "generic-review",
        generic_factory,
        matcher=lambda target: True,
        priority=10,
    )
    register_eval_suite(
        "image-review",
        image_factory,
        matcher=lambda target: target["target_kind"] == "image",
        priority=50,
    )

    image_target = tmp_path / "artifact.png"
    image_target.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="))
    text_target = tmp_path / "artifact.txt"
    text_target.write_text("artifact", encoding="utf-8")

    listed = list_eval_suites()
    explicit = resolve_eval_suite("generic-review", image_target)
    image_default = resolve_eval_suite(None, image_target)
    text_default = resolve_eval_suite(None, text_target)

    assert listed == ["generic-review", "image-review"]
    assert explicit.suite_id == "generic-review"
    assert image_default.suite_id == "image-review"
    assert image_default.cases[0].input["target_kind"] == "image"
    assert text_default.suite_id == "generic-review"
    assert text_default.cases[0].input["target_kind"] == "file"


def test_eval_suite_registry_reports_matching_suites_and_selection_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})

    register_eval_suite(
        "generic-review",
        lambda target: EvalSuiteDef(suite_id="generic-review"),
        matcher=lambda target: True,
        priority=10,
    )
    register_eval_suite(
        "image-review",
        lambda target: EvalSuiteDef(suite_id="image-review"),
        matcher=lambda target: target["target_kind"] == "image",
        priority=50,
    )

    image_target = tmp_path / "artifact.png"
    image_target.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="))

    matching = list_matching_eval_suites(image_target)
    auto_selection = resolve_eval_suite_selection(None, image_target)
    explicit_selection = resolve_eval_suite_selection("generic-review", image_target)

    assert matching == ["image-review", "generic-review"]
    assert auto_selection.mode == "auto"
    assert auto_selection.suite_id == "image-review"
    assert explicit_selection.mode == "explicit"
    assert explicit_selection.suite_id == "generic-review"


def test_load_declared_eval_suites_registers_manifest_backed_suite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "strict-ui.json").write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file", "directory"],
  "gate_policy": {
    "metric_name": "score",
    "pass_threshold": 0.92,
    "approval_threshold": 0.8
  },
  "metadata": {
    "owner": "qa"
  }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})

    loaded = load_declared_eval_suites(tmp_path)
    listed = list_eval_suites()

    assert loaded == ["strict-ui"]
    assert "strict-ui" in listed


def test_load_declared_eval_suites_rejects_execution_in_manifest(tmp_path) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "program-suite.json").write_text(
        """
{
  "suite_id": "program-suite",
  "base_suite": "app-evaluator",
  "execution": {
    "mode": "program",
    "target_ref": "pkg.module:run_case"
  }
}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Additional properties are not allowed"):
        load_declared_eval_suites(tmp_path)


def test_declared_eval_suite_manifest_schema_rejects_execution_contract() -> None:
    with pytest.raises(ValueError, match="Additional properties are not allowed"):
        validate_declared_eval_suite_manifest(
            {
                "suite_id": "program-suite",
                "base_suite": "app-evaluator",
                "execution": {"mode": "program", "target_ref": "pkg.module:run_case"},
            }
        )


def test_declared_eval_suite_can_be_selected_for_matching_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "strict-ui.json").write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"],
  "gate_policy": {
    "metric_name": "score",
    "pass_threshold": 0.92,
    "approval_threshold": 0.8
  }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})
    load_declared_eval_suites(tmp_path)

    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    selection = resolve_eval_suite_selection("strict-ui", target)

    assert selection.suite_id == "strict-ui"
    assert selection.suite.gate_policy.pass_threshold == pytest.approx(0.92)


def test_load_declared_eval_suites_refreshes_existing_manifest_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "strict-ui.json"
    manifest_path.write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"],
  "gate_policy": {
    "metric_name": "score",
    "pass_threshold": 0.92,
    "approval_threshold": 0.8
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})

    load_declared_eval_suites(tmp_path)

    manifest_path.write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"],
  "gate_policy": {
    "metric_name": "score",
    "pass_threshold": 0.99,
    "approval_threshold": 0.8
  }
}
""".strip(),
        encoding="utf-8",
    )

    load_declared_eval_suites(tmp_path)
    selection = resolve_eval_suite_selection("strict-ui", tmp_path / "artifact.txt")

    assert selection.suite.gate_policy.pass_threshold == pytest.approx(0.99)


def test_load_declared_eval_suites_removes_deleted_manifest_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "strict-ui.json"
    manifest_path.write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"]
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})

    load_declared_eval_suites(tmp_path)
    manifest_path.unlink()

    load_declared_eval_suites(tmp_path)

    assert "strict-ui" not in list_eval_suites()


def test_declared_eval_suites_are_resolved_per_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    for workspace, threshold in ((workspace_a, 0.91), (workspace_b, 0.99)):
        manifest_dir = workspace / ".aworld" / "evaluators"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "strict-ui.json").write_text(
            f"""
{{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"],
  "gate_policy": {{
    "metric_name": "score",
    "pass_threshold": {threshold},
    "approval_threshold": 0.8
  }}
}}
""".strip(),
            encoding="utf-8",
        )

    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})

    load_declared_eval_suites(workspace_a)
    load_declared_eval_suites(workspace_b)

    selection_a = resolve_eval_suite_selection("strict-ui", workspace_a / "artifact.txt")
    selection_b = resolve_eval_suite_selection("strict-ui", workspace_b / "artifact.txt")

    assert selection_a.suite.gate_policy.pass_threshold == pytest.approx(0.91)
    assert selection_b.suite.gate_policy.pass_threshold == pytest.approx(0.99)


def test_load_declared_eval_suites_rejects_builtin_suite_id_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "override.json").write_text(
        """
{
  "suite_id": "app-evaluator",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"]
}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reserved suite_id"):
        load_declared_eval_suites(tmp_path)

    assert list_eval_suites() == ["app-evaluator"]


@pytest.mark.asyncio
async def test_agent_judge_backend_parses_app_evaluator_json_payload() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        assert "artifact.txt" in prompt
        assert "UI review committee" in system_prompt
        return {
            "results": [
                {
                    "filename": "artifact.txt",
                    "score": 0.91,
                    "rank": "Exemplary",
                    "criticism": "Almost none.",
                    "praise": "Strong visual hierarchy.",
                    "improvement_advice": "Keep the current direction.",
                }
            ]
        }

    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="You are a UI review committee.",
        executor=fake_executor,
    )

    payload = await backend.judge(
        case_input={"target_path": "artifact.txt"},
        target={"target_path": "artifact.txt", "target_kind": "file"},
        suite=EvalSuiteDef(suite_id="app-evaluator"),
    )

    assert payload["score"] == pytest.approx(0.91)
    assert payload["rank"] == "Exemplary"


@pytest.mark.asyncio
async def test_agent_judge_backend_parses_fenced_json_payload() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        return """
I checked the trajectory evidence.

```json
{
  "score": 72.5,
  "verdict": "Pass",
  "veto_triggered": false
}
```
"""

    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: "judge this trajectory",
    )

    payload = await backend.judge(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
    )

    assert payload["score"] == pytest.approx(72.5)
    assert payload["verdict"] == "Pass"
    assert payload["veto_triggered"] is False


@pytest.mark.asyncio
async def test_agent_judge_backend_parses_json_payload_with_explanatory_text() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        return """
Here is the evaluation. I found one unrelated example first: {"ignored": true}

{
  "score": 23.4,
  "verdict": "Fail",
  "veto_triggered": true,
  "has_evidence": true
}

The candidate should not pass.
"""

    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: "judge this trajectory",
    )

    payload = await backend.judge(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
    )

    assert payload["score"] == pytest.approx(23.4)
    assert payload["verdict"] == "Fail"
    assert payload["veto_triggered"] is True
    assert payload["has_evidence"] is True


@pytest.mark.asyncio
async def test_agent_judge_backend_prefers_complete_judge_payload_over_nested_score_objects() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        return """
The groundedness dimension is:

```json
{
  "score": 3,
  "weight": 0.25,
  "evidence": ["partial support"],
  "rationale": "This is only a nested dimension object."
}
```

Final report:

```json
{
  "task_id": "case-1",
  "score": 74.6,
  "verdict": "Pass",
  "veto_triggered": false,
  "has_evidence": true,
  "evidence_block_count": 5,
  "A1_groundedness": 3,
  "A2_completeness": 5,
  "A3_relevance": 5,
  "A4_readability": 4,
  "B1_tool_use": 3,
  "B2_efficiency": 3,
  "B3_compliance": 5,
  "B4_robustness": 4
}
```
"""

    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: "judge this trajectory",
    )

    payload = await backend.judge(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
    )

    assert payload["score"] == pytest.approx(74.6)
    assert payload["verdict"] == "Pass"
    assert payload["A1_groundedness"] == 3


@pytest.mark.asyncio
async def test_agent_judge_backend_resolves_read_only_artifact_requests(tmp_path: Path) -> None:
    artifact_path = tmp_path / "evidence.txt"
    artifact_path.write_text("artifact evidence content", encoding="utf-8")
    calls: list[str] = []

    async def fake_executor(prompt: str, system_prompt: str):
        calls.append(prompt)
        payload = json.loads(prompt)
        if len(calls) == 1:
            return {
                "artifact_read_requests": [
                    {
                        "path": str(artifact_path),
                        "max_chars": 200,
                    }
                ]
            }
        assert payload["artifact_read_results"][0]["content"] == "artifact evidence content"
        return {
            "score": 88.0,
            "verdict": "Pass",
            "veto_triggered": False,
        }

    prompt = {
        "artifact_backed_evidence": {
            "mode": "read_only_artifact_index",
            "read_policy": {
                "read_only": True,
                "external_network_allowed": False,
                "mutation_allowed": False,
            },
            "artifacts": [
                {
                    "kind": "source_artifact",
                    "path": str(artifact_path),
                    "available": True,
                }
            ],
        },
        "required_output_schema": {"score": "number"},
    }
    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: json.dumps(prompt),
    )

    payload = await backend.judge(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
    )

    assert payload["score"] == pytest.approx(88.0)
    assert payload["verdict"] == "Pass"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_agent_judge_backend_denies_artifact_reads_outside_index(tmp_path: Path) -> None:
    allowed_path = tmp_path / "allowed.txt"
    denied_path = tmp_path / "denied.txt"
    allowed_path.write_text("allowed evidence", encoding="utf-8")
    denied_path.write_text("denied evidence", encoding="utf-8")

    async def fake_executor(prompt: str, system_prompt: str):
        payload = json.loads(prompt)
        if "artifact_read_results" not in payload:
            return {"artifact_read_requests": [{"path": str(denied_path)}]}
        result = payload["artifact_read_results"][0]
        assert result["status"] == "denied"
        assert result["reason"] == "path_not_in_artifact_index"
        assert result["artifact_index_present"] is True
        assert result["allowed_path_count"] == 1
        assert len(result["requested_path_fingerprint"]) == 16
        assert "content" not in result
        assert str(allowed_path) not in json.dumps(result)
        return {"score": 10.0, "verdict": "Fail"}

    prompt = {
        "artifact_backed_evidence": {
            "mode": "read_only_artifact_index",
            "read_policy": {
                "read_only": True,
                "external_network_allowed": False,
                "mutation_allowed": False,
            },
            "artifacts": [
                {
                    "kind": "source_artifact",
                    "path": str(allowed_path),
                    "available": True,
                }
            ],
        },
    }
    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: json.dumps(prompt),
    )

    execution = await backend.execute(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
    )

    assert execution.payload["score"] == pytest.approx(10.0)
    denied_diagnostic = execution.diagnostics[-1]
    assert denied_diagnostic["artifact_read_denied_count"] == 1
    assert denied_diagnostic["artifact_read_denial_reasons"] == [
        "path_not_in_artifact_index"
    ]
    assert len(denied_diagnostic["artifact_read_denied_path_fingerprints"][0]) == 16
    assert str(allowed_path) not in json.dumps(denied_diagnostic)


@pytest.mark.asyncio
async def test_agent_judge_backend_accumulates_multi_round_artifact_reads(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.txt"
    second_path = tmp_path / "second.txt"
    first_path.write_text("first evidence", encoding="utf-8")
    second_path.write_text("second evidence", encoding="utf-8")
    calls: list[dict] = []

    async def fake_executor(prompt: str, system_prompt: str):
        payload = json.loads(prompt)
        calls.append(payload)
        results = payload.get("artifact_read_results") or []
        if len(results) == 0:
            return {"artifact_read_requests": [{"path": str(first_path)}]}
        if len(results) == 1:
            assert results[0]["content"] == "first evidence"
            return {"artifact_read_requests": [{"path": str(second_path)}]}
        assert [result["content"] for result in results] == [
            "first evidence",
            "second evidence",
        ]
        return {"score": 91.0, "verdict": "Pass"}

    prompt = {
        "artifact_backed_evidence": {
            "mode": "read_only_artifact_index",
            "read_policy": {
                "read_only": True,
                "external_network_allowed": False,
                "mutation_allowed": False,
            },
            "artifacts": [
                {"kind": "source_artifact", "path": str(first_path), "available": True},
                {"kind": "source_artifact", "path": str(second_path), "available": True},
            ],
        },
    }
    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: json.dumps(prompt),
    )

    payload = await backend.judge(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
    )

    assert payload["score"] == pytest.approx(91.0)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_agent_judge_backend_records_per_call_artifact_diagnostics(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "evidence.txt"
    artifact_path.write_text("grounded evidence", encoding="utf-8")
    call_count = 0

    async def fake_executor(prompt: str, system_prompt: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"artifact_read_requests": [{"path": str(artifact_path)}]}
        return {"score": 91.0, "verdict": "Pass"}

    prompt = {
        "artifact_backed_evidence": {
            "mode": "read_only_artifact_index",
            "read_policy": {
                "read_only": True,
                "external_network_allowed": False,
                "mutation_allowed": False,
            },
            "artifacts": [
                {"kind": "source_artifact", "path": str(artifact_path), "available": True}
            ],
        },
    }
    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: json.dumps(prompt),
    )

    execution = await backend.execute(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
    )

    assert [item["phase"] for item in execution.diagnostics] == [
        "initial_judge",
        "artifact_read_round_1",
    ]
    initial, followup = execution.diagnostics
    assert initial["status"] == "succeeded"
    assert initial["artifact_request_count"] == 1
    assert initial["artifact_read_count"] == 0
    assert followup["status"] == "succeeded"
    assert followup["artifact_read_count"] == 1
    assert followup["artifact_read_chars"] == len("grounded evidence")
    assert followup["prompt_chars"] > initial["prompt_chars"]
    assert followup["estimated_input_tokens"] > 0
    assert followup["latency_ms"] >= 0
    assert "content" not in json.dumps(execution.diagnostics)


@pytest.mark.asyncio
async def test_agent_judge_backend_timeout_identifies_artifact_read_phase(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "evidence.txt"
    artifact_path.write_text("grounded evidence", encoding="utf-8")
    call_count = 0

    async def fake_executor(prompt: str, system_prompt: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"artifact_read_requests": [{"path": str(artifact_path)}]}
        await asyncio.sleep(1)
        return {"score": 91.0, "verdict": "Pass"}

    prompt = {
        "artifact_backed_evidence": {
            "mode": "read_only_artifact_index",
            "read_policy": {
                "read_only": True,
                "external_network_allowed": False,
                "mutation_allowed": False,
            },
            "artifacts": [
                {"kind": "source_artifact", "path": str(artifact_path), "available": True}
            ],
        },
    }
    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: json.dumps(prompt),
        timeout_seconds=0.01,
    )

    with pytest.raises(asyncio.TimeoutError) as exc_info:
        await backend.execute(
            case_input={"query": "evaluate"},
            target={"answer": "done"},
            suite=EvalSuiteDef(suite_id="trajectory-source-evaluator"),
        )

    diagnostics = exc_info.value.judge_diagnostics
    assert [item["status"] for item in diagnostics] == ["succeeded", "timed_out"]
    assert diagnostics[-1]["phase"] == "artifact_read_round_1"
    assert diagnostics[-1]["artifact_read_count"] == 1
    assert diagnostics[-1]["timeout_seconds"] == pytest.approx(0.01)
    assert diagnostics[-1]["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_run_evaluation_flow_exposes_judge_call_diagnostics() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        return {"score": 0.9, "verdict": "Pass"}

    suite = EvalSuiteDef(
        suite_id="diagnostic-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        judge_schema=JudgeSchemaDef(required_fields=("score", "verdict")),
        judge_backend=AgentJudgeBackend(
            backend_id="diagnostic-agent",
            system_prompt="judge",
            executor=fake_executor,
            prompt_builder=lambda case_input, target, suite: "judge this trajectory",
        ),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "file", "target_path": "artifact.txt"},
            suite=suite,
        )
    )

    diagnostics = report["results"][0]["judge_diagnostics"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["phase"] == "initial_judge"
    assert diagnostics[0]["status"] == "succeeded"
    assert "_judge_diagnostics" not in report["results"][0]["judge"]


@pytest.mark.asyncio
async def test_agent_judge_backend_uses_schema_to_select_generic_json_payload() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        return """
Intermediate calculation:

```json
{"score": 3, "weight": 0.25, "rationale": "not the payload"}
```

Final answer:

```json
{"decision": "accept", "confidence": 0.82}
```
"""

    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: "judge this trajectory",
    )

    payload = await backend.judge(
        case_input={"query": "evaluate"},
        target={"answer": "done"},
        suite=EvalSuiteDef(
            suite_id="generic-json-judge",
            judge_schema=JudgeSchemaDef(output_model=GenericJudgeOutput),
        ),
    )

    assert payload == {"decision": "accept", "confidence": 0.82}


@pytest.mark.asyncio
async def test_agent_judge_backend_does_not_fallback_when_schema_matches_no_json_payload() -> None:
    async def fake_executor(prompt: str, system_prompt: str):
        return """
```json
{"has_evidence": true, "evidence_block_count": 3}
```
"""

    backend = AgentJudgeBackend(
        backend_id="agent-backend",
        system_prompt="judge",
        executor=fake_executor,
        prompt_builder=lambda case_input, target, suite: "judge this trajectory",
    )

    with pytest.raises(ValueError, match="no JSON object matches judge schema"):
        await backend.judge(
            case_input={"query": "evaluate"},
            target={"answer": "done"},
            suite=EvalSuiteDef(
                suite_id="generic-json-judge",
                judge_schema=JudgeSchemaDef(output_model=GenericJudgeOutput),
            ),
        )


@pytest.mark.asyncio
async def test_builtin_app_evaluator_can_use_injected_judge_backend() -> None:
    class StubBackend:
        backend_id = "stub-agent"

        def is_available(self) -> bool:
            return True

        async def judge(self, case_input, target, suite):
            return {
                "score": 0.72,
                "rank": "Good",
                "criticism": "Needs slightly better spacing.",
                "praise": "Solid composition.",
                "improvement_advice": "Increase whitespace around the main section.",
            }

    suite = get_builtin_eval_suite("app-evaluator", judge_backend=StubBackend()).with_cases(
        [EvalCaseDef(case_id="artifact", input={"target_path": "artifact.txt"})]
    )
    flow = EvaluationFlowDef(
        target={"target_path": "artifact.txt", "target_kind": "file"},
        suite=suite,
    )

    report = await run_evaluation_flow(flow)

    assert report["judge_backend"]["backend_id"] == "stub-agent"
    assert report["results"][0]["judge"]["rank"] == "Good"
    assert report["report_version"] == 1
    assert report["approval"]["required"] is True


@pytest.mark.asyncio
async def test_fallback_judge_backend_uses_next_backend_after_timeout() -> None:
    async def slow_executor(prompt: str, system_prompt: str):
        await asyncio.sleep(0.05)
        return {"results": [{"filename": "artifact.txt", "score": 0.99}]}

    fallback = FallbackJudgeBackend(
        backend_id="fallback",
        backends=(
            AgentJudgeBackend(
                backend_id="slow-agent",
                system_prompt="judge",
                executor=slow_executor,
                timeout_seconds=0.01,
            ),
            CallableJudgeBackend(
                backend_id="heuristic",
                judge=lambda case_input, target: {
                    "score": 0.61,
                    "rank": "Good",
                    "criticism": "Fallback path used.",
                    "praise": "Fallback stayed responsive.",
                    "improvement_advice": "Keep timeout budgets explicit.",
                },
            ),
        ),
    )

    execution = await fallback.execute(
        case_input={"target_path": "artifact.txt"},
        target={"target_path": "artifact.txt", "target_kind": "file"},
        suite=EvalSuiteDef(suite_id="app-evaluator"),
    )

    assert execution.backend_id == "heuristic"
    assert execution.payload["score"] == pytest.approx(0.61)


@pytest.mark.asyncio
async def test_builtin_app_evaluator_passes_visual_target_images_to_agent_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path = tmp_path / "artifact.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="
        )
    )

    captured = {}

    async def fake_executor(prompt, system_prompt: str, **kwargs):
        captured["prompt"] = prompt
        return {
            "results": [
                {
                    "filename": image_path.name,
                    "score": 0.88,
                    "rank": "Exemplary",
                    "criticism": "Minor spacing polish remains.",
                    "praise": "The main visual is clear.",
                    "improvement_advice": "Tighten secondary detail spacing.",
                }
            ]
        }

    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(substrate_module, "_default_agent_judge_executor", fake_executor)

    suite = get_builtin_eval_suite("app-evaluator").with_cases(
        [
            EvalCaseDef(
                case_id="artifact",
                input={"target_path": str(image_path), "target_kind": "image"},
            )
        ]
    )
    flow = EvaluationFlowDef(
        target={"target_path": str(image_path), "target_kind": "image"},
        suite=suite,
    )

    report = await run_evaluation_flow(flow)

    prompt = captured["prompt"]

    assert isinstance(prompt, tuple)
    assert prompt[0].startswith("Evaluate the following app artifact")
    assert len(prompt[1]) == 1
    assert prompt[1][0].startswith("data:image/png;base64,")
    assert report["judge_backend"]["backend_id"] == "app-evaluator-agent"
