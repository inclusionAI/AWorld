"""Typed, fresh-context acceptance critic contracts.

The critic is intentionally given a bounded reconstruction rather than the
solver transcript.  A successful solver-authored self-check is evidence, but
cannot by itself produce an acceptance receipt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from aworld.core.context.compiler import semantic_fingerprint


class AcceptanceDecision(str, Enum):
    ACCEPT = "accept"
    REPAIR = "repair"
    UNCERTAIN = "uncertain"


PROBE_KINDS = frozenset(
    {
        "artifact_readback",
        "spec_roundtrip",
        "performance_comparison",
        "real_signal_delivery",
        "independent_cross_check",
    }
)
_FORBIDDEN_PROBE_COMMAND = re.compile(
    r"(?:^|[;&|\s])(?:echo|printf|base64|true|false|sleep|:)(?:\s|$)",
    re.IGNORECASE,
)


def probe_arguments_are_executable(arguments: Mapping[str, Any]) -> bool:
    if not isinstance(arguments, Mapping) or not arguments:
        return False
    command = arguments.get("command") or arguments.get("code")
    if isinstance(command, str):
        folded = command.casefold()
        if not command.strip() or _FORBIDDEN_PROBE_COMMAND.search(command):
            return False
        if any(token in folded for token in ("b64decode", "decode64")):
            return False
    return True


def validate_probe_result(
    *,
    probe_kind: str,
    tool_identity: str,
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    artifact_after: Any,
    evidence: Mapping[str, Any],
) -> tuple[bool, str]:
    if probe_kind not in PROBE_KINDS:
        return False, "unsupported_probe_kind"
    if not probe_arguments_are_executable(arguments):
        return False, "non_executable_probe"
    if result.get("return_code") != 0 or result.get("success") is not True:
        return False, "probe_process_failed"
    if result.get("failure_code"):
        return False, "probe_semantic_failure"
    payload = result.get("structured_payload")
    if not isinstance(payload, Mapping):
        return False, "structured_attestation_missing"

    if probe_kind == "artifact_readback":
        content_hash = payload.get("content_hash")
        valid = bool(
            payload.get("readback_matches") is True
            and isinstance(content_hash, str)
            and content_hash.startswith("sha256:")
            and isinstance(artifact_after, str)
            and content_hash == artifact_after
            and any(
                key in arguments
                for key in ("path", "file", "artifact", "command", "code")
            )
        )
    elif probe_kind == "spec_roundtrip":
        valid = bool(
            payload.get("roundtrip_equal") is True
            and isinstance(payload.get("input_hash"), str)
            and payload.get("input_hash") == payload.get("output_hash")
        )
    elif probe_kind == "performance_comparison":
        baseline = payload.get("baseline_duration")
        candidate = payload.get("candidate_duration")
        valid = bool(
            isinstance(baseline, (int, float))
            and not isinstance(baseline, bool)
            and isinstance(candidate, (int, float))
            and not isinstance(candidate, bool)
            and baseline > 0
            and 0 <= candidate <= baseline
        )
    elif probe_kind == "real_signal_delivery":
        command = str(arguments.get("command") or arguments.get("code") or "")
        valid = bool(
            payload.get("signal_delivered") is True
            and payload.get("signal") in {"SIGINT", "SIGTERM", "SIGHUP"}
            and re.search(r"(?:\bkill\b|os\.kill\s*\(|raise_signal\s*\()", command)
        )
    else:
        framework_hashes = {
            value
            for value in (
                evidence.get("artifact_fingerprint"),
                evidence.get("completion_evidence_fingerprint"),
            )
            if isinstance(value, str)
        }
        valid = bool(
            payload.get("matches") is True
            and isinstance(payload.get("primary_hash"), str)
            and payload.get("primary_hash") == payload.get("check_hash")
            and payload.get("primary_hash") in framework_hashes
            and "check" in tool_identity.casefold()
        )
    return valid, "probe_attested" if valid else f"{probe_kind}_not_attested"


def framework_probe_attestation(
    *, challenge: str, probe_kind: str, receipt_fields: Mapping[str, Any]
) -> str:
    return semantic_fingerprint(
        {"challenge": challenge, "probe_kind": probe_kind, **dict(receipt_fields)}
    )


def _bounded_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return text


@dataclass(frozen=True, slots=True)
class AcceptanceCriticDecision:
    decision: AcceptanceDecision
    highest_risk_counterexample: str
    hypothesis_id: str
    reason: str

    @classmethod
    def from_value(cls, value: Any) -> "AcceptanceCriticDecision":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("critic decision must be a JSON object") from exc
        if not isinstance(value, Mapping):
            raise ValueError("critic decision must be an object")
        expected = {
            "decision",
            "highest_risk_counterexample",
            "hypothesis_id",
            "reason",
        }
        if set(value) != expected:
            raise ValueError("critic decision has missing or unknown fields")
        try:
            decision = AcceptanceDecision(value.get("decision"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "critic decision must be accept, repair, or uncertain"
            ) from exc
        return cls(
            decision=decision,
            highest_risk_counterexample=_bounded_text(
                value.get("highest_risk_counterexample"),
                "highest_risk_counterexample",
                1024,
            ),
            hypothesis_id=_bounded_text(
                value.get("hypothesis_id"), "hypothesis_id", 128
            ),
            reason=_bounded_text(value.get("reason"), "reason", 1024),
        )


@dataclass(frozen=True, slots=True)
class AcceptanceProbeReceipt:
    schema_version: str
    hypothesis_hash: str
    counterexample_hash: str
    tool_identity: str
    tool_identity_hash: str
    arguments_hash: str
    candidate_hash: str
    evidence_hash: str
    artifact_before_hash: str
    artifact_after_hash: str
    probe_kind: str
    challenge_hash: str
    attestation_hash: str
    validation_code: str
    result_hash: str
    arguments_projection: Mapping[str, Any]
    result_projection: Mapping[str, Any]
    success: bool
    failure_code: str | None = None

    SCHEMA_VERSION = "aworld.acceptance-probe-receipt/v3"

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("unsupported acceptance probe receipt schema")
        for name in (
            "hypothesis_hash",
            "counterexample_hash",
            "tool_identity_hash",
            "arguments_hash",
            "candidate_hash",
            "evidence_hash",
            "artifact_before_hash",
            "artifact_after_hash",
            "challenge_hash",
            "attestation_hash",
            "result_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise ValueError(f"{name} must be a semantic hash")
        if not isinstance(self.tool_identity, str) or not self.tool_identity:
            raise ValueError("tool_identity must be a non-empty string")
        if self.probe_kind not in PROBE_KINDS:
            raise ValueError("unsupported probe_kind")
        if not isinstance(self.validation_code, str) or not self.validation_code:
            raise ValueError("validation_code must be non-empty")
        if not isinstance(self.success, bool):
            raise ValueError("receipt status must be boolean")
        for name in ("arguments_projection", "result_projection"):
            if not isinstance(getattr(self, name), Mapping):
                raise ValueError(f"{name} must be a mapping")
            if (
                len(
                    json.dumps(
                        getattr(self, name),
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                )
                > 16_384
            ):
                raise ValueError(f"{name} exceeds bounded receipt size")
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str) or len(self.failure_code) > 128
        ):
            raise ValueError("failure_code must be a bounded string or None")

    @classmethod
    def build(
        cls,
        *,
        hypothesis_id: str,
        highest_risk_counterexample: str,
        tool_identity: str,
        arguments_projection: Mapping[str, Any],
        candidate: Any,
        evidence: Any,
        artifact_before: Any,
        artifact_after: Any,
        probe_kind: str,
        challenge: str,
        validation_code: str,
        validated: bool,
        result_projection: Mapping[str, Any],
        success: bool,
        failure_code: str | None = None,
    ) -> "AcceptanceProbeReceipt":
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            hypothesis_hash=semantic_fingerprint(hypothesis_id),
            counterexample_hash=semantic_fingerprint(highest_risk_counterexample),
            tool_identity=tool_identity[:256],
            tool_identity_hash=semantic_fingerprint(tool_identity),
            arguments_hash=semantic_fingerprint(arguments_projection),
            candidate_hash=semantic_fingerprint(candidate),
            evidence_hash=semantic_fingerprint(evidence),
            artifact_before_hash=semantic_fingerprint(artifact_before),
            artifact_after_hash=semantic_fingerprint(artifact_after),
            probe_kind=probe_kind,
            challenge_hash=semantic_fingerprint(challenge),
            attestation_hash=framework_probe_attestation(
                challenge=challenge,
                probe_kind=probe_kind,
                receipt_fields={
                    "tool_identity": tool_identity,
                    "arguments_hash": semantic_fingerprint(arguments_projection),
                    "candidate_hash": semantic_fingerprint(candidate),
                    "evidence_hash": semantic_fingerprint(evidence),
                    "artifact_before_hash": semantic_fingerprint(artifact_before),
                    "artifact_after_hash": semantic_fingerprint(artifact_after),
                    "result_hash": semantic_fingerprint(result_projection),
                    "validation_code": validation_code,
                },
            ),
            validation_code=validation_code,
            result_hash=semantic_fingerprint(result_projection),
            arguments_projection=dict(arguments_projection),
            result_projection=dict(result_projection),
            success=bool(success and validated),
            failure_code=failure_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis_hash": self.hypothesis_hash,
            "counterexample_hash": self.counterexample_hash,
            "tool_identity": self.tool_identity,
            "tool_identity_hash": self.tool_identity_hash,
            "arguments_hash": self.arguments_hash,
            "candidate_hash": self.candidate_hash,
            "evidence_hash": self.evidence_hash,
            "artifact_before_hash": self.artifact_before_hash,
            "artifact_after_hash": self.artifact_after_hash,
            "probe_kind": self.probe_kind,
            "challenge_hash": self.challenge_hash,
            "attestation_hash": self.attestation_hash,
            "validation_code": self.validation_code,
            "result_hash": self.result_hash,
            "arguments_projection": dict(self.arguments_projection),
            "result_projection": dict(self.result_projection),
            "success": self.success,
            "failure_code": self.failure_code,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AcceptanceProbeReceipt":
        if not isinstance(value, Mapping):
            raise ValueError("acceptance probe receipt must be an object")
        return cls(
            schema_version=value.get("schema_version"),
            hypothesis_hash=value.get("hypothesis_hash"),
            counterexample_hash=value.get("counterexample_hash"),
            tool_identity=value.get("tool_identity"),
            tool_identity_hash=value.get("tool_identity_hash"),
            arguments_hash=value.get("arguments_hash"),
            candidate_hash=value.get("candidate_hash"),
            evidence_hash=value.get("evidence_hash"),
            artifact_before_hash=value.get("artifact_before_hash"),
            artifact_after_hash=value.get("artifact_after_hash"),
            probe_kind=value.get("probe_kind"),
            challenge_hash=value.get("challenge_hash"),
            attestation_hash=value.get("attestation_hash"),
            validation_code=value.get("validation_code"),
            result_hash=value.get("result_hash"),
            arguments_projection=value.get("arguments_projection"),
            result_projection=value.get("result_projection"),
            success=value.get("success"),
            failure_code=value.get("failure_code"),
        )

    def supports(self, decision: AcceptanceCriticDecision) -> bool:
        return bool(
            self.success
            and self.hypothesis_hash == semantic_fingerprint(decision.hypothesis_id)
            and self.counterexample_hash
            == semantic_fingerprint(decision.highest_risk_counterexample)
            and self.tool_identity_hash == semantic_fingerprint(self.tool_identity)
            and self.arguments_hash == semantic_fingerprint(self.arguments_projection)
            and self.result_hash == semantic_fingerprint(self.result_projection)
        )

    def has_valid_framework_attestation(self, challenge: str) -> bool:
        expected = framework_probe_attestation(
            challenge=challenge,
            probe_kind=self.probe_kind,
            receipt_fields={
                "tool_identity": self.tool_identity,
                "arguments_hash": self.arguments_hash,
                "candidate_hash": self.candidate_hash,
                "evidence_hash": self.evidence_hash,
                "artifact_before_hash": self.artifact_before_hash,
                "artifact_after_hash": self.artifact_after_hash,
                "result_hash": self.result_hash,
                "validation_code": self.validation_code,
            },
        )
        return self.challenge_hash == semantic_fingerprint(challenge) and (
            self.attestation_hash == expected
        )


def fresh_acceptance_critic_messages(
    *,
    public_request: str,
    candidate: str,
    evidence: Mapping[str, Any],
    probe_receipt: AcceptanceProbeReceipt | None,
    probe_hypothesis_id: str | None = None,
    probe_counterexample: str | None = None,
) -> list[dict[str, str]]:
    """Build the complete critic request without solver reasoning/history."""
    request = str(public_request or "")[:32_000]
    candidate_text = str(candidate or "")[:32_000]
    evidence_projection = {
        key: evidence.get(key)
        for key in (
            "artifact_fingerprint",
            "failure_signature",
            "completion_evidence_fingerprint",
            "goal_progress_count",
            "last_meaningful_progress_agent_step",
        )
        if evidence.get(key) is not None
    }
    payload = {
        "public_request": request,
        "candidate": candidate_text,
        "framework_evidence": evidence_projection,
        "probe_receipt": probe_receipt.to_dict() if probe_receipt else None,
        "probe_plan": (
            {
                "hypothesis_id": probe_hypothesis_id,
                "highest_risk_counterexample": probe_counterexample,
            }
            if probe_hypothesis_id and probe_counterexample
            else None
        ),
    }
    if probe_receipt is None:
        instruction = (
            "Identify the single highest-risk counterexample to this candidate. "
            "Execute exactly one fresh equivalent probe with an available Tool. "
            "The Tool call must include __aworld_acceptance_probe containing the "
            "same hypothesis_id and highest_risk_counterexample plus one bounded "
            "probe_kind. AWorld owns the challenge and success criteria; do not "
            "print or encode a marker. Solver-authored claims are not evidence."
        )
    else:
        instruction = (
            "Return only one JSON object with exactly: decision (accept, repair, "
            "or uncertain), highest_risk_counterexample, hypothesis_id, and reason. "
            "accept is allowed only when the separate framework probe receipt is "
            "successful and matches both identifiers."
            " Judge the bounded tool arguments and captured result projection; "
            "transport success alone is not evidence."
        )
    return [
        {
            "role": "system",
            "content": (
                "You are AWorld's independent acceptance critic. You receive no "
                "solver reasoning and must judge only bounded public inputs and "
                "framework-observed evidence."
            ),
        },
        {
            "role": "user",
            "content": instruction
            + "\n\nINPUT="
            + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    ]


__all__ = [
    "AcceptanceCriticDecision",
    "AcceptanceDecision",
    "AcceptanceProbeReceipt",
    "PROBE_KINDS",
    "framework_probe_attestation",
    "fresh_acceptance_critic_messages",
    "probe_arguments_are_executable",
    "validate_probe_result",
]
