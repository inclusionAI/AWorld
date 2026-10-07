from __future__ import annotations

import pytest

from aworld.core.context.generation_budget import (
    GenerationBudgetController,
    GenerationBudgetPolicy,
    GenerationStopReason,
)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 0])
def test_generation_policy_requires_finite_positive_deadlines(value):
    with pytest.raises(ValueError):
        GenerationBudgetPolicy(total_timeout_seconds=value)


def test_late_calls_share_absolute_task_allowance():
    now = [100.0]
    wall = [1000.0]
    environ = {
        "AWORLD_TASK_DEADLINE_EPOCH_SECONDS": "1500",
        "AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS": "30",
    }
    policy = GenerationBudgetPolicy(total_timeout_seconds=938)
    first = GenerationBudgetController(
        policy,
        clock=lambda: now[0],
        wall_clock=lambda: wall[0],
        environ=environ,
    )
    assert first.remaining_seconds(streaming=False) == 470

    now[0], wall[0] = 550, 1450
    late = GenerationBudgetController(
        policy,
        clock=lambda: now[0],
        wall_clock=lambda: wall[0],
        environ=environ,
    )
    assert late.remaining_seconds(streaming=False) == 20
    now[0] = 560
    wall[0] = 1200
    assert late.remaining_seconds(streaming=False) == 10

    receipt = late.receipt(GenerationStopReason.CALL_DEADLINE_EXCEEDED).to_dict()
    assert receipt["budget_source"] == "task_deadline"
    assert receipt["requested_total_timeout_seconds"] == 938
    assert receipt["effective_total_timeout_seconds"] == 20
    assert receipt["completion_reserve_seconds"] == 30

    exhausted = GenerationBudgetController(
        policy,
        clock=lambda: 800,
        wall_clock=lambda: 1490,
        environ=environ,
    )
    assert exhausted.remaining_seconds(streaming=False) == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "nan"),
        ("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "inf"),
        ("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", True),
        ("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "nan"),
        ("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", True),
        ("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "-1"),
    ],
)
def test_invalid_task_deadline_cannot_disable_supervisor_bound(key, value):
    environ = {"AWORLD_TASK_DEADLINE_EPOCH_SECONDS": "1500", key: value}
    with pytest.raises(ValueError):
        GenerationBudgetController(GenerationBudgetPolicy(), environ=environ)
