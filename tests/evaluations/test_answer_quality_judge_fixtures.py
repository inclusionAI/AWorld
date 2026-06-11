from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "evaluator_judges"


def test_answer_quality_prompt_fixture_matches_judge_contract() -> None:
    prompt = (FIXTURE_ROOT / "answer_quality_agent.md").read_text(encoding="utf-8")

    assert "Answer Quality Evaluator" in prompt
    assert "Q1_correctness" in prompt
    assert "veto_triggered" in prompt
    assert "最终回复必须是且仅是一个 JSON 对象" in prompt


def test_answer_quality_judge_agent_name_fixture_registers_local_swarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.core.agent_registry import LocalAgentRegistry
    from aworld_cli.core.loader import init_agents

    monkeypatch.setattr(LocalAgentRegistry, "_instance", None)

    init_agents(FIXTURE_ROOT / "agents")

    local_agent = LocalAgentRegistry.get_agent("answer-quality-judge")
    assert local_agent is not None
    assert local_agent.register_dir == str((FIXTURE_ROOT / "agents").resolve())

    swarm = asyncio.run(local_agent.get_swarm(refresh=True))
    judge_agent = swarm.topology[0]
    assert judge_agent.name() == "answer-quality-judge"
    assert "Answer Quality Evaluator" in judge_agent.system_prompt
    assert "Q1_correctness" in judge_agent.system_prompt


def test_answer_quality_backend_ref_fixture_builds_prompt_backed_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    module = importlib.import_module("answer_quality_backend")

    backend = module.build_backend()

    assert backend.backend_id == "answer-quality-fixture-backend"
    assert "Answer Quality Evaluator" in backend.system_prompt
    assert "Q1_correctness" in backend.system_prompt

    execution = asyncio.run(
        backend.execute(
            {"input": "What is AWorld?"},
            {"answer": "AWorld is an agent framework."},
            suite=None,
        )
    )
    assert execution.backend_id == "answer-quality-fixture-backend"
    assert execution.payload["verdict"] == "Pass"
    assert execution.payload["veto_triggered"] is False
