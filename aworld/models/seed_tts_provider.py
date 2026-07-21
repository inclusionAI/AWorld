# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Seed TTS Provider — Text-to-Speech via MatrixCube genericCall (seed-tts-2.0).

Uses the unidirectional SSE method ``/api/v3/tts/unidirectional/sse``.
Same gateway as ``doubao_tts`` (api_key / base_url), but a different request
schema and streaming response protocol.

Configuration (aworld.json ``audio`` block example)::

    {
      "api_key": "<same as doubao_tts>",
      "model": "seed-tts-2.0",
      "base_url": "https://matrixcube.alipay.com",
      "provider": "seed_tts",
      "temperature": 0.1
    }

Request shape (gateway wraps vendor API)::

    {
      "model": "seed-tts-2.0",
      "method": "/api/v3/tts/unidirectional/sse",
      "user": {"uid": "12345"},
      "req_params": {
        "text": "...",
        "speaker": "zh_female_vv_uranus_bigtts",
        "audio_params": {"format": "mp3", "sample_rate": 24000}
      }
    }

SSE events (``data:{...}``):
  - ``code == 0`` and ``data`` is a base64 string → audio chunk
  - ``code == 0`` and ``data`` is null → sentence metadata (ignore for bytes)
  - ``code == 20000000`` → stream finished (``message`` OK, optional ``usage``)
"""

import base64
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from aworld.core.llm_provider import LLMProviderBase
from aworld.logs.util import logger
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import ModelResponse, LLMResponseError

PROVIDER_NAME = "seed_tts"
DEFAULT_API_MODEL = "seed-tts-2.0"
TTS_METHOD = "/api/v3/tts/unidirectional/sse"
GENERIC_CALL_ENDPOINT = "/v1/genericCall"
# Stream end / success marker from seed-tts-2.0 SSE.
SSE_DONE_CODE = 20000000
SSE_CHUNK_OK = 0


class SeedTTSProvider(LLMProviderBase):
    """Seed TTS 2.0 provider (MatrixCube genericCall + unidirectional SSE)."""

    DEFAULT_VOICE_TYPE = "zh_female_vv_uranus_bigtts"
    DEFAULT_ENCODING = "mp3"
    DEFAULT_SAMPLE_RATE = 24000
    DEFAULT_SPEED_RATIO = 1.0
    SUPPORTED_ENCODINGS = ["mp3", "wav", "pcm", "ogg_opus"]

    def _init_provider(self) -> LLMHTTPHandler:
        api_key = self.api_key or os.getenv("SEED_TTS_API_KEY") or os.getenv(
            "DOUBAO_TTS_API_KEY", ""
        )
        if not api_key:
            raise ValueError(
                "Seed TTS API key not found. Set SEED_TTS_API_KEY / DOUBAO_TTS_API_KEY "
                "or pass api_key to the constructor."
            )

        base_url = self.base_url or os.getenv("SEED_TTS_BASE_URL") or os.getenv(
            "DOUBAO_TTS_BASE_URL", ""
        )
        if not base_url:
            raise ValueError(
                "Seed TTS base URL not found. Set SEED_TTS_BASE_URL / DOUBAO_TTS_BASE_URL "
                "or pass base_url to the constructor."
            )

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

        return LLMHTTPHandler(
            base_url=self.base_url,
            api_key=api_key,
            model_name=self.model_name or DEFAULT_API_MODEL,
            timeout=self.kwargs.get("timeout", 120),
            max_retries=self.kwargs.get("max_retries", 3),
        )

    def _init_async_provider(self) -> LLMHTTPHandler:
        return self.provider if self.need_sync else self._init_provider()

    @classmethod
    def supported_models(cls) -> list:
        return [DEFAULT_API_MODEL, PROVIDER_NAME, "seed-tts"]

    def _api_model_name(self) -> str:
        name = (self.model_name or "").strip()
        if name and name not in (PROVIDER_NAME, "seed-tts"):
            return name
        return DEFAULT_API_MODEL

    def _build_payload(
        self,
        text: str,
        speaker: str,
        encoding: str,
        sample_rate: int,
        uid: str,
        **extra: Any,
    ) -> Dict[str, Any]:
        audio_params: Dict[str, Any] = {
            "format": encoding,
            "sample_rate": sample_rate,
        }
        # Forward optional audio_params overrides from caller / params.
        for key in ("bitrate", "channel", "speech_rate"):
            if key in extra and extra[key] is not None:
                audio_params[key] = extra[key]

        req_params: Dict[str, Any] = {
            "text": text,
            "speaker": speaker,
            "audio_params": audio_params,
        }
        for key in ("emotion", "context_texts", "additions"):
            if key in extra and extra[key] is not None:
                req_params[key] = extra[key]

        return {
            "model": self._api_model_name(),
            "method": TTS_METHOD,
            "user": {"uid": uid},
            "req_params": req_params,
        }

    def text_to_speech(
        self,
        text: str,
        voice_type: Optional[str] = None,
        encoding: Optional[str] = None,
        speed_ratio: Optional[float] = None,
        uid: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs,
    ) -> ModelResponse:
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Set 'sync_enabled=True' in the constructor."
            )
        if not text:
            raise ValueError("Text parameter is required and cannot be empty")

        speaker = kwargs.pop("speaker", None) or voice_type or self.DEFAULT_VOICE_TYPE
        encoding = encoding or self.DEFAULT_ENCODING
        sample_rate = int(
            kwargs.pop("sample_rate", None)
            or (self.kwargs.get("params") or {}).get("sample_rate")
            or self.DEFAULT_SAMPLE_RATE
        )
        uid = uid or f"uid_{uuid.uuid4().hex[:8]}"

        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding '{encoding}'. "
                f"Supported encodings: {', '.join(self.SUPPORTED_ENCODINGS)}"
            )
        if speed_ratio is not None and speed_ratio != self.DEFAULT_SPEED_RATIO:
            logger.debug(
                f"[SeedTTSProvider] speed_ratio={speed_ratio} is accepted for "
                "AudioAgent compatibility but not mapped in seed-tts-2.0 req_params."
            )

        payload = self._build_payload(
            text=text,
            speaker=speaker,
            encoding=encoding,
            sample_rate=sample_rate,
            uid=uid,
            **kwargs,
        )

        logger.info(
            f"[SeedTTSProvider] Synthesizing: text_len={len(text)}, "
            f"speaker={speaker}, encoding={encoding}, sample_rate={sample_rate}"
        )

        try:
            # Do not use sync_stream_call — it injects data["stream"]=True.
            chunks = self.provider._make_request(  # noqa: SLF001 — intentional SSE
                GENERIC_CALL_ENDPOINT,
                payload,
                stream=True,
            )
            return self._parse_sse_chunks(
                chunks,
                encoding=encoding,
                output_path=output_path,
            )
        except Exception as e:
            error_msg = f"Seed TTS request failed: {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, self._api_model_name(), None)

    async def atext_to_speech(
        self,
        text: str,
        voice_type: Optional[str] = None,
        encoding: Optional[str] = None,
        speed_ratio: Optional[float] = None,
        uid: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs,
    ) -> ModelResponse:
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Set 'async_enabled=True' in the constructor."
            )
        if not text:
            raise ValueError("Text parameter is required and cannot be empty")

        speaker = kwargs.pop("speaker", None) or voice_type or self.DEFAULT_VOICE_TYPE
        encoding = encoding or self.DEFAULT_ENCODING
        sample_rate = int(
            kwargs.pop("sample_rate", None)
            or (self.kwargs.get("params") or {}).get("sample_rate")
            or self.DEFAULT_SAMPLE_RATE
        )
        uid = uid or f"uid_{uuid.uuid4().hex[:8]}"

        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding '{encoding}'. "
                f"Supported encodings: {', '.join(self.SUPPORTED_ENCODINGS)}"
            )
        if speed_ratio is not None and speed_ratio != self.DEFAULT_SPEED_RATIO:
            logger.debug(
                f"[SeedTTSProvider] speed_ratio={speed_ratio} is accepted for "
                "AudioAgent compatibility but not mapped in seed-tts-2.0 req_params."
            )

        payload = self._build_payload(
            text=text,
            speaker=speaker,
            encoding=encoding,
            sample_rate=sample_rate,
            uid=uid,
            **kwargs,
        )

        logger.info(
            f"[SeedTTSProvider] Synthesizing (async): text_len={len(text)}, "
            f"speaker={speaker}, encoding={encoding}"
        )

        try:
            chunks = []
            async for chunk in self.async_provider._make_async_request_stream(  # noqa: SLF001
                GENERIC_CALL_ENDPOINT,
                payload,
            ):
                chunks.append(chunk)
            return self._parse_sse_chunks(
                chunks,
                encoding=encoding,
                output_path=output_path,
            )
        except Exception as e:
            error_msg = f"Seed TTS request failed (async): {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, self._api_model_name(), None)

    def _parse_sse_chunks(
        self,
        chunks,
        encoding: str,
        output_path: Optional[str] = None,
    ) -> ModelResponse:
        """Aggregate SSE events into a single ModelResponse with concatenated audio."""
        audio_parts: List[bytes] = []
        usage: Dict[str, Any] = {}
        request_id: Optional[str] = None
        last_event: Optional[Dict[str, Any]] = None
        saw_done = False

        for event in chunks:
            if not isinstance(event, dict):
                continue
            # LLMHTTPHandler may emit control status frames.
            if event.get("status") in ("done", "fail", "cancel"):
                if event.get("status") == "fail":
                    raise LLMResponseError(
                        f"Seed TTS stream failed: {event.get('message')}",
                        self._api_model_name(),
                        event,
                    )
                continue

            last_event = event
            code = event.get("code")
            if event.get("request_id"):
                request_id = event["request_id"]

            if code == SSE_DONE_CODE:
                saw_done = True
                if isinstance(event.get("usage"), dict):
                    usage.update(event["usage"])
                continue

            if code != SSE_CHUNK_OK:
                error_msg = event.get("message") or "Unknown error"
                raise LLMResponseError(
                    f"Seed TTS API error (code {code}): {error_msg}",
                    self._api_model_name(),
                    event,
                )

            data = event.get("data")
            if not data:
                # Sentence / metadata frame with data=null
                continue
            if not isinstance(data, str):
                raise LLMResponseError(
                    f"Unexpected audio data type: {type(data).__name__}",
                    self._api_model_name(),
                    event,
                )
            try:
                audio_parts.append(base64.b64decode(data))
            except Exception as e:
                raise LLMResponseError(
                    f"Failed to decode audio chunk: {e}",
                    self._api_model_name(),
                    event,
                )

        if not audio_parts:
            codes = [
                e.get("code") if isinstance(e, dict) else type(e).__name__
                for e in chunks
            ]
            raise LLMResponseError(
                "No audio data in Seed TTS SSE response "
                f"(events={len(codes)}, codes={codes}). "
                "If only code=20000000 appears, SSE audio lines were likely dropped "
                "while parsing the stream.",
                self._api_model_name(),
                last_event,
            )

        if not saw_done:
            logger.warning(
                "[SeedTTSProvider] Stream ended without code=20000000; "
                "using collected audio chunks anyway."
            )

        audio_bytes = b"".join(audio_parts)
        logger.info(
            f"[SeedTTSProvider] OK: bytes={len(audio_bytes)}, "
            f"chunks={len(audio_parts)}, usage={usage}"
        )

        if output_path:
            try:
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(audio_bytes)
                logger.info(f"[SeedTTSProvider] Saved: {output_path}")
            except Exception as e:
                logger.warning(
                    f"[SeedTTSProvider] Failed to save audio to {output_path}: {e}"
                )

        response_id = request_id or f"tts-{uuid.uuid4().hex[:8]}"
        usage_out = {
            "audio_bytes": len(audio_bytes),
            "duration_ms": 0,
            "first_pkg_ms": 0,
            **usage,
        }
        response = ModelResponse(
            id=response_id,
            model=self._api_model_name(),
            content="",
            usage=usage_out,
            finish_reason="success",
            raw_response=last_event,
        )
        response.audio_data = audio_bytes
        response.audio_encoding = encoding
        response.audio_duration_ms = 0
        response.output_path = output_path
        return response

    def completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        context: Any = None,
        **kwargs,
    ) -> ModelResponse:
        raise NotImplementedError(
            "SeedTTSProvider does not support completion(). "
            "Use text_to_speech() instead."
        )

    def postprocess_response(self, response: Any) -> ModelResponse:
        raise NotImplementedError(
            "SeedTTSProvider uses custom processing in text_to_speech()."
        )
