"""
Coordinator Agent Definition

The coordinator is the team leader responsible for:
- Receiving user tasks
- Decomposing tasks into subtasks
- Delegating subtasks to specialized subagents
- Aggregating results
"""

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from typing import Optional
import os


def create_coordinator(
    llm_provider: Optional[str] = None,
    llm_model_name: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    llm_base_url: Optional[str] = None
) -> Agent:
    """
    Create coordinator agent with subagent capability enabled.

    Args:
        llm_provider: LLM provider (openai, anthropic)
        llm_model_name: Model name (gpt-4o, claude-sonnet-4)
        llm_api_key: API key
        llm_base_url: Base URL for API

    Returns:
        Agent: Coordinator agent with subagent delegation capability
    """
    # Use environment variables as defaults
    llm_provider = llm_provider or os.getenv('LLM_PROVIDER', 'openai')
    llm_model_name = llm_model_name or os.getenv('LLM_MODEL_NAME', 'gpt-4o')
    llm_api_key = llm_api_key or os.getenv('LLM_API_KEY')
    llm_base_url = llm_base_url or os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')

    if not llm_api_key:
        raise ValueError("LLM_API_KEY is required. Set it in .env or pass as parameter.")

    # Create agent config
    conf = AgentConfig(
        llm_provider=llm_provider,
        llm_model_name=llm_model_name,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url
    )

    # Coordinator system prompt
    system_prompt = """
You are a Coordinator Agent leading a team of specialized subagents.

Your role:
1. Receive complex tasks from users
2. Analyze and decompose tasks into subtasks
3. Delegate subtasks to appropriate specialists using spawn_subagent tool
4. Aggregate and synthesize results
5. Provide comprehensive answers to users

Available Specialists:
- code_analyzer: Code structure analysis, design patterns, refactoring suggestions
- web_searcher: Web research, documentation search, best practices
- report_writer: Synthesize findings into professional reports

Task Decomposition Strategy:
1. Identify required expertise (code, research, writing)
2. Delegate focused subtasks with clear directives
3. Use specialists' outputs to build final answer
4. Ensure proper sequencing (e.g., analyze before reporting)

Example Delegation:
```
spawn_subagent(
    name="code_analyzer",
    directive="Analyze design patterns in aworld/core/agent/subagent_manager.py"
)
```

Guidelines:
- Delegate when specialist expertise is needed
- Provide clear, specific directives
- Synthesize specialist outputs into coherent answer
- Don't delegate simple tasks you can handle directly
"""

    # Create coordinator with subagent capability
    coordinator = Agent(
        name="coordinator",
        conf=conf,
        desc="Team coordinator with subagent delegation capability",
        system_prompt=system_prompt,
        tool_names=["spawn_subagent", "read_file", "write_file", "list_directory"],  # Explicitly add spawn_subagent
        enable_subagent=True,
        subagent_search_paths=["./agents"]  # Search for agent.md files
    )

    return coordinator


def create_team_members() -> list[Agent]:
    """
    Create specialized team member agents.

    These agents will be registered as available subagents
    in the TeamSwarm configuration.

    Returns:
        list[Agent]: List of specialized agents
    """
    from aworld.config.conf import AgentConfig

    # Use environment config for all members
    llm_provider = os.getenv('LLM_PROVIDER', 'openai')
    llm_model_name = os.getenv('LLM_MODEL_NAME', 'gpt-4o')
    llm_api_key = os.getenv('LLM_API_KEY')
    llm_base_url = os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')

    if not llm_api_key:
        raise ValueError("LLM_API_KEY is required")

    conf = AgentConfig(
        llm_provider=llm_provider,
        llm_model_name=llm_model_name,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url
    )

    # Code Analyzer
    code_analyzer = Agent(
        name="code_analyzer",
        conf=conf,
        desc="Expert in code analysis and design patterns",
        tool_names=["cast_analysis", "cast_search", "read_file"]
    )

    # Web Searcher
    web_searcher = Agent(
        name="web_searcher",
        conf=conf,
        desc="Expert in web research and documentation retrieval",
        tool_names=["web_search", "web_fetch", "read_file"]
    )

    # Report Writer
    report_writer = Agent(
        name="report_writer",
        conf=conf,
        desc="Expert in technical writing and report synthesis",
        tool_names=["write_file", "read_file"]
    )

    return [code_analyzer, web_searcher, report_writer]
