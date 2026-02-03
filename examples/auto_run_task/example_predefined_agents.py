"""
Predefined agents example.

This example demonstrates how to:
- Create and pass predefined agents
- Let MetaAgent choose from available agents
- Mix predefined agents with skill-based agents
"""

import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


async def main():
    from aworld.runner import Runners
    from aworld.agents.llm_agent import Agent
    from aworld.config import AgentConfig, ModelConfig
    import os
    
    # Create predefined agents with specialized capabilities
    search_agent = Agent(
        name="SearchAgent",
        desc="Web search specialist using Tavily API for real-time information retrieval",
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.getenv("LLM_MODEL_NAME"),
                llm_provider=os.getenv("LLM_PROVIDER"),
                llm_api_key=os.getenv("LLM_API_KEY"),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_temperature=0.0
            )
        ),
        system_prompt="You are a web search specialist. Use search tools to find accurate, up-to-date information.",
        mcp_servers=["tavily-mcp"],
        mcp_config={
            "mcpServers": {
                "tavily-mcp": {
                    "command": "npx",
                    "args": ["-y", "tavily-mcp@0.1.2"],
                    "env": {
                        "TAVILY_API_KEY": os.getenv("TAVILY_API_KEY"),
                        "NODE_ENV": "production"
                    }
                }
            }
        }
    )
    
    analyst_agent = Agent(
        name="AnalystAgent",
        desc="Data analyst specialist for processing and interpreting search results",
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.getenv("LLM_MODEL_NAME"),
                llm_provider=os.getenv("LLM_PROVIDER"),
                llm_api_key=os.getenv("LLM_API_KEY"),
                llm_base_url=os.getenv("LLM_BASE_URL"),
                llm_temperature=0.2
            )
        ),
        system_prompt="You are a data analyst. Analyze information and provide insights with clear reasoning."
    )
    
    # Define query
    query = "搜索并分析最新的 AI 技术趋势"
    
    skills_path = Path(__file__).resolve().parents[1] / "skill_agent" / "skills"
    
    print(f"🎯 Query: {query}")
    print("\n📦 Available predefined agents:")
    print(f"  - SearchAgent: {search_agent.desc}")
    print(f"  - AnalystAgent: {analyst_agent.desc}\n")
    
    # Auto-run with predefined agents
    # MetaAgent will decide whether to use them based on query
    results, yaml_path = await Runners.auto_run_task(
        query=query,
        available_agents={
            "search_agent": search_agent,
            "analyst_agent": analyst_agent
        },
        skills_path=skills_path,
        save_plan=True
    )
    
    # Print results
    task_id = list(results.keys())[0]
    response = results[task_id]
    
    print(f"\n✅ Task completed!")
    print(f"📄 Plan saved at: {yaml_path}")
    
    # Show which agents were actually used
    print("\n📊 Task plan overview:")
    with open(yaml_path, 'r', encoding='utf-8') as f:
        import yaml
        plan = yaml.safe_load(f)
        agents = plan.get('agents', [])
        print(f"  Total agents: {len(agents)}")
        for agent in agents:
            agent_type = agent.get('type', 'unknown')
            print(f"  - {agent['id']} (type={agent_type})")
    
    print(f"\n📝 Answer:\n{response.answer}")


if __name__ == "__main__":
    asyncio.run(main())
