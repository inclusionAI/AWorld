"""Durable successful-batch checkpoints for resumable PDF parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..document_artifact_models import DocumentAnchor, DocumentAsset, MarkdownArtifact


_SAFE_RESUME_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class PdfBatchCheckpointStore:
    """Persist only completed PDF batches under the mounted FileX workspace."""

    def __init__(self, root: Path, resume_id: str) -> None:
        normalized = str(resume_id or "").strip()
        if not _SAFE_RESUME_ID.fullmatch(normalized):
            raise ValueError("pdf_batch_resume_id must contain only letters, numbers, '.', '_' or '-'")
        self._directory = root / normalized

    def load(self, *, batch_index: int, pages: list[int]) -> MarkdownArtifact | None:
        path = self._batch_path(batch_index)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("status") != "succeeded" or payload.get("pages") != pages:
            return None
        artifact_payload = payload.get("artifact")
        if not isinstance(artifact_payload, dict):
            return None
        return MarkdownArtifact(
            markdown_text=str(artifact_payload.get("markdown_text") or ""),
            assets=[self._deserialize_asset(item) for item in artifact_payload.get("assets") or []],
            diagnostics=dict(artifact_payload.get("diagnostics") or {}),
        )

    def save(self, *, batch_index: int, pages: list[int], artifact: MarkdownArtifact) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._batch_path(batch_index)
        temporary_path = path.with_suffix(".json.tmp")
        payload = {
            "status": "succeeded",
            "pages": pages,
            "artifact": {
                "markdown_text": artifact.markdown_text,
                "assets": [self._serialize_asset(asset) for asset in artifact.assets],
                "diagnostics": artifact.diagnostics,
            },
        }
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def read_progress(self) -> dict[str, Any]:
        path = self._directory / "progress.json"
        if not path.exists():
            return {"status": "queued"}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "queued"}
        return payload if isinstance(payload, dict) else {"status": "queued"}

    def read_incremental_results(
        self,
        *,
        after_batch_index: int = 0,
        max_batches: int = 10,
    ) -> list[dict[str, Any]]:
        """Read completed batch text after a caller cursor.

        Batch checkpoints are written atomically before progress advances, so a
        returned batch is immediately consumable even while later batches are
        still parsing. Asset publication remains a final-result responsibility.
        """

        normalized_after = max(int(after_batch_index or 0), 0)
        normalized_limit = max(int(max_batches or 1), 1)
        progress = self.read_progress()
        total_batches = max(int(progress.get("total_batches") or 0), 0)
        run_is_final = str(progress.get("status") or "").lower() in {"succeeded", "failed"}
        results: list[dict[str, Any]] = []
        for path in sorted(self._directory.glob("batch-*.json"), key=self._batch_file_index):
            batch_index = self._batch_file_index(path)
            if batch_index <= normalized_after:
                continue
            payload = self._read_batch_payload(path)
            if not payload:
                continue
            artifact = payload.get("artifact")
            if not isinstance(artifact, dict):
                continue
            markdown = str(artifact.get("markdown_text") or "")
            assets = artifact.get("assets") if isinstance(artifact.get("assets"), list) else []
            is_last_batch = bool(total_batches and batch_index >= total_batches)
            results.append(
                {
                    "batch_index": batch_index,
                    "pages": list(payload.get("pages") or []),
                    "status": "succeeded",
                    "is_last_batch": is_last_batch,
                    "is_final": bool(run_is_final and is_last_batch),
                    "markdown": markdown,
                    "output_char_count": len(markdown),
                    "asset_count": len(assets),
                    "assets_pending": bool(assets),
                }
            )
            if len(results) >= normalized_limit:
                break
        return results

    def write_progress(self, **updates: Any) -> dict[str, Any]:
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {**self.read_progress(), **updates}
        path = self._directory / "progress.json"
        temporary_path = path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        temporary_path.replace(path)
        return payload

    def _batch_path(self, batch_index: int) -> Path:
        return self._directory / f"batch-{batch_index}.json"

    @staticmethod
    def _batch_file_index(path: Path) -> int:
        try:
            return int(path.stem.removeprefix("batch-"))
        except ValueError:
            return 0

    @staticmethod
    def _read_batch_payload(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("status") != "succeeded":
            return {}
        return payload

    @staticmethod
    def _serialize_asset(asset: DocumentAsset) -> dict[str, Any]:
        return {
            "asset_id": asset.asset_id,
            "kind": asset.kind,
            "remote_id": asset.remote_id,
            "local_path": str(asset.local_path) if asset.local_path else "",
            "page_number": asset.page_number,
            "order": asset.order,
            "anchor": {
                "page_number": asset.anchor.page_number,
                "top": asset.anchor.top,
                "left": asset.anchor.left,
                "before_snippets": asset.anchor.before_snippets,
                "after_snippets": asset.anchor.after_snippets,
            },
            "meta": asset.meta,
        }

    @staticmethod
    def _deserialize_asset(payload: dict[str, Any]) -> DocumentAsset:
        anchor_payload = payload.get("anchor") if isinstance(payload.get("anchor"), dict) else {}
        local_path = str(payload.get("local_path") or "")
        return DocumentAsset(
            asset_id=str(payload.get("asset_id") or ""),
            kind=payload.get("kind") or "embedded_image",
            remote_id=str(payload.get("remote_id") or ""),
            local_path=Path(local_path) if local_path else None,
            page_number=int(payload.get("page_number") or 0),
            order=int(payload.get("order") or 0),
            anchor=DocumentAnchor(
                page_number=int(anchor_payload.get("page_number") or 0),
                top=int(anchor_payload.get("top") or 0),
                left=int(anchor_payload.get("left") or 0),
                before_snippets=list(anchor_payload.get("before_snippets") or []),
                after_snippets=list(anchor_payload.get("after_snippets") or []),
            ),
            meta=dict(payload.get("meta") or {}),
        )
