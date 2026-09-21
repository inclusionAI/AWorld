"""Pure controlled-measurement reporting primitives."""

from __future__ import annotations

import math


def _finite_measurement_metric(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _optional_measurement_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _non_negative_measurement_int(value: object) -> int | None:
    numeric = _finite_measurement_metric(value)
    if numeric is None or numeric < 0 or not numeric.is_integer():
        return None
    return int(numeric)


def _non_negative_measurement_float(value: object) -> float | None:
    numeric = _finite_measurement_metric(value)
    return numeric if numeric is not None and numeric >= 0 else None


def _budget_curve_points(total: int | float) -> tuple[int | float, ...]:
    if total <= 0:
        return (0,)
    return tuple(sorted({total / 4, total / 2, total}))
