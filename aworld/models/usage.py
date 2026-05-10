# coding: utf-8

import copy
from typing import Any, Dict, Union


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


def normalize_usage(usage: Dict[str, Union[int, Dict[str, int]]] | None = None) -> Dict[str, Any]:
    """Normalize provider-specific token usage into the common AWorld schema."""
    if not isinstance(usage, dict):
        return {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }

    normalized = copy.deepcopy(usage)
    normalized["completion_tokens"] = _coerce_int(normalized.get("completion_tokens"))
    normalized["prompt_tokens"] = _coerce_int(normalized.get("prompt_tokens"))
    normalized["total_tokens"] = _coerce_int(normalized.get("total_tokens"))

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

    cache_write_tokens = normalized.get("cache_write_tokens")
    if cache_write_tokens is None:
        cache_write_tokens = normalized.get("cache_creation_input_tokens")

    if cache_hit_tokens is not None:
        normalized["cache_hit_tokens"] = _coerce_int(cache_hit_tokens)
    if cache_write_tokens is not None:
        normalized["cache_write_tokens"] = _coerce_int(cache_write_tokens)

    normalized.pop("cache_read_input_tokens", None)
    normalized.pop("cache_creation_input_tokens", None)

    return normalized
