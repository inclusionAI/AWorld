# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Example: Parallel Subagent Spawning

Demonstrates how to use the spawn_parallel action for concurrent subagent execution.

Use Cases:
1. Data analysis: Process multiple datasets in parallel
2. Content generation: Generate multiple reports/documents concurrently
3. Code review: Analyze multiple modules simultaneously
4. Testing: Run multiple test suites in parallel

Prerequisites:
- Enable subagent functionality: enable_subagent=True
- Configure subagents (TeamSwarm members or agent.md files)
- Set up appropriate tools for each subagent
"""

import asyncio
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm
from aworld.config.conf import AgentConfig
from aworld.runner import Runners


async def example_1_basic_parallel_spawn():
    """
    Example 1: Basic parallel spawning with TeamSwarm members.

    Scenario: Coordinator agent delegates data analysis tasks to multiple
    analyst agents in parallel.
    """
    print("=" * 60)
    print("Example 1: Basic Parallel Spawn")
    print("=" * 60)

    # Configure LLM
    agent_conf = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="your_api_key"
    )

    # Create coordinator agent (with subagent capability)
    coordinator = Agent(
        name="coordinator",
        conf=agent_conf,
        desc="Coordinates data analysis tasks",
        system_prompt="""You are a data analysis coordinator.

When given a dataset analysis request, you should:
1. Break down the work into independent subtasks
2. Use spawn_parallel to delegate tasks to analyst agents concurrently
3. Review and synthesize the results

Example usage:
```
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "data_analyzer", "directive": "Calculate statistics for dataset A"},
        {"name": "data_analyzer", "directive": "Identify outliers in dataset B"},
        {"name": "data_analyzer", "directive": "Generate correlation matrix for dataset C"}
    ],
    max_concurrent=3,
    aggregate=True
)
```
""",
        enable_subagent=True  # Enable subagent functionality
    )

    # Create analyst agent (will be used as subagent)
    analyst = Agent(
        name="data_analyzer",
        conf=agent_conf,
        desc="Analyzes datasets and generates statistical reports",
        system_prompt="You are a data analyst. Perform statistical analysis on given datasets."
    )

    # Create TeamSwarm (coordinator can spawn analyst)
    swarm = TeamSwarm(
        coordinator,
        analyst
    )

    # Execute task
    result = await Runners.run(
        input="Analyze three datasets: sales_2024.csv, marketing_2024.csv, support_2024.csv",
        swarm=swarm
    )

    print(f"\nResult:\n{result}")
    print("\n" + "=" * 60 + "\n")


async def example_2_mixed_subagents():
    """
    Example 2: Parallel spawning with different subagent types.

    Scenario: Project manager delegates tasks to code analyzer,
    documentation writer, and test runner concurrently.
    """
    print("=" * 60)
    print("Example 2: Mixed Subagent Types")
    print("=" * 60)

    agent_conf = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="your_api_key"
    )

    # Create project manager
    manager = Agent(
        name="project_manager",
        conf=agent_conf,
        system_prompt="""You are a project manager for code review tasks.

When given a code review request, use spawn_parallel to run multiple tasks:
- Code analysis
- Documentation check
- Test execution

Example:
```
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {
            "name": "code_analyzer",
            "directive": "Analyze code quality in src/module.py",
            "disallowedTools": "write_file"  # Read-only
        },
        {
            "name": "doc_writer",
            "directive": "Check if API documentation is complete",
            "model": "gpt-4o"
        },
        {
            "name": "test_runner",
            "directive": "Run unit tests and report coverage"
        }
    ],
    max_concurrent=3,
    aggregate=True
)
```
""",
        enable_subagent=True
    )

    # Create specialized agents
    code_analyzer = Agent(
        name="code_analyzer",
        conf=agent_conf,
        desc="Analyzes code quality, complexity, and potential issues",
        tool_names=["read_file", "glob", "grep"]  # Read-only tools
    )

    doc_writer = Agent(
        name="doc_writer",
        conf=agent_conf,
        desc="Checks and updates documentation",
        tool_names=["read_file", "write_file"]
    )

    test_runner = Agent(
        name="test_runner",
        conf=agent_conf,
        desc="Runs tests and generates coverage reports",
        tool_names=["terminal"]
    )

    # Create TeamSwarm
    swarm = TeamSwarm(
        manager,
        code_analyzer,
        doc_writer,
        test_runner
    )

    # Execute
    result = await Runners.run(
        input="Review the authentication module: analyze code, check docs, run tests",
        swarm=swarm
    )

    print(f"\nResult:\n{result}")
    print("\n" + "=" * 60 + "\n")


async def example_3_structured_output():
    """
    Example 3: Using structured JSON output instead of aggregated summary.

    Scenario: Need programmatic access to individual task results.
    """
    print("=" * 60)
    print("Example 3: Structured Output")
    print("=" * 60)

    agent_conf = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="your_api_key"
    )

    orchestrator = Agent(
        name="orchestrator",
        conf=agent_conf,
        system_prompt="""You are an orchestrator agent.

Use spawn_parallel with aggregate=false to get structured JSON output:
```
spawn_subagent(
    action="spawn_parallel",
    tasks=[...],
    aggregate=False  # Get JSON instead of markdown summary
)
```

The result will be a JSON object with:
{
  "summary": {
    "total_tasks": N,
    "success_count": M,
    "failed_count": K,
    "total_elapsed": seconds
  },
  "tasks": [
    {
      "name": "task_name",
      "status": "success" or "error",
      "result": "..." or "error": "...",
      "elapsed": seconds
    }
  ]
}
""",
        enable_subagent=True
    )

    worker = Agent(
        name="worker",
        conf=agent_conf,
        desc="General-purpose worker"
    )

    swarm = TeamSwarm(orchestrator, worker)

    result = await Runners.run(
        input="Process items A, B, C in parallel and return structured results",
        swarm=swarm
    )

    print(f"\nResult:\n{result}")
    print("\n" + "=" * 60 + "\n")


async def example_4_error_handling():
    """
    Example 4: Error handling with fail_fast mode.

    Scenario: Stop processing if critical task fails.
    """
    print("=" * 60)
    print("Example 4: Error Handling (fail_fast)")
    print("=" * 60)

    agent_conf = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="your_api_key"
    )

    supervisor = Agent(
        name="supervisor",
        conf=agent_conf,
        system_prompt="""You are a deployment supervisor.

Use fail_fast=True to stop immediately if any validation fails:
```
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "validator", "directive": "Validate database schema"},
        {"name": "validator", "directive": "Validate API endpoints"},
        {"name": "validator", "directive": "Validate security configs"}
    ],
    fail_fast=True  # Stop on first failure
)
```

Use fail_fast=False (default) to complete all tasks despite failures:
- Useful for comprehensive reports
- Collect all errors at once
""",
        enable_subagent=True
    )

    validator = Agent(
        name="validator",
        conf=agent_conf,
        desc="Validates system components"
    )

    swarm = TeamSwarm(supervisor, validator)

    result = await Runners.run(
        input="Run pre-deployment validation (stop on first failure)",
        swarm=swarm
    )

    print(f"\nResult:\n{result}")
    print("\n" + "=" * 60 + "\n")


async def example_5_concurrency_control():
    """
    Example 5: Controlling concurrency with max_concurrent.

    Scenario: Limit concurrent executions to avoid resource exhaustion.
    """
    print("=" * 60)
    print("Example 5: Concurrency Control")
    print("=" * 60)

    agent_conf = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="your_api_key"
    )

    controller = Agent(
        name="batch_controller",
        conf=agent_conf,
        system_prompt="""You are a batch processing controller.

Use max_concurrent to limit parallel execution:
```
spawn_subagent(
    action="spawn_parallel",
    tasks=[...20 tasks...],
    max_concurrent=5  # Process 5 at a time
)
```

Guidelines:
- CPU-bound tasks: max_concurrent = num_cores (4-8)
- I/O-bound tasks: max_concurrent = 10-20
- API rate limits: max_concurrent = 2-5
""",
        enable_subagent=True
    )

    processor = Agent(
        name="data_processor",
        conf=agent_conf,
        desc="Processes data items"
    )

    swarm = TeamSwarm(controller, processor)

    result = await Runners.run(
        input="Process 50 data items with max 5 concurrent executions",
        swarm=swarm
    )

    print(f"\nResult:\n{result}")
    print("\n" + "=" * 60 + "\n")


async def main():
    """Run all examples"""
    print("\n" + "=" * 60)
    print("Parallel Subagent Spawning Examples")
    print("=" * 60 + "\n")

    examples = [
        ("Basic Parallel Spawn", example_1_basic_parallel_spawn),
        ("Mixed Subagent Types", example_2_mixed_subagents),
        ("Structured Output", example_3_structured_output),
        ("Error Handling", example_4_error_handling),
        ("Concurrency Control", example_5_concurrency_control)
    ]

    for name, example_func in examples:
        print(f"\nRunning: {name}")
        try:
            await example_func()
        except Exception as e:
            print(f"❌ Example failed: {e}")
            import traceback
            traceback.print_exc()

        print("\n" + "-" * 60 + "\n")
        await asyncio.sleep(1)  # Brief pause between examples


if __name__ == '__main__':
    asyncio.run(main())
