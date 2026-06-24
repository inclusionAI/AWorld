from __future__ import annotations

import json
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import SelfEvolveEvalSourceConfig, build_dataset_from_source
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.runner import SelfEvolveRunner, optimize_explicit_target
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.types import EvaluationSummary


def _write_trajectory_log(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(
            repr(
                {
                    "task_id": record["task_id"],
                    "is_sub_task": False,
                    "trajectory": json.dumps(record["trajectory"]),
                }
            )
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_runner_persists_proposal_artifacts_without_mutating_skill_target(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix browser login guidance."}},
            "action": {"content": "I will inspect login traces."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="run-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="run-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nMention CDP profile mismatch.\n",
            "rationale": "Trace shows browser profile mismatch.",
        }

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
    )

    result = await runner.run_explicit_target(
        run_id="run-proposal",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is not None
    assert skill_path.read_text(encoding="utf-8") == original_content

    run_dir = store.run_path("run-proposal")
    candidate_path = run_dir / "candidates" / f"{result.selected_candidate.candidate_id}.md"
    diff_path = candidate_path.with_suffix(".diff")
    lineage_path = run_dir / "optimizer_lineage" / f"{result.selected_candidate.candidate_id}.json"
    report_path = run_dir / "report.json"

    assert candidate_path.exists()
    assert diff_path.exists()
    assert "-Old guidance." in diff_path.read_text(encoding="utf-8")
    assert "+Mention CDP profile mismatch." in diff_path.read_text(encoding="utf-8")
    assert json.loads(lineage_path.read_text(encoding="utf-8"))["trainable_case_ids"] == [
        "run-task"
    ]
    assert json.loads(report_path.read_text(encoding="utf-8"))["apply_policy"] == "proposal"


@pytest.mark.asyncio
async def test_runner_auto_verified_applies_allowlisted_candidate_after_post_apply_gate(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nVerified guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="apply-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="apply-task",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Verified candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "score": 1.0},
            dataset_split="post_apply",
        )

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-verified",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert skill_path.read_text(encoding="utf-8") == candidate_content
    report = json.loads((store.run_path("run-auto-verified") / "report.json").read_text(encoding="utf-8"))
    assert report["apply_policy"] == "auto_verified"
    assert report["post_apply"]["status"] == "accepted"
    assert report["post_apply"]["metrics"]["post_apply_passed"] is True
    assert {gate["gate_name"] for gate in report["gate_results"]} >= {
        "score_improvement",
        "required_verification",
        "held_out_verification",
        "global_regression_benchmark",
    }


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_without_evaluation_backend(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nUnsafe guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="no-eval-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="no-eval-task",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "No verification."}

    async def post_apply(candidate):
        pytest.fail("auto_verified must not apply before verification gates pass")

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-no-eval",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((store.run_path("run-auto-no-eval") / "report.json").read_text(encoding="utf-8"))
    assert any(
        gate["gate_name"] == "auto_verified_evaluation"
        and gate["passed"] is False
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_failed_candidate_gates_before_apply(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="bad-candidate")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="bad-candidate",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": "# Demo\n\nMissing frontmatter.\n", "rationale": "Malformed."}

    async def post_apply(candidate):
        pytest.fail("malformed auto_verified candidate must not be applied")

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-bad-candidate",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((store.run_path("run-auto-bad-candidate") / "report.json").read_text(encoding="utf-8"))
    assert any(
        gate["gate_name"] == "skill_markdown"
        and gate["passed"] is False
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_rolls_back_when_post_apply_gate_fails(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nRegressing guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="rollback-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="rollback-task",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Regressing candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": False, "score": 0.0},
            dataset_split="post_apply",
        )

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-rollback",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((store.run_path("run-auto-rollback") / "report.json").read_text(encoding="utf-8"))
    assert report["post_apply"]["status"] == "rolled_back"
    assert report["post_apply"]["metrics"]["post_apply_passed"] is False


@pytest.mark.asyncio
async def test_optimize_explicit_target_python_api_uses_framework_runner(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nSDK proposal.\n",
            "rationale": "SDK path candidate.",
        }

    result = await optimize_explicit_target(
        workspace_root=tmp_path,
        run_id="run-sdk",
        target=SkillTextTarget(skill_path),
        current_trajectory=trajectory,
        task_id="sdk-task",
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
    )

    assert result.run.run_id == "run-sdk"
    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is not None
    assert (tmp_path / ".aworld" / "self_evolve" / "run-sdk" / "report.json").exists()


@pytest.mark.asyncio
async def test_runner_orchestrates_baseline_candidate_evaluation_and_gates(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="gate-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="gate-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nBetter guidance.\n",
            "rationale": "Improve guidance.",
        }

    class RecordingBackend:
        def __init__(self):
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.4, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={"score": 0.8, "latency_ms": 110.0, "cost_usd": 1.0},
                dataset_split=request.dataset_split,
            )

    backend = RecordingBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=backend,
    )

    result = await runner.run_explicit_target(
        run_id="run-orchestrated",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
    )

    assert result.run.status.value == "succeeded"
    assert [request.variant_id for request in backend.requests][0] == "baseline"
    assert backend.requests[1].candidate is result.selected_candidate
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-orchestrated" / "report.json").read_text())
    assert report["baseline_metrics"]["score"] == 0.4
    assert report["candidate_metrics"]["score"] == 0.8
    assert any(
        gate["gate_name"] == "score_improvement"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_stops_when_duplicate_pending_proposal_exists(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="stop-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="stop-task",
    )

    async def mutate(prompt: str) -> dict:
        pytest.fail("optimizer should not run when stopping conditions block")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        pending_duplicate=True,
    )

    result = await runner.run_explicit_target(
        run_id="run-stopped",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
    )

    assert result.run.status.value == "rejected"
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-stopped" / "report.json").read_text())
    assert report["stopping_condition"]["reason"] == "duplicate pending proposal exists"


def test_optimize_cli_request_infers_skill_target_from_trajectory_log(tmp_path) -> None:
    from aworld.self_evolve import optimize_from_cli_request

    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = (
        "---\n"
        "name: agent-browser\n"
        "---\n"
        "# Browser Automation\n\n"
        "Keep existing guidance.\n"
    )
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {
                "input": {
                    "content": (
                        "I am definitely logged in. Why do you keep seeing a "
                        "logged-out browser?"
                    )
                },
                "messages": [],
            },
            "action": {
                "content": "I will inspect browser login traces and Chrome profiles.",
                "tool_calls": [],
                "is_agent_finished": False,
            },
            "reward": {"status": "failed"},
        },
        {
            "meta": {"step": 2, "agent_id": "agent", "pre_agent": "agent"},
            "state": {"messages": []},
            "action": {
                "content": "No login traces were found in the checked browser sessions.",
                "tool_calls": [],
                "is_agent_finished": True,
            },
            "reward": {"status": "failed"},
        },
    ]
    trajectory_log = tmp_path / "trajectory.log"
    trajectory_log.write_text(
        repr(
            {
                "task_id": "browser-login-task",
                "is_sub_task": False,
                "trajectory": json.dumps(trajectory),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix browser login",
        target=None,
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=True,
    )

    assert report_summary["status"] == "succeeded"
    assert skill_path.read_text(encoding="utf-8") == original_content

    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_type"] == "skill"
    assert report["target"]["target_id"] == "agent-browser"
    assert report["target_selection"]["confidence"] >= 0.8
    assert report["target_selection"]["evidence_step_ids"]
    assert report["candidate_ids"]
    assert report["selected_candidate_id"] == report["candidate_ids"][0]

    target_selection_path = Path(report_summary["target_selection_path"])
    target_selection = json.loads(target_selection_path.read_text(encoding="utf-8"))
    assert target_selection["selected_target"]["target_id"] == "agent-browser"
    assert target_selection["failure_category"] == "browser_session"

    candidate_path = (
        Path(report_summary["report_path"]).parent
        / "candidates"
        / f"{report['selected_candidate_id']}.md"
    )
    candidate_content = candidate_path.read_text(encoding="utf-8")
    assert candidate_content.startswith("---\nname: agent-browser\n---")
    assert "Self-Evolve Trace Guidance" in candidate_content
    assert "browser-login-task" in candidate_content


def test_optimize_cli_request_persists_unsupported_inferred_target(tmp_path) -> None:
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": "validation-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {
                            "input": {"content": "Validate result anchors before writing."},
                            "messages": [],
                        },
                        "action": {
                            "content": (
                                "Result validation mismatch: required anchors are missing "
                                "from source evidence."
                            ),
                            "tool_calls": [],
                            "is_agent_finished": True,
                        },
                        "reward": {"status": "failed"},
                    }
                ],
            }
        ],
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix validation anchors",
        target=None,
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=True,
    )

    assert report_summary["status"] == "rejected"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_type"] == "prompt-section"
    assert report["target"]["target_id"] == "result-validation-anchor-policy"
    assert report["unsupported_target"]["target_ref"] == "prompt-section:result-validation-anchor-policy"
    assert report["candidate_ids"] == []

    assert Path(report_summary["target_selection_path"]).exists()
    assert Path(report_summary["target_provenance_path"]).exists()


def test_optimize_cli_request_infers_highest_confidence_target_from_trajectory_log(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "agent-browser-cdp-login-guidance" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: agent-browser-cdp-login-guidance\n---\n# Browser Login Guidance\n",
        encoding="utf-8",
    )
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": "browser-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {
                            "input": {
                                "content": "I am logged in but you see a logged-out browser."
                            }
                        },
                        "action": {"content": "I will inspect login traces."},
                        "reward": {"status": "failed"},
                    }
                ],
            },
            {
                "task_id": "validation-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {"input": {"content": "Validate anchors."}},
                        "action": {
                            "content": "Result validation mismatch: anchors are missing.",
                            "tool_calls": [],
                            "is_agent_finished": True,
                        },
                        "reward": {"status": "failed"},
                    }
                ],
            },
        ],
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix validation anchors",
        target=None,
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=True,
    )

    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "rejected"
    assert report["target"]["target_type"] == "prompt-section"
    assert report["target_selection"]["confidence"] == 0.9


def test_optimize_cli_request_infers_target_from_session_trajectory(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: agent-browser\n---\n# Browser Automation\n",
        encoding="utf-8",
    )
    session_log = tmp_path / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    session_log.parent.mkdir(parents=True)
    session_log.write_text(
        json.dumps(
            {
                "session_id": "session-1",
                "task_id": "browser-session-task",
                "input": {"content": "I am logged in but you see a logged-out browser."},
                "final_answer": "No login traces were found in browser sessions.",
                "task_status": "failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix browser login",
        target=None,
        from_session="session-1",
        apply_policy="proposal",
        infer_target=True,
    )

    assert report_summary["status"] == "succeeded"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_id"] == "agent-browser"
    assert report["target_selection"]["evidence_step_ids"] == [
        "browser-session-task:step-1"
    ]


def test_optimize_cli_request_records_explicit_target_trajectory_evidence(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": "explicit-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {"input": {"content": "Fix demo guidance."}},
                        "action": {"content": "Guidance failed."},
                        "reward": {"status": "failed"},
                    }
                ],
            }
        ],
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=False,
    )

    assert Path(report_summary["target_selection_path"]).exists()
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target_selection"]["failure_category"] == "explicit_target"
    assert report["target_selection"]["evidence_step_ids"] == ["explicit-task:step-1"]
