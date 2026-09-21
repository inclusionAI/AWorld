"""LLMAgent owner adapter for the exact final messages and Tool catalog."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aworld.core.context.compiler import (
    AdapterResult,
    Authority,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    Trust,
    canonical_json_hash,
    isolate_untrusted_context_item,
)


def adapt_agent_final_request(
    *,
    messages: Sequence[Any],
    tools: Sequence[Any],
    source_identity: str,
    task_id: str,
    task_epoch: int,
    agent_id: str,
    amni_folded_system: bool,
    required_message_hashes: frozenset[str] = frozenset(),
    required_tool_names: frozenset[str] = frozenset(),
    untrusted_message_hashes: frozenset[str] = frozenset(),
) -> tuple[AdapterResult, AdapterResult]:
    scope = ContextScope(
        kinds=(ScopeKind.TASK, ScopeKind.AGENT),
        task_id=task_id,
        agent_id=agent_id,
    )
    message_items: list[ContextItem] = []
    tool_call_groups: dict[str, tuple[str, int]] = {}
    assistant_group_by_index: dict[int, str] = {}
    for index, payload in enumerate(messages):
        if not isinstance(payload, dict) or payload.get("role") != "assistant":
            continue
        calls = payload.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            continue
        group_id = f"tool-turn-{index}"
        assistant_group_by_index[index] = group_id
        for call in calls:
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                tool_call_groups[call["id"]] = (group_id, index)
    latest_tool_group = (
        assistant_group_by_index[max(assistant_group_by_index)]
        if assistant_group_by_index
        else None
    )
    last_user_index = max(
        (index for index, value in enumerate(messages) if isinstance(value, dict) and value.get("role") == "user"),
        default=-1,
    )
    for index, payload in enumerate(messages):
        role = payload.get("role") if isinstance(payload, dict) else None
        if role == "system":
            kind = ContextKind.SYSTEM
            authority = Authority.APPLICATION_AGENT
            # Amni may fold retrieved or user-controlled material into the
            # application-owned system role.  Its trust is therefore known
            # low-trust, not unknown; this matches the authoritative
            # post-template folded-system adapter below.
            trust = Trust.USER_CONTROLLED if amni_folded_system else Trust.TRUSTED
            required = True
        elif role == "user":
            kind = ContextKind.USER
            authority = Authority.USER
            trust = Trust.USER_CONTROLLED
            required = index == last_user_index
        elif role == "tool":
            kind = ContextKind.TOOL_RESULT
            authority = Authority.EXTERNAL_TOOL
            trust = Trust.TOOL_UNTRUSTED
            group = (
                tool_call_groups.get(payload.get("tool_call_id"))
                if isinstance(payload, dict)
                else None
            )
            required = bool(group and group[0] == latest_tool_group)
        else:
            kind = ContextKind.MEMORY if role == "assistant" else ContextKind.UNKNOWN
            authority = Authority.RECALLED_MEMORY if role == "assistant" else Authority.UNKNOWN
            trust = Trust.USER_CONTROLLED if role == "assistant" else Trust.UNKNOWN
            required = assistant_group_by_index.get(index) == latest_tool_group
        if canonical_json_hash(payload) in untrusted_message_hashes:
            # A framework recovery capsule can contain a preview of Tool data.
            # Its wire user role must not promote that data to user authority.
            kind = ContextKind.MEMORY
            authority = Authority.RECALLED_MEMORY
            trust = Trust.TOOL_UNTRUSTED
        ref = {
            "role": role,
            "occurrence": index,
            "model_final_boundary": source_identity.startswith("model-final://"),
        }
        if index in assistant_group_by_index:
            ref["atomic_group_id"] = assistant_group_by_index[index]
            ref["atomic_group_priority"] = index
        if role == "tool" and isinstance(payload, dict):
            group = tool_call_groups.get(payload.get("tool_call_id"))
            if group is not None:
                group_id, group_priority = group
                ref["atomic_group_id"] = group_id
                ref["atomic_group_priority"] = group_priority
        context_item = ContextItem(
                id=f"{source_identity}:message:{index}",
                kind=kind,
                payload=payload,
                task_epoch=task_epoch,
                authority=authority,
                scope=scope,
                lifetime=Lifetime.TASK,
                priority=index,
                required=required or canonical_json_hash(payload) in required_message_hashes,
                trust=trust,
                stability=(
                    Stability.SESSION_STABLE
                    if role == "system" and not amni_folded_system
                    else Stability.TURN_DYNAMIC
                ),
                token_limit=None,
                reducer=None,
                source=ContextSource(
                    kind=SourceKind.AGENT,
                    uri=source_identity,
                    version="agent-final-request-v1",
                    ref=ref,
                ),
                version="v1",
                activation_reason="agent_final_request_boundary",
                occurrence=index,
            )
        if context_item.trust is Trust.TOOL_UNTRUSTED:
            context_item = isolate_untrusted_context_item(context_item).isolated_item
        message_items.append(context_item)
    tool_items = tuple(
        ContextItem(
            id=f"{source_identity}:tool:{index}",
            kind=ContextKind.TOOL_CATALOG,
            payload=payload,
            task_epoch=task_epoch,
            authority=Authority.APPLICATION_AGENT,
            scope=scope,
            lifetime=Lifetime.TASK,
            priority=index,
            required=payload.get("function", {}).get("name") in required_tool_names,
            trust=Trust.TRUSTED,
            stability=Stability.SESSION_STABLE,
            token_limit=None,
            reducer=None,
            source=ContextSource(
                kind=SourceKind.TOOL_CATALOG,
                uri=source_identity,
                version="agent-final-tool-catalog-v1",
                ref={
                    "permission_checked": True,
                    "occurrence": index,
                    "model_final_boundary": source_identity.startswith(
                        "model-final://"
                    ),
                },
            ),
            version="v1",
            activation_reason="agent_final_tool_catalog_boundary",
            occurrence=index,
        )
        for index, payload in enumerate(tools)
    )
    return (
        AdapterResult(items=tuple(message_items), diagnostics=()),
        AdapterResult(items=tool_items, diagnostics=()),
    )


def adapt_amni_folded_system_message(
    *,
    content: str,
    source_identity: str,
    task_id: str,
    task_epoch: int,
    agent_id: str,
    dynamic: bool,
) -> AdapterResult:
    """Capture the exact post-template Amni fold at its authoritative owner."""
    if not isinstance(content, str) or not content:
        raise ValueError("folded Amni system content must be non-empty")
    item = ContextItem(
        id=f"{source_identity}:folded-system",
        kind=ContextKind.SYSTEM,
        payload={"role": "system", "content": content},
        task_epoch=task_epoch,
        authority=Authority.APPLICATION_AGENT,
        scope=ContextScope(
            kinds=(ScopeKind.TASK, ScopeKind.AGENT),
            task_id=task_id,
            agent_id=agent_id,
        ),
        lifetime=Lifetime.TASK,
        priority=0,
        required=True,
        # The application owns the system role, but folded neuron content can
        # contain user-controlled memory/retrieval data.
        trust=Trust.USER_CONTROLLED,
        stability=(Stability.TURN_DYNAMIC if dynamic else Stability.SESSION_STABLE),
        token_limit=None,
        reducer=None,
        source=ContextSource(
            kind=SourceKind.AGENT,
            uri=source_identity,
            version="amni-folded-system-v1",
            ref={"amni_folded_system": True, "post_template": True},
        ),
        version="v1",
        activation_reason="amni_post_template_fold",
        occurrence=0,
    )
    return AdapterResult(items=(item,), diagnostics=())


def adapt_amni_system_sections(
    *,
    sections: Sequence[dict[str, Any]],
    source_identity: str,
    task_id: str,
    task_epoch: int,
    agent_id: str,
) -> AdapterResult:
    """Capture exact ordered Amni system sections before model assembly."""
    scope = ContextScope(
        kinds=(ScopeKind.TASK, ScopeKind.AGENT),
        task_id=task_id,
        agent_id=agent_id,
    )
    items: list[ContextItem] = []
    for occurrence, section in enumerate(sections):
        if not isinstance(section, dict):
            raise TypeError("Amni system sections must be objects")
        content = section.get("content")
        stability = section.get("stability")
        if not isinstance(content, str) or not content:
            raise ValueError("Amni system section content must be non-empty")
        if stability not in {"stable", "dynamic"}:
            raise ValueError("Amni system section stability must be explicit")
        stable = stability == "stable"
        items.append(
            ContextItem(
                id=f"{source_identity}:system-section:{occurrence}",
                kind=ContextKind.SYSTEM,
                payload={"role": "system", "content": content},
                task_epoch=task_epoch,
                authority=Authority.APPLICATION_AGENT,
                scope=scope,
                lifetime=Lifetime.TASK,
                priority=occurrence,
                required=True,
                trust=Trust.TRUSTED if stable else Trust.USER_CONTROLLED,
                stability=(
                    Stability.SESSION_STABLE if stable else Stability.TURN_DYNAMIC
                ),
                token_limit=None,
                reducer=None,
                source=ContextSource(
                    kind=SourceKind.AGENT,
                    uri=source_identity,
                    version="amni-system-sections-v1",
                    ref={
                        "amni_system_section": True,
                        "occurrence": occurrence,
                        "name": section.get("name"),
                    },
                ),
                version="v1",
                activation_reason="amni_ordered_system_section",
                occurrence=occurrence,
            )
        )
    return AdapterResult(items=tuple(items), diagnostics=())


def adapt_prompt_assembly_system_sections(
    *,
    sections: Sequence[Any],
    messages: Sequence[Any],
    source_identity: str,
    task_id: str,
    task_epoch: int,
    agent_id: str,
    user_controlled: bool,
) -> AdapterResult:
    """Bind framework-owned assembly semantics to exact final system occurrences.

    This adapter never changes the prompt.  It accepts stability evidence only
    when every declared section reproduces one and only one final system
    message in occurrence order.  Provider-specific cache lowering therefore
    consumes an immutable, provider-neutral proof instead of trusting an
    observability label or a provider name.
    """
    system_messages = tuple(
        (occurrence, payload)
        for occurrence, payload in enumerate(messages)
        if isinstance(payload, dict) and payload.get("role") == "system"
    )
    if not system_messages or len(sections) != len(system_messages):
        raise ValueError(
            "prompt assembly sections must exactly cover final system messages"
        )
    scope = ContextScope(
        kinds=(ScopeKind.TASK, ScopeKind.AGENT),
        task_id=task_id,
        agent_id=agent_id,
    )
    items: list[ContextItem] = []
    for section_ordinal, (occurrence, payload) in enumerate(system_messages):
        section = sections[section_ordinal]
        content = getattr(section, "content", None)
        stability = getattr(section, "stability", None)
        kind = getattr(section, "kind", None)
        if kind != "system" or stability not in {"stable", "dynamic"}:
            raise ValueError(
                "prompt assembly system section semantics must be explicit"
            )
        if content != payload:
            raise ValueError(
                "prompt assembly system section does not match final message"
            )
        stable = stability == "stable"
        items.append(
            ContextItem(
                id=f"{source_identity}:system-section:{section_ordinal}",
                kind=ContextKind.SYSTEM,
                payload=payload,
                task_epoch=task_epoch,
                authority=Authority.APPLICATION_AGENT,
                scope=scope,
                lifetime=Lifetime.TASK,
                priority=occurrence,
                required=True,
                trust=(
                    Trust.USER_CONTROLLED if user_controlled else Trust.TRUSTED
                ),
                stability=(
                    Stability.SESSION_STABLE if stable else Stability.TURN_DYNAMIC
                ),
                token_limit=None,
                reducer=None,
                source=ContextSource(
                    kind=SourceKind.AGENT,
                    uri=source_identity,
                    version="prompt-assembly-system-sections-v1",
                    ref={
                        "framework_prompt_assembly": True,
                        "occurrence": occurrence,
                        "section_ordinal": section_ordinal,
                        "name": getattr(section, "name", None),
                    },
                ),
                version="v1",
                activation_reason="framework_prompt_assembly_system_section",
                occurrence=occurrence,
            )
        )
    return AdapterResult(items=tuple(items), diagnostics=())


__all__ = [
    "adapt_agent_final_request",
    "adapt_amni_folded_system_message",
    "adapt_amni_system_sections",
    "adapt_prompt_assembly_system_sections",
]
