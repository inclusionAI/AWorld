from __future__ import annotations

from aworld.self_evolve.causal_admission import (
    candidate_causal_admission_blocker,
)


def _screening_candidate_failure() -> dict[str, object]:
    event = {
        "owner": "candidate",
        "scope": "candidate",
        "repairable": True,
        "stage": "task_rollout",
        "code": "candidate_runtime_policy_regressed",
    }
    return {
        "failure_class": "candidate",
        "failure_owner": "candidate",
        "failure_scope": "candidate",
        "repairable": True,
        "evaluator_skipped": True,
        "checkpoint_stage": "screening",
        "failure_event": event,
        "causal_failure_events": [event],
    }


def test_screening_rollout_failure_is_candidate_admission_blocker() -> None:
    assert candidate_causal_admission_blocker(
        gate_name="candidate_replay",
        passed=False,
        details=_screening_candidate_failure(),
    )


def test_authoritative_candidate_conclusion_is_not_prerequisite_blocker() -> None:
    assert not candidate_causal_admission_blocker(
        gate_name="candidate_replay",
        passed=False,
        details={
            "failure_class": "candidate",
            "failure_owner": "candidate",
            "failure_scope": "candidate",
            "repairable": True,
            "checkpoint_stage": "authoritative_replay",
            "evaluator_skipped": False,
            "failure_event": {
                "owner": "candidate",
                "scope": "candidate",
                "repairable": True,
                "stage": "task_rollout",
            },
        },
    )


def test_derived_measurement_failure_is_not_candidate_blocker() -> None:
    assert not candidate_causal_admission_blocker(
        gate_name="trusted_improvement_measurement",
        passed=False,
        details={
            "failure_class": "measurement",
            "next_action": "repair_measurement",
        },
    )
