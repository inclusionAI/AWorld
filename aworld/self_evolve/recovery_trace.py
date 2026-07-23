from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Mapping, Sequence

from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.trace_pack import TracePack


RECOVERY_TRACE_SCHEMA_VERSION = "aworld.self_evolve.recovery_trace.public.v1"
CONSTRAINT_RECOVERY_TRACE_SCHEMA_VERSION = (
    "aworld.self_evolve.constraint_recovery_trace.public.v1"
)
_SUCCESS_STATUSES = frozenset(
    {"success", "succeeded", "completed", "finished", "pass", "passed", "ok"}
)
_FAILURE_STATUSES = frozenset(
    {"cancelled", "error", "failed", "failure", "rejected", "timeout"}
)
_STDOUT_TOOL_PATTERN = re.compile(r"(?m)^\s*(?:\u25b6|\u25b7)\s+([A-Za-z][A-Za-z0-9_.:-]{0,79})\s*$")


def trace_pack_recovery_summary(pack: TracePack) -> dict[str, object]:
    """Return a bounded structural recovery summary for a historical trace.

    A trace that contains an intermediate failure but ends successfully is a
    recovery path, not a terminal failure path.  The summary intentionally
    contains no action arguments, task text, endpoint, or response payload.
    """

    statuses = tuple(_step_status(step.reward) for step in pack.steps)
    failure_indexes = tuple(
        index for index, status in enumerate(statuses) if status in _FAILURE_STATUSES
    )
    final_succeeded = bool(statuses) and statuses[-1] in _SUCCESS_STATUSES
    first_recovery_index = next(
        (
            index
            for index, status in enumerate(statuses)
            if status in _SUCCESS_STATUSES
            and any(failure_index < index for failure_index in failure_indexes)
        ),
        None,
    )
    recovered = first_recovery_index is not None and final_succeeded
    path = _trajectory_path_summary(pack.steps)
    return {
        "schema_version": RECOVERY_TRACE_SCHEMA_VERSION,
        "trace_identity": _identity_digest(pack.pack_id),
        "recovered": recovered,
        "terminal_success": final_succeeded,
        "failure_count": len(failure_indexes),
        "failed_attempts_before_recovery": (
            sum(1 for index in failure_indexes if index < first_recovery_index)
            if first_recovery_index is not None
            else len(failure_indexes)
        ),
        "steps_to_recovery": (
            first_recovery_index - failure_indexes[0]
            if first_recovery_index is not None and failure_indexes
            else 0
        ),
        **path,
    }


def replay_recovery_trace(
    members: Sequence[object],
) -> dict[str, object] | None:
    """Summarize paired replay recovery across arbitrary member cardinality.

    The function uses the public ReplayVariantResult surface by duck typing so
    the recovery schema remains independent from replay persistence versions.
    Independent repetitions are treated as stability evidence, while an
    in-trajectory failure-to-success transition is treated as recovery evidence.
    """

    member_summaries: list[dict[str, object]] = []
    total_baseline = 0
    total_candidate = 0
    baseline_successes = 0
    candidate_successes = 0
    recovered_members = 0
    stable_recovery_members = 0
    partial_recovery_members = 0
    regressed_members = 0
    recovered_transitions = 0
    for member in members[:64]:
        baseline_results = _variant_repetitions(getattr(member, "baseline", None))
        candidate_results = _variant_repetitions(getattr(member, "candidate", None))
        if not baseline_results and not candidate_results:
            continue
        baseline_paths = tuple(_result_path_summary(result) for result in baseline_results)
        candidate_paths = tuple(_result_path_summary(result) for result in candidate_results)
        baseline_count = len(baseline_results)
        candidate_count = len(candidate_results)
        baseline_ok = sum(1 for result in baseline_results if _result_succeeded(result))
        candidate_ok = sum(1 for result in candidate_results if _result_succeeded(result))
        total_baseline += baseline_count
        total_candidate += candidate_count
        baseline_successes += baseline_ok
        candidate_successes += candidate_ok
        baseline_rate = _rate(baseline_ok, baseline_count)
        candidate_rate = _rate(candidate_ok, candidate_count)
        delta = round(candidate_rate - baseline_rate, 6)
        if delta > 0:
            recovered_members += 1
            if candidate_ok == candidate_count and candidate_count:
                stable_recovery_members += 1
                classification = "stable_recovery"
            else:
                partial_recovery_members += 1
                classification = "partial_recovery"
        elif delta < 0:
            regressed_members += 1
            classification = "regression"
        elif candidate_ok == candidate_count and candidate_count:
            classification = "stable_success"
        else:
            classification = "unrecovered"
        member_transition_count = sum(
            int(path.get("recovered") is True) for path in candidate_paths
        )
        recovered_transitions += member_transition_count
        successful_paths = tuple(
            path
            for result, path in zip(candidate_results, candidate_paths)
            if _result_succeeded(result)
        )
        failed_paths = tuple(
            path
            for result, path in zip(candidate_results, candidate_paths)
            if not _result_succeeded(result)
        )
        successful_progress = _path_progress_values(successful_paths)
        failed_progress = _path_progress_values(failed_paths)
        successful_path_summary = _aggregate_path_summaries(successful_paths)
        failed_path_summary = _aggregate_path_summaries(failed_paths)
        failure_loop_detected = _failed_path_repetition_loop(
            failed_path_summary
        )
        member_summaries.append(
            {
                "member_identity": _identity_digest(
                    str(getattr(member, "case_id", len(member_summaries)))
                ),
                "classification": classification,
                "baseline_repetition_count": baseline_count,
                "candidate_repetition_count": candidate_count,
                "baseline_success_rate": baseline_rate,
                "candidate_success_rate": candidate_rate,
                "repeated_failure_rate": round(1.0 - candidate_rate, 6),
                "recovery_delta": delta,
                "failure_to_success_transition_count": member_transition_count,
                "successful_progress_min": min(successful_progress, default=0),
                "failed_progress_max": max(failed_progress, default=0),
                "failed_progress_exceeded_success": bool(
                    successful_progress
                    and failed_progress
                    and max(failed_progress) > min(successful_progress)
                ),
                "failure_loop_detected": failure_loop_detected,
                "successful_path": successful_path_summary,
                "failed_path": failed_path_summary,
            }
        )
    if not member_summaries:
        return None
    guidance: list[str] = []
    if recovered_members:
        guidance.append("preserve_positive_recovery_delta")
    if partial_recovery_members:
        guidance.append("stabilize_partial_recovery_across_repetitions")
    if any(item["failed_progress_exceeded_success"] for item in member_summaries):
        guidance.append("bound_post_checkpoint_exploration")
    if any(item["failure_loop_detected"] for item in member_summaries):
        guidance.append("replace_repeated_failure_loop_with_bounded_strategy_switch")
    if any(item["classification"] == "unrecovered" for item in member_summaries):
        guidance.append("repair_unrecovered_members_without_regressing_recovered_members")
    if regressed_members:
        guidance.append("restore_regressed_member_behavior")
    return {
        "schema_version": RECOVERY_TRACE_SCHEMA_VERSION,
        "member_count": len(member_summaries),
        "baseline_repetition_count": total_baseline,
        "candidate_repetition_count": total_candidate,
        "baseline_success_rate": _rate(baseline_successes, total_baseline),
        "candidate_success_rate": _rate(candidate_successes, total_candidate),
        "repeated_failure_rate": round(
            1.0 - _rate(candidate_successes, total_candidate),
            6,
        ),
        "recovery_success_delta": round(
            _rate(candidate_successes, total_candidate)
            - _rate(baseline_successes, total_baseline),
            6,
        ),
        "recovered_member_count": recovered_members,
        "stable_recovery_member_count": stable_recovery_members,
        "partial_recovery_member_count": partial_recovery_members,
        "regressed_member_count": regressed_members,
        "failure_to_success_transition_count": recovered_transitions,
        "guidance": guidance,
        "members": member_summaries[:16],
    }


def validate_public_recovery_trace(value: object) -> dict[str, object] | None:
    """Validate and bound an optimizer-facing recovery trace projection."""

    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") != RECOVERY_TRACE_SCHEMA_VERSION:
        return None
    scalar_keys = (
        "member_count",
        "baseline_repetition_count",
        "candidate_repetition_count",
        "baseline_success_rate",
        "candidate_success_rate",
        "recovery_success_delta",
        "recovered_member_count",
        "stable_recovery_member_count",
        "partial_recovery_member_count",
        "regressed_member_count",
        "failure_to_success_transition_count",
        "failure_count",
        "failed_attempts_before_recovery",
        "steps_to_recovery",
        "step_count",
        "tool_call_count",
        "distinct_tool_count",
        "strategy_switch_count",
        "repeated_action_rate",
        "repeated_failure_rate",
    )
    projected: dict[str, object] = {
        "schema_version": RECOVERY_TRACE_SCHEMA_VERSION,
    }
    for key in scalar_keys:
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        projected[key] = max(0, item) if key != "recovery_success_delta" else item
    trace_identity = value.get("trace_identity")
    if isinstance(trace_identity, str) and re.fullmatch(
        r"sha256:[0-9a-f]{64}", trace_identity
    ):
        projected["trace_identity"] = trace_identity
    for key in (
        "recovered",
        "terminal_success",
        "candidate_intervention_required",
        "candidate_intervention_observed",
    ):
        if isinstance(value.get(key), bool):
            projected[key] = value[key]
    tool_kinds = value.get("tool_kinds")
    if isinstance(tool_kinds, (list, tuple)):
        projected["tool_kinds"] = [
            sanitize_text(item, max_chars=80)
            for item in tool_kinds[:8]
            if isinstance(item, str) and item.strip()
        ]
    guidance = value.get("guidance")
    if isinstance(guidance, (list, tuple)):
        projected["guidance"] = [
            sanitize_text(item, max_chars=120)
            for item in guidance[:8]
            if isinstance(item, str) and item.strip()
        ]
    raw_members = value.get("members")
    members: list[dict[str, object]] = []
    if isinstance(raw_members, (list, tuple)):
        for raw in raw_members[:16]:
            if not isinstance(raw, Mapping):
                continue
            member: dict[str, object] = {}
            identity = raw.get("member_identity")
            if isinstance(identity, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
                member["member_identity"] = identity
            classification = raw.get("classification")
            if classification in {
                "stable_recovery",
                "partial_recovery",
                "regression",
                "stable_success",
                "unrecovered",
            }:
                member["classification"] = classification
            for key in (
                "baseline_repetition_count",
                "candidate_repetition_count",
                "baseline_success_rate",
                "candidate_success_rate",
                "repeated_failure_rate",
                "recovery_delta",
                "failure_to_success_transition_count",
                "successful_progress_min",
                "failed_progress_max",
            ):
                item = raw.get(key)
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    member[key] = item
            if isinstance(raw.get("failed_progress_exceeded_success"), bool):
                member["failed_progress_exceeded_success"] = raw[
                    "failed_progress_exceeded_success"
                ]
            if isinstance(raw.get("failure_loop_detected"), bool):
                member["failure_loop_detected"] = raw["failure_loop_detected"]
            for key in ("successful_path", "failed_path"):
                path = _validate_path_summary(raw.get(key))
                if path:
                    member[key] = path
            if member:
                members.append(member)
    if members:
        projected["members"] = members
    return projected


def update_constraint_recovery_trace(
    previous: object,
    *,
    violated_constraint_ids: Sequence[str],
    contract_constraint_ids: Sequence[str],
) -> dict[str, object] | None:
    """Advance a payload-free recovery trace for typed repair constraints.

    A validation attempt is informative only when it reports at least one
    concrete violated constraint.  Other inherited constraints in the same
    contract are then known to have survived that validation frontier.  The
    trace is cardinality-neutral: candidate populations and trajectory sets of
    any size contribute the same per-constraint state transitions.
    """

    active = {_constraint_identity(item) for item in violated_constraint_ids}
    active.discard(None)
    if not active:
        return validate_public_constraint_recovery_trace(previous)
    contract = {_constraint_identity(item) for item in contract_constraint_ids}
    contract.discard(None)
    contract.update(active)
    prior = validate_public_constraint_recovery_trace(previous) or {}
    states: dict[str, dict[str, object]] = {}
    raw_constraints = prior.get("constraints")
    if isinstance(raw_constraints, list):
        for raw in raw_constraints:
            if not isinstance(raw, Mapping):
                continue
            identity = _constraint_identity(raw.get("constraint_identity"))
            if identity is not None:
                states[identity] = dict(raw)
    for identity in sorted(contract | set(states)):
        state = states.get(
            identity,
            {
                "constraint_identity": identity,
                "status": "unobserved",
                "violation_attempt_count": 0,
                "consecutive_violation_count": 0,
                "recovery_count": 0,
                "regression_count": 0,
            },
        )
        previous_status = state.get("status")
        if identity in active:
            state["violation_attempt_count"] = int(
                state.get("violation_attempt_count") or 0
            ) + 1
            state["consecutive_violation_count"] = (
                int(state.get("consecutive_violation_count") or 0) + 1
                if previous_status in {"active", "regressed"}
                else 1
            )
            if previous_status == "recovered":
                state["regression_count"] = int(
                    state.get("regression_count") or 0
                ) + 1
                state["status"] = "regressed"
            else:
                state["status"] = "active"
        elif identity in contract and previous_status in {"active", "regressed"}:
            state["status"] = "recovered"
            state["consecutive_violation_count"] = 0
            state["recovery_count"] = int(state.get("recovery_count") or 0) + 1
        states[identity] = state
    constraints = [states[key] for key in sorted(states)][:100]
    active_count = sum(
        item.get("status") in {"active", "regressed"} for item in constraints
    )
    repeated_count = sum(
        int(item.get("violation_attempt_count") or 0) > 1
        for item in constraints
    )
    recovered_count = sum(
        item.get("status") == "recovered" for item in constraints
    )
    regressed_count = sum(
        int(item.get("regression_count") or 0) > 0 for item in constraints
    )
    guidance: list[str] = []
    if recovered_count:
        guidance.append("preserve_recovered_constraint_behavior")
    if repeated_count:
        guidance.append("switch_implementation_for_repeated_constraint_failure")
    if regressed_count:
        guidance.append("restore_regressed_constraint_checkpoint")
    return {
        "schema_version": CONSTRAINT_RECOVERY_TRACE_SCHEMA_VERSION,
        "attempt_count": int(prior.get("attempt_count") or 0) + 1,
        "constraint_count": len(constraints),
        "active_violation_count": active_count,
        "repeated_violation_count": repeated_count,
        "recovered_constraint_count": recovered_count,
        "regressed_constraint_count": regressed_count,
        "guidance": guidance,
        "constraints": constraints,
    }


def validate_public_constraint_recovery_trace(
    value: object,
) -> dict[str, object] | None:
    """Validate the public, identity-only conformance recovery projection."""

    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") != CONSTRAINT_RECOVERY_TRACE_SCHEMA_VERSION:
        return None
    projected: dict[str, object] = {
        "schema_version": CONSTRAINT_RECOVERY_TRACE_SCHEMA_VERSION,
    }
    for key in (
        "attempt_count",
        "constraint_count",
        "active_violation_count",
        "repeated_violation_count",
        "recovered_constraint_count",
        "regressed_constraint_count",
    ):
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            projected[key] = item
    guidance = value.get("guidance")
    if isinstance(guidance, (list, tuple)):
        projected["guidance"] = [
            sanitize_text(item, max_chars=120)
            for item in guidance[:8]
            if isinstance(item, str) and item.strip()
        ]
    constraints: list[dict[str, object]] = []
    raw_constraints = value.get("constraints")
    if isinstance(raw_constraints, (list, tuple)):
        for raw in raw_constraints[:100]:
            if not isinstance(raw, Mapping):
                continue
            identity = _constraint_identity(raw.get("constraint_identity"))
            status = raw.get("status")
            if identity is None or status not in {
                "unobserved",
                "active",
                "recovered",
                "regressed",
            }:
                continue
            item: dict[str, object] = {
                "constraint_identity": identity,
                "status": status,
            }
            for key in (
                "violation_attempt_count",
                "consecutive_violation_count",
                "recovery_count",
                "regression_count",
            ):
                count = raw.get(key)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    item[key] = count
            constraints.append(item)
    if constraints:
        projected["constraints"] = constraints
    return projected


def _constraint_identity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value if value.startswith("sha256:") else f"sha256:{value}"
    return (
        normalized
        if re.fullmatch(r"sha256:[0-9a-f]{64}", normalized)
        else None
    )


def _variant_repetitions(variant: object) -> tuple[object, ...]:
    if variant is None:
        return ()
    repetitions = getattr(variant, "repetition_results", ())
    candidates = tuple(repetitions) if repetitions else (variant,)
    return tuple(result for result in candidates if _result_executed(result))


def _result_executed(result: object) -> bool:
    executed = getattr(result, "executed", None)
    if isinstance(executed, bool):
        return executed
    return str(getattr(result, "status", "")).strip().lower() in {
        *_SUCCESS_STATUSES,
        *_FAILURE_STATUSES,
    }


def _result_succeeded(result: object) -> bool:
    succeeded = getattr(result, "succeeded", None)
    if isinstance(succeeded, bool):
        return succeeded
    return str(getattr(result, "status", "")).strip().lower() in _SUCCESS_STATUSES


def _result_path_summary(result: object) -> dict[str, object]:
    trajectory = getattr(result, "trajectory", ())
    steps = tuple(item for item in trajectory if isinstance(item, Mapping))
    summary = _trajectory_path_summary(steps)
    statuses = tuple(
        _step_status(item.get("reward"))
        for item in steps
        if isinstance(item, Mapping)
    )
    failure_seen = any(status in _FAILURE_STATUSES for status in statuses)
    summary["recovered"] = bool(
        failure_seen and statuses and statuses[-1] in _SUCCESS_STATUSES
    )
    if not summary.get("tool_call_count"):
        stdout_path = getattr(result, "stdout_path", None)
        if isinstance(stdout_path, str):
            stdout_summary = _stdout_path_summary(stdout_path)
            summary.update(
                {
                    key: value
                    for key, value in stdout_summary.items()
                    if value not in (None, (), [], 0)
                }
            )
    metrics = getattr(result, "metrics", None)
    if isinstance(metrics, Mapping):
        latency = metrics.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            summary["latency_ms"] = max(0.0, float(latency))
        cost = metrics.get("cost_usd", metrics.get("cost"))
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            summary["cost_usd"] = max(0.0, float(cost))
    return summary


def _trajectory_path_summary(steps: Sequence[object]) -> dict[str, object]:
    tools: list[str] = []
    for step in steps:
        raw_tools = getattr(step, "tool_names", None)
        if isinstance(raw_tools, tuple):
            tools.extend(str(item) for item in raw_tools if item)
            continue
        action = step.get("action") if isinstance(step, Mapping) else None
        calls = action.get("tool_calls") if isinstance(action, Mapping) else None
        if not isinstance(calls, list):
            continue
        for call in calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            name = function.get("name") if isinstance(function, Mapping) else None
            if isinstance(name, str) and name:
                tools.append(name)
    return _path_summary(step_count=len(steps), tools=tools)


def _stdout_path_summary(path: str) -> dict[str, object]:
    try:
        source = Path(path)
        if not source.is_file() or source.stat().st_size > 2_000_000:
            return {}
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    marker = "No history file. Start chatting to generate history."
    if marker in text:
        text = text.rsplit(marker, 1)[-1]
    tools = _STDOUT_TOOL_PATTERN.findall(text)
    return _path_summary(step_count=0, tools=tools)


def _path_summary(*, step_count: int, tools: Sequence[str]) -> dict[str, object]:
    normalized_tools = [sanitize_text(item, max_chars=80) for item in tools if item]
    switches = sum(
        1
        for previous, current in zip(normalized_tools, normalized_tools[1:])
        if previous != current
    )
    repeated = max(0, len(normalized_tools) - len(set(normalized_tools)))
    return {
        "step_count": max(0, int(step_count)),
        "tool_call_count": len(normalized_tools),
        "distinct_tool_count": len(set(normalized_tools)),
        "strategy_switch_count": switches,
        "repeated_action_rate": _rate(repeated, len(normalized_tools)),
        "tool_kinds": list(dict.fromkeys(normalized_tools))[:8],
    }


def _aggregate_path_summaries(
    paths: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if not paths:
        return {}
    numeric_keys = (
        "step_count",
        "tool_call_count",
        "distinct_tool_count",
        "strategy_switch_count",
        "repeated_action_rate",
        "latency_ms",
        "cost_usd",
    )
    result: dict[str, object] = {"sample_count": len(paths)}
    for key in numeric_keys:
        values = [
            float(value)
            for path in paths
            if isinstance((value := path.get(key)), (int, float))
            and not isinstance(value, bool)
        ]
        if values:
            result[f"{key}_min"] = min(values)
            result[f"{key}_max"] = max(values)
    kinds = [
        str(kind)
        for path in paths
        for kind in (
            path.get("tool_kinds")
            if isinstance(path.get("tool_kinds"), list)
            else []
        )
        if kind
    ]
    if kinds:
        result["tool_kinds"] = list(dict.fromkeys(kinds))[:8]
    return result


def _validate_path_summary(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, object] = {}
    for key, item in value.items():
        if key == "tool_kinds" and isinstance(item, (list, tuple)):
            result[key] = [
                sanitize_text(tool, max_chars=80)
                for tool in item[:8]
                if isinstance(tool, str) and tool.strip()
            ]
        elif key == "sample_count" or key.endswith("_min") or key.endswith("_max"):
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                result[str(key)] = max(0, item)
    return result


def _failed_path_repetition_loop(path: Mapping[str, object]) -> bool:
    repeated_rate = path.get("repeated_action_rate_max")
    tool_calls = path.get("tool_call_count_max")
    return bool(
        isinstance(repeated_rate, (int, float))
        and not isinstance(repeated_rate, bool)
        and float(repeated_rate) >= 0.5
        and isinstance(tool_calls, (int, float))
        and not isinstance(tool_calls, bool)
        and int(tool_calls) >= 3
    )


def _path_progress_values(
    paths: Sequence[Mapping[str, object]],
) -> tuple[int, ...]:
    return tuple(
        max(
            int(path.get("step_count", 0) or 0),
            int(path.get("tool_call_count", 0) or 0),
        )
        for path in paths
    )


def _step_status(reward: object) -> str:
    if not isinstance(reward, Mapping):
        return ""
    status = reward.get("status")
    return str(status).strip().lower() if status is not None else ""


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _identity_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
