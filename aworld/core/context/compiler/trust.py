"""Explicit prompt-data boundaries for external and Tool-provided content."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json

from .frozen_json import FrozenMap, freeze_json, thaw_json
from .models import ContextItem, ContextSource, Trust


TRUST_BOUNDARY_VERSION = "aworld-untrusted-data-v1"


@dataclass(frozen=True, slots=True)
class TrustIsolationReceipt:
    original_content_hash: str
    isolated_item: ContextItem
    boundary_version: str = TRUST_BOUNDARY_VERSION


def has_trust_boundary(item: ContextItem) -> bool:
    ref = item.source.ref
    return (
        isinstance(ref, FrozenMap)
        and ref.get("trust_boundary_version") == TRUST_BOUNDARY_VERSION
        and isinstance(ref.get("original_content_hash"), str)
    )


def isolate_untrusted_context_item(item: ContextItem) -> TrustIsolationReceipt:
    """Wrap untrusted content as data without interpreting embedded directives."""
    if item.trust not in {Trust.EXTERNAL_UNTRUSTED, Trust.TOOL_UNTRUSTED}:
        raise ValueError("trust isolation requires an untrusted ContextItem")
    original_hash = item.content_hash or ""
    payload = item.payload
    if isinstance(payload, FrozenMap) and "content" in payload:
        mutable = thaw_json(payload)
        content = mutable.get("content")
        if not isinstance(content, str):
            content = json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        isolated_content = (
            f"<aworld-untrusted-data version={TRUST_BOUNDARY_VERSION} "
            f"source_hash={original_hash}>\n{content}\n"
            "</aworld-untrusted-data>"
        )
        mutable["content"] = (
            [{"type": "text", "text": isolated_content}]
            if mutable.get("role") in {"tool", "function"}
            else isolated_content
        )
        isolated_payload = mutable
    else:
        isolated_payload = {
            "boundary": TRUST_BOUNDARY_VERSION,
            "source_hash": original_hash,
            "data": payload,
        }
    source_ref = thaw_json(item.source.ref) if item.source.ref is not None else {}
    if not isinstance(source_ref, dict):
        source_ref = {"owner_ref": source_ref}
    source_ref.update(
        {
            "trust_boundary_version": TRUST_BOUNDARY_VERSION,
            "original_content_hash": original_hash,
        }
    )
    isolated = replace(
        item,
        payload=freeze_json(isolated_payload),
        source=ContextSource(
            kind=item.source.kind,
            uri=item.source.uri,
            version=item.source.version,
            ref=source_ref,
        ),
        content_hash=None,
    )
    return TrustIsolationReceipt(
        original_content_hash=original_hash,
        isolated_item=isolated,
    )


def verifies_trust_isolation(
    isolated_item: ContextItem,
    *,
    original_item: ContextItem,
) -> bool:
    """Verify an owner-produced boundary against the exact original payload.

    The owner sidecar is allowed to carry the deterministic trust-boundary
    transform that the candidate compiler will emit.  Its provenance claim is
    not sufficient by itself: recomputing the transform must reproduce the
    isolated payload byte-for-byte from the observed ordinal.
    """
    if (
        isolated_item.trust not in {Trust.EXTERNAL_UNTRUSTED, Trust.TOOL_UNTRUSTED}
        or not has_trust_boundary(isolated_item)
        or isolated_item.occurrence != original_item.occurrence
    ):
        return False
    ref = isolated_item.source.ref
    if not isinstance(ref, FrozenMap):
        return False
    original_hash = original_item.content_hash or ""
    if ref.get("original_content_hash") != original_hash:
        return False
    unisolated = replace(
        isolated_item,
        payload=original_item.payload,
        content_hash=None,
    )
    recomputed = isolate_untrusted_context_item(unisolated)
    return (
        recomputed.original_content_hash == original_hash
        and recomputed.isolated_item.payload == isolated_item.payload
    )


__all__ = [
    "TRUST_BOUNDARY_VERSION",
    "TrustIsolationReceipt",
    "has_trust_boundary",
    "isolate_untrusted_context_item",
    "verifies_trust_isolation",
]
