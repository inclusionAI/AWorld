from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.evaluations.sources import AWorldTrajectoryLogSource, create_source_eval_suite
from aworld.evaluations.substrate import (
    AgentJudgeBackend,
    GateMetricCondition,
    GatePolicyDef,
    StateCheckGrader,
    EvaluationFlowDef,
    EvalSuiteDef,
    load_agent_markdown,
    run_evaluation_flow,
)
from aworld.evaluations.report import validate_evaluator_report
from aworld.evaluations.trajectory_judge import TrajectoryJudgeSchema
from aworld_cli.evaluator_runtime import run_evaluator_source_cli


DEFAULT_JUDGE_TIMEOUT_SECONDS = 600.0


class _FakePytestConfig:
    def __init__(self, values: Mapping[str, Any]):
        self._values = values

    def getoption(self, name: str) -> Any:
        return self._values.get(name)


def _manual_replay_config(pytest_config: Any) -> dict[str, Any]:
    required_options = {
        "--task-id": pytest_config.getoption("trajectory_task_id"),
        "--trajectory-log": pytest_config.getoption("trajectory_log"),
        "--agent-prompt": pytest_config.getoption("trajectory_agent_prompt"),
        "--out-dir": pytest_config.getoption("trajectory_out_dir"),
    }
    missing = [name for name, value in required_options.items() if not value]
    if missing:
        raise pytest.UsageError(
            "manual trajectory replay requires explicit pytest options: "
            + ", ".join(missing)
        )

    task_id = required_options["--task-id"]
    log_path = Path(str(required_options["--trajectory-log"])).expanduser()
    agent_prompt_path = Path(str(required_options["--agent-prompt"]))
    out_dir = Path(str(required_options["--out-dir"]))
    judge_timeout_seconds = pytest_config.getoption("trajectory_judge_timeout") or DEFAULT_JUDGE_TIMEOUT_SECONDS
    return {
        "task_id": str(task_id),
        "log_path": log_path,
        "agent_prompt_path": agent_prompt_path,
        "out_dir": out_dir,
        "judge_timeout_seconds": float(judge_timeout_seconds),
    }


def test_manual_replay_config_requires_explicit_pytest_options(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWORLD_TRAJECTORY_TASK_ID", "task_from_env")
    monkeypatch.setenv("AWORLD_TRAJECTORY_LOG", "~/env/trajectory.log")
    monkeypatch.setenv("AWORLD_TRAJECTORY_AGENT_PROMPT", "env/agent.md")
    monkeypatch.setenv("AWORLD_TRAJECTORY_OUT_DIR", "env/reports")

    with pytest.raises(pytest.UsageError, match="--task-id"):
        _manual_replay_config(_FakePytestConfig({}))

    config = _manual_replay_config(
        _FakePytestConfig(
            {
                "trajectory_task_id": "task_from_cli",
                "trajectory_log": "~/cli/trajectory.log",
                "trajectory_agent_prompt": "cli/agent.md",
                "trajectory_out_dir": "cli/reports",
                "trajectory_judge_timeout": 12.5,
            }
        )
    )

    assert config["task_id"] == "task_from_cli"
    assert config["log_path"] == Path("~/cli/trajectory.log").expanduser()
    assert config["agent_prompt_path"] == Path("cli/agent.md")
    assert config["out_dir"] == Path("cli/reports")
    assert config["judge_timeout_seconds"] == 12.5


@pytest.mark.asyncio
async def test_agent_markdown_loads_as_aworld_agent_via_existing_skill_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    agent_md = tmp_path / "agent.md"
    agent_md.write_text(
        "---\n"
        "name: custom trajectory judge\n"
        "description: Evaluates trajectories\n"
        "tools: Bash, Read\n"
        "model: opus\n"
        "---\n\n"
        "# Judge Contract\n"
        "Return strict JSON.\n",
        encoding="utf-8",
    )

    agent = await load_agent_markdown(agent_md, agent_id="custom-judge")

    assert agent.name() == "custom-judge"
    assert agent.desc() == "Evaluates trajectories"
    assert agent.mcp_servers == []
    assert "Return strict JSON." in agent.system_prompt


@pytest.mark.asyncio
async def test_markdown_agent_judge_backend_runs_loaded_agent_with_runners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    agent_md = tmp_path / "agent.md"
    agent_md.write_text(
        "---\n"
        "name: trajectory judge\n"
        "description: Test judge\n"
        "---\n\n"
        "You are the test judge.\n",
        encoding="utf-8",
    )
    calls: dict[str, Any] = {}

    class _FakeTaskResponse:
        answer = json.dumps(
            {
                "weighted_score": 88,
                "verdict": "Pass",
                "dimensions": {"A1_groundedness": {"score": 4}},
            }
        )

    async def fake_run(input: str, agent: Any, **kwargs: Any) -> _FakeTaskResponse:
        calls["input"] = input
        calls["agent_name"] = agent.name()
        calls["system_prompt"] = agent.system_prompt
        return _FakeTaskResponse()

    monkeypatch.setattr("aworld.runner.Runners.run", fake_run)

    backend = AgentJudgeBackend.from_agent_markdown(
        agent_md,
        backend_id="trajectory-evaluator-agent-md",
        prompt_builder=lambda case_input, target, suite: "judge this trajectory",
    )
    execution = await backend.execute({}, {}, object())

    assert calls == {
        "input": "judge this trajectory",
        "agent_name": "trajectory-evaluator-agent-md",
        "system_prompt": "You are the test judge.",
    }
    assert execution.backend_id == "trajectory-evaluator-agent-md"
    assert execution.payload["weighted_score"] == 88
    assert execution.payload["dimensions"]["A1_groundedness"]["score"] == 4


@pytest.mark.asyncio
async def test_trajectory_log_source_default_adapter_populates_tool_calls_and_standard_metrics(tmp_path: Path):
    task_id = "task_with_tool"
    trajectory = [
        {
            "state": {
                "input": {"content": "question"},
                "messages": [{"role": "system", "content": "system"}],
            },
            "meta": {"step": 1, "pre_agent": "user", "agent_id": "agent"},
            "action": {
                "tool_calls": [
                    {"function": {"name": "search", "arguments": "{}"}},
                    {"function": {"name": "open", "arguments": "{\"url\":\"https://example.com\"}"}},
                ],
                "is_agent_finished": "False",
            },
        },
        {
            "state": {
                "messages": [
                    {"role": "tool", "content": "search result"},
                    {"role": "tool", "content": "page text"},
                ],
            },
            "meta": {"step": 2, "pre_agent": "agent", "agent_id": "agent"},
            "action": {"content": "final", "is_agent_finished": "True"},
        },
    ]
    log_path = tmp_path / "trajectory.log"
    log_path.write_text(
        repr({"task_id": task_id, "is_sub_task": False, "trajectory": json.dumps(trajectory)})
        + "\n",
        encoding="utf-8",
    )
    source = AWorldTrajectoryLogSource(path=log_path, task_ids=[task_id], extraction_dir=tmp_path)
    record = next(iter(source.iter_records()))
    case = source.to_cases()[0]

    state = source.default_adapter().adapt(record=record, case=case, target={})

    assert [call["name"] for call in state.tool_calls] == ["search", "open"]
    assert state.usage == {"total_tokens": 0}
    assert state.timing == {"duration_ms": 0}
    assert state.standard_metrics["n_turns"] == 2
    assert state.standard_metrics["n_tool_calls"] == 2


def test_trajectory_step_assertion_uses_extracted_num_steps(tmp_path: Path):
    extracted_path = tmp_path / "extracted_task.json"
    extracted_path.write_text(json.dumps({"num_steps": 81}), encoding="utf-8")
    result = {
        "state_summary": {"trajectory_steps": 81},
        "metadata": {"extracted_path": str(extracted_path)},
    }

    _assert_report_trajectory_steps_match_extracted(result)


def test_source_cli_report_assertion_matches_manual_trajectory_goal(tmp_path: Path):
    task_id = "task_for_cli_assertion"
    log_path = tmp_path / "trajectory.log"
    agent_prompt_path = tmp_path / "agent.md"
    report_path = tmp_path / "report.json"
    extracted_path = tmp_path / f"extracted_{task_id}.json"
    log_path.write_text("log", encoding="utf-8")
    agent_prompt_path.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    extracted_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "num_steps": 2,
                "final_answer": "final",
                "evidence": [{"content": "tool result"}],
            }
        ),
        encoding="utf-8",
    )
    report = {
        "report_version": 1,
        "report_format": {"id": "aworld.evaluator.report", "version": 1},
        "generated_at": "2026-06-10T00:00:00Z",
        "suite_id": "trajectory-source-evaluator",
        "target": {"target_kind": "source", "target_path": str(log_path)},
        "judge_backend": {"backend_id": "trajectory-evaluator-agent-md"},
        "summary": {"trajectory-source-evaluator": {"score": {"mean": 64.0}}},
        "metrics": {
            "score": {"mean": 64.0},
            "has_evidence": {"mean": 1.0},
            "agent_finished": {"mean": 1.0},
        },
        "results": [
            {
                "case_id": task_id,
                "input": {"task_id": task_id, "trajectory_log": str(log_path)},
                "metrics": {
                    "score": {"value": 64.0, "status": "PASSED"},
                    "has_evidence": {"value": True, "status": "PASSED"},
                    "agent_finished": {"value": True, "status": "PASSED"},
                },
                "judge": {"score": 64.0, "verdict": "Marginal", "A1_groundedness": 3},
                "judge_backend": {"backend_id": "trajectory-evaluator-agent-md"},
                "state_summary": {"answer": "final", "trajectory_steps": 2},
                "metadata": {"extracted_path": str(extracted_path)},
            }
        ],
        "result_counts": {"cases_total": 1, "cases_with_metrics": 1, "cases_with_judge": 1},
        "gate": {"status": "fail", "metric_name": None, "value": None},
        "approval": {"required": False, "resolved": False, "approved": None},
        "automation": {
            "gate_status": "fail",
            "metric_name": None,
            "metric_value": None,
            "approval_required": False,
            "approval_resolved": False,
            "approved": None,
            "suggested_exit_code": 2,
            "case_count": 1,
            "judge_backend": "trajectory-evaluator-agent-md",
            "source_kind": "trajectory",
            "source_input": str(log_path),
            "task_id": task_id,
        },
        "source_selection": {
            "mode": "source",
            "input": str(log_path),
            "kind": "trajectory",
            "task_id": task_id,
            "judge_agent": str(agent_prompt_path),
        },
        "report_path": str(report_path),
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    _assert_source_cli_trajectory_report_matches_manual_goal(
        report,
        task_id=task_id,
        log_path=log_path,
        agent_prompt_path=agent_prompt_path,
    )


def _assert_report_trajectory_steps_match_extracted(result: Mapping[str, Any]) -> None:
    extracted_path = Path(str(result["metadata"]["extracted_path"]))
    extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
    assert result["state_summary"]["trajectory_steps"] == extracted["num_steps"]


def _assert_source_cli_trajectory_report_matches_manual_goal(
    report: Mapping[str, Any],
    *,
    task_id: str,
    log_path: Path,
    agent_prompt_path: Path,
) -> None:
    validate_evaluator_report(dict(report))
    report_path = Path(str(report["report_path"]))
    assert report_path.exists()
    assert report["suite_id"] == "trajectory-source-evaluator"
    assert report["gate"]["status"] in {"pass", "fail", "needs_approval"}
    assert report["metrics"]["has_evidence"]["mean"] == 1.0
    assert report["metrics"]["agent_finished"]["mean"] == 1.0
    assert report["judge_backend"]["backend_id"] == "trajectory-evaluator-agent-md"

    source_selection = report["source_selection"]
    assert source_selection["mode"] == "source"
    assert source_selection["kind"] == "trajectory"
    assert source_selection["task_id"] == task_id
    assert Path(str(source_selection["input"])).resolve() == log_path.resolve()
    assert Path(str(source_selection["judge_agent"])).resolve() == agent_prompt_path.resolve()

    automation = report["automation"]
    assert automation["source_kind"] == "trajectory"
    assert automation["task_id"] == task_id
    assert Path(str(automation["source_input"])).resolve() == log_path.resolve()

    result = report["results"][0]
    assert result["case_id"] == task_id
    assert result["judge"]["verdict"] in {"Excellent", "Pass", "Marginal", "Fail"}
    assert 0 <= result["judge"]["score"] <= 100
    assert result["state_summary"]["answer"]
    assert Path(result["metadata"]["extracted_path"]).exists()
    _assert_report_trajectory_steps_match_extracted(result)

    extracted = json.loads(Path(result["metadata"]["extracted_path"]).read_text(encoding="utf-8"))
    assert extracted["task_id"] == task_id
    assert extracted["final_answer"]
    assert extracted["evidence"]


def _trajectory_judge_prompt(case_input: dict[str, Any], target: dict[str, Any], suite: EvalSuiteDef) -> str:
    outcome = (target.get("artifacts") or {}).get("outcome") or {}
    extracted_path = outcome.get("extracted_path")
    extracted_payload: dict[str, Any] = {}
    if extracted_path:
        extracted_payload = json.loads(Path(str(extracted_path)).read_text(encoding="utf-8"))

    payload = {
        "case": {
            "task_id": case_input["task_id"],
            "trajectory_log": case_input["trajectory_log"],
        },
        "extracted_trajectory": extracted_payload,
        "required_output_schema": {
            "score": "number, weighted score from 0 to 100",
            "verdict": "Excellent|Pass|Marginal|Fail",
            "A1_groundedness": "integer 1-5",
            "A2_completeness": "integer 1-5",
            "A3_relevance": "integer 1-5",
            "A4_readability": "integer 1-5",
            "B1_tool_use": "integer 1-5",
            "B2_efficiency": "integer 1-5",
            "B3_compliance": "integer 1-5",
            "B4_robustness": "integer 1-5",
            "veto_triggered": "boolean",
        },
        "instruction": (
            "Apply the trajectory-evaluator agent contract to the extracted trajectory. "
            "Do not call tools and do not re-read the raw log; all required evidence is in extracted_trajectory. "
            "Return exactly one JSON object matching required_output_schema, with no markdown."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@pytest.mark.asyncio
async def test_manual_trajectory_log_case_runs_end_to_end_for_human_replay(request: pytest.FixtureRequest):
    try:
        config = _manual_replay_config(request.config)
    except pytest.UsageError as exc:
        pytest.skip(str(exc))
    task_id = config["task_id"]
    log_path = config["log_path"]
    agent_prompt_path = config["agent_prompt_path"]
    out_dir = config["out_dir"]
    judge_timeout_seconds = config["judge_timeout_seconds"]

    if not log_path.exists():
        pytest.skip(f"manual trajectory log not found: {log_path}")
    if not agent_prompt_path.exists():
        pytest.skip(f"manual trajectory evaluator agent prompt not found: {agent_prompt_path}")
    if not os.getenv("LLM_MODEL_NAME") or not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
        pytest.skip("real trajectory judge requires LLM_MODEL_NAME and LLM_API_KEY/OPENAI_API_KEY")

    suite = create_source_eval_suite(
        suite_id="trajectory-log-manual-replay",
        source=AWorldTrajectoryLogSource(
            path=log_path,
            task_ids=[task_id],
            extraction_dir=out_dir,
        ),
        judge_schema=TrajectoryJudgeSchema.default(),
        judge_backend=AgentJudgeBackend.from_agent_markdown(
            agent_prompt_path,
            backend_id="trajectory-evaluator-agent-md",
            prompt_builder=_trajectory_judge_prompt,
            timeout_seconds=judge_timeout_seconds,
        ),
        outcome_scorers=(
            StateCheckGrader(
                metric_name="has_evidence",
                source="outcome",
                path=("evidence_blocks",),
                op=">",
                expected=0,
            ),
            StateCheckGrader(
                metric_name="agent_finished",
                source="outcome",
                path=("is_finished",),
                op="==",
                expected=True,
            ),
        ),
        gate_policy=GatePolicyDef(
            pass_all=(
                GateMetricCondition(metric_name="score", op=">=", threshold=70.0),
                GateMetricCondition(metric_name="A1_groundedness", op=">=", threshold=3),
                GateMetricCondition(metric_name="has_evidence", op="==", threshold=1.0),
                GateMetricCondition(metric_name="agent_finished", op="==", threshold=1.0),
            )
        ),
        metadata={
            "manual_replay": True,
            "judge_agent_prompt": str(agent_prompt_path),
            "trajectory_log": str(log_path),
        },
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(
            target={"kind": "inline", "value": {"target_path": str(log_path), "target_kind": "trajectory_log"}},
            suite=suite,
        )
    )

    report_dict = report.to_dict()
    validate_evaluator_report(report_dict)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"evaluator_report_{task_id}.json"
    report_path.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    assert report["gate"]["status"] in {"pass", "fail", "needs_approval"}
    assert report["metrics"]["has_evidence"]["mean"] == 1.0
    assert report["metrics"]["agent_finished"]["mean"] == 1.0
    assert report["judge_backend"]["backend_id"] == "trajectory-evaluator-agent-md"
    assert report["results"][0]["judge"]["verdict"] in {"Excellent", "Pass", "Marginal", "Fail"}
    assert 0 <= report["results"][0]["judge"]["score"] <= 100
    assert report["results"][0]["state_summary"]["answer"]
    assert Path(report["results"][0]["metadata"]["extracted_path"]).exists()
    _assert_report_trajectory_steps_match_extracted(report["results"][0])
    assert report_path.exists()


def test_manual_trajectory_log_case_runs_via_source_cli_for_human_replay(request: pytest.FixtureRequest):
    try:
        config = _manual_replay_config(request.config)
    except pytest.UsageError as exc:
        pytest.skip(str(exc))
    task_id = config["task_id"]
    log_path = config["log_path"]
    agent_prompt_path = config["agent_prompt_path"]
    out_dir = config["out_dir"]

    if not log_path.exists():
        pytest.skip(f"manual trajectory log not found: {log_path}")
    if not agent_prompt_path.exists():
        pytest.skip(f"manual trajectory evaluator agent prompt not found: {agent_prompt_path}")
    if not os.getenv("LLM_MODEL_NAME") or not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
        pytest.skip("real trajectory judge requires LLM_MODEL_NAME and LLM_API_KEY/OPENAI_API_KEY")

    report = run_evaluator_source_cli(
        input=str(log_path),
        kind="trajectory",
        task_id=task_id,
        judge_agent=str(agent_prompt_path),
        out_dir=str(out_dir),
    )

    _assert_source_cli_trajectory_report_matches_manual_goal(
        report,
        task_id=task_id,
        log_path=log_path,
        agent_prompt_path=agent_prompt_path,
    )
