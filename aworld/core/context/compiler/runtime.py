"""Model-boundary adapter into the pure universal final compiler."""

from __future__ import annotations

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
from .frozen_json import FrozenMap, canonical_json_bytes, canonical_json_hash
from .attribution import AttributionCollection, AttributionOwnerCode
from .models import (
    ContextItem,
    ContextKind,
    InferenceProfile,
    ProviderRequestSnapshot,
    TokenEstimate,
)
from .scope import ContextResolutionTarget
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


def _final_owner_sidecar(
    observations: tuple[ContextObservationSidecar, ...],
    *,
    owner: str,
    request_id: str,
    collection: AttributionCollection,
    task_epoch: int | None,
) -> ContextObservationSidecar | None:
    """Select only the current model-owned collection sidecar.

    Source identity is an in-memory correlation key.  Payload hashes are not
    used to find, rank, or choose provenance.
    """
    request_id_hash = canonical_json_hash({"request_id": request_id})
    matches = [
        sidecar
        for sidecar in observations
        if sidecar.owner == owner
        and sidecar.request_id_hash == request_id_hash
        and sidecar.collection is collection
        and sidecar.task_epoch == task_epoch
    ]
    return matches[0] if len(matches) == 1 else None


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


def _bind_final_collection(
    fallback_items: tuple[ContextItem, ...],
    sidecar: ContextObservationSidecar | None,
) -> tuple[tuple[ContextItem, ...], bool]:
    """Bind by ordinal and then validate the value hash at that ordinal."""
    if sidecar is None or len(sidecar.result.items) != len(fallback_items):
        return fallback_items, False
    bound: list[ContextItem] = []
    for ordinal, fallback in enumerate(fallback_items):
        item = sidecar.result.items[ordinal]
        ref = item.source.ref
        if (
            item.occurrence != ordinal
            or not isinstance(ref, FrozenMap)
            or ref.get("occurrence") != ordinal
            or ref.get("model_final_boundary") is not True
            or item.content_hash != fallback.content_hash
        ):
            return fallback_items, False
        bound.append(item)
    return tuple(bound), True


def _owner_code(owner: str) -> AttributionOwnerCode:
    if owner == "workspace.nested_instructions":
        return AttributionOwnerCode.SCOPED_INSTRUCTION
    if owner == "skills.progressive":
        return AttributionOwnerCode.PROGRESSIVE_SKILL
    if owner == "delegation.context_pack":
        return AttributionOwnerCode.DELEGATION_CONTEXT
    if owner in {"amni.folded_system", "amni.restored_folded_system"}:
        return AttributionOwnerCode.AMNI_FOLDED_SYSTEM
    return AttributionOwnerCode.UNKNOWN


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

    observations = tuple(observations)
    fallback_message_items = adapt_final_messages(
        messages,
        source_identity=f"model-boundary:{legacy_request.request_id}:messages",
        task_epoch=task_epoch,
    ).items
    fallback_tool_items = adapt_tool_schemas(
        tools or (),
        source_identity=f"model-boundary:{legacy_request.request_id}:tools",
        task_epoch=task_epoch,
    ).items
    message_items, messages_bound = _bind_final_collection(
        fallback_message_items,
        _final_owner_sidecar(
            observations,
            owner="model.final_messages",
            request_id=legacy_request.request_id or "",
            collection=AttributionCollection.MESSAGES,
            task_epoch=task_epoch,
        ),
    )
    tool_items, tools_bound = _bind_final_collection(
        fallback_tool_items,
        _final_owner_sidecar(
            observations,
            owner="model.final_tool_catalog",
            request_id=legacy_request.request_id or "",
            collection=AttributionCollection.TOOLS,
            task_epoch=task_epoch,
        ),
    )
    consumed_owner_ids = {
        item.id
        for item in (
            *(message_items if messages_bound else ()),
            *(tool_items if tools_bound else ()),
        )
    }
    candidates: list[FinalCompileCandidate] = []
    for item in message_items:
        required = item.required
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
                semantics_proven=messages_bound and _known_semantics(item, task_epoch),
                lowering_proven=messages_bound,
                owner_code=(
                    AttributionOwnerCode.MODEL_FINAL_MESSAGES
                    if messages_bound
                    else AttributionOwnerCode.UNKNOWN
                ),
            )
        )
    for item in tool_items:
        candidates.append(
            FinalCompileCandidate(
                item=item,
                tokens=estimate_canonical_json_tokens(item.payload),
                allocation_tier=BudgetAllocationTier(rank=2, name="tool_catalog"),
                emission=ContextEmissionKind.TOOL,
                semantics_proven=tools_bound and _known_semantics(item, task_epoch),
                lowering_proven=tools_bound,
                owner_code=(
                    AttributionOwnerCode.MODEL_FINAL_TOOL_CATALOG
                    if tools_bound
                    else AttributionOwnerCode.UNKNOWN
                ),
            )
        )
    candidate_ids = {candidate.item.id for candidate in candidates}
    amni_folded_selected = any(
        sidecar.owner in {"amni.folded_system", "amni.restored_folded_system"}
        for sidecar in observations
    )
    evidence_by_id: dict[str, tuple[str, ContextItem]] = {}
    for sidecar in observations:
        for item in sidecar.result.items:
            if item.id in consumed_owner_ids:
                continue
            # These owners are alternative observations of the same exact
            # final occurrence, not additional prompt material.  A stronger
            # owner (for example Amni's post-template fold) may have won the
            # reconciliation without making the downstream observation a new
            # message.
            if sidecar.owner in {
                    "agent.final_messages",
                    "agent.final_tool_catalog",
                    "model.final_messages",
                    "model.final_tool_catalog",
                    "amni.folded_system",
                    "amni.restored_folded_system",
                }:
                continue
            # Exact folded-system ownership covers the pre-fold neuron
            # observations.  They remain auditable sidecars but are not a
            # second set of provider messages.
            if amni_folded_selected and sidecar.owner == "amni.neuron_outputs":
                continue
            existing = evidence_by_id.get(item.id)
            if existing is not None and existing[1] != item:
                raise ValueError("owner sidecars contain conflicting Context item ids")
            evidence_by_id[item.id] = (sidecar.owner, item)
    additional_message_candidates: list[FinalCompileCandidate] = []
    for sidecar_owner, item in evidence_by_id.values():
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
                owner_code=_owner_code(sidecar_owner),
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
