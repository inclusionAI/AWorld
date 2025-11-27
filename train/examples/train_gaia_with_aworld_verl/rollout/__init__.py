# Import prompts first (no dependencies)
# Import gaia next (depends on prompts, but not on custom_agent_loop)
from train.adapter.verl.utils import (
    build_context_aware_agent,
    build_task,
)
from .prompts import (
    GAIA_SYSTEM_PROMPT,
    episode_memory_summary_rule,
    episode_memory_summary_schema,
    working_memory_summary_rule,
    working_memory_summary_schema,
    tool_memory_summary_rule,
    tool_memory_summary_schema,
)
# Re-export build_mcp_config for convenience
from ..mcp_tools import build_mcp_config

# Import custom_agent_loop last (depends on gaia and agent_loop)

__all__ = [
    "GAIA_SYSTEM_PROMPT",
    "build_context_aware_agent",
    "build_task",
    "build_mcp_config",
    "episode_memory_summary_rule",
    "episode_memory_summary_schema",
    "working_memory_summary_rule",
    "working_memory_summary_schema",
    "tool_memory_summary_rule",
    "tool_memory_summary_schema",
]
