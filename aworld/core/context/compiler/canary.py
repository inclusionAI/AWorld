"""Session-sticky Context rollout cohorts and default-on readiness gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Iterable

from .frozen_json import FrozenMap, canonical_json_hash, freeze_json
from .rollout import ContextCompilerMode


class ReadinessStatus(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
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
) -> DefaultOnReadinessReport:
    if isinstance(complete_pairs, bool) or not isinstance(complete_pairs, int) or complete_pairs < 0:
        raise ValueError("complete_pairs must be a non-negative integer")
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
    kinds = tuple(sorted(set(workload_kinds)))
    if not capability_values or any(not value.enforce_ready for value in capability_values):
        failures.add("capability_matrix_incomplete")
    if len(kinds) < 2:
        failures.add("cross_workload_evidence_missing")
    if complete_pairs <= 0:
        failures.add("paired_evidence_missing")
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
    "DefaultOnReadinessReport",
    "ReadinessStatus",
    "RolloutAssignment",
    "RollbackBundle",
    "RolloutCapability",
    "RolloutCohortPolicy",
    "assess_default_on_readiness",
    "assign_rollout_mode",
]
