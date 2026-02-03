"""
Custom MetaAgent example.

This example demonstrates how to:
- Create a custom MetaAgent with specialized prompt
- Configure retry behavior
- Use custom LLM settings
"""

import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


async def main():
    from aworld.runner import Runners
    from aworld.agents.meta_agent import MetaAgent
    from aworld.config import AgentConfig, ModelConfig
    import os
    
    # Create custom MetaAgent with specialized prompt
    custom_prompt = """
You are a specialized MetaAgent for financial data analysis tasks.

When planning tasks:
1. Prioritize accuracy and data validation
2. Always include error handling agents
3. Prefer established financial data sources
4. Include visualization when appropriate

Follow the standard Task YAML format defined in your training.
"""
    
    meta_agent = MetaAgent(
        system_prompt=custom_prompt,
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.getenv("LLM_MODEL_NAME", "gpt-4"),
                llm_provider=os.getenv("LLM_PROVIDER", "openai"),
                llm_api_key=os.getenv("LLM_API_KEY"),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_temperature=0.1  # Slightly higher for more creative planning
            )
        ),
        max_yaml_retry=5  # More retries for complex financial tasks
    )
    
    # Define query
    query = "分析 AAPL 最近的财报数据，并预测下季度表现"
    
    skills_path = Path(__file__).resolve().parents[1] / "skill_agent" / "skills"
    
    print(f"🎯 Query: {query}")
    print(f"🧠 Using custom MetaAgent with specialized financial analysis prompt\n")
    
    # Auto-run with custom MetaAgent
    results, yaml_path = await Runners.auto_run_task(
        query=query,
        meta_agent=meta_agent,  # Use custom MetaAgent
        skills_path=skills_path,
        save_plan=True
    )
    
    # Print results
    task_id = list(results.keys())[0]
    response = results[task_id]
    
    print(f"\n✅ Task completed!")
    print(f"📄 Plan saved at: {yaml_path}")
    print(f"\n📝 Answer:\n{response.answer}")


if __name__ == "__main__":
    asyncio.run(main())
