"""
PE Pattern (Plan-Execute) Multi-Agent System example for aworld-cli.

This demonstrates how to create a Multi-Agent System (MAS) using the PE (Plan-Execute) pattern,
where multiple specialized agents collaborate to handle complex tasks through a structured workflow.
This is a true multi-agent system with distinct agents working together, not a single agent with multiple skills.
"""
from aworld_cli.core import agent
from aworld.core.agent.swarm import Swarm, TeamSwarm
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
import os


@agent(
    name="PE Team Agent",
    desc="A Multi-Agent System (MAS) that handles complex tasks through the PE (Plan-Execute) pattern with specialized collaborating agents"
)
def build_swarm():
    """
    Build a Multi-Agent System (MAS) with specialized agents working together in the PE (Plan-Execute) pattern.
    
    This example demonstrates how to create a collaborative Multi-Agent System where distinct agents
    work together to solve complex tasks:
    - **Planner Agent**: Breaks down complex tasks into manageable steps and creates execution plans
    - **Executor Agent**: Executes the planned tasks efficiently and accurately
    - **Reviewer Agent**: Validates the quality and correctness of the execution results
    
    The PE (Plan-Execute) pattern is particularly useful for complex tasks that require:
    - Task decomposition and planning
    - Step-by-step execution
    - Quality assurance and validation
    
    Each agent in this system has its own specialized role and system prompt, enabling
    true agent collaboration and division of labor.
    
    Returns:
        TeamSwarm: A TeamSwarm instance containing specialized agents forming a Multi-Agent System
    """
    # Create shared configuration
    base_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=0.7
        )
    )
    
    # Create planner agent - specializes in task planning and decomposition
    planner_agent = Agent(
        name="planner",
        desc="Agent specialized in task planning and decomposition",
        conf=base_config,
        system_prompt="You are a planning agent. Your role is to break down complex tasks into smaller, manageable steps."
    )
    
    # Create executor agent - specializes in task execution
    executor_agent = Agent(
        name="executor",
        desc="Agent specialized in task execution",
        conf=base_config,
        system_prompt="You are an execution agent. Your role is to execute tasks efficiently and accurately."
    )
    
    # Create reviewer agent - specializes in quality review and validation
    reviewer_agent = Agent(
        name="reviewer",
        desc="Agent specialized in quality review and validation",
        conf=base_config,
        system_prompt="You are a review agent. Your role is to review and validate the work done by other agents."
    )
    
    # Create TeamSwarm with multiple agents
    # TeamSwarm provides better coordination for multi-agent scenarios
    return TeamSwarm(planner_agent, executor_agent, reviewer_agent)
    
    # Alternative: Use regular Swarm
    # return Swarm(planner_agent, executor_agent, reviewer_agent)

