# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Image Provider - Generic image generation provider.

This provider implements image generation functionality using image generation APIs
(e.g., Qwen-Image-2512-Lightning from Alipay Theta platform).

Example usage:
    from aworld.models.image_provider import ImageProvider
    
    provider = ImageProvider(
        api_key="your_api_key",
        base_url="https://antchat.alipay.com"
    )
    
    # Generate image synchronously
    response = provider.generate_image(
        prompt="A beautiful sunset over mountains",
        size="1024x1024",
        output_format="png"
    )
    
    # Save image to file
    with open("output.png", "wb") as f:
        f.write(response.image_data)
    
    # Generate image asynchronously
    response = await provider.agenerate_image(
        prompt="A cute cat playing with a ball",
        size="1024x1024"
    )
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


class ImageProvider(LLMProviderBase):
    """Image generation provider implementation.
    
    This provider interfaces with image generation APIs to generate images from text prompts.
    
    Attributes:
        DEFAULT_SIZE: Default image size (1024x1024)
        DEFAULT_RESPONSE_FORMAT: Default response format (b64_json)
        DEFAULT_OUTPUT_FORMAT: Default output format (png)
        SUPPORTED_SIZES: List of supported image sizes
        SUPPORTED_RESPONSE_FORMATS: List of supported response formats
        SUPPORTED_OUTPUT_FORMATS: List of supported output formats
    """
    
    DEFAULT_SIZE = "1024x1024"
    DEFAULT_RESPONSE_FORMAT = "b64_json"
    DEFAULT_OUTPUT_FORMAT = "png"
    DEFAULT_MODEL = "Qwen-Image"
    
    SUPPORTED_SIZES = ["1024x1024", "1024x768", "768x1024", "512x512"]
    SUPPORTED_RESPONSE_FORMATS = ["b64_json", "url"]
    SUPPORTED_OUTPUT_FORMATS = ["png", "jpeg", "webp"]
    
    def _init_provider(self) -> LLMHTTPHandler:
        """Initialize Image provider with HTTP handler.
        
        Returns:
            LLMHTTPHandler: Configured HTTP handler for API requests
            
        Raises:
            ValueError: If API key is not provided
        """
        api_key = self.api_key or os.getenv("IMAGE_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Image API key not found. Set the IMAGE_API_KEY "
                "environment variable or pass api_key to the constructor."
            )
        
        base_url = self.base_url or os.getenv("IMAGE_BASE_URL", "https://antchat.alipay.com")
        
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        
        return LLMHTTPHandler(
            base_url=self.base_url,
            api_key=api_key,
            model_name=self.model_name or self.DEFAULT_MODEL,
            timeout=self.kwargs.get("timeout", 120),
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
        """Get list of supported image generation models.
        
        Returns:
            list: List of supported model names
        """
        return [cls.DEFAULT_MODEL, "image"]
    
    def generate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        size: Optional[str] = None,
        response_format: Optional[str] = None,
        output_format: Optional[str] = None,
        output_compression: Optional[int] = None,
        seed: Optional[int] = None,
        user: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> ModelResponse:
        """Generate image using Qwen Image API (synchronous).
        
        Args:
            prompt: Image description text (required)
            negative_prompt: Negative description (optional)
            size: Output image size, format "wxh" (e.g., "1024x1024")
            response_format: "b64_json" (default) or "url"
            output_format: "png" (default), "jpeg", or "webp"
            output_compression: Compression factor for jpeg/webp (optional)
            seed: Random seed for reproducible generation (optional)
            user: User identifier (optional)
            output_path: Optional path to save the image file
            **kwargs: Additional parameters for the API request
            
        Returns:
            ModelResponse: Response containing image data and metadata
            
        Raises:
            LLMResponseError: If the API request fails
            ValueError: If invalid parameters are provided
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Set 'sync_enabled=True' in the constructor."
            )
        
        if not prompt:
            raise ValueError("Prompt parameter is required and cannot be empty")
        
        # Set default values
        size = size or self.DEFAULT_SIZE
        response_format = response_format or self.DEFAULT_RESPONSE_FORMAT
        output_format = output_format or self.DEFAULT_OUTPUT_FORMAT
        
        # Validate parameters
        if size not in self.SUPPORTED_SIZES:
            logger.warning(
                f"Size '{size}' may not be supported. "
                f"Supported sizes: {', '.join(self.SUPPORTED_SIZES)}"
            )
        
        if response_format not in self.SUPPORTED_RESPONSE_FORMATS:
            raise ValueError(
                f"Unsupported response_format '{response_format}'. "
                f"Supported formats: {', '.join(self.SUPPORTED_RESPONSE_FORMATS)}"
            )
        
        if output_format not in self.SUPPORTED_OUTPUT_FORMATS:
            raise ValueError(
                f"Unsupported output_format '{output_format}'. "
                f"Supported formats: {', '.join(self.SUPPORTED_OUTPUT_FORMATS)}"
            )
        
        # Build request payload
        payload = {
            "model": self.DEFAULT_MODEL,
            "prompt": prompt,
            "size": size,
            "response_format": response_format,
            "output_format": output_format,
        }
        
        # Add optional parameters
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if output_compression is not None:
            payload["output_compression"] = output_compression
        if seed is not None:
            payload["seed"] = seed
        if user:
            payload["user"] = user
        
        # Add any additional kwargs
        payload.update(kwargs)
        
        logger.info(
            f"[ImageProvider] Generating image: "
            f"prompt_length={len(prompt)}, size={size}, "
            f"response_format={response_format}, output_format={output_format}"
        )
        
        try:
            # Make API request
            response_data = self.provider.sync_call(
                payload, 
                endpoint="/v1/images/generations"
            )
            
            # Parse response
            return self._parse_image_response(
                response_data,
                output_format=output_format,
                output_path=output_path
            )
            
        except Exception as e:
            error_msg = f"Qwen Image generation failed: {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, "image", None)
    
    async def agenerate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        size: Optional[str] = None,
        response_format: Optional[str] = None,
        output_format: Optional[str] = None,
        output_compression: Optional[int] = None,
        seed: Optional[int] = None,
        user: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> ModelResponse:
        """Generate image using Qwen Image API (asynchronous).
        
        Args:
            prompt: Image description text (required)
            negative_prompt: Negative description (optional)
            size: Output image size, format "wxh" (e.g., "1024x1024")
            response_format: "b64_json" (default) or "url"
            output_format: "png" (default), "jpeg", or "webp"
            output_compression: Compression factor for jpeg/webp (optional)
            seed: Random seed for reproducible generation (optional)
            user: User identifier (optional)
            output_path: Optional path to save the image file
            **kwargs: Additional parameters for the API request
            
        Returns:
            ModelResponse: Response containing image data and metadata
            
        Raises:
            LLMResponseError: If the API request fails
            ValueError: If invalid parameters are provided
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Set 'async_enabled=True' in the constructor."
            )
        
        if not prompt:
            raise ValueError("Prompt parameter is required and cannot be empty")
        
        # Set default values
        size = size or self.DEFAULT_SIZE
        response_format = response_format or self.DEFAULT_RESPONSE_FORMAT
        output_format = output_format or self.DEFAULT_OUTPUT_FORMAT
        
        # Validate parameters
        if response_format not in self.SUPPORTED_RESPONSE_FORMATS:
            raise ValueError(
                f"Unsupported response_format '{response_format}'. "
                f"Supported formats: {', '.join(self.SUPPORTED_RESPONSE_FORMATS)}"
            )
        
        if output_format not in self.SUPPORTED_OUTPUT_FORMATS:
            raise ValueError(
                f"Unsupported output_format '{output_format}'. "
                f"Supported formats: {', '.join(self.SUPPORTED_OUTPUT_FORMATS)}"
            )
        
        # Build request payload
        payload = {
            "model": self.DEFAULT_MODEL,
            "prompt": prompt,
            "size": size,
            "response_format": response_format,
            "output_format": output_format,
        }
        
        # Add optional parameters
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if output_compression is not None:
            payload["output_compression"] = output_compression
        if seed is not None:
            payload["seed"] = seed
        if user:
            payload["user"] = user
        
        # Add any additional kwargs
        payload.update(kwargs)
        
        logger.info(
            f"[ImageProvider] Generating image (async): "
            f"prompt_length={len(prompt)}, size={size}, "
            f"response_format={response_format}, output_format={output_format}"
        )
        
        try:
            # Make async API request
            response_data = await self.async_provider.async_call(
                payload,
                endpoint="/v1/images/generations"
            )
            
            # Parse response
            return self._parse_image_response(
                response_data,
                output_format=output_format,
                output_path=output_path
            )
            
        except Exception as e:
            error_msg = f"Qwen Image generation failed (async): {e}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise LLMResponseError(error_msg, "image", None)
    
    def _parse_image_response(
        self,
        response_data: Dict[str, Any],
        output_format: str,
        output_path: Optional[str] = None
    ) -> ModelResponse:
        """Parse image generation API response and extract image data.
        
        Args:
            response_data: Raw API response data
            output_format: Image output format (png, jpeg, webp)
            output_path: Optional path to save the image file
            
        Returns:
            ModelResponse: Parsed response with image data
            
        Raises:
            LLMResponseError: If response parsing fails or API returns error
        """
        # Check for API errors
        if response_data.get("error"):
            error_info = response_data["error"]
            error_msg = error_info.get("message", "Unknown error")
            error_code = error_info.get("code", "unknown")
            logger.error(
                f"[ImageProvider] API error: code={error_code}, message={error_msg}"
            )
            raise LLMResponseError(
                f"Qwen Image API error (code {error_code}): {error_msg}",
                "image",
                response_data
            )
        
        # Extract image data from response
        # The API returns data in OpenAI-compatible format
        data_list = response_data.get("data", [])
        if not data_list:
            raise LLMResponseError(
                "No image data in response",
                "image",
                response_data
            )
        
        image_data = data_list[0]
        
        # Check if response contains base64 data or URL
        b64_data = image_data.get("b64_json")
        image_url = image_data.get("url")
        
        image_bytes = None
        
        if b64_data:
            # Decode base64 image data
            try:
                image_bytes = base64.b64decode(b64_data)
                logger.info(
                    f"[ImageProvider] Image generated successfully: "
                    f"size={len(image_bytes)} bytes"
                )
            except Exception as e:
                raise LLMResponseError(
                    f"Failed to decode image data: {e}",
                    "image",
                    response_data
                )
        elif image_url:
            # URL format - image_bytes will be None, but URL is available
            logger.info(
                f"[ImageProvider] Image generated successfully: url={image_url}"
            )
        else:
            raise LLMResponseError(
                "No image data (b64_json or url) in response",
                "image",
                response_data
            )
        
        # Save to file if output_path is provided
        if output_path and image_bytes:
            try:
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(image_bytes)
                logger.info(f"[ImageProvider] Image saved to: {output_path}")
            except Exception as e:
                logger.warning(
                    f"[ImageProvider] Failed to save image to {output_path}: {e}"
                )
        
        # Generate a unique ID for this response
        response_id = response_data.get("id") or f"img-{uuid.uuid4().hex[:8]}"
        
        # Build ModelResponse
        response = ModelResponse(
            id=response_id,
            model=response_data.get("model", "image"),
            content="",  # Image generation doesn't have text content
            usage={
                "image_size": image_data.get("size", "unknown"),
                "output_format": output_format,
            },
            finish_reason="success",
            raw_response=response_data
        )
        
        # Attach image data as custom attributes
        if image_bytes:
            response.image_data = image_bytes
            response.image_bytes = len(image_bytes)
        if image_url:
            response.image_url = image_url
        response.image_format = output_format
        response.output_path = output_path
        
        return response
    
    # -------------------------------------------------------------------------
    # Abstract method implementations (required by LLMProviderBase)
    # These methods are not used for image generation, but must be implemented
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
        """Not implemented for image generation provider.
        
        ImageProvider is an image generation provider and does not support
        text completion. Use generate_image() method instead.
        
        Raises:
            NotImplementedError: Always raised as this method is not applicable
        """
        raise NotImplementedError(
            "QwenImageProvider is an image generation provider and does not support completion(). "
            "Use generate_image() method instead."
        )
    
    def postprocess_response(self, response: Any) -> ModelResponse:
        """Not implemented for image generation provider.
        
        QwenImageProvider uses custom response processing in generate_image()
        and agenerate_image() methods.
        
        Raises:
            NotImplementedError: Always raised as this method is not applicable
        """
        raise NotImplementedError(
            "QwenImageProvider uses custom response processing. "
            "This method is not used."
        )
