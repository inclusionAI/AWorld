from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from aworld_cli.builtin_plugins.goal_session.common import parse_goal_args
from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    apply_turn_outcome, handle_event, new_goal_contract_state,
)
from aworld_cli.executors.local import LocalAgentExecutor, _GoalContinuation


def test_goal_has_no_default_turn_or_time_cap():
    state = new_goal_contract_state("finish the work")
    for _ in range(5000):
        state, again = apply_turn_outcome(state, {"semantic_status": "incomplete", "recoverable": True})
        assert again
    assert state["max_turns"] is None
    assert "deadline_epoch_seconds" not in state


def test_explicit_goal_attempt_limit_is_preserved_on_continuation():
    state = new_goal_contract_state("work", max_turns=2)
    state, again = apply_turn_outcome(state, {"semantic_status":"budget_exhausted", "recoverable":True})
    assert again and state["max_turns"] == 2
    state, again = apply_turn_outcome(state, {"semantic_status":"incomplete", "recoverable":True})
    assert not again and state["status"] == "budget_limited"


def test_incomplete_promise_does_not_complete_goal_and_old_deadline_is_ignored():
    state = new_goal_contract_state("work", completion_promise="DONE")
    state, again = apply_turn_outcome(state, {"semantic_status":"incomplete", "recoverable":True, "final_answer":"<promise>DONE</promise>"})
    assert again and not state["completion_promise_satisfied"]
    state["deadline_epoch_seconds"] = 1
    state, again = apply_turn_outcome(state, {"semantic_status":"succeeded", "final_answer":"<promise>DONE</promise>"})
    assert not again and state["status"] == "complete"
    assert "deadline_epoch_seconds" not in state


def test_goal_without_promise_stops_on_typed_success():
    state, again = apply_turn_outcome(new_goal_contract_state("work"), {"semantic_status":"succeeded"})
    assert not again and state["status"] == "complete"


def test_untyped_text_promise_is_not_completion_evidence():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work", completion_promise="DONE"),
        {"final_answer":"<promise>DONE</promise>","task_status":"completed"},
    )
    assert again and state["status"] == "active"


def test_no_progress_does_not_stop_attempts_or_invent_a_budget():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work"),
        {"semantic_status":"incomplete","completion_reason":"no_new_evidence","recoverable":False},
    )
    assert again and state["status"] == "active"
    assert "deadline_epoch_seconds" not in state and state["max_turns"] is None


def test_pause_wins_race_with_old_completion_hook():
    stale = new_goal_contract_state("work")
    writes=[]
    handle=SimpleNamespace(read=lambda:{**stale,"active":False,"status":"paused"}, write=writes.append)
    assert handle_event({"semantic_status":"incomplete"}, {**stale,"__plugin_state__":handle}) == {"action":"allow"}
    assert writes == []


@pytest.mark.parametrize("args", ["work --max-turns 0", "work --timeout-seconds 0", "work --timeout-seconds nan", "work --deadline-epoch-seconds inf"])
def test_invalid_attempt_limits_and_unsupported_time_options_rejected(args):
    with pytest.raises(ValueError):
        parse_goal_args(args)


@pytest.mark.asyncio
async def test_cli_continues_thousands_of_goal_turns_without_recursion_or_time_limit(monkeypatch):
    executor=object.__new__(LocalAgentExecutor)
    executor._base_runtime=None
    seen=[]

    async def turn(message, requested_skill_names=None, _previous_goal_context=None):
        seen.append(message)
        return _GoalContinuation("continue") if len(seen)<1500 else "done"

    executor._chat_turn=turn
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1")
    assert await executor.chat("work") == "done"
    assert len(seen) == 1500


@pytest.mark.asyncio
@pytest.mark.parametrize("status,maximum", [("paused", None), ("budget_limited", None), ("budget_limited", 5)])
async def test_goal_resume_keeps_work_scope_and_discards_old_time_limit(status, maximum):
    from aworld_cli.builtin_plugins.goal_session.hooks.stop import GoalCommand
    from aworld_cli.core.command_system import CommandContext

    state={**new_goal_contract_state("work", max_turns=maximum), "active":False,
           "status":status, "last_task_id":"prior-task", "last_task_epoch":7,
           "deadline_epoch_seconds":1}
    def update(values):
        state.update(values)
        return dict(state)
    def write(values):
        state.clear()
        state.update(values)
    handle=SimpleNamespace(read=lambda:dict(state), update=update, write=write)
    command=object.__new__(GoalCommand)
    command.get_state_handle=lambda _:handle
    executor=SimpleNamespace()
    context=CommandContext(cwd="/tmp",user_args="resume",executor=executor,session_id="same-session")
    assert await command.pre_execute(context) is None
    assert command.should_start_new_session(context) is False
    await command.get_prompt(context)
    assert state["status"] == "active" and "deadline_epoch_seconds" not in state
    assert executor._resume_context_checkpoint_once
    assert executor._resume_goal_work_scope_once == {"source_task_id":"prior-task","source_task_epoch":7}



@pytest.mark.asyncio
async def test_user_pause_cancels_context_construction_without_a_timer(monkeypatch):
    executor=object.__new__(LocalAgentExecutor)
    executor._base_runtime=None
    executor.session_id="session"
    executor._run_plugin_task_hook=AsyncMock(return_value=[])
    entered=asyncio.Event()
    cancelled=[]
    async def blocked_turn(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    executor._chat_turn=blocked_turn
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1")
    running=asyncio.create_task(executor.chat("work"))
    await entered.wait()
    assert not running.done()
    executor.request_goal_pause()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert cancelled == [True]
    assert executor._active_chat_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["complete", "paused", "budget_limited"])
async def test_ordinary_cli_work_does_not_inherit_inactive_goal_limits(monkeypatch, status):
    executor=object.__new__(LocalAgentExecutor)
    executor._base_runtime=SimpleNamespace(build_plugin_hook_state=lambda *args:{
        "active":False, "status":status, "deadline_epoch_seconds":1,
    })
    async def turn(*args, **kwargs):
        assert "_lifetime" not in kwargs
        return "ordinary CLI work"
    executor._chat_turn=turn
    monkeypatch.delenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", raising=False)
    assert await executor.chat("new request") == "ordinary CLI work"


def test_goal_resume_maps_only_unique_configured_agent_names():
    executor=object.__new__(LocalAgentExecutor)
    executor.swarm=SimpleNamespace(agents={
        "new-a":SimpleNamespace(name=lambda:"worker",id=lambda:"new-a"),
        "new-b":SimpleNamespace(name=lambda:"duplicate",id=lambda:"new-b"),
        "new-c":SimpleNamespace(name=lambda:"duplicate",id=lambda:"new-c"),
    })
    assert executor._goal_agent_ids() == {"worker":"new-a"}
