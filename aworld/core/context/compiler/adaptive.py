"""Deterministic adaptive checkpoint and semantic-progress policy.

The policy consumes only bounded, privacy-safe measurements.  It never tries to
understand a benchmark answer and therefore remains reusable across workloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Mapping, Sequence

from .frozen_json import canonical_json_hash


class AdaptiveCheckpointReason(str, Enum):
    BUDGET_PRESSURE = "budget_pressure"
    REPEATED_OPERATION = "repeated_operation"
    LOW_INFORMATION_GAIN = "low_information_gain"


@dataclass(frozen=True, slots=True)
class AdaptiveCheckpointPolicy:
    budget_pressure_ratio: float = 0.78
    repeated_operation_threshold: int = 3
    low_information_gain_threshold: int = 3
    minimum_turn_interval: int = 2
    keep_recent_messages: int = 8

    def __post_init__(self) -> None:
        if not 0 < self.budget_pressure_ratio <= 1:
            raise ValueError("budget_pressure_ratio must be in (0, 1]")
        for name in (
            "repeated_operation_threshold",
            "low_information_gain_threshold",
            "minimum_turn_interval",
            "keep_recent_messages",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class AdaptiveCheckpointDecision:
    checkpoint: bool
    compact: bool
    reasons: tuple[AdaptiveCheckpointReason, ...]
    prompt_tokens: int
    input_budget: int
    repetition_count: int
    low_information_gain_count: int


def evaluate_adaptive_checkpoint(
    *,
    policy_name: str,
    prompt_tokens: int,
    input_budget: int,
    repetition_count: int,
    low_information_gain_count: int,
    turn_epoch: int,
    last_checkpoint_turn: int | None,
    policy: AdaptiveCheckpointPolicy | None = None,
) -> AdaptiveCheckpointDecision:
    """Return a fail-safe decision without mutating Context state."""
    policy = policy or AdaptiveCheckpointPolicy()
    if policy_name not in {"explicit", "budget_pressure", "adaptive"}:
        raise ValueError(f"unsupported checkpoint policy: {policy_name}")
    if min(prompt_tokens, input_budget, repetition_count, low_information_gain_count) < 0:
        raise ValueError("adaptive checkpoint measurements must be non-negative")

    reasons: list[AdaptiveCheckpointReason] = []
    if input_budget and prompt_tokens / input_budget >= policy.budget_pressure_ratio:
        reasons.append(AdaptiveCheckpointReason.BUDGET_PRESSURE)
    if policy_name == "adaptive":
        if repetition_count >= policy.repeated_operation_threshold:
            reasons.append(AdaptiveCheckpointReason.REPEATED_OPERATION)
        if low_information_gain_count >= policy.low_information_gain_threshold:
            reasons.append(AdaptiveCheckpointReason.LOW_INFORMATION_GAIN)
    if policy_name == "explicit":
        reasons = []
    if policy_name == "budget_pressure":
        reasons = [
            reason
            for reason in reasons
            if reason is AdaptiveCheckpointReason.BUDGET_PRESSURE
        ]

    cooled_down = (
        last_checkpoint_turn is None
        or turn_epoch - last_checkpoint_turn >= policy.minimum_turn_interval
    )
    checkpoint = bool(reasons) and cooled_down
    return AdaptiveCheckpointDecision(
        checkpoint=checkpoint,
        compact=checkpoint,
        reasons=tuple(reasons) if checkpoint else (),
        prompt_tokens=prompt_tokens,
        input_budget=input_budget,
        repetition_count=repetition_count,
        low_information_gain_count=low_information_gain_count,
    )


_VOLATILE_KEYS = {
    "tool_call_id",
    "call_id",
    "request_id",
    "started_at",
    "finished_at",
    "timestamp",
    "execution_time",
    "latency",
    "latency_seconds",
}


def semantic_projection(value: Any) -> Any:
    """Remove transport identities/timing before a semantic progress hash."""
    if isinstance(value, Mapping):
        return {
            str(key): semantic_projection(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in _VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [semantic_projection(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return semantic_projection(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        if len(value) > 32_768:
            return {
                "prefix": value[:16_384],
                "suffix": value[-16_384:],
                "original_length": len(value),
            }
    return value


def semantic_fingerprint(value: Any) -> str:
    return canonical_json_hash(semantic_projection(value))


def semantic_result_fingerprint(value: Any) -> str:
    """Hash a Tool result after removing command echoes as well as transport data."""
    projection = semantic_projection(value)

    def remove_command_echo(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): remove_command_echo(child)
                for key, child in item.items()
                if str(key).lower() != "command"
            }
        if isinstance(item, list):
            return [remove_command_echo(child) for child in item]
        return item

    return canonical_json_hash(remove_command_echo(projection))


def compact_message_history(
    messages: Sequence[Mapping[str, Any]], *, keep_recent: int = 8
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Compact old turns while retaining system policy, the task, and recent work.

    The receipt contains hashes and shape only; it is suitable for trajectory and
    checkpoint evidence without duplicating potentially sensitive message text.
    """
    values = [dict(message) for message in messages]
    if len(values) <= keep_recent + 2:
        return values, None

    protected: set[int] = set()
    for index, message in enumerate(values):
        if message.get("role") == "system":
            protected.add(index)
    first_user = next(
        (index for index, message in enumerate(values) if message.get("role") == "user"),
        None,
    )
    if first_user is not None:
        protected.add(first_user)
    protected.update(range(max(0, len(values) - keep_recent), len(values)))
    removed = [message for index, message in enumerate(values) if index not in protected]
    if not removed:
        return values, None

    role_counts: dict[str, int] = {}
    for message in removed:
        role = str(message.get("role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
    receipt = {
        "schema_version": "aworld.context.adaptive-compaction/v1",
        "removed_message_count": len(removed),
        "removed_role_counts": dict(sorted(role_counts.items())),
        "removed_messages_hash": semantic_fingerprint(removed),
    }
    marker = {
        "role": "user",
        "content": (
            "AWorld compacted earlier low-value or repetitive turns at a verified "
            "checkpoint. Preserve the original task and use the recent evidence. "
            "Do not repeat an operation unless it can produce new information."
        ),
    }
    compacted: list[dict[str, Any]] = []
    recent_start = max(0, len(values) - keep_recent)
    marker_inserted = False
    for index, message in enumerate(values):
        if index in protected:
            if not marker_inserted and index >= recent_start:
                compacted.append(marker)
                marker_inserted = True
            compacted.append(message)
    if not marker_inserted:
        compacted.append(marker)
    return compacted, receipt


__all__ = [
    "AdaptiveCheckpointDecision",
    "AdaptiveCheckpointPolicy",
    "AdaptiveCheckpointReason",
    "compact_message_history",
    "evaluate_adaptive_checkpoint",
    "semantic_fingerprint",
    "semantic_result_fingerprint",
    "semantic_projection",
]
