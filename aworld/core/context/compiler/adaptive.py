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
    NO_GOAL_PROGRESS = "no_goal_progress"


class AdaptiveEscalationStage(str, Enum):
    NONE = "none"
    REASSESS = "reassess"
    DIVERSIFY = "diversify"
    RECOVER = "recover"


@dataclass(frozen=True, slots=True)
class AdaptiveEscalationDecision:
    stage: AdaptiveEscalationStage
    no_progress_checkpoint_count: int
    progress_reset: bool


_NO_PROGRESS_REASONS = frozenset(
    {
        AdaptiveCheckpointReason.REPEATED_OPERATION,
        AdaptiveCheckpointReason.LOW_INFORMATION_GAIN,
        AdaptiveCheckpointReason.NO_GOAL_PROGRESS,
    }
)


def advance_adaptive_escalation(
    *,
    previous_no_progress_checkpoints: int,
    checkpoint_reasons: Sequence[AdaptiveCheckpointReason],
    goal_progress: bool,
) -> AdaptiveEscalationDecision:
    """Advance a task-generic escalation window across adaptive checkpoints.

    A checkpoint acknowledgement deliberately resets short-window repetition
    counters.  This separate counter preserves whether multiple such resets
    still failed to produce artifact or Completion Contract progress.
    """
    if (
        isinstance(previous_no_progress_checkpoints, bool)
        or not isinstance(previous_no_progress_checkpoints, int)
        or previous_no_progress_checkpoints < 0
    ):
        raise ValueError("previous_no_progress_checkpoints must be non-negative")
    if goal_progress:
        return AdaptiveEscalationDecision(
            stage=AdaptiveEscalationStage.NONE,
            no_progress_checkpoint_count=0,
            progress_reset=previous_no_progress_checkpoints > 0,
        )
    increment = any(reason in _NO_PROGRESS_REASONS for reason in checkpoint_reasons)
    checkpoint_count = previous_no_progress_checkpoints + int(increment)
    if checkpoint_count <= 0:
        stage = AdaptiveEscalationStage.NONE
    elif checkpoint_count == 1:
        stage = AdaptiveEscalationStage.REASSESS
    elif checkpoint_count == 2:
        stage = AdaptiveEscalationStage.DIVERSIFY
    else:
        stage = AdaptiveEscalationStage.RECOVER
    return AdaptiveEscalationDecision(
        stage=stage,
        no_progress_checkpoint_count=checkpoint_count,
        progress_reset=False,
    )


def adaptive_escalation_message(stage: AdaptiveEscalationStage) -> str:
    """Return a benchmark-independent strategy directive for a typed stage."""
    if stage is AdaptiveEscalationStage.REASSESS:
        return (
            "AWorld detected insufficient semantic progress. Reassess the plan, "
            "identify the missing evidence, and do not repeat an operation unless "
            "it can change the result."
        )
    if stage is AdaptiveEscalationStage.DIVERSIFY:
        return (
            "AWorld detected another checkpoint without goal progress. Use a "
            "materially different approach: validate assumptions, target a new "
            "source of evidence, and avoid prior operation/result patterns."
        )
    if stage is AdaptiveEscalationStage.RECOVER:
        return (
            "AWorld recovery mode: inspect current artifacts and completion "
            "evidence, identify the blocker, preserve or restore valid work, and "
            "choose one bounded alternative not already attempted. Do not repeat "
            "an action without a stated path to new evidence."
        )
    return (
        "AWorld checkpointed under Context budget pressure. Preserve the task and "
        "verified evidence while continuing with the smallest useful next step."
    )


@dataclass(frozen=True, slots=True)
class AdaptiveCheckpointPolicy:
    budget_pressure_ratio: float = 0.95
    repeated_operation_threshold: int = 3
    low_information_gain_threshold: int = 3
    no_goal_progress_threshold: int = 6
    minimum_turn_interval: int = 2
    keep_recent_messages: int = 6

    def __post_init__(self) -> None:
        if not 0 < self.budget_pressure_ratio <= 1:
            raise ValueError("budget_pressure_ratio must be in (0, 1]")
        for name in (
            "repeated_operation_threshold",
            "low_information_gain_threshold",
            "no_goal_progress_threshold",
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
    no_goal_progress_count: int


def evaluate_adaptive_checkpoint(
    *,
    policy_name: str,
    prompt_tokens: int,
    input_budget: int,
    repetition_count: int,
    low_information_gain_count: int,
    no_goal_progress_count: int = 0,
    turn_epoch: int,
    last_checkpoint_turn: int | None,
    policy: AdaptiveCheckpointPolicy | None = None,
) -> AdaptiveCheckpointDecision:
    """Return a fail-safe decision without mutating Context state."""
    policy = policy or AdaptiveCheckpointPolicy()
    if policy_name not in {"explicit", "budget_pressure", "adaptive"}:
        raise ValueError(f"unsupported checkpoint policy: {policy_name}")
    if min(
        prompt_tokens,
        input_budget,
        repetition_count,
        low_information_gain_count,
        no_goal_progress_count,
    ) < 0:
        raise ValueError("adaptive checkpoint measurements must be non-negative")

    reasons: list[AdaptiveCheckpointReason] = []
    if input_budget and prompt_tokens / input_budget >= policy.budget_pressure_ratio:
        reasons.append(AdaptiveCheckpointReason.BUDGET_PRESSURE)
    if policy_name == "adaptive":
        if repetition_count >= policy.repeated_operation_threshold:
            reasons.append(AdaptiveCheckpointReason.REPEATED_OPERATION)
        if low_information_gain_count >= policy.low_information_gain_threshold:
            reasons.append(AdaptiveCheckpointReason.LOW_INFORMATION_GAIN)
        if no_goal_progress_count >= policy.no_goal_progress_threshold:
            reasons.append(AdaptiveCheckpointReason.NO_GOAL_PROGRESS)
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
        no_goal_progress_count=no_goal_progress_count,
    )


_VOLATILE_KEYS = {
    "tool_call_id",
    "call_id",
    "request_id",
    "started_at",
    "finished_at",
    "timestamp",
    "execution_time",
    "checkpoint_duration_seconds",
    "transaction_wall_seconds",
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

_DUPLICATE_TOOL_RESULT_MIN_CHARS = 512
_DUPLICATE_TOOL_RESULT_MARKER = "AWorld cached duplicate tool observation"


def compact_duplicate_tool_results(
    messages: Sequence[Mapping[str, Any]],
    *,
    minimum_chars: int = _DUPLICATE_TOOL_RESULT_MIN_CHARS,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Replace older byte-identical Tool observations with bounded receipts.

    Amni offload protects the prompt from one oversized Tool result, while a
    long-running agent can still accumulate many medium-sized copies of the
    same file range, log, or command output. Keep the newest complete copy and
    preserve every assistant/Tool causal pair; only older identical string
    payloads become content-addressed receipts.

    This is deliberately semantic-free. It does not infer that a command was
    safe, skip Tool execution, or treat similarity as equality.
    """
    if (isinstance(minimum_chars, bool) or not isinstance(minimum_chars, int)
            or minimum_chars < 1):
        raise ValueError("minimum_chars must be a positive integer")

    values = [dict(message) for message in messages]
    occurrences: dict[str, list[tuple[int, str]]] = {}
    for index, message in enumerate(values):
        content = message.get("content")
        if (message.get("role") != "tool" or not isinstance(content, str)
                or len(content) < minimum_chars
                or content.startswith(_DUPLICATE_TOOL_RESULT_MARKER)):
            continue
        content_hash = canonical_json_hash({"content": content})
        occurrences.setdefault(content_hash, []).append((index, content))

    compacted_count = saved_chars = duplicate_groups = 0
    hashes: list[str] = []
    for content_hash, copies in occurrences.items():
        if len(copies) < 2:
            continue
        duplicate_groups += 1
        hashes.append(content_hash)
        # The newest occurrence remains complete, so no observed information
        # is lost from the provider-bound request.
        for occurrence, (index, content) in enumerate(copies[:-1], start=1):
            receipt = (
                f"{_DUPLICATE_TOOL_RESULT_MARKER} omitted. "
                "An identical complete result is retained in a later Tool "
                f"message. content_hash={content_hash}; "
                f"original_chars={len(content)}; occurrence={occurrence}/{len(copies)}."
            )
            values[index]["content"] = receipt
            compacted_count += 1
            saved_chars += max(0, len(content) - len(receipt))

    if not compacted_count:
        return values, None
    return values, {
        "schema_version": "aworld.context.duplicate-tool-compaction/v1",
        "duplicate_group_count": duplicate_groups,
        "compacted_message_count": compacted_count,
        "saved_chars": saved_chars,
        "content_hashes": sorted(hashes),
    }


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
    # Preserve complete assistant/tool atomic groups.  A suffix boundary must
    # never leave an orphan Tool result or an assistant call without its result.
    tool_groups: list[set[int]] = []
    for assistant_index, message in enumerate(values):
        if message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            continue
        call_ids = {
            call.get("id")
            for call in tool_calls
            if isinstance(call, Mapping)
            and isinstance(call.get("id"), str)
            and call.get("id")
        }
        if not call_ids:
            continue
        group = {assistant_index}
        for result_index in range(assistant_index + 1, len(values)):
            result = values[result_index]
            if result.get("role") in {"assistant", "user", "system"}:
                break
            if (
                result.get("role") == "tool"
                and result.get("tool_call_id") in call_ids
            ):
                group.add(result_index)
        tool_groups.append(group)
    # The latest completed Tool exchange is the operational hand-off point for
    # the next model call. Preserve it even when later framework/user sidecars
    # would otherwise push the whole group beyond ``keep_recent``.
    latest_tool_group = tool_groups[-1] if tool_groups else set()
    protected.update(latest_tool_group)
    changed = True
    while changed:
        changed = False
        for group in tool_groups:
            if protected.intersection(group) and not group.issubset(protected):
                protected.update(group)
                changed = True
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
        "latest_tool_atomic_group_retained": bool(latest_tool_group),
        "latest_tool_atomic_group_size": len(latest_tool_group),
        "latest_tool_atomic_group_hash": (
            semantic_fingerprint(
                [values[index] for index in sorted(latest_tool_group)]
            )
            if latest_tool_group
            else None
        ),
    }
    marker = {
        "role": "user",
        "content": (
            "AWorld compacted earlier messages to fit the model context window. "
            "Continue from the original task, retained work state, and tool results."
        ),
    }
    compacted: list[dict[str, Any]] = []
    marker_inserted = False
    for index, message in enumerate(values):
        if index in protected:
            is_prefix_policy = message.get("role") == "system" or index == first_user
            if not marker_inserted and not is_prefix_policy:
                compacted.append(marker)
                marker_inserted = True
            compacted.append(message)
    if not marker_inserted:
        compacted.append(marker)
    return compacted, receipt


def restore_adaptive_continuation(
    messages: Sequence[Mapping[str, Any]],
    capsule: Sequence[Mapping[str, Any]] | None,
    *,
    keep_recent: int | None = 8,
) -> list[dict[str, Any]]:
    """Merge a prior verified continuation capsule with newly replayed history.

    Event-driven Memory/Amni persistence may be observed through different
    Context transport copies.  A compacted request must not forget already
    verified work merely because one copy temporarily exposes only the stable
    prefix.  The capsule is runtime-only; this function never adds its content
    to receipts or checkpoint metadata.
    """
    current = [dict(message) for message in messages]
    previous = [dict(message) for message in (capsule or ())]
    if not previous:
        return current

    def identity(message: Mapping[str, Any]) -> tuple[Any, ...]:
        role = str(message.get("role") or "")
        if role == "tool" and message.get("tool_call_id"):
            return role, str(message.get("tool_call_id"))
        tool_calls = message.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
            call_ids = tuple(
                str(call.get("id"))
                for call in tool_calls
                if isinstance(call, Mapping) and call.get("id")
            )
            if call_ids:
                return role, "tool_calls", call_ids
        return role, semantic_fingerprint(message)

    if keep_recent is None:
        # The capsule is the exact provider prefix already committed for this
        # epoch. Preserve it byte-for-byte and append only unseen replay
        # occurrences. Rebuilding a fresh system/task prefix here would turn
        # an append-only Amni event stream back into a mutable wire prompt.
        merged = [dict(message) for message in previous]
        seen = {identity(message) for message in previous}
        for message in current:
            message_identity = identity(message)
            if message_identity in seen:
                continue
            seen.add(message_identity)
            merged.append(dict(message))
        return merged

    def split_prefix(
        values: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        first_user = next(
            (index for index, item in enumerate(values) if item.get("role") == "user"),
            None,
        )
        prefix_indexes = {
            index
            for index, item in enumerate(values)
            if item.get("role") == "system"
        }
        if first_user is not None:
            prefix_indexes.add(first_user)
        return (
            [item for index, item in enumerate(values) if index in prefix_indexes],
            [item for index, item in enumerate(values) if index not in prefix_indexes],
        )

    current_prefix, current_body = split_prefix(current)
    _, previous_body = split_prefix(previous)

    body: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for message in [*previous_body, *current_body]:
        message_identity = identity(message)
        if message_identity in seen:
            continue
        seen.add(message_identity)
        body.append(message)

    merged = [*current_prefix, *body]
    compacted, _ = compact_message_history(merged, keep_recent=keep_recent)
    return compacted


__all__ = [
    "AdaptiveCheckpointDecision",
    "AdaptiveCheckpointPolicy",
    "AdaptiveCheckpointReason",
    "AdaptiveEscalationDecision",
    "AdaptiveEscalationStage",
    "adaptive_escalation_message",
    "advance_adaptive_escalation",
    "compact_duplicate_tool_results",
    "compact_message_history",
    "restore_adaptive_continuation",
    "evaluate_adaptive_checkpoint",
    "semantic_fingerprint",
    "semantic_result_fingerprint",
    "semantic_projection",
]
