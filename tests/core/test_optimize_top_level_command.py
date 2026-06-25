from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module
from aworld_cli.top_level_commands.optimize_cmd import run_optimize_cli


def test_registry_registers_builtin_optimize_command_from_plugin_manifest() -> None:
    registry = main_module._build_top_level_command_registry()

    command = registry.get("optimize")

    assert command is not None
    assert command.name == "optimize"


def test_optimize_command_passes_generic_target_dataset_and_apply_to_framework(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = {}

    def fake_run_optimize_cli(**kwargs):
        calls.update(kwargs)
        return {"report_path": str(tmp_path / "report.json"), "best_candidate_id": "cand-1"}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.optimize_cmd.run_optimize_cli",
        fake_run_optimize_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "optimize",
            "--target",
            "skill:demo",
            "--dataset",
            "eval.jsonl",
            "--apply",
            "proposal",
        ]
    )

    output = capsys.readouterr().out
    assert handled is True
    assert calls["target"] == "skill:demo"
    assert calls["dataset"] == "eval.jsonl"
    assert calls["apply"] == "proposal"
    assert calls["from_trajectory"] is None
    assert calls["task"] is None
    assert "report.json" in output
    assert "cand-1" in output


def test_optimize_command_drains_pending_self_evolve_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = {}

    def fake_drain_pending_self_evolve_jobs(*, workspace_root):
        calls["workspace_root"] = workspace_root
        return 2

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.optimize_cmd.drain_pending_self_evolve_jobs",
        fake_drain_pending_self_evolve_jobs,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "optimize", "--drain-pending"]
    )

    output = capsys.readouterr().out
    assert handled is True
    assert calls["workspace_root"] == str(Path.cwd())
    assert "Drained pending self-evolve jobs: 2" in output


def test_optimize_command_rejects_unsupported_apply_modes(capsys: pytest.CaptureFixture[str]) -> None:
    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "optimize", "--target", "skill:demo", "--apply", "write"]
    )

    output = capsys.readouterr().out
    assert handled is True
    assert "Optimize error: --apply must be one of proposal, auto_verified" in output


@pytest.mark.parametrize("apply_mode", ["write", "branch"])
def test_optimize_command_rejects_phase1_external_apply_modes(
    apply_mode: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "optimize", "--target", "skill:demo", "--apply", apply_mode]
    )

    output = capsys.readouterr().out
    assert handled is True
    assert "Optimize error: --apply must be one of proposal, auto_verified" in output


@pytest.mark.parametrize("target", ["skill:demo", "prompt:system", "tool:browser"])
def test_optimize_command_uses_one_generic_path_for_target_forms(
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {}

    def fake_run_optimize_cli(**kwargs):
        calls.update(kwargs)
        return {"report_path": ".aworld/self_evolve/run/report.json"}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.optimize_cmd.run_optimize_cli",
        fake_run_optimize_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "optimize", "--target", target, "--dataset", "eval.jsonl"]
    )

    assert handled is True
    assert calls["target"] == target
    assert calls["dataset"] == "eval.jsonl"


def test_optimize_command_passes_session_batch_iterations_and_auto_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {}

    def fake_run_optimize_cli(**kwargs):
        calls.update(kwargs)
        return {"report_path": ".aworld/self_evolve/run/report.json"}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.optimize_cmd.run_optimize_cli",
        fake_run_optimize_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "optimize",
            "--target",
            "tool:browser",
            "--from-session",
            "session-1",
            "--batch-config",
            "batch.yaml",
            "--iterations",
            "3",
            "--apply",
            "auto_verified",
        ]
    )

    assert handled is True
    assert calls["target"] == "tool:browser"
    assert calls["from_session"] == "session-1"
    assert calls["batch_config"] == "batch.yaml"
    assert calls["iterations"] == 3
    assert calls["apply"] == "auto_verified"


def test_optimize_command_passes_judge_agent_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {}

    def fake_run_optimize_cli(**kwargs):
        calls.update(kwargs)
        return {"report_path": ".aworld/self_evolve/run/report.json"}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.optimize_cmd.run_optimize_cli",
        fake_run_optimize_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "optimize",
            "--target",
            "skill:workflow-helper",
            "--from-trajectory",
            "trajectory.log",
            "--apply",
            "auto_verified",
            "--judge-agent",
            "agent.md",
        ]
    )

    assert handled is True
    assert calls["judge_agent"] == "agent.md"
    assert calls["judge_agent_name"] is None
    assert calls["judge_backend_ref"] is None


def test_optimize_command_passes_judge_backend_ref_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {}

    def fake_run_optimize_cli(**kwargs):
        calls.update(kwargs)
        return {"report_path": ".aworld/self_evolve/run/report.json"}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.optimize_cmd.run_optimize_cli",
        fake_run_optimize_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        [
            "aworld-cli",
            "optimize",
            "--target",
            "skill:workflow-helper",
            "--from-trajectory",
            "trajectory.log",
            "--apply",
            "auto_verified",
            "--judge-backend-ref",
            "pkg.module:build_judge",
        ]
    )

    assert handled is True
    assert calls["judge_agent"] is None
    assert calls["judge_agent_name"] is None
    assert calls["judge_backend_ref"] == "pkg.module:build_judge"


def test_optimize_command_rejects_multiple_judge_selectors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module._maybe_dispatch_top_level_command(
            [
                "aworld-cli",
                "optimize",
                "--target",
                "skill:workflow-helper",
                "--from-trajectory",
                "trajectory.log",
                "--apply",
                "auto_verified",
                "--judge-agent",
                "agent.md",
                "--judge-backend-ref",
                "pkg.module:build_judge",
            ]
        )

    output = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert (
        "Optimize error: use only one of --judge-agent, --judge-agent-name, or --judge-backend-ref"
        in output
    )


def test_optimize_command_task_without_target_uses_framework_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {}

    def fake_run_optimize_cli(**kwargs):
        calls.update(kwargs)
        return {"report_path": ".aworld/self_evolve/run/report.json"}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.optimize_cmd.run_optimize_cli",
        fake_run_optimize_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "optimize", "--task", "fix browser login", "--from-trajectory", "trajectory.log"]
    )

    assert handled is True
    assert calls["target"] is None
    assert calls["task"] == "fix browser login"
    assert calls["infer_target"] is True
    assert calls["from_trajectory"] == "trajectory.log"


def test_run_optimize_cli_delegates_generic_request_to_framework_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve as self_evolve

    calls = {}

    def fake_optimize_from_cli_request(**kwargs):
        calls.update(kwargs)
        return {"report_path": str(tmp_path / "report.json")}

    monkeypatch.setattr(
        self_evolve,
        "optimize_from_cli_request",
        fake_optimize_from_cli_request,
        raising=False,
    )

    report = run_optimize_cli(
        agent="Agent",
        task=None,
        target="prompt:system",
        dataset="eval.jsonl",
        from_session=None,
        from_trajectory=None,
        batch_config=None,
        iterations=3,
        apply="auto_verified",
        infer_target=False,
        workspace_root=str(tmp_path),
        judge_agent="agent.md",
        judge_agent_name=None,
        judge_backend_ref=None,
    )

    assert report["report_path"].endswith("report.json")
    assert calls["workspace_root"] == str(tmp_path)
    assert calls["agent"] == "Agent"
    assert calls["target"] == "prompt:system"
    assert calls["dataset"] == "eval.jsonl"
    assert calls["iterations"] == 3
    assert calls["apply_policy"] == "auto_verified"
    assert calls["infer_target"] is False
    assert calls["judge_config"].mode == "agent_md"
    assert calls["judge_config"].agent_path == "agent.md"


def test_run_optimize_cli_maps_judge_backend_ref_to_framework_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve as self_evolve

    calls = {}

    def fake_optimize_from_cli_request(**kwargs):
        calls.update(kwargs)
        return {"report_path": str(tmp_path / "report.json")}

    monkeypatch.setattr(
        self_evolve,
        "optimize_from_cli_request",
        fake_optimize_from_cli_request,
        raising=False,
    )

    run_optimize_cli(
        agent=None,
        task=None,
        target="skill:workflow-helper",
        dataset="eval.jsonl",
        from_session=None,
        from_trajectory=None,
        batch_config=None,
        iterations=None,
        apply="auto_verified",
        infer_target=False,
        workspace_root=str(tmp_path),
        judge_agent=None,
        judge_agent_name=None,
        judge_backend_ref="pkg.module:build_judge",
    )

    assert calls["judge_config"].mode == "backend_ref"
    assert calls["judge_config"].backend_ref == "pkg.module:build_judge"


def test_run_optimize_cli_leaves_target_inference_to_framework(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve as self_evolve

    calls = {}

    def fake_optimize_from_cli_request(**kwargs):
        calls.update(kwargs)
        return {"report_path": str(tmp_path / "report.json")}

    monkeypatch.setattr(
        self_evolve,
        "optimize_from_cli_request",
        fake_optimize_from_cli_request,
        raising=False,
    )

    run_optimize_cli(
        agent=None,
        task="fix login",
        target=None,
        dataset=None,
        from_session=None,
        from_trajectory="trajectory.log",
        batch_config=None,
        iterations=None,
        apply="proposal",
        infer_target=True,
        workspace_root=str(tmp_path),
    )

    assert calls["task"] == "fix login"
    assert calls["target"] is None
    assert calls["infer_target"] is True
    assert calls["from_trajectory"] == "trajectory.log"


def test_run_optimize_cli_enables_framework_replay_for_auto_verified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve as self_evolve

    calls = {}

    def fake_optimize_from_cli_request(**kwargs):
        calls.update(kwargs)
        return {"report_path": str(tmp_path / "report.json")}

    monkeypatch.setattr(
        self_evolve,
        "optimize_from_cli_request",
        fake_optimize_from_cli_request,
        raising=False,
    )

    run_optimize_cli(
        agent="Aworld",
        task=None,
        target=None,
        dataset=None,
        from_session=None,
        from_trajectory="trajectory.log",
        batch_config=None,
        iterations=None,
        apply="auto_verified",
        infer_target=True,
        workspace_root=str(tmp_path),
        judge_agent_name="JudgeTeam",
    )

    assert calls["replay_enabled"] is True


def test_render_optimize_summary_includes_status_and_target_selection_path() -> None:
    from aworld_cli.top_level_commands.optimize_cmd import render_optimize_summary

    summary = render_optimize_summary(
        {
            "status": "rejected",
            "report_path": ".aworld/self_evolve/run/report.json",
            "target_selection_path": ".aworld/self_evolve/run/target_selection.json",
            "replay_path": ".aworld/self_evolve/run/replay/cand-1",
            "evaluator_report_paths": [
                ".aworld/self_evolve/evaluator/cand-1/validation/report.json"
            ],
        }
    )

    assert "Status: rejected" in summary
    assert "Report: .aworld/self_evolve/run/report.json" in summary
    assert "Target selection: .aworld/self_evolve/run/target_selection.json" in summary
    assert "Replay: .aworld/self_evolve/run/replay/cand-1" in summary
    assert (
        "Evaluator report: .aworld/self_evolve/evaluator/cand-1/validation/report.json"
        in summary
    )


def test_optimize_command_module_does_not_own_framework_self_evolve_components() -> None:
    import aworld_cli.top_level_commands.optimize_cmd as optimize_cmd

    source = inspect.getsource(optimize_cmd)

    forbidden_framework_symbols = {
        "SelfEvolveScheduler",
        "SelfEvolveRunner",
        "EvaluationBackend",
        "CandidateOptimizer",
        "FilesystemSelfEvolveStore",
        "TrajectoryCreditAssigner",
        "SelfEvolveConfig",
        "AgentConfig",
    }
    assert not [symbol for symbol in forbidden_framework_symbols if symbol in source]


def test_optimize_command_does_not_define_cli_owned_self_evolve_mode(capsys: pytest.CaptureFixture[str]) -> None:
    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "optimize", "--mode", "online"]
    )

    output = capsys.readouterr().err
    assert handled is True
    assert "unrecognized arguments: --mode online" in output


def test_framework_cli_request_runs_explicit_skill_target_without_cli_owned_optimizer(tmp_path: Path) -> None:
    from aworld.self_evolve import optimize_from_cli_request

    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    dataset_path = tmp_path / "eval.jsonl"
    dataset_path.write_text('{"case_id":"case-1","input":"demo"}\n', encoding="utf-8")

    report = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        dataset=str(dataset_path),
        apply_policy="proposal",
    )

    assert Path(report["report_path"]).exists()
    assert report["status"] == "succeeded"
    assert report["best_candidate_id"] is None
    assert skill_path.read_text(encoding="utf-8").endswith("Old guidance.\n")
