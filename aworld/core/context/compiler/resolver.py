"""Authority, activation, scope, and trust resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import (
    Authority,
    ContextItem,
    ResolutionAction,
    ResolutionDecision,
    ResolutionReason,
    TokenEstimate,
    Trust,
)
from .scope import (
    ContextResolutionTarget,
    lifetime_matches,
    scope_matches,
    scope_specificity,
)
from .trust import has_trust_boundary


_AUTHORITY_RANK = {
    Authority.PLATFORM_SYSTEM: 0,
    Authority.APPLICATION_AGENT: 1,
    Authority.WORKSPACE: 2,
    Authority.DIRECTORY: 3,
    Authority.USER: 4,
    Authority.RECALLED_MEMORY: 5,
    Authority.EXTERNAL_TOOL: 6,
    Authority.UNKNOWN: 100,
}


@dataclass(frozen=True, slots=True)
class ResolutionOccurrence:
    item: ContextItem
    tokens: TokenEstimate
    activated: bool = True
    allowed: bool = True
    conflict_domain: str | None = None
    semantics_proven: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.item, ContextItem):
            raise TypeError("item must be a ContextItem")
        if not isinstance(self.tokens, TokenEstimate):
            raise TypeError("tokens must be a TokenEstimate")
        if self.conflict_domain is not None and (
            not isinstance(self.conflict_domain, str)
            or not self.conflict_domain.strip()
        ):
            raise ValueError("conflict_domain must be a non-empty string or None")


@dataclass(frozen=True, slots=True)
class ContextResolutionResult:
    included_item_ids: tuple[str, ...]
    excluded_decisions: tuple[ResolutionDecision, ...]
    blocker_codes: tuple[str, ...]


def _excluded(
    occurrence: ResolutionOccurrence, reason: ResolutionReason
) -> ResolutionDecision:
    return ResolutionDecision(
        item_id=occurrence.item.id,
        action=ResolutionAction.EXCLUDED,
        reason=reason,
        tokens_before=occurrence.tokens,
        tokens_after=TokenEstimate(
            value=0, estimator="aworld-context-resolver-v1", exact=True
        ),
        authority=occurrence.item.authority,
        scope=occurrence.item.scope,
        trust=occurrence.item.trust,
        content_hash=occurrence.item.content_hash or "",
        artifact_ref=None,
    )


def resolve_context_occurrences(
    occurrences: Iterable[ResolutionOccurrence],
    *,
    target: ContextResolutionTarget | None,
) -> ContextResolutionResult:
    """Resolve explicit metadata without interpreting item text as policy."""
    values = tuple(occurrences)
    excluded: dict[str, ResolutionDecision] = {}
    blockers: set[str] = set()
    eligible: list[ResolutionOccurrence] = []
    for occurrence in values:
        if not occurrence.activated:
            excluded[occurrence.item.id] = _excluded(
                occurrence, ResolutionReason.NOT_ACTIVATED
            )
            continue
        if not occurrence.allowed:
            excluded[occurrence.item.id] = _excluded(
                occurrence, ResolutionReason.TOOL_NOT_ALLOWED
            )
            continue
        if target is not None and (
            not scope_matches(occurrence.item.scope, target)
            or not lifetime_matches(occurrence.item, target)
        ):
            excluded[occurrence.item.id] = _excluded(
                occurrence, ResolutionReason.SCOPE_MISMATCH
            )
            continue
        eligible.append(occurrence)

    domains: dict[str, list[ResolutionOccurrence]] = {}
    for occurrence in eligible:
        if occurrence.conflict_domain is not None:
            domains.setdefault(occurrence.conflict_domain, []).append(occurrence)
    for domain_occurrences in domains.values():
        winner = min(
            domain_occurrences,
            key=lambda value: (
                _AUTHORITY_RANK[value.item.authority],
                -scope_specificity(value.item.scope),
                -value.item.priority,
                value.item.version or "",
                value.item.occurrence,
                value.item.id,
            ),
        )
        for occurrence in domain_occurrences:
            if occurrence is not winner:
                excluded[occurrence.item.id] = _excluded(
                    occurrence, ResolutionReason.LOWER_AUTHORITY_CONFLICT
                )

    included = tuple(
        occurrence.item.id
        for occurrence in values
        if occurrence.item.id not in excluded
    )
    for occurrence in values:
        if occurrence.item.id not in included:
            continue
        if not occurrence.semantics_proven:
            blockers.add("context_semantics_unproven")
        if occurrence.item.trust in {
            Trust.EXTERNAL_UNTRUSTED,
            Trust.TOOL_UNTRUSTED,
        }:
            if occurrence.item.authority not in {
                Authority.EXTERNAL_TOOL,
                Authority.RECALLED_MEMORY,
            }:
                blockers.add("untrusted_authority_escalation")
            if not has_trust_boundary(occurrence.item):
                blockers.add("untrusted_boundary_unproven")
    return ContextResolutionResult(
        included_item_ids=included,
        excluded_decisions=tuple(
            excluded[occurrence.item.id]
            for occurrence in values
            if occurrence.item.id in excluded
        ),
        blocker_codes=tuple(sorted(blockers)),
    )


__all__ = [
    "ContextResolutionResult",
    "ResolutionOccurrence",
    "resolve_context_occurrences",
]
