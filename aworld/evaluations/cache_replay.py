"""Provider-neutral deterministic replay checks for Context cache evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aworld.core.context.compiler import CacheBreakReason, canonical_json_hash
from aworld.models.usage import reconcile_cache_usage_receipt


_PROFILE_BREAK_FIELDS = {
    "provider": CacheBreakReason.PROVIDER_CHANGE,
    "model": CacheBreakReason.MODEL_CHANGE,
    "reasoning_effort": CacheBreakReason.EFFORT_CHANGE,
    "execution_mode": CacheBreakReason.EXECUTION_MODE_CHANGE,
    "response_format_hash": CacheBreakReason.RESPONSE_FORMAT_CHANGE,
    "context_limit": CacheBreakReason.CONTEXT_LIMIT_CHANGE,
}
_PLAN_BREAK_FIELDS = {
    "tool_catalog_hash": CacheBreakReason.TOOL_CATALOG_CHANGE,
    "skill_set_hash": CacheBreakReason.SKILL_SET_CHANGE,
    "policy_version": CacheBreakReason.POLICY_VERSION_CHANGE,
    "provider_cache_namespace_hash": (
        CacheBreakReason.PROVIDER_CACHE_NAMESPACE_CHANGE
    ),
}
_EXTERNAL_LIFECYCLE_BREAKS = {
    CacheBreakReason.TASK_RESET,
    CacheBreakReason.RESUME_CACHE_EXPIRED,
    CacheBreakReason.PROVIDER_CACHE_UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class CacheReplayCallReceipt:
    """Redacted result for one captured provider attempt."""

    ordinal: int
    request_id_hash: str
    provider_request_hash: str | None
    cache_plan_fingerprint: str | None
    cache_epoch: int | None
    expected_cache_reuse: bool | None
    observed_cache_read_tokens: int | None
    trace_match: bool
    exact_usage: bool
    explained_break_reasons: tuple[str, ...]
    failure_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "request_id_hash": self.request_id_hash,
            "provider_request_hash": self.provider_request_hash,
            "cache_plan_fingerprint": self.cache_plan_fingerprint,
            "cache_epoch": self.cache_epoch,
            "expected_cache_reuse": self.expected_cache_reuse,
            "observed_cache_read_tokens": self.observed_cache_read_tokens,
            "trace_match": self.trace_match,
            "exact_usage": self.exact_usage,
            "explained_break_reasons": list(self.explained_break_reasons),
            "failure_codes": list(self.failure_codes),
        }


@dataclass(frozen=True, slots=True)
class CacheReplayReport:
    """Machine-verifiable aggregate without prompt or response content."""

    calls: tuple[CacheReplayCallReceipt, ...]
    status: str
    request_trace_match_rate: float
    exact_usage_coverage: float
    break_event_count: int
    explained_break_count: int
    unexplained_break_count: int
    failure_codes: tuple[str, ...]

    SCHEMA_VERSION = "aworld.context.cache-replay.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "status": self.status,
            "request_trace_match_rate": self.request_trace_match_rate,
            "exact_usage_coverage": self.exact_usage_coverage,
            "break_event_count": self.break_event_count,
            "explained_break_count": self.explained_break_count,
            "unexplained_break_count": self.unexplained_break_count,
            "failure_codes": list(self.failure_codes),
            "calls": [call.to_dict() for call in self.calls],
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _cache_key(plan: Mapping[str, Any]) -> tuple[Any, ...]:
    profile = _mapping(plan.get("inference_profile"))
    return (
        *(profile.get(field) for field in _PROFILE_BREAK_FIELDS),
        *(plan.get(field) for field in _PLAN_BREAK_FIELDS),
        plan.get("logical_stable_prefix_hash"),
        plan.get("cache_epoch"),
    )


def _transition_breaks(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> tuple[set[CacheBreakReason], list[str]]:
    if previous is None:
        return set(), []
    explained: set[CacheBreakReason] = set()
    failures: list[str] = []
    previous_profile = _mapping(previous.get("inference_profile"))
    current_profile = _mapping(current.get("inference_profile"))
    for field, reason in _PROFILE_BREAK_FIELDS.items():
        if previous_profile.get(field) != current_profile.get(field):
            explained.add(reason)
    for field, reason in _PLAN_BREAK_FIELDS.items():
        if previous.get(field) != current.get(field):
            explained.add(reason)
    if previous.get("logical_stable_prefix_hash") != current.get(
        "logical_stable_prefix_hash"
    ):
        explained.add(CacheBreakReason.SERIALIZED_PREFIX_CHANGE)

    previous_epoch = previous.get("cache_epoch")
    current_epoch = current.get("cache_epoch")
    if (
        isinstance(previous_epoch, int)
        and not isinstance(previous_epoch, bool)
        and isinstance(current_epoch, int)
        and not isinstance(current_epoch, bool)
    ):
        if current_epoch < previous_epoch:
            failures.append("cache_epoch_regressed")
        elif current_epoch > previous_epoch:
            explained.add(CacheBreakReason.HISTORY_COMPACTION)
    return explained, failures


def evaluate_cache_replay(
    calls: Sequence[Mapping[str, Any]],
    *,
    require_behavioral_cache: bool = True,
) -> CacheReplayReport:
    """Replay captured cache keys and validate request, break, and usage truth.

    The simulator operates only on immutable hashes, epochs, and provider usage.
    It does not inspect model identity or vendor-specific cache fields.
    """
    seen_keys: set[tuple[Any, ...]] = set()
    receipts: list[CacheReplayCallReceipt] = []
    previous_plan: Mapping[str, Any] | None = None
    break_events = 0
    explained_breaks = 0
    unexplained_breaks = 0

    for ordinal, call in enumerate(calls):
        failures: list[str] = []
        explained: set[CacheBreakReason] = set()
        request_id = call.get("request_id")
        request_id_hash = canonical_json_hash({"request_id": request_id})
        provider_request = _mapping(call.get("provider_request"))
        provider_payload = provider_request.get("payload")
        provider_hash = (
            canonical_json_hash(provider_payload)
            if isinstance(provider_payload, Mapping)
            else None
        )
        if call.get("status") != "success":
            failures.append("call_not_successful")
        if call.get("provider_invoked") is not True or call.get(
            "provider_attempt_status"
        ) != "attempted":
            failures.append("provider_attempt_missing")
        if provider_hash is None or provider_request.get("content_hash") != provider_hash:
            failures.append("provider_request_hash_mismatch")
        if provider_request.get("request_id") != request_id:
            failures.append("provider_request_id_mismatch")

        rollout = _mapping(call.get("context_rollout"))
        final_compile = _mapping(rollout.get("final_compile"))
        plan = _mapping(final_compile.get("cache_plan"))
        candidate = _mapping(rollout.get("candidate_snapshot"))
        lowering = _mapping(rollout.get("provider_lowering"))
        lowering_request = _mapping(lowering.get("provider_request"))
        plan_fingerprint = plan.get("fingerprint")
        if not plan:
            failures.append("cache_plan_missing")
        else:
            candidate_hash = candidate.get("content_hash")
            candidate_contract_hash = canonical_json_hash(
                {
                    "candidate_content_hash": candidate_hash,
                    "cache_plan_fingerprint": plan_fingerprint,
                }
            )
            cache_epoch = plan.get("cache_epoch")
            if (
                isinstance(cache_epoch, bool)
                or not isinstance(cache_epoch, int)
                or cache_epoch < 0
            ):
                failures.append("cache_epoch_invalid")
            if plan.get("schema_version") != "aworld.context.cache-plan.v1":
                failures.append("cache_plan_schema_invalid")
            if plan.get("candidate_content_hash") != candidate_hash:
                failures.append("cache_plan_candidate_mismatch")
            if candidate.get("cache_plan_fingerprint") != plan_fingerprint:
                failures.append("candidate_cache_plan_mismatch")
            if candidate.get("candidate_contract_hash") != candidate_contract_hash:
                failures.append("candidate_contract_mismatch")
            if plan.get("candidate_contract_hash") != candidate_contract_hash:
                failures.append("cache_plan_contract_mismatch")
            if lowering.get("cache_plan_fingerprint") != plan_fingerprint:
                failures.append("lowering_cache_plan_mismatch")
            if lowering.get("candidate_contract_hash") != candidate_contract_hash:
                failures.append("lowering_candidate_contract_mismatch")
            if lowering.get("candidate_content_hash") != candidate_hash:
                failures.append("lowering_candidate_mismatch")
            if lowering_request.get("content_hash") != provider_hash:
                failures.append("lowering_provider_request_mismatch")
            partition = _mapping(final_compile.get("partition"))
            if plan.get("logical_stable_prefix_hash") != partition.get(
                "stable_prefix_hash"
            ):
                failures.append("stable_prefix_partition_mismatch")

        trace_match = call.get("request_trace_match") is True
        if not trace_match:
            failures.append("request_trace_mismatch")
        if provider_request.get("capture_stage") != "provider_prepared" or (
            provider_request.get("fidelity") != "provider_prepared"
        ):
            failures.append("provider_request_not_prepared")

        usage = reconcile_cache_usage_receipt(
            captured_receipt=call.get("cache_usage_receipt"),
            raw_usage=call.get("usage_raw"),
            normalized_usage=call.get("usage_normalized") or call.get("usage"),
        )
        exact_usage = usage.fidelity.value == "exact"
        if not exact_usage:
            failures.append("cache_usage_not_exact")

        expected_reuse: bool | None = None
        if plan:
            cache_key = _cache_key(plan)
            expected_reuse = cache_key in seen_keys
            declared_breaks: set[CacheBreakReason] = set()
            try:
                declared_breaks = {
                    CacheBreakReason(reason)
                    for reason in plan.get("break_reasons", ())
                }
            except (TypeError, ValueError):
                failures.append("cache_break_reason_invalid")
            observed_breaks, transition_failures = _transition_breaks(
                previous_plan, plan
            )
            failures.extend(transition_failures)
            explained.update(observed_breaks)
            explained.update(declared_breaks & _EXTERNAL_LIFECYCLE_BREAKS)
            for reason in declared_breaks - _EXTERNAL_LIFECYCLE_BREAKS:
                if reason not in observed_breaks:
                    failures.append(f"unexplained_declared_break:{reason.value}")
                else:
                    explained.add(reason)
            if cache_key not in seen_keys:
                break_events += 1
                if previous_plan is None or explained:
                    explained_breaks += 1
                else:
                    unexplained_breaks += 1
                    failures.append("cache_key_changed_without_reason")
            seen_keys.add(cache_key)
            previous_plan = plan

        cache_read_tokens = (
            usage.cache_read_tokens if exact_usage else None
        )
        if require_behavioral_cache and exact_usage and expected_reuse is not None:
            if expected_reuse and not (cache_read_tokens and cache_read_tokens > 0):
                failures.append("expected_cache_reuse_not_observed")
            if not expected_reuse and cache_read_tokens != 0:
                failures.append("unexpected_cache_reuse_observed")

        receipts.append(
            CacheReplayCallReceipt(
                ordinal=ordinal,
                request_id_hash=request_id_hash,
                provider_request_hash=provider_hash,
                cache_plan_fingerprint=(
                    str(plan_fingerprint) if plan_fingerprint is not None else None
                ),
                cache_epoch=(
                    plan.get("cache_epoch")
                    if isinstance(plan.get("cache_epoch"), int)
                    and not isinstance(plan.get("cache_epoch"), bool)
                    else None
                ),
                expected_cache_reuse=expected_reuse,
                observed_cache_read_tokens=cache_read_tokens,
                trace_match=trace_match,
                exact_usage=exact_usage,
                explained_break_reasons=tuple(
                    sorted(reason.value for reason in explained)
                ),
                failure_codes=tuple(dict.fromkeys(failures)),
            )
        )

    call_count = len(receipts)
    trace_matches = sum(receipt.trace_match for receipt in receipts)
    exact_usages = sum(receipt.exact_usage for receipt in receipts)
    failures = tuple(
        dict.fromkeys(
            failure
            for receipt in receipts
            for failure in receipt.failure_codes
        )
    )
    if not calls:
        failures = ("cache_replay_calls_missing",)
    return CacheReplayReport(
        calls=tuple(receipts),
        status="passed" if not failures and unexplained_breaks == 0 else "failed",
        request_trace_match_rate=(trace_matches / call_count if call_count else 0.0),
        exact_usage_coverage=(exact_usages / call_count if call_count else 0.0),
        break_event_count=break_events,
        explained_break_count=explained_breaks,
        unexplained_break_count=unexplained_breaks,
        failure_codes=failures,
    )


__all__ = [
    "CacheReplayCallReceipt",
    "CacheReplayReport",
    "evaluate_cache_replay",
]
