# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Text-to-Swarm and Text-to-Run Example

This example demonstrates how to use natural language to automatically generate
and execute multi-agent systems using SwarmComposerAgent.

Key features:
1. text_to_swarm: Generate reusable Swarm from natural language
2. text_to_task: Create Task with automatic or reused Swarm
3. text_to_run: One-shot conversion from text to execution results
4. Swarm reuse: Create once, run multiple times for efficiency
"""

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Set environment variables, configure LLM model
# os.environ["LLM_MODEL_NAME"] = "YOUR_LLM_MODEL_NAME"
# os.environ["LLM_BASE_URL"] = "YOUR_LLM_BASE_URL"
# os.environ["LLM_API_KEY"] = "YOUR_LLM_API_KEY"


async def example_1_text_to_swarm():
    """
    Example 1: Basic text_to_swarm usage
    
    Generate a reusable Swarm from natural language description,
    then use it for multiple different queries.
    """
    print("\n" + "="*70)
    print("Example 1: Generate Reusable Swarm with text_to_swarm")
    print("="*70)
    
    from aworld.runner import Runners
    
    # Step 1: Generate a reusable swarm from natural language
    query = """
    Create a stock analysis team with:
    - A data collector who can search for stock information
    - An analyst who can analyze financial data
    - A risk assessor who evaluates investment risks
    
    They should work in a workflow: collector -> analyst -> risk assessor
    """
    
    print(f"\n📋 Team Description:\n{query.strip()}")
    print("\n🔄 Generating reusable Swarm...")
    
    swarm = await Runners.text_to_swarm(
        query=query,
        # Optional: specify skills directory if you have custom skills
        # skills_path=Path(__file__).parent.parent.parent / "skill_agent" / "skills"
    )
    
    print(f"✅ Swarm created: type={swarm.build_type}, agents={len(swarm.agents)}")
    print(f"   Agents: {[agent.id() for id, agent in swarm.agents.items()]}")


async def example_2_with_predefined_agents():
    """
    Example 2: text_to_swarm with predefined agents
    
    Provide custom agents with specific capabilities (e.g., special tools, MCP servers).
    SwarmComposerAgent will decide whether to use them based on the query.
    """
    print("\n" + "="*70)
    print("Example 2: text_to_swarm with Predefined Agents")
    print("="*70)
    
    from aworld.runner import Runners
    from aworld.agents.llm_agent import Agent
    from aworld.config import AgentConfig, ModelConfig
    
    # Create a specialized search agent with Tavily MCP server
    search_agent = Agent(
        name="WebSearchAgent",
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
                        "TAVILY_API_KEY": os.getenv("TAVILY_API_KEY", ""),
                        "NODE_ENV": "production"
                    }
                }
            }
        }
    )
    
    query = """
    Create a research team that can search the web for latest AI news 
    and analyze the trends. The team should have a searcher and an analyst.
    """
    
    print(f"\n📋 Query: {query.strip()}")
    print(f"\n📦 Providing predefined agent: {search_agent.name}")
    
    # Generate swarm with predefined agents
    swarm = await Runners.text_to_swarm(
        query=query,
        available_agents={
            "web_search_agent": search_agent  # Make it available for selection
        }
    )
    
    print(f"\n✅ Swarm created with {len(swarm.agents)} agents")


async def example_3_with_available_tools():
    """
    Example 3: text_to_swarm with available tools specification
    
    Specify which tools are available for the generated agents to use.
    """
    print("\n" + "="*70)
    print("Example 3: text_to_swarm with Available Tools")
    print("="*70)
    
    from aworld.runner import Runners
    
    query = "Create a data analysis team that can perform calculations"
    
    print(f"\n📋 Query: {query}")
    print("\n🔧 Available tools: ['calculator', 'python_repl']")
    
    swarm = await Runners.text_to_swarm(
        query=query,
        available_tools=['calculator', 'python_repl']  # Limit to specific tools
    )
    
    print(f"\n✅ Swarm created with {len(swarm.agents)} agents")


async def main():
    """
    Main function to run all examples.
    
    You can comment out examples you don't want to run.
    """
    print("\n" + "█"*70)
    print(" Text-to-Swarm and Text-to-Run Examples")
    print("█"*70)
    
    # Run examples
    # Comment out the ones you don't want to run
    
    # Example 1: Generate reusable swarm and use it for multiple tasks
    await example_1_text_to_swarm()

    # Example 2: Provide predefined agents with special capabilities
    # Note: Requires TAVILY_API_KEY environment variable
    # await example_2_with_predefined_agents()
    
    # Example 3: Specify available tools
    # await example_3_with_available_tools()
    
    print("\n" + "█"*70)
    print(" All Examples Completed!")
    print("█"*70 + "\n")


if __name__ == '__main__':
    asyncio.run(main())
