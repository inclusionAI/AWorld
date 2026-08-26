"""Generic OCR HTTP adapter backed by the file server VLM config."""

from __future__ import annotations

import argparse
import base64
import io
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from aiohttp import web
from PIL import Image

from .filex_config import DEFAULT_FILEX_CONFIG_PATH, load_filex_config
from .media_transcription.gateway_vlm_service import GatewayVlmService

logger = logging.getLogger(__name__)

DEFAULT_OCR_PROMPT = (
    "请识别图片中所有可见文字、公式和表格内容，按从上到下、从左到右的阅读顺序输出。"
    "只输出识别到的正文内容，不要解释，不要总结。"
)
_PADDLE_LOCATION_TOKEN_PATTERN = re.compile(r"<\|LOC_\d+\|>")

class OcrServer:
    """Expose a small OCR HTTP service returning text boxes."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if config is not None and config_path is not None:
            raise ValueError("config and config_path cannot be used together")
        self._config = config if config is not None else load_filex_config(config_path)
        self._vlm_service = GatewayVlmService(
            config=self._config,
            env_content=_build_ocr_vlm_env_content(self._config),
        )

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def handle_ocr(self, request: web.Request) -> web.Response:
        try:
            upload = await self._read_upload(request)
            width, height = self._read_image_size(upload["content"])
            text = await self._recognize_text(upload)
            results = self._text_to_ocr_results(text, width=width, height=height)
            logger.info(
                "OCR finished | filename=%s width=%s height=%s result_count=%s",
                upload["filename"],
                width,
                height,
                len(results),
            )
            return web.json_response({"results": results})
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("OCR failed")
            return web.json_response({"detail": f"OCR failed: {exc}"}, status=502)

    async def handle_figure_locate(self, request: web.Request) -> web.Response:
        return web.json_response({"regions": []})

    async def _read_upload(self, request: web.Request) -> dict[str, Any]:
        reader = await request.multipart()
        file_field = None
        language = "zh"
        async for field in reader:
            if field.name == "language":
                language = (await field.text()).strip() or language
                continue
            if field.name != "file":
                continue
            content = await field.read(decode=False)
            file_field = {
                "filename": field.filename or "image.png",
                "content_type": field.headers.get("Content-Type") or "image/png",
                "content": content,
            }
        if not file_field:
            raise web.HTTPBadRequest(text='{"detail":"missing file upload"}', content_type="application/json")
        if not str(file_field["content_type"]).startswith("image/"):
            raise web.HTTPBadRequest(
                text='{"detail":"OCR adapter only accepts image uploads"}',
                content_type="application/json",
            )
        file_field["language"] = language
        return file_field

    @staticmethod
    def _read_image_size(content: bytes) -> tuple[int, int]:
        with Image.open(io.BytesIO(content)) as image:
            return int(image.width), int(image.height)

    async def _recognize_text(self, upload: dict[str, Any]) -> str:
        filename = str(upload["filename"])
        content_type = str(upload["content_type"])
        data_url = self._to_data_url(upload["content"], content_type, filename)
        options = self._build_vlm_options(
            data_url=data_url,
            language=str(upload.get("language") or "zh"),
            filename=filename,
        )
        result = await self._vlm_service.transcribe(
            Path(filename),
            media_type="image",
            file_type=Path(filename).suffix.lower().lstrip(".") or "png",
            source_file_name=filename,
            options=options,
        )
        return self._normalize_ocr_text(result.text)

    @staticmethod
    def _normalize_ocr_text(text: str) -> str:
        normalized = _PADDLE_LOCATION_TOKEN_PATTERN.sub("", str(text or ""))
        return "\n".join(line.strip() for line in normalized.splitlines() if line.strip())

    @staticmethod
    def _to_data_url(content: bytes, content_type: str, filename: str) -> str:
        mime_type = content_type or mimetypes.guess_type(filename)[0] or "image/png"
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _build_vlm_options(self, *, data_url: str, language: str, filename: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "backend": "openai_compatible",
            "media_url": data_url,
            "media_content_type": "image_url",
            "prompt": self._build_prompt(language=language, filename=filename),
            "timeout_seconds": _int_env("FILEX_OCR_TIMEOUT_SECONDS", 300),
        }
        return self._vlm_service.build_options(options)

    @staticmethod
    def _build_prompt(*, language: str, filename: str) -> str:
        language_hint = "中文为主" if language.lower() in {"zh", "ch", "chi_sim", "ch_sim"} else language
        return (
            f"{DEFAULT_OCR_PROMPT}\n"
            f"文件名：{filename}\n"
            f"识别语言提示：{language_hint}\n"
            "请尽量保留换行；每一行输出一个自然文本行。"
        )

    @staticmethod
    def _text_to_ocr_results(text: str, *, width: int, height: int) -> list[dict[str, Any]]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines and text.strip():
            lines = [text.strip()]
        if not lines:
            return []

        bounded_width = max(1, int(width or 1000))
        bounded_height = max(1, int(height or 1000))
        line_height = max(12, bounded_height // max(len(lines), 1))
        results: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            top = min(bounded_height - 1, index * line_height)
            bottom = min(bounded_height, max(top + 1, top + line_height))
            results.append({"text": line, "bbox": [0, top, bounded_width, bottom], "confidence": 0.5})
        return results

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=_int_env("FILEX_OCR_MAX_UPLOAD_BYTES", 20 * 1024 * 1024))
        app.router.add_get("/health", self.handle_health)
        app.router.add_post("/ocr", self.handle_ocr)
        app.router.add_post("/figure-locate", self.handle_figure_locate)
        return app


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _build_ocr_vlm_env_content(config: dict[str, Any]) -> dict[str, Any]:
    """Prefer the Paddle OCR-VL endpoint for the LiteParse OCR adapter."""

    document_parse = config.get("document_parse") or {}
    pdf_config = document_parse.get("pdf") if isinstance(document_parse, dict) else {}
    if not isinstance(pdf_config, dict):
        return {}

    paddle_gateway_config = {
        target_key: pdf_config.get(source_key)
        for target_key, source_key in {
            "base_url": "paddle_ocr_vl_rec_server_url",
            "model_name": "paddle_ocr_vl_rec_api_model_name",
            "api_key": "paddle_ocr_vl_rec_api_key",
        }.items()
        if pdf_config.get(source_key)
    }
    return {"gateway_vllm": paddle_gateway_config} if paddle_gateway_config else {}


def build_app(*, config_path: Path | None = None) -> web.Application:
    return OcrServer(config_path=config_path).create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the filex OCR adapter.")
    parser.add_argument("--host", default=os.getenv("FILEX_OCR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_int_env("FILEX_OCR_PORT", 18081))
    parser.add_argument(
        "--config-file",
        default=os.getenv("FILEX_CONFIG_PATH", str(DEFAULT_FILEX_CONFIG_PATH)),
        help="Path to the shared FileX YAML configuration.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("FILEX_OCR_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    web.run_app(
        build_app(config_path=Path(args.config_file).expanduser()),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
