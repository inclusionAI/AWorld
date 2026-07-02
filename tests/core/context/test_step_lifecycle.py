import pytest

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.output.base import StepOutput


def test_context_step_lifecycle_produces_stable_step_id() -> None:
    context = Context()

    started = context.open_step(
        name="planner.agent",
        alias_name="Planner",
        step_num=1,
        namespace="planner.agent",
    )
    start_output = StepOutput.build_start_output(
        name=started["name"],
        alias_name=started["alias_name"],
        step_num=started["step_num"],
        step_id=started["step_id"],
        parent_step_id=started["parent_step_id"],
    )

    finished = context.close_step(namespace="planner.agent")
    assert finished is not None

    finish_output = StepOutput.build_finished_output(
        name=finished["name"],
        alias_name=finished["alias_name"],
        step_num=finished["step_num"],
        step_id=finished["step_id"],
        parent_step_id=finished["parent_step_id"],
    )

    assert start_output.step_id == finish_output.step_id
    assert context.current_step_id(namespace="planner.agent") is None


def test_nested_context_steps_record_parent_step_id_and_survive_deep_copy() -> None:
    context = Context()

    parent = context.open_step(
        name="planner.agent",
        alias_name="Planner",
        step_num=1,
        namespace="planner.agent",
    )
    child = context.open_step(
        name="search.agent",
        alias_name="Searcher",
        step_num=2,
        namespace="planner.agent",
    )

    assert child["parent_step_id"] == parent["step_id"]

    copied = context.deep_copy()
    closed_child = copied.close_step(namespace="planner.agent")
    closed_parent = copied.close_step(namespace="planner.agent")

    assert closed_child is not None
    assert closed_parent is not None
    assert closed_child["step_id"] == child["step_id"]
    assert closed_parent["step_id"] == parent["step_id"]


def test_cross_namespace_steps_inherit_parent_step_id() -> None:
    context = Context()

    parent = context.open_step(
        name="planner.agent",
        alias_name="Planner",
        step_num=1,
        namespace="planner.agent",
    )
    child = context.open_step(
        name="search.agent",
        alias_name="Searcher",
        step_num=1,
        namespace="search.agent",
    )

    assert child["parent_step_id"] == parent["step_id"]


@pytest.mark.asyncio
async def test_sub_task_context_inherits_parent_step_id() -> None:
    context = ApplicationContext.create(
        session_id="session-test",
        task_id="task-root",
        task_content="Root task",
    )

    parent = context.open_step(
        name="planner.agent",
        alias_name="Planner",
        step_num=1,
        namespace="planner.agent",
    )

    sub_context = await context.build_sub_context(
        "Sub task",
        sub_task_id="task-child",
    )
    child = sub_context.open_step(
        name="research.agent",
        alias_name="Researcher",
        step_num=1,
        namespace="research.agent",
    )

    assert child["parent_step_id"] == parent["step_id"]
