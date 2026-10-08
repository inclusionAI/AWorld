"""Bounded, provider-neutral observations for workspace image artifacts.

The Tool transport may carry image bytes, but ordinary ActionResult, Memory,
trajectory and Context records retain only the receipt and an opaque local
reference.  Bytes are materialized only at the final provider boundary.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


ARTIFACT_OBSERVATION_SCHEMA = "aworld.artifact-observation/v1"
ARTIFACT_OBSERVATION_URI_PREFIX = "aworld-artifact://observation/"
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_HARD_MAX_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_DIMENSION = 16_384
_DEFAULT_MAX_PIXELS = 40_000_000
_MAX_SERVER_CACHE_ENTRIES = 256
_MAX_SIDECAR_ENTRIES = 32
_MAX_SIDECAR_BYTES = 32 * 1024 * 1024
_MAX_PROVIDER_IMAGES_PER_REQUEST = 4
_MAX_PROVIDER_IMAGE_BYTES_PER_REQUEST = 16 * 1024 * 1024
_SUPPORTED_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class ArtifactObservationError(ValueError):
    """The requested artifact cannot be exposed safely as model input."""


@dataclass(frozen=True, slots=True)
class ObservedArtifact:
    data: bytes
    receipt: dict[str, Any]


@dataclass(slots=True)
class _SidecarEntry:
    data: bytes
    mime_type: str
    receipt: dict[str, Any]
    task_scope_hash: str
    authorized_call_hashes: set[str] = field(default_factory=set)
    bound_call_ids: set[str] = field(default_factory=set)


_state_lock = threading.RLock()
_server_cache: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_sidecars: "OrderedDict[str, _SidecarEntry]" = OrderedDict()
_sidecar_identity: dict[tuple[str, str], str] = {}
_sidecar_bytes = 0


def _sha256(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return _sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def artifact_task_scope_hash(
    *, task_id: Any = None, session_id: Any = None, task_epoch: Any = None
) -> str:
    if isinstance(task_epoch, bool):
        normalized_epoch: int | str = int(task_epoch)
    elif isinstance(task_epoch, int):
        normalized_epoch = task_epoch
    else:
        normalized_epoch = str(task_epoch or "")[:128]
    return _canonical_hash(
        {
            "task_id": str(task_id or ""),
            "session_id": str(session_id or ""),
            "task_epoch": normalized_epoch,
        }
    )


def artifact_call_id_hash(tool_call_id: Any) -> str:
    return _sha256(str(tool_call_id or ""))


def _configured_max_bytes() -> int:
    try:
        value = int(os.environ.get("AWORLD_ARTIFACT_OBSERVATION_MAX_BYTES", ""))
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_BYTES
    return max(1, min(value, _HARD_MAX_BYTES))


def _image_shape(data: bytes) -> tuple[str, int, int]:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        if data[12:16] != b"IHDR":
            raise ArtifactObservationError("invalid PNG header")
        return (
            "image/png",
            int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"),
        )
    if len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        position = 13
        packed = data[10]
        if packed & 0x80:
            position += 3 * (2 ** ((packed & 0x07) + 1))
        frame_count = 0

        def skip_sub_blocks(offset: int) -> int:
            while offset < len(data):
                size = data[offset]
                offset += 1
                if size == 0:
                    return offset
                offset += size
                if offset > len(data):
                    break
            raise ArtifactObservationError("invalid GIF data blocks")

        while position < len(data):
            marker = data[position]
            if marker == 0x3B:
                return "image/gif", width, height
            if marker == 0x21:
                if position + 2 >= len(data):
                    break
                position = skip_sub_blocks(position + 2)
                continue
            if marker == 0x2C:
                frame_count += 1
                if frame_count > 1:
                    raise ArtifactObservationError(
                        "animated GIF is unsupported; select one static frame"
                    )
                if position + 10 > len(data):
                    break
                descriptor_packed = data[position + 9]
                position += 10
                if descriptor_packed & 0x80:
                    position += 3 * (2 ** ((descriptor_packed & 0x07) + 1))
                if position >= len(data):
                    break
                position = skip_sub_blocks(position + 1)
                continue
            break
        raise ArtifactObservationError("invalid GIF structure")
    if len(data) >= 16 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            return (
                "image/webp",
                1 + int.from_bytes(data[24:27], "little"),
                1 + int.from_bytes(data[27:30], "little"),
            )
        if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            return (
                "image/webp",
                int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF,
            )
        if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return "image/webp", 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
        raise ArtifactObservationError("unsupported or invalid WebP header")
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        index = 2
        while index + 4 <= len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            while index < len(data) and data[index] == 0xFF:
                index += 1
            if index >= len(data):
                break
            marker = data[index]
            index += 1
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index : index + 2], "big")
            if length < 2 or index + length > len(data):
                break
            if (
                marker
                in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }
                and length >= 7
            ):
                height = int.from_bytes(data[index + 3 : index + 5], "big")
                width = int.from_bytes(data[index + 5 : index + 7], "big")
                return "image/jpeg", width, height
            index += length
        raise ArtifactObservationError("invalid JPEG dimensions")
    raise ArtifactObservationError(
        "unsupported media magic; expected PNG, JPEG, WebP, or GIF"
    )


def inspect_image_bytes(
    data: bytes,
    *,
    declared_mime: str | None = None,
    max_bytes: int | None = None,
    max_dimension: int = _DEFAULT_MAX_DIMENSION,
    max_pixels: int = _DEFAULT_MAX_PIXELS,
) -> tuple[str, int, int]:
    limit = (
        _configured_max_bytes()
        if max_bytes is None
        else min(max_bytes, _HARD_MAX_BYTES)
    )
    if len(data) > limit:
        raise ArtifactObservationError(f"artifact exceeds byte limit ({limit})")
    mime_type, width, height = _image_shape(data)
    if declared_mime and declared_mime.casefold() != mime_type:
        raise ArtifactObservationError(
            f"declared MIME {declared_mime!r} does not match media magic {mime_type!r}"
        )
    if width <= 0 or height <= 0 or width > max_dimension or height > max_dimension:
        raise ArtifactObservationError("image dimensions exceed the observation limit")
    if width * height > max_pixels:
        raise ArtifactObservationError("image dimensions exceed the pixel limit")
    return mime_type, width, height


def _scope_values(framework_scope: Mapping[str, Any] | None) -> tuple[str, str]:
    scope = framework_scope if isinstance(framework_scope, Mapping) else {}
    return (
        artifact_task_scope_hash(
            task_id=scope.get("task_id"),
            session_id=scope.get("session_id"),
            task_epoch=scope.get("task_epoch"),
        ),
        artifact_call_id_hash(scope.get("tool_call_id")),
    )


def _receipt_for_bytes(
    data: bytes,
    *,
    mime_type: str,
    width: int,
    height: int,
    path_key: str,
    file_epoch: str,
    framework_scope: Mapping[str, Any] | None,
) -> dict[str, Any]:
    task_scope_hash, call_id_hash = _scope_values(framework_scope)
    content_sha256 = _sha256(data)
    observation_id = _canonical_hash(
        {
            "task_scope_hash": task_scope_hash,
            "path_key": path_key,
            "content_sha256": content_sha256,
            "mime_type": mime_type,
        }
    )
    cache_key = (task_scope_hash, path_key)
    with _state_lock:
        previous = _server_cache.get(cache_key)
        cache_state = (
            "retained"
            if previous == content_sha256
            else ("changed" if previous is not None else "new")
        )
        _server_cache[cache_key] = content_sha256
        _server_cache.move_to_end(cache_key)
        while len(_server_cache) > _MAX_SERVER_CACHE_ENTRIES:
            _server_cache.popitem(last=False)
    return {
        "schema_version": ARTIFACT_OBSERVATION_SCHEMA,
        "status": "ready",
        "media_type": "image",
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "byte_count": len(data),
        "content_sha256": content_sha256,
        "observation_id": observation_id,
        "cache_state": cache_state,
        "file_epoch": file_epoch,
        "task_scope_hash": task_scope_hash,
        "call_id_hash": call_id_hash,
    }


def observe_artifact_bytes(
    data: bytes,
    *,
    suffix: str = "",
    expected_mime: str | None = None,
    path_key: str,
    file_epoch: str,
    framework_scope: Mapping[str, Any] | None = None,
    max_bytes: int | None = None,
) -> ObservedArtifact:
    suffix_mime = _SUPPORTED_MIME_BY_SUFFIX.get(suffix.casefold()) if suffix else None
    declared = expected_mime or suffix_mime
    mime_type, width, height = inspect_image_bytes(
        data, declared_mime=declared, max_bytes=max_bytes
    )
    if suffix and suffix_mime is None:
        guessed, _ = mimetypes.guess_type("artifact" + suffix)
        if guessed is not None:
            raise ArtifactObservationError(f"unsupported MIME type {guessed!r}")
    receipt = _receipt_for_bytes(
        data,
        mime_type=mime_type,
        width=width,
        height=height,
        path_key=path_key,
        file_epoch=file_epoch,
        framework_scope=framework_scope,
    )
    return ObservedArtifact(data=data, receipt=receipt)


def _ensure_no_symlink_components(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ArtifactObservationError("symlink artifacts are not allowed")


def observe_local_artifact(
    path: str,
    *,
    workspace_root: str | Path,
    expected_mime: str | None = None,
    framework_scope: Mapping[str, Any] | None = None,
    max_bytes: int | None = None,
) -> ObservedArtifact:
    root = Path(workspace_root).expanduser().resolve(strict=True)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ArtifactObservationError(
            "artifact is outside the workspace or unavailable"
        ) from exc
    _ensure_no_symlink_components(candidate.absolute(), root)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise ArtifactObservationError("artifact cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactObservationError("artifact must be a regular file")
        limit = (
            _configured_max_bytes()
            if max_bytes is None
            else min(max_bytes, _HARD_MAX_BYTES)
        )
        if before.st_size > limit:
            raise ArtifactObservationError(f"artifact exceeds byte limit ({limit})")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    epoch_values = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if epoch_values != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ArtifactObservationError("artifact changed while it was being observed")
    if len(data) != before.st_size:
        raise ArtifactObservationError("artifact changed while it was being observed")
    relative_path = resolved.relative_to(root).as_posix()
    path_key = _sha256(relative_path)
    file_epoch = _canonical_hash(
        {
            "device": before.st_dev,
            "inode": before.st_ino,
            "size": before.st_size,
            "mtime_ns": before.st_mtime_ns,
        }
    )
    return observe_artifact_bytes(
        data,
        suffix=resolved.suffix,
        expected_mime=expected_mime,
        path_key=path_key,
        file_epoch=file_epoch,
        framework_scope=framework_scope,
        max_bytes=limit,
    )


def artifact_mcp_content(observed: ObservedArtifact) -> list[Any]:
    """Build MCP content lazily so the core module has no hard server dependency."""

    from mcp.types import ImageContent, TextContent

    return [
        TextContent(
            type="text",
            text=json.dumps(observed.receipt, sort_keys=True, separators=(",", ":")),
        ),
        ImageContent(
            type="image",
            data=base64.b64encode(observed.data).decode("ascii"),
            mimeType=observed.receipt["mime_type"],
        ),
    ]


def parse_artifact_receipt(value: Any) -> dict[str, Any] | None:
    candidate = value
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            return None
    if (
        not isinstance(candidate, dict)
        or candidate.get("schema_version") != ARTIFACT_OBSERVATION_SCHEMA
    ):
        return None
    required = {
        "observation_id",
        "mime_type",
        "content_sha256",
        "byte_count",
        "width",
        "height",
        "task_scope_hash",
        "call_id_hash",
        "file_epoch",
    }
    if not required.issubset(candidate):
        return None
    if candidate.get("status") != "ready" or candidate.get("media_type") != "image":
        return None
    if candidate.get("mime_type") not in set(_SUPPORTED_MIME_BY_SUFFIX.values()):
        return None
    for key in (
        "observation_id",
        "content_sha256",
        "task_scope_hash",
        "call_id_hash",
        "file_epoch",
    ):
        if (
            not isinstance(candidate.get(key), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", candidate[key]) is None
        ):
            return None
    for key in ("width", "height", "byte_count"):
        number = candidate.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            return None
    if (
        candidate["width"] > _DEFAULT_MAX_DIMENSION
        or candidate["height"] > _DEFAULT_MAX_DIMENSION
        or candidate["width"] * candidate["height"] > _DEFAULT_MAX_PIXELS
        or candidate["byte_count"] > _HARD_MAX_BYTES
    ):
        return None
    if candidate.get("cache_state") not in {"new", "retained", "changed"}:
        return None
    return dict(candidate)


def _evict_sidecars() -> None:
    global _sidecar_bytes
    while len(_sidecars) > _MAX_SIDECAR_ENTRIES or _sidecar_bytes > _MAX_SIDECAR_BYTES:
        reference, entry = _sidecars.popitem(last=False)
        _sidecar_bytes -= len(entry.data)
        identity = (entry.task_scope_hash, str(entry.receipt["observation_id"]))
        if _sidecar_identity.get(identity) == reference:
            _sidecar_identity.pop(identity, None)


def register_artifact_sidecar(
    *, image_base64: str, mime_type: str, receipt: Mapping[str, Any]
) -> str:
    global _sidecar_bytes
    parsed = parse_artifact_receipt(dict(receipt))
    if parsed is None:
        raise ArtifactObservationError("artifact receipt is incomplete")
    try:
        data = base64.b64decode(image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ArtifactObservationError(
            "artifact image payload is invalid base64"
        ) from exc
    actual_mime, width, height = inspect_image_bytes(data, declared_mime=mime_type)
    if (
        actual_mime != parsed["mime_type"]
        or width != parsed["width"]
        or height != parsed["height"]
    ):
        raise ArtifactObservationError("artifact receipt does not match image payload")
    if _sha256(data) != parsed["content_sha256"] or len(data) != parsed["byte_count"]:
        raise ArtifactObservationError("artifact checksum does not match image payload")
    identity = (str(parsed["task_scope_hash"]), str(parsed["observation_id"]))
    with _state_lock:
        existing = _sidecar_identity.get(identity)
        if existing in _sidecars:
            entry = _sidecars[existing]
            if (
                entry.data != data
                or entry.mime_type != mime_type
                or entry.receipt.get("content_sha256") != parsed["content_sha256"]
            ):
                raise ArtifactObservationError(
                    "artifact observation identity conflicts with retained payload"
                )
            entry.authorized_call_hashes.add(str(parsed["call_id_hash"]))
            _sidecars.move_to_end(existing)
            return ARTIFACT_OBSERVATION_URI_PREFIX + existing
        token = hashlib.sha256(os.urandom(32) + str(identity).encode()).hexdigest()
        _sidecars[token] = _SidecarEntry(
            data=data,
            mime_type=mime_type,
            receipt=parsed,
            task_scope_hash=str(parsed["task_scope_hash"]),
            authorized_call_hashes={str(parsed["call_id_hash"])},
        )
        _sidecar_identity[identity] = token
        _sidecar_bytes += len(data)
        _evict_sidecars()
    return ARTIFACT_OBSERVATION_URI_PREFIX + token


def bind_artifact_sidecar(
    reference: str,
    *,
    task_id: Any,
    session_id: Any,
    task_epoch: Any,
    tool_call_id: str,
) -> bool:
    if not isinstance(reference, str) or not reference.startswith(
        ARTIFACT_OBSERVATION_URI_PREFIX
    ):
        return False
    token = reference[len(ARTIFACT_OBSERVATION_URI_PREFIX) :]
    with _state_lock:
        entry = _sidecars.get(token)
        if entry is None:
            return False
        if entry.task_scope_hash != artifact_task_scope_hash(
            task_id=task_id, session_id=session_id, task_epoch=task_epoch
        ):
            return False
        if artifact_call_id_hash(tool_call_id) not in entry.authorized_call_hashes:
            return False
        entry.bound_call_ids.add(tool_call_id)
        _sidecars.move_to_end(token)
        return True


def artifact_memory_descriptor(
    result: Any,
    *,
    context: Any,
    tool_call_id: str,
) -> dict[str, Any] | None:
    """Bind an ActionResult sidecar and return serialization-safe Memory data."""

    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    receipt = parse_artifact_receipt(metadata.get("artifact_observation"))
    reference = metadata.get("artifact_observation_ref")
    if receipt is None or not isinstance(reference, str):
        return None
    # Match McpServers' hidden env_content authority exactly. Falling back to a
    # nested Task copy here would create a different scope than the provider
    # received and turn a safe unscoped programmatic call into a false match.
    task_id = getattr(context, "task_id", None)
    session_id = getattr(context, "session_id", None)
    task_epoch = getattr(context, "task_epoch", 0)
    if not bind_artifact_sidecar(
        reference,
        task_id=task_id,
        session_id=session_id,
        task_epoch=task_epoch,
        tool_call_id=tool_call_id,
    ):
        return {
            "schema_version": ARTIFACT_OBSERVATION_SCHEMA,
            "status": "unavailable",
            "reason": "scope_binding_failed",
            "observation_id": receipt.get("observation_id"),
        }
    return {
        "schema_version": ARTIFACT_OBSERVATION_SCHEMA,
        "status": "ready",
        "reference": reference,
        "observation_id": receipt["observation_id"],
        "mime_type": receipt["mime_type"],
        "width": receipt["width"],
        "height": receipt["height"],
        "byte_count": receipt["byte_count"],
        "tool_call_id": tool_call_id,
    }


def artifact_prompt_message(
    descriptors: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Build an opaque framework-authored user message after Tool results."""

    selected = [
        item
        for item in descriptors[:4]
        if item.get("status") == "ready"
        and isinstance(item.get("reference"), str)
        and isinstance(item.get("tool_call_id"), str)
    ]
    if not selected:
        return None
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "AWorld artifact observation follows for the completed Tool "
                "result(s). Inspect the image directly; do not convert it to ASCII."
            ),
        }
    ]
    for item in selected:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": item["reference"]},
                "__aworld_artifact_tool_call_id": item["tool_call_id"],
                "__aworld_artifact_observation_id": item["observation_id"],
            }
        )
    return {"role": "user", "content": content}


def contains_artifact_references(messages: Any) -> bool:
    for message in messages if isinstance(messages, (list, tuple)) else ():
        content = message.get("content") if isinstance(message, Mapping) else None
        for item in content if isinstance(content, list) else ():
            url = (
                item.get("image_url", {}).get("url")
                if isinstance(item, Mapping)
                and isinstance(item.get("image_url"), Mapping)
                else None
            )
            if isinstance(url, str) and url.startswith(ARTIFACT_OBSERVATION_URI_PREFIX):
                return True
    return False


def mark_artifact_rollout_late_bound(value: Any) -> Any:
    """Make Context attribution truthful for an ephemeral media transport."""

    if not isinstance(value, dict):
        return value
    updated = dict(value)
    updated["candidate_applied"] = False
    updated["candidate_status"] = "late_bound_artifact_transport"
    updated["artifact_observation"] = {
        "late_bound": True,
        "provider_cache_eligible": False,
        "retained_payload": "opaque_reference",
    }
    return updated


def _projection_state(context: Any, agent_id: str) -> dict[str, Any]:
    owner = getattr(getattr(context, "event_manager", None), "context", None) or context
    context_info = getattr(owner, "context_info", None)
    if not (hasattr(context_info, "get") and hasattr(context_info, "__setitem__")):
        return {}
    key = f"artifact_observation_projection:{agent_id}"
    state = context_info.get(key)
    if not isinstance(state, dict) or state.get("task_epoch") != getattr(
        context, "task_epoch", 0
    ):
        state = {"task_epoch": getattr(context, "task_epoch", 0), "delivered": {}}
        context_info[key] = state
    delivered = state.get("delivered")
    if not isinstance(delivered, dict):
        state["delivered"] = {}
    return state


def hydrate_artifact_messages(
    messages: list[dict[str, Any]],
    *,
    context: Any,
    agent_id: str,
    vision_enabled: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Late-bind opaque refs into one-shot provider image data URLs.

    The returned list is ephemeral provider input. The caller must keep the
    original reference-bearing messages for logs, trajectories and snapshots.
    """

    task_id = getattr(context, "task_id", None)
    session_id = getattr(context, "session_id", None)
    task_epoch = getattr(context, "task_epoch", 0)
    lifecycle = getattr(context, "context_lifecycle_state", None)
    checkpoint = int(getattr(lifecycle, "checkpoint_revision", 0) or 0)
    state = _projection_state(context, agent_id)
    delivered = state.setdefault("delivered", {})
    hydrated_count = 0
    hydrated_bytes = 0
    hydrated_observation_ids: list[str] = []
    degraded_count = 0
    output: list[dict[str, Any]] = []
    causal_tool_ids: set[str] = set()
    for message in messages:
        value = dict(message)
        if value.get("role") == "tool" and isinstance(value.get("tool_call_id"), str):
            causal_tool_ids.add(value["tool_call_id"])
            output.append(value)
            continue
        content = value.get("content")
        if not isinstance(content, list):
            if value.get("role") != "tool" and value.get("role") != "user":
                causal_tool_ids.clear()
            output.append(value)
            continue
        projected: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            clean = dict(item)
            call_id = clean.pop("__aworld_artifact_tool_call_id", None)
            observation_id = clean.pop("__aworld_artifact_observation_id", None)
            image = clean.get("image_url")
            reference = image.get("url") if isinstance(image, Mapping) else None
            if not (
                isinstance(reference, str)
                and reference.startswith(ARTIFACT_OBSERVATION_URI_PREFIX)
            ):
                projected.append(clean)
                continue
            reason = None
            if not vision_enabled:
                reason = "current model is not configured for vision"
            elif not isinstance(call_id, str) or call_id not in causal_tool_ids:
                reason = "causal Tool result is unavailable"
            elif delivered.get(str(observation_id)) == checkpoint:
                reason = "unchanged observation retained for this checkpoint"
            elif hydrated_count >= _MAX_PROVIDER_IMAGES_PER_REQUEST:
                reason = "per-request image count limit reached"
            else:
                hydrated = _hydrate_artifact_reference(
                    reference,
                    task_id=task_id,
                    session_id=session_id,
                    task_epoch=task_epoch,
                    tool_call_id=call_id,
                )
                if hydrated is None:
                    reason = "bounded artifact sidecar is unavailable"
                else:
                    data_url, byte_count = hydrated
                    if (
                        hydrated_bytes + byte_count
                        > _MAX_PROVIDER_IMAGE_BYTES_PER_REQUEST
                    ):
                        reason = "per-request image byte limit reached"
                    else:
                        clean["image_url"] = {"url": data_url}
                        projected.append(clean)
                        hydrated_count += 1
                        hydrated_bytes += byte_count
                        hydrated_observation_ids.append(str(observation_id))
            if reason is not None:
                degraded_count += 1
                projected.append(
                    {
                        "type": "text",
                        "text": f"Artifact image not attached: {reason}.",
                    }
                )
        value["content"] = projected
        output.append(value)
        causal_tool_ids.clear()
    # Bound one-shot state independently of Memory history length.
    if len(delivered) > _MAX_SIDECAR_ENTRIES:
        for key in list(delivered)[: len(delivered) - _MAX_SIDECAR_ENTRIES]:
            delivered.pop(key, None)
    return output, {
        "schema_version": "aworld.artifact-observation-projection/v1",
        "hydrated_count": hydrated_count,
        "degraded_count": degraded_count,
        "hydrated_bytes": hydrated_bytes,
        "checkpoint_revision": checkpoint,
        "observation_ids": hydrated_observation_ids,
    }


def commit_artifact_projection(
    context: Any,
    *,
    agent_id: str,
    receipt: Mapping[str, Any] | None,
) -> None:
    """Commit one-shot delivery only after a provider accepted the request."""

    if not isinstance(receipt, Mapping) or receipt.get("schema_version") != (
        "aworld.artifact-observation-projection/v1"
    ):
        return
    lifecycle = getattr(context, "context_lifecycle_state", None)
    checkpoint = int(getattr(lifecycle, "checkpoint_revision", 0) or 0)
    if receipt.get("checkpoint_revision") != checkpoint:
        return
    observation_ids = receipt.get("observation_ids")
    if not isinstance(observation_ids, list):
        return
    state = _projection_state(context, agent_id)
    delivered = state.setdefault("delivered", {})
    for observation_id in observation_ids[:4]:
        if isinstance(observation_id, str) and observation_id:
            delivered[observation_id] = checkpoint
    if len(delivered) > _MAX_SIDECAR_ENTRIES:
        for key in list(delivered)[: len(delivered) - _MAX_SIDECAR_ENTRIES]:
            delivered.pop(key, None)


def hydrate_artifact_reference(
    reference: str,
    *,
    task_id: Any,
    session_id: Any,
    task_epoch: Any,
    tool_call_id: str,
) -> str | None:
    hydrated = _hydrate_artifact_reference(
        reference,
        task_id=task_id,
        session_id=session_id,
        task_epoch=task_epoch,
        tool_call_id=tool_call_id,
    )
    return hydrated[0] if hydrated is not None else None


def _hydrate_artifact_reference(
    reference: str,
    *,
    task_id: Any,
    session_id: Any,
    task_epoch: Any,
    tool_call_id: str,
) -> tuple[str, int] | None:
    if not isinstance(reference, str) or not reference.startswith(
        ARTIFACT_OBSERVATION_URI_PREFIX
    ):
        return None
    token = reference[len(ARTIFACT_OBSERVATION_URI_PREFIX) :]
    with _state_lock:
        entry = _sidecars.get(token)
        if entry is None or tool_call_id not in entry.bound_call_ids:
            return None
        if entry.task_scope_hash != artifact_task_scope_hash(
            task_id=task_id, session_id=session_id, task_epoch=task_epoch
        ):
            return None
        _sidecars.move_to_end(token)
        return (
            f"data:{entry.mime_type};base64,{base64.b64encode(entry.data).decode('ascii')}",
            len(entry.data),
        )


def clear_artifact_observation_state() -> None:
    global _sidecar_bytes
    with _state_lock:
        _server_cache.clear()
        _sidecars.clear()
        _sidecar_identity.clear()
        _sidecar_bytes = 0
