"""Runtime bridge from trusted Skill configuration to progressive Context."""

from __future__ import annotations

from dataclasses import replace
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
    SkillIndexEntry,
    SourceKind,
    Stability,
    Trust,
    canonical_json_hash,
    estimate_canonical_json_tokens,
    route_skills,
)


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
    """Publish only application-activated Skill content with proven semantics."""
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

    requested = tuple(configured_active)
    active_ids, _deferred = context.bind_task_skill_set(
        agent_id,
        requested,
        sticky=sticky,
    )
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
    if require_resolved_tools and any(
        activation.reason_code == "skill_required_tool_unavailable"
        for activation in activations
    ):
        # Enforce callers opt into exact identity resolution.  Failing here
        # keeps Skill content and its required Tool set atomic.
        context.record_skill_activations(agent_id, activations)
        raise ValueError("skill_required_tool_unavailable")
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
                    },
                ),
                version="v1",
                activation_reason=activation.reason_code,
                occurrence=occurrence,
            )
        )
    context.publish_context_observation(
        ContextObservationSidecar.from_adapter_result(
            owner="skills.progressive",
            namespace=agent_id,
            source_identity=f"progressive-skills:{agent_id}:{context.task_epoch}",
            result=AdapterResult(items=tuple(items), diagnostics=()),
        )
    )
    context.record_skill_activations(agent_id, activations)
    return active_ids


__all__ = ["publish_progressive_skill_context"]
