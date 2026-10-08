"""Bounded, provider-neutral observations for workspace image artifacts.

The Tool transport may carry image bytes, but ordinary ActionResult, Memory,
trajectory and Context records retain only the receipt and an opaque local
reference.  Bytes are materialized only at the final provider boundary.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import mimetypes
import os
import re
import stat
import threading
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


ARTIFACT_OBSERVATION_SCHEMA = "aworld.artifact-observation/v1"
ARTIFACT_OBSERVATION_URI_PREFIX = "aworld-artifact://observation/"
ARTIFACT_RETAINED_MESSAGE = (
    "AWorld retained verified image artifact observation(s) from the immediately "
    "preceding completed Tool call group. Media is attached only on the bounded "
    "first provider attempt; later turns retain this stable text receipt."
)
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_HARD_MAX_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_DIMENSION = 16_384
_DEFAULT_MAX_PIXELS = 40_000_000
_MAX_SERVER_CACHE_ENTRIES = 256
_MAX_SIDECAR_ENTRIES = 32
_MAX_SIDECAR_BYTES = 32 * 1024 * 1024
_MAX_CALL_HASHES_PER_SIDECAR = 32
_MAX_PROVIDER_IMAGES_PER_REQUEST = 4
_MAX_PROVIDER_IMAGE_BYTES_PER_REQUEST = 16 * 1024 * 1024
_SUPPORTED_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_ARTIFACT_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "media_type",
        "mime_type",
        "width",
        "height",
        "byte_count",
        "content_sha256",
        "observation_id",
        "cache_state",
        "file_epoch",
        "task_scope_hash",
        "call_id_hash",
    }
)


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
    authorized_call_hashes: "OrderedDict[str, None]" = field(
        default_factory=OrderedDict
    )
    bound_call_hashes: "OrderedDict[str, None]" = field(default_factory=OrderedDict)


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
        normalized_epoch = str(task_epoch or "")
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
    try:
        from PIL import Image, ImageFile, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - packaging regression guard
        raise ArtifactObservationError(
            "Pillow is required for bounded artifact verification"
        ) from exc

    format_to_mime = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
        "GIF": "image/gif",
    }
    try:
        # Pillow's truncated-image switch is process-global. Serialize the
        # short verify/decode section so another integration cannot weaken it.
        with _state_lock, warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            previous_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
            ImageFile.LOAD_TRUNCATED_IMAGES = False
            try:
                with Image.open(io.BytesIO(data)) as image:
                    mime_type = format_to_mime.get(str(image.format or "").upper())
                    if mime_type is None:
                        raise ArtifactObservationError(
                            "unsupported image format; expected PNG, JPEG, WebP, or GIF"
                        )
                    width, height = image.size
                    if (
                        width <= 0
                        or height <= 0
                        or width > _DEFAULT_MAX_DIMENSION
                        or height > _DEFAULT_MAX_DIMENSION
                        or width * height > _DEFAULT_MAX_PIXELS
                    ):
                        raise ArtifactObservationError(
                            "image dimensions exceed the decode limit"
                        )
                    if (
                        bool(getattr(image, "is_animated", False))
                        or int(getattr(image, "n_frames", 1) or 1) != 1
                    ):
                        raise ArtifactObservationError(
                            "animated media is unsupported; select one static frame"
                        )
                    image.verify()
                # ``verify`` checks container integrity without decoding pixels;
                # reopen and load to reject truncated/corrupt compressed data.
                with Image.open(io.BytesIO(data)) as decoded:
                    if (
                        bool(getattr(decoded, "is_animated", False))
                        or int(getattr(decoded, "n_frames", 1) or 1) != 1
                    ):
                        raise ArtifactObservationError(
                            "animated media is unsupported; select one static frame"
                        )
                    decoded.load()
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = previous_truncated
    except ArtifactObservationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ArtifactObservationError(
            "image dimensions exceed the decode limit"
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ArtifactObservationError(
            "image payload is truncated, corrupt, or unsupported"
        ) from exc
    return mime_type, int(width), int(height)


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


def _open_confined_regular_file(
    path: str,
    *,
    workspace_root: str | Path,
) -> tuple[int, str]:
    """Open ``path`` by fd-relative, no-follow traversal from filesystem root."""

    if (
        os.open not in getattr(os, "supports_dir_fd", set())
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        raise ArtifactObservationError(
            "this platform cannot prove workspace artifact confinement"
        )
    root = Path(os.path.abspath(Path(workspace_root).expanduser()))
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactObservationError("artifact is outside the workspace") from exc
    if not relative.parts:
        raise ArtifactObservationError("artifact must be a regular file")
    if any(part in {"", ".", ".."} for part in (*root.parts[1:], *relative.parts)):
        raise ArtifactObservationError("artifact path is not canonical")

    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(os.path.sep, directory_flags)
    try:
        components = [*root.parts[1:], *relative.parts]
        for index, component in enumerate(components):
            flags = file_flags if index == len(components) - 1 else directory_flags
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(stat_result.st_mode):
            raise ArtifactObservationError("artifact must be a regular file")
        return descriptor, relative.as_posix()
    except BaseException:
        os.close(descriptor)
        raise


def observe_local_artifact(
    path: str,
    *,
    workspace_root: str | Path,
    expected_mime: str | None = None,
    framework_scope: Mapping[str, Any] | None = None,
    max_bytes: int | None = None,
) -> ObservedArtifact:
    try:
        descriptor, relative_path = _open_confined_regular_file(
            path,
            workspace_root=workspace_root,
        )
    except ArtifactObservationError:
        raise
    except OSError as exc:
        raise ArtifactObservationError(
            "artifact is outside the workspace or unavailable"
        ) from exc
    try:
        before = os.fstat(descriptor)
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
    epoch_values = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if epoch_values != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ArtifactObservationError("artifact changed while it was being observed")
    if len(data) != before.st_size:
        raise ArtifactObservationError("artifact changed while it was being observed")
    path_key = _sha256(relative_path)
    file_epoch = _canonical_hash(
        {
            "device": before.st_dev,
            "inode": before.st_ino,
            "mode": before.st_mode,
            "size": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "ctime_ns": before.st_ctime_ns,
        }
    )
    return observe_artifact_bytes(
        data,
        suffix=Path(relative_path).suffix,
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


def artifact_mcp_result(observed: ObservedArtifact) -> Any:
    """Return the exact closed MCP result without FastMCP structured mirroring."""

    from mcp.types import CallToolResult

    return CallToolResult(content=artifact_mcp_content(observed), isError=False)


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
    if set(candidate) != _ARTIFACT_RECEIPT_KEYS:
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


def _retain_call_hash(values: "OrderedDict[str, None]", call_hash: str) -> None:
    values[call_hash] = None
    values.move_to_end(call_hash)
    while len(values) > _MAX_CALL_HASHES_PER_SIDECAR:
        values.popitem(last=False)


def register_artifact_sidecar(
    *, image_base64: str, mime_type: str, receipt: Mapping[str, Any]
) -> str:
    global _sidecar_bytes
    parsed = parse_artifact_receipt(dict(receipt))
    if parsed is None:
        raise ArtifactObservationError("artifact receipt is incomplete")
    if not isinstance(image_base64, str):
        raise ArtifactObservationError("artifact image payload is invalid base64")
    expected_encoded_length = 4 * ((int(parsed["byte_count"]) + 2) // 3)
    hard_encoded_limit = 4 * ((_HARD_MAX_BYTES + 2) // 3)
    if (
        len(image_base64) != expected_encoded_length
        or len(image_base64) > hard_encoded_limit
    ):
        raise ArtifactObservationError("artifact image payload length is invalid")
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
            _retain_call_hash(entry.authorized_call_hashes, str(parsed["call_id_hash"]))
            _sidecars.move_to_end(existing)
            return ARTIFACT_OBSERVATION_URI_PREFIX + existing
        token = hashlib.sha256(
            ("artifact-sidecar/v1\0" + identity[0] + "\0" + identity[1]).encode()
        ).hexdigest()
        _sidecars[token] = _SidecarEntry(
            data=data,
            mime_type=mime_type,
            receipt=parsed,
            task_scope_hash=str(parsed["task_scope_hash"]),
            authorized_call_hashes=OrderedDict(((str(parsed["call_id_hash"]), None),)),
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
        call_hash = artifact_call_id_hash(tool_call_id)
        if call_hash not in entry.authorized_call_hashes:
            return False
        _retain_call_hash(entry.bound_call_hashes, call_hash)
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
        "observation_id": receipt["observation_id"],
        "mime_type": receipt["mime_type"],
        "width": receipt["width"],
        "height": receipt["height"],
        "byte_count": receipt["byte_count"],
        "call_id_hash": receipt["call_id_hash"],
    }


def artifact_prompt_message(
    descriptors: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Build an opaque framework-authored user message after Tool results."""

    selected = [
        item
        for item in descriptors[:16]
        if item.get("status") == "ready"
        and isinstance(item.get("observation_id"), str)
        and isinstance(item.get("call_id_hash"), str)
    ]
    if not selected:
        return None
    return {"role": "user", "content": ARTIFACT_RETAINED_MESSAGE}


def mark_artifact_rollout_late_bound(
    value: Any,
    receipt: Mapping[str, Any] | None = None,
) -> Any:
    """Make Context attribution truthful for an ephemeral media transport."""

    if not isinstance(value, dict):
        return value
    updated = dict(value)
    updated["artifact_observation"] = {
        "late_bound": True,
        "provider_cache_eligible": True,
        "retained_payload": "stable_text_receipt",
        "dynamic_media_suffix": True,
        "hydrated_count": int((receipt or {}).get("hydrated_count", 0) or 0),
        "hydrated_bytes": int((receipt or {}).get("hydrated_bytes", 0) or 0),
        "attempt_key_hashes": list((receipt or {}).get("attempt_keys", ()))[:16],
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


def _tool_content_receipt(content: Any, *, depth: int = 0) -> dict[str, Any] | None:
    if depth > 5:
        return None
    if isinstance(content, Mapping):
        receipt = parse_artifact_receipt(content)
        if receipt is not None:
            return receipt
        for value in list(content.values())[:16]:
            receipt = _tool_content_receipt(value, depth=depth + 1)
            if receipt is not None:
                return receipt
        return None
    if isinstance(content, (list, tuple)):
        for value in content[:16]:
            receipt = _tool_content_receipt(value, depth=depth + 1)
            if receipt is not None:
                return receipt
        return None
    if not isinstance(content, str) or len(content) > 32_768:
        return None
    receipt = parse_artifact_receipt(content)
    if receipt is not None:
        return receipt
    candidates = [content]
    if content.startswith("<aworld-untrusted-data ") and "\n" in content:
        candidates.append(content.split("\n", 1)[1].rsplit("\n", 1)[0])
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        receipt = _tool_content_receipt(decoded, depth=depth + 1)
        if receipt is not None:
            return receipt
    return None


def artifact_receipt_from_tool_content(content: Any) -> dict[str, Any] | None:
    """Recover a validated artifact receipt from framework Tool-result text."""

    return _tool_content_receipt(content)


def _artifact_candidates(
    messages: list[dict[str, Any]],
    *,
    task_scope_hash: str,
) -> list[dict[str, Any]]:
    """Return only complete assistant-call/result/framework-marker chains."""

    candidates: list[dict[str, Any]] = []
    declared: dict[str, str] | None = None
    observed: dict[str, dict[str, Any] | None] = {}
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            declared = None
            observed = {}
            continue
        role = message.get("role")
        if role == "assistant":
            # Any later assistant response proves that an earlier media suffix
            # already reached a complete model turn. Only the newest causal
            # Tool group remains eligible for attachment.
            candidates.clear()
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            if len(message["tool_calls"]) > _MAX_CALL_HASHES_PER_SIDECAR:
                declared = None
                observed = {}
                continue
            values: dict[str, str] = {}
            valid = True
            for call in message["tool_calls"]:
                function = call.get("function") if isinstance(call, Mapping) else None
                call_id = call.get("id") if isinstance(call, Mapping) else None
                name = function.get("name") if isinstance(function, Mapping) else None
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or call_id in values
                    or not isinstance(name, str)
                    or not name
                ):
                    valid = False
                    break
                values[call_id] = name
            declared = values if valid and values else None
            observed = {}
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if (
                declared is None
                or not isinstance(call_id, str)
                or call_id not in declared
                or call_id in observed
            ):
                declared = None
                observed = {}
                continue
            receipt = None
            if declared[call_id].split("__")[-1] == "observe_artifact":
                receipt = _tool_content_receipt(message.get("content"))
            observed[call_id] = receipt
            continue
        content = message.get("content")
        framework_marker = content == ARTIFACT_RETAINED_MESSAGE or (
            isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], Mapping)
            and set(content[0]) == {"type", "text"}
            and content[0].get("type") == "text"
            and content[0].get("text") == ARTIFACT_RETAINED_MESSAGE
        )
        if (
            role == "user"
            and framework_marker
            and declared is not None
            and set(observed) == set(declared)
        ):
            for call_id, name in declared.items():
                if name.split("__")[-1] != "observe_artifact":
                    continue
                receipt = observed.get(call_id)
                if (
                    receipt is None
                    or receipt.get("task_scope_hash") != task_scope_hash
                    or receipt.get("call_id_hash") != artifact_call_id_hash(call_id)
                ):
                    continue
                candidates.append(
                    {
                        "message_index": index,
                        "call_id_hash": receipt["call_id_hash"],
                        "observation_id": receipt["observation_id"],
                    }
                )
            declared = None
            observed = {}
            continue
        declared = None
        observed = {}
    return candidates


def _hydrate_artifact_observation(
    *,
    task_scope_hash: str,
    observation_id: str,
    call_id_hash: str,
) -> tuple[bytes, str] | None:
    with _state_lock:
        token = _sidecar_identity.get((task_scope_hash, observation_id))
        entry = _sidecars.get(token) if token is not None else None
        if entry is None or call_id_hash not in entry.bound_call_hashes:
            return None
        _sidecars.move_to_end(token)
        return entry.data, entry.mime_type


def _delivery_key(candidate: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {
            "observation_id": candidate.get("observation_id"),
            "call_id_hash": candidate.get("call_id_hash"),
        }
    )


def hydrate_artifact_messages(
    messages: list[dict[str, Any]],
    *,
    context: Any,
    agent_id: str,
    vision_enabled: bool,
    media_projection: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project newest undelivered causal observations into provider-native media."""

    task_id = getattr(context, "task_id", None)
    session_id = getattr(context, "session_id", None)
    task_epoch = getattr(context, "task_epoch", 0)
    state = _projection_state(context, agent_id)
    delivered = state.setdefault("delivered", {})
    task_scope_hash = artifact_task_scope_hash(
        task_id=task_id,
        session_id=session_id,
        task_epoch=task_epoch,
    )
    candidates = _artifact_candidates(messages, task_scope_hash=task_scope_hash)
    unique_candidates: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = _delivery_key(candidate)
        unique_candidates.pop(key, None)
        unique_candidates[key] = candidate
    undelivered = [
        candidate
        for candidate in unique_candidates.values()
        if _delivery_key(candidate) not in delivered
    ]
    projection_supported = vision_enabled and media_projection in {
        "openai.image_url.data_url.v1",
        "anthropic.image.base64.v1",
    }
    if not projection_supported or not undelivered:
        return messages, {
            "schema_version": "aworld.artifact-observation-projection/v2",
            "hydrated_count": 0,
            "degraded_count": 0,
            "hydrated_bytes": 0,
            "attempt_keys": [],
            "hydrated_attempt_keys": [],
            "degraded_attempt_keys": [],
            "unsupported_count": len(undelivered),
        }

    selected: list[tuple[dict[str, Any], bytes, str]] = []
    degraded: list[dict[str, Any]] = []
    hydrated_bytes = 0
    for candidate in reversed(undelivered):
        hydrated = _hydrate_artifact_observation(
            task_scope_hash=task_scope_hash,
            observation_id=candidate["observation_id"],
            call_id_hash=candidate["call_id_hash"],
        )
        if hydrated is None:
            degraded.append(candidate)
            continue
        data, mime_type = hydrated
        if (
            len(selected) >= _MAX_PROVIDER_IMAGES_PER_REQUEST
            or hydrated_bytes + len(data) > _MAX_PROVIDER_IMAGE_BYTES_PER_REQUEST
        ):
            degraded.append(candidate)
            continue
        selected.append((candidate, data, mime_type))
        hydrated_bytes += len(data)
    selected.reverse()

    selected_by_message: dict[int, list[tuple[dict[str, Any], bytes, str]]] = {}
    degraded_by_message: dict[int, int] = {}
    for candidate, data, mime_type in selected:
        selected_by_message.setdefault(candidate["message_index"], []).append(
            (candidate, data, mime_type)
        )
    for candidate in degraded:
        index = candidate["message_index"]
        degraded_by_message[index] = degraded_by_message.get(index, 0) + 1

    output = [dict(message) for message in messages]
    for index in set(selected_by_message) | set(degraded_by_message):
        text = ARTIFACT_RETAINED_MESSAGE
        overflow = degraded_by_message.get(index, 0)
        if overflow:
            text += (
                f"\nAWorld media projection degraded {overflow} "
                "overflow/unavailable image(s)."
            )
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for _candidate, data, mime_type in selected_by_message.get(index, ()):
            encoded = base64.b64encode(data).decode("ascii")
            if media_projection == "openai.image_url.data_url.v1":
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    }
                )
            else:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": encoded,
                        },
                    }
                )
        output[index]["content"] = blocks

    attempted = [candidate for candidate, _data, _mime in selected] + degraded
    return output, {
        "schema_version": "aworld.artifact-observation-projection/v2",
        "hydrated_count": len(selected),
        "degraded_count": len(degraded),
        "hydrated_bytes": hydrated_bytes,
        "attempt_keys": [_delivery_key(candidate) for candidate in attempted],
        "hydrated_attempt_keys": [
            _delivery_key(candidate) for candidate, _data, _mime in selected
        ],
        "degraded_attempt_keys": [_delivery_key(candidate) for candidate in degraded],
        "unsupported_count": 0,
    }


def commit_artifact_projection(
    context: Any,
    *,
    agent_id: str,
    receipt: Mapping[str, Any] | None,
) -> None:
    """Commit one-shot delivery only after one complete provider attempt."""

    if not isinstance(receipt, Mapping) or receipt.get("schema_version") != (
        "aworld.artifact-observation-projection/v2"
    ):
        return
    attempt_keys = receipt.get("attempt_keys")
    if not isinstance(attempt_keys, list):
        return
    state = _projection_state(context, agent_id)
    delivered = state.setdefault("delivered", {})
    for attempt_key in attempt_keys[:_MAX_CALL_HASHES_PER_SIDECAR]:
        if isinstance(attempt_key, str) and re.fullmatch(
            r"sha256:[0-9a-f]{64}", attempt_key
        ):
            delivered[attempt_key] = True
    while len(delivered) > _MAX_SIDECAR_ENTRIES:
        delivered.pop(next(iter(delivered)), None)


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
        call_hash = artifact_call_id_hash(tool_call_id)
        if entry is None or call_hash not in entry.bound_call_hashes:
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
