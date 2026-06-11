from __future__ import annotations

import pytest

from aworld.evaluations.runtime_composition import (
    CallableRuntimeHarness,
    LLMUserSimulator,
    RolloutState,
    RolloutTurn,
)
from aworld.evaluations.substrate import EvalCaseDef


@pytest.mark.asyncio
async def test_callable_runtime_harness_awaits_async_simulator():
    class AsyncSimulator:
        async def next_turn(self, *, case, target, state, last_output=None):
            if any(turn.role == "user" for turn in state.turns):
                return None
            return RolloutTurn(role="user", content="async hello")

    async def assistant_step(*, user_turn, state, case, target):
        return {"answer": f"ack:{user_turn.content}"}

    harness = CallableRuntimeHarness(
        simulator=AsyncSimulator(),
        assistant_step=assistant_step,
        max_turns=1,
    )

    state = await harness.run_rollout(
        case=EvalCaseDef(case_id="case-1", input={}),
        target={},
    )

    assert state.answer == "ack:async hello"
    assert state.turns[0].content == "async hello"


@pytest.mark.asyncio
async def test_llm_user_simulator_generates_adaptive_turns_from_context():
    calls = []

    async def turn_generator(*, case, target, state, last_output, turn_index):
        calls.append(
            {
                "case_id": case.case_id,
                "goal": target["goal"],
                "last_output": last_output,
                "turn_index": turn_index,
                "turn_count": len(state.turns),
            }
        )
        if turn_index == 0:
            return "start"
        if turn_index == 1:
            return {
                "content": f"clarify after {last_output}",
                "metadata": {"intent": "clarify", "client": object()},
            }
        return {"stop": True, "metadata": {"reason": "done"}}

    async def assistant_step(*, user_turn, state, case, target):
        return {"answer": f"assistant:{user_turn.content}"}

    harness = CallableRuntimeHarness(
        simulator=LLMUserSimulator(turn_generator=turn_generator),
        assistant_step=assistant_step,
        max_turns=3,
    )

    state = await harness.run_rollout(
        case=EvalCaseDef(case_id="case-1", input={}),
        target={"goal": "resolve ticket"},
    )

    assert [turn.content for turn in state.turns if turn.role == "user"] == [
        "start",
        "clarify after assistant:start",
    ]
    assert calls[0]["turn_index"] == 0
    assert calls[1]["last_output"] == "assistant:start"
    assert calls[2]["turn_count"] == 4
    assert state.turns[2].metadata["intent"] == "clarify"
    assert "client" not in state.trajectory[2]["metadata"]


def test_llm_user_simulator_accepts_rollout_turn_output():
    simulator = LLMUserSimulator(
        turn_generator=lambda **kwargs: RolloutTurn(
            role="user",
            content="custom",
            metadata={"safe": True},
        )
    )

    turn = simulator.next_turn(
        case=EvalCaseDef(case_id="case-1", input={}),
        target={},
        state=RolloutState(case_id="case-1"),
        last_output=None,
    )

    assert turn == RolloutTurn(role="user", content="custom", metadata={"safe": True})
