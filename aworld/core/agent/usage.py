"""Per-call provider receipts. Unknown token counts stay unknown."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TokenUsage:
    # Input includes cache reads/writes; reasoning is a subset of output.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    invalid_fields: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
        for name, total in (("cache_read_tokens", self.input_tokens), ("cache_write_tokens", self.input_tokens),
                            ("reasoning_tokens", self.output_tokens)):
            value = getattr(self, name)
            if value is not None and total is not None and value > total:
                raise ValueError(f"{name} exceeds its inclusive token total")

    @property
    def total_tokens(self):
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    def to_dict(self):
        return {**asdict(self), "total_tokens": self.total_tokens}


class ModelResponseError(ValueError):
    """Rejected output can still have a billable provider receipt."""

    def __init__(self, message, *, usage=None):
        super().__init__(message)
        self.usage = usage


def summarize_usage(receipts):
    """Exact totals require coverage; measured subtotals remain inspectable."""
    receipts = list(receipts)
    totals, reported = {}, {}
    for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
        values = [receipt.get(name) if isinstance(receipt, dict) else None for receipt in receipts]
        known = [value for value in values if type(value) is int and value >= 0]
        reported[name] = sum(known) if known else None
        totals[name] = sum(known) if receipts and len(known) == len(receipts) else None
    complete = sum(isinstance(value, dict) and value.get("input_tokens") is not None
                   and value.get("output_tokens") is not None for value in receipts)
    available = any(value is not None for value in reported.values())
    totals["total_tokens"] = (totals["input_tokens"] + totals["output_tokens"]
                              if totals["input_tokens"] is not None and totals["output_tokens"] is not None else None)
    return {**totals, "status": "reported" if receipts and complete == len(receipts)
            else "partial" if available else "unavailable", "model_calls": len(receipts),
            "reported_calls": complete, "reported_subtotals": reported}
