import pytest

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.context.amni import ApplicationContext


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_free", [False, True])
async def test_forced_instruction_only_skill_preserves_tools_unless_opted_out(tool_free) -> None:
    context = ApplicationContext.create(
        session_id="session-test",
        task_id="task-test",
        task_content="Create a plan",
    )

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            disable_tools_for_instruction_only_skills=tool_free,
            skill_configs={
                "writing-plans": {
                    "name": "writing-plans",
                    "description": "Create implementation plans",
                    "usage": "Use the planning workflow",
                    "tool_list": {},
                    "active": True,
                }
            }
        ),
    )
    agent._llm = object()  # Tool filtering should not instantiate a model client.
    agent.tool_mapping = {}
    context.task_input_object.metadata["requested_skill_names"] = ["writing-plans"]

    async def _fake_get_active_skills(namespace: str):
        assert namespace == agent.id()
        return ["writing-plans"]

    context.get_active_skills = _fake_get_active_skills
    agent.tools = [{"type": "function", "function": {"name": "bash"}}]

    filtered = await agent._filter_tools(context)

    assert filtered == ([] if tool_free else agent.tools)


@pytest.mark.asyncio
async def test_filter_tools_keeps_tools_without_forced_skill() -> None:
    context = ApplicationContext.create(
        session_id="session-test",
        task_id="task-test",
        task_content="Create a plan",
    )

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            skill_configs={
                "writing-plans": {
                    "name": "writing-plans",
                    "description": "Create implementation plans",
                    "usage": "Use the planning workflow",
                    "tool_list": {},
                    "active": True,
                }
            }
        ),
    )
    agent._llm = object()
    agent.tool_mapping = {}
    async def _fake_get_active_skills(namespace: str):
        assert namespace == agent.id()
        return ["writing-plans"]

    context.get_active_skills = _fake_get_active_skills
    agent.tools = [{"type": "function", "function": {"name": "bash"}}]

    filtered = await agent._filter_tools(context)

    assert filtered == [{"type": "function", "function": {"name": "bash"}}]
