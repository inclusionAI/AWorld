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
from aworld_cli.core.context_tool import CONTEXT_TOOL
from .audio.audio import build_audio_swarm
from .developer.developer import build_developer_swarm
from .evaluator.evaluator import build_evaluator_swarm
from .diffusion.diffusion import build_diffusion_swarm
import traceback

from .image.image import build_image_swarm

# Import SpawnSubagentTool to ensure it's registered in ToolFactory
from aworld.core.tool.builtin import SpawnSubagentTool

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm, Swarm
from aworld.core.agent.base import BaseAgent
from aworld_cli.core import agent

from aworld.config import AgentConfig, ModelConfig

# for skills use
CAST_ANALYSIS, CAST_CODER

global_agent_registry = None
AGENT_REGISTRY = None

from datetime import datetime
from zoneinfo import ZoneInfo


def _build_beijing_date_line() -> str:
    """Return a line stating today's Beijing date in Chinese format."""
    beijing_now = datetime.now(ZoneInfo("Asia/Shanghai"))

    return f"Today is {beijing_now.year} (year)-{beijing_now.month} (month)-{beijing_now.day}(day)."

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
    - FileSystemMemoryStore for persistent memory storage

    The agent can:
    1. Execute tasks directly using available tools and skills
    2. Delegate complex tasks to specialized agent teams
    3. Coordinate multi-agent workflows when needed
    4. Adapt execution strategy based on task complexity
    5. Persist conversation memory to filesystem via Sandbox

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
        >>> # Memory is persisted to filesystem automatically
    """

    from pathlib import Path
    from aworld.utils.skill_loader import collect_skill_docs

    # 收集 skills
    ALL_SKILLS = {}

    # 1. 从 plugin 目录收集
    plugin_base_dir = Path(__file__).resolve().parents[1]
    if plugin_base_dir.exists():
        try:
            plugin_skills = collect_skill_docs(plugin_base_dir)
            ALL_SKILLS.update(plugin_skills)
            logger.debug(f"✅ Loaded {len(plugin_skills)} skills from plugin directory")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load skills from plugin directory: {e}")

    # 2. 从用户目录收集（AWORLD_SKILLS_PATH 环境变量）
    user_dir = os.environ.get("AWORLD_SKILLS_PATH")  # semicolon-separated paths
    if user_dir:
        for dir_path in user_dir.split(";"):
            dir_path = dir_path.strip()
            if dir_path and Path(dir_path).exists():
                try:
                    user_skills = collect_skill_docs(Path(dir_path))
                    ALL_SKILLS.update(user_skills)
                    logger.debug(f"✅ Loaded {len(user_skills)} skills from user directory: {dir_path}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to load skills from {dir_path}: {e}")

    # Configure agent: provider/base_url use getenv defaults; model_name/api_key may be None (ModelConfig accepts Optional[str])
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.1")),
            params={"max_completion_tokens": 64000},
            llm_stream_call=os.environ.get("STREAM", "0").lower() in ("1", "true", "yes")
        ),
        use_vision=False,  # Enable if needed for image analysis
        skill_configs=ALL_SKILLS
    )

    # Create sandbox with builtin filesystem and terminal tools (Phase 1)
    from aworld.sandbox import Sandbox

    mcp_config = {
        "mcpServers": {
            "terminal": {
                "command": sys.executable,
                "args": ["-m", "examples.gaia.mcp_collections.tools.terminal"],
                "env": {},
                "client_session_timeout_seconds": 9999.0,
            }
        }
    }

    sandbox = Sandbox(
        mcp_config=mcp_config,
        builtin_tools=["filesystem", "terminal"],  # Phase 1: Expose filesystem tools
        workspaces=[os.getcwd()]  # Allow current working directory
    )
    sandbox.reuse = True

    # Create the Aworld agent with filesystem and terminal tools enabled
    # Note: Aworld is a coordinator with lightweight tool access for information gathering
    # Complex development tasks are delegated to sub-agents (e.g., Developer)
    aworld_agent = Agent(
        name="Aworld",
        desc="Aworld - A versatile AI assistant capable of executing tasks directly or delegating to agent teams",
        conf=agent_config,
        system_prompt=(Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8"),
        mcp_servers=["terminal"],  # Enable terminal for information gathering (curl, wget, etc.)
        sandbox=sandbox,  # Shared sandbox (tools filtered by agent's mcp_servers config)
        tool_names=[
            CONTEXT_TOOL,      # Core: Context management
            'CAST_SEARCH',     # Core: Lightweight code search
            'async_spawn_subagent',  # Core: Dynamic subagent delegation (AsyncTool, needs async_ prefix)
            'cron',            # Core: Scheduled task management
        ],
        enable_subagent=True,  # Enable subagent capability (Aworld-specific default)
    )

    # Directly instantiate developer, evaluator, and diffusion as sub-agents
    # Pass shared sandbox to enable resource sharing while maintaining tool access control
    try:
        developer_swarm = build_developer_swarm(sandbox=sandbox)  # ✅ Share sandbox
        evaluator_swarm = build_evaluator_swarm()  # TODO: Add sandbox parameter
        diffusion_swarm = build_diffusion_swarm()  # TODO: Add sandbox parameter
        audio_swarm = build_audio_swarm()  # TODO: Add sandbox parameter
        image_swarm = build_image_swarm()
        sub_agents = (
            extract_agents_from_swarm(developer_swarm)
            + extract_agents_from_swarm(evaluator_swarm)
            + extract_agents_from_swarm(diffusion_swarm)
            + extract_agents_from_swarm(audio_swarm)
            + extract_agents_from_swarm(image_swarm)
        )

        if sub_agents:
            logger.info(f"🤝 Adding {len(sub_agents)} sub-agent(s) to Aworld TeamSwarm (developer, evaluator, diffusion)")
            return TeamSwarm(aworld_agent, *sub_agents, max_steps=100)
        else:
            logger.info("ℹ️ No sub-agents extracted, creating Aworld TeamSwarm without sub-agents")
            return TeamSwarm(aworld_agent)
    except Exception as e:
        logger.warning(f"⚠️ Failed to instantiate sub-agents: {e}, creating Aworld TeamSwarm without sub-agents {traceback.format_exc()}")
        return TeamSwarm(aworld_agent)
