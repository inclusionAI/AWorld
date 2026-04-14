# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Test suite for parallel subagent spawning functionality.

Tests the spawn_parallel action of SpawnSubagentTool.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aworld.config.conf import ConfigDict
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message
from aworld.core.tool.builtin.spawn_subagent_tool import SpawnSubagentTool
from aworld.core.common import ActionModel, Observation
from aworld.core.agent.subagent_manager import SubagentManager


@pytest.fixture
def mock_subagent_manager():
    """Create a mock SubagentManager for testing"""
    manager = MagicMock(spec=SubagentManager)

    # Mock spawn method to return success
    async def mock_spawn(name, directive, **kwargs):
        # Simulate processing time
        await asyncio.sleep(0.1)
        return f"[{name}] Processed: {directive[:50]}..."

    manager.spawn = AsyncMock(side_effect=mock_spawn)
    return manager


@pytest.fixture
def spawn_tool(mock_subagent_manager):
    """Create SpawnSubagentTool instance with mock manager"""
    tool = SpawnSubagentTool(subagent_manager=mock_subagent_manager)
    return tool


@pytest.mark.asyncio
async def test_spawn_parallel_basic(spawn_tool):
    """Test basic parallel spawning with multiple tasks"""
    # Prepare action
    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': 'analyzer1', 'directive': 'Analyze data set A'},
                {'name': 'analyzer2', 'directive': 'Analyze data set B'},
                {'name': 'reporter', 'directive': 'Generate summary report'}
            ],
            'max_concurrent': 3,
            'aggregate': True
        }
    )

    # Execute
    observation, reward, terminated, truncated, info = await spawn_tool.do_step([action])

    # Assertions
    assert isinstance(observation, Observation)
    assert reward == 1.0  # All tasks succeeded
    assert terminated is False
    assert truncated is False
    assert info['action'] == 'spawn_parallel'
    assert info['total_tasks'] == 3
    assert info['success_count'] == 3
    assert info['failed_count'] == 0
    assert 'elapsed_seconds' in info

    # Check result format
    result_content = observation.content
    assert 'Parallel Subagent Execution Results' in result_content
    assert 'analyzer1' in result_content
    assert 'analyzer2' in result_content
    assert 'reporter' in result_content


@pytest.mark.asyncio
async def test_spawn_parallel_structured_output(spawn_tool):
    """Test parallel spawning with structured JSON output"""
    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': 'task1', 'directive': 'Do task 1'},
                {'name': 'task2', 'directive': 'Do task 2'}
            ],
            'aggregate': False  # Request structured output
        }
    )

    observation, reward, _, _, info = await spawn_tool.do_step([action])

    # Check JSON format
    import json
    result_json = json.loads(observation.content)

    assert 'summary' in result_json
    assert result_json['summary']['total_tasks'] == 2
    assert result_json['summary']['success_count'] == 2
    assert 'tasks' in result_json
    assert len(result_json['tasks']) == 2


@pytest.mark.asyncio
async def test_spawn_parallel_with_failure(mock_subagent_manager):
    """Test parallel spawning when some tasks fail"""
    # Mock spawn to fail for specific task
    async def mock_spawn_with_failure(name, directive, **kwargs):
        if name == 'faulty_agent':
            raise ValueError("Simulated failure")
        await asyncio.sleep(0.1)
        return f"[{name}] Success"

    mock_subagent_manager.spawn = AsyncMock(side_effect=mock_spawn_with_failure)
    tool = SpawnSubagentTool(subagent_manager=mock_subagent_manager)

    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': 'good_agent', 'directive': 'Do good work'},
                {'name': 'faulty_agent', 'directive': 'This will fail'},
                {'name': 'another_good', 'directive': 'Also succeeds'}
            ],
            'fail_fast': False
        }
    )

    observation, reward, _, _, info = await tool.do_step([action])

    # Should complete all tasks despite failure
    assert info['total_tasks'] == 3
    assert info['success_count'] == 2
    assert info['failed_count'] == 1
    assert 0 < reward < 1.0  # Partial success reward
    assert 'faulty_agent' in observation.content
    assert 'Simulated failure' in observation.content


@pytest.mark.asyncio
async def test_spawn_parallel_fail_fast(mock_subagent_manager):
    """Test fail_fast mode stops on first failure"""
    call_count = 0

    async def mock_spawn_fail_fast(name, directive, **kwargs):
        nonlocal call_count
        call_count += 1

        if name == 'fail_agent':
            raise ValueError("First failure")

        await asyncio.sleep(0.5)  # Slow tasks
        return f"[{name}] Success"

    mock_subagent_manager.spawn = AsyncMock(side_effect=mock_spawn_fail_fast)
    tool = SpawnSubagentTool(subagent_manager=mock_subagent_manager)

    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': 'fail_agent', 'directive': 'This fails immediately'},
                {'name': 'slow_agent1', 'directive': 'Slow task 1'},
                {'name': 'slow_agent2', 'directive': 'Slow task 2'},
                {'name': 'slow_agent3', 'directive': 'Slow task 3'}
            ],
            'fail_fast': True
        }
    )

    observation, reward, _, _, info = await tool.do_step([action])

    # Should stop after first failure
    # Note: Some tasks might have started before cancellation
    assert info['failed_count'] >= 1
    assert reward < 1.0


@pytest.mark.asyncio
async def test_spawn_parallel_concurrency_limit(mock_subagent_manager):
    """Test max_concurrent limits parallel execution"""
    active_tasks = 0
    max_active = 0
    lock = asyncio.Lock()

    async def mock_spawn_concurrent(name, directive, **kwargs):
        nonlocal active_tasks, max_active

        async with lock:
            active_tasks += 1
            max_active = max(max_active, active_tasks)

        await asyncio.sleep(0.1)

        async with lock:
            active_tasks -= 1

        return f"[{name}] Done"

    mock_subagent_manager.spawn = AsyncMock(side_effect=mock_spawn_concurrent)
    tool = SpawnSubagentTool(subagent_manager=mock_subagent_manager)

    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': f'task_{i}', 'directive': f'Task {i}'}
                for i in range(10)
            ],
            'max_concurrent': 3
        }
    )

    await tool.do_step([action])

    # Verify max concurrent was respected
    assert max_active <= 3


@pytest.mark.asyncio
async def test_spawn_parallel_resolves_manager_from_context_agent():
    """Tool should resolve the caller agent's SubagentManager from context."""
    manager = MagicMock(spec=SubagentManager)
    manager.spawn = AsyncMock(return_value="[image_generator] ok")

    caller_agent = MagicMock()
    caller_agent.subagent_manager = manager

    swarm = MagicMock()
    swarm.agents = {"root-agent": caller_agent}

    context = Context()
    task = MagicMock()
    task.swarm = swarm
    context.set_task(task)
    context.agent_info.current_agent_id = "root-agent"

    tool = SpawnSubagentTool(conf=ConfigDict({}))
    tool.context = context

    action = ActionModel(
        agent_name="root-agent",
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': 'image_generator', 'directive': 'Generate one image'}
            ],
            'aggregate': True,
        }
    )

    observation, reward, _, _, info = await tool.do_step([action])

    assert reward == 1.0
    assert info['success_count'] == 1
    assert 'image_generator' in observation.content
    manager.spawn.assert_awaited_once()
    assert manager.spawn.await_args.kwargs['context'] is context


@pytest.mark.asyncio
async def test_spawn_parallel_resolves_manager_from_object_agent_info_without_action_agent_name():
    """Tool should support object-like agent_info, not only dict-like access."""
    manager = MagicMock(spec=SubagentManager)
    manager.spawn = AsyncMock(return_value="[image_generator] ok")

    caller_agent = MagicMock()
    caller_agent.subagent_manager = manager

    swarm = MagicMock()
    swarm.agents = {"root-agent": caller_agent}

    context = Context()
    task = MagicMock()
    task.swarm = swarm
    context.set_task(task)
    context.agent_info.current_agent_id = "root-agent"

    tool = SpawnSubagentTool(conf=ConfigDict({}))
    tool.context = context

    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': 'image_generator', 'directive': 'Generate one image'}
            ],
            'aggregate': True,
        }
    )

    observation, reward, _, _, info = await tool.do_step([action])

    assert reward == 1.0
    assert info['success_count'] == 1
    assert 'image_generator' in observation.content
    manager.spawn.assert_awaited_once()
    assert manager.spawn.await_args.kwargs['context'] is context


@pytest.mark.asyncio
async def test_spawn_parallel_step_returns_error_without_secondary_index_error():
    """Error responses should not crash AsyncTool.post_step with empty action_result."""
    swarm = MagicMock()
    caller_agent = MagicMock(feedback_tool_result=True)
    caller_agent.subagent_manager = None
    swarm.agents = {"root-agent": caller_agent}

    context = Context()
    task = MagicMock()
    task.swarm = swarm
    context.set_task(task)

    tool = SpawnSubagentTool(conf=ConfigDict({}))
    message = Message(
        category=Constants.TOOL,
        payload=[
            ActionModel(
                tool_name='async_spawn_subagent',
                tool_call_id='call-1',
                agent_name='root-agent',
                action_name='spawn_parallel',
                params={'tasks': [{'name': 'image_generator', 'directive': 'Generate one image'}]},
            )
        ],
        headers={'context': context},
    )

    with patch("aworld.core.tool.base.send_message", AsyncMock()), patch(
        "aworld.core.tool.base.send_message_with_future",
        AsyncMock(side_effect=RuntimeError("skip callback in unit test"))
    ):
        result = await tool.step(message)

    assert result.payload[0].content == "spawn_parallel: No SubagentManager available"
    assert result.payload[0].action_result[0].error == "No SubagentManager"


@pytest.mark.asyncio
async def test_spawn_parallel_empty_tasks():
    """Test error handling for empty tasks array"""
    tool = SpawnSubagentTool()

    action = ActionModel(
        action_name='spawn_parallel',
        params={'tasks': []}
    )

    observation, reward, _, _, info = await tool.do_step([action])

    assert reward == 0.0
    assert 'error' in info
    assert 'empty' in observation.content.lower()


@pytest.mark.asyncio
async def test_spawn_parallel_invalid_task_format(spawn_tool):
    """Test handling of tasks with missing required fields"""
    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {'name': 'valid', 'directive': 'Valid task'},
                {'name': 'missing_directive'},  # Missing directive
                {'directive': 'Missing name'},  # Missing name
            ]
        }
    )

    observation, reward, _, _, info = await spawn_tool.do_step([action])

    # Should complete valid task, report errors for invalid ones
    assert info['success_count'] == 1
    assert info['failed_count'] == 2
    assert 0 < reward < 1.0


@pytest.mark.asyncio
async def test_spawn_parallel_with_disallowed_tools(mock_subagent_manager):
    """Test that disallowedTools parameter is passed correctly"""
    spawn_kwargs_capture = {}

    async def mock_spawn_capture(name, directive, **kwargs):
        spawn_kwargs_capture[name] = kwargs
        return f"[{name}] Done"

    mock_subagent_manager.spawn = AsyncMock(side_effect=mock_spawn_capture)
    tool = SpawnSubagentTool(subagent_manager=mock_subagent_manager)

    action = ActionModel(
        action_name='spawn_parallel',
        params={
            'tasks': [
                {
                    'name': 'restricted_agent',
                    'directive': 'Do work with restrictions',
                    'disallowedTools': 'terminal,write_file'
                }
            ]
        }
    )

    await tool.do_step([action])

    # Verify disallowedTools was passed
    assert 'restricted_agent' in spawn_kwargs_capture
    assert 'disallowedTools' in spawn_kwargs_capture['restricted_agent']
    assert spawn_kwargs_capture['restricted_agent']['disallowedTools'] == ['terminal', 'write_file']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
