"""Aggregate real paired experiment artifacts into benefit/readiness evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aworld.core.context.compiler import (
    AttributionCollection,
    AttributionCollectionShape,
    AttributionOwnerCode,
    AttributionSerialization,
    ContextKind,
    ContextCompilerMode,
    RollbackBundle,
    RolloutCapability,
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
from aworld.evaluations.context_benefit import (
    ContextEvaluationManifest,
    ContextTrialEvidence,
    ContextVariant,
    TrialFidelity,
    build_paired_deltas,
    summarize_context_benefit,
)
from aworld.evaluations.normalized_cost import (
    NormalizedCostPolicy,
    compute_normalized_cost,
)


_COST_BENEFIT_METRICS = (
    "cost_per_successful_task",
    "provider_billed_cost",
    "normalized_cost",
)

_TURN_CAUSES = {
    "initial_input", "model_choice", "validation_repair", "framework_retry",
    "deferred_catalog_expansion", "deferred_skill_expansion",
    "artifact_retrieval", "unavailable",
}
_SUPPORTED_TURN_CAUSES = {
    "initial_input", "model_choice", "framework_retry", "artifact_retrieval",
}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


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
    if not isinstance(value, dict) or value.get("schema_version") != "aworld.context.turn-economics.v1":
        return False
    cause = value.get("cause")
    supported = value.get("cause_supported")
    return bool(
        value.get("turn_kind") == expected_kind
        and cause in _TURN_CAUSES
        and isinstance(supported, bool)
        and supported == (cause in _SUPPORTED_TURN_CAUSES)
        and _SHA256.fullmatch(str(value.get("turn_id_hash", "")))
        and (expected_kind != "model" or _SHA256.fullmatch(str(value.get("request_id_hash", ""))))
        and (expected_kind != "tool" or _SHA256.fullmatch(str(value.get("tool_call_id_hash", ""))))
    )


def _valid_retrieval_receipt(value: Any, *, action_content: Any = None) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != "aworld.context.artifact-retrieval-receipt.v1":
        return False
    ints = ("artifact_byte_count", "offset", "limit", "returned_offset", "next_offset", "returned_byte_count")
    if any(isinstance(value.get(key), bool) or not isinstance(value.get(key), int) or value[key] < 0 for key in ints):
        return False
    if value["limit"] <= 0 or value["offset"] > value["artifact_byte_count"] or not isinstance(value.get("complete"), bool):
        return False
    hashes = (
        "plan_fingerprint", "owner_code", "action_code", "artifact_ref_hash",
        "artifact_content_hash", "consumer_tool_call_id_hash", "chunk_checksum",
        "source_content_hash", "result_content_hash",
    )
    if any(not _SHA256.fullmatch(str(value.get(key, ""))) for key in hashes):
        return False
    plan_projection = {
        "schema_version": "aworld.context.artifact-retrieval-plan.v1",
        **{
            key: value[key]
            for key in (
                "owner_code", "action_code", "artifact_ref_hash",
                "artifact_content_hash", "artifact_byte_count", "offset", "limit",
                "consumer_tool_call_id_hash",
            )
        },
    }
    if value_hash(plan_projection) != value["plan_fingerprint"]:
        return False
    if value["source_content_hash"] != value["artifact_content_hash"]:
        return False
    if value["returned_offset"] != value["offset"] or value["next_offset"] - value["returned_offset"] != value["returned_byte_count"]:
        return False
    if value["returned_byte_count"] > value["limit"] or value["next_offset"] > value["artifact_byte_count"]:
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
        and content.get("returned_bytes") == len(chunk) == value.get("returned_byte_count")
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
        return value.get("artifact_ref") == upstream[0]["ref"] and context_ref != value.get("artifact_ref")
    if upstream and role == "primary":
        return value.get("artifact_ref") == context_ref
    return value.get("artifact_ref") == (context_ref if context_ref else value.get("artifact_ref"))


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


def turn_artifact_economics_summary(
    calls: list[dict], raw_trajectory: Any, artifact_files: list[Path] | tuple[Path, ...]
) -> dict[str, Any]:
    """Recompute economics exclusively from typed runtime receipts and files."""
    trajectory_available = isinstance(raw_trajectory, list)
    action_results = _action_results(raw_trajectory)
    model_receipts = [call.get("turn_economics") for call in calls]
    tool_receipts = [((result.get("metadata") or {}).get("turn_economics")) for result in action_results]
    model_valid = bool(calls) and all(
        _valid_turn_receipt(item, expected_kind="model")
        and item.get("cause") != "unavailable"
        and isinstance(call.get("request_id"), str)
        and item.get("request_id_hash") == value_hash({"request_id": call["request_id"]})
        for call, item in zip(calls, model_receipts)
    ) and len({item["turn_id_hash"] for item in model_receipts}) == len(model_receipts)
    tool_valid = trajectory_available and bool(tool_receipts) and all(
        _valid_turn_receipt(item, expected_kind="tool")
        and item.get("cause") != "unavailable"
        and isinstance(result.get("tool_call_id"), str)
        and item.get("tool_call_id_hash") == value_hash({"tool_call_id": result["tool_call_id"]})
        for result, item in zip(action_results, tool_receipts)
    ) and len({item["turn_id_hash"] for item in tool_receipts}) == len(tool_receipts)
    if model_valid and tool_valid:
        model_turns = {item["turn_id_hash"] for item in model_receipts}
        tool_turns = {item["turn_id_hash"] for item in tool_receipts}
        parent_integrity = all(
            item.get("parent_turn_id_hash") is None
            or item["parent_turn_id_hash"] in tool_turns
            for item in model_receipts
        ) and all(
            item.get("parent_turn_id_hash") in model_turns
            for item in tool_receipts
        )
        model_valid = tool_valid = parent_integrity
    causes = {cause: {"model": 0, "tool": 0} for cause in sorted(_TURN_CAUSES)}
    if model_valid and tool_valid:
        for receipt in model_receipts:
            causes[receipt["cause"]]["model"] += 1
        for receipt in tool_receipts:
            causes[receipt["cause"]]["tool"] += 1

    output_receipts = [((result.get("metadata") or {}).get("tool_output_policy")) for result in action_results]
    artifact_files = list(artifact_files)
    artifact_by_checksum = {
        "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(): path
        for path in artifact_files
    }
    output_valid = trajectory_available and bool(output_receipts) and all(
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
                len(_raw_action_bytes(result.get("content"))) == item.get("raw_byte_count")
                and "sha256:" + hashlib.sha256(
                    _raw_action_bytes(result.get("content"))
                ).hexdigest() == item.get("raw_checksum")
            )
        )
        for result, item in zip(action_results, output_receipts)
    )
    retrieval_tool_receipts = [
        (result.get("metadata") or {}).get("artifact_retrieval")
        for result in action_results
        if (result.get("metadata") or {}).get("artifact_retrieval") is not None
    ]
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
    retrieval_valid = trajectory_available and bool(retrieval_pairs) and bool(consumption_receipts) and all(
        _valid_retrieval_receipt(item, action_content=result.get("content"))
        and item.get("owner_code") == value_hash({"owner_tool": result.get("tool_name")})
        and item.get("action_code") == value_hash({"retrieval_action": result.get("action_name")})
        and item.get("consumer_tool_call_id_hash")
        == value_hash({"tool_call_id": result.get("tool_call_id")})
        and _content_object(result.get("content")) is not None
        and item.get("artifact_ref_hash")
        == value_hash({"artifact_ref": _content_object(result.get("content")).get("artifact_ref")})
        for result, item in retrieval_pairs
    ) and all(
        _valid_retrieval_receipt(item)
        and isinstance(call.get("request_id"), str)
        and item.get("next_request_id_hash")
        == value_hash({"request_id": call["request_id"]})
        for call, item in consumption_pairs
    )
    tool_by_plan = {
        item["plan_fingerprint"]: item
        for result, item in retrieval_pairs
        if _valid_retrieval_receipt(item, action_content=result.get("content"))
        and item.get("owner_code") == value_hash({"owner_tool": result.get("tool_name")})
        and item.get("action_code") == value_hash({"retrieval_action": result.get("action_name")})
        and item.get("consumer_tool_call_id_hash")
        == value_hash({"tool_call_id": result.get("tool_call_id")})
        and _content_object(result.get("content")) is not None
        and item.get("artifact_ref_hash")
        == value_hash({"artifact_ref": _content_object(result.get("content")).get("artifact_ref")})
    }
    consumed_by_plan = {item["plan_fingerprint"]: item for item in consumption_receipts if _valid_retrieval_receipt(item)}
    consumption_bound = all(
        fingerprint in tool_by_plan
        and item.get("consumed") is True
        and item.get("result_content_hash") == tool_by_plan[fingerprint].get("result_content_hash")
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
    retrieval_valid = (
        retrieval_valid
        and consumption_bound
        and len(tool_by_plan) == len(retrieval_tool_receipts)
        and len(consumed_by_plan) == len(consumption_receipts)
        and set(tool_by_plan) == set(consumed_by_plan)
    )
    return {
        "schema_version": "aworld.context.turn-artifact-economics-summary.v1",
        "turn_causes": {
            "status": "available" if model_valid and tool_valid else "unavailable",
            "model_receipt_count": len(model_receipts) if model_valid else 0,
            "tool_receipt_count": len(tool_receipts) if tool_valid else 0,
            "counts": causes if model_valid and tool_valid else {},
        },
        "tool_outputs": {
            "status": "available" if output_valid else "unavailable",
            "raw_bytes": sum(item["raw_byte_count"] for item in output_receipts) if output_valid else None,
            "inline_tokens": sum(item["inline_tokens"] for item in output_receipts) if output_valid else None,
            "offloaded_tokens": sum(item["offloaded_tokens"] for item in output_receipts) if output_valid else None,
            "double_offload_count": sum(
                bool(
                    item.get("context_artifact_role") == "primary"
                    and item.get("context_artifact_ref")
                    and item.get("upstream_artifacts")
                )
                for item in output_receipts
            ) if output_valid else None,
            "audit_snapshot_count": sum(
                item.get("context_artifact_role") == "audit_snapshot"
                for item in output_receipts
            ) if output_valid else None,
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
            "opportunity_count": len(retrieval_pairs),
            "retrieval_count": len(tool_by_plan) if retrieval_valid else 0 if not retrieval_pairs else None,
            "retrieved_bytes": sum(item["returned_byte_count"] for item in tool_by_plan.values()) if retrieval_valid else 0 if not retrieval_pairs else None,
            "consumed_count": len(consumed_by_plan) if retrieval_valid else 0 if not retrieval_pairs else None,
            "consumption_coverage": (
                len(consumed_by_plan) / len(retrieval_pairs)
                if retrieval_pairs
                else 0.0
            ),
        },
    }


def paired_turn_artifact_deltas(
    runs: list[dict[str, Any]], *, baseline: str, candidate: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for run in runs:
        grouped.setdefault(
            (run["experiment"], run["case_id"], int(run["repeat"])), {}
        )[run["variant"]] = run["summary"]
    output: list[dict[str, Any]] = []
    for (experiment, case_id, repeat), variants in sorted(grouped.items()):
        left, right = variants.get(baseline), variants.get(candidate)
        available = bool(
            left and right
            and all(
                summary[section]["status"] == "available"
                for summary in (left, right)
                for section in ("turn_causes", "tool_outputs")
            )
            and (
                all(summary["retrieval"]["status"] == "available" for summary in (left, right))
                or all(summary["retrieval"]["status"] == "not_applicable" for summary in (left, right))
            )
        )
        row: dict[str, Any] = {
            "experiment": experiment,
            "case_id": case_id,
            "repeat": repeat,
            "status": "available" if available else "unsupported",
        }
        if available:
            row["candidate_minus_baseline"] = {
                "raw_tool_output_bytes": right["tool_outputs"]["raw_bytes"] - left["tool_outputs"]["raw_bytes"],
                "inline_tool_output_tokens": right["tool_outputs"]["inline_tokens"] - left["tool_outputs"]["inline_tokens"],
                "offloaded_tool_output_tokens": right["tool_outputs"]["offloaded_tokens"] - left["tool_outputs"]["offloaded_tokens"],
                "double_offload_count": right["tool_outputs"]["double_offload_count"] - left["tool_outputs"]["double_offload_count"],
                "retrieved_bytes": (right["retrieval"]["retrieved_bytes"] or 0) - (left["retrieval"]["retrieved_bytes"] or 0),
                "artifact_consumed_count": (right["retrieval"]["consumed_count"] or 0) - (left["retrieval"]["consumed_count"] or 0),
                "turn_causes": {
                    cause: {
                        kind: right["turn_causes"]["counts"][cause][kind] - left["turn_causes"]["counts"][cause][kind]
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
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def value_hash(value: object) -> str:
    return canonical_json_hash(value)


def normalized_hash(value: str | None, fallback: object) -> str:
    if isinstance(value, str):
        if value.startswith("sha256:") and len(value) == 71:
            return value
        if len(value) == 64:
            return "sha256:" + value
    return value_hash(fallback)


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
            receipt = (rollout.get("provider_lowering") or {}).get("provider_request") or {}
            if receipt.get("content_hash") != provider_request.get("content_hash"):
                return False
    return True


_PLAN_ENTRY_FIELDS = {
    "item_identity_hash", "owner_code", "kind", "source_kind", "stability",
    "collection", "ordinal", "content_hash", "token_estimate", "residency",
}
_PLAN_FIELDS = {
    "schema_version", "plan_fingerprint", "entry_count", "request_id_hash",
    "candidate_content_hash", "messages_shape", "messages_count",
    "tools_shape", "tools_count", "entries", "subject",
}


def _provider_attribution_evidence(rollout: dict) -> dict[str, Any] | None:
    evidence = rollout.get("provider_attribution")
    if isinstance(evidence, dict):
        return evidence
    lowering = rollout.get("provider_lowering")
    if isinstance(lowering, dict) and isinstance(lowering.get("attribution"), dict):
        candidate = rollout.get("candidate_snapshot") or {}
        return {
            **lowering,
            "subject": "candidate_selected",
            "subject_content_hash": candidate.get("content_hash"),
            "plan_fingerprint": lowering["attribution"].get("plan_fingerprint"),
        }
    return None


def validated_compiler_attribution_plan(
    call: dict, *, subject: str
) -> dict[str, Any] | None:
    """Validate the independent compiler evidence and canonical fingerprint."""
    rollout = call.get("context_rollout") or {}
    plan = rollout.get("compiler_attribution_plan")
    subject_snapshot = rollout.get(
        "observed_snapshot" if subject == "legacy_observed" else "candidate_snapshot"
    ) or {}
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
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("item_identity_hash", "")))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("content_hash", "")))
            or not isinstance(token_estimate, dict)
            or set(token_estimate) != {"value", "estimator", "exact"}
            or isinstance(token_estimate.get("value"), bool)
            or not isinstance(token_estimate.get("value"), int)
            or token_estimate.get("value") < 0
            or not isinstance(token_estimate.get("estimator"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", token_estimate["estimator"])
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
            and (isinstance(tools_count, bool) or not isinstance(tools_count, int) or tools_count < 0)
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
        or subject_snapshot.get("attribution_plan_fingerprint") != plan.get("plan_fingerprint")
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
    for call in calls:
        provider_snapshot = call.get("provider_request") or {}
        provider_payload = provider_snapshot.get("payload")
        rollout = call.get("context_rollout") or {}
        evidence = _provider_attribution_evidence(rollout) or {}
        subject = evidence.get("subject")
        receipt = evidence.get("attribution")
        if not isinstance(receipt, dict):
            continue
        if (
            call.get("provider_invoked") is not True
            or call.get("provider_attempt_status") != "attempted"
            or call.get("status") != "success"
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
            or receipt.get("serialization") not in {value.value for value in AttributionSerialization}
            or not isinstance(provider_payload, dict)
            or not isinstance(entries, list)
            or not all(
            isinstance(entry, dict) for entry in entries
            )
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
        subject_snapshot = rollout.get(
            "observed_snapshot" if subject == "legacy_observed" else "candidate_snapshot"
        ) or {}
        subject_hash = subject_snapshot.get("content_hash")
        if (
            provider_snapshot.get("request_id") != call.get("request_id")
            or provider_snapshot.get("content_hash") != canonical_hash
            or receipt.get("provider_request_content_hash") != canonical_hash
            or receipt.get("canonical_request_checksum") != canonical_hash
            or receipt.get("total_canonical_bytes") != len(canonical_body)
            or receipt.get("plan_request_id_hash") != value_hash({"request_id": call.get("request_id")})
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
            "absent" if not tools_present else "null" if tools is None else "array" if isinstance(tools, list) else "invalid"
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
                and (not isinstance(tools, list) or receipt.get("tools_count") != len(tools))
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
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("item_identity_hash", "")))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("content_hash", "")))
                or not isinstance(token_estimate, dict)
                or isinstance(token_estimate.get("value"), bool)
                or not isinstance(token_estimate.get("value"), int)
                or token_estimate.get("value") < 0
                or not isinstance(token_estimate.get("estimator"), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", token_estimate["estimator"])
                or not isinstance(token_estimate.get("exact"), bool)
            ):
                entries_valid = False
                break
            collection = entry["collection"]
            ordinal = entry.get("ordinal")
            values = messages if collection == "messages" else tools
            if (
                isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0
                or not isinstance(values, list) or ordinal >= len(values)
                or entry.get("content_hash") != value_hash(values[ordinal])
                or entry.get("canonical_value_bytes") != len(canonical_json_bytes(values[ordinal]))
            ):
                entries_valid = False
                break
            positions.append((collection, ordinal))
        expected_positions = [
            *(('messages', index) for index in range(len(messages))),
            *(('tools', index) for index in range(len(tools) if isinstance(tools, list) else 0)),
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
        "by_dimension": {
            name: dict(sorted(values.items())) for name, values in dimensions.items()
        },
        "dimension_resolution": dimension_resolution,
        "fallback": "none",
        "reason": None if complete else "provider_attribution_incomplete",
    }


def paired_attribution_deltas(
    rows: list[dict[str, Any]], *, baseline: str, candidate: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(
            (row["experiment"], row["case_id"], row["repeat"]), {}
        )[row["variant"]] = row
    deltas = []
    for (experiment, case_id, repeat), variants in sorted(grouped.items()):
        base = variants.get(baseline)
        cand = variants.get(candidate)
        if base is None or cand is None:
            status, reason = "unsupported", "paired_variant_missing"
            dimension_delta = None
        elif base["summary"]["status"] != "available" or cand["summary"]["status"] != "available":
            status, reason = "unsupported", "baseline_or_candidate_attribution_unavailable"
            dimension_delta = None
        elif (
            base["summary"].get("subject") != "legacy_observed"
            or cand["summary"].get("subject") != "candidate_selected"
        ):
            status, reason = "unsupported", "paired_attribution_subject_mismatch"
            dimension_delta = None
        else:
            status, reason = "available", None
            dimension_delta = {}
            dimension_status = {}
            for dimension in ("owner", "kind", "source_kind", "residency"):
                before_resolution = base["summary"].get("dimension_resolution", {}).get(dimension)
                after_resolution = cand["summary"].get("dimension_resolution", {}).get(dimension)
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
        deltas.append({
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
        })
    return deltas


def provider_attribution_pairing_status(
    rows: list[dict[str, Any]],
    *,
    experiment: str,
    case_ids: tuple[str, ...],
    repeats: int,
    baseline: str,
    candidate: str,
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
    ]
    actual = set(actual_list)
    deltas = paired_attribution_deltas(
        rows, baseline=baseline, candidate=candidate
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


def authoritative_provider_metrics(calls: list[dict]) -> dict[str, int | float]:
    """Recompute provider metrics from captured calls, never a stale summary."""
    metrics: dict[str, int | float] = {
        "provider_call_count": len(calls),
        "provider_request_bytes": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
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
        raw_usage = call.get("usage_raw") or usage
        prompt_details = raw_usage.get("prompt_tokens_details") or {}
        metrics["prompt_tokens"] += int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        metrics["completion_tokens"] += int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        metrics["cache_read_tokens"] += int(
            raw_usage.get("cache_hit_tokens")
            or raw_usage.get("cache_read_input_tokens")
            or prompt_details.get("cached_tokens")
            or 0
        )
        metrics["request_trace_match_count"] += int(
            call.get("request_trace_match") is True
        )
    if calls:
        metrics["request_trace_match_rate"] = (
            metrics["request_trace_match_count"] / len(calls)
        )
    attribution = provider_attribution_summary(calls)
    metrics["provider_attribution_receipt_count"] = attribution[
        "available_receipt_count"
    ]
    metrics["provider_attributed_value_bytes"] = attribution[
        "attributed_value_bytes"
    ]
    metrics["provider_attribution_overhead_bytes"] = attribution[
        "provider_envelope_and_params"
    ]
    return metrics


def benefit_evidence(
    summary: Any, *, normalized_cost_policy_ready: bool = False
) -> dict[str, Any]:
    """Accept quality gain or quality-safe, explicitly costed efficiency gain."""
    if summary is None:
        return {
            "proven": False,
            "path": None,
            "reason": "paired_summary_missing",
        }
    reward_lower = float(summary.reward_interval.lower)
    if reward_lower > 0.0:
        return {
            "proven": True,
            "path": "quality",
            "reason": "reward_confidence_lower_bound_positive",
        }
    # The quality gate permits at most one percentage point of regression.
    quality_non_regression = reward_lower >= -0.01
    for metric in _COST_BENEFIT_METRICS:
        if metric == "normalized_cost" and not normalized_cost_policy_ready:
            continue
        interval = summary.metric_intervals.get(metric)
        if (
            quality_non_regression
            and interval is not None
            and float(interval.upper) < 0.0
        ):
            return {
                "proven": True,
                "path": "efficiency",
                "reason": "quality_non_regression_and_cost_confidence_upper_bound_negative",
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
    }


def experiment_manifest(experiment: Path, manifest_payload: dict, results: list[dict]) -> ContextEvaluationManifest:
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
        workload_kind = "terminal_bench"
        verifier_id = "packaged-test-sh-v1"
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
    commits = sorted(
        {
            ((item.get("aworld_source") or {}).get("commit"))
            for item in run_manifests
            if ((item.get("aworld_source") or {}).get("commit"))
        }
    )
    repository_snapshot = commits[0] if len(commits) == 1 else value_hash(commits)
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
    )


def trial_from_result(
    experiment: Path,
    manifest: ContextEvaluationManifest,
    result: dict,
    normalized_cost_policy: NormalizedCostPolicy | None = None,
) -> tuple[ContextTrialEvidence | None, dict]:
    run_dir = run_directory(experiment, result)
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
    trajectory_complete = build.get("status") == "complete" and build.get("fidelity") == "complete"
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
        path for path in (run_dir / "tool-output-artifacts").glob("*.bin") if path.is_file()
    )
    numeric_metrics = {
        key: value
        for key, value in metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    provider_metrics = authoritative_provider_metrics(calls)
    numeric_metrics.update(provider_metrics)
    if normalized_cost_policy is not None:
        try:
            normalized = compute_normalized_cost(
                policy=normalized_cost_policy,
                input_tokens=int(provider_metrics["prompt_tokens"]),
                cache_read_tokens=int(provider_metrics["cache_read_tokens"]),
                output_tokens=int(provider_metrics["completion_tokens"]),
            )
            numeric_metrics["normalized_cost"] = normalized.normalized_cost
        except (TypeError, ValueError):
            pass
    raw_trajectory = read_json(run_dir / "raw_trajectory.json", None)
    numeric_metrics["trajectory_items"] = (
        len(raw_trajectory) if isinstance(raw_trajectory, list) else 0
    )
    numeric_metrics["offloaded_artifact_count"] = len(artifact_files)
    numeric_metrics["offloaded_artifact_bytes"] = sum(
        path.stat().st_size for path in artifact_files
    )
    economics = turn_artifact_economics_summary(calls, raw_trajectory, artifact_files)
    if economics["tool_outputs"]["status"] == "available":
        numeric_metrics.update({
            "raw_tool_output_bytes": economics["tool_outputs"]["raw_bytes"],
            "inline_tool_output_tokens": economics["tool_outputs"]["inline_tokens"],
            "offloaded_tool_output_tokens": economics["tool_outputs"]["offloaded_tokens"],
            "double_offload_count": economics["tool_outputs"]["double_offload_count"],
        })
    if economics["retrieval"]["status"] == "available":
        numeric_metrics.update({
            "artifact_retrieval_count": economics["retrieval"]["retrieval_count"],
            "artifact_retrieved_bytes": economics["retrieval"]["retrieved_bytes"],
            "artifact_consumed_count": economics["retrieval"]["consumed_count"],
        })
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
) -> dict:
    workload_reports = []
    all_deltas = []
    all_gates = []
    all_calls: list[dict] = []
    all_attribution_runs: list[dict[str, Any]] = []
    all_attribution_pairing: list[dict[str, Any]] = []
    all_economics_runs: list[dict[str, Any]] = []
    workload_kinds = []
    normalized_cost_policy_hashes: list[str | None] = []
    for experiment in experiments:
        manifest_payload = read_json(experiment / "experiment_manifest.json")
        results = read_json(experiment / "results.json", [])
        if not isinstance(manifest_payload, dict) or not isinstance(results, list):
            raise ValueError(f"Incomplete experiment directory: {experiment}")
        manifest = experiment_manifest(experiment, manifest_payload, results)
        try:
            normalized_cost_policy = NormalizedCostPolicy.from_dict(
                manifest_payload["normalized_cost_policy"]
            )
        except (KeyError, TypeError, ValueError):
            normalized_cost_policy = None
        normalized_cost_policy_hashes.append(
            normalized_cost_policy.policy_hash
            if normalized_cost_policy is not None
            else None
        )
        trials = []
        gate_rows = []
        experiment_calls: list[dict] = []
        attribution_runs: list[dict[str, Any]] = []
        economics_runs: list[dict[str, Any]] = []
        for result in results:
            trial, gates = trial_from_result(
                experiment,
                manifest,
                result,
                normalized_cost_policy=normalized_cost_policy,
            )
            gate_rows.append({"task": result.get("task"), "variant": result.get("variant"), **gates})
            if trial is not None:
                trials.append(trial)
            calls = read_json(
                run_directory(experiment, result) / "provider_calls.json", []
            )
            if isinstance(calls, list):
                valid_calls = [call for call in calls if isinstance(call, dict)]
                experiment_calls.extend(valid_calls)
                attribution_runs.append({
                    "experiment": str(experiment),
                    "run": str(run_directory(experiment, result)),
                    "case_id": result["task"],
                    "variant": result["variant"],
                    "repeat": int(result["repetition"]),
                    "summary": provider_attribution_summary(valid_calls),
                })
                run_dir = run_directory(experiment, result)
                artifacts = sorted(
                    path for path in (run_dir / "tool-output-artifacts").glob("*.bin")
                    if path.is_file()
                )
                economics_runs.append({
                    "experiment": str(experiment),
                    "run": str(run_dir),
                    "case_id": result["task"],
                    "variant": result["variant"],
                    "repeat": int(result["repetition"]),
                    "summary": turn_artifact_economics_summary(
                        valid_calls,
                        read_json(run_dir / "raw_trajectory.json", None),
                        artifacts,
                    ),
                })
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
                    attribution_runs, baseline=baseline, candidate=candidate
                ),
                "provider_attribution_pairing": attribution_pairing,
                "turn_artifact_economics_runs": economics_runs,
                "turn_artifact_economics_deltas": paired_turn_artifact_deltas(
                    economics_runs, baseline=baseline, candidate=candidate
                ),
                "normalized_cost_policy": (
                    normalized_cost_policy.to_dict()
                    if normalized_cost_policy is not None
                    else {"status": "unavailable"}
                ),
            }
        )
        all_deltas.extend(deltas)
        all_gates.extend(gate_rows)
        all_calls.extend(experiment_calls)
        all_attribution_runs.extend(attribution_runs)
        all_attribution_pairing.append(attribution_pairing)
        all_economics_runs.extend(economics_runs)
        workload_kinds.append(manifest.workload_kind)
    combined = (
        summarize_context_benefit(
            all_deltas,
            bootstrap_samples=bootstrap_samples,
            seed=seed + 1000,
        )
        if all_deltas
        else None
    )
    capture_rate = (
        sum(row["provider_snapshot_integrity"] and row["capture_continuity"] for row in all_gates)
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
    normalized_cost_policy_ready = bool(
        normalized_cost_policy_hashes
        and all(normalized_cost_policy_hashes)
        and len(set(normalized_cost_policy_hashes)) == 1
        and sum(len(report["trials"]) for report in workload_reports)
        == len(all_gates)
        and all(
            "normalized_cost" in (trial.get("metrics") or {})
            for report in workload_reports
            for trial in report["trials"]
        )
    )
    benefit = benefit_evidence(
        combined,
        normalized_cost_policy_ready=normalized_cost_policy_ready,
    )
    if not benefit["proven"]:
        hard_failures.add("positive_benefit_not_proven")
    if not normalized_cost_policy_ready:
        hard_failures.add("normalized_cost_evidence_incomplete")
    if any(row["summary"]["status"] != "available" for row in all_attribution_runs) or not all_attribution_runs:
        hard_failures.add("provider_attribution_incomplete")
    if any(row["status"] != "available" for row in all_attribution_pairing) or not all_attribution_pairing:
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
        ) <= 0
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
    rollback = RollbackBundle.build(
        previous_mode=ContextCompilerMode.SHADOW,
        previous_config={"mode": "shadow"},
        provider_capability_hash=value_hash({"openai": "provider-prepared-v1"}),
    )
    capability = RolloutCapability(
        provider="openai",
        entry_point="agent",
        provider_lowering=capture_rate == 1.0,
        request_trace_match=request_trace_rate == 1.0,
        lifecycle=True,
        trajectory_complete=trajectory_rate == 1.0,
    )
    quality_regression = bool(
        combined is not None and combined.reward_interval.upper < 0.0
    )
    readiness = assess_default_on_readiness(
        capabilities=(capability,),
        workload_kinds=workload_kinds,
        complete_pairs=len(all_deltas),
        quality_regression=quality_regression,
        request_trace_match_rate=request_trace_rate,
        trajectory_complete_rate=trajectory_rate,
        rollback_config_hash=rollback.bundle_hash,
        hard_gate_failures=hard_failures,
        required_capabilities=(
            ("openai", "agent"),
            ("openai", "amni"),
            ("openai", "cli"),
            ("openai", "acp"),
            ("openai", "resume"),
        ),
    )
    return {
        "schema_version": "aworld.context-benefit-report/v1",
        "baseline_variant": baseline,
        "candidate_variant": candidate,
        "workloads": workload_reports,
        "combined_benefit": plain(combined) if combined else None,
        "benefit_evidence": benefit,
        "normalized_cost_policy_ready": normalized_cost_policy_ready,
        "capture_integrity_rate": capture_rate,
        "request_trace_match_rate": request_trace_rate,
        "trajectory_complete_rate": trajectory_rate,
        "provider_attribution_runs": all_attribution_runs,
        "provider_attribution_deltas": paired_attribution_deltas(
            all_attribution_runs, baseline=baseline, candidate=candidate
        ),
        "provider_attribution_pairing": all_attribution_pairing,
        "turn_artifact_economics_runs": all_economics_runs,
        "turn_artifact_economics_deltas": paired_turn_artifact_deltas(
            all_economics_runs, baseline=baseline, candidate=candidate
        ),
        "rollback_bundle": plain(rollback),
        "default_on_readiness": plain(readiness),
        "decision_note": (
            "READY requires complete provider/trajectory gates, at least two workload kinds, "
            "at least ten complete pairs, and either a positive reward confidence lower bound "
            "or quality non-regression plus a confidence-bounded versioned/billed cost reduction."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-dir", type=Path, action="append", required=True)
    parser.add_argument("--baseline", default="legacy")
    parser.add_argument("--candidate", default="unified-context-enforce")
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = aggregate(
        [path.resolve() for path in args.experiment_dir],
        baseline=args.baseline,
        candidate=args.candidate,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "status": report["default_on_readiness"]["status"]}))


if __name__ == "__main__":
    main()
