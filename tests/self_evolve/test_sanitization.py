from __future__ import annotations

import json

import pytest

from aworld.self_evolve.failure_events import (
    AggregatedReplayFailure,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
)
from aworld.self_evolve.sanitization import public_diagnostic_projection


def _large_private_failure() -> AggregatedReplayFailure:
    observations = tuple(
        ReplayFailureObservation(
            event=ReplayFailureEvent(
                event_id=f"event-{index:03d}",
                code="capability_contract_rejected",
                owner=FailureOwner.CANDIDATE,
                stage=FailureStage.CAPABILITY_PREFLIGHT,
                scope=FailureScope.MEMBER,
                repairable=True,
                capability_id="/private/raw/capability-identity",
                requirement_id="private-requirement-identity",
                diagnostics={"raw_response": f"PRIVATE_SECRET_{index}"},
            ),
            case_id=f"case-{index:03d}",
            run_id=f"run-{index:03d}",
            task_id=f"task-{index:03d}",
            candidate_id=f"candidate-{index:03d}",
        )
        for index in range(130)
    )
    return aggregate_replay_failure_observations(observations)[0]


def test_public_projection_preserves_large_typed_aggregate_integrity() -> None:
    aggregate = _large_private_failure()

    projected = public_diagnostic_projection(
        {"nested": [{"failure": aggregate.to_dict()}]}
    )
    failure = projected["nested"][0]["failure"]
    loaded = AggregatedReplayFailure.from_dict(failure)
    encoded = json.dumps(projected, sort_keys=True)

    assert loaded.occurrence_count == 130
    assert loaded.affected_member_count == 130
    assert loaded.distinct_source_count == 130
    assert len(loaded.affected_case_identity_digests) == 130
    assert len(loaded.source_identity_digests) == 130
    assert "/private/raw" not in encoded
    assert "private-requirement-identity" not in encoded
    assert "PRIVATE_SECRET" not in encoded


def test_public_projection_rejects_forged_typed_aggregate() -> None:
    forged = _large_private_failure().to_dict()
    forged["affected_member_count"] += 1

    with pytest.raises(ValueError, match="complete affected identity set"):
        public_diagnostic_projection({"failure": forged})
