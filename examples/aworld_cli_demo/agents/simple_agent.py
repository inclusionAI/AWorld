"""
Basic Agent example for aworld-cli.

This demonstrates the minimal setup required to create a single-agent Swarm using the @agent decorator.
This is the simplest example to get started with aworld-cli, suitable for basic tasks and Q&A scenarios.
"""
from aworld.experimental.aworld_cli.core.registry import agent
from aworld.core.agent.swarm import Swarm
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
import os


@agent(
    name="BasicAgent",
    desc="A basic single-agent Swarm that can answer questions and perform simple tasks"
)
def build_simple_swarm():
    """
    Build a basic Swarm with a single agent.
    
    This is the simplest agent configuration example. The function is decorated with @agent,
    which automatically registers the agent with the LocalAgentRegistry when the module is imported.
    
    This agent is suitable for:
    - Simple Q&A tasks
    - Basic text processing
    - Straightforward problem-solving
    
    Returns:
        Swarm: A Swarm instance containing a single basic agent
    """
    # Create agent configuration
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7"))
        )
    )
    
    # Create the basic agent
    simple_agent = Agent(
        name="basic_agent",
        desc="Basic AI Agent for simple tasks and Q&A",
        conf=agent_config
    )
    
    # Return Swarm with the agent
    return Swarm(simple_agent)

