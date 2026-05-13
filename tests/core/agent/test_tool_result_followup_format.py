from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentMemoryConfig
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionModel, ActionResult
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.memory import MemoryConfig
from aworld.memory.db.filesystem import FileSystemMemoryStore
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryAIMessage, MemoryHumanMessage, MemoryToolMessage, MessageMetadata


@pytest.mark.asyncio
async def test_cron_tool_results_are_reframed_with_confirmed_next_run():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    aggregated = await agent._tools_aggregate_func([
        ActionResult(
            tool_name="cron",
            content={
                "success": True,
                "job_id": "job-123",
                "next_run": "2026-04-14T17:17:00+08:00",
                "next_run_display": "2026年4月14日（星期二）17:17",
                "message": "Created task '喝水提醒' (ID: job-123)",
            },
        )
    ])

    policy_info = aggregated[0].policy_info
    assert "next_run=2026-04-14T17:17:00+08:00" in policy_info
    assert "next_run_display=2026年4月14日（星期二）17:17" in policy_info
    assert "source of truth" in policy_info
    assert "do not reuse any earlier guessed schedule_value" in policy_info
    assert "infer the weekday yourself" in policy_info


@pytest.mark.asyncio
async def test_failed_cron_tool_results_block_false_success_claims():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    aggregated = await agent._tools_aggregate_func([
        ActionResult(
            tool_name="cron",
            content={
                "success": False,
                "error": "One-time schedule is already in the past",
            },
        )
    ])

    policy_info = aggregated[0].policy_info
    assert "Cron returned an error" in policy_info
    assert "Do not claim the reminder or scheduled task was created" in policy_info


@pytest.mark.asyncio
async def test_large_tool_results_are_compacted_for_followup():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    aggregated = await agent._tools_aggregate_func([
        ActionResult(
            tool_name="terminal",
            action_name="exec",
            content="HEADER\n" + ("A" * 9000) + "\nFOOTER",
        )
    ])

    policy_info = aggregated[0].policy_info
    assert "Tool output compacted for context reuse." in policy_info
    assert "HEADER" in policy_info
    assert "FOOTER" in policy_info
    assert "Original size:" in policy_info


def test_aworld_result_validation_flags_adjacent_but_wrong_target():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    feedback = agent._build_result_validation_feedback(
        authoritative_request="看看我的x账号关注的elliotchen100用户发布的帖子，将其中AI 编程的下一个瓶颈，不是代码，是理解主题的文章添加到我的本地知识库Obsidian中管理起来",
        final_response_text="我已经成功保存了 elliotchen100 那篇关于 AI 工具的下一个瓶颈在交互界面的帖子，并整理了 pneuma-skills 项目内容。",
        source_evidence_text="source: https://x.com/elliotchen100/status/2041300212875243752\n标题: AI 工具的下一个瓶颈 - 交互界面而非模型能力",
    )

    assert feedback is not None
    assert "result validation" in feedback.lower()
    assert "AI 编程的下一个瓶颈，不是代码，是理解" in feedback


def test_aworld_result_validation_requires_source_evidence_not_final_wording_or_artifact():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    feedback = agent._build_result_validation_feedback(
        authoritative_request="看看我的x账号关注的elliotchen100用户发布的帖子，将其中AI 编程的下一个瓶颈，不是代码，是理解主题的文章添加到我的本地知识库Obsidian中管理起来",
        final_response_text="我已经成功保存了《AI 编程的下一个瓶颈，不是代码，是理解》这篇文章。",
        source_evidence_text="source: https://x.com/elliotchen100/status/2041300212875243752\n核心观点: AI工具的下一个瓶颈不在模型能力，在交互界面。",
        artifact_evidence_text="[artifact:/tmp/wrong.md]\ntitle: AI 编程的下一个瓶颈，不是代码，是理解\nsource: https://x.com/elliotchen100/status/2041300212875243752",
    )

    assert feedback is not None
    assert "source evidence" in feedback.lower()
    assert "generated artifacts or final wording" in feedback


def test_aworld_result_validation_recovery_brief_uses_authoritative_request_and_verified_evidence():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    brief = agent._build_result_validation_recovery_brief(
        authoritative_request="看看我的x账号关注的elliotchen100用户发布的帖子，将其中AI 编程的下一个瓶颈，不是代码，是理解主题的文章添加到我的本地知识库Obsidian中管理起来",
        validation_feedback=(
            "Result validation mismatch: the authoritative request still requires these anchors to be present in "
            "source evidence from this run, but they are still missing: AI 编程的下一个瓶颈，不是代码，是理解."
        ),
        source_evidence_text="source: https://x.com/elliotchen100/status/2041300212875243752\n核心观点：AI工具的下一个瓶颈不在模型能力，在交互界面。",
        artifact_evidence_text="[artifact:/tmp/wrong.md]\ntitle: AI 工具的下一个瓶颈 - 交互界面",
    )

    assert "Original request" in brief
    assert "Verified source evidence from this run" in brief
    assert "Treat this as unfinished" in brief
    assert "AI 编程的下一个瓶颈，不是代码，是理解" in brief
    assert "adjacent but wrong" in brief


@pytest.mark.asyncio
async def test_aworld_result_validation_does_not_use_human_request_as_evidence(tmp_path):
    import aworld.memory.main as memory_main

    class DummyContext:
        def __init__(self, authoritative_request: str):
            self.origin_user_input = authoritative_request
            self.task_input = authoritative_request

        def get_agent_memory_config(self, namespace: str):
            return AgentMemoryConfig()

        def get_task(self):
            return SimpleNamespace(id="test_task", session_id="test_session", user_id="user")

    authoritative_request = '请找到标题为“只应存在于人类请求里的目标短语”的帖子，并保存到 Obsidian。'
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    context = DummyContext(authoritative_request)

    memory_main.MEMORY_HOLDER.clear()
    try:
        MemoryFactory.init(
            custom_memory_store=FileSystemMemoryStore(memory_root=str(tmp_path)),
            config=MemoryConfig(provider="aworld"),
        )
        await MemoryFactory.instance().add(
            MemoryHumanMessage(
                content=authoritative_request,
                metadata=MessageMetadata(
                    agent_id=agent.id(),
                    agent_name="Aworld",
                    session_id="test_session",
                    task_id="test_task",
                    user_id="user",
                ),
            ),
            agent_memory_config=context.get_agent_memory_config(agent.id()),
        )

        evidence = agent._collect_result_validation_evidence(context)
        feedback = agent._build_result_validation_feedback_from_context(
            context=context,
            final_response_text="我已经保存了另一篇不相关的文章。",
        )

        assert evidence == {"source": "", "artifact": ""}
        assert feedback is None
    finally:
        memory_main.MEMORY_HOLDER.clear()


@pytest.mark.asyncio
async def test_aworld_result_validation_ignores_ai_rephrasing_and_uses_tool_output_only(tmp_path):
    import aworld.memory.main as memory_main

    class DummyContext:
        def __init__(self, authoritative_request: str):
            self.origin_user_input = authoritative_request
            self.task_input = authoritative_request

        def get_agent_memory_config(self, namespace: str):
            return AgentMemoryConfig()

        def get_task(self):
            return SimpleNamespace(id="test_task", session_id="test_session", user_id="user")

    authoritative_request = "查找标题为“AI 编程的下一个瓶颈，不是代码，是理解”的帖子并保存。"
    tool_wrapper = {
        "success": True,
        "message": (
            "# Terminal Command Execution ✅\n"
            "**Command:** `echo target`\n"
            "## Output\n"
            "```\n"
            "source: https://x.com/elliotchen100/status/2041300212875243752\n"
            "核心观点：AI工具的下一个瓶颈不在模型能力，在交互界面。\n"
            "```"
        ),
    }

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    context = DummyContext(authoritative_request)
    metadata = MessageMetadata(
        agent_id=agent.id(),
        agent_name="Aworld",
        session_id="test_session",
        task_id="test_task",
        user_id="user",
    )

    memory_main.MEMORY_HOLDER.clear()
    try:
        MemoryFactory.init(
            custom_memory_store=FileSystemMemoryStore(memory_root=str(tmp_path)),
            config=MemoryConfig(provider="aworld"),
        )
        await MemoryFactory.instance().add(
            MemoryAIMessage(
                content="我已经找到“AI 编程的下一个瓶颈，不是代码，是理解”这篇帖子了。",
                metadata=metadata,
            ),
            agent_memory_config=context.get_agent_memory_config(agent.id()),
        )
        await MemoryFactory.instance().add(
            MemoryToolMessage(
                tool_call_id="call-1",
                content=tool_wrapper,
                metadata=metadata,
            ),
            agent_memory_config=context.get_agent_memory_config(agent.id()),
        )

        evidence = agent._collect_result_validation_evidence(context)

        assert "不是代码，是理解" not in evidence["source"]
        assert "交互界面" in evidence["source"]
        assert evidence["artifact"] == ""
    finally:
        memory_main.MEMORY_HOLDER.clear()


@pytest.mark.asyncio
async def test_aworld_result_validation_retry_degrades_empty_llm_response():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    context = SimpleNamespace(context_info={})
    message = SimpleNamespace(context=context)
    observation = SimpleNamespace(from_agent_name=None)

    async def _raise_empty_response(*args, **kwargs):
        raise AWorldRuntimeException("LLM returned empty or invalid response: {}")

    agent.async_policy = _raise_empty_response

    result = await agent._retry_for_result_validation(
        validation_feedback="Result validation mismatch: target evidence is still missing.",
        observation=observation,
        info={},
        message=message,
        kwargs={},
    )

    assert len(result) == 1
    assert "not claiming success" in result[0].policy_info
    assert "follow-up validation round failed" in result[0].policy_info
    assert context.context_info == {}
    assert agent._finished is True


@pytest.mark.asyncio
async def test_aworld_result_validation_retry_uses_recovery_brief():
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    context = SimpleNamespace(
        context_info={},
        origin_user_input="查找标题为“AI 编程的下一个瓶颈，不是代码，是理解”的帖子并保存到 Obsidian。",
        task_input="查找标题为“AI 编程的下一个瓶颈，不是代码，是理解”的帖子并保存到 Obsidian。",
    )
    message = SimpleNamespace(context=context)
    observation = SimpleNamespace(from_agent_name=None)
    captured = {}

    agent._collect_result_validation_evidence = lambda ctx: {
        "source": "source: https://x.com/elliotchen100/status/2041300212875243752\n核心观点：AI工具的下一个瓶颈不在模型能力，在交互界面。",
        "artifact": "[artifact:/tmp/wrong.md]\ntitle: AI 工具的下一个瓶颈 - 交互界面",
    }

    async def _capture_followup(observation_arg, **kwargs):
        captured["content"] = observation_arg.content
        return [ActionModel(agent_name=agent.id(), policy_info="继续调查")]

    agent.async_policy = _capture_followup

    result = await agent._retry_for_result_validation(
        validation_feedback="Result validation mismatch: target evidence is still missing.",
        observation=observation,
        info={},
        message=message,
        kwargs={},
    )

    assert len(result) == 1
    assert "Original request" in captured["content"]
    assert "Verified source evidence from this run" in captured["content"]
    assert "Treat this as unfinished" in captured["content"]
