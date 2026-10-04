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
    READ_ONLY_FILESYSTEM_ALLOWLIST,
    inherited_collaborator_config,
    public_collaborator_prompt,
)


_EVALUATOR_PROMPT = """
You are AWorld's fresh-context evaluator. Assess the delegated public objective
against the actual candidate and shared workspace evidence. The root agent may
invoke you whenever an independent quality assessment could change its next
decision; no special wording in the original user request is required.

Inspect rather than modify the candidate through the read-only filesystem
surface. Assess existing public checks, distinguish process success from task
correctness, and identify stale or missing evidence. If a new executable probe
is needed, specify it for the root agent to run. Return exactly these sections: Decision (`ready`,
`repair`, or `uncertain`), Evidence, Gaps, and Recommended next action. The
root agent owns any repair and the final completion decision.
"""


class MultiTaskEvaluatorAgent(FreshAnswerOnlyCollaborator):
    """Evaluate a delegated public candidate and return advisory findings."""

    mcp_tool_action_allowlist = READ_ONLY_FILESYSTEM_ALLOWLIST


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
    name="evaluator",
    desc="""A versatile intelligent assistant for evaluation, when to use:
- Evaluation: Analyze the app/code/html/website's performance, user experience, and so on.
- Improvement: Present professional suggestions for the app/code/html/website improvement.
""",
    context_config=build_context_config(
        debug_mode=True,
    ),
)
def build_evaluator_swarm(
    sandbox: Sandbox | None = None,
    *,
    agent_config: AgentConfig | None = None,
    generation_budget_policy: GenerationBudgetPolicy | None = None,
    generation_budget_explicit_fields: Sequence[str] = (),
    max_loop_steps: int = 0,
    llm_max_attempts: int = 3,
    llm_retry_delay: float = 2.0,
):
    """Build and configure the multi-task evaluator agent swarm."""
    # APP_EVALUATOR_SKILLS_DIR: override skill read directory (plugin root with skills/ subdir)
    plugin_base_dir = Path(__file__).resolve().parents[2]  # smllc bundle root
    # Get user skills directory from environment (optional)
    env_skills_path = os.environ.get("EVALUATOR_SKILLS_PATH")
    resolver_inputs = build_skill_resolver_inputs(
        plugin_base_dir,
        user_dir=env_skills_path,
    )

    collaborator_config = inherited_collaborator_config(
        agent_config,
        resolver_inputs=resolver_inputs,
    )

    mcp_servers = ["filesystem"]
    if sandbox is None:
        sandbox = create_agent_sandbox(mcp_servers)

    # Create MultiTaskEvaluatorAgent instance
    evaluator = MultiTaskEvaluatorAgent(
        name="evaluator",
        desc=(
            "Evaluates a delegated public app, code, HTML, or website candidate "
            "and returns improvement guidance."
        ),
        conf=collaborator_config,
        system_prompt=public_collaborator_prompt(_EVALUATOR_PROMPT),
        mcp_servers=mcp_servers,
        sandbox=sandbox,
        # CAST_ANALYSIS records under a global basename-derived cache and
        # CAST_SEARCH can consume that cross-task state. A fresh evaluator is
        # deliberately restricted to its scoped read-only filesystem view.
        tool_names=[],
        enable_subagent=False,
        llm_max_attempts=llm_max_attempts,
        llm_retry_delay=llm_retry_delay,
        generation_budget_policy=generation_budget_policy,
        _generation_budget_explicit_fields=tuple(generation_budget_explicit_fields),
        max_loop_steps=max_loop_steps,
    )

    # Return the Swarm containing this Agent
    return Swarm(evaluator)
