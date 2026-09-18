from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pytest

from aworld.core.task import TaskResponse
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
    assert state["deadline_epoch_seconds"] is None


def test_explicit_goal_budget_is_preserved_on_continuation():
    state = new_goal_contract_state("work", max_turns=2, deadline_epoch_seconds=time.time()+100)
    deadline = state["deadline_epoch_seconds"]
    state, again = apply_turn_outcome(state, {"semantic_status":"budget_exhausted", "recoverable":True})
    assert again and state["deadline_epoch_seconds"] == deadline
    state, again = apply_turn_outcome(state, {"semantic_status":"incomplete", "recoverable":True})
    assert not again and state["status"] == "budget_limited"


def test_incomplete_promise_does_not_complete_goal_and_expired_budget_cannot_continue():
    state = new_goal_contract_state("work", completion_promise="DONE")
    state, again = apply_turn_outcome(state, {"semantic_status":"incomplete", "recoverable":True, "final_answer":"<promise>DONE</promise>"})
    assert again and not state["completion_promise_satisfied"]
    state["deadline_epoch_seconds"] = time.time()-1
    state, again = apply_turn_outcome(state, {"semantic_status":"succeeded", "final_answer":"<promise>DONE</promise>"})
    assert not again and state["status"] == "budget_limited"


def test_goal_without_promise_stops_on_typed_success():
    state, again = apply_turn_outcome(new_goal_contract_state("work"), {"semantic_status":"succeeded"})
    assert not again and state["status"] == "complete"


def test_untyped_text_promise_is_not_completion_evidence():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work", completion_promise="DONE"),
        {"final_answer":"<promise>DONE</promise>","task_status":"completed"},
    )
    assert again and state["status"] == "active"


def test_non_recoverable_no_progress_pauses_without_inventing_time_budget():
    state, again = apply_turn_outcome(
        new_goal_contract_state("work"),
        {"semantic_status":"incomplete","completion_reason":"no_new_evidence","recoverable":False},
    )
    assert not again and state["status"] == "paused"
    assert state["deadline_epoch_seconds"] is None and state["max_turns"] is None


def test_pause_wins_race_with_old_completion_hook():
    stale = new_goal_contract_state("work")
    writes=[]
    handle=SimpleNamespace(read=lambda:{**stale,"active":False,"status":"paused"}, write=writes.append)
    assert handle_event({"semantic_status":"incomplete"}, {**stale,"__plugin_state__":handle}) == {"action":"allow"}
    assert writes == []


@pytest.mark.parametrize("args", ["work --max-turns 0", "work --timeout-seconds 0", "work --timeout-seconds nan", "work --deadline-epoch-seconds inf"])
def test_invalid_goal_budgets_rejected(args):
    with pytest.raises(ValueError):
        parse_goal_args(args)


@pytest.mark.asyncio
async def test_cli_continues_thousands_of_goal_turns_without_recursion_or_budget_renewal(monkeypatch):
    executor=object.__new__(LocalAgentExecutor)
    executor._base_runtime=None
    seen=[]

    async def turn(message, requested_skill_names=None, _lifetime=None, _previous_goal_context=None):
        seen.append(_lifetime.deadline_epoch_seconds)
        return _GoalContinuation("continue") if len(seen)<1500 else "done"

    executor._chat_turn=turn
    monkeypatch.delenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", raising=False)
    assert await executor.chat("work", timeout=100) == "done"
    assert len(seen) == 1500 and len(set(seen)) == 1


@pytest.mark.parametrize("value", ["", "nan", "inf", "bad", "-1"])
def test_cli_invalid_inherited_deadline_fails_closed(monkeypatch,value):
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", value)
    with pytest.raises(ValueError):
        LocalAgentExecutor._caller_deadline_from_environment()


@pytest.mark.asyncio
async def test_goal_resume_keeps_persisted_deadline_and_work_scope():
    from aworld_cli.builtin_plugins.goal_session.hooks.stop import GoalCommand
    from aworld_cli.core.command_system import CommandContext

    state={**new_goal_contract_state("work", timeout_seconds=100), "active":False,
           "status":"paused", "last_task_id":"prior-task", "last_task_epoch":7}
    deadline=state["deadline_epoch_seconds"]
    def update(values):
        state.update(values)
        return dict(state)
    handle=SimpleNamespace(read=lambda:dict(state), update=update)
    command=object.__new__(GoalCommand)
    command.get_state_handle=lambda _:handle
    executor=SimpleNamespace()
    context=CommandContext(cwd="/tmp",user_args="resume",executor=executor,session_id="same-session")
    assert await command.pre_execute(context) is None
    assert command.should_start_new_session(context) is False
    await command.get_prompt(context)
    assert state["status"] == "active" and state["deadline_epoch_seconds"] == deadline
    assert executor._resume_context_checkpoint_once
    assert executor._resume_goal_work_scope_once == {"source_task_id":"prior-task","source_task_epoch":7}
    state.update(status="paused", active=False, deadline_epoch_seconds=time.time()-1)
    assert "expired" in await command.pre_execute(context)
    assert state["status"] == "budget_limited"


@pytest.mark.asyncio
async def test_explicit_cli_deadline_covers_context_build_not_only_runner(monkeypatch):
    executor=object.__new__(LocalAgentExecutor)
    executor._base_runtime=None
    executor.session_id="session"
    executor._run_plugin_task_hook=AsyncMock(return_value=[])
    cancelled=[]
    async def blocked_turn(*args, **kwargs):
        import asyncio
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    executor._chat_turn=blocked_turn
    monkeypatch.delenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", raising=False)
    assert await executor.chat("work", timeout=0.03) == ""
    assert cancelled == [True]
    assert executor.last_task_response.semantic_status == "budget_exhausted"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["complete", "paused", "budget_limited"])
async def test_ordinary_cli_work_does_not_inherit_inactive_goal_limits(monkeypatch, status):
    executor=object.__new__(LocalAgentExecutor)
    executor._base_runtime=SimpleNamespace(build_plugin_hook_state=lambda *args:{
        "active":False, "status":status, "deadline_epoch_seconds":1,
    })
    async def turn(*args, _lifetime=None, **kwargs):
        assert _lifetime.remaining_seconds() is None
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
