import pytest

from aworld.core.context import ApplicationContext
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)
from aworld.core.context.amni.prompt.neurons.task_grounding_neuron import (
    TASK_GROUNDING_NEURON_NAME,
    TaskGroundingNeuron,
)


def create_test_context(
    task_content: str = "Save today's @omarsar0 AI papers about agentic RL into Obsidian.",
    origin_user_input: str | None = None,
):
    task_input = TaskInput(
        session_id="test_session",
        task_id="test_task",
        task_content=task_content,
        origin_user_input=origin_user_input,
    )
    working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
    task_state = ApplicationTaskContextState(
        task_input=task_input,
        working_state=working_state,
        task_output=TaskOutput(),
    )
    return ApplicationContext(task_state=task_state)


@pytest.mark.asyncio
async def test_task_grounding_neuron_uses_origin_user_input_as_authoritative_request():
    context = create_test_context(
        task_content="Inspect page and summarize findings",
        origin_user_input="Find today's AI paper recommendations from @omarsar0 about agentic RL and save them into Obsidian.",
    )
    neuron = TaskGroundingNeuron()

    items = await neuron.format_items(context)
    formatted = await neuron.format(context, items=items)

    assert items == [
        "Find today's AI paper recommendations from @omarsar0 about agentic RL and save them into Obsidian."
    ]
    assert "Task Grounding" in formatted
    assert "Authoritative user request" in formatted
    assert "Current task view" in formatted
    assert "Do not silently change named entities, handles, URLs, file paths, dates, time windows, topic filters, or requested deliverables." in formatted
    assert "Before claiming success, verify the final result matches the authoritative request using evidence from this run." in formatted


@pytest.mark.asyncio
async def test_task_grounding_neuron_skips_duplicate_current_task_view():
    context = create_test_context()
    neuron = TaskGroundingNeuron()

    formatted = await neuron.format(context)

    assert formatted.count("Authoritative user request") == 1
    assert "Current task view" not in formatted


@pytest.mark.asyncio
async def test_task_grounding_neuron_desc():
    context = create_test_context()
    neuron = TaskGroundingNeuron()

    desc = await neuron.desc(context)

    assert desc == "Task grounding rules derived from the authoritative user request"
    assert neuron.name == TASK_GROUNDING_NEURON_NAME


@pytest.mark.asyncio
async def test_task_grounding_neuron_surfaces_required_anchors():
    context = create_test_context(
        task_content="查找帖子并保存结果",
        origin_user_input="看看我的x账号关注的elliotchen100用户发布的帖子，将其中AI 编程的下一个瓶颈，不是代码，是理解主题的文章添加到我的本地知识库Obsidian中管理起来",
    )
    neuron = TaskGroundingNeuron()

    formatted = await neuron.format(context)

    assert "Required anchors to preserve" in formatted
    assert "@elliotchen100" in formatted or "elliotchen100" in formatted
    assert "AI 编程的下一个瓶颈，不是代码，是理解" in formatted
