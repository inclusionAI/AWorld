from __future__ import annotations

import inspect

import pytest

from aworld.self_evolve.controllers import (
    run_generation_execution,
    run_generation_helpers,
    run_iteration_execution,
    run_iteration_helpers,
    run_terminal_lifecycle,
)
from aworld.self_evolve.controllers.run_generation_execution import (
    GenerationExecutionDisposition,
    GenerationExecutionPolicy,
)


def test_generation_execution_modules_do_not_import_runner() -> None:
    for module in (
        run_generation_execution,
        run_generation_helpers,
        run_iteration_execution,
        run_iteration_helpers,
        run_terminal_lifecycle,
    ):
        source = inspect.getsource(module)
        assert "from aworld.self_evolve.runner" not in source
        assert "import runner" not in source


def test_generation_execution_policy_keeps_capability_and_limits_typed() -> None:
    policy = GenerationExecutionPolicy(
        max_iterations=3,
        max_generated_candidates=4,
        max_full_evaluation_candidates=2,
        replay_candidate_limit=2,
        replay_enabled=True,
        candidate_screening_max_cases=3,
    )

    assert policy.max_generated_candidates == 4
    assert policy.replay_enabled is True
    assert GenerationExecutionDisposition.PROCEED.value == "proceed"
    assert GenerationExecutionDisposition.NEXT_ITERATION.value == ("next_iteration")


def test_generation_execution_policy_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="max_iterations"):
        GenerationExecutionPolicy(
            max_iterations=-1,
            max_generated_candidates=4,
            max_full_evaluation_candidates=2,
            replay_candidate_limit=2,
            replay_enabled=True,
            candidate_screening_max_cases=3,
        )
