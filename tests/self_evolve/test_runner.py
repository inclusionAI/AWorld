from __future__ import annotations

import json

import pytest

from aworld.self_evolve.datasets import SelfEvolveEvalSourceConfig, build_dataset_from_source
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.runner import SelfEvolveRunner, optimize_explicit_target
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.types import EvaluationSummary


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

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
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

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
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
    assert report["gate_results"][0]["gate_name"] == "score_improvement"


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
