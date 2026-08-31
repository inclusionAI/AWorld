"""Deterministic provider-neutral input budget planning."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable

from .models import (
    ContextItem,
    ContextKind,
    ResolutionAction,
    ResolutionDecision,
    ResolutionReason,
    TokenAccounting,
    TokenEstimate,
)


_CONFIG_ESTIMATOR = "context-budget-config-v1"
_PLANNER_ESTIMATOR = "context-budget-planner-v1"


def _non_negative_integer(name: str, value: int, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")


@dataclass(frozen=True, slots=True)
class ContextInputBudget:
    context_limit: int
    reserved_output_tokens: int
    provider_protocol_reserve: int
    safety_margin_tokens: int
    max_item_tokens: int

    def __post_init__(self) -> None:
        _non_negative_integer("context_limit", self.context_limit, positive=True)
        _non_negative_integer(
            "reserved_output_tokens", self.reserved_output_tokens
        )
        _non_negative_integer(
            "provider_protocol_reserve", self.provider_protocol_reserve
        )
        _non_negative_integer(
            "safety_margin_tokens", self.safety_margin_tokens
        )
        _non_negative_integer("max_item_tokens", self.max_item_tokens, positive=True)
        if self.available_input_tokens < 0:
            raise ValueError("reserves exceed context limit")

    @property
    def available_input_tokens(self) -> int:
        return (
            self.context_limit
            - self.reserved_output_tokens
            - self.provider_protocol_reserve
            - self.safety_margin_tokens
        )


@dataclass(frozen=True, slots=True)
class BudgetCandidate:
    item: ContextItem
    tokens: TokenEstimate
    atomic_group: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item, ContextItem):
            raise TypeError("item must be a ContextItem")
        if isinstance(self.tokens, dict):
            object.__setattr__(
                self, "tokens", TokenEstimate.from_dict(self.tokens)
            )
        if not isinstance(self.tokens, TokenEstimate):
            raise TypeError("tokens must be a TokenEstimate")
        if self.atomic_group is not None and (
            not isinstance(self.atomic_group, str) or not self.atomic_group.strip()
        ):
            raise ValueError("atomic_group must be a non-empty string or None")


class ContextBudgetError(ValueError):
    code = "context_budget_error"


class UnknownTokenEstimate(ContextBudgetError):
    code = "token_estimate_unknown"

    def __init__(self, item_ids: Iterable[str]):
        self.item_ids = tuple(item_ids)
        super().__init__(
            f"{self.code}: {len(self.item_ids)} candidate token estimates are unknown"
        )


class RequiredContextBudgetExceeded(ContextBudgetError):
    code = "required_context_budget_exceeded"

    def __init__(self, *, required_tokens: int, available_tokens: int):
        self.required_tokens = required_tokens
        self.available_tokens = available_tokens
        super().__init__(
            f"{self.code}: required={required_tokens}, available={available_tokens}"
        )


class ItemTokenLimitExceeded(ContextBudgetError):
    code = "required_item_token_limit_exceeded"

    def __init__(self, *, item_id: str, tokens: int, limit: int):
        self.item_id = item_id
        self.tokens = tokens
        self.limit = limit
        super().__init__(
            f"{self.code}: tokens={tokens}, limit={limit}"
        )


@dataclass(frozen=True, slots=True)
class ContextBudgetPlan:
    selected_items: tuple[ContextItem, ...]
    decisions: tuple[ResolutionDecision, ...]
    token_accounting: TokenAccounting
    available_input_tokens: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_items", tuple(self.selected_items))
        object.__setattr__(self, "decisions", tuple(self.decisions))
        if not all(isinstance(item, ContextItem) for item in self.selected_items):
            raise TypeError("selected_items must contain ContextItem values")
        if not all(
            isinstance(decision, ResolutionDecision)
            for decision in self.decisions
        ):
            raise TypeError("decisions must contain ResolutionDecision values")
        if not isinstance(self.token_accounting, TokenAccounting):
            raise TypeError("token_accounting must be a TokenAccounting")
        _non_negative_integer(
            "available_input_tokens", self.available_input_tokens
        )


@dataclass(frozen=True, slots=True)
class _CandidateGroup:
    key: str
    candidate_indexes: tuple[int, ...]
    required: bool
    priority: int
    token_count: int
    first_index: int
    item_limit_exceeded: bool


def _sum_estimates(estimates: Iterable[TokenEstimate]) -> TokenEstimate:
    values = tuple(estimates)
    total = sum(estimate.value or 0 for estimate in values)
    estimators = sorted(
        {estimate.estimator for estimate in values if estimate.estimator}
    )
    estimator = (
        estimators[0]
        if len(estimators) == 1
        else f"{_PLANNER_ESTIMATOR}[{','.join(estimators)}]"
    )
    if not estimator:
        estimator = _PLANNER_ESTIMATOR
    return TokenEstimate(
        value=total,
        estimator=estimator,
        exact=all(estimate.exact for estimate in values),
    )


def _candidate_limit(candidate: BudgetCandidate, budget: ContextInputBudget) -> int:
    if candidate.item.token_limit is None:
        return budget.max_item_tokens
    return min(candidate.item.token_limit, budget.max_item_tokens)


def _build_groups(
    candidates: tuple[BudgetCandidate, ...],
    budget: ContextInputBudget,
) -> tuple[_CandidateGroup, ...]:
    indexes_by_key: OrderedDict[str, list[int]] = OrderedDict()
    for index, candidate in enumerate(candidates):
        key = candidate.atomic_group or f"__single__:{index}"
        indexes_by_key.setdefault(key, []).append(index)

    groups: list[_CandidateGroup] = []
    for key, indexes in indexes_by_key.items():
        group_candidates = tuple(candidates[index] for index in indexes)
        exceeded = any(
            (candidate.tokens.value or 0) > _candidate_limit(candidate, budget)
            for candidate in group_candidates
        )
        groups.append(
            _CandidateGroup(
                key=key,
                candidate_indexes=tuple(indexes),
                required=any(candidate.item.required for candidate in group_candidates),
                priority=max(candidate.item.priority for candidate in group_candidates),
                token_count=sum(candidate.tokens.value or 0 for candidate in group_candidates),
                first_index=indexes[0],
                item_limit_exceeded=exceeded,
            )
        )
    return tuple(groups)


def _decision(
    candidate: BudgetCandidate,
    *,
    action: ResolutionAction,
    reason: ResolutionReason,
) -> ResolutionDecision:
    after = (
        candidate.tokens
        if action is ResolutionAction.INCLUDED
        else TokenEstimate(value=0, estimator=_PLANNER_ESTIMATOR, exact=True)
    )
    return ResolutionDecision(
        item_id=candidate.item.id,
        action=action,
        reason=reason,
        tokens_before=candidate.tokens,
        tokens_after=after,
        authority=candidate.item.authority,
        scope=candidate.item.scope,
        trust=candidate.item.trust,
        content_hash=candidate.item.content_hash or "",
        artifact_ref=None,
    )


def plan_context_budget(
    candidates: Iterable[BudgetCandidate],
    budget: ContextInputBudget,
) -> ContextBudgetPlan:
    """Select candidates deterministically without rewriting their payload/order.

    Reducers run before this planner and must provide a new candidate plus token
    estimate. This function never summarizes, truncates, offloads, changes the
    output reserve, or splits an atomic group.
    """
    if not isinstance(budget, ContextInputBudget):
        raise TypeError("budget must be a ContextInputBudget")
    values = tuple(candidates)
    if not all(isinstance(candidate, BudgetCandidate) for candidate in values):
        raise TypeError("candidates must contain BudgetCandidate values")
    item_ids = tuple(candidate.item.id for candidate in values)
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("candidate item ids must be unique")
    unknown_ids = tuple(
        candidate.item.id for candidate in values if candidate.tokens.value is None
    )
    if unknown_ids:
        raise UnknownTokenEstimate(unknown_ids)

    groups = _build_groups(values, budget)
    for group in groups:
        if not group.required or not group.item_limit_exceeded:
            continue
        offending = next(
            values[index]
            for index in group.candidate_indexes
            if (values[index].tokens.value or 0)
            > _candidate_limit(values[index], budget)
        )
        raise ItemTokenLimitExceeded(
            item_id=offending.item.id,
            tokens=offending.tokens.value or 0,
            limit=_candidate_limit(offending, budget),
        )

    required_groups = tuple(
        group
        for group in groups
        if group.required and not group.item_limit_exceeded
    )
    required_tokens = sum(group.token_count for group in required_groups)
    if required_tokens > budget.available_input_tokens:
        raise RequiredContextBudgetExceeded(
            required_tokens=required_tokens,
            available_tokens=budget.available_input_tokens,
        )

    selected_group_keys = {group.key for group in required_groups}
    remaining = budget.available_input_tokens - required_tokens
    optional_groups = sorted(
        (
            group
            for group in groups
            if not group.required and not group.item_limit_exceeded
        ),
        key=lambda group: (-group.priority, group.first_index, group.key),
    )
    for group in optional_groups:
        if group.token_count <= remaining:
            selected_group_keys.add(group.key)
            remaining -= group.token_count

    group_by_candidate_index = {
        index: group
        for group in groups
        for index in group.candidate_indexes
    }
    decisions: list[ResolutionDecision] = []
    selected_items: list[ContextItem] = []
    for index, candidate in enumerate(values):
        group = group_by_candidate_index[index]
        if group.item_limit_exceeded:
            action = ResolutionAction.EXCLUDED
            reason = ResolutionReason.ITEM_TOKEN_LIMIT_EXCEEDED
        elif group.key in selected_group_keys:
            action = ResolutionAction.INCLUDED
            if candidate.item.required:
                reason = ResolutionReason.REQUIRED
            elif group.required:
                reason = ResolutionReason.ATOMIC_GROUP_REQUIRED
            else:
                reason = ResolutionReason.BUDGET_INCLUDED
            selected_items.append(candidate.item)
        else:
            action = ResolutionAction.EXCLUDED
            reason = ResolutionReason.BUDGET_EXCLUDED
        decisions.append(_decision(candidate, action=action, reason=reason))

    selected_estimates = tuple(
        values[index].tokens
        for index, decision in enumerate(decisions)
        if decision.action is ResolutionAction.INCLUDED
    )
    by_kind_values: OrderedDict[ContextKind, list[TokenEstimate]] = OrderedDict()
    for index, decision in enumerate(decisions):
        if decision.action is ResolutionAction.INCLUDED:
            by_kind_values.setdefault(values[index].item.kind, []).append(
                values[index].tokens
            )
    accounting = TokenAccounting(
        total_before=_sum_estimates(candidate.tokens for candidate in values),
        total_after=_sum_estimates(selected_estimates),
        reserved_output=TokenEstimate(
            value=budget.reserved_output_tokens,
            estimator=_CONFIG_ESTIMATOR,
            exact=True,
        ),
        context_limit=TokenEstimate(
            value=budget.context_limit,
            estimator=_CONFIG_ESTIMATOR,
            exact=True,
        ),
        by_kind=tuple(
            (kind, _sum_estimates(estimates))
            for kind, estimates in by_kind_values.items()
        ),
    )
    return ContextBudgetPlan(
        selected_items=tuple(selected_items),
        decisions=tuple(decisions),
        token_accounting=accounting,
        available_input_tokens=budget.available_input_tokens,
    )


__all__ = [
    "BudgetCandidate",
    "ContextBudgetError",
    "ContextBudgetPlan",
    "ContextInputBudget",
    "ItemTokenLimitExceeded",
    "RequiredContextBudgetExceeded",
    "UnknownTokenEstimate",
    "plan_context_budget",
]
