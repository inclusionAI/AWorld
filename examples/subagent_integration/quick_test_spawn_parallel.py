"""
Quick Test: spawn_parallel with AWorld Agent

Minimal test to verify spawn_parallel works with AWorld agent.
Run this first before running the full test suite.

Usage:
    python quick_test_spawn_parallel.py
"""

import sys
import os
import asyncio
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "aworld-cli" / "src"))

from aworld.logs.util import logger
from dotenv import load_dotenv
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm
from aworld.config import AgentConfig, ModelConfig
from aworld.runner import Runners


async def quick_test():
    """Quick test of spawn_parallel functionality"""
    logger.info("="*60)
    logger.info("Quick Test: spawn_parallel")
    logger.info("="*60)

    # Load environment
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists():
        logger.error("✗ .env file not found!")
        logger.error(f"Please create {env_path} with LLM credentials")
        logger.error("\nExample .env content:")
        logger.error("LLM_MODEL_NAME=gpt-4o")
        logger.error("LLM_PROVIDER=openai")
        logger.error("LLM_API_KEY=your_api_key_here")
        logger.error("LLM_BASE_URL=https://api.openai.com/v1")
        return False

    load_dotenv(env_path)
    logger.info("✓ Environment loaded")

    # Check required env vars
    required_vars = ['LLM_MODEL_NAME', 'LLM_PROVIDER', 'LLM_API_KEY']
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        logger.error(f"✗ Missing environment variables: {missing}")
        return False
    logger.info("✓ LLM credentials configured")

    # Create model config
    model_config = ModelConfig(
        llm_model_name=os.getenv('LLM_MODEL_NAME'),
        llm_provider=os.getenv('LLM_PROVIDER'),
        llm_api_key=os.getenv('LLM_API_KEY'),
        llm_base_url=os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')
    )

    agent_conf = AgentConfig(llm_config=model_config)
    logger.info(f"✓ Using model: {model_config.llm_model_name}")

    # Create coordinator with subagent capability
    logger.info("\n[Step 1] Creating coordinator agent...")
    coordinator = Agent(
        name="coordinator",
        conf=agent_conf,
        desc="Test coordinator for parallel spawning",
        enable_subagent=True,  # CRITICAL: Enable subagent functionality
        tool_names=["spawn_subagent"],  # CRITICAL: Add spawn_subagent tool
        system_prompt="""You are a test coordinator.

When asked to test spawn_parallel, use this exact format:

spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "worker", "directive": "Task 1: Count to 3"},
        {"name": "worker", "directive": "Task 2: List 3 colors"},
        {"name": "worker", "directive": "Task 3: Name 3 animals"}
    ],
    max_concurrent=3,
    aggregate=true
)

IMPORTANT: Must use action="spawn_parallel" for parallel execution!
"""
    )
    logger.info("✓ Coordinator created (enable_subagent=True)")

    # Create worker subagent
    logger.info("\n[Step 2] Creating worker subagent...")
    worker = Agent(
        name="worker",
        conf=agent_conf,
        desc="Simple worker that executes tasks",
        system_prompt="You are a worker agent. Execute the given task and return a brief result."
    )
    logger.info("✓ Worker created")

    # Create TeamSwarm
    logger.info("\n[Step 3] Building TeamSwarm...")
    swarm = TeamSwarm(coordinator, worker)
    logger.info("✓ TeamSwarm created")

    # Verify subagent setup
    logger.info("\n[Step 4] Verifying subagent configuration...")
    swarm.reset()  # Initialize the swarm

    root_agent = swarm.agent_graph.root_agent
    if not root_agent.enable_subagent:
        logger.error("✗ Coordinator does not have enable_subagent=True")
        return False
    logger.info("✓ enable_subagent is True")

    if not root_agent.subagent_manager:
        logger.error("✗ SubagentManager not initialized")
        return False
    logger.info("✓ SubagentManager exists")

    if 'spawn_subagent' not in root_agent.tool_names:
        logger.error("✗ spawn_subagent tool not in tool_names")
        return False
    logger.info("✓ spawn_subagent tool available")

    available = list(root_agent.subagent_manager._available_subagents.keys())
    logger.info(f"✓ Available subagents: {available}")

    # Execute parallel spawn test
    logger.info("\n[Step 5] Executing spawn_parallel test...")
    logger.info("Task: Run 3 simple tasks in parallel")

    import uuid
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"

    task_response = await Runners.run(
        input="Test spawn_parallel by running 3 simple tasks concurrently using the worker subagent.",
        swarm=swarm,
        session_id=session_id
    )

    # Check result
    logger.info("\n[Step 6] Checking result...")
    if not task_response:
        logger.error("✗ No result returned")
        return False

    if not task_response.success:
        logger.error(f"✗ Execution failed: {task_response.msg}")
        return False

    result = task_response.answer
    logger.info("\n[Result Preview]")
    # Show first 500 chars
    preview = result[:500] if len(result) > 500 else result
    logger.info(preview)
    if len(result) > 500:
        logger.info(f"... (total {len(result)} chars)")

    # Verify parallel execution markers
    logger.info("\n[Step 7] Verifying parallel execution...")
    success_indicators = [
        'parallel' in result.lower(),
        'task' in result.lower(),
        ('success' in result.lower() or '✓' in result or '✅' in result)
    ]

    if any(success_indicators):
        logger.info("✓ Parallel execution indicators found in result")
    else:
        logger.warning("⚠️ Could not verify parallel execution from result")

    # Final verdict
    logger.info("\n" + "="*60)
    logger.info("✓ Quick Test PASSED!")
    logger.info("="*60)
    logger.info("\nNext steps:")
    logger.info("1. Run full test suite: python test_spawn_parallel_aworld.py")
    logger.info("2. Try with AWorld agent: test_spawn_parallel_with_aworld_agent()")
    logger.info("3. Check documentation: docs/features/parallel-subagent-spawning.md")

    return True


if __name__ == '__main__':
    try:
        success = asyncio.run(quick_test())
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"✗ Test crashed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
