"""Pure, bounded lifecycle for one public reviewer value lease.

The lease binds one reviewer opportunity to a task scope fingerprint, public
request fingerprint, current candidate fingerprint, and one public obligation.
It intentionally stores no task text, paths, prompts, reports, raw Tool
arguments, verifier material, or reward data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, ClassVar

_CANONICAL_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_OBLIGATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_MAX_REVIEW_ELAPSED_MS = 1_000_000
_MAX_REPORTED_TOKENS = 1_000_000


class ReviewerLeaseStatus(str, Enum):
    OFFERED = "offered"
    CLAIMED = "claimed"
    DIRECTIVE_PENDING = "directive_pending"
    VALIDATION_PENDING = "validation_pending"
    REPAIR_PENDING = "repair_pending"
    REVALIDATION_PENDING = "revalidation_pending"
    CLOSED = "closed"


class ReviewerDirectiveSource(str, Enum):
    REVIEWER = "reviewer"
    PUBLIC_CONTRACT_FALLBACK = "public_contract_fallback"


class ReviewerValidationOutcome(str, Enum):
    PASSED = "passed"
    CONTRADICTED = "contradicted"
    REPEATED = "repeated"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class ReviewerLeaseClosureReason(str, Enum):
    REVIEWER_NOT_INVOKED = "reviewer_not_invoked"
    DIRECTIVE_UNAVAILABLE = "directive_unavailable"
    DIRECTIVE_NOT_CONSUMED = "directive_not_consumed"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_REPEATED = "validation_repeated"
    VALIDATION_AMBIGUOUS = "validation_ambiguous"
    VALIDATION_UNAVAILABLE = "validation_unavailable"
    REPAIR_NOT_ATTEMPTED = "repair_not_attempted"
    REPAIR_CANDIDATE_UNCHANGED = "repair_candidate_unchanged"
    REVALIDATION_PASSED = "revalidation_passed"
    REVALIDATION_CONTRADICTED = "revalidation_contradicted"
    REVALIDATION_REPEATED = "revalidation_repeated"
    REVALIDATION_AMBIGUOUS = "revalidation_ambiguous"
    REVALIDATION_UNAVAILABLE = "revalidation_unavailable"
    REVALIDATION_NOT_ATTEMPTED = "revalidation_not_attempted"


def _enum_value(value: Any, enum_type: type[Enum], name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported {name}") from exc


def _canonical_hash(value: Any, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _CANONICAL_HASH.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 fingerprint")
    return value


def _bounded_counter(value: Any, name: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise ValueError(f"{name} must be an integer in [0, {maximum}]")
    return value


def _optional_counter(value: Any, name: str, *, maximum: int) -> int | None:
    if value is None:
        return None
    return _bounded_counter(value, name, maximum=maximum)


@dataclass(frozen=True, slots=True)
class ReviewerValueLease:
    """Immutable state for one reviewer, validation, repair, and revalidation.

    All identities are content-free fingerprints except ``obligation_id``,
    which is a bounded public-ledger identifier and deliberately cannot contain
    path separators or whitespace.
    """

    SCHEMA_VERSION: ClassVar[str] = "aworld.reviewer-value-lease/v1"

    scope_hash: str
    request_hash: str
    candidate_hash: str
    obligation_id: str
    obligation_source_fingerprint: str
    status: ReviewerLeaseStatus = ReviewerLeaseStatus.OFFERED
    directive_source: ReviewerDirectiveSource | None = None
    directive_hash: str | None = None
    validation_candidate_hash: str | None = None
    validation_arguments_hash: str | None = None
    repair_candidate_hash: str | None = None
    revalidation_arguments_hash: str | None = None
    review_claim_count: int = 0
    validation_attempt_count: int = 0
    repair_attempt_count: int = 0
    revalidation_attempt_count: int = 0
    closure_reason: ReviewerLeaseClosureReason | None = None
    review_elapsed_ms: int | None = None
    reported_input_tokens: int | None = None
    reported_output_tokens: int | None = None
    usage_reported: bool = False

    def __post_init__(self) -> None:
        for name in (
            "scope_hash",
            "request_hash",
            "candidate_hash",
            "obligation_source_fingerprint",
        ):
            _canonical_hash(getattr(self, name), name)
        if (
            not isinstance(self.obligation_id, str)
            or _OBLIGATION_ID.fullmatch(self.obligation_id) is None
        ):
            raise ValueError("obligation_id must be a bounded public identifier")

        object.__setattr__(
            self,
            "status",
            _enum_value(self.status, ReviewerLeaseStatus, "reviewer lease status"),
        )
        if self.directive_source is not None:
            object.__setattr__(
                self,
                "directive_source",
                _enum_value(
                    self.directive_source,
                    ReviewerDirectiveSource,
                    "reviewer directive source",
                ),
            )
        if self.closure_reason is not None:
            object.__setattr__(
                self,
                "closure_reason",
                _enum_value(
                    self.closure_reason,
                    ReviewerLeaseClosureReason,
                    "reviewer lease closure reason",
                ),
            )
        for name in (
            "directive_hash",
            "validation_candidate_hash",
            "validation_arguments_hash",
            "repair_candidate_hash",
            "revalidation_arguments_hash",
        ):
            _canonical_hash(getattr(self, name), name, optional=True)
        for name in (
            "review_claim_count",
            "validation_attempt_count",
            "repair_attempt_count",
            "revalidation_attempt_count",
        ):
            _bounded_counter(getattr(self, name), name, maximum=1)
        _optional_counter(
            self.review_elapsed_ms,
            "review_elapsed_ms",
            maximum=_MAX_REVIEW_ELAPSED_MS,
        )
        _optional_counter(
            self.reported_input_tokens,
            "reported_input_tokens",
            maximum=_MAX_REPORTED_TOKENS,
        )
        _optional_counter(
            self.reported_output_tokens,
            "reported_output_tokens",
            maximum=_MAX_REPORTED_TOKENS,
        )
        if not isinstance(self.usage_reported, bool):
            raise TypeError("usage_reported must be boolean")
        token_counts = (self.reported_input_tokens, self.reported_output_tokens)
        if self.usage_reported and not any(value is not None for value in token_counts):
            raise ValueError("reported usage requires at least one token counter")
        if not self.usage_reported and any(value is not None for value in token_counts):
            raise ValueError("token counters require usage_reported=true")

        self._validate_lifecycle_shape()

    def _validate_lifecycle_shape(self) -> None:
        status = self.status
        counts = (
            self.review_claim_count,
            self.validation_attempt_count,
            self.repair_attempt_count,
            self.revalidation_attempt_count,
        )
        directive_present = bool(
            self.directive_source is not None and self.directive_hash is not None
        )
        if (self.directive_source is None) != (self.directive_hash is None):
            raise ValueError("directive source and fingerprint must appear together")
        if status is ReviewerLeaseStatus.OFFERED:
            if counts != (0, 0, 0, 0) or directive_present:
                raise ValueError("offered lease cannot contain consumed work")
        elif status in {
            ReviewerLeaseStatus.CLAIMED,
            ReviewerLeaseStatus.DIRECTIVE_PENDING,
        }:
            if counts != (1, 0, 0, 0) or directive_present:
                raise ValueError("claimed lease cannot contain a directive or attempt")
        elif status is ReviewerLeaseStatus.VALIDATION_PENDING:
            if counts != (1, 0, 0, 0) or not directive_present:
                raise ValueError("validation_pending requires one claimed directive")
        elif status is ReviewerLeaseStatus.REPAIR_PENDING:
            if counts != (1, 1, 0, 0) or not directive_present:
                raise ValueError("repair_pending requires one contradicted validation")
        elif status is ReviewerLeaseStatus.REVALIDATION_PENDING:
            if counts != (1, 1, 1, 0) or not directive_present:
                raise ValueError("revalidation_pending requires one changed repair")
        elif status is ReviewerLeaseStatus.CLOSED:
            if self.closure_reason is None:
                raise ValueError("closed lease requires a closure reason")
            if not directive_present and self.closure_reason not in {
                ReviewerLeaseClosureReason.REVIEWER_NOT_INVOKED,
                ReviewerLeaseClosureReason.DIRECTIVE_UNAVAILABLE,
            }:
                raise ValueError("closed lease requires its claimed directive")

        if status is not ReviewerLeaseStatus.CLOSED and self.closure_reason is not None:
            raise ValueError("closure reason is valid only for a closed lease")
        if self.validation_attempt_count:
            if (
                self.validation_candidate_hash != self.candidate_hash
                or self.validation_arguments_hash is None
            ):
                raise ValueError("initial validation must bind the original candidate")
        elif (
            self.validation_candidate_hash is not None
            or self.validation_arguments_hash is not None
        ):
            raise ValueError("validation fingerprints require a validation attempt")
        if self.repair_candidate_hash is not None:
            if not self.repair_attempt_count:
                raise ValueError("repair candidate requires a repair attempt")
            if self.repair_candidate_hash == self.candidate_hash:
                raise ValueError("repair candidate must differ from original candidate")
        if self.revalidation_attempt_count:
            if (
                self.repair_candidate_hash is None
                or self.revalidation_arguments_hash is None
            ):
                raise ValueError("revalidation must bind the changed repair candidate")
        elif self.revalidation_arguments_hash is not None:
            raise ValueError("revalidation fingerprint requires a revalidation attempt")
        if status is ReviewerLeaseStatus.REVALIDATION_PENDING and (
            self.repair_candidate_hash is None
        ):
            raise ValueError("revalidation_pending requires a changed repair candidate")
        if status in {
            ReviewerLeaseStatus.OFFERED,
            ReviewerLeaseStatus.CLAIMED,
            ReviewerLeaseStatus.DIRECTIVE_PENDING,
        } and (
            self.review_elapsed_ms is not None
            or self.usage_reported
            or self.reported_input_tokens is not None
            or self.reported_output_tokens is not None
        ):
            raise ValueError("review ROI is valid only after a directive is recorded")

        validation_reasons = {
            ReviewerLeaseClosureReason.VALIDATION_PASSED,
            ReviewerLeaseClosureReason.VALIDATION_REPEATED,
            ReviewerLeaseClosureReason.VALIDATION_AMBIGUOUS,
            ReviewerLeaseClosureReason.VALIDATION_UNAVAILABLE,
        }
        if (
            self.closure_reason
            is ReviewerLeaseClosureReason.REVIEWER_NOT_INVOKED
            and counts != (0, 0, 0, 0)
        ):
            raise ValueError("unclaimed closure cannot contain consumed work")
        if (
            self.closure_reason
            is ReviewerLeaseClosureReason.DIRECTIVE_UNAVAILABLE
            and counts != (1, 0, 0, 0)
        ):
            raise ValueError("unavailable directive requires one reviewer claim")
        if (
            self.closure_reason
            is ReviewerLeaseClosureReason.DIRECTIVE_NOT_CONSUMED
            and counts != (1, 0, 0, 0)
        ):
            raise ValueError("unconsumed directive cannot contain validation work")
        revalidation_reasons = {
            ReviewerLeaseClosureReason.REVALIDATION_PASSED,
            ReviewerLeaseClosureReason.REVALIDATION_CONTRADICTED,
            ReviewerLeaseClosureReason.REVALIDATION_REPEATED,
            ReviewerLeaseClosureReason.REVALIDATION_AMBIGUOUS,
            ReviewerLeaseClosureReason.REVALIDATION_UNAVAILABLE,
        }
        if self.closure_reason in validation_reasons and counts != (1, 1, 0, 0):
            raise ValueError("validation closure requires exactly one validation")
        if (
            self.closure_reason
            is ReviewerLeaseClosureReason.REPAIR_CANDIDATE_UNCHANGED
            and counts != (1, 1, 1, 0)
        ):
            raise ValueError(
                "unchanged repair closure requires exactly one repair attempt"
            )
        if (
            self.closure_reason
            is ReviewerLeaseClosureReason.REPAIR_CANDIDATE_UNCHANGED
            and self.repair_candidate_hash is not None
        ):
            raise ValueError("unchanged repair closure cannot bind a changed candidate")
        if (
            self.closure_reason
            is ReviewerLeaseClosureReason.REPAIR_NOT_ATTEMPTED
            and counts != (1, 1, 0, 0)
        ):
            raise ValueError("unattempted repair requires one contradicted validation")
        if (
            self.closure_reason
            is ReviewerLeaseClosureReason.REVALIDATION_NOT_ATTEMPTED
            and counts != (1, 1, 1, 0)
        ):
            raise ValueError("unattempted revalidation requires one changed repair")
        if self.closure_reason in revalidation_reasons and counts != (1, 1, 1, 1):
            raise ValueError("revalidation closure requires exactly one revalidation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "scope_hash": self.scope_hash,
            "request_hash": self.request_hash,
            "candidate_hash": self.candidate_hash,
            "obligation_id": self.obligation_id,
            "obligation_source_fingerprint": self.obligation_source_fingerprint,
            "status": self.status.value,
            "directive_source": (
                self.directive_source.value
                if self.directive_source is not None
                else None
            ),
            "directive_hash": self.directive_hash,
            "validation_candidate_hash": self.validation_candidate_hash,
            "validation_arguments_hash": self.validation_arguments_hash,
            "repair_candidate_hash": self.repair_candidate_hash,
            "revalidation_arguments_hash": self.revalidation_arguments_hash,
            "review_claim_count": self.review_claim_count,
            "validation_attempt_count": self.validation_attempt_count,
            "repair_attempt_count": self.repair_attempt_count,
            "revalidation_attempt_count": self.revalidation_attempt_count,
            "closure_reason": (
                self.closure_reason.value if self.closure_reason is not None else None
            ),
            "review_elapsed_ms": self.review_elapsed_ms,
            "reported_input_tokens": self.reported_input_tokens,
            "reported_output_tokens": self.reported_output_tokens,
            "usage_reported": self.usage_reported,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReviewerValueLease:
        if not isinstance(value, Mapping):
            raise TypeError("reviewer value lease must be a mapping")
        fields = {
            "schema_version",
            "scope_hash",
            "request_hash",
            "candidate_hash",
            "obligation_id",
            "obligation_source_fingerprint",
            "status",
            "directive_source",
            "directive_hash",
            "validation_candidate_hash",
            "validation_arguments_hash",
            "repair_candidate_hash",
            "revalidation_arguments_hash",
            "review_claim_count",
            "validation_attempt_count",
            "repair_attempt_count",
            "revalidation_attempt_count",
            "closure_reason",
            "review_elapsed_ms",
            "reported_input_tokens",
            "reported_output_tokens",
            "usage_reported",
        }
        unknown = set(value) - fields
        if unknown:
            raise ValueError("reviewer value lease contains unknown fields")
        required = {
            "schema_version",
            "scope_hash",
            "request_hash",
            "candidate_hash",
            "obligation_id",
            "obligation_source_fingerprint",
            "status",
        }
        if not required.issubset(value):
            raise ValueError("reviewer value lease is missing required fields")
        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported reviewer value lease schema")
        return cls(
            scope_hash=value.get("scope_hash"),
            request_hash=value.get("request_hash"),
            candidate_hash=value.get("candidate_hash"),
            obligation_id=value.get("obligation_id"),
            obligation_source_fingerprint=value.get(
                "obligation_source_fingerprint"
            ),
            status=value.get("status"),
            directive_source=value.get("directive_source"),
            directive_hash=value.get("directive_hash"),
            validation_candidate_hash=value.get("validation_candidate_hash"),
            validation_arguments_hash=value.get("validation_arguments_hash"),
            repair_candidate_hash=value.get("repair_candidate_hash"),
            revalidation_arguments_hash=value.get("revalidation_arguments_hash"),
            review_claim_count=value.get("review_claim_count", 0),
            validation_attempt_count=value.get("validation_attempt_count", 0),
            repair_attempt_count=value.get("repair_attempt_count", 0),
            revalidation_attempt_count=value.get("revalidation_attempt_count", 0),
            closure_reason=value.get("closure_reason"),
            review_elapsed_ms=value.get("review_elapsed_ms"),
            reported_input_tokens=value.get("reported_input_tokens"),
            reported_output_tokens=value.get("reported_output_tokens"),
            usage_reported=value.get("usage_reported", False),
        )


def _require_lease(value: Any) -> ReviewerValueLease:
    if not isinstance(value, ReviewerValueLease):
        raise TypeError("lease must be ReviewerValueLease")
    return value


def offer_reviewer_value_lease(
    *,
    scope_hash: str,
    request_hash: str,
    candidate_hash: str,
    obligation_id: str,
    obligation_source_fingerprint: str,
) -> ReviewerValueLease:
    return ReviewerValueLease(
        scope_hash=scope_hash,
        request_hash=request_hash,
        candidate_hash=candidate_hash,
        obligation_id=obligation_id,
        obligation_source_fingerprint=obligation_source_fingerprint,
    )


def claim_reviewer_value_lease(lease: ReviewerValueLease) -> ReviewerValueLease:
    lease = _require_lease(lease)
    if lease.status is not ReviewerLeaseStatus.OFFERED:
        raise ValueError("only an offered reviewer value lease may be claimed")
    return replace(
        lease,
        status=ReviewerLeaseStatus.CLAIMED,
        review_claim_count=1,
    )


def mark_reviewer_directive_pending(
    lease: ReviewerValueLease,
) -> ReviewerValueLease:
    lease = _require_lease(lease)
    if lease.status is not ReviewerLeaseStatus.CLAIMED:
        raise ValueError("directive may become pending only for a claimed lease")
    return replace(lease, status=ReviewerLeaseStatus.DIRECTIVE_PENDING)


def record_reviewer_directive(
    lease: ReviewerValueLease,
    *,
    source: ReviewerDirectiveSource,
    directive_hash: str,
    review_elapsed_ms: int | None = None,
    reported_input_tokens: int | None = None,
    reported_output_tokens: int | None = None,
    usage_reported: bool = False,
) -> ReviewerValueLease:
    lease = _require_lease(lease)
    if lease.status is not ReviewerLeaseStatus.DIRECTIVE_PENDING:
        raise ValueError("directive requires a directive_pending lease")
    return replace(
        lease,
        status=ReviewerLeaseStatus.VALIDATION_PENDING,
        directive_source=source,
        directive_hash=directive_hash,
        review_elapsed_ms=review_elapsed_ms,
        reported_input_tokens=reported_input_tokens,
        reported_output_tokens=reported_output_tokens,
        usage_reported=usage_reported,
    )


_VALIDATION_CLOSURES = {
    ReviewerValidationOutcome.PASSED: ReviewerLeaseClosureReason.VALIDATION_PASSED,
    ReviewerValidationOutcome.REPEATED: ReviewerLeaseClosureReason.VALIDATION_REPEATED,
    ReviewerValidationOutcome.AMBIGUOUS: (
        ReviewerLeaseClosureReason.VALIDATION_AMBIGUOUS
    ),
    ReviewerValidationOutcome.UNAVAILABLE: (
        ReviewerLeaseClosureReason.VALIDATION_UNAVAILABLE
    ),
}


def record_reviewer_validation(
    lease: ReviewerValueLease,
    *,
    candidate_hash: str,
    arguments_hash: str,
    outcome: ReviewerValidationOutcome,
) -> ReviewerValueLease:
    lease = _require_lease(lease)
    if lease.status is not ReviewerLeaseStatus.VALIDATION_PENDING:
        raise ValueError("validation requires a validation_pending lease")
    _canonical_hash(candidate_hash, "candidate_hash")
    _canonical_hash(arguments_hash, "arguments_hash")
    outcome = _enum_value(
        outcome, ReviewerValidationOutcome, "reviewer validation outcome"
    )
    if candidate_hash != lease.candidate_hash:
        raise ValueError("initial validation must use the original candidate")
    updates: dict[str, Any] = {
        "validation_candidate_hash": candidate_hash,
        "validation_arguments_hash": arguments_hash,
        "validation_attempt_count": 1,
    }
    if outcome is ReviewerValidationOutcome.CONTRADICTED:
        updates["status"] = ReviewerLeaseStatus.REPAIR_PENDING
    else:
        updates.update(
            status=ReviewerLeaseStatus.CLOSED,
            closure_reason=_VALIDATION_CLOSURES[outcome],
        )
    return replace(lease, **updates)


def record_reviewer_repair(
    lease: ReviewerValueLease,
    *,
    candidate_hash: str,
) -> ReviewerValueLease:
    lease = _require_lease(lease)
    if lease.status is not ReviewerLeaseStatus.REPAIR_PENDING:
        raise ValueError("repair requires a repair_pending lease")
    _canonical_hash(candidate_hash, "candidate_hash")
    if candidate_hash == lease.candidate_hash:
        return replace(
            lease,
            status=ReviewerLeaseStatus.CLOSED,
            repair_attempt_count=1,
            closure_reason=ReviewerLeaseClosureReason.REPAIR_CANDIDATE_UNCHANGED,
        )
    return replace(
        lease,
        status=ReviewerLeaseStatus.REVALIDATION_PENDING,
        repair_candidate_hash=candidate_hash,
        repair_attempt_count=1,
    )


_REVALIDATION_CLOSURES = {
    ReviewerValidationOutcome.PASSED: ReviewerLeaseClosureReason.REVALIDATION_PASSED,
    ReviewerValidationOutcome.CONTRADICTED: (
        ReviewerLeaseClosureReason.REVALIDATION_CONTRADICTED
    ),
    ReviewerValidationOutcome.REPEATED: (
        ReviewerLeaseClosureReason.REVALIDATION_REPEATED
    ),
    ReviewerValidationOutcome.AMBIGUOUS: (
        ReviewerLeaseClosureReason.REVALIDATION_AMBIGUOUS
    ),
    ReviewerValidationOutcome.UNAVAILABLE: (
        ReviewerLeaseClosureReason.REVALIDATION_UNAVAILABLE
    ),
}


def record_reviewer_revalidation(
    lease: ReviewerValueLease,
    *,
    candidate_hash: str,
    arguments_hash: str,
    outcome: ReviewerValidationOutcome,
) -> ReviewerValueLease:
    lease = _require_lease(lease)
    if lease.status is not ReviewerLeaseStatus.REVALIDATION_PENDING:
        raise ValueError("revalidation requires a revalidation_pending lease")
    _canonical_hash(candidate_hash, "candidate_hash")
    _canonical_hash(arguments_hash, "arguments_hash")
    outcome = _enum_value(
        outcome, ReviewerValidationOutcome, "reviewer revalidation outcome"
    )
    if candidate_hash != lease.repair_candidate_hash:
        raise ValueError("revalidation must use the changed repair candidate")
    return replace(
        lease,
        status=ReviewerLeaseStatus.CLOSED,
        revalidation_arguments_hash=arguments_hash,
        revalidation_attempt_count=1,
        closure_reason=_REVALIDATION_CLOSURES[outcome],
    )


def reopen_reviewer_revalidation_for_candidate_change(
    lease: ReviewerValueLease,
    *,
    candidate_hash: str,
) -> ReviewerValueLease:
    """Require one revalidation when a validated candidate changes later."""

    lease = _require_lease(lease)
    _canonical_hash(candidate_hash, "candidate_hash")
    if (
        lease.status is not ReviewerLeaseStatus.CLOSED
        or lease.closure_reason is not ReviewerLeaseClosureReason.VALIDATION_PASSED
        or lease.validation_attempt_count != 1
        or lease.repair_attempt_count != 0
        or candidate_hash == lease.candidate_hash
    ):
        raise ValueError("only a changed validated candidate may be revalidated")
    return replace(
        lease,
        status=ReviewerLeaseStatus.REVALIDATION_PENDING,
        repair_candidate_hash=candidate_hash,
        repair_attempt_count=1,
        closure_reason=None,
    )


def close_reviewer_value_lease_for_liveness(
    lease: ReviewerValueLease,
) -> ReviewerValueLease:
    """Close an unconsumed lifecycle stage before external finalization."""

    lease = _require_lease(lease)
    reasons = {
        ReviewerLeaseStatus.OFFERED: (
            ReviewerLeaseClosureReason.REVIEWER_NOT_INVOKED
        ),
        ReviewerLeaseStatus.CLAIMED: (
            ReviewerLeaseClosureReason.DIRECTIVE_UNAVAILABLE
        ),
        ReviewerLeaseStatus.DIRECTIVE_PENDING: (
            ReviewerLeaseClosureReason.DIRECTIVE_UNAVAILABLE
        ),
        ReviewerLeaseStatus.VALIDATION_PENDING: (
            ReviewerLeaseClosureReason.DIRECTIVE_NOT_CONSUMED
        ),
        ReviewerLeaseStatus.REPAIR_PENDING: (
            ReviewerLeaseClosureReason.REPAIR_NOT_ATTEMPTED
        ),
        ReviewerLeaseStatus.REVALIDATION_PENDING: (
            ReviewerLeaseClosureReason.REVALIDATION_NOT_ATTEMPTED
        ),
    }
    reason = reasons.get(lease.status)
    if reason is None:
        raise ValueError("closed reviewer value lease cannot be closed again")
    return replace(
        lease,
        status=ReviewerLeaseStatus.CLOSED,
        closure_reason=reason,
    )


__all__ = [
    "ReviewerDirectiveSource",
    "ReviewerLeaseClosureReason",
    "ReviewerLeaseStatus",
    "ReviewerValidationOutcome",
    "ReviewerValueLease",
    "claim_reviewer_value_lease",
    "close_reviewer_value_lease_for_liveness",
    "mark_reviewer_directive_pending",
    "offer_reviewer_value_lease",
    "record_reviewer_directive",
    "record_reviewer_repair",
    "record_reviewer_revalidation",
    "record_reviewer_validation",
    "reopen_reviewer_revalidation_for_candidate_change",
]
