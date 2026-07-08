from __future__ import annotations

from aworld.self_evolve.lessons import extract_lesson_records
from aworld.self_evolve.types import EvaluationSummary


def test_extract_lesson_records_normalizes_feedback_without_raw_evidence() -> None:
    feedback = EvaluationSummary(
        variant_id="candidate-a",
        dataset_split="validation",
        metrics={
            "score": 62.0,
            "A1_groundedness": 2.0,
            "evidence_compacted": True,
            "evidence_incomplete": True,
            "evidence_issues": [
                "tool output included SECRET_TOKEN=abc123 and a private transcript"
            ],
            "failed_gates": ["evidence_quality", "score_improvement"],
            "run_id": "run-a",
            "task_id": "task-a",
        },
    )

    lessons = extract_lesson_records(
        (feedback,),
        target_scope={"target_type": "skill", "target_id": "demo"},
    )

    assert [lesson.lesson_type for lesson in lessons] == [
        "failure_memory",
        "required_runtime_behavior",
    ]
    assert lessons[0].source_run_ids == ("run-a",)
    assert lessons[0].source_task_ids == ("task-a",)
    assert "evidence_quality" in lessons[0].metrics["failed_gates"]
    assert "artifact_first" in lessons[1].metrics["required_behaviors"]
    serialized = "\n".join(lesson.summary for lesson in lessons)
    assert "SECRET_TOKEN" not in serialized
    assert "abc123" not in serialized


def test_extract_lesson_records_records_success_memory_for_high_scoring_feedback() -> None:
    lessons = extract_lesson_records(
        (
            EvaluationSummary(
                variant_id="candidate-good",
                dataset_split="validation",
                metrics={
                    "score": 91.0,
                    "candidate_score": 91.0,
                    "baseline_score": 88.0,
                    "score_delta": 3.0,
                    "failed_gates": [],
                    "run_id": "run-b",
                    "task_id": "task-b",
                },
            ),
        ),
        target_scope={"target_type": "skill", "target_id": "demo"},
    )

    assert len(lessons) == 1
    assert lessons[0].lesson_type == "success_memory"
    assert lessons[0].confidence == "high"
    assert lessons[0].metrics["score"] == 91.0
    assert lessons[0].target_scope == {"target_type": "skill", "target_id": "demo"}
