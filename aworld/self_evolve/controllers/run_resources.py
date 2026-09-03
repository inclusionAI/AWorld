"""Run-scoped budget, attempt, and failure-cleanup resources."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetEstimateConfidence,
    BudgetEstimateSource,
    BudgetStage,
    BudgetUsage,
    BudgetUsageCompleteness,
    BudgetUsageObservation,
    CandidateAttemptEvent,
    CandidateAttemptKey,
    CandidateAttemptStage,
    RunBudgetLedger,
    candidate_attempt_terminal_stage,
)
from aworld.self_evolve.measurement import MeasurementUsage
from aworld.self_evolve.store import FilesystemSelfEvolveStore


@dataclass
class RunBudgetContext:
    ledger: RunBudgetLedger
    cold_start_by_stage: Mapping[BudgetStage, BudgetUsage | None]
    backend_proven_zero_by_stage: Mapping[BudgetStage, bool] = field(
        default_factory=dict
    )
    decisions: list[dict[str, object]] = field(default_factory=list)
    debits: list[dict[str, object]] = field(default_factory=list)
    releases: list[dict[str, object]] = field(default_factory=list)

    def estimate(
        self,
        stage: BudgetStage,
        item_id: str,
        *,
        units: int = 1,
        backend_proven_zero: bool | None = None,
    ):
        return self.ledger.estimate_next(
            stage=stage,
            item_id=item_id,
            units=units,
            cold_start_per_unit=self.cold_start_by_stage.get(stage),
            backend_proven_zero=(
                backend_proven_zero
                if backend_proven_zero is not None
                else self.backend_proven_zero_by_stage.get(stage) is True
            ),
        )

    def can_fit(self, stage: BudgetStage, item_id: str, *, units: int = 1) -> bool:
        if self.ledger.ceilings.is_unbounded:
            return True
        estimate = self.estimate(stage, item_id, units=units)
        usage = estimate.resolved_usage()
        if usage is None:
            return False
        remaining = self.ledger.remaining()
        return bool(
            (remaining.tokens is None or usage.tokens <= remaining.tokens)
            and (remaining.cost_usd is None or usage.cost_usd <= remaining.cost_usd)
            and (
                remaining.wall_seconds is None
                or usage.wall_seconds <= remaining.wall_seconds
            )
        )

    def can_fit_workflow(
        self,
        work: Iterable[tuple[BudgetStage, str, int]],
    ) -> bool:
        if self.ledger.ceilings.is_unbounded:
            return True
        required = BudgetUsage()
        for stage, item_id, units in work:
            usage = self.estimate(stage, item_id, units=units).resolved_usage()
            if usage is None:
                return False
            required += usage
        remaining = self.ledger.remaining()
        return bool(
            (remaining.tokens is None or required.tokens <= remaining.tokens)
            and (remaining.cost_usd is None or required.cost_usd <= remaining.cost_usd)
            and (
                remaining.wall_seconds is None
                or required.wall_seconds <= remaining.wall_seconds
            )
        )

    def reserve(
        self,
        stage: BudgetStage,
        item_id: str,
        *,
        units: int = 1,
        backend_proven_zero: bool | None = None,
        request_derived_tokens: int | None = None,
    ) -> BudgetDecision:
        estimate = self.estimate(
            stage,
            item_id,
            units=units,
            backend_proven_zero=backend_proven_zero,
        )
        if request_derived_tokens is not None:
            if isinstance(request_derived_tokens, bool) or request_derived_tokens < 0:
                raise ValueError("request_derived_tokens must be non-negative")
            observed_estimate = estimate.source in {
                BudgetEstimateSource.OBSERVED_ROBUST,
                BudgetEstimateSource.OBSERVED_LOWER_BOUND,
            }
            resolved_tokens = (
                max(request_derived_tokens, estimate.tokens or 0)
                if observed_estimate
                else request_derived_tokens
            )
            estimate = replace(
                estimate,
                tokens=resolved_tokens,
                source=(
                    estimate.source
                    if observed_estimate
                    and (estimate.tokens or 0) >= request_derived_tokens
                    else BudgetEstimateSource.REQUEST_DERIVED
                ),
                confidence=(
                    estimate.confidence
                    if observed_estimate
                    and (estimate.tokens or 0) >= request_derived_tokens
                    else BudgetEstimateConfidence.MEDIUM
                ),
                backend_proven_zero=False,
            )
        decision = self.ledger.reserve(estimate)
        self.decisions.append(decision.to_dict())
        return decision

    def debit(
        self,
        decision: BudgetDecision,
        *,
        tokens: int | None = None,
        cost_usd: Decimal | None = None,
        wall_seconds: Decimal | None = None,
        usage_observation: BudgetUsageObservation | None = None,
        actual_source: str,
    ) -> None:
        if not decision.allowed or decision.reservation_id is None:
            return
        if usage_observation is not None and any(
            value is not None for value in (tokens, cost_usd, wall_seconds)
        ):
            raise ValueError(
                "usage_observation cannot be combined with dimension arguments"
            )
        observation = usage_observation or BudgetUsageObservation(
            known_lower_bound=BudgetUsage(
                tokens=0 if tokens is None else tokens,
                cost_usd=Decimal("0") if cost_usd is None else cost_usd,
                wall_seconds=(Decimal("0") if wall_seconds is None else wall_seconds),
            ),
            completeness=BudgetUsageCompleteness(
                tokens=tokens is not None,
                cost_usd=cost_usd is not None,
                wall_seconds=wall_seconds is not None,
            ),
        )
        result = self.ledger.debit_actual(
            decision.reservation_id,
            observation.known_lower_bound,
            actual_completeness=observation.completeness,
        )
        self.debits.append({**result.to_dict(), "actual_source": actual_source})

    def release(self, decision: BudgetDecision, *, reason_code: str) -> None:
        if not decision.allowed or decision.reservation_id is None:
            return
        reservation = self.ledger.release(decision.reservation_id)
        self.releases.append({**reservation.to_dict(), "reason_code": reason_code})

    def release_all(self, *, reason_code: str) -> None:
        for reservation in tuple(self.ledger.outstanding_reservations):
            released = self.ledger.release(reservation.reservation_id)
            self.releases.append({**released.to_dict(), "reason_code": reason_code})

    def release_all_best_effort(self, *, reason_code: str) -> None:
        """Release every surviving reservation without masking a run exception."""

        for reservation in tuple(self.ledger.outstanding_reservations):
            try:
                released = self.ledger.release(reservation.reservation_id)
            except BaseException:
                continue
            self.releases.append({**released.to_dict(), "reason_code": reason_code})

    def to_dict(self) -> dict[str, object]:
        return {
            "budget_mode": self.ledger.ceilings.budget_mode,
            "ledger": self.ledger.to_dict(),
            "decisions": list(self.decisions),
            "debits": list(self.debits),
            "releases": list(self.releases),
        }


def remaining_measurement_budget(
    context: RunBudgetContext,
) -> MeasurementUsage:
    remaining = context.ledger.remaining()
    return MeasurementUsage(
        tokens=remaining.tokens,
        cost_usd=(
            float(remaining.cost_usd) if remaining.cost_usd is not None else None
        ),
        wall_seconds=(
            float(remaining.wall_seconds)
            if remaining.wall_seconds is not None
            else None
        ),
    )


@dataclass
class CandidateAttemptTracker:
    store: FilesystemSelfEvolveStore
    run_id: str
    _events: dict[CandidateAttemptKey, list[CandidateAttemptEvent]] = field(
        default_factory=dict
    )
    _candidate_keys: dict[str, CandidateAttemptKey] = field(default_factory=dict)

    def start(
        self,
        *,
        iteration: int,
        slot: int,
        candidate_id: str,
        usage: BudgetUsage | None = None,
    ) -> CandidateAttemptKey:
        key = CandidateAttemptKey(self.run_id, iteration, slot)
        self._append(
            key,
            CandidateAttemptStage.GENERATED,
            candidate_id=candidate_id,
            usage=usage,
        )
        self._candidate_keys.setdefault(candidate_id, key)
        return key

    def key_for_candidate(self, candidate_id: str) -> CandidateAttemptKey | None:
        return self._candidate_keys.get(candidate_id)

    def last_stage(self, key: CandidateAttemptKey) -> CandidateAttemptStage:
        return self._events[key][-1].stage

    def terminal(self, key: CandidateAttemptKey) -> bool:
        return self._events[key][-1].terminal

    def has_stage(
        self,
        key: CandidateAttemptKey,
        *stages: CandidateAttemptStage,
    ) -> bool:
        expected = set(stages)
        return any(event.stage in expected for event in self._events.get(key, ()))

    def finalize_open(self, *, reason_code: str) -> None:
        for key in sorted(self._events):
            if not self.terminal(key):
                stage = candidate_attempt_terminal_stage(
                    self.last_stage(key)
                )
                self.emit(
                    key,
                    stage,
                    reason_code=(
                        reason_code
                        if stage is CandidateAttemptStage.NOT_RUN
                        else "run_terminated_after_candidate_execution"
                    ),
                )

    def finalize_evaluated(
        self,
        key: CandidateAttemptKey,
        *,
        status: str,
        infrastructure_failure: bool = False,
    ) -> None:
        """Close an evaluated attempt even when a controller omitted its event."""

        if self.terminal(key):
            return
        preferred = (
            CandidateAttemptStage.SELECTED
            if status == "accepted"
            else (
                CandidateAttemptStage.PREREQUISITE_READY
                if status == "prerequisite"
                else (
                    CandidateAttemptStage.BLOCKED
                    if infrastructure_failure or status == "blocked"
                    else CandidateAttemptStage.REJECTED
                )
            )
        )
        stage = candidate_attempt_terminal_stage(
            self.last_stage(key),
            preferred=preferred,
        )
        reason_code = {
            CandidateAttemptStage.SELECTED: "candidate_selected",
            CandidateAttemptStage.PREREQUISITE_READY: (
                "evaluation_support_bootstrap_ready"
            ),
            CandidateAttemptStage.REJECTED: "candidate_evaluation_rejected",
            CandidateAttemptStage.BLOCKED: (
                "candidate_evaluation_blocked"
                if preferred is CandidateAttemptStage.BLOCKED
                else "candidate_evaluation_lifecycle_incomplete"
            ),
            CandidateAttemptStage.NOT_RUN: (
                "candidate_evaluation_lifecycle_incomplete"
            ),
        }[stage]
        self.emit(key, stage, reason_code=reason_code)

    def block_open_best_effort(self, *, reason_code: str) -> None:
        """Fail closed after an unhandled run error while preserving that error."""

        for key in sorted(self._events):
            try:
                if self.terminal(key):
                    continue
                self.emit(
                    key,
                    CandidateAttemptStage.BLOCKED,
                    reason_code=reason_code,
                )
            except BaseException:
                continue

    def emit(
        self,
        key: CandidateAttemptKey,
        stage: CandidateAttemptStage,
        *,
        reason_code: str | None = None,
        failure_event_id: str | None = None,
        semantic_failure_key: str | None = None,
        usage: BudgetUsage | None = None,
        case_count: int | None = None,
        distinct_conformance_shape_count: int | None = None,
    ) -> CandidateAttemptEvent:
        candidate_id = self._events[key][0].candidate_id
        return self._append(
            key,
            stage,
            candidate_id=candidate_id,
            reason_code=reason_code,
            failure_event_id=failure_event_id,
            semantic_failure_key=semantic_failure_key,
            usage=usage,
            case_count=case_count,
            distinct_conformance_shape_count=distinct_conformance_shape_count,
        )

    def _append(
        self,
        key: CandidateAttemptKey,
        stage: CandidateAttemptStage,
        *,
        candidate_id: str,
        reason_code: str | None = None,
        failure_event_id: str | None = None,
        semantic_failure_key: str | None = None,
        usage: BudgetUsage | None = None,
        case_count: int | None = None,
        distinct_conformance_shape_count: int | None = None,
    ) -> CandidateAttemptEvent:
        values = self._events.get(key, ())
        event = CandidateAttemptEvent(
            key=key,
            sequence=len(values),
            stage=stage,
            candidate_id=candidate_id,
            reason_code=reason_code,
            failure_event_id=failure_event_id,
            semantic_failure_key=semantic_failure_key,
            usage=usage or BudgetUsage(),
            case_count=case_count,
            distinct_conformance_shape_count=distinct_conformance_shape_count,
        )
        try:
            self.store.append_candidate_attempt_event(event)
        except BaseException:
            # An fsync/rename boundary can raise after the event became
            # durable. Reconcile only the exact deterministic event id, then
            # preserve the storage exception for the caller.
            try:
                persisted = self.store.read_candidate_attempt_events(key)
            except BaseException:
                persisted = ()
            if any(item.event_id == event.event_id for item in persisted):
                self._events[key] = list(persisted)
            raise
        self._events.setdefault(key, []).append(event)
        return event


@dataclass
class RunFailureCleanup:
    """Bindings used by the public runner boundary for fail-closed cleanup."""

    budget_context: RunBudgetContext | None = None
    attempt_tracker: CandidateAttemptTracker | None = None

    def register_budget_context(self, context: RunBudgetContext) -> None:
        """Bind cleanup authority before startup performs any fallible effects."""

        self.budget_context = context

    def cleanup(self) -> None:
        if self.attempt_tracker is not None:
            try:
                self.attempt_tracker.block_open_best_effort(
                    reason_code="run_unhandled_exception"
                )
            except BaseException:
                pass
        if self.budget_context is not None:
            try:
                self.budget_context.release_all_best_effort(
                    reason_code="run_unhandled_exception_cleanup"
                )
            except BaseException:
                pass


__all__ = [
    "CandidateAttemptTracker",
    "RunBudgetContext",
    "RunFailureCleanup",
    "remaining_measurement_budget",
]
