from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4


@dataclass
class PublishedArtifact:
    token: str
    path: Path
    source_path: Path
    content_type: str
    published_at: datetime


class ArtifactService:
    def __init__(self, public_base_url: str | None, allowed_roots: list[Path]) -> None:
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._allowed_roots = [root.resolve() for root in allowed_roots]
        self._artifacts: dict[str, PublishedArtifact] = {}
        self._snapshot_root = Path(tempfile.mkdtemp(prefix="aworld-gateway-artifacts-")).resolve()

    def _normalize_and_validate_path(
        self,
        path: str | Path,
        *,
        raise_on_missing: bool,
        raise_on_outside_roots: bool,
    ) -> Path | None:
        input_path = Path(path)
        try:
            resolved_path = input_path.resolve(strict=True)
        except FileNotFoundError:
            if raise_on_missing:
                raise
            return None

        if not resolved_path.is_file():
            if raise_on_missing:
                raise FileNotFoundError(str(path))
            return None

        is_allowed = any(resolved_path.is_relative_to(root) for root in self._allowed_roots)
        if not is_allowed:
            if raise_on_outside_roots:
                raise ValueError(f"Path is outside allowed roots: {path}")
            return None

        return resolved_path

    def publish(self, path: str | Path) -> str:
        resolved_source_path = self._normalize_and_validate_path(
            path,
            raise_on_missing=True,
            raise_on_outside_roots=True,
        )
        assert resolved_source_path is not None

        token = uuid4().hex
        snapshot_dir = self._snapshot_root / token
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = (snapshot_dir / resolved_source_path.name).resolve()
        shutil.copy2(resolved_source_path, snapshot_path)

        content_type = mimetypes.guess_type(str(resolved_source_path))[0] or "application/octet-stream"
        self._artifacts[token] = PublishedArtifact(
            token=token,
            path=snapshot_path,
            source_path=resolved_source_path,
            content_type=content_type,
            published_at=datetime.now(timezone.utc),
        )
        return token

    def resolve(self, token: str) -> PublishedArtifact | None:
        artifact = self._artifacts.get(token)
        if artifact is None:
            return None

        if not artifact.path.exists() or not artifact.path.is_file():
            self._artifacts.pop(token, None)
            return None

        if artifact.source_path.exists():
            resolved_source_path = self._normalize_and_validate_path(
                artifact.source_path,
                raise_on_missing=False,
                raise_on_outside_roots=False,
            )
            if resolved_source_path is None:
                self._artifacts.pop(token, None)
                return None
            artifact.source_path = resolved_source_path

        return artifact

    def build_external_url(self, token: str) -> str:
        if self._public_base_url is None:
            raise ValueError("Artifact public_base_url is not configured.")
        suffix = f"/artifacts/{token}"
        return f"{self._public_base_url}{suffix}"
