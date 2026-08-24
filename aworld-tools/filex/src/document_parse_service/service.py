"""Shared filex runtime used by MCP tools and CLI."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

from utils import validate_file, verify_file_type

from .asset_reference import AssetReferenceMode
from .document_parse_executor import DocumentParseExecutor
from .document_service_factory import DocumentServiceFactory
from .filex_config import build_default_env_content, merge_env_content
from .media_file_types import MEDIA_FILE_TYPES
from .media_file_types import AUDIO_FILE_TYPES, IMAGE_FILE_TYPES, VIDEO_FILE_TYPES
from .paths import DOCUMENT_PARSE_WORKSPACE, FS_WORKSPACE_ROOT
from .parse_result_cache import (
    CACHE_KEY_VERSION,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    ParseResultCache,
    build_parse_cache_key,
)
from .provider_registry import normalize_provider_env

logger = logging.getLogger(__name__)

SUPPORTED_FILE_TYPES = {
    "pdf",
    "txt",
    "md",
    "markdown",
    "doc",
    "docx",
    "xlsx",
    "xls",
    "csv",
    "ppt",
    "pptx",
    *MEDIA_FILE_TYPES,
}
_ZIP_FILE_TYPES = {
    "word/": "docx",
    "xl/": "xlsx",
    "ppt/": "pptx",
}
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def normalize_env_content(raw: Any) -> dict[str, Any]:
    """Accept dict or JSON string env_content from callers."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("env_content JSON string must decode to an object")
        return parsed
    raise ValueError("env_content must be a dict or JSON string")


class DocumentParseService:
    """Parses files that have been placed in the FileX workspace."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = workspace_root or FS_WORKSPACE_ROOT
        self._output_root = self._workspace_root / "document_parse"
        self._parse_result_cache = ParseResultCache(self._workspace_root / ".filex_parse_cache")

    async def parse(
        self,
        *,
        workspace_path: Optional[str] = None,
        file_type: Optional[str] = None,
        task_id: Optional[str] = None,
        sync_mode: str = "sync",
        asset_reference_mode: AssetReferenceMode = "remote_id",
        env_content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_workspace_path = str(workspace_path or "").strip()
        if not normalized_workspace_path:
            raise ValueError("workspace_path is required")
        if sync_mode not in {"sync", "async"}:
            raise ValueError("sync_mode must be 'sync' or 'async'")

        normalized_env = normalize_env_content(env_content)
        if file_type:
            normalized_env = normalize_provider_env(file_type, normalized_env, use_default=False)
        cache_enabled = _as_bool(normalized_env.pop("filex_cache_enabled", True))
        no_cache = _as_bool(normalized_env.pop("filex_no_cache", False))
        force_refresh = _as_bool(normalized_env.pop("filex_force_refresh", False))
        if sync_mode != "sync" or not cache_enabled or no_cache:
            result = await self._parse_uncached(
                workspace_path=normalized_workspace_path,
                file_type=file_type,
                task_id=task_id,
                sync_mode=sync_mode,
                asset_reference_mode=asset_reference_mode,
                env_content=normalized_env,
            )
            result.setdefault("metrics", {})["cache"] = {
                "status": "bypass",
                "key_version": CACHE_KEY_VERSION,
                "lookup_duration_ms": 0,
                "single_flight_wait_duration_ms": 0,
                "age_ms": 0,
                "saved_duration_ms": 0,
            }
            return result

        ttl_seconds = _positive_int(
            normalized_env.pop("filex_cache_ttl_seconds", DEFAULT_TTL_SECONDS),
            name="filex_cache_ttl_seconds",
        )
        max_entries = _positive_int(
            normalized_env.pop("filex_cache_max_entries", DEFAULT_MAX_ENTRIES),
            name="filex_cache_max_entries",
        )
        cache_env = normalized_env
        if file_type:
            normalized_file_type = file_type.lower().lstrip(".")
            cache_env = merge_env_content(
                build_default_env_content(
                    file_type=normalized_file_type,
                    media_type=self._resolve_media_type(normalized_file_type),
                ),
                normalized_env,
            )
            cache_env = normalize_provider_env(normalized_file_type, cache_env)
        cache_key = build_parse_cache_key(
            file_id="",
            workspace_path=normalized_workspace_path,
            file_type=file_type,
            asset_reference_mode=asset_reference_mode,
            env_content=cache_env,
        )
        return await self._parse_result_cache.get_or_compute(
            key=cache_key,
            task_id=task_id,
            ttl_seconds=ttl_seconds,
            max_entries=max_entries,
            force_refresh=force_refresh,
            compute=lambda: self._parse_uncached(
                workspace_path=normalized_workspace_path,
                file_type=file_type,
                task_id=task_id,
                sync_mode=sync_mode,
                asset_reference_mode=asset_reference_mode,
                env_content=normalized_env,
            ),
        )

    async def _parse_uncached(
        self,
        *,
        workspace_path: Optional[str] = None,
        file_type: Optional[str] = None,
        task_id: Optional[str] = None,
        sync_mode: str = "sync",
        asset_reference_mode: AssetReferenceMode = "remote_id",
        env_content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_workspace_path = str(workspace_path or "").strip()
        normalized_env = normalize_env_content(env_content)
        resolved_task_id = self._resolve_task_id(
            workspace_path=normalized_workspace_path,
            task_id=task_id,
        )
        source_path = self._resolve_workspace_input_path(normalized_workspace_path)
        source_file_id = ""
        runtime_metrics = {"queue": 0, "download": 0}
        source_file_type = self._resolve_file_type(
            source_path,
            explicit_file_type=file_type,
        )
        normalized_env = merge_env_content(
            build_default_env_content(
                file_type=source_file_type,
                media_type=self._resolve_media_type(source_file_type),
            ),
            normalized_env,
        )
        normalized_env = normalize_provider_env(source_file_type, normalized_env)
        validation_result = await validate_file(source_path)
        if not validation_result["is_valid"]:
            raise RuntimeError(validation_result["error_message"])
        if not await verify_file_type(source_path, source_file_type):
            raise RuntimeError(f"下载的文件类型与指定的file_type不匹配。预期: {source_file_type}")

        source_path = self._ensure_expected_file_suffix(source_path, source_file_type)
        source_file_name = source_path.stem

        document_service_env = dict(normalized_env)
        if source_file_id:
            document_service_env.setdefault("media_parse_file_id", source_file_id)

        document_service = DocumentServiceFactory.create(
            file_type=source_file_type,
            env_content=document_service_env,
            asset_reference_mode=asset_reference_mode,
        )
        executor = DocumentParseExecutor(document_service)

        if sync_mode == "async":
            result = await executor.async_parse(
                file_path=source_path,
                task_id=resolved_task_id,
                source_file_id=source_file_id,
                source_file_name=source_file_name,
                afts_service=None,
                run_in_background=True,
                runtime_metrics=runtime_metrics,
            )
            return {
                "success": True,
                "message": "Document parsing task started",
                "file_type": source_file_type,
                "task_id": result["task_id"],
                "source_file_id": result["source_file_id"],
                "output_file_id": "",
                "file_path": "",
                "source_file_path": self._to_workspace_relative_path(source_path),
                "file_dir_path": self._to_workspace_relative_path(self._output_root / resolved_task_id),
            }

        result = await executor.sync_parse(
            file_path=source_path,
            task_id=resolved_task_id,
            source_file_id=source_file_id,
            source_file_name=source_file_name,
            afts_service=None,
            runtime_metrics=runtime_metrics,
        )
        self._apply_provider_identity(result, normalized_env)
        output_file_id = result.get("output_file_id") or ""
        file_url = ""
        return {
            "success": True,
            "message": "Document parsed successfully",
            "file_type": source_file_type,
            "task_id": result["task_id"],
            "source_file_id": result["source_file_id"],
            "output_file_id": output_file_id,
            "file_path": result.get("file_path", ""),
            "source_file_path": self._to_workspace_relative_path(source_path),
            "file_url": file_url,
            "file_dir_path": self._to_workspace_relative_path(self._output_root / resolved_task_id),
            "metrics": result.get("metrics") or {},
            "metrics_file_path": result.get("metrics_file_path") or "",
            "evidence_file_path": result.get("evidence_file_path") or "",
        }

    @staticmethod
    def _apply_provider_identity(result: dict[str, Any], env_content: dict[str, Any]) -> None:
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            return
        metrics["provider"] = str(env_content.get("filex_parse_provider") or metrics.get("provider") or "")
        metrics["provider_version"] = str(
            env_content.get("filex_provider_version") or metrics.get("provider_version") or ""
        )
        metrics_relative_path = str(result.get("metrics_file_path") or "").strip()
        if not metrics_relative_path:
            return
        metrics_path = FS_WORKSPACE_ROOT / metrics_relative_path
        metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _resolve_output_path(self, output_path: str) -> Path:
        normalized = str(output_path or "").strip()
        if not normalized:
            raise ValueError("output_path is required")
        path = Path(normalized).expanduser()
        if not path.is_absolute():
            raise ValueError("output_path must be an absolute path")
        self._ensure_under_workspace(path)
        return path

    def _resolve_workspace_input_path(self, workspace_path: str) -> Path:
        normalized = str(workspace_path or "").strip()
        if not normalized:
            raise ValueError("workspace_path is required")
        path = Path(normalized).expanduser()
        if not path.is_absolute():
            raise ValueError("workspace_path must be an absolute path")
        self._ensure_under_workspace(path)
        if not path.exists():
            raise FileNotFoundError(f"file not found: {path}")
        return path

    def _resolve_file_type(self, source_path: Path, explicit_file_type: Optional[str]) -> str:
        if explicit_file_type:
            normalized_type = explicit_file_type.lower().strip()
            if normalized_type not in SUPPORTED_FILE_TYPES:
                raise ValueError(f"Unsupported file type: {explicit_file_type}")
            return "md" if normalized_type == "markdown" else normalized_type

        suffix = source_path.suffix.lower().lstrip(".")
        if suffix in SUPPORTED_FILE_TYPES:
            return "md" if suffix == "markdown" else suffix

        header = source_path.read_bytes()[:16]
        if header.startswith(b"%PDF"):
            return "pdf"
        detected_image_type = self._detect_image_file_type(header)
        if detected_image_type:
            return detected_image_type
        detected_media_type = self._detect_media_file_type(header)
        if detected_media_type:
            return detected_media_type
        if header.startswith(_OLE2_SIGNATURE):
            raise ValueError("无法从无后缀 OLE 文件判断类型，请显式传入 file_type")
        if header.startswith(b"PK\x03\x04"):
            with zipfile.ZipFile(source_path) as archive:
                names = archive.namelist()
            for prefix, detected_type in _ZIP_FILE_TYPES.items():
                if any(name.startswith(prefix) for name in names):
                    return detected_type

        raise ValueError(
            f"Unsupported file type: could not infer from path {source_path.name}. "
            "Pass file_type explicitly."
        )

    @staticmethod
    def _detect_media_file_type(header: bytes) -> str:
        if header.startswith(b"ID3") or header.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
            return "mp3"
        if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
            return "wav"
        if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
            return "avi"
        if header.startswith(b"fLaC"):
            return "flac"
        if header.startswith(b"OggS"):
            return "ogg"
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            return "mkv"
        if len(header) >= 12 and header[4:8] == b"ftyp":
            return "mp4"
        if header.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")):
            return "mpeg"
        return ""

    @staticmethod
    def _detect_image_file_type(header: bytes) -> str:
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if header.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
            return "gif"
        if header.startswith(b"BM"):
            return "bmp"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "webp"
        return ""

    @staticmethod
    def _resolve_media_type(file_type: str) -> str:
        normalized_file_type = file_type.lower().strip()
        if normalized_file_type in AUDIO_FILE_TYPES:
            return "audio"
        if normalized_file_type in VIDEO_FILE_TYPES:
            return "video"
        if normalized_file_type in IMAGE_FILE_TYPES:
            return "image"
        return ""

    def _resolve_task_id(
        self,
        *,
        workspace_path: str,
        task_id: Optional[str],
    ) -> str:
        normalized_task_id = str(task_id or "").strip()
        if normalized_task_id:
            return normalized_task_id
        source_path = Path(workspace_path)
        return f"{source_path.stem}_{int(time.time() * 1000)}"

    def _ensure_under_workspace(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self._workspace_root.resolve())
        except ValueError as exc:
            raise ValueError("path must be under the filesystem workspace") from exc

    def _move_file(self, source: Path, target: Path) -> Path:
        if source == target:
            return source
        if target.exists():
            target.unlink()
        shutil.move(str(source), str(target))
        return target

    @staticmethod
    def _copy_file(source: Path, target: Path) -> Path:
        """Copy a shared download into task-owned storage without consuming it."""

        if source == target:
            return source
        if not source.is_file():
            raise FileNotFoundError(f"downloaded source file not found: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_target = target.with_name(
            f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        temporary_target.unlink(missing_ok=True)
        try:
            shutil.copy2(source, temporary_target)
            temporary_target.replace(target)
        finally:
            temporary_target.unlink(missing_ok=True)
        return target

    def _ensure_expected_file_suffix(self, file_path: Path, expected_type: str) -> Path:
        expected_suffix = f".{expected_type.lower()}"
        current_suffix = file_path.suffix.lower()
        if current_suffix == expected_suffix:
            return file_path
        if file_path.name.startswith(".") and file_path.stem == "":
            target_path = file_path.with_name(f"downloaded_file{expected_suffix}")
        else:
            target_path = file_path.with_suffix(expected_suffix)
        if target_path.exists() and target_path != file_path:
            target_path = file_path.with_name(
                f"{file_path.stem or 'downloaded_file'}_{int(time.time() * 1000)}{expected_suffix}"
            )
        logger.info(
            "document_parse normalize downloaded file suffix | old_path=%s new_path=%s expected_type=%s",
            file_path,
            target_path,
            expected_type,
        )
        return self._move_file(file_path, target_path)

    def _to_workspace_relative_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self._workspace_root.resolve()))
        except ValueError:
            return str(path)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _positive_int(value: Any, *, name: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} must be greater than zero")
    return parsed
