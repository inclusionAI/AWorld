from __future__ import annotations

from aworld.self_evolve.budget import CandidateAttemptStage
from aworld.self_evolve.controllers.run_resources import CandidateAttemptTracker
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _tracker(tmp_path) -> CandidateAttemptTracker:
    return CandidateAttemptTracker(
        store=FilesystemSelfEvolveStore(tmp_path),
        run_id="run-attempt-finalization",
    )


def _comparable_attempt(tracker: CandidateAttemptTracker):
    key = tracker.start(iteration=0, slot=0, candidate_id="candidate-1")
    for stage in (
        CandidateAttemptStage.UNIQUE,
        CandidateAttemptStage.LOCAL_GATES,
        CandidateAttemptStage.PAIRED_REPLAY_STARTED,
        CandidateAttemptStage.PAIRED_REPLAY_COMPLETED,
        CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE,
    ):
        tracker.emit(key, stage)
    return key


def test_terminal_cleanup_blocks_open_comparable_attempt(tmp_path) -> None:
    tracker = _tracker(tmp_path)
    key = _comparable_attempt(tracker)

    tracker.finalize_open(reason_code="run_terminated_before_candidate")

    event = tracker._events[key][-1]
    assert event.stage is CandidateAttemptStage.BLOCKED
    assert event.reason_code == "run_terminated_after_candidate_execution"


def test_terminal_cleanup_marks_unstarted_attempt_not_run(tmp_path) -> None:
    tracker = _tracker(tmp_path)
    key = tracker.start(iteration=0, slot=0, candidate_id="candidate-1")

    tracker.finalize_open(reason_code="run_terminated_before_candidate")

    event = tracker._events[key][-1]
    assert event.stage is CandidateAttemptStage.NOT_RUN
    assert event.reason_code == "run_terminated_before_candidate"


def test_evaluation_result_closes_open_comparable_attempt(tmp_path) -> None:
    tracker = _tracker(tmp_path)
    key = _comparable_attempt(tracker)

    tracker.finalize_evaluated(
        key,
        status="rejected",
        infrastructure_failure=False,
    )

    event = tracker._events[key][-1]
    assert event.stage is CandidateAttemptStage.REJECTED
    assert event.reason_code == "candidate_evaluation_rejected"


def test_evaluation_infrastructure_failure_blocks_open_attempt(tmp_path) -> None:
    tracker = _tracker(tmp_path)
    key = _comparable_attempt(tracker)

    tracker.finalize_evaluated(
        key,
        status="rejected",
        infrastructure_failure=True,
    )

    event = tracker._events[key][-1]
    assert event.stage is CandidateAttemptStage.BLOCKED
    assert event.reason_code == "candidate_evaluation_blocked"
