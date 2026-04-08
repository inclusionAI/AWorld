# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Integration test for background subagent execution.

Demonstrates a realistic Orchestrator scenario where background tasks
enable non-blocking parallel execution.
"""

import asyncio
import time
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm
from aworld.runner import Runners
from aworld.logs.util import logger


async def test_orchestrator_with_background_execution():
    """
    Test Orchestrator using background execution for parallel work.

    Simplified test that verifies the background task management API
    without needing full agent execution.
    """
    print("\n" + "="*80)
    print("Integration Test: Background Task Management API")
    print("="*80)

    from aworld.core.tool.builtin.spawn_subagent_tool import SpawnSubagentTool
    from aworld.core.common import ActionModel
    from unittest.mock import AsyncMock, MagicMock

    # Create mock SubagentManager
    mock_manager = MagicMock()
    mock_manager.agent.name.return_value = "Orchestrator"

    # Simulate slow research tasks
    async def mock_spawn(name, directive, **kwargs):
        """Simulate async research work."""
        await asyncio.sleep(0.5)  # Each task takes 500ms
        return f"Research result from {name}: {directive}"

    mock_manager.spawn = AsyncMock(side_effect=mock_spawn)

    # Create SpawnSubagentTool
    spawn_tool = SpawnSubagentTool(
        subagent_manager=mock_manager,
        conf=ConfigDict({})
    )

    print("\n📊 Test Setup:")
    print(f"  - Simulated orchestrator with 3 research tasks")
    print(f"  - Each task takes ~500ms")
    print(f"  - Sequential: ~1500ms, Parallel: ~500ms + overhead")

    # Test: Background execution (parallel)
    print("\n⚡ Background Execution Test:")
    start_bg = time.time()

    # Spawn 3 tasks in background
    task_ids = []
    print("  📤 Spawning background tasks...")
    for idx in range(3):
        task_id = f"research_task_{idx}"
        action = ActionModel(
            action_name='spawn_background',
            params={
                'name': f'Researcher_{idx}',
                'directive': f'Research topic {idx}',
                'task_id': task_id
            }
        )
        obs, reward, _, _, info = await spawn_tool.do_step([action])
        assert reward == 1.0, f"Failed to spawn {task_id}"
        task_ids.append(task_id)
        print(f"    ✓ {task_id} spawned")

    spawn_time = time.time() - start_bg
    print(f"  🚀 All tasks spawned in {spawn_time:.3f}s (non-blocking!)")

    # Verify all tasks started immediately (< 100ms)
    assert spawn_time < 0.1, "Background spawning should be instant"

    # Simulate orchestrator doing other work
    print("  💼 Orchestrator doing other work while research runs...")
    await asyncio.sleep(0.1)  # Simulate 100ms of other work
    print("    ✓ Other work completed (100ms)")

    # Check status while running
    print("  🔍 Checking task status...")
    check_action = ActionModel(
        action_name='check_task',
        params={'task_id': 'all', 'include_result': False}
    )
    obs, reward, _, _, info = await spawn_tool.do_step([check_action])
    print(f"    ✓ Status check completed: {info['total_tasks']} tasks")

    # Wait for all tasks
    print("  ⏳ Waiting for all research tasks to complete...")
    wait_action = ActionModel(
        action_name='wait_task',
        params={'task_ids': ','.join(task_ids), 'timeout': 10}
    )
    obs, reward, _, _, info = await spawn_tool.do_step([wait_action])
    assert reward == 1.0, "Not all tasks completed"
    assert info['completed'] == 3
    assert info['pending'] == 0

    total_time = time.time() - start_bg
    print(f"  ✅ All tasks completed in {total_time:.2f}s")

    # Verify parallelism
    print("\n📈 Performance Analysis:")
    sequential_time = 3 * 0.5  # 3 tasks × 500ms each
    print(f"  - Expected sequential: {sequential_time:.2f}s")
    print(f"  - Actual parallel:     {total_time:.2f}s")
    speedup = sequential_time / total_time
    print(f"  - Speedup:             {speedup:.2f}x")

    assert total_time < 1.0, "Background execution should be much faster than sequential"
    print(f"  ✅ Verified: {speedup:.1f}x faster than sequential!")

    # Check individual results
    print("\n📋 Individual Task Results:")
    for task_id in task_ids:
        check_action = ActionModel(
            action_name='check_task',
            params={'task_id': task_id, 'include_result': True}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([check_action])
        assert reward == 1.0
        assert info['status'] == 'completed'
        print(f"  ✓ {task_id}: completed ({info['elapsed']:.2f}s)")

    print("\n✅ All integration tests passed!")
    print("="*80)


async def test_mixed_foreground_background():
    """
    Test mixing foreground (blocking) and background (non-blocking) spawns.
    """
    print("\n" + "="*80)
    print("Integration Test: Mixed Foreground/Background Execution")
    print("="*80)

    from aworld.core.tool.builtin.spawn_subagent_tool import SpawnSubagentTool
    from aworld.core.common import ActionModel
    from unittest.mock import AsyncMock, MagicMock

    # Create mock SubagentManager
    mock_manager = MagicMock()
    mock_manager.agent.name.return_value = "Orchestrator"

    # Mock different speed tasks
    async def mock_spawn(name, directive, **kwargs):
        if 'Agent_A' in name:
            await asyncio.sleep(0.8)  # Slow task
            return "Result A (slow)"
        else:
            await asyncio.sleep(0.3)  # Fast task
            return "Result B (fast)"

    mock_manager.spawn = AsyncMock(side_effect=mock_spawn)

    spawn_tool = SpawnSubagentTool(
        subagent_manager=mock_manager,
        conf=ConfigDict({})
    )

    print("\n🔀 Execution Flow:")
    start = time.time()

    # 1. Spawn Task A in background (non-blocking, 800ms)
    print("  1. Spawning Task A in background (800ms)...")
    bg_action = ActionModel(
        action_name='spawn_background',
        params={'name': 'Agent_A', 'directive': 'Long task A', 'task_id': 'task_a'}
    )
    obs, reward, _, _, info = await spawn_tool.do_step([bg_action])
    assert reward == 1.0
    spawn_time = time.time() - start
    print(f"    ✓ Task A spawned ({spawn_time*1000:.0f}ms, non-blocking)")

    # 2. Spawn Task B in foreground (blocks, 300ms)
    print("  2. Spawning Task B in foreground (300ms, blocking)...")
    fg_start = time.time()
    fg_action = ActionModel(
        action_name='spawn',
        params={'name': 'Agent_B', 'directive': 'Quick task B'}
    )
    obs, reward, _, _, info = await spawn_tool.do_step([fg_action])
    assert reward == 1.0
    fg_time = time.time() - fg_start
    print(f"    ✓ Task B completed ({fg_time*1000:.0f}ms, blocked)")

    # 3. Check Task A status
    print("  3. Checking Task A status...")
    check_action = ActionModel(
        action_name='check_task',
        params={'task_id': 'task_a', 'include_result': False}
    )
    obs, reward, _, _, info = await spawn_tool.do_step([check_action])
    print(f"    ✓ Task A status: {info['status']} ({info['elapsed']:.2f}s elapsed)")

    # 4. Wait for Task A if needed
    if info['status'] == 'running':
        print("  4. Waiting for Task A to complete...")
        wait_action = ActionModel(
            action_name='wait_task',
            params={'task_ids': 'task_a', 'timeout': 5}
        )
        obs, reward, _, _, info = await spawn_tool.do_step([wait_action])
        assert reward == 1.0
        print("    ✓ Task A completed")

    total_time = time.time() - start
    print(f"\n📊 Results:")
    print(f"  - Total execution time: {total_time:.2f}s")
    print(f"  - Expected sequential: ~1.1s (800ms + 300ms)")
    print(f"  - Actual (with overlap): {total_time:.2f}s")

    # Verify parallelism
    assert total_time < 1.0, "Should be faster than sequential"
    print("  ✅ Verified: Tasks ran in parallel!")

    print("="*80)


if __name__ == '__main__':
    print("Running Background Subagent Integration Tests\n")

    # Run tests
    asyncio.run(test_orchestrator_with_background_execution())
    asyncio.run(test_mixed_foreground_background())

    print("\n🎉 All integration tests passed successfully!")
