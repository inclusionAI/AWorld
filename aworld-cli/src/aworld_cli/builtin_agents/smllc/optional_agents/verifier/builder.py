"""Pure verifier construction without CLI registry side effects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from aworld.config import AgentConfig
from aworld.core.common import ActionModel
from aworld.core.agent.swarm import Swarm
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.models.reasoning_policy import ReasoningPhasePolicy, ReasoningProfile
from aworld.sandbox import Sandbox

from ...agents.sandbox_factory import create_agent_sandbox
from .._common import (
    FreshAnswerOnlyCollaborator,
    READ_ONLY_FILESYSTEM_ALLOWLIST,
    inherited_collaborator_config,
    public_collaborator_prompt,
)

_REVIEW_MAX_INPUT_TOKENS_PER_TURN = 5_120
_REVIEW_MAX_OUTPUT_TOKENS_PER_TURN = 1_024


class TaskVerifierAgent(FreshAnswerOnlyCollaborator):
    """Fresh, answer-only collaborator with a read-only filesystem surface."""

    mcp_tool_action_allowlist = READ_ONLY_FILESYSTEM_ALLOWLIST
    reasoning_phase_override = "review"
    failed_request_diagnostics_enabled = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bounded_review_model_turns = 0
        self._bounded_review_report_only = False

    async def async_desc_transform(self, context) -> None:
        """Expose read tools only to the first bounded review model turn."""

        await super().async_desc_transform(context)
        if self._bounded_review_report_only:
            self.tools = []
            self.tool_mapping = {}

    async def async_policy(
        self,
        observation,
        info=None,
        message=None,
        **kwargs,
    ):
        """Use one inspection turn and one forced report turn, never a third."""

        if self._bounded_review_model_turns >= 2:
            return [
                ActionModel(
                    agent_name=self.id(),
                    policy_info=(
                        "- Decision: `uncertain`\n"
                        "- Evidence: the bounded verifier turn limit was reached\n"
                        "- Gaps: no valid final report was produced in two turns\n"
                        "- Recommended next action: preserve the current candidate"
                    ),
                )
            ]
        self._bounded_review_report_only = self._bounded_review_model_turns == 1
        try:
            return await super().async_policy(
                observation,
                info=info or {},
                message=message,
                **kwargs,
            )
        finally:
            self._bounded_review_model_turns += 1
            self._bounded_review_report_only = False


def _bounded_review_config(parent: AgentConfig | None) -> AgentConfig:
    """Preserve routing identity while bounding the verifier's own inference."""

    config = inherited_collaborator_config(parent)
    model = config.llm_config
    params = dict(model.params or {})
    for key in ("reasoning_effort", "thinking", "enable_thinking"):
        params.pop(key, None)

    configured_output = params.get("max_completion_tokens")
    if (
        isinstance(configured_output, int)
        and not isinstance(configured_output, bool)
        and configured_output > 0
    ):
        # Reasoning/OpenAI-compatible routes use max_completion_tokens. Do not
        # also send the legacy max_tokens field: some providers reject the
        # mutually exclusive pair.
        params["max_completion_tokens"] = min(
            configured_output,
            _REVIEW_MAX_OUTPUT_TOKENS_PER_TURN,
        )
        params.pop("max_tokens", None)
        model.max_tokens = None
    elif model.max_tokens is not None:
        # Preserve a parent's max_tokens-only transport instead of inventing a
        # second provider parameter.
        params.pop("max_completion_tokens", None)
        params.pop("max_tokens", None)
        model.max_tokens = min(
            model.max_tokens,
            _REVIEW_MAX_OUTPUT_TOKENS_PER_TURN,
        )
    else:
        params["max_completion_tokens"] = _REVIEW_MAX_OUTPUT_TOKENS_PER_TURN
        params.pop("max_tokens", None)
        model.max_tokens = None

    template = params.get("chat_template_kwargs")
    if isinstance(template, Mapping):
        bounded_template = dict(template)
        bounded_template.pop("reasoning_effort", None)
        bounded_template.pop("thinking", None)
        if bounded_template:
            params["chat_template_kwargs"] = bounded_template
        else:
            params.pop("chat_template_kwargs", None)

    extra_body = params.get("extra_body")
    if isinstance(extra_body, Mapping):
        bounded_extra = dict(extra_body)
        bounded_extra.pop("reasoning_effort", None)
        # extra_body is an override at the provider boundary. Never let it
        # bypass the bounded top-level output contract or recreate an invalid
        # max_tokens/max_completion_tokens pair.
        bounded_extra.pop("max_tokens", None)
        bounded_extra.pop("max_completion_tokens", None)
        nested = bounded_extra.get("chat_template_kwargs")
        if isinstance(nested, Mapping):
            bounded_nested = dict(nested)
            bounded_nested.pop("reasoning_effort", None)
            bounded_nested.pop("thinking", None)
            if bounded_nested:
                bounded_extra["chat_template_kwargs"] = bounded_nested
            else:
                bounded_extra.pop("chat_template_kwargs", None)
        if bounded_extra:
            params["extra_body"] = bounded_extra
        else:
            params.pop("extra_body", None)

    model.params = params
    configured_input = config.max_input_tokens
    config.max_input_tokens = min(
        configured_input or _REVIEW_MAX_INPUT_TOKENS_PER_TURN,
        _REVIEW_MAX_INPUT_TOKENS_PER_TURN,
    )
    model.reasoning_phase_policy = ReasoningPhasePolicy(
        policy_id="bounded-review/v1",
        review=ReasoningProfile(reasoning_effort="low"),
    )
    return config


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
    """Build one verifier without registering a process-level CLI agent."""

    if sandbox is None:
        sandbox = create_agent_sandbox(["filesystem"])

    verifier = TaskVerifierAgent(
        name="verifier",
        desc=(
            "Independently checks a delegated task candidate using only the public "
            "request, shared workspace, and direct observations."
        ),
        conf=_bounded_review_config(agent_config),
        system_prompt=public_collaborator_prompt(
            (Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8")
        ),
        mcp_servers=["filesystem"],
        sandbox=sandbox,
        tool_names=[],
        enable_subagent=False,
        llm_max_attempts=1,
        llm_retry_delay=0.0,
        generation_budget_policy=generation_budget_policy,
        _generation_budget_explicit_fields=tuple(generation_budget_explicit_fields),
        max_loop_steps=max_loop_steps,
    )
    return Swarm(verifier)


__all__ = ["TaskVerifierAgent", "build_verifier_swarm"]
