# Import summary first (no dependencies)
from .summary_prompts import (
    episode_memory_summary_rule,
    episode_memory_summary_schema,
    working_memory_summary_rule,
    working_memory_summary_schema,
    tool_memory_summary_rule,
    tool_memory_summary_schema,
)

# Import gaia next (depends on summary, but not on custom_agent_loop)
from .gaia import (
    GAIA_SYSTEM_PROMPT,
    build_gaia_agent,
    build_gaia_task,
    build_amni_gaia_task,
    build_common_gaia_task,
)

# Import custom_agent_loop last (depends on gaia and agent_loop)

# Re-export build_mcp_config for convenience
from ..env import build_mcp_config

__all__ = [
    "GAIA_SYSTEM_PROMPT",
    "build_gaia_agent",
    "build_gaia_task",
    "build_amni_gaia_task",
    "build_common_gaia_task",
    "build_mcp_config",
    "episode_memory_summary_rule",
    "episode_memory_summary_schema",
    "working_memory_summary_rule",
    "working_memory_summary_schema",
    "tool_memory_summary_rule",
    "tool_memory_summary_schema",
]
