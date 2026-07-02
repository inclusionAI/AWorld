from __future__ import annotations

import json
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import SelfEvolveEvalSourceConfig, build_dataset_from_source
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.replay import (
    CandidateReplayResult,
    ReplayVariantResult,
)
from aworld.self_evolve.runner import (
    SelfEvolveRunner,
    _default_cli_skill_candidate,
    _default_post_apply_evaluator,
    optimize_explicit_target,
    optimize_from_cli_request,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, SelfEvolveTargetRef


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
    candidate_artifact = candidate_path.read_text(encoding="utf-8")
    assert "release_state: candidate" in candidate_artifact
    assert "Mention CDP profile mismatch." in candidate_artifact
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

    refreshed = []

    async def refresh_runtime(candidate):
        refreshed.append(candidate.candidate_id)
        return {"refreshed": True, "strategy": "test-hook"}

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
        runtime_registry_refresher=refresh_runtime,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-verified",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    updated_content = skill_path.read_text(encoding="utf-8")
    assert "self_evolve:" in updated_content
    assert "release_state: verified" in updated_content
    assert "verified_run_id: run-auto-verified" in updated_content
    assert "# Demo\n\nVerified guidance." in updated_content
    report = json.loads((store.run_path("run-auto-verified") / "report.json").read_text(encoding="utf-8"))
    assert report["apply_policy"] == "auto_verified"
    assert report["post_apply"]["status"] == "accepted"
    assert report["post_apply"]["metrics"]["post_apply_passed"] is True
    assert refreshed == [result.selected_candidate.candidate_id]
    assert report["post_apply"]["refresh"] == {"refreshed": True, "strategy": "test-hook"}
    assert {gate["gate_name"] for gate in report["gate_results"]} >= {
        "score_improvement",
        "required_verification",
        "held_out_verification",
        "global_regression_benchmark",
    }
    apply_dir = store.run_path("run-auto-verified") / "apply"
    assert (apply_dir / f"{result.selected_candidate.candidate_id}.backup.md").read_text(encoding="utf-8") == original_content
    journal = json.loads((apply_dir / f"{result.selected_candidate.candidate_id}.journal.json").read_text(encoding="utf-8"))
    assert journal["candidate_id"] == result.selected_candidate.candidate_id
    assert journal["status"] == "accepted"
    assert journal["target"]["target_id"] == "demo"
    assert journal["backup_path"].endswith(".backup.md")


@pytest.mark.asyncio
async def test_runner_refines_candidates_across_iterations_with_validation_feedback(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="iter-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="iter-task",
    )
    bad_candidate = CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nWeak guidance.\n",
        rationale="first attempt",
        target_fingerprint="fingerprint",
    )
    good_candidate = CandidateVariant(
        candidate_id="candidate-2",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nVerified guidance.\n",
        rationale="refined attempt",
        parent_candidate_ids=("candidate-1",),
        target_fingerprint="fingerprint",
    )

    class IteratingOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            self.requests.append(request)
            candidate = bad_candidate if len(self.requests) == 1 else good_candidate
            return OptimizerResult(candidates=(candidate,))

    class IteratingBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            score = 0.4 if request.candidate.candidate_id == "candidate-1" else 0.9
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": score,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": score >= 0.9,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    optimizer = IteratingOptimizer()
    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=optimizer,
        post_apply_evaluator=post_apply,
        evaluation_backend=IteratingBackend(),
        min_eval_cases=0,
        max_iterations=2,
    )

    result = await runner.run_explicit_target(
        run_id="run-iterative",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is good_candidate
    assert len(optimizer.requests) == 2
    assert optimizer.requests[0].validation_feedback == ()
    assert optimizer.requests[1].validation_feedback
    assert optimizer.requests[1].validation_feedback[0].variant_id == "candidate-1"
    assert optimizer.requests[1].validation_feedback[0].metrics["score"] == 0.4
    assert "Verified guidance." in skill_path.read_text(encoding="utf-8")
    report = json.loads((store.run_path("run-iterative") / "report.json").read_text(encoding="utf-8"))
    assert report["candidate_ids"] == ["candidate-1", "candidate-2"]
    assert report["selected_candidate_id"] == "candidate-2"
    assert report["iterations"][0]["candidate_id"] == "candidate-1"
    assert report["iterations"][0]["status"] == "rejected"
    assert report["iterations"][1]["candidate_id"] == "candidate-2"
    assert report["iterations"][1]["status"] == "accepted"


@pytest.mark.asyncio
async def test_runner_uses_prior_rejected_candidate_feedback_across_runs(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-run"
    historical_dir.mkdir(parents=True)
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "rejected",
                "candidate_ids": ["candidate-dup"],
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-dup",
                        "status": "rejected",
                        "candidate_metrics": {
                            "score": 35.0,
                            "A1_groundedness": 1.0,
                            "evidence_compacted": True,
                        },
                        "failed_gates": ["evidence_quality", "score_improvement"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="history-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="history-task",
    )
    duplicate_candidate = CandidateVariant(
        candidate_id="candidate-dup",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nDuplicate guidance.\n",
        rationale="repeated historical failure",
        target_fingerprint="fingerprint",
    )
    fresh_candidate = CandidateVariant(
        candidate_id="candidate-fresh",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nFresh guidance.\n",
        rationale="uses historical failure feedback",
        target_fingerprint="fingerprint",
    )

    class HistoryAwareOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            self.requests.append(request)
            return OptimizerResult(
                candidates=(
                    duplicate_candidate
                    if len(self.requests) == 1
                    else fresh_candidate,
                )
            )

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.2, "latency_ms": 100.0, "cost_usd": 1.0},
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

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    optimizer = HistoryAwareOptimizer()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=optimizer,
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
        max_iterations=2,
    )

    result = await runner.run_explicit_target(
        run_id="new-run",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is fresh_candidate
    assert len(optimizer.requests) == 2
    assert optimizer.requests[0].prior_feedback
    assert optimizer.requests[0].prior_feedback[0].variant_id == "candidate-dup"
    assert optimizer.requests[0].prior_feedback[0].metrics["failed_gates"] == [
        "evidence_quality",
        "score_improvement",
    ]
    assert optimizer.requests[1].validation_feedback[0].metrics["failed_gates"] == [
        "duplicate_rejected_candidate"
    ]
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "new-run" / "report.json").read_text())
    assert report["iterations"][0]["status"] == "rejected"
    assert report["iterations"][0]["failed_gates"] == ["duplicate_rejected_candidate"]
    assert report["iterations"][1]["status"] == "accepted"


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
    journal = json.loads(Path(report["post_apply"]["journal_path"]).read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_skill_candidate_when_replay_backend_missing(
    tmp_path,
) -> None:
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
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="replay-required")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="replay-required",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Candidate requires replay.",
        }

    async def post_apply(candidate):
        pytest.fail("auto_verified must not apply when replay backend is missing")

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 1.0 if request.candidate else 0.1,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=VerifiedBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=None,
    )

    result = await runner.run_explicit_target(
        run_id="run-replay-required",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-replay-required" / "report.json").read_text())
    assert any(
        gate["gate_name"] == "candidate_replay"
        and gate["passed"] is False
        and gate["reason"] == "auto_verified skill apply requires candidate replay backend"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_uses_candidate_replay_dataset_for_evaluation(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    helper_path = tmp_path / "skills" / "helper" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    helper_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    helper_path.write_text("---\nname: helper\n---\n# Helper\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="replay-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="replay-task",
    )

    candidate_content = "---\nname: demo\n---\n# Demo\n\nReplay verified guidance.\n"

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Replay verified candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "deterministic_signal": True},
            dataset_split="post_apply",
        )

    class FakeReplayBackend:
        def __init__(self):
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            baseline_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"baseline-{index}",
                    status="succeeded",
                    trajectory=[
                        {
                            "state": {"input": request.task_input},
                            "action": {"content": f"old-{index}"},
                        }
                    ],
                )
                for index in range(1, request.baseline_repetitions + 1)
            )
            candidate_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"{candidate.candidate_id}-{index}",
                    status="succeeded",
                    trajectory=[
                        {
                            "state": {"input": request.task_input},
                            "action": {"content": f"new-{index}"},
                        }
                    ],
                )
                for index in range(1, request.candidate_repetitions + 1)
            )
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=baseline_repetitions[-1].trajectory,
                    metrics={
                        "score": 0.4,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": len(baseline_repetitions),
                        "successful_repetition_count": len(baseline_repetitions),
                    },
                    repetition_results=baseline_repetitions,
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=candidate_repetitions[-1].trajectory,
                    metrics={
                        "score": 0.9,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": len(candidate_repetitions),
                        "successful_repetition_count": len(candidate_repetitions),
                    },
                    repetition_results=candidate_repetitions,
                ),
            )

    class PairedDatasetBackend:
        def __init__(self):
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            if request.candidate is None:
                baseline_outputs = [
                    case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
                    for case in request.dataset.cases
                ]
                assert baseline_outputs == ["old-1", "old-2", "old-1"]
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.4,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": len(request.dataset.cases),
                        "command_pass_count": len(request.dataset.cases),
                        "global_regression_passed": True,
                        "report_path": str(tmp_path / "baseline-eval-report.json"),
                    },
                    dataset_split=request.dataset_split,
                )
            candidate_outputs = [
                case.metadata["variant_trajectories"][request.candidate.candidate_id][0][
                    "action"
                ]["content"]
                for case in request.dataset.cases
            ]
            assert candidate_outputs == ["new-1", "new-2", "new-3"]
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "global_regression_passed": True,
                    "report_path": str(tmp_path / "candidate-eval-report.json"),
                },
                dataset_split=request.dataset_split,
            )

    replay_backend = FakeReplayBackend()
    evaluation_backend = PairedDatasetBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
        replay_stability_margin=0.1,
    )

    result = await runner.run_explicit_target(
        run_id="run-replay-eval",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    applied_content = skill_path.read_text(encoding="utf-8")
    assert "release_state: verified" in applied_content
    assert "verified_run_id: run-replay-eval" in applied_content
    assert "# Demo\n\nReplay verified guidance." in applied_content
    assert replay_backend.requests
    assert replay_backend.requests[0].baseline_repetitions == 2
    assert replay_backend.requests[0].candidate_repetitions == 3
    assert Path(replay_backend.requests[0].overlay_skill_root, "demo", "SKILL.md").read_text(encoding="utf-8") == candidate_content
    assert Path(replay_backend.requests[0].overlay_skill_root, "helper", "SKILL.md").exists()
    assert all(
        request.dataset.cases[0].metadata["variant_trajectories"]
        for request in evaluation_backend.requests
    )
    assert len(evaluation_backend.requests[0].dataset.cases) == 3
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-replay-eval" / "report.json").read_text())
    assert report["replay"]["candidate"]["status"] == "succeeded"
    assert report["replay"]["overlay_skill_root"] == replay_backend.requests[0].overlay_skill_root
    assert "/run-replay-eval/replay/llm-mutator-" in report["replay_path"]
    assert str(tmp_path / "candidate-eval-report.json") in report["evaluator_report_paths"]
    assert any(
        gate["gate_name"] == "candidate_replay" and gate["passed"] is True
        for gate in report["gate_results"]
    )
    assert any(
        gate["gate_name"] == "replay_stability_margin"
        and gate["passed"] is True
        and gate["details"]["delta"] == 0.5
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_accepts_stable_single_case_replay(
    tmp_path,
) -> None:
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
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="single-replay-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="single-replay-task",
    )
    candidate_content = "---\nname: demo\n---\n# Demo\n\nStable single-case replay guidance.\n"

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Stable replay candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "deterministic_signal": True},
            dataset_split="post_apply",
        )

    class StableReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[
                        {"state": {"input": request.task_input}, "action": {"content": "old"}}
                    ],
                    metrics={
                        "score": 0.3,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": request.baseline_repetitions,
                        "successful_repetition_count": request.baseline_repetitions,
                    },
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[
                        {"state": {"input": request.task_input}, "action": {"content": "new"}}
                    ],
                    metrics={
                        "score": 0.9,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": request.candidate_repetitions,
                        "successful_repetition_count": request.candidate_repetitions,
                    },
                ),
            )

    evaluation_calls = []

    class PositiveReplayEvaluationBackend:
        async def evaluate_variant(self, request):
            evaluation_calls.append((request.variant_id, request.dataset_split))
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.3,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": 1,
                        "command_pass_count": 1,
                        "global_regression_passed": True,
                    },
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

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=PositiveReplayEvaluationBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=30,
        replay_enabled=True,
        candidate_replay_backend=StableReplayBackend(),
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    result = await runner.run_explicit_target(
        run_id="run-single-case-replay-verified",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert skill_path.read_text(encoding="utf-8") != original_content
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-single-case-replay-verified"
            / "report.json"
        ).read_text()
    )
    assert report["post_apply"]["status"] == "accepted"
    assert any(
        gate["gate_name"] == "held_out_verification"
        and gate["passed"] is True
        and gate["details"]["verification_mode"] == "single_case_replay"
        for gate in report["gate_results"]
    )
    assert evaluation_calls == [
        ("baseline", "validation"),
        (report["selected_candidate_id"], "validation"),
    ]


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_compacted_evaluator_evidence(
    tmp_path,
) -> None:
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
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="compacted-evidence-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="compacted-evidence-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Candidate with compacted evidence.",
        }

    async def post_apply(candidate):
        pytest.fail("compacted evaluator evidence must not auto apply")

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            baseline_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"baseline-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"old-{index}"}}],
                )
                for index in range(1, request.baseline_repetitions + 1)
            )
            candidate_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"{candidate.candidate_id}-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"new-{index}"}}],
                )
                for index in range(1, request.candidate_repetitions + 1)
            )
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=baseline_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(baseline_repetitions),
                        "successful_repetition_count": len(baseline_repetitions),
                    },
                    repetition_results=baseline_repetitions,
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=candidate_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(candidate_repetitions),
                        "successful_repetition_count": len(candidate_repetitions),
                    },
                    repetition_results=candidate_repetitions,
                ),
            )

    class CompactedEvidenceBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.2,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": len(request.dataset.cases),
                        "command_pass_count": len(request.dataset.cases),
                        "global_regression_passed": True,
                        "has_evidence": 1.0,
                        "evidence_block_count": 1,
                        "evidence_compacted": False,
                    },
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "global_regression_passed": True,
                    "has_evidence": 1.0,
                    "evidence_block_count": 1,
                    "evidence_compacted": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=CompactedEvidenceBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=30,
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    result = await runner.run_explicit_target(
        run_id="run-compacted-evidence",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-compacted-evidence"
            / "report.json"
        ).read_text()
    )
    assert any(
        gate["gate_name"] == "evidence_quality"
        and gate["passed"] is False
        and gate["reason"] == "evaluation evidence is compacted or incomplete"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_single_candidate_rerun_without_baseline_rerun(
    tmp_path,
) -> None:
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
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="limited-replay")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="limited-replay",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Limited replay candidate.",
        }

    async def post_apply(candidate):
        pytest.fail("limited confidence replay must not apply")

    class LimitedReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "historical baseline"}}],
                    metrics={"replay_source": "historical", "score": 0.2},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate rerun"}}],
                    metrics={"repetition_count": 1, "score": 0.9},
                ),
            )

    class PositiveBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.2,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": 1,
                        "command_pass_count": 1,
                        "global_regression_passed": True,
                    },
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

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=PositiveBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=LimitedReplayBackend(),
    )

    result = await runner.run_explicit_target(
        run_id="run-limited-replay",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-limited-replay" / "report.json").read_text())
    assert any(
        gate["gate_name"] == "replay_confidence"
        and gate["passed"] is False
        and gate["reason"] == "fixed historical baseline plus one candidate rerun is limited confidence"
        for gate in report["gate_results"]
    )


def test_default_post_apply_evaluator_requires_runtime_loader_match(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    candidate_content = "---\nname: demo\n---\n# Demo\n\nApplied guidance.\n"
    skill_path.write_text(candidate_content, encoding="utf-8")
    target = SkillTextTarget(skill_path, target_id="missing", allow_auto_apply=True)
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="missing", path=str(skill_path)),
        content=candidate_content,
        rationale="loader mismatch",
    )

    assert target.load_current_content() == candidate_content

    summary = _default_post_apply_evaluator(target)(candidate)

    assert summary.metrics["post_apply_passed"] is False
    assert summary.metrics["content_matches_target_file"] is True
    assert summary.metrics["runtime_skill_found"] is False
    assert summary.metrics["evaluator_mode"] == "post_apply_runtime_loader"


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
async def test_runner_rejects_auto_verified_candidate_when_evaluation_backend_fails(
    tmp_path,
) -> None:
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
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="eval-failure-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="eval-failure-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nBetter guidance.\n",
            "rationale": "Improve guidance.",
        }

    class BrokenBackend:
        async def evaluate_variant(self, request):
            raise ValueError("judge response does not contain a valid JSON object")

    async def post_apply(candidate):
        pytest.fail("evaluation failure must block auto apply")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=BrokenBackend(),
        post_apply_evaluator=post_apply,
    )

    result = await runner.run_explicit_target(
        run_id="run-eval-failure",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-eval-failure" / "report.json").read_text())
    assert any(
        gate["gate_name"] == "evaluation"
        and gate["passed"] is False
        and gate["details"]["type"] == "ValueError"
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
    assert target_selection["failure_category"] == "skill"

    candidate_path = (
        Path(report_summary["report_path"]).parent
        / "candidates"
        / f"{report['selected_candidate_id']}.md"
    )
    candidate_content = candidate_path.read_text(encoding="utf-8")
    assert candidate_content.startswith("---\nname: agent-browser\nself_evolve:")
    assert "release_state: candidate" in candidate_content
    assert "Self-Evolve Trace Guidance" in candidate_content
    assert "browser-login-task" in candidate_content


def test_default_cli_skill_candidate_includes_iteration_feedback() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "validation_feedback": [
                    {
                        "variant_id": "candidate-1",
                        "dataset_split": "held_out",
                        "metrics": {
                            "score": 68.0,
                            "failed_gates": [
                                "held_out_verification",
                                "global_regression_benchmark",
                            ],
                        },
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "Previous validation feedback" in candidate_content
    assert "score=68.0" in candidate_content
    assert "held_out_verification" in candidate_content


def test_default_cli_skill_candidate_turns_compacted_evidence_feedback_into_preservation_guidance() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "variant_id": "candidate-1",
                        "dataset_split": "historical",
                        "metrics": {
                            "score": 45.0,
                            "failed_gates": ["evidence_quality"],
                            "evidence_compacted": True,
                            "evidence_incomplete": True,
                            "evidence_issues": [
                                "tool output compacted for context reuse",
                                "final answer claims not verifiable from available evidence",
                            ],
                        },
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "Evidence preservation requirements" in candidate_content
    assert "Do not stream large raw pages" in candidate_content
    assert "large or unknown-size sources" in candidate_content
    assert "line-based previews" in candidate_content
    assert "Save full raw evidence to a file" in candidate_content
    assert "bounded JSON summary" in candidate_content
    assert "small, verifiable extracts" in candidate_content
    assert "evidence_compacted=True" in candidate_content


def test_auto_verified_inferred_target_can_create_new_skill_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: agent-browser\n---\n# Browser\n\nOriginal guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {
                "input": {
                    "content": (
                        "Summarize this podcast page with grounded evidence: "
                        "https://www.xiaoyuzhoufm.com/episode/6a26b911b30e1571aea2c09d"
                    )
                }
            },
            "action": {"content": "The final answer drifted away from the podcast evidence."},
            "reward": {"status": "failed"},
        }
    ]
    draft_skill_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "drafts"
        / "skills"
        / "web-content-grounding"
        / "SKILL.md"
    )
    new_skill_path = tmp_path / "aworld-skills" / "web-content-grounding" / "SKILL.md"
    inferred_target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="web-content-grounding",
        path=str(draft_skill_path),
    )

    def fake_infer_target_from_trace_packs(trace_packs, *, workspace_root):
        return (
            TargetSelectionReport(
                selected_target=inferred_target,
                confidence=0.85,
                evidence_step_ids=("weak-task:step-1",),
                failure_category="skill",
                signals=("low_confidence", "new_skill_candidate"),
                diagnostics={"rationale": "task needs a dedicated grounded web-summary skill"},
            ),
            None,
        )

    class FakeReplayBackend:
        def __init__(self):
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            baseline_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"baseline-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"baseline-{index}"}}],
                )
                for index in range(1, request.baseline_repetitions + 1)
            )
            candidate_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"{candidate.candidate_id}-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"candidate-{index}"}}],
                )
                for index in range(1, request.candidate_repetitions + 1)
            )
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=baseline_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(baseline_repetitions),
                        "successful_repetition_count": len(baseline_repetitions),
                    },
                    repetition_results=baseline_repetitions,
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=candidate_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(candidate_repetitions),
                        "successful_repetition_count": len(candidate_repetitions),
                    },
                    repetition_results=candidate_repetitions,
                ),
            )

    class PositiveEvaluationBackend:
        def __init__(self):
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            score = 0.4 if request.candidate is None else 0.9
            return EvaluationSummary(
                variant_id=request.variant_id,
                dataset_split=request.dataset_split,
                metrics={
                    "score": score,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "global_regression_passed": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "report_path": str(tmp_path / f"{request.variant_id}-{request.dataset_split}.json"),
                },
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            dataset_split="post_apply",
            metrics={"post_apply_passed": True},
        )

    monkeypatch.setattr(
        runner_module,
        "_infer_target_from_trace_packs",
        fake_infer_target_from_trace_packs,
    )

    replay_backend = FakeReplayBackend()
    evaluation_backend = PositiveEvaluationBackend()
    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        current_trajectory=trajectory,
        task="weak-task",
        target=None,
        apply_policy="auto_verified",
        infer_target=True,
        candidate_replay_backend=replay_backend,
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    assert report_summary["status"] == "succeeded"
    assert replay_backend.requests
    assert replay_backend.requests[0].baseline_repetitions == 2
    assert replay_backend.requests[0].candidate_repetitions == 3
    assert replay_backend.requests[0].baseline_skill_root is None
    assert Path(
        replay_backend.requests[0].overlay_skill_root,
        "web-content-grounding",
        "SKILL.md",
    ).exists()
    assert skill_path.read_text(encoding="utf-8") == original_content
    assert [request.dataset_split for request in evaluation_backend.requests] == [
        "validation",
        "validation",
    ]
    assert new_skill_path.exists()
    assert "release_state: verified" in new_skill_path.read_text(encoding="utf-8")
    assert "web-content-grounding" in new_skill_path.read_text(encoding="utf-8")
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["apply_policy"] == "auto_verified"
    assert report["target"]["target_id"] == "web-content-grounding"
    assert report["target"]["path"] == str(draft_skill_path)
    assert report["candidate_ids"]
    assert report["selected_candidate_id"] == report["candidate_ids"][0]
    assert report["target_selection"]["selected_target"]["target_id"] == "web-content-grounding"
    assert report["target_selection"]["selected_target"]["path"] == str(draft_skill_path)
    assert report["target_selection"]["confidence"] == 0.85
    assert "low_confidence" in report["target_selection"]["signals"]
    assert report["replay"]["baseline"]["status"] == "succeeded"
    assert report["replay"]["candidate"]["status"] == "succeeded"
    assert any(
        gate["gate_name"] == "held_out_verification" and gate["passed"] is True
        for gate in report["gate_results"]
    )
    assert report["post_apply"]["status"] == "accepted"


def test_auto_verified_inferred_target_blocks_low_confidence_auto_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: agent-browser\n---\n# Browser\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix browser workflow."}},
            "action": {"content": "Browser alias matched, but evidence is weak."},
            "reward": {"status": "failed"},
        }
    ]
    inferred_target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="agent-browser",
        path=str(skill_path),
    )

    def fake_infer_target_from_trace_packs(trace_packs, *, workspace_root):
        return (
            TargetSelectionReport(
                selected_target=inferred_target,
                confidence=0.85,
                evidence_step_ids=("weak-task:step-1",),
                failure_category="skill",
                signals=("low_confidence", "skill_alias_match:agent-browser"),
                diagnostics={"matched_aliases": ["browser"]},
            ),
            None,
        )

    def fail_if_target_is_loaded(*args, **kwargs):
        pytest.fail("low-confidence inferred targets must not be loaded for auto apply")

    monkeypatch.setattr(
        runner_module,
        "_infer_target_from_trace_packs",
        fake_infer_target_from_trace_packs,
    )
    monkeypatch.setattr(runner_module, "_target_from_ref", fail_if_target_is_loaded)

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        current_trajectory=trajectory,
        task="weak-task",
        target=None,
        apply_policy="auto_verified",
        infer_target=True,
    )

    assert report_summary["status"] == "rejected"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_type"] == "no_target"
    assert report["target_selection"]["selected_target"] is None
    assert report["target_selection"]["confidence"] == 0.85
    assert report["target_selection"]["no_target_reason"] == (
        "auto_verified target inference requires confidence >= 0.9 without low_confidence signal"
    )
    assert "auto_verified_low_confidence_blocked" in report["target_selection"]["signals"]
    assert report["target_selection"]["diagnostics"]["blocked_selected_target"]["target_id"] == "agent-browser"


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


def test_optimize_cli_request_uses_framework_default_replay_backend_when_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from aworld.self_evolve import optimize_from_cli_request

    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Use demo skill for this task."}},
            "action": {"content": "demo failed"},
            "reward": {"status": "failed"},
        }
    ]
    created = {"count": 0}
    replay_agents = []
    replay_max_steps = []

    class FakeDefaultReplayBackend:
        def __init__(self):
            created["count"] += 1

        async def replay_candidate(self, request, *, candidate, dataset):
            from aworld.self_evolve.replay import CandidateReplayResult, ReplayVariantResult

            replay_agents.append(request.agent)
            replay_max_steps.append(request.max_steps)
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "old"}}],
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="failed",
                    trajectory=[],
                    failure={"reason": "fake replay rejection"},
                ),
            )

    monkeypatch.setattr(
        "aworld.self_evolve.runner.AWorldCliCandidateReplayBackend",
        FakeDefaultReplayBackend,
    )

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        agent="Aworld",
        target="skill:demo",
        current_trajectory=trajectory,
        task="default-replay",
        apply_policy="auto_verified",
        replay_enabled=True,
        min_eval_cases=0,
    )

    assert created["count"] == 1
    assert replay_agents == ["Aworld"]
    assert replay_max_steps == [1]
    assert report_summary["best_candidate_id"] is None
    assert report_summary["selected_candidate_id"] is not None
    assert any(
        gate["gate_name"] == "candidate_replay" and gate["passed"] is False
        for gate in report_summary["gate_results"]
    )
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "rejected"
    assert report["replay"]["candidate"]["failure"] == {"reason": "fake replay rejection"}


def test_optimize_cli_request_auto_verified_smoke_applies_and_loads_real_skill(tmp_path: Path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": f"release-smoke-{index}",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {"input": {"content": f"Improve demo guidance {index}."}},
                        "action": {"content": "demo guidance failed"},
                        "reward": {"status": "failed"},
                    }
                ],
            }
            for index in range(5)
        ],
    )

    class SuccessfulReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[
                        {"state": {"input": request.task_input}, "action": {"content": "old"}}
                    ],
                    metrics={"repetition_count": 1},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[
                        {"state": {"input": request.task_input}, "action": {"content": "new"}}
                    ],
                    metrics={"repetition_count": 1},
                ),
            )

    class VerifiedEvaluationBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.2, "latency_ms": 100.0, "cost_usd": 1.0},
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

    refresh_calls = []

    def refresh_runtime(candidate):
        refresh_calls.append(candidate.candidate_id)
        return {"status": "refreshed", "runtime_skill_count": 1}

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        from_trajectory=str(trajectory_log),
        apply_policy="auto_verified",
        replay_enabled=True,
        candidate_replay_backend=SuccessfulReplayBackend(),
        evaluation_backend=VerifiedEvaluationBackend(),
        min_eval_cases=1,
        runtime_registry_refresher=refresh_runtime,
    )

    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    updated_content = skill_path.read_text(encoding="utf-8")
    candidate_id = report_summary["best_candidate_id"]

    assert report_summary["status"] == "succeeded"
    assert candidate_id == report["selected_candidate_id"]
    assert updated_content != original_content
    assert "release_state: verified" in updated_content
    assert f"verified_run_id: {report['run_id']}" in updated_content
    assert "Self-Evolve Trace Guidance" in updated_content
    assert report["post_apply"]["status"] == "accepted"
    assert refresh_calls == [candidate_id]
    assert report["post_apply"]["refresh"] == {
        "status": "refreshed",
        "runtime_skill_count": 1,
    }
    assert report["post_apply"]["metrics"]["post_apply_passed"] is True
    assert report["post_apply"]["metrics"]["runtime_content_matches"] is True
    assert report["post_apply"]["metrics"]["loaded_from_real_path"] is True
    assert Path(report["post_apply"]["backup_path"]).exists()
    journal = json.loads(Path(report["post_apply"]["journal_path"]).read_text(encoding="utf-8"))
    assert journal["status"] == "accepted"
    assert journal["target"]["target_id"] == "demo"
