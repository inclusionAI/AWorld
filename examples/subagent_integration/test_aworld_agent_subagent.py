"""
Verify that Aworld agent has subagent capability enabled by default
"""

import sys
import os
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "aworld-cli" / "src"))

from aworld.logs.util import logger
from dotenv import load_dotenv


def _run_aworld_agent_subagent():
    """Run the Aworld agent subagent capability verification."""
    logger.info("="*60)
    logger.info("Aworld Agent Subagent Capability Test")
    logger.info("="*60)

    # Load environment
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)
    logger.info("✓ Environment loaded")

    # Build Aworld agent
    logger.info("\n[Step 1] Building Aworld agent...")
    try:
        from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent
        aworld_swarm = build_aworld_agent()
        logger.info("✓ Aworld agent built successfully")
    except Exception as e:
        logger.error(f"✗ Failed to build Aworld agent: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

    # Initialize the TeamSwarm to build agent_graph
    logger.info("\n[Step 2] Initializing TeamSwarm...")
    aworld_swarm.reset()  # This builds the agent_graph

    # Get the root agent from TeamSwarm
    logger.info("\n[Step 3] Extracting root agent from TeamSwarm...")
    if hasattr(aworld_swarm, 'agent_graph') and aworld_swarm.agent_graph:
        aworld_agent = aworld_swarm.agent_graph.root_agent
    else:
        logger.error("✗ TeamSwarm has no agent_graph or root_agent")
        return False

    if not aworld_agent:
        logger.error("✗ Failed to get root agent from TeamSwarm")
        return False

    logger.info(f"✓ Root agent: {aworld_agent.name()}")

    # Check enable_subagent
    logger.info("\n[Step 4] Checking enable_subagent...")
    if aworld_agent.enable_subagent:
        logger.info("✓ enable_subagent is True")
    else:
        logger.error("✗ enable_subagent is False (expected True)")
        return False

    # Check SubagentManager
    logger.info("\n[Step 5] Checking SubagentManager...")
    if aworld_agent.subagent_manager:
        logger.info("✓ SubagentManager exists")
    else:
        logger.error("✗ SubagentManager is None")
        return False

    # Check spawn_subagent in tool_names
    logger.info("\n[Step 6] Checking spawn_subagent tool...")
    if 'spawn_subagent' in aworld_agent.tool_names:
        logger.info("✓ spawn_subagent in tool_names")
    else:
        logger.error("✗ spawn_subagent NOT in tool_names")
        return False

    # Check available subagents
    logger.info("\n[Step 7] Checking available subagents...")
    if aworld_agent.subagent_manager._available_subagents:
        available = list(aworld_agent.subagent_manager._available_subagents.keys())
        logger.info(f"✓ Available subagents: {available}")
    else:
        logger.info("ℹ️ No subagents registered yet (will be registered when TeamSwarm members are added)")

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Test Summary")
    logger.info("="*60)
    logger.info("✓ Aworld agent has enable_subagent=True by default")
    logger.info("✓ SubagentManager is created automatically")
    logger.info("✓ spawn_subagent tool is available")
    logger.info("✓ Aworld agent is ready for dynamic subagent delegation!")

    return True


def test_aworld_agent_subagent():
    """Test that Aworld agent has enable_subagent=True by default."""
    _run_aworld_agent_subagent()


if __name__ == '__main__':
    success = _run_aworld_agent_subagent()
    sys.exit(0 if success else 1)
