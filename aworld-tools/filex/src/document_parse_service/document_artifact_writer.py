"""
文档产物写入抽象。

后续将承接 MarkdownArtifact 的统一落盘逻辑；
当前仅提供最小写盘能力，尚未接管现有主链路。
"""

from __future__ import annotations

import json
from pathlib import Path

from .document_artifact_models import MarkdownArtifact


class DocumentArtifactWriter:
    """统一文档产物写入器。"""

    def write_markdown(
        self,
        artifact: MarkdownArtifact,
        *,
        output_dir: Path,
        file_name: str,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / file_name
        output_path.write_text(artifact.markdown_text, encoding="utf-8")
        return output_path

    def write_metrics(self, metrics: dict, *, output_path: Path) -> Path:
        metrics_path = output_path.with_suffix(".metrics.json")
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        return metrics_path
