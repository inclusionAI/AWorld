"""
Aworld Agent - A versatile AI agent that can execute tasks directly or delegate to agent teams.

This agent supports:
1. Direct task execution: Handle tasks directly using available tools and skills
2. Agent team delegation: Create and delegate tasks to specialized agent teams when needed

Role: Aworld - A versatile AI assistant capable of solving any task through direct execution
or coordinated multi-agent collaboration.
"""
import os
import sys
from typing import Optional, List

from aworld.core.context.amni import AgentContextConfig
from aworld.core.context.amni.config import get_default_config, ContextEnvConfig
from aworld.experimental.cast.tools import CAST_ANALYSIS, CAST_CODER
from aworld.logs.util import logger
from aworld_cli.core.agent_registry_tool import AGENT_REGISTRY

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm, Swarm
from aworld.core.agent.base import BaseAgent
from aworld_cli.core import agent, LocalAgentRegistry
from aworld_cli.core.loader import init_agents
from aworld_cli.core.agent_scanner import global_agent_registry
import asyncio
from aworld.config import AgentConfig, ModelConfig
# for skills use
CAST_ANALYSIS, CAST_CODER, AGENT_REGISTRY

from datetime import datetime
from zoneinfo import ZoneInfo

def _build_beijing_date_line() -> str:
    """Return a line stating today's Beijing date in Chinese format."""
    beijing_now = datetime.now(ZoneInfo("Asia/Shanghai"))

    return f"Today is {beijing_now.year} (year)-{beijing_now.month} (month)-{beijing_now.day}(day)."


# System prompt based on orchestrator_agent prompt
aworld_system_prompt = """
You are AWorld, a versatile AI assistant designed to solve any task presented by users.

Today is {{current_date}}, {{current_datetime}} (Beijing time). Your own knowledge has a cutoff in 2024, please keep in mind!

## 1. Role & Identity
You are AWorldAgent, a sophisticated AI agent acting as a central coordinator. Your primary role is to understand complex user requests and orchestrate a solution by dispatching tasks to a suite of specialized assistants (tools). You do not solve tasks directly; you manage the workflow.

## 2. Core Operational Workflow
You must tackle every user request by following this iterative, step-by-step process:
1.  **Analyze & Decompose:** Break down the user's complex request into a sequence of smaller, manageable sub-tasks.
2.  **Select & Execute:** For the immediate sub-task, select **one and only one** assistant (tool) best suited to complete it.
3.  **Report & Plan:** After the tool executes, clearly explain the results of that step and state your plan for the next action.
4.  **Iterate:** Repeat this process until the user's overall request is fully resolved.

## 3. Available Assistants/Tools
You are equipped with multiple assistants. It is your job to know which to use and when. Your key assistants include:
*   `text2agent`: a sub-agent that creates a new agent from a user's description.
*   `optimizer`: a sub-agent that optimizes an existing agent to better meet user requirements.
*   `explorer`: a sub-agent that can deeply analyze codebases like Github, by using terminal and professional code analysis tools.
*   `terminal_tool`: A tool set that can execute terminal commands.
*   `SKILL_tool`: A tool set that can activate, deactivate skills, and so on.

## 4. Available Skills
*    Please be aware that if you need to have access to a particular skill to help you to complete the task, you MUST use the appropriate `SKILL_tool` to activate the skill, which returns you the exact skill content.
*    You MUST NOT call the skill as a tool, since the skill is not a tool. You have to use the `SKILL_tool` to activate the skill.

## 5. Critical Guardrails
- **One Tool Per Step:** You **must** call only one tool at a time. Do not chain multiple tool calls in a single response.
- **True to Task:** While calling your assistant, you must pass the user's raw request/details to the assistant, without any modification.
- **Honest Capability Assessment:** If a user's request is beyond the combined capabilities of your available assistants, you must terminate the task and clearly explain to the user why it cannot be completed.
- **Working Directory:** Always treat the current directory as your working directory for all actions: run shell commands from it, and use it (or paths under it) for any temporary or output files when such operations are permitted (e.g. non-code tasks). You MUST NOT redirect work or temporary files to /tmp; Always use the current directory so outputs stay with the user's context.
- **Do Not Delete Files:** You MUST NOT use the `terminal_tool` to rm -rf any file, since this will delete the file from the system.
"""


def extract_agents_from_swarm(swarm: Swarm) -> List[BaseAgent]:
    """
    Extract all Agent instances from a Swarm.

    This function extracts agents from a Swarm in multiple ways:
    1. If swarm has agent_graph with agents dict, extract from there
    2. If swarm has agents property, extract from there
    3. If swarm has topology, extract agents from topology
    4. If swarm is a single Agent wrapped, extract the communicate_agent

    Args:
        swarm: The Swarm instance to extract agents from

    Returns:
        List of BaseAgent instances extracted from the swarm

    Example:
        >>> swarm = TeamSwarm(agent1, agent2, agent3)
        >>> agents = extract_agents_from_swarm(swarm)
        >>> print(f"Extracted {len(agents)} agents")
    """
    agents = []

    try:
        # Method 1: Try agent_graph.agents (most reliable after initialization)
        if hasattr(swarm, 'agent_graph') and swarm.agent_graph:
            if hasattr(swarm.agent_graph, 'agents') and swarm.agent_graph.agents:
                if isinstance(swarm.agent_graph.agents, dict):
                    agents.extend(swarm.agent_graph.agents.values())
                elif isinstance(swarm.agent_graph.agents, (list, tuple)):
                    agents.extend(swarm.agent_graph.agents)

        # Method 2: Try swarm.agents (direct access)
        if not agents and hasattr(swarm, 'agents') and swarm.agents:
            if isinstance(swarm.agents, dict):
                agents.extend(swarm.agents.values())
            elif isinstance(swarm.agents, (list, tuple)):
                agents.extend(swarm.agents)
            elif isinstance(swarm.agents, BaseAgent):
                agents.append(swarm.agents)

        # Method 3: Try topology (before initialization)
        if not agents and hasattr(swarm, 'topology') and swarm.topology:
            for item in swarm.topology:
                if isinstance(item, BaseAgent):
                    agents.append(item)
                elif isinstance(item, (list, tuple)):
                    # Handle tuple/list of agents
                    for sub_item in item:
                        if isinstance(sub_item, BaseAgent):
                            agents.append(sub_item)
                elif isinstance(item, Swarm):
                    # Recursively extract from nested swarm
                    nested_agents = extract_agents_from_swarm(item)
                    agents.extend(nested_agents)

        # Method 4: Try communicate_agent (root agent)
        if not agents and hasattr(swarm, 'communicate_agent') and swarm.communicate_agent:
            if isinstance(swarm.communicate_agent, BaseAgent):
                agents.append(swarm.communicate_agent)
            elif isinstance(swarm.communicate_agent, (list, tuple)):
                agents.extend([a for a in swarm.communicate_agent if isinstance(a, BaseAgent)])

        # Remove duplicates based on agent id
        seen_ids = set()
        unique_agents = []
        for ag in agents:
            if isinstance(ag, BaseAgent):
                agent_id = ag.id() if hasattr(ag, 'id') else id(ag)
                if agent_id not in seen_ids:
                    seen_ids.add(agent_id)
                    unique_agents.append(ag)

        return unique_agents

    except Exception as e:
        logger.warning(f"⚠️ Failed to extract agents from swarm: {e}")
        return []


async def _load_agents_from_global_registry(exclude_names: List[str]) -> List[BaseAgent]:
    """
    Async helper function to load agents from global_agent_registry.
    
    Args:
        exclude_names: List of agent names to exclude
        
    Returns:
        List of BaseAgent instances loaded from global_agent_registry
    """
    registry_agents = []

    try:
        # Get all agent names from global_agent_registry
        agent_names = await global_agent_registry.list_as_source()
        logger.debug(f"📋 Found {len(agent_names)} agent(s) in global_agent_registry")

        for agent_name in agent_names:
            # Skip excluded agents
            if agent_name in exclude_names:
                logger.debug(f"⏭️ Skipping excluded agent from global_agent_registry: {agent_name}")
                continue

            try:
                # Load agent from global_agent_registry
                agent = await global_agent_registry.load_agent(agent_name)
                if agent and isinstance(agent, BaseAgent):
                    registry_agents.append(agent)
                    logger.debug(f"✅ Loaded agent '{agent_name}' from global_agent_registry")
                else:
                    logger.debug(
                        f"⚠️ Failed to load agent '{agent_name}' from global_agent_registry: agent is None or not BaseAgent")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load agent '{agent_name}' from global_agent_registry: {e}")
                continue

    except Exception as e:
        logger.warning(f"⚠️ Error listing agents from global_agent_registry: {e}")

    return registry_agents


def load_all_registered_agents(
        agents_dir: Optional[str] = None,
        exclude_names: Optional[List[str]] = None
) -> List[BaseAgent]:
    """
    Load all registered agents from global_agent_registry.

    This function:
    1. Initializes agents from the specified directory (or current directory) if needed
    2. Gets all registered agent names from global_agent_registry
    3. Loads each agent from the registry
    4. Returns a list of all loaded Agent instances

    Args:
        agents_dir: Directory to initialize agents from. If None, uses current working directory.
                   This is used to ensure agents are loaded into the registry before querying.
        exclude_names: List of agent names to exclude (e.g., ["Aworld"] to exclude self)

    Returns:
        List of BaseAgent instances from all registered agents in global_agent_registry

    Example:
        >>> agents = load_all_registered_agents(exclude_names=["Aworld"])
        >>> print(f"Loaded {len(agents)} sub-agents")
    """
    if exclude_names is None:
        exclude_names = []

    logger.info(f"🔄 Starting to load registered agents (exclude: {exclude_names if exclude_names else 'none'})")

    # Initialize agents from directory if provided
    if agents_dir:
        logger.info(f"📁 Initializing agents from directory: {agents_dir}")
        try:
            init_agents(agents_dir)
            logger.info(f"✅ Successfully initialized agents from {agents_dir}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize agents from {agents_dir}: {e}")
    else:
        # Try to initialize from current working directory
        logger.info(f"📁 Attempting to initialize agents from current working directory")
        try:
            init_agents()
            logger.info(f"✅ Successfully initialized agents from current directory")
        except Exception as e:
            logger.debug(f"ℹ️ Could not initialize agents from current directory: {e} (this is usually fine)")

    # Get all registered agents
    registered_agents = LocalAgentRegistry.list_agents()
    logger.info(f"📋 Found {len(registered_agents)} registered agent(s) in LocalAgentRegistry {registered_agents}")
    
    all_agent_instances = []
    skipped_count = 0
    failed_count = 0
    no_swarm_count = 0
    empty_swarm_count = 0
    success_count = 0
    
    for local_agent in registered_agents:
        # Skip excluded agents
        if local_agent.name in exclude_names:
            logger.debug(f"⏭️ Skipping excluded agent: {local_agent.name}")
            skipped_count += 1
            continue
        
        logger.debug(f"🔍 Processing agent: {local_agent.name}")
        try:
            # Try to get swarm without context first
            swarm = None
            swarm_type = None
            swarm_id = 'N/A'
            swarm_name = 'N/A'
            try:
                # For sync callables or direct instances
                if isinstance(local_agent.swarm, Swarm):
                    swarm = local_agent.swarm
                    swarm_type = "Swarm instance"
                    swarm_id = swarm.id() if hasattr(swarm, 'id') else 'N/A'
                    swarm_name = swarm.name() if hasattr(swarm, 'name') else 'N/A'
                    logger.debug(f"  ✓ Found Swarm instance for {local_agent.name} [Swarm ID: {swarm_id}, Swarm Name: {swarm_name}]")
                elif callable(local_agent.swarm):
                    # Try calling without context
                    import inspect
                    sig = inspect.signature(local_agent.swarm)
                    if len(sig.parameters) == 0:
                        swarm = local_agent.swarm()
                        swarm_type = "callable (no params)"
                        swarm_id = swarm.id() if hasattr(swarm, 'id') else 'N/A'
                        swarm_name = swarm.name() if hasattr(swarm, 'name') else 'N/A'
                        logger.debug(f"  ✓ Created Swarm from callable (no params) for {local_agent.name} [Swarm ID: {swarm_id}, Swarm Name: {swarm_name}]")
                    else:
                        swarm_type = "callable (requires context)"
                        logger.debug(f"  ℹ️ Swarm is callable but requires context for {local_agent.name}")
            except Exception as e:
                logger.debug(f"  ⚠️ Could not get swarm for {local_agent.name} without context: {e}")
            
            if swarm:
                # Extract agents from swarm
                extracted_agents = extract_agents_from_swarm(swarm)
                if extracted_agents:
                    # Get agent names and IDs
                    agent_info_list = []
                    for agent in extracted_agents:
                        agent_name = agent.name() if hasattr(agent, 'name') else str(type(agent).__name__)
                        agent_id = agent.id() if hasattr(agent, 'id') else 'N/A'
                        agent_info_list.append(f"{agent_name}[ID: {agent_id}]")
                    
                    all_agent_instances.extend(extracted_agents)
                    success_count += 1
                    logger.info(f"✅ Loaded {len(extracted_agents)} agent(s) from '{local_agent.name}' (swarm type: {swarm_type}, Swarm ID: {swarm_id}, Swarm Name: {swarm_name}):")
                    for agent_info in agent_info_list:
                        logger.info(f"   • {agent_info}")
                else:
                    logger.warning(f"⚠️ No agents extracted from '{local_agent.name}' swarm (swarm type: {swarm_type})")
                    empty_swarm_count += 1
            else:
                logger.debug(
                    f"⚠️ Could not get swarm for '{local_agent.name}' (swarm type: {swarm_type or 'unknown'}, may require context)")
                no_swarm_count += 1

        except Exception as e:
            logger.warning(f"❌ Failed to load agents from '{local_agent.name}': {e}")
            failed_count += 1
            continue

    # Load agents from global_agent_registry
    try:
        logger.info(f"🔄 Loading agents from global_agent_registry...")
        # Get list of all agent names from global_agent_registry
        try:
            # Try to get existing event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, we need to use a different approach
                    # Use asyncio.create_task or run in a new thread
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(asyncio.run, _load_agents_from_global_registry(exclude_names))
                        registry_agents = future.result(timeout=30)
                else:
                    registry_agents = loop.run_until_complete(_load_agents_from_global_registry(exclude_names))
            except RuntimeError:
                # No event loop exists, create a new one
                registry_agents = asyncio.run(_load_agents_from_global_registry(exclude_names))
        except Exception as e:
            logger.warning(f"⚠️ Failed to load agents from global_agent_registry: {e}")
            registry_agents = []
        
        if registry_agents:
            all_agent_instances.extend(registry_agents)
            logger.info(f"✅ Loaded {len(registry_agents)} agent(s) from global_agent_registry")
            for agent in registry_agents:
                agent_name = agent.name() if hasattr(agent, 'name') else str(type(agent).__name__)
                agent_id = agent.id() if hasattr(agent, 'id') else 'N/A'
                logger.info(f"   • {agent_name} [ID: {agent_id}]")
    except Exception as e:
        logger.warning(f"⚠️ Error loading agents from global_agent_registry: {e}")

    # Summary log
    logger.info(f"📊 Load summary:")
    logger.info(f"   • Total registered agents: {len(registered_agents)}")
    logger.info(f"   • Skipped (excluded): {skipped_count}")
    logger.info(f"   • Successfully loaded: {len(all_agent_instances)} sub-agent(s) from {success_count} agent(s)")

    # List all loaded agent instances with their IDs
    if all_agent_instances:
        logger.info(f"   • Loaded agent instances:")
        for agent in all_agent_instances:
            agent_name = agent.name() if hasattr(agent, 'name') else str(type(agent).__name__)
            agent_id = agent.id() if hasattr(agent, 'id') else 'N/A'
            logger.info(f"     - {agent_name} [ID: {agent_id}]")

    if no_swarm_count > 0:
        logger.info(f"   • No swarm available: {no_swarm_count}")
    if empty_swarm_count > 0:
        logger.info(f"   • Empty swarms: {empty_swarm_count}")
    if failed_count > 0:
        logger.warning(f"   • Failed to load: {failed_count}")

    return all_agent_instances


def build_context_config(debug_mode):
    config = get_default_config()
    config.debug_mode = debug_mode
    config.agent_config = AgentContextConfig(
        enable_system_prompt_augment=True,
        neuron_names=["skills"],
        history_scope='session'
    )
    config.env_config = ContextEnvConfig()
    return config


@agent(
    name="Aworld",
    desc="Aworld is a versatile AI assistant that can execute tasks directly or delegate to specialized agent teams. Use when you need: (1) General-purpose task execution, (2) Complex multi-step problem solving, (3) Coordination of specialized agent teams, (4) Adaptive task handling that switches between direct execution and team delegation",
    context_config=build_context_config(
        debug_mode=True,
    ),
    unique=True
)
def build_aworld_agent(include_skills: Optional[str] = None):
    """
    Build the Aworld agent with integrated capabilities for direct execution and team delegation.

    This agent is equipped with:
    - Comprehensive tool access for direct task execution
    - Agent team delegation capabilities
    - Multiple skills for various task types
    - Adaptive execution strategy (direct vs. team-based)

    The agent can:
    1. Execute tasks directly using available tools and skills
    2. Delegate complex tasks to specialized agent teams
    3. Coordinate multi-agent workflows when needed
    4. Adapt execution strategy based on task complexity

    Args:
        include_skills (str, optional): Specify which skills to include.
            - Comma-separated list: "notify,bash" (exact match for each name)
            - Regex pattern: "notify.*" (pattern match)
            - If None, uses INCLUDE_SKILLS environment variable or loads all skills

    Returns:
        TeamSwarm: A TeamSwarm instance containing the Aworld agent

    Example:
        >>> agent = build_aworld_agent()
        >>> # Agent can execute tasks directly or delegate to teams
    """
    from pathlib import Path

    from aworld_cli.core.skill_registry import collect_plugin_and_user_skills

    plugin_base_dir = Path(__file__).resolve().parents[1]
    ALL_SKILLS = collect_plugin_and_user_skills(plugin_base_dir)

    # Configure agent: provider/base_url use getenv defaults; model_name/api_key may be None (ModelConfig accepts Optional[str])
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.1")),
            params={"max_completion_tokens": 1024000}
        ),
        use_vision=False,  # Enable if needed for image analysis
        skill_configs=ALL_SKILLS
    )

    # Create the Aworld agent
    aworld_agent = Agent(
        name="Aworld",
        desc="Aworld - A versatile AI assistant capable of executing tasks directly or delegating to agent teams",
        conf=agent_config,
        system_prompt=aworld_system_prompt,
        mcp_servers=["terminal"],
        mcp_config={
            "mcpServers": {
                "terminal": {
                    "command": "python",
                    "args": ["-m", "examples.gaia.mcp_collections.tools.terminal"],
                    "env": {},
                    "client_session_timeout_seconds": 9999.0,
                }
            }
        }
    )

    # Load all registered agents as sub-agents
    try:
        # Try to load from current working directory first
        sub_agents = load_all_registered_agents(
            agents_dir=None,  # Use default (current directory)
            exclude_names=["Aworld"]  # Exclude self to avoid circular reference
        )

        if sub_agents:
            logger.info(f"🤝 Adding {len(sub_agents)} sub-agent(s) to Aworld TeamSwarm")
            # Create TeamSwarm with Aworld as leader and all other agents as sub-agents
            return TeamSwarm(aworld_agent, *sub_agents, max_steps=100)
        else:
            logger.info("ℹ️ No sub-agents found, creating Aworld TeamSwarm without sub-agents")
            return TeamSwarm(aworld_agent)
    except Exception as e:

        logger.warning(f"⚠️ Failed to load sub-agents: {e}, creating Aworld TeamSwarm without sub-agents")
        return TeamSwarm(aworld_agent)
