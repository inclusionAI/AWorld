from unittest.mock import AsyncMock

import pytest

from aworld import runner as runner_module
from aworld.core.context.amni import ApplicationContext
from aworld.core.task import Task, TaskResponse
from aworld.runners.ralph.config import RalphConfig, RalphVerifyConfig
from aworld.runners.ralph.detect.types import StopType
from aworld.runners.ralph.input_builder import IterationInput, IterationInputBuilder
from aworld.runners.ralph.memory import LoopMemoryStore
from aworld.runners.ralph.policy import RalphLoopPolicy
from aworld.runners.ralph.state import LoopContext, LoopState
from aworld.runners.ralph.types import CompletionCriteria
from aworld.runners.ralph_runner import RalphRunner


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


@pytest.mark.asyncio
async def test_iteration_input_builder_fresh_context_includes_original_task_and_memory(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    builder = IterationInputBuilder(
        policy=RalphLoopPolicy(execution_mode="fresh_context", verify_enabled=False),
        memory_store=LoopMemoryStore(context),
    )

    payload = await builder.build(
        task_id="task-1",
        original_task="Build a REST API",
        iteration=2,
        previous_answer="Created app.py",
        reflection_feedback="Add tests next",
    )

    assert payload.reuse_context is False
    assert "Original task:" in payload.task_input
    assert "Build a REST API" in payload.task_input
    assert "Previous answer summary:" in payload.task_input
    assert "Created app.py" in payload.task_input
    assert "Add tests next" in payload.task_input


@pytest.mark.asyncio
async def test_iteration_input_builder_reuse_context_omits_original_task_header(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    builder = IterationInputBuilder(
        policy=RalphLoopPolicy(execution_mode="reuse_context", verify_enabled=False),
        memory_store=LoopMemoryStore(context),
    )

    payload = await builder.build(
        task_id="task-1",
        original_task="Build a REST API",
        iteration=2,
        previous_answer="Created app.py",
        reflection_feedback="Add tests next",
    )

    assert payload.reuse_context is True
    assert "Original task:" not in payload.task_input
    assert "Previous answer summary:" in payload.task_input
    assert "Created app.py" in payload.task_input
    assert "Add tests next" in payload.task_input


@pytest.mark.asyncio
async def test_ralph_runner_build_iteration_context_uses_fresh_sub_context(tmp_path):
    task = Task(input="Build API", conf=RalphConfig(execution_mode="fresh_context", workspace=str(tmp_path)))
    runner = RalphRunner(task=task, completion_criteria=CompletionCriteria())
    runner.loop_context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )

    sub_context = ApplicationContext.create(task_id=task.id, task_content="fresh iteration")
    runner.loop_context.build_sub_context = AsyncMock(return_value=sub_context)

    iteration_input = IterationInput(task_input="fresh iteration", reuse_context=False)

    context = await runner._build_iteration_context(iteration_input, task, iter_num=2)

    runner.loop_context.build_sub_context.assert_awaited_once_with(
        sub_task_content="fresh iteration",
        sub_task_id=task.id,
        task=task,
    )
    assert context.task_input == "fresh iteration"


@pytest.mark.asyncio
async def test_ralph_runner_build_iteration_context_seeds_real_fresh_context_from_iteration_payload(tmp_path):
    task = Task(input="stale task input", conf=RalphConfig(execution_mode="fresh_context", workspace=str(tmp_path)))
    runner = RalphRunner(task=task, completion_criteria=CompletionCriteria())
    runner.loop_context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )

    iteration_input = IterationInput(task_input="current iteration payload", reuse_context=False)

    context = await runner._build_iteration_context(iteration_input, task, iter_num=2)

    assert context.task_input == "current iteration payload"
    assert context.origin_user_input == "current iteration payload"
    assert context.task_state.task_input.task_content == "current iteration payload"
    assert context.task_state.task_input.origin_user_input == "current iteration payload"


@pytest.mark.asyncio
async def test_ralph_runner_execute_task_uses_current_iteration_payload_for_fresh_context(tmp_path, monkeypatch):
    task = Task(input="stale task input", conf=RalphConfig(execution_mode="fresh_context", workspace=str(tmp_path)))
    runner = RalphRunner(task=task, completion_criteria=CompletionCriteria())
    runner.loop_context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    runner.memory_store = runner.loop_context.memory

    class StubBuilder:
        async def build(self, **kwargs):
            return IterationInput(task_input="current iteration payload", reuse_context=False)

    runner.input_builder = StubBuilder()
    captured = {}

    async def fake_exec_tasks(tasks):
        current_task = tasks[0]
        captured["task_input"] = current_task.input
        captured["origin_user_input"] = current_task.context.origin_user_input
        return {
            current_task.id: TaskResponse(
                id=current_task.id,
                answer="done",
                success=True,
            )
        }

    monkeypatch.setattr("aworld.runners.ralph_runner.exec_tasks", fake_exec_tasks)

    await runner._execute_task(task, iter_num=2)

    assert captured["task_input"] == "current iteration payload"
    assert captured["origin_user_input"] == "current iteration payload"
    assert task.input == "current iteration payload"


@pytest.mark.asyncio
async def test_runner_ralph_run_preserves_public_api(monkeypatch):
    task = Task(input="Build API", conf=RalphConfig())
    response = TaskResponse(id=task.id, answer="done", success=True)

    async def fake_run(self):
        return response

    monkeypatch.setattr(RalphRunner, "run", fake_run)

    result = await runner_module.Runners.ralph_run(task, CompletionCriteria(max_iterations=1))

    assert result is response


@pytest.mark.asyncio
async def test_ralph_runner_do_run_invokes_iteration_evaluator_after_execution(tmp_path):
    task = Task(
        input="Build API",
        conf=RalphConfig(
            workspace=str(tmp_path),
            verify=RalphVerifyConfig(enabled=True, commands=["pytest -q"], run_on_each_iteration=True),
        ),
    )
    runner = RalphRunner(task=task, completion_criteria=CompletionCriteria(max_iterations=2))
    runner.loop_context = LoopContext(
        completion_criteria=CompletionCriteria(max_iterations=2),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    response = TaskResponse(id=task.id, answer="done", success=True)
    runner._execute_task = AsyncMock(return_value=response)
    runner.evaluator = AsyncMock()
    runner.stop_detector.should_stop = AsyncMock(
        side_effect=[
            type("StopDecision", (), {"should_stop": False, "stop_type": None, "reason": None})(),
            type("StopDecision", (), {"should_stop": True, "stop_type": "max_iterations", "reason": "done"})(),
        ]
    )

    result = await runner.do_run()

    assert result is response
    runner.evaluator.evaluate.assert_awaited_once_with(
        task=task,
        iter_num=1,
        execution_result=response,
        phase="post_iteration",
    )


@pytest.mark.asyncio
async def test_ralph_runner_do_run_skips_iteration_evaluator_when_run_on_each_iteration_is_disabled(tmp_path):
    task = Task(
        input="Build API",
        conf=RalphConfig(
            workspace=str(tmp_path),
            verify=RalphVerifyConfig(enabled=True, commands=["pytest -q"], run_on_each_iteration=False),
        ),
    )
    runner = RalphRunner(task=task, completion_criteria=CompletionCriteria(max_iterations=2))
    runner.loop_context = LoopContext(
        completion_criteria=CompletionCriteria(max_iterations=2),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    response = TaskResponse(id=task.id, answer="done", success=True)
    runner._execute_task = AsyncMock(return_value=response)
    runner.evaluator = AsyncMock()
    runner.stop_detector.should_stop = AsyncMock(
        side_effect=[
            type("StopDecision", (), {"should_stop": False, "stop_type": StopType.NONE, "reason": None})(),
            type("StopDecision", (), {"should_stop": True, "stop_type": StopType.MAX_ITERATIONS, "reason": "done"})(),
        ]
    )

    result = await runner.do_run()

    assert result is response
    runner.evaluator.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_ralph_runner_do_run_verifies_before_completion_when_configured(tmp_path):
    task = Task(
        input="Build API",
        conf=RalphConfig(
            workspace=str(tmp_path),
            verify=RalphVerifyConfig(
                enabled=True,
                commands=["pytest -q"],
                run_on_each_iteration=False,
                run_before_completion=True,
            ),
        ),
    )
    runner = RalphRunner(task=task, completion_criteria=CompletionCriteria(max_iterations=3))
    runner.loop_context = LoopContext(
        completion_criteria=CompletionCriteria(max_iterations=3),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    response = TaskResponse(id=task.id, answer="done", success=True)
    runner._execute_task = AsyncMock(return_value=response)
    runner.evaluator = AsyncMock()
    runner.stop_detector.should_stop = AsyncMock(
        side_effect=[
            type("StopDecision", (), {"should_stop": False, "stop_type": StopType.NONE, "reason": None})(),
            type("StopDecision", (), {"should_stop": True, "stop_type": StopType.COMPLETION, "reason": "done"})(),
        ]
    )

    result = await runner.do_run()

    assert result is response
    runner.evaluator.evaluate.assert_awaited_once_with(
        task=task,
        iter_num=1,
        execution_result=response,
        phase="before_completion",
    )


@pytest.mark.asyncio
async def test_ralph_runner_do_run_does_not_verify_before_non_completion_stop(tmp_path):
    task = Task(
        input="Build API",
        conf=RalphConfig(
            workspace=str(tmp_path),
            verify=RalphVerifyConfig(
                enabled=True,
                commands=["pytest -q"],
                run_on_each_iteration=False,
                run_before_completion=True,
            ),
        ),
    )
    runner = RalphRunner(task=task, completion_criteria=CompletionCriteria(max_iterations=2))
    runner.loop_context = LoopContext(
        completion_criteria=CompletionCriteria(max_iterations=2),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    response = TaskResponse(id=task.id, answer="done", success=True)
    runner._execute_task = AsyncMock(return_value=response)
    runner.evaluator = AsyncMock()
    runner.stop_detector.should_stop = AsyncMock(
        side_effect=[
            type("StopDecision", (), {"should_stop": False, "stop_type": StopType.NONE, "reason": None})(),
            type("StopDecision", (), {"should_stop": True, "stop_type": StopType.MAX_ITERATIONS, "reason": "done"})(),
        ]
    )

    result = await runner.do_run()

    assert result is response
    runner.evaluator.evaluate.assert_not_awaited()
