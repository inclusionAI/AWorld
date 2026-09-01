"""Runtime bridge from trusted Skill configuration to progressive Context."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from aworld.core.context.compiler import (
    AdapterResult,
    Authority,
    ContextItem,
    ContextKind,
    ContextObservationSidecar,
    ContextScope,
    ContextSource,
    DisclosureLevel,
    Lifetime,
    ScopeKind,
    SkillDescriptor,
    SkillCatalogEntry,
    SkillActivation,
    SkillIndexEntry,
    SourceKind,
    Stability,
    TaskSkillSnapshot,
    Trust,
    canonical_json_hash,
    estimate_canonical_json_tokens,
    route_skills,
)


@dataclass(frozen=True, slots=True)
class ProgressiveSkillProposal:
    snapshot: TaskSkillSnapshot
    activations: tuple[SkillActivation, ...]


def prepare_progressive_skill_context(
    *,
    context,
    agent_id: str,
    skill_configs: Mapping[str, Any],
    available_tool_ids: Iterable[str] | None = None,
    tool_identity_mapping: Mapping[str, str] | None = None,
    require_resolved_tools: bool = False,
) -> ProgressiveSkillProposal:
    """Build a typed Skill/content/Tool proposal without mutating Context."""
    indexes: list[SkillIndexEntry] = []
    descriptors: list[SkillDescriptor] = []
    configured_active: list[str] = []
    risk_by_id: dict[str, str] = {}
    config_by_id: dict[str, Mapping[str, Any]] = {}
    unavailable_by_id: dict[str, tuple[str, ...]] = {}
    available_ids = set(available_tool_ids or ())
    identity_mapping = dict(tool_identity_mapping or {})

    def resolve_required_tools(
        tool_list: Mapping[str, Any]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Resolve structured Skill declarations to exact model-visible ids."""
        if not require_resolved_tools:
            return tuple(sorted(str(name) for name in tool_list)), ()
        resolved: list[str] = []
        unavailable: list[str] = []
        # The mapping is model-visible id -> original ``server__tool`` id.
        by_origin = {
            origin: visible
            for visible, origin in identity_mapping.items()
            if visible in available_ids
        }
        for server_name in sorted(str(value) for value in tool_list):
            configured = tool_list.get(server_name)
            server_prefix = f"{server_name}__"
            if not configured:
                matches = sorted(
                    visible
                    for origin, visible in by_origin.items()
                    if origin.startswith(server_prefix)
                )
                if not matches:
                    unavailable.append(f"{server_name}__*")
                resolved.extend(matches)
                continue
            names = configured if isinstance(configured, list) else [configured]
            for name in names:
                origin = f"{server_name}__{str(name)}"
                visible = by_origin.get(origin)
                if visible is None:
                    unavailable.append(origin)
                else:
                    resolved.append(visible)
        return tuple(dict.fromkeys(resolved)), tuple(dict.fromkeys(unavailable))

    for skill_id in sorted(skill_configs):
        config = skill_configs[skill_id]
        if not isinstance(config, Mapping):
            continue
        stable_id = str(skill_id).strip()
        if not stable_id:
            continue
        metadata = config.get("aworld_metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        risk = str(metadata.get("risk", config.get("risk", "standard")))
        usage = str(config.get("usage", "") or "")
        tools = config.get("tool_list")
        tools = tools if isinstance(tools, Mapping) else {}
        required_tools, unavailable_tools = resolve_required_tools(tools)
        index = SkillIndexEntry(
            skill_id=stable_id,
            name=str(config.get("name", stable_id) or stable_id),
            description=str(config.get("description", stable_id) or stable_id),
            trigger_codes=(),
            risk=risk,
            estimated_tokens=(
                estimate_canonical_json_tokens(usage).value or 0
            ),
            version=str(metadata.get("version", "v1") or "v1"),
        )
        indexes.append(index)
        descriptors.append(
            SkillDescriptor(
                index=index,
                required_tools=required_tools,
                resource_refs=tuple(
                    value
                    for value in (
                        str(config.get("skill_path", "") or ""),
                        str(config.get("asset_root", "") or ""),
                    )
                    if value
                ),
                content_hash=canonical_json_hash(
                    {"usage": usage, "tool_list": dict(tools)}
                ),
            )
        )
        risk_by_id[stable_id] = risk
        config_by_id[stable_id] = config
        unavailable_by_id[stable_id] = unavailable_tools
        if config.get("active") is True:
            configured_active.append(stable_id)

    active_ids = tuple(configured_active)
    allowed_risks = {risk_by_id[skill_id] for skill_id in active_ids}
    activations = route_skills(
        indexes,
        descriptors,
        explicit_skill_ids=active_ids,
        allowed_risks=allowed_risks,
        content_available_ids=active_ids,
    )
    activations = tuple(
        replace(
            activation,
            activated=False,
            level=DisclosureLevel.DESCRIPTOR,
            reason_code="skill_required_tool_unavailable",
            loaded_tokens=0,
            unavailable_tools=unavailable_by_id.get(activation.skill_id, ()),
        )
        if activation.activated and unavailable_by_id.get(activation.skill_id)
        else activation
        for activation in activations
    )
    activation_by_id = {value.skill_id: value for value in activations}
    descriptor_by_id = {
        value.index.skill_id: value for value in descriptors
    }
    items: list[ContextItem] = []
    scope = ContextScope(
        kinds=(ScopeKind.TASK, ScopeKind.AGENT),
        task_id=context.task_id,
        agent_id=agent_id,
    )
    for occurrence, skill_id in enumerate(active_ids):
        activation = activation_by_id.get(skill_id)
        if activation is None or not activation.activated:
            continue
        config = config_by_id[skill_id]
        usage = str(config.get("usage", "") or "").strip()
        if not usage:
            continue
        payload = {
            "role": "system",
            "content": f"Skill {skill_id} instructions:\n{usage}",
        }
        items.append(
            ContextItem(
                id=f"progressive-skill:{agent_id}:{skill_id}",
                kind=ContextKind.SKILL,
                payload=payload,
                task_epoch=context.task_epoch,
                authority=Authority.APPLICATION_AGENT,
                scope=scope,
                lifetime=Lifetime.TASK,
                priority=occurrence,
                required=False,
                trust=Trust.USER_CONTROLLED,
                stability=Stability.SESSION_STABLE,
                token_limit=max(
                    1, estimate_canonical_json_tokens(payload).value or 0
                ),
                reducer=None,
                source=ContextSource(
                    kind=SourceKind.SKILL,
                    uri=str(config.get("skill_path", "") or f"skill://{skill_id}"),
                    version="progressive-skill-v1",
                    ref={
                        "skill_id": skill_id,
                        "activation_reason": activation.reason_code,
                        "content_hash": descriptor_by_id[skill_id].content_hash,
                        "required_tool_ids": list(
                            activation.requested_tools
                            if require_resolved_tools
                            else ()
                        ),
                    },
                ),
                version=descriptor_by_id[skill_id].index.version,
                activation_reason=activation.reason_code,
                occurrence=occurrence,
            )
        )
    item_by_skill_id = {
        item.source.ref.get("skill_id"): item for item in items
    }
    entries = tuple(
        SkillCatalogEntry(
            skill_id=activation.skill_id,
            descriptor_content_hash=descriptor_by_id[activation.skill_id].content_hash,
            required_tools=(
                activation.requested_tools if require_resolved_tools else ()
            ),
            content_item=item_by_skill_id[activation.skill_id],
        )
        for activation in activations
        if activation.activated and activation.skill_id in item_by_skill_id
    )
    return ProgressiveSkillProposal(
        snapshot=TaskSkillSnapshot.build(context.task_epoch, entries),
        activations=activations,
    )


def apply_progressive_skill_proposal(
    *,
    context,
    agent_id: str,
    proposal: ProgressiveSkillProposal,
    sticky: bool,
    available_tool_ids: Iterable[str],
) -> tuple[str, ...]:
    """Atomically apply one Skill proposal against the actual Tool snapshot."""
    transition = context.bind_task_skill_snapshot(
        agent_id,
        proposal.snapshot,
        available_tool_ids=tuple(available_tool_ids),
        sticky=sticky,
    )
    applied_by_id = {entry.skill_id: entry for entry in transition.snapshot.entries}
    candidate_by_id = {
        entry.skill_id: entry for entry in proposal.snapshot.entries
    }
    final_activations = []
    for activation in proposal.activations:
        applied_entry = applied_by_id.get(activation.skill_id)
        candidate_entry = candidate_by_id.get(activation.skill_id)
        if applied_entry is None:
            reason = (
                "skill_required_tool_unavailable"
                if activation.skill_id in transition.deactivated
                else "skill_sticky_expansion_deferred"
                if activation.skill_id in transition.deferred
                else activation.reason_code
            )
            final_activations.append(replace(
                activation,
                activated=False,
                level=DisclosureLevel.DESCRIPTOR,
                reason_code=reason,
                loaded_tokens=0,
            ))
        elif candidate_entry is not None and applied_entry != candidate_entry:
            final_activations.append(replace(
                activation,
                activated=True,
                reason_code="skill_sticky_previous_retained",
                requested_tools=applied_entry.required_tools,
                loaded_tokens=estimate_canonical_json_tokens(
                    applied_entry.content_item.payload
                ).value or 0,
            ))
        else:
            final_activations.append(activation)
    context.publish_context_observation(
        ContextObservationSidecar.from_adapter_result(
            owner="skills.progressive",
            namespace=agent_id,
            source_identity=f"progressive-skills:{agent_id}:{context.task_epoch}",
            result=AdapterResult(
                items=tuple(entry.content_item for entry in transition.snapshot.entries),
                diagnostics=(),
            ),
        )
    )
    context.record_skill_activations(agent_id, tuple(final_activations))
    return tuple(entry.skill_id for entry in transition.snapshot.entries)


def publish_progressive_skill_context(
    *,
    context,
    agent_id: str,
    skill_configs: Mapping[str, Any],
    sticky: bool,
    available_tool_ids: Iterable[str] | None = None,
    tool_identity_mapping: Mapping[str, str] | None = None,
    require_resolved_tools: bool = False,
) -> tuple[str, ...]:
    """Compatibility wrapper for owners without a separate Tool transition."""
    proposal = prepare_progressive_skill_context(
        context=context,
        agent_id=agent_id,
        skill_configs=skill_configs,
        available_tool_ids=available_tool_ids,
        tool_identity_mapping=tool_identity_mapping,
        require_resolved_tools=require_resolved_tools,
    )
    if require_resolved_tools and any(
        activation.reason_code == "skill_required_tool_unavailable"
        for activation in proposal.activations
    ):
        context.record_skill_activations(agent_id, proposal.activations)
        raise ValueError("skill_required_tool_unavailable")
    return apply_progressive_skill_proposal(
        context=context,
        agent_id=agent_id,
        proposal=proposal,
        sticky=sticky,
        available_tool_ids=available_tool_ids or (),
    )


__all__ = [
    "ProgressiveSkillProposal",
    "apply_progressive_skill_proposal",
    "prepare_progressive_skill_context",
    "publish_progressive_skill_context",
]
