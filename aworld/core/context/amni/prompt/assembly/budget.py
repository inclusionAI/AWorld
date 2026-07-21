from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from aworld.core.context.amni.prompt.assembly.plan import PromptAssemblyPlan
from aworld.core.context.amni.prompt.assembly.provider import PromptAssemblyProvider
from aworld.models.utils import ModelUtils
from aworld.utils.serialized_util import to_serializable


OMISSION_MARKER = "\n...[omitted]...\n"


class PromptBudgetPolicy(BaseModel):
    """Opt-in prompt overflow policy and optional direct-provider budget."""

    overflow_strategy: Literal["compact_then_error", "error"] = "compact_then_error"
    minimum_remaining_tokens: int = Field(default=0, ge=0)
    input_budget: Optional[int] = Field(default=None, gt=0)
    reserved_output_tokens: Optional[int] = Field(default=None, gt=0)
    provider_overhead_tokens: int = Field(default=3, ge=0)
    section_hints: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class PromptBudgetExceededError(RuntimeError):
    """Bounded prompt-budget failure that never includes prompt content."""

    code = "prompt_budget_exceeded"

    def __init__(
        self,
        *,
        original_input_tokens: int,
        final_input_tokens: int,
        input_budget: int,
        reserved_output_tokens: int,
        tool_tokens: int,
        required_sections: List[str],
    ) -> None:
        self.original_input_tokens = original_input_tokens
        self.final_input_tokens = final_input_tokens
        self.input_budget = input_budget
        self.reserved_output_tokens = reserved_output_tokens
        self.tool_tokens = tool_tokens
        self.required_sections = list(required_sections)
        super().__init__(
            "prompt input exceeds budget "
            f"(final_tokens={final_input_tokens}, input_budget={input_budget}, "
            f"required_sections={len(required_sections)})"
        )

    def to_diagnostic(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "original_input_tokens": self.original_input_tokens,
            "final_input_tokens": self.final_input_tokens,
            "input_budget": self.input_budget,
            "reserved_output_tokens": self.reserved_output_tokens,
            "tool_tokens": self.tool_tokens,
            "required_sections": list(self.required_sections),
        }


@dataclass
class BudgetedPromptSection:
    name: str
    message_index: int
    priority: int
    required: bool
    compressible: bool
    reducer: str
    original_tokens: int
    final_tokens: int
    applied_reducer: Optional[str] = None


@dataclass
class BudgetedPromptAssemblyPlan(PromptAssemblyPlan):
    input_budget: int = 0
    reserved_output_tokens: int = 0
    original_input_tokens: int = 0
    final_input_tokens: int = 0
    budget_sections: List[BudgetedPromptSection] = field(default_factory=list)


class BudgetedPromptAssemblyProvider(PromptAssemblyProvider):
    """Decorate an existing assembly provider with deterministic prompt reduction."""

    def __init__(
        self,
        delegate: PromptAssemblyProvider,
        policy: PromptBudgetPolicy,
    ) -> None:
        self.delegate = delegate
        self.policy = policy

    @classmethod
    def estimate_request_tokens(
        cls,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_name: str = "gpt-4o",
        provider_overhead_tokens: int = 3,
    ) -> Dict[str, int]:
        serializable_messages = to_serializable(messages)
        serializable_tools = to_serializable(tools or [])
        message_content_tokens = ModelUtils.calculate_token_breakdown(
            serializable_messages,
            model_name,
        ).get("total", 0)
        message_tokens = int(message_content_tokens or 0) + 4 * len(serializable_messages)

        tool_tokens = 0
        if serializable_tools:
            tool_payload = json.dumps(
                serializable_tools,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            tool_tokens = int(
                ModelUtils.calculate_token_breakdown(
                    [{"role": "tool", "content": tool_payload}],
                    model_name,
                ).get("total", 0)
                or 0
            ) + 4

        total = message_tokens + tool_tokens + provider_overhead_tokens
        return {
            "total": total,
            "message_tokens": message_tokens,
            "tool_tokens": tool_tokens,
            "provider_overhead_tokens": provider_overhead_tokens,
        }

    def build_plan(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PromptAssemblyPlan:
        plan_metadata = dict(metadata or {})
        runtime_budget = plan_metadata.get("prompt_budget")
        runtime_budget = runtime_budget if isinstance(runtime_budget, dict) else {}
        input_budget = self._positive_int(
            runtime_budget.get("input_budget", self.policy.input_budget),
            "input_budget",
        )
        reserved_output_tokens = self._non_negative_int(
            runtime_budget.get(
                "reserved_output_tokens",
                self.policy.reserved_output_tokens or 0,
            ),
            "reserved_output_tokens",
        )
        model_name = str(runtime_budget.get("model_name") or "gpt-4o")
        provider_overhead_tokens = self._non_negative_int(
            runtime_budget.get(
                "provider_overhead_tokens",
                self.policy.provider_overhead_tokens,
            ),
            "provider_overhead_tokens",
        )
        target_budget = input_budget - self.policy.minimum_remaining_tokens
        if target_budget <= 0:
            raise ValueError(
                "input_budget must exceed prompt policy minimum_remaining_tokens"
            )

        preview_delegate = copy.copy(self.delegate)
        runtime_state = getattr(self.delegate, "runtime_state", None)
        if runtime_state is not None:
            preview_delegate.runtime_state = copy.deepcopy(runtime_state)
        initial_plan = preview_delegate.build_plan(
            messages=messages,
            tools=tools,
            metadata=plan_metadata,
        )
        source_messages = initial_plan.to_model_messages()
        working_messages = [dict(message) for message in source_messages]
        sections = self._build_sections(working_messages, plan_metadata, model_name)
        original_estimate = self.estimate_request_tokens(
            messages=working_messages,
            tools=tools,
            model_name=model_name,
            provider_overhead_tokens=provider_overhead_tokens,
        )
        current_estimate = original_estimate
        reduced_sections: List[Dict[str, str]] = []

        if (
            current_estimate["total"] > target_budget
            and self.policy.overflow_strategy == "compact_then_error"
        ):
            working_messages, current_estimate = self._remove_optional_sections(
                working_messages=working_messages,
                sections=sections,
                tools=tools,
                model_name=model_name,
                provider_overhead_tokens=provider_overhead_tokens,
                target_budget=target_budget,
                reduced_sections=reduced_sections,
            )

        if (
            current_estimate["total"] > target_budget
            and self.policy.overflow_strategy == "compact_then_error"
        ):
            working_messages, current_estimate = self._compact_required_sections(
                working_messages=working_messages,
                sections=sections,
                tools=tools,
                model_name=model_name,
                provider_overhead_tokens=provider_overhead_tokens,
                target_budget=target_budget,
                reduced_sections=reduced_sections,
            )

        if current_estimate["total"] > target_budget:
            raise PromptBudgetExceededError(
                original_input_tokens=original_estimate["total"],
                final_input_tokens=current_estimate["total"],
                input_budget=input_budget,
                reserved_output_tokens=reserved_output_tokens,
                tool_tokens=current_estimate["tool_tokens"],
                required_sections=[section.name for section in sections if section.required],
            )

        final_plan = self.delegate.build_plan(
            messages=working_messages,
            tools=tools,
            metadata=plan_metadata,
        )
        final_messages = final_plan.to_model_messages()
        current_estimate = self.estimate_request_tokens(
            messages=final_messages,
            tools=tools,
            model_name=model_name,
            provider_overhead_tokens=provider_overhead_tokens,
        )
        if current_estimate["total"] > target_budget:
            raise PromptBudgetExceededError(
                original_input_tokens=original_estimate["total"],
                final_input_tokens=current_estimate["total"],
                input_budget=input_budget,
                reserved_output_tokens=reserved_output_tokens,
                tool_tokens=current_estimate["tool_tokens"],
                required_sections=[section.name for section in sections if section.required],
            )
        self._update_final_section_tokens(sections, final_messages, model_name)
        observability = dict(final_plan.observability)
        observability.update(
            {
                "assembly_provider": type(self).__name__,
                "assembly_delegate": type(self.delegate).__name__,
                "prompt_budget_enabled": True,
                "original_input_tokens": original_estimate["total"],
                "final_input_tokens": current_estimate["total"],
                "input_budget": input_budget,
                "reserved_output_tokens": reserved_output_tokens,
                "tool_tokens": current_estimate["tool_tokens"],
                "provider_overhead_tokens": provider_overhead_tokens,
                "reduced_sections": reduced_sections,
                "section_token_counts": [
                    {
                        "name": section.name,
                        "required": section.required,
                        "original_tokens": section.original_tokens,
                        "final_tokens": section.final_tokens,
                        "reducer": section.applied_reducer,
                    }
                    for section in sections
                ],
            }
        )
        return BudgetedPromptAssemblyPlan(
            messages=final_plan.messages,
            stable_system_sections=final_plan.stable_system_sections,
            dynamic_system_sections=final_plan.dynamic_system_sections,
            conversation_messages=final_plan.conversation_messages,
            tool_section=final_plan.tool_section,
            stable_hash=final_plan.stable_hash,
            observability=observability,
            metadata=final_plan.metadata,
            input_budget=input_budget,
            reserved_output_tokens=reserved_output_tokens,
            original_input_tokens=original_estimate["total"],
            final_input_tokens=current_estimate["total"],
            budget_sections=sections,
        )

    def _build_sections(
        self,
        messages: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        model_name: str,
    ) -> List[BudgetedPromptSection]:
        raw_hints = metadata.get("budget_section_hints")
        aligned_hints = raw_hints if isinstance(raw_hints, list) else []
        system_hints = metadata.get("system_section_hints")
        system_hints = system_hints if isinstance(system_hints, list) else []
        system_hint_index = 0
        non_system_indexes = [
            index
            for index, message in enumerate(messages)
            if message.get("role") != "system"
        ]
        current_task_index = non_system_indexes[-1] if non_system_indexes else -1
        sections: List[BudgetedPromptSection] = []

        for index, message in enumerate(messages):
            hint: Dict[str, Any] = {}
            if index < len(aligned_hints) and isinstance(aligned_hints[index], dict):
                hint.update(aligned_hints[index])
            if message.get("role") == "system":
                if not hint and system_hint_index < len(system_hints):
                    system_hint = system_hints[system_hint_index]
                    if isinstance(system_hint, dict):
                        hint.update(system_hint)
                    elif isinstance(system_hint, str):
                        hint["name"] = system_hint
                system_hint_index += 1

            default_name = (
                "system_prompt"
                if message.get("role") == "system"
                else "current_task"
                if index == current_task_index
                else f"conversation_message_{index}"
            )
            name = str(hint.get("name") or default_name)
            named_hint = self.policy.section_hints.get(name)
            if isinstance(named_hint, dict):
                merged_hint = dict(named_hint)
                merged_hint.update(hint)
                hint = merged_hint
            required_default = message.get("role") == "system" or index == current_task_index
            required = bool(hint.get("required", required_default))
            compressible = bool(hint.get("compressible", False))
            reducer = str(
                hint.get("reducer")
                or ("optional_remove" if not required else "head_tail" if compressible else "none")
            )
            try:
                priority = int(hint.get("priority", 100))
            except (TypeError, ValueError):
                priority = 100
            token_count = self._message_token_count(message, model_name)
            sections.append(
                BudgetedPromptSection(
                    name=name,
                    message_index=index,
                    priority=priority,
                    required=required,
                    compressible=compressible,
                    reducer=reducer,
                    original_tokens=token_count,
                    final_tokens=token_count,
                )
            )
        return sections

    def _remove_optional_sections(
        self,
        *,
        working_messages: List[Dict[str, Any]],
        sections: List[BudgetedPromptSection],
        tools: Optional[List[Dict[str, Any]]],
        model_name: str,
        provider_overhead_tokens: int,
        target_budget: int,
        reduced_sections: List[Dict[str, str]],
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        indexed_messages = list(enumerate(working_messages))
        removable = sorted(
            (
                section
                for section in sections
                if not section.required and section.reducer != "none"
            ),
            key=lambda section: (section.priority, section.message_index),
        )
        estimate = self.estimate_request_tokens(
            messages=working_messages,
            tools=tools,
            model_name=model_name,
            provider_overhead_tokens=provider_overhead_tokens,
        )
        for section in removable:
            if estimate["total"] <= target_budget:
                break
            indexed_messages = [
                item for item in indexed_messages if item[0] != section.message_index
            ]
            working_messages = [message for _, message in indexed_messages]
            section.final_tokens = 0
            section.applied_reducer = section.reducer
            reduced_sections.append({"name": section.name, "reducer": section.reducer})
            estimate = self.estimate_request_tokens(
                messages=working_messages,
                tools=tools,
                model_name=model_name,
                provider_overhead_tokens=provider_overhead_tokens,
            )
        return working_messages, estimate

    def _compact_required_sections(
        self,
        *,
        working_messages: List[Dict[str, Any]],
        sections: List[BudgetedPromptSection],
        tools: Optional[List[Dict[str, Any]]],
        model_name: str,
        provider_overhead_tokens: int,
        target_budget: int,
        reduced_sections: List[Dict[str, str]],
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        retained_indexes = [
            section.message_index for section in sections if section.final_tokens > 0
        ]
        original_to_position = {
            original_index: position
            for position, original_index in enumerate(retained_indexes)
        }
        compressible = sorted(
            (
                section
                for section in sections
                if section.final_tokens > 0
                and section.compressible
                and section.reducer == "head_tail"
            ),
            key=lambda section: (section.priority, section.message_index),
        )
        estimate = self.estimate_request_tokens(
            messages=working_messages,
            tools=tools,
            model_name=model_name,
            provider_overhead_tokens=provider_overhead_tokens,
        )
        for section in compressible:
            if estimate["total"] <= target_budget:
                break
            position = original_to_position[section.message_index]
            message = working_messages[position]
            content = message.get("content")
            if not isinstance(content, str) or len(content) < 3:
                continue
            best_messages: Optional[List[Dict[str, Any]]] = None
            low = 2
            high = len(content) - 1
            while low <= high:
                kept_characters = (low + high) // 2
                candidate_message = dict(message)
                candidate_message["content"] = self._head_tail(
                    content,
                    kept_characters,
                )
                candidate_messages = list(working_messages)
                candidate_messages[position] = candidate_message
                candidate_estimate = self.estimate_request_tokens(
                    messages=candidate_messages,
                    tools=tools,
                    model_name=model_name,
                    provider_overhead_tokens=provider_overhead_tokens,
                )
                if candidate_estimate["total"] <= target_budget:
                    best_messages = candidate_messages
                    low = kept_characters + 1
                else:
                    high = kept_characters - 1

            if best_messages is None:
                continue
            working_messages = best_messages
            message = working_messages[position]
            section.final_tokens = self._message_token_count(message, model_name)
            section.applied_reducer = section.reducer
            reduced_sections.append({"name": section.name, "reducer": section.reducer})
            estimate = self.estimate_request_tokens(
                messages=working_messages,
                tools=tools,
                model_name=model_name,
                provider_overhead_tokens=provider_overhead_tokens,
            )
        return working_messages, estimate

    def _update_final_section_tokens(
        self,
        sections: List[BudgetedPromptSection],
        final_messages: List[Dict[str, Any]],
        model_name: str,
    ) -> None:
        retained_sections = [section for section in sections if section.final_tokens > 0]
        for section, message in zip(retained_sections, final_messages):
            section.final_tokens = self._message_token_count(message, model_name)

    @classmethod
    def _message_token_count(cls, message: Dict[str, Any], model_name: str) -> int:
        content_tokens = ModelUtils.calculate_token_breakdown(
            [message],
            model_name,
        ).get("total", 0)
        return int(content_tokens or 0) + 4

    @staticmethod
    def _head_tail(content: str, kept_characters: int) -> str:
        head = (kept_characters + 1) // 2
        tail = kept_characters // 2
        suffix = content[-tail:] if tail else ""
        return f"{content[:head]}{OMISSION_MARKER}{suffix}"

    @staticmethod
    def _positive_int(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _non_negative_int(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value
