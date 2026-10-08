"""Explicit provider media projection contracts and ephemeral audit sidecars."""

from __future__ import annotations

import threading
import weakref
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderMediaProjectionCapability:
    provider_name: str
    projection: str
    version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.provider_name, str) or not self.provider_name:
            raise ValueError("provider_name must be non-empty")
        if self.projection not in {
            "openai.image_url.data_url.v1",
            "anthropic.image.base64.v1",
        }:
            raise ValueError("unsupported provider media projection")
        if self.version != 1:
            raise ValueError("unsupported provider media capability version")


OPENAI_MEDIA_PROJECTION = ProviderMediaProjectionCapability(
    provider_name="openai",
    projection="openai.image_url.data_url.v1",
)
AZURE_OPENAI_MEDIA_PROJECTION = ProviderMediaProjectionCapability(
    provider_name="azure_openai",
    projection="openai.image_url.data_url.v1",
)
ANTHROPIC_MEDIA_PROJECTION = ProviderMediaProjectionCapability(
    provider_name="anthropic",
    projection="anthropic.image.base64.v1",
)


_lock = threading.RLock()
_audit: "weakref.WeakKeyDictionary[Any, dict[str, list[dict[str, Any]]]]" = (
    weakref.WeakKeyDictionary()
)
_MAX_PENDING_AUDITS_PER_PROVIDER = 32
_active_audit: ContextVar[tuple[int, list[dict[str, Any]]] | None] = ContextVar(
    "aworld_provider_media_audit",
    default=None,
)


def stage_provider_media_audit(
    provider: Any,
    *,
    request_id: str,
    messages: list[dict[str, Any]],
) -> None:
    """Retain the media-free request view outside provider kwargs/logs."""

    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be non-empty")
    with _lock:
        pending = _audit.setdefault(provider, {})
        pending[request_id] = deepcopy(messages)
        while len(pending) > _MAX_PENDING_AUDITS_PER_PROVIDER:
            pending.pop(next(iter(pending)), None)


def consume_provider_media_audit(
    provider: Any,
    *,
    request_id: str | None,
) -> list[dict[str, Any]] | None:
    if not isinstance(request_id, str) or not request_id:
        return None
    with _lock:
        pending = _audit.get(provider)
        if not pending:
            return None
        value = pending.pop(request_id, None)
        if not pending:
            _audit.pop(provider, None)
        return value


def discard_provider_media_audit(provider: Any, *, request_id: str | None) -> None:
    consume_provider_media_audit(provider, request_id=request_id)


@contextmanager
def bind_provider_media_audit(
    provider: Any,
    messages: list[dict[str, Any]] | None,
):
    token = _active_audit.set(
        (id(provider), messages) if isinstance(messages, list) else None
    )
    try:
        yield
    finally:
        _active_audit.reset(token)


def current_provider_media_audit(provider: Any) -> list[dict[str, Any]] | None:
    value = _active_audit.get()
    return value[1] if value is not None and value[0] == id(provider) else None


def redact_provider_media_payloads(value: Any) -> Any:
    """Replace provider image bytes with bounded hash-only diagnostics."""

    if isinstance(value, str) and value.startswith("data:image/"):
        prefix, _, encoded = value.partition(",")
        return {
            "type": "redacted_image_data_url",
            "mime_type": prefix[5:].split(";", 1)[0],
            "encoded_chars": len(encoded),
            "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }
    if isinstance(value, list):
        return [redact_provider_media_payloads(item) for item in value]
    if isinstance(value, tuple):
        return [redact_provider_media_payloads(item) for item in value]
    if isinstance(value, dict):
        if (
            value.get("type") == "base64"
            and isinstance(value.get("media_type"), str)
            and value["media_type"].startswith("image/")
            and isinstance(value.get("data"), str)
        ):
            encoded = value["data"]
            return {
                "type": "redacted_image_base64",
                "media_type": value["media_type"],
                "encoded_chars": len(encoded),
                "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            }
        return {
            key: redact_provider_media_payloads(item) for key, item in value.items()
        }
    return value


def _is_retained_artifact_marker(content: Any) -> bool:
    from aworld.sandbox.artifact_observation import ARTIFACT_RETAINED_MESSAGE

    return content == ARTIFACT_RETAINED_MESSAGE or (
        isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
        and set(content[0]) == {"type", "text"}
        and content[0].get("type") == "text"
        and content[0].get("text") == ARTIFACT_RETAINED_MESSAGE
    )


def _artifact_marker_causal_chain(
    messages: list[dict[str, Any]], marker_index: int
) -> tuple[Any, ...] | None:
    """Return the semantic assistant/tool chain immediately owning one marker."""

    from aworld.sandbox.artifact_observation import (
        artifact_receipt_from_tool_content,
    )

    cursor = marker_index - 1
    while cursor >= 0 and messages[cursor].get("role") == "tool":
        cursor -= 1
    if cursor < 0 or messages[cursor].get("role") != "assistant":
        return None
    assistant = messages[cursor]
    calls = assistant.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    expected_ids = [
        call.get("id") for call in calls if isinstance(call, dict)
    ]
    results = messages[cursor + 1 : marker_index]
    result_ids = [result.get("tool_call_id") for result in results]
    if (
        len(expected_ids) != len(calls)
        or len(results) != len(calls)
        or any(not isinstance(call_id, str) or not call_id for call_id in expected_ids)
        or result_ids != expected_ids
    ):
        return None
    signature = []
    artifact_receipts = 0
    for call, result in zip(calls, results):
        function = call.get("function")
        if not isinstance(function, dict):
            return None
        name = function.get("name")
        arguments = function.get("arguments")
        try:
            argument_identity = json.dumps(
                arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return None
        receipt = artifact_receipt_from_tool_content(result.get("content"))
        if receipt is not None:
            artifact_receipts += 1
            receipt_identity = (
                receipt.get("observation_id"),
                receipt.get("content_sha256"),
                receipt.get("call_id_hash"),
                receipt.get("task_scope_hash"),
            )
        else:
            receipt_identity = None
        signature.append(
            (
                call.get("id"),
                name,
                argument_identity,
                result.get("tool_call_id"),
                receipt_identity,
            )
        )
    if artifact_receipts == 0:
        return None
    return tuple(signature)


def merge_verified_media_suffix(
    *,
    candidate_messages: list[dict[str, Any]],
    wire_messages: list[dict[str, Any]],
    audit_messages: list[dict[str, Any]],
    stable_message_count: int = 0,
) -> list[dict[str, Any]]:
    """Apply only verified media marker replacements to immutable candidates."""

    from aworld.sandbox.artifact_observation import ARTIFACT_RETAINED_MESSAGE

    if len(wire_messages) != len(audit_messages):
        raise ValueError("media audit message count changed")
    wire_suffixes: list[tuple[Any, tuple[Any, ...]]] = []
    for index, (audit, wire) in enumerate(zip(audit_messages, wire_messages)):
        if not isinstance(audit, dict) or not isinstance(wire, dict):
            raise TypeError("media messages must be mappings")
        if _is_retained_artifact_marker(audit.get("content")):
            if (
                audit.get("role") != "user"
                or wire.get("role") != "user"
                or {key: value for key, value in wire.items() if key != "content"}
                != {key: value for key, value in audit.items() if key != "content"}
                or not isinstance(wire.get("content"), list)
                or len(wire["content"]) < 2
                or not isinstance(wire["content"][0], dict)
                or wire["content"][0].get("type") != "text"
                or not str(wire["content"][0].get("text") or "").startswith(
                    ARTIFACT_RETAINED_MESSAGE
                )
            ):
                raise ValueError("media suffix is not bound to its retained marker")
            causal_chain = _artifact_marker_causal_chain(audit_messages, index)
            if causal_chain is None:
                raise ValueError("media marker has no complete causal tool chain")
            wire_suffixes.append((deepcopy(wire["content"]), causal_chain))
        elif wire != audit:
            raise ValueError("media projection changed non-marker messages")
    if not wire_suffixes:
        raise ValueError("media projection contains no verified suffix")

    merged = deepcopy(candidate_messages)
    used_indices: set[int] = set()
    for suffix, causal_chain in wire_suffixes:
        candidates = [
            index
            for index, message in enumerate(candidate_messages)
            if index not in used_indices
            and isinstance(message, dict)
            and message.get("role") == "user"
            and _is_retained_artifact_marker(message.get("content"))
            and _artifact_marker_causal_chain(candidate_messages, index)
            == causal_chain
        ]
        if len(candidates) != 1:
            raise ValueError("candidate discarded or duplicated a media causal chain")
        index = candidates[0]
        if index < stable_message_count:
            raise ValueError("media marker overlaps the stable cache prefix")
        used_indices.add(index)
        merged[index]["content"] = suffix
    return merged


__all__ = [
    "ANTHROPIC_MEDIA_PROJECTION",
    "AZURE_OPENAI_MEDIA_PROJECTION",
    "OPENAI_MEDIA_PROJECTION",
    "ProviderMediaProjectionCapability",
    "bind_provider_media_audit",
    "consume_provider_media_audit",
    "discard_provider_media_audit",
    "current_provider_media_audit",
    "redact_provider_media_payloads",
    "merge_verified_media_suffix",
    "stage_provider_media_audit",
]
