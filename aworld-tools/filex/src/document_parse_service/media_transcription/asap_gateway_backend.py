"""ASAP Gateway backend for image OCR/understanding."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .models import TranscriptResult

_AFTS_FILE_ID_PATTERN = re.compile(r"A[*_][A-Za-z0-9]+")


class AsapGatewayBackend:
    """Call ASAP Gateway `/api/files/understand` for image OCR and understanding."""

    name = "asap_gateway"

    async def transcribe(
        self,
        file_path: Path,
        *,
        media_type: str,
        file_type: str,
        source_file_name: str,
        options: dict[str, Any],
    ) -> TranscriptResult:
        if media_type != "image":
            raise ValueError("asap_gateway backend only supports image media")

        endpoint = self._resolve_endpoint(options)
        timeout_seconds = float(options.get("timeout_seconds") or options.get("timeout") or 180)
        raw_file_id = str(
            options.get("file_id")
            or options.get("fileId")
            or options.get("source_file_id")
            or ""
        ).strip()
        file_url = str(options.get("file_url") or options.get("fileUrl") or "").strip()
        if not file_url and self._is_http_url(raw_file_id):
            file_url = raw_file_id
        file_id = self._normalize_candidate_file_id(raw_file_id)
        if not file_id:
            file_id = self._extract_afts_file_id_from_url(file_url) or source_file_name
        session_id = str(options.get("session_id") or options.get("sessionId") or "").strip()
        trace_id = str(options.get("trace_id") or options.get("traceId") or "").strip()
        prompt = str(options.get("prompt") or options.get("understand_prompt") or "").strip()

        attachment = {
            "fileId": file_id,
            "fileType": "image",
            "fileFormat": file_type,
            "fileName": file_path.name,
            "type": "image",
        }
        if file_url:
            attachment["fileUrl"] = file_url

        payload = {
            "file_id": file_id,
            "file_type": file_type,
            "attachments": [attachment],
            "session_id": session_id,
            "trace_id": trace_id,
        }
        if prompt:
            payload["prompt"] = prompt

        headers = self._build_headers(options)

        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError("asap_gateway image backend requires aiohttp") from exc

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
            async with session.post(endpoint, headers=headers, json=payload) as response:
                raw = await response.text()
                if response.status >= 400:
                    raise RuntimeError(
                        f"asap_gateway image backend failed: "
                        f"status={response.status} response={raw[:1000]}"
                    )
                data = await response.json(content_type=None)

        content = self._extract_file_content(data)
        return TranscriptResult(
            text=content,
            backend=self.name,
            model="file-understand",
            metadata={
                "endpoint": endpoint,
                "file_id": file_id,
                "raw_file_id": raw_file_id,
                "file_url": file_url,
                "file_type": file_type,
                "source_file_name": source_file_name,
                "status": data.get("status"),
                "parse_result": (data.get("data") or {}).get("parse_result", {}),
            },
        )

    @staticmethod
    def _resolve_endpoint(options: dict[str, Any]) -> str:
        explicit_endpoint = str(
            options.get("endpoint")
            or options.get("asap_gateway_endpoint")
            or options.get("file_understand_endpoint")
            or options.get("ocr_endpoint")
            or ""
        ).strip()
        if explicit_endpoint:
            return explicit_endpoint
        base_url = str(
            options.get("base_url")
            or options.get("asap_gateway_base_url")
            or options.get("file_understand_base_url")
            or options.get("ocr_base_url")
            or os.getenv("ASAP_GATEWAY_BASE_URL")
            or os.getenv("FILE_UNDERSTAND_BASE_URL")
            or os.getenv("IMAGE_OCR_BASE_URL")
            or "http://127.0.0.1:8081"
        ).rstrip("/")
        return f"{base_url}/api/files/understand"

    @staticmethod
    def _is_http_url(value: str) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized.startswith("http://") or normalized.startswith("https://")

    @staticmethod
    def _extract_afts_file_id_from_url(value: str) -> str:
        if not AsapGatewayBackend._is_http_url(value):
            return ""
        parsed = urlparse(str(value or "").strip())
        path_parts = [unquote(part) for part in parsed.path.split("/") if part]
        for index, part in enumerate(path_parts):
            if part == "afts" and index + 2 < len(path_parts):
                kind = path_parts[index + 1]
                candidate = path_parts[index + 2]
                if kind in {"file", "img"} and candidate:
                    return candidate
        return ""

    @staticmethod
    def _extract_afts_file_id_from_text(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        match = _AFTS_FILE_ID_PATTERN.search(normalized)
        if match is None:
            return ""
        candidate = match.group(0)
        if candidate.startswith("A_"):
            return "A*" + candidate[2:]
        return candidate

    @staticmethod
    def _normalize_candidate_file_id(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        extracted_from_url = AsapGatewayBackend._extract_afts_file_id_from_url(normalized)
        if extracted_from_url:
            return extracted_from_url
        extracted_from_text = AsapGatewayBackend._extract_afts_file_id_from_text(normalized)
        if extracted_from_text:
            return extracted_from_text
        return normalized

    @staticmethod
    def _build_headers(options: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        extra_headers = options.get("headers") or {}
        if extra_headers:
            if not isinstance(extra_headers, dict):
                raise ValueError("media_parse_options.headers must be an object")
            headers.update({str(key): str(value) for key, value in extra_headers.items()})

        if not any(key.lower() == "authorization" for key in headers):
            authorization = AsapGatewayBackend._resolve_authorization(options)
            if authorization:
                headers["Authorization"] = authorization
        return headers

    @staticmethod
    def _resolve_authorization(options: dict[str, Any]) -> str:
        explicit_authorization = str(
            options.get("authorization")
            or options.get("auth_header")
            or options.get("authHeader")
            or os.getenv("ASAP_GATEWAY_AUTHORIZATION")
            or os.getenv("FILE_UNDERSTAND_AUTHORIZATION")
            or os.getenv("GATEWAY_AUTHORIZATION")
            or ""
        ).strip()
        if explicit_authorization:
            return explicit_authorization

        auth_token = str(
            options.get("auth_token")
            or options.get("authToken")
            or options.get("bearer_token")
            or options.get("asap_gateway_auth_token")
            or options.get("gateway_auth_token")
            or os.getenv("ASAP_GATEWAY_AUTH_TOKEN")
            or os.getenv("FILE_UNDERSTAND_AUTH_TOKEN")
            or os.getenv("GATEWAY_AUTH_TOKEN")
            or os.getenv("MEDIA_PARSE_AUTH_TOKEN")
            or ""
        ).strip()
        if not auth_token:
            return ""
        if auth_token.lower().startswith("bearer "):
            return auth_token
        return f"Bearer {auth_token}"

    @staticmethod
    def _extract_file_content(data: dict[str, Any]) -> str:
        if "data" in data and isinstance(data["data"], dict):
            content = data["data"].get("file_content")
            if content:
                return str(content).strip()
        content = data.get("file_content")
        if content:
            return str(content).strip()
        return ""
