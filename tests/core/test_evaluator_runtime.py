from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

import aworld.evaluations.substrate as substrate_module
from aworld.evaluations.manifests import get_declared_eval_suite_schema
from aworld.evaluations.report import EvaluatorReport
from aworld_cli.evaluator_runtime import (
    _CliAgentRuntimeHarness,
    _build_source_suite,
    _build_source_prompt,
    _build_trajectory_prompt,
    available_evaluator_suites,
    evaluator_exit_code,
    get_declared_evaluator_suite_schema,
    get_evaluator_report_schema,
    run_evaluator_cli,
    run_evaluator_source_cli,
    validate_evaluator_report,
)
from aworld_cli.evaluator_rendering import render_evaluator_summary


def _write_answer_source(path: Path) -> None:
    path.write_text('{"id":"case-1","input":"question","answer":"existing"}\n', encoding="utf-8")


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
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    output = tmp_path / "report.json"
    captured = {}

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent=str(judge_agent),
        output=str(output),
        judge_timeout_seconds=12.5,
    )

    flow = captured["flow"]
    assert flow.target["target_kind"] == "source"
    assert flow.target["source_kind"] == "answer"
    assert flow.suite.cases[0].case_id == "case-1"
    assert flow.suite.cases[0].input == {"input": "question"}
    assert flow.suite.judge_backend.backend_id == "source-agent-md"
    assert flow.suite.judge_backend.timeout_seconds == 12.5
    assert report["source_selection"]["kind"] == "answer"
    assert report["source_selection"]["judge_timeout_seconds"] == 12.5
    assert report["automation"]["source_kind"] == "answer"
    assert output.exists()


def test_source_file_judge_agent_uses_direct_instruction_backend(tmp_path: Path) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text(
        "---\nname: judge\n---\nReturn JSON only.\n",
        encoding="utf-8",
    )

    suite = _build_source_suite(
        kind="answer",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    assert suite.judge_backend.backend_id == "source-agent-md"
    assert suite.judge_backend.executor is None
    assert "Return JSON only." in suite.judge_backend.system_prompt
    assert "Agent loaded from" not in suite.judge_backend.system_prompt


def test_run_evaluator_source_cli_supports_cli_judge_agent_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    captured = {}

    class FakeExecutor:
        async def chat(self, prompt):
            captured["prompt"] = prompt
            return '{"score": 91, "verdict": "Pass", "veto_triggered": false}'

    async def fake_load_cli_agent_executor(agent_name):
        captured["agent_name"] = agent_name
        return FakeExecutor()

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_cli_agent_executor",
        fake_load_cli_agent_executor,
    )

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        execution = await flow.suite.judge_backend.execute(
            flow.suite.cases[0].input,
            {"answer": "existing"},
            flow.suite,
        )
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "judge_backend": {"backend_id": execution.backend_id},
            "summary": {"answer-source-evaluator": {"score": {"mean": execution.payload["score"]}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": execution.payload["score"]},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_agent_name="JudgeTeam",
        output=str(tmp_path / "report.json"),
    )

    assert captured["agent_name"] == "JudgeTeam"
    assert captured["flow"].suite.judge_backend.backend_id == "source-agent:JudgeTeam"
    assert report["judge_backend"]["backend_id"] == "source-agent:JudgeTeam"
    assert report["source_selection"]["judge_agent_name"] == "JudgeTeam"
    assert report["source_selection"]["judge_agent"] is None


def test_run_evaluator_source_cli_supports_judge_backend_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    module_path = tmp_path / "custom_judge.py"
    module_path.write_text(
        "\n".join(
            [
                "from aworld.evaluations.substrate import CallableJudgeBackend",
                "",
                "async def judge(case_input, target):",
                "    return {'score': 82, 'verdict': 'Pass', 'veto_triggered': False}",
                "",
                "def build_backend():",
                "    return CallableJudgeBackend(backend_id='custom-backend', judge=judge)",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    captured = {}

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        execution = await flow.suite.judge_backend.execute(
            flow.suite.cases[0].input,
            {"answer": "existing"},
            flow.suite,
        )
        return {
            "report_version": 1,
            "suite_id": "answer-source-evaluator",
            "judge_backend": {"backend_id": execution.backend_id},
            "summary": {"answer-source-evaluator": {"score": {"mean": execution.payload["score"]}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": execution.payload["score"]},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="answer",
        judge_backend_ref="custom_judge:build_backend",
        output=str(tmp_path / "report.json"),
    )

    assert captured["flow"].suite.judge_backend.backend_id == "custom-backend"
    assert report["judge_backend"]["backend_id"] == "custom-backend"
    assert report["source_selection"]["judge_backend_ref"] == "custom_judge:build_backend"


def test_run_evaluator_source_cli_rejects_missing_judge_selector(tmp_path: Path) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)

    with pytest.raises(ValueError, match="exactly one judge selector"):
        run_evaluator_source_cli(
            input=str(input_path),
            kind="answer",
            output=str(tmp_path / "report.json"),
        )


def test_run_evaluator_source_cli_rejects_multiple_judge_selectors(tmp_path: Path) -> None:
    input_path = tmp_path / "answers.jsonl"
    _write_answer_source(input_path)
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one judge selector"):
        run_evaluator_source_cli(
            input=str(input_path),
            kind="answer",
            judge_agent=str(judge_agent),
            judge_agent_name="JudgeTeam",
            output=str(tmp_path / "report.json"),
        )


def test_run_evaluator_source_cli_builds_task_flow_with_default_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    captured = {}

    class FakeHarness:
        pass

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        captured["agent_name"] = agent_name
        return FakeHarness()

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        return {
            "report_version": 1,
            "suite_id": "task-source-evaluator",
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"task-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="task",
        judge_agent=str(judge_agent),
        output=str(tmp_path / "report.json"),
    )

    flow = captured["flow"]
    assert captured["agent_name"] == "Aworld"
    assert flow.target["source_kind"] == "task"
    assert flow.target["agent"] == "Aworld"
    assert flow.suite.cases[0].case_id == "case-1"
    assert flow.suite.cases[0].input == {"input": "question"}
    assert flow.suite.runtime_harness is not None
    assert report["source_selection"]["kind"] == "task"
    assert report["source_selection"]["agent"] == "Aworld"
    assert report["automation"]["source_kind"] == "task"


def test_task_source_gate_consumes_answer_veto_signal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    class FakeHarness:
        pass

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        lambda *, agent_name: FakeHarness(),
    )

    suite = _build_source_suite(
        kind="task",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    payload = suite.judge_schema.validate_payload(
        {"score": 95.0, "verdict": "Excellent", "veto_triggered": True}
    )
    assert payload["veto_triggered"] is True
    pass_conditions = suite.gate_policy.normalized_conditions()[0]
    assert any(
        condition.metric_name == "veto_triggered"
        and condition.op == "=="
        and condition.threshold is False
        for condition in pass_conditions
    )
    decision = suite.gate_policy.evaluate({"score": 95.0, "veto_triggered": True})
    assert decision.status == "fail"
    assert any(condition["metric_name"] == "veto_triggered" for condition in decision.failed_conditions)


def test_run_evaluator_source_cli_builds_generated_trajectory_flow_with_default_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    captured = {}

    class FakeHarness:
        pass

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        captured["agent_name"] = agent_name
        return FakeHarness()

    async def fake_run_evaluation_flow(flow):
        captured["flow"] = flow
        return {
            "report_version": 1,
            "suite_id": "trajectory-source-evaluator",
            "judge_backend": {"backend_id": "trajectory-evaluator-agent-md"},
            "summary": {"trajectory-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_source_cli(
        input=str(input_path),
        kind="trajectory",
        judge_agent=str(judge_agent),
        output=str(tmp_path / "report.json"),
    )

    flow = captured["flow"]
    assert captured["agent_name"] == "Aworld"
    assert flow.target["source_kind"] == "trajectory"
    assert flow.target["agent"] == "Aworld"
    assert flow.suite.cases[0].case_id == "case-1"
    assert flow.suite.cases[0].input == {"input": "question"}
    assert report["source_selection"]["kind"] == "trajectory"
    assert report["source_selection"]["agent"] == "Aworld"


@pytest.mark.asyncio
async def test_cli_agent_runtime_harness_returns_rollout_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeExecutor:
        async def chat(self, query):
            return f"answer for {query}"

    async def fake_load_cli_agent_executor(agent_name):
        assert agent_name == "Aworld"
        return FakeExecutor()

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_cli_agent_executor",
        fake_load_cli_agent_executor,
    )

    case = SimpleNamespace(
        case_id="case-1",
        input={"input": "question"},
        metadata={
            "source_record": {
                "metadata": {"source_kind": "task", "source_path": "tasks.jsonl"},
            },
        },
    )
    state = await _CliAgentRuntimeHarness(agent_name="Aworld").run_rollout(
        case=case,
        target={"source_kind": "task"},
    )

    assert state.status == "success"
    assert state.answer == "answer for question"
    assert state.outcome["has_answer"] is True
    assert state.metadata["agent"] == "Aworld"
    assert state.metadata["source_kind"] == "task"
    assert state.standard_metrics["n_turns"] == 2


@pytest.mark.asyncio
async def test_cli_agent_runtime_harness_prefers_swarm_task_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSwarm:
        pass

    class FakeExecutor:
        swarm = FakeSwarm()

        async def chat(self, query):
            raise AssertionError("chat fallback should not be used for local swarm executors")

    async def fake_load_cli_agent_executor(agent_name):
        return FakeExecutor()

    async def fake_run(*, input, swarm):
        assert input == "question"
        assert isinstance(swarm, FakeSwarm)
        return {
            "answer": "answer with tools",
            "trajectory": [{"tool_calls": [{"name": "search"}]}],
            "usage": {"total_tokens": 12},
        }

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._load_cli_agent_executor",
        fake_load_cli_agent_executor,
    )
    monkeypatch.setattr("aworld_cli.evaluator_runtime.Runners.run", fake_run)

    case = SimpleNamespace(case_id="case-1", input={"input": "question"}, metadata={})
    state = await _CliAgentRuntimeHarness(agent_name="Aworld").run_rollout(
        case=case,
        target={"source_kind": "task"},
    )

    assert state.answer == "answer with tools"
    assert state.tool_calls == [{"name": "search"}]
    assert state.trajectory == [{"tool_calls": [{"name": "search"}]}]
    assert state.standard_metrics["n_tool_calls"] == 1
    assert state.standard_metrics["n_tokens"] == 12


def test_source_prompt_uses_zero_to_hundred_score_contract() -> None:
    prompt = _build_source_prompt(
        {"input": "question"},
        {"answer": "existing"},
        suite=None,
    )

    payload = json.loads(prompt)
    assert payload["required_output_schema"]["score"] == "number, weighted score from 0 to 100"
    assert payload["required_output_schema"]["veto_triggered"] == "boolean, true only for one-vote veto failures"


def test_run_evaluator_source_cli_rejects_unsupported_source_kind(tmp_path: Path) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported source kind"):
        run_evaluator_source_cli(
            input=str(input_path),
            kind="task-only",
            judge_agent=str(judge_agent),
        )


def test_trajectory_source_gate_consumes_veto_signal(tmp_path: Path) -> None:
    task_id = "task-with-veto"
    trajectory = [
        {
            "state": {"input": {"content": "question"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "final", "is_agent_finished": "True"},
        }
    ]
    input_path = tmp_path / "trajectory.log"
    input_path.write_text(
        repr({"task_id": task_id, "is_sub_task": False, "trajectory": json.dumps(trajectory)}) + "\n",
        encoding="utf-8",
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=task_id,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    pass_conditions = suite.gate_policy.normalized_conditions()[0]
    assert any(
        condition.metric_name == "veto_triggered"
        and condition.op == "=="
        and condition.threshold is False
        for condition in pass_conditions
    )
    decision = suite.gate_policy.evaluate(
        {
            "score": 95.0,
            "A1_groundedness": 5,
            "has_evidence": 1.0,
            "agent_finished": 1.0,
            "veto_triggered": True,
        }
    )
    assert decision.status == "fail"
    assert any(condition["metric_name"] == "veto_triggered" for condition in decision.failed_conditions)


def test_trajectory_source_judge_system_prompt_prefers_artifact_backed_contract(
    tmp_path: Path,
) -> None:
    task_id = "task-contract"
    trajectory = [
        {
            "state": {"input": {"content": "question"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "final", "is_agent_finished": "True"},
        }
    ]
    input_path = tmp_path / "trajectory.log"
    input_path.write_text(
        repr({"task_id": task_id, "is_sub_task": False, "trajectory": json.dumps(trajectory)}) + "\n",
        encoding="utf-8",
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text(
        "---\nname: judge\n---\nParse TRAJECTORY_LOG yourself before scoring.\n",
        encoding="utf-8",
    )

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=task_id,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    system_prompt = suite.judge_backend.system_prompt
    assert system_prompt.startswith("AWorld trajectory evaluator runtime contract:")
    assert "Prefer artifact_backed_evidence over any legacy TRAJECTORY_LOG parsing instructions" in system_prompt
    assert "artifact_read_requests" in system_prompt
    assert "Parse TRAJECTORY_LOG yourself before scoring" in system_prompt


def test_aworld_trajectory_log_without_task_id_builds_task_execution_suite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    captured = {}

    class FakeHarness:
        pass

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        captured["agent_name"] = agent_name
        return FakeHarness()

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    assert captured["agent_name"] == "Aworld"
    assert suite.suite_id == "trajectory-source-evaluator"
    assert suite.cases[0].case_id == "case-1"
    assert suite.cases[0].input == {"input": "question"}
    assert suite.runtime_harness is not None
    assert suite.judge_backend.backend_id == "trajectory-evaluator-agent-md"
    pass_conditions = suite.gate_policy.normalized_conditions()[0]
    assert any(condition.metric_name == "A1_groundedness" for condition in pass_conditions)
    assert any(condition.metric_name == "veto_triggered" for condition in pass_conditions)


def test_trajectory_log_without_task_id_builds_replay_suite_for_all_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "trajectory.log"
    trajectory = [
        {
            "state": {"input": {"content": "question"}, "messages": []},
            "meta": {"step": 1},
            "action": {"content": "final", "is_agent_finished": "True"},
        }
    ]
    input_path.write_text(
        "\n".join(
            [
                repr({"task_id": "task-1", "is_sub_task": False, "trajectory": json.dumps(trajectory)}),
                repr({"task_id": "task-2", "is_sub_task": False, "trajectory": json.dumps(trajectory)}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

    def fake_build_cli_agent_runtime_harness(*, agent_name):
        raise AssertionError("trajectory log replay must not execute the main agent")

    monkeypatch.setattr(
        "aworld_cli.evaluator_runtime._build_cli_agent_runtime_harness",
        fake_build_cli_agent_runtime_harness,
    )

    suite = _build_source_suite(
        kind="trajectory",
        input_path=input_path,
        judge_agent_path=judge_agent,
        task_id=None,
        id_field="id",
        task_field="input",
        answer_field="answer",
        out_dir=str(tmp_path),
    )

    assert suite.suite_id == "trajectory-source-evaluator"
    assert [case.case_id for case in suite.cases] == ["task-1", "task-2"]
    assert suite.runtime_harness is not None


def test_trajectory_prompt_can_use_generated_runtime_trajectory() -> None:
    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question", "_case_metadata": {}},
            {
                "case_id": "case-1",
                "answer": "final answer",
                "trajectory": [
                    {
                        "state": {
                            "input": {"content": "question"},
                            "messages": [{"role": "tool", "content": "evidence"}],
                        },
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {
                            "content": "final answer",
                            "is_agent_finished": "True",
                            "tool_calls": [{"function": {"name": "search", "arguments": "{}"}}],
                        },
                    }
                ],
            },
            suite=None,
        )
    )

    extracted = prompt["extracted_trajectory"]
    assert extracted["task_id"] == "case-1"
    assert extracted["question"] == "question"
    assert extracted["final_answer"] == "final answer"
    assert extracted["evidence"][0]["content"] == "evidence"
    assert extracted["steps"][0]["tool_calls"] == [{"name": "search", "arguments": "{}"}]
    runtime_context = prompt["runtime_context"]
    assert runtime_context["trajectory_log_path"] == ""
    assert runtime_context["task_id"] == "case-1"
    assert runtime_context["TRAJECTORY_LOG"] == ""
    assert runtime_context["TASK_ID"] == "case-1"
    assert runtime_context["OUT_DIR"] == ""
    contract = prompt["evaluation_runtime_contract"]
    assert contract["inputs_are_complete"] is True
    assert contract["primary_evaluation_input"] == "artifact_backed_evidence"
    assert contract["bounded_prompt_input"] == "extracted_trajectory"
    assert contract["do_not_request_missing_parameters"] is True
    assert contract["output_format"] == "single_json_object"
    assert "Do not ask the user for TRAJECTORY_LOG" in prompt["instruction"]
    assert "Return only one compact JSON object" in prompt["instruction"]
    assert "Do not include analysis" in prompt["instruction"]
    assert "Do not include markdown" in prompt["instruction"]


def test_build_trajectory_prompt_includes_runtime_context_from_source_target() -> None:
    prompt = json.loads(
        _build_trajectory_prompt(
            case_input={"task_id": "task-1", "trajectory_log": "/tmp/trajectory.log"},
            target={
                "target_path": "/tmp/trajectory.log",
                "source_out_dir": "/tmp/extracted",
                "report_output_path": "/tmp/report.json",
                "artifacts": {
                    "outcome": {
                        "extracted_path": None,
                    }
                },
                "trajectory": [
                    {
                        "state": {"input": {"content": "question"}, "messages": []},
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
            },
            suite=None,
        )
    )

    runtime_context = prompt["runtime_context"]
    assert runtime_context == {
        "trajectory_log_path": "/tmp/trajectory.log",
        "task_id": "task-1",
        "out_dir": "/tmp/extracted",
        "report_output_path": "/tmp/report.json",
        "TRAJECTORY_LOG": "/tmp/trajectory.log",
        "TASK_ID": "task-1",
        "OUT_DIR": "/tmp/extracted",
    }


def test_trajectory_prompt_includes_canonical_evidence_bundle(tmp_path: Path) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "format": "aworld.self_evolve.evidence_bundle",
                "version": 1,
                "valid": True,
                "entries": [
                    {
                        "source_id": "source-1",
                        "artifact_path": str(tmp_path / "source.txt"),
                        "extraction_method": "bounded_extract",
                        "bounded_evidence": {
                            "bounded_excerpt": "short verified evidence",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "trajectory": [
                    {
                        "state": {"input": {"content": "question"}, "messages": []},
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
                "artifacts": {
                    "outcome": {
                        "extracted_path": None,
                    }
                },
                "evidence_bundle_path": str(bundle_path),
            },
            suite=None,
        )
    )

    bundle = prompt["extracted_trajectory"]["evidence_bundle"]
    assert bundle["valid"] is True
    assert bundle["entry_count"] == 1
    assert bundle["entries"][0]["source_id"] == "source-1"
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"] == (
        "short verified evidence"
    )
    assert prompt["evidence_summary"]["canonical_bundle_entry_count"] == 1
    assert prompt["evaluation_runtime_contract"]["primary_evaluation_input"] == (
        "artifact_backed_evidence"
    )


def test_trajectory_prompt_uses_bundle_first_compaction_for_large_replay_payload(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "evidence_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "format": "aworld.self_evolve.evidence_bundle",
                "version": 1,
                "valid": True,
                "entries": [
                    {
                        "source_id": "source-1",
                        "artifact_path": str(tmp_path / "source.txt"),
                        "extraction_method": "bounded_extract",
                        "bounded_evidence": {
                            "claim_support": "compact verified evidence",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    extracted_path = tmp_path / "extracted.json"
    extracted_path.write_text(
        json.dumps(
            {
                "task_id": "case-1",
                "question": (
                    "summarize the source"
                    "\n\nSelf-evolve replay evidence requirements:\n"
                    + ("internal replay instruction " * 200)
                ),
                "system_prompt_excerpt": "system instructions " * 1000,
                "steps": [
                    {
                        "step": index,
                        "agent_id": "Aworld",
                        "tool_calls": [
                            {
                                "name": "reader",
                                "args": {"content": "large argument " * 1000},
                            }
                        ],
                        "assistant_content": "assistant reasoning " * 1000,
                        "is_agent_finished": index == 3,
                    }
                    for index in range(1, 4)
                ],
                "final_answer": "answer",
                "evidence": [
                    {
                        "source": "state.messages",
                        "content": "raw evidence " * 5000,
                        "original_length": 65000,
                        "truncated": True,
                    }
                    for _ in range(8)
                ],
                "evidence_bundle_path": str(bundle_path),
            }
        ),
        encoding="utf-8",
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "summarize the source"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "artifacts": {"outcome": {"extracted_path": str(extracted_path)}},
            },
            suite=None,
        )
    )

    trajectory = prompt["extracted_trajectory"]
    evidence = trajectory["evidence"]
    assert prompt["evidence_summary"]["bundle_first"] is True
    assert prompt["evidence_summary"]["raw_evidence_content_suppressed"] is True
    assert trajectory["evidence_bundle"]["valid"] is True
    assert "Self-evolve replay evidence requirements" not in trajectory["question"]
    assert trajectory["system_prompt_excerpt"] == ""
    assert len(evidence) <= 3
    assert all("content" not in item for item in evidence)
    assert all(len(step.get("assistant_content", "")) <= 200 for step in trajectory["steps"])
    assert all(
        "args" not in call
        for step in trajectory["steps"]
        for call in step.get("tool_calls", [])
    )
    artifact_backed = prompt["artifact_backed_evidence"]
    assert artifact_backed["mode"] == "read_only_artifact_index"
    assert artifact_backed["prompt_payload_is_bounded"] is True
    assert artifact_backed["read_policy"]["external_network_allowed"] is False
    assert artifact_backed["read_policy"]["mutation_allowed"] is False
    assert {
        (artifact["kind"], artifact["path"])
        for artifact in artifact_backed["artifacts"]
    } >= {
        ("extracted_trajectory_json", str(extracted_path)),
        ("canonical_evidence_bundle", str(bundle_path)),
        ("source_artifact", str(tmp_path / "source.txt")),
    }
    assert prompt["evaluation_runtime_contract"]["primary_evaluation_input"] == (
        "artifact_backed_evidence"
    )
    assert prompt["evaluation_runtime_contract"]["may_use_read_only_artifact_access"] is True
    assert prompt["evaluation_runtime_contract"]["do_not_call_external_tools"] is True
    assert len(json.dumps(prompt, ensure_ascii=False)) < 30000


def test_trajectory_prompt_artifact_index_lists_all_bundle_source_artifacts(
    tmp_path: Path,
) -> None:
    entries = []
    expected_paths = set()
    for index in range(7):
        source_path = tmp_path / f"source-{index}.txt"
        source_path.write_text(f"source evidence {index}", encoding="utf-8")
        expected_paths.add(str(source_path))
        entries.append(
            {
                "source_id": f"source-{index}",
                "artifact_path": str(source_path),
                "extraction_method": "bounded_extract",
                "bounded_evidence": {"excerpt": f"bounded evidence {index}"},
            }
        )
    bundle_path = tmp_path / "evidence_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "format": "aworld.self_evolve.evidence_bundle",
                "version": 1,
                "valid": True,
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "trajectory": [
                    {
                        "state": {"input": {"content": "question"}, "messages": []},
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
                "evidence_bundle_path": str(bundle_path),
            },
            suite=None,
        )
    )

    prompt_entries = prompt["extracted_trajectory"]["evidence_bundle"]["entries"]
    source_artifact_paths = {
        artifact["path"]
        for artifact in prompt["artifact_backed_evidence"]["artifacts"]
        if artifact["kind"] == "source_artifact"
    }
    assert len(prompt_entries) == 5
    assert source_artifact_paths == expected_paths
    assert prompt["evidence_summary"]["canonical_bundle_entry_count"] == 7


def test_trajectory_prompt_artifact_index_rejects_bundle_paths_outside_trusted_roots(
    tmp_path: Path,
) -> None:
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    trusted_source = trusted_dir / "source.txt"
    trusted_source.write_text("trusted evidence", encoding="utf-8")
    untrusted_source = tmp_path / "outside" / "secret.txt"
    untrusted_source.parent.mkdir()
    untrusted_source.write_text("secret evidence", encoding="utf-8")
    bundle_path = trusted_dir / "evidence_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "format": "aworld.self_evolve.evidence_bundle",
                "version": 1,
                "valid": True,
                "entries": [
                    {
                        "source_id": "trusted",
                        "artifact_path": str(trusted_source),
                        "bounded_evidence": {"excerpt": "trusted evidence"},
                    },
                    {
                        "source_id": "untrusted",
                        "artifact_path": str(untrusted_source),
                        "bounded_evidence": {"excerpt": "untrusted evidence"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    extracted_path = trusted_dir / "extracted.json"
    extracted_path.write_text(
        json.dumps(
            {
                "task_id": "case-1",
                "question": "question",
                "steps": [{"step": 1, "is_agent_finished": True}],
                "final_answer": "answer",
                "evidence": [],
                "evidence_bundle_path": str(bundle_path),
            }
        ),
        encoding="utf-8",
    )

    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "artifacts": {"outcome": {"extracted_path": str(extracted_path)}},
            },
            suite=None,
        )
    )

    source_artifact_paths = {
        artifact["path"]
        for artifact in prompt["artifact_backed_evidence"]["artifacts"]
        if artifact["kind"] == "source_artifact"
    }
    assert str(trusted_source) in source_artifact_paths
    assert str(untrusted_source) not in source_artifact_paths


def test_trajectory_prompt_compacts_noisy_evidence_without_losing_quality_signals() -> None:
    noisy_content = "alpha " * 2000
    prompt = json.loads(
        _build_trajectory_prompt(
            {"input": "question"},
            {
                "case_id": "case-1",
                "answer": "answer",
                "trajectory": [
                    {
                        "state": {
                            "input": {"content": "question"},
                            "messages": [
                                {
                                    "role": "tool",
                                    "content": noisy_content,
                                }
                            ],
                        },
                        "meta": {"step": 1, "agent_id": "Aworld"},
                        "action": {"content": "answer", "is_agent_finished": "True"},
                    }
                ],
            },
            suite=None,
        )
    )

    evidence = prompt["extracted_trajectory"]["evidence"][0]
    evidence_summary = prompt["evidence_summary"]
    assert len(evidence["content"]) < len(noisy_content)
    assert evidence["prompt_compacted"] is True
    assert evidence["original_length"] == len(noisy_content)
    assert evidence["content"].startswith("alpha")
    assert "omitted" in evidence["content"]
    assert evidence_summary["evidence_block_count"] == 1
    assert evidence_summary["prompt_compacted_count"] == 1
    assert evidence_summary["total_original_chars"] == len(noisy_content)
    assert evidence_summary["sources"] == ["state.messages"]


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
            "suite_id": "answer-source-evaluator",
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
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
        kind="answer",
        judge_agent=str(judge_agent),
        task_id="case-1",
        output=str(tmp_path / "report.json"),
    )

    assert events[0][0] == "pre"
    assert events[0][1]["mode"] == "source"
    assert events[0][1]["input"] == str(input_path.resolve())
    assert events[0][1]["kind"] == "answer"
    assert events[0][1]["task_id"] == "case-1"
    assert events[0][1]["judge_agent"] == str(judge_agent.resolve())
    assert events[1][0] == "post"
    assert events[1][1]["mode"] == "source"
    assert events[1][1]["report"]["source_selection"]["kind"] == "answer"


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
            "suite_id": "answer-source-evaluator",
            "target": flow.target,
            "judge_backend": {"backend_id": "source-agent-md"},
            "summary": {"answer-source-evaluator": {"score": {"mean": 88.0}}},
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
        kind="answer",
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
