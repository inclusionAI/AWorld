from __future__ import annotations

import base64
import json
import zlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, features

import aworld.sandbox.artifact_observation as artifact_module
from aworld.sandbox.artifact_observation import (
    ARTIFACT_OBSERVATION_SCHEMA,
    ArtifactObservationError,
    artifact_task_scope_hash,
    clear_artifact_observation_state,
    observe_artifact_bytes,
    observe_local_artifact,
    register_artifact_sidecar,
)


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4"
    "z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
_PNG_1X1_BLUE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNg"
    "YPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
)
_GIF_1X1 = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")


@pytest.fixture(autouse=True)
def _reset_artifact_state() -> None:
    clear_artifact_observation_state()


def _scope() -> dict[str, object]:
    return {
        "task_id": "task-1",
        "session_id": "session-1",
        "task_epoch": 2,
        "tool_call_id": "call-1",
    }


def test_observe_local_artifact_returns_bounded_typed_receipt(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(_PNG_1X1)

    observed = observe_local_artifact(
        str(image), workspace_root=tmp_path, framework_scope=_scope()
    )

    assert observed.data == _PNG_1X1
    assert observed.receipt == {
        **observed.receipt,
        "schema_version": ARTIFACT_OBSERVATION_SCHEMA,
        "status": "ready",
        "media_type": "image",
        "mime_type": "image/png",
        "width": 1,
        "height": 1,
        "byte_count": len(_PNG_1X1),
        "task_scope_hash": artifact_task_scope_hash(
            task_id="task-1", session_id="session-1", task_epoch=2
        ),
    }
    assert observed.receipt["content_sha256"].startswith("sha256:")
    assert observed.receipt["observation_id"].startswith("sha256:")
    assert observed.receipt["file_epoch"].startswith("sha256:")
    assert observed.receipt["call_id_hash"].startswith("sha256:")
    assert str(image) not in json.dumps(observed.receipt)


def test_artifact_task_scope_accepts_opaque_epoch_without_exposing_it() -> None:
    receipt = artifact_task_scope_hash(
        task_id="task", session_id="session", task_epoch="resume:branch/a"
    )

    assert receipt.startswith("sha256:")
    assert "resume:branch/a" not in receipt


def test_artifact_task_scope_hashes_complete_opaque_epoch_without_collision() -> None:
    shared = "x" * 256

    first = artifact_task_scope_hash(
        task_id="task", session_id="session", task_epoch=shared + "a"
    )
    second = artifact_task_scope_hash(
        task_id="task", session_id="session", task_epoch=shared + "b"
    )

    assert first != second


def test_sidecar_rejects_encoded_length_before_base64_decode(monkeypatch) -> None:
    observed = observe_artifact_bytes(
        _PNG_1X1,
        suffix=".png",
        path_key="sha256:path",
        file_epoch="sha256:" + ("0" * 64),
        framework_scope=_scope(),
    )

    def decode_must_not_run(*args, **kwargs):
        raise AssertionError("oversized encoded payload reached decoder")

    monkeypatch.setattr(artifact_module.base64, "b64decode", decode_must_not_run)
    with pytest.raises(ArtifactObservationError, match="payload length"):
        register_artifact_sidecar(
            image_base64="A" * (len(_PNG_1X1) * 128),
            mime_type="image/png",
            receipt=observed.receipt,
        )


def test_observe_local_artifact_reuses_identity_until_content_changes(
    tmp_path: Path,
) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(_PNG_1X1)

    first = observe_local_artifact(
        str(image), workspace_root=tmp_path, framework_scope=_scope()
    )
    second = observe_local_artifact(
        str(image), workspace_root=tmp_path, framework_scope=_scope()
    )
    image.write_bytes(_PNG_1X1_BLUE)
    third = observe_local_artifact(
        str(image), workspace_root=tmp_path, framework_scope=_scope()
    )

    assert first.receipt["cache_state"] == "new"
    assert second.receipt["cache_state"] == "retained"
    assert second.receipt["observation_id"] == first.receipt["observation_id"]
    assert third.receipt["cache_state"] == "changed"
    assert third.receipt["observation_id"] != first.receipt["observation_id"]


@pytest.mark.parametrize("kind", ["outside", "symlink", "directory"])
def test_observe_local_artifact_rejects_unsafe_paths(tmp_path: Path, kind: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG_1X1)
    if kind == "outside":
        target = outside
    elif kind == "symlink":
        target = workspace / "link.png"
        target.symlink_to(outside)
    else:
        target = workspace

    with pytest.raises(ArtifactObservationError):
        observe_local_artifact(
            str(target), workspace_root=workspace, framework_scope=_scope()
        )


def test_observe_local_artifact_rejects_intermediate_symlink_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    (nested / "pixel.png").write_bytes(_PNG_1X1)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pixel.png").write_bytes(_PNG_1X1_BLUE)
    original_open = artifact_module.os.open
    swapped = False

    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            nested.rename(workspace / "nested-original")
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(artifact_module.os, "open", racing_open)
    monkeypatch.setattr(
        artifact_module.os,
        "supports_dir_fd",
        {*artifact_module.os.supports_dir_fd, racing_open},
    )

    with pytest.raises(ArtifactObservationError):
        observe_local_artifact(
            str(nested / "pixel.png"),
            workspace_root=workspace,
            framework_scope=_scope(),
        )
    assert swapped is True


def test_observe_local_artifact_rejects_mime_mismatch_and_oversize(
    tmp_path: Path,
) -> None:
    mismatched = tmp_path / "pixel.jpg"
    mismatched.write_bytes(_PNG_1X1)
    with pytest.raises(ArtifactObservationError, match="MIME"):
        observe_local_artifact(
            str(mismatched), workspace_root=tmp_path, framework_scope=_scope()
        )

    oversized = tmp_path / "large.png"
    oversized.write_bytes(_PNG_1X1 + b"x" * 32)
    with pytest.raises(ArtifactObservationError, match="byte limit"):
        observe_local_artifact(
            str(oversized),
            workspace_root=tmp_path,
            framework_scope=_scope(),
            max_bytes=len(_PNG_1X1),
        )


def test_observe_local_artifact_rejects_decompression_bomb_dimensions(
    tmp_path: Path,
) -> None:
    image = tmp_path / "bomb.png"
    data = bytearray(_PNG_1X1)
    data[16:20] = (20_000).to_bytes(4, "big")
    data[20:24] = (20_000).to_bytes(4, "big")
    data[29:33] = zlib.crc32(data[12:29]).to_bytes(4, "big")
    image.write_bytes(data)

    with pytest.raises(ArtifactObservationError, match="dimensions"):
        observe_local_artifact(
            str(image), workspace_root=tmp_path, framework_scope=_scope()
        )


def test_observe_local_artifact_rejects_24_byte_png_header_reproduction(
    tmp_path: Path,
) -> None:
    image = tmp_path / "header-only.png"
    image.write_bytes(_PNG_1X1[:24])

    with pytest.raises(ArtifactObservationError, match="truncated|corrupt|unsupported"):
        observe_local_artifact(
            str(image), workspace_root=tmp_path, framework_scope=_scope()
        )


def test_observe_local_artifact_accepts_static_gif_and_rejects_animation(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static.gif"
    static.write_bytes(_GIF_1X1)
    assert (
        observe_local_artifact(
            str(static), workspace_root=tmp_path, framework_scope=_scope()
        ).receipt["mime_type"]
        == "image/gif"
    )

    # Duplicate the single image descriptor/data before the trailer to form a
    # valid two-frame structure without relying on an image library.
    animated = tmp_path / "animated.gif"
    image_block = _GIF_1X1[19:-1]
    animated.write_bytes(_GIF_1X1[:-1] + image_block + b"\x3b")
    with pytest.raises(ArtifactObservationError, match="animated"):
        observe_local_artifact(
            str(animated), workspace_root=tmp_path, framework_scope=_scope()
        )


@pytest.mark.parametrize(
    ("format_name", "suffix", "mime_type"),
    (
        ("PNG", ".png", "image/png"),
        ("JPEG", ".jpg", "image/jpeg"),
        ("WEBP", ".webp", "image/webp"),
        ("GIF", ".gif", "image/gif"),
    ),
)
def test_observe_local_artifact_fully_decodes_supported_static_images(
    tmp_path: Path,
    format_name: str,
    suffix: str,
    mime_type: str,
) -> None:
    if format_name == "WEBP" and not features.check("webp"):
        pytest.skip("Pillow build does not include WebP")
    stream = BytesIO()
    Image.new("RGB", (3, 2), (12, 34, 56)).save(stream, format=format_name)
    path = tmp_path / f"static{suffix}"
    path.write_bytes(stream.getvalue())

    observed = observe_local_artifact(
        str(path), workspace_root=tmp_path, framework_scope=_scope()
    )

    assert observed.receipt["mime_type"] == mime_type
    assert (observed.receipt["width"], observed.receipt["height"]) == (3, 2)


@pytest.mark.parametrize(
    ("format_name", "suffix"), (("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"))
)
def test_observe_local_artifact_rejects_truncated_supported_images(
    tmp_path: Path, format_name: str, suffix: str
) -> None:
    if format_name == "WEBP" and not features.check("webp"):
        pytest.skip("Pillow build does not include WebP")
    stream = BytesIO()
    Image.new("RGB", (3, 2), (12, 34, 56)).save(stream, format=format_name)
    payload = stream.getvalue()
    path = tmp_path / f"truncated{suffix}"
    path.write_bytes(payload[: max(24, len(payload) // 2)])

    with pytest.raises(ArtifactObservationError, match="truncated|corrupt|unsupported"):
        observe_local_artifact(
            str(path), workspace_root=tmp_path, framework_scope=_scope()
        )


@pytest.mark.parametrize(
    ("format_name", "suffix"), (("GIF", ".gif"), ("PNG", ".png"), ("WEBP", ".webp"))
)
def test_observe_local_artifact_rejects_animated_image_containers(
    tmp_path: Path, format_name: str, suffix: str
) -> None:
    stream = BytesIO()
    frames = [
        Image.new("RGBA", (2, 2), (255, 0, 0, 255)),
        Image.new("RGBA", (2, 2), (0, 0, 255, 255)),
    ]
    try:
        frames[0].save(
            stream,
            format=format_name,
            save_all=True,
            append_images=frames[1:],
            duration=20,
            loop=0,
        )
    except OSError:
        pytest.skip(f"Pillow build does not include animated {format_name}")
    path = tmp_path / f"animated{suffix}"
    path.write_bytes(stream.getvalue())

    with pytest.raises(ArtifactObservationError, match="animated"):
        observe_local_artifact(
            str(path), workspace_root=tmp_path, framework_scope=_scope()
        )
