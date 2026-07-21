"""Pure stage-aware budgeting, candidate lifecycle, and scheduling primitives.

The runner deliberately does not live in this module.  Every decision is a
deterministic function of typed inputs so one- and multi-trajectory runs share
the same accounting and transition contracts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from aworld.self_evolve.failure_events import FailureOwner, FailureScope


BUDGET_LEDGER_SCHEMA_VERSION = "aworld.self_evolve.run_budget.v1"
BUDGET_DECISION_SCHEMA_VERSION = "aworld.self_evolve.budget_decision.v1"
CANDIDATE_ATTEMPT_EVENT_SCHEMA_VERSION = (
    "aworld.self_evolve.candidate_attempt_event.v1"
)
CANDIDATE_ATTEMPT_AGGREGATE_SCHEMA_VERSION = (
    "aworld.self_evolve.candidate_attempt_aggregate.v1"
)
SCHEDULER_DECISION_SCHEMA_VERSION = "aworld.self_evolve.scheduler_decision.v1"

_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_MAX_OBSERVED_SAMPLES_PER_STAGE = 64


class BudgetStage(str, Enum):
    CANDIDATE_GENERATION = "candidate_generation"
    LOCAL_GATES = "local_gates"
    ADAPTATION = "adaptation"
    CONFORMANCE = "conformance"
    SCREENING = "screening"
    PAIRED_REPLAY = "paired_replay"
    EVALUATION = "evaluation"
    JUDGE = "judge"


class BudgetEstimateSource(str, Enum):
    UNKNOWN = "unknown"
    CONFIGURED_COLD_START = "configured_cold_start"
    OBSERVED_ROBUST = "observed_robust"
    BACKEND_PROVEN_ZERO = "backend_proven_zero"


class BudgetEstimateConfidence(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROVEN = "proven"


class BudgetDecisionReason(str, Enum):
    RESERVED = "reserved"
    RESERVATION_EXISTS = "reservation_exists"
    UNKNOWN_ESTIMATE = "unknown_estimate"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    COST_BUDGET_EXHAUSTED = "cost_budget_exhausted"
    WALL_BUDGET_EXHAUSTED = "wall_budget_exhausted"


def _decimal(value: Decimal | int | float | str, *, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field_name} must be a non-negative finite decimal")
    return result


def _optional_decimal(
    value: Decimal | int | float | str | None,
    *,
    field_name: str,
) -> Decimal | None:
    return None if value is None else _decimal(value, field_name=field_name)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _positive_int(value: Any, *, field_name: str) -> int:
    result = _non_negative_int(value, field_name=field_name)
    if result == 0:
        raise ValueError(f"{field_name} must be positive")
    return result


def _identity(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty exact string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _reason_code(value: Any, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("terminal candidate attempt requires a reason code")
        return None
    if not isinstance(value, str) or _REASON_CODE_RE.fullmatch(value) is None:
        raise ValueError("reason_code must be a stable lower_snake_case code")
    return value


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-sha256-{digest}"


@dataclass(frozen=True)
class BudgetUsage:
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    wall_seconds: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tokens",
            _non_negative_int(self.tokens, field_name="tokens"),
        )
        object.__setattr__(
            self,
            "cost_usd",
            _decimal(self.cost_usd, field_name="cost_usd"),
        )
        object.__setattr__(
            self,
            "wall_seconds",
            _decimal(self.wall_seconds, field_name="wall_seconds"),
        )

    def __add__(self, other: "BudgetUsage") -> "BudgetUsage":
        return BudgetUsage(
            tokens=self.tokens + other.tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            wall_seconds=self.wall_seconds + other.wall_seconds,
        )

    def scale(self, units: int) -> "BudgetUsage":
        count = _non_negative_int(units, field_name="units")
        return BudgetUsage(
            tokens=self.tokens * count,
            cost_usd=self.cost_usd * count,
            wall_seconds=self.wall_seconds * count,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "cost_usd": _decimal_text(self.cost_usd),
            "wall_seconds": _decimal_text(self.wall_seconds),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BudgetUsage":
        return cls(
            tokens=_non_negative_int(value.get("tokens"), field_name="tokens"),
            cost_usd=_decimal(value.get("cost_usd", "0"), field_name="cost_usd"),
            wall_seconds=_decimal(
                value.get("wall_seconds", "0"),
                field_name="wall_seconds",
            ),
        )


@dataclass(frozen=True)
class BudgetCeilings:
    total_tokens: int | None
    total_cost_usd: Decimal | None
    wall_seconds: Decimal | None = None

    def __post_init__(self) -> None:
        if self.total_tokens is not None:
            object.__setattr__(
                self,
                "total_tokens",
                _non_negative_int(self.total_tokens, field_name="total_tokens"),
            )
        object.__setattr__(
            self,
            "total_cost_usd",
            _optional_decimal(
                self.total_cost_usd,
                field_name="total_cost_usd",
            ),
        )
        object.__setattr__(
            self,
            "wall_seconds",
            _optional_decimal(self.wall_seconds, field_name="wall_seconds"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_tokens": self.total_tokens,
            "total_cost_usd": (
                _decimal_text(self.total_cost_usd)
                if self.total_cost_usd is not None
                else None
            ),
            "wall_seconds": (
                _decimal_text(self.wall_seconds)
                if self.wall_seconds is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BudgetCeilings":
        raw_tokens = value.get("total_tokens")
        return cls(
            total_tokens=(
                _non_negative_int(raw_tokens, field_name="total_tokens")
                if raw_tokens is not None
                else None
            ),
            total_cost_usd=_optional_decimal(
                value.get("total_cost_usd"),
                field_name="total_cost_usd",
            ),
            wall_seconds=_optional_decimal(
                value.get("wall_seconds"),
                field_name="wall_seconds",
            ),
        )


@dataclass(frozen=True)
class StageBudgetEstimate:
    stage: BudgetStage
    item_id: str
    tokens: int | None
    cost_usd: Decimal | None
    wall_seconds: Decimal | None
    source: BudgetEstimateSource = BudgetEstimateSource.UNKNOWN
    confidence: BudgetEstimateConfidence = BudgetEstimateConfidence.UNKNOWN
    backend_proven_zero: bool = False
    units: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", BudgetStage(self.stage))
        object.__setattr__(self, "item_id", _identity(self.item_id, field_name="item_id"))
        if self.tokens is not None:
            object.__setattr__(
                self,
                "tokens",
                _non_negative_int(self.tokens, field_name="tokens"),
            )
        object.__setattr__(
            self,
            "cost_usd",
            _optional_decimal(self.cost_usd, field_name="cost_usd"),
        )
        object.__setattr__(
            self,
            "wall_seconds",
            _optional_decimal(self.wall_seconds, field_name="wall_seconds"),
        )
        object.__setattr__(self, "source", BudgetEstimateSource(self.source))
        object.__setattr__(
            self,
            "confidence",
            BudgetEstimateConfidence(self.confidence),
        )
        object.__setattr__(self, "units", _positive_int(self.units, field_name="units"))
        if self.backend_proven_zero:
            known = (self.tokens, self.cost_usd, self.wall_seconds)
            if any(value not in (None, 0, Decimal("0")) for value in known):
                raise ValueError("backend_proven_zero estimate cannot include non-zero usage")

    @property
    def reservation_id(self) -> str:
        return _stable_id(
            "budget-reservation",
            {
                "stage": self.stage.value,
                "item_id": self.item_id,
                "tokens": self.tokens,
                "cost_usd": (
                    _decimal_text(self.cost_usd)
                    if self.cost_usd is not None
                    else None
                ),
                "wall_seconds": (
                    _decimal_text(self.wall_seconds)
                    if self.wall_seconds is not None
                    else None
                ),
                "source": self.source.value,
                "confidence": self.confidence.value,
                "backend_proven_zero": self.backend_proven_zero,
                "units": self.units,
            },
        )

    @classmethod
    def from_per_unit(
        cls,
        *,
        stage: BudgetStage,
        item_id: str,
        per_unit: BudgetUsage,
        units: int,
        source: BudgetEstimateSource,
        confidence: BudgetEstimateConfidence,
    ) -> "StageBudgetEstimate":
        scaled = per_unit.scale(units)
        return cls(
            stage=stage,
            item_id=item_id,
            tokens=scaled.tokens,
            cost_usd=scaled.cost_usd,
            wall_seconds=scaled.wall_seconds,
            source=source,
            confidence=confidence,
            units=units,
        )

    def resolved_usage(self) -> BudgetUsage | None:
        if self.backend_proven_zero:
            return BudgetUsage()
        if self.tokens is None or self.cost_usd is None or self.wall_seconds is None:
            return None
        return BudgetUsage(
            tokens=self.tokens,
            cost_usd=self.cost_usd,
            wall_seconds=self.wall_seconds,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "item_id": self.item_id,
            "tokens": self.tokens,
            "cost_usd": (
                _decimal_text(self.cost_usd) if self.cost_usd is not None else None
            ),
            "wall_seconds": (
                _decimal_text(self.wall_seconds)
                if self.wall_seconds is not None
                else None
            ),
            "source": self.source.value,
            "confidence": self.confidence.value,
            "backend_proven_zero": self.backend_proven_zero,
            "units": self.units,
            "reservation_id": self.reservation_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "StageBudgetEstimate":
        estimate = cls(
            stage=BudgetStage(str(value.get("stage"))),
            item_id=_identity(value.get("item_id"), field_name="item_id"),
            tokens=(
                _non_negative_int(value.get("tokens"), field_name="tokens")
                if value.get("tokens") is not None
                else None
            ),
            cost_usd=_optional_decimal(value.get("cost_usd"), field_name="cost_usd"),
            wall_seconds=_optional_decimal(
                value.get("wall_seconds"),
                field_name="wall_seconds",
            ),
            source=BudgetEstimateSource(str(value.get("source"))),
            confidence=BudgetEstimateConfidence(str(value.get("confidence"))),
            backend_proven_zero=value.get("backend_proven_zero") is True,
            units=_positive_int(value.get("units", 1), field_name="units"),
        )
        raw_id = value.get("reservation_id")
        if raw_id is not None and raw_id != estimate.reservation_id:
            raise ValueError("reservation_id does not match estimate payload")
        return estimate


@dataclass(frozen=True)
class BudgetRemaining:
    tokens: int | None
    cost_usd: Decimal | None
    wall_seconds: Decimal | None

    def __post_init__(self) -> None:
        if self.tokens is not None:
            object.__setattr__(
                self,
                "tokens",
                _non_negative_int(self.tokens, field_name="remaining tokens"),
            )
        object.__setattr__(
            self,
            "cost_usd",
            _optional_decimal(self.cost_usd, field_name="remaining cost_usd"),
        )
        object.__setattr__(
            self,
            "wall_seconds",
            _optional_decimal(
                self.wall_seconds,
                field_name="remaining wall_seconds",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "cost_usd": (
                _decimal_text(self.cost_usd) if self.cost_usd is not None else None
            ),
            "wall_seconds": (
                _decimal_text(self.wall_seconds)
                if self.wall_seconds is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BudgetRemaining":
        raw_tokens = value.get("tokens")
        return cls(
            tokens=(
                _non_negative_int(raw_tokens, field_name="remaining tokens")
                if raw_tokens is not None
                else None
            ),
            cost_usd=_optional_decimal(
                value.get("cost_usd"),
                field_name="remaining cost_usd",
            ),
            wall_seconds=_optional_decimal(
                value.get("wall_seconds"),
                field_name="remaining wall_seconds",
            ),
        )


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason_code: BudgetDecisionReason
    stage: BudgetStage
    item_id: str
    reservation_id: str | None
    estimate: StageBudgetEstimate
    remaining_before: BudgetRemaining
    remaining_after: BudgetRemaining

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("budget decision allowed must be boolean")
        object.__setattr__(self, "reason_code", BudgetDecisionReason(self.reason_code))
        object.__setattr__(self, "stage", BudgetStage(self.stage))
        object.__setattr__(self, "item_id", _identity(self.item_id, field_name="item_id"))
        if not isinstance(self.estimate, StageBudgetEstimate):
            raise TypeError("budget decision estimate must be typed")
        if not isinstance(self.remaining_before, BudgetRemaining) or not isinstance(
            self.remaining_after,
            BudgetRemaining,
        ):
            raise TypeError("budget decision remaining values must be typed")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": BUDGET_DECISION_SCHEMA_VERSION,
            "allowed": self.allowed,
            "reason_code": self.reason_code.value,
            "stage": self.stage.value,
            "item_id": self.item_id,
            "reservation_id": self.reservation_id,
            "estimate": self.estimate.to_dict(),
            "remaining_before": self.remaining_before.to_dict(),
            "remaining_after": self.remaining_after.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BudgetDecision":
        if value.get("schema_version") != BUDGET_DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported budget decision schema")
        raw_estimate = value.get("estimate")
        raw_before = value.get("remaining_before")
        raw_after = value.get("remaining_after")
        if not all(
            isinstance(item, Mapping)
            for item in (raw_estimate, raw_before, raw_after)
        ):
            raise ValueError("budget decision payload is malformed")
        assert isinstance(raw_estimate, Mapping)
        assert isinstance(raw_before, Mapping)
        assert isinstance(raw_after, Mapping)
        estimate = StageBudgetEstimate.from_dict(raw_estimate)
        decision = cls(
            allowed=value.get("allowed") is True,
            reason_code=BudgetDecisionReason(str(value.get("reason_code"))),
            stage=BudgetStage(str(value.get("stage"))),
            item_id=_identity(value.get("item_id"), field_name="item_id"),
            reservation_id=(
                _identity(value.get("reservation_id"), field_name="reservation_id")
                if value.get("reservation_id") is not None
                else None
            ),
            estimate=estimate,
            remaining_before=BudgetRemaining.from_dict(raw_before),
            remaining_after=BudgetRemaining.from_dict(raw_after),
        )
        if decision.stage is not estimate.stage or decision.item_id != estimate.item_id:
            raise ValueError("budget decision does not match its estimate")
        if decision.allowed:
            if (
                decision.reason_code is not BudgetDecisionReason.RESERVED
                or decision.reservation_id != estimate.reservation_id
            ):
                raise ValueError("allowed budget decision has invalid reservation")
        elif decision.reason_code is BudgetDecisionReason.RESERVED:
            raise ValueError("denied budget decision cannot use reserved reason")
        return decision


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    estimate: StageBudgetEstimate
    usage: BudgetUsage

    def to_dict(self) -> dict[str, object]:
        return {
            "reservation_id": self.reservation_id,
            "estimate": self.estimate.to_dict(),
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True)
class BudgetDebitResult:
    reservation_id: str
    stage: BudgetStage
    item_id: str
    reserved: BudgetUsage
    actual: BudgetUsage
    reservation_overrun: BudgetUsage
    ceiling_overrun: BudgetUsage
    remaining: BudgetRemaining

    def to_dict(self) -> dict[str, object]:
        return {
            "reservation_id": self.reservation_id,
            "stage": self.stage.value,
            "item_id": self.item_id,
            "reserved": self.reserved.to_dict(),
            "actual": self.actual.to_dict(),
            "reservation_overrun": self.reservation_overrun.to_dict(),
            "ceiling_overrun": self.ceiling_overrun.to_dict(),
            "remaining": self.remaining.to_dict(),
        }


@dataclass(frozen=True)
class StageEstimateStatistics:
    stage: BudgetStage
    sample_count: int
    median: BudgetUsage
    upper_conservative: BudgetUsage

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "sample_count": self.sample_count,
            "median": self.median.to_dict(),
            "upper_conservative": self.upper_conservative.to_dict(),
        }


@dataclass
class RunBudgetLedger:
    ceilings: BudgetCeilings
    _spent_by_stage: dict[BudgetStage, BudgetUsage] = field(default_factory=dict)
    _reservations: dict[str, BudgetReservation] = field(default_factory=dict)
    _observed_by_stage: dict[BudgetStage, list[BudgetUsage]] = field(
        default_factory=dict
    )

    @property
    def outstanding_reservations(self) -> tuple[BudgetReservation, ...]:
        return tuple(self._reservations[key] for key in sorted(self._reservations))

    @property
    def spent_by_stage(self) -> Mapping[BudgetStage, BudgetUsage]:
        return dict(self._spent_by_stage)

    def total_spent(self) -> BudgetUsage:
        return _sum_usage(self._spent_by_stage.values())

    def total_reserved(self) -> BudgetUsage:
        return _sum_usage(item.usage for item in self._reservations.values())

    def remaining(self) -> BudgetRemaining:
        committed = self.total_spent() + self.total_reserved()
        return BudgetRemaining(
            tokens=(
                max(0, self.ceilings.total_tokens - committed.tokens)
                if self.ceilings.total_tokens is not None
                else None
            ),
            cost_usd=(
                max(Decimal("0"), self.ceilings.total_cost_usd - committed.cost_usd)
                if self.ceilings.total_cost_usd is not None
                else None
            ),
            wall_seconds=(
                max(Decimal("0"), self.ceilings.wall_seconds - committed.wall_seconds)
                if self.ceilings.wall_seconds is not None
                else None
            ),
        )

    def overrun(self) -> BudgetUsage:
        spent = self.total_spent()
        return BudgetUsage(
            tokens=(
                max(0, spent.tokens - self.ceilings.total_tokens)
                if self.ceilings.total_tokens is not None
                else 0
            ),
            cost_usd=(
                max(Decimal("0"), spent.cost_usd - self.ceilings.total_cost_usd)
                if self.ceilings.total_cost_usd is not None
                else Decimal("0")
            ),
            wall_seconds=(
                max(Decimal("0"), spent.wall_seconds - self.ceilings.wall_seconds)
                if self.ceilings.wall_seconds is not None
                else Decimal("0")
            ),
        )

    def reserve(self, estimate: StageBudgetEstimate) -> BudgetDecision:
        if not isinstance(estimate, StageBudgetEstimate):
            raise TypeError("budget reservation estimate must be typed")
        before = self.remaining()
        existing_reservation = next(
            (
                reservation
                for reservation in self._reservations.values()
                if reservation.estimate.stage is estimate.stage
                and reservation.estimate.item_id == estimate.item_id
            ),
            None,
        )
        if existing_reservation is not None:
            return BudgetDecision(
                allowed=False,
                reason_code=BudgetDecisionReason.RESERVATION_EXISTS,
                stage=estimate.stage,
                item_id=estimate.item_id,
                reservation_id=existing_reservation.reservation_id,
                estimate=estimate,
                remaining_before=before,
                remaining_after=before,
            )
        usage = self._usage_for_reservation(estimate)
        if usage is None:
            return BudgetDecision(
                allowed=False,
                reason_code=BudgetDecisionReason.UNKNOWN_ESTIMATE,
                stage=estimate.stage,
                item_id=estimate.item_id,
                reservation_id=None,
                estimate=estimate,
                remaining_before=before,
                remaining_after=before,
            )
        denied_reason = _denied_budget_reason(before, usage)
        if denied_reason is not None:
            return BudgetDecision(
                allowed=False,
                reason_code=denied_reason,
                stage=estimate.stage,
                item_id=estimate.item_id,
                reservation_id=None,
                estimate=estimate,
                remaining_before=before,
                remaining_after=before,
            )
        reservation = BudgetReservation(
            reservation_id=estimate.reservation_id,
            estimate=estimate,
            usage=usage,
        )
        self._reservations[reservation.reservation_id] = reservation
        return BudgetDecision(
            allowed=True,
            reason_code=BudgetDecisionReason.RESERVED,
            stage=estimate.stage,
            item_id=estimate.item_id,
            reservation_id=reservation.reservation_id,
            estimate=estimate,
            remaining_before=before,
            remaining_after=self.remaining(),
        )

    def debit_actual(
        self,
        reservation_id: str,
        actual: BudgetUsage,
    ) -> BudgetDebitResult:
        if not isinstance(actual, BudgetUsage):
            raise TypeError("actual budget usage must be typed")
        reservation = self._reservations.pop(reservation_id, None)
        if reservation is None:
            raise KeyError(f"unknown budget reservation: {reservation_id}")
        self._spent_by_stage[reservation.estimate.stage] = (
            self._spent_by_stage.get(reservation.estimate.stage, BudgetUsage())
            + actual
        )
        observations = self._observed_by_stage.setdefault(
            reservation.estimate.stage,
            [],
        )
        observations.append(actual)
        del observations[:-_MAX_OBSERVED_SAMPLES_PER_STAGE]
        return BudgetDebitResult(
            reservation_id=reservation_id,
            stage=reservation.estimate.stage,
            item_id=reservation.estimate.item_id,
            reserved=reservation.usage,
            actual=actual,
            reservation_overrun=_positive_usage_difference(
                actual,
                reservation.usage,
            ),
            ceiling_overrun=self.overrun(),
            remaining=self.remaining(),
        )

    def release(self, reservation_id: str) -> BudgetReservation:
        reservation = self._reservations.pop(reservation_id, None)
        if reservation is None:
            raise KeyError(f"unknown budget reservation: {reservation_id}")
        return reservation

    def estimate_statistics(
        self,
        stage: BudgetStage,
    ) -> StageEstimateStatistics | None:
        normalized_stage = BudgetStage(stage)
        samples = self._observed_by_stage.get(normalized_stage, ())
        if not samples:
            return None
        return StageEstimateStatistics(
            stage=normalized_stage,
            sample_count=len(samples),
            median=BudgetUsage(
                tokens=math.ceil(statistics.median(item.tokens for item in samples)),
                cost_usd=_decimal_median(item.cost_usd for item in samples),
                wall_seconds=_decimal_median(item.wall_seconds for item in samples),
            ),
            upper_conservative=BudgetUsage(
                tokens=_upper_quantile_int(item.tokens for item in samples),
                cost_usd=_upper_quantile_decimal(item.cost_usd for item in samples),
                wall_seconds=_upper_quantile_decimal(
                    item.wall_seconds for item in samples
                ),
            ),
        )

    def estimate_next(
        self,
        *,
        stage: BudgetStage,
        item_id: str,
        units: int = 1,
        cold_start_per_unit: BudgetUsage | None = None,
        backend_proven_zero: bool = False,
    ) -> StageBudgetEstimate:
        count = _positive_int(units, field_name="units")
        if cold_start_per_unit is not None and not isinstance(
            cold_start_per_unit,
            BudgetUsage,
        ):
            raise TypeError("cold-start budget estimate must be BudgetUsage")
        if backend_proven_zero:
            return StageBudgetEstimate(
                stage=stage,
                item_id=item_id,
                tokens=None,
                cost_usd=None,
                wall_seconds=None,
                source=BudgetEstimateSource.BACKEND_PROVEN_ZERO,
                confidence=BudgetEstimateConfidence.PROVEN,
                backend_proven_zero=True,
                units=count,
            )
        statistics_value = self.estimate_statistics(stage)
        if statistics_value is not None:
            return StageBudgetEstimate.from_per_unit(
                stage=stage,
                item_id=item_id,
                per_unit=statistics_value.upper_conservative,
                units=count,
                source=BudgetEstimateSource.OBSERVED_ROBUST,
                confidence=(
                    BudgetEstimateConfidence.HIGH
                    if statistics_value.sample_count >= 5
                    else BudgetEstimateConfidence.MEDIUM
                ),
            )
        if cold_start_per_unit is not None:
            return StageBudgetEstimate.from_per_unit(
                stage=stage,
                item_id=item_id,
                per_unit=cold_start_per_unit,
                units=count,
                source=BudgetEstimateSource.CONFIGURED_COLD_START,
                confidence=BudgetEstimateConfidence.LOW,
            )
        return StageBudgetEstimate(
            stage=stage,
            item_id=item_id,
            tokens=None,
            cost_usd=None,
            wall_seconds=None,
            source=BudgetEstimateSource.UNKNOWN,
            confidence=BudgetEstimateConfidence.UNKNOWN,
            units=count,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": BUDGET_LEDGER_SCHEMA_VERSION,
            "ceilings": self.ceilings.to_dict(),
            "spent_by_stage": {
                stage.value: self._spent_by_stage[stage].to_dict()
                for stage in sorted(self._spent_by_stage, key=lambda item: item.value)
            },
            "outstanding_reservations": [
                item.to_dict() for item in self.outstanding_reservations
            ],
            "observed_by_stage": {
                stage.value: [sample.to_dict() for sample in samples]
                for stage, samples in sorted(
                    self._observed_by_stage.items(),
                    key=lambda item: item[0].value,
                )
            },
            "remaining": self.remaining().to_dict(),
            "overrun": self.overrun().to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RunBudgetLedger":
        if value.get("schema_version") != BUDGET_LEDGER_SCHEMA_VERSION:
            raise ValueError("unsupported run budget ledger schema")
        raw_ceilings = value.get("ceilings")
        if not isinstance(raw_ceilings, Mapping):
            raise ValueError("run budget ledger ceilings must be a mapping")
        ledger = cls(ceilings=BudgetCeilings.from_dict(raw_ceilings))
        raw_spent = value.get("spent_by_stage", {})
        if not isinstance(raw_spent, Mapping):
            raise ValueError("spent_by_stage must be a mapping")
        ledger._spent_by_stage = {
            BudgetStage(str(stage)): BudgetUsage.from_dict(usage)
            for stage, usage in raw_spent.items()
            if isinstance(usage, Mapping)
        }
        if len(ledger._spent_by_stage) != len(raw_spent):
            raise ValueError("spent_by_stage contains invalid usage")
        raw_reservations = value.get("outstanding_reservations", [])
        if not isinstance(raw_reservations, list):
            raise ValueError("outstanding_reservations must be a list")
        for raw in raw_reservations:
            if not isinstance(raw, Mapping):
                raise ValueError("budget reservation must be a mapping")
            raw_estimate = raw.get("estimate")
            raw_usage = raw.get("usage")
            if not isinstance(raw_estimate, Mapping) or not isinstance(
                raw_usage,
                Mapping,
            ):
                raise ValueError("budget reservation payload is malformed")
            estimate = StageBudgetEstimate.from_dict(raw_estimate)
            usage = BudgetUsage.from_dict(raw_usage)
            reservation_id = str(raw.get("reservation_id") or "")
            if reservation_id != estimate.reservation_id:
                raise ValueError("budget reservation id does not match estimate")
            if reservation_id in ledger._reservations:
                raise ValueError("duplicate outstanding budget reservation")
            if any(
                existing.estimate.stage is estimate.stage
                and existing.estimate.item_id == estimate.item_id
                for existing in ledger._reservations.values()
            ):
                raise ValueError("duplicate outstanding stage/item reservation")
            expected_usage = ledger._usage_for_reservation(estimate)
            if expected_usage is None or usage != expected_usage:
                raise ValueError("budget reservation usage does not match estimate")
            ledger._reservations[reservation_id] = BudgetReservation(
                reservation_id=reservation_id,
                estimate=estimate,
                usage=usage,
            )
        raw_observed = value.get("observed_by_stage", {})
        if not isinstance(raw_observed, Mapping):
            raise ValueError("observed_by_stage must be a mapping")
        for stage, samples in raw_observed.items():
            if not isinstance(samples, list):
                raise ValueError("observed stage samples must be a list")
            if len(samples) > _MAX_OBSERVED_SAMPLES_PER_STAGE:
                raise ValueError("observed stage samples exceed rolling window")
            ledger._observed_by_stage[BudgetStage(str(stage))] = [
                BudgetUsage.from_dict(sample)
                for sample in samples
                if isinstance(sample, Mapping)
            ]
            if len(ledger._observed_by_stage[BudgetStage(str(stage))]) != len(samples):
                raise ValueError("observed stage samples contain invalid usage")
        if value.get("remaining") != ledger.remaining().to_dict():
            raise ValueError("run budget ledger remaining summary is inconsistent")
        if value.get("overrun") != ledger.overrun().to_dict():
            raise ValueError("run budget ledger overrun summary is inconsistent")
        return ledger

    def _usage_for_reservation(
        self,
        estimate: StageBudgetEstimate,
    ) -> BudgetUsage | None:
        tokens = estimate.tokens
        cost = estimate.cost_usd
        wall = estimate.wall_seconds
        if estimate.backend_proven_zero:
            tokens = 0 if tokens is None else tokens
            cost = Decimal("0") if cost is None else cost
            wall = Decimal("0") if wall is None else wall
        # Unknown is distinct from zero even for an unbounded dimension.  This
        # prevents later configuration changes or report consumers from
        # silently interpreting missing estimates as free work.
        if tokens is None or cost is None or wall is None:
            return None
        return BudgetUsage(
            tokens=tokens or 0,
            cost_usd=cost or Decimal("0"),
            wall_seconds=wall or Decimal("0"),
        )


def _sum_usage(values: Iterable[BudgetUsage]) -> BudgetUsage:
    total = BudgetUsage()
    for value in values:
        total = total + value
    return total


def _positive_usage_difference(actual: BudgetUsage, expected: BudgetUsage) -> BudgetUsage:
    return BudgetUsage(
        tokens=max(0, actual.tokens - expected.tokens),
        cost_usd=max(Decimal("0"), actual.cost_usd - expected.cost_usd),
        wall_seconds=max(
            Decimal("0"),
            actual.wall_seconds - expected.wall_seconds,
        ),
    )


def _denied_budget_reason(
    remaining: BudgetRemaining,
    estimate: BudgetUsage,
) -> BudgetDecisionReason | None:
    if remaining.tokens is not None and estimate.tokens > remaining.tokens:
        return BudgetDecisionReason.TOKEN_BUDGET_EXHAUSTED
    if remaining.cost_usd is not None and estimate.cost_usd > remaining.cost_usd:
        return BudgetDecisionReason.COST_BUDGET_EXHAUSTED
    if (
        remaining.wall_seconds is not None
        and estimate.wall_seconds > remaining.wall_seconds
    ):
        return BudgetDecisionReason.WALL_BUDGET_EXHAUSTED
    return None


def _upper_quantile_index(length: int) -> int:
    return max(0, math.ceil(length * 0.75) - 1)


def _upper_quantile_int(values: Iterable[int]) -> int:
    ordered = sorted(values)
    return ordered[_upper_quantile_index(len(ordered))]


def _upper_quantile_decimal(values: Iterable[Decimal]) -> Decimal:
    ordered = sorted(values)
    return ordered[_upper_quantile_index(len(ordered))]


def _decimal_median(values: Iterable[Decimal]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


@dataclass(frozen=True)
class StageWorkload:
    case_count: int
    repetitions: int = 1
    distinct_conformance_shape_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "case_count",
            _non_negative_int(self.case_count, field_name="case_count"),
        )
        object.__setattr__(
            self,
            "repetitions",
            _positive_int(self.repetitions, field_name="repetitions"),
        )
        if self.distinct_conformance_shape_count is not None:
            shape_count = _non_negative_int(
                self.distinct_conformance_shape_count,
                field_name="distinct_conformance_shape_count",
            )
            object.__setattr__(
                self,
                "distinct_conformance_shape_count",
                shape_count,
            )

    def units_for(self, stage: BudgetStage) -> int:
        normalized = BudgetStage(stage)
        if normalized is BudgetStage.CONFORMANCE:
            return (
                self.distinct_conformance_shape_count
                if self.distinct_conformance_shape_count is not None
                else self.case_count
            )
        if normalized is BudgetStage.SCREENING:
            return min(1, self.case_count)
        if normalized in {
            BudgetStage.PAIRED_REPLAY,
            BudgetStage.EVALUATION,
            BudgetStage.JUDGE,
        }:
            return self.case_count * self.repetitions
        return 1


class CandidateAttemptStage(str, Enum):
    GENERATED = "generated"
    UNIQUE = "unique"
    DUPLICATE_FILTERED = "duplicate_filtered"
    LOCAL_GATES = "local_gates"
    ADAPTATION = "adaptation_compile"
    CONFORMANCE = "repair_conformance"
    SCREENING = "representative_screening"
    PAIRED_REPLAY_STARTED = "paired_replay_started"
    PAIRED_REPLAY_COMPLETED = "paired_replay_completed"
    PAIRED_REPLAY_COMPARABLE = "paired_replay_comparable"
    EVALUATION = "evaluation"
    SELECTED = "selected"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"


TERMINAL_ATTEMPT_STAGES = frozenset(
    {
        CandidateAttemptStage.SELECTED,
        CandidateAttemptStage.REJECTED,
        CandidateAttemptStage.BLOCKED,
        CandidateAttemptStage.NOT_RUN,
    }
)

_ATTEMPT_TRANSITIONS: Mapping[CandidateAttemptStage, frozenset[CandidateAttemptStage]] = {
    CandidateAttemptStage.GENERATED: frozenset(
        {
            CandidateAttemptStage.UNIQUE,
            CandidateAttemptStage.DUPLICATE_FILTERED,
            CandidateAttemptStage.BLOCKED,
            CandidateAttemptStage.NOT_RUN,
        }
    ),
    CandidateAttemptStage.UNIQUE: frozenset(
        {
            CandidateAttemptStage.LOCAL_GATES,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
            CandidateAttemptStage.NOT_RUN,
        }
    ),
    CandidateAttemptStage.DUPLICATE_FILTERED: frozenset(
        {CandidateAttemptStage.NOT_RUN}
    ),
    CandidateAttemptStage.LOCAL_GATES: frozenset(
        {
            CandidateAttemptStage.ADAPTATION,
            CandidateAttemptStage.CONFORMANCE,
            CandidateAttemptStage.SCREENING,
            CandidateAttemptStage.PAIRED_REPLAY_STARTED,
            CandidateAttemptStage.EVALUATION,
            CandidateAttemptStage.SELECTED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
            CandidateAttemptStage.NOT_RUN,
        }
    ),
    CandidateAttemptStage.ADAPTATION: frozenset(
        {
            CandidateAttemptStage.CONFORMANCE,
            CandidateAttemptStage.SCREENING,
            CandidateAttemptStage.PAIRED_REPLAY_STARTED,
            CandidateAttemptStage.EVALUATION,
            CandidateAttemptStage.SELECTED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
            CandidateAttemptStage.NOT_RUN,
        }
    ),
    CandidateAttemptStage.CONFORMANCE: frozenset(
        {
            CandidateAttemptStage.SCREENING,
            CandidateAttemptStage.PAIRED_REPLAY_STARTED,
            CandidateAttemptStage.EVALUATION,
            CandidateAttemptStage.SELECTED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
            CandidateAttemptStage.NOT_RUN,
        }
    ),
    CandidateAttemptStage.SCREENING: frozenset(
        {
            CandidateAttemptStage.PAIRED_REPLAY_STARTED,
            CandidateAttemptStage.EVALUATION,
            CandidateAttemptStage.SELECTED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
            CandidateAttemptStage.NOT_RUN,
        }
    ),
    CandidateAttemptStage.PAIRED_REPLAY_STARTED: frozenset(
        {
            CandidateAttemptStage.PAIRED_REPLAY_COMPLETED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
        }
    ),
    CandidateAttemptStage.PAIRED_REPLAY_COMPLETED: frozenset(
        {
            CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE,
            CandidateAttemptStage.EVALUATION,
            CandidateAttemptStage.SELECTED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
        }
    ),
    CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE: frozenset(
        {
            CandidateAttemptStage.EVALUATION,
            CandidateAttemptStage.SELECTED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
        }
    ),
    CandidateAttemptStage.EVALUATION: frozenset(
        {
            CandidateAttemptStage.SELECTED,
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.BLOCKED,
        }
    ),
    **{stage: frozenset() for stage in TERMINAL_ATTEMPT_STAGES},
}


@dataclass(frozen=True, order=True)
class CandidateAttemptKey:
    run_id: str
    iteration: int
    slot: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identity(self.run_id, field_name="run_id"))
        object.__setattr__(
            self,
            "iteration",
            _non_negative_int(self.iteration, field_name="iteration"),
        )
        object.__setattr__(
            self,
            "slot",
            _non_negative_int(self.slot, field_name="slot"),
        )

    @property
    def attempt_id(self) -> str:
        return _stable_id(
            "candidate-attempt",
            {
                "run_id": self.run_id,
                "iteration": self.iteration,
                "slot": self.slot,
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "iteration": self.iteration,
            "slot": self.slot,
            "attempt_id": self.attempt_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CandidateAttemptKey":
        key = cls(
            run_id=_identity(value.get("run_id"), field_name="run_id"),
            iteration=_non_negative_int(
                value.get("iteration"),
                field_name="iteration",
            ),
            slot=_non_negative_int(value.get("slot"), field_name="slot"),
        )
        raw_attempt_id = value.get("attempt_id")
        if raw_attempt_id is not None and raw_attempt_id != key.attempt_id:
            raise ValueError("attempt_id does not match attempt key")
        return key


@dataclass(frozen=True)
class CandidateAttemptEvent:
    key: CandidateAttemptKey
    sequence: int
    stage: CandidateAttemptStage
    candidate_id: str
    reason_code: str | None = None
    failure_event_id: str | None = None
    semantic_failure_key: str | None = None
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    case_count: int | None = None
    distinct_conformance_shape_count: int | None = None
    event_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, CandidateAttemptKey):
            raise TypeError("candidate attempt event key must be typed")
        object.__setattr__(
            self,
            "sequence",
            _non_negative_int(self.sequence, field_name="sequence"),
        )
        object.__setattr__(self, "stage", CandidateAttemptStage(self.stage))
        object.__setattr__(
            self,
            "candidate_id",
            _identity(self.candidate_id, field_name="candidate_id"),
        )
        reason = _reason_code(
            self.reason_code,
            required=self.stage in TERMINAL_ATTEMPT_STAGES,
        )
        object.__setattr__(self, "reason_code", reason)
        if self.failure_event_id is not None:
            object.__setattr__(
                self,
                "failure_event_id",
                _identity(self.failure_event_id, field_name="failure_event_id"),
            )
        if self.semantic_failure_key is not None:
            object.__setattr__(
                self,
                "semantic_failure_key",
                _identity(
                    self.semantic_failure_key,
                    field_name="semantic_failure_key",
                ),
            )
        if not isinstance(self.usage, BudgetUsage):
            raise TypeError("candidate attempt usage must be BudgetUsage")
        for field_name in ("case_count", "distinct_conformance_shape_count"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _non_negative_int(value, field_name=field_name),
                )
        expected_event_id = self._computed_event_id()
        if self.event_id is not None and self.event_id != expected_event_id:
            raise ValueError("candidate attempt event_id does not match payload")
        object.__setattr__(self, "event_id", expected_event_id)

    def _computed_event_id(self) -> str:
        return _stable_id(
            "candidate-attempt-event",
            {
                "attempt_id": self.key.attempt_id,
                "sequence": self.sequence,
                "stage": self.stage.value,
                "candidate_id": self.candidate_id,
                "reason_code": self.reason_code,
                "failure_event_id": self.failure_event_id,
                "semantic_failure_key": self.semantic_failure_key,
                "usage": self.usage.to_dict(),
                "case_count": self.case_count,
                "distinct_conformance_shape_count": (
                    self.distinct_conformance_shape_count
                ),
            },
        )

    @property
    def terminal(self) -> bool:
        return self.stage in TERMINAL_ATTEMPT_STAGES

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CANDIDATE_ATTEMPT_EVENT_SCHEMA_VERSION,
            "key": self.key.to_dict(),
            "sequence": self.sequence,
            "stage": self.stage.value,
            "candidate_id": self.candidate_id,
            "reason_code": self.reason_code,
            "failure_event_id": self.failure_event_id,
            "semantic_failure_key": self.semantic_failure_key,
            "usage": self.usage.to_dict(),
            "case_count": self.case_count,
            "distinct_conformance_shape_count": (
                self.distinct_conformance_shape_count
            ),
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CandidateAttemptEvent":
        if value.get("schema_version") != CANDIDATE_ATTEMPT_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported candidate attempt event schema")
        raw_key = value.get("key")
        raw_usage = value.get("usage")
        if not isinstance(raw_key, Mapping) or not isinstance(raw_usage, Mapping):
            raise ValueError("candidate attempt event payload is malformed")
        return cls(
            key=CandidateAttemptKey.from_dict(raw_key),
            sequence=_non_negative_int(value.get("sequence"), field_name="sequence"),
            stage=CandidateAttemptStage(str(value.get("stage"))),
            candidate_id=_identity(value.get("candidate_id"), field_name="candidate_id"),
            reason_code=(
                str(value.get("reason_code"))
                if value.get("reason_code") is not None
                else None
            ),
            failure_event_id=(
                str(value.get("failure_event_id"))
                if value.get("failure_event_id") is not None
                else None
            ),
            semantic_failure_key=(
                str(value.get("semantic_failure_key"))
                if value.get("semantic_failure_key") is not None
                else None
            ),
            usage=BudgetUsage.from_dict(raw_usage),
            case_count=(
                _non_negative_int(value.get("case_count"), field_name="case_count")
                if value.get("case_count") is not None
                else None
            ),
            distinct_conformance_shape_count=(
                _non_negative_int(
                    value.get("distinct_conformance_shape_count"),
                    field_name="distinct_conformance_shape_count",
                )
                if value.get("distinct_conformance_shape_count") is not None
                else None
            ),
            event_id=_identity(value.get("event_id"), field_name="event_id"),
        )


def validate_candidate_attempt_lifecycle(
    events: Sequence[CandidateAttemptEvent],
    *,
    require_terminal: bool = False,
) -> None:
    if not events:
        raise ValueError("candidate attempt lifecycle cannot be empty")
    key = events[0].key
    candidate_id = events[0].candidate_id
    if events[0].stage is not CandidateAttemptStage.GENERATED:
        raise ValueError("candidate attempt lifecycle must start at generated")
    seen_stages: set[CandidateAttemptStage] = set()
    for index, event in enumerate(events):
        if event.key != key:
            raise ValueError("candidate attempt lifecycle mixes attempt keys")
        if event.candidate_id != candidate_id:
            raise ValueError("candidate id cannot change within an attempt")
        if event.sequence != index:
            raise ValueError("candidate attempt sequence must be contiguous from zero")
        if event.stage in seen_stages:
            raise ValueError("candidate attempt stage cannot be emitted twice")
        seen_stages.add(event.stage)
        if index:
            previous = events[index - 1].stage
            if event.stage not in _ATTEMPT_TRANSITIONS[previous]:
                raise ValueError(
                    f"illegal candidate attempt transition: {previous.value} -> "
                    f"{event.stage.value}"
                )
    if require_terminal and not events[-1].terminal:
        raise ValueError("candidate attempt lifecycle is not terminal")


@dataclass(frozen=True)
class CandidateAttemptAggregate:
    attempt_count: int
    unique_candidate_count: int
    duplicate_attempt_count: int
    terminal_attempt_count: int
    stage_counts: Mapping[str, int]
    paired_replay_started_count: int
    paired_replay_completed_count: int
    paired_replay_comparable_count: int
    terminal_reason_counts: Mapping[str, int]
    per_stage_usage: Mapping[str, BudgetUsage]
    max_case_count: int
    max_distinct_conformance_shape_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CANDIDATE_ATTEMPT_AGGREGATE_SCHEMA_VERSION,
            "attempt_count": self.attempt_count,
            "unique_candidate_count": self.unique_candidate_count,
            "duplicate_attempt_count": self.duplicate_attempt_count,
            "terminal_attempt_count": self.terminal_attempt_count,
            "stage_counts": dict(sorted(self.stage_counts.items())),
            "paired_replay_started_count": self.paired_replay_started_count,
            "paired_replay_completed_count": self.paired_replay_completed_count,
            "paired_replay_comparable_count": self.paired_replay_comparable_count,
            "terminal_reason_counts": dict(sorted(self.terminal_reason_counts.items())),
            "per_stage_usage": {
                stage: usage.to_dict()
                for stage, usage in sorted(self.per_stage_usage.items())
            },
            "max_case_count": self.max_case_count,
            "max_distinct_conformance_shape_count": (
                self.max_distinct_conformance_shape_count
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CandidateAttemptAggregate":
        if value.get("schema_version") != CANDIDATE_ATTEMPT_AGGREGATE_SCHEMA_VERSION:
            raise ValueError("unsupported candidate attempt aggregate schema")
        raw_stage_counts = value.get("stage_counts")
        raw_reasons = value.get("terminal_reason_counts")
        raw_usage = value.get("per_stage_usage")
        if not all(
            isinstance(item, Mapping)
            for item in (raw_stage_counts, raw_reasons, raw_usage)
        ):
            raise ValueError("candidate attempt aggregate payload is malformed")
        assert isinstance(raw_stage_counts, Mapping)
        assert isinstance(raw_reasons, Mapping)
        assert isinstance(raw_usage, Mapping)
        stage_counts = {
            CandidateAttemptStage(str(stage)).value: _non_negative_int(
                count,
                field_name="stage count",
            )
            for stage, count in raw_stage_counts.items()
        }
        terminal_reasons = {
            _reason_code(str(reason), required=True) or "": _non_negative_int(
                count,
                field_name="terminal reason count",
            )
            for reason, count in raw_reasons.items()
        }
        per_stage_usage = {
            CandidateAttemptStage(str(stage)).value: BudgetUsage.from_dict(usage)
            for stage, usage in raw_usage.items()
            if isinstance(usage, Mapping)
        }
        if len(per_stage_usage) != len(raw_usage):
            raise ValueError("candidate attempt aggregate usage is malformed")
        aggregate = cls(
            attempt_count=_non_negative_int(
                value.get("attempt_count"),
                field_name="attempt_count",
            ),
            unique_candidate_count=_non_negative_int(
                value.get("unique_candidate_count"),
                field_name="unique_candidate_count",
            ),
            duplicate_attempt_count=_non_negative_int(
                value.get("duplicate_attempt_count"),
                field_name="duplicate_attempt_count",
            ),
            terminal_attempt_count=_non_negative_int(
                value.get("terminal_attempt_count"),
                field_name="terminal_attempt_count",
            ),
            stage_counts=stage_counts,
            paired_replay_started_count=_non_negative_int(
                value.get("paired_replay_started_count"),
                field_name="paired_replay_started_count",
            ),
            paired_replay_completed_count=_non_negative_int(
                value.get("paired_replay_completed_count"),
                field_name="paired_replay_completed_count",
            ),
            paired_replay_comparable_count=_non_negative_int(
                value.get("paired_replay_comparable_count"),
                field_name="paired_replay_comparable_count",
            ),
            terminal_reason_counts=terminal_reasons,
            per_stage_usage=per_stage_usage,
            max_case_count=_non_negative_int(
                value.get("max_case_count"),
                field_name="max_case_count",
            ),
            max_distinct_conformance_shape_count=_non_negative_int(
                value.get("max_distinct_conformance_shape_count"),
                field_name="max_distinct_conformance_shape_count",
            ),
        )
        if aggregate.paired_replay_started_count != stage_counts.get(
            CandidateAttemptStage.PAIRED_REPLAY_STARTED.value,
            0,
        ):
            raise ValueError("paired replay started count is inconsistent")
        if aggregate.paired_replay_completed_count != stage_counts.get(
            CandidateAttemptStage.PAIRED_REPLAY_COMPLETED.value,
            0,
        ):
            raise ValueError("paired replay completed count is inconsistent")
        if aggregate.paired_replay_comparable_count != stage_counts.get(
            CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE.value,
            0,
        ):
            raise ValueError("paired replay comparable count is inconsistent")
        return aggregate


def aggregate_candidate_attempts(
    events: Iterable[CandidateAttemptEvent],
) -> CandidateAttemptAggregate:
    grouped: dict[CandidateAttemptKey, list[CandidateAttemptEvent]] = {}
    for event in events:
        if not isinstance(event, CandidateAttemptEvent):
            raise TypeError("candidate attempt aggregation requires typed events")
        grouped.setdefault(event.key, []).append(event)
    stage_counts: dict[str, int] = {}
    terminal_reasons: dict[str, int] = {}
    stage_usage: dict[str, BudgetUsage] = {}
    unique_candidate_ids: set[str] = set()
    duplicate_attempt_count = 0
    terminal_attempt_count = 0
    max_case_count = 0
    max_shape_count = 0
    for key in sorted(grouped):
        attempt_events = sorted(grouped[key], key=lambda item: item.sequence)
        validate_candidate_attempt_lifecycle(attempt_events)
        for event in attempt_events:
            stage = event.stage.value
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            stage_usage[stage] = stage_usage.get(stage, BudgetUsage()) + event.usage
            max_case_count = max(max_case_count, event.case_count or 0)
            max_shape_count = max(
                max_shape_count,
                event.distinct_conformance_shape_count or 0,
            )
            if event.stage is CandidateAttemptStage.UNIQUE:
                unique_candidate_ids.add(event.candidate_id)
            elif event.stage is CandidateAttemptStage.DUPLICATE_FILTERED:
                duplicate_attempt_count += 1
        terminal = attempt_events[-1]
        if terminal.terminal:
            terminal_attempt_count += 1
            assert terminal.reason_code is not None
            terminal_reasons[terminal.reason_code] = (
                terminal_reasons.get(terminal.reason_code, 0) + 1
            )
    return CandidateAttemptAggregate(
        attempt_count=len(grouped),
        unique_candidate_count=len(unique_candidate_ids),
        duplicate_attempt_count=duplicate_attempt_count,
        terminal_attempt_count=terminal_attempt_count,
        stage_counts=stage_counts,
        paired_replay_started_count=stage_counts.get(
            CandidateAttemptStage.PAIRED_REPLAY_STARTED.value,
            0,
        ),
        paired_replay_completed_count=stage_counts.get(
            CandidateAttemptStage.PAIRED_REPLAY_COMPLETED.value,
            0,
        ),
        paired_replay_comparable_count=stage_counts.get(
            CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE.value,
            0,
        ),
        terminal_reason_counts=terminal_reasons,
        per_stage_usage=stage_usage,
        max_case_count=max_case_count,
        max_distinct_conformance_shape_count=max_shape_count,
    )


@dataclass(frozen=True)
class RepairFrontier:
    semantic_key: str
    progress: int
    owner: FailureOwner
    scope: FailureScope
    repairable: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "semantic_key",
            _identity(self.semantic_key, field_name="semantic_key"),
        )
        object.__setattr__(
            self,
            "progress",
            _non_negative_int(self.progress, field_name="progress"),
        )
        object.__setattr__(self, "owner", FailureOwner(self.owner))
        object.__setattr__(self, "scope", FailureScope(self.scope))
        if not isinstance(self.repairable, bool):
            raise TypeError("repairable must be boolean")

    @property
    def shared_blocking(self) -> bool:
        return (
            self.scope is FailureScope.SHARED_RUN
            and self.owner in {FailureOwner.INFRASTRUCTURE, FailureOwner.FRAMEWORK}
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "semantic_key": self.semantic_key,
            "progress": self.progress,
            "owner": self.owner.value,
            "scope": self.scope.value,
            "repairable": self.repairable,
        }


@dataclass(frozen=True)
class SchedulerState:
    initial_exploration_scheduled: bool = False
    untyped_frontier_exploration_scheduled: bool = False
    frontier_progress: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.initial_exploration_scheduled, bool):
            raise TypeError("initial_exploration_scheduled must be boolean")
        if not isinstance(self.untyped_frontier_exploration_scheduled, bool):
            raise TypeError("untyped frontier exploration flag must be boolean")
        normalized: dict[str, int] = {}
        for semantic_key, progress in self.frontier_progress.items():
            normalized[_identity(semantic_key, field_name="semantic_key")] = (
                _non_negative_int(progress, field_name="frontier progress")
            )
        object.__setattr__(self, "frontier_progress", normalized)

    def to_dict(self) -> dict[str, object]:
        return {
            "initial_exploration_scheduled": self.initial_exploration_scheduled,
            "untyped_frontier_exploration_scheduled": (
                self.untyped_frontier_exploration_scheduled
            ),
            "frontier_progress": dict(sorted(self.frontier_progress.items())),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SchedulerState":
        raw_progress = value.get("frontier_progress", {})
        if not isinstance(raw_progress, Mapping):
            raise ValueError("scheduler frontier_progress must be a mapping")
        return cls(
            initial_exploration_scheduled=(
                value.get("initial_exploration_scheduled") is True
            ),
            untyped_frontier_exploration_scheduled=(
                value.get("untyped_frontier_exploration_scheduled") is True
            ),
            frontier_progress={
                _identity(key, field_name="semantic_key"): _non_negative_int(
                    progress,
                    field_name="frontier progress",
                )
                for key, progress in raw_progress.items()
            },
        )


class ScheduledSlotRole(str, Enum):
    INITIAL_EXPLORATION = "initial_exploration"
    FOCUSED_REPAIR = "focused_repair"
    DIVERSE_EXPLORATION = "diverse_exploration"
    BOUNDED_EXPLORATION = "bounded_exploration"


@dataclass(frozen=True)
class ScheduledCandidateSlot:
    slot: int
    role: ScheduledSlotRole
    semantic_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot", _non_negative_int(self.slot, field_name="slot"))
        object.__setattr__(self, "role", ScheduledSlotRole(self.role))
        if self.semantic_key is not None:
            object.__setattr__(
                self,
                "semantic_key",
                _identity(self.semantic_key, field_name="semantic_key"),
            )
        if (
            self.role is ScheduledSlotRole.FOCUSED_REPAIR
            and self.semantic_key is None
        ):
            raise ValueError("focused repair slot requires a typed semantic key")
        if (
            self.role is not ScheduledSlotRole.FOCUSED_REPAIR
            and self.semantic_key is not None
        ):
            raise ValueError("only focused repair slots may carry a semantic key")

    def to_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "role": self.role.value,
            "semantic_key": self.semantic_key,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ScheduledCandidateSlot":
        return cls(
            slot=_non_negative_int(value.get("slot"), field_name="slot"),
            role=ScheduledSlotRole(str(value.get("role"))),
            semantic_key=(
                _identity(value.get("semantic_key"), field_name="semantic_key")
                if value.get("semantic_key") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class SchedulerDecision:
    reason_code: str
    slots: tuple[ScheduledCandidateSlot, ...]
    stop: bool
    state: SchedulerState

    def __post_init__(self) -> None:
        _reason_code(self.reason_code, required=True)
        if any(not isinstance(slot, ScheduledCandidateSlot) for slot in self.slots):
            raise TypeError("scheduler decision slots must be typed")
        if not isinstance(self.stop, bool):
            raise TypeError("scheduler stop must be boolean")
        if not isinstance(self.state, SchedulerState):
            raise TypeError("scheduler decision state must be typed")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEDULER_DECISION_SCHEMA_VERSION,
            "reason_code": self.reason_code,
            "slots": [slot.to_dict() for slot in self.slots],
            "stop": self.stop,
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SchedulerDecision":
        if value.get("schema_version") != SCHEDULER_DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported scheduler decision schema")
        raw_slots = value.get("slots")
        raw_state = value.get("state")
        if not isinstance(raw_slots, list) or not isinstance(raw_state, Mapping):
            raise ValueError("scheduler decision payload is malformed")
        slots = tuple(
            ScheduledCandidateSlot.from_dict(item)
            for item in raw_slots
            if isinstance(item, Mapping)
        )
        if len(slots) != len(raw_slots):
            raise ValueError("scheduler decision contains invalid slot")
        if tuple(slot.slot for slot in slots) != tuple(range(len(slots))):
            raise ValueError("scheduler decision slots must be contiguous")
        return cls(
            reason_code=_reason_code(value.get("reason_code"), required=True) or "",
            slots=slots,
            stop=value.get("stop") is True,
            state=SchedulerState.from_dict(raw_state),
        )


@dataclass(frozen=True)
class StageAwareCandidateScheduler:
    exploration_population: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "exploration_population",
            _positive_int(
                self.exploration_population,
                field_name="exploration_population",
            ),
        )

    def schedule(
        self,
        *,
        state: SchedulerState,
        frontiers: Sequence[RepairFrontier],
        focused_budget_available: bool = True,
        diverse_budget_available: bool = False,
        untyped_feedback_present: bool = False,
    ) -> SchedulerDecision:
        if not isinstance(state, SchedulerState):
            raise TypeError("scheduler state must be typed")
        if any(not isinstance(item, RepairFrontier) for item in frontiers):
            raise TypeError("scheduler frontiers must be typed")
        if any(item.shared_blocking for item in frontiers):
            return SchedulerDecision(
                reason_code="shared_run_blocked",
                slots=(),
                stop=True,
                state=state,
            )
        repairable = tuple(
            item
            for item in frontiers
            if item.repairable and item.owner is FailureOwner.CANDIDATE
        )
        if not state.initial_exploration_scheduled and not repairable:
            next_state = SchedulerState(
                initial_exploration_scheduled=True,
                untyped_frontier_exploration_scheduled=(
                    state.untyped_frontier_exploration_scheduled
                ),
                frontier_progress=state.frontier_progress,
            )
            return SchedulerDecision(
                reason_code="initial_exploration",
                slots=tuple(
                    ScheduledCandidateSlot(
                        slot=index,
                        role=ScheduledSlotRole.INITIAL_EXPLORATION,
                    )
                    for index in range(self.exploration_population)
                ),
                stop=False,
                state=next_state,
            )
        if (
            not repairable
            and untyped_feedback_present
            and not state.untyped_frontier_exploration_scheduled
            and focused_budget_available
        ):
            return SchedulerDecision(
                reason_code="bounded_exploration_without_typed_frontier",
                slots=(
                    ScheduledCandidateSlot(
                        slot=0,
                        role=ScheduledSlotRole.BOUNDED_EXPLORATION,
                    ),
                ),
                stop=False,
                state=SchedulerState(
                    initial_exploration_scheduled=True,
                    untyped_frontier_exploration_scheduled=True,
                    frontier_progress=state.frontier_progress,
                ),
            )
        if not repairable:
            return SchedulerDecision(
                reason_code="no_repairable_frontier",
                slots=(),
                stop=False,
                state=state,
            )
        updated_progress = dict(state.frontier_progress)
        new_frontier = False
        for frontier in repairable:
            previous = updated_progress.get(frontier.semantic_key, -1)
            if frontier.progress > previous:
                new_frontier = True
                updated_progress[frontier.semantic_key] = frontier.progress
        next_state = SchedulerState(
            initial_exploration_scheduled=True,
            untyped_frontier_exploration_scheduled=(
                state.untyped_frontier_exploration_scheduled
            ),
            frontier_progress=updated_progress,
        )
        if not focused_budget_available:
            return SchedulerDecision(
                reason_code="focused_budget_denied",
                slots=(),
                stop=False,
                state=next_state,
            )
        focused = sorted(
            repairable,
            key=lambda item: (item.progress, item.semantic_key),
        )[-1]
        slots = [
            ScheduledCandidateSlot(
                slot=0,
                role=ScheduledSlotRole.FOCUSED_REPAIR,
                semantic_key=focused.semantic_key,
            )
        ]
        if (
            new_frontier
            and diverse_budget_available
            and self.exploration_population > 1
        ):
            slots.append(
                ScheduledCandidateSlot(
                    slot=1,
                    role=ScheduledSlotRole.DIVERSE_EXPLORATION,
                    semantic_key=None,
                )
            )
        return SchedulerDecision(
            reason_code=(
                "focused_repair_with_diversity"
                if len(slots) == 2
                else "focused_repair"
            ),
            slots=tuple(slots),
            stop=False,
            state=next_state,
        )
