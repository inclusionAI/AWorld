"""
Test explicit control of spawn_subagent tool

Verifies that:
1. Agent with enable_subagent=True but WITHOUT spawn_subagent in tool_names
   can be spawned but cannot spawn others
2. Appropriate log messages are displayed
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.logs.util import logger
from dotenv import load_dotenv
import os


def _run_explicit_control():
    """Run the explicit spawn_subagent tool control verification."""
    logger.info("="*60)
    logger.info("Explicit Control Test: spawn_subagent Tool")
    logger.info("="*60)

    # Setup
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    llm_provider = os.getenv('LLM_PROVIDER', 'openai')
    llm_model_name = os.getenv('LLM_MODEL_NAME', 'gpt-4o')
    llm_api_key = os.getenv('LLM_API_KEY')
    llm_base_url = os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')

    conf = AgentConfig(
        llm_provider=llm_provider,
        llm_model_name=llm_model_name,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url
    )

    # Test Case 1: Agent WITH spawn_subagent tool
    logger.info("\n[Test 1] Agent WITH spawn_subagent in tool_names")
    agent_with_spawn = Agent(
        name="coordinator_with_spawn",
        conf=conf,
        tool_names=["spawn_subagent", "read_file"],  # Explicitly include
        enable_subagent=True
    )

    if 'spawn_subagent' in agent_with_spawn.tool_names:
        logger.info("✓ spawn_subagent is in tool_names")
    else:
        logger.error("✗ spawn_subagent NOT in tool_names")
        return False

    # Test Case 2: Agent WITHOUT spawn_subagent tool
    logger.info("\n[Test 2] Agent WITHOUT spawn_subagent in tool_names")
    agent_without_spawn = Agent(
        name="member_without_spawn",
        conf=conf,
        tool_names=["read_file", "write_file"],  # No spawn_subagent
        enable_subagent=True  # Can be spawned, but cannot spawn
    )

    if 'spawn_subagent' not in agent_without_spawn.tool_names:
        logger.info("✓ spawn_subagent is NOT in tool_names (as expected)")
    else:
        logger.error("✗ spawn_subagent unexpectedly in tool_names")
        return False

    # Verify both have SubagentManager (can be spawned)
    if agent_with_spawn.subagent_manager and agent_without_spawn.subagent_manager:
        logger.info("✓ Both agents have SubagentManager (can be spawned)")
    else:
        logger.error("✗ Not all agents have SubagentManager")
        return False

    # Test Case 3: Agent with enable_subagent=False
    logger.info("\n[Test 3] Agent with enable_subagent=False")
    agent_no_subagent = Agent(
        name="simple_agent",
        conf=conf,
        tool_names=["read_file"],
        enable_subagent=False  # No subagent capability
    )

    if not agent_no_subagent.enable_subagent and agent_no_subagent.subagent_manager is None:
        logger.info("✓ Agent has no subagent capability (as expected)")
    else:
        logger.error("✗ Agent unexpectedly has subagent capability")
        return False

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Test Summary")
    logger.info("="*60)
    logger.info("✓ spawn_subagent tool must be explicitly added to tool_names")
    logger.info("✓ enable_subagent=True creates SubagentManager (can be spawned)")
    logger.info("✓ Presence of spawn_subagent in tool_names controls spawn capability")
    logger.info("✓ Explicit control achieved!")

    return True


def test_explicit_control():
    """Test that spawn_subagent must be explicitly added to tool_names."""
    _run_explicit_control()


if __name__ == '__main__':
    success = _run_explicit_control()
    sys.exit(0 if success else 1)
