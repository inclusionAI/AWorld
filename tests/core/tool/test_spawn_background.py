# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Unit tests for background subagent spawning functionality.

Tests the spawn_background, check_task, wait_task, and cancel_task actions
of the SpawnSubagentTool.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aworld.core.tool.builtin.spawn_subagent_tool import SpawnSubagentTool, SpawnSubagentAction
from aworld.core.common import ActionModel, Observation
from aworld.config.conf import ConfigDict


@pytest.fixture
def mock_subagent_manager():
    """Create a mock SubagentManager."""
    manager = MagicMock()
    manager.agent.name.return_value = "test_agent"

    # Mock spawn method to simulate async execution
    async def mock_spawn(name, directive, **kwargs):
        await asyncio.sleep(0.1)  # Simulate work
        return f"Result from {name}: {directive[:50]}"

    manager.spawn = AsyncMock(side_effect=mock_spawn)
    return manager


@pytest.fixture
def spawn_tool(mock_subagent_manager):
    """Create SpawnSubagentTool instance with mock manager."""
    # Create minimal conf for AsyncTool
    conf = ConfigDict({})
    return SpawnSubagentTool(subagent_manager=mock_subagent_manager, conf=conf)


class TestSpawnBackground:
    """Test spawn_background action."""

    @pytest.mark.asyncio
    async def test_spawn_background_success(self, spawn_tool):
        """Test successful background task spawning."""
        action = ActionModel(
            action_name='spawn_background',
            params={
                'name': 'researcher',
                'directive': 'Research topic X'
            }
        )

        obs, reward, terminated, truncated, info = await spawn_tool.do_step([action])

        # Should return immediately with task_id
        assert reward == 1.0
        assert not terminated
        assert not truncated
        assert 'task_id' in info
        assert info['action'] == 'spawn_background'

        task_id = info['task_id']
        assert task_id.startswith('bg_researcher_')
        assert "Background task started" in obs.content

        # Task should be in registry
        assert task_id in spawn_tool._background_tasks
        task_info = spawn_tool._background_tasks[task_id]
        assert task_info['name'] == 'researcher'
        assert task_info['status'] == 'running'

    @pytest.mark.asyncio
    async def test_spawn_background_with_custom_task_id(self, spawn_tool):
        """Test background spawning with custom task_id."""
        action = ActionModel(
            action_name='spawn_background',
            params={
                'name': 'analyst',
                'directive': 'Analyze data',
                'task_id': 'my_custom_id'
            }
        )

        obs, reward, _, _, info = await spawn_tool.do_step([action])

        assert reward == 1.0
        assert info['task_id'] == 'my_custom_id'
        assert 'my_custom_id' in spawn_tool._background_tasks

    @pytest.mark.asyncio
    async def test_spawn_background_duplicate_task_id(self, spawn_tool):
        """Test error when using duplicate task_id."""
        # First spawn
        action1 = ActionModel(
            action_name='spawn_background',
            params={
                'name': 'agent1',
                'directive': 'Task 1',
                'task_id': 'duplicate_id'
            }
        )
        await spawn_tool.do_step([action1])

        # Second spawn with same ID
        action2 = ActionModel(
            action_name='spawn_background',
            params={
                'name': 'agent2',
                'directive': 'Task 2',
                'task_id': 'duplicate_id'
            }
        )

        obs, reward, _, _, info = await spawn_tool.do_step([action2])

        assert reward == 0.0
        assert 'Duplicate task_id' in info['error']

    @pytest.mark.asyncio
    async def test_spawn_background_missing_name(self, spawn_tool):
        """Test error when name parameter is missing."""
        action = ActionModel(
            action_name='spawn_background',
            params={
                'directive': 'Some task'
            }
        )

        obs, reward, _, _, info = await spawn_tool.do_step([action])

        assert reward == 0.0
        assert 'Missing name parameter' in info['error']

    @pytest.mark.asyncio
    async def test_spawn_background_missing_directive(self, spawn_tool):
        """Test error when directive parameter is missing."""
        action = ActionModel(
            action_name='spawn_background',
            params={
                'name': 'researcher'
            }
        )

        obs, reward, _, _, info = await spawn_tool.do_step([action])

        assert reward == 0.0
        assert 'Missing directive parameter' in info['error']


class TestCheckTask:
    """Test check_task action."""

    @pytest.mark.asyncio
    async def test_check_running_task(self, spawn_tool):
        """Test checking status of running task."""
        # Spawn background task
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Long task', 'task_id': 'task1'}
        )
        await spawn_tool.do_step([spawn_action])

        # Check status immediately
        check_action = ActionModel(
            action_name='check_task',
            params={'task_id': 'task1'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([check_action])

        assert reward == 1.0
        assert info['status'] == 'running'
        assert 'still executing' in obs.content.lower()

    @pytest.mark.asyncio
    async def test_check_completed_task(self, spawn_tool):
        """Test checking completed task."""
        # Spawn and wait for completion
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Quick task', 'task_id': 'task2'}
        )
        await spawn_tool.do_step([spawn_action])

        # Wait for completion
        await asyncio.sleep(0.2)

        # Check status
        check_action = ActionModel(
            action_name='check_task',
            params={'task_id': 'task2', 'include_result': True}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([check_action])

        assert reward == 1.0
        assert info['status'] == 'completed'
        assert 'Result from worker' in obs.content

    @pytest.mark.asyncio
    async def test_check_all_tasks(self, spawn_tool):
        """Test checking all background tasks."""
        # Spawn multiple tasks
        for i in range(3):
            action = ActionModel(
                action_name='spawn_background',
                params={'name': f'worker{i}', 'directive': f'Task {i}', 'task_id': f'task_{i}'}
            )
            await spawn_tool.do_step([action])

        # Check all
        check_action = ActionModel(
            action_name='check_task',
            params={'task_id': 'all'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([check_action])

        assert reward == 1.0
        assert info['total_tasks'] == 3
        assert 'task_0' in obs.content
        assert 'task_1' in obs.content
        assert 'task_2' in obs.content

    @pytest.mark.asyncio
    async def test_check_nonexistent_task(self, spawn_tool):
        """Test checking non-existent task."""
        check_action = ActionModel(
            action_name='check_task',
            params={'task_id': 'nonexistent'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([check_action])

        assert reward == 0.0
        assert 'not found' in info['error']


class TestWaitTask:
    """Test wait_task action."""

    @pytest.mark.asyncio
    async def test_wait_single_task(self, spawn_tool):
        """Test waiting for single task to complete."""
        # Spawn task
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Task', 'task_id': 'wait_test1'}
        )
        await spawn_tool.do_step([spawn_action])

        # Wait for completion
        wait_action = ActionModel(
            action_name='wait_task',
            params={'task_ids': 'wait_test1', 'timeout': 5}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([wait_action])

        assert reward == 1.0
        assert info['completed'] == 1
        assert info['pending'] == 0
        assert not info['timed_out']

    @pytest.mark.asyncio
    async def test_wait_multiple_tasks(self, spawn_tool):
        """Test waiting for multiple tasks."""
        # Spawn 3 tasks
        task_ids = []
        for i in range(3):
            task_id = f'multi_wait_{i}'
            task_ids.append(task_id)
            action = ActionModel(
                action_name='spawn_background',
                params={'name': f'worker{i}', 'directive': f'Task {i}', 'task_id': task_id}
            )
            await spawn_tool.do_step([action])

        # Wait for all
        wait_action = ActionModel(
            action_name='wait_task',
            params={'task_ids': ','.join(task_ids), 'timeout': 5}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([wait_action])

        assert reward == 1.0
        assert info['completed'] == 3
        assert info['pending'] == 0

    @pytest.mark.asyncio
    async def test_wait_timeout(self, spawn_tool):
        """Test wait with timeout."""
        # Create a long-running task
        async def slow_spawn(name, directive, **kwargs):
            await asyncio.sleep(10)  # Very long
            return "result"

        spawn_tool.subagent_manager.spawn = AsyncMock(side_effect=slow_spawn)

        # Spawn slow task
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'slow', 'directive': 'Slow task', 'task_id': 'timeout_test'}
        )
        await spawn_tool.do_step([spawn_action])

        # Wait with short timeout
        wait_action = ActionModel(
            action_name='wait_task',
            params={'task_ids': 'timeout_test', 'timeout': 0.2}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([wait_action])

        assert reward == 0.5  # Partial success
        assert info['timed_out']
        assert info['pending'] > 0

    @pytest.mark.asyncio
    async def test_wait_already_completed(self, spawn_tool):
        """Test waiting for already completed tasks."""
        # Spawn and complete
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Quick', 'task_id': 'already_done'}
        )
        await spawn_tool.do_step([spawn_action])
        await asyncio.sleep(0.2)

        # Wait (should return immediately)
        wait_action = ActionModel(
            action_name='wait_task',
            params={'task_ids': 'already_done'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([wait_action])

        assert reward == 1.0
        assert info['already_completed']


class TestCancelTask:
    """Test cancel_task action."""

    @pytest.mark.asyncio
    async def test_cancel_running_task(self, spawn_tool):
        """Test cancelling a running task."""
        # Create long-running task
        async def long_spawn(name, directive, **kwargs):
            await asyncio.sleep(5)
            return "result"

        spawn_tool.subagent_manager.spawn = AsyncMock(side_effect=long_spawn)

        # Spawn task
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Long task', 'task_id': 'cancel_test'}
        )
        await spawn_tool.do_step([spawn_action])

        # Cancel immediately
        cancel_action = ActionModel(
            action_name='cancel_task',
            params={'task_id': 'cancel_test'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([cancel_action])

        assert reward == 1.0
        assert info['cancelled']

        # Check task status
        async with spawn_tool._bg_lock:
            task_info = spawn_tool._background_tasks['cancel_test']
            assert task_info['status'] == 'cancelled'

    @pytest.mark.asyncio
    async def test_cancel_completed_task(self, spawn_tool):
        """Test cancelling already completed task."""
        # Spawn and complete
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Quick', 'task_id': 'completed_cancel'}
        )
        await spawn_tool.do_step([spawn_action])
        await asyncio.sleep(0.2)

        # Try to cancel
        cancel_action = ActionModel(
            action_name='cancel_task',
            params={'task_id': 'completed_cancel'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([cancel_action])

        assert reward == 0.0
        assert not info['cancelled']

    @pytest.mark.asyncio
    async def test_cancel_all_tasks(self, spawn_tool):
        """Test cancelling all background tasks."""
        # Create long-running spawn
        async def long_spawn(name, directive, **kwargs):
            await asyncio.sleep(5)
            return "result"

        spawn_tool.subagent_manager.spawn = AsyncMock(side_effect=long_spawn)

        # Spawn multiple tasks
        for i in range(3):
            action = ActionModel(
                action_name='spawn_background',
                params={'name': f'worker{i}', 'directive': f'Task {i}', 'task_id': f'cancel_all_{i}'}
            )
            await spawn_tool.do_step([action])

        # Cancel all
        cancel_action = ActionModel(
            action_name='cancel_task',
            params={'task_id': 'all'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([cancel_action])

        assert reward == 1.0
        assert info['cancelled_count'] == 3


class TestBackgroundTaskExecution:
    """Test actual background task execution."""

    @pytest.mark.asyncio
    async def test_task_completes_in_background(self, spawn_tool):
        """Test that task actually completes in background."""
        # Spawn task
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Background work', 'task_id': 'bg_test'}
        )
        _, reward, _, _, _ = await spawn_tool.do_step([spawn_action])
        assert reward == 1.0

        # Immediately check - should be running
        check_action = ActionModel(
            action_name='check_task',
            params={'task_id': 'bg_test'}
        )
        _, _, _, _, info1 = await spawn_tool.do_step([check_action])
        assert info1['status'] == 'running'

        # Wait for completion
        await asyncio.sleep(0.2)

        # Check again - should be completed
        _, _, _, _, info2 = await spawn_tool.do_step([check_action])
        assert info2['status'] == 'completed'

    @pytest.mark.asyncio
    async def test_error_propagation(self, spawn_tool):
        """Test that errors in background tasks are captured."""
        # Mock spawn to raise error
        async def error_spawn(name, directive, **kwargs):
            raise ValueError("Simulated error")

        spawn_tool.subagent_manager.spawn = AsyncMock(side_effect=error_spawn)

        # Spawn task
        spawn_action = ActionModel(
            action_name='spawn_background',
            params={'name': 'worker', 'directive': 'Failing task', 'task_id': 'error_test'}
        )
        await spawn_tool.do_step([spawn_action])

        # Wait for error
        await asyncio.sleep(0.1)

        # Check status
        check_action = ActionModel(
            action_name='check_task',
            params={'task_id': 'error_test'}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([check_action])

        assert info['status'] == 'error'
        assert reward == 0.0
        assert 'Simulated error' in obs.content


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
