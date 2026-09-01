"""Pure universal final Context compilation.

The final compiler owns deterministic selection and request construction, but
never owns I/O.  Reducers and artifact offload execute at their owner boundary
and enter this module only as immutable, hash-bound replacement receipts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import re
from .budget import (
    AtomicGroupRef,
    BudgetAllocationTier,
    BudgetCandidate,
    ContextBudgetPlan,
    ContextInputBudget,
    plan_context_budget,
)
from .cache import StablePrefixPartition, partition_stable_prefix
from .frozen_json import FrozenJSON, FrozenMap, canonical_json_hash, freeze_json
from .models import (
    Authority,
    ContextItem,
    ContextKind,
    InferenceProfile,
    Lifetime,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    ResolutionAction,
    ResolutionDecision,
    ResolutionReason,
    ScopeKind,
    Stability,
    TokenAccounting,
    TokenEstimate,
    Trust,
)
from .trace import ContextDecisionTrace
from .resolver import ResolutionOccurrence, resolve_context_occurrences
from .scope import ContextResolutionTarget
from .attribution import (
    AttributionCollection,
    AttributionCollectionShape,
    AttributionOwnerCode,
    ContextAttributionPlanEntry,
    LogicalResidency,
    ProviderRequestAttributionPlan,
)


FINAL_COMPILER_IDENTITY = "aworld.context.compiler.final"


def _stable_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value
    ):
        raise ValueError(f"{name} must be a bounded stable identifier")


def _non_negative_epoch(name: str, value: int | None) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{name} must be a non-negative integer or None")


class ContextEmissionKind(str, Enum):
    MESSAGE = "message"
    TOOL = "tool"
    EVIDENCE_ONLY = "evidence_only"


@dataclass(frozen=True, slots=True)
class FinalCompileCandidate:
    """One occurrence considered by final selection and request emission."""

    item: ContextItem
    tokens: TokenEstimate
    allocation_tier: BudgetAllocationTier
    emission: ContextEmissionKind
    atomic_group: AtomicGroupRef | None = None
    semantics_proven: bool = False
    lowering_proven: bool = True
    activated: bool = True
    allowed: bool = True
    conflict_domain: str | None = None
    owner_code: AttributionOwnerCode = AttributionOwnerCode.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.item, ContextItem):
            raise TypeError("item must be a ContextItem")
        if isinstance(self.tokens, dict):
            object.__setattr__(self, "tokens", TokenEstimate.from_dict(self.tokens))
        if not isinstance(self.tokens, TokenEstimate):
            raise TypeError("tokens must be a TokenEstimate")
        if not isinstance(self.allocation_tier, BudgetAllocationTier):
            raise TypeError("allocation_tier must be a BudgetAllocationTier")
        object.__setattr__(self, "emission", ContextEmissionKind(self.emission))
        object.__setattr__(self, "owner_code", AttributionOwnerCode(self.owner_code))
        if self.atomic_group is not None and not isinstance(
            self.atomic_group, AtomicGroupRef
        ):
            raise TypeError("atomic_group must be an AtomicGroupRef or None")
        if not isinstance(self.semantics_proven, bool):
            raise TypeError("semantics_proven must be a boolean")
        if not isinstance(self.lowering_proven, bool):
            raise TypeError("lowering_proven must be a boolean")
        if not isinstance(self.activated, bool) or not isinstance(self.allowed, bool):
            raise TypeError("activated and allowed must be booleans")
        if self.conflict_domain is not None and (
            not isinstance(self.conflict_domain, str)
            or not self.conflict_domain.strip()
        ):
            raise ValueError("conflict_domain must be a non-empty string or None")
        if (
            self.emission is ContextEmissionKind.TOOL
            and self.item.kind is not ContextKind.TOOL_CATALOG
        ):
            raise ValueError("tool emission requires a tool_catalog ContextItem")
        if (
            self.emission is ContextEmissionKind.MESSAGE
            and self.item.kind is ContextKind.TOOL_CATALOG
        ):
            raise ValueError("tool_catalog ContextItem cannot emit as a message")

    def to_budget_candidate(self) -> BudgetCandidate:
        return BudgetCandidate(
            item=self.item,
            tokens=self.tokens,
            allocation_tier=self.allocation_tier,
            atomic_group=self.atomic_group,
        )


@dataclass(frozen=True, slots=True)
class ReducerReplacement:
    """Owner-produced replacement; this contract performs no reduction I/O."""

    item_id: str
    expected_content_hash: str
    replacement_payload: FrozenJSON
    replacement_tokens: TokenEstimate
    reducer_identity: str
    action: ResolutionAction = ResolutionAction.COMPACTED
    artifact_ref: str | None = None

    def __post_init__(self) -> None:
        _stable_identifier("reducer_identity", self.reducer_identity)
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("item_id must be a non-empty string")
        if not isinstance(self.expected_content_hash, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.expected_content_hash
        ):
            raise ValueError("expected_content_hash must be a canonical sha256 hash")
        object.__setattr__(
            self, "replacement_payload", freeze_json(self.replacement_payload)
        )
        if isinstance(self.replacement_tokens, dict):
            object.__setattr__(
                self,
                "replacement_tokens",
                TokenEstimate.from_dict(self.replacement_tokens),
            )
        if not isinstance(self.replacement_tokens, TokenEstimate):
            raise TypeError("replacement_tokens must be a TokenEstimate")
        object.__setattr__(self, "action", ResolutionAction(self.action))
        if self.action not in {
            ResolutionAction.COMPACTED,
            ResolutionAction.OFFLOADED,
        }:
            raise ValueError("replacement action must be compacted or offloaded")
        if self.action is ResolutionAction.OFFLOADED:
            if not isinstance(self.artifact_ref, str) or not self.artifact_ref.strip():
                raise ValueError("offloaded replacement requires an artifact_ref")
        elif self.artifact_ref is not None:
            if not isinstance(self.artifact_ref, str) or not self.artifact_ref.strip():
                raise ValueError("artifact_ref must be a non-empty string or None")


@dataclass(frozen=True, slots=True)
class FinalCompilePolicy:
    compiler_version: str
    policy_version: str
    input_budget: ContextInputBudget
    replacements: tuple[ReducerReplacement, ...] = ()
    require_proven_semantics_for_enforce: bool = True

    def __post_init__(self) -> None:
        _stable_identifier("compiler_version", self.compiler_version)
        _stable_identifier("policy_version", self.policy_version)
        if not isinstance(self.input_budget, ContextInputBudget):
            raise TypeError("input_budget must be a ContextInputBudget")
        object.__setattr__(self, "replacements", tuple(self.replacements))
        if not all(
            isinstance(item, ReducerReplacement) for item in self.replacements
        ):
            raise TypeError("replacements must contain ReducerReplacement values")
        replacement_ids = [item.item_id for item in self.replacements]
        if len(set(replacement_ids)) != len(replacement_ids):
            raise ValueError("replacements must contain unique item ids")
        if not isinstance(self.require_proven_semantics_for_enforce, bool):
            raise TypeError(
                "require_proven_semantics_for_enforce must be a boolean"
            )


@dataclass(frozen=True, slots=True)
class FinalCompileInput:
    request_id: str
    provider_name: str
    provider_params: FrozenMap
    candidates: tuple[FinalCompileCandidate, ...]
    inference_profile: InferenceProfile
    created_at: datetime
    trace_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    task_epoch: int | None = None
    tools_present: bool = False
    resolution_target: ContextResolutionTarget | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "provider_name"):
            if not isinstance(getattr(self, name), str) or not getattr(
                self, name
            ).strip():
                raise ValueError(f"{name} must be a non-empty string")
        params = freeze_json(self.provider_params)
        if not isinstance(params, FrozenMap):
            raise TypeError("provider_params must be a JSON object")
        object.__setattr__(self, "provider_params", params)
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if not all(
            isinstance(item, FinalCompileCandidate) for item in self.candidates
        ):
            raise TypeError("candidates must contain FinalCompileCandidate values")
        item_ids = [candidate.item.id for candidate in self.candidates]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("final compile candidate item ids must be unique")
        if not isinstance(self.inference_profile, InferenceProfile):
            raise TypeError("inference_profile must be an InferenceProfile")
        if self.inference_profile.provider != self.provider_name:
            raise ValueError("inference profile provider must match provider_name")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        for name in ("trace_id", "task_id", "session_id"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{name} must be a non-empty string or None")
        _non_negative_epoch("task_epoch", self.task_epoch)
        if not isinstance(self.tools_present, bool):
            raise TypeError("tools_present must be a boolean")
        if self.resolution_target is not None and not isinstance(
            self.resolution_target, ContextResolutionTarget
        ):
            raise TypeError(
                "resolution_target must be a ContextResolutionTarget or None"
            )
        if (
            self.resolution_target is not None
            and self.resolution_target.task_epoch != self.task_epoch
        ):
            raise ValueError("resolution target epoch must match compile input")


@dataclass(frozen=True, slots=True)
class FinalCompileResult:
    request_snapshot: ProviderRequestSnapshot
    selected_items: tuple[ContextItem, ...]
    decisions: tuple[ResolutionDecision, ...]
    token_accounting: TokenAccounting
    stable_partition: StablePrefixPartition
    tool_catalog_hash: str
    skill_set_hash: str
    trace: ContextDecisionTrace
    compiler_identity: str
    compiler_version: str
    policy_version: str
    enforce_ready: bool
    attribution_plan: ProviderRequestAttributionPlan | None = None
    blocker_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.request_snapshot, ProviderRequestSnapshot):
            raise TypeError("request_snapshot must be a ProviderRequestSnapshot")
        object.__setattr__(self, "selected_items", tuple(self.selected_items))
        object.__setattr__(self, "decisions", tuple(self.decisions))
        object.__setattr__(self, "blocker_codes", tuple(self.blocker_codes))
        if not all(isinstance(item, ContextItem) for item in self.selected_items):
            raise TypeError("selected_items must contain ContextItem values")
        if not all(isinstance(item, ResolutionDecision) for item in self.decisions):
            raise TypeError("decisions must contain ResolutionDecision values")
        if not isinstance(self.token_accounting, TokenAccounting):
            raise TypeError("token_accounting must be a TokenAccounting")
        if not isinstance(self.stable_partition, StablePrefixPartition):
            raise TypeError("stable_partition must be a StablePrefixPartition")
        if not isinstance(self.trace, ContextDecisionTrace):
            raise TypeError("trace must be a ContextDecisionTrace")
        if self.attribution_plan is not None:
            if not isinstance(self.attribution_plan, ProviderRequestAttributionPlan):
                raise TypeError("attribution_plan must be a ProviderRequestAttributionPlan or None")
            if self.attribution_plan.candidate_content_hash != self.request_snapshot.content_hash:
                raise ValueError("attribution plan must bind the candidate snapshot")
        for name in (
            "tool_catalog_hash",
            "skill_set_hash",
            "compiler_identity",
            "compiler_version",
            "policy_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.enforce_ready, bool):
            raise TypeError("enforce_ready must be a boolean")
        if self.enforce_ready and self.blocker_codes:
            raise ValueError("enforce_ready result cannot retain blocker codes")
        if any(
            not isinstance(code, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
            for code in self.blocker_codes
        ):
            raise ValueError("blocker_codes must contain stable reason codes")


class FinalCompileContractError(ValueError):
    code = "final_compile_contract_error"


class StaleContextItem(FinalCompileContractError):
    code = "stale_context_item"

    def __init__(self, item_ids: tuple[str, ...]) -> None:
        self.item_ids = item_ids
        super().__init__(f"{self.code}: count={len(item_ids)}")


class ReducerReceiptMismatch(FinalCompileContractError):
    code = "reducer_receipt_mismatch"

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(self.code)


def _replace_candidates(
    candidates: tuple[FinalCompileCandidate, ...],
    replacements: tuple[ReducerReplacement, ...],
) -> tuple[
    tuple[FinalCompileCandidate, ...], dict[str, ReducerReplacement]
]:
    by_id = {replacement.item_id: replacement for replacement in replacements}
    candidate_ids = {candidate.item.id for candidate in candidates}
    unknown_replacements = tuple(sorted(set(by_id) - candidate_ids))
    if unknown_replacements:
        raise ReducerReceiptMismatch(unknown_replacements[0])
    values: list[FinalCompileCandidate] = []
    for candidate in candidates:
        replacement_receipt = by_id.get(candidate.item.id)
        if replacement_receipt is None:
            values.append(candidate)
            continue
        if candidate.item.content_hash != replacement_receipt.expected_content_hash:
            raise ReducerReceiptMismatch(candidate.item.id)
        replacement_item = replace(
            candidate.item,
            payload=replacement_receipt.replacement_payload,
            reducer=replacement_receipt.reducer_identity,
            content_hash=None,
        )
        values.append(
            FinalCompileCandidate(
                item=replacement_item,
                tokens=replacement_receipt.replacement_tokens,
                allocation_tier=candidate.allocation_tier,
                emission=candidate.emission,
                atomic_group=candidate.atomic_group,
                semantics_proven=candidate.semantics_proven,
                lowering_proven=candidate.lowering_proven,
                activated=candidate.activated,
                allowed=candidate.allowed,
                conflict_domain=candidate.conflict_domain,
                owner_code=candidate.owner_code,
            )
        )
    return tuple(values), by_id


def _resolved_decisions(
    *,
    original_candidates: tuple[FinalCompileCandidate, ...],
    budget_plan: ContextBudgetPlan,
    replacements: dict[str, ReducerReplacement],
) -> tuple[ResolutionDecision, ...]:
    planned = {decision.item_id: decision for decision in budget_plan.decisions}
    decisions: list[ResolutionDecision] = []
    for candidate in original_candidates:
        decision = planned[candidate.item.id]
        replacement_receipt = replacements.get(candidate.item.id)
        if (
            replacement_receipt is None
            or decision.action is not ResolutionAction.INCLUDED
        ):
            decisions.append(decision)
            continue
        decisions.append(
            ResolutionDecision(
                item_id=candidate.item.id,
                action=replacement_receipt.action,
                reason=(
                    ResolutionReason.BUDGET_OFFLOADED
                    if replacement_receipt.action is ResolutionAction.OFFLOADED
                    else ResolutionReason.BUDGET_COMPACTED
                ),
                tokens_before=candidate.tokens,
                tokens_after=replacement_receipt.replacement_tokens,
                authority=candidate.item.authority,
                scope=candidate.item.scope,
                trust=candidate.item.trust,
                content_hash=candidate.item.content_hash or "",
                artifact_ref=replacement_receipt.artifact_ref,
            )
        )
    return tuple(decisions)


def _semantic_blockers(
    candidates: tuple[FinalCompileCandidate, ...],
    *,
    task_epoch: int | None,
    require_proven: bool,
) -> tuple[str, ...]:
    blockers: set[str] = set()
    for candidate in candidates:
        item = candidate.item
        if require_proven and not candidate.semantics_proven:
            blockers.add("context_semantics_unproven")
        if not candidate.lowering_proven:
            blockers.add("source_lowering_unproven")
        if item.kind is ContextKind.UNKNOWN:
            blockers.add("context_kind_unknown")
        if item.authority is Authority.UNKNOWN:
            blockers.add("context_authority_unknown")
        if item.scope.kinds == (ScopeKind.UNKNOWN,):
            blockers.add("context_scope_unknown")
        if item.lifetime is Lifetime.UNKNOWN:
            blockers.add("context_lifetime_unknown")
        if item.trust is Trust.UNKNOWN:
            blockers.add("context_trust_unknown")
        if item.stability is Stability.UNKNOWN:
            blockers.add("context_stability_unknown")
        if (
            task_epoch is not None
            and item.task_epoch is None
            and item.lifetime not in {Lifetime.INSTALLATION, Lifetime.WORKSPACE}
        ):
            blockers.add("context_task_epoch_unknown")
    return tuple(sorted(blockers))


def compile_final_context(
    *,
    compiler_input: FinalCompileInput,
    policy: FinalCompilePolicy,
) -> FinalCompileResult:
    """Compile one immutable request without invoking runtime capabilities."""
    if not isinstance(compiler_input, FinalCompileInput):
        raise TypeError("compiler_input must be a FinalCompileInput")
    if type(policy) is not FinalCompilePolicy:
        raise TypeError("policy must be the sealed FinalCompilePolicy type")
    stale = tuple(
        candidate.item.id
        for candidate in compiler_input.candidates
        if compiler_input.task_epoch is not None
        and candidate.item.task_epoch is not None
        and candidate.item.task_epoch != compiler_input.task_epoch
    )
    if stale:
        raise StaleContextItem(stale)
    candidate_ids = {candidate.item.id for candidate in compiler_input.candidates}
    unknown_replacements = tuple(
        sorted(
            replacement.item_id
            for replacement in policy.replacements
            if replacement.item_id not in candidate_ids
        )
    )
    if unknown_replacements:
        raise ReducerReceiptMismatch(unknown_replacements[0])

    resolution = resolve_context_occurrences(
        (
            ResolutionOccurrence(
                item=candidate.item,
                tokens=candidate.tokens,
                activated=candidate.activated,
                allowed=candidate.allowed,
                conflict_domain=candidate.conflict_domain,
                semantics_proven=candidate.semantics_proven,
            )
            for candidate in compiler_input.candidates
        ),
        target=compiler_input.resolution_target,
    )
    eligible_ids = set(resolution.included_item_ids)
    eligible_candidates = tuple(
        candidate
        for candidate in compiler_input.candidates
        if candidate.item.id in eligible_ids
    )
    eligible_replacement_ids = {
        replacement.item_id
        for replacement in policy.replacements
        if replacement.item_id in eligible_ids
    }
    reduced_candidates, replacements = _replace_candidates(
        eligible_candidates,
        tuple(
            replacement
            for replacement in policy.replacements
            if replacement.item_id in eligible_replacement_ids
        ),
    )
    budget_plan = plan_context_budget(
        (candidate.to_budget_candidate() for candidate in reduced_candidates),
        policy.input_budget,
    )
    selected_ids = {item.id for item in budget_plan.selected_items}
    selected_candidates = tuple(
        candidate
        for candidate in reduced_candidates
        if candidate.item.id in selected_ids
    )
    selected_items = tuple(candidate.item for candidate in selected_candidates)
    emitted_items = tuple(
        candidate.item
        for candidate in selected_candidates
        if candidate.emission is not ContextEmissionKind.EVIDENCE_ONLY
    )
    partition = partition_stable_prefix(emitted_items)
    stable_item_ids = {item.id for item in partition.stable_items}
    messages: list[FrozenJSON] = []
    tools: list[FrozenJSON] = []
    attribution_entries: list[ContextAttributionPlanEntry] = []
    for candidate in selected_candidates:
        if candidate.emission is ContextEmissionKind.EVIDENCE_ONLY:
            continue
        if candidate.emission is ContextEmissionKind.MESSAGE:
            collection = AttributionCollection.MESSAGES
            ordinal = len(messages)
            messages.append(candidate.item.payload)
        else:
            collection = AttributionCollection.TOOLS
            ordinal = len(tools)
            tools.append(candidate.item.payload)
        attribution_entries.append(
            ContextAttributionPlanEntry.from_item(
                item=candidate.item,
                owner_code=candidate.owner_code,
                collection=collection,
                ordinal=ordinal,
                token_estimate=candidate.tokens,
                residency=(
                    LogicalResidency.STABLE
                    if candidate.item.id in stable_item_ids
                    else LogicalResidency.DYNAMIC
                ),
            )
        )
    request_snapshot = ProviderRequestSnapshot(
        request_id=compiler_input.request_id,
        provider_name=compiler_input.provider_name,
        payload={
            "messages": tuple(messages),
            "tools": tuple(tools) if compiler_input.tools_present else None,
            "params": compiler_input.provider_params,
        },
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    attribution_plan = ProviderRequestAttributionPlan(
        request_id_hash=canonical_json_hash(
            {"request_id": compiler_input.request_id}
        ),
        candidate_content_hash=request_snapshot.content_hash,
        entries=tuple(attribution_entries),
        messages_count=len(messages),
        tools_shape=(
            AttributionCollectionShape.ARRAY
            if compiler_input.tools_present
            else AttributionCollectionShape.NULL
        ),
        tools_count=(len(tools) if compiler_input.tools_present else None),
    )
    budget_decisions = _resolved_decisions(
        original_candidates=eligible_candidates,
        budget_plan=budget_plan,
        replacements=replacements,
    )
    decisions_by_id = {
        decision.item_id: decision
        for decision in (*resolution.excluded_decisions, *budget_decisions)
    }
    decisions = tuple(
        decisions_by_id[candidate.item.id]
        for candidate in compiler_input.candidates
    )
    blocker_codes = tuple(sorted(set(resolution.blocker_codes) | set(_semantic_blockers(
        selected_candidates,
        task_epoch=compiler_input.task_epoch,
        require_proven=policy.require_proven_semantics_for_enforce,
    ))))
    trace = ContextDecisionTrace.build(
        trace_id=compiler_input.trace_id,
        task_id=compiler_input.task_id,
        session_id=compiler_input.session_id,
        task_epoch=compiler_input.task_epoch,
        compiler_version=policy.compiler_version,
        items=(candidate.item for candidate in compiler_input.candidates),
        decisions=decisions,
        token_accounting=budget_plan.token_accounting,
        stable_prefix_hash=partition.stable_prefix_hash,
        serialized_prefix_hash=None,
        dynamic_context_hash=partition.dynamic_context_hash,
        request_snapshot=request_snapshot,
        created_at=compiler_input.created_at,
    )
    return FinalCompileResult(
        request_snapshot=request_snapshot,
        selected_items=selected_items,
        decisions=decisions,
        token_accounting=budget_plan.token_accounting,
        stable_partition=partition,
        tool_catalog_hash=canonical_json_hash(
            [
                item.content_hash
                for item in selected_items
                if item.kind is ContextKind.TOOL_CATALOG
            ]
        ),
        skill_set_hash=canonical_json_hash(
            [
                item.content_hash
                for item in selected_items
                if item.kind is ContextKind.SKILL
            ]
        ),
        trace=trace,
        attribution_plan=attribution_plan,
        compiler_identity=FINAL_COMPILER_IDENTITY,
        compiler_version=policy.compiler_version,
        policy_version=policy.policy_version,
        enforce_ready=not blocker_codes,
        blocker_codes=blocker_codes,
    )


__all__ = [
    "ContextEmissionKind",
    "FINAL_COMPILER_IDENTITY",
    "FinalCompileCandidate",
    "FinalCompileContractError",
    "FinalCompileInput",
    "FinalCompilePolicy",
    "FinalCompileResult",
    "ReducerReceiptMismatch",
    "ReducerReplacement",
    "StaleContextItem",
    "compile_final_context",
]
