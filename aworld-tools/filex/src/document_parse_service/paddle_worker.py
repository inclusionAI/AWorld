"""Persistent PaddleOCR worker used by the public FileX service.

The worker owns the native PaddleOCR-VL pipeline and processes one JSON-line
request at a time.  Keeping this process alive lets consecutive HTTP Jobs reuse
the same GPU model.  The supervising HTTP service may terminate the whole
worker process after an idle period or when a parse watchdog fires.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .paths import FS_WORKSPACE_ROOT
from .pdf.paddle_ocr_pdf_provider import PaddleOcrPdfProvider
from .service import DocumentParseService

_RESPONSE_PREFIX = "FILEX_PADDLE_RESPONSE\t"


def _paddle_env() -> dict[str, Any]:
    return {
        "filex_parse_provider": "paddle_ocr",
        "pdf_parse_provider": "paddle_ocr",
        "paddle_ocr_pipeline_version": "v1.6",
        "paddle_ocr_vl_rec_backend": "native",
        "paddle_ocr_vl_rec_max_concurrency": 1,
        "paddle_ocr_use_doc_orientation_classify": False,
        "paddle_ocr_use_doc_unwarping": False,
        "paddle_ocr_use_layout_detection": True,
        "paddle_ocr_use_chart_recognition": True,
        "paddle_ocr_format_block_content": True,
        "paddle_ocr_text_layer_formatting": True,
        "paddle_ocr_use_queues": False,
    }


async def _warmup() -> dict[str, Any]:
    """Load the model and execute one tiny real inference before readiness."""

    warmup_path = FS_WORKSPACE_ROOT / "filex-service" / "paddle-warmup.png"
    warmup_path.parent.mkdir(parents=True, exist_ok=True)
    if not warmup_path.is_file():
        image = Image.new("RGB", (256, 96), "white")
        ImageDraw.Draw(image).text((16, 32), "FileX OCR warmup 123", fill="black")
        image.save(warmup_path)

    provider = PaddleOcrPdfProvider(env_content=_paddle_env())

    def predict() -> int:
        pipeline = provider._resolve_pipeline()
        return sum(
            1
            for _ in pipeline.predict(
                str(warmup_path),
                **provider._predict_kwargs(),
            )
        )

    result_count = await asyncio.to_thread(predict)
    return {"success": True, "warm": True, "result_count": result_count}


async def _serve() -> None:
    service = DocumentParseService()
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        try:
            request = json.loads(line)
            operation = str(request.get("op") or "")
            if operation == "warmup":
                response = await _warmup()
            elif operation == "parse":
                response = await service.parse(
                    workspace_path=str(request["workspace_path"]),
                    task_id=str(request["task_id"]),
                    sync_mode="sync",
                    asset_reference_mode=str(
                        request.get("asset_reference_mode") or "local_path"
                    ),
                    env_content=dict(request.get("env_content") or {}),
                )
            else:
                raise ValueError(f"unsupported worker operation: {operation}")
            payload = {"ok": True, "result": response}
        except Exception as exc:  # keep the worker available after job errors
            logging.exception("Persistent PaddleOCR worker request failed")
            payload = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        sys.stdout.write(
            _RESPONSE_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n"
        )
        sys.stdout.flush()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
