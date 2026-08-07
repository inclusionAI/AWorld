"""
统一的文档解析阶段日志。
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


class DocumentParseLogger:
    """输出统一格式的解析阶段日志。"""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        task_id: str,
        file_type: str,
        file_path: Path,
        source_file_name: str,
        stage_names: Sequence[str],
    ) -> None:
        self._logger = logger
        self._base_fields = {
            "task_id": task_id,
            "file_type": file_type,
            "file_path": str(file_path),
            "source_file_name": source_file_name,
        }
        self._stage_to_index = {
            stage_name: index for index, stage_name in enumerate(stage_names, start=1)
        }
        self._stage_total = len(stage_names)
        self.stage_durations_ms: dict[str, int] = {}

    @contextmanager
    def stage(self, stage: str, **fields: Any) -> Iterator[None]:
        """记录阶段开始、结束和失败日志。"""
        stage_start = time.perf_counter()
        self.emit(stage, "started", **fields)
        try:
            yield
        except BaseException as exc:
            self.emit(
                stage,
                "failed",
                duration_ms=int((time.perf_counter() - stage_start) * 1000),
                error=str(exc),
                **fields,
            )
            raise
        duration_ms = int((time.perf_counter() - stage_start) * 1000)
        self.stage_durations_ms[stage] = duration_ms
        self.emit(
            stage,
            "completed",
            duration_ms=duration_ms,
            **fields,
        )

    def progress(self, stage: str, **fields: Any) -> None:
        """记录阶段内进度。"""
        self.emit(stage, "progress", **fields)

    def emit(self, stage: str, status: str, **fields: Any) -> None:
        """输出一条阶段日志。"""
        record = dict(self._base_fields)
        stage_index = self._stage_to_index.get(stage)
        record.update(
            {
                "stage": stage,
                "stage_index": stage_index if stage_index is not None else 0,
                "stage_total": self._stage_total,
                "status": status,
            }
        )
        for key, value in fields.items():
            if value is not None:
                record[key] = value
        serialized = " ".join(
            f"{key}={self._format_value(value)}" for key, value in record.items()
        )
        self._logger.info("document_parse.stage | %s", serialized)

    def _format_value(self, value: Any) -> str:
        if isinstance(value, Path):
            return self._format_value(str(value))
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (list, dict, tuple)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return json.dumps(str(value), ensure_ascii=False)
