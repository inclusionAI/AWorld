"""
Code Analysis Task Example

Demonstrates coordinator delegating code analysis to specialized subagent.
"""

CODE_ANALYSIS_TASK = """
Analyze the SubagentManager implementation in aworld/core/agent/subagent_manager.py:

1. Use code_analyzer to identify key design patterns:
   - Singleton pattern?
   - Factory pattern?
   - Strategy pattern?
   - Observer pattern?

2. Use code_analyzer to analyze the spawn() method:
   - Method complexity (cyclomatic complexity)
   - Dependencies and coupling
   - Error handling strategy

3. Use web_searcher to research best practices:
   - Python delegation patterns
   - Agent coordination patterns
   - Tool access control patterns

4. Use report_writer to synthesize findings:
   - Design patterns summary
   - Strengths and weaknesses
   - Comparison with best practices
   - Recommendations for improvement

Save final report to outputs/code_analysis_report.md
"""


if __name__ == '__main__':
    """
    Standalone runner for this task.

    Usage:
        python tasks/code_analysis.py
    """
    import asyncio
    import sys
    from pathlib import Path

    # Add parent directory to path
    parent = Path(__file__).parent.parent
    sys.path.insert(0, str(parent))

    from run_demo import setup_environment, create_team_swarm
    from aworld.runner import Runners

    async def run():
        config = setup_environment()
        team_swarm = create_team_swarm(config)

        result = await Runners.async_run(
            input=CODE_ANALYSIS_TASK,
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
