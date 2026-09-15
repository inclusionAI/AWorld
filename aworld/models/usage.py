# coding: utf-8

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Union


class CacheUsageFidelity(str, Enum):
    """Confidence attached to one provider call's cache accounting."""

    EXACT = "exact"
    BOUNDED = "bounded"
    CONFLICTING = "conflicting"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CacheUsageReceipt:
    """Provider-neutral, non-coercing cache usage evidence for one call.

    Compatibility token summaries historically convert missing values to zero.
    This receipt deliberately keeps an exact zero distinct from absent provider
    data so cache experiments cannot manufacture hits or misses.
    """

    fidelity: CacheUsageFidelity
    reason_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cache_read_lower_bound: int | None
    cache_read_upper_bound: int | None
    reported_input_tokens: int | None = None
    input_token_accounting: str | None = None
    raw_cache_sources: tuple[str, ...] = ()
    normalized_cache_sources: tuple[str, ...] = ()

    SCHEMA_VERSION = "aworld.cache-usage-receipt.v1"

    @property
    def uncached_input_tokens(self) -> int | None:
        if self.input_tokens is None or self.cache_read_tokens is None:
            return None
        return self.input_tokens - self.cache_read_tokens

    @property
    def cache_read_ratio(self) -> float | None:
        if self.input_tokens is None or self.cache_read_tokens is None:
            return None
        if self.input_tokens == 0:
            return 0.0
        return self.cache_read_tokens / self.input_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "fidelity": self.fidelity.value,
            "reason_code": self.reason_code,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_lower_bound": self.cache_read_lower_bound,
            "cache_read_upper_bound": self.cache_read_upper_bound,
            "reported_input_tokens": self.reported_input_tokens,
            "input_token_accounting": self.input_token_accounting,
            "uncached_input_tokens": self.uncached_input_tokens,
            "cache_read_ratio": self.cache_read_ratio,
            "raw_cache_sources": list(self.raw_cache_sources),
            "normalized_cache_sources": list(self.normalized_cache_sources),
        }


_INPUT_PATHS = (("prompt_tokens",), ("input_tokens",))
_OUTPUT_PATHS = (("completion_tokens",), ("output_tokens",))
_TOTAL_PATHS = (("total_tokens",),)
_CACHE_READ_PATHS = (
    ("cache_hit_tokens",),
    ("cache_read_input_tokens",),
    ("prompt_tokens_details", "cached_tokens"),
    ("input_tokens_details", "cached_tokens"),
)
_CACHE_WRITE_PATHS = (
    ("cache_write_tokens",),
    ("cache_creation_input_tokens",),
    ("prompt_tokens_details", "cache_creation_input_tokens"),
    ("input_tokens_details", "cache_creation_input_tokens"),
)


def _strict_values_at_paths(
    usage: Dict[str, Any], paths: tuple[tuple[str, ...], ...]
) -> tuple[list[int], tuple[str, ...], bool]:
    values: list[int] = []
    sources: list[str] = []
    invalid = False
    for path in paths:
        current: Any = usage
        present = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                present = False
                break
            current = current[key]
        if not present:
            continue
        sources.append(".".join(path))
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            invalid = True
            continue
        values.append(current)
    return values, tuple(sources), invalid


def _strict_single_value(
    usage: Dict[str, Any], paths: tuple[tuple[str, ...], ...]
) -> tuple[int | None, tuple[str, ...], str | None]:
    values, sources, invalid = _strict_values_at_paths(usage, paths)
    if invalid:
        return None, sources, "invalid"
    if not sources:
        return None, (), "missing"
    if len(values) != len(sources) or len(set(values)) != 1:
        return None, sources, "conflicting"
    return values[0], sources, None


def _receipt(
    *,
    fidelity: CacheUsageFidelity,
    reason_code: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    cache_read_lower_bound: int | None = None,
    cache_read_upper_bound: int | None = None,
    reported_input_tokens: int | None = None,
    input_token_accounting: str | None = None,
    raw_cache_sources: tuple[str, ...] = (),
    normalized_cache_sources: tuple[str, ...] = (),
) -> CacheUsageReceipt:
    return CacheUsageReceipt(
        fidelity=fidelity,
        reason_code=reason_code,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_lower_bound=cache_read_lower_bound,
        cache_read_upper_bound=cache_read_upper_bound,
        reported_input_tokens=reported_input_tokens,
        input_token_accounting=input_token_accounting,
        raw_cache_sources=raw_cache_sources,
        normalized_cache_sources=normalized_cache_sources,
    )


def _uses_component_input_accounting(usage: Dict[str, Any]) -> bool:
    """Whether cache components are additional to the reported input value.

    This is inferred from field semantics, never provider identity.  Schemas
    with only ``input_tokens`` plus top-level cache read/write components report
    those components separately; schemas with ``prompt_tokens`` report an
    inclusive prompt total and expose cached tokens as details/subsets.
    """
    return bool(
        "input_tokens" in usage
        and "prompt_tokens" not in usage
        and any(
            name in usage
            for name in ("cache_read_input_tokens", "cache_creation_input_tokens")
        )
    )


def build_cache_usage_receipt(
    *,
    raw_usage: Dict[str, Any] | None,
    normalized_usage: Dict[str, Any] | None = None,
) -> CacheUsageReceipt:
    """Validate cache usage without assuming a particular provider schema."""
    if not isinstance(raw_usage, dict) or not raw_usage:
        return _receipt(
            fidelity=CacheUsageFidelity.UNAVAILABLE,
            reason_code="provider_usage_unavailable",
        )

    reported_raw_input, _, raw_input_error = _strict_single_value(
        raw_usage, _INPUT_PATHS
    )
    raw_output, _, raw_output_error = _strict_single_value(raw_usage, _OUTPUT_PATHS)
    if "invalid" in {raw_input_error, raw_output_error}:
        return _receipt(
            fidelity=CacheUsageFidelity.INVALID,
            reason_code="provider_token_usage_invalid",
        )
    if "conflicting" in {raw_input_error, raw_output_error}:
        return _receipt(
            fidelity=CacheUsageFidelity.CONFLICTING,
            reason_code="provider_token_usage_conflicting_aliases",
        )
    if reported_raw_input is None or raw_output is None:
        return _receipt(
            fidelity=CacheUsageFidelity.UNAVAILABLE,
            reason_code="provider_token_usage_missing",
            input_tokens=reported_raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
        )

    raw_cache, raw_sources, raw_cache_error = _strict_single_value(
        raw_usage, _CACHE_READ_PATHS
    )
    if raw_cache_error == "invalid":
        return _receipt(
            fidelity=CacheUsageFidelity.INVALID,
            reason_code="provider_cache_usage_invalid",
            input_tokens=reported_raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            raw_cache_sources=raw_sources,
        )
    if raw_cache_error == "conflicting":
        return _receipt(
            fidelity=CacheUsageFidelity.CONFLICTING,
            reason_code="provider_cache_usage_conflicting_aliases",
            input_tokens=reported_raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            raw_cache_sources=raw_sources,
        )
    if raw_cache is None:
        return _receipt(
            fidelity=CacheUsageFidelity.BOUNDED,
            reason_code="provider_cache_usage_missing",
            input_tokens=reported_raw_input,
            output_tokens=raw_output,
            cache_read_lower_bound=0,
            cache_read_upper_bound=reported_raw_input,
            reported_input_tokens=reported_raw_input,
            input_token_accounting="unknown",
        )

    raw_write, _, raw_write_error = _strict_single_value(
        raw_usage, _CACHE_WRITE_PATHS
    )
    if raw_write_error in {"invalid", "conflicting"}:
        return _receipt(
            fidelity=(
                CacheUsageFidelity.INVALID
                if raw_write_error == "invalid"
                else CacheUsageFidelity.CONFLICTING
            ),
            reason_code="provider_cache_write_usage_invalid_or_conflicting",
            input_tokens=reported_raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            raw_cache_sources=raw_sources,
        )

    component_accounting = _uses_component_input_accounting(raw_usage)
    raw_input = reported_raw_input + (
        raw_cache + (raw_write or 0) if component_accounting else 0
    )
    input_token_accounting = (
        "exclusive_cache_components" if component_accounting else "inclusive"
    )
    if raw_cache > raw_input:
        return _receipt(
            fidelity=CacheUsageFidelity.INVALID,
            reason_code="provider_cache_usage_exceeds_input",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
            raw_cache_sources=raw_sources,
        )

    total, _, total_error = _strict_single_value(raw_usage, _TOTAL_PATHS)
    if total_error == "invalid":
        return _receipt(
            fidelity=CacheUsageFidelity.INVALID,
            reason_code="provider_total_usage_invalid",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
        )
    if total is not None and total != raw_input + raw_output:
        return _receipt(
            fidelity=CacheUsageFidelity.CONFLICTING,
            reason_code="provider_total_usage_conflict",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
        )

    supplied_normalized = (
        normalized_usage
        if isinstance(normalized_usage, dict) and normalized_usage
        else normalize_usage(raw_usage)
    )
    normalized_input, _, normalized_input_error = _strict_single_value(
        supplied_normalized, _INPUT_PATHS
    )
    normalized_output, _, normalized_output_error = _strict_single_value(
        supplied_normalized, _OUTPUT_PATHS
    )
    normalized_cache, normalized_sources, normalized_cache_error = _strict_single_value(
        supplied_normalized, _CACHE_READ_PATHS
    )
    normalized_write, _, normalized_write_error = _strict_single_value(
        supplied_normalized, _CACHE_WRITE_PATHS
    )
    normalized_errors = {
        normalized_input_error,
        normalized_output_error,
        normalized_cache_error,
        normalized_write_error,
    }
    if "invalid" in normalized_errors:
        return _receipt(
            fidelity=CacheUsageFidelity.INVALID,
            reason_code="normalized_usage_invalid",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
            raw_cache_sources=raw_sources,
            normalized_cache_sources=normalized_sources,
        )
    if "conflicting" in normalized_errors:
        return _receipt(
            fidelity=CacheUsageFidelity.CONFLICTING,
            reason_code="normalized_usage_conflicting_aliases",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
            raw_cache_sources=raw_sources,
            normalized_cache_sources=normalized_sources,
        )

    if normalized_cache is None:
        normalized_cache = raw_cache
    if normalized_write is None:
        normalized_write = raw_write
    normalized_logical_input = normalized_input
    if normalized_input is not None and _uses_component_input_accounting(
        supplied_normalized
    ):
        normalized_logical_input += normalized_cache + (normalized_write or 0)
    if (
        normalized_logical_input is not None
        and normalized_logical_input != raw_input
        or normalized_output is not None
        and normalized_output != raw_output
    ):
        return _receipt(
            fidelity=CacheUsageFidelity.CONFLICTING,
            reason_code="provider_token_usage_conflicting_views",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
            raw_cache_sources=raw_sources,
            normalized_cache_sources=normalized_sources,
        )
    if normalized_cache != raw_cache:
        return _receipt(
            fidelity=CacheUsageFidelity.CONFLICTING,
            reason_code="provider_cache_usage_conflicting_views",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
            raw_cache_sources=raw_sources,
            normalized_cache_sources=normalized_sources,
        )
    if normalized_write != raw_write:
        return _receipt(
            fidelity=CacheUsageFidelity.CONFLICTING,
            reason_code="provider_cache_write_usage_conflicting_views",
            input_tokens=raw_input,
            output_tokens=raw_output,
            reported_input_tokens=reported_raw_input,
            input_token_accounting=input_token_accounting,
            raw_cache_sources=raw_sources,
            normalized_cache_sources=normalized_sources,
        )

    return _receipt(
        fidelity=CacheUsageFidelity.EXACT,
        reason_code=None,
        input_tokens=raw_input,
        output_tokens=raw_output,
        cache_read_tokens=raw_cache,
        cache_write_tokens=raw_write,
        cache_read_lower_bound=raw_cache,
        cache_read_upper_bound=raw_cache,
        reported_input_tokens=reported_raw_input,
        input_token_accounting=input_token_accounting,
        raw_cache_sources=raw_sources,
        normalized_cache_sources=normalized_sources,
    )


def reconcile_cache_usage_receipt(
    *,
    captured_receipt: Any,
    raw_usage: Dict[str, Any] | None,
    normalized_usage: Dict[str, Any] | None = None,
) -> CacheUsageReceipt:
    """Verify a captured receipt against provider usage, rebuilding old records."""
    recomputed = build_cache_usage_receipt(
        raw_usage=raw_usage,
        normalized_usage=normalized_usage,
    )
    if captured_receipt is None:
        return recomputed
    if isinstance(captured_receipt, dict) and captured_receipt == recomputed.to_dict():
        return recomputed
    return _receipt(
        fidelity=CacheUsageFidelity.CONFLICTING,
        reason_code="captured_cache_usage_receipt_mismatch",
        input_tokens=recomputed.input_tokens,
        output_tokens=recomputed.output_tokens,
        cache_read_lower_bound=recomputed.cache_read_lower_bound,
        cache_read_upper_bound=recomputed.cache_read_upper_bound,
        reported_input_tokens=recomputed.reported_input_tokens,
        input_token_accounting=recomputed.input_token_accounting,
        raw_cache_sources=recomputed.raw_cache_sources,
        normalized_cache_sources=recomputed.normalized_cache_sources,
    )


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _lookup_usage_int(usage: Dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in usage:
            return _coerce_int(usage.get(key))

    for detail_key in ("prompt_tokens_details", "input_tokens_details"):
        details = usage.get(detail_key)
        if not isinstance(details, dict):
            continue
        for key in keys:
            if key in details:
                return _coerce_int(details.get(key))
    return None


def summarize_prompt_cache_usage(usage: Dict[str, Union[int, Dict[str, int]]] | None = None) -> Dict[str, Any]:
    """Build a concise prompt-cache summary for task-level logging."""
    if not isinstance(usage, dict):
        return {}

    normalized = normalize_usage(usage)
    prompt_tokens = _coerce_int(normalized.get("prompt_tokens"))
    cache_read_tokens = _lookup_usage_int(usage, "cache_read_input_tokens") or 0
    cache_write_tokens = _lookup_usage_int(usage, "cache_write_tokens", "cache_creation_input_tokens") or 0
    cache_related_tokens = _lookup_usage_int(usage, "cache_related_tokens", "cache_hit_tokens", "cached_tokens")
    if cache_related_tokens is None:
        cache_related_tokens = cache_read_tokens + cache_write_tokens

    if cache_read_tokens <= 0 and cache_write_tokens <= 0 and cache_related_tokens <= 0:
        return {}

    def _ratio(value: int) -> float:
        if prompt_tokens <= 0:
            return 0.0
        return round(value / prompt_tokens, 4)

    return {
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cache_related_tokens": cache_related_tokens,
        "cache_read_ratio": _ratio(cache_read_tokens),
        "cache_write_ratio": _ratio(cache_write_tokens),
        "cache_related_ratio": _ratio(cache_related_tokens),
    }


def normalize_usage(usage: Dict[str, Union[int, Dict[str, int]]] | None = None) -> Dict[str, Any]:
    """Normalize provider-specific token usage into the common AWorld schema."""
    if not isinstance(usage, dict):
        return {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }

    normalized = copy.deepcopy(usage)
    component_accounting = _uses_component_input_accounting(normalized)
    cache_read_tokens = _lookup_usage_int(
        normalized, "cache_hit_tokens", "cache_read_input_tokens", "cached_tokens"
    )
    cache_write_tokens = _lookup_usage_int(
        normalized, "cache_write_tokens", "cache_creation_input_tokens"
    )
    if "prompt_tokens" in normalized:
        prompt_tokens = _coerce_int(normalized.get("prompt_tokens"))
    else:
        prompt_tokens = _coerce_int(normalized.get("input_tokens"))
        if component_accounting:
            prompt_tokens += (cache_read_tokens or 0) + (cache_write_tokens or 0)
    completion_tokens = _coerce_int(
        normalized.get("completion_tokens", normalized.get("output_tokens"))
    )
    normalized["completion_tokens"] = completion_tokens
    normalized["prompt_tokens"] = prompt_tokens
    normalized["total_tokens"] = _coerce_int(
        normalized.get("total_tokens", prompt_tokens + completion_tokens)
    )
    if "input_tokens" in normalized:
        normalized["input_tokens"] = prompt_tokens
    if "output_tokens" in normalized:
        normalized["output_tokens"] = completion_tokens

    cache_hit_tokens = normalized.get("cache_hit_tokens")
    if cache_hit_tokens is None:
        cache_hit_tokens = normalized.get("cache_read_input_tokens")
    if cache_hit_tokens is None:
        prompt_details = normalized.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cache_hit_tokens = prompt_details.get("cached_tokens")
    if cache_hit_tokens is None:
        input_details = normalized.get("input_tokens_details")
        if isinstance(input_details, dict):
            cache_hit_tokens = input_details.get("cached_tokens")

    normalized_cache_write_tokens = normalized.get("cache_write_tokens")
    if normalized_cache_write_tokens is None:
        normalized_cache_write_tokens = normalized.get(
            "cache_creation_input_tokens"
        )

    if cache_hit_tokens is not None:
        normalized["cache_hit_tokens"] = _coerce_int(cache_hit_tokens)
    if normalized_cache_write_tokens is not None:
        normalized["cache_write_tokens"] = _coerce_int(
            normalized_cache_write_tokens
        )

    normalized.pop("cache_read_input_tokens", None)
    normalized.pop("cache_creation_input_tokens", None)

    return normalized
