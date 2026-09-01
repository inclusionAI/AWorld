"""Session-sticky Context rollout cohorts and default-on readiness gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import math
import re
from typing import Iterable

from .frozen_json import FrozenMap, canonical_json_hash, freeze_json
from .lifecycle import ContextLifecycleState
from .parity import VerifiedContextEntrypointParityReceipt
from .rollout import ContextCompilerMode
from aworld.core.trajectory import (
    TrajectoryBuildResult,
    TrajectoryBuildStatus,
    TrajectoryFidelity,
)


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
    call_shape: str = "unknown"
    evidence_fingerprint: str | None = field(default=None, init=False)

    @classmethod
    def from_verified_evidence(
        cls,
        *,
        entrypoint_receipt: VerifiedContextEntrypointParityReceipt,
        lifecycle_state: ContextLifecycleState,
        trajectory_result: TrajectoryBuildResult,
    ) -> "RolloutCapability":
        if not isinstance(
            entrypoint_receipt, VerifiedContextEntrypointParityReceipt
        ):
            raise TypeError("entrypoint_receipt must be provider-verified")
        if not isinstance(lifecycle_state, ContextLifecycleState):
            raise TypeError("lifecycle_state must be ContextLifecycleState")
        if not isinstance(trajectory_result, TrajectoryBuildResult):
            raise TypeError("trajectory_result must be TrajectoryBuildResult")
        if (
            trajectory_result.task_epoch is not None
            and trajectory_result.task_epoch != lifecycle_state.task_epoch
        ):
            raise ValueError("trajectory and lifecycle task epochs do not match")
        trajectory_complete = (
            trajectory_result.status is TrajectoryBuildStatus.COMPLETE
            and trajectory_result.fidelity is TrajectoryFidelity.COMPLETE
            and trajectory_result.llm_call_count > 0
        )
        receipt = entrypoint_receipt.receipt
        value = cls(
            provider=entrypoint_receipt.provider_name,
            entry_point=receipt.entry_point.value,
            call_shape=receipt.call_shape.value,
            provider_lowering=True,
            request_trace_match=True,
            lifecycle=True,
            trajectory_complete=trajectory_complete,
        )
        object.__setattr__(value, "evidence_fingerprint", canonical_json_hash({
            "entrypoint_evidence": entrypoint_receipt.evidence_fingerprint,
            "lifecycle": {
                "session_epoch": lifecycle_state.session_epoch,
                "task_epoch": lifecycle_state.task_epoch,
                "turn_epoch": lifecycle_state.turn_epoch,
                "branch_id": lifecycle_state.branch_id,
                "checkpoint_revision": lifecycle_state.checkpoint_revision,
            },
            "trajectory": {
                "builder_version": trajectory_result.builder_version,
                "trajectory_checksum": trajectory_result.trajectory_checksum,
                "status": trajectory_result.status.value,
                "fidelity": trajectory_result.fidelity.value,
            },
        }))
        return value

    @classmethod
    def combine_verified(
        cls, capabilities: Iterable["RolloutCapability"]
    ) -> "RolloutCapability":
        values = tuple(capabilities)
        if not values or any(not value.enforce_ready for value in values):
            raise ValueError("all combined capabilities must be verified and ready")
        keys = {
            (value.provider, value.entry_point, value.call_shape)
            for value in values
        }
        if len(keys) != 1:
            raise ValueError("combined capabilities must share one matrix key")
        first = values[0]
        value = cls(
            provider=first.provider,
            entry_point=first.entry_point,
            call_shape=first.call_shape,
            provider_lowering=True,
            request_trace_match=True,
            lifecycle=True,
            trajectory_complete=True,
        )
        object.__setattr__(value, "evidence_fingerprint", canonical_json_hash({
            "capability_key": next(iter(keys)),
            "run_evidence": sorted(
                item.evidence_fingerprint for item in values
            ),
        }))
        return value

    @property
    def enforce_ready(self) -> bool:
        return self.evidence_fingerprint is not None and all(
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
    canary_health_decision_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class RollbackBundle:
    previous_mode: ContextCompilerMode
    previous_config: FrozenMap
    provider_capability_hash: str
    bundle_hash: str

    def __post_init__(self) -> None:
        mode = ContextCompilerMode(self.previous_mode)
        config = freeze_json(self.previous_config)
        if not isinstance(config, FrozenMap):
            raise TypeError("previous_config must be a JSON object")
        if mode is ContextCompilerMode.ENFORCE:
            raise ValueError("rollback previous_mode cannot be enforce")
        configured_mode = config.get("mode")
        if configured_mode is not None and configured_mode != mode.value:
            raise ValueError("previous_config mode must match previous_mode")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.provider_capability_hash):
            raise ValueError("provider_capability_hash must be canonical")
        expected_hash = canonical_json_hash({
            "previous_mode": mode.value,
            "previous_config": config,
            "provider_capability_hash": self.provider_capability_hash,
        })
        if self.bundle_hash != expected_hash:
            raise ValueError("rollback bundle hash mismatch")
        object.__setattr__(self, "previous_mode", mode)
        object.__setattr__(self, "previous_config", config)

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
        if mode is ContextCompilerMode.ENFORCE:
            raise ValueError("rollback previous_mode cannot be enforce")
        configured_mode = config.get("mode")
        if configured_mode is not None and configured_mode != mode.value:
            raise ValueError("previous_config mode must match previous_mode")
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

    @classmethod
    def from_dict(cls, payload: dict) -> "RollbackBundle":
        if not isinstance(payload, dict) or set(payload) != {
            "previous_mode",
            "previous_config",
            "provider_capability_hash",
            "bundle_hash",
        }:
            raise ValueError("unsupported rollback bundle")
        rebuilt = cls.build(
            previous_mode=ContextCompilerMode(payload["previous_mode"]),
            previous_config=payload["previous_config"],
            provider_capability_hash=payload["provider_capability_hash"],
        )
        if rebuilt.bundle_hash != payload["bundle_hash"]:
            raise ValueError("rollback bundle hash mismatch")
        return rebuilt


@dataclass(frozen=True, slots=True)
class CanaryHealthPolicy:
    policy_version: str
    minimum_shadow_calls: int
    minimum_enforce_sessions: int
    max_provider_error_rate_delta: float
    minimum_baseline_provider_attempts: int = 1
    minimum_enforce_provider_attempts: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise ValueError("policy_version must be non-empty")
        for name in (
            "minimum_shadow_calls",
            "minimum_enforce_sessions",
            "minimum_baseline_provider_attempts",
            "minimum_enforce_provider_attempts",
        ):
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
    baseline_provider_attempt_count: int = 0
    baseline_provider_error_count: int = 0
    enforce_sessions_with_provider_attempt_count: int = 0

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
            "baseline_provider_attempt_count",
            "baseline_provider_error_count",
            "enforce_sessions_with_provider_attempt_count",
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
        if self.baseline_provider_error_count > self.baseline_provider_attempt_count:
            raise ValueError("baseline provider errors cannot exceed attempts")
        if (
            self.enforce_sessions_with_provider_attempt_count
            > self.enforce_session_count
        ):
            raise ValueError("sessions with provider attempts cannot exceed sessions")
        if (
            self.enforce_sessions_with_provider_attempt_count
            > self.enforce_provider_attempt_count
        ):
            raise ValueError(
                "sessions with provider attempts cannot exceed provider attempts"
            )
        if (
            isinstance(self.baseline_provider_error_rate, bool)
            or not isinstance(self.baseline_provider_error_rate, (int, float))
            or not math.isfinite(float(self.baseline_provider_error_rate))
            or not 0.0 <= float(self.baseline_provider_error_rate) <= 1.0
        ):
            raise ValueError("baseline_provider_error_rate must be within 0..1")
        if not isinstance(self.quality_regression, bool):
            raise TypeError("quality_regression must be boolean")
        computed_baseline_rate = (
            self.baseline_provider_error_count / self.baseline_provider_attempt_count
            if self.baseline_provider_attempt_count
            else 0.0
        )
        if float(self.baseline_provider_error_rate) != computed_baseline_rate:
            raise ValueError(
                "baseline_provider_error_rate must match the exact baseline counts"
            )


@dataclass(frozen=True, slots=True)
class CanaryHealthDecision:
    status: CanaryHealthStatus
    reason_codes: tuple[str, ...]
    rollback_bundle_hash: str | None
    policy_fingerprint: str
    evidence_fingerprint: str
    decision_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", CanaryHealthStatus(self.status))
        if (
            not isinstance(self.reason_codes, tuple)
            or tuple(sorted(set(self.reason_codes))) != self.reason_codes
            or any(
                not isinstance(code, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
                for code in self.reason_codes
            )
        ):
            raise ValueError("reason_codes must be unique sorted stable codes")
        for name in (
            "policy_fingerprint",
            "evidence_fingerprint",
            "decision_fingerprint",
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(getattr(self, name))):
                raise ValueError(f"{name} must be canonical")
        if self.rollback_bundle_hash is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.rollback_bundle_hash
        ):
            raise ValueError("rollback_bundle_hash must be canonical or None")
        expected_fingerprint = canonical_json_hash({
            "schema_version": "aworld.context.canary-health-decision.v1",
            "status": self.status.value,
            "reason_codes": self.reason_codes,
            "rollback_bundle_hash": self.rollback_bundle_hash,
            "policy_fingerprint": self.policy_fingerprint,
            "evidence_fingerprint": self.evidence_fingerprint,
        })
        if self.decision_fingerprint != expected_fingerprint:
            raise ValueError("canary health decision fingerprint mismatch")


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
    if (
        evidence.enforce_provider_attempt_count
        < policy.minimum_enforce_provider_attempts
    ):
        hold_reasons.add("enforce_provider_attempt_sample_incomplete")
    if (
        evidence.baseline_provider_attempt_count
        < policy.minimum_baseline_provider_attempts
    ):
        hold_reasons.add("baseline_provider_attempt_sample_incomplete")
    if (
        evidence.enforce_sessions_with_provider_attempt_count
        != evidence.enforce_session_count
    ):
        hold_reasons.add("enforce_session_provider_coverage_incomplete")
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
        and candidate_error_rate
        - (
            evidence.baseline_provider_error_count
            / evidence.baseline_provider_attempt_count
            if evidence.baseline_provider_attempt_count
            else 0.0
        )
        > float(policy.max_provider_error_rate_delta)
    ):
        rollback_reasons.add("provider_error_rate_regression")
    if rollback_bundle is None:
        if rollback_reasons:
            rollback_reasons.add("rollback_bundle_missing")
        else:
            hold_reasons.add("rollback_bundle_missing")

    if rollback_reasons:
        status = CanaryHealthStatus.ROLLBACK_REQUIRED
        reasons = tuple(sorted(rollback_reasons))
    elif hold_reasons:
        status = CanaryHealthStatus.HOLD
        reasons = tuple(sorted(hold_reasons))
    else:
        status = CanaryHealthStatus.CONTINUE
        reasons = ()
    policy_payload = {
        name: getattr(policy, name) for name in policy.__dataclass_fields__
    }
    evidence_payload = {
        name: getattr(evidence, name) for name in evidence.__dataclass_fields__
    }
    policy_fingerprint = canonical_json_hash(policy_payload)
    evidence_fingerprint = canonical_json_hash(evidence_payload)
    rollback_bundle_hash = (
        rollback_bundle.bundle_hash if rollback_bundle is not None else None
    )
    decision_fingerprint = canonical_json_hash({
        "schema_version": "aworld.context.canary-health-decision.v1",
        "status": status.value,
        "reason_codes": reasons,
        "rollback_bundle_hash": rollback_bundle_hash,
        "policy_fingerprint": policy_fingerprint,
        "evidence_fingerprint": evidence_fingerprint,
    })
    return CanaryHealthDecision(
        status=status,
        reason_codes=reasons,
        rollback_bundle_hash=rollback_bundle_hash,
        policy_fingerprint=policy_fingerprint,
        evidence_fingerprint=evidence_fingerprint,
        decision_fingerprint=decision_fingerprint,
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
    required_capabilities: Iterable[
        tuple[str, str] | tuple[str, str, str]
    ] = (),
    canary_health_decision: CanaryHealthDecision | None = None,
    required_canary_policy_fingerprint: str | None = None,
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
    if canary_health_decision is not None and not isinstance(
        canary_health_decision, CanaryHealthDecision
    ):
        raise TypeError("canary_health_decision must be CanaryHealthDecision or None")
    if required_canary_policy_fingerprint is not None and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", required_canary_policy_fingerprint
    ):
        raise ValueError("required_canary_policy_fingerprint must be canonical or None")
    failures: set[str] = set(hard_gate_failures)
    if any(
        not isinstance(code, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
        for code in failures
    ):
        raise ValueError("hard gate failures must be stable reason codes")
    capability_values = tuple(capabilities)
    if not all(isinstance(value, RolloutCapability) for value in capability_values):
        raise TypeError("capabilities must contain RolloutCapability values")
    capability_keys = [
        (value.provider, value.entry_point, value.call_shape)
        for value in capability_values
    ]
    if len(set(capability_keys)) != len(capability_keys):
        raise ValueError(
            "capabilities must be unique by provider, entry point and call shape"
        )
    required_capability_values = tuple(required_capabilities)
    if any(
        not isinstance(value, tuple)
        or len(value) not in {2, 3}
        or not all(isinstance(item, str) and item for item in value)
        for value in required_capability_values
    ):
        raise ValueError(
            "required_capabilities must contain provider/entry-point pairs or "
            "provider/entry-point/call-shape triples"
        )
    # Legacy pairs remain parseable for API compatibility, but cannot silently
    # authorize a concrete sync/async path.
    required_capability_keys = tuple(
        value if len(value) == 3 else (value[0], value[1], "unknown")
        for value in required_capability_values
    )
    if len(set(required_capability_keys)) != len(required_capability_keys):
        raise ValueError("required_capabilities must be unique")
    kinds = tuple(sorted(set(workload_kinds)))
    capability_by_key = dict(zip(capability_keys, capability_values))
    if (
        not capability_values
        or not required_capability_keys
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
    canary_rollback_required = False
    if canary_health_decision is None:
        failures.add("canary_health_missing")
    else:
        if required_canary_policy_fingerprint is None:
            failures.add("canary_policy_binding_missing")
        elif (
            canary_health_decision.policy_fingerprint
            != required_canary_policy_fingerprint
        ):
            failures.add("canary_policy_mismatch")
        if canary_health_decision.rollback_bundle_hash != rollback_config_hash:
            failures.add("canary_rollback_bundle_mismatch")
        if canary_health_decision.status is CanaryHealthStatus.HOLD:
            failures.add("canary_health_hold")
        elif canary_health_decision.status is CanaryHealthStatus.ROLLBACK_REQUIRED:
            failures.add("canary_rollback_required")
            canary_rollback_required = True
    status = (
        ReadinessStatus.ROLLBACK_REQUIRED
        if quality_regression or canary_rollback_required
        else ReadinessStatus.NOT_READY if failures else ReadinessStatus.READY
    )
    return DefaultOnReadinessReport(
        status=status,
        gate_failures=tuple(sorted(failures)),
        workload_kinds=kinds,
        complete_pairs=complete_pairs,
        rollback_config_hash=rollback_config_hash,
        canary_health_decision_fingerprint=(
            canary_health_decision.decision_fingerprint
            if canary_health_decision is not None
            else None
        ),
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
