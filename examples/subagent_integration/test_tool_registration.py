"""
Verify spawn_subagent tool is registered and accessible
"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agents.coordinator import create_coordinator
from aworld.core.tool.base import ToolFactory
from aworld.core.tool.builtin import SpawnSubagentTool  # Import to trigger @ToolFactory.register
from aworld.logs.util import logger
from dotenv import load_dotenv


async def test_tool_registration():
    """Test that spawn_subagent tool is properly registered in ToolFactory"""
    logger.info("="*60)
    logger.info("Spawn Subagent Tool Registration Test")
    logger.info("="*60)

    # Setup
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    # Create coordinator
    logger.info("\n[Step 1] Creating coordinator...")
    coordinator = create_coordinator()

    if not coordinator.enable_subagent:
        logger.error("✗ Subagent capability not enabled")
        return False

    logger.info(f"✓ Coordinator created: {coordinator.name()}")

    # Check if spawn_subagent is in tool_names
    logger.info("\n[Step 2] Checking tool_names...")
    if 'spawn_subagent' in coordinator.tool_names:
        logger.info("✓ spawn_subagent in coordinator.tool_names")
    else:
        logger.error("✗ spawn_subagent NOT in coordinator.tool_names")
        logger.info(f"  Available tools: {coordinator.tool_names}")
        return False

    # Check if spawn_subagent is registered in ToolFactory
    logger.info("\n[Step 3] Checking ToolFactory registration...")
    if 'spawn_subagent' in ToolFactory:
        logger.info("✓ spawn_subagent registered in ToolFactory")

        # Get the tool instance
        tool = ToolFactory('spawn_subagent')
        logger.info(f"  Tool type: {type(tool).__name__}")
        logger.info(f"  Tool class: {tool.__class__.__module__}.{tool.__class__.__name__}")

        # Check if it's SpawnSubagentTool
        from aworld.core.tool.builtin import SpawnSubagentTool
        if isinstance(tool, SpawnSubagentTool):
            logger.info("✓ Tool is SpawnSubagentTool instance")
        else:
            logger.warning(f"⚠ Tool is {type(tool)}, expected SpawnSubagentTool")

        # Check tool configuration
        if hasattr(tool, 'conf'):
            logger.info(f"  Tool conf keys: {list(tool.conf.keys())}")
            if 'description' in tool.conf:
                logger.info(f"  Description: {tool.conf['description'][:80]}...")
            if 'parameters' in tool.conf:
                params = tool.conf['parameters']
                if 'properties' in params:
                    logger.info(f"  Parameters: {list(params['properties'].keys())}")

    else:
        logger.error("✗ spawn_subagent NOT registered in ToolFactory")
        # ToolFactory uses __iter__ not keys()
        registered_tools = list(ToolFactory)[:10]
        logger.info(f"  Registered tools: {registered_tools}...")
        return False

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Registration Test Summary")
    logger.info("="*60)

    checks = {
        "Coordinator created": coordinator is not None,
        "Subagent capability enabled": coordinator.enable_subagent,
        "spawn_subagent in tool_names": 'spawn_subagent' in coordinator.tool_names,
        "spawn_subagent in ToolFactory": 'spawn_subagent' in ToolFactory,
        "Tool is SpawnSubagentTool": isinstance(ToolFactory('spawn_subagent'), SpawnSubagentTool)
    }

    passed = sum(checks.values())
    total = len(checks)

    for check, result in checks.items():
        status = "✓" if result else "✗"
        logger.info(f"{status} {check}")

    logger.info(f"\nResult: {passed}/{total} checks passed")

    if passed == total:
        logger.info("✅ spawn_subagent tool is fully registered and ready for LLM use!")
        return True
    else:
        logger.error("❌ Some registration checks failed")
        return False


if __name__ == '__main__':
    success = asyncio.run(test_tool_registration())
    sys.exit(0 if success else 1)
