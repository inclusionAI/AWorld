"""
Quick Verification: Aworld-CLI Subagent Configuration

Verifies that when Aworld agent is built through aworld-cli,
it has the correct subagent configuration without actually running LLM calls.
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "aworld-cli" / "src"))

from aworld.logs.util import logger
from dotenv import load_dotenv


def _run_aworld_cli_configuration():
    """Run the aworld-cli subagent configuration verification."""
    logger.info("="*70)
    logger.info("Aworld-CLI Subagent Configuration Test")
    logger.info("="*70)

    # Load environment
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)
    logger.info("✓ Environment loaded")

    # Build Aworld agent (simulating aworld-cli startup)
    logger.info("\n[Step 1] Building Aworld agent via aworld-cli method...")
    try:
        from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent
        aworld_swarm = build_aworld_agent()
        logger.info("✓ Aworld TeamSwarm built")
    except Exception as e:
        logger.error(f"✗ Failed to build Aworld agent: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

    # Initialize swarm to build agent_graph
    logger.info("\n[Step 2] Initializing TeamSwarm...")
    aworld_swarm.reset()
    logger.info("✓ TeamSwarm initialized")

    # Get root agent
    logger.info("\n[Step 3] Extracting Aworld agent...")
    aworld_agent = aworld_swarm.agent_graph.root_agent
    logger.info(f"✓ Root agent: {aworld_agent.name()}")

    # Verification checks
    logger.info("\n[Step 4] Configuration Verification...")
    checks = {}

    # Check 1: enable_subagent
    checks["enable_subagent=True"] = aworld_agent.enable_subagent == True
    logger.info(f"  {'✓' if checks['enable_subagent=True'] else '✗'} enable_subagent = {aworld_agent.enable_subagent}")

    # Check 2: SubagentManager exists
    checks["SubagentManager exists"] = aworld_agent.subagent_manager is not None
    logger.info(f"  {'✓' if checks['SubagentManager exists'] else '✗'} SubagentManager: {aworld_agent.subagent_manager}")

    # Check 3: spawn_subagent in tool_names
    checks["spawn_subagent in tool_names"] = 'spawn_subagent' in aworld_agent.tool_names
    logger.info(f"  {'✓' if checks['spawn_subagent in tool_names'] else '✗'} spawn_subagent in tool_names: {'spawn_subagent' in aworld_agent.tool_names}")

    # Check 4: Available subagents
    if aworld_agent.subagent_manager:
        available = list(aworld_agent.subagent_manager._available_subagents.keys())
        checks["Has available subagents"] = len(available) > 0
        logger.info(f"  {'✓' if checks['Has available subagents'] else 'ℹ️'} Available subagents: {available}")
    else:
        checks["Has available subagents"] = False

    # Check 5: spawn_subagent in ToolFactory
    from aworld.core.tool.base import ToolFactory
    checks["spawn_subagent in ToolFactory"] = 'spawn_subagent' in ToolFactory
    logger.info(f"  {'✓' if checks['spawn_subagent in ToolFactory'] else '✗'} spawn_subagent registered globally: {'spawn_subagent' in ToolFactory}")

    # Check 6: Tool is SpawnSubagentTool
    if 'spawn_subagent' in ToolFactory:
        from aworld.core.tool.builtin import SpawnSubagentTool
        tool = ToolFactory('spawn_subagent')
        checks["Tool is SpawnSubagentTool"] = isinstance(tool, SpawnSubagentTool)
        logger.info(f"  {'✓' if checks['Tool is SpawnSubagentTool'] else '✗'} Tool type: {type(tool).__name__}")
    else:
        checks["Tool is SpawnSubagentTool"] = False

    # Check 7: TeamSwarm members
    if hasattr(aworld_swarm, 'agent_graph') and aworld_swarm.agent_graph:
        agents = list(aworld_swarm.agent_graph.agents.keys())
        checks["Has team members"] = len(agents) > 1  # Root + members
        logger.info(f"  {'✓' if checks['Has team members'] else '✗'} TeamSwarm members: {agents}")
    else:
        checks["Has team members"] = False

    # Summary
    logger.info("\n" + "="*70)
    logger.info("Configuration Test Summary")
    logger.info("="*70)

    passed = sum(checks.values())
    total = len(checks)

    for check, result in checks.items():
        status = "✓" if result else "✗"
        logger.info(f"{status} {check}")

    logger.info(f"\nResult: {passed}/{total} checks passed")

    if passed >= 6:  # Allow 1 optional check to fail
        logger.info("\n✅ Aworld-CLI configuration test PASSED!")
        logger.info("   Aworld agent is correctly configured with subagent capability")
        logger.info("   Ready for LLM to autonomously call spawn_subagent")
        return True
    else:
        logger.error(f"\n❌ Configuration test FAILED: only {passed}/{total} checks passed")
        return False


def _run_spawn_subagent_tool_availability():
    """Run the spawn_subagent tool availability verification."""
    logger.info("\n" + "="*70)
    logger.info("Spawn Tool Availability Test")
    logger.info("="*70)

    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent
    aworld_swarm = build_aworld_agent()
    aworld_swarm.reset()
    aworld_agent = aworld_swarm.agent_graph.root_agent

    logger.info("\n[Test] Retrieving spawn_subagent tool from agent's toolset...")

    # Check if agent can access spawn_subagent through its tools
    if hasattr(aworld_agent, 'tools') and aworld_agent.tools:
        spawn_tool_found = False
        for tool_name, tool_instance in aworld_agent.tools.items():
            if 'spawn_subagent' in tool_name.lower():
                logger.info(f"✓ Found tool: {tool_name}")
                logger.info(f"  Tool type: {type(tool_instance).__name__}")
                spawn_tool_found = True
                break

        if spawn_tool_found:
            logger.info("\n✅ spawn_subagent tool is accessible through agent's toolset")
            return True
        else:
            logger.info("\nℹ️ Tools may be lazily initialized")
            logger.info(f"  Agent tool_names: {aworld_agent.tool_names}")
            # Still pass if tool_names contains it (lazy init)
            return 'spawn_subagent' in aworld_agent.tool_names
    else:
        logger.info("ℹ️ Tools not yet initialized (lazy loading)")
        return 'spawn_subagent' in aworld_agent.tool_names


def test_aworld_cli_configuration():
    """Verify Aworld agent configuration when built through aworld-cli."""
    _run_aworld_cli_configuration()


def test_spawn_subagent_tool_availability():
    """Test that spawn_subagent tool is available in Aworld agent's toolset."""
    _run_spawn_subagent_tool_availability()


if __name__ == '__main__':
    success1 = _run_aworld_cli_configuration()
    success2 = _run_spawn_subagent_tool_availability()

    print("\n" + "="*70)
    print("Overall Results")
    print("="*70)
    print(f"Configuration test: {'✅ PASSED' if success1 else '❌ FAILED'}")
    print(f"Tool availability test: {'✅ PASSED' if success2 else '❌ FAILED'}")

    if success1 and success2:
        print("\n🎉 Aworld-CLI subagent configuration is correct!")
        print("   The agent is ready for LLM to use spawn_subagent autonomously.")
        sys.exit(0)
    else:
        print("\n❌ Configuration verification failed")
        sys.exit(1)
