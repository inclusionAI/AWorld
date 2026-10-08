"""Explicit provider media projection contracts and ephemeral audit sidecars."""

from __future__ import annotations

import threading
import weakref
import hashlib
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
    "stage_provider_media_audit",
]
