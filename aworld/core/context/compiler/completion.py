"""Completion contracts that separate claims, self-checks, and verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Iterable

from .frozen_json import FrozenMap, freeze_json


class CompletionMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


class CompletionStatus(str, Enum):
    SATISFIED = "satisfied"
    REPAIR_REQUIRED = "repair_required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ArtifactRequirement:
    requirement_id: str
    path: str
    media_type: str | None = None
    expected_hash: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.requirement_id):
            raise ValueError("requirement_id must be stable")
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("path must be a non-empty string")
        if self.expected_hash is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.expected_hash
        ):
            raise ValueError("expected_hash must be a canonical sha256 hash")


@dataclass(frozen=True, slots=True)
class ValidationCommand:
    command_id: str
    argv: tuple[str, ...]
    cwd: str | None = None
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.command_id):
            raise ValueError("command_id must be stable")
        object.__setattr__(self, "argv", tuple(self.argv))
        if not self.argv or any(not isinstance(arg, str) for arg in self.argv):
            raise ValueError("argv must contain at least one string")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, int
        ) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class CompletionContract:
    required_artifacts: tuple[ArtifactRequirement, ...]
    immutable_inputs: tuple[str, ...]
    validation_commands: tuple[ValidationCommand, ...]
    max_evidence_age_seconds: int | None
    required_final_evidence: tuple[str, ...]
    max_repairs: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_artifacts", tuple(self.required_artifacts))
        object.__setattr__(self, "immutable_inputs", tuple(self.immutable_inputs))
        object.__setattr__(self, "validation_commands", tuple(self.validation_commands))
        object.__setattr__(
            self, "required_final_evidence", tuple(self.required_final_evidence)
        )
        if self.max_evidence_age_seconds is not None and (
            isinstance(self.max_evidence_age_seconds, bool)
            or not isinstance(self.max_evidence_age_seconds, int)
            or self.max_evidence_age_seconds < 0
        ):
            raise ValueError("max_evidence_age_seconds must be non-negative or None")
        if isinstance(self.max_repairs, bool) or not isinstance(
            self.max_repairs, int
        ) or self.max_repairs < 0:
            raise ValueError("max_repairs must be non-negative")


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    requirement_id: str
    exists: bool
    content_hash: str | None
    observed_at: datetime
    media_type: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.content_hash is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.content_hash
        ):
            raise ValueError("content_hash must be canonical or None")


@dataclass(frozen=True, slots=True)
class SelfCheckEvidence:
    command_id: str
    exit_code: int
    output_hash: str | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise TypeError("exit_code must be an integer")


@dataclass(frozen=True, slots=True)
class ImmutableInputEvidence:
    input_id: str
    expected_hash: str
    observed_hash: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.input_id, str) or not self.input_id.strip():
            raise ValueError("input_id must be non-empty")
        for name in ("expected_hash", "observed_hash"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(f"{name} must be a canonical sha256 hash")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ExternalVerifierEvidence:
    verifier_id: str
    passed: bool
    result: FrozenMap
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        value = freeze_json(self.result)
        if not isinstance(value, FrozenMap):
            raise TypeError("result must be a JSON object")
        object.__setattr__(self, "result", value)


@dataclass(frozen=True, slots=True)
class CompletionAssessment:
    mode: CompletionMode
    status: CompletionStatus
    reason_codes: tuple[str, ...]
    repair_attempt: int
    agent_claimed_finished: bool
    external_verifier: ExternalVerifierEvidence | None


def assess_completion(
    contract: CompletionContract,
    *,
    mode: CompletionMode,
    artifact_evidence: Iterable[ArtifactEvidence],
    immutable_input_evidence: Iterable[ImmutableInputEvidence] = (),
    self_checks: Iterable[SelfCheckEvidence],
    final_evidence_codes: Iterable[str],
    agent_claimed_finished: bool,
    repair_attempt: int = 0,
    external_verifier: ExternalVerifierEvidence | None = None,
    now: datetime | None = None,
) -> CompletionAssessment:
    """Assess supplied evidence; this function never executes validation commands."""
    mode = CompletionMode(mode)
    now = now or datetime.now(timezone.utc)
    artifacts = {item.requirement_id: item for item in artifact_evidence}
    immutable_inputs = {
        item.input_id: item for item in immutable_input_evidence
    }
    checks = {item.command_id: item for item in self_checks}
    reasons: set[str] = set()
    for requirement in contract.required_artifacts:
        evidence = artifacts.get(requirement.requirement_id)
        if evidence is None or not evidence.exists:
            if requirement.required:
                reasons.add("required_artifact_missing")
            continue
        if requirement.expected_hash and evidence.content_hash != requirement.expected_hash:
            reasons.add("artifact_hash_mismatch")
        if requirement.media_type and evidence.media_type != requirement.media_type:
            reasons.add("artifact_media_type_mismatch")
    for input_id in contract.immutable_inputs:
        evidence = immutable_inputs.get(input_id)
        if evidence is None:
            reasons.add("immutable_input_evidence_missing")
        elif evidence.expected_hash != evidence.observed_hash:
            reasons.add("immutable_input_changed")
    for command in contract.validation_commands:
        evidence = checks.get(command.command_id)
        if evidence is None:
            reasons.add("self_check_missing")
        elif evidence.exit_code != 0:
            reasons.add("self_check_failed")
    if not set(contract.required_final_evidence).issubset(set(final_evidence_codes)):
        reasons.add("final_evidence_missing")
    if contract.max_evidence_age_seconds is not None:
        evidence_times = (
            [item.observed_at for item in artifacts.values()]
            + [item.observed_at for item in immutable_inputs.values()]
            + [item.observed_at for item in checks.values()]
        )
        if any(
            (now - observed_at).total_seconds() > contract.max_evidence_age_seconds
            for observed_at in evidence_times
        ):
            reasons.add("evidence_stale")
        if any(observed_at > now for observed_at in evidence_times):
            reasons.add("evidence_from_future")
    if external_verifier is not None and not external_verifier.passed:
        reasons.add("external_verifier_failed")
    if mode is CompletionMode.OFF or not reasons:
        status = CompletionStatus.SATISFIED
    elif mode is CompletionMode.OBSERVE:
        status = CompletionStatus.SATISFIED
    elif repair_attempt < contract.max_repairs:
        status = CompletionStatus.REPAIR_REQUIRED
    else:
        status = CompletionStatus.FAILED
    return CompletionAssessment(
        mode=mode,
        status=status,
        reason_codes=tuple(sorted(reasons)),
        repair_attempt=repair_attempt,
        agent_claimed_finished=agent_claimed_finished,
        external_verifier=external_verifier,
    )


__all__ = [
    "ArtifactEvidence",
    "ArtifactRequirement",
    "CompletionAssessment",
    "CompletionContract",
    "CompletionMode",
    "CompletionStatus",
    "ExternalVerifierEvidence",
    "ImmutableInputEvidence",
    "SelfCheckEvidence",
    "ValidationCommand",
    "assess_completion",
]
