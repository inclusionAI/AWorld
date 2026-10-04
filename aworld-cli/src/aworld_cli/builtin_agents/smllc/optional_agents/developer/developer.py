from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni.config import (
    AgentContextConfig,
    ContextEnvConfig,
    get_default_config,
)
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.sandbox import Sandbox
from aworld_cli.core import agent
from aworld_cli.core.skill_registry import build_skill_resolver_inputs

from ...agents.sandbox_factory import create_agent_sandbox
from .._common import (
    FreshAnswerOnlyCollaborator,
    inherited_collaborator_config,
    public_collaborator_prompt,
    registered_optional_tools,
)
from .mcp_config import mcp_config


_DEVELOPER_PROMPT = """
You are AWorld's developer collaborator. Implement the bounded public
development subtask delegated by the root agent in the shared workspace.

Inspect relevant files before editing, preserve unrelated user work, and use
only tools actually exposed in this run. CAST tools may help when present but
are never required; terminal and filesystem operations are valid fallbacks.
Verify material changes with the smallest relevant checks. Do not commit,
publish, or broaden scope unless the delegated public objective explicitly
requires it.

Return a concise report containing the outcome, files changed, checks run, and
remaining gaps. The root agent owns integration and completion.
"""


class DeveloperAgent(FreshAnswerOnlyCollaborator):
    """Implement a delegated public development task in the shared workspace."""


def build_context_config(debug_mode: bool):
    config = get_default_config()
    config.debug_mode = debug_mode
    config.agent_config = AgentContextConfig(
        enable_system_prompt_augment=True,
        neuron_names=["skills"],
    )
    config.env_config = ContextEnvConfig()
    return config


@agent(
    name="developer",
    desc="Analyzes and edits code, HTML, and other files for development work; can develop apps; supports code refactoring and optimization.",
    context_config=build_context_config(
        debug_mode=True,
    ),
)
def build_developer_swarm(
    sandbox: Sandbox | None = None,
    *,
    agent_config: AgentConfig | None = None,
    generation_budget_policy: GenerationBudgetPolicy | None = None,
    generation_budget_explicit_fields: Sequence[str] = (),
    max_loop_steps: int = 0,
    llm_max_attempts: int = 3,
    llm_retry_delay: float = 2.0,
):
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc bundle root
    # Get user skills directory from environment (optional)
    env_skills_path = os.environ.get("DEVELOPER_SKILLS_PATH")
    resolver_inputs = build_skill_resolver_inputs(
        plugin_base_dir,
        user_dir=env_skills_path,
    )

    collaborator_config = inherited_collaborator_config(
        agent_config,
        resolver_inputs=resolver_inputs,
    )

    # Sandbox: reuse shared sandbox if provided, otherwise create new one
    if sandbox is None:
        sandbox = create_agent_sandbox(
            ["filesystem", "terminal"],
            mcp_config=mcp_config,
        )

    # Developer has full MCP tool access: filesystem + terminal
    # Note: Actual tools exposed are filtered by mcp_servers config
    developer_mcp_servers = ["filesystem", "terminal"]

    # Skill tool_list: AGENT_REGISTRY, CAST_ANALYSIS, CAST_CODER, CAST_SEARCH
    tool_names = registered_optional_tools(
        [
            "CAST_ANALYSIS",
            "CAST_CODER",
            "CAST_SEARCH",
            "glob",
            "git_status",
            "git_diff",
            "git_log",
            "git_commit",
            "git_blame",
        ]
    )

    developer_agent = DeveloperAgent(
        name="developer",
        desc=(
            "Analyzes and edits code, HTML, and other files for delegated "
            "development work."
        ),
        conf=collaborator_config,
        system_prompt=public_collaborator_prompt(_DEVELOPER_PROMPT),
        tool_names=tool_names,
        mcp_servers=developer_mcp_servers,  # Explicitly set allowed MCP servers
        sandbox=sandbox,  # Shared sandbox (tools filtered by developer_mcp_servers)
        enable_subagent=False,
        llm_max_attempts=llm_max_attempts,
        llm_retry_delay=llm_retry_delay,
        generation_budget_policy=generation_budget_policy,
        _generation_budget_explicit_fields=tuple(generation_budget_explicit_fields),
        max_loop_steps=max_loop_steps,
    )

    # Return the Swarm containing this Agent
    return Swarm(developer_agent)
