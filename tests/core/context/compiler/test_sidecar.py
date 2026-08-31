from __future__ import annotations

import json

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    AdapterDiagnostic,
    AdapterDiagnosticSeverity,
    AdapterResult,
    ContextObservationSidecar,
    adapt_final_messages,
)


def _sidecar(secret: str = "private-owner-output") -> ContextObservationSidecar:
    result = adapt_final_messages(
        [{"role": "system", "content": secret}],
        source_identity="owner://private/path",
    )
    return ContextObservationSidecar.from_adapter_result(
        owner="test.owner",
        namespace="agent-1",
        source_identity="owner://private/path",
        result=result,
    )


def test_context_observation_sidecar_is_immutable_and_redacted_by_default() -> None:
    sidecar = _sidecar()

    assert isinstance(sidecar.result, AdapterResult)
    assert sidecar.owner == "test.owner"
    assert sidecar.namespace == "agent-1"
    rendered = json.dumps(sidecar.to_redacted_dict(), ensure_ascii=False)
    assert "private-owner-output" not in rendered
    assert "owner://private/path" not in rendered
    assert "namespace_hash" not in sidecar.to_redacted_dict()
    assert "source_identity_hash" not in sidecar.to_redacted_dict()
    assert sidecar.to_redacted_dict()["items"][0]["item_id"].startswith(
        "item:sha256:"
    )


def test_redacted_sidecar_never_emits_owner_controlled_diagnostic_strings() -> None:
    secret = "api-key-SHOULD-NOT-LEAK"
    original = _sidecar()
    sidecar = ContextObservationSidecar.from_adapter_result(
        owner=original.owner,
        namespace=original.namespace,
        source_identity=original.source_identity,
        result=AdapterResult(
            items=original.result.items,
            diagnostics=(
                AdapterDiagnostic(
                    code=f"owner_{secret}",
                    message=f"message {secret}",
                    severity=AdapterDiagnosticSeverity.WARNING,
                    source_identity=f"source://{secret}",
                    occurrence=0,
                    unknown_fields=(f"field_{secret}",),
                ),
            ),
        ),
    )

    payload = sidecar.to_redacted_dict()
    rendered = json.dumps(payload, ensure_ascii=False)

    assert secret not in rendered
    assert payload["diagnostics"] == [
        {
            "code_hash": payload["diagnostics"][0]["code_hash"],
            "severity": "warning",
            "occurrence": 0,
            "unknown_field_count": 1,
        }
    ]
    assert payload["diagnostics"][0]["code_hash"].startswith("sha256:")


def test_context_stores_sidecars_outside_serialized_context_state_and_copies_them() -> None:
    context = Context(task_id="task-sidecar")
    sidecar = _sidecar()

    context.publish_context_observation(sidecar)

    assert context.get_context_observations(
        owner="test.owner", namespace="agent-1"
    ) == (sidecar,)
    assert "context_observations" not in context.context_info

    copied = context.deep_copy()
    assert copied.get_context_observations(
        owner="test.owner", namespace="agent-1"
    ) == (sidecar,)

    legacy_context = Context(task_id="legacy-context")
    del legacy_context._context_observations
    assert legacy_context.get_context_observations() == ()
    legacy_context.publish_context_observation(sidecar)
    assert legacy_context.get_context_observations() == (sidecar,)


@pytest.mark.asyncio
async def test_context_clears_request_observations_at_child_task_boundary() -> None:
    context = Context(task_id="parent-task")
    context.publish_context_observation(_sidecar())

    child = await context.build_sub_context("child input", "child-task")

    assert child.task_id == "child-task"
    assert child.get_context_observations() == ()
    assert context.get_context_observations()
