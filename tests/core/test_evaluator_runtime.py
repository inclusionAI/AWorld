from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

import aworld.evaluations.substrate as substrate_module
from aworld.evaluations.manifests import get_declared_eval_suite_schema
from aworld.evaluations.report import EvaluatorReport
from aworld_cli.evaluator_runtime import (
    _build_source_prompt,
    available_evaluator_suites,
    evaluator_exit_code,
    get_declared_evaluator_suite_schema,
    get_evaluator_report_schema,
    run_evaluator_cli,
    run_evaluator_source_cli,
    validate_evaluator_report,
)
from aworld_cli.evaluator_rendering import render_evaluator_summary


@pytest.fixture(autouse=True)
def _reset_eval_registry_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(substrate_module, "_EVAL_SUITE_REGISTRY", {})
    monkeypatch.setattr(substrate_module, "_LOADED_EVAL_MANIFEST_PATHS", set())
    monkeypatch.setattr(substrate_module, "_DECLARED_EVAL_SUITE_IDS_BY_WORKSPACE", {})
    substrate_module.register_eval_suite(
        "app-evaluator",
        lambda target: substrate_module.get_builtin_eval_suite("app-evaluator"),
        matcher=lambda target: target.get("target_kind") in {"file", "directory", "image"},
        priority=10,
    )


def test_run_evaluator_cli_persists_approval_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    output = tmp_path / "report.json"

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.7}}},
            "results": [],
            "gate": {"status": "needs_approval", "metric_name": "score", "value": 0.7},
            "approval": {"required": True, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    report = run_evaluator_cli(
        target=str(target),
        interactive_approval=True,
        output=str(output),
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert report["approval"]["resolved"] is True
    assert report["approval"]["approved"] is True
    assert persisted["approval"]["approved"] is True
    assert persisted["judge_backend"]["backend_id"] == "stub-agent"


def test_run_evaluator_source_cli_builds_task_answer_flow_with_default_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"existing"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    output = tmp_path / "report.json"
    captured = {}

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        return {
            "report_version": 1,
            "suite_id": "source-evaluator",
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="task-answer",
        judge_agent=str(judge_agent),
        output=str(output),
    )

    flow = captured["flow"]
    assert flow.target["target_kind"] == "source"
    assert flow.target["source_kind"] == "task-answer"
    assert flow.suite.cases[0].case_id == "case-1"
    assert flow.suite.cases[0].input == {"input": "question"}
    assert flow.suite.judge_backend.backend_id == "source-agent-md"
    assert report["source_selection"]["kind"] == "task-answer"
    assert report["automation"]["source_kind"] == "task-answer"
    assert output.exists()


def test_source_prompt_uses_zero_to_hundred_score_contract() -> None:
    prompt = _build_source_prompt(
        {"input": "question"},
        {"answer": "existing"},
        suite=None,
    )

    payload = json.loads(prompt)
    assert payload["required_output_schema"]["score"] == "number, weighted score from 0 to 100"


def test_run_evaluator_source_cli_rejects_unsupported_source_kind(tmp_path: Path) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported source kind"):
        run_evaluator_source_cli(
            input=str(input_path),
            kind="task",
            judge_agent=str(judge_agent),
        )


def test_run_evaluator_source_cli_passes_source_fields_to_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"existing"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    events: list[tuple[str, dict]] = []

    class CaptureHook:
        def __init__(self, hook_point: str):
            self.hook_point = hook_point

        async def run(self, *, event, state):
            events.append((self.hook_point, dict(event)))
            return {"metadata": {"hook_tag": "source-hook"}}

    async def fake_run_evaluation_flow(flow):
        assert flow.target["hook_tag"] == "source-hook"
        return {
            "report_version": 1,
            "suite_id": "source-evaluator",
            "summary": {"source-evaluator": {"score": {"mean": 0.9}}},
            "metrics": {"score": {"mean": 0.9}},
            "results": [],
            "result_counts": {"cases_total": 0, "cases_with_metrics": 0, "cases_with_judge": 0},
            "approval": {"required": False, "resolved": False, "approved": None},
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_evaluator_hooks",
        lambda: {
            "evaluator.pre_run": (CaptureHook("pre"),),
            "evaluator.post_run": (CaptureHook("post"),),
        },
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    run_evaluator_source_cli(
        input=str(input_path),
        kind="task-answer",
        judge_agent=str(judge_agent),
        task_id="case-1",
        output=str(tmp_path / "report.json"),
    )

    assert events[0][0] == "pre"
    assert events[0][1]["mode"] == "source"
    assert events[0][1]["input"] == str(input_path.resolve())
    assert events[0][1]["kind"] == "task-answer"
    assert events[0][1]["task_id"] == "case-1"
    assert events[0][1]["judge_agent"] == str(judge_agent.resolve())
    assert events[1][0] == "post"
    assert events[1][1]["mode"] == "source"
    assert events[1][1]["report"]["source_selection"]["kind"] == "task-answer"


def test_run_evaluator_source_cli_persists_schema_valid_source_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"existing"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "report_format": {"id": "aworld.evaluator.report", "version": 1},
            "generated_at": "2026-06-10T00:00:00Z",
            "suite_id": "source-evaluator",
            "target": flow.target,
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"source-evaluator": {"score": {"mean": 88.0}}},
            "metrics": {"score": {"mean": 88.0}},
            "results": [
                {
                    "case_id": "case-1",
                    "input": {"input": "question"},
                    "metrics": {"score": {"value": 88.0, "status": "PASSED"}},
                    "judge": {"score": 88.0, "verdict": "Pass"},
                    "judge_backend": {"backend_id": "source-agent-md"},
                    "state_summary": {"answer": "existing"},
                }
            ],
            "result_counts": {"cases_total": 1, "cases_with_metrics": 1, "cases_with_judge": 1},
            "gate": {"status": "pass", "metric_name": "score", "value": 88.0},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="task-answer",
        judge_agent=str(judge_agent),
        output=str(tmp_path / "report.json"),
    )

    validate_evaluator_report(report)


@pytest.mark.asyncio
async def test_framework_run_evaluation_flow_returns_report_object() -> None:
    async def fake_judge(case_input, target):
        return {"score": 0.9}

    flow = substrate_module.EvaluationFlowDef(
        target={"kind": "file", "target_path": "artifact.txt"},
        suite=substrate_module.EvalSuiteDef(
            suite_id="app-evaluator",
            cases=[substrate_module.EvalCaseDef(case_id="case-1", input={"query": "demo"})],
            gate_policy=substrate_module.GatePolicyDef(metric_name="score", pass_threshold=0.0),
            judge=fake_judge,
        ),
    )

    report = await substrate_module.run_evaluation_flow(flow)

    assert isinstance(report, EvaluatorReport)
    assert report["suite_id"] == "app-evaluator"


def test_run_evaluator_cli_writes_default_report_when_output_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    report_path = Path(report["report_path"])
    persisted = json.loads(report_path.read_text(encoding="utf-8"))

    assert report_path.exists()
    assert report_path.parent == tmp_path / ".aworld" / "evaluations"
    assert persisted["suite_id"] == "app-evaluator"


def test_available_evaluator_suites_lists_builtin_suite() -> None:
    suites = available_evaluator_suites()

    assert "app-evaluator" in suites


def test_cli_schema_helpers_delegate_to_framework_sources() -> None:
    assert get_declared_evaluator_suite_schema() == get_declared_eval_suite_schema()
    assert get_evaluator_report_schema()["title"] == "AWorld Evaluator Report"


def test_available_evaluator_suites_filters_by_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.png"
    target.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="
        )
    )

    suites = available_evaluator_suites(target=str(target))

    assert suites == ["app-evaluator"]


def test_available_evaluator_suites_loads_declared_suites_from_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    (manifest_dir / "strict-ui.json").write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"]
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    suites = available_evaluator_suites(target=str(target))

    assert "strict-ui" in suites


def test_available_evaluator_suites_uses_target_workspace_not_process_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    manifest_dir = workspace / ".aworld" / "evaluators"
    manifest_dir.mkdir(parents=True)
    target = workspace / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    (manifest_dir / "strict-ui.json").write_text(
        """
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file"]
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    suites = available_evaluator_suites(target=str(target))

    assert "strict-ui" in suites


def test_run_evaluator_cli_marks_image_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.png"
    target.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="
        )
    )

    async def fake_run_evaluation_flow(flow):
        assert flow.target["target_kind"] == "image"
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    assert report["suite_id"] == "app-evaluator"


def test_run_evaluator_cli_records_suite_selection_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    assert report["suite_selection"]["mode"] == "auto"
    assert report["suite_selection"]["resolved"] == "app-evaluator"


def test_run_evaluator_cli_adds_automation_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.7}}},
            "metrics": {"score": {"mean": 0.7}},
            "result_counts": {"cases_total": 2, "cases_with_metrics": 2, "cases_with_judge": 2},
            "results": [{}, {}],
            "gate": {"status": "needs_approval", "metric_name": "score", "value": 0.7},
            "approval": {"required": True, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    assert report["automation"]["gate_status"] == "needs_approval"
    assert report["automation"]["case_count"] == 2
    assert report["automation"]["judge_backend"] == "stub-agent"
    assert report["automation"]["suggested_exit_code"] == 3


def test_run_evaluator_cli_rejects_missing_target(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        run_evaluator_cli(target=str(missing))


def test_evaluator_exit_code_matches_gate_and_approval() -> None:
    assert evaluator_exit_code({"gate": {"status": "pass"}, "approval": {}}) == 0
    assert evaluator_exit_code({"gate": {"status": "fail"}, "approval": {}}) == 2
    assert evaluator_exit_code(
        {"gate": {"status": "needs_approval"}, "approval": {"approved": False}}
    ) == 3


def test_get_evaluator_report_schema_describes_report_contract() -> None:
    schema = get_evaluator_report_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "AWorld Evaluator Report"
    assert "report_format" in schema["required"]
    assert schema["properties"]["report_format"]["properties"]["id"]["const"] == "aworld.evaluator.report"
    assert schema["properties"]["report_format"]["properties"]["version"]["const"] == 1
    assert schema["properties"]["metrics"]["additionalProperties"]["$ref"] == "#/$defs/metricAggregate"
    assert (
        schema["properties"]["results"]["items"]["properties"]["metrics"]["additionalProperties"]["$ref"]
        == "#/$defs/caseMetric"
    )
    assert schema["properties"]["gate"]["$ref"] == "#/$defs/gateDecision"
    assert schema["properties"]["automation"]["$ref"] == "#/$defs/automationSummary"
    assert schema["$defs"]["gateDecision"]["properties"]["status"]["enum"] == ["pass", "fail", "needs_approval"]
    assert schema["$defs"]["automationSummary"]["properties"]["suggested_exit_code"]["enum"] == [0, 2, 3]
    assert schema["$defs"]["automationSummary"]["required"] == [
        "gate_status",
        "metric_name",
        "metric_value",
        "approval_required",
        "approval_resolved",
        "approved",
        "suggested_exit_code",
        "case_count",
        "judge_backend",
    ]


def test_validate_evaluator_report_accepts_valid_report() -> None:
    report = {
        "report_version": 1,
        "report_format": {"id": "aworld.evaluator.report", "version": 1},
        "generated_at": "2026-06-02T04:00:00Z",
        "suite_id": "app-evaluator",
        "target": {"target_path": "/tmp/artifact.txt", "target_kind": "file"},
        "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
        "metrics": {"score": {"mean": 0.9, "min": 0.9, "max": 0.9, "std": 0.0, "eval_status": "PASSED"}},
        "results": [
            {
                "case_id": "artifact.txt",
                "input": {"target_path": "/tmp/artifact.txt"},
                "metrics": {"score": {"value": 0.9, "status": "PASSED"}},
                "judge": {"score": 0.9},
                "judge_backend": {"backend_id": "stub-agent"},
            }
        ],
        "result_counts": {"cases_total": 1, "cases_with_metrics": 1, "cases_with_judge": 1},
        "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
        "approval": {"required": False, "resolved": False, "approved": None},
        "automation": {
            "gate_status": "pass",
            "metric_name": "score",
            "metric_value": 0.9,
            "approval_required": False,
            "approval_resolved": False,
            "approved": None,
            "suggested_exit_code": 0,
            "case_count": 1,
            "judge_backend": "stub-agent",
        },
    }

    validate_evaluator_report(report)


def test_validate_and_render_categorical_gate_report() -> None:
    report = {
        "report_version": 1,
        "report_format": {"id": "aworld.evaluator.report", "version": 1},
        "generated_at": "2026-06-02T04:00:00Z",
        "suite_id": "categorical-suite",
        "target": {"target_path": "/tmp/artifact.txt", "target_kind": "file"},
        "summary": {"categorical-suite": {"verdict": {"value": "approved"}}},
        "metrics": {"verdict": {"value": "approved", "eval_status": "PASSED"}},
        "results": [
            {
                "case_id": "artifact.txt",
                "input": {"target_path": "/tmp/artifact.txt"},
                "metrics": {"verdict": {"value": "approved", "status": "PASSED"}},
                "judge": {"score": 1.0, "verdict": "approved"},
            }
        ],
        "result_counts": {"cases_total": 1, "cases_with_metrics": 1, "cases_with_judge": 1},
        "gate": {"status": "pass", "metric_name": "verdict", "value": "approved"},
        "approval": {"required": False, "resolved": False, "approved": None},
        "automation": {
            "gate_status": "pass",
            "metric_name": "verdict",
            "metric_value": "approved",
            "approval_required": False,
            "approval_resolved": False,
            "approved": None,
            "suggested_exit_code": 0,
            "case_count": 1,
            "judge_backend": None,
        },
    }

    validate_evaluator_report(report)

    assert "approved" in render_evaluator_summary(report)


def test_validate_evaluator_report_rejects_invalid_gate_status() -> None:
    report = {
        "report_version": 1,
        "report_format": {"id": "aworld.evaluator.report", "version": 1},
        "generated_at": "2026-06-02T04:00:00Z",
        "suite_id": "app-evaluator",
        "target": {"target_path": "/tmp/artifact.txt", "target_kind": "file"},
        "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
        "metrics": {"score": {"mean": 0.9}},
        "results": [],
        "result_counts": {"cases_total": 0, "cases_with_metrics": 0, "cases_with_judge": 0},
        "gate": {"status": "maybe", "metric_name": "score", "value": 0.9},
        "approval": {"required": False, "resolved": False, "approved": None},
        "automation": {
            "gate_status": "maybe",
            "metric_name": "score",
            "metric_value": 0.9,
            "approval_required": False,
            "approval_resolved": False,
            "approved": None,
            "suggested_exit_code": 0,
            "case_count": 0,
            "judge_backend": None,
        },
    }

    with pytest.raises(ValueError, match="status"):
        validate_evaluator_report(report)


def test_get_declared_evaluator_suite_schema_describes_manifest_contract() -> None:
    schema = get_declared_evaluator_suite_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "AWorld Declared Evaluator Suite"
    assert schema["properties"]["base_suite"]["const"] == "app-evaluator"
    assert "suite_id" in schema["required"]
    assert "target_kinds" in schema["properties"]
