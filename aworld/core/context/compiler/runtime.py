"""Model-boundary adapter into the pure universal final compiler."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
from typing import Iterable

from .adapters import adapt_final_messages, adapt_tool_schemas
from .budget import AtomicGroupRef, BudgetAllocationTier
from .final import (
    ContextEmissionKind,
    FinalCompileCandidate,
    FinalCompileInput,
    FinalCompilePolicy,
    FinalCompileResult,
    compile_final_context,
)
from .frozen_json import FrozenMap, canonical_json_bytes
from .models import (
    ContextItem,
    ContextKind,
    InferenceProfile,
    ProviderRequestSnapshot,
    SourceKind,
    TokenEstimate,
)
from .scope import ContextResolutionTarget
from .scope import lifetime_matches, scope_matches
from .sidecar import ContextObservationSidecar


_TOKEN_ESTIMATOR = "aworld-canonical-json-byte4-v1"


def estimate_canonical_json_tokens(payload) -> TokenEstimate:
    """Versioned conservative transport-neutral estimate, never claimed exact."""
    return TokenEstimate(
        value=math.ceil(len(canonical_json_bytes(payload)) / 4),
        estimator=_TOKEN_ESTIMATOR,
        exact=False,
    )


def _known_semantics(item: ContextItem, task_epoch: int | None) -> bool:
    from .models import Authority, Lifetime, ScopeKind, Stability, Trust

    return (
        item.authority is not Authority.UNKNOWN
        and item.scope.kinds != (ScopeKind.UNKNOWN,)
        and item.lifetime is not Lifetime.UNKNOWN
        and item.trust is not Trust.UNKNOWN
        and item.stability is not Stability.UNKNOWN
        and (
            task_epoch is None
            or item.task_epoch == task_epoch
            or (
                item.task_epoch is None
                and item.lifetime in {Lifetime.INSTALLATION, Lifetime.WORKSPACE}
            )
        )
    )


def _owner_items(
    observations: Iterable[ContextObservationSidecar],
) -> dict[str, list[ContextItem]]:
    by_hash: dict[str, list[ContextItem]] = {}
    for sidecar in observations:
        for item in sidecar.result.items:
            by_hash.setdefault(item.content_hash or "", []).append(item)
            ref = item.source.ref
            if isinstance(ref, FrozenMap):
                original_hash = ref.get("original_content_hash")
                if isinstance(original_hash, str):
                    by_hash.setdefault(original_hash, []).append(item)
    return by_hash


def _atomic_group(item: ContextItem) -> AtomicGroupRef | None:
    ref = item.source.ref
    if not isinstance(ref, FrozenMap):
        return None
    group_id = ref.get("atomic_group_id")
    if not isinstance(group_id, str) or not group_id:
        return None
    group_priority = ref.get("atomic_group_priority")
    if isinstance(group_priority, bool) or not isinstance(group_priority, int):
        return None
    return AtomicGroupRef(
        owner="agent.final_messages",
        namespace="tool-call-turn",
        group_id=group_id,
        selection_priority=group_priority,
    )


def _select_item(
    fallback: ContextItem,
    *,
    owner_by_hash: dict[str, list[ContextItem]],
    consumed_owner_ids: set[str],
    task_epoch: int | None,
    resolution_target: ContextResolutionTarget | None,
) -> ContextItem:
    matches = [
        item
        for item in owner_by_hash.get(fallback.content_hash or "", ())
        if item.id not in consumed_owner_ids
    ]
    if not matches:
        return fallback
    if resolution_target is not None:
        matches = [
            item
            for item in matches
            if scope_matches(item.scope, resolution_target)
            and lifetime_matches(item, resolution_target)
        ]
        if not matches:
            return fallback
    occurrence_matches = [
        item for item in matches if item.occurrence == fallback.occurrence
    ]
    if occurrence_matches:
        matches = occurrence_matches
    source_rank = {
        SourceKind.STEERING: 100,
        SourceKind.TOOL_CATALOG: 90,
        SourceKind.WORKSPACE_FILE: 80,
        SourceKind.SKILL: 70,
        SourceKind.NEURON: 60,
        SourceKind.MEMORY: 50,
        SourceKind.AGENT: 40,
    }
    ranked = sorted(
        matches,
        key=lambda item: (
            int(_known_semantics(item, task_epoch)),
            source_rank.get(item.source.kind, 0),
            int(
                isinstance(item.source.ref, FrozenMap)
                and item.source.ref.get("model_final_boundary") is True
            ),
        ),
        reverse=True,
    )
    best_score = (
        int(_known_semantics(ranked[0], task_epoch)),
        source_rank.get(ranked[0].source.kind, 0),
        int(
            isinstance(ranked[0].source.ref, FrozenMap)
            and ranked[0].source.ref.get("model_final_boundary") is True
        ),
    )
    if sum(
        (
            int(_known_semantics(item, task_epoch)),
            source_rank.get(item.source.kind, 0),
            int(
                isinstance(item.source.ref, FrozenMap)
                and item.source.ref.get("model_final_boundary") is True
            ),
        ) == best_score
        for item in ranked
    ) != 1:
        return fallback
    selected = ranked[0]
    consumed_owner_ids.add(selected.id)
    return selected


def compile_model_boundary_context(
    *,
    legacy_request: ProviderRequestSnapshot,
    observations: Iterable[ContextObservationSidecar],
    inference_profile: InferenceProfile,
    policy: FinalCompilePolicy,
    created_at: datetime,
    task_id: str | None,
    session_id: str | None,
    trace_id: str | None,
    task_epoch: int | None,
    resolution_target: ContextResolutionTarget | None = None,
) -> FinalCompileResult:
    """Reconcile exact finalized occurrences with owner-proven sidecars."""
    payload = legacy_request.payload
    if not isinstance(payload, FrozenMap):
        raise TypeError("legacy model-boundary payload must be an object")
    messages = payload["messages"]
    tools = payload["tools"]
    params = payload["params"]
    if not isinstance(messages, tuple):
        raise TypeError("legacy messages must be an array")
    if tools is not None and not isinstance(tools, tuple):
        raise TypeError("legacy tools must be an array or null")
    if not isinstance(params, FrozenMap):
        raise TypeError("legacy params must be an object")

    message_items = adapt_final_messages(
        messages,
        source_identity=f"model-boundary:{legacy_request.request_id}:messages",
        task_epoch=task_epoch,
    ).items
    tool_items = adapt_tool_schemas(
        tools or (),
        source_identity=f"model-boundary:{legacy_request.request_id}:tools",
        task_epoch=task_epoch,
    ).items
    owner_by_hash = _owner_items(observations)
    consumed_owner_ids: set[str] = set()
    candidates: list[FinalCompileCandidate] = []
    last_user_occurrence = max(
        (
            item.occurrence
            for item in message_items
            if isinstance(item.payload, FrozenMap)
            and item.payload.get("role") == "user"
        ),
        default=-1,
    )
    for fallback in message_items:
        item = _select_item(
            fallback,
            owner_by_hash=owner_by_hash,
            consumed_owner_ids=consumed_owner_ids,
            task_epoch=task_epoch,
            resolution_target=resolution_target,
        )
        required = item.required or (
            item is fallback
            and (
                item.kind is ContextKind.SYSTEM
                or (
                    item.kind is ContextKind.USER
                    and item.occurrence == last_user_occurrence
                )
            )
        )
        if required != item.required:
            item = replace(item, required=True)
        candidates.append(
            FinalCompileCandidate(
                item=item,
                tokens=estimate_canonical_json_tokens(item.payload),
                allocation_tier=BudgetAllocationTier(
                    rank=0 if required else 3,
                    name="required" if required else "dynamic_context",
                ),
                emission=ContextEmissionKind.MESSAGE,
                atomic_group=_atomic_group(item),
                semantics_proven=_known_semantics(item, task_epoch),
            )
        )
    for fallback in tool_items:
        item = _select_item(
            fallback,
            owner_by_hash=owner_by_hash,
            consumed_owner_ids=consumed_owner_ids,
            task_epoch=task_epoch,
            resolution_target=resolution_target,
        )
        candidates.append(
            FinalCompileCandidate(
                item=item,
                tokens=estimate_canonical_json_tokens(item.payload),
                allocation_tier=BudgetAllocationTier(rank=2, name="tool_catalog"),
                emission=ContextEmissionKind.TOOL,
                semantics_proven=_known_semantics(item, task_epoch),
            )
        )
    candidate_ids = {candidate.item.id for candidate in candidates}
    reconciled_occurrences = {
        (candidate.item.content_hash, candidate.item.occurrence)
        for candidate in candidates
    }
    amni_folded_selected = any(
        isinstance(candidate.item.source.ref, FrozenMap)
        and candidate.item.source.ref.get("amni_folded_system") is True
        for candidate in candidates
    )
    evidence_by_id: dict[str, ContextItem] = {}
    for sidecar in observations:
        for item in sidecar.result.items:
            if item.id in consumed_owner_ids:
                continue
            # These owners are alternative observations of the same exact
            # final occurrence, not additional prompt material.  A stronger
            # owner (for example Amni's post-template fold) may have won the
            # reconciliation without making the downstream observation a new
            # message.
            if (
                sidecar.owner in {
                    "agent.final_messages",
                    "agent.final_tool_catalog",
                    "model.final_messages",
                    "model.final_tool_catalog",
                }
                and (item.content_hash, item.occurrence)
                in reconciled_occurrences
            ):
                continue
            # Exact folded-system ownership covers the pre-fold neuron
            # observations.  They remain auditable sidecars but are not a
            # second set of provider messages.
            if amni_folded_selected and sidecar.owner == "amni.neuron_outputs":
                continue
            existing = evidence_by_id.get(item.id)
            if existing is not None and existing != item:
                raise ValueError("owner sidecars contain conflicting Context item ids")
            evidence_by_id[item.id] = item
    additional_message_candidates: list[FinalCompileCandidate] = []
    for item in evidence_by_id.values():
        if item.id in candidate_ids:
            raise ValueError("owner evidence collides with an emitted candidate id")
        delegated = (
            isinstance(item.source.ref, FrozenMap)
            and item.source.ref.get("delegation_context_pack") is True
        )
        message_shaped = (
            isinstance(item.payload, FrozenMap)
            and isinstance(item.payload.get("content"), str)
        )
        can_lower_instruction = (
            message_shaped
            and _known_semantics(item, task_epoch)
            and (
                (
                    item.kind in {ContextKind.INSTRUCTION, ContextKind.SKILL}
                    and item.payload.get("role") == "system"
                )
                or (
                    delegated
                    and item.kind is not ContextKind.TOOL_CATALOG
                    and item.payload.get("role") in {"system", "user", "assistant"}
                )
            )
        )
        candidate = FinalCompileCandidate(
                item=item,
                tokens=(
                    estimate_canonical_json_tokens(item.payload)
                    if can_lower_instruction
                    else TokenEstimate(
                        value=0,
                        estimator="aworld-evidence-only-v1",
                        exact=True,
                    )
                ),
                allocation_tier=BudgetAllocationTier(
                    rank=(1 if can_lower_instruction else 4),
                    name=(
                        (
                            "delegated_context"
                            if delegated
                            else "progressive_skill"
                            if item.kind is ContextKind.SKILL
                            else "scoped_instruction"
                        )
                        if can_lower_instruction
                        else "owner_evidence"
                    ),
                ),
                emission=(
                    ContextEmissionKind.MESSAGE
                    if can_lower_instruction
                    else ContextEmissionKind.EVIDENCE_ONLY
                ),
                semantics_proven=_known_semantics(item, task_epoch),
                lowering_proven=can_lower_instruction,
            )
        if can_lower_instruction:
            additional_message_candidates.append(candidate)
        else:
            candidates.append(candidate)
    if additional_message_candidates:
        insert_at = next(
            (
                index
                for index, candidate in enumerate(candidates)
                if candidate.emission is ContextEmissionKind.MESSAGE
                and candidate.item.kind is not ContextKind.SYSTEM
            ),
            len(candidates),
        )
        candidates[insert_at:insert_at] = additional_message_candidates
    all_proven = all(candidate.semantics_proven for candidate in candidates)
    return compile_final_context(
        compiler_input=FinalCompileInput(
            request_id=legacy_request.request_id,
            provider_name=legacy_request.provider_name,
            provider_params=params,
            candidates=tuple(candidates),
            inference_profile=inference_profile,
            created_at=created_at,
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            task_epoch=task_epoch,
            tools_present=tools is not None,
            resolution_target=resolution_target if all_proven else None,
        ),
        policy=policy,
    )


__all__ = [
    "compile_model_boundary_context",
    "estimate_canonical_json_tokens",
]
