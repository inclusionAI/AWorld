"""Session-sticky Context rollout cohorts and default-on readiness gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import math
import re
from typing import Iterable

from .frozen_json import FrozenMap, canonical_json_hash, freeze_json
from .rollout import ContextCompilerMode


class ReadinessStatus(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    ROLLBACK_REQUIRED = "rollback_required"


class CanaryHealthStatus(str, Enum):
    CONTINUE = "continue"
    HOLD = "hold"
    ROLLBACK_REQUIRED = "rollback_required"


@dataclass(frozen=True, slots=True)
class RolloutCohortPolicy:
    policy_version: str
    enforce_basis_points: int
    shadow_basis_points: int
    salt: str

    def __post_init__(self) -> None:
        for name in ("enforce_basis_points", "shadow_basis_points"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10000:
                raise ValueError(f"{name} must be within 0..10000")
        if self.enforce_basis_points + self.shadow_basis_points > 10000:
            raise ValueError("rollout cohort allocation exceeds 100%")
        if not self.policy_version or not self.salt:
            raise ValueError("policy_version and salt must be non-empty")


@dataclass(frozen=True, slots=True)
class RolloutCapability:
    provider: str
    entry_point: str
    provider_lowering: bool
    request_trace_match: bool
    lifecycle: bool
    trajectory_complete: bool

    @property
    def enforce_ready(self) -> bool:
        return all(
            (
                self.provider_lowering,
                self.request_trace_match,
                self.lifecycle,
                self.trajectory_complete,
            )
        )


@dataclass(frozen=True, slots=True)
class RolloutAssignment:
    session_hash: str
    bucket: int
    requested_mode: ContextCompilerMode
    effective_mode: ContextCompilerMode
    reason_code: str


def assign_rollout_mode(
    *,
    session_id: str,
    policy: RolloutCohortPolicy,
    capability: RolloutCapability,
) -> RolloutAssignment:
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be non-empty")
    digest = hashlib.sha256(
        f"{policy.salt}\0{session_id}".encode("utf-8")
    ).hexdigest()
    bucket = int(digest[:8], 16) % 10000
    if bucket < policy.enforce_basis_points:
        requested = ContextCompilerMode.ENFORCE
    elif bucket < policy.enforce_basis_points + policy.shadow_basis_points:
        requested = ContextCompilerMode.SHADOW
    else:
        requested = ContextCompilerMode.OBSERVE
    if requested is ContextCompilerMode.ENFORCE and not capability.enforce_ready:
        effective = ContextCompilerMode.SHADOW
        reason = "capability_gate_shadow_fallback"
    else:
        effective = requested
        reason = "cohort_assignment"
    return RolloutAssignment(
        session_hash=f"sha256:{digest}",
        bucket=bucket,
        requested_mode=requested,
        effective_mode=effective,
        reason_code=reason,
    )


@dataclass(frozen=True, slots=True)
class DefaultOnReadinessReport:
    status: ReadinessStatus
    gate_failures: tuple[str, ...]
    workload_kinds: tuple[str, ...]
    complete_pairs: int
    rollback_config_hash: str | None


@dataclass(frozen=True, slots=True)
class RollbackBundle:
    previous_mode: ContextCompilerMode
    previous_config: FrozenMap
    provider_capability_hash: str
    bundle_hash: str

    @classmethod
    def build(
        cls,
        *,
        previous_mode: ContextCompilerMode,
        previous_config: dict,
        provider_capability_hash: str,
    ) -> "RollbackBundle":
        mode = ContextCompilerMode(previous_mode)
        config = freeze_json(previous_config)
        if not isinstance(config, FrozenMap):
            raise TypeError("previous_config must be a JSON object")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", provider_capability_hash):
            raise ValueError("provider_capability_hash must be canonical")
        bundle_hash = canonical_json_hash(
            {
                "previous_mode": mode.value,
                "previous_config": config,
                "provider_capability_hash": provider_capability_hash,
            }
        )
        return cls(
            previous_mode=mode,
            previous_config=config,
            provider_capability_hash=provider_capability_hash,
            bundle_hash=bundle_hash,
        )


@dataclass(frozen=True, slots=True)
class CanaryHealthPolicy:
    policy_version: str
    minimum_shadow_calls: int
    minimum_enforce_sessions: int
    max_provider_error_rate_delta: float

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise ValueError("policy_version must be non-empty")
        for name in ("minimum_shadow_calls", "minimum_enforce_sessions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if (
            isinstance(self.max_provider_error_rate_delta, bool)
            or not isinstance(self.max_provider_error_rate_delta, (int, float))
            or not math.isfinite(float(self.max_provider_error_rate_delta))
            or not 0.0 <= float(self.max_provider_error_rate_delta) <= 1.0
        ):
            raise ValueError("max_provider_error_rate_delta must be within 0..1")


@dataclass(frozen=True, slots=True)
class CanaryHealthEvidence:
    shadow_call_count: int
    shadow_request_trace_match_count: int
    shadow_provider_attribution_complete_count: int
    enforce_session_count: int
    enforce_provider_attempt_count: int
    enforce_provider_error_count: int
    baseline_provider_error_rate: float
    security_violation_count: int
    trajectory_incomplete_count: int
    quality_regression: bool

    def __post_init__(self) -> None:
        count_names = (
            "shadow_call_count",
            "shadow_request_trace_match_count",
            "shadow_provider_attribution_complete_count",
            "enforce_session_count",
            "enforce_provider_attempt_count",
            "enforce_provider_error_count",
            "security_violation_count",
            "trajectory_incomplete_count",
        )
        for name in count_names:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.shadow_request_trace_match_count > self.shadow_call_count:
            raise ValueError("shadow trace matches cannot exceed calls")
        if self.shadow_provider_attribution_complete_count > self.shadow_call_count:
            raise ValueError("shadow attribution count cannot exceed calls")
        if self.enforce_provider_error_count > self.enforce_provider_attempt_count:
            raise ValueError("provider errors cannot exceed attempts")
        if (
            isinstance(self.baseline_provider_error_rate, bool)
            or not isinstance(self.baseline_provider_error_rate, (int, float))
            or not math.isfinite(float(self.baseline_provider_error_rate))
            or not 0.0 <= float(self.baseline_provider_error_rate) <= 1.0
        ):
            raise ValueError("baseline_provider_error_rate must be within 0..1")
        if not isinstance(self.quality_regression, bool):
            raise TypeError("quality_regression must be boolean")


@dataclass(frozen=True, slots=True)
class CanaryHealthDecision:
    status: CanaryHealthStatus
    reason_codes: tuple[str, ...]
    rollback_bundle_hash: str | None
    evidence_fingerprint: str


def assess_canary_health(
    *,
    policy: CanaryHealthPolicy,
    evidence: CanaryHealthEvidence,
    rollback_bundle: RollbackBundle | None,
) -> CanaryHealthDecision:
    if not isinstance(policy, CanaryHealthPolicy):
        raise TypeError("policy must be CanaryHealthPolicy")
    if not isinstance(evidence, CanaryHealthEvidence):
        raise TypeError("evidence must be CanaryHealthEvidence")
    if rollback_bundle is not None and not isinstance(rollback_bundle, RollbackBundle):
        raise TypeError("rollback_bundle must be RollbackBundle or None")

    rollback_reasons: set[str] = set()
    hold_reasons: set[str] = set()
    if evidence.shadow_call_count < policy.minimum_shadow_calls:
        hold_reasons.add("shadow_sample_incomplete")
    if evidence.shadow_request_trace_match_count != evidence.shadow_call_count:
        rollback_reasons.add("shadow_request_trace_mismatch")
    if evidence.shadow_provider_attribution_complete_count != evidence.shadow_call_count:
        rollback_reasons.add("shadow_provider_attribution_incomplete")
    if evidence.enforce_session_count < policy.minimum_enforce_sessions:
        hold_reasons.add("enforce_sample_incomplete")
    if evidence.enforce_provider_attempt_count < policy.minimum_enforce_sessions:
        hold_reasons.add("enforce_provider_attempt_sample_incomplete")
    if evidence.security_violation_count:
        rollback_reasons.add("security_violation")
    if evidence.trajectory_incomplete_count:
        rollback_reasons.add("trajectory_fidelity_incomplete")
    if evidence.quality_regression:
        rollback_reasons.add("quality_regression")
    candidate_error_rate = (
        evidence.enforce_provider_error_count / evidence.enforce_provider_attempt_count
        if evidence.enforce_provider_attempt_count
        else 0.0
    )
    if (
        evidence.enforce_provider_attempt_count
        and candidate_error_rate - float(evidence.baseline_provider_error_rate)
        > float(policy.max_provider_error_rate_delta)
    ):
        rollback_reasons.add("provider_error_rate_regression")
    if rollback_reasons and rollback_bundle is None:
        rollback_reasons.add("rollback_bundle_missing")

    if rollback_reasons:
        status = CanaryHealthStatus.ROLLBACK_REQUIRED
        reasons = tuple(sorted(rollback_reasons))
    elif hold_reasons:
        status = CanaryHealthStatus.HOLD
        reasons = tuple(sorted(hold_reasons))
    else:
        status = CanaryHealthStatus.CONTINUE
        reasons = ()
    return CanaryHealthDecision(
        status=status,
        reason_codes=reasons,
        rollback_bundle_hash=(
            rollback_bundle.bundle_hash if rollback_bundle is not None else None
        ),
        evidence_fingerprint=canonical_json_hash({
            "policy": {
                "policy_version": policy.policy_version,
                "minimum_shadow_calls": policy.minimum_shadow_calls,
                "minimum_enforce_sessions": policy.minimum_enforce_sessions,
                "max_provider_error_rate_delta": policy.max_provider_error_rate_delta,
            },
            "evidence": {
                name: getattr(evidence, name)
                for name in evidence.__dataclass_fields__
            },
        }),
    )


def assess_default_on_readiness(
    *,
    capabilities: Iterable[RolloutCapability],
    workload_kinds: Iterable[str],
    complete_pairs: int,
    quality_regression: bool,
    request_trace_match_rate: float,
    trajectory_complete_rate: float,
    rollback_config_hash: str | None,
    hard_gate_failures: Iterable[str] = (),
    required_capabilities: Iterable[tuple[str, str]] = (),
    minimum_complete_pairs: int = 10,
) -> DefaultOnReadinessReport:
    if isinstance(complete_pairs, bool) or not isinstance(complete_pairs, int) or complete_pairs < 0:
        raise ValueError("complete_pairs must be a non-negative integer")
    if (
        isinstance(minimum_complete_pairs, bool)
        or not isinstance(minimum_complete_pairs, int)
        or minimum_complete_pairs <= 0
    ):
        raise ValueError("minimum_complete_pairs must be positive")
    for name, value in (
        ("request_trace_match_rate", request_trace_match_rate),
        ("trajectory_complete_rate", trajectory_complete_rate),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{name} must be within 0..1")
    if rollback_config_hash is not None and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", rollback_config_hash
    ):
        raise ValueError("rollback_config_hash must be canonical or None")
    failures: set[str] = set(hard_gate_failures)
    if any(
        not isinstance(code, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
        for code in failures
    ):
        raise ValueError("hard gate failures must be stable reason codes")
    capability_values = tuple(capabilities)
    capability_keys = [(value.provider, value.entry_point) for value in capability_values]
    if len(set(capability_keys)) != len(capability_keys):
        raise ValueError("capabilities must be unique by provider and entry point")
    required_capability_keys = tuple(required_capabilities)
    if any(
        not isinstance(value, tuple)
        or len(value) != 2
        or not all(isinstance(item, str) and item for item in value)
        for value in required_capability_keys
    ):
        raise ValueError("required_capabilities must contain provider/entry-point pairs")
    if len(set(required_capability_keys)) != len(required_capability_keys):
        raise ValueError("required_capabilities must be unique")
    kinds = tuple(sorted(set(workload_kinds)))
    capability_by_key = dict(zip(capability_keys, capability_values))
    if (
        not capability_values
        or any(not value.enforce_ready for value in capability_values)
        or any(
            key not in capability_by_key
            or not capability_by_key[key].enforce_ready
            for key in required_capability_keys
        )
    ):
        failures.add("capability_matrix_incomplete")
    if len(kinds) < 2:
        failures.add("cross_workload_evidence_missing")
    if complete_pairs <= 0:
        failures.add("paired_evidence_missing")
    elif complete_pairs < minimum_complete_pairs:
        failures.add("insufficient_paired_evidence")
    if quality_regression:
        failures.add("quality_regression")
    if request_trace_match_rate < 1.0:
        failures.add("request_trace_mismatch")
    if trajectory_complete_rate < 1.0:
        failures.add("trajectory_fidelity_incomplete")
    if rollback_config_hash is None:
        failures.add("rollback_bundle_missing")
    status = (
        ReadinessStatus.ROLLBACK_REQUIRED
        if quality_regression
        else ReadinessStatus.NOT_READY if failures else ReadinessStatus.READY
    )
    return DefaultOnReadinessReport(
        status=status,
        gate_failures=tuple(sorted(failures)),
        workload_kinds=kinds,
        complete_pairs=complete_pairs,
        rollback_config_hash=rollback_config_hash,
    )


__all__ = [
    "CanaryHealthDecision",
    "CanaryHealthEvidence",
    "CanaryHealthPolicy",
    "CanaryHealthStatus",
    "DefaultOnReadinessReport",
    "ReadinessStatus",
    "RolloutAssignment",
    "RollbackBundle",
    "RolloutCapability",
    "RolloutCohortPolicy",
    "assess_default_on_readiness",
    "assess_canary_health",
    "assign_rollout_mode",
]
