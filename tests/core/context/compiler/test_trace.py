from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aworld.core.context.compiler import (
    Authority,
    ContextDecisionTrace,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    Lifetime,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    ResolutionAction,
    ResolutionDecision,
    ResolutionReason,
    ScopeKind,
    SourceKind,
    Stability,
    TokenAccounting,
    TokenEstimate,
    Trust,
)


def _sensitive_item(item_id: str, occurrence: int) -> ContextItem:
    return ContextItem(
        id=item_id,
        kind=ContextKind.INSTRUCTION,
        payload={
            "content": "do not reveal ultra-secret-token",
            "absolute_path": "/Users/private/project/AWORLD.md",
            "provider_extra": {"api_key": "sk-private-value"},
        },
        task_epoch=1,
        authority=Authority.WORKSPACE,
        scope=ContextScope(
            kinds=(ScopeKind.WORKSPACE, ScopeKind.PATH_PATTERN),
            workspace_id="/Users/private/project",
            path_pattern="/Users/private/project/**/*.py",
        ),
        lifetime=Lifetime.WORKSPACE,
        priority=5,
        required=True,
        trust=Trust.TRUSTED,
        stability=Stability.STABLE,
        token_limit=1000,
        reducer=None,
        source=ContextSource(
            kind=SourceKind.WORKSPACE_FILE,
            uri="file:///Users/private/project/AWORLD.md",
            version="v3",
            ref={"provider_extra": {"authorization": "Bearer secret"}},
        ),
        version="v3",
        activation_reason="workspace_instruction",
        created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        occurrence=occurrence,
    )


def _decision(item: ContextItem) -> ResolutionDecision:
    return ResolutionDecision(
        item_id=item.id,
        action=ResolutionAction.INCLUDED,
        reason=ResolutionReason.REQUIRED,
        tokens_before=TokenEstimate(value=12, estimator="test", exact=False),
        tokens_after=TokenEstimate(value=12, estimator="test", exact=False),
        authority=item.authority,
        scope=item.scope,
        trust=item.trust,
        content_hash=item.content_hash,
        artifact_ref="/Users/private/project/.aworld/artifacts/raw-secret.txt",
    )


def _snapshot() -> ProviderRequestSnapshot:
    return ProviderRequestSnapshot(
        request_id="request-random",
        provider_name="openai-compatible",
        payload={
            "messages": [{"role": "system", "content": "ultra-secret-token"}],
            "extra_body": {"authorization": "Bearer secret"},
        },
        capture_stage=RequestCaptureStage.PROVIDER_PREPARED,
        fidelity=ProviderRequestFidelity.PROVIDER_PREPARED,
    )


def test_default_trace_contains_only_redacted_refs_and_decision_metadata() -> None:
    item = _sensitive_item("random-item-id", 0)
    trace = ContextDecisionTrace.build(
        trace_id="trace-random",
        task_id="task-1",
        session_id="session-1",
        task_epoch=1,
        compiler_version="compiler-v1",
        items=(item,),
        decisions=(_decision(item),),
        token_accounting=TokenAccounting.unknown(),
        stable_prefix_hash="sha256:" + "1" * 64,
        serialized_prefix_hash="sha256:" + "2" * 64,
        dynamic_context_hash="sha256:" + "3" * 64,
        request_snapshot=_snapshot(),
        created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    payload = trace.to_dict()
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["items"][0]["preview"] == "<object fields=3>"
    assert payload["items"][0]["source_kind"] == "workspace_file"
    assert payload["decisions"][0]["artifact_present"] is True
    assert payload["decisions"][0]["scope_kinds"] == ["workspace", "path_pattern"]
    assert trace.decisions[0].artifact_ref == "<redacted>"
    assert trace.decisions[0].scope.workspace_id == "<redacted>"
    assert trace.decisions[0].scope.path_pattern == "<redacted>"
    assert payload["request"]["content_hash"] == _snapshot().content_hash
    assert "payload" not in payload["request"]
    for secret in (
        "ultra-secret-token",
        "sk-private-value",
        "Bearer secret",
        "/Users/private/project",
        "file:///Users/private/project/AWORLD.md",
        "raw-secret.txt",
        "extra_body",
        "api_key",
        "authorization",
    ):
        assert secret not in rendered


def test_trace_fingerprint_excludes_trace_item_ids_and_created_at() -> None:
    first_item = _sensitive_item("random-a", 0)
    second_item = ContextItem.from_dict(
        {
            **first_item.to_dict(),
            "id": "random-b",
            "created_at": "2027-01-01T00:00:00Z",
        }
    )
    first = ContextDecisionTrace.build(
        trace_id="trace-a",
        task_id="task-a",
        session_id="session-a",
        task_epoch=1,
        compiler_version="compiler-v1",
        items=(first_item,),
        decisions=(_decision(first_item),),
        token_accounting=TokenAccounting.unknown(),
        stable_prefix_hash="stable",
        serialized_prefix_hash="serialized",
        dynamic_context_hash="dynamic",
        request_snapshot=_snapshot(),
        created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    second = ContextDecisionTrace.build(
        trace_id="trace-b",
        task_id="task-b",
        session_id="session-b",
        task_epoch=1,
        compiler_version="compiler-v1",
        items=(second_item,),
        decisions=(_decision(second_item),),
        token_accounting=TokenAccounting.unknown(),
        stable_prefix_hash="stable",
        serialized_prefix_hash="serialized",
        dynamic_context_hash="dynamic",
        request_snapshot=ProviderRequestSnapshot.from_dict(
            {**_snapshot().to_dict(), "request_id": "another-random-request"}
        ),
        created_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )

    assert first.fingerprint == second.fingerprint


def test_trace_preserves_duplicate_occurrences_and_round_trip_projection() -> None:
    first = _sensitive_item("item-1", 0)
    second = ContextItem.from_dict(
        {**first.to_dict(), "id": "item-2", "occurrence": 1}
    )
    trace = ContextDecisionTrace.build(
        trace_id=None,
        task_id="task-1",
        session_id=None,
        task_epoch=1,
        compiler_version="compiler-v1",
        items=(first, second),
        decisions=(_decision(first), _decision(second)),
        token_accounting=TokenAccounting.unknown(),
        stable_prefix_hash="stable",
        serialized_prefix_hash="serialized",
        dynamic_context_hash="dynamic",
        request_snapshot=_snapshot(),
        created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    payload = trace.to_dict()
    restored = ContextDecisionTrace.from_dict(payload)

    assert [item["occurrence"] for item in payload["items"]] == [0, 1]
    assert payload["counts"] == {
        "candidates": 2,
        "included": 2,
        "excluded": 0,
        "compacted": 0,
        "offloaded": 0,
        "unknown": 0,
    }
    assert restored.to_dict() == payload
    assert restored.fingerprint == trace.fingerprint


def test_trace_requires_one_decision_per_candidate() -> None:
    item = _sensitive_item("item-1", 0)

    with pytest.raises(ValueError, match="exactly one resolution decision"):
        ContextDecisionTrace.build(
            trace_id=None,
            task_id="task-1",
            session_id=None,
            task_epoch=1,
            compiler_version="compiler-v1",
            items=(item,),
            decisions=(),
            token_accounting=TokenAccounting.unknown(),
            stable_prefix_hash="stable",
            serialized_prefix_hash="serialized",
            dynamic_context_hash="dynamic",
            request_snapshot=_snapshot(),
            created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        )
