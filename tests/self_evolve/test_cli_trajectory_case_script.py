from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


def _load_script_module():
    script_path = Path("scripts/self_evolve_cli_trajectory_case.py")
    spec = importlib.util.spec_from_file_location("self_evolve_cli_trajectory_case", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_trajectory_case_script_reports_pass_for_supported_skill_candidate_with_metrics(
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    task_id = "task_20260609193335"
    raw_log = tmp_path / "trajectory.log"
    trajectory = [
        {
            "id": "step-a",
            "meta": {"task_id": task_id, "step": 1},
            "state": {"input": {"content": "podcast task"}},
            "action": {
                "content": "trying browser",
                "tool_calls": [
                    {
                        "function": {
                            "name": "mcp",
                            "arguments": "{\"command\":\"agent-browser --cdp 9222 open\"}",
                        }
                    }
                ],
            },
            "reward": {},
        },
        {
            "id": "step-b",
            "meta": {"task_id": task_id, "step": 2},
            "state": {"messages": [{"role": "tool", "content": "show notes"}]},
            "action": {"content": "final answer", "is_agent_finished": "True"},
            "reward": {},
        },
    ]
    raw_log.write_text(
        " | prefix before record\n"
        + repr(
            {
                "task_id": task_id,
                "is_sub_task": False,
                "trajectory": json.dumps(trajectory),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run_cli(command, env, cwd):
        run_dir = tmp_path / ".aworld" / "self_evolve" / "cli-test"
        run_dir.mkdir(parents=True)
        target_selection = {
            "selected_target": {
                "target_type": "skill",
                "target_id": "agent-browser",
                "path": str(tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"),
            },
            "confidence": 0.85,
            "failure_category": "browser_config",
            "signals": ["browser_cdp_profile_config"],
            "evidence_step_ids": [f"{task_id}:step-a"],
            "no_target_reason": None,
            "diagnostics": {"pack_id": f"trajectory_log:{task_id}"},
        }
        report = {
            "run_id": "cli-test",
            "status": "succeeded",
            "target": target_selection["selected_target"],
            "candidate_ids": ["llm-mutator-demo"],
            "selected_candidate_id": "llm-mutator-demo",
            "apply_policy": "proposal",
            "target_selection": target_selection,
            "baseline_metrics": {"score": 0.4},
            "candidate_metrics": {"score": 0.7},
            "gate_results": [{"gate_name": "noop_candidate", "passed": True}],
        }
        (run_dir / "target_selection.json").write_text(
            json.dumps(target_selection),
            encoding="utf-8",
        )
        (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        assert "optimize" in command
        assert "--from-trajectory" in command
        assert "--target" not in command
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Optimize run submitted.\n"
                "Status: succeeded\n"
                f"Report: {run_dir / 'report.json'}\n"
                f"Target selection: {run_dir / 'target_selection.json'}\n"
                "Best candidate: llm-mutator-demo\n"
            ),
            stderr="",
        )

    result = module.run_test_case(
        trajectory_log=raw_log,
        task_id=task_id,
        out_dir=tmp_path / "out",
        workspace_root=tmp_path,
        evaluator_agent_md=None,
        run_cli=fake_run_cli,
    )

    assert result["self_evolve"]["status"] == "succeeded"
    assert result["self_evolve"]["target_ref"] == "skill:agent-browser"
    assert result["evaluation"]["design_goal_satisfied"] is True
    assert result["evaluation"]["verdict"] == "Pass"
    assert result["evaluation"]["reasons"] == []
    assert Path(result["artifacts"]["filtered_trajectory_log"]).exists()
    assert Path(result["artifacts"]["json_report"]).exists()
    assert Path(result["artifacts"]["markdown_report"]).exists()


def test_cli_trajectory_case_script_can_route_configured_evaluator_agent(
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    task_id = "task-evaluator"
    raw_log = tmp_path / "trajectory.log"
    raw_log.write_text(
        repr(
            {
                "task_id": task_id,
                "is_sub_task": False,
                "trajectory": json.dumps(
                    [
                        {
                            "state": {"input": {"content": "workflow task"}},
                            "action": {"content": "final", "is_agent_finished": "True"},
                            "reward": {},
                        }
                    ]
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: trajectory-judge\n---\nJudge trajectory quality.\n", encoding="utf-8")
    captured = {}

    def fake_run_cli(command, env, cwd):
        captured["command"] = command
        run_dir = tmp_path / ".aworld" / "self_evolve" / "cli-test"
        run_dir.mkdir(parents=True)
        report = {
            "run_id": "cli-test",
            "status": "succeeded",
            "target": {"target_type": "skill", "target_id": "workflow-helper"},
            "candidate_ids": ["candidate"],
            "selected_candidate_id": "candidate",
            "baseline_metrics": {"score": 55.0},
            "candidate_metrics": {"score": 88.0, "evaluator_mode": "aworld_trajectory_evaluator"},
        }
        (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"Optimize run submitted.\nStatus: succeeded\nReport: {run_dir / 'report.json'}\n",
            stderr="",
        )

    result = module.run_test_case(
        trajectory_log=raw_log,
        task_id=task_id,
        out_dir=tmp_path / "out",
        workspace_root=tmp_path,
        evaluator_agent_md=judge_agent,
        apply_policy="auto_verified",
        run_cli=fake_run_cli,
    )

    assert "--judge-agent" in captured["command"]
    assert str(judge_agent) in captured["command"]
    assert "--apply" in captured["command"]
    assert "auto_verified" in captured["command"]
    assert result["evaluator_agent"]["usage"] == "aworld_trajectory_evaluator"
