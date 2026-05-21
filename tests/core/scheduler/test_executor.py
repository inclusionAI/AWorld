# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for CronExecutor - agent resolution and execution.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from types import SimpleNamespace
import pytz

from aworld.core.scheduler.executor import CronExecutor
from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload, CronJobState
from aworld.core.task import TaskResponse


@pytest.fixture
def executor():
    """Create executor instance."""
    return CronExecutor()


@pytest.fixture
def sample_job():
    """Create a sample job for testing."""
    return CronJob(
        name="test-job",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(
            message="Run test task",
            agent_name="TestAgent",
            tool_names=["test_tool"],
        ),
        state=CronJobState(next_run_at=datetime.now(pytz.UTC).isoformat()),
    )


class MockSwarm:
    """Mock Swarm for testing."""
    def __init__(self, name="MockSwarm"):
        self.name = name
        self.root = MagicMock(name="root_agent")


@pytest.mark.asyncio
async def test_resolve_swarm_success(executor):
    """
    Test that _resolve_swarm correctly resolves and caches swarm.

    This verifies fix for Issue #1:
    - Uses injected resolver instead of importing CLI registry
    - Returns full swarm without re-wrapping
    - Caches the swarm for reuse
    """
    mock_swarm = MockSwarm("TestSwarm")
    resolve_swarm = AsyncMock(return_value=mock_swarm)
    executor = CronExecutor(swarm_resolver=resolve_swarm)

    # First call should resolve and cache
    swarm1 = await executor._resolve_swarm("TestAgent")

    assert swarm1 is mock_swarm
    assert swarm1.name == "TestSwarm"
    assert "TestAgent" in executor._agent_cache

    # Second call should return cached swarm
    swarm2 = await executor._resolve_swarm("TestAgent")
    assert swarm2 is swarm1  # Same instance
    resolve_swarm.assert_awaited_once_with("TestAgent")


@pytest.mark.asyncio
async def test_resolve_swarm_aworld_bypasses_cache():
    """Aworld cron jobs should rebuild swarm each run to refresh dynamic prompt state."""
    resolve_swarm = AsyncMock(
        side_effect=[
            MockSwarm("AworldSwarm-1"),
            MockSwarm("AworldSwarm-2"),
        ]
    )
    executor = CronExecutor(swarm_resolver=resolve_swarm)

    swarm1 = await executor._resolve_swarm("Aworld")
    swarm2 = await executor._resolve_swarm("Aworld")

    assert swarm1 is not swarm2
    assert swarm1.name == "AworldSwarm-1"
    assert swarm2.name == "AworldSwarm-2"
    assert "Aworld" not in executor._agent_cache
    assert resolve_swarm.await_count == 2


@pytest.mark.asyncio
async def test_resolve_swarm_agent_not_found(executor):
    """Test that _resolve_swarm returns None for missing agent."""
    resolve_swarm = AsyncMock(return_value=None)
    executor = CronExecutor(swarm_resolver=resolve_swarm)

    swarm = await executor._resolve_swarm("NonExistentAgent")

    assert swarm is None
    resolve_swarm.assert_awaited_once_with("NonExistentAgent")


@pytest.mark.asyncio
async def test_resolve_swarm_without_resolver_returns_none(executor):
    """Core executor should fail gracefully when no resolver is configured."""
    swarm = await executor._resolve_swarm("UnconfiguredAgent")

    assert swarm is None


@pytest.mark.asyncio
async def test_resolve_swarm_preserves_team_structure(executor):
    """
    Test that executor preserves TeamSwarm structure.

    Critical test for Issue #1 - ensures we don't re-wrap swarm as Swarm(agent),
    which would lose TeamSwarm sub-agents.
    """
    # Create a mock TeamSwarm with multiple agents
    mock_team_swarm = MockSwarm("TeamSwarm")
    mock_team_swarm.agents = {
        "leader": MagicMock(name="leader"),
        "worker1": MagicMock(name="worker1"),
        "worker2": MagicMock(name="worker2"),
    }
    resolve_swarm = AsyncMock(return_value=mock_team_swarm)
    executor = CronExecutor(swarm_resolver=resolve_swarm)

    swarm = await executor._resolve_swarm("TeamAgent")

    # Verify full TeamSwarm structure is preserved
    assert swarm is mock_team_swarm
    assert hasattr(swarm, 'agents')
    assert len(swarm.agents) == 3
    resolve_swarm.assert_awaited_once_with("TeamAgent")


@pytest.mark.asyncio
async def test_resolve_swarm_supports_sync_resolver():
    """Resolver may be provided as a synchronous callback."""
    mock_swarm = MockSwarm("SyncSwarm")
    executor = CronExecutor(swarm_resolver=lambda agent_name: mock_swarm if agent_name == "SyncAgent" else None)

    swarm = await executor._resolve_swarm("SyncAgent")

    assert swarm is mock_swarm


@pytest.mark.asyncio
async def test_execute_success(executor, sample_job):
    """
    Test successful job execution.

    Verifies that:
    - Swarm is resolved correctly
    - Runners.run() is called with correct parameters
    - Result is returned
    """
    mock_swarm = MockSwarm()
    mock_result = TaskResponse(success=True, msg="Task completed", answer="Result")

    # Mock _resolve_swarm
    executor._resolve_swarm = AsyncMock(return_value=mock_swarm)

    # Mock Runners.run
    with patch('aworld.runner.Runners.run', new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_result

        result = await executor.execute(sample_job)

        # Verify swarm resolution
        executor._resolve_swarm.assert_called_once_with("TestAgent")

        # Verify Runners.run called correctly
        mock_run.assert_called_once_with(
            input="Run test task",
            swarm=mock_swarm,
            tool_names=["test_tool"],
            session_id=None,
        )

        # Verify result
        assert result.success is True
        assert result.msg == "Task completed"


@pytest.mark.asyncio
async def test_execute_ignores_persisted_tool_restrictions_for_aworld(executor):
    """Legacy Aworld cron jobs should run without persisted tool allowlists."""
    aworld_job = CronJob(
        name="aworld-job",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        payload=CronPayload(
            message="Run aworld task",
            agent_name="Aworld",
            tool_names=["CAST_SEARCH", "bash", "SKILL"],
        ),
        state=CronJobState(next_run_at=datetime.now(pytz.UTC).isoformat()),
    )

    mock_swarm = MockSwarm()
    mock_result = TaskResponse(success=True, msg="Task completed", answer="Result")

    executor._resolve_swarm = AsyncMock(return_value=mock_swarm)

    with patch('aworld.runner.Runners.run', new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_result

        result = await executor.execute(aworld_job)

        mock_run.assert_called_once_with(
            input="Run aworld task",
            swarm=mock_swarm,
            tool_names=[],
            session_id=None,
        )
        assert result.success is True


@pytest.mark.asyncio
async def test_execute_agent_not_found(executor, sample_job):
    """Test execution fails gracefully when agent not found."""
    executor._resolve_swarm = AsyncMock(return_value=None)

    result = await executor.execute(sample_job)

    assert result.success is False
    assert "Agent not found" in result.msg


@pytest.mark.asyncio
async def test_execute_with_retry_success_first_attempt(executor, sample_job):
    """Test execute_with_retry succeeds on first attempt."""
    mock_result = TaskResponse(success=True, msg="Success")
    executor.execute = AsyncMock(return_value=mock_result)

    result = await executor.execute_with_retry(sample_job, max_retries=3)

    assert result.success is True
    assert executor.execute.call_count == 1


@pytest.mark.asyncio
async def test_execute_with_retry_success_after_failures(executor, sample_job):
    """Test execute_with_retry succeeds after some failures."""
    # First two attempts fail, third succeeds
    executor.execute = AsyncMock(
        side_effect=[
            TaskResponse(success=False, msg="Fail 1"),
            TaskResponse(success=False, msg="Fail 2"),
            TaskResponse(success=True, msg="Success"),
        ]
    )

    result = await executor.execute_with_retry(sample_job, max_retries=3)

    assert result.success is True
    assert executor.execute.call_count == 3


@pytest.mark.asyncio
async def test_execute_with_retry_all_attempts_fail(executor, sample_job):
    """Test execute_with_retry fails after exhausting retries."""
    executor.execute = AsyncMock(
        return_value=TaskResponse(success=False, msg="Persistent failure")
    )

    result = await executor.execute_with_retry(sample_job, max_retries=2)

    assert result.success is False
    assert executor.execute.call_count == 3  # Initial + 2 retries


@pytest.mark.asyncio
async def test_execute_with_retry_exception_handling(executor, sample_job):
    """Test execute_with_retry handles exceptions during execution."""
    executor.execute = AsyncMock(side_effect=RuntimeError("Execution error"))

    result = await executor.execute_with_retry(sample_job, max_retries=1)

    assert result.success is False
    assert "failed after 1 retries" in result.msg


@pytest.mark.asyncio
async def test_execute_with_retry_streams_detailed_progress(executor, sample_job):
    """Cron execution should surface stream events and final answer to follow mode."""
    mock_swarm = MockSwarm()
    executor._resolve_swarm = AsyncMock(return_value=mock_swarm)

    events = [
        SimpleNamespace(
            output_type=lambda: "step",
            alias_name="读取 skill 文档",
            name="ReadSkill",
            status="START",
            step_num=1,
        ),
        SimpleNamespace(
            output_type=lambda: "tool_call",
            data=SimpleNamespace(
                function=SimpleNamespace(
                    name="bash",
                    arguments='{"cmd":"python twitter_scraper_10_posts.py"}',
                )
            ),
        ),
        SimpleNamespace(
            output_type=lambda: "tool_call_result",
            tool_name="bash",
            data="saved twitter_latest_10_posts.md",
        ),
        SimpleNamespace(
            output_type=lambda: "message",
            response="已抓取完成，并保存到当前目录。",
            reasoning=None,
        ),
    ]

    class FakeStreamingOutputs:
        def __init__(self):
            self._task_response = TaskResponse(
                success=True,
                answer="已抓取完成，并保存到当前目录。",
                msg="ok",
            )
            self._run_impl_task = asyncio.create_task(self._result())

        async def _result(self):
            return {
                "task-123": self._task_response
            }

        async def stream_events(self):
            for event in events:
                yield event

        def response(self):
            return self._task_response

        def get_message_output_content(self):
            return "Aworld:已抓取完成，并保存到当前目录。"

    progress_messages = []

    async def record_progress(level, message):
        progress_messages.append((level, message))

    with patch("aworld.runner.Runners.streamed_run_task", return_value=FakeStreamingOutputs()):
        result = await executor.execute_with_retry(
            sample_job,
            max_retries=0,
            progress_callback=record_progress,
        )

    assert result.success is True
    assert any("步骤 #1 开始：读取 skill 文档" in message for _, message in progress_messages)
    assert any("工具调用：bash" in message for _, message in progress_messages)
    assert any("工具结果：bash" in message for _, message in progress_messages)
    assert any("Agent 输出：" in message for _, message in progress_messages)
    assert any("最终回答：" in message for _, message in progress_messages)


@pytest.mark.asyncio
async def test_cache_isolation_between_agents(executor):
    """Test that swarm cache is isolated per agent name."""
    swarm1 = MockSwarm("Swarm1")
    swarm2 = MockSwarm("Swarm2")
    resolve_swarm = AsyncMock(side_effect=lambda agent_name: {
        "Agent1": swarm1,
        "Agent2": swarm2,
    }.get(agent_name))
    executor = CronExecutor(swarm_resolver=resolve_swarm)

    result1 = await executor._resolve_swarm("Agent1")
    result2 = await executor._resolve_swarm("Agent2")

    assert result1 is swarm1
    assert result2 is swarm2
    assert result1 is not result2
    assert resolve_swarm.await_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
