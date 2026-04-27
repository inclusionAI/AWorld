from unittest.mock import AsyncMock

import pytest

from aworld.core.task import Task, TaskResponse
from aworld.runners.ralph.config import RalphVerifyConfig
from aworld.runners.ralph.evaluator import IterationEvaluator
from aworld.runners.ralph.input_builder import IterationInputBuilder
from aworld.runners.ralph.memory import LoopMemoryStore
from aworld.runners.ralph.policy import RalphLoopPolicy
from aworld.runners.ralph.state import LoopContext, LoopState
from aworld.runners.ralph.types import CompletionCriteria


@pytest.mark.asyncio
async def test_iteration_evaluator_persists_failed_verify_output(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    task = Task(input="Build an API")
    evaluator = IterationEvaluator(
        context=context,
        memory_store=LoopMemoryStore(context),
        verify_config=RalphVerifyConfig(
            enabled=True,
            commands=["pytest -q"],
            run_on_each_iteration=True,
        ),
    )
    context.sand_box.terminal.run_code = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "success": False,
                "metadata": {
                    "return_code": 1,
                    "output_data": "FAILED tests/test_api.py::test_handlers",
                },
            },
            "error": None,
        }
    )

    result = await evaluator.evaluate(
        task=task,
        iter_num=1,
        execution_result=TaskResponse(id=task.id, answer="Created API handlers", success=True),
    )

    assert result.verify_result is not None
    assert result.verify_result.passed is False
    assert result.verify_result.commands[0].command == "pytest -q"
    assert result.verify_result.commands[0].exit_code == 1
    assert "FAILED tests/test_api.py::test_handlers" in result.verify_result.commands[0].output

    persisted_verify = await context.memory.read_verify_result(task.id, 1)
    assert persisted_verify is not None
    assert persisted_verify["passed"] is False
    assert persisted_verify["commands"][0]["command"] == "pytest -q"

    persisted_feedback = await context.memory.read_reflection_feedback(task.id, 1)
    assert persisted_feedback is not None
    assert "Verification failed" in persisted_feedback
    assert "pytest -q" in persisted_feedback


@pytest.mark.asyncio
async def test_iteration_evaluator_feeds_verify_failure_into_next_iteration_input(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    task = Task(input="Build an API")
    evaluator = IterationEvaluator(
        context=context,
        memory_store=LoopMemoryStore(context),
        verify_config=RalphVerifyConfig(
            enabled=True,
            commands=["pytest -q"],
            run_on_each_iteration=True,
        ),
    )
    context.sand_box.terminal.run_code = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "success": False,
                "metadata": {
                    "return_code": 1,
                    "output_data": "FAILED tests/test_api.py::test_handlers",
                },
            },
            "error": None,
        }
    )

    await evaluator.evaluate(
        task=task,
        iter_num=1,
        execution_result=TaskResponse(id=task.id, answer="Created API handlers", success=True),
    )

    builder = IterationInputBuilder(
        policy=RalphLoopPolicy(execution_mode="fresh_context", verify_enabled=True),
        memory_store=context.memory,
    )
    payload = await builder.build(
        task_id=task.id,
        original_task="Build an API",
        iteration=2,
        previous_answer="Created API handlers",
    )

    assert "Verification failed" in payload.task_input
    assert "pytest -q" in payload.task_input


@pytest.mark.asyncio
async def test_iteration_evaluator_skips_verify_when_disabled(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    task = Task(input="Build an API")
    evaluator = IterationEvaluator(
        context=context,
        memory_store=LoopMemoryStore(context),
        verify_config=RalphVerifyConfig(
            enabled=False,
            commands=["pytest -q"],
            run_on_each_iteration=True,
        ),
    )
    context.sand_box.terminal.run_code = AsyncMock(side_effect=AssertionError("verify should be skipped"))

    result = await evaluator.evaluate(
        task=task,
        iter_num=1,
        execution_result=TaskResponse(id=task.id, answer="Created API handlers", success=True),
    )

    assert result.verify_result is None
    assert await context.memory.read_verify_result(task.id, 1) is None
    assert await context.memory.read_reflection_feedback(task.id, 1) is None
