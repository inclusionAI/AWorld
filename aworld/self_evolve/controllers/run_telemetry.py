"""Neutral telemetry accounting helpers shared by execution controllers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from aworld.self_evolve.budget import (
    BudgetUsage,
    BudgetUsageCompleteness,
    BudgetUsageObservation,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry


@dataclass(frozen=True)
class TelemetryUsageSnapshot:
    batches: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True)
class TelemetryUsageDelta:
    observation: BudgetUsageObservation
    source: str


def decimal_metric(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() and result >= 0 else None


def sanitized_telemetry_usage_batch(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Retain only bounded accounting fields from a telemetry batch."""

    result: dict[str, object] = {}
    token_usage = value.get("token_usage")
    if isinstance(token_usage, Mapping):
        result["token_usage"] = {
            key: item
            for key, item in token_usage.items()
            if key
            in {
                "total_tokens",
                "input_tokens",
                "output_tokens",
                "prompt_tokens",
                "completion_tokens",
            }
            and isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
        }
    for key in (
        "total_cost_usd",
        "cost_usd",
        "elapsed_seconds",
        "execution_seconds",
    ):
        item = decimal_metric(value.get(key))
        if item is not None:
            result[key] = str(item)
    return result


def stage_telemetry_usage_snapshot(
    telemetry: SelfEvolveExecutionTelemetry,
    stage: str,
) -> TelemetryUsageSnapshot:
    """Capture a stable cursor over sanitized per-batch stage telemetry."""

    report = telemetry.to_report()
    stage_report = report.get(stage)
    if not isinstance(stage_report, Mapping):
        return TelemetryUsageSnapshot()
    batches = stage_report.get("batches")
    if not isinstance(batches, (list, tuple)):
        return TelemetryUsageSnapshot()
    return TelemetryUsageSnapshot(
        batches=tuple(
            sanitized_telemetry_usage_batch(item)
            for item in batches
            if isinstance(item, Mapping)
        )
    )


def canonical_batch_token_usage(batch: Mapping[str, object]) -> int | None:
    usage = batch.get("token_usage")
    if not isinstance(usage, Mapping):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    for input_key, output_key in (
        ("input_tokens", "output_tokens"),
        ("prompt_tokens", "completion_tokens"),
    ):
        input_tokens = usage.get(input_key)
        output_tokens = usage.get(output_key)
        if all(
            isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
            for item in (input_tokens, output_tokens)
        ):
            return int(input_tokens) + int(output_tokens)
    return None


def canonical_batch_decimal_usage(
    batch: Mapping[str, object],
    *keys: str,
) -> Decimal | None:
    for key in keys:
        value = decimal_metric(batch.get(key))
        if value is not None:
            return value
    return None


def stage_telemetry_usage_delta(
    before: TelemetryUsageSnapshot,
    after: TelemetryUsageSnapshot,
) -> TelemetryUsageDelta:
    cursor = len(before.batches)
    if len(after.batches) <= cursor or after.batches[:cursor] != before.batches:
        return TelemetryUsageDelta(
            observation=BudgetUsageObservation(
                known_lower_bound=BudgetUsage(),
                completeness=BudgetUsageCompleteness.incomplete(),
            ),
            source="reserved_fallback_missing_stage_telemetry_delta",
        )
    new_batches = after.batches[cursor:]
    batch_tokens = tuple(canonical_batch_token_usage(batch) for batch in new_batches)
    batch_costs = tuple(
        canonical_batch_decimal_usage(batch, "total_cost_usd", "cost_usd")
        for batch in new_batches
    )
    batch_walls = tuple(
        canonical_batch_decimal_usage(batch, "elapsed_seconds", "execution_seconds")
        for batch in new_batches
    )
    token_complete = all(value is not None for value in batch_tokens)
    cost_complete = all(value is not None for value in batch_costs)
    wall_complete = all(value is not None for value in batch_walls)
    observed = []
    for name, values, complete in (
        ("tokens", batch_tokens, token_complete),
        ("cost_usd", batch_costs, cost_complete),
        ("wall_seconds", batch_walls, wall_complete),
    ):
        if complete:
            observed.append(name)
        elif any(value is not None for value in values):
            observed.append(f"{name}_lower_bound")
    return TelemetryUsageDelta(
        observation=BudgetUsageObservation(
            known_lower_bound=BudgetUsage(
                tokens=sum(int(value) for value in batch_tokens if value is not None),
                cost_usd=sum(
                    (value for value in batch_costs if value is not None), Decimal("0")
                ),
                wall_seconds=sum(
                    (value for value in batch_walls if value is not None), Decimal("0")
                ),
            ),
            completeness=BudgetUsageCompleteness(
                tokens=token_complete,
                cost_usd=cost_complete,
                wall_seconds=wall_complete,
            ),
        ),
        source=(
            "telemetry_delta_" + "+".join(observed)
            if observed
            else "reserved_fallback_missing_stage_telemetry_delta"
        ),
    )


def telemetry_usage_with_observed_wall(
    usage: TelemetryUsageDelta,
    *,
    elapsed_seconds: float,
) -> TelemetryUsageDelta:
    """Complete wall accounting even when a stage exits before telemetry starts."""

    observation = usage.observation
    lower_bound = observation.known_lower_bound
    observed_wall = Decimal(str(max(0.0, elapsed_seconds)))
    source = usage.source
    if not observation.completeness.wall_seconds:
        source = (
            "observed_stage_elapsed_seconds"
            if source == "reserved_fallback_missing_stage_telemetry_delta"
            else f"{source}+observed_stage_elapsed_seconds"
        )
    return TelemetryUsageDelta(
        observation=BudgetUsageObservation(
            known_lower_bound=BudgetUsage(
                tokens=lower_bound.tokens,
                cost_usd=lower_bound.cost_usd,
                wall_seconds=max(lower_bound.wall_seconds, observed_wall),
            ),
            completeness=BudgetUsageCompleteness(
                tokens=observation.completeness.tokens,
                cost_usd=observation.completeness.cost_usd,
                wall_seconds=True,
            ),
        ),
        source=source,
    )


# Private compatibility aliases used by historical tests and controller imports.
_TelemetryUsageSnapshot = TelemetryUsageSnapshot
_TelemetryUsageDelta = TelemetryUsageDelta
_decimal_metric = decimal_metric
_sanitized_telemetry_usage_batch = sanitized_telemetry_usage_batch
_stage_telemetry_usage_snapshot = stage_telemetry_usage_snapshot
_canonical_batch_token_usage = canonical_batch_token_usage
_canonical_batch_decimal_usage = canonical_batch_decimal_usage
_stage_telemetry_usage_delta = stage_telemetry_usage_delta
_telemetry_usage_with_observed_wall = telemetry_usage_with_observed_wall
