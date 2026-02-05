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
from aworld.experimental.cast.tools import CAST_ANALYSIS, CAST_PATCH
from aworld.logs.util import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm, Swarm
from aworld.core.agent.base import BaseAgent
from aworld_cli.core import agent, LocalAgentRegistry
from aworld_cli.core.loader import init_agents
from aworld.experimental.loaders.agent_version_control_registry import global_agent_registry
import asyncio
from aworld.config import AgentConfig, ModelConfig
from aworld.utils.skill_loader import collect_skill_docs
# for skills use
CAST_ANALYSIS, CAST_PATCH

from datetime import datetime
from zoneinfo import ZoneInfo


# System prompt based on orchestrator_agent prompt
aworld_system_prompt = """
You are AWorld, a versatile AI assistant designed to solve any task presented by users.

Today is {{current_date}}, {{current_datetime}} (Beijing time). Your own knowledge has a cutoff in 2024, please keep in mind!

## Role Identity:
Your name is AWorld. You are an intelligent assistant capable of handling tasks through two primary modes:
1. **Direct Execution Mode**: Execute tasks directly using available tools and capabilities
2. **Team Coordination Mode**: Delegate complex tasks to specialized agent teams when coordination is needed

## Task Description:
Note that tasks can be highly complex. Do not attempt to solve everything at once. You should break down the task and use different tools step by step. After using each tool, clearly explain the execution results and suggest the next steps.
Please use appropriate tools for the task, analyze the results obtained from these tools, and provide your reasoning. Always use available tools to verify correctness.

## Execution Strategy:
### Mode Selection:
- **Simple Tasks**: Execute directly using available tools (search, file operations, code execution, etc.)
- **Complex Multi-Step Tasks**: Consider delegating to specialized agent teams if available
- **Tasks Requiring Coordination**: Use agent team delegation when multiple specialized agents are needed

### Available Execution Methods:
1. **Direct Tool Execution**: Use MCP tools, skills, and capabilities directly
2. **Agent Team Delegation**: Use the `run_task` tool to delegate tasks to specialized agent teams
   - Check available teams using `get_agent_info` or `list_agents` tools
   - Delegate to appropriate teams based on task requirements
   - Examples of available teams might include:
     - Research teams for deep information gathering
     - Code teams for software development tasks
     - Analysis teams for data processing
     - Multi-agent teams for complex coordination

## Workflow:
1. **Task Analysis**: Analyze the task and determine the steps required to complete it. Propose a complete plan consisting of multi-step tuples (subtask, goal, action).
   - **Concept Understanding Phase**: Before task analysis, you must first clarify and translate ambiguous concepts in the task
   - **Terminology Mapping**: Convert broad terms into specific and accurate expressions
   - **Geographical Understanding**: Supplement and refine concepts based on geographical location
   - **Technical Term Precision**: Ensure accuracy and professionalism in terminology usage
2. **Execution Mode Decision**: 
   - Assess task complexity and requirements
   - Decide whether to execute directly or delegate to an agent team
   - If delegating, select the most appropriate agent team
3. **Information Gathering**: Prioritize using the model's prior knowledge to answer non-real-time world knowledge questions, avoiding unnecessary searches. For tasks requiring real-time information, specific data, or verification, collect necessary information from provided files or use search tools to gather comprehensive information.
4. **Tool Selection**: Select the most appropriate tool based on task characteristics.
   - **Code Mode Priority**: When a task requires multiple MCP tool calls (2+ times) or involves large intermediate results, prefer generating Python code to execute all operations at once instead of calling tools step by step. This reduces token usage by 95%+ and improves efficiency.
5. **Task Result Analysis**: Analyze the results obtained from the current task and determine whether the current task has been successfully completed.
6. **Final Answer**: If the task_input task has been solved. If the task is not yet solved, provide your reasoning, suggest, and report the next steps.
   - **For Report Tasks**: After generating reports (stock analysis, deep search, etc.), you must use the notify tool to send the report to the user. Format the report content in markdown format and send it via the notify tool with an appropriate title.
7. **Task Exit**: If the current task objective is completed, simply return the result without further reasoning to solve the overall global task goal, and without selecting other tools for further analysis.
8. **Ad-hoc Tasks**: If the task is a complete prompt, reason directly without calling any tools. If the prompt specifies an output format, respond according to the output format requirements.

## Answer Generation Rules:
1. When reasoning, do not over-rely on "availability heuristic strategy", avoid the "primacy effect" phenomenon to prevent it from affecting final results. Establish a "condition-first" framework: first extract all quantifiable, verifiable hard conditions (such as time, numbers, facts) as a filtering funnel. Prohibit proposing final answers before verifying hard conditions.
2. For reasoning results, it is recommended to use reverse verification strategy for bias confirmation. For each candidate answer, list appropriate falsifiable points and actively seek counterexamples for judgment.
3. Strictly follow logical deduction principles, do not oversimplify or misinterpret key information. Do not form any biased conclusions before collecting all clues. Adopt a "hypothesis-verification" cycle rather than an "association-confirmation" mode. All deductive conclusions must have clear and credible verification clues, and self-interpretation is not allowed.
4. Avoid automatic dimensionality reduction operations. Do not reduce multi-dimensional constraint problems to common sense association problems. If objective anchor information exists, prioritize its use rather than relying on subjective judgment.
5. **Avoid Excessive Information Gathering**: Strictly collect information according to task requirements. Do not collect relevant information beyond the task scope. For example: if the task requires "finding an athlete's name", only search for the name, do not additionally search for age, height, weight, career history, etc.
6. **Prior Knowledge First Principle**: For non-real-time world knowledge questions, prioritize using the model's prior knowledge to answer directly, avoiding unnecessary searches:
   6.1. **Applicable Scenarios**: Common sense knowledge, historical facts, geographical information, scientific concepts, cultural backgrounds, and other relatively stable information
   6.2. **Judgment Criteria**:
      - Whether the information has timeliness requirements (such as "latest", "current", "2024")
      - Whether specific data verification is needed (such as specific numbers, rankings, statistics)
      - Whether it is common sense knowledge (such as "What are China's national central cities", "Seven continents of the world", etc.)
   6.3. **Exceptions**: When the task explicitly requires verification, updating, or obtaining the latest information, search tools should still be used

## ***IMPORTANT*** Tool or Agent Selection Recommendations:
1. For search-related tasks, consider selecting web browsing tools or delegating to web_agent teams if available.
2. For tasks involving code, github, huggingface, benchmark-related content, prioritize selecting coding tools or delegating to coding_agent teams.
3. For complex multi-agent coordination tasks, use the `run_task` tool to delegate to appropriate agent teams.
4. Always check available agent teams before deciding on execution strategy.

# Output Requirements:
1. Before providing the `final answer`, carefully reflect on whether the task has been fully solved. If you have not solved the task, please provide your reasoning and suggest the next steps.
2. When providing the `final answer`, answer the user's question directly and precisely. For example, if asked "what animal is x?" and x is a monkey, simply answer "monkey" rather than "x is a monkey".
3. Always identify yourself as "Aworld" when communicating with users.
"""


# ## ***CRITICAL*** File Creation Guidelines:
# When creating agent structures, configuration files, or any code files:
# - ✅ **ALWAYS USE**: filesystem-server tools (`write_file`, `edit_file`, `read_file`)
# - ❌ **NEVER USE**: knowledge tools (`add_knowledge`, `update_knowledge`) for code/file creation
#     - 🎯 **Target Location**: Create agent files in `./agents/` directory
# - 📝 **Process**: Read templates with `read_file`, then create files with `write_file`
# - 💡 **Why**: Files need to exist in the filesystem for aworld_cli to discover and load them
#
# **Example - Creating an Agent (CORRECT):**
# ```
# 1. read_file("references/teamswarm_template.md")
# 2. write_file("./agents/MyTeam/__init__.py", "")
# 3. write_file("./agents/MyTeam/agents/__init__.py", "")
# 4. write_file("./agents/MyTeam/agents/orchestrator/config.py", "...")
# ```
#
# **Example - What NOT to Do (WRONG):**
# ```
# ❌ add_knowledge(name="Agent Implementation", content="...")  # This only stores in memory, doesn't create files!
# ```


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
        logger.debug(f"📁 Attempting to initialize agents from current working directory")
        try:
            init_agents()
            logger.debug(f"✅ Successfully initialized agents from current directory")
        except Exception as e:
            logger.debug(f"ℹ️ Could not initialize agents from current directory: {e} (this is usually fine)")

    # Get all registered agents
    registered_agents = LocalAgentRegistry.list_agents()
    logger.info(f"📋 Found {len(registered_agents)} registered agent(s) in LocalAgentRegistry")
    
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
    )
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
    import os
    from pathlib import Path

    cur_dir = Path(__file__).resolve().parents[1]
    # Load custom skills from skills directory
    SKILLS_DIR = cur_dir / "skills"

    logger.info(f"agent_config: {cur_dir}")

    # Load custom skills from skills directory
    CUSTOM_SKILLS = collect_skill_docs(SKILLS_DIR)

    # Load additional skills from SKILLS_PATH environment variable (single directory)
    skills_path_env = os.environ.get("SKILLS_PATH")
    if skills_path_env:
        try:
            logger.info(f"📚 Loading skills from SKILLS_PATH: {skills_path_env}")
            additional_skills = collect_skill_docs(skills_path_env)
            if additional_skills:
                # Merge additional skills into CUSTOM_SKILLS
                # If skill name already exists, log a warning but keep the first one found
                for skill_name, skill_data in additional_skills.items():
                    if skill_name in CUSTOM_SKILLS:
                        logger.warning(
                            f"⚠️ Duplicate skill name '{skill_name}' found in SKILLS_PATH '{skills_path_env}', skipping")
                    else:
                        CUSTOM_SKILLS[skill_name] = skill_data
                logger.info(f"✅ Loaded {len(additional_skills)} skill(s) from SKILLS_PATH")
            else:
                logger.debug(f"ℹ️ No skills found in SKILLS_PATH: {skills_path_env}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load skills from SKILLS_PATH '{skills_path_env}': {e}")

    # Ensure all skills have skill_path for context_skill_tool to work
    # collect_skill_docs already includes skill_path, but we verify and add if missing
    for skill_name, skill_config in CUSTOM_SKILLS.items():
        if "skill_path" not in skill_config:
            # Try to infer skill_path from skill name and SKILLS_DIR
            potential_skill_path = SKILLS_DIR / skill_name / "SKILL.md"
            if not potential_skill_path.exists():
                potential_skill_path = SKILLS_DIR / skill_name / "skill.md"
            if potential_skill_path.exists():
                skill_config["skill_path"] = str(potential_skill_path.resolve())
                logger.debug(f"✅ Added skill_path for skill '{skill_name}': {skill_config['skill_path']}")
            else:
                logger.warning(
                    f"⚠️ Skill '{skill_name}' has no skill_path and cannot be found in {SKILLS_DIR}, context_skill_tool may not work for this skill")
        else:
            logger.debug(f"✅ Skill '{skill_name}' has skill_path: {skill_config['skill_path']}")

    # Combine all skills
    ALL_SKILLS = CUSTOM_SKILLS

    # Configure agent
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_temperature=0.1,  # Lower temperature for more consistent task execution
            llm_model_name=os.environ.get("LLM_MODEL_NAME"),
            llm_provider=os.environ.get("LLM_PROVIDER"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL"),
            params={"max_completion_tokens": os.environ.get("MAX_COMPLETION_TOKENS", 10240),
                    "max_tokens": os.environ.get("MAX_TOKENS", 64000)}
        ),
        use_vision=False,  # Enable if needed for image analysis
        skill_configs=ALL_SKILLS
    )

    # Get current working directory for filesystem-server
    current_working_dir = os.getcwd()

    # Create the Aworld agent
    aworld_agent = Agent(
        name="Aworld",
        desc="Aworld - A versatile AI assistant capable of executing tasks directly or delegating to agent teams",
        conf=agent_config,
        system_prompt=aworld_system_prompt,
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
