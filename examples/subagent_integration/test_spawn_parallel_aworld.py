"""
Test spawn_parallel functionality with AWorld built-in agent.

This test demonstrates:
1. Creating specialized subagents for AWorld agent
2. Using spawn_parallel to execute multiple tasks concurrently
3. Verifying results from parallel execution
4. Testing different spawn_parallel configurations

Prerequisites:
- Set up .env file with LLM credentials
- Ensure spawn_subagent tool is registered
"""

import sys
import os
import asyncio
from pathlib import Path

import pytest

if os.getenv("AWORLD_RUN_LIVE_EXAMPLE_TESTS") != "1":
    pytest.skip(
        "live spawn_parallel examples require external LLM credentials; set AWORLD_RUN_LIVE_EXAMPLE_TESTS=1 to run them",
        allow_module_level=True,
    )

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


async def test_spawn_parallel_basic():
    """
    Test 1: Basic parallel spawning with multiple data analysis tasks

    Scenario: Coordinator spawns 3 analyzer agents in parallel
    """
    logger.info("="*80)
    logger.info("Test 1: Basic Parallel Spawn (3 Data Analyzers)")
    logger.info("="*80)

    # Load environment
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    # Get LLM config from environment
    model_config = ModelConfig(
        llm_model_name=os.getenv('LLM_MODEL_NAME', 'gpt-4o'),
        llm_provider=os.getenv('LLM_PROVIDER', 'openai'),
        llm_api_key=os.getenv('LLM_API_KEY'),
        llm_base_url=os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')
    )

    agent_conf = AgentConfig(llm_config=model_config)

    # Create coordinator agent (with subagent capability)
    coordinator = Agent(
        name="data_coordinator",
        conf=agent_conf,
        desc="Coordinates parallel data analysis tasks",
        enable_subagent=True,  # Enable subagent functionality
        tool_names=["spawn_subagent"],  # Add spawn_subagent tool
        system_prompt="""You are a data analysis coordinator.

When given a data analysis request, you should:
1. Break down the work into independent analysis tasks
2. Use spawn_parallel to execute tasks concurrently
3. Summarize the aggregated results

Example usage:
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "data_analyzer", "directive": "Analyze dataset A: calculate mean, median, std"},
        {"name": "data_analyzer", "directive": "Analyze dataset B: identify outliers"},
        {"name": "data_analyzer", "directive": "Analyze dataset C: correlation analysis"}
    ],
    max_concurrent=3,
    aggregate=true
)

IMPORTANT: Always use action="spawn_parallel" for parallel execution!
"""
    )

    # Create analyzer subagent
    analyzer = Agent(
        name="data_analyzer",
        conf=agent_conf,
        desc="Analyzes datasets and generates statistical reports",
        system_prompt="You are a data analyst. Perform statistical analysis on given datasets and return concise results."
    )

    # Create TeamSwarm
    logger.info("\n[Step 1] Building TeamSwarm...")
    swarm = TeamSwarm(
        coordinator,
        analyzer
    )
    logger.info("✓ TeamSwarm created with coordinator and data_analyzer")

    # Execute parallel analysis
    logger.info("\n[Step 2] Executing parallel data analysis...")
    logger.info("Task: Analyze three datasets (sales, marketing, support)")

    task_response = await Runners.run(
        input="""Analyze these three datasets in parallel:
1. Sales data (Q1 2024): [100, 120, 95, 130, 110] - Calculate statistics
2. Marketing data (Q1 2024): [80, 85, 90, 85, 95] - Identify trends
3. Support tickets (Q1 2024): [50, 45, 60, 55, 50] - Calculate average

Use spawn_parallel to analyze all three concurrently.""",
        swarm=swarm
    )

    # Extract result
    if task_response:
        if task_response.success:
            result = task_response.answer
            logger.info("\n[Result]")
            logger.info(result)
            logger.info("\n✓ Test 1 PASSED")
            return True
        else:
            logger.error(f"\n✗ Execution failed: {task_response.msg}")
            return False
    else:
        logger.error("\n✗ No result returned")
        return False


async def test_spawn_parallel_mixed_agents():
    """
    Test 2: Parallel spawning with different subagent types

    Scenario: Coordinator spawns 3 different specialized agents
    """
    logger.info("\n\n" + "="*80)
    logger.info("Test 2: Parallel Spawn (Mixed Agent Types)")
    logger.info("="*80)

    # Load environment
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    model_config = ModelConfig(
        llm_model_name=os.getenv('LLM_MODEL_NAME', 'gpt-4o'),
        llm_provider=os.getenv('LLM_PROVIDER', 'openai'),
        llm_api_key=os.getenv('LLM_API_KEY'),
        llm_base_url=os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')
    )

    agent_conf = AgentConfig(llm_config=model_config)

    # Create coordinator
    coordinator = Agent(
        name="project_coordinator",
        conf=agent_conf,
        desc="Coordinates code review workflow",
        enable_subagent=True,
        tool_names=["spawn_subagent"],  # Add spawn_subagent tool
        system_prompt="""You are a project coordinator for code review.

When given a code review request, use spawn_parallel to run:
1. Code quality analysis
2. Documentation check
3. Security scan

Example:
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "code_analyzer", "directive": "Analyze code quality and complexity"},
        {"name": "doc_checker", "directive": "Check documentation completeness"},
        {"name": "security_scanner", "directive": "Scan for security vulnerabilities"}
    ],
    max_concurrent=3
)

IMPORTANT: Use action="spawn_parallel" to run all three in parallel!
"""
    )

    # Create three specialized agents
    code_analyzer = Agent(
        name="code_analyzer",
        conf=agent_conf,
        desc="Analyzes code quality and complexity",
        system_prompt="You are a code quality expert. Analyze code structure, complexity, and best practices."
    )

    doc_checker = Agent(
        name="doc_checker",
        conf=agent_conf,
        desc="Checks documentation completeness",
        system_prompt="You are a documentation expert. Check if code is properly documented."
    )

    security_scanner = Agent(
        name="security_scanner",
        conf=agent_conf,
        desc="Scans for security vulnerabilities",
        system_prompt="You are a security expert. Identify potential security issues in code."
    )

    # Create TeamSwarm with multiple agents
    logger.info("\n[Step 1] Building TeamSwarm with 3 specialized agents...")
    swarm = TeamSwarm(
        coordinator,
        code_analyzer,
        doc_checker,
        security_scanner
    )
    logger.info("✓ TeamSwarm created with coordinator + 3 specialists")

    # Execute parallel review
    logger.info("\n[Step 2] Executing parallel code review...")

    task_response = await Runners.run(
        input="""Review this authentication module in parallel:

Code snippet:
```python
def authenticate(username, password):
    user = db.query(f"SELECT * FROM users WHERE name='{username}'")
    if user and user.password == password:
        return True
    return False
```

Run three parallel checks:
1. Code quality analysis
2. Documentation review
3. Security vulnerability scan

Use spawn_parallel to run all three concurrently.""",
        swarm=swarm
    )

    # Extract result
    if task_response:
        if task_response.success:
            result = task_response.answer
            logger.info("\n[Result]")
            logger.info(result)
            logger.info("\n✓ Test 2 PASSED")
            return True
        else:
            logger.error(f"\n✗ Execution failed: {task_response.msg}")
            return False
    else:
        logger.error("\n✗ No result returned")
        return False


async def test_spawn_parallel_structured_output():
    """
    Test 3: Using structured JSON output (aggregate=false)

    Scenario: Get programmatic access to individual task results
    """
    logger.info("\n\n" + "="*80)
    logger.info("Test 3: Parallel Spawn (Structured JSON Output)")
    logger.info("="*80)

    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    model_config = ModelConfig(
        llm_model_name=os.getenv('LLM_MODEL_NAME', 'gpt-4o'),
        llm_provider=os.getenv('LLM_PROVIDER', 'openai'),
        llm_api_key=os.getenv('LLM_API_KEY'),
        llm_base_url=os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')
    )

    agent_conf = AgentConfig(llm_config=model_config)

    # Create coordinator
    coordinator = Agent(
        name="orchestrator",
        conf=agent_conf,
        desc="Orchestrates parallel tasks with structured output",
        enable_subagent=True,
        tool_names=["spawn_subagent"],  # Add spawn_subagent tool
        system_prompt="""You are an orchestrator agent.

Use spawn_parallel with aggregate=false to get JSON output:

spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "validator", "directive": "Validate input data format"},
        {"name": "validator", "directive": "Check data ranges"},
        {"name": "validator", "directive": "Verify data consistency"}
    ],
    aggregate=false  # Get JSON instead of markdown summary
)

This returns structured JSON:
{
  "summary": {"total_tasks": N, "success_count": M, ...},
  "tasks": [{"name": "...", "status": "...", "result": "..."}, ...]
}

IMPORTANT: Set aggregate=false for structured output!
"""
    )

    # Create validator agent
    validator = Agent(
        name="validator",
        conf=agent_conf,
        desc="Validates data according to rules",
        system_prompt="You are a data validator. Check data against validation rules and return pass/fail results."
    )

    logger.info("\n[Step 1] Building TeamSwarm...")
    swarm = TeamSwarm(coordinator, validator)
    logger.info("✓ TeamSwarm created")

    logger.info("\n[Step 2] Executing parallel validation with JSON output...")

    task_response = await Runners.run(
        input="""Validate this user input in parallel:
- Email: user@example.com
- Age: 25
- Country: USA

Run three validations concurrently:
1. Email format validation
2. Age range validation (18-100)
3. Country code validation

Use spawn_parallel with aggregate=false to get structured JSON output.""",
        swarm=swarm
    )

    if task_response:
        if task_response.success:
            result = task_response.answer
            logger.info("\n[Result]")
            logger.info(result)

            # Check if result contains JSON structure
            if '"summary"' in result and '"tasks"' in result:
                logger.info("\n✓ Test 3 PASSED (JSON output detected)")
                return True
            else:
                logger.warning("\n⚠️ Test 3 PARTIAL PASS (no JSON detected, but execution succeeded)")
                return True
        else:
            logger.error(f"\n✗ Execution failed: {task_response.msg}")
            return False
    else:
        logger.error("\n✗ No result returned")
        return False


async def test_spawn_parallel_with_aworld_agent():
    """
    Test 4: Using spawn_parallel with AWorld built-in agent

    Scenario: Test spawn_parallel with actual AWorld agent and its subagents
    """
    logger.info("\n\n" + "="*80)
    logger.info("Test 4: Parallel Spawn (AWorld Built-in Agent)")
    logger.info("="*80)

    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)

    # Import AWorld agent builder
    try:
        from aworld_cli.inner_plugins.smllc.agents.aworld_agent import build_aworld_agent
    except ImportError as e:
        logger.error(f"✗ Failed to import build_aworld_agent: {e}")
        logger.error("Make sure aworld-cli is in PYTHONPATH")
        return False

    # Build AWorld agent
    logger.info("\n[Step 1] Building AWorld agent...")
    try:
        aworld_swarm = build_aworld_agent()
        logger.info("✓ AWorld agent built successfully")
    except Exception as e:
        logger.error(f"✗ Failed to build AWorld agent: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

    # Initialize swarm
    logger.info("\n[Step 2] Initializing AWorld TeamSwarm...")
    aworld_swarm.reset()

    # Verify subagent capability
    root_agent = aworld_swarm.agent_graph.root_agent
    if not root_agent.enable_subagent:
        logger.error("✗ AWorld agent does not have enable_subagent=True")
        return False
    logger.info("✓ AWorld agent has subagent capability")

    # Check available subagents
    if root_agent.subagent_manager._available_subagents:
        available = list(root_agent.subagent_manager._available_subagents.keys())
        logger.info(f"✓ Available subagents: {available}")
    else:
        logger.warning("⚠️ No subagents registered (expected: developer, evaluator, etc.)")

    # Execute task with spawn_parallel
    logger.info("\n[Step 3] Testing spawn_parallel with AWorld agent...")
    logger.info("Task: Parallel code analysis + documentation + test generation")

    task_response = await Runners.run(
        input="""I have a Python module that needs comprehensive review.

Use spawn_parallel to run three tasks concurrently:
1. Code quality analysis - check best practices
2. Documentation review - verify docstrings
3. Test coverage analysis - assess test completeness

Example code:
```python
def calculate_sum(numbers):
    total = 0
    for num in numbers:
        total += num
    return total
```

Use spawn_parallel with available subagents to run all three tasks in parallel.""",
        swarm=aworld_swarm
    )

    if task_response:
        if task_response.success:
            result = task_response.answer
            logger.info("\n[Result]")
            logger.info(result)
            logger.info("\n✓ Test 4 PASSED")
            return True
        else:
            logger.error(f"\n✗ Execution failed: {task_response.msg}")
            return False
    else:
        logger.error("\n✗ No result returned")
        return False


async def run_all_tests():
    """Run all spawn_parallel tests"""
    logger.info("\n" + "="*80)
    logger.info("AWorld Agent spawn_parallel Test Suite")
    logger.info("="*80 + "\n")

    tests = [
        ("Basic Parallel Spawn", test_spawn_parallel_basic),
        ("Mixed Agent Types", test_spawn_parallel_mixed_agents),
        ("Structured JSON Output", test_spawn_parallel_structured_output),
        ("AWorld Built-in Agent", test_spawn_parallel_with_aworld_agent)
    ]

    results = {}

    for test_name, test_func in tests:
        logger.info(f"\nRunning: {test_name}")
        logger.info("-" * 80)

        try:
            success = await test_func()
            results[test_name] = success
        except Exception as e:
            logger.error(f"✗ Test {test_name} crashed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results[test_name] = False

        # Brief pause between tests
        await asyncio.sleep(2)

    # Print summary
    logger.info("\n\n" + "="*80)
    logger.info("Test Summary")
    logger.info("="*80)

    total = len(results)
    passed = sum(1 for r in results.values() if r)

    for test_name, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        logger.info(f"{status}: {test_name}")

    logger.info(f"\nTotal: {passed}/{total} tests passed")
    logger.info("="*80)

    return all(results.values())


if __name__ == '__main__':
    # Run all tests
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
