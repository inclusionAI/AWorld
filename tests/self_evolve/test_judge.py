from __future__ import annotations

import json

import pytest

from aworld.config.conf import SelfEvolveJudgeConfig
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.judge import (
    AgentMdJudgeBackend,
    CustomAgentJudgeBackend,
    DefaultTrajectoryJudgeBackend,
    DisabledJudgeBackend,
    JudgeInput,
    build_judge_backend,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.types import (
    EvaluationSummary,
    SelfEvolveRun,
    SelfEvolveTargetRef,
)


def _judge_input() -> JudgeInput:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo-skill")
    trace_pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "Fix browser login guidance."}},
                "action": {"content": "I will inspect the login failure."},
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "agent"},
                "state": {"messages": []},
                "action": {"content": "Browser login guidance was incomplete."},
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="judge-task",
    )
    return JudgeInput(
        trace_pack=trace_pack,
        target_selection=TargetSelectionReport(
            selected_target=target,
            confidence=0.9,
            evidence_step_ids=("judge-task:step-2",),
            failure_category="browser_session",
            signals=("browser_login_profile_mismatch",),
        ),
        baseline=EvaluationSummary(
            variant_id="baseline",
            metrics={"score": 0.3, "command_pass_rate": 0.0},
        ),
        candidate=EvaluationSummary(
            variant_id="cand-1",
            metrics={"score": 0.8, "command_pass_rate": 1.0},
        ),
        scorer_diagnostics={"trajectory_quality": "candidate avoids repeated login loop"},
    )


@pytest.mark.asyncio
async def test_default_trajectory_judge_builds_compact_evidence_record() -> None:
    record = await DefaultTrajectoryJudgeBackend().judge(_judge_input())

    assert record.backend_id == "default_trajectory"
    assert record.prompt.startswith("Evaluate whether a self-evolve candidate")
    assert record.compact_input["trace_pack"]["task_id"] == "judge-task"
    assert record.compact_input["trace_pack"]["evidence_step_ids"] == [
        "judge-task:step-1",
        "judge-task:step-2",
    ]
    assert record.compact_input["target_selection"]["target"] == "skill:demo-skill"
    assert record.verdict.score == 1.0
    assert record.verdict.confidence == "limited"
    assert record.verdict.metadata["judge_only_signal"] is False


@pytest.mark.asyncio
async def test_disabled_judge_returns_no_signal() -> None:
    record = await DisabledJudgeBackend().judge(_judge_input())

    assert record.backend_id == "disabled"
    assert record.verdict.score == 0.0
    assert record.verdict.verdict == "disabled"
    assert record.compact_input == {}


@pytest.mark.asyncio
async def test_agent_md_judge_uses_configured_loader_and_persists_artifact(tmp_path) -> None:
    agent_path = tmp_path / "agent.md"
    agent_path.write_text("# Judge\n\nReturn strict verdicts.\n", encoding="utf-8")
    calls = []

    def load_agent_md(path):
        calls.append(path)

        async def judge(compact_input):
            return {
                "score": 0.7,
                "verdict": "candidate is directionally better",
                "rationale": f"reviewed {compact_input['target_selection']['target']}",
            }

        return judge

    store = FilesystemSelfEvolveStore(tmp_path)
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo-skill")
    store.create_run(SelfEvolveRun(run_id="run-judge", target=target))
    backend = AgentMdJudgeBackend(agent_path=agent_path, loader=load_agent_md)

    record = await backend.judge(_judge_input())
    artifact_path = store.write_judge_record("run-judge", record)

    assert calls == [agent_path]
    assert record.backend_id == "agent_md"
    assert record.verdict.score == 0.7
    assert record.verdict.rationale == "reviewed skill:demo-skill"
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved["backend_id"] == "agent_md"
    assert saved["verdict"]["verdict"] == "candidate is directionally better"


@pytest.mark.asyncio
async def test_custom_agent_judge_runs_through_evaluation_contract() -> None:
    async def custom_agent(compact_input):
        return {"score": 0.4, "verdict": "weak", "metadata": {"agent_id": "judge-1"}}

    backend = CustomAgentJudgeBackend(agent_id="judge-1", agent=custom_agent)
    record = await backend.judge(_judge_input())

    assert record.backend_id == "custom_agent"
    assert record.verdict.score == 0.4
    assert record.verdict.metadata["agent_id"] == "judge-1"
    assert "trace_pack" in record.compact_input


def test_build_judge_backend_respects_config_modes(tmp_path) -> None:
    agent_path = tmp_path / "agent.md"
    agent_path.write_text("# Judge\n", encoding="utf-8")

    assert isinstance(
        build_judge_backend(SelfEvolveJudgeConfig(mode="trajectory")),
        DefaultTrajectoryJudgeBackend,
    )
    assert isinstance(
        build_judge_backend(SelfEvolveJudgeConfig(mode="disabled")),
        DisabledJudgeBackend,
    )
    assert isinstance(
        build_judge_backend(
            SelfEvolveJudgeConfig(mode="agent_md", agent_path=str(agent_path)),
            agent_md_loader=lambda path: (lambda compact_input: {"score": 0.5}),
        ),
        AgentMdJudgeBackend,
    )
    assert isinstance(
        build_judge_backend(
            SelfEvolveJudgeConfig(mode="custom_agent", agent_id="judge-1"),
            custom_agents={"judge-1": lambda compact_input: {"score": 0.5}},
        ),
        CustomAgentJudgeBackend,
    )
