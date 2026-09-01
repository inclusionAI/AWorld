"""Aggregate real paired experiment artifacts into benefit/readiness evidence."""

from __future__ import annotations

import argparse
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


_COST_BENEFIT_METRICS = (
    "cost_per_successful_task",
    "provider_billed_cost",
    "normalized_cost",
)


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


def provider_attribution_summary(calls: list[dict]) -> dict[str, Any]:
    """Aggregate only provider receipts; prompt text is never classified."""
    dimensions = {name: {} for name in ("owner", "kind", "source_kind", "residency")}
    available = 0
    invalid = 0
    attributed = 0
    overhead = 0
    total = 0
    for call in calls:
        provider_snapshot = call.get("provider_request") or {}
        provider_payload = provider_snapshot.get("payload")
        rollout = call.get("context_rollout") or {}
        lowering = rollout.get("provider_lowering") or {}
        receipt = (
            lowering.get("attribution")
        )
        if not isinstance(receipt, dict):
            continue
        entries = receipt.get("entries")
        if (
            receipt.get("schema_version") != "aworld.context.provider-attribution.v1"
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
        canonical_body = canonical_json_bytes(provider_payload)
        canonical_hash = value_hash(provider_payload)
        candidate_hash = ((rollout.get("candidate_snapshot") or {}).get("content_hash"))
        if (
            provider_snapshot.get("request_id") != call.get("request_id")
            or provider_snapshot.get("content_hash") != canonical_hash
            or receipt.get("provider_request_content_hash") != canonical_hash
            or receipt.get("canonical_request_checksum") != canonical_hash
            or receipt.get("total_canonical_bytes") != len(canonical_body)
            or receipt.get("plan_request_id_hash") != value_hash({"request_id": call.get("request_id")})
            or receipt.get("candidate_content_hash") != candidate_hash
            or lowering.get("candidate_content_hash") != candidate_hash
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
        "by_dimension": {
            name: dict(sorted(values.items())) for name, values in dimensions.items()
        },
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
        else:
            status, reason = "available", None
            dimension_delta = {}
            for dimension in ("owner", "kind", "source_kind", "residency"):
                before = base["summary"]["by_dimension"][dimension]
                after = cand["summary"]["by_dimension"][dimension]
                dimension_delta[dimension] = {
                    code: after.get(code, 0) - before.get(code, 0)
                    for code in sorted(set(before) | set(after))
                }
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
            "by_dimension_delta": dimension_delta,
        })
    return deltas


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


def benefit_evidence(summary: Any) -> dict[str, Any]:
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
    numeric_metrics.update(authoritative_provider_metrics(calls))
    raw_trajectory = read_json(run_dir / "raw_trajectory.json", [])
    numeric_metrics["trajectory_items"] = (
        len(raw_trajectory) if isinstance(raw_trajectory, list) else 0
    )
    numeric_metrics["offloaded_artifact_count"] = len(artifact_files)
    numeric_metrics["offloaded_artifact_bytes"] = sum(
        path.stat().st_size for path in artifact_files
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
) -> dict:
    workload_reports = []
    all_deltas = []
    all_gates = []
    all_calls: list[dict] = []
    all_attribution_runs: list[dict[str, Any]] = []
    workload_kinds = []
    for experiment in experiments:
        manifest_payload = read_json(experiment / "experiment_manifest.json")
        results = read_json(experiment / "results.json", [])
        if not isinstance(manifest_payload, dict) or not isinstance(results, list):
            raise ValueError(f"Incomplete experiment directory: {experiment}")
        manifest = experiment_manifest(experiment, manifest_payload, results)
        trials = []
        gate_rows = []
        experiment_calls: list[dict] = []
        attribution_runs: list[dict[str, Any]] = []
        for result in results:
            trial, gates = trial_from_result(experiment, manifest, result)
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
            }
        )
        all_deltas.extend(deltas)
        all_gates.extend(gate_rows)
        all_calls.extend(experiment_calls)
        all_attribution_runs.extend(attribution_runs)
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
    benefit = benefit_evidence(combined)
    if not benefit["proven"]:
        hard_failures.add("positive_benefit_not_proven")
    if any(row["summary"]["status"] != "available" for row in all_attribution_runs) or not all_attribution_runs:
        hard_failures.add("provider_attribution_incomplete")
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
    )
    return {
        "schema_version": "aworld.context-benefit-report/v1",
        "baseline_variant": baseline,
        "candidate_variant": candidate,
        "workloads": workload_reports,
        "combined_benefit": plain(combined) if combined else None,
        "benefit_evidence": benefit,
        "capture_integrity_rate": capture_rate,
        "request_trace_match_rate": request_trace_rate,
        "trajectory_complete_rate": trajectory_rate,
        "provider_attribution_runs": all_attribution_runs,
        "provider_attribution_deltas": paired_attribution_deltas(
            all_attribution_runs, baseline=baseline, candidate=candidate
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
