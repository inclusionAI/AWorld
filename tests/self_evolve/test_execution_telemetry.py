from __future__ import annotations

import json
from pathlib import Path

import pytest

from aworld.self_evolve.concurrency import (
    SelfEvolveConcurrencyPolicy,
    SelfEvolveExecutionTelemetry,
)
from aworld.self_evolve.runner import optimize_from_cli_request


def test_execution_telemetry_aggregates_only_bounded_stage_metadata() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    telemetry.record(
        "evaluation",
        {
            "item_count": 2,
            "configured_concurrency": 2,
            "effective_concurrency": 2,
            "max_observed_concurrency": 2,
            "resource_serialized_count": 0,
            "queue_wait_seconds": 0.2,
            "execution_seconds": 0.8,
            "elapsed_seconds": 0.5,
            "prompt": "must-not-be-recorded",
        },
    )
    telemetry.record(
        "evaluation",
        {
            "item_count": 1,
            "configured_concurrency": 2,
            "effective_concurrency": 1,
            "max_observed_concurrency": 1,
            "resource_serialized_count": 1,
            "queue_wait_seconds": 0.1,
            "execution_seconds": 0.4,
            "elapsed_seconds": 0.4,
        },
    )

    report = telemetry.to_report()["evaluation"]

    assert report["batch_count"] == 2
    assert report["item_count"] == 3
    assert report["configured_concurrency"] == 2
    assert report["effective_concurrency"] == 2
    assert report["max_observed_concurrency"] == 2
    assert report["resource_serialized_count"] == 1
    assert report["queue_wait_seconds"] == pytest.approx(0.3)
    assert report["execution_seconds"] == pytest.approx(1.2)
    assert report["elapsed_seconds"] == pytest.approx(0.9)
    assert "prompt" not in json.dumps(report)


def test_execution_telemetry_aggregates_repair_accounting_and_bounded_usage() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    telemetry.record(
        "candidate_generation",
        {
            "item_count": 1,
            "repair_attempt_count": 1,
            "repair_success_count": 1,
            "repair_protocol_invalid_count": 0,
            "repair_infrastructure_failure_count": 0,
            "initial_queue_wait_seconds": 0.1,
            "initial_execution_seconds": 0.2,
            "repair_queue_wait_seconds": 0.3,
            "repair_execution_seconds": 0.4,
            "initial_discarded_count": 0,
            "repair_discarded_count": 0,
            "token_usage": {
                "prompt_tokens": 40,
                "completion_tokens": 20,
                "total_tokens": 60,
                "raw_response": "must-not-be-recorded",
            },
        },
    )

    report = telemetry.to_report()["candidate_generation"]

    assert report["repair_attempt_count"] == 1
    assert report["repair_success_count"] == 1
    assert report["initial_execution_seconds"] == pytest.approx(0.2)
    assert report["repair_execution_seconds"] == pytest.approx(0.4)
    assert report["token_usage"] == {
        "completion_tokens": 20,
        "prompt_tokens": 40,
        "total_tokens": 60,
    }
    assert "raw_response" not in json.dumps(report)


def test_execution_telemetry_retains_only_nonnegative_batch_cost_usage() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    telemetry.record(
        "replay",
        {"item_count": 1, "cost_usd": 1.25, "total_cost_usd": -1},
    )

    batch = telemetry.to_report()["replay"]["batches"][0]

    assert batch["cost_usd"] == "1.25"
    assert "total_cost_usd" not in batch


def test_all_ones_policy_is_the_serial_rollback() -> None:
    policy = SelfEvolveConcurrencyPolicy(
        max_total_concurrency=1,
        candidate_generation_concurrency=1,
        replay_concurrency=1,
        judge_concurrency=1,
        candidate_screening_concurrency=1,
    )

    for stage in (
        "candidate_generation",
        "candidate_screening",
        "replay",
        "evaluation",
    ):
        assert policy.effective_limit(stage, item_count=10) == 1


def test_optimize_report_separates_stage_telemetry_and_usage(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    dataset_path = tmp_path / "eval.jsonl"
    dataset_path.write_text(
        '{"case_id":"case-1","input":"demo"}\n',
        encoding="utf-8",
    )

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        dataset=str(dataset_path),
        apply_policy="proposal",
        concurrency_policy=SelfEvolveConcurrencyPolicy(
            max_total_concurrency=1,
            candidate_generation_concurrency=1,
            replay_concurrency=1,
            judge_concurrency=1,
        ),
    )
    report = json.loads(
        Path(summary["report_path"]).read_text(encoding="utf-8")
    )

    execution = report["execution"]
    assert execution["stages"]["candidate_generation"][
        "configured_concurrency"
    ] == 1
    assert execution["total_usage"]["candidate_generation_usage"][
        "scheduled_slots"
    ] >= 1
    assert execution["total_usage"]["replay_usage"] == {
        "scheduled_repetition_tasks": 0
    }
    assert "token_usage" in execution["total_usage"]
