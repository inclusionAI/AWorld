from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from aworld.core.context.compiler import (
    HashEvidenceProvenance,
    ProviderRequestFidelity,
    RequestCaptureStage,
    ResolutionAction,
    ResolutionReason,
    TokenEstimate,
    observe_legacy_provider_request,
    request_trace_match,
    thaw_json,
)


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _request_parts():
    duplicate = {"role": "user", "content": ["same", {"nested": True}]}
    messages = [
        {"role": "system", "content": "private-system-instruction"},
        copy.deepcopy(duplicate),
        copy.deepcopy(duplicate),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "terminal", "arguments": '{"cmd":"secret"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": {"stdout": "private-tool-output"},
        },
    ]
    tool = {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "private-tool-description",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    tools = [copy.deepcopy(tool), copy.deepcopy(tool)]
    params = {
        "temperature": 0,
        "stop": ["private-stop"],
        "provider_extra": {"api_key": "private-key"},
    }
    return messages, tools, params


def test_observe_request_is_exact_immutable_and_occurrence_preserving() -> None:
    messages, tools, params = _request_parts()
    original = copy.deepcopy(
        {"messages": messages, "tools": tools, "params": params}
    )

    result = observe_legacy_provider_request(
        messages=messages,
        tools=tools,
        params=params,
        provider_name="test-provider",
        request_id="request-1",
        capture_stage=RequestCaptureStage.PROVIDER_PREPARED,
        fidelity=ProviderRequestFidelity.PROVIDER_PREPARED,
        source_identity="provider-request://request-1",
        task_id="task-1",
        session_id="session-1",
        task_epoch=3,
        trace_id="trace-1",
        created_at=NOW,
    )

    assert {"messages": messages, "tools": tools, "params": params} == original
    assert result.request_snapshot.thaw() == original
    assert len(result.items) == len(messages) + len(tools)
    assert [thaw_json(item.payload) for item in result.items[: len(messages)]] == messages
    assert [thaw_json(item.payload) for item in result.items[len(messages) :]] == tools
    assert result.items[1].content_hash == result.items[2].content_hash
    assert result.items[1].id != result.items[2].id
    assert result.items[-1].content_hash == result.items[-2].content_hash
    assert result.items[-1].id != result.items[-2].id
    assert result.items[3].payload["tool_calls"][0]["id"] == "call-1"
    assert result.items[4].payload["tool_call_id"] == "call-1"
    assert [decision.item_id for decision in result.decisions] == [
        item.id for item in result.items
    ]
    assert all(
        decision.action is ResolutionAction.INCLUDED
        and decision.reason is ResolutionReason.LEGACY_INCLUDED
        and decision.tokens_before == TokenEstimate.unknown()
        and decision.tokens_after == TokenEstimate.unknown()
        for decision in result.decisions
    )
    assert result.token_accounting.total_before == TokenEstimate.unknown()
    assert result.trace.stable_prefix_hash is None
    assert result.trace.serialized_prefix_hash is None
    assert result.trace.dynamic_context_hash is None
    assert result.trace.to_dict()["counts"]["included"] == len(result.items)

    messages[3]["tool_calls"][0]["function"]["arguments"] = "mutated"
    tools[0]["function"]["description"] = "mutated"
    params["provider_extra"]["api_key"] = "mutated"
    assert result.request_snapshot.thaw() == original


def test_request_trace_match_reports_exact_and_structured_mismatch_paths() -> None:
    messages, tools, params = _request_parts()
    result = observe_legacy_provider_request(
        messages=messages,
        tools=tools,
        params=params,
        created_at=NOW,
    )
    provider_bound = copy.deepcopy(result.request_snapshot.thaw())

    exact = request_trace_match(result.request_snapshot, provider_bound)
    assert exact.to_dict() == {
        "exact": True,
        "mismatch_paths": [],
        "mismatch_count": 0,
    }

    provider_bound["messages"][4]["content"]["stdout"] = "changed-secret"
    provider_bound["params"]["temperature"] = 1
    provider_bound["params"]["new_secret"] = "never-report-this-value"
    provider_bound["tools"].pop()
    mismatch = request_trace_match(result.request_snapshot, provider_bound)

    assert mismatch.exact is False
    assert mismatch.mismatch_count == 4
    assert mismatch.mismatch_paths[0].startswith(
        "/messages/4/content/key:sha256:"
    )
    assert mismatch.mismatch_paths[1].startswith("/params/key:sha256:")
    assert mismatch.mismatch_paths[2:] == (
        "/params/temperature",
        "/tools/1",
    )
    rendered = json.dumps(mismatch.to_dict())
    assert "changed-secret" not in rendered
    assert "never-report-this-value" not in rendered
    assert "stdout" not in rendered
    assert "new_secret" not in rendered


def test_serialized_evidence_is_unknown_unless_caller_supplies_it() -> None:
    messages, tools, params = _request_parts()
    unknown = observe_legacy_provider_request(
        messages=messages,
        tools=tools,
        params=params,
        created_at=NOW,
    )

    assert unknown.request_snapshot.serialized_checksum is None
    assert unknown.hash_evidence.request_content_hash.provenance is (
        HashEvidenceProvenance.CANONICAL_JSON_PAYLOAD
    )
    assert unknown.hash_evidence.serialized_prefix_hash.value is None
    assert unknown.hash_evidence.serialized_prefix_hash.provenance is (
        HashEvidenceProvenance.UNKNOWN
    )
    assert unknown.hash_evidence.serialized_request_checksum.value is None
    assert unknown.trace.serialized_prefix_hash is None
    assert {
        "stable_prefix_hash",
        "serialized_prefix_hash",
        "dynamic_context_hash",
        "serialized_request_checksum",
    }.issubset(unknown.diagnostics[-1].unknown_fields)

    supplied = observe_legacy_provider_request(
        messages=messages,
        tools=tools,
        params=params,
        created_at=NOW,
        serialized_checksum="sha256:real-provider-body",
        serialized_prefix_hash="sha256:real-provider-prefix",
    )
    assert supplied.request_snapshot.serialized_checksum == "sha256:real-provider-body"
    assert supplied.trace.serialized_prefix_hash == "sha256:real-provider-prefix"
    assert supplied.hash_evidence.serialized_prefix_hash.provenance is (
        HashEvidenceProvenance.CALLER_PROVIDED_PROVIDER_SERIALIZATION
    )
    assert supplied.hash_evidence.serialized_request_checksum.provenance is (
        HashEvidenceProvenance.CALLER_PROVIDED_PROVIDER_SERIALIZATION
    )
    assert "serialized_prefix_hash" not in supplied.diagnostics[-1].unknown_fields
    assert "serialized_request_checksum" not in supplied.diagnostics[-1].unknown_fields


def test_observe_redacted_serialization_and_unknown_hash_round_trip() -> None:
    messages, tools, params = _request_parts()
    result = observe_legacy_provider_request(
        messages=messages,
        tools=tools,
        params=params,
        source_identity="file:///private/workspace/history.jsonl",
        created_at=NOW,
    )

    redacted = result.to_redacted_dict()
    rendered = json.dumps(redacted, ensure_ascii=False)
    restored_trace = type(result.trace).from_dict(result.trace.to_dict())

    assert restored_trace == result.trace
    assert redacted["trace"]["hashes"] == {
        "stable_prefix": None,
        "serialized_prefix": None,
        "dynamic_context": None,
    }
    assert "payload" not in redacted["request"]
    for secret in (
        "private-system-instruction",
        "private-tool-output",
        "private-tool-description",
        "private-stop",
        "private-key",
        "file:///private/workspace/history.jsonl",
        '{"cmd":"secret"}',
    ):
        assert secret not in rendered


def test_observe_rejects_empty_base_source_identity() -> None:
    with pytest.raises(ValueError, match="source_identity must be a non-empty string"):
        observe_legacy_provider_request(
            messages=[],
            tools=None,
            params={},
            source_identity="  ",
            created_at=NOW,
        )
