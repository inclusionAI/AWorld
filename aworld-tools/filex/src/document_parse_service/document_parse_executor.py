"""Execute document parsing and normalize result paths."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .document_service import DocumentService
from .paths import FS_WORKSPACE_ROOT

logger = logging.getLogger(__name__)


class DocumentParseExecutor:
    """Run the shared document parsing pipeline."""

    def __init__(self, document_service: DocumentService) -> None:
        self._document_service = document_service

    async def sync_parse(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_id: str,
        source_file_name: str,
        afts_service: Optional[Any] = None,
        runtime_metrics: Optional[dict[str, int]] = None,
    ) -> dict[str, Any]:
        start_time = time.time()
        file_size = file_path.stat().st_size if file_path.exists() else 0
        logger.info(
            "document_parse_executor.sync_parse started | "
            f"task_id={task_id} file_path={file_path} file_size={file_size} "
            f"source_file_id={source_file_id} source_file_name={source_file_name} "
            f"service_type={type(self._document_service).__name__}"
        )

        parsed_file_path = await self._document_service.parse_to_markdown(
            file_path=file_path,
            task_id=task_id,
            source_file_name=source_file_name,
            afts_service=afts_service,
        )
        if not parsed_file_path.exists():
            raise RuntimeError(f"解析后的文件路径无效或文件不存在: {parsed_file_path}")

        output_file_id = None
        upload_duration_ms = 0
        if afts_service:
            upload_start = time.time()
            logger.info(
                "document_parse_executor.upload_parsed_content started | "
                f"task_id={task_id} parsed_file_path={parsed_file_path}"
            )
            output_file_id = await self._upload_parsed_content(
                parsed_file_path=parsed_file_path,
                afts_service=afts_service,
            )
            upload_duration_ms = int((time.time() - upload_start) * 1000)
            logger.info(
                "document_parse_executor.upload_parsed_content completed | "
                f"task_id={task_id} output_file_id={output_file_id} "
                f"duration={time.time() - upload_start:.3f}s"
            )

        relative_path = self._to_workspace_relative_path(parsed_file_path)
        metrics = self._load_metrics(parsed_file_path)
        if metrics:
            completed_runtime_metrics = dict(runtime_metrics or {})
            completed_runtime_metrics["upload"] = upload_duration_ms
            completed_runtime_metrics["total"] = (
                int((time.time() - start_time) * 1000)
                + int(completed_runtime_metrics.get("queue") or 0)
                + int(completed_runtime_metrics.get("download") or 0)
            )
            self._apply_runtime_metrics(metrics, completed_runtime_metrics)
            parsed_file_path.with_suffix(".metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        logger.info(
            "document_parse_executor.sync_parse success | "
            f"task_id={task_id} output_file_id={output_file_id} "
            f"parsed_file_path={parsed_file_path} duration={time.time() - start_time:.3f}s"
        )
        evidence_path = parsed_file_path.with_suffix(".evidence.json")
        document_path = parsed_file_path.with_suffix(".document.json")
        storyboard_path = parsed_file_path.with_suffix(".storyboard.jpg")
        return {
            "task_id": task_id,
            "source_file_id": source_file_id,
            "output_file_id": output_file_id,
            "file_path": relative_path,
            "metrics": metrics,
            "metrics_file_path": self._to_workspace_relative_path(
                parsed_file_path.with_suffix(".metrics.json")
            )
            if metrics
            else "",
            "evidence_file_path": self._to_workspace_relative_path(evidence_path)
            if evidence_path.exists()
            else "",
            "document_file_path": self._to_workspace_relative_path(document_path)
            if document_path.exists()
            else "",
            "storyboard_file_path": self._to_workspace_relative_path(storyboard_path)
            if storyboard_path.exists()
            else "",
        }

    async def async_parse(
        self,
        *,
        file_path: Path,
        task_id: str,
        source_file_id: str,
        source_file_name: str,
        afts_service: Optional[Any] = None,
        run_in_background: bool = False,
        runtime_metrics: Optional[dict[str, int]] = None,
    ) -> dict[str, Any]:
        async def _parse_and_upload() -> dict[str, Any]:
            try:
                return await self.sync_parse(
                    file_path=file_path,
                    task_id=task_id,
                    source_file_id=source_file_id,
                    source_file_name=source_file_name,
                    afts_service=afts_service,
                    runtime_metrics=runtime_metrics,
                )
            except BaseException as exc:
                logger.warning(
                    "document_parse_executor.async_parse failed | "
                    f"task_id={task_id} error={exc}",
                    exc_info=True,
                )
                return {
                    "task_id": task_id,
                    "source_file_id": source_file_id,
                    "output_file_id": None,
                    "file_path": "",
                }

        if run_in_background:
            asyncio.create_task(_parse_and_upload())
            return {
                "task_id": task_id,
                "source_file_id": source_file_id,
                "output_file_id": None,
                "file_path": "",
            }
        return await _parse_and_upload()

    @staticmethod
    async def _upload_parsed_content(
        *,
        parsed_file_path: Path,
        afts_service: Any,
    ) -> str | None:
        try:
            return await afts_service.upload_file(
                file_path=parsed_file_path,
                file_name=parsed_file_path.name,
                setpublic=True,
                update_alias=True,
            )
        except BaseException as exc:
            logger.warning(
                "document_parse_executor upload parsed content failed | "
                f"parsed_file_path={parsed_file_path} error={exc}",
                exc_info=True,
            )
            return None

    @staticmethod
    def _to_workspace_relative_path(parsed_file_path: Path) -> str:
        workspace_base = FS_WORKSPACE_ROOT
        try:
            if parsed_file_path.is_relative_to(workspace_base):
                return str(parsed_file_path.relative_to(workspace_base))
            return str(parsed_file_path.relative_to(Path.home()))
        except ValueError:
            return str(parsed_file_path)

    @staticmethod
    def _load_metrics(parsed_file_path: Path) -> dict[str, Any]:
        metrics_path = parsed_file_path.with_suffix(".metrics.json")
        if not metrics_path.exists():
            return {}
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _apply_runtime_metrics(
        metrics: dict[str, Any], runtime_metrics: dict[str, int]
    ) -> None:
        timings = metrics.get("timings_ms")
        if not isinstance(timings, dict):
            return
        for key in ("queue", "download", "upload"):
            if key in runtime_metrics:
                timings[key] = max(0, int(runtime_metrics[key]))
        if "total" in runtime_metrics:
            timings["total"] = max(0, int(runtime_metrics["total"]))
        else:
            timings["total"] = (
                int(timings.get("total") or 0)
                + int(timings.get("queue") or 0)
                + int(timings.get("download") or 0)
                + int(timings.get("upload") or 0)
            )
