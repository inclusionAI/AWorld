from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aworld.core.context.compiler import (
    Authority,
    CacheBreakReason,
    CacheIdentity,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    InferenceProfile,
    Lifetime,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    ResolutionAction,
    ResolutionDecision,
    ResolutionReason,
    ResolvedContext,
    ScopeKind,
    SourceKind,
    Stability,
    TokenAccounting,
    TokenEstimate,
    Trust,
    canonical_json_hash,
    freeze_json,
    thaw_json,
)


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


def _item(*, item_id: str, occurrence: int = 0) -> ContextItem:
    return ContextItem(
        id=item_id,
        kind=ContextKind.USER,
        payload={"content": "same payload", "parts": ["one", {"two": 2}]},
        task_epoch=3,
        authority=Authority.USER,
        scope=ContextScope(
            kinds=(ScopeKind.SESSION, ScopeKind.AGENT),
            session_id="session-1",
            agent_id="agent-1",
        ),
        lifetime=Lifetime.TURN,
        priority=10,
        required=True,
        trust=Trust.USER_CONTROLLED,
        stability=Stability.TURN_DYNAMIC,
        token_limit=None,
        reducer=None,
        source=ContextSource(kind=SourceKind.USER, uri="user://turn/1"),
        version="v1",
        activation_reason="current_user_turn",
        created_at=NOW,
        occurrence=occurrence,
    )


def _snapshot() -> ProviderRequestSnapshot:
    return ProviderRequestSnapshot(
        request_id="request-1",
        provider_name="test-provider",
        payload={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [],
        },
        capture_stage=RequestCaptureStage.PROVIDER_PREPARED,
        fidelity=ProviderRequestFidelity.PROVIDER_PREPARED,
    )


def test_freeze_json_is_deeply_immutable_and_thaw_preserves_order() -> None:
    original = {
        "z": [{"b": 2, "a": 1}],
        "a": {"nested": [1, 2]},
    }
    frozen = freeze_json(original)

    original["z"][0]["b"] = 99
    original["a"]["nested"].append(3)

    assert list(frozen) == ["z", "a"]
    assert thaw_json(frozen) == {
        "z": [{"b": 2, "a": 1}],
        "a": {"nested": [1, 2]},
    }
    with pytest.raises(TypeError):
        frozen["new"] = "value"
    with pytest.raises(TypeError):
        frozen["z"][0]["b"] = 3


def test_canonical_hash_is_key_order_independent_but_list_order_sensitive() -> None:
    left = {"b": 2, "a": [1, 2]}
    right = {"a": [1, 2], "b": 2}

    assert canonical_json_hash(left) == canonical_json_hash(right)
    assert canonical_json_hash(left) != canonical_json_hash({"a": [2, 1], "b": 2})


def test_context_scope_supports_composed_selectors_and_round_trip() -> None:
    scope = ContextScope(
        kinds=(ScopeKind.WORKSPACE, ScopeKind.PATH_PATTERN, ScopeKind.SESSION, ScopeKind.AGENT),
        workspace_id="workspace-1",
        path_pattern="src/**/*.py",
        session_id="session-1",
        agent_id="agent-1",
    )

    restored = ContextScope.from_dict(scope.to_dict())

    assert restored == scope
    assert restored.kinds == (
        ScopeKind.WORKSPACE,
        ScopeKind.PATH_PATTERN,
        ScopeKind.SESSION,
        ScopeKind.AGENT,
    )


def test_context_item_freezes_payload_and_content_hash_excludes_metadata() -> None:
    first = _item(item_id="random-a", occurrence=0)
    second = ContextItem.from_dict(
        {
            **first.to_dict(),
            "id": "random-b",
            "created_at": "2027-01-01T00:00:00Z",
            "occurrence": 1,
        }
    )

    assert first.content_hash == second.content_hash
    assert first.id != second.id
    assert first.occurrence != second.occurrence
    assert first.to_ref() != second.to_ref()
    assert len((first.to_ref(), second.to_ref())) == 2
    with pytest.raises(TypeError):
        first.payload["parts"][1]["two"] = 9


def test_unknown_tokens_are_not_serialized_as_zero_or_exact() -> None:
    unknown = TokenEstimate.unknown()
    actual_zero = TokenEstimate(value=0, estimator="provider", exact=True)

    assert unknown.value is None
    assert unknown.exact is False
    assert unknown.to_dict() == {"value": None, "estimator": None, "exact": False}
    assert TokenEstimate.from_dict(unknown.to_dict()) == unknown
    assert actual_zero.value == 0
    assert actual_zero != unknown

    with pytest.raises(ValueError, match="unknown token estimate"):
        TokenEstimate(value=None, estimator="provider", exact=True)


def test_unknown_provenance_dimensions_remain_explicit() -> None:
    item = ContextItem(
        id="legacy-1",
        kind=ContextKind.UNKNOWN,
        payload={"role": "system", "content": "legacy"},
        task_epoch=None,
        authority=Authority.UNKNOWN,
        scope=ContextScope.unknown(),
        lifetime=Lifetime.UNKNOWN,
        priority=0,
        required=False,
        trust=Trust.UNKNOWN,
        stability=Stability.UNKNOWN,
        token_limit=None,
        reducer=None,
        source=ContextSource.unknown(),
        version=None,
        activation_reason="legacy_unclassified",
        created_at=None,
    )

    restored = ContextItem.from_dict(item.to_dict())

    assert restored.authority is Authority.UNKNOWN
    assert restored.trust is Trust.UNKNOWN
    assert restored.stability is Stability.UNKNOWN
    assert restored.source.kind is SourceKind.UNKNOWN
    assert restored.scope.kinds == (ScopeKind.UNKNOWN,)


@pytest.mark.parametrize(
    "enum_type",
    (
        ContextKind,
        SourceKind,
        Authority,
        ScopeKind,
        Lifetime,
        Trust,
        Stability,
        ResolutionAction,
        ResolutionReason,
    ),
)
def test_all_inference_sensitive_enums_have_unknown(enum_type) -> None:
    assert enum_type.UNKNOWN.value == "unknown"


def test_provider_request_snapshot_is_immutable_and_round_trips() -> None:
    snapshot = _snapshot()
    restored = ProviderRequestSnapshot.from_dict(snapshot.to_dict())

    assert restored == snapshot
    assert restored.thaw() == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [],
    }
    assert restored.content_hash == canonical_json_hash(restored.payload)
    with pytest.raises(TypeError):
        restored.payload["messages"][0]["content"] = "changed"


def test_resolved_context_is_deeply_immutable_and_has_stable_round_trip() -> None:
    item = _item(item_id="item-1")
    decision = ResolutionDecision(
        item_id=item.id,
        action=ResolutionAction.INCLUDED,
        reason=ResolutionReason.REQUIRED,
        tokens_before=TokenEstimate.unknown(),
        tokens_after=TokenEstimate.unknown(),
        authority=item.authority,
        scope=item.scope,
        trust=item.trust,
        content_hash=item.content_hash,
        artifact_ref=None,
    )
    accounting = TokenAccounting.unknown()
    profile = InferenceProfile(
        provider="test-provider",
        model="test-model",
        reasoning_effort=None,
        execution_mode="chat",
        context_limit=None,
    )
    identity = CacheIdentity(
        inference_profile=profile,
        serialization_version="v1",
        policy_version="v1",
        tool_catalog_hash=canonical_json_hash([]),
        skill_set_hash=canonical_json_hash([]),
        serialized_prefix_hash=canonical_json_hash(["prefix"]),
        provider_cache_namespace=None,
    )
    resolved = ResolvedContext(
        messages=[{"role": "user", "content": ["hello"]}],
        tools=[{"type": "function", "function": {"name": "search"}}],
        provider_params={"temperature": 0},
        stable_prefix_hash=canonical_json_hash(["stable"]),
        serialized_prefix_hash=canonical_json_hash(["serialized"]),
        dynamic_context_hash=canonical_json_hash(["dynamic"]),
        cache_identity=identity,
        cache_break_reason=CacheBreakReason.UNKNOWN,
        token_accounting=accounting,
        decisions=(decision,),
        request_snapshot=_snapshot(),
        compiler_version="compiler-v1",
    )

    restored = ResolvedContext.from_dict(resolved.to_dict())

    assert restored == resolved
    assert restored.messages[0]["content"] == ("hello",)
    with pytest.raises(TypeError):
        restored.provider_params["temperature"] = 1
    assert json.loads(json.dumps(restored.to_dict())) == restored.to_dict()


def test_invalid_non_json_payload_is_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported JSON value"):
        freeze_json({"bad": object()})
