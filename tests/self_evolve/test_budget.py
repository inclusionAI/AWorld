from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal

import pytest

from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetDecision,
    BudgetDecisionReason,
    BudgetEstimateConfidence,
    BudgetEstimateSource,
    BudgetStage,
    BudgetUsage,
    CandidateAttemptEvent,
    CandidateAttemptAggregate,
    CandidateAttemptKey,
    CandidateAttemptStage,
    RepairFrontier,
    RunBudgetLedger,
    ScheduledCandidateSlot,
    ScheduledSlotRole,
    SchedulerDecision,
    SchedulerState,
    StageAwareCandidateScheduler,
    StageBudgetEstimate,
    StageWorkload,
    aggregate_candidate_attempts,
    validate_candidate_attempt_lifecycle,
)
from aworld.self_evolve.failure_events import FailureOwner, FailureScope


def _estimate(
    *,
    item_id: str = "item-1",
    tokens: int | None = 200,
    cost: str | None = "2",
    wall: str | None = "10",
    stage: BudgetStage = BudgetStage.CANDIDATE_GENERATION,
) -> StageBudgetEstimate:
    return StageBudgetEstimate(
        stage=stage,
        item_id=item_id,
        tokens=tokens,
        cost_usd=Decimal(cost) if cost is not None else None,
        wall_seconds=Decimal(wall) if wall is not None else None,
        source=BudgetEstimateSource.CONFIGURED_COLD_START,
        confidence=BudgetEstimateConfidence.LOW,
    )


def _ledger() -> RunBudgetLedger:
    return RunBudgetLedger(
        BudgetCeilings(
            total_tokens=1_000,
            total_cost_usd=Decimal("10"),
            wall_seconds=Decimal("100"),
        )
    )


def test_ledger_reserves_debits_actual_releases_and_roundtrips() -> None:
    ledger = _ledger()
    first = ledger.reserve(_estimate())

    assert first.allowed is True
    assert first.reason_code is BudgetDecisionReason.RESERVED
    assert BudgetDecision.from_dict(first.to_dict()) == first
    assert ledger.remaining().to_dict() == {
        "tokens": 800,
        "cost_usd": "8",
        "wall_seconds": "90",
    }
    assert len(ledger.outstanding_reservations) == 1
    changed_same_item = ledger.reserve(_estimate(tokens=300))
    assert changed_same_item.allowed is False
    assert changed_same_item.reason_code is BudgetDecisionReason.RESERVATION_EXISTS
    assert len(ledger.outstanding_reservations) == 1

    debit = ledger.debit_actual(
        first.reservation_id or "",
        BudgetUsage(tokens=250, cost_usd=Decimal("3"), wall_seconds=Decimal("12")),
    )
    assert debit.reservation_overrun == BudgetUsage(
        tokens=50,
        cost_usd=Decimal("1"),
        wall_seconds=Decimal("2"),
    )
    second = ledger.reserve(_estimate(item_id="item-2", tokens=100, cost="1", wall="5"))
    assert second.allowed is True
    released = ledger.release(second.reservation_id or "")
    assert released.estimate.item_id == "item-2"
    assert ledger.remaining().tokens == 750
    assert RunBudgetLedger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()


def test_ledger_unknown_estimate_fails_closed_unless_backend_proves_zero() -> None:
    ledger = _ledger()
    unknown = StageBudgetEstimate(
        stage=BudgetStage.JUDGE,
        item_id="judge-unknown",
        tokens=None,
        cost_usd=None,
        wall_seconds=None,
    )

    denied = ledger.reserve(unknown)
    assert denied.allowed is False
    assert denied.reason_code is BudgetDecisionReason.UNKNOWN_ESTIMATE
    assert ledger.outstanding_reservations == ()
    unbounded = RunBudgetLedger(BudgetCeilings(None, None, None))
    assert unbounded.reserve(unknown).reason_code is BudgetDecisionReason.UNKNOWN_ESTIMATE

    proven_zero = ledger.estimate_next(
        stage=BudgetStage.JUDGE,
        item_id="judge-local",
        backend_proven_zero=True,
    )
    allowed = ledger.reserve(proven_zero)
    assert allowed.allowed is True
    assert proven_zero.source is BudgetEstimateSource.BACKEND_PROVEN_ZERO


def test_ledger_denial_is_deterministic_and_does_not_mutate_state() -> None:
    ledger = _ledger()
    estimate = _estimate(tokens=1_001, cost="1", wall="1")

    first = ledger.reserve(estimate)
    second = ledger.reserve(estimate)

    assert first.to_dict() == second.to_dict()
    assert first.reason_code is BudgetDecisionReason.TOKEN_BUDGET_EXHAUSTED
    assert ledger.total_spent() == BudgetUsage()
    assert ledger.outstanding_reservations == ()


@pytest.mark.parametrize(
    "tamper",
    (
        "remaining",
        "overrun",
        "reservation",
        "spent",
        "observed",
        "debit_observation",
    ),
)
def test_ledger_serialization_rejects_derived_summary_and_reservation_tamper(
    tamper: str,
) -> None:
    ledger = _ledger()
    decision = ledger.reserve(_estimate())
    assert decision.allowed is True
    if tamper in {"spent", "observed", "debit_observation"}:
        ledger.debit_actual(
            decision.reservation_id or "",
            BudgetUsage(
                tokens=200,
                cost_usd=Decimal("2"),
                wall_seconds=Decimal("10"),
            ),
        )
    payload = deepcopy(ledger.to_dict())
    if tamper == "remaining":
        payload["remaining"]["tokens"] = 999  # type: ignore[index]
    elif tamper == "overrun":
        payload["overrun"]["tokens"] = 1  # type: ignore[index]
    elif tamper == "reservation":
        payload["outstanding_reservations"][0]["usage"]["tokens"] = 201  # type: ignore[index]
    elif tamper == "spent":
        payload["spent_by_stage"]["candidate_generation"]["tokens"] = 201  # type: ignore[index]
    elif tamper == "observed":
        payload["observed_by_stage"]["candidate_generation"][0][  # type: ignore[index]
            "tokens"
        ] = 201
    else:
        payload["debit_observations"][0]["actual"]["tokens"] = 201  # type: ignore[index]

    with pytest.raises(
        ValueError,
        match=(
            "remaining|overrun|reservation usage|spent_by_stage|"
            "observed_by_stage|debit observation"
        ),
    ):
        RunBudgetLedger.from_dict(payload)


def test_actual_usage_can_overrun_reservation_and_run_ceiling() -> None:
    ledger = RunBudgetLedger(
        BudgetCeilings(total_tokens=100, total_cost_usd=Decimal("10"))
    )
    decision = ledger.reserve(
        _estimate(tokens=50, cost="1", wall="0", stage=BudgetStage.PAIRED_REPLAY)
    )
    assert decision.allowed is True

    debit = ledger.debit_actual(
        decision.reservation_id or "",
        BudgetUsage(tokens=150, cost_usd=Decimal("2")),
    )
    assert debit.reservation_overrun.tokens == 100
    assert debit.ceiling_overrun.tokens == 50
    assert ledger.remaining().tokens == 0


def test_observed_estimate_uses_robust_upper_value_not_single_outlier() -> None:
    ledger = RunBudgetLedger(
        BudgetCeilings(total_tokens=10_000, total_cost_usd=Decimal("100"))
    )
    for index, tokens in enumerate((10, 1_000, 11, 12, 13)):
        reservation = ledger.reserve(
            _estimate(
                item_id=f"sample-{index}",
                tokens=1_000,
                cost="1",
                wall="0",
            )
        )
        assert reservation.allowed is True
        ledger.debit_actual(
            reservation.reservation_id or "",
            BudgetUsage(tokens=tokens, cost_usd=Decimal("1")),
        )

    statistics_value = ledger.estimate_statistics(BudgetStage.CANDIDATE_GENERATION)
    assert statistics_value is not None
    assert statistics_value.median.tokens == 12
    assert statistics_value.upper_conservative.tokens == 13
    next_estimate = ledger.estimate_next(
        stage=BudgetStage.CANDIDATE_GENERATION,
        item_id="next-batch",
        units=3,
    )
    assert next_estimate.tokens == 39
    assert next_estimate.source is BudgetEstimateSource.OBSERVED_ROBUST


@pytest.mark.parametrize(
    "stage",
    (
        BudgetStage.CANDIDATE_GENERATION,
        BudgetStage.CONFORMANCE,
        BudgetStage.PAIRED_REPLAY,
        BudgetStage.EVALUATION,
        BudgetStage.JUDGE,
    ),
)
@pytest.mark.parametrize(
    ("first_units", "next_units", "expected_tokens"),
    ((1, 3, 30), (3, 1, 10), (3, 3, 30)),
)
def test_batch_actual_history_is_normalized_per_unit_before_future_scaling(
    stage: BudgetStage,
    first_units: int,
    next_units: int,
    expected_tokens: int,
) -> None:
    ledger = RunBudgetLedger(
        BudgetCeilings(
            total_tokens=10_000,
            total_cost_usd=Decimal("1000"),
            wall_seconds=Decimal("1000"),
        )
    )
    per_unit = BudgetUsage(
        tokens=10,
        cost_usd=Decimal("1.5"),
        wall_seconds=Decimal("2.25"),
    )
    first = ledger.reserve(
        ledger.estimate_next(
            stage=stage,
            item_id="first",
            units=first_units,
            cold_start_per_unit=per_unit,
        )
    )
    assert first.allowed is True
    aggregate_actual = per_unit.scale(first_units)
    debit = ledger.debit_actual(
        first.reservation_id or "",
        aggregate_actual,
    )

    assert debit.actual == aggregate_actual
    assert debit.observed_per_unit == per_unit
    assert ledger.spent_by_stage[stage] == aggregate_actual
    next_estimate = ledger.estimate_next(
        stage=stage,
        item_id="next",
        units=next_units,
        cold_start_per_unit=BudgetUsage(tokens=999),
    )
    assert next_estimate.tokens == expected_tokens
    assert next_estimate.cost_usd == per_unit.cost_usd * next_units
    assert next_estimate.wall_seconds == per_unit.wall_seconds * next_units
    assert next_estimate.source is BudgetEstimateSource.OBSERVED_ROBUST
    assert RunBudgetLedger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()


def test_batch_token_observation_rounds_up_without_rounding_aggregate_spend() -> None:
    ledger = _ledger()
    decision = ledger.reserve(
        ledger.estimate_next(
            stage=BudgetStage.PAIRED_REPLAY,
            item_id="three-members",
            units=3,
            cold_start_per_unit=BudgetUsage(
                tokens=10,
                cost_usd=Decimal("1"),
                wall_seconds=Decimal("1"),
            ),
        )
    )
    debit = ledger.debit_actual(
        decision.reservation_id or "",
        BudgetUsage(
            tokens=31,
            cost_usd=Decimal("3"),
            wall_seconds=Decimal("6"),
        ),
    )

    assert debit.observed_per_unit == BudgetUsage(
        tokens=11,
        cost_usd=Decimal("1"),
        wall_seconds=Decimal("2"),
    )
    assert ledger.total_spent().tokens == 31


def test_debit_observation_journal_rejects_removed_prefix() -> None:
    ledger = _ledger()
    for index in range(2):
        decision = ledger.reserve(_estimate(item_id=f"item-{index}"))
        ledger.debit_actual(
            decision.reservation_id or "",
            BudgetUsage(
                tokens=200,
                cost_usd=Decimal("2"),
                wall_seconds=Decimal("10"),
            ),
        )
    payload = deepcopy(ledger.to_dict())
    del payload["debit_observations"][0]  # type: ignore[index]

    with pytest.raises(ValueError, match="sequence|chain"):
        RunBudgetLedger.from_dict(payload)


def test_stage_workload_is_cardinality_monotonic_and_shape_aware() -> None:
    one = StageWorkload(
        case_count=1,
        repetitions=2,
        distinct_conformance_shape_count=1,
    )
    three_same_shape = StageWorkload(
        case_count=3,
        repetitions=2,
        distinct_conformance_shape_count=1,
    )
    three_two_shapes = StageWorkload(
        case_count=3,
        repetitions=2,
        distinct_conformance_shape_count=2,
    )
    one_case_two_shapes = StageWorkload(
        case_count=1,
        repetitions=2,
        distinct_conformance_shape_count=2,
    )

    assert one.units_for(BudgetStage.PAIRED_REPLAY) == 2
    assert three_same_shape.units_for(BudgetStage.PAIRED_REPLAY) == 6
    assert one.units_for(BudgetStage.CONFORMANCE) == 1
    assert three_same_shape.units_for(BudgetStage.CONFORMANCE) == 1
    assert three_two_shapes.units_for(BudgetStage.CONFORMANCE) == 2
    assert one_case_two_shapes.units_for(BudgetStage.CONFORMANCE) == 2
    assert three_two_shapes.units_for(BudgetStage.SCREENING) == 1


def _event(
    key: CandidateAttemptKey,
    sequence: int,
    stage: CandidateAttemptStage,
    *,
    candidate_id: str = "candidate-same",
    reason_code: str | None = None,
    usage: BudgetUsage | None = None,
) -> CandidateAttemptEvent:
    return CandidateAttemptEvent(
        key=key,
        sequence=sequence,
        stage=stage,
        candidate_id=candidate_id,
        reason_code=reason_code,
        usage=usage or BudgetUsage(),
    )


def _selected_attempt(key: CandidateAttemptKey) -> tuple[CandidateAttemptEvent, ...]:
    stages = (
        CandidateAttemptStage.GENERATED,
        CandidateAttemptStage.UNIQUE,
        CandidateAttemptStage.LOCAL_GATES,
        CandidateAttemptStage.ADAPTATION,
        CandidateAttemptStage.CONFORMANCE,
        CandidateAttemptStage.SCREENING,
        CandidateAttemptStage.PAIRED_REPLAY_STARTED,
        CandidateAttemptStage.PAIRED_REPLAY_COMPLETED,
        CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE,
        CandidateAttemptStage.EVALUATION,
        CandidateAttemptStage.SELECTED,
    )
    return tuple(
        _event(
            key,
            index,
            stage,
            reason_code="selected_by_policy" if stage is CandidateAttemptStage.SELECTED else None,
            usage=(
                BudgetUsage(tokens=10, cost_usd=Decimal("0.1"))
                if stage is CandidateAttemptStage.PAIRED_REPLAY_COMPLETED
                else None
            ),
        )
        for index, stage in enumerate(stages)
    )


def test_attempt_lifecycle_has_stable_ids_and_strict_transitions() -> None:
    events = _selected_attempt(CandidateAttemptKey("run-1", 0, 0))

    validate_candidate_attempt_lifecycle(events, require_terminal=True)
    assert CandidateAttemptEvent.from_dict(events[0].to_dict()) == events[0]
    assert events[0].event_id == CandidateAttemptEvent.from_dict(
        events[0].to_dict()
    ).event_id

    with pytest.raises(ValueError, match="illegal candidate attempt transition"):
        validate_candidate_attempt_lifecycle(
            (
                _event(events[0].key, 0, CandidateAttemptStage.GENERATED),
                _event(
                    events[0].key,
                    1,
                    CandidateAttemptStage.PAIRED_REPLAY_STARTED,
                ),
            )
        )
    with pytest.raises(ValueError, match="requires a reason code"):
        _event(events[0].key, 1, CandidateAttemptStage.REJECTED)
    tampered = events[0].to_dict()
    tampered["event_id"] = "candidate-attempt-event-sha256-" + ("0" * 64)
    with pytest.raises(ValueError, match="event_id does not match"):
        CandidateAttemptEvent.from_dict(tampered)


def test_attempt_aggregation_keeps_duplicate_attempts_and_exact_stage_counts() -> None:
    first_key = CandidateAttemptKey("run-1", 0, 0)
    second_key = CandidateAttemptKey("run-1", 1, 0)
    first = _selected_attempt(first_key)
    duplicate = (
        _event(second_key, 0, CandidateAttemptStage.GENERATED),
        _event(second_key, 1, CandidateAttemptStage.DUPLICATE_FILTERED),
        _event(
            second_key,
            2,
            CandidateAttemptStage.NOT_RUN,
            reason_code="duplicate_candidate",
        ),
    )

    aggregate = aggregate_candidate_attempts((*first, *duplicate))
    assert aggregate.attempt_count == 2
    assert aggregate.unique_candidate_count == 1
    assert aggregate.duplicate_attempt_count == 1
    assert aggregate.paired_replay_started_count == 1
    assert aggregate.paired_replay_completed_count == 1
    assert aggregate.paired_replay_comparable_count == 1
    assert aggregate.terminal_reason_counts == {
        "duplicate_candidate": 1,
        "selected_by_policy": 1,
    }
    assert aggregate.per_stage_usage[
        CandidateAttemptStage.PAIRED_REPLAY_COMPLETED.value
    ].tokens == 10
    assert first[0].event_id != duplicate[0].event_id
    assert CandidateAttemptAggregate.from_dict(aggregate.to_dict()) == aggregate


def test_attempt_aggregation_rejects_incomplete_lifecycle() -> None:
    incomplete = _selected_attempt(CandidateAttemptKey("run-incomplete", 0, 0))[:-1]

    with pytest.raises(ValueError, match="lifecycle is not terminal"):
        aggregate_candidate_attempts(incomplete)


def test_attempt_aggregation_counts_are_monotonic_when_attempt_is_added() -> None:
    first = _selected_attempt(CandidateAttemptKey("run-1", 0, 0))
    baseline = aggregate_candidate_attempts(first)
    blocked_key = CandidateAttemptKey("run-1", 0, 1)
    blocked = (
        _event(blocked_key, 0, CandidateAttemptStage.GENERATED, candidate_id="candidate-2"),
        _event(
            blocked_key,
            1,
            CandidateAttemptStage.BLOCKED,
            candidate_id="candidate-2",
            reason_code="shared_run_blocked",
        ),
    )
    expanded = aggregate_candidate_attempts((*first, *blocked))

    assert expanded.attempt_count >= baseline.attempt_count
    for stage, count in baseline.stage_counts.items():
        assert expanded.stage_counts[stage] >= count


def _frontier(
    semantic_key: str,
    *,
    progress: int = 1,
    owner: FailureOwner = FailureOwner.CANDIDATE,
    scope: FailureScope = FailureScope.CANDIDATE,
) -> RepairFrontier:
    return RepairFrontier(
        semantic_key=semantic_key,
        progress=progress,
        owner=owner,
        scope=scope,
        repairable=True,
    )


def test_scheduler_initial_then_stable_frontier_uses_one_focused_slot() -> None:
    scheduler = StageAwareCandidateScheduler(exploration_population=3)
    initial = scheduler.schedule(state=SchedulerState(), frontiers=())
    assert len(initial.slots) == 3
    assert all(
        slot.role is ScheduledSlotRole.INITIAL_EXPLORATION for slot in initial.slots
    )

    first_repair = scheduler.schedule(
        state=initial.state,
        frontiers=(_frontier("semantic-a"),),
        diverse_budget_available=True,
    )
    assert [slot.role for slot in first_repair.slots] == [
        ScheduledSlotRole.FOCUSED_REPAIR,
        ScheduledSlotRole.DIVERSE_EXPLORATION,
    ]
    stable = scheduler.schedule(
        state=first_repair.state,
        frontiers=(_frontier("semantic-a"),),
        diverse_budget_available=True,
    )
    assert [slot.role for slot in stable.slots] == [
        ScheduledSlotRole.FOCUSED_REPAIR
    ]
    assert SchedulerDecision.from_dict(stable.to_dict()) == stable


@pytest.mark.parametrize(
    "frontiers",
    [
        (_frontier("semantic-a", progress=2),),
        (_frontier("semantic-a"), _frontier("semantic-b")),
    ],
)
def test_scheduler_new_or_progressed_frontier_can_open_one_diverse_slot(
    frontiers: tuple[RepairFrontier, ...],
) -> None:
    scheduler = StageAwareCandidateScheduler(exploration_population=4)
    state = SchedulerState(
        initial_exploration_scheduled=True,
        frontier_progress={"semantic-a": 1},
    )

    decision = scheduler.schedule(
        state=state,
        frontiers=frontiers,
        diverse_budget_available=True,
    )
    assert len(decision.slots) == 2
    assert decision.slots[1].role is ScheduledSlotRole.DIVERSE_EXPLORATION


def test_scheduler_shared_blocking_event_stops_but_candidate_event_does_not() -> None:
    scheduler = StageAwareCandidateScheduler(exploration_population=2)
    state = SchedulerState(initial_exploration_scheduled=True)
    shared = scheduler.schedule(
        state=state,
        frontiers=(
            _frontier(
                "shared-semantic",
                owner=FailureOwner.INFRASTRUCTURE,
                scope=FailureScope.SHARED_RUN,
            ),
        ),
    )
    assert shared.stop is True
    assert shared.slots == ()

    candidate = scheduler.schedule(
        state=state,
        frontiers=(_frontier("candidate-semantic"),),
    )
    assert candidate.stop is False
    assert len(candidate.slots) == 1


def test_scheduler_fails_closed_on_untyped_frontier_and_budget_denial() -> None:
    scheduler = StageAwareCandidateScheduler(exploration_population=2)
    state = SchedulerState(initial_exploration_scheduled=True)
    with pytest.raises(TypeError, match="frontiers must be typed"):
        scheduler.schedule(
            state=state,
            frontiers=({"semantic_key": "raw"},),  # type: ignore[arg-type]
        )

    denied = scheduler.schedule(
        state=state,
        frontiers=(_frontier("semantic-a"),),
        focused_budget_available=False,
        diverse_budget_available=True,
    )
    assert denied.slots == ()
    assert denied.stop is False
    assert denied.reason_code == "focused_budget_denied"
    assert json.dumps(denied.to_dict(), sort_keys=True)


def test_scheduler_allows_exactly_one_bounded_untyped_exploration_slot() -> None:
    scheduler = StageAwareCandidateScheduler(exploration_population=3)
    state = SchedulerState(initial_exploration_scheduled=True)

    bounded = scheduler.schedule(
        state=state,
        frontiers=(),
        untyped_feedback_present=True,
    )

    assert bounded.reason_code == "bounded_exploration_without_typed_frontier"
    assert len(bounded.slots) == 1
    assert bounded.slots[0].role is ScheduledSlotRole.BOUNDED_EXPLORATION
    assert bounded.slots[0].semantic_key is None
    exhausted = scheduler.schedule(
        state=bounded.state,
        frontiers=(),
        untyped_feedback_present=True,
    )
    assert exhausted.slots == ()
    assert exhausted.reason_code == "no_repairable_frontier"


def test_focused_repair_slot_requires_typed_semantic_key() -> None:
    with pytest.raises(ValueError, match="requires a typed semantic key"):
        ScheduledCandidateSlot(
            slot=0,
            role=ScheduledSlotRole.FOCUSED_REPAIR,
        )


def test_candidate_lifecycle_allows_real_optional_stage_skips() -> None:
    key = CandidateAttemptKey("run-optional", 0, 0)
    events = (
        CandidateAttemptEvent(key, 0, CandidateAttemptStage.GENERATED, "candidate-a"),
        CandidateAttemptEvent(key, 1, CandidateAttemptStage.UNIQUE, "candidate-a"),
        CandidateAttemptEvent(key, 2, CandidateAttemptStage.LOCAL_GATES, "candidate-a"),
        CandidateAttemptEvent(
            key,
            3,
            CandidateAttemptStage.SELECTED,
            "candidate-a",
            reason_code="candidate_selected",
        ),
    )

    validate_candidate_attempt_lifecycle(events)


def test_candidate_lifecycle_preserves_more_shapes_than_trajectory_cases() -> None:
    key = CandidateAttemptKey("run-shapes", 0, 0)
    conformance = CandidateAttemptEvent(
        key,
        3,
        CandidateAttemptStage.CONFORMANCE,
        "candidate-a",
        case_count=1,
        distinct_conformance_shape_count=2,
    )
    events = (
        CandidateAttemptEvent(key, 0, CandidateAttemptStage.GENERATED, "candidate-a"),
        CandidateAttemptEvent(key, 1, CandidateAttemptStage.UNIQUE, "candidate-a"),
        CandidateAttemptEvent(key, 2, CandidateAttemptStage.LOCAL_GATES, "candidate-a"),
        conformance,
        CandidateAttemptEvent(
            key,
            4,
            CandidateAttemptStage.REJECTED,
            "candidate-a",
            reason_code="candidate_validation_rejected",
        ),
    )

    validate_candidate_attempt_lifecycle(events)
    aggregate = aggregate_candidate_attempts(events)
    assert aggregate.max_case_count == 1
    assert aggregate.max_distinct_conformance_shape_count == 2
