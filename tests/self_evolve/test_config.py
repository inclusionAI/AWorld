from __future__ import annotations

import pytest
from pydantic import ValidationError

from aworld.config.conf import AgentConfig, SelfEvolveConfig, SelfEvolveJudgeConfig


def test_agent_config_disables_self_evolve_by_default() -> None:
    config = AgentConfig()

    assert config.self_evolve_config.mode == "off"
    assert not hasattr(config.self_evolve_config, "enabled")
    assert not hasattr(config, "optimize")
    assert config.self_evolve_config.max_run_tokens == 500_000
    assert config.self_evolve_config.min_eval_cases == 30
    assert config.self_evolve_config.judge_repetitions == 3
    assert config.self_evolve_config.auto_apply_target_types == ("skill",)
    assert config.self_evolve_config.require_deterministic_signal_for_verified is True
    assert config.self_evolve_config.max_iterations == 1
    assert config.self_evolve_config.max_background_jobs == 1


@pytest.mark.parametrize("mode", ["off", "offline", "shadow"])
def test_self_evolve_modes_accept_non_online_without_verified_apply(mode: str) -> None:
    config = SelfEvolveConfig(mode=mode)

    assert config.mode == mode
    assert config.apply_policy == "proposal"


def test_self_evolve_online_requires_auto_verified_apply_policy() -> None:
    with pytest.raises(ValidationError, match="online self-evolve requires apply_policy='auto_verified'"):
        SelfEvolveConfig(mode="online")

    config = SelfEvolveConfig(mode="online", apply_policy="auto_verified")

    assert config.mode == "online"
    assert config.apply_policy == "auto_verified"
    assert config.requires_post_apply_reevaluation is True


def test_self_evolve_budget_fields_parse() -> None:
    config = SelfEvolveConfig(
        mode="shadow",
        max_run_tokens=50_000,
        max_run_cost_usd=1.25,
        min_eval_cases=5,
        judge_repetitions=3,
        cooldown_seconds=600,
        auto_apply_target_types=("skill", "prompt-section"),
        require_deterministic_signal_for_verified=False,
        regression_benchmarks=("global",),
        max_iterations=2,
        min_improvement=0.1,
        target_types=("skill", "tool-description"),
        eval_sources=("jsonl", "trajectory_log"),
        max_background_jobs=2,
    )

    assert config.max_run_tokens == 50_000
    assert config.max_run_cost_usd == 1.25
    assert config.min_eval_cases == 5
    assert config.judge_repetitions == 3
    assert config.cooldown_seconds == 600
    assert config.auto_apply_target_types == ("skill", "prompt-section")
    assert config.require_deterministic_signal_for_verified is False
    assert config.regression_benchmarks == ("global",)
    assert config.max_iterations == 2
    assert config.min_improvement == 0.1
    assert config.target_types == ("skill", "tool-description")
    assert config.eval_sources == ("jsonl", "trajectory_log")
    assert config.max_background_jobs == 2


@pytest.mark.parametrize(
    ("payload", "expected_mode"),
    [
        ({}, "trajectory"),
        ({"mode": "agent_md", "agent_path": "agent.md"}, "agent_md"),
        ({"mode": "custom_agent", "agent_id": "judge-agent"}, "custom_agent"),
        ({"mode": "backend_ref", "backend_ref": "pkg.module:build_judge"}, "backend_ref"),
        ({"mode": "disabled"}, "disabled"),
    ],
)
def test_self_evolve_judge_config_modes_parse(payload: dict, expected_mode: str) -> None:
    config = SelfEvolveJudgeConfig(**payload)

    assert config.mode == expected_mode


def test_self_evolve_judge_config_preserves_backend_ref() -> None:
    config = SelfEvolveJudgeConfig(mode="backend_ref", backend_ref="pkg.module:build_judge")

    assert config.backend_ref == "pkg.module:build_judge"


def test_self_evolve_config_parses_nested_judge_config_from_dict() -> None:
    config = SelfEvolveConfig(judge_config={"mode": "agent_md", "agent_path": "agent.md"})

    assert config.judge_config.mode == "agent_md"
    assert config.judge_config.agent_path == "agent.md"


def test_agent_config_preserves_old_fields_and_llm_extra_kwargs() -> None:
    config = AgentConfig(
        max_steps=42,
        llm_model_name="test-model",
        provider_specific_option="kept",
    )

    assert config.max_steps == 42
    assert config.llm_config.llm_model_name == "test-model"
    assert config.llm_config.ext_config["provider_specific_option"] == "kept"
