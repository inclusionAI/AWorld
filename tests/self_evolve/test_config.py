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
    assert config.self_evolve_config.total_run_token_budget == 500_000
    assert config.self_evolve_config.per_attempt_replay_token_limit == 500_000
    assert config.self_evolve_config.deprecated_config_mappings == (
        "max_run_tokens_to_total_run_token_budget",
        "max_run_tokens_to_per_attempt_replay_token_limit",
    )
    assert config.self_evolve_config.min_eval_cases == 30
    assert config.self_evolve_config.judge_repetitions == 3
    assert config.self_evolve_config.judge_timeout_seconds == 300
    assert config.self_evolve_config.auto_apply_target_types == ("skill",)
    assert config.self_evolve_config.inferred_new_skill_policy == "auto_verified"
    assert config.self_evolve_config.require_deterministic_signal_for_verified is True
    assert config.self_evolve_config.max_iterations == 1
    assert config.self_evolve_config.max_background_jobs == 1
    assert config.self_evolve_config.replay_enabled is True
    assert config.self_evolve_config.replay_timeout_seconds == 600
    assert config.self_evolve_config.replay_max_steps == 1
    assert config.self_evolve_config.replay_candidate_limit == 2
    assert config.self_evolve_config.baseline_replay_repetitions == 1
    assert config.self_evolve_config.candidate_replay_repetitions == 1
    assert config.self_evolve_config.replay_stability_margin == 0.0


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


@pytest.mark.parametrize(
    "policy",
    ("disabled", "draft_only", "auto_verified"),
)
def test_self_evolve_config_accepts_named_inferred_new_skill_policy(policy: str) -> None:
    assert SelfEvolveConfig(inferred_new_skill_policy=policy).inferred_new_skill_policy == policy


def test_self_evolve_config_rejects_unknown_inferred_new_skill_policy() -> None:
    with pytest.raises(ValidationError):
        SelfEvolveConfig(inferred_new_skill_policy="allow_all")


def test_self_evolve_budget_fields_parse() -> None:
    config = SelfEvolveConfig(
        mode="shadow",
        max_run_tokens=50_000,
        total_run_token_budget=60_000,
        per_attempt_replay_token_limit=5_000,
        max_run_cost_usd=1.25,
        max_run_wall_seconds=900.0,
        candidate_generation_tokens_per_unit=1_000,
        candidate_generation_cost_usd_per_unit=0.1,
        candidate_generation_wall_seconds_per_unit=10.0,
        candidate_screening_tokens_per_unit=200,
        candidate_screening_cost_usd_per_unit=0.02,
        candidate_screening_wall_seconds_per_unit=2.0,
        replay_tokens_per_unit=2_000,
        replay_cost_usd_per_unit=0.2,
        replay_wall_seconds_per_unit=20.0,
        evaluation_tokens_per_unit=500,
        evaluation_cost_usd_per_unit=0.05,
        evaluation_wall_seconds_per_unit=5.0,
        min_eval_cases=5,
        judge_repetitions=3,
        judge_timeout_seconds=120,
        cooldown_seconds=600,
        auto_apply_target_types=("skill", "prompt-section"),
        require_deterministic_signal_for_verified=False,
        regression_benchmarks=("global",),
        max_iterations=2,
        min_improvement=0.1,
        target_types=("skill", "tool-description"),
        eval_sources=("jsonl", "trajectory_log"),
        max_background_jobs=2,
        replay_timeout_seconds=120,
        replay_max_steps=2,
        replay_candidate_limit=2,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
        replay_stability_margin=0.2,
    )

    assert config.max_run_tokens == 50_000
    assert config.total_run_token_budget == 60_000
    assert config.per_attempt_replay_token_limit == 5_000
    assert config.max_run_cost_usd == 1.25
    assert config.max_run_wall_seconds == 900.0
    assert config.candidate_generation_tokens_per_unit == 1_000
    assert config.candidate_generation_cost_usd_per_unit == 0.1
    assert config.candidate_generation_wall_seconds_per_unit == 10.0
    assert config.candidate_screening_tokens_per_unit == 200
    assert config.candidate_screening_cost_usd_per_unit == 0.02
    assert config.candidate_screening_wall_seconds_per_unit == 2.0
    assert config.replay_tokens_per_unit == 2_000
    assert config.replay_cost_usd_per_unit == 0.2
    assert config.replay_wall_seconds_per_unit == 20.0
    assert config.evaluation_tokens_per_unit == 500
    assert config.evaluation_cost_usd_per_unit == 0.05
    assert config.evaluation_wall_seconds_per_unit == 5.0
    assert config.deprecated_config_mappings == ()
    assert config.min_eval_cases == 5
    assert config.judge_repetitions == 3
    assert config.judge_timeout_seconds == 120
    assert config.cooldown_seconds == 600
    assert config.auto_apply_target_types == ("skill", "prompt-section")
    assert config.require_deterministic_signal_for_verified is False
    assert config.regression_benchmarks == ("global",)
    assert config.max_iterations == 2
    assert config.min_improvement == 0.1
    assert config.target_types == ("skill", "tool-description")
    assert config.eval_sources == ("jsonl", "trajectory_log")
    assert config.max_background_jobs == 2
    assert config.replay_timeout_seconds == 120
    assert config.replay_max_steps == 2
    assert config.replay_candidate_limit == 2
    assert config.baseline_replay_repetitions == 2
    assert config.candidate_replay_repetitions == 3
    assert config.replay_stability_margin == 0.2


def test_self_evolve_legacy_token_budget_maps_and_reports_deprecation() -> None:
    config = SelfEvolveConfig(max_run_tokens=42_000)

    assert config.total_run_token_budget == 42_000
    assert config.per_attempt_replay_token_limit == 42_000
    assert config.deprecated_config_mappings == (
        "max_run_tokens_to_total_run_token_budget",
        "max_run_tokens_to_per_attempt_replay_token_limit",
    )
    dumped = config.model_dump()
    assert dumped["per_attempt_replay_token_limit"] == 42_000
    assert dumped["deprecated_config_mappings"] == (
        "max_run_tokens_to_total_run_token_budget",
        "max_run_tokens_to_per_attempt_replay_token_limit",
    )
    reloaded = SelfEvolveConfig.model_validate(config.model_dump())
    assert reloaded.total_run_token_budget == 42_000
    assert reloaded.per_attempt_replay_token_limit == 42_000
    assert reloaded.deprecated_config_mappings == config.deprecated_config_mappings


def test_legacy_token_budget_maps_only_missing_explicit_ceiling_fields() -> None:
    config = SelfEvolveConfig(
        max_run_tokens=42_000,
        total_run_token_budget=60_000,
    )

    assert config.total_run_token_budget == 60_000
    assert config.per_attempt_replay_token_limit == 42_000
    assert config.deprecated_config_mappings == (
        "max_run_tokens_to_per_attempt_replay_token_limit",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"max_run_tokens": 0},
        {"total_run_token_budget": 0},
        {"per_attempt_replay_token_limit": -1},
        {"max_run_cost_usd": 0},
        {"max_run_wall_seconds": -1},
        {"candidate_generation_tokens_per_unit": 0},
        {"candidate_screening_tokens_per_unit": -1},
        {"replay_tokens_per_unit": 0},
        {"evaluation_tokens_per_unit": -1},
        {"candidate_generation_cost_usd_per_unit": -0.1},
        {"candidate_screening_wall_seconds_per_unit": -0.1},
        {"replay_cost_usd_per_unit": -0.1},
        {"evaluation_wall_seconds_per_unit": -0.1},
    ],
)
def test_self_evolve_budget_fields_reject_invalid_values(payload: dict) -> None:
    with pytest.raises(ValidationError, match="must be (positive|non-negative)"):
        SelfEvolveConfig(**payload)


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
