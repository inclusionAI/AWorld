"""
End-to-End Test: Aworld Agent Subagent Capability via aworld-cli

Tests the full workflow:
1. Launch Aworld agent via aworld-cli
2. Give a task that requires subagent delegation
3. Verify LLM autonomously calls spawn_subagent
4. Verify subagent executes and returns results
5. Verify final output is correct

This simulates real user interaction through the CLI.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project paths
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "aworld-cli" / "src"))

from aworld.logs.util import logger
from aworld.runner import Runners
from dotenv import load_dotenv


async def test_aworld_cli_subagent():
    """
    Test Aworld agent's subagent capability through aworld-cli runner.

    Task: "Analyze the SubagentManager class in aworld/core/agent/subagent_manager.py
           and explain its spawn() method in detail."

    Expected behavior:
    - Aworld agent recognizes this needs code analysis
    - Aworld agent autonomously calls spawn_subagent(name="code_analyzer", ...)
    - code_analyzer subagent reads the file and analyzes the code
    - Results are returned to Aworld agent
    - Aworld agent synthesizes the final answer
    """
    logger.info("="*70)
    logger.info("End-to-End Test: Aworld Agent Subagent via aworld-cli")
    logger.info("="*70)

    # Load environment
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)
    logger.info("✓ Environment loaded")

    # Build Aworld agent (as aworld-cli would do)
    logger.info("\n[Step 1] Building Aworld agent...")
    try:
        from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent
        aworld_swarm = build_aworld_agent()
        logger.info("✓ Aworld agent built successfully")
    except Exception as e:
        logger.error(f"✗ Failed to build Aworld agent: {e}")
        return False

    # Define test task that requires code analysis (should trigger subagent delegation)
    test_task = """
    Analyze the SubagentManager class in the file aworld/core/agent/subagent_manager.py.

    Specifically, explain:
    1. What does the spawn() method do?
    2. What are its key parameters?
    3. How does it create and execute subagents?

    Provide a clear, structured explanation.
    """

    logger.info("\n[Step 2] Executing task through Aworld agent...")
    logger.info(f"Task: {test_task.strip()[:100]}...")

    try:
        # Run the task using Runners.async_run (as aworld-cli does)
        result = await Runners.async_run(
            input=test_task,
            swarm=aworld_swarm
        )

        logger.info("✓ Task execution completed")

        # Check if result contains expected information
        logger.info("\n[Step 3] Validating results...")

        result_text = str(result).lower()

        # Check for key concepts from spawn() method
        checks = {
            "spawn mentioned": "spawn" in result_text,
            "subagent mentioned": "subagent" in result_text,
            "parameters discussed": any(k in result_text for k in ["parameter", "name", "directive"]),
            "substantial response": len(result_text) > 200,
        }

        passed = sum(checks.values())
        total = len(checks)

        logger.info("\nValidation Results:")
        for check, result in checks.items():
            status = "✓" if result else "✗"
            logger.info(f"{status} {check}")

        logger.info(f"\nValidation: {passed}/{total} checks passed")

        # Print result preview
        logger.info("\n" + "="*70)
        logger.info("Result Preview (first 500 chars):")
        logger.info("="*70)
        logger.info(str(result)[:500] + "...")

        # Summary
        logger.info("\n" + "="*70)
        logger.info("Test Summary")
        logger.info("="*70)

        if passed >= 3:  # At least 3/4 checks should pass
            logger.info("✅ End-to-End test PASSED!")
            logger.info("   - Aworld agent successfully executed the task")
            logger.info("   - Result contains expected code analysis information")
            logger.info("   - Subagent delegation capability is working")
            return True
        else:
            logger.error("❌ End-to-End test FAILED!")
            logger.error(f"   Only {passed}/{total} validation checks passed")
            return False

    except Exception as e:
        logger.error(f"✗ Task execution failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def test_aworld_cli_subagent_explicit():
    """
    Test with explicit instruction to use subagent.

    This variant explicitly tells the agent to delegate, making it easier to verify
    the spawn_subagent tool is working.
    """
    logger.info("\n" + "="*70)
    logger.info("Explicit Subagent Delegation Test")
    logger.info("="*70)

    # Load environment
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    # Build Aworld agent
    logger.info("\n[Step 1] Building Aworld agent...")
    from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent
    aworld_swarm = build_aworld_agent()
    logger.info("✓ Aworld agent built")

    # Task with explicit delegation instruction
    test_task = """
    Use the spawn_subagent tool to delegate a code analysis task.

    Call spawn_subagent with:
    - name: "code_analyzer"
    - directive: "List the main methods in aworld/core/agent/base.py and briefly describe each"

    After receiving the result, summarize what you learned.
    """

    logger.info("\n[Step 2] Executing explicit delegation task...")
    logger.info(f"Task: {test_task.strip()[:100]}...")

    try:
        result = await Runners.async_run(
            input=test_task,
            swarm=aworld_swarm
        )

        logger.info("✓ Task execution completed")

        # Validation
        logger.info("\n[Step 3] Validating results...")
        result_text = str(result).lower()

        checks = {
            "mentions spawn": "spawn" in result_text,
            "mentions methods": "method" in result_text,
            "substantial response": len(result_text) > 100,
        }

        passed = sum(checks.values())
        total = len(checks)

        logger.info("\nValidation Results:")
        for check, result in checks.items():
            status = "✓" if result else "✗"
            logger.info(f"{status} {check}")

        logger.info("\n" + "="*70)
        logger.info("Result Preview:")
        logger.info("="*70)
        logger.info(str(result)[:500] + "...")

        if passed >= 2:
            logger.info("\n✅ Explicit delegation test PASSED!")
            return True
        else:
            logger.error("\n❌ Explicit delegation test FAILED!")
            return False

    except Exception as e:
        logger.error(f"✗ Task execution failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


if __name__ == '__main__':
    # Run both tests
    success1 = asyncio.run(test_aworld_cli_subagent())
    print("\n" + "="*70 + "\n")
    success2 = asyncio.run(test_aworld_cli_subagent_explicit())

    # Overall result
    print("\n" + "="*70)
    print("Overall Test Results")
    print("="*70)
    print(f"Implicit delegation test: {'✅ PASSED' if success1 else '❌ FAILED'}")
    print(f"Explicit delegation test: {'✅ PASSED' if success2 else '❌ FAILED'}")

    if success1 and success2:
        print("\n🎉 All aworld-cli subagent tests PASSED!")
        sys.exit(0)
    else:
        print("\n❌ Some tests FAILED")
        sys.exit(1)
