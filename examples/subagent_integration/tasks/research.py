"""
Research Task Example

Demonstrates coordinator delegating web research and report writing.
"""

RESEARCH_TASK = """
Research Python asyncio best practices for multi-agent systems:

1. Use web_searcher to find official documentation:
   - Python asyncio official docs
   - AsyncIO design patterns
   - Context propagation in async code

2. Use web_searcher to research best practices:
   - Error handling in async workflows
   - Performance optimization tips
   - Common pitfalls and solutions

3. Use web_searcher to find real-world examples:
   - Open-source projects using asyncio for agents
   - Case studies of multi-agent systems
   - Lessons learned from production systems

4. Use report_writer to create comprehensive guide:
   - Executive summary
   - Key concepts explained
   - Best practices checklist
   - Code examples
   - Common pitfalls section
   - References

Save final guide to outputs/asyncio_best_practices.md
"""


if __name__ == '__main__':
    """
    Standalone runner for this task.

    Usage:
        python tasks/research.py
    """
    import asyncio
    import sys
    from pathlib import Path

    parent = Path(__file__).parent.parent
    sys.path.insert(0, str(parent))

    from run_demo import setup_environment, create_team_swarm
    from aworld.runner import Runners

    async def run():
        config = setup_environment()
        team_swarm = create_team_swarm(config)

        result = await Runners.async_run(
            input=RESEARCH_TASK,
            swarm=team_swarm
        )

        print("\nResult:")
        print("="*60)
        if hasattr(result, 'output'):
            print(result.output)
        else:
            print(result)
        print("="*60)

    asyncio.run(run())
