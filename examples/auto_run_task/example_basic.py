"""
Basic usage example of auto_run_task.

This example demonstrates the simplest usage:
- Auto-generate Task YAML from query
- Execute immediately
- Save plan for review
"""

import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


async def main():
    from aworld.runner import Runners
    
    # Define query
    query = "帮我总结一下 AWorld 框架的核心功能"
    
    # Path to skills directory (optional)
    skills_path = Path(__file__).resolve().parents[1] / "skill_agent" / "skills"
    
    # Auto-run: plan + execute in one call
    print(f"🎯 Query: {query}\n")
    
    results, yaml_path = await Runners.auto_run_task(
        query=query,
        skills_path=skills_path,
        save_plan=True  # Save YAML for review
    )
    
    # Print results
    task_id = list(results.keys())[0]
    response = results[task_id]
    
    print(f"\n✅ Task completed!")
    print(f"📄 Plan saved at: {yaml_path}")
    print(f"\n📝 Answer:\n{response.answer}")


if __name__ == "__main__":
    asyncio.run(main())
