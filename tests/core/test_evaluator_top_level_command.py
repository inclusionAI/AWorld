from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

import aworld.evaluations.substrate as substrate_module
from aworld_cli import main as main_module
from aworld_cli.core.top_level_command_system import TopLevelCommandContext
from aworld_cli.top_level_commands.evaluator_cmd import EvaluatorTopLevelCommand


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


def test_registry_registers_builtin_evaluator_command() -> None:
    registry = main_module._build_top_level_command_registry()

    command = registry.get("evaluator")

    assert command is not None
    assert command.name == "evaluator"


def test_maybe_dispatch_top_level_command_runs_evaluator_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    def fake_run_evaluator_cli(**kwargs):
        assert kwargs["target"] == str(target)
        return {
            "suite_id": "app-evaluator",
            "gate": {"status": "pass"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_cli",
        fake_run_evaluator_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "evaluator", "--target", str(target)]
    )
    output = capsys.readouterr().out

    assert handled is True
    assert "app-evaluator" in output
    assert "pass" in output


def test_maybe_dispatch_top_level_command_runs_source_evaluator_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"answer"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    calls = {}

    def fake_run_evaluator_source_cli(**kwargs):
        calls.update(kwargs)
        return {
            "suite_id": "answer-source-evaluator",
            "gate": {"status": "pass"},
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        fake_run_evaluator_source_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "evaluator",
            "--input",
            str(input_path),
            "--kind",
            "answer",
            "--judge-agent",
            str(judge_agent),
            "--out-dir",
            str(tmp_path / "reports"),
        ]
    )
    output = capsys.readouterr().out

    assert handled is True
    assert calls["input"] == str(input_path)
    assert calls["kind"] == "answer"
    assert calls["judge_agent"] == str(judge_agent)
    assert calls["out_dir"] == str(tmp_path / "reports")
    assert calls["id_field"] == "id"
    assert calls["task_field"] == "input"
    assert calls["answer_field"] == "answer"
    assert "answer-source-evaluator" in output
    assert "pass" in output


def test_maybe_dispatch_top_level_command_accepts_judge_agent_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"answer"}\n', encoding="utf-8")
    calls = {}

    def fake_run_evaluator_source_cli(**kwargs):
        calls.update(kwargs)
        return {
            "suite_id": "answer-source-evaluator",
            "gate": {"status": "pass"},
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        fake_run_evaluator_source_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "evaluator",
            "--input",
            str(input_path),
            "--kind",
            "answer",
            "--judge-agent-name",
            "JudgeTeam",
        ]
    )
    output = capsys.readouterr().out

    assert handled is True
    assert calls["judge_agent"] is None
    assert calls["judge_agent_name"] == "JudgeTeam"
    assert calls["judge_backend_ref"] is None
    assert "answer-source-evaluator" in output


def test_maybe_dispatch_top_level_command_accepts_judge_backend_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "answers.jsonl"
    input_path.write_text('{"id":"case-1","input":"question","answer":"answer"}\n', encoding="utf-8")
    calls = {}

    def fake_run_evaluator_source_cli(**kwargs):
        calls.update(kwargs)
        return {
            "suite_id": "answer-source-evaluator",
            "gate": {"status": "pass"},
            "summary": {"answer-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        fake_run_evaluator_source_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "evaluator",
            "--input",
            str(input_path),
            "--kind",
            "answer",
            "--judge-backend-ref",
            "custom_judge:build_backend",
        ]
    )
    output = capsys.readouterr().out

    assert handled is True
    assert calls["judge_agent"] is None
    assert calls["judge_agent_name"] is None
    assert calls["judge_backend_ref"] == "custom_judge:build_backend"
    assert "answer-source-evaluator" in output


def test_maybe_dispatch_top_level_command_runs_task_source_with_default_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "tasks.jsonl"
    input_path.write_text('{"id":"case-1","input":"question"}\n', encoding="utf-8")
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")
    calls = {}

    def fake_run_evaluator_source_cli(**kwargs):
        calls.update(kwargs)
        return {
            "suite_id": "task-source-evaluator",
            "gate": {"status": "pass"},
            "summary": {"task-source-evaluator": {"score": {"mean": 0.9}}},
            "results": [{"state_summary": {"answer": "answer"}}],
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        fake_run_evaluator_source_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "evaluator",
            "--input",
            str(input_path),
            "--kind",
            "task",
            "--judge-agent",
            str(judge_agent),
            "--out-dir",
            str(tmp_path / "reports"),
        ]
    )
    output = capsys.readouterr().out

    assert handled is True
    assert calls["kind"] == "task"
    assert calls["agent"] is None
    assert "task-source-evaluator" in output
    assert "pass" in output


def test_evaluator_command_returns_nonzero_for_failed_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_cli",
        lambda **kwargs: {
            "suite_id": "app-evaluator",
            "gate": {"status": "fail", "value": 0.3},
            "approval": {"required": False, "resolved": False, "approved": None},
        },
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target="artifact.txt",
            suite=None,
            output=None,
            interactive_approval=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    assert exit_code == 2


def test_evaluator_source_run_rejects_target_mode_arguments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        lambda **kwargs: pytest.fail("source runtime should not be called"),
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target="artifact.txt",
            input="answers.jsonl",
            kind="answer",
            judge_agent="agent.md",
            out_dir=None,
            output=None,
            task_id=None,
            agent=None,
            id_field="id",
            task_field="input",
            answer_field="answer",
            interactive_approval=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "--target cannot be used with --input" in output


@pytest.mark.parametrize(
    ("arg_name", "expected"),
    [
        ("suite", "--suite cannot be used with --input"),
        ("list_suites", "--list-suites cannot be used with --input"),
        ("print_report_schema", "--print-report-schema cannot be used with --input"),
        ("validate_report", "--validate-report cannot be used with --input"),
    ],
)
def test_evaluator_source_run_rejects_other_target_mode_arguments(
    arg_name: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        lambda **kwargs: pytest.fail("source runtime should not be called"),
    )
    args = {
        "target": None,
        "suite": None,
        "input": "answers.jsonl",
        "kind": "answer",
        "judge_agent": "agent.md",
        "out_dir": None,
        "output": None,
        "task_id": None,
        "agent": None,
        "id_field": "id",
        "task_field": "input",
        "answer_field": "answer",
        "interactive_approval": False,
        "list_suites": False,
        "print_report_schema": False,
        "validate_report": None,
    }
    args[arg_name] = "value" if arg_name in {"suite", "validate_report"} else True

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(**args),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert expected in output


def test_evaluator_source_mode_requires_kind_and_judge_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        lambda **kwargs: pytest.fail("source runtime should not be called"),
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            input="answers.jsonl",
            kind=None,
            judge_agent=None,
            judge_agent_name=None,
            judge_backend_ref=None,
            out_dir=None,
            output=None,
            task_id=None,
            agent=None,
            id_field="id",
            task_field="input",
            answer_field="answer",
            interactive_approval=False,
            list_suites=False,
            print_report_schema=False,
            validate_report=None,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "--kind is required with --input" in output


def test_evaluator_source_mode_requires_one_judge_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_source_cli",
        lambda **kwargs: pytest.fail("source runtime should not be called"),
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            input="answers.jsonl",
            kind="answer",
            judge_agent=None,
            judge_agent_name=None,
            judge_backend_ref=None,
            out_dir=None,
            output=None,
            task_id=None,
            agent=None,
            id_field="id",
            task_field="input",
            answer_field="answer",
            interactive_approval=False,
            list_suites=False,
            print_report_schema=False,
            validate_report=None,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "exactly one of --judge-agent, --judge-agent-name, or --judge-backend-ref is required" in output


def test_evaluator_command_returns_nonzero_for_unresolved_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_cli",
        lambda **kwargs: {
            "suite_id": "app-evaluator",
            "gate": {"status": "needs_approval", "value": 0.7},
            "approval": {"required": True, "resolved": False, "approved": None},
        },
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target="artifact.txt",
            suite=None,
            output=None,
            interactive_approval=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    assert exit_code == 3


def test_evaluator_command_lists_available_suites(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=True,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "app-evaluator" in output


def test_evaluator_command_lists_target_matching_suites_and_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=str(target),
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=True,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Available evaluator suites for target:" in output
    assert "Default suite: app-evaluator" in output


def test_evaluator_command_prints_report_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=False,
            print_report_schema=True,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "\"title\": \"AWorld Evaluator Report\"" in output
    assert "\"aworld.evaluator.report\"" in output


def test_evaluator_command_validates_report_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(
        """
{
  "report_version": 1,
  "report_format": {"id": "aworld.evaluator.report", "version": 1},
  "generated_at": "2026-06-02T04:00:00Z",
  "suite_id": "app-evaluator",
  "target": {"target_path": "/tmp/artifact.txt", "target_kind": "file"},
  "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
  "metrics": {"score": {"mean": 0.9}},
  "results": [],
  "result_counts": {"cases_total": 0, "cases_with_metrics": 0, "cases_with_judge": 0},
  "approval": {"required": false, "resolved": false, "approved": null},
  "automation": {
    "gate_status": null,
    "metric_name": null,
    "metric_value": null,
    "approval_required": false,
    "approval_resolved": false,
    "approved": null,
    "suggested_exit_code": 0,
    "case_count": 0,
    "judge_backend": null
  }
}
""".strip(),
        encoding="utf-8",
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=False,
            print_report_schema=False,
            validate_report=str(report_path),
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Report is valid" in output


def test_evaluator_command_returns_nonzero_for_invalid_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(
        """
{
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
  "approval": {"required": false, "resolved": false, "approved": null},
  "automation": {
    "gate_status": "maybe",
    "metric_name": "score",
    "metric_value": 0.9,
    "approval_required": false,
    "approval_resolved": false,
    "approved": null,
    "suggested_exit_code": 0,
    "case_count": 0,
    "judge_backend": null
  }
}
""".strip(),
        encoding="utf-8",
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=False,
            print_report_schema=False,
            validate_report=str(report_path),
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 4
    assert "Report is invalid" in output


def test_evaluator_command_returns_usage_error_without_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "--target is required" in output


def test_evaluator_command_returns_nonzero_for_missing_target(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.txt"

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=str(missing),
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "does not exist" in output
