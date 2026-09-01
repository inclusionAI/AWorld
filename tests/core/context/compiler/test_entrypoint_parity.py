from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aworld.core.context.compiler import (
    AttributionOwnerCode,
    Authority,
    BudgetAllocationTier,
    ContextEmissionKind,
    ContextEntryPoint,
    ContextEntrypointParityReceipt,
    ContextInputBudget,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    FinalCompileCandidate,
    FinalCompileInput,
    FinalCompilePolicy,
    InferenceProfile,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    TokenEstimate,
    Trust,
    assess_entrypoint_parity,
    compile_final_context,
)


def _result(
    request_id: str = "request-a",
    *,
    model: str = "gpt-test",
    reasoning_effort: str | None = None,
    max_item_tokens: int = 2048,
    task_id: str = "task",
):
    item = ContextItem(
        id="user-occurrence-0",
        kind=ContextKind.USER,
        payload={"role": "user", "content": "same semantic input"},
        task_epoch=1,
        authority=Authority.USER,
        scope=ContextScope(kinds=(ScopeKind.TASK,), task_id=task_id),
        lifetime=Lifetime.TASK,
        priority=0,
        required=True,
        trust=Trust.TRUSTED,
        stability=Stability.TURN_DYNAMIC,
        token_limit=None,
        reducer=None,
        source=ContextSource(kind=SourceKind.USER),
        occurrence=0,
    )
    return compile_final_context(
        compiler_input=FinalCompileInput(
            request_id=request_id,
            provider_name="openai",
            provider_params={"temperature": 0},
            candidates=(
                FinalCompileCandidate(
                    item=item,
                    tokens=TokenEstimate(4, "parity-test-v1", False),
                    allocation_tier=BudgetAllocationTier(0, "required"),
                    emission=ContextEmissionKind.MESSAGE,
                    semantics_proven=True,
                    owner_code=AttributionOwnerCode.MODEL_FINAL_MESSAGES,
                ),
            ),
            inference_profile=InferenceProfile(
                provider="openai",
                model=model,
                reasoning_effort=reasoning_effort,
                execution_mode="chat_completions",
                context_limit=4096,
            ),
            created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            task_id=task_id,
            task_epoch=1,
        ),
        policy=FinalCompilePolicy(
            compiler_version="parity-test-v1",
            policy_version="policy-v1",
            input_budget=ContextInputBudget(
                4096, 64, 16, 16, max_item_tokens
            ),
        ),
    )


def test_all_entrypoints_share_one_semantic_fingerprint_despite_request_ids():
    entries = (
        ContextEntryPoint.AGENT,
        ContextEntryPoint.AMNI,
        ContextEntryPoint.CLI,
        ContextEntryPoint.ACP,
        ContextEntryPoint.RESUME,
    )
    receipts = tuple(
        ContextEntrypointParityReceipt.from_final_result(
            entry_point=entry,
            result=_result(
                f"request-{index}", task_id=f"runtime-task-{index}"
            ),
        )
        for index, entry in enumerate(entries)
    )

    status = assess_entrypoint_parity(
        receipts, required_entry_points=entries
    )

    assert status["status"] == "available"
    assert status["reason_code"] is None
    assert len(set(status["fingerprints"].values())) == 1
    assert "same semantic input" not in repr([receipt.to_dict() for receipt in receipts])


def test_parity_detects_semantic_change_and_incomplete_evidence():
    normal = _result()
    changed = replace(normal, tool_catalog_hash="sha256:" + "1" * 64)
    receipts = (
        ContextEntrypointParityReceipt.from_final_result(
            entry_point="agent", result=normal
        ),
        ContextEntrypointParityReceipt.from_final_result(
            entry_point="cli", result=changed
        ),
    )

    mismatch = assess_entrypoint_parity(
        receipts, required_entry_points=("agent", "cli")
    )
    assert mismatch["status"] == "mismatch"
    assert mismatch["reason_code"] == "entrypoint_semantics_mismatch"

    incomplete = assess_entrypoint_parity(
        receipts[:1], required_entry_points=("agent", "cli")
    )
    assert incomplete == {
        "status": "unavailable",
        "reason_code": "entrypoint_evidence_incomplete",
        "missing": ["cli"],
        "duplicates": [],
    }


@pytest.mark.parametrize(
    "changed",
    (
        _result(model="gpt-other"),
        _result(reasoning_effort="high"),
        _result(max_item_tokens=1024),
    ),
)
def test_parity_preserves_inference_and_budget_semantics(changed):
    receipts = (
        ContextEntrypointParityReceipt.from_final_result(
            entry_point="agent", result=_result()
        ),
        ContextEntrypointParityReceipt.from_final_result(
            entry_point="cli", result=changed
        ),
    )

    status = assess_entrypoint_parity(
        receipts, required_entry_points=("agent", "cli")
    )

    assert status["status"] == "mismatch"


def test_serialized_parity_receipt_is_independently_revalidated():
    receipt = ContextEntrypointParityReceipt.from_final_result(
        entry_point="agent", result=_result()
    )
    payload = receipt.to_dict()
    assert ContextEntrypointParityReceipt.from_dict(payload) == receipt

    payload["semantic_projection"]["policy_version"] = "tampered"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        ContextEntrypointParityReceipt.from_dict(payload)
