from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from aworld.core.context.compiler import (
    Authority,
    CacheBreakReason,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    InferenceProfile,
    InsufficientSerializedPrefixEvidence,
    Lifetime,
    SourceKind,
    SerializedPrefixEvidence,
    SerializedPrefixProvenance,
    StablePrefixPartition,
    Stability,
    Trust,
    build_cache_identity,
    cache_break_reasons,
    canonical_json_hash,
    partition_stable_prefix,
    serialized_prefix_checksum,
)


def _item(item_id: str, stability: Stability, content: str) -> ContextItem:
    return ContextItem(
        id=item_id,
        kind=ContextKind.SYSTEM,
        payload={"content": content},
        task_epoch=1,
        authority=Authority.PLATFORM_SYSTEM,
        scope=ContextScope.unknown(),
        lifetime=Lifetime.TASK,
        priority=0,
        required=False,
        trust=Trust.TRUSTED,
        stability=stability,
        token_limit=None,
        reducer=None,
        source=ContextSource(kind=SourceKind.PLATFORM),
        version="v1",
        activation_reason="test",
    )


def _profile() -> InferenceProfile:
    return InferenceProfile(
        provider="provider-a",
        model="model-a",
        reasoning_effort="medium",
        execution_mode="tools",
        context_limit=1000,
        response_format_hash=canonical_json_hash({"type": "json"}),
    )


def _identity(**overrides):
    values = {
        "inference_profile": _profile(),
        "policy_version": "policy-v1",
        "tool_catalog_hash": canonical_json_hash(["tool-a"]),
        "skill_set_hash": canonical_json_hash(["skill-a"]),
        "serialized_prefix_evidence": SerializedPrefixEvidence.provider_wire(
            serialized_prefix=b'{"system":"stable"',
            serialized_request=b'{"system":"stable","messages":[]}',
            provider_name="provider-a",
            adapter_identity="test-http-adapter-v1",
            serialization_version="provider-wire-v1",
            request_id="request-1",
        ),
        "provider_cache_namespace": "cache-a",
    }
    values.update(overrides)
    return build_cache_identity(**values).identity


def test_partition_is_contiguous_and_never_reorders_late_stable_items() -> None:
    items = (
        _item("policy", Stability.STABLE, "policy"),
        _item("workspace", Stability.SESSION_STABLE, "workspace"),
        _item("user", Stability.TURN_DYNAMIC, "question"),
        _item("late-stable", Stability.STABLE, "must stay after user"),
    )

    partition = partition_stable_prefix(items)

    assert [item.id for item in partition.stable_items] == ["policy", "workspace"]
    assert [item.id for item in partition.dynamic_items] == ["user", "late-stable"]

    changed = partition_stable_prefix(
        (*items[:2], _item("user", Stability.TURN_DYNAMIC, "new question"), items[3])
    )
    assert changed.stable_prefix_hash == partition.stable_prefix_hash
    assert changed.dynamic_context_hash != partition.dynamic_context_hash

    with pytest.raises(ValueError, match="does not match"):
        StablePrefixPartition(
            stable_items=partition.stable_items,
            dynamic_items=partition.dynamic_items,
            stable_prefix_hash="sha256:forged",
            dynamic_context_hash=partition.dynamic_context_hash,
        )
    with pytest.raises(ValueError, match="must begin at a non-stable item"):
        StablePrefixPartition(
            stable_items=(),
            dynamic_items=(items[0],),
            stable_prefix_hash=canonical_json_hash([]),
            dynamic_context_hash=canonical_json_hash(
                [
                    {
                        "item_id": items[0].id,
                        "version": items[0].version,
                        "content_hash": items[0].content_hash,
                    }
                ]
            ),
        )


def test_serialized_prefix_hashes_exact_bytes_not_logical_json() -> None:
    compact = b'{"a":1,"b":2}'
    spaced = b'{"a": 1, "b": 2}'

    assert serialized_prefix_checksum(compact) != serialized_prefix_checksum(spaced)
    with pytest.raises(TypeError, match="must be bytes"):
        serialized_prefix_checksum('{"a":1}')  # type: ignore[arg-type]


def test_cache_identity_round_trip_carries_response_format_and_wire_checksum() -> None:
    evidence = SerializedPrefixEvidence.provider_wire(
        serialized_prefix=b'{"system":"stable"',
        serialized_request=b'{"system":"stable","messages":[]}',
        provider_name="provider-a",
        adapter_identity="private-adapter-path-v1",
        serialization_version="provider-wire-v1",
        request_id="private-request-1",
    )
    verified = build_cache_identity(
        inference_profile=_profile(),
        policy_version="policy-v1",
        tool_catalog_hash=canonical_json_hash([]),
        skill_set_hash=canonical_json_hash([]),
        serialized_prefix_evidence=evidence,
    )
    identity = verified.identity

    restored = type(identity).from_dict(identity.to_dict())

    assert restored == identity
    assert restored.inference_profile.response_format_hash
    assert restored.serialized_prefix_hash == serialized_prefix_checksum(
        b'{"system":"stable"'
    )
    rendered = (
        str(verified.to_redacted_dict())
        + repr(verified)
        + str(asdict(verified))
    )
    assert "private-adapter-path-v1" not in rendered
    assert "private-request-1" not in rendered
    assert '"system":"stable"' not in rendered


def test_cache_break_reasons_are_precise_complete_and_stably_ordered() -> None:
    previous = _identity()
    profile = replace(
        previous.inference_profile,
        provider="provider-b",
        model="model-b",
        reasoning_effort="high",
        execution_mode="chat",
        context_limit=2000,
        response_format_hash=canonical_json_hash({"type": "text"}),
    )
    current = _identity(
        inference_profile=profile,
        policy_version="policy-v2",
        tool_catalog_hash=canonical_json_hash(["tool-b"]),
        skill_set_hash=canonical_json_hash(["skill-b"]),
        serialized_prefix_evidence=SerializedPrefixEvidence.provider_wire(
            serialized_prefix=b"different bytes",
            serialized_request=b"different bytes plus suffix",
            provider_name="provider-b",
            adapter_identity="test-http-adapter-v2",
            serialization_version="provider-wire-v2",
            request_id="request-2",
        ),
        provider_cache_namespace="cache-b",
    )

    assert cache_break_reasons(
        previous,
        current,
        history_compaction=True,
        task_reset=True,
        resume_cache_expired=True,
        provider_cache_unknown=True,
    ) == (
        CacheBreakReason.PROVIDER_CHANGE,
        CacheBreakReason.MODEL_CHANGE,
        CacheBreakReason.EFFORT_CHANGE,
        CacheBreakReason.EXECUTION_MODE_CHANGE,
        CacheBreakReason.RESPONSE_FORMAT_CHANGE,
        CacheBreakReason.CONTEXT_LIMIT_CHANGE,
        CacheBreakReason.TOOL_CATALOG_CHANGE,
        CacheBreakReason.SKILL_SET_CHANGE,
        CacheBreakReason.POLICY_VERSION_CHANGE,
        CacheBreakReason.SERIALIZATION_CHANGE,
        CacheBreakReason.SERIALIZED_PREFIX_CHANGE,
        CacheBreakReason.PROVIDER_CACHE_NAMESPACE_CHANGE,
        CacheBreakReason.HISTORY_COMPACTION,
        CacheBreakReason.TASK_RESET,
        CacheBreakReason.RESUME_CACHE_EXPIRED,
        CacheBreakReason.PROVIDER_CACHE_UNKNOWN,
    )

    assert cache_break_reasons(previous, previous) == ()
    assert cache_break_reasons(None, current, task_reset=True) == (
        CacheBreakReason.TASK_RESET,
    )
    with pytest.raises(TypeError, match="task_reset must be a boolean"):
        cache_break_reasons(previous, current, task_reset="yes")  # type: ignore[arg-type]


def test_cache_identity_rejects_logical_or_unbound_prefix_bytes() -> None:
    logical = SerializedPrefixEvidence.unverified(
        SerializedPrefixProvenance.LOGICAL_CANONICAL_JSON,
    )

    with pytest.raises(InsufficientSerializedPrefixEvidence) as captured:
        build_cache_identity(
            inference_profile=_profile(),
            policy_version="policy-v1",
            tool_catalog_hash=canonical_json_hash([]),
            skill_set_hash=canonical_json_hash([]),
            serialized_prefix_evidence=logical,
        )

    assert captured.value.code == "serialized_prefix_evidence_insufficient"

    wrong_provider = SerializedPrefixEvidence.provider_wire(
        serialized_prefix=b"prefix",
        serialized_request=b"prefix and suffix",
        provider_name="provider-b",
        adapter_identity="adapter-v1",
        serialization_version="wire-v1",
        request_id="request-1",
    )
    with pytest.raises(
        InsufficientSerializedPrefixEvidence,
        match="provider_mismatch",
    ):
        build_cache_identity(
            inference_profile=_profile(),
            policy_version="policy-v1",
            tool_catalog_hash=canonical_json_hash([]),
            skill_set_hash=canonical_json_hash([]),
            serialized_prefix_evidence=wrong_provider,
        )

    with pytest.raises(
        InsufficientSerializedPrefixEvidence,
        match="not_request_prefix",
    ):
        SerializedPrefixEvidence.provider_wire(
            serialized_prefix=b"not a prefix",
            serialized_request=b"actual provider request",
            provider_name="provider-a",
            adapter_identity="adapter-v1",
            serialization_version="wire-v1",
            request_id="request-2",
        )
