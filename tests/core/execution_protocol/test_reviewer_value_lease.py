import json
from dataclasses import FrozenInstanceError

import pytest

from aworld.core.execution_protocol import (
    ReviewerDirectiveSource,
    ReviewerLeaseClosureReason,
    ReviewerLeaseStatus,
    ReviewerValidationOutcome,
    ReviewerValueLease,
    claim_reviewer_value_lease,
    close_reviewer_value_lease_for_liveness,
    mark_reviewer_directive_pending,
    offer_reviewer_value_lease,
    record_reviewer_directive,
    record_reviewer_repair,
    record_reviewer_revalidation,
    record_reviewer_validation,
    reopen_reviewer_revalidation_for_candidate_change,
)


def _hash(character: str) -> str:
    return "sha256:" + character * 64


def _offered() -> ReviewerValueLease:
    return offer_reviewer_value_lease(
        scope_hash=_hash("a"),
        request_hash=_hash("b"),
        candidate_hash=_hash("c"),
        obligation_id="public-obligation-3",
        obligation_source_fingerprint=_hash("d"),
    )


def _validation_pending(
    *,
    source: ReviewerDirectiveSource = ReviewerDirectiveSource.REVIEWER,
    usage_reported: bool = True,
) -> ReviewerValueLease:
    lease = mark_reviewer_directive_pending(
        claim_reviewer_value_lease(_offered())
    )
    return record_reviewer_directive(
        lease,
        source=source,
        directive_hash=_hash("e"),
        review_elapsed_ms=1250,
        reported_input_tokens=400,
        reported_output_tokens=50,
        usage_reported=usage_reported,
    )


def test_lease_binds_only_content_free_public_identities() -> None:
    lease = _offered()

    assert lease.status is ReviewerLeaseStatus.OFFERED
    assert lease.scope_hash == _hash("a")
    assert lease.request_hash == _hash("b")
    assert lease.candidate_hash == _hash("c")
    assert lease.obligation_id == "public-obligation-3"
    assert lease.obligation_source_fingerprint == _hash("d")
    persisted = lease.to_dict()
    assert not set(persisted).intersection(
        {
            "prompt",
            "report",
            "raw_arguments",
            "task_text",
            "path",
            "verifier",
            "reward",
        }
    )
    assert "/tmp/" not in json.dumps(persisted, sort_keys=True)

    with pytest.raises(FrozenInstanceError):
        lease.status = ReviewerLeaseStatus.CLAIMED


def test_reviewer_can_be_claimed_exactly_once() -> None:
    claimed = claim_reviewer_value_lease(_offered())

    assert claimed.status is ReviewerLeaseStatus.CLAIMED
    assert claimed.review_claim_count == 1
    with pytest.raises(ValueError, match="offered"):
        claim_reviewer_value_lease(claimed)


def test_directive_lifecycle_records_bounded_roi_without_raw_material() -> None:
    pending = mark_reviewer_directive_pending(
        claim_reviewer_value_lease(_offered())
    )
    assert pending.status is ReviewerLeaseStatus.DIRECTIVE_PENDING

    lease = record_reviewer_directive(
        pending,
        source=ReviewerDirectiveSource.PUBLIC_CONTRACT_FALLBACK,
        directive_hash=_hash("f"),
        review_elapsed_ms=999,
        reported_input_tokens=None,
        reported_output_tokens=17,
        usage_reported=True,
    )

    assert lease.status is ReviewerLeaseStatus.VALIDATION_PENDING
    assert lease.directive_source is ReviewerDirectiveSource.PUBLIC_CONTRACT_FALLBACK
    assert lease.directive_hash == _hash("f")
    assert lease.review_elapsed_ms == 999
    assert lease.reported_input_tokens is None
    assert lease.reported_output_tokens == 17
    assert lease.usage_reported is True


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (
            ReviewerValidationOutcome.PASSED,
            ReviewerLeaseClosureReason.VALIDATION_PASSED,
        ),
        (
            ReviewerValidationOutcome.REPEATED,
            ReviewerLeaseClosureReason.VALIDATION_REPEATED,
        ),
        (
            ReviewerValidationOutcome.AMBIGUOUS,
            ReviewerLeaseClosureReason.VALIDATION_AMBIGUOUS,
        ),
        (
            ReviewerValidationOutcome.UNAVAILABLE,
            ReviewerLeaseClosureReason.VALIDATION_UNAVAILABLE,
        ),
    ],
)
def test_non_contradictory_validation_outcomes_close_once(
    outcome: ReviewerValidationOutcome,
    reason: ReviewerLeaseClosureReason,
) -> None:
    closed = record_reviewer_validation(
        _validation_pending(),
        candidate_hash=_hash("c"),
        arguments_hash=_hash("1"),
        outcome=outcome,
    )

    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is reason
    assert closed.validation_attempt_count == 1
    with pytest.raises(ValueError, match="validation_pending"):
        record_reviewer_validation(
            closed,
            candidate_hash=_hash("c"),
            arguments_hash=_hash("1"),
            outcome=outcome,
        )


def test_contradiction_allows_one_changed_repair_and_one_revalidation() -> None:
    contradicted = record_reviewer_validation(
        _validation_pending(),
        candidate_hash=_hash("c"),
        arguments_hash=_hash("1"),
        outcome=ReviewerValidationOutcome.CONTRADICTED,
    )
    assert contradicted.status is ReviewerLeaseStatus.REPAIR_PENDING
    with pytest.raises(ValueError, match="validation_pending"):
        record_reviewer_validation(
            contradicted,
            candidate_hash=_hash("c"),
            arguments_hash=_hash("1"),
            outcome=ReviewerValidationOutcome.CONTRADICTED,
        )

    repaired = record_reviewer_repair(
        contradicted,
        candidate_hash=_hash("9"),
    )
    assert repaired.status is ReviewerLeaseStatus.REVALIDATION_PENDING
    assert repaired.repair_attempt_count == 1
    assert repaired.repair_candidate_hash == _hash("9")

    # The exact validation parameters may be reused after the candidate changes.
    closed = record_reviewer_revalidation(
        repaired,
        candidate_hash=_hash("9"),
        arguments_hash=_hash("1"),
        outcome=ReviewerValidationOutcome.PASSED,
    )
    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is ReviewerLeaseClosureReason.REVALIDATION_PASSED
    assert closed.revalidation_attempt_count == 1
    with pytest.raises(ValueError, match="revalidation_pending"):
        record_reviewer_revalidation(
            closed,
            candidate_hash=_hash("9"),
            arguments_hash=_hash("1"),
            outcome=ReviewerValidationOutcome.PASSED,
        )


def test_unchanged_repair_candidate_closes_without_repair_retry() -> None:
    contradicted = record_reviewer_validation(
        _validation_pending(),
        candidate_hash=_hash("c"),
        arguments_hash=_hash("1"),
        outcome=ReviewerValidationOutcome.CONTRADICTED,
    )

    closed = record_reviewer_repair(
        contradicted,
        candidate_hash=_hash("c"),
    )

    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.repair_attempt_count == 1
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.REPAIR_CANDIDATE_UNCHANGED
    )


def test_liveness_closes_unclaimed_offer_without_manufacturing_cost() -> None:
    closed = close_reviewer_value_lease_for_liveness(_offered())

    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.review_claim_count == 0
    assert closed.closure_reason is (
        ReviewerLeaseClosureReason.REVIEWER_NOT_INVOKED
    )


@pytest.mark.parametrize(
    ("lease", "reason"),
    [
        (
            lambda: mark_reviewer_directive_pending(
                claim_reviewer_value_lease(_offered())
            ),
            ReviewerLeaseClosureReason.DIRECTIVE_UNAVAILABLE,
        ),
        (
            _validation_pending,
            ReviewerLeaseClosureReason.DIRECTIVE_NOT_CONSUMED,
        ),
        (
            lambda: record_reviewer_validation(
                _validation_pending(),
                candidate_hash=_hash("c"),
                arguments_hash=_hash("1"),
                outcome=ReviewerValidationOutcome.CONTRADICTED,
            ),
            ReviewerLeaseClosureReason.REPAIR_NOT_ATTEMPTED,
        ),
        (
            lambda: record_reviewer_repair(
                record_reviewer_validation(
                    _validation_pending(),
                    candidate_hash=_hash("c"),
                    arguments_hash=_hash("1"),
                    outcome=ReviewerValidationOutcome.CONTRADICTED,
                ),
                candidate_hash=_hash("9"),
            ),
            ReviewerLeaseClosureReason.REVALIDATION_NOT_ATTEMPTED,
        ),
    ],
)
def test_liveness_closes_each_unconsumed_stage(lease, reason) -> None:
    closed = close_reviewer_value_lease_for_liveness(lease())

    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is reason


def test_later_candidate_change_reopens_only_one_revalidation() -> None:
    validated = record_reviewer_validation(
        _validation_pending(),
        candidate_hash=_hash("c"),
        arguments_hash=_hash("1"),
        outcome=ReviewerValidationOutcome.PASSED,
    )

    reopened = reopen_reviewer_revalidation_for_candidate_change(
        validated,
        candidate_hash=_hash("9"),
    )

    assert reopened.status is ReviewerLeaseStatus.REVALIDATION_PENDING
    assert reopened.repair_candidate_hash == _hash("9")
    with pytest.raises(ValueError, match="changed validated"):
        reopen_reviewer_revalidation_for_candidate_change(
            reopened,
            candidate_hash=_hash("8"),
        )


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (
            ReviewerValidationOutcome.PASSED,
            ReviewerLeaseClosureReason.REVALIDATION_PASSED,
        ),
        (
            ReviewerValidationOutcome.CONTRADICTED,
            ReviewerLeaseClosureReason.REVALIDATION_CONTRADICTED,
        ),
        (
            ReviewerValidationOutcome.REPEATED,
            ReviewerLeaseClosureReason.REVALIDATION_REPEATED,
        ),
        (
            ReviewerValidationOutcome.AMBIGUOUS,
            ReviewerLeaseClosureReason.REVALIDATION_AMBIGUOUS,
        ),
        (
            ReviewerValidationOutcome.UNAVAILABLE,
            ReviewerLeaseClosureReason.REVALIDATION_UNAVAILABLE,
        ),
    ],
)
def test_every_revalidation_outcome_closes_the_lease(
    outcome: ReviewerValidationOutcome,
    reason: ReviewerLeaseClosureReason,
) -> None:
    contradicted = record_reviewer_validation(
        _validation_pending(),
        candidate_hash=_hash("c"),
        arguments_hash=_hash("1"),
        outcome=ReviewerValidationOutcome.CONTRADICTED,
    )
    repaired = record_reviewer_repair(
        contradicted,
        candidate_hash=_hash("9"),
    )

    closed = record_reviewer_revalidation(
        repaired,
        candidate_hash=_hash("9"),
        arguments_hash=_hash("2"),
        outcome=outcome,
    )

    assert closed.status is ReviewerLeaseStatus.CLOSED
    assert closed.closure_reason is reason
    assert closed.revalidation_attempt_count == 1


def test_validation_rejects_stale_candidate_and_requires_repair_candidate() -> None:
    with pytest.raises(ValueError, match="original candidate"):
        record_reviewer_validation(
            _validation_pending(),
            candidate_hash=_hash("8"),
            arguments_hash=_hash("1"),
            outcome=ReviewerValidationOutcome.CONTRADICTED,
        )

    contradicted = record_reviewer_validation(
        _validation_pending(),
        candidate_hash=_hash("c"),
        arguments_hash=_hash("1"),
        outcome=ReviewerValidationOutcome.CONTRADICTED,
    )
    repaired = record_reviewer_repair(
        contradicted,
        candidate_hash=_hash("9"),
    )
    with pytest.raises(ValueError, match="repair candidate"):
        record_reviewer_revalidation(
            repaired,
            candidate_hash=_hash("8"),
            arguments_hash=_hash("1"),
            outcome=ReviewerValidationOutcome.PASSED,
        )


def test_strict_round_trip_and_legacy_safe_optional_defaults() -> None:
    lease = _validation_pending(
        source=ReviewerDirectiveSource.PUBLIC_CONTRACT_FALLBACK
    )
    persisted = lease.to_dict()

    assert ReviewerValueLease.from_dict(persisted) == lease
    legacy = dict(persisted)
    for key in (
        "review_elapsed_ms",
        "reported_input_tokens",
        "reported_output_tokens",
        "usage_reported",
    ):
        legacy.pop(key)
    restored = ReviewerValueLease.from_dict(legacy)
    assert restored.review_elapsed_ms is None
    assert restored.reported_input_tokens is None
    assert restored.reported_output_tokens is None
    assert restored.usage_reported is False

    with pytest.raises(ValueError, match="unknown fields"):
        ReviewerValueLease.from_dict({**persisted, "raw_report": "not allowed"})
    with pytest.raises(ValueError, match="schema"):
        ReviewerValueLease.from_dict(
            {**persisted, "schema_version": "aworld.reviewer-value-lease/v0"}
        )
    closed = record_reviewer_validation(
        lease,
        candidate_hash=_hash("c"),
        arguments_hash=_hash("1"),
        outcome=ReviewerValidationOutcome.PASSED,
    ).to_dict()
    closed["directive_source"] = None
    closed["directive_hash"] = None
    with pytest.raises(ValueError, match="claimed directive"):
        ReviewerValueLease.from_dict(closed)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(scope_hash="not-a-hash"),
        lambda value: value.update(obligation_id="/tmp/private.txt"),
        lambda value: value.update(review_claim_count=2),
        lambda value: value.update(validation_attempt_count=2),
        lambda value: value.update(repair_attempt_count=2),
        lambda value: value.update(revalidation_attempt_count=2),
        lambda value: value.update(review_elapsed_ms=86_400_001),
        lambda value: value.update(usage_reported=False, reported_input_tokens=1),
        lambda value: value.update(
            usage_reported=True,
            reported_input_tokens=None,
            reported_output_tokens=None,
        ),
    ],
)
def test_untrusted_persisted_state_rejects_invalid_or_unbounded_values(mutate) -> None:
    value = _validation_pending().to_dict()
    mutate(value)

    with pytest.raises((TypeError, ValueError)):
        ReviewerValueLease.from_dict(value)
