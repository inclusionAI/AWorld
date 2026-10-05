"""Explicit CLI-registration wrapper for the optional verifier collaborator."""

from __future__ import annotations

from aworld_cli.core import agent

from .builder import TaskVerifierAgent, build_verifier_swarm as _build_verifier_swarm


@agent(
    name="verifier",
    desc=(
        "Fresh-context task verifier that checks public requirements against "
        "workspace state and direct tool evidence, then reports ready, repair, "
        "or uncertain without deciding reward."
    ),
)
def build_verifier_swarm(
    sandbox=None,
    *,
    agent_config=None,
    generation_budget_policy=None,
    generation_budget_explicit_fields=(),
    max_loop_steps: int = 0,
    llm_max_attempts: int = 3,
    llm_retry_delay: float = 2.0,
):
    """Retain explicit opt-in registration while delegating pure construction."""

    return _build_verifier_swarm(
        sandbox=sandbox,
        agent_config=agent_config,
        generation_budget_policy=generation_budget_policy,
        generation_budget_explicit_fields=generation_budget_explicit_fields,
        max_loop_steps=max_loop_steps,
        llm_max_attempts=llm_max_attempts,
        llm_retry_delay=llm_retry_delay,
    )


__all__ = ["TaskVerifierAgent", "build_verifier_swarm"]
