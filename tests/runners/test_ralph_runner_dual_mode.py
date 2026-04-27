from aworld.runners.ralph.config import RalphConfig
from aworld.runners.ralph.policy import RalphLoopPolicy


def test_ralph_config_defaults_to_reuse_context_execution_mode():
    config = RalphConfig()

    assert config.execution_mode == "reuse_context"
    assert config.reuse_context is True


def test_ralph_config_explicit_execution_mode_wins_and_normalizes_reuse_context():
    config = RalphConfig(execution_mode="fresh_context")

    assert config.execution_mode == "fresh_context"
    assert config.reuse_context is False


def test_ralph_loop_policy_maps_reuse_context_false_to_fresh_context():
    config = RalphConfig(reuse_context=False)

    policy = RalphLoopPolicy.from_config(config)

    assert policy.execution_mode == "fresh_context"


def test_ralph_config_round_trip_preserves_effective_execution_mode():
    config = RalphConfig(reuse_context=False)

    reloaded = RalphConfig.model_validate(config.model_dump())

    assert reloaded.execution_mode == "fresh_context"
    assert reloaded.reuse_context is False


def test_ralph_config_conflicting_knobs_are_normalized_to_execution_mode():
    config = RalphConfig(execution_mode="reuse_context", reuse_context=False)

    assert config.execution_mode == "reuse_context"
    assert config.reuse_context is True


def test_ralph_config_assignment_updates_execution_mode_from_reuse_context():
    config = RalphConfig()

    config.reuse_context = False

    assert config.execution_mode == "fresh_context"
    assert config.reuse_context is False


def test_ralph_config_assignment_updates_reuse_context_from_execution_mode():
    config = RalphConfig()

    config.execution_mode = "fresh_context"

    assert config.execution_mode == "fresh_context"
    assert config.reuse_context is False


def test_ralph_config_parses_verify_from_raw_dict():
    config = RalphConfig.model_validate({"verify": {"enabled": True, "commands": ["pytest -q"]}})

    assert config.verify.enabled is True
    assert config.verify.commands == ["pytest -q"]
