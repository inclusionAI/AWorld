from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from aworld.sandbox.artifact_observation import (
    ARTIFACT_OBSERVATION_SCHEMA,
    ArtifactObservationError,
    artifact_task_scope_hash,
    clear_artifact_observation_state,
    observe_local_artifact,
)


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Y9Z4E8AAAAASUVORK5CYII="
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
    changed_bytes = bytearray(_PNG_1X1)
    changed_bytes[-1] ^= 1
    image.write_bytes(changed_bytes)
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
    image.write_bytes(data)

    with pytest.raises(ArtifactObservationError, match="dimensions"):
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
    with pytest.raises(ArtifactObservationError, match="animated GIF"):
        observe_local_artifact(
            str(animated), workspace_root=tmp_path, framework_scope=_scope()
        )
