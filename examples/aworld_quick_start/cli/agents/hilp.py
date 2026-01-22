"""
HUMAN-in-the-Loop (HITL) Agent example for aworld-cli.

This demonstrates how to create a single-agent Swarm with HUMAN-in-the-Loop capability using the @agent decorator.
The agent can autonomously request user input or confirmation when needed, enabling interactive and collaborative workflows.

This example is suitable for:
- Tasks requiring user confirmation or approval
- Scenarios where additional information from the user is needed
- Interactive Q&A and consultation tasks
- Operations that need human judgment or authorization
"""
from aworld_cli.core import agent
from aworld.core.agent.swarm import Swarm
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
import os

from aworld.tools.human.human import HUMAN


@agent(
    name="HILPAgent",
    desc="A HUMAN-in-the-Loop agent that can request user input or confirmation when needed"
)
def build_simple_swarm():
    """
    Build a HUMAN-in-the-Loop Swarm with a single agent.
    
    This agent includes the HUMAN tool, which enables it to:
    - Request user confirmation for actions that require authorization
    - Ask for additional information when user input is needed
    - Pause execution and wait for human judgment at critical decision points
    
    The function is decorated with @agent, which automatically registers the agent 
    with the LocalAgentRegistry when the module is imported.
    
    Use cases:
    - Interactive Q&A sessions where the agent may need to ask clarifying questions
    - Tasks requiring user approval (e.g., payment, login, privileged operations)
    - Information gathering scenarios where user input is essential
    - Collaborative workflows where human expertise is needed
    
    Returns:
        Swarm: A Swarm instance containing a single agent with HUMAN-in-the-Loop capability
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
    
    # Create the agent with HUMAN tool enabled for interactive user collaboration
    simple_agent = Agent(
        name="basic_agent",
        desc="A basic AI Agent with HUMAN-in-the-Loop capability for interactive tasks and Q&A",
        conf=agent_config,
        system_prompt="You are a helpful AI Agent. When the user provides insufficient information or when you need user confirmation, please use the HUMAN tool to request additional input or approval.",
        tool_names=[HUMAN]  # Enable HUMAN-in-the-Loop capability
    )
    
    # Return Swarm with the agent
    return Swarm(simple_agent)

