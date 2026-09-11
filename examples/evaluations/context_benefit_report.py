"""Aggregate real paired experiment artifacts into benefit/readiness evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import lzma
import math
import re
import statistics
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aworld.core.context.compiler import (  # noqa: E402
    AttributionCollection,
    AttributionOwnerCode,
    AttributionSerialization,
    CanaryHealthDecision,
    CanaryHealthEvidence,
    CanaryHealthPolicy,
    ContextLifecycleState,
    ContextKind,
    RollbackBundle,
    RolloutCapability,
    VerifiedContextEntrypointParityReceipt,
    assess_canary_health,
    assess_default_on_readiness,
    canonical_json_hash,
    FrozenMap,
    LogicalResidency,
    ProviderToolsLowering,
    SourceKind,
    Stability,
    canonical_json_bytes,
    thaw_json,
)
from aworld.core.trajectory import TrajectoryBuildResult  # noqa: E402
from aworld.evaluations.context_benefit import (  # noqa: E402
    ContextAblationComponent,
    ContextAblationContrast,
    ContextAblationPlan,
    ContextEvaluationManifest,
    ContextTrialEvidence,
    ContextVariant,
    TrialFidelity,
    build_paired_deltas,
    summarize_context_benefit,
    summarize_stratified_context_benefit,
)
from aworld.evaluations.normalized_cost import (  # noqa: E402
    NormalizedCostBoundReceipt,
    NormalizedCostPolicy,
    NormalizedCostReceipt,
    compute_normalized_cost,
)
from aworld.models.usage import reconcile_cache_usage_receipt  # noqa: E402


_COST_BENEFIT_METRICS = (
    "cost_per_successful_task",
    "provider_billed_cost",
    "normalized_cost_microunits",
    "normalized_cost_conservative_delta_microunits",
)
_EXECUTION_EFFICIENCY_METRICS = ("provider_call_count",)

_TURN_CAUSES = {
    "initial_input",
    "model_choice",
    "validation_repair",
    "framework_retry",
    "deferred_catalog_expansion",
    "deferred_skill_expansion",
    "artifact_retrieval",
    "unavailable",
}
_SUPPORTED_TURN_CAUSES = {
    "initial_input",
    "model_choice",
    "framework_retry",
    "artifact_retrieval",
}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SEMANTIC_PROGRESS_COUNTS = frozenset(
    {
        "semantic_tool_observation_count",
        "repeated_operation_count",
        "low_information_gain_count",
        "task_artifact_change_count",
        "goal_progress_count",
        "no_goal_progress_observation_count",
        "sandbox_rollback_count",
        "implicit_artifact_loss_count",
        "watchdog_trigger_count",
        "sanitized_history_retry_count",
        "tool_success_to_next_llm_count",
        "agent_step_count",
        "agent_loop_budget_exhausted_count",
        "configured_max_steps",
        "adaptive_checkpoint_count",
        "adaptive_no_progress_checkpoint_count",
        "adaptive_escalation_count",
        "adaptive_escalation_level_max",
        "adaptive_goal_progress_reset_count",
        "agent_step_budget_extension_count",
        "agent_step_budget_extended_steps",
        "agent_step_budget_effective_limit",
        "agent_step_budget_hard_limit",
    }
)


def _action_results(raw_trajectory: Any) -> list[dict[str, Any]]:
    """Read typed ActionResult slots without classifying transcript text."""
    found: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        action_results = value.get("action_result")
        if isinstance(action_results, list):
            found.extend(item for item in action_results if isinstance(item, dict))
        for key, item in value.items():
            if key != "action_result":
                visit(item)

    visit(raw_trajectory)
    return found


def _valid_turn_receipt(value: Any, *, expected_kind: str) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "aworld.context.turn-economics.v1"
    ):
        return False
    cause = value.get("cause")
    supported = value.get("cause_supported")
    return bool(
        value.get("turn_kind") == expected_kind
        and cause in _TURN_CAUSES
        and isinstance(supported, bool)
        and supported == (cause in _SUPPORTED_TURN_CAUSES)
        and _SHA256.fullmatch(str(value.get("turn_id_hash", "")))
        and (
            expected_kind != "model"
            or _SHA256.fullmatch(str(value.get("request_id_hash", "")))
        )
        and (
            expected_kind != "tool"
            or _SHA256.fullmatch(str(value.get("tool_call_id_hash", "")))
        )
    )


def _valid_retrieval_receipt(value: Any, *, action_content: Any = None) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "aworld.context.artifact-retrieval-receipt.v1"
    ):
        return False
    ints = (
        "artifact_byte_count",
        "offset",
        "limit",
        "returned_offset",
        "next_offset",
        "returned_byte_count",
    )
    if any(
        isinstance(value.get(key), bool)
        or not isinstance(value.get(key), int)
        or value[key] < 0
        for key in ints
    ):
        return False
    if (
        value["limit"] <= 0
        or value["offset"] > value["artifact_byte_count"]
        or not isinstance(value.get("complete"), bool)
    ):
        return False
    hashes = (
        "plan_fingerprint",
        "owner_code",
        "action_code",
        "artifact_ref_hash",
        "artifact_content_hash",
        "consumer_tool_call_id_hash",
        "chunk_checksum",
        "source_content_hash",
        "result_content_hash",
    )
    if any(not _SHA256.fullmatch(str(value.get(key, ""))) for key in hashes):
        return False
    plan_projection = {
        "schema_version": "aworld.context.artifact-retrieval-plan.v1",
        **{
            key: value[key]
            for key in (
                "owner_code",
                "action_code",
                "artifact_ref_hash",
                "artifact_content_hash",
                "artifact_byte_count",
                "offset",
                "limit",
                "consumer_tool_call_id_hash",
            )
        },
    }
    if value_hash(plan_projection) != value["plan_fingerprint"]:
        return False
    if value["source_content_hash"] != value["artifact_content_hash"]:
        return False
    if (
        value["returned_offset"] != value["offset"]
        or value["next_offset"] - value["returned_offset"]
        != value["returned_byte_count"]
    ):
        return False
    if (
        value["returned_byte_count"] > value["limit"]
        or value["next_offset"] > value["artifact_byte_count"]
    ):
        return False
    consumed = value.get("consumed")
    next_request = value.get("next_request_id_hash")
    consumed_hash = value.get("consumed_content_hash")
    structurally_valid = bool(
        isinstance(consumed, bool)
        and consumed == (next_request is not None)
        and (next_request is None or _SHA256.fullmatch(str(next_request)))
        and (consumed_hash is None or _SHA256.fullmatch(str(consumed_hash)))
        and ((next_request is None) == (consumed_hash is None))
        and (consumed_hash is None or consumed_hash == value.get("result_content_hash"))
    )
    if not structurally_valid or action_content is None:
        return structurally_valid
    content = action_content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    if not isinstance(content, dict):
        return False
    chunk_value = content.get("content")
    try:
        chunk = (
            base64.b64decode(chunk_value, validate=True)
            if content.get("type") == "base64" and isinstance(chunk_value, str)
            else chunk_value.encode("utf-8")
            if content.get("type", "text") == "text" and isinstance(chunk_value, str)
            else None
        )
    except (TypeError, ValueError):
        return False
    if chunk is None:
        return False
    return bool(
        value_hash(action_content) == value.get("result_content_hash")
        and content.get("artifact_ref") is not None
        and content.get("offset") == value.get("returned_offset")
        and content.get("next_offset") == value.get("next_offset")
        and content.get("returned_bytes")
        == len(chunk)
        == value.get("returned_byte_count")
        and content.get("total_bytes") == value.get("artifact_byte_count")
        and str(content.get("content_sha256", "")).removeprefix("sha256:")
        == str(value.get("source_content_hash", "")).removeprefix("sha256:")
        and "sha256:" + hashlib.sha256(chunk).hexdigest() == value.get("chunk_checksum")
    )


def _valid_output_ownership(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    upstream = value.get("upstream_artifacts") or []
    if not isinstance(upstream, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("ref"), str)
        and bool(item["ref"])
        and _SHA256.fullmatch(str(item.get("content_hash", "")))
        and isinstance(item.get("byte_count"), int)
        and not isinstance(item.get("byte_count"), bool)
        and item["byte_count"] >= 0
        and isinstance(item.get("owner_tool"), str)
        and bool(item["owner_tool"])
        and isinstance(item.get("retrieval_action"), str)
        and bool(item["retrieval_action"])
        for item in upstream
    ):
        return False
    if len({item["ref"] for item in upstream}) != len(upstream):
        return False
    context_ref = value.get("context_artifact_ref")
    role = value.get("context_artifact_role")
    if bool(context_ref) != (role in {"primary", "audit_snapshot"}):
        return False
    if upstream and role == "audit_snapshot":
        return value.get("artifact_ref") == upstream[0][
            "ref"
        ] and context_ref != value.get("artifact_ref")
    if upstream and role == "primary":
        return value.get("artifact_ref") == context_ref
    return value.get("artifact_ref") == (
        context_ref if context_ref else value.get("artifact_ref")
    )


def _raw_action_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _content_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def artifact_progress_summary(raw_trajectory: Any) -> dict[str, Any]:
    """Recompute generic task-state progress from sandbox receipts only."""
    if not isinstance(raw_trajectory, list):
        return {"status": "unavailable", "reason": "raw_trajectory_unavailable"}
    receipts = [
        (result.get("metadata") or {}).get("context_management")
        for result in _action_results(raw_trajectory)
    ]
    if not receipts or all(receipt is None for receipt in receipts):
        return {"status": "not_applicable", "reason": "sandbox_receipts_absent"}
    required_booleans = (
        "artifact_changed",
        "rollback_performed",
        "implicit_artifact_loss_detected",
    )
    if any(
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != "aworld.sandbox-artifact-progress/v1"
        or any(not isinstance(receipt.get(key), bool) for key in required_booleans)
        or (
            receipt.get("artifact_fingerprint_after") is not None
            and not isinstance(receipt.get("artifact_fingerprint_after"), str)
        )
        for receipt in receipts
    ):
        return {"status": "unavailable", "reason": "sandbox_receipt_invalid"}
    seen_states: set[str] = set()
    new_states = 0
    for receipt in receipts:
        fingerprint = receipt.get("artifact_fingerprint_after")
        if (
            receipt["artifact_changed"]
            and not receipt["rollback_performed"]
            and fingerprint
            and fingerprint not in seen_states
        ):
            new_states += 1
        if fingerprint:
            seen_states.add(fingerprint)
    return {
        "status": "available",
        "evidence_basis": "raw_trajectory_sandbox_receipts",
        "artifact_receipt_count": len(receipts),
        "artifact_change_count": sum(
            receipt["artifact_changed"] for receipt in receipts
        ),
        "new_artifact_state_count": new_states,
        "rollback_count": sum(receipt["rollback_performed"] for receipt in receipts),
        "implicit_artifact_loss_count": sum(
            receipt["implicit_artifact_loss_detected"] for receipt in receipts
        ),
        "implicit_artifact_loss_prevented_count": sum(
            receipt["implicit_artifact_loss_detected"] and receipt["rollback_performed"]
            for receipt in receipts
        ),
        "no_artifact_change_count": sum(
            not receipt["artifact_changed"] for receipt in receipts
        ),
    }


def turn_artifact_economics_summary(
    calls: list[dict],
    raw_trajectory: Any,
    artifact_files: list[Path] | tuple[Path, ...],
) -> dict[str, Any]:
    """Recompute economics exclusively from typed runtime receipts and files."""
    trajectory_available = isinstance(raw_trajectory, list)
    action_results = _action_results(raw_trajectory)
    model_receipts = [call.get("turn_economics") for call in calls]
    inferred_framework_retry_count = 0
    for index, (call, receipt) in enumerate(zip(calls, model_receipts)):
        if (
            index == 0
            or not _valid_turn_receipt(receipt, expected_kind="model")
            or receipt.get("cause") != "unavailable"
            or not isinstance(call.get("attempt"), int)
            or call["attempt"] <= 1
        ):
            continue
        previous = calls[index - 1]
        same_provider_attempt = all(
            call.get(key) == previous.get(key)
            for key in ("call_id", "step_id", "task_id", "model", "provider_name")
        )
        if not (
            same_provider_attempt
            and previous.get("status") == "failed"
            and previous.get("provider_invoked") is True
            and previous.get("attempt") == call["attempt"] - 1
        ):
            continue
        repaired = dict(receipt)
        repaired["cause"] = "framework_retry"
        repaired["cause_supported"] = True
        model_receipts[index] = repaired
        inferred_framework_retry_count += 1
    tool_receipts = [
        ((result.get("metadata") or {}).get("turn_economics"))
        for result in action_results
    ]
    model_valid = (
        bool(calls)
        and all(
            _valid_turn_receipt(item, expected_kind="model")
            and item.get("cause") != "unavailable"
            and isinstance(call.get("request_id"), str)
            and item.get("request_id_hash")
            == value_hash({"request_id": call["request_id"]})
            for call, item in zip(calls, model_receipts)
        )
        and len({item["turn_id_hash"] for item in model_receipts})
        == len(model_receipts)
    )
    tool_valid = (
        trajectory_available
        and bool(tool_receipts)
        and all(
            _valid_turn_receipt(item, expected_kind="tool")
            and item.get("cause") != "unavailable"
            and isinstance(result.get("tool_call_id"), str)
            and item.get("tool_call_id_hash")
            == value_hash({"tool_call_id": result["tool_call_id"]})
            for result, item in zip(action_results, tool_receipts)
        )
        and len({item["turn_id_hash"] for item in tool_receipts}) == len(tool_receipts)
    )
    if model_valid and tool_valid:
        model_turns = {item["turn_id_hash"] for item in model_receipts}
        tool_turns = {item["turn_id_hash"] for item in tool_receipts}
        parent_integrity = all(
            item.get("parent_turn_id_hash") is None
            or item["parent_turn_id_hash"] in tool_turns
            for item in model_receipts
        ) and all(
            item.get("parent_turn_id_hash") in model_turns for item in tool_receipts
        )
        model_valid = tool_valid = parent_integrity
    causes = {cause: {"model": 0, "tool": 0} for cause in sorted(_TURN_CAUSES)}
    if model_valid and tool_valid:
        for receipt in model_receipts:
            causes[receipt["cause"]]["model"] += 1
        for receipt in tool_receipts:
            causes[receipt["cause"]]["tool"] += 1

    output_receipts = [
        ((result.get("metadata") or {}).get("tool_output_policy"))
        for result in action_results
    ]
    artifact_files = list(artifact_files)
    artifact_by_checksum = {
        "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(): path
        for path in artifact_files
    }
    output_valid = (
        trajectory_available
        and bool(output_receipts)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("raw_byte_count"), int)
            and not isinstance(item.get("raw_byte_count"), bool)
            and isinstance(item.get("inline_tokens"), int)
            and isinstance(item.get("offloaded_tokens"), int)
            and _SHA256.fullmatch(str(item.get("raw_checksum", "")))
            and _valid_output_ownership(item)
            and (
                not item.get("context_artifact_ref")
                or (
                    item.get("raw_checksum") in artifact_by_checksum
                    and artifact_by_checksum[item["raw_checksum"]].stat().st_size
                    == item.get("raw_byte_count")
                )
            )
            and (
                bool(item.get("context_artifact_ref"))
                or (
                    len(_raw_action_bytes(result.get("content")))
                    == item.get("raw_byte_count")
                    and "sha256:"
                    + hashlib.sha256(
                        _raw_action_bytes(result.get("content"))
                    ).hexdigest()
                    == item.get("raw_checksum")
                )
            )
            for result, item in zip(action_results, output_receipts)
        )
    )
    consumption_pairs = [
        (call, item)
        for call in calls
        for item in (call.get("artifact_retrieval_consumption") or [])
    ]
    consumption_receipts = [item for _, item in consumption_pairs]
    retrieval_pairs = [
        (result, (result.get("metadata") or {}).get("artifact_retrieval"))
        for result in action_results
        if (result.get("metadata") or {}).get("artifact_retrieval") is not None
    ]

    def valid_tool_retrieval(result: dict, item: Any) -> bool:
        content = _content_object(result.get("content"))
        return bool(
            _valid_retrieval_receipt(item, action_content=result.get("content"))
            and item.get("artifact_content_hash") in artifact_by_checksum
            and artifact_by_checksum[item["artifact_content_hash"]].stat().st_size
            == item.get("artifact_byte_count")
            and item.get("owner_code")
            == value_hash({"owner_tool": result.get("tool_name")})
            and item.get("action_code")
            == value_hash({"retrieval_action": result.get("action_name")})
            and item.get("consumer_tool_call_id_hash")
            == value_hash({"tool_call_id": result.get("tool_call_id")})
            and content is not None
            and item.get("artifact_ref_hash")
            == value_hash({"artifact_ref": content.get("artifact_ref")})
        )

    def valid_consumption(call: dict, item: Any) -> bool:
        return bool(
            _valid_retrieval_receipt(item)
            and isinstance(call.get("request_id"), str)
            and item.get("next_request_id_hash")
            == value_hash({"request_id": call["request_id"]})
        )

    valid_tool_pairs = [
        (result, item)
        for result, item in retrieval_pairs
        if valid_tool_retrieval(result, item)
    ]
    tool_by_plan = {item["plan_fingerprint"]: item for _, item in valid_tool_pairs}
    valid_consumption_pairs = [
        (call, item)
        for call, item in consumption_pairs
        if valid_consumption(call, item)
    ]
    consumed_by_plan = {
        item["plan_fingerprint"]: item for _, item in valid_consumption_pairs
    }
    consumption_bound = all(
        fingerprint in tool_by_plan
        and item.get("consumed") is True
        and item.get("result_content_hash")
        == tool_by_plan[fingerprint].get("result_content_hash")
        and {
            key: value
            for key, value in item.items()
            if key not in {"next_request_id_hash", "consumed_content_hash", "consumed"}
        }
        == {
            key: value
            for key, value in tool_by_plan[fingerprint].items()
            if key not in {"next_request_id_hash", "consumed_content_hash", "consumed"}
        }
        for fingerprint, item in consumed_by_plan.items()
    )
    retrieval_valid = bool(
        trajectory_available
        and tool_by_plan
        and len(tool_by_plan) == len(valid_tool_pairs)
        and len(consumed_by_plan) == len(valid_consumption_pairs)
        and consumption_bound
        and set(tool_by_plan) == set(consumed_by_plan)
    )
    failed_retrieval_attempt_count = len(retrieval_pairs) - len(tool_by_plan)
    return {
        "schema_version": "aworld.context.turn-artifact-economics-summary.v1",
        "turn_causes": {
            "status": "available" if model_valid and tool_valid else "unavailable",
            "inferred_framework_retry_count": inferred_framework_retry_count,
            "model_receipt_count": len(model_receipts) if model_valid else 0,
            "tool_receipt_count": len(tool_receipts) if tool_valid else 0,
            "counts": causes if model_valid and tool_valid else {},
        },
        "tool_outputs": {
            "status": "available" if output_valid else "unavailable",
            "raw_bytes": sum(item["raw_byte_count"] for item in output_receipts)
            if output_valid
            else None,
            "inline_tokens": sum(item["inline_tokens"] for item in output_receipts)
            if output_valid
            else None,
            "offloaded_tokens": sum(
                item["offloaded_tokens"] for item in output_receipts
            )
            if output_valid
            else None,
            "double_offload_count": sum(
                bool(
                    item.get("context_artifact_role") == "primary"
                    and item.get("context_artifact_ref")
                    and item.get("upstream_artifacts")
                )
                for item in output_receipts
            )
            if output_valid
            else None,
            "audit_snapshot_count": sum(
                item.get("context_artifact_role") == "audit_snapshot"
                for item in output_receipts
            )
            if output_valid
            else None,
        },
        "artifacts": {
            "persisted_count": len(artifact_files),
            "persisted_bytes": sum(path.stat().st_size for path in artifact_files),
        },
        "retrieval": {
            "status": (
                "available"
                if retrieval_valid
                else "not_applicable"
                if not retrieval_pairs and not consumption_receipts
                else "unavailable"
            ),
            "attempt_count": len(retrieval_pairs),
            "failed_attempt_count": failed_retrieval_attempt_count,
            "opportunity_count": len(tool_by_plan),
            "retrieval_count": len(tool_by_plan)
            if retrieval_valid
            else 0
            if not retrieval_pairs
            else None,
            "retrieved_bytes": sum(
                item["returned_byte_count"] for item in tool_by_plan.values()
            )
            if retrieval_valid
            else 0
            if not retrieval_pairs
            else None,
            "consumed_count": len(consumed_by_plan)
            if retrieval_valid
            else 0
            if not retrieval_pairs
            else None,
            "consumption_coverage": (
                len(consumed_by_plan) / len(tool_by_plan) if tool_by_plan else 0.0
            ),
        },
        "artifact_progress": artifact_progress_summary(raw_trajectory),
    }


def paired_turn_artifact_deltas(
    runs: list[dict[str, Any]], *, baseline: str, candidate: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for run in runs:
        grouped.setdefault((run["experiment"], run["case_id"], int(run["repeat"])), {})[
            run["variant"]
        ] = run["summary"]
    output: list[dict[str, Any]] = []
    for (experiment, case_id, repeat), variants in sorted(grouped.items()):
        left, right = variants.get(baseline), variants.get(candidate)
        available = bool(
            left
            and right
            and all(
                summary[section]["status"] == "available"
                for summary in (left, right)
                for section in ("turn_causes", "tool_outputs")
            )
            and (
                all(
                    summary["retrieval"]["status"] == "available"
                    for summary in (left, right)
                )
                or all(
                    summary["retrieval"]["status"] == "not_applicable"
                    for summary in (left, right)
                )
            )
        )
        row: dict[str, Any] = {
            "experiment": experiment,
            "case_id": case_id,
            "repeat": repeat,
            "status": "available" if available else "unsupported",
        }
        progress_available = bool(
            left
            and right
            and left.get("artifact_progress", {}).get("status") == "available"
            and right.get("artifact_progress", {}).get("status") == "available"
        )
        row["artifact_progress"] = (
            {
                "status": "available",
                "candidate_minus_baseline": {
                    key: right["artifact_progress"][key]
                    - left["artifact_progress"][key]
                    for key in (
                        "artifact_receipt_count",
                        "artifact_change_count",
                        "new_artifact_state_count",
                        "rollback_count",
                        "implicit_artifact_loss_count",
                        "implicit_artifact_loss_prevented_count",
                        "no_artifact_change_count",
                    )
                },
            }
            if progress_available
            else {
                "status": "candidate_only_observed",
                "reason": "baseline_progress_receipts_not_applicable",
                "candidate": right["artifact_progress"],
            }
            if left
            and right
            and left.get("artifact_progress", {}).get("status") == "not_applicable"
            and right.get("artifact_progress", {}).get("status") == "available"
            else {
                "status": "unsupported",
                "reason": "paired_artifact_progress_unavailable",
            }
        )
        semantic_available = bool(
            left
            and right
            and left.get("semantic_progress", {}).get("status") == "available"
            and right.get("semantic_progress", {}).get("status") == "available"
        )
        row["semantic_progress"] = (
            {
                "status": "available",
                "candidate_minus_baseline": {
                    key: right["semantic_progress"]["counts"].get(key, 0)
                    - left["semantic_progress"]["counts"].get(key, 0)
                    for key in sorted(
                        set(left["semantic_progress"]["counts"])
                        | set(right["semantic_progress"]["counts"])
                    )
                },
            }
            if semantic_available
            else {
                "status": "unsupported",
                "reason": "paired_semantic_progress_unavailable",
            }
        )
        if available:
            row["candidate_minus_baseline"] = {
                "raw_tool_output_bytes": right["tool_outputs"]["raw_bytes"]
                - left["tool_outputs"]["raw_bytes"],
                "inline_tool_output_tokens": right["tool_outputs"]["inline_tokens"]
                - left["tool_outputs"]["inline_tokens"],
                "offloaded_tool_output_tokens": right["tool_outputs"][
                    "offloaded_tokens"
                ]
                - left["tool_outputs"]["offloaded_tokens"],
                "double_offload_count": right["tool_outputs"]["double_offload_count"]
                - left["tool_outputs"]["double_offload_count"],
                "retrieved_bytes": (right["retrieval"]["retrieved_bytes"] or 0)
                - (left["retrieval"]["retrieved_bytes"] or 0),
                "artifact_consumed_count": (right["retrieval"]["consumed_count"] or 0)
                - (left["retrieval"]["consumed_count"] or 0),
                "turn_causes": {
                    cause: {
                        kind: right["turn_causes"]["counts"][cause][kind]
                        - left["turn_causes"]["counts"][cause][kind]
                        for kind in ("model", "tool")
                    }
                    for cause in sorted(_TURN_CAUSES)
                },
            }
        else:
            row["reason"] = "baseline_or_candidate_turn_artifact_economics_unavailable"
        output.append(row)
    return output


def plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, FrozenMap):
        return plain(thaw_json(value))
    if is_dataclass(value):
        return {
            field.name: plain(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def read_json(path: Path, default: Any = None) -> Any:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    compressed = path.with_name(path.name + ".xz")
    if compressed.is_file():
        with lzma.open(compressed, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return default


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def semantic_progress_summary(run_dir: Path) -> dict[str, Any]:
    """Validate the bounded runtime progress receipt against the run manifest."""
    path = run_dir / "semantic_progress.json"
    if not path.is_file():
        return {"status": "not_applicable", "reason": "semantic_progress_absent"}
    manifest = read_json(run_dir / "run_manifest.json", {})
    expected = ((manifest.get("capture") or {}).get("checksums") or {}).get(
        "semantic_progress.json"
    )
    if not isinstance(expected, str) or expected.removeprefix("sha256:") != file_hash(
        path
    ).removeprefix("sha256:"):
        return {
            "status": "unavailable",
            "reason": "semantic_progress_checksum_mismatch",
        }
    payload = read_json(path, {})
    counts = payload.get("counts") if isinstance(payload, dict) else None
    agents = payload.get("agents") if isinstance(payload, dict) else None
    allowed_agent_fields = {
        "agent_id_hash",
        "repetition_count",
        "low_information_gain_count",
        "no_goal_progress_count",
        "goal_progress_count",
        "last_goal_progress_agent_step",
        "goal_progress",
        "artifact_advanced",
        "completion_advanced",
        "operation_hash",
        "result_hash",
    }
    valid = bool(
        isinstance(payload, dict)
        and payload.get("schema_version")
        == "aworld.context.semantic-progress-evidence/v1"
        and payload.get("status") == "available"
        and isinstance(counts, dict)
        and set(counts).issubset(_SEMANTIC_PROGRESS_COUNTS)
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in counts.values()
        )
        and isinstance(agents, list)
        and all(
            isinstance(agent, dict)
            and set(agent).issubset(allowed_agent_fields)
            and isinstance(agent.get("agent_id_hash"), str)
            and bool(re.fullmatch(r"[0-9a-f]{64}", agent["agent_id_hash"]))
            for agent in agents
        )
    )
    if not valid:
        return {"status": "unavailable", "reason": "semantic_progress_invalid"}
    return payload


def execution_depth_summary(run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Describe execution continuity without treating it as task quality.

    Final provider truth is preferred.  When a rollout is interrupted, the
    checksum-validated partial Raw trajectory remains useful for measuring how
    far the framework ran, but can never manufacture Reward or a success claim.
    """
    final_calls = read_json(run_dir / "provider_calls.json", None)
    partial = read_json(run_dir / "raw_trajectory.partial.json", {})
    metrics = result.get("context_metrics") or {}
    if isinstance(final_calls, list) and final_calls:
        model_round_count = len(final_calls)
        model_round_fidelity = "final_provider_truth"
    elif (
        isinstance(partial, dict)
        and partial.get("schema_version") == "aworld.raw-trajectory.partial/v1"
        and partial.get("completion_state") == "incomplete"
        and isinstance(partial.get("calls"), list)
    ):
        model_round_count = len(partial["calls"])
        model_round_fidelity = "partial_journal"
    else:
        model_round_count = int(metrics.get("partial_provider_call_count") or 0)
        model_round_fidelity = (
            "partial_journal_summary" if model_round_count else "unavailable"
        )

    recovery = result.get("capture_recovery") or {}
    tool_journal = recovery.get("tool_action_journal") or {}
    event_counts = tool_journal.get("event_type_counts") or {}
    tool_started_count = int(event_counts.get("sandbox_call_started") or 0)
    tool_completed_count = int(event_counts.get("sandbox_call_completed") or 0)
    if not event_counts and isinstance(partial, dict):
        tool_events = partial.get("tool_events") or []
        if isinstance(tool_events, list):
            tool_started_count = sum(
                isinstance(event, dict)
                and event.get("event_type") == "sandbox_call_started"
                for event in tool_events
            )
            tool_completed_count = sum(
                isinstance(event, dict)
                and event.get("event_type") == "sandbox_call_completed"
                for event in tool_events
            )

    semantic = semantic_progress_summary(run_dir)
    raw_trajectory = read_json(run_dir / "raw_trajectory.json", None)
    artifact = artifact_progress_summary(raw_trajectory)
    positive_count = no_progress_count = recovery_count = 0
    progress_sources = []
    if semantic.get("status") == "available":
        counts = semantic["counts"]
        positive_count += int(counts.get("goal_progress_count") or 0)
        positive_count += int(counts.get("task_artifact_change_count") or 0)
        no_progress_count += int(counts.get("repeated_operation_count") or 0)
        no_progress_count += int(counts.get("low_information_gain_count") or 0)
        no_progress_count += int(counts.get("no_goal_progress_observation_count") or 0)
        recovery_count += int(counts.get("sandbox_rollback_count") or 0)
        progress_sources.append("semantic_progress")
    if artifact.get("status") == "available":
        positive_count += int(artifact.get("new_artifact_state_count") or 0)
        recovery_count += int(
            artifact.get("implicit_artifact_loss_prevented_count") or 0
        )
        progress_sources.append("artifact_progress")
    typed_progress = {
        "status": "available" if progress_sources else "unavailable",
        "sources": progress_sources,
        "positive_count": positive_count,
        "no_progress_count": no_progress_count,
        "recovery_count": recovery_count,
    }

    agent_execution = result.get("agent_execution") or {}
    wall_time = agent_execution.get("wall_time_seconds")
    if not isinstance(wall_time, (int, float)) or isinstance(wall_time, bool):
        manifest = read_json(run_dir / "run_manifest.json", {})
        started = manifest.get("started_at_epoch")
        finished = manifest.get("finished_at_epoch")
        wall_time = (
            float(finished) - float(started)
            if isinstance(started, (int, float))
            and not isinstance(started, bool)
            and isinstance(finished, (int, float))
            and not isinstance(finished, bool)
            and finished >= started
            else None
        )
    agent_step_count = None
    if semantic.get("status") == "available":
        value = semantic["counts"].get("agent_step_count")
        if isinstance(value, int) and not isinstance(value, bool):
            agent_step_count = value

    agent_process_completed = result.get("agent_exit_code") == 0
    configured_max_steps = agent_execution.get("configured_max_steps")
    loop_budget_exhausted = bool(
        semantic.get("status") == "available"
        and (
            semantic["counts"].get("agent_loop_budget_exhausted_count", 0) > 0
            or (
                isinstance(agent_step_count, int)
                and isinstance(configured_max_steps, int)
                and not isinstance(configured_max_steps, bool)
                and configured_max_steps > 0
                and agent_step_count >= configured_max_steps
            )
        )
    )
    agent_completed = agent_process_completed and not loop_budget_exhausted
    reward_available = result.get("reward") not in (None, "")
    if loop_budget_exhausted:
        classification = (
            "budget_exhausted_with_typed_progress"
            if typed_progress["status"] == "available" and positive_count > 0
            else "budget_exhausted_progress_unavailable"
        )
    elif agent_completed:
        classification = "agent_completed"
    elif typed_progress["status"] == "available" and positive_count > 0:
        classification = "productive_incomplete"
    elif model_round_count or tool_completed_count:
        classification = "sustained_incomplete_progress_unavailable"
    else:
        classification = "no_execution_evidence"
    return {
        "status": (
            "available"
            if model_round_count or tool_started_count or agent_process_completed
            else "unavailable"
        ),
        "agent_process_completed": agent_process_completed,
        "agent_completed": agent_completed,
        "loop_budget_exhausted": loop_budget_exhausted,
        "reward_available": reward_available,
        "reward": float(result["reward"]) if reward_available else None,
        "failure_reason_code": (result.get("failure") or {}).get("reason_code"),
        "model_round_count": model_round_count,
        "model_round_fidelity": model_round_fidelity,
        "tool_started_count": tool_started_count,
        "tool_completed_count": tool_completed_count,
        "agent_step_count": agent_step_count,
        "configured_max_steps": configured_max_steps,
        "wall_time_seconds": wall_time,
        "typed_progress": typed_progress,
        "classification": classification,
        "supports_quality_claim": False,
        "interpretation": (
            "Leading execution indicators explain continuity and productive depth; "
            "they do not replace independent Reward or completion evidence."
        ),
    }


def paired_execution_depth_deltas(
    rows: list[dict[str, Any]], *, baseline: str, candidate: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["experiment"], row["case_id"], row["repeat"]), {})[
            row["variant"]
        ] = row
    output = []
    for (experiment, case_id, repeat), variants in sorted(grouped.items()):
        base = variants.get(baseline)
        cand = variants.get(candidate)
        if base is None or cand is None:
            output.append(
                {
                    "experiment": experiment,
                    "case_id": case_id,
                    "repeat": repeat,
                    "status": "unsupported",
                    "reason": "paired_variant_missing",
                    "supports_quality_claim": False,
                }
            )
            continue
        before, after = base["summary"], cand["summary"]

        def delta(name: str) -> int | float | None:
            left, right = before.get(name), after.get(name)
            if (
                isinstance(left, (int, float))
                and not isinstance(left, bool)
                and isinstance(right, (int, float))
                and not isinstance(right, bool)
            ):
                return right - left
            return None

        before_progress = before.get("typed_progress") or {}
        after_progress = after.get("typed_progress") or {}
        progress_available = (
            before_progress.get("status") == "available"
            and after_progress.get("status") == "available"
        )
        positive_delta = (
            int(after_progress.get("positive_count") or 0)
            - int(before_progress.get("positive_count") or 0)
            if progress_available
            else None
        )
        no_progress_delta = (
            int(after_progress.get("no_progress_count") or 0)
            - int(before_progress.get("no_progress_count") or 0)
            if progress_available
            else None
        )
        rounds_delta = delta("model_round_count")
        if after.get("agent_completed") and not before.get("agent_completed"):
            classification = "completion_improved"
        elif before.get("agent_completed") and not after.get("agent_completed"):
            classification = "completion_regressed"
        elif (
            progress_available
            and isinstance(rounds_delta, (int, float))
            and rounds_delta > 0
            and positive_delta is not None
            and positive_delta > 0
        ):
            classification = "productive_depth_increased"
        elif (
            progress_available
            and isinstance(rounds_delta, (int, float))
            and rounds_delta > 0
            and (positive_delta or 0) <= 0
            and (no_progress_delta or 0) > 0
        ):
            classification = "no_progress_amplification"
        elif isinstance(rounds_delta, (int, float)) and rounds_delta > 0:
            classification = "depth_increased_progress_unavailable"
        else:
            classification = "no_depth_increase"
        output.append(
            {
                "experiment": experiment,
                "case_id": case_id,
                "repeat": repeat,
                "baseline_variant": baseline,
                "candidate_variant": candidate,
                "status": "available",
                "agent_completion_delta": int(bool(after.get("agent_completed")))
                - int(bool(before.get("agent_completed"))),
                "model_round_count_delta": rounds_delta,
                "tool_completed_count_delta": delta("tool_completed_count"),
                "agent_step_count_delta": delta("agent_step_count"),
                "wall_time_seconds_delta": delta("wall_time_seconds"),
                "typed_positive_progress_delta": positive_delta,
                "typed_no_progress_delta": no_progress_delta,
                "classification": classification,
                "supports_quality_claim": False,
            }
        )
    return output


def execution_depth_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("summary"), dict):
            by_variant.setdefault(str(row.get("variant")), []).append(row["summary"])
    variants = {}
    for variant, summaries in sorted(by_variant.items()):
        rewards = [
            float(summary["reward"])
            for summary in summaries
            if summary.get("reward_available") is True
        ]

        def mean_metric(name: str) -> float | None:
            values = [
                float(summary[name])
                for summary in summaries
                if isinstance(summary.get(name), (int, float))
                and not isinstance(summary.get(name), bool)
            ]
            return statistics.fmean(values) if values else None

        classifications: dict[str, int] = {}
        for summary in summaries:
            code = str(summary.get("classification") or "unknown")
            classifications[code] = classifications.get(code, 0) + 1
        variants[variant] = {
            "run_count": len(summaries),
            "agent_process_completion_rate": sum(
                summary.get("agent_process_completed") is True for summary in summaries
            )
            / len(summaries),
            "agent_completion_rate": sum(
                summary.get("agent_completed") is True for summary in summaries
            )
            / len(summaries),
            "loop_budget_exhaustion_rate": sum(
                summary.get("loop_budget_exhausted") is True for summary in summaries
            )
            / len(summaries),
            "reward_availability_rate": len(rewards) / len(summaries),
            "reward_pass_rate_when_available": (
                sum(reward > 0 for reward in rewards) / len(rewards)
                if rewards
                else None
            ),
            "mean_model_round_count": mean_metric("model_round_count"),
            "mean_tool_completed_count": mean_metric("tool_completed_count"),
            "mean_agent_step_count": mean_metric("agent_step_count"),
            "mean_wall_time_seconds": mean_metric("wall_time_seconds"),
            "classification_counts": dict(sorted(classifications.items())),
        }
    return {
        "schema_version": "aworld.context-execution-depth-summary/v1",
        "variants": variants,
        "supports_quality_claim": False,
        "interpretation": (
            "Completion, depth, progress and recovery are leading indicators. "
            "Only independent task outcomes establish quality benefit."
        ),
    }


def validated_context_artifact_files(run_dir: Path) -> list[Path]:
    """Resolve only checksum-bound Context and upstream Tool artifacts."""
    manifest = read_json(run_dir / "run_manifest.json", {})
    capture = manifest.get("capture") or {}
    entry_groups = []
    for name in (
        "context_tool_output_artifacts",
        "upstream_tool_output_artifacts",
    ):
        entries = capture.get(name)
        if entries is None and name == "upstream_tool_output_artifacts":
            entries = []
        if not isinstance(entries, list):
            return []
        entry_groups.extend(entries)
    root = run_dir.resolve()
    resolved: list[Path] = []
    seen_paths: set[Path] = set()
    for entry in entry_groups:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"artifact_ref_hash", "content_hash", "byte_count", "path"}
            or not _SHA256.fullmatch(str(entry.get("artifact_ref_hash", "")))
            or not _SHA256.fullmatch(str(entry.get("content_hash", "")))
            or isinstance(entry.get("byte_count"), bool)
            or not isinstance(entry.get("byte_count"), int)
            or entry["byte_count"] < 0
            or not isinstance(entry.get("path"), str)
        ):
            return []
        path = (run_dir / entry["path"]).resolve()
        if (
            not path.is_relative_to(root)
            or path in seen_paths
            or not path.is_file()
            or path.stat().st_size != entry["byte_count"]
            or file_hash(path) != entry["content_hash"]
        ):
            return []
        seen_paths.add(path)
        resolved.append(path)
    return sorted(resolved)


def value_hash(value: object) -> str:
    return canonical_json_hash(value)


def normalized_hash(value: str | None, fallback: object) -> str:
    if isinstance(value, str):
        if value.startswith("sha256:") and len(value) == 71:
            return value
        if len(value) == 64:
            return "sha256:" + value
    return value_hash(fallback)


def _verified_run_capabilities(
    run_dir: Path,
) -> tuple[tuple[RolloutCapability, ...], str | None]:
    """Rebuild canary capability only from checksum-bound production receipts."""
    try:
        manifest = read_json(run_dir / "run_manifest.json", {})
        checksums = (manifest.get("capture") or {}).get("checksums") or {}
        required_files = (
            "provider_calls.json",
            "task_response.json",
            "context_lifecycle.json",
        )
        if not isinstance(checksums, dict) or any(
            name not in checksums for name in required_files
        ):
            raise ValueError("canary_receipt_checksum_missing")
        for name in required_files:
            path = run_dir / name
            if not path.is_file() or normalized_hash(
                checksums[name], {"missing": name}
            ) != file_hash(path):
                raise ValueError("canary_receipt_checksum_mismatch")

        calls = read_json(run_dir / "provider_calls.json", None)
        if (
            not isinstance(calls, list)
            or not calls
            or (manifest.get("capture") or {}).get("provider_call_count") != len(calls)
        ):
            raise ValueError("canary_provider_calls_incomplete")
        if any(call.get("status") not in {"success", "failed"} for call in calls):
            raise ValueError("canary_provider_attempt_nonterminal")
        successful_calls = [call for call in calls if call.get("status") == "success"]
        if not successful_calls:
            raise ValueError("canary_successful_provider_call_missing")
        # Provider transport failures are health evidence, not proof that an
        # otherwise successful immutable lowering path lacks capability.  Build
        # the capability only from successful, trace-matched attempts while the
        # health gate accounts for every failed attempt separately.
        verified_calls = tuple(
            VerifiedContextEntrypointParityReceipt.from_llm_call_record(call)
            for call in successful_calls
        )

        lifecycle_payload = read_json(run_dir / "context_lifecycle.json", {})
        if (
            not isinstance(lifecycle_payload, dict)
            or lifecycle_payload.get("schema_version")
            != "aworld.context.lifecycle-evidence.v1"
            or lifecycle_payload.get("status") != "available"
        ):
            raise ValueError("canary_lifecycle_receipt_unavailable")
        state_payload = lifecycle_payload.get("state")
        expected_state_fields = {
            "session_id_hash",
            "session_epoch",
            "task_epoch",
            "turn_epoch",
            "branch_id_hash",
            "checkpoint_revision",
        }
        if (
            not isinstance(state_payload, dict)
            or set(state_payload) != expected_state_fields
            or lifecycle_payload.get("state_hash") != value_hash(state_payload)
            or not _SHA256.fullmatch(str(state_payload.get("session_id_hash", "")))
            or not _SHA256.fullmatch(str(state_payload.get("branch_id_hash", "")))
        ):
            raise ValueError("canary_lifecycle_receipt_invalid")
        lifecycle_state = ContextLifecycleState(
            session_id=state_payload["session_id_hash"],
            session_epoch=state_payload["session_epoch"],
            task_epoch=state_payload["task_epoch"],
            turn_epoch=state_payload["turn_epoch"],
            branch_id="h-" + state_payload["branch_id_hash"].removeprefix("sha256:"),
            checkpoint_revision=state_payload["checkpoint_revision"],
        )

        response_payload = read_json(run_dir / "task_response.json", {})
        trajectory_payload = response_payload.get("trajectory_build_result")
        if not isinstance(trajectory_payload, dict):
            raise ValueError("canary_trajectory_receipt_unavailable")
        trajectory_result = TrajectoryBuildResult.from_dict(trajectory_payload)
        if trajectory_result.llm_call_count != len(calls) or any(
            call.get("task_id") != trajectory_result.task_id for call in calls
        ):
            raise ValueError("canary_trajectory_call_correlation_mismatch")
        capabilities = tuple(
            RolloutCapability.from_verified_evidence(
                entrypoint_receipt=receipt,
                lifecycle_state=lifecycle_state,
                trajectory_result=trajectory_result,
            )
            for receipt in verified_calls
        )
        if any(not capability.enforce_ready for capability in capabilities):
            raise ValueError("canary_trajectory_receipt_incomplete")
        return capabilities, None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason):
            reason = "canary_receipt_revalidation_failed"
        return (), reason


def variant_settings(payload: dict) -> dict:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "name"}
    }


def run_directory(experiment: Path, result: dict) -> Path:
    return (
        experiment
        / "runs"
        / result["task"]
        / result["variant"]
        / f"repeat-{int(result['repetition']):02d}"
    )


def provider_snapshot_integrity(calls: list[dict]) -> bool:
    if not calls:
        return False
    for call in calls:
        provider_request = call.get("provider_request") or {}
        if (
            provider_request.get("capture_stage") != "provider_prepared"
            or provider_request.get("fidelity") != "provider_prepared"
            or not isinstance(provider_request.get("payload"), dict)
            or provider_request.get("content_hash")
            != value_hash(provider_request["payload"])
        ):
            return False
        rollout = call.get("context_rollout") or {}
        if rollout.get("candidate_applied") is True:
            receipt = (rollout.get("provider_lowering") or {}).get(
                "provider_request"
            ) or {}
            if receipt.get("content_hash") != provider_request.get("content_hash"):
                return False
    return True


_PLAN_ENTRY_FIELDS = {
    "item_identity_hash",
    "owner_code",
    "kind",
    "source_kind",
    "stability",
    "collection",
    "ordinal",
    "content_hash",
    "token_estimate",
    "residency",
}
_PLAN_FIELDS = {
    "schema_version",
    "plan_fingerprint",
    "entry_count",
    "request_id_hash",
    "candidate_content_hash",
    "messages_shape",
    "messages_count",
    "tools_shape",
    "tools_count",
    "entries",
    "subject",
}


def _provider_attribution_evidence(rollout: dict) -> dict[str, Any] | None:
    evidence = rollout.get("provider_attribution")
    lowering = rollout.get("provider_lowering")
    if isinstance(lowering, dict) and isinstance(lowering.get("attribution"), dict):
        candidate = rollout.get("candidate_snapshot") or {}
        derived = {
            **lowering,
            "subject": "candidate_selected",
            "subject_content_hash": candidate.get("content_hash"),
            "plan_fingerprint": lowering["attribution"].get("plan_fingerprint"),
        }
        if isinstance(evidence, dict):
            expected = {
                "subject": derived["subject"],
                "subject_content_hash": derived["subject_content_hash"],
                "plan_fingerprint": derived["plan_fingerprint"],
            }
            if any(
                evidence.get(key) is not None and evidence.get(key) != value
                for key, value in expected.items()
            ):
                # Preserve the conflicting sibling so validation fails closed.
                return evidence
            derived.update(
                {
                    key: value
                    for key, value in evidence.items()
                    if key not in {"provider_request", "attribution"}
                }
            )
        return derived
    if isinstance(evidence, dict):
        return evidence
    return None


def validated_compiler_attribution_plan(
    call: dict, *, subject: str
) -> dict[str, Any] | None:
    """Validate the independent compiler evidence and canonical fingerprint."""
    rollout = call.get("context_rollout") or {}
    plan = rollout.get("compiler_attribution_plan")
    subject_snapshot = (
        rollout.get(
            "observed_snapshot"
            if subject == "legacy_observed"
            else "candidate_snapshot"
        )
        or {}
    )
    if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS:
        return None
    entries = plan.get("entries")
    if not isinstance(entries, list) or plan.get("entry_count") != len(entries):
        return None
    allowed = {
        "owner_code": {value.value for value in AttributionOwnerCode},
        "kind": {value.value for value in ContextKind},
        "source_kind": {value.value for value in SourceKind},
        "stability": {value.value for value in Stability},
        "collection": {value.value for value in AttributionCollection},
        "residency": {value.value for value in LogicalResidency},
    }
    positions = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _PLAN_ENTRY_FIELDS:
            return None
        token_estimate = entry.get("token_estimate")
        if (
            any(entry.get(field) not in values for field, values in allowed.items())
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(entry.get("item_identity_hash", ""))
            )
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(entry.get("content_hash", ""))
            )
            or not isinstance(token_estimate, dict)
            or set(token_estimate) != {"value", "estimator", "exact"}
            or isinstance(token_estimate.get("value"), bool)
            or not isinstance(token_estimate.get("value"), int)
            or token_estimate.get("value") < 0
            or not isinstance(token_estimate.get("estimator"), str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", token_estimate["estimator"]
            )
            or not isinstance(token_estimate.get("exact"), bool)
            or isinstance(entry.get("ordinal"), bool)
            or not isinstance(entry.get("ordinal"), int)
            or entry.get("ordinal") < 0
        ):
            return None
        positions.append((entry["collection"], entry["ordinal"]))
    messages_count = plan.get("messages_count")
    tools_shape = plan.get("tools_shape")
    tools_count = plan.get("tools_count")
    if (
        plan.get("schema_version") != "aworld.context.attribution-plan.v2"
        or plan.get("subject") != subject
        or plan.get("messages_shape") != "array"
        or isinstance(messages_count, bool)
        or not isinstance(messages_count, int)
        or messages_count < 0
        or tools_shape not in {"null", "array"}
        or (tools_shape == "null" and tools_count is not None)
        or (
            tools_shape == "array"
            and (
                isinstance(tools_count, bool)
                or not isinstance(tools_count, int)
                or tools_count < 0
            )
        )
    ):
        return None
    expected_positions = [
        *(("messages", ordinal) for ordinal in range(messages_count)),
        *(("tools", ordinal) for ordinal in range(tools_count or 0)),
    ]
    request_hash = value_hash({"request_id": call.get("request_id")})
    subject_hash = subject_snapshot.get("content_hash")
    projection = {
        "schema_version": "aworld.context.attribution-plan-fingerprint.v2",
        "request_id_hash": plan.get("request_id_hash"),
        "candidate_content_hash": plan.get("candidate_content_hash"),
        "subject": plan.get("subject"),
        "messages_shape": plan.get("messages_shape"),
        "messages_count": messages_count,
        "tools_shape": tools_shape,
        "tools_count": tools_count,
        "entries": entries,
    }
    if (
        positions != expected_positions
        or plan.get("request_id_hash") != request_hash
        or plan.get("candidate_content_hash") != subject_hash
        or plan.get("plan_fingerprint") != value_hash(projection)
        or subject_snapshot.get("attribution_plan_fingerprint")
        != plan.get("plan_fingerprint")
    ):
        return None
    final_compile = rollout.get("final_compile")
    if final_compile is not None:
        if not isinstance(final_compile, dict):
            return None
        final_attribution = final_compile.get("attribution")
        if not isinstance(final_attribution, dict) or (
            final_attribution.get("plan_fingerprint") != plan.get("plan_fingerprint")
            or final_attribution.get("request_id_hash") != plan.get("request_id_hash")
            or final_attribution.get("candidate_content_hash") != subject_hash
            or final_attribution.get("entries") != entries
        ):
            return None
    return plan


def provider_attribution_summary(calls: list[dict]) -> dict[str, Any]:
    """Aggregate only provider receipts; prompt text is never classified."""
    dimensions = {name: {} for name in ("owner", "kind", "source_kind", "residency")}
    available = 0
    invalid = 0
    attributed = 0
    overhead = 0
    total = 0
    subjects: dict[str, int] = {}
    per_call: list[dict[str, Any]] = []
    for call_ordinal, call in enumerate(calls):
        provider_snapshot = call.get("provider_request") or {}
        provider_payload = provider_snapshot.get("payload")
        rollout = call.get("context_rollout") or {}
        evidence = _provider_attribution_evidence(rollout) or {}
        subject = evidence.get("subject")
        receipt = evidence.get("attribution")
        if not isinstance(receipt, dict):
            continue
        # Attribution proves which bytes were presented at the provider
        # boundary. A transport failure after that boundary does not invalidate
        # the immutable request or its byte-conservation receipt. Billing/usage
        # accounting remains fail-closed in authoritative_normalized_usage().
        if (
            call.get("provider_invoked") is not True
            or call.get("provider_attempt_status") != "attempted"
            or call.get("status") not in {"success", "failed"}
        ):
            invalid += 1
            continue
        if subject not in {"legacy_observed", "candidate_selected"}:
            invalid += 1
            continue
        compiler_plan = validated_compiler_attribution_plan(call, subject=subject)
        entries = receipt.get("entries")
        if (
            compiler_plan is None
            or receipt.get("plan_fingerprint") != compiler_plan.get("plan_fingerprint")
            or receipt.get("schema_version") != "aworld.context.provider-attribution.v2"
            or receipt.get("subject") != subject
            or receipt.get("status") != "available"
            or receipt.get("serialization")
            not in {value.value for value in AttributionSerialization}
            or not isinstance(provider_payload, dict)
            or not isinstance(entries, list)
            or not all(isinstance(entry, dict) for entry in entries)
        ):
            invalid += 1
            continue
        plan_entries = compiler_plan["entries"]
        if len(entries) != len(plan_entries) or any(
            set(entry) != _PLAN_ENTRY_FIELDS | {"canonical_value_bytes"}
            or {key: entry.get(key) for key in _PLAN_ENTRY_FIELDS} != plan_entry
            for entry, plan_entry in zip(entries, plan_entries)
        ):
            invalid += 1
            continue
        canonical_body = canonical_json_bytes(provider_payload)
        canonical_hash = value_hash(provider_payload)
        subject_snapshot = (
            rollout.get(
                "observed_snapshot"
                if subject == "legacy_observed"
                else "candidate_snapshot"
            )
            or {}
        )
        subject_hash = subject_snapshot.get("content_hash")
        if (
            provider_snapshot.get("request_id") != call.get("request_id")
            or provider_snapshot.get("content_hash") != canonical_hash
            or receipt.get("provider_request_content_hash") != canonical_hash
            or receipt.get("canonical_request_checksum") != canonical_hash
            or receipt.get("total_canonical_bytes") != len(canonical_body)
            or receipt.get("plan_request_id_hash")
            != value_hash({"request_id": call.get("request_id")})
            or receipt.get("candidate_content_hash") != subject_hash
            or evidence.get("subject_content_hash") != subject_hash
            or evidence.get("plan_fingerprint") != compiler_plan.get("plan_fingerprint")
        ):
            invalid += 1
            continue
        if (
            receipt.get("serialization")
            == AttributionSerialization.HTTP_SERIALIZED_CANONICAL_JSON.value
            and provider_snapshot.get("serialized_checksum") != canonical_hash
        ) or (
            receipt.get("serialization")
            == AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON.value
            and provider_snapshot.get("serialized_checksum") is not None
        ):
            invalid += 1
            continue
        messages = provider_payload.get("messages")
        tools_present = "tools" in provider_payload
        tools = provider_payload.get("tools")
        provider_tools_shape = (
            "absent"
            if not tools_present
            else "null"
            if tools is None
            else "array"
            if isinstance(tools, list)
            else "invalid"
        )
        tools_lowering = receipt.get("tools_lowering")
        expected_provider_tools_shape = (
            "absent"
            if receipt.get("tools_shape") == "null"
            and tools_lowering == ProviderToolsLowering.NULL_TO_ABSENT.value
            else receipt.get("tools_shape")
        )
        if (
            receipt.get("messages_shape") != "array"
            or not isinstance(messages, list)
            or receipt.get("messages_count") != len(messages)
            or receipt.get("tools_shape") not in {"null", "array"}
            or tools_lowering not in {value.value for value in ProviderToolsLowering}
            or receipt.get("provider_tools_shape") != provider_tools_shape
            or provider_tools_shape != expected_provider_tools_shape
            or (
                receipt.get("tools_shape") == "array"
                and (
                    not isinstance(tools, list)
                    or receipt.get("tools_count") != len(tools)
                )
            )
            or (
                receipt.get("tools_shape") == "null"
                and receipt.get("tools_count") is not None
            )
        ):
            invalid += 1
            continue
        allowed = {
            "owner_code": {value.value for value in AttributionOwnerCode},
            "kind": {value.value for value in ContextKind},
            "source_kind": {value.value for value in SourceKind},
            "stability": {value.value for value in Stability},
            "collection": {value.value for value in AttributionCollection},
            "residency": {value.value for value in LogicalResidency},
        }
        positions = []
        entries_valid = True
        for entry in entries:
            token_estimate = entry.get("token_estimate")
            if (
                any(entry.get(field) not in values for field, values in allowed.items())
                or not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", str(entry.get("item_identity_hash", ""))
                )
                or not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", str(entry.get("content_hash", ""))
                )
                or not isinstance(token_estimate, dict)
                or isinstance(token_estimate.get("value"), bool)
                or not isinstance(token_estimate.get("value"), int)
                or token_estimate.get("value") < 0
                or not isinstance(token_estimate.get("estimator"), str)
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", token_estimate["estimator"]
                )
                or not isinstance(token_estimate.get("exact"), bool)
            ):
                entries_valid = False
                break
            collection = entry["collection"]
            ordinal = entry.get("ordinal")
            values = messages if collection == "messages" else tools
            if (
                isinstance(ordinal, bool)
                or not isinstance(ordinal, int)
                or ordinal < 0
                or not isinstance(values, list)
                or ordinal >= len(values)
                or entry.get("content_hash") != value_hash(values[ordinal])
                or entry.get("canonical_value_bytes")
                != len(canonical_json_bytes(values[ordinal]))
            ):
                entries_valid = False
                break
            positions.append((collection, ordinal))
        expected_positions = [
            *(("messages", index) for index in range(len(messages))),
            *(
                ("tools", index)
                for index in range(len(tools) if isinstance(tools, list) else 0)
            ),
        ]
        if (
            not entries_valid
            or receipt.get("entry_count") != len(entries)
            or positions != expected_positions
            or len(set(positions)) != len(positions)
        ):
            invalid += 1
            continue
        byte_values = [entry.get("canonical_value_bytes") for entry in entries]
        totals = [
            receipt.get("attributed_value_bytes"),
            receipt.get("provider_envelope_and_params"),
            receipt.get("total_canonical_bytes"),
        ]
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (*byte_values, *totals)
        ):
            invalid += 1
            continue
        entry_bytes = sum(byte_values)
        receipt_attributed, receipt_overhead, receipt_total = totals
        if (
            entry_bytes < 0
            or entry_bytes != receipt_attributed
            or receipt_attributed + receipt_overhead != receipt_total
            or receipt.get("byte_conservation") is not True
        ):
            invalid += 1
            continue
        available += 1
        subjects[subject] = subjects.get(subject, 0) + 1
        attributed += receipt_attributed
        overhead += receipt_overhead
        total += receipt_total
        call_dimensions = {
            name: {} for name in ("owner", "kind", "source_kind", "residency")
        }
        for entry in entries:
            value_bytes = int(entry["canonical_value_bytes"])
            for dimension, field in (
                ("owner", "owner_code"),
                ("kind", "kind"),
                ("source_kind", "source_kind"),
                ("residency", "residency"),
            ):
                code = str(entry.get(field, "unknown"))
                bucket = dimensions[dimension]
                bucket[code] = bucket.get(code, 0) + value_bytes
                call_bucket = call_dimensions[dimension]
                call_bucket[code] = call_bucket.get(code, 0) + value_bytes
        per_call.append(
            {
                "ordinal": call_ordinal,
                "request_id_hash": value_hash({"request_id": call.get("request_id")}),
                "total_canonical_bytes": receipt_total,
                "message_bytes": sum(
                    int(entry["canonical_value_bytes"])
                    for entry in entries
                    if entry["collection"] == "messages"
                ),
                "tool_schema_bytes": sum(
                    int(entry["canonical_value_bytes"])
                    for entry in entries
                    if entry["collection"] == "tools"
                ),
                "provider_envelope_and_params": receipt_overhead,
                "messages_content_hash": value_hash(messages),
                "tools_content_hash": value_hash(tools),
                "by_dimension": {
                    name: dict(sorted(values.items()))
                    for name, values in call_dimensions.items()
                },
            }
        )
    unavailable = len(calls) - available - invalid
    complete = bool(calls) and available == len(calls) and invalid == 0
    subject = next(iter(subjects)) if len(subjects) == 1 and complete else None
    dimension_resolution = {
        "owner": (
            "legacy_model_boundary_owner_v1"
            if subject == "legacy_observed"
            else "compiler_owner_v1"
            if subject == "candidate_selected"
            else "unavailable"
        ),
        "kind": "provider_occurrence_kind_v1" if complete else "unavailable",
        "source_kind": "provider_occurrence_source_v1" if complete else "unavailable",
        "residency": (
            "legacy_unknown_residency_v1"
            if subject == "legacy_observed"
            else "compiler_logical_residency_v1"
            if subject == "candidate_selected"
            else "unavailable"
        ),
    }
    return {
        "status": "available" if complete else "unavailable",
        "provider_call_count": len(calls),
        "available_receipt_count": available,
        "unavailable_receipt_count": unavailable,
        "invalid_receipt_count": invalid,
        "coverage_rate": (available / len(calls)) if calls else 0.0,
        "byte_conservation": complete,
        "attributed_value_bytes": attributed,
        "provider_envelope_and_params": overhead,
        "total_canonical_bytes": total,
        "subject": subject,
        "subject_counts": dict(sorted(subjects.items())),
        "per_call": per_call if complete else [],
        "by_dimension": {
            name: dict(sorted(values.items())) for name, values in dimensions.items()
        },
        "dimension_resolution": dimension_resolution,
        "fallback": "none",
        "reason": None if complete else "provider_attribution_incomplete",
    }


def request_amplification_delta(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Separate aligned request growth from unmatched-call amplification.

    Ordinal alignment is accounting evidence, not a claim that later stochastic
    turns are semantically equivalent.  Only the separately reported first-call
    message/Tool hashes can establish equal model-visible initial inputs.
    """
    left = baseline.get("per_call")
    right = candidate.get("per_call")
    if (
        not isinstance(left, list)
        or not isinstance(right, list)
        or not left
        or not right
    ):
        return {"status": "unsupported", "reason": "per_call_attribution_unavailable"}
    if any(
        not isinstance(row, dict)
        or row.get("ordinal") != index
        or isinstance(row.get("total_canonical_bytes"), bool)
        or not isinstance(row.get("total_canonical_bytes"), int)
        for values in (left, right)
        for index, row in enumerate(values)
    ):
        return {"status": "unsupported", "reason": "per_call_attribution_invalid"}
    common = min(len(left), len(right))
    aligned = sum(
        int(right[index]["total_canonical_bytes"])
        - int(left[index]["total_canonical_bytes"])
        for index in range(common)
    )
    candidate_only = sum(int(row["total_canonical_bytes"]) for row in right[common:])
    baseline_only = sum(int(row["total_canonical_bytes"]) for row in left[common:])
    total_delta = int(candidate["total_canonical_bytes"]) - int(
        baseline["total_canonical_bytes"]
    )
    first_left, first_right = left[0], right[0]
    messages_match = first_left.get("messages_content_hash") == first_right.get(
        "messages_content_hash"
    )
    tools_match = first_left.get("tools_content_hash") == first_right.get(
        "tools_content_hash"
    )
    return {
        "status": "available",
        "baseline_call_count": len(left),
        "candidate_call_count": len(right),
        "aligned_call_count": common,
        "candidate_only_call_count": len(right) - common,
        "baseline_only_call_count": len(left) - common,
        "aligned_provider_bytes_delta": aligned,
        "candidate_only_provider_bytes": candidate_only,
        "baseline_only_provider_bytes": baseline_only,
        "total_provider_bytes_delta": total_delta,
        "byte_reconciliation": total_delta == aligned + candidate_only - baseline_only,
        "first_call": {
            "messages_match": messages_match,
            "tools_match": tools_match,
            "model_visible_inputs_match": messages_match and tools_match,
            "provider_bytes_delta": (
                int(first_right["total_canonical_bytes"])
                - int(first_left["total_canonical_bytes"])
            ),
            "message_bytes_delta": int(first_right["message_bytes"])
            - int(first_left["message_bytes"]),
            "tool_schema_bytes_delta": int(first_right["tool_schema_bytes"])
            - int(first_left["tool_schema_bytes"]),
            "provider_envelope_and_params_delta": (
                int(first_right["provider_envelope_and_params"])
                - int(first_left["provider_envelope_and_params"])
            ),
        },
    }


def paired_attribution_deltas(
    rows: list[dict[str, Any]],
    *,
    baseline: str,
    candidate: str,
    allow_candidate_baseline: bool = False,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["experiment"], row["case_id"], row["repeat"]), {})[
            row["variant"]
        ] = row
    deltas = []
    for (experiment, case_id, repeat), variants in sorted(grouped.items()):
        base = variants.get(baseline)
        cand = variants.get(candidate)
        if base is None or cand is None:
            status, reason = "unsupported", "paired_variant_missing"
            dimension_delta = None
        elif (
            base["summary"]["status"] != "available"
            or cand["summary"]["status"] != "available"
        ):
            status, reason = (
                "unsupported",
                "baseline_or_candidate_attribution_unavailable",
            )
            dimension_delta = None
        elif (base["summary"].get("subject"), cand["summary"].get("subject")) not in (
            {
                ("legacy_observed", "candidate_selected"),
                ("candidate_selected", "candidate_selected"),
            }
            if allow_candidate_baseline
            else {("legacy_observed", "candidate_selected")}
        ):
            status, reason = "unsupported", "paired_attribution_subject_mismatch"
            dimension_delta = None
        else:
            status, reason = "available", None
            dimension_delta = {}
            dimension_status = {}
            for dimension in ("owner", "kind", "source_kind", "residency"):
                before_resolution = (
                    base["summary"].get("dimension_resolution", {}).get(dimension)
                )
                after_resolution = (
                    cand["summary"].get("dimension_resolution", {}).get(dimension)
                )
                if before_resolution != after_resolution:
                    dimension_delta[dimension] = None
                    dimension_status[dimension] = {
                        "status": "unsupported",
                        "reason": "resolution_mismatch",
                        "baseline_resolution": before_resolution,
                        "candidate_resolution": after_resolution,
                    }
                    continue
                before = base["summary"]["by_dimension"][dimension]
                after = cand["summary"]["by_dimension"][dimension]
                dimension_delta[dimension] = {
                    code: after.get(code, 0) - before.get(code, 0)
                    for code in sorted(set(before) | set(after))
                }
                dimension_status[dimension] = {"status": "available", "reason": None}
        if status != "available":
            dimension_status = None
        deltas.append(
            {
                "experiment": experiment,
                "case_id": case_id,
                "repeat": repeat,
                "baseline_variant": baseline,
                "candidate_variant": candidate,
                "baseline_run": base["run"] if base is not None else None,
                "candidate_run": cand["run"] if cand is not None else None,
                "status": status,
                "reason": reason,
                "total_canonical_bytes_delta": (
                    cand["summary"].get("total_canonical_bytes", 0)
                    - base["summary"].get("total_canonical_bytes", 0)
                    if status == "available"
                    else None
                ),
                "by_dimension_delta": dimension_delta,
                "dimension_status": dimension_status,
                "request_amplification": (
                    request_amplification_delta(base["summary"], cand["summary"])
                    if status == "available"
                    else None
                ),
            }
        )
    return deltas


def provider_attribution_pairing_status(
    rows: list[dict[str, Any]],
    *,
    experiment: str,
    case_ids: tuple[str, ...],
    repeats: int,
    baseline: str,
    candidate: str,
    allow_candidate_baseline: bool = False,
) -> dict[str, Any]:
    """Compare observed attribution runs with the manifest cartesian product."""
    expected = {
        (experiment, case_id, repeat, variant)
        for case_id in case_ids
        for repeat in range(1, repeats + 1)
        for variant in (baseline, candidate)
    }
    actual_list = [
        (row["experiment"], row["case_id"], row["repeat"], row["variant"])
        for row in rows
        if row.get("variant") in {baseline, candidate}
    ]
    actual = set(actual_list)
    deltas = paired_attribution_deltas(
        rows,
        baseline=baseline,
        candidate=candidate,
        allow_candidate_baseline=allow_candidate_baseline,
    )
    complete = (
        len(actual_list) == len(actual)
        and actual == expected
        and len(deltas) == len(case_ids) * repeats
        and all(delta["status"] == "available" for delta in deltas)
    )
    return {
        "status": "available" if complete else "unavailable",
        "expected_run_count": len(expected),
        "actual_run_count": len(actual_list),
        "unique_actual_run_count": len(actual),
        "missing_run_count": len(expected - actual),
        "unexpected_run_count": len(actual - expected),
        "duplicate_run_count": len(actual_list) - len(actual),
        "available_pair_count": sum(delta["status"] == "available" for delta in deltas),
        "expected_pair_count": len(case_ids) * repeats,
        "reason": None if complete else "provider_attribution_pairing_incomplete",
    }


def authoritative_cache_usage_receipt(call: dict[str, Any]) -> dict[str, Any]:
    """Recompute and verify provider-neutral cache truth for one captured call."""
    return reconcile_cache_usage_receipt(
        captured_receipt=call.get("cache_usage_receipt"),
        raw_usage=call.get("usage_raw"),
        normalized_usage=call.get("usage_normalized") or call.get("usage"),
    ).to_dict()


def authoritative_provider_metrics(calls: list[dict]) -> dict[str, int | float]:
    """Recompute provider metrics from captured calls, never a stale summary.

    Cache totals are exact-only.  Coverage and fidelity counters make it
    impossible for a missing provider cache field to masquerade as a cache miss.
    """
    metrics: dict[str, int | float] = {
        "provider_call_count": len(calls),
        "provider_request_bytes": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_usage_exact_call_count": 0,
        "cache_usage_exact_coverage": 0.0,
        "cache_usage_exact_input_tokens": 0,
        "uncached_input_tokens_exact": 0,
        "cache_usage_bounded_call_count": 0,
        "cache_usage_conflicting_call_count": 0,
        "cache_usage_invalid_call_count": 0,
        "cache_usage_unavailable_call_count": 0,
        "request_trace_match_count": 0,
        "request_trace_match_rate": 0.0,
        "provider_attribution_receipt_count": 0,
        "provider_attributed_value_bytes": 0,
        "provider_attribution_overhead_bytes": 0,
    }
    for call in calls:
        provider_request = call.get("provider_request") or {}
        payload = provider_request.get("payload") or call.get("request") or {}
        metrics["provider_request_bytes"] += len(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        usage = call.get("usage_normalized") or call.get("usage") or {}
        metrics["prompt_tokens"] += int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        metrics["completion_tokens"] += int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        receipt = authoritative_cache_usage_receipt(call)
        fidelity = receipt["fidelity"]
        counter = f"cache_usage_{fidelity}_call_count"
        if counter in metrics:
            metrics[counter] += 1
        if fidelity == "exact":
            input_tokens = receipt["input_tokens"]
            cache_read_tokens = receipt["cache_read_tokens"]
            uncached_input_tokens = receipt["uncached_input_tokens"]
            assert isinstance(input_tokens, int)
            assert isinstance(cache_read_tokens, int)
            assert isinstance(uncached_input_tokens, int)
            metrics["cache_read_tokens"] += cache_read_tokens
            metrics["cache_usage_exact_input_tokens"] += input_tokens
            metrics["uncached_input_tokens_exact"] += uncached_input_tokens
        metrics["request_trace_match_count"] += int(
            call.get("request_trace_match") is True
        )
    if calls:
        metrics["request_trace_match_rate"] = metrics[
            "request_trace_match_count"
        ] / len(calls)
        metrics["cache_usage_exact_coverage"] = metrics[
            "cache_usage_exact_call_count"
        ] / len(calls)
    attribution = provider_attribution_summary(calls)
    metrics["provider_attribution_receipt_count"] = attribution[
        "available_receipt_count"
    ]
    metrics["provider_attributed_value_bytes"] = attribution["attributed_value_bytes"]
    metrics["provider_attribution_overhead_bytes"] = attribution[
        "provider_envelope_and_params"
    ]
    return metrics


def authoritative_normalized_usage(
    calls: list[dict],
) -> tuple[dict[str, int] | None, str | None]:
    """Validate complete per-call token truth without coercion or defaulting."""
    if not calls:
        return None, "provider_calls_missing"

    totals = {"input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0}
    for call in calls:
        if (
            not isinstance(call, dict)
            or call.get("status") != "success"
            or call.get("provider_invoked") is not True
            or call.get("provider_attempt_status") != "attempted"
        ):
            return None, "provider_attempt_truth_incomplete"
        receipt = authoritative_cache_usage_receipt(call)
        if receipt["fidelity"] != "exact":
            reason = receipt.get("reason_code")
            if reason == "provider_total_usage_conflict":
                normalized = call.get("usage_normalized") or call.get("usage") or {}
                raw = call.get("usage_raw") or {}

                def token_value(mapping: Any, aliases: tuple[str, ...]) -> Any:
                    if not isinstance(mapping, dict):
                        return None
                    return next(
                        (mapping[name] for name in aliases if name in mapping),
                        None,
                    )

                if token_value(normalized, ("prompt_tokens", "input_tokens")) != (
                    token_value(raw, ("prompt_tokens", "input_tokens"))
                ) or token_value(
                    normalized, ("completion_tokens", "output_tokens")
                ) != token_value(raw, ("completion_tokens", "output_tokens")):
                    return None, "provider_usage_conflict"
            if reason == "provider_token_usage_conflicting_views":
                return None, "provider_usage_conflict"
            if reason == "provider_cache_usage_conflicting_views":
                return None, "provider_cache_usage_conflicting_views"
            if reason == "provider_cache_usage_exceeds_input":
                return None, "provider_cache_usage_exceeds_input"
            if reason == "captured_cache_usage_receipt_mismatch":
                return None, "provider_cache_usage_receipt_conflict"
            if reason == "provider_cache_usage_missing":
                return None, "provider_cache_usage_missing_or_conflicting"
            return None, reason or "provider_usage_missing_or_invalid"
        input_tokens = receipt["input_tokens"]
        output_tokens = receipt["output_tokens"]
        cache_tokens = receipt["cache_read_tokens"]
        assert isinstance(input_tokens, int)
        assert isinstance(output_tokens, int)
        assert isinstance(cache_tokens, int)
        totals["input_tokens"] += input_tokens
        totals["cache_read_tokens"] += cache_tokens
        totals["output_tokens"] += output_tokens
    return totals, None


def authoritative_normalized_usage_bounds(
    calls: list[dict],
) -> tuple[dict[str, Any] | None, str | None]:
    """Build conservative token bounds from terminal provider-attempt truth.

    This is intentionally stricter than a request-byte proxy.  Exact provider
    usage is retained whenever present.  Missing cache detail widens only the
    cache component; terminal attempts without usage use canonical serialized
    provider bytes solely as an upper token bound.  Active/unbound calls remain
    unavailable, so an incomplete rollout can never acquire cost evidence.
    """
    if not calls:
        return None, "provider_calls_missing"

    lower = {"input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0}
    upper = {"input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0}
    counts = {
        "exact_call_count": 0,
        "cache_bounded_call_count": 0,
        "provider_attempt_bounded_call_count": 0,
        "usage_bounded_call_count": 0,
    }
    for call in calls:
        if (
            not isinstance(call, dict)
            or call.get("provider_invoked") is not True
            or call.get("provider_attempt_status") != "attempted"
            or call.get("status") not in {"success", "failed"}
        ):
            return None, "provider_attempt_truth_incomplete"
        provider_request = call.get("provider_request")
        if not isinstance(provider_request, dict):
            return None, "provider_prepared_request_missing"

        if call.get("status") == "failed":
            upper["input_tokens"] += len(canonical_json_bytes(provider_request))
            response = call.get("response")
            if response is not None:
                upper["output_tokens"] += len(canonical_json_bytes(response))
            counts["provider_attempt_bounded_call_count"] += 1
            continue

        receipt = authoritative_cache_usage_receipt(call)
        input_tokens = receipt.get("input_tokens")
        output_tokens = receipt.get("output_tokens")
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            upper["input_tokens"] += len(canonical_json_bytes(provider_request))
            response = call.get("response")
            if response is not None:
                upper["output_tokens"] += len(canonical_json_bytes(response))
            counts["usage_bounded_call_count"] += 1
            continue

        lower["input_tokens"] += input_tokens
        upper["input_tokens"] += input_tokens
        lower["output_tokens"] += output_tokens
        upper["output_tokens"] += output_tokens
        cache_read_tokens = receipt.get("cache_read_tokens")
        if receipt["fidelity"] == "exact" and isinstance(cache_read_tokens, int):
            lower["cache_read_tokens"] += cache_read_tokens
            upper["cache_read_tokens"] += cache_read_tokens
            counts["exact_call_count"] += 1
        elif (
            receipt["fidelity"] == "bounded"
            and receipt.get("reason_code") == "provider_cache_usage_missing"
        ):
            # Cache makes the frozen policy cheaper.  All-cache is therefore
            # the safe lower endpoint and no-cache the safe upper endpoint.
            lower["cache_read_tokens"] += input_tokens
            counts["cache_bounded_call_count"] += 1
        else:
            # Captured/recomputed conflicts and invalid provider usage keep the
            # known input/output totals but receive the widest cache bound.
            lower["cache_read_tokens"] += input_tokens
            counts["cache_bounded_call_count"] += 1

    return {
        "lower": lower,
        "upper": upper,
        "total_call_count": len(calls),
        **counts,
        "source_hash": value_hash(calls),
    }, None


def benefit_evidence(
    summary: Any, *, normalized_cost_policy_ready: bool = False
) -> dict[str, Any]:
    """Accept quality gain or quality-safe, explicitly costed efficiency gain."""

    def interval_bound(interval: Any, name: str) -> float:
        if isinstance(interval, (dict, FrozenMap)):
            return float(interval[name])
        return float(getattr(interval, name))

    if summary is None:
        return {
            "proven": False,
            "path": None,
            "reason": "paired_summary_missing",
        }
    reward_lower = interval_bound(summary.reward_interval, "lower")
    if reward_lower > 0.0:
        return {
            "proven": True,
            "path": "quality",
            "reason": "reward_confidence_lower_bound_positive",
        }
    # The quality gate permits at most one percentage point of regression.
    quality_non_regression = reward_lower >= -0.01
    for metric in (*_COST_BENEFIT_METRICS, *_EXECUTION_EFFICIENCY_METRICS):
        if metric.startswith("normalized_cost_") and not normalized_cost_policy_ready:
            continue
        interval = summary.metric_intervals.get(metric)
        if (
            quality_non_regression
            and interval is not None
            and interval_bound(interval, "upper") < 0.0
        ):
            return {
                "proven": True,
                "path": (
                    "execution_efficiency"
                    if metric in _EXECUTION_EFFICIENCY_METRICS
                    else "efficiency"
                ),
                "reason": (
                    "quality_non_regression_and_provider_work_confidence_upper_bound_negative"
                    if metric in _EXECUTION_EFFICIENCY_METRICS
                    else "quality_non_regression_and_cost_confidence_upper_bound_negative"
                ),
                "cost_metric": metric,
            }
    return {
        "proven": False,
        "path": None,
        "reason": (
            "quality_gain_or_versioned_cost_reduction_not_proven"
            if quality_non_regression
            else "quality_regression_not_excluded"
        ),
        "accepted_cost_metrics": list(_COST_BENEFIT_METRICS),
        "accepted_execution_efficiency_metrics": list(_EXECUTION_EFFICIENCY_METRICS),
    }


def normalized_cost_evidence_ready(workload_reports: list[dict[str, Any]]) -> bool:
    """Revalidate every policy/receipt and its trial-manifest binding."""
    if not workload_reports:
        return False
    policy_hashes: set[str] = set()
    trial_count = 0
    try:
        for report in workload_reports:
            policy = NormalizedCostPolicy.from_dict(report["normalized_cost_policy"])
            manifest = report["manifest"]
            if (
                not isinstance(manifest, dict)
                or manifest.get("cost_policy_hash") != policy.policy_hash
            ):
                return False
            manifest_projection = {
                key: manifest.get(key)
                for key in (
                    "experiment_id",
                    "workload_id",
                    "workload_kind",
                    "dataset_checksum",
                    "repository_snapshot",
                    "environment_hash",
                    "inference_profile_hash",
                    "case_ids",
                    "repeats",
                    "interleaving_seed",
                    "independent_verifier_id",
                    "cost_policy_hash",
                )
            }
            variants = manifest.get("variants")
            if not isinstance(variants, list):
                return False
            manifest_projection["variants"] = [
                {
                    "name": variant.get("name"),
                    "settings_hash": variant.get("settings_hash"),
                }
                for variant in variants
                if isinstance(variant, dict)
            ]
            if len(manifest_projection["variants"]) != len(variants) or value_hash(
                manifest_projection
            ) != manifest.get("manifest_hash"):
                return False
            policy_hashes.add(policy.policy_hash)
            manifest_hash = manifest.get("manifest_hash")
            for trial in report.get("trials", ()):
                if (
                    not isinstance(trial, dict)
                    or trial.get("manifest_hash") != manifest_hash
                ):
                    return False
                metrics = trial.get("metrics")
                if not isinstance(metrics, dict):
                    return False
                receipt = NormalizedCostReceipt.from_dict(
                    metrics["normalized_cost_receipt"], policy=policy
                )
                if (
                    metrics.get("normalized_cost_microunits")
                    != receipt.total_microunits
                    or metrics.get("normalized_cost") != receipt.normalized_cost
                ):
                    return False
                trial_count += 1
    except (KeyError, TypeError, ValueError):
        return False
    return trial_count > 0 and len(policy_hashes) == 1


def normalized_cost_bound_evidence_ready(
    workload_reports: list[dict[str, Any]],
) -> bool:
    """Revalidate every conservative bound and frozen-policy binding."""
    if not workload_reports:
        return False
    policy_hashes: set[str] = set()
    trial_count = 0
    try:
        for report in workload_reports:
            policy = NormalizedCostPolicy.from_dict(report["normalized_cost_policy"])
            manifest = report["manifest"]
            if manifest.get("cost_policy_hash") != policy.policy_hash:
                return False
            policy_hashes.add(policy.policy_hash)
            manifest_hash = manifest.get("manifest_hash")
            for trial in report.get("trials", ()):
                if trial.get("manifest_hash") != manifest_hash:
                    return False
                metrics = trial["metrics"]
                receipt = NormalizedCostBoundReceipt.from_dict(
                    metrics["normalized_cost_bound_receipt"], policy=policy
                )
                if (
                    metrics.get("normalized_cost_lower_bound_microunits")
                    != receipt.lower.total_microunits
                    or metrics.get("normalized_cost_upper_bound_microunits")
                    != receipt.upper.total_microunits
                ):
                    return False
                trial_count += 1
    except (KeyError, TypeError, ValueError):
        return False
    return trial_count > 0 and len(policy_hashes) == 1


def experiment_manifest(
    experiment: Path,
    manifest_payload: dict,
    results: list[dict],
    *,
    cost_policy_hash: str | None = None,
) -> ContextEvaluationManifest:
    variants = tuple(
        ContextVariant.build(payload["name"], variant_settings(payload))
        for payload in manifest_payload["variants"]
    )
    if manifest_payload.get("tasks"):
        case_ids = tuple(manifest_payload["tasks"])
        dataset_checksum = normalized_hash(
            manifest_payload.get("dataset_sha256"),
            {"tasks": case_ids},
        )
        adapter = manifest_payload.get("benchmark_adapter")
        if isinstance(adapter, str) and adapter.startswith("skillsbench-"):
            workload_kind = "skills_bench"
        elif adapter == "openai-browsecomp":
            workload_kind = "browse_research"
        elif adapter in (None, "terminal-bench-2.1"):
            workload_kind = "terminal_bench"
        else:
            workload_kind = "mixed_task_benchmark"
        verifier_id = (
            "python-functions-immutable-task-snapshot-v1"
            if manifest_payload.get("verifier_mode") == "python-functions"
            else "packaged-test-sh-v1"
        )
    else:
        cases = manifest_payload.get("cases") or []
        case_ids = tuple(case["case_id"] for case in cases)
        dataset_checksum = value_hash(
            [(case["case_id"], case["checksum"]) for case in cases]
        )
        kinds = sorted({case.get("workload_kind", "tool_research") for case in cases})
        workload_kind = kinds[0] if len(kinds) == 1 else "mixed_non_terminal"
        verifier_id = value_hash(
            [(case["case_id"], case.get("verifier_id")) for case in cases]
        )
    run_manifests = [
        read_json(run_directory(experiment, result) / "run_manifest.json", {})
        for result in results
    ]
    source_fingerprints = sorted(
        {
            (
                (item.get("aworld_source") or {}).get("source_fingerprint")
                or (item.get("aworld_source") or {}).get("commit")
            )
            for item in run_manifests
            if (
                (item.get("aworld_source") or {}).get("source_fingerprint")
                or (item.get("aworld_source") or {}).get("commit")
            )
        }
    )
    if len(source_fingerprints) > 1:
        raise ValueError(
            "experiment mixes AWorld runtime source fingerprints across runs"
        )
    repository_snapshot = source_fingerprints[0] if source_fingerprints else None
    inference_profiles = [item.get("invariants") or {} for item in run_manifests]
    containers = [item.get("container") or {} for item in run_manifests]
    return ContextEvaluationManifest.build(
        experiment_id=experiment.name,
        workload_id=str(manifest_payload.get("benchmark_adapter") or workload_kind),
        workload_kind=workload_kind,
        dataset_checksum=dataset_checksum,
        repository_snapshot=repository_snapshot or "unknown-local-snapshot",
        environment_hash=value_hash(containers),
        inference_profile_hash=value_hash(inference_profiles),
        variants=variants,
        case_ids=case_ids,
        repeats=int(manifest_payload["repeat"]),
        interleaving_seed=int(manifest_payload["seed"]),
        independent_verifier_id=str(verifier_id),
        cost_policy_hash=cost_policy_hash,
    )


def ablation_plan_from_manifest(payload: dict[str, Any]) -> ContextAblationPlan:
    """Rebuild a frozen ablation plan from variant truth, never self-attestation."""
    raw_plan = payload.get("ablation_plan")
    raw_variants = payload.get("variants")
    if not isinstance(raw_plan, dict) or not isinstance(raw_variants, list):
        raise ValueError("experiment has no ablation plan")
    variants = tuple(
        ContextVariant.build(item["name"], variant_settings(item))
        for item in raw_variants
        if isinstance(item, dict)
    )
    if len(variants) != len(raw_variants):
        raise ValueError("ablation variants are malformed")
    by_name = {variant.name: variant for variant in variants}
    raw_contrasts = raw_plan.get("contrasts")
    if not isinstance(raw_contrasts, list):
        raise ValueError("ablation contrasts are malformed")
    contrasts = []
    for raw in raw_contrasts:
        if not isinstance(raw, dict):
            raise ValueError("ablation contrast is malformed")
        try:
            contrast = ContextAblationContrast.build(
                baseline=by_name[raw["baseline_variant"]],
                candidate=by_name[raw["candidate_variant"]],
                component=ContextAblationComponent(raw["component"]),
            )
        except KeyError as exc:
            raise ValueError(
                "ablation contrast references an undeclared variant"
            ) from exc
        if contrast.to_dict() != raw:
            raise ValueError("ablation contrast receipt mismatch")
        contrasts.append(contrast)
    plan = ContextAblationPlan.build(
        name=str(raw_plan.get("name") or ""),
        variants=variants,
        contrasts=contrasts,
    )
    if plan.to_dict() != raw_plan:
        raise ValueError("ablation plan receipt mismatch")
    return plan


def trial_from_result(
    experiment: Path,
    manifest: ContextEvaluationManifest,
    result: dict,
    normalized_cost_policy: NormalizedCostPolicy | None = None,
) -> tuple[ContextTrialEvidence | None, dict]:
    run_dir = run_directory(experiment, result)
    run_manifest = read_json(run_dir / "run_manifest.json", {})
    task_response = read_json(run_dir / "task_response.json", {})
    build = task_response.get("trajectory_build_result") or {}
    calls = read_json(run_dir / "provider_calls.json", []) or []
    trace = read_json(run_dir / "context_trace.json", []) or []
    verifier_path = run_dir / "verifier.json"
    if verifier_path.exists():
        verifier_payload = read_json(verifier_path, {})
    else:
        reward_path = run_dir / "verifier" / "reward.txt"
        verifier_payload = {
            "reward": reward_path.read_text(encoding="utf-8").strip()
            if reward_path.exists()
            else result.get("reward"),
            "stdout_sha256": file_hash(run_dir / "verifier" / "stdout.log")
            if (run_dir / "verifier" / "stdout.log").exists()
            else None,
        }
    metrics = result.get("context_metrics") or {}
    integrity = provider_snapshot_integrity(calls)
    request_trace_exact = bool(
        calls
        and all(
            isinstance(call, dict) and call.get("request_trace_match") is True
            for call in calls
        )
    )
    trajectory_complete = (
        build.get("status") == "complete" and build.get("fidelity") == "complete"
    )
    gates = {
        "agent_completed": result.get("agent_exit_code") == 0,
        "capture_continuity": metrics.get("capture_integrity_available") is True,
        "provider_snapshot_integrity": integrity,
        "request_trace_match": request_trace_exact,
        "trajectory_complete": trajectory_complete,
        "raw_trajectory_available": metrics.get("raw_trajectory_available") is True,
        "reward_available": result.get("reward") not in (None, ""),
    }
    if not gates["reward_available"]:
        return None, gates
    fidelity = (
        TrialFidelity.COMPLETE
        if all(gates.values())
        else TrialFidelity.PARTIAL
        if gates["raw_trajectory_available"]
        else TrialFidelity.UNAVAILABLE
    )
    artifact_files = sorted(
        path
        for path in (run_dir / "tool-output-artifacts").glob("*.bin")
        if path.is_file()
    )
    numeric_metrics = {
        key: value
        for key, value in metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    started_at = run_manifest.get("started_at_epoch")
    finished_at = run_manifest.get("finished_at_epoch")
    if (
        isinstance(started_at, (int, float))
        and not isinstance(started_at, bool)
        and isinstance(finished_at, (int, float))
        and not isinstance(finished_at, bool)
        and math.isfinite(float(started_at))
        and math.isfinite(float(finished_at))
        and finished_at >= started_at
    ):
        numeric_metrics["wall_time_seconds"] = finished_at - started_at
    provider_metrics = authoritative_provider_metrics(calls)
    numeric_metrics.update(provider_metrics)
    if normalized_cost_policy is not None:
        usage, usage_reason = authoritative_normalized_usage(calls)
        try:
            if usage is None:
                raise ValueError(usage_reason or "normalized usage unavailable")
            normalized = compute_normalized_cost(
                policy=normalized_cost_policy,
                input_tokens=usage["input_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                output_tokens=usage["output_tokens"],
            )
            numeric_metrics["normalized_cost"] = normalized.normalized_cost
            numeric_metrics["normalized_cost_microunits"] = normalized.total_microunits
            numeric_metrics["normalized_cost_receipt"] = normalized.to_dict()
        except (TypeError, ValueError):
            numeric_metrics["normalized_cost_status"] = "unavailable"
            numeric_metrics["normalized_cost_reason"] = (
                usage_reason or "normalized_cost_computation_failed"
            )
        bounded_usage, bounded_reason = authoritative_normalized_usage_bounds(calls)
        try:
            if bounded_usage is None:
                raise ValueError(
                    bounded_reason or "normalized usage bounds unavailable"
                )
            lower = compute_normalized_cost(
                policy=normalized_cost_policy,
                **bounded_usage["lower"],
            )
            upper = compute_normalized_cost(
                policy=normalized_cost_policy,
                **bounded_usage["upper"],
            )
            bound_receipt = NormalizedCostBoundReceipt(
                policy_hash=normalized_cost_policy.policy_hash,
                lower=lower,
                upper=upper,
                total_call_count=bounded_usage["total_call_count"],
                exact_call_count=bounded_usage["exact_call_count"],
                cache_bounded_call_count=bounded_usage["cache_bounded_call_count"],
                provider_attempt_bounded_call_count=bounded_usage[
                    "provider_attempt_bounded_call_count"
                ],
                usage_bounded_call_count=bounded_usage["usage_bounded_call_count"],
                source_hash=bounded_usage["source_hash"],
            )
            numeric_metrics["normalized_cost_lower_bound_microunits"] = (
                lower.total_microunits
            )
            numeric_metrics["normalized_cost_upper_bound_microunits"] = (
                upper.total_microunits
            )
            numeric_metrics["normalized_cost_bound_receipt"] = bound_receipt.to_dict()
        except (TypeError, ValueError):
            numeric_metrics["normalized_cost_bound_status"] = "unavailable"
            numeric_metrics["normalized_cost_bound_reason"] = (
                bounded_reason or "normalized_cost_bound_computation_failed"
            )
    raw_trajectory = read_json(run_dir / "raw_trajectory.json", None)
    numeric_metrics["trajectory_items"] = (
        len(raw_trajectory) if isinstance(raw_trajectory, list) else 0
    )
    numeric_metrics["offloaded_artifact_count"] = len(artifact_files)
    numeric_metrics["offloaded_artifact_bytes"] = sum(
        path.stat().st_size for path in artifact_files
    )
    economics = turn_artifact_economics_summary(calls, raw_trajectory, artifact_files)
    semantic_progress = semantic_progress_summary(run_dir)
    if semantic_progress["status"] == "available":
        numeric_metrics.update(
            {
                f"semantic_{key}": value
                for key, value in semantic_progress["counts"].items()
            }
        )
    if economics["artifact_progress"]["status"] == "available":
        numeric_metrics.update(
            {
                "artifact_change_count": economics["artifact_progress"][
                    "artifact_change_count"
                ],
                "new_artifact_state_count": economics["artifact_progress"][
                    "new_artifact_state_count"
                ],
                "sandbox_rollback_count": economics["artifact_progress"][
                    "rollback_count"
                ],
                "implicit_artifact_loss_prevented_count": economics[
                    "artifact_progress"
                ]["implicit_artifact_loss_prevented_count"],
                "no_artifact_change_count": economics["artifact_progress"][
                    "no_artifact_change_count"
                ],
            }
        )
    if economics["tool_outputs"]["status"] == "available":
        numeric_metrics.update(
            {
                "raw_tool_output_bytes": economics["tool_outputs"]["raw_bytes"],
                "inline_tool_output_tokens": economics["tool_outputs"]["inline_tokens"],
                "offloaded_tool_output_tokens": economics["tool_outputs"][
                    "offloaded_tokens"
                ],
                "double_offload_count": economics["tool_outputs"][
                    "double_offload_count"
                ],
            }
        )
    if economics["retrieval"]["status"] == "available":
        numeric_metrics.update(
            {
                "artifact_retrieval_count": economics["retrieval"]["retrieval_count"],
                "artifact_retrieved_bytes": economics["retrieval"]["retrieved_bytes"],
                "artifact_consumed_count": economics["retrieval"]["consumed_count"],
            }
        )
    trial = ContextTrialEvidence(
        manifest_hash=manifest.manifest_hash,
        case_id=result["task"],
        repeat=int(result["repetition"]),
        variant=result["variant"],
        request_hash=value_hash(calls),
        trace_hash=value_hash(trace),
        trajectory_checksum=build.get("trajectory_checksum"),
        artifact_checksum=value_hash(
            [(path.name, file_hash(path)) for path in artifact_files]
        )
        if artifact_files
        else None,
        verifier_result_hash=value_hash(verifier_payload),
        reward=float(result["reward"]),
        fidelity=fidelity,
        metrics=numeric_metrics,
    )
    return trial, gates


def aggregate(
    experiments: list[Path],
    *,
    baseline: str,
    candidate: str,
    bootstrap_samples: int,
    seed: int,
    required_capabilities: tuple[tuple[str, str, str], ...] = (),
    rollback_bundle: RollbackBundle | None = None,
    canary_health_decision: CanaryHealthDecision | None = None,
    required_canary_policy_fingerprint: str | None = None,
) -> dict:
    workload_reports = []
    all_deltas = []
    delta_strata = []
    all_gates = []
    all_calls: list[dict] = []
    all_attribution_runs: list[dict[str, Any]] = []
    all_attribution_pairing: list[dict[str, Any]] = []
    all_economics_runs: list[dict[str, Any]] = []
    all_execution_depth_runs: list[dict[str, Any]] = []
    raw_rollout_capabilities: list[RolloutCapability] = []
    capability_evidence_runs: list[dict[str, Any]] = []
    workload_kinds = []
    candidate_baseline_modes: list[bool] = []
    for experiment in experiments:
        manifest_payload = read_json(experiment / "experiment_manifest.json")
        results = read_json(experiment / "results.json", [])
        if not isinstance(manifest_payload, dict) or not isinstance(results, list):
            raise ValueError(f"Incomplete experiment directory: {experiment}")
        manifest_variant_names = {
            item.get("name")
            for item in manifest_payload.get("variants", ())
            if isinstance(item, dict)
        }
        if (
            baseline not in manifest_variant_names
            or candidate not in manifest_variant_names
        ):
            raise ValueError(
                f"Experiment {experiment} does not declare contrast {baseline}:{candidate}"
            )
        variant_payload_by_name = {
            item["name"]: item
            for item in manifest_payload.get("variants", ())
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        allow_candidate_baseline = (
            variant_payload_by_name[baseline].get("context_compiler") or {}
        ).get("mode") in {"shadow", "enforce"}
        candidate_baseline_modes.append(allow_candidate_baseline)
        results = [
            result
            for result in results
            if isinstance(result, dict)
            and result.get("variant") in {baseline, candidate}
        ]
        try:
            normalized_cost_policy = NormalizedCostPolicy.from_dict(
                manifest_payload["normalized_cost_policy"]
            )
        except (KeyError, TypeError, ValueError):
            normalized_cost_policy = None
        manifest = experiment_manifest(
            experiment,
            manifest_payload,
            results,
            cost_policy_hash=(
                normalized_cost_policy.policy_hash
                if normalized_cost_policy is not None
                else None
            ),
        )
        trials = []
        gate_rows = []
        experiment_calls: list[dict] = []
        attribution_runs: list[dict[str, Any]] = []
        economics_runs: list[dict[str, Any]] = []
        execution_depth_runs: list[dict[str, Any]] = []
        for result in results:
            trial, gates = trial_from_result(
                experiment,
                manifest,
                result,
                normalized_cost_policy=normalized_cost_policy,
            )
            gate_rows.append(
                {"task": result.get("task"), "variant": result.get("variant"), **gates}
            )
            run_dir = run_directory(experiment, result)
            execution_depth_runs.append(
                {
                    "experiment": str(experiment),
                    "run": str(run_dir),
                    "case_id": result["task"],
                    "variant": result["variant"],
                    "repeat": int(result["repetition"]),
                    "summary": execution_depth_summary(run_dir, result),
                }
            )
            if trial is not None:
                trials.append(trial)
            calls = read_json(
                run_directory(experiment, result) / "provider_calls.json", []
            )
            if isinstance(calls, list):
                valid_calls = [call for call in calls if isinstance(call, dict)]
                experiment_calls.extend(valid_calls)
                attribution_runs.append(
                    {
                        "experiment": str(experiment),
                        "run": str(run_directory(experiment, result)),
                        "case_id": result["task"],
                        "variant": result["variant"],
                        "repeat": int(result["repetition"]),
                        "summary": provider_attribution_summary(valid_calls),
                    }
                )
                if result.get("variant") == candidate:
                    run_capabilities, capability_error = _verified_run_capabilities(
                        run_dir
                    )
                    raw_rollout_capabilities.extend(run_capabilities)
                    capability_evidence_runs.append(
                        {
                            "run": str(run_dir),
                            "status": (
                                "available"
                                if capability_error is None
                                else "unavailable"
                            ),
                            "reason_code": capability_error,
                            "verified_call_count": len(run_capabilities),
                        }
                    )
                artifacts = validated_context_artifact_files(run_dir)
                economics = turn_artifact_economics_summary(
                    valid_calls,
                    read_json(run_dir / "raw_trajectory.json", None),
                    artifacts,
                )
                economics["semantic_progress"] = semantic_progress_summary(run_dir)
                economics_runs.append(
                    {
                        "experiment": str(experiment),
                        "run": str(run_dir),
                        "case_id": result["task"],
                        "variant": result["variant"],
                        "repeat": int(result["repetition"]),
                        "summary": economics,
                    }
                )
        deltas = build_paired_deltas(
            trials,
            baseline_variant=baseline,
            candidate_variant=candidate,
        )
        summary = (
            summarize_context_benefit(
                deltas,
                bootstrap_samples=bootstrap_samples,
                seed=seed + len(workload_reports),
            )
            if deltas
            else None
        )
        attribution_pairing = provider_attribution_pairing_status(
            attribution_runs,
            experiment=str(experiment),
            case_ids=manifest.case_ids,
            repeats=manifest.repeats,
            baseline=baseline,
            candidate=candidate,
            allow_candidate_baseline=allow_candidate_baseline,
        )
        workload_reports.append(
            {
                "experiment": str(experiment),
                "manifest": plain(manifest),
                "trials": [plain(trial) for trial in trials],
                "gates": gate_rows,
                "benefit": plain(summary) if summary else None,
                "provider_attribution_runs": attribution_runs,
                "provider_attribution_deltas": paired_attribution_deltas(
                    attribution_runs,
                    baseline=baseline,
                    candidate=candidate,
                    allow_candidate_baseline=allow_candidate_baseline,
                ),
                "provider_attribution_pairing": attribution_pairing,
                "turn_artifact_economics_runs": economics_runs,
                "turn_artifact_economics_deltas": paired_turn_artifact_deltas(
                    economics_runs, baseline=baseline, candidate=candidate
                ),
                "execution_depth_runs": execution_depth_runs,
                "execution_depth_deltas": paired_execution_depth_deltas(
                    execution_depth_runs,
                    baseline=baseline,
                    candidate=candidate,
                ),
                "execution_depth_summary": execution_depth_aggregate(
                    execution_depth_runs
                ),
                "normalized_cost_policy": (
                    normalized_cost_policy.to_dict()
                    if normalized_cost_policy is not None
                    else {"status": "unavailable"}
                ),
            }
        )
        all_deltas.extend(deltas)
        if deltas:
            delta_strata.append(deltas)
        all_gates.extend(gate_rows)
        all_calls.extend(experiment_calls)
        all_attribution_runs.extend(attribution_runs)
        all_attribution_pairing.append(attribution_pairing)
        all_economics_runs.extend(economics_runs)
        all_execution_depth_runs.extend(execution_depth_runs)
        workload_kinds.append(manifest.workload_kind)
    combined = (
        summarize_stratified_context_benefit(
            delta_strata,
            bootstrap_samples=bootstrap_samples,
            seed=seed + 1000,
        )
        if all_deltas
        else None
    )
    capture_rate = (
        sum(
            row["provider_snapshot_integrity"] and row["capture_continuity"]
            for row in all_gates
        )
        / len(all_gates)
        if all_gates
        else 0.0
    )
    trajectory_rate = (
        sum(row["trajectory_complete"] for row in all_gates) / len(all_gates)
        if all_gates
        else 0.0
    )
    request_trace_rate = (
        sum(call.get("request_trace_match") is True for call in all_calls)
        / len(all_calls)
        if all_calls
        else 0.0
    )
    hard_failures = set()
    if not all_gates or any(not all(row.values()) for row in all_gates):
        hard_failures.add("trial_hard_gate_failed")
    normalized_cost_exact_policy_ready = normalized_cost_evidence_ready(
        workload_reports
    )
    normalized_cost_bound_policy_ready = normalized_cost_bound_evidence_ready(
        workload_reports
    )
    normalized_cost_policy_ready = bool(
        (normalized_cost_exact_policy_ready or normalized_cost_bound_policy_ready)
        and sum(len(report["trials"]) for report in workload_reports) == len(all_gates)
    )
    benefit = benefit_evidence(
        combined,
        normalized_cost_policy_ready=normalized_cost_policy_ready,
    )
    if not benefit["proven"]:
        hard_failures.add("positive_benefit_not_proven")
    if (
        any(row["summary"]["status"] != "available" for row in all_attribution_runs)
        or not all_attribution_runs
    ):
        hard_failures.add("provider_attribution_incomplete")
    if (
        any(row["status"] != "available" for row in all_attribution_pairing)
        or not all_attribution_pairing
    ):
        hard_failures.add("provider_attribution_pairing_incomplete")
    if (
        not all_economics_runs
        or any(
            row["summary"]["turn_causes"]["status"] != "available"
            or row["summary"]["tool_outputs"]["status"] != "available"
            or row["summary"]["retrieval"]["status"] == "unavailable"
            for row in all_economics_runs
        )
        or sum(
            row["summary"]["retrieval"]["opportunity_count"]
            for row in all_economics_runs
        )
        <= 0
        or sum(
            row["summary"]["retrieval"]["consumed_count"] or 0
            for row in all_economics_runs
        )
        != sum(
            row["summary"]["retrieval"]["opportunity_count"]
            for row in all_economics_runs
        )
    ):
        hard_failures.add("turn_artifact_economics_incomplete")
    capabilities_by_key: dict[tuple[str, str, str], list[RolloutCapability]] = {}
    for capability in raw_rollout_capabilities:
        capabilities_by_key.setdefault(
            (capability.provider, capability.entry_point, capability.call_shape), []
        ).append(capability)
    rollout_capabilities: tuple[RolloutCapability, ...] = tuple(
        RolloutCapability.combine_verified(capabilities_by_key[key])
        for key in sorted(capabilities_by_key)
    )
    capability_matrix_hash = value_hash(
        [
            {
                "provider": capability.provider,
                "entry_point": capability.entry_point,
                "call_shape": capability.call_shape,
                "evidence_fingerprint": capability.evidence_fingerprint,
            }
            for capability in rollout_capabilities
        ]
    )
    rollback_valid = bool(
        rollback_bundle is not None
        and rollout_capabilities
        and rollback_bundle.provider_capability_hash == capability_matrix_hash
    )
    if rollback_bundle is not None and not rollback_valid:
        hard_failures.add("rollback_capability_mismatch")
    if not capability_evidence_runs or any(
        row["status"] != "available" for row in capability_evidence_runs
    ):
        hard_failures.add("canary_receipt_evidence_incomplete")
    quality_regression = bool(
        combined is not None and combined.reward_interval.upper < 0.0
    )
    readiness = assess_default_on_readiness(
        capabilities=rollout_capabilities,
        workload_kinds=workload_kinds,
        complete_pairs=len(all_deltas),
        quality_regression=quality_regression,
        request_trace_match_rate=request_trace_rate,
        trajectory_complete_rate=trajectory_rate,
        rollback_config_hash=(rollback_bundle.bundle_hash if rollback_valid else None),
        hard_gate_failures=hard_failures,
        required_capabilities=required_capabilities,
        canary_health_decision=canary_health_decision,
        required_canary_policy_fingerprint=required_canary_policy_fingerprint,
    )
    return {
        "schema_version": "aworld.context-benefit-report/v1",
        "baseline_variant": baseline,
        "candidate_variant": candidate,
        "workloads": workload_reports,
        "combined_benefit": plain(combined) if combined else None,
        "combined_benefit_sampling": (
            {
                "method": "stratified_paired_bootstrap",
                "stratum_count": len(delta_strata),
                "weighting": "observed_pair_count",
            }
            if combined
            else None
        ),
        "benefit_evidence": benefit,
        "normalized_cost_policy_ready": normalized_cost_policy_ready,
        "normalized_cost_exact_policy_ready": normalized_cost_exact_policy_ready,
        "normalized_cost_bound_policy_ready": normalized_cost_bound_policy_ready,
        "capture_integrity_rate": capture_rate,
        "request_trace_match_rate": request_trace_rate,
        "trajectory_complete_rate": trajectory_rate,
        "provider_attribution_runs": all_attribution_runs,
        "provider_attribution_deltas": paired_attribution_deltas(
            all_attribution_runs,
            baseline=baseline,
            candidate=candidate,
            allow_candidate_baseline=bool(candidate_baseline_modes)
            and all(candidate_baseline_modes),
        ),
        "provider_attribution_pairing": all_attribution_pairing,
        "turn_artifact_economics_runs": all_economics_runs,
        "turn_artifact_economics_deltas": paired_turn_artifact_deltas(
            all_economics_runs, baseline=baseline, candidate=candidate
        ),
        "execution_depth_runs": all_execution_depth_runs,
        "execution_depth_deltas": paired_execution_depth_deltas(
            all_execution_depth_runs,
            baseline=baseline,
            candidate=candidate,
        ),
        "execution_depth_summary": execution_depth_aggregate(all_execution_depth_runs),
        "rollout_capabilities": [plain(value) for value in rollout_capabilities],
        "capability_evidence_runs": capability_evidence_runs,
        "required_capabilities": [list(value) for value in required_capabilities],
        "capability_matrix_hash": capability_matrix_hash,
        "rollback_bundle": plain(rollback_bundle) if rollback_valid else None,
        "canary_health": (
            plain(canary_health_decision) if canary_health_decision else None
        ),
        "default_on_readiness": plain(readiness),
        "decision_note": (
            "READY requires complete provider/trajectory gates, at least two workload kinds, "
            "at least ten complete pairs, and either a positive reward confidence lower bound "
            "or quality non-regression plus a confidence-bounded versioned/billed cost reduction "
            "or provider-call reduction. "
            "It also requires a healthy canary decision bound to the expected frozen health "
            "policy and the same executable rollback bundle; this offline report supplies no "
            "operational canary evidence by itself. Absolute token or wall-time growth is "
            "descriptive, not an automatic regression: quality still comes from the independent "
            "verifier, while typed progress/recovery evidence explains whether longer execution "
            "was productive."
        ),
    }


def aggregate_ablation(
    experiments: list[Path],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    """Evaluate every pre-declared component edge without outcome-driven selection."""
    plans = [
        ablation_plan_from_manifest(read_json(path / "experiment_manifest.json"))
        for path in experiments
    ]
    if not plans or len({plan.plan_hash for plan in plans}) != 1:
        raise ValueError("ablation experiments must share one frozen plan")
    plan = plans[0]
    contrast_reports = []
    for index, contrast in enumerate(plan.contrasts):
        report = aggregate(
            experiments,
            baseline=contrast.baseline_variant,
            candidate=contrast.candidate_variant,
            bootstrap_samples=bootstrap_samples,
            seed=seed + index * 10_000,
        )
        contrast_reports.append(
            {
                **contrast.to_dict(),
                "combined_benefit": report["combined_benefit"],
                "benefit_evidence": report["benefit_evidence"],
                "provider_attribution_deltas": report["provider_attribution_deltas"],
                "provider_attribution_pairing": report["provider_attribution_pairing"],
                "turn_artifact_economics_deltas": report[
                    "turn_artifact_economics_deltas"
                ],
                "hard_gate_failures": report["default_on_readiness"]["gate_failures"],
            }
        )
    return {
        "schema_version": "aworld.context-ablation-report/v1",
        "plan": plan.to_dict(),
        "experiment_count": len(experiments),
        "contrasts": contrast_reports,
        "interpretation_contract": {
            "absolute_token_growth_is_regression": False,
            "quality_requires_independent_reward": True,
            "efficiency_requires_quality_non_regression": True,
            "unmatched_calls_are_accounted_not_automatically_waste": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-dir", type=Path, action="append", required=True)
    parser.add_argument("--baseline", default="legacy")
    parser.add_argument("--candidate", default="unified-context-enforce")
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument(
        "--all-ablation-contrasts",
        action="store_true",
        help="Evaluate every contrast embedded in a frozen multi-arm experiment.",
    )
    parser.add_argument(
        "--required-capability",
        action="append",
        default=[],
        metavar="PROVIDER:ENTRY_POINT:CALL_SHAPE",
        help="Production capability matrix key; repeat for every required path.",
    )
    parser.add_argument(
        "--rollback-bundle",
        type=Path,
        help="Externally provisioned rollback bundle bound to the evidence matrix.",
    )
    parser.add_argument(
        "--canary-health-receipt",
        type=Path,
        help=(
            "Externally observed canary policy/evidence/decision receipt. The "
            "decision is recomputed and must bind to --rollback-bundle."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    required_capabilities = []
    for raw in args.required_capability:
        parts = tuple(raw.split(":"))
        if len(parts) != 3 or any(not part for part in parts):
            parser.error(
                "--required-capability must be PROVIDER:ENTRY_POINT:CALL_SHAPE"
            )
        required_capabilities.append(parts)
    rollback_bundle = None
    if args.rollback_bundle is not None:
        try:
            rollback_bundle = RollbackBundle.from_dict(
                read_json(args.rollback_bundle.resolve())
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"invalid --rollback-bundle: {exc}")
    canary_health_decision = None
    canary_policy_fingerprint = None
    if args.canary_health_receipt is not None:
        if rollback_bundle is None:
            parser.error("--canary-health-receipt requires --rollback-bundle")
        try:
            payload = read_json(args.canary_health_receipt.resolve())
            if (
                not isinstance(payload, dict)
                or set(payload) != {"schema_version", "policy", "evidence", "decision"}
                or payload.get("schema_version")
                != "aworld.context.canary-health-receipt.v1"
            ):
                raise ValueError("unsupported canary health receipt")
            policy = CanaryHealthPolicy(**payload["policy"])
            evidence = CanaryHealthEvidence(**payload["evidence"])
            canary_health_decision = assess_canary_health(
                policy=policy,
                evidence=evidence,
                rollback_bundle=rollback_bundle,
            )
            if plain(canary_health_decision) != payload["decision"]:
                raise ValueError("canary health decision receipt mismatch")
            canary_policy_fingerprint = canary_health_decision.policy_fingerprint
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"invalid --canary-health-receipt: {exc}")
    experiment_dirs = [path.resolve() for path in args.experiment_dir]
    if args.all_ablation_contrasts:
        if (
            required_capabilities
            or rollback_bundle is not None
            or canary_health_decision is not None
        ):
            parser.error(
                "capability and rollback gates apply to a shipping candidate, not component ablation"
            )
        report = aggregate_ablation(
            experiment_dirs,
            bootstrap_samples=args.bootstrap_samples,
            seed=args.seed,
        )
    else:
        report = aggregate(
            experiment_dirs,
            baseline=args.baseline,
            candidate=args.candidate,
            bootstrap_samples=args.bootstrap_samples,
            seed=args.seed,
            required_capabilities=tuple(required_capabilities),
            rollback_bundle=rollback_bundle,
            canary_health_decision=canary_health_decision,
            required_canary_policy_fingerprint=canary_policy_fingerprint,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "status": (
                    report["default_on_readiness"]["status"]
                    if "default_on_readiness" in report
                    else "ablation_complete"
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
