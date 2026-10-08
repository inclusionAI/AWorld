"""Task-scoped append-only prompt sessions owned by AminiContext.

Provider prompt caches reuse an exact left prefix.  Semantic equivalence,
stable-section hashes, and an append-only memory store are not sufficient when
the final provider request is rebuilt or compacted between calls.  This module
owns the last *wire-ready* message sequence and makes every mutation explicit:

* within one epoch, a request is the prior request plus an appended delta;
* checkpoint, scope, Tool-catalog, or historical-prefix rewrites start a new epoch;
* an otherwise unexplained rewrite is surfaced as a typed epoch rollover.

The state is JSON-compatible so ``ApplicationContext`` can keep it in its
task-scoped runtime sidecar shared by transport copies.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aworld.core.context.compiler.frozen_json import canonical_json_hash
from aworld.utils.serialized_util import to_serializable


PROMPT_SESSION_SCHEMA_VERSION = "aworld.context.amni-prompt-session/v1"
PROMPT_SESSION_RECEIPT_SCHEMA_VERSION = "aworld.context.amni-prompt-session-receipt/v1"
PROMPT_SESSION_STATE_KEY = "amni_prompt_session"
_MAX_TRANSITIONS = 32


def _as_messages(values: Any) -> list[Any]:
    serialized = to_serializable(values or [])
    if not isinstance(serialized, list):
        raise TypeError("prompt session messages must serialize to a list")
    return copy.deepcopy(serialized)


def _fingerprint(value: Any) -> str:
    return canonical_json_hash(to_serializable(value))


def _common_prefix_length(previous: Sequence[Any], current: Sequence[Any]) -> int:
    count = 0
    for old, new in zip(previous, current):
        if old != new:
            break
        count += 1
    return count


@dataclass(frozen=True, slots=True)
class PromptSessionTransition:
    state: dict[str, Any]
    messages: list[Any]
    receipt: dict[str, Any]


def advance_prompt_session(
    previous_state: Any,
    *,
    messages: Any,
    tools: Any,
    scope: Mapping[str, Any],
    checkpoint_revision: int,
    stable_prefix_hash: str | None,
) -> PromptSessionTransition:
    """Materialize one wire-ready request under the append-only invariant."""

    if (
        isinstance(checkpoint_revision, bool)
        or not isinstance(checkpoint_revision, int)
        or checkpoint_revision < 0
    ):
        raise ValueError("checkpoint_revision must be a non-negative integer")
    candidate = _as_messages(messages)
    scope_hash = _fingerprint(dict(scope))
    tool_catalog_hash = _fingerprint(tools or [])
    stable_hash = str(stable_prefix_hash or "")
    previous = (
        copy.deepcopy(previous_state)
        if isinstance(previous_state, Mapping)
        and previous_state.get("schema_version") == PROMPT_SESSION_SCHEMA_VERSION
        else None
    )

    prior_messages = _as_messages(previous.get("messages")) if previous else []
    prior_epoch = int(previous.get("epoch_id", 0) or 0) if previous else 0
    prior_total_requests = (
        int(previous.get("total_request_count", 0) or 0) if previous else 0
    )
    prior_rollovers = int(previous.get("rollover_count", 0) or 0) if previous else 0
    stable_prefix_changed = bool(
        previous is not None
        and str(previous.get("stable_prefix_hash") or "") != stable_hash
    )

    rollover_reason: str | None = None
    mode = "append"
    if previous is None:
        rollover_reason = "initial"
    elif previous.get("scope_hash") != scope_hash:
        rollover_reason = "scope_change"
    elif int(previous.get("checkpoint_revision", 0) or 0) != checkpoint_revision:
        rollover_reason = "context_checkpoint"
    elif previous.get("tool_catalog_hash") != tool_catalog_hash:
        rollover_reason = "tool_catalog_change"

    common_prefix = _common_prefix_length(prior_messages, candidate)
    if rollover_reason is not None:
        effective = candidate
        epoch_id = prior_epoch + 1
        epoch_request_index = 1
        mode = "epoch_start"
    elif common_prefix == len(prior_messages):
        effective = candidate
        epoch_id = prior_epoch
        epoch_request_index = int(previous.get("epoch_request_index", 0) or 0) + 1
    else:
        # Never reorder an inserted assistant/Tool/system occurrence merely to
        # manufacture a prefix: doing so can break Tool-call adjacency or
        # change instruction precedence.  Preserve provider semantics and make
        # the producer's rewrite an explicit, observable epoch boundary.
        rollover_reason = "projection_rewrite"
        effective = candidate
        epoch_id = prior_epoch + 1
        epoch_request_index = 1
        mode = "epoch_start"

    epoch_started = rollover_reason is not None
    epoch_rollover = previous is not None and epoch_started
    append_only = epoch_started or effective[: len(prior_messages)] == prior_messages
    if rollover_reason is None and not append_only:
        raise RuntimeError("Amini prompt session violated its append-only invariant")

    appended_count = (
        len(effective)
        if rollover_reason is not None
        else max(0, len(effective) - len(prior_messages))
    )
    receipt = {
        "schema_version": PROMPT_SESSION_RECEIPT_SCHEMA_VERSION,
        "epoch_id": epoch_id,
        "epoch_request_index": epoch_request_index,
        "mode": mode,
        "append_only": True,
        "epoch_started": epoch_started,
        "epoch_rollover": epoch_rollover,
        "rollover_reason": rollover_reason,
        "previous_message_count": len(prior_messages),
        "candidate_message_count": len(candidate),
        "wire_message_count": len(effective),
        "appended_message_count": appended_count,
        "candidate_common_prefix_messages": common_prefix,
        "scope_hash": scope_hash,
        "stable_prefix_hash": stable_hash,
        "stable_prefix_changed": stable_prefix_changed,
        "tool_catalog_hash": tool_catalog_hash,
        "wire_messages_hash": _fingerprint(effective),
    }
    prior_cache_evidence = previous.get("provider_cache_evidence") if previous else None
    if isinstance(prior_cache_evidence, Mapping):
        receipt["prior_provider_cache_evidence"] = {
            key: int(prior_cache_evidence.get(key, 0) or 0)
            for key in (
                "last_cache_hit_tokens",
                "last_cache_write_tokens",
                "observation_count",
                "cache_hit_request_count",
                "total_cache_hit_tokens",
                "total_cache_write_tokens",
            )
        }
    transitions = list(previous.get("transitions") or []) if previous else []
    transitions.append(copy.deepcopy(receipt))
    state = {
        "schema_version": PROMPT_SESSION_SCHEMA_VERSION,
        "epoch_id": epoch_id,
        "epoch_request_index": epoch_request_index,
        "total_request_count": prior_total_requests + 1,
        "rollover_count": prior_rollovers + int(epoch_rollover),
        "scope_hash": scope_hash,
        "scope": to_serializable(dict(scope)),
        "checkpoint_revision": checkpoint_revision,
        "stable_prefix_hash": stable_hash,
        "tool_catalog_hash": tool_catalog_hash,
        "messages": copy.deepcopy(effective),
        "wire_messages_hash": receipt["wire_messages_hash"],
        "last_transition": copy.deepcopy(receipt),
        "transitions": transitions[-_MAX_TRANSITIONS:],
    }
    if previous and isinstance(previous.get("provider_cache_evidence"), Mapping):
        state["provider_cache_evidence"] = copy.deepcopy(
            previous["provider_cache_evidence"]
        )
    return PromptSessionTransition(
        state=state,
        messages=copy.deepcopy(effective),
        receipt=receipt,
    )


def record_prompt_session_cache_usage(
    previous_state: Any,
    *,
    cache_hit_tokens: Any = 0,
    cache_write_tokens: Any = 0,
) -> Any:
    """Attach bounded provider cache evidence to the active prompt epoch."""

    if not (
        isinstance(previous_state, Mapping)
        and previous_state.get("schema_version") == PROMPT_SESSION_SCHEMA_VERSION
    ):
        return previous_state

    def token_count(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return max(0, int(value))

    hit = token_count(cache_hit_tokens)
    written = token_count(cache_write_tokens)
    state = copy.deepcopy(previous_state)
    evidence = dict(state.get("provider_cache_evidence") or {})
    epoch_id = int(state.get("epoch_id", 0) or 0)
    if int(evidence.get("epoch_id", -1) or -1) != epoch_id:
        evidence.update(
            {
                "epoch_id": epoch_id,
                "epoch_observation_count": 0,
                "epoch_cache_hit_request_count": 0,
                "epoch_cache_hit_tokens": 0,
                "epoch_cache_write_tokens": 0,
            }
        )
    evidence.update(
        {
            "last_cache_hit_tokens": hit,
            "last_cache_write_tokens": written,
            "observation_count": int(evidence.get("observation_count", 0) or 0) + 1,
            "cache_hit_request_count": int(
                evidence.get("cache_hit_request_count", 0) or 0
            )
            + int(hit > 0),
            "total_cache_hit_tokens": int(
                evidence.get("total_cache_hit_tokens", 0) or 0
            )
            + hit,
            "total_cache_write_tokens": int(
                evidence.get("total_cache_write_tokens", 0) or 0
            )
            + written,
            "epoch_observation_count": int(
                evidence.get("epoch_observation_count", 0) or 0
            )
            + 1,
            "epoch_cache_hit_request_count": int(
                evidence.get("epoch_cache_hit_request_count", 0) or 0
            )
            + int(hit > 0),
            "epoch_cache_hit_tokens": int(
                evidence.get("epoch_cache_hit_tokens", 0) or 0
            )
            + hit,
            "epoch_cache_write_tokens": int(
                evidence.get("epoch_cache_write_tokens", 0) or 0
            )
            + written,
        }
    )
    state["provider_cache_evidence"] = evidence
    return state


__all__ = [
    "PROMPT_SESSION_RECEIPT_SCHEMA_VERSION",
    "PROMPT_SESSION_SCHEMA_VERSION",
    "PROMPT_SESSION_STATE_KEY",
    "PromptSessionTransition",
    "advance_prompt_session",
    "record_prompt_session_cache_usage",
]
