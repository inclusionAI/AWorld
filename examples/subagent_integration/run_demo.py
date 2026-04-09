#!/usr/bin/env python3
"""
Subagent Integration Test Demo

Demonstrates end-to-end subagent delegation in a real TeamSwarm environment.

Usage:
    python run_demo.py [--task TASK_NAME] [--verbose]

Tasks:
    code_analysis: Analyze SubagentManager implementation
    research: Research asyncio best practices
    custom: Enter custom task interactively
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.runner import Runners
from aworld.logs.util import logger


def setup_environment():
    """Load environment configuration"""
    # Try to load .env from current directory
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded configuration from .env")
    else:
        logger.warning(".env not found. Using environment variables or defaults.")
        logger.info("Copy .env.example to .env and configure your API keys.")

    # Validate required config
    required_vars = ['LLM_API_KEY']
    missing = [var for var in required_vars if not os.getenv(var)]

    if missing:
        logger.error(f"Missing required environment variables: {missing}")
        logger.info("Please set them in .env or environment")
        sys.exit(1)

    # Create output directory
    output_dir = Path(__file__).parent / 'outputs'
    output_dir.mkdir(exist_ok=True)

    return {
        'llm_provider': os.getenv('LLM_PROVIDER', 'openai'),
        'llm_model_name': os.getenv('LLM_MODEL_NAME', 'gpt-4o'),
        'llm_api_key': os.getenv('LLM_API_KEY'),
        'llm_base_url': os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1'),
        'output_dir': output_dir,
        'verbose': os.getenv('DEMO_VERBOSE', 'false').lower() == 'true'
    }


def create_team_swarm(config: dict) -> Swarm:
    """
    Create TeamSwarm with coordinator and specialized members.

    Args:
        config: Environment configuration

    Returns:
        Swarm: Configured TeamSwarm
    """
    from agents.coordinator import create_coordinator, create_team_members

    logger.info("Creating coordinator agent...")
    coordinator = create_coordinator(
        llm_provider=config['llm_provider'],
        llm_model_name=config['llm_model_name'],
        llm_api_key=config['llm_api_key'],
        llm_base_url=config['llm_base_url']
    )

    logger.info("Creating team member agents...")
    members = create_team_members()

    logger.info("Building TeamSwarm...")
    team_swarm = Swarm(
        coordinator,  # Leader
        *members,     # Executors
        build_type=GraphBuildType.TEAM
    )

    logger.info(f"TeamSwarm created with {len(members)} specialized members")
    return team_swarm


# Demo Task Definitions
DEMO_TASKS = {
    'code_analysis': """
Analyze the SubagentManager implementation in aworld/core/agent/subagent_manager.py:

1. Identify key design patterns used in the implementation
2. Analyze the spawn() method's complexity and architecture
3. Search for Python best practices about delegation patterns
4. Generate a summary report highlighting:
   - Design patterns found
   - Strengths and potential improvements
   - Comparison with industry best practices

Save the report to outputs/code_analysis_report.md
""",

    'research': """
Research Python asyncio best practices for multi-agent systems:

1. Search for official asyncio documentation
2. Find best practices for:
   - Context propagation in async code
   - Error handling in async workflows
   - Performance optimization
3. Look for common pitfalls and their solutions
4. Create a reference guide with:
   - Key concepts
   - Best practices checklist
   - Code examples

Save the guide to outputs/asyncio_best_practices.md
""",

    'integration_test': """
Verify the subagent delegation mechanism is working correctly:

1. Check that coordinator has spawn_subagent tool available
2. Attempt to spawn code_analyzer with a simple task
3. Attempt to spawn web_searcher with a simple task
4. Verify results are returned correctly
5. Check token usage is tracked properly
6. Generate a validation report

Save the report to outputs/integration_test_report.md
"""
}


async def run_task(task_name: str, team_swarm: Swarm, config: dict):
    """
    Run a demo task using the team swarm.

    Args:
        task_name: Name of the task to run
        team_swarm: Configured TeamSwarm
        config: Environment configuration
    """
    if task_name not in DEMO_TASKS:
        logger.error(f"Unknown task: {task_name}")
        logger.info(f"Available tasks: {', '.join(DEMO_TASKS.keys())}")
        return

    task_input = DEMO_TASKS[task_name]

    logger.info(f"\n{'='*60}")
    logger.info(f"Running task: {task_name}")
    logger.info(f"{'='*60}\n")

    if config['verbose']:
        logger.info(f"Task input:\n{task_input}\n")

    try:
        # Run task
        logger.info("Executing task...")
        result = await Runners.run(
            input=task_input,
            swarm=team_swarm
        )

        logger.info("\n" + "="*60)
        logger.info("Task completed successfully!")
        logger.info("="*60 + "\n")

        # Display result
        if hasattr(result, 'output'):
            output = result.output
        elif hasattr(result, 'content'):
            output = result.content
        else:
            output = str(result)

        logger.info("Result:")
        logger.info("-" * 60)
        logger.info(output)
        logger.info("-" * 60)

        # Display token usage if available
        if hasattr(result, 'token_usage'):
            logger.info(f"\nToken usage: {result.token_usage}")

    except Exception as e:
        logger.error(f"Task failed with error: {e}")
        if config['verbose']:
            import traceback
            traceback.print_exc()


async def run_custom_task(team_swarm: Swarm, config: dict):
    """
    Run a custom task entered by the user.

    Args:
        team_swarm: Configured TeamSwarm
        config: Environment configuration
    """
    logger.info("\nEnter your custom task (press Ctrl+D or Ctrl+Z when done):")
    logger.info("-" * 60)

    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass

    task_input = '\n'.join(lines).strip()

    if not task_input:
        logger.warning("No task entered. Exiting.")
        return

    logger.info(f"\n{'='*60}")
    logger.info("Running custom task")
    logger.info(f"{'='*60}\n")

    try:
        result = await Runners.run(
            input=task_input,
            swarm=team_swarm
        )

        logger.info("\n" + "="*60)
        logger.info("Task completed!")
        logger.info("="*60 + "\n")

        if hasattr(result, 'output'):
            output = result.output
        elif hasattr(result, 'content'):
            output = result.content
        else:
            output = str(result)

        logger.info("Result:")
        logger.info("-" * 60)
        logger.info(output)
        logger.info("-" * 60)

    except Exception as e:
        logger.error(f"Task failed: {e}")
        if config['verbose']:
            import traceback
            traceback.print_exc()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Subagent Integration Demo',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available Tasks:
{chr(10).join(f'  - {name}: {desc.split(chr(10))[0].strip()}'
              for name, desc in DEMO_TASKS.items())}

Examples:
  python run_demo.py --task code_analysis
  python run_demo.py --task research --verbose
  python run_demo.py --task custom
        """
    )

    parser.add_argument(
        '--task',
        choices=list(DEMO_TASKS.keys()) + ['custom'],
        default='code_analysis',
        help='Task to run (default: code_analysis)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Setup
    logger.info("Subagent Integration Demo")
    logger.info("=" * 60)

    config = setup_environment()
    config['verbose'] = config['verbose'] or args.verbose

    logger.info(f"Configuration:")
    logger.info(f"  Provider: {config['llm_provider']}")
    logger.info(f"  Model: {config['llm_model_name']}")
    logger.info(f"  Output: {config['output_dir']}")
    logger.info(f"  Verbose: {config['verbose']}")
    logger.info("")

    # Create team
    team_swarm = create_team_swarm(config)

    # Run task
    if args.task == 'custom':
        asyncio.run(run_custom_task(team_swarm, config))
    else:
        asyncio.run(run_task(args.task, team_swarm, config))

    logger.info("\nDemo complete!")


if __name__ == '__main__':
    main()
