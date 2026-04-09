# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for CronExecutor - agent resolution and execution.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
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


class MockLocalAgent:
    """Mock LocalAgent for testing."""
    def __init__(self, swarm):
        self._swarm = swarm

    async def get_swarm(self, context=None):
        """Return mock swarm."""
        return self._swarm


class MockLocalAgentRegistry:
    """Mock LocalAgentRegistry for testing."""
    def __init__(self, agents=None):
        self._agents = agents or {}

    def get(self, name):
        """Get agent by name."""
        return self._agents.get(name)


@pytest.mark.asyncio
async def test_resolve_swarm_success(executor):
    """
    Test that _resolve_swarm correctly resolves and caches swarm.

    This verifies fix for Issue #1:
    - Properly awaits async get_swarm()
    - Returns full swarm without re-wrapping
    - Caches the swarm for reuse
    """
    mock_swarm = MockSwarm("TestSwarm")
    mock_local_agent = MockLocalAgent(mock_swarm)
    mock_registry = MockLocalAgentRegistry({"TestAgent": mock_local_agent})

    with patch('aworld_cli.core.agent_registry.LocalAgentRegistry', return_value=mock_registry):
        # First call should resolve and cache
        swarm1 = await executor._resolve_swarm("TestAgent")

        assert swarm1 is mock_swarm
        assert swarm1.name == "TestSwarm"
        assert "TestAgent" in executor._agent_cache

        # Second call should return cached swarm
        swarm2 = await executor._resolve_swarm("TestAgent")
        assert swarm2 is swarm1  # Same instance


@pytest.mark.asyncio
async def test_resolve_swarm_agent_not_found(executor):
    """Test that _resolve_swarm returns None for missing agent."""
    mock_registry = MockLocalAgentRegistry({})  # Empty registry

    with patch('aworld_cli.core.agent_registry.LocalAgentRegistry', return_value=mock_registry):
        swarm = await executor._resolve_swarm("NonExistentAgent")

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

    mock_local_agent = MockLocalAgent(mock_team_swarm)
    mock_registry = MockLocalAgentRegistry({"TeamAgent": mock_local_agent})

    with patch('aworld_cli.core.agent_registry.LocalAgentRegistry', return_value=mock_registry):
        swarm = await executor._resolve_swarm("TeamAgent")

        # Verify full TeamSwarm structure is preserved
        assert swarm is mock_team_swarm
        assert hasattr(swarm, 'agents')
        assert len(swarm.agents) == 3


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
async def test_cache_isolation_between_agents(executor):
    """Test that swarm cache is isolated per agent name."""
    swarm1 = MockSwarm("Swarm1")
    swarm2 = MockSwarm("Swarm2")

    agent1 = MockLocalAgent(swarm1)
    agent2 = MockLocalAgent(swarm2)

    registry = MockLocalAgentRegistry({
        "Agent1": agent1,
        "Agent2": agent2,
    })

    with patch('aworld_cli.core.agent_registry.LocalAgentRegistry', return_value=registry):
        result1 = await executor._resolve_swarm("Agent1")
        result2 = await executor._resolve_swarm("Agent2")

        assert result1 is swarm1
        assert result2 is swarm2
        assert result1 is not result2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
