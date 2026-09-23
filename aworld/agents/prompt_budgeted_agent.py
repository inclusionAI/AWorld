from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aworld.agents.llm_agent import Agent
from aworld.models.context_window import ContextWindowResolution, resolve_model_context_window
from aworld.models.request_model import effective_request_model_name, effective_request_output_limits
from aworld.core.context.compiler import ContextInputBudget
from aworld.core.context.amni.prompt.assembly.budget import (
    BudgetedPromptAssemblyProvider,
    PromptBudgetExceededError,
    PromptBudgetPolicy,
)


@dataclass(frozen=True)
class _PromptRequestBudget:
    window: ContextWindowResolution
    capacity: ContextInputBudget
    input_budget: int


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
        resolved = self._resolve_prompt_request_budget(output_limit, request_kwargs)
        metadata["prompt_budget"] = {
            "input_budget": resolved.input_budget,
            "reserved_output_tokens": resolved.capacity.reserved_output_tokens,
            "model_name": resolved.window.model_name or "unknown-model",
            "context_limit": resolved.window.tokens,
            "context_window_source": resolved.window.source,
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
        resolved = self._resolve_prompt_request_budget(output_limit, kwargs, initialize_llm=True)
        input_budget = resolved.input_budget
        tools = kwargs.get("prepared_tools")
        estimate = BudgetedPromptAssemblyProvider.estimate_request_tokens(
            messages=messages,
            tools=tools,
            model_name=resolved.window.model_name or "unknown-model",
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
                reserved_output_tokens=resolved.capacity.reserved_output_tokens,
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

    def _resolve_input_budget(self, output_limit: int, request_kwargs: Optional[Dict[str, Any]] = None) -> int:
        request = {"max_tokens": output_limit} if request_kwargs is None else request_kwargs
        return self._resolve_prompt_request_budget(output_limit, request).input_budget

    def _resolve_prompt_request_budget(
        self, output_limit: int, request_kwargs: Optional[Dict[str, Any]] = None,
        *, initialize_llm: bool = False,
    ) -> _PromptRequestBudget:
        """Read the same request capacity as final compilation before reducing input.

        Metadata-only callers never construct an SDK client. Normal Agent flow
        already initializes its model before assembly; direct invoke does so at
        the usual model-use boundary, without making a model request here.
        """
        request = dict(request_kwargs or {})
        llm = self._llm
        if llm is None and initialize_llm:
            llm = self.llm
        window_resolver = getattr(llm, "resolve_context_window", None)
        budget_resolver = getattr(llm, "resolve_request_context_budget", None)
        if callable(window_resolver) and callable(budget_resolver):
            window = window_resolver(request)
            capacity = budget_resolver(request)
        else:
            window, capacity = self._uninitialized_request_capacity(llm, request, output_limit)
        max_input_tokens = self._optional_positive_limit(
            self.conf.max_input_tokens,
            "AgentConfig.max_input_tokens",
        )
        model_input_capacity = capacity.available_input_tokens
        if model_input_capacity <= 0:
            raise ValueError("model request reserves must leave a positive input budget")
        input_budget = (
            min(max_input_tokens, model_input_capacity)
            if max_input_tokens is not None else model_input_capacity
        )
        return _PromptRequestBudget(window=window, capacity=capacity, input_budget=input_budget)

    def _uninitialized_request_capacity(self, llm, request, output_limit):
        """Conservative metadata support for uninitialized or custom LLM objects."""
        model = self.conf.llm_config
        provider = getattr(llm, "provider", None)
        params = model.params or {}
        if provider is not None:
            bound_model = effective_request_model_name(provider)
            target_model = effective_request_model_name(provider, request)
            deployment_applies = target_model == bound_model and bool(target_model)
            output_limits = effective_request_output_limits(
                provider, max_tokens=request.get("max_tokens", model.max_tokens), request_kwargs=request,
            )
        else:
            # Routing precedence depends on the adapter. Do not infer the old
            # configured identity when an uninitialized request can replace it.
            has_route_override = any(
                any(key in source for key in ("model", "model_name"))
                or (source.get("extra_body") is not None and (
                    not isinstance(source["extra_body"], dict) or "model" in source["extra_body"]
                ))
                for source in (params, request)
            )
            target_model = None if has_route_override else model.llm_model_name
            deployment_applies = bool(target_model)
            output_limits = [output_limit]
            # Before the adapter exists, account for every declared output cap;
            # the real request later resolves exact SDK/HTTP merge semantics.
            for source in (params, request):
                extra = source.get("extra_body")
                if isinstance(extra, dict):
                    output_limits.extend(extra.get(key) for key in ("max_tokens", "max_completion_tokens"))
        window = resolve_model_context_window(
            target_model,
            context_limit=model.context_compiler.context_limit,
            max_model_len=model.max_model_len if deployment_applies else None,
        )
        compiler = model.context_compiler
        explicit_fields = compiler.keys() if isinstance(compiler, dict) else compiler.model_fields_set
        default_reserve = (
            compiler.get("reserved_output_tokens", 4096)
            if isinstance(compiler, dict) else compiler.reserved_output_tokens
        )
        declared_reserve = default_reserve if "reserved_output_tokens" in explicit_fields else 0
        limits = [self._positive_limit(value, "request output token limit")
                  for value in output_limits if value is not None]
        reserved_output = max([declared_reserve, *limits]) if limits else max(
            declared_reserve, default_reserve,
        )
        capacity = ContextInputBudget(
            context_limit=window.tokens,
            reserved_output_tokens=reserved_output,
            provider_protocol_reserve=model.context_compiler.provider_protocol_reserve,
            safety_margin_tokens=model.context_compiler.safety_margin_tokens,
        )
        return window, capacity

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
