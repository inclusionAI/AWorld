# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Volcano OpenSpeech TTS — direct HTTP access to ByteDance OpenSpeech TTS (non-proxy).

Uses the official V1 non-streaming endpoint and Bearer;token auth as required by the API.
See: https://openspeech.bytedance.com/api/v1/tts

Configuration (aworld.json ``audio`` block example)::

    {
      "provider": "volcano_openspeech_tts",
      "model": "volcano_openspeech_tts",
      "api_key": "<access_token from Volcengine console>",
      "base_url": "https://openspeech.bytedance.com",
      "appid": "<appid from console>",
      "params": { "cluster": "volcano_tts" }
    }

- ``api_key``: maps to the console **access_token** (used in ``Authorization: Bearer;<token>``
  and in JSON ``app.token``).
- ``base_url``: host only (``https://openspeech.bytedance.com``) or the full TTS URL; both work.
- ``model`` (``llm_model_name``): logical name for AWorld (e.g. ``volcano_openspeech_tts``).
  To use the **seed-tts-1.1** API field, set ``params.tts_request_model`` to ``seed-tts-1.1``
  or set ``llm_model_name`` to ``seed-tts-1.1``.
"""

import base64
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aworld.core.llm_provider import LLMProviderBase
from aworld.logs.util import logger
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import ModelResponse, LLMResponseError

DEFAULT_OPENSPEECH_HOST = "https://openspeech.bytedance.com"
TTS_API_SUFFIX = "api/v1/tts"
DEFAULT_CLUSTER = "volcano_tts"
PROVIDER_MODEL_TAG = "volcano_openspeech_tts"


class VolcanoOpenSpeechTTSProvider(LLMProviderBase):
    """OpenSpeech HTTP TTS (direct), V1 non-streaming JSON API."""

    DEFAULT_VOICE_TYPE = "zh_male_liufei_uranus_bigtts"
    DEFAULT_ENCODING = "mp3"
    DEFAULT_SPEED_RATIO = 1.0
    SUPPORTED_ENCODINGS = ["mp3", "wav", "pcm", "ogg_opus"]

    def _init_provider(self) -> LLMHTTPHandler:
        api_key = self.api_key or os.getenv("VOLCANO_TTS_ACCESS_TOKEN") or os.getenv(
            "DOUBAO_TTS_API_KEY", ""
        )
        if not api_key:
            raise ValueError(
                "Volcano OpenSpeech access token not found. Set llm_api_key / api_key "
                "(Volcengine access_token), or VOLCANO_TTS_ACCESS_TOKEN."
            )

        params = self.kwargs.get("params") or {}
        self.appid = (
            self.kwargs.get("appid")
            or params.get("appid")
            or os.getenv("VOLCANO_TTS_APPID", "")
        )
        if not self.appid:
            raise ValueError(
                "Volcano OpenSpeech requires appid. Set appid in ModelConfig.params, "
                "ext_config, or environment variable VOLCANO_TTS_APPID."
            )

        self.cluster = (
            params.get("cluster")
            or self.kwargs.get("cluster")
            or DEFAULT_CLUSTER
        )

        base_url = self.base_url or os.getenv("VOLCANO_TTS_BASE_URL") or DEFAULT_OPENSPEECH_HOST
        self._http_base, self._tts_endpoint = _normalize_tts_url(base_url)

        self.api_key = api_key
        self.base_url = self._http_base

        return LLMHTTPHandler(
            base_url=self._http_base,
            api_key=api_key,
            model_name=self.model_name or PROVIDER_MODEL_TAG,
            timeout=self.kwargs.get("timeout", 60),
            max_retries=self.kwargs.get("max_retries", 3),
        )

    def _init_async_provider(self) -> LLMHTTPHandler:
        return self.provider if self.need_sync else self._init_provider()

    @classmethod
    def supported_models(cls) -> list:
        return [PROVIDER_MODEL_TAG, "seed-tts-1.1"]

    def _auth_headers(self) -> Dict[str, str]:
        # Official format: "Authorization": "Bearer;${token}" (semicolon, not space)
        return {"Authorization": f"Bearer;{self.api_key}"}

    def _request_model_for_api(self) -> Optional[str]:
        """Optional ``request.model`` (e.g. seed-tts-1.1)."""
        params = self.kwargs.get("params") or {}
        explicit = params.get("tts_request_model") or params.get("request_model")
        if explicit:
            return explicit
        name = self.model_name or ""
        if name == "seed-tts-1.1":
            return "seed-tts-1.1"
        return None

    def _build_payload(
        self,
        text: str,
        voice_type: str,
        encoding: str,
        speed_ratio: float,
        uid: str,
        reqid: str,
        **extra: Any,
    ) -> Dict[str, Any]:
        request_block: Dict[str, Any] = {
            "reqid": reqid,
            "text": text,
            "operation": "query",
        }
        rm = self._request_model_for_api()
        if rm:
            request_block["model"] = rm

        audio: Dict[str, Any] = {
            "voice_type": voice_type,
            "encoding": encoding,
            "speed_ratio": speed_ratio,
        }
        # Forward known optional audio / request keys from extra (caller / params)
        for key in (
            "emotion",
            "enable_emotion",
            "emotion_scale",
            "rate",
            "bitrate",
            "explicit_language",
            "context_language",
            "loudness_ratio",
        ):
            if key in extra and extra[key] is not None:
                audio[key] = extra[key]
        for key in (
            "text_type",
            "silence_duration",
            "with_timestamp",
            "extra_param",
            "disable_markdown_filter",
        ):
            if key in extra and extra[key] is not None:
                request_block[key] = extra[key]

        return {
            "app": {
                "appid": self.appid,
                "token": self.api_key,
                "cluster": self.cluster,
            },
            "user": {"uid": uid},
            "audio": audio,
            "request": request_block,
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

        voice_type = voice_type or self.DEFAULT_VOICE_TYPE
        encoding = encoding or self.DEFAULT_ENCODING
        speed_ratio = speed_ratio if speed_ratio is not None else self.DEFAULT_SPEED_RATIO
        uid = uid or f"uid_{uuid.uuid4().hex[:8]}"

        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding '{encoding}'. "
                f"Supported encodings: {', '.join(self.SUPPORTED_ENCODINGS)}"
            )

        reqid = kwargs.get("reqid", str(uuid.uuid4()))
        payload = self._build_payload(
            text=text,
            voice_type=voice_type,
            encoding=encoding,
            speed_ratio=speed_ratio,
            uid=uid,
            reqid=reqid,
            **kwargs,
        )

        logger.info(
            f"[VolcanoOpenSpeechTTSProvider] Synthesizing: text_len={len(text)}, "
            f"voice_type={voice_type}, encoding={encoding}, speed_ratio={speed_ratio}"
        )

        try:
            response_data = self.provider.sync_call(
                payload,
                endpoint=self._tts_endpoint,
                headers=self._auth_headers(),
            )
            return self._parse_tts_response(
                response_data,
                encoding=encoding,
                output_path=output_path,
            )
        except Exception as e:
            error_msg = f"Volcano OpenSpeech TTS request failed: {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, PROVIDER_MODEL_TAG, None)

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

        voice_type = voice_type or self.DEFAULT_VOICE_TYPE
        encoding = encoding or self.DEFAULT_ENCODING
        speed_ratio = speed_ratio if speed_ratio is not None else self.DEFAULT_SPEED_RATIO
        uid = uid or f"uid_{uuid.uuid4().hex[:8]}"

        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding '{encoding}'. "
                f"Supported encodings: {', '.join(self.SUPPORTED_ENCODINGS)}"
            )

        reqid = kwargs.get("reqid", str(uuid.uuid4()))
        payload = self._build_payload(
            text=text,
            voice_type=voice_type,
            encoding=encoding,
            speed_ratio=speed_ratio,
            uid=uid,
            reqid=reqid,
            **kwargs,
        )

        logger.info(
            f"[VolcanoOpenSpeechTTSProvider] Synthesizing (async): text_len={len(text)}, "
            f"voice_type={voice_type}, encoding={encoding}"
        )

        try:
            response_data = await self.async_provider.async_call(
                payload,
                endpoint=self._tts_endpoint,
                headers=self._auth_headers(),
            )
            return self._parse_tts_response(
                response_data,
                encoding=encoding,
                output_path=output_path,
            )
        except Exception as e:
            error_msg = f"Volcano OpenSpeech TTS request failed (async): {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, PROVIDER_MODEL_TAG, None)

    def _parse_tts_response(
        self,
        response_data: Dict[str, Any],
        encoding: str,
        output_path: Optional[str] = None,
    ) -> ModelResponse:
        code = response_data.get("code")
        if code != 3000:
            error_msg = response_data.get("message", "Unknown error")
            logger.error(
                f"[VolcanoOpenSpeechTTSProvider] API error: code={code}, message={error_msg}"
            )
            raise LLMResponseError(
                f"Volcano OpenSpeech TTS API error (code {code}): {error_msg}",
                PROVIDER_MODEL_TAG,
                response_data,
            )

        audio_base64 = response_data.get("data")
        if not audio_base64:
            raise LLMResponseError(
                "No audio data in response",
                PROVIDER_MODEL_TAG,
                response_data,
            )

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as e:
            raise LLMResponseError(
                f"Failed to decode audio data: {e}",
                PROVIDER_MODEL_TAG,
                response_data,
            )

        addition = response_data.get("addition") or {}
        duration_ms = addition.get("duration", "0")
        first_pkg_ms = addition.get("first_pkg", "0")

        logger.info(
            f"[VolcanoOpenSpeechTTSProvider] OK: bytes={len(audio_bytes)}, "
            f"duration_ms={duration_ms}"
        )

        if output_path:
            try:
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(audio_bytes)
                logger.info(f"[VolcanoOpenSpeechTTSProvider] Saved: {output_path}")
            except Exception as e:
                logger.warning(
                    f"[VolcanoOpenSpeechTTSProvider] Failed to save audio to {output_path}: {e}"
                )

        response_id = (
            response_data.get("request_id")
            or response_data.get("reqid")
            or f"tts-{uuid.uuid4().hex[:8]}"
        )

        dur_i = _safe_int_ms(duration_ms)
        fp_i = _safe_int_ms(first_pkg_ms)
        response = ModelResponse(
            id=response_id,
            model=self.model_name or PROVIDER_MODEL_TAG,
            content="",
            usage={
                "audio_bytes": len(audio_bytes),
                "duration_ms": dur_i,
                "first_pkg_ms": fp_i,
            },
            finish_reason="success",
            raw_response=response_data,
        )
        response.audio_data = audio_bytes
        response.audio_encoding = encoding
        response.audio_duration_ms = dur_i
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
            "VolcanoOpenSpeechTTSProvider does not support completion(). "
            "Use text_to_speech() instead."
        )

    def postprocess_response(self, response: Any) -> ModelResponse:
        raise NotImplementedError(
            "VolcanoOpenSpeechTTSProvider uses custom processing in text_to_speech()."
        )


def _normalize_tts_url(base_url: str) -> Tuple[str, str]:
    """Return (origin for LLMHTTPHandler, endpoint path for POST)."""
    raw = base_url.strip().rstrip("/")
    marker = "/api/v1/tts"
    if raw.endswith(marker):
        origin = raw[: -len(marker)].rstrip("/") or DEFAULT_OPENSPEECH_HOST
        return origin, TTS_API_SUFFIX
    return raw, TTS_API_SUFFIX


def _safe_int_ms(val: Any) -> int:
    if val is None:
        return 0
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0
