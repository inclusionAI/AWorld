"""Runtime bridge from trusted Skill configuration to progressive Context."""

from __future__ import annotations

from typing import Any, Mapping

from aworld.core.context.compiler import (
    AdapterResult,
    Authority,
    ContextItem,
    ContextKind,
    ContextObservationSidecar,
    ContextScope,
    ContextSource,
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
) -> tuple[str, ...]:
    """Publish only application-activated Skill content with proven semantics."""
    indexes: list[SkillIndexEntry] = []
    descriptors: list[SkillDescriptor] = []
    configured_active: list[str] = []
    risk_by_id: dict[str, str] = {}
    config_by_id: dict[str, Mapping[str, Any]] = {}
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
                required_tools=tuple(sorted(str(name) for name in tools)),
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
