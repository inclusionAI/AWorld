# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Doubao TTS Provider - Text-to-Speech provider for Doubao (豆包) TTS API.

This provider implements text-to-speech functionality using the Doubao TTS API.
It supports various voice types, audio encoding formats, and speech speed control.

Example usage:
    from aworld.models.doubao_tts_provider import DoubaoTTSProvider
    
    provider = DoubaoTTSProvider(
        api_key="your_api_key",
        base_url="https://your-api-endpoint.com"
    )
    
    response = provider.text_to_speech(
        text="字节跳动语音合成",
        voice_type="zh_male_M392_conversation_wvae_bigtts",
        encoding="mp3",
        speed_ratio=1.0
    )
    
    # Save audio to file
    with open("output.mp3", "wb") as f:
        f.write(response.audio_data)
"""

import base64
import json
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from aworld.core.llm_provider import LLMProviderBase
from aworld.logs.util import logger
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import ModelResponse, LLMResponseError


class DoubaoTTSProvider(LLMProviderBase):
    """Doubao TTS (Text-to-Speech) provider implementation.
    
    This provider interfaces with the Doubao TTS API to convert text into speech.
    It supports multiple voice types, audio formats, and speech speed control.
    
    Attributes:
        DEFAULT_VOICE_TYPE: Default voice type for speech synthesis
        DEFAULT_ENCODING: Default audio encoding format
        DEFAULT_SPEED_RATIO: Default speech speed ratio
        SUPPORTED_ENCODINGS: List of supported audio encoding formats
    """
    
    DEFAULT_VOICE_TYPE = "zh_male_M392_conversation_wvae_bigtts"
    DEFAULT_ENCODING = "mp3"
    DEFAULT_SPEED_RATIO = 1.0
    SUPPORTED_ENCODINGS = ["mp3", "wav", "pcm", "ogg_opus"]
    
    def _init_provider(self) -> LLMHTTPHandler:
        """Initialize Doubao TTS provider with HTTP handler.
        
        Returns:
            LLMHTTPHandler: Configured HTTP handler for API requests
            
        Raises:
            ValueError: If API key or base URL is not provided
        """
        api_key = self.api_key or os.getenv("DOUBAO_TTS_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Doubao TTS API key not found. Set the DOUBAO_TTS_API_KEY "
                "environment variable or pass api_key to the constructor."
            )
        
        base_url = self.base_url or os.getenv("DOUBAO_TTS_BASE_URL", "")
        if not base_url:
            raise ValueError(
                "Doubao TTS base URL not found. Set the DOUBAO_TTS_BASE_URL "
                "environment variable or pass base_url to the constructor."
            )
        
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        
        return LLMHTTPHandler(
            base_url=self.base_url,
            api_key=api_key,
            model_name=self.model_name or "doubao_tts",
            timeout=self.kwargs.get("timeout", 60),
            max_retries=self.kwargs.get("max_retries", 3),
        )
    
    def _init_async_provider(self) -> LLMHTTPHandler:
        """Initialize async provider (reuses sync provider).
        
        Returns:
            LLMHTTPHandler: The same HTTP handler used for sync operations
        """
        return self.provider if self.need_sync else self._init_provider()
    
    @classmethod
    def supported_models(cls) -> list:
        """Get list of supported TTS models.
        
        Returns:
            list: List of supported model names
        """
        return ["doubao_tts"]
    
    def text_to_speech(
        self,
        text: str,
        voice_type: Optional[str] = None,
        encoding: Optional[str] = None,
        speed_ratio: Optional[float] = None,
        uid: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> ModelResponse:
        """Convert text to speech using Doubao TTS API (synchronous).
        
        Args:
            text: Text content to synthesize into speech
            voice_type: Voice type identifier (default: zh_male_M392_conversation_wvae_bigtts)
            encoding: Audio encoding format (mp3, wav, pcm, ogg_opus)
            speed_ratio: Speech speed ratio (0.5 to 2.0, default: 1.0)
            uid: User ID for the request (auto-generated if not provided)
            output_path: Optional path to save the audio file
            **kwargs: Additional parameters for the API request
            
        Returns:
            ModelResponse: Response containing audio data and metadata
            
        Raises:
            LLMResponseError: If the API request fails
            ValueError: If invalid parameters are provided
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Set 'sync_enabled=True' in the constructor."
            )
        
        if not text:
            raise ValueError("Text parameter is required and cannot be empty")
        
        # Set default values
        voice_type = voice_type or self.DEFAULT_VOICE_TYPE
        encoding = encoding or self.DEFAULT_ENCODING
        speed_ratio = speed_ratio if speed_ratio is not None else self.DEFAULT_SPEED_RATIO
        uid = uid or f"uid_{uuid.uuid4().hex[:8]}"
        
        # Validate encoding
        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding '{encoding}'. "
                f"Supported encodings: {', '.join(self.SUPPORTED_ENCODINGS)}"
            )
        
        # Validate speed ratio
        if not (0.5 <= speed_ratio <= 2.0):
            logger.warning(
                f"Speed ratio {speed_ratio} is outside recommended range [0.5, 2.0]. "
                "Using anyway, but results may be unexpected."
            )
        
        # Build request payload
        reqid = kwargs.get("reqid", str(uuid.uuid4()))
        payload = {
            "model": "doubao_tts",
            "method": "/api/v1/tts",
            "app": {
                "token": "111",  # Required non-empty string (no actual meaning)
                "cluster": "volcano_tts"  # Must be "volcano_tts"
            },
            "user": {
                "uid": uid
            },
            "audio": {
                "voice_type": voice_type,
                "encoding": encoding,
                "speed_ratio": speed_ratio
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "operation": "query"
            }
        }
        
        logger.info(
            f"[DoubaoTTSProvider] Synthesizing speech: "
            f"text_length={len(text)}, voice_type={voice_type}, "
            f"encoding={encoding}, speed_ratio={speed_ratio}"
        )
        
        try:
            # Make API request
            response_data = self.provider.sync_call(payload, endpoint="/v1/genericCall")
            
            # Parse response
            return self._parse_tts_response(
                response_data, 
                encoding=encoding,
                output_path=output_path
            )
            
        except Exception as e:
            error_msg = f"Doubao TTS request failed: {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, "doubao_tts", None)
    
    async def atext_to_speech(
        self,
        text: str,
        voice_type: Optional[str] = None,
        encoding: Optional[str] = None,
        speed_ratio: Optional[float] = None,
        uid: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> ModelResponse:
        """Convert text to speech using Doubao TTS API (asynchronous).
        
        Args:
            text: Text content to synthesize into speech
            voice_type: Voice type identifier
            encoding: Audio encoding format
            speed_ratio: Speech speed ratio
            uid: User ID for the request
            output_path: Optional path to save the audio file
            **kwargs: Additional parameters
            
        Returns:
            ModelResponse: Response containing audio data and metadata
            
        Raises:
            LLMResponseError: If the API request fails
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Set 'async_enabled=True' in the constructor."
            )
        
        if not text:
            raise ValueError("Text parameter is required and cannot be empty")
        
        # Set default values
        voice_type = voice_type or self.DEFAULT_VOICE_TYPE
        encoding = encoding or self.DEFAULT_ENCODING
        speed_ratio = speed_ratio if speed_ratio is not None else self.DEFAULT_SPEED_RATIO
        uid = uid or f"uid_{uuid.uuid4().hex[:8]}"
        
        # Validate encoding
        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding '{encoding}'. "
                f"Supported encodings: {', '.join(self.SUPPORTED_ENCODINGS)}"
            )
        
        # Build request payload
        reqid = kwargs.get("reqid", str(uuid.uuid4()))
        payload = {
            "model": "doubao_tts",
            "method": "/api/v1/tts",
            "app": {
                "token": "111",
                "cluster": "volcano_tts"
            },
            "user": {
                "uid": uid
            },
            "audio": {
                "voice_type": voice_type,
                "encoding": encoding,
                "speed_ratio": speed_ratio
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "operation": "query"
            }
        }
        
        logger.info(
            f"[DoubaoTTSProvider] Synthesizing speech (async): "
            f"text_length={len(text)}, voice_type={voice_type}, "
            f"encoding={encoding}, speed_ratio={speed_ratio}"
        )
        
        try:
            # Make async API request
            response_data = await self.async_provider.async_call(
                payload, 
                endpoint="/v1/genericCall"
            )
            
            # Parse response
            return self._parse_tts_response(
                response_data,
                encoding=encoding,
                output_path=output_path
            )
            
        except Exception as e:
            error_msg = f"Doubao TTS request failed (async): {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, "doubao_tts", None)
    
    def _parse_tts_response(
        self,
        response_data: Dict[str, Any],
        encoding: str,
        output_path: Optional[str] = None
    ) -> ModelResponse:
        """Parse TTS API response and extract audio data.
        
        Args:
            response_data: Raw API response data
            encoding: Audio encoding format
            output_path: Optional path to save the audio file
            
        Returns:
            ModelResponse: Parsed response with audio data
            
        Raises:
            LLMResponseError: If response parsing fails or API returns error
        """
        # Check for API errors
        code = response_data.get("code")
        if code != 3000:
            error_msg = response_data.get("message", "Unknown error")
            logger.error(
                f"[DoubaoTTSProvider] API error: code={code}, message={error_msg}"
            )
            raise LLMResponseError(
                f"Doubao TTS API error (code {code}): {error_msg}",
                "doubao_tts",
                response_data
            )
        
        # Extract audio data (base64 encoded)
        audio_base64 = response_data.get("data")
        if not audio_base64:
            raise LLMResponseError(
                "No audio data in response",
                "doubao_tts",
                response_data
            )
        
        # Decode base64 audio data
        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as e:
            raise LLMResponseError(
                f"Failed to decode audio data: {e}",
                "doubao_tts",
                response_data
            )
        
        # Extract metadata
        addition = response_data.get("addition", {})
        duration_ms = addition.get("duration", "0")
        first_pkg_ms = addition.get("first_pkg", "0")
        
        logger.info(
            f"[DoubaoTTSProvider] Speech synthesis successful: "
            f"audio_size={len(audio_bytes)} bytes, "
            f"duration={duration_ms}ms, "
            f"first_package={first_pkg_ms}ms"
        )
        
        # Save to file if output_path is provided
        if output_path:
            try:
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(audio_bytes)
                logger.info(f"[DoubaoTTSProvider] Audio saved to: {output_path}")
            except Exception as e:
                logger.warning(
                    f"[DoubaoTTSProvider] Failed to save audio to {output_path}: {e}"
                )
        
        # Generate a unique ID for this response
        # Use request_id from response if available, otherwise generate UUID
        response_id = response_data.get("request_id") or response_data.get("reqid") or f"tts-{uuid.uuid4().hex[:8]}"
        
        # Build ModelResponse
        response = ModelResponse(
            id=response_id,  # Required: unique identifier for this response
            model="doubao_tts",
            content="",  # TTS doesn't have text content
            usage={
                "audio_bytes": len(audio_bytes),
                "duration_ms": int(duration_ms),
                "first_pkg_ms": int(first_pkg_ms)
            },
            finish_reason="success",
            raw_response=response_data
        )
        
        # Attach audio data as custom attribute
        response.audio_data = audio_bytes
        response.audio_encoding = encoding
        response.audio_duration_ms = int(duration_ms)
        response.output_path = output_path
        
        return response
    
    # -------------------------------------------------------------------------
    # Abstract method implementations (required by LLMProviderBase)
    # These methods are not used for TTS, but must be implemented
    # -------------------------------------------------------------------------
    
    def completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        context: Any = None,
        **kwargs
    ) -> ModelResponse:
        """Not implemented for TTS provider.
        
        DoubaoTTSProvider is a text-to-speech provider and does not support
        text completion. Use text_to_speech() method instead.
        
        Raises:
            NotImplementedError: Always raised as this method is not applicable
        """
        raise NotImplementedError(
            "DoubaoTTSProvider is a TTS provider and does not support completion(). "
            "Use text_to_speech() method instead."
        )
    
    def postprocess_response(self, response: Any) -> ModelResponse:
        """Not implemented for TTS provider.
        
        DoubaoTTSProvider uses custom response processing in text_to_speech()
        and atext_to_speech() methods.
        
        Raises:
            NotImplementedError: Always raised as this method is not applicable
        """
        raise NotImplementedError(
            "DoubaoTTSProvider uses custom response processing. "
            "This method is not used."
        )


# New canonical public name without the vendor-specific prefix.
SpeechProvider = DoubaoTTSProvider
