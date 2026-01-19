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
import logging

from aworld.tools.human.human import HUMAN

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm, Swarm
from aworld.core.agent.base import BaseAgent
from aworld_cli.core import agent
from aworld_cli.core.agent_registry import LocalAgentRegistry
from aworld_cli.core.loader import init_agents
from aworld.config import AgentConfig, ModelConfig
from aworld.utils.skill_loader import collect_skill_docs

logger = logging.getLogger(__name__)


# System prompt based on orchestrator_agent prompt
aworld_system_prompt = """
You are Aworld, a versatile AI assistant designed to solve any task presented by users.

## Role Identity:
Your name is Aworld. You are an intelligent assistant capable of handling tasks through two primary modes:
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
   - **Report Type Tasks**: If the task involves generating reports such as stock analysis (股票分析), deep search reports (深度检索报告), research reports, or any comprehensive analysis reports, you must directly send the final report to the user using the notify tool (dingtalk_notify skill) after completing the analysis. Do not just return the result in the conversation - use the notify tool to ensure the user receives the report via notification.

5. **Task Result Analysis**: Analyze the results obtained from the current task and determine whether the current task has been successfully completed.

6. **Final Answer**: If the task_input task has been solved. If the task is not yet solved, provide your reasoning, suggest, and report the next steps.
   - **For Report Tasks**: After generating reports (stock analysis, deep search, etc.), you must use the notify tool to send the report to the user. Format the report content in markdown format and send it via the notify tool with an appropriate title.

7. **Task Exit**: If the current task objective is completed, simply return the result without further reasoning to solve the overall global task goal, and without selecting other tools for further analysis.

8. **Ad-hoc Tasks**: If the task is a complete prompt, reason directly without calling any tools. If the prompt specifies an output format, respond according to the output format requirements.

## Answer Generation Rules:
1. When involving mathematical operations, date calculations, and other related tasks, strictly follow logical reasoning requirements. For example:
   - If it's yesterday, perform date minus 1 operation;
   - If it's the day before yesterday, perform date minus 2 operation;
   - If it's tomorrow, perform date plus 1 operation.

2. When reasoning, do not over-rely on "availability heuristic strategy", avoid the "primacy effect" phenomenon to prevent it from affecting final results. Establish a "condition-first" framework: first extract all quantifiable, verifiable hard conditions (such as time, numbers, facts) as a filtering funnel. Prohibit proposing final answers before verifying hard conditions.

3. For reasoning results, it is recommended to use reverse verification strategy for bias confirmation. For each candidate answer, list appropriate falsifiable points and actively seek counterexamples for judgment.

4. Strictly follow logical deduction principles, do not oversimplify or misinterpret key information. Do not form any biased conclusions before collecting all clues. Adopt a "hypothesis-verification" cycle rather than an "association-confirmation" mode. All deductive conclusions must have clear and credible verification clues, and self-interpretation is not allowed.

5. Avoid automatic dimensionality reduction operations. Do not reduce multi-dimensional constraint problems to common sense association problems. If objective anchor information exists, prioritize its use rather than relying on subjective judgment.

6. **Strictly Answer According to Task Requirements**: Do not add any extra conditions, do not self-explain, strictly judge according to the conditions set by the task (such as specified technical specifications, personnel position information):
   6.1. When a broad time range condition is set in the original conditions, converting the condition into a fixed time window for hard filtering is not allowed
   6.2. When the original conditions only require partial condition satisfaction, converting the conditions into stricter filtering conditions is not allowed. For example: only requiring participation in projects but converting to participation in all projects during execution is not allowed
   6.3. **Do Not Add Qualifiers Not Explicitly Mentioned in the Task**:
       - If the task does not specify status conditions like "completed", "in use", "built", do not add them in your answer
       - If the task does not specify quantity conditions like "ranking", "top few", do not add them in your answer
       - If the task does not specify classification conditions like "region", "type", do not add them in your answer
       - If the task does not specify authority conditions like "official", "formal", do not add them in your answer
   6.4. **Example Comparisons**:
       - ❌ Wrong: Task asks "highest peak", answer "highest climbed peak"
       - ❌ Wrong: Task asks "longest river", answer "longest among major rivers"
       - ❌ Wrong: Task asks "largest company", answer "largest among listed companies"
       - ✅ Correct: Task asks "highest peak", directly answer "highest peak"
       - ✅ Correct: Task asks "longest river", directly answer "longest river"
       - ✅ Correct: Task asks "largest company", directly answer "largest company"

7. **Avoid Excessive Information Gathering**: Strictly collect information according to task requirements. Do not collect relevant information beyond the task scope. For example: if the task requires "finding an athlete's name", only search for the name, do not additionally search for age, height, weight, career history, etc.

8. **Prior Knowledge First Principle**: For non-real-time world knowledge questions, prioritize using the model's prior knowledge to answer directly, avoiding unnecessary searches:
   8.1. **Applicable Scenarios**: Common sense knowledge, historical facts, geographical information, scientific concepts, cultural backgrounds, and other relatively stable information
   8.2. **Judgment Criteria**:
      - Whether the information has timeliness requirements (such as "latest", "current", "2024")
      - Whether specific data verification is needed (such as specific numbers, rankings, statistics)
      - Whether it is common sense knowledge (such as "What are China's national central cities", "Seven continents of the world", etc.)
   8.3. **Exceptions**: When the task explicitly requires verification, updating, or obtaining the latest information, search tools should still be used

9. **Progressive Search Optimization Principle**: When conducting multi-step search tasks, precise searches should be based on clues already obtained, avoiding repeated searches of known information:
   9.1. **Clue Inheritance**: When generating subsequent search tasks, you must refer to clues and limiting conditions obtained from previous searches, avoiding repeated searches of known information
   9.2. **Search Scope Precision**: Narrow search scope based on existing clues, for example:
      - If a specific region is identified, subsequent searches should focus on that region rather than global scope
      - If a specific category is identified, subsequent searches should focus on that category rather than all categories
      - If a specific time range is identified, subsequent searches should focus on that period rather than all time
   9.3. **Avoid Repeated Searches**: Do not re-search information already obtained. Instead, conduct more precise targeted searches based on existing information
   9.4. **Search Task Progression**: Each search task should be further refined based on previous task results, rather than starting over

## ***IMPORTANT*** Tool or Agent Selection Recommendations:
1. For search-related tasks, consider selecting web browsing tools or delegating to web_agent teams if available.
2. For tasks involving code, github, huggingface, benchmark-related content, prioritize selecting coding tools or delegating to coding_agent teams.
3. For complex multi-agent coordination tasks, use the `run_task` tool to delegate to appropriate agent teams.
4. Always check available agent teams before deciding on execution strategy.

## ***CRITICAL*** File Creation Guidelines:
When creating agent structures, configuration files, or any code files:
- ✅ **ALWAYS USE**: filesystem-server tools (`write_file`, `edit_file`, `read_file`)
- ❌ **NEVER USE**: knowledge tools (`add_knowledge`, `update_knowledge`) for code/file creation
- 🎯 **Target Location**: Create agent files in `./agents/` directory
- 📝 **Process**: Read templates with `read_file`, then create files with `write_file`
- 💡 **Why**: Files need to exist in the filesystem for aworld_cli to discover and load them

**Example - Creating an Agent (CORRECT):**
```
1. read_file("references/teamswarm_template.md")
2. write_file("./agents/MyTeam/__init__.py", "")
3. write_file("./agents/MyTeam/agents/__init__.py", "")
4. write_file("./agents/MyTeam/agents/orchestrator/config.py", "...")
```

**Example - What NOT to Do (WRONG):**
```
❌ add_knowledge(name="Agent Implementation", content="...")  # This only stores in memory, doesn't create files!
```

# Output Requirements:
1. Before providing the `final answer`, carefully reflect on whether the task has been fully solved. If you have not solved the task, please provide your reasoning and suggest the next steps.
2. When providing the `final answer`, answer the user's question directly and precisely. For example, if asked "what animal is x?" and x is a monkey, simply answer "monkey" rather than "x is a monkey".
3. Always identify yourself as "Aworld" when communicating with users.
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


def load_all_registered_agents(
    agents_dir: Optional[str] = None,
    exclude_names: Optional[List[str]] = None
) -> List[BaseAgent]:
    """
    Load all registered agents and extract their Agent instances.
    
    This function:
    1. Initializes agents from the specified directory (or current directory)
    2. Gets all registered LocalAgent instances
    3. Extracts Agent instances from each LocalAgent's swarm
    4. Returns a list of all extracted Agent instances
    
    Args:
        agents_dir: Directory to load agents from. If None, uses current working directory
        exclude_names: List of agent names to exclude (e.g., ["Aworld"] to exclude self)
        
    Returns:
        List of BaseAgent instances from all registered agents
        
    Example:
        >>> agents = load_all_registered_agents(exclude_names=["Aworld"])
        >>> print(f"Loaded {len(agents)} sub-agents")
    """
    if exclude_names is None:
        exclude_names = []
    
    # Initialize agents from directory if provided
    if agents_dir:
        try:
            init_agents(agents_dir)
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize agents from {agents_dir}: {e}")
    else:
        # Try to initialize from current working directory
        try:
            init_agents()
        except Exception as e:
            logger.debug(f"Could not initialize agents from current directory: {e}")
    
    # Get all registered agents
    registered_agents = LocalAgentRegistry.list_agents()
    
    all_agent_instances = []
    
    for local_agent in registered_agents:
        # Skip excluded agents
        if local_agent.name in exclude_names:
            logger.debug(f"⏭️ Skipping excluded agent: {local_agent.name}")
            continue
        
        try:
            # Try to get swarm without context first
            swarm = None
            try:
                # For sync callables or direct instances
                if isinstance(local_agent.swarm, Swarm):
                    swarm = local_agent.swarm
                elif callable(local_agent.swarm):
                    # Try calling without context
                    import inspect
                    sig = inspect.signature(local_agent.swarm)
                    if len(sig.parameters) == 0:
                        swarm = local_agent.swarm()
            except Exception as e:
                logger.debug(f"Could not get swarm for {local_agent.name} without context: {e}")
            
            if swarm:
                # Extract agents from swarm
                extracted_agents = extract_agents_from_swarm(swarm)
                if extracted_agents:
                    all_agent_instances.extend(extracted_agents)
                    logger.info(f"✅ Loaded {len(extracted_agents)} agent(s) from {local_agent.name}")
                else:
                    logger.warning(f"⚠️ No agents extracted from {local_agent.name}")
            else:
                logger.debug(f"⚠️ Could not get swarm for {local_agent.name} (may require context)")
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to load agents from {local_agent.name}: {e}")
            continue
    
    logger.info(f"📊 Total loaded {len(all_agent_instances)} sub-agent(s) from registered agents")
    return all_agent_instances


@agent(
    name="Aworld",
    desc="Aworld is a versatile AI assistant that can execute tasks directly or delegate to specialized agent teams. Use when you need: (1) General-purpose task execution, (2) Complex multi-step problem solving, (3) Coordination of specialized agent teams, (4) Adaptive task handling that switches between direct execution and team delegation"
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

    print(f"agent_config: {cur_dir}")


    # Support skill filtering via parameter or environment variable
    if include_skills is None:
        include_skills = os.environ.get("INCLUDE_SKILLS")

    CUSTOM_SKILLS = collect_skill_docs(SKILLS_DIR)

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
            params={"max_completion_tokens": os.environ.get("MAX_COMPLETION_TOKENS", 10240), "max_tokens": os.environ.get("MAX_TOKENS", 64000)}
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
        human_tools=[HUMAN]
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
            return TeamSwarm(aworld_agent, *sub_agents)
        else:
            logger.info("ℹ️ No sub-agents found, creating Aworld TeamSwarm without sub-agents")
            return TeamSwarm(aworld_agent)
    except Exception as e:



        logger.warning(f"⚠️ Failed to load sub-agents: {e}, creating Aworld TeamSwarm without sub-agents")
        return TeamSwarm(aworld_agent)
