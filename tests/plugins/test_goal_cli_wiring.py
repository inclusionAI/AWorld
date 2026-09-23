"""Exercise CLI task construction and goal hooks with real scoped Context state."""

from copy import deepcopy
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console

from aworld.core.context.base import Context
from aworld.core.context.compiler import CompletionStatus
from aworld.core.context.execution_state import get_execution_state, record_execution_state
from aworld.core.context.session import Session
from aworld.core.context.work_progress import retain_work_progress
from aworld.core.task import Task, TaskResponse
from aworld_cli.builtin_plugins.goal_session.hooks.stop import GoalCommand
from aworld_cli.builtin_plugins.goal_session.hooks.task_error import handle_event as handle_error
from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    handle_event,
    new_goal_contract_state,
)
from aworld_cli.core.command_system import CommandContext
from aworld_cli.executors.local import LocalAgentExecutor


def _state_handle(state):
    def write(value):
        state.clear()
        state.update(deepcopy(value))
        return dict(state)

    def update(value):
        state.update(deepcopy(value))
        return dict(state)

    return SimpleNamespace(read=lambda: deepcopy(state), write=write, update=update)


def _executor(monkeypatch, tmp_path, state, agent_id):
    handle = _state_handle(state)

    def hook_state(*args):
        return {**deepcopy(state), "__plugin_state__": handle}

    async def run_hooks(hook_point, *, event, executor_instance):
        if hook_point == "task_completed":
            result = handle_event(event, hook_state())
        elif hook_point == "task_error":
            result = handle_error(event, hook_state())
        else:
            return []
        return [("goal-session", SimpleNamespace(**result))]

    runtime = SimpleNamespace(build_plugin_hook_state=hook_state, run_plugin_hooks=run_hooks)
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "goal-session"
    executor.console = Console(file=io.StringIO(), force_terminal=False)
    executor.context_config = SimpleNamespace()
    executor.swarm = SimpleNamespace(
        agents={agent_id: SimpleNamespace(name=lambda: "worker", id=lambda: agent_id)},
    )
    executor._execute_hooks = AsyncMock(return_value=None)
    executor._create_workspace = AsyncMock(return_value=None)
    executor._resolve_swarm_skills = Mock()
    executor._update_session_last_used = Mock()
    executor._record_cli_session_transcript_turn = Mock()
    executor._suppress_interactive_loading_status = True
    monkeypatch.chdir(tmp_path)
    # CLI does not adopt a runtime's protocol deadline as its own stop rule.
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1")
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    monkeypatch.setattr("aworld_cli.core.session_store.CliSessionStore", Mock())
    monkeypatch.setattr("aworld_cli.history.JSONLHistory", Mock())
    return executor, handle


def _context_factory(monkeypatch, restored=None):
    contexts, calls = [], []

    async def from_input(task_input, **kwargs):
        calls.append(kwargs)
        context = Context(task_id=task_input.task_id, session=Session(session_id=task_input.session_id))
        context.user_id = task_input.user_id
        context.set_task(Task(id=task_input.task_id, input=task_input.origin_user_input))
        if restored is not None and kwargs.get("use_checkpoint"):
            context.context_info.update(json.loads(json.dumps(restored)))
        context.get_config = lambda: SimpleNamespace(debug_mode=False)
        context.init_swarm_state = AsyncMock()
        contexts.append(context)
        return context

    monkeypatch.setattr("aworld_cli.executors.local.ApplicationContext.from_input", from_input)
    return contexts, calls


def _streamed_runner(monkeypatch, run):
    def streamed_run_task(*, task):
        output = SimpleNamespace(is_complete=True)

        async def stream_events():
            output.final = await run(task)
            if False:
                yield None

        output.stream_events = stream_events
        output.response = lambda: output.final
        return output

    monkeypatch.setattr("aworld_cli.executors.local.Runners.streamed_run_task", streamed_run_task)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_deadline", ["1", "nan", ""])
async def test_ordinary_cli_builds_unbounded_task_without_goal_or_deadline_input(monkeypatch, tmp_path, legacy_deadline):
    executor, _ = _executor(monkeypatch, tmp_path, {}, "worker-id")
    _context_factory(monkeypatch)
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", legacy_deadline)
    task = await executor._build_task("Finish ordinary CLI work")
    assert task.timeout is task.deadline_epoch_seconds is None
    assert task.remaining_seconds() is None


@pytest.mark.asyncio
async def test_cli_goal_carries_intent_between_real_task_builds_and_revalidates(monkeypatch, tmp_path):
    state = new_goal_contract_state(
        "Produce delivery.txt", verification_commands=["test -f delivery.txt"],
    )
    executor, _ = _executor(monkeypatch, tmp_path, state, "worker-id")
    contexts, calls = _context_factory(monkeypatch)
    tasks = []

    async def run(task):
        tasks.append(task)
        context = task.context
        context.set_task(task)
        assert task.timeout is task.deadline_epoch_seconds is None
        assert not context._completion_self_checks
        assert get_execution_state(context) is None
        if len(tasks) == 1:
            retain_work_progress(context, "worker-id", plan="Generate delivery then validate it")
        else:
            migrated = context.context_info["adaptive_work_state:worker-id"]
            assert migrated["scope"]["task_id"] == task.id
            assert migrated["carried_from"]["task_id"] == tasks[0].id
            assert migrated["public_requirements"]["text"] == "Produce delivery.txt"
            assert migrated["current_plan"]["text"] == "Generate delivery then validate it"
            assert migrated["validation_evidence"][0]["historical"] is True
            assert migrated["validation_evidence"][0]["exit_code"] != 0
            (tmp_path / "delivery.txt").write_text("delivered")
        await context.resolve_completion_evidence()
        context.record_completion_final_evidence("agent_final_response")
        assessment = context.assess_completion_contract(agent_claimed_finished=True)
        succeeded = assessment.status is CompletionStatus.SATISFIED
        retain_work_progress(context, "worker-id")
        status = "succeeded" if succeeded else "incomplete"
        record_execution_state(context, "worker-id", status, "verified" if succeeded else "validation_failed")
        return TaskResponse(
            success=succeeded, answer="verified delivery" if succeeded else "more work remains",
            semantic_status=status, completion_reason="verified" if succeeded else "validation_failed",
            recoverable=not succeeded,
        )

    _streamed_runner(monkeypatch, run)
    assert await executor.chat("Produce delivery.txt") == "verified delivery"
    assert len(tasks) == len(contexts) == len(calls) == 2
    assert tasks[0].id != tasks[1].id
    assert state["status"] == "complete"
    assert "deadline_epoch_seconds" not in state
    assert state["agent_ids_by_name"] == {"worker": "worker-id"}


@pytest.mark.asyncio
async def test_cli_goal_resume_loads_checkpoint_and_maps_new_agent_uuid(monkeypatch, tmp_path):
    old = Context(task_id="prior-task", task_epoch=7)
    old.set_task(Task(id="prior-task", input="Produce delivery.txt"))
    retain_work_progress(old, "old-worker-id", plan="Finish the pending delivery")
    record_execution_state(old, "old-worker-id", "incomplete", "paused")
    state = {
        **new_goal_contract_state("Produce delivery.txt"),
        "active": False, "status": "paused", "last_task_id": "prior-task", "last_task_epoch": 7,
        "deadline_epoch_seconds": 1,
        "agent_ids_by_name": {"worker": "old-worker-id"},
    }
    executor, handle = _executor(monkeypatch, tmp_path, state, "new-worker-id")
    contexts, calls = _context_factory(monkeypatch, restored={
        key: value for key, value in old.context_info.items()
        if key.startswith("adaptive_work_state:") or key.startswith("agent_execution_state")
    })
    command = object.__new__(GoalCommand)
    command.get_state_handle = lambda _: handle
    command_context = CommandContext(
        cwd=str(tmp_path), user_args="resume", executor=executor, session_id=executor.session_id,
    )
    assert await command.pre_execute(command_context) is None
    prompt = await command.get_prompt(command_context)

    async def run(task):
        context = task.context
        context.set_task(task)
        migrated = retain_work_progress(context, "new-worker-id")
        assert migrated["scope"] == {"task_id": task.id, "task_epoch": context.task_epoch}
        assert migrated["carried_from"] == {"task_id": "prior-task", "task_epoch": 7}
        assert migrated["public_requirements"]["text"] == "Produce delivery.txt"
        assert migrated["current_plan"]["text"] == "Finish the pending delivery"
        assert get_execution_state(context) is None
        assert task.timeout is task.deadline_epoch_seconds is None
        return TaskResponse(success=True, answer="done", semantic_status="succeeded")

    _streamed_runner(monkeypatch, run)
    assert await executor.chat(prompt) == "done"
    assert len(contexts) == len(calls) == 1 and calls[0]["use_checkpoint"] is True
    assert executor._resume_goal_work_scope_once is None
    assert executor._resume_goal_agent_ids_once is None
    assert state["status"] == "complete"
    assert "deadline_epoch_seconds" not in state
    assert state["agent_ids_by_name"] == {"worker": "new-worker-id"}


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [1, 2])
async def test_cli_goal_retries_a_failed_attempt_until_completion_or_maximum(monkeypatch, tmp_path, maximum):
    state = new_goal_contract_state("Finish the operation", max_turns=maximum)
    executor, _ = _executor(monkeypatch, tmp_path, state, "worker-id")
    contexts, _ = _context_factory(monkeypatch)
    attempts = []

    async def run(task):
        attempts.append(task.id)
        task.context.set_task(task)
        if len(attempts) == 1:
            retain_work_progress(task.context, "worker-id", plan="Retry after the temporary provider failure")
            raise TimeoutError("one model call timed out")
        ledger = retain_work_progress(task.context, "worker-id")
        assert ledger["public_requirements"]["text"] == "Finish the operation"
        assert ledger["current_plan"]["text"] == "Retry after the temporary provider failure"
        assert "one model call timed out" in task.input
        return TaskResponse(success=True, answer="done", semantic_status="succeeded")

    _streamed_runner(monkeypatch, run)
    if maximum == 1:
        with pytest.raises(TimeoutError, match="one model call"):
            await executor.chat("Finish the operation")
        assert state["status"] == "budget_limited"
        assert executor.last_task_response.success is False
        assert executor.last_task_response.semantic_status == "incomplete"
    else:
        assert await executor.chat("Finish the operation") == "done"
        assert state["status"] == "complete"
    assert len(attempts) == len(contexts) == maximum


def test_pause_wins_a_race_with_attempt_error():
    state = new_goal_contract_state("Work until complete")
    handle = _state_handle(state)
    stale = {**state, "__plugin_state__": handle}
    handle.update({"active": False, "status": "paused"})
    assert handle_error({"error": "late provider timeout"}, stale) == {"action": "allow"}
    assert state["status"] == "paused"
