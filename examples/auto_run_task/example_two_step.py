"""
Two-step usage example: plan_task + execute_plan.

This example demonstrates:
- Step 1: Generate Task YAML (review/modify before execution)
- Step 2: Execute the plan
"""

import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


async def main():
    from aworld.runner import Runners
    
    # Define query
    query = "帮我找到最新一周 BABA 的股价并分析趋势"
    
    # Path to skills directory
    skills_path = Path(__file__).resolve().parents[1] / "skill_agent" / "skills"
    
    print(f"🎯 Query: {query}\n")
    
    # ============================================================
    # Step 1: Plan (generate YAML)
    # ============================================================
    print("📝 Step 1: Generating task plan...")
    
    yaml_path = await Runners.plan_task(
        query=query,
        skills_path=skills_path,
        output_yaml="./stock_analysis_task.yaml"
    )
    
    print(f"✅ Task plan generated: {yaml_path}")
    print("\n⚠️ You can now review/modify the YAML before execution.")
    
    # Optional: Display YAML content
    with open(yaml_path, 'r', encoding='utf-8') as f:
        yaml_content = f.read()
    print(f"\n{'='*60}")
    print("Generated YAML:")
    print('='*60)
    print(yaml_content)
    print('='*60)
    
    # Ask user to confirm
    user_input = input("\n▶️ Press Enter to execute, or 'q' to quit: ")
    if user_input.lower() == 'q':
        print("❌ Execution cancelled.")
        return
    
    # ============================================================
    # Step 2: Execute
    # ============================================================
    print("\n🚀 Step 2: Executing task...")
    
    results = await Runners.execute_plan(
        yaml_path=yaml_path,
        skills_path=skills_path
    )
    
    # Print results
    task_id = list(results.keys())[0]
    response = results[task_id]
    
    print(f"\n✅ Task completed!")
    print(f"\n📝 Answer:\n{response.answer}")


if __name__ == "__main__":
    asyncio.run(main())
