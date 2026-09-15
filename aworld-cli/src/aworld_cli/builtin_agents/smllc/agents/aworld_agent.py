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
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Callable, Optional, List, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aworld.core.context.amni import AgentContextConfig
from aworld.core.context.amni.config import get_default_config, ContextEnvConfig
from aworld.core.context.amni.prompt.assembly.budget import PromptBudgetPolicy
from aworld.core.tool.surface import ToolLifecycle, ToolSurfaceProfile
from aworld.logs.util import logger
from aworld_cli.core.context_tool import CONTEXT_TOOL
from aworld_cli.core.skill_registry import build_skill_resolver_inputs
from .audio.audio import build_audio_swarm
from .avatar.avatar import build_avatar_swarm
from .developer.developer import build_developer_swarm
from .diffusion.diffusion import build_diffusion_swarm
from .evaluator.evaluator import build_evaluator_swarm
from .image.image import build_image_swarm
from .mac_ui_automation import (
    augment_aworld_agent_builtin_tools,
    augment_aworld_agent_mcp_servers,
)
from .sandbox_factory import create_agent_sandbox

# Import SpawnSubagentTool to ensure it's registered in ToolFactory
from aworld.core.tool.builtin import SpawnSubagentTool  # noqa: F401

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aworld.agents.llm_agent import Agent
from aworld.agents.prompt_budgeted_agent import PromptBudgetedAgent
from aworld.core.agent.swarm import TeamSwarm, Swarm
from aworld.core.agent.base import BaseAgent
from aworld_cli.core import agent

from aworld.config import AgentConfig, ModelConfig

CAST_ANALYSIS = "CAST_ANALYSIS"
CAST_CODER = "CAST_CODER"
CAST_SEARCH = "CAST_SEARCH"
AWORLD_MAX_LOOP_STEPS_HARD_LIMIT = 240
AWORLD_DEFAULT_MAX_COMPLETION_TOKENS = 16384
AWORLD_MAX_COMPLETION_TOKENS_HARD_LIMIT = 64000
AWORLD_BUILTIN_SUBAGENT_NAMES = (
    "developer",
    "evaluator",
    "diffusion",
    "avatar",
    "audio",
    "image",
)
_BACKGROUND_SUBAGENT_ACTIONS = (
    "spawn_background",
    "check_task",
    "wait_task",
    "cancel_task",
)


def _register_optional_cast_tools(
    module_loader: Callable[[str], object] = import_module,
) -> tuple[bool, Optional[str]]:
    """Register CAST tools when their optional native dependencies are usable.

    CAST is an enhancement for the built-in AWorld agent, not a prerequisite for
    terminal execution. In particular, a tree-sitter wheel built for a newer
    GLIBC must not prevent the agent itself from loading in an older task image.
    """
    try:
        module_loader("aworld.experimental.cast.tools")
    except (ImportError, OSError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning(
            f"CAST tools are unavailable; continuing without AST tools: {reason}"
        )
        return False, reason
    return True, None


def _resolve_beijing_timezone():
    """Return Asia/Shanghai when available, with a portable UTC+8 fallback."""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        logger.warning(
            "Asia/Shanghai timezone data is unavailable; using fixed UTC+08:00"
        )
        return timezone(timedelta(hours=8), name="UTC+08:00")


_CAST_TOOLS_AVAILABLE, _CAST_TOOLS_UNAVAILABLE_REASON = _register_optional_cast_tools()
_BEIJING_TZ = _resolve_beijing_timezone()


def resolve_aworld_prompt_budget() -> Optional[PromptBudgetPolicy]:
    """Resolve the opt-in request budget used by runtime-owned Aworld tasks."""

    raw_value = os.environ.get("AWORLD_PROMPT_BUDGET_RESERVED_OUTPUT_TOKENS")
    if raw_value is None or not raw_value.strip():
        return None
    try:
        reserved_output_tokens = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            "AWORLD_PROMPT_BUDGET_RESERVED_OUTPUT_TOKENS must be a positive integer"
        ) from exc
    if reserved_output_tokens <= 0:
        raise ValueError(
            "AWORLD_PROMPT_BUDGET_RESERVED_OUTPUT_TOKENS must be a positive integer"
        )
    return PromptBudgetPolicy(reserved_output_tokens=reserved_output_tokens)


def resolve_aworld_max_completion_tokens() -> int:
    """Resolve a bounded per-turn output budget for the built-in agent."""

    raw_value = os.environ.get(
        "AWORLD_MAX_COMPLETION_TOKENS",
        str(AWORLD_DEFAULT_MAX_COMPLETION_TOKENS),
    )
    try:
        max_completion_tokens = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            "AWORLD_MAX_COMPLETION_TOKENS must be a positive integer"
        ) from exc
    if max_completion_tokens <= 0:
        raise ValueError(
            "AWORLD_MAX_COMPLETION_TOKENS must be a positive integer"
        )
    if max_completion_tokens > AWORLD_MAX_COMPLETION_TOKENS_HARD_LIMIT:
        raise ValueError(
            "AWORLD_MAX_COMPLETION_TOKENS must not exceed the hard limit of "
            f"{AWORLD_MAX_COMPLETION_TOKENS_HARD_LIMIT}"
        )
    return max_completion_tokens


def resolve_aworld_tool_surface_profile() -> ToolSurfaceProfile:
    """Resolve a lifecycle-only Tool policy for the bundled root agent.

    ``general`` preserves the interactive CLI surface. ``one_shot`` is for an
    enclosing runner that expects the task to finish in this process: durable
    schedules and background task-management actions are excluded, while
    ordinary terminal/filesystem work remains process-local.
    """

    profile_id = os.environ.get("AWORLD_TOOL_SURFACE_PROFILE", "general")
    profile_id = profile_id.strip().lower()
    if profile_id == "general":
        return ToolSurfaceProfile(profile_id="general")
    if profile_id == "one_shot":
        return ToolSurfaceProfile(
            profile_id="one_shot",
            allowed_lifecycles=(ToolLifecycle.IMMEDIATE,),
        )
    raise ValueError(
        "AWORLD_TOOL_SURFACE_PROFILE must be either 'general' or 'one_shot'"
    )


def resolve_aworld_builtin_subagents() -> tuple[str, ...]:
    """Resolve an explicit, task-text-independent collaborator allowlist."""

    raw_value = os.environ.get("AWORLD_BUILTIN_SUBAGENTS", "all").strip().lower()
    if raw_value in {"all", "auto"}:
        return AWORLD_BUILTIN_SUBAGENT_NAMES
    if raw_value in {"", "none"}:
        return ()

    requested = tuple(
        value.strip() for value in raw_value.split(",") if value.strip()
    )
    unknown = sorted(set(requested) - set(AWORLD_BUILTIN_SUBAGENT_NAMES))
    if unknown:
        raise ValueError(
            "AWORLD_BUILTIN_SUBAGENTS contains unknown names: "
            + ", ".join(unknown)
        )
    requested_set = set(requested)
    return tuple(
        name for name in AWORLD_BUILTIN_SUBAGENT_NAMES if name in requested_set
    )


def _aworld_root_tool_policy(
    profile: ToolSurfaceProfile,
    *,
    has_subagents: bool,
) -> tuple[list[str], dict[str, list[str]]]:
    """Build the root Tool allowlist and action denylist from lifecycle policy."""

    tool_names = [
        CONTEXT_TOOL,
        *([CAST_SEARCH] if _CAST_TOOLS_AVAILABLE else []),
        *(["async_spawn_subagent"] if has_subagents else []),
    ]
    if ToolLifecycle.DURABLE in profile.allowed_lifecycles:
        tool_names.append("cron")

    black_tool_actions: dict[str, list[str]] = {}
    if (
        has_subagents
        and ToolLifecycle.BACKGROUND not in profile.allowed_lifecycles
    ):
        black_tool_actions["async_spawn_subagent"] = list(
            _BACKGROUND_SUBAGENT_ACTIONS
        )
    return tool_names, black_tool_actions


def render_aworld_system_prompt(
    now: Optional[datetime] = None,
    *,
    available_tools: Sequence[str] = (),
    available_subagents: Sequence[str] = (),
) -> str:
    prompt_template = (Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8")
    current = now or datetime.now(_BEIJING_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_BEIJING_TZ)
    current = current.astimezone(_BEIJING_TZ)
    replacements = {
        "{{current_date}}": current.strftime("%Y-%m-%d"),
        "{{current_datetime}}": current.strftime("%Y-%m-%d %H:%M:%S"),
        "{{available_tools}}": (
            ", ".join(sorted(set(available_tools))) or "none"
        ),
        "{{available_subagents}}": (
            ", ".join(sorted(set(available_subagents))) or "none"
        ),
        "{{delegation_guidance}}": (
            "Delegate only when a listed subagent is materially better suited "
            "to an independent subtask. Use its exact listed name."
            if available_subagents
            else "No subagents are available in this run. Execute the task "
            "directly and do not attempt delegation."
        ),
    }
    rendered = prompt_template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def load_aworld_system_prompt(
    *,
    available_tools: Sequence[str] = (),
    available_subagents: Sequence[str] = (),
) -> str:
    return render_aworld_system_prompt(
        available_tools=available_tools,
        available_subagents=available_subagents,
    )


def resolve_aworld_max_loop_steps() -> int:
    """Resolve the bounded soft limit for one Aworld agent task."""

    raw_value = os.environ.get("AWORLD_MAX_LOOP_STEPS", "120")
    try:
        max_loop_steps = int(raw_value)
    except ValueError as exc:
        raise ValueError("AWORLD_MAX_LOOP_STEPS must be a positive integer") from exc
    if max_loop_steps <= 0:
        raise ValueError("AWORLD_MAX_LOOP_STEPS must be a positive integer")
    if max_loop_steps > AWORLD_MAX_LOOP_STEPS_HARD_LIMIT:
        raise ValueError(
            "AWORLD_MAX_LOOP_STEPS must not exceed the hard limit of "
            f"{AWORLD_MAX_LOOP_STEPS_HARD_LIMIT}"
        )
    return max_loop_steps


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


def _subagent_names(sub_agents: Sequence[BaseAgent]) -> List[str]:
    return sorted({agent.name() for agent in sub_agents})


def _build_aworld_sub_agents(
    sandbox,
    enabled_names: Optional[Sequence[str]] = None,
) -> List[BaseAgent]:
    """Build optional collaborators before publishing root capabilities."""

    enabled = set(
        AWORLD_BUILTIN_SUBAGENT_NAMES
        if enabled_names is None
        else enabled_names
    )
    builders = []
    if _CAST_TOOLS_AVAILABLE:
        if "developer" in enabled:
            builders.append(
                ("developer", lambda: build_developer_swarm(sandbox=sandbox))
            )
        if "evaluator" in enabled:
            builders.append(
                ("evaluator", lambda: build_evaluator_swarm(sandbox=sandbox))
            )
    elif {"developer", "evaluator"} & enabled:
        logger.warning(
            "Developer and evaluator sub-agents are disabled because CAST "
            f"dependencies are unavailable: {_CAST_TOOLS_UNAVAILABLE_REASON}"
        )
    optional_builders = (
        ("diffusion", lambda: build_diffusion_swarm(sandbox=sandbox)),
        ("avatar", lambda: build_avatar_swarm(sandbox=sandbox)),
        ("audio", lambda: build_audio_swarm(sandbox=sandbox)),
        ("image", lambda: build_image_swarm(sandbox=sandbox)),
    )
    builders.extend(item for item in optional_builders if item[0] in enabled)

    sub_agents = []
    for label, builder in builders:
        try:
            sub_agents.extend(extract_agents_from_swarm(builder()))
        except Exception as exc:
            logger.warning(
                f"Optional Aworld {label} sub-agent is unavailable: {exc}"
            )
    return sub_agents


def build_context_config(debug_mode):
    config = get_default_config()
    config.debug_mode = debug_mode
    config.agent_config = AgentContextConfig(
        enable_system_prompt_augment=True,
        neuron_names=["task_grounding", "skills"],
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

    plugin_base_dir = Path(__file__).resolve().parents[1]
    resolver_inputs = build_skill_resolver_inputs(
        plugin_base_dir,
        user_dir=os.environ.get("AWORLD_SKILLS_PATH"),
    )

    prompt_budget_policy = resolve_aworld_prompt_budget()
    max_completion_tokens = (
        prompt_budget_policy.reserved_output_tokens
        if prompt_budget_policy is not None
        else resolve_aworld_max_completion_tokens()
    )

    # Configure agent: provider/base_url use getenv defaults; model_name/api_key may be None (ModelConfig accepts Optional[str])
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.1")),
            params={"max_completion_tokens": max_completion_tokens},
            llm_stream_call=os.environ.get("STREAM", "0").lower() in ("1", "true", "yes")
        ),
        use_vision=False,  # Enable if needed for image analysis
        skill_configs={},
        ext={"skill_resolver_inputs": resolver_inputs},
    )

    # Use the packaged Sandbox providers rather than coupling the CLI agent to
    # a benchmark example MCP server.
    builtin_tools = augment_aworld_agent_builtin_tools(["filesystem", "terminal"])
    aworld_mcp_servers = augment_aworld_agent_mcp_servers(["terminal"])
    sandbox = create_agent_sandbox(builtin_tools)

    tool_surface_profile = resolve_aworld_tool_surface_profile()

    # Resolve optional collaborators before constructing the root agent so its
    # prompt and tool catalog describe capabilities that actually exist.
    sub_agents = _build_aworld_sub_agents(
        sandbox,
        enabled_names=resolve_aworld_builtin_subagents(),
    )
    subagent_names = _subagent_names(sub_agents)
    root_tool_names, black_tool_actions = _aworld_root_tool_policy(
        tool_surface_profile,
        has_subagents=bool(sub_agents),
    )
    # Advertise only capabilities the root agent is allowed to use. The
    # Sandbox may host additional providers for specialized subagents.
    prompt_capabilities = [*root_tool_names, *aworld_mcp_servers]

    # Create the root as a direct executor. Delegation is an optional capability,
    # not its identity, and is exposed only when collaborators were initialized.
    agent_class = PromptBudgetedAgent if prompt_budget_policy is not None else Agent
    budgeted_agent_kwargs = (
        {"prompt_budget_policy": prompt_budget_policy}
        if prompt_budget_policy is not None
        else {}
    )
    aworld_agent = agent_class(
        name="Aworld",
        desc="Aworld - A versatile AI assistant capable of executing tasks directly or delegating to agent teams",
        conf=agent_config,
        system_prompt=load_aworld_system_prompt(
            available_tools=prompt_capabilities,
            available_subagents=subagent_names,
        ),
        mcp_servers=aworld_mcp_servers,  # Keep default terminal access and opt-in macOS UI automation when enabled
        sandbox=sandbox,  # Shared sandbox (tools filtered by agent's mcp_servers config)
        tool_names=root_tool_names,
        black_tool_actions=black_tool_actions,
        enable_subagent=bool(sub_agents),
        llm_max_attempts=3,
        llm_retry_delay=2.0,
        max_loop_steps=resolve_aworld_max_loop_steps(),
        **budgeted_agent_kwargs,
    )
    aworld_agent.tool_surface_profile = tool_surface_profile

    if sub_agents:
        logger.info(
            f"Adding {len(sub_agents)} initialized sub-agent(s) to Aworld TeamSwarm"
        )
        return TeamSwarm(aworld_agent, *sub_agents, max_steps=100)
    logger.info("No sub-agents initialized; Aworld will execute directly")
    return TeamSwarm(aworld_agent)
