"""Normalize original provider usage without legacy synthetic zero defaults."""

from aworld.core.agent.usage import TokenUsage


def parse_usage(raw):
    if not isinstance(raw, dict):
        return None
    invalid = []

    def read(*paths):
        values = []
        for path in paths:
            value = raw
            for key in path.split("."):
                if not isinstance(value, dict) or key not in value:
                    break
                value = value[key]
            else:
                # Optional SDK fields serialized as null are not measurements.
                if value is None:
                    continue
                if type(value) is not int or value < 0:
                    invalid.append(path)
                    return None
                values.append(value)
        if len(set(values)) > 1:
            invalid.extend(paths)
            return None
        return values[0] if values else None

    input_tokens = read("prompt_tokens", "input_tokens")
    output_tokens = read("completion_tokens", "output_tokens")
    cache_read = read("prompt_tokens_details.cached_tokens", "input_tokens_details.cached_tokens",
                      "prompt_cache_hit_tokens", "cache_hit_tokens", "cache_read_input_tokens")
    cache_write = read("cache_write_tokens", "cache_creation_input_tokens",
                       "prompt_tokens_details.cache_creation_input_tokens", "input_tokens_details.cache_creation_input_tokens")
    reasoning = read("completion_tokens_details.reasoning_tokens", "output_tokens_details.reasoning_tokens")
    # Anthropic input is uncached; OpenAI/DeepSeek prompt_tokens is inclusive.
    if "prompt_tokens" not in raw and "input_tokens" in raw and any(
            key in raw for key in ("cache_read_input_tokens", "cache_creation_input_tokens")):
        if input_tokens is not None:
            components = [cache_read if raw.get("cache_read_input_tokens") is not None else 0,
                          cache_write if raw.get("cache_creation_input_tokens") is not None else 0]
            input_tokens = input_tokens + sum(components) if None not in components else None
    if input_tokens is not None:
        if cache_read is not None and cache_read > input_tokens:
            invalid.append("cache_read_tokens")
            cache_read = None
        if cache_write is not None and cache_write > input_tokens:
            invalid.append("cache_write_tokens")
            cache_write = None
    if reasoning is not None and output_tokens is not None and reasoning > output_tokens:
        invalid.append("reasoning_tokens")
        reasoning = None
    total = read("total_tokens")
    if total is not None and input_tokens is not None and output_tokens is not None and total != input_tokens + output_tokens:
        invalid.append("total_tokens")
    return TokenUsage(input_tokens, output_tokens, cache_read, cache_write, reasoning, tuple(invalid))
