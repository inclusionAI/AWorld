from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
from uuid import uuid4


@dataclass
class PublishedArtifact:
    token: str
    path: Path
    content_type: str
    published_at: datetime


class ArtifactService:
    def __init__(self, public_base_url: str | None, allowed_roots: list[Path]) -> None:
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._allowed_roots = [root.resolve() for root in allowed_roots]
        self._artifacts: dict[str, PublishedArtifact] = {}

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
        resolved_path = self._normalize_and_validate_path(
            path,
            raise_on_missing=True,
            raise_on_outside_roots=True,
        )
        assert resolved_path is not None

        token = uuid4().hex
        content_type = mimetypes.guess_type(str(resolved_path))[0] or "application/octet-stream"
        self._artifacts[token] = PublishedArtifact(
            token=token,
            path=resolved_path,
            content_type=content_type,
            published_at=datetime.now(timezone.utc),
        )
        return token

    def resolve(self, token: str) -> PublishedArtifact | None:
        artifact = self._artifacts.get(token)
        if artifact is None:
            return None
        resolved_path = self._normalize_and_validate_path(
            artifact.path,
            raise_on_missing=False,
            raise_on_outside_roots=False,
        )
        if resolved_path is None:
            self._artifacts.pop(token, None)
            return None
        artifact.path = resolved_path
        return artifact

    def build_external_url(self, token: str) -> str:
        suffix = f"/artifacts/{token}"
        if self._public_base_url is None:
            return suffix
        return f"{self._public_base_url}{suffix}"
