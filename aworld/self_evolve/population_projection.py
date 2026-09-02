"""Population lifecycle and persistence projections."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from aworld.self_evolve.budget import (
    CandidateAttemptEvent,
    CandidateAttemptKey,
    CandidateAttemptStage,
    aggregate_candidate_attempts,
)
from aworld.self_evolve.history_support import (
    _non_negative_int,
    _non_negative_screening_float,
)
from aworld.self_evolve.sanitization import public_diagnostic_projection
from aworld.self_evolve.types import CandidateVariant


def _candidate_validation_report_for_persistence(value: object) -> object:
    return public_diagnostic_projection(value)


def _candidate_strategy_records(
    optimizer_diagnostics: list[dict[str, object]] | tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for item in optimizer_diagnostics:
        diagnostics = item.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        strategies = diagnostics.get("candidate_strategies")
        if not isinstance(strategies, list):
            continue
        for strategy in strategies:
            if isinstance(strategy, Mapping) and isinstance(
                strategy.get("candidate_id"), str
            ):
                records.append(dict(strategy))
    return records


def _population_report(
    *,
    all_candidates: list[CandidateVariant],
    iteration_reports: list[dict[str, object]],
    replay_candidate_limit: int,
    optimizer_diagnostics: list[dict[str, object]] | None = None,
    screening_reports: list[dict[str, object]] | None = None,
    attempt_events: Iterable[CandidateAttemptEvent] = (),
    budget_report: Mapping[str, object] | None = None,
    scheduler_decisions: Iterable[Mapping[str, object]] = (),
) -> dict[str, object] | None:
    attempt_events = tuple(attempt_events)
    if not all_candidates and not iteration_reports and not attempt_events:
        return None
    replayed_candidate_ids = [
        str(item.get("candidate_id"))
        for item in iteration_reports
        if isinstance(item.get("candidate_id"), str)
        and item.get("lifecycle_stage") == "authoritative_replay"
    ]
    report: dict[str, object] = {
        "generated_candidate_count": len(all_candidates),
        "generated_candidate_ids": [
            candidate.candidate_id for candidate in all_candidates
        ],
        "replayed_candidate_count": len(replayed_candidate_ids),
        "replayed_candidate_ids": replayed_candidate_ids,
        "replay_candidate_limit": replay_candidate_limit,
        "non_replayed_candidate_count": max(
            0,
            len(all_candidates) - len(set(replayed_candidate_ids)),
        ),
    }
    lifecycle: dict[str, object] = {
        "generated": {
            "candidate_count": len(all_candidates),
            "candidate_ids": [candidate.candidate_id for candidate in all_candidates],
        },
        "conformance": {
            "attempted_candidate_count": 0,
            "rejected_candidate_count": 0,
            "attempted_candidate_ids": [],
            "rejected_candidate_ids": [],
        },
        "screening": {
            "attempted_candidate_count": 0,
            "rejected_candidate_count": 0,
            "attempted_candidate_ids": [],
            "rejected_candidate_ids": [],
        },
        "authoritative_replay": {
            "attempted_candidate_count": len(replayed_candidate_ids),
            "attempted_candidate_ids": replayed_candidate_ids,
        },
    }
    if screening_reports:
        latest_validation = screening_reports[-1]
        latest_conformance = latest_validation.get("conformance")
        latest_screening = latest_validation.get("screening")
        if isinstance(latest_conformance, Mapping):
            report["conformance"] = _candidate_validation_report_for_persistence(
                latest_conformance
            )
        if isinstance(latest_screening, Mapping):
            report["screening"] = _candidate_validation_report_for_persistence(
                latest_screening
            )
        elif "conformance" not in latest_validation:
            report["screening"] = latest_validation
        if len(screening_reports) > 1:
            conformance_iterations = [
                _candidate_validation_report_for_persistence(item["conformance"])
                for item in screening_reports
                if isinstance(item.get("conformance"), Mapping)
            ]
            task_screening_iterations = [
                _candidate_validation_report_for_persistence(item["screening"])
                for item in screening_reports
                if isinstance(item.get("screening"), Mapping)
            ]
            if conformance_iterations:
                report["conformance_iterations"] = conformance_iterations
            if task_screening_iterations:
                report["screening_iterations"] = task_screening_iterations
        conformance_attempts = [
            attempt
            for validation in screening_reports
            for conformance in (validation.get("conformance"),)
            if isinstance(conformance, Mapping)
            for attempt in conformance.get("attempts", ())
            if isinstance(attempt, Mapping)
        ]
        screening_attempts = [
            attempt
            for validation in screening_reports
            for screening in (validation.get("screening"),)
            if isinstance(screening, Mapping)
            for attempt in screening.get("attempts", ())
            if isinstance(attempt, Mapping)
        ]
        screening_stage_reports = [
            screening
            for validation in screening_reports
            for screening in (validation.get("screening"),)
            if isinstance(screening, Mapping)
        ]
        termination_axis_counts: dict[str, int] = {}
        for screening in screening_stage_reports:
            raw_counts = screening.get("termination_budget_axis_counts")
            if not isinstance(raw_counts, Mapping):
                continue
            for axis, count in raw_counts.items():
                if not isinstance(axis, str):
                    continue
                termination_axis_counts[axis] = termination_axis_counts.get(
                    axis, 0
                ) + _non_negative_int(count)
        report["screening_execution"] = {
            "physical_pair_execution_count": sum(
                _non_negative_int(screening.get("physical_pair_execution_count"))
                for screening in screening_stage_reports
            ),
            "wall_seconds": sum(
                _non_negative_screening_float(screening.get("screening_wall_seconds"))
                for screening in screening_stage_reports
            ),
            "right_censored_batch_count": sum(
                int(screening.get("stopped_after_budget_censor") is True)
                for screening in screening_stage_reports
            ),
            "termination_budget_axis_counts": termination_axis_counts,
            "strategy_counts": dict(
                Counter(
                    str(screening.get("screening_strategy") or "unknown")
                    for screening in screening_stage_reports
                )
            ),
        }
        for stage_name, attempts in (
            ("conformance", conformance_attempts),
            ("screening", screening_attempts),
        ):
            attempted_ids = list(
                dict.fromkeys(
                    str(attempt.get("candidate_id"))
                    for attempt in attempts
                    if isinstance(attempt.get("candidate_id"), str)
                )
            )
            rejected_ids = list(
                dict.fromkeys(
                    str(attempt.get("candidate_id"))
                    for attempt in attempts
                    if isinstance(attempt.get("candidate_id"), str)
                    and attempt.get("passed") is False
                )
            )
            stage = lifecycle[stage_name]
            assert isinstance(stage, dict)
            stage.update(
                {
                    "attempted_candidate_count": len(attempted_ids),
                    "rejected_candidate_count": len(rejected_ids),
                    "attempted_candidate_ids": attempted_ids,
                    "rejected_candidate_ids": rejected_ids,
                }
            )
    stored_events = attempt_events
    terminal_reason_by_candidate: dict[str, str] = {}
    if stored_events:
        compatibility_lifecycle = lifecycle
        aggregate = aggregate_candidate_attempts(stored_events)
        grouped_events: dict[CandidateAttemptKey, list[CandidateAttemptEvent]] = {}
        for event in stored_events:
            grouped_events.setdefault(event.key, []).append(event)
        replayed_candidate_ids = list(
            dict.fromkeys(
                event.candidate_id
                for event in stored_events
                if event.stage is CandidateAttemptStage.PAIRED_REPLAY_STARTED
            )
        )
        for events in grouped_events.values():
            terminal = sorted(events, key=lambda item: item.sequence)[-1]
            if terminal.terminal and terminal.reason_code is not None:
                terminal_reason_by_candidate[terminal.candidate_id] = (
                    terminal.reason_code
                )
        report.update(
            {
                "generation_attempt_count": aggregate.attempt_count,
                "unique_candidate_count": aggregate.unique_candidate_count,
                "duplicate_attempt_count": aggregate.duplicate_attempt_count,
                "terminal_attempt_count": aggregate.terminal_attempt_count,
                "replayed_candidate_count": aggregate.paired_replay_started_count,
                "replayed_candidate_ids": replayed_candidate_ids,
                "paired_replay_started_count": (aggregate.paired_replay_started_count),
                "paired_replay_completed_count": (
                    aggregate.paired_replay_completed_count
                ),
                "paired_replay_comparable_count": (
                    aggregate.paired_replay_comparable_count
                ),
                "non_replayed_candidate_count": max(
                    0,
                    aggregate.unique_candidate_count - len(set(replayed_candidate_ids)),
                ),
            }
        )
        lifecycle = aggregate.to_dict()
        report["compatibility_aliases"] = {
            "generated_candidate_count": {
                "value": len(all_candidates),
                "semantic": "canonical_unique_candidates_persisted",
            },
            "replayed_candidate_count": {
                "value": aggregate.paired_replay_started_count,
                "semantic": "paired_replay_started_attempts",
            },
            "legacy_stage_details": compatibility_lifecycle,
        }
    strategy_records = _candidate_strategy_records(optimizer_diagnostics or ())
    if strategy_records:
        replayed_set = set(replayed_candidate_ids)
        non_replayed: list[dict[str, object]] = []
        for record in strategy_records:
            candidate_id = str(record.get("candidate_id"))
            if candidate_id in replayed_set:
                continue
            terminal_reason = terminal_reason_by_candidate.get(candidate_id)
            item = dict(record)
            if terminal_reason is not None:
                item["terminal_reason_code"] = terminal_reason
                if "budget_denied" in terminal_reason:
                    item["not_replayed_reason"] = "not_replayed_due_to_budget"
            non_replayed.append(item)
        if non_replayed:
            report["non_replayed_candidate_strategies"] = non_replayed
    report["lifecycle"] = lifecycle
    if budget_report is not None:
        report["budget"] = dict(budget_report)
    scheduler_payload = [dict(item) for item in scheduler_decisions]
    if scheduler_payload:
        report["scheduler_decisions"] = scheduler_payload
    return report
