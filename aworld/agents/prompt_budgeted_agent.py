from __future__ import annotations

from typing import Any, Dict, List, Optional

from aworld.agents.llm_agent import Agent
from aworld.core.context.amni.prompt.assembly.budget import (
    BudgetedPromptAssemblyProvider,
    PromptBudgetExceededError,
    PromptBudgetPolicy,
)


class PromptBudgetedAgent(Agent):
    """Agent extension that enforces one model-aware request budget."""

    def __init__(
        self,
        *,
        prompt_budget_policy: PromptBudgetPolicy,
        prompt_budget_section_hints: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.prompt_budget_policy = prompt_budget_policy.model_copy(deep=True)
        self.prompt_budget_section_hints = list(prompt_budget_section_hints or [])

        model_params = dict(self.conf.llm_config.params or {})
        configured_max_tokens = self._optional_positive_limit(
            model_params.pop("max_tokens", None),
            "ModelConfig.params.max_tokens",
        )
        configured_max_completion_tokens = self._optional_positive_limit(
            model_params.pop("max_completion_tokens", None),
            "ModelConfig.params.max_completion_tokens",
        )
        self.conf.llm_config.params = model_params
        self._configured_output_limits = tuple(
            value
            for value in (
                configured_max_tokens,
                configured_max_completion_tokens,
            )
            if value is not None
        )
        self._configured_output_parameter = (
            "max_completion_tokens"
            if configured_max_completion_tokens is not None
            else "max_tokens"
        )

    def _get_prompt_assembly_provider(self, context: Any = None):
        delegate = super()._get_prompt_assembly_provider(context)
        if isinstance(delegate, BudgetedPromptAssemblyProvider):
            return delegate
        return BudgetedPromptAssemblyProvider(delegate, self.prompt_budget_policy)

    def _build_prompt_assembly_metadata(
        self,
        *,
        context: Any = None,
        request_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        request_kwargs = request_kwargs if request_kwargs is not None else {}
        metadata = super()._build_prompt_assembly_metadata(
            context=context,
            request_kwargs=request_kwargs,
        )
        output_limit = self._resolve_effective_output_limit(request_kwargs)
        input_budget = self._resolve_input_budget(output_limit)
        metadata["prompt_budget"] = {
            "input_budget": input_budget,
            "reserved_output_tokens": output_limit,
            "model_name": self.model_name or "gpt-4o",
            "provider_overhead_tokens": self.prompt_budget_policy.provider_overhead_tokens,
        }
        if self.prompt_budget_section_hints:
            metadata["budget_section_hints"] = [
                dict(hint) for hint in self.prompt_budget_section_hints
            ]
        return metadata

    async def invoke_model(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        message: Any = None,
        **kwargs: Any,
    ) -> Any:
        messages = messages or []
        output_limit = self._resolve_effective_output_limit(kwargs)
        input_budget = self._resolve_input_budget(output_limit)
        tools = kwargs.get("prepared_tools")
        estimate = BudgetedPromptAssemblyProvider.estimate_request_tokens(
            messages=messages,
            tools=tools,
            model_name=self.model_name or "gpt-4o",
            provider_overhead_tokens=self.prompt_budget_policy.provider_overhead_tokens,
        )
        target_budget = input_budget - self.prompt_budget_policy.minimum_remaining_tokens
        if target_budget <= 0:
            raise ValueError(
                "input budget must exceed prompt policy minimum_remaining_tokens"
            )
        if estimate["total"] > target_budget:
            raise PromptBudgetExceededError(
                original_input_tokens=estimate["total"],
                final_input_tokens=estimate["total"],
                input_budget=input_budget,
                reserved_output_tokens=output_limit,
                tool_tokens=estimate["tool_tokens"],
                required_sections=["assembled_request"],
            )
        return await super().invoke_model(messages, message=message, **kwargs)

    def _resolve_effective_output_limit(self, request_kwargs: Dict[str, Any]) -> int:
        requested_max_tokens = self._optional_positive_limit(
            request_kwargs.pop("max_tokens", None),
            "max_tokens",
        )
        requested_max_completion_tokens = self._optional_positive_limit(
            request_kwargs.pop("max_completion_tokens", None),
            "max_completion_tokens",
        )
        policy_limit = self.prompt_budget_policy.reserved_output_tokens
        limits = [
            value
            for value in (
                *self._configured_output_limits,
                policy_limit,
                requested_max_tokens,
                requested_max_completion_tokens,
            )
            if value is not None
        ]
        if not limits:
            raise ValueError(
                "PromptBudgetedAgent requires max_tokens, max_completion_tokens, "
                "or PromptBudgetPolicy.reserved_output_tokens"
            )
        effective_limit = min(limits)
        output_parameter = (
            "max_completion_tokens"
            if requested_max_completion_tokens is not None
            or self._configured_output_parameter == "max_completion_tokens"
            else "max_tokens"
        )
        request_kwargs[output_parameter] = effective_limit
        return effective_limit

    def _resolve_input_budget(self, output_limit: int) -> int:
        max_input_tokens = self._positive_limit(
            self.conf.max_input_tokens,
            "AgentConfig.max_input_tokens",
        )
        max_model_len = self._positive_limit(
            self.conf.llm_config.max_model_len,
            "ModelConfig.max_model_len",
        )
        model_input_capacity = max_model_len - output_limit
        if model_input_capacity <= 0:
            raise ValueError(
                "reserved output tokens must be smaller than ModelConfig.max_model_len"
            )
        return min(max_input_tokens, model_input_capacity)

    @classmethod
    def _optional_positive_limit(cls, value: Any, name: str) -> Optional[int]:
        if value is None:
            return None
        return cls._positive_limit(value, name)

    @staticmethod
    def _positive_limit(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

