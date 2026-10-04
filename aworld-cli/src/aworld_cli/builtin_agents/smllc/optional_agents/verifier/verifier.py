"""General-purpose task verifier used as an optional AWorld collaborator."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.sandbox import Sandbox
from aworld_cli.core import agent

from ...agents.sandbox_factory import create_agent_sandbox
from .._common import (
    FreshAnswerOnlyCollaborator,
    READ_ONLY_FILESYSTEM_ALLOWLIST,
    inherited_collaborator_config,
    public_collaborator_prompt,
)


class TaskVerifierAgent(FreshAnswerOnlyCollaborator):
    """Team member whose delegation boundary is fresh and answer-only."""

    mcp_tool_action_allowlist = READ_ONLY_FILESYSTEM_ALLOWLIST


@agent(
    name="verifier",
    desc=(
        "Fresh-context task verifier that checks public requirements against "
        "workspace state and direct tool evidence, then reports ready, repair, "
        "or uncertain without deciding reward."
    ),
)
def build_verifier_swarm(
    sandbox: Sandbox | None = None,
    *,
    agent_config: AgentConfig | None = None,
    generation_budget_policy: GenerationBudgetPolicy | None = None,
    generation_budget_explicit_fields: Sequence[str] = (),
    max_loop_steps: int = 0,
    llm_max_attempts: int = 3,
    llm_retry_delay: float = 2.0,
):
    """Build a verifier that shares authority and budgets with the root agent."""

    if sandbox is None:
        sandbox = create_agent_sandbox(["filesystem"])

    verifier = TaskVerifierAgent(
        name="verifier",
        desc=(
            "Independently checks a delegated task candidate using only the public "
            "request, shared workspace, and direct observations."
        ),
        conf=inherited_collaborator_config(agent_config),
        system_prompt=public_collaborator_prompt(
            (Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8")
        ),
        mcp_servers=["filesystem"],
        sandbox=sandbox,
        tool_names=[],
        enable_subagent=False,
        llm_max_attempts=llm_max_attempts,
        llm_retry_delay=llm_retry_delay,
        generation_budget_policy=generation_budget_policy,
        _generation_budget_explicit_fields=tuple(generation_budget_explicit_fields),
        max_loop_steps=max_loop_steps,
    )
    return Swarm(verifier)


__all__ = ["TaskVerifierAgent", "build_verifier_swarm"]
