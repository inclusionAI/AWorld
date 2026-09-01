"""Frozen provider-neutral cost evidence for paired Context evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from aworld.core.context.compiler import canonical_json_hash


_MAX_EXACT_JSON_INTEGER = (1 << 53) - 1


@dataclass(frozen=True, slots=True)
class NormalizedCostPolicy:
    """Cache-adjusted token-equivalent policy, not a currency estimate."""

    version: str = "aworld.normalized-cost.cache-adjusted-tokens.v1"
    uncached_input_microunits_per_token: int = 1_000_000
    cache_read_microunits_per_token: int = 100_000
    output_microunits_per_token: int = 1_000_000

    def __post_init__(self) -> None:
        if self.version != "aworld.normalized-cost.cache-adjusted-tokens.v1":
            raise ValueError("unsupported normalized cost policy version")
        for name in (
            "uncached_input_microunits_per_token",
            "cache_read_microunits_per_token",
            "output_microunits_per_token",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.cache_read_microunits_per_token > self.uncached_input_microunits_per_token:
            raise ValueError("cache-read weight cannot exceed uncached input weight")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.fingerprint_payload())

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "unit": "cache_adjusted_token_equivalent",
            "uncached_input_microunits_per_token": self.uncached_input_microunits_per_token,
            "cache_read_microunits_per_token": self.cache_read_microunits_per_token,
            "output_microunits_per_token": self.output_microunits_per_token,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.fingerprint_payload(), "policy_hash": self.policy_hash}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NormalizedCostPolicy":
        if not isinstance(value, Mapping):
            raise TypeError("normalized cost policy must be an object")
        if value.get("unit") != "cache_adjusted_token_equivalent":
            raise ValueError("unsupported normalized cost unit")
        policy = cls(
            version=value.get("version"),
            uncached_input_microunits_per_token=value.get(
                "uncached_input_microunits_per_token"
            ),
            cache_read_microunits_per_token=value.get(
                "cache_read_microunits_per_token"
            ),
            output_microunits_per_token=value.get(
                "output_microunits_per_token"
            ),
        )
        if value.get("policy_hash") != policy.policy_hash:
            raise ValueError("normalized cost policy hash mismatch")
        return policy


@dataclass(frozen=True, slots=True)
class NormalizedCostReceipt:
    policy_hash: str
    input_tokens: int
    cache_read_tokens: int
    output_tokens: int
    total_microunits: int

    SCHEMA_VERSION = "aworld.normalized-cost-receipt.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.policy_hash, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.policy_hash
        ):
            raise ValueError("policy_hash must be canonical")
        for name in (
            "input_tokens", "cache_read_tokens", "output_tokens", "total_microunits"
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            if value > _MAX_EXACT_JSON_INTEGER:
                raise ValueError(f"{name} exceeds the exact JSON integer range")
        if self.cache_read_tokens > self.input_tokens:
            raise ValueError("cache-read tokens cannot exceed input tokens")

    @property
    def normalized_cost(self) -> float:
        return self.total_microunits / 1_000_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "policy_hash": self.policy_hash,
            "input_tokens": self.input_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "output_tokens": self.output_tokens,
            "total_microunits": self.total_microunits,
            "normalized_cost": self.normalized_cost,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, policy: NormalizedCostPolicy
    ) -> "NormalizedCostReceipt":
        if not isinstance(value, Mapping):
            raise TypeError("normalized cost receipt must be an object")
        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported normalized cost receipt version")
        receipt = compute_normalized_cost(
            policy=policy,
            input_tokens=value.get("input_tokens"),
            cache_read_tokens=value.get("cache_read_tokens"),
            output_tokens=value.get("output_tokens"),
        )
        normalized_value = value.get("normalized_cost")
        if (
            isinstance(value.get("total_microunits"), bool)
            or not isinstance(value.get("total_microunits"), int)
            or isinstance(normalized_value, bool)
            or not isinstance(normalized_value, (int, float))
            or not math.isfinite(float(normalized_value))
        ):
            raise ValueError("normalized cost receipt has invalid numeric evidence")
        if (
            value.get("policy_hash") != receipt.policy_hash
            or value.get("total_microunits") != receipt.total_microunits
            or value.get("normalized_cost") != receipt.normalized_cost
        ):
            raise ValueError("normalized cost receipt mismatch")
        return receipt


def compute_normalized_cost(
    *,
    policy: NormalizedCostPolicy,
    input_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
) -> NormalizedCostReceipt:
    if not isinstance(policy, NormalizedCostPolicy):
        raise TypeError("policy must be NormalizedCostPolicy")
    for name, value in (
        ("input_tokens", input_tokens),
        ("cache_read_tokens", cache_read_tokens),
        ("output_tokens", output_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if cache_read_tokens > input_tokens:
        raise ValueError("cache-read tokens cannot exceed input tokens")
    total = (
        (input_tokens - cache_read_tokens)
        * policy.uncached_input_microunits_per_token
        + cache_read_tokens * policy.cache_read_microunits_per_token
        + output_tokens * policy.output_microunits_per_token
    )
    return NormalizedCostReceipt(
        policy_hash=policy.policy_hash,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        output_tokens=output_tokens,
        total_microunits=total,
    )


__all__ = [
    "NormalizedCostPolicy",
    "NormalizedCostReceipt",
    "compute_normalized_cost",
]
