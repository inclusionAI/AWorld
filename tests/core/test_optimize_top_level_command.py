from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module
from aworld_cli.top_level_commands.optimize_cmd import render_optimize_summary, run_optimize_cli


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
    assert callable(calls["progress_callback"])
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


def test_optimize_command_passes_replay_runtime_limits(
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
            "--from-trajectory",
            "trajectory.log",
            "--apply",
            "auto_verified",
            "--replay-timeout",
            "300",
            "--replay-max-runs",
            "1",
            "--judge-repetitions",
            "5",
            "--judge-timeout",
            "120",
            "--baseline-replay-repetitions",
            "2",
            "--candidate-replay-repetitions",
            "3",
        ]
    )

    assert handled is True
    assert calls["agent"] is None
    assert calls["replay_timeout_seconds"] == 300
    assert calls["replay_max_steps"] == 1
    assert calls["judge_repetitions"] == 5
    assert calls["judge_timeout_seconds"] == 120
    assert calls["baseline_replay_repetitions"] == 2
    assert calls["candidate_replay_repetitions"] == 3


def test_optimize_command_passes_rerun_evaluator_from_run(
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
            "--from-run",
            "cli-159068069202",
            "--rerun-evaluator",
            "--apply",
            "auto_verified",
            "--judge-agent",
            "agent.md",
        ]
    )

    assert handled is True
    assert calls["from_run"] == "cli-159068069202"
    assert calls["rerun_evaluator"] is True
    assert calls["from_trajectory"] is None
    assert calls["judge_agent"] == "agent.md"


def test_render_optimize_summary_suggests_rerun_evaluator_after_judge_timeout() -> None:
    summary = render_optimize_summary(
        {
            "run_id": "cli-123",
            "status": "rejected",
            "report_path": ".aworld/self_evolve/cli-123/report.json",
            "replay_path": ".aworld/self_evolve/cli-123/replay/cand-1",
            "selected_candidate_id": "cand-1",
            "gate_results": [
                {
                    "gate_name": "score_improvement",
                    "passed": False,
                    "reason": "baseline judge failed completely; score improvement is inconclusive",
                }
            ],
            "baseline_metrics": {
                "judge_attempt_count": 3,
                "judge_success_count": 0,
                "judge_failure_count": 3,
                "judge_failures": [
                    {"type": "TimeoutError", "reason": "AWorld trajectory judge timed out after 120s"}
                ],
            },
        }
    )

    assert "Rejected gates: score_improvement" in summary
    assert (
        "Resume evaluator: aworld-cli optimize --from-run cli-123 --rerun-evaluator"
        in summary
    )


def test_render_optimize_summary_warns_when_replay_success_count_is_insufficient() -> None:
    summary = render_optimize_summary(
        {
            "run_id": "cli-456",
            "status": "rejected",
            "report_path": ".aworld/self_evolve/cli-456/report.json",
            "replay_path": ".aworld/self_evolve/cli-456/replay/cand-1",
            "selected_candidate_id": "cand-1",
            "gate_results": [
                {
                    "gate_name": "held_out_verification",
                    "passed": False,
                    "reason": "candidate is not verified on sufficient held-out cases",
                }
            ],
            "candidate_metrics": {
                "repetition_count": 3,
                "successful_repetition_count": 1,
                "failed_repetition_count": 2,
                "replay_failure_types": ["TimeoutExpired"],
            },
        }
    )

    assert "Rejected gates: held_out_verification" in summary
    assert "Replay recovery:" in summary
    assert "Resume evaluator:" not in summary


def test_render_optimize_summary_explains_no_candidate_rejection() -> None:
    summary = render_optimize_summary(
        {
            "status": "rejected",
            "report_path": "/tmp/report.json",
            "candidate_ids": [],
            "selected_candidate_id": None,
            "iterations": [{"iteration": 1, "status": "no_candidate"}],
            "gate_results": [
                {
                    "gate_name": "auto_verified_evaluation",
                    "passed": False,
                    "reason": "auto_verified apply policy requires a candidate",
                }
            ],
        }
    )

    assert "Status: rejected" in summary
    assert "Rejected gates: auto_verified_evaluation" in summary
    assert "No candidate generated:" in summary
    assert "replay/evaluation/apply were skipped" in summary


def test_run_optimize_cli_uses_interactive_auto_verified_defaults(
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
        target=None,
        dataset=None,
        from_session=None,
        from_trajectory="trajectory.log",
        batch_config=None,
        iterations=None,
        apply="auto_verified",
        infer_target=True,
        workspace_root=str(tmp_path),
        judge_agent="agent.md",
    )

    assert calls["judge_repetitions"] == 1
    assert calls["judge_timeout_seconds"] == 120
    assert calls["baseline_replay_repetitions"] == 2
    assert calls["candidate_replay_repetitions"] == 3
    assert calls["iterations"] == 1


def test_run_optimize_cli_can_forward_progress_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve as self_evolve

    calls = {}
    events = []

    def fake_optimize_from_cli_request(**kwargs):
        calls.update(kwargs)
        kwargs["progress_callback"]("replay", "Replay started")
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
        target=None,
        dataset=None,
        from_session=None,
        from_trajectory="trajectory.log",
        batch_config=None,
        iterations=None,
        apply="auto_verified",
        infer_target=True,
        workspace_root=str(tmp_path),
        judge_agent="agent.md",
        progress_callback=lambda stage, message: events.append((stage, message)),
    )

    assert events == [
        ("prepare", "Preparing self-evolve optimize request"),
        ("replay", "Replay started"),
    ]
    assert callable(calls["progress_callback"])


def test_run_optimize_cli_keeps_proposal_defaults_cheap(
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
        target="skill:demo",
        dataset="eval.jsonl",
        from_session=None,
        from_trajectory=None,
        batch_config=None,
        iterations=None,
        apply="proposal",
        infer_target=False,
        workspace_root=str(tmp_path),
    )

    assert "judge_repetitions" not in calls
    assert "judge_timeout_seconds" not in calls
    assert "baseline_replay_repetitions" not in calls
    assert "candidate_replay_repetitions" not in calls


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


def test_optimize_command_forwards_trajectory_set_to_framework(
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
            "--from-trajectory-set",
            "trajectory-set.json",
            "--include-prior-runs",
            "--apply",
            "proposal",
        ]
    )

    assert handled is True
    assert calls["from_trajectory_set"] == "trajectory-set.json"
    assert calls["include_prior_runs"] is True
    assert calls["infer_target"] is True


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
        from_trajectory_set=None,
        include_prior_runs=True,
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
    assert calls["from_trajectory_set"] is None
    assert calls["include_prior_runs"] is True
    assert calls["iterations"] == 3
    assert calls["apply_policy"] == "auto_verified"
    assert calls["infer_target"] is False
    assert calls["judge_config"].mode == "agent_md"
    assert calls["judge_config"].agent_path == "agent.md"


def test_run_optimize_cli_forwards_runtime_registry_refresher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve as self_evolve

    calls = {}

    def fake_optimize_from_cli_request(**kwargs):
        calls.update(kwargs)
        return {"report_path": str(tmp_path / "report.json")}

    def refresh_runtime(candidate):
        return {"status": "refreshed", "candidate_id": candidate.candidate_id}

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
        runtime_registry_refresher=refresh_runtime,
    )

    assert calls["runtime_registry_refresher"] is refresh_runtime
    assert calls["runtime_skill_activator"] is not None


def test_run_optimize_cli_defaults_runtime_skill_activator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve as self_evolve
    from aworld.self_evolve.types import SelfEvolveTargetRef

    calls = {}

    def fake_optimize_from_cli_request(**kwargs):
        calls.update(kwargs)
        return {"report_path": str(tmp_path / "report.json")}

    class FakeStateManager:
        enabled: list[str] = []

        def is_enabled(self, skill_name: str) -> bool:
            return skill_name in self.enabled

        def enable_skill(self, skill_name: str) -> None:
            self.enabled.append(skill_name)

    monkeypatch.setattr(
        self_evolve,
        "optimize_from_cli_request",
        fake_optimize_from_cli_request,
        raising=False,
    )
    monkeypatch.setattr(
        "aworld_cli.core.skill_state_manager.SkillStateManager",
        FakeStateManager,
    )

    run_optimize_cli(
        agent=None,
        task=None,
        target="skill:web-content-grounding",
        dataset="eval.jsonl",
        from_session=None,
        from_trajectory=None,
        batch_config=None,
        iterations=None,
        apply="auto_verified",
        infer_target=False,
        workspace_root=str(tmp_path),
    )

    activator = calls["runtime_skill_activator"]
    result = activator(
        type(
            "Candidate",
            (),
            {
                "target": SelfEvolveTargetRef(
                    target_type="skill",
                    target_id="web-content-grounding",
                    path=str(tmp_path / "SKILL.md"),
                )
            },
        )()
    )

    assert result == {
        "status": "enabled",
        "skill_name": "web-content-grounding",
        "was_enabled": False,
        "enabled": True,
    }


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


def test_render_optimize_summary_distinguishes_selected_from_best_candidate() -> None:
    from aworld_cli.top_level_commands.optimize_cmd import render_optimize_summary

    summary = render_optimize_summary(
        {
            "status": "rejected",
            "selected_candidate_id": "cand-selected",
            "best_candidate_id": None,
        }
    )

    assert "Selected candidate: cand-selected" in summary
    assert "Best candidate:" not in summary


def test_render_optimize_summary_lists_failed_gates_for_rejected_runs() -> None:
    from aworld_cli.top_level_commands.optimize_cmd import render_optimize_summary

    summary = render_optimize_summary(
        {
            "status": "rejected",
            "selected_candidate_id": "cand-selected",
            "gate_results": [
                {"gate_name": "score_improvement", "passed": True},
                {"gate_name": "held_out_verification", "passed": False},
                {"gate_name": "global_regression_benchmark", "passed": False},
            ],
        }
    )

    assert (
        "Rejected gates: held_out_verification, global_regression_benchmark"
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
    assert report["status"] == "rejected"
    assert report["best_candidate_id"] is None
    assert skill_path.read_text(encoding="utf-8").endswith("Old guidance.\n")


def test_framework_cli_request_can_include_prior_runs_as_trainable_cases(
    tmp_path: Path,
) -> None:
    from aworld.self_evolve import optimize_from_cli_request

    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    dataset_path = tmp_path / "eval.jsonl"
    dataset_path.write_text('{"case_id":"case-1","input":"demo"}\n', encoding="utf-8")
    prior_run_dir = tmp_path / ".aworld" / "self_evolve" / "prior-run"
    prior_run_dir.mkdir(parents=True)
    (prior_run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "prior-run",
                "status": "rejected",
                "target": {"target_type": "skill", "target_id": "demo"},
                "selected_candidate_id": "cand-old",
                "gate_results": [
                    {"gate_name": "score_improvement", "passed": False}
                ],
                "baseline_metrics": {"score": 90.0},
                "candidate_metrics": {"score": 80.0},
            }
        ),
        encoding="utf-8",
    )

    report = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        dataset=str(dataset_path),
        apply_policy="proposal",
        include_prior_runs=True,
    )

    recipe = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / report["run_id"]
            / "dataset_recipe.json"
        ).read_text(encoding="utf-8")
    )
    report_payload = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    prior_case_id = "prior-run:prior-run:cand-old"
    assert recipe["source"]["include_prior_runs"] is True
    assert recipe["source"]["prior_run_case_count"] == 1
    assert prior_case_id in recipe["trainable_case_ids"]
    assert prior_case_id in recipe["splits"]["train"]
    assert report_payload["trajectory_set"]["include_prior_runs"] is True
    assert report_payload["trajectory_set"]["prior_run_case_ids"] == [prior_case_id]
