"""Read-only redacted projection over compiler truth objects."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .final import FinalCompileResult
from .frozen_json import canonical_json_hash
from .models import ResolutionAction
from .attribution import summarize_attribution_plan


def inspect_final_context(result: FinalCompileResult) -> dict[str, Any]:
    """Project existing trace/decisions; never re-run compilation or token math."""
    if not isinstance(result, FinalCompileResult):
        raise TypeError("result must be a FinalCompileResult")
    actions = Counter(decision.action.value for decision in result.decisions)
    reasons = Counter(decision.reason.value for decision in result.decisions)
    offloaded = [
        {
            "item_hash": decision.content_hash,
            "artifact_present": decision.artifact_ref is not None,
            "artifact_ref_hash": (
                canonical_json_hash({"artifact_ref": decision.artifact_ref})
                if decision.artifact_ref is not None
                else None
            ),
            "tokens_before": decision.tokens_before.to_dict(),
            "tokens_after": decision.tokens_after.to_dict(),
        }
        for decision in result.decisions
        if decision.action is ResolutionAction.OFFLOADED
    ]
    attribution = (
        {
            **summarize_attribution_plan(result.attribution_plan.entries),
            "request_id_hash": result.attribution_plan.request_id_hash,
            "candidate_content_hash": result.attribution_plan.candidate_content_hash,
            "plan_fingerprint": result.attribution_plan.fingerprint,
            "entries": [
                entry.to_redacted_dict()
                for entry in result.attribution_plan.entries
            ],
        }
        if result.attribution_plan is not None
        else {"status": "unavailable", "reason_code": "legacy_result_without_attribution"}
    )
    return {
        "schema_version": "aworld.context.inspector.v1",
        "compiler": {
            "identity": result.compiler_identity,
            "version": result.compiler_version,
            "policy_version": result.policy_version,
        },
        "request": {
            "content_hash": result.request_snapshot.content_hash,
            "capture_stage": result.request_snapshot.capture_stage.value,
            "fidelity": result.request_snapshot.fidelity.value,
        },
        "partition": {
            "stable_prefix_hash": result.stable_partition.stable_prefix_hash,
            "dynamic_context_hash": result.stable_partition.dynamic_context_hash,
            "tool_catalog_hash": result.tool_catalog_hash,
            "skill_set_hash": result.skill_set_hash,
        },
        "tokens": result.token_accounting.to_dict(),
        "attribution": attribution,
        "decisions": {
            "action_counts": dict(sorted(actions.items())),
            "reason_counts": dict(sorted(reasons.items())),
            "offloaded": offloaded,
        },
        "enforce": {
            "ready": result.enforce_ready,
            "blocker_codes": list(result.blocker_codes),
        },
        # ContextDecisionTrace is already constructed from redacted item refs,
        # scopes, and artifact-presence metadata.
        "trace": result.trace.to_dict(),
    }


__all__ = ["inspect_final_context"]
