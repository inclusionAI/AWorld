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
_ADAPTIVE_CONTINUATION_TOMBSTONE_LIMIT = 32
_ADAPTIVE_WORK_STATE_MESSAGE_PREFIX = "AWorld verified continuation state"


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


def advance_adaptive_continuation_sequence(
    messages: Sequence[Mapping[str, Any]],
    previous_state: Mapping[str, Any] | None,
    *,
    occurrence_ids: Sequence[str | None] | None = None,
    scope: Mapping[str, Any] | None = None,
    legacy_retained_messages: Sequence[Mapping[str, Any]] | None = None,
    tombstone_limit: int = _ADAPTIVE_CONTINUATION_TOMBSTONE_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return only occurrences after the last consumed raw-Memory sequence.

    Adaptive compaction retains a bounded provider capsule while Memory remains
    append-only and may replay the complete pre-checkpoint history on every
    turn. Content is never cursor authority: every projected Memory occurrence
    has a private ID derived from its durable ``MemoryItem.id``. The bounded ID
    tail survives a sliding history window even when every message is byte
    identical. IDs are carried out-of-band and are stripped before the provider.

    When a rewrite removes every known anchor, this turn fails closed and the
    current bounded sequence becomes a recovery anchor. A later append can then
    resume instead of freezing the continuation forever.
    """
    if (
        isinstance(tombstone_limit, bool)
        or not isinstance(tombstone_limit, int)
        or tombstone_limit < 1
    ):
        raise ValueError("tombstone_limit must be a positive integer")

    current = [dict(message) for message in messages]
    if occurrence_ids is None:
        occurrence_ids = [None] * len(current)
    if len(occurrence_ids) != len(current):
        raise ValueError("occurrence_ids must align one-to-one with messages")

    def valid_id(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value if 0 < len(value) <= 256 else None

    normalized_ids = [valid_id(value) for value in occurrence_ids]
    present_ids = [value for value in normalized_ids if value is not None]
    if len(present_ids) != len(set(present_ids)):
        raise ValueError("occurrence_ids must be unique")

    allowed_scope_keys = ("session_id", "task_id", "task_epoch", "agent_id")
    normalized_scope: dict[str, Any] = {}
    for key in allowed_scope_keys:
        value = scope.get(key) if isinstance(scope, Mapping) else None
        if isinstance(value, str):
            normalized_scope[key] = value[:256]
        elif isinstance(value, int) and not isinstance(value, bool):
            normalized_scope[key] = value
        elif value is None:
            normalized_scope[key] = None

    def build_state(reset_reason: str | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {
            "schema_version": "aworld.context.adaptive-continuation-sequence/v2",
            "scope": normalized_scope,
            "high_water_occurrence_id": present_ids[-1] if present_ids else None,
            "recent_occurrence_ids": present_ids[-tombstone_limit:],
        }
        if reset_reason:
            state["reset_reason"] = reset_reason
        return state

    def causal_key(message: Mapping[str, Any]) -> tuple[str, tuple[str, ...]] | None:
        if message.get("role") == "tool":
            call_id = valid_id(message.get("tool_call_id"))
            return ("tool", (call_id,)) if call_id else None
        if message.get("role") != "assistant":
            return None
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            return None
        call_ids = tuple(
            call_id
            for call in calls
            if isinstance(call, Mapping)
            for call_id in (valid_id(call.get("id")),)
            if call_id is not None
        )
        return ("assistant", call_ids) if call_ids else None

    if not present_ids:
        return current, build_state("no_occurrence_authority")

    previous_scope = (
        previous_state.get("scope") if isinstance(previous_state, Mapping) else None
    )
    scope_mismatch = isinstance(previous_scope, Mapping) and {
        key: previous_scope.get(key) for key in allowed_scope_keys
    } != normalized_scope
    if scope_mismatch:
        return current, build_state("scope_mismatch")

    sequence_schema = (
        previous_state.get("schema_version")
        if isinstance(previous_state, Mapping)
        else None
    )
    if sequence_schema != "aworld.context.adaptive-continuation-sequence/v2":
        retained = [
            dict(message) for message in (legacy_retained_messages or ())
        ]
        retained_key = next(
            (
                key
                for message in reversed(retained)
                for key in (causal_key(message),)
                if key is not None
            ),
            None,
        )
        if retained_key is not None:
            matches = [
                index
                for index, message in enumerate(current)
                if causal_key(message) == retained_key
                and normalized_ids[index] is not None
            ]
            if len(matches) == 1:
                return current[matches[0] + 1 :], build_state(
                    "legacy_causal_migration"
                )
        # No retained occurrence proves a safe boundary. Do not replay raw
        # history, but establish a bounded anchor so the next append can resume.
        return [], build_state("legacy_unanchored_recovery")

    high_water = valid_id(previous_state.get("high_water_occurrence_id"))
    raw_recent = previous_state.get("recent_occurrence_ids")
    recent = (
        [
            occurrence_id
            for value in raw_recent[-tombstone_limit:]
            for occurrence_id in (valid_id(value),)
            if occurrence_id is not None
        ]
        if isinstance(raw_recent, list)
        else []
    )
    index_by_id = {
        occurrence_id: index
        for index, occurrence_id in enumerate(normalized_ids)
        if occurrence_id is not None
    }
    anchor = high_water if high_water in index_by_id else next(
        (value for value in reversed(recent) if value in index_by_id),
        None,
    )
    if anchor is None:
        return [], build_state("source_rewrite")
    return current[index_by_id[anchor] + 1 :], build_state()


def restore_adaptive_continuation(
    messages: Sequence[Mapping[str, Any]],
    capsule: Sequence[Mapping[str, Any]] | None,
    *,
    continuation_delta: Sequence[Mapping[str, Any]] | None = None,
    keep_recent: int | None = 8,
) -> list[dict[str, Any]]:
    """Merge a prior verified continuation capsule with newly replayed history.

    Event-driven Memory/Amni persistence may be observed through different
    Context transport copies. ``continuation_delta`` must come from
    :func:`advance_adaptive_continuation_sequence`; raw replay is not merged by
    content identity. The capsule is runtime-only and this function never adds
    its content to receipts or checkpoint metadata.
    """
    current = [dict(message) for message in messages]
    previous = [dict(message) for message in (capsule or ())]
    if not previous:
        return current

    # A caller without a trusted sequence delta cannot distinguish stale replay
    # from a new byte-identical occurrence. Preserve the capsule and fail
    # closed instead of falling back to content set-difference.
    delta = [dict(message) for message in (continuation_delta or ())]
    if any(
        message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(_ADAPTIVE_WORK_STATE_MESSAGE_PREFIX)
        for message in delta
    ):
        previous = [
            message
            for message in previous
            if not (
                message.get("role") == "user"
                and isinstance(message.get("content"), str)
                and message["content"].startswith(
                    _ADAPTIVE_WORK_STATE_MESSAGE_PREFIX
                )
            )
        ]
    merged = [*previous, *delta]
    if keep_recent is None:
        return merged
    compacted, _ = compact_message_history(merged, keep_recent=keep_recent)
    return compacted


__all__ = [
    "AdaptiveCheckpointDecision",
    "AdaptiveCheckpointPolicy",
    "AdaptiveCheckpointReason",
    "AdaptiveEscalationDecision",
    "AdaptiveEscalationStage",
    "adaptive_escalation_message",
    "advance_adaptive_continuation_sequence",
    "advance_adaptive_escalation",
    "compact_duplicate_tool_results",
    "compact_message_history",
    "restore_adaptive_continuation",
    "evaluate_adaptive_checkpoint",
    "semantic_fingerprint",
    "semantic_result_fingerprint",
    "semantic_projection",
]
