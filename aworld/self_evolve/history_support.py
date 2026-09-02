"""Controller-free JSON loading and persisted numeric normalization."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _non_negative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _non_negative_numeric_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _non_negative_screening_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return 0.0
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return 0.0
    return result
