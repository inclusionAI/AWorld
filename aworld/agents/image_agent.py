# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Image Agent - Agent for image generation.

This agent provides a simple interface for generating images from text prompts using
the Qwen Image provider. It handles the entire workflow from prompt input to
image file generation.

Example usage:
    from aworld.agents.image_agent import ImageAgent
    from aworld.config.conf import AgentConfig
    
    agent = ImageAgent(
        name="image_gen",
        conf=AgentConfig(
            llm_provider="qwen_image",
            llm_api_key="YOUR_API_KEY",
            llm_base_url="https://antchat.alipay.com"
        ),
        default_size="1024x1024",
        default_output_format="png",
        output_dir="./image_output"
    )
    
    # Use the agent
    from aworld.core.common import Observation
    
    obs = Observation(
        content="A beautiful sunset over mountains",
        info={
            "size": "1024x768",
            "output_format": "jpeg",
            "negative_prompt": "blurry, low quality"
        }
    )
    
    result = await agent.async_policy(obs)
"""

import os
import traceback
import uuid
from typing import Any, Dict, List, Optional

from aworld.agents.llm_agent import LLMAgent
from aworld.core.agent.base import AgentResult
from aworld.core.common import ActionModel, Observation, Config
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants
from aworld.events.util import send_message
from aworld.logs.util import logger
from aworld.models.image_provider import ImageProvider
from aworld.models.kling_image_provider import KlingImageProvider
from aworld.models.model_response import ModelResponse
from aworld.output.base import Output


class ImageAgent(LLMAgent):
    """An agent dedicated to image generation.
    
    Each invocation is a single-round call: the agent takes a text prompt,
    generates an image, saves it to a file, and terminates.
    No tool-calling loop is entered.
    
    The image prompt is taken from ``Observation.content``.
    Additional image parameters can be supplied via ``Observation.info``
    (keys: ``size``, ``output_format``, ``negative_prompt``, ``seed``,
    ``response_format``, ``output_compression``, ``user``, ``output_path``).
    When using image-edit models, the current stable input form is a remote
    image URL passed via ``image_url``/``image_urls`` (or compatible aliases).
    Instance-level defaults are used as fallbacks.
    
    Attributes:
        default_size: Default image size (e.g., "1024x1024")
        default_output_format: Default output format (png, jpeg, webp)
        default_response_format: Default response format (b64_json, url)
        default_negative_prompt: Default negative prompt
        default_seed: Default random seed
        output_dir: Default directory for saving image files
        auto_filename: Whether to auto-generate filenames
    """
    
    @staticmethod
    def _ensure_image_config(conf):
        """Ensure the config uses a supported image backend.
        
        ``llm_provider`` is set to ``image`` or ``kling_image``. Any other
        value is replaced with ``image`` and a warning is logged.
        
        Args:
            conf: Input configuration (AgentConfig, dict, or ConfigDict)
            
        Returns:
            A new config object with ``llm_provider`` set to ``image`` or ``kling_image``
            
        Raises:
            ValueError: If conf is None
        """
        from aworld.config.conf import AgentConfig, ModelConfig
        
        if conf is None:
            raise ValueError(
                "conf must be provided. Pass an AgentConfig with llm_provider, "
                "llm_api_key, and llm_base_url."
            )
        
        # Check if provider needs to be overridden
        original_provider = None
        if isinstance(conf, AgentConfig):
            original_provider = conf.llm_config.llm_provider
        elif hasattr(conf, 'llm_provider'):
            original_provider = conf.llm_provider
        elif isinstance(conf, dict):
            original_provider = conf.get('llm_provider')
        
        allowed = ("image", "kling_image")
        target_provider = "image"
        if original_provider in allowed:
            target_provider = original_provider
        elif original_provider:
            logger.warning(
                f"ImageAgent: Overriding llm_provider from '{original_provider}' "
                f"to 'image'. Use 'image' or 'kling_image' for image backends."
            )

        # Create a new AgentConfig with resolved image provider
        if isinstance(conf, AgentConfig):
            # For AgentConfig, we need to modify llm_config
            # Get the llm_config dict
            llm_config_dict = conf.llm_config.model_dump(exclude_none=True)
            llm_config_dict['llm_provider'] = target_provider
            
            # Create new ModelConfig
            new_llm_config = ModelConfig(**llm_config_dict)
            
            # Create new AgentConfig with the modified llm_config
            conf_dict = conf.model_dump(exclude_none=True)
            conf_dict['llm_config'] = new_llm_config
            
            return AgentConfig(**conf_dict)
        elif isinstance(conf, dict):
            # Modify dict directly
            conf['llm_provider'] = target_provider
            return conf
        else:
            # For other types (ConfigDict, etc.), try to handle gracefully
            logger.warning(
                f"ImageAgent: Unexpected config type {type(conf).__name__}. "
                f"Attempting to proceed anyway."
            )
            return conf
    
    def __init__(
        self,
        name: str,
        conf: Config | None = None,
        desc: str = None,
        agent_id: str = None,
        *,
        # Image generation defaults (overridable per-call via Observation.info)
        default_size: Optional[str] = None,
        default_output_format: str = "png",
        default_response_format: str = "b64_json",
        default_negative_prompt: Optional[str] = None,
        default_seed: Optional[int] = None,
        output_dir: Optional[str] = None,
        auto_filename: bool = True,
        **kwargs,
    ):
        """Initialize ImageAgent.
        
        Args:
            name: Agent name
            conf: AgentConfig specifying the image provider, API key, and base URL.
                Must not be None. ``llm_provider`` is normalized to ``image`` or
                ``kling_image`` (other values become ``image`` with a warning).
            desc: Agent description exposed as tool description
            agent_id: Explicit agent ID; auto-generated if None
            default_size: Default image size (e.g., "1024x1024", "1024x768", "768x1024")
            default_output_format: Default output format (png, jpeg, webp)
            default_response_format: Default response format (b64_json, url)
            default_negative_prompt: Default negative prompt to exclude from generation
            default_seed: Default random seed for reproducible generation
            output_dir: Directory to save generated image files.
                Defaults to current working directory.
            auto_filename: Whether to auto-generate filenames based on timestamp
                and UUID. If False, output_path must be provided per call.
            **kwargs: Forwarded to ``LLMAgent.__init__``
            
        Raises:
            ValueError: If conf is None or invalid
            TypeError: If the provider is not ImageProvider after initialization
        """
        # Validate and ensure image config
        conf = self._ensure_image_config(conf)
        
        super().__init__(
            name=name,
            conf=conf,
            desc=desc or "Image generation agent",
            agent_id=agent_id,
            **kwargs,
        )
        
        # Verify that the provider is a supported image backend
        if self.llm and self.llm.provider:
            if not isinstance(self.llm.provider, (ImageProvider, KlingImageProvider)):
                error_msg = (
                    f"[ImageAgent:{self.id()}] Expected ImageProvider or KlingImageProvider, "
                    f"but got {type(self.llm.provider).__name__}. "
                    f"Set llm_provider to 'image' or 'kling_image'. "
                    f"Provider initialization may have failed; check provider registry and API config."
                )
                logger.error(error_msg)
                raise TypeError(error_msg)
        else:
            error_msg = (
                f"[ImageAgent:{self.id()}] Provider initialization failed. "
                f"self.llm or self.llm.provider is None. "
                f"Please check your configuration (api_key, base_url)."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        self.default_size = default_size or "1024x1024"
        self.default_output_format = default_output_format
        self.default_response_format = default_response_format
        self.default_negative_prompt = default_negative_prompt
        self.default_seed = default_seed
        self.output_dir = output_dir or os.getcwd()
        self.auto_filename = auto_filename

    @staticmethod
    def _env_or_default(env_key: str, default: Optional[str] = None) -> Optional[str]:
        value = os.environ.get(env_key)
        if value is None:
            return default
        value = value.strip()
        return value or default

    def _resolve_provider_runtime_config(
        self,
        provider: Any,
        *,
        has_input_images: bool,
    ) -> Dict[str, Any]:
        prefix = "IMAGE_TO_IMAGE" if has_input_images else "TEXT_TO_IMAGE"
        legacy_prefix = "IMAGE" if not has_input_images else None
        default_llm_provider = "kling_image" if isinstance(provider, KlingImageProvider) else "image"

        def pick(name: str, fallback: Optional[str]) -> Optional[str]:
            value = self._env_or_default(f"{prefix}_{name}")
            if value is None and legacy_prefix:
                value = self._env_or_default(f"{legacy_prefix}_{name}")
            return value if value is not None else fallback

        config = {
            "api_key": pick("API_KEY", provider.api_key),
            "base_url": pick("BASE_URL", provider.base_url),
            "model_name": pick("MODEL_NAME", provider.model_name),
            "provider": pick("PROVIDER", None) or default_llm_provider,
        }
        temp_value = pick("TEMPERATURE", None)
        if temp_value is not None:
            try:
                config["temperature"] = float(temp_value)
            except ValueError:
                pass
        return config

    def _apply_provider_runtime_config(
        self,
        provider: Any,
        runtime_config: Dict[str, Any],
    ) -> None:
        provider.api_key = runtime_config.get("api_key") or provider.api_key
        provider.base_url = runtime_config.get("base_url") or provider.base_url
        provider.model_name = runtime_config.get("model_name") or provider.model_name
        temperature = runtime_config.get("temperature")
        if temperature is not None:
            provider.kwargs["temperature"] = temperature

        if provider.need_sync:
            provider.provider = provider._init_provider()
        else:
            provider.provider = None

        if provider.need_async:
            provider.async_provider = provider.provider if provider.need_sync else provider._init_async_provider()
        else:
            provider.async_provider = None

    @staticmethod
    def _collect_input_images(
        observation: Observation,
        message: Optional[Message],
        obs_info: Dict[str, Any],
    ) -> List[str]:
        """Collect image inputs from multiple aliases and transport layers."""
        images: List[str] = []

        def add(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    images.append(normalized)
                return
            if isinstance(value, list):
                for item in value:
                    add(item)

        for key in (
            "image",
            "image_url",
            "image_urls",
            "images",
            "input_image",
            "input_images",
            "reference_images",
        ):
            add(obs_info.pop(key, None))

        add(getattr(observation, "image", None))
        add(getattr(observation, "images", None))

        if message and isinstance(message.headers, dict):
            add(message.headers.get("image_urls"))

        deduped: List[str] = []
        seen = set()
        for item in images:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped
    
    # ------------------------------------------------------------------
    # Core policy — single-round image generation
    # ------------------------------------------------------------------
    
    async def async_policy(
        self,
        observation: Observation,
        info: Dict[str, Any] = {},
        message: Message = None,
        **kwargs,
    ) -> List[ActionModel]:
        """Single-round image generation policy.
        
        Extracts the prompt and image parameters from the observation, calls
        the image generation provider, saves the image file, and returns the result as an
        ActionModel. The agent is marked as finished immediately so no
        further loop iterations occur.
        
        Args:
            observation: Contains the image prompt in ``content`` and
                optional overrides in ``info`` (size, output_format, negative_prompt,
                seed, response_format, output_compression, user, output_path).
            info: Supplementary information dict (merged with
                ``observation.info`` if both are non-empty).
            message: Incoming event message carrying context.
            **kwargs: Additional parameters (unused by this agent).
        
        Returns:
            A single-element list with an ActionModel whose ``policy_info``
            contains the image generation result dict, or an error description
            string on failure.
        """
        self.context = message.context if message else None
        self._finished = False
        
        # Merge observation.info and caller-supplied info
        obs_info: Dict[str, Any] = dict(observation.info or {})
        obs_info.update(info or {})
        
        prompt = observation.content or ""
        if not prompt:
            error_msg = "Empty prompt provided; image generation requires non-empty prompt."
            logger.warning(f"[ImageAgent:{self.id()}] {error_msg}")
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info=error_msg)]
        
        # Resolve image parameters (observation.info overrides instance defaults)
        size: str = obs_info.pop("size", self.default_size)
        output_format: str = obs_info.pop("output_format", self.default_output_format)
        response_format: str = obs_info.pop("response_format", self.default_response_format)
        negative_prompt: Optional[str] = obs_info.pop("negative_prompt", self.default_negative_prompt)
        seed: Optional[int] = obs_info.pop("seed", self.default_seed)
        output_compression: Optional[int] = obs_info.pop("output_compression", None)
        user: Optional[str] = obs_info.pop("user", None)
        input_images: List[str] = self._collect_input_images(observation, message, obs_info)
        
        # Determine output path
        output_path: Optional[str] = obs_info.pop("output_path", None)
        if not output_path and self.auto_filename:
            # Auto-generate filename
            timestamp = uuid.uuid4().hex[:8]
            filename = f"image_{timestamp}.{output_format}"
            output_path = os.path.join(self.output_dir, filename)
        
        if not output_path:
            error_msg = (
                "No output_path provided and auto_filename is disabled. "
                "Please provide output_path in observation.info or enable auto_filename."
            )
            logger.error(f"[ImageAgent:{self.id()}] {error_msg}")
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info=error_msg)]
        
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path) or self.output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        logger.info(
            f"[ImageAgent:{self.id()}] Generating image: "
            f"prompt_length={len(prompt)}, size={size}, "
            f"output_format={output_format}, response_format={response_format}, "
            f"output_path={output_path}"
        )
        
        image_response: Optional[ModelResponse] = None
        try:
            image_response = await self._invoke_image_generation(
                prompt=prompt,
                size=size,
                output_format=output_format,
                response_format=response_format,
                negative_prompt=negative_prompt,
                seed=seed,
                output_compression=output_compression,
                user=user,
                output_path=output_path,
                context=message.context if message else None,
                image_urls=input_images,
                **obs_info,  # Forward any remaining parameters
            )
            logger.info(f"[ImageAgent:{self.id()}] Image generation response: {image_response}")
        except Exception as exc:
            error_msg = f"Image generation failed: {exc}"
            logger.error(
                f"[ImageAgent:{self.id()}] {error_msg}\n{traceback.format_exc()}"
            )
            if message:
                await send_message(
                    Message(
                        category=Constants.OUTPUT,
                        payload=Output(data=error_msg),
                        sender=self.id(),
                        session_id=message.context.session_id if message.context else "",
                        headers={"context": message.context},
                    )
                )
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info=error_msg)]
        
        # Build result payload
        result_payload: Dict[str, Any] = {
            "status": "success",
            "output_path": output_path,
            "prompt": prompt,
            "size": size,
            "output_format": output_format,
        }
        
        if image_response:
            # Add image metadata to payload
            result_payload.update({
                "image_size_bytes": getattr(image_response, "image_bytes", 0),
                "image_format": getattr(image_response, "image_format", output_format),
                "usage": image_response.usage,
            })
            
            # If response contains URL instead of local file
            image_url = getattr(image_response, "image_url", None)
            if image_url:
                result_payload["image_url"] = image_url
                # Download if URL format
                if response_format == "url" and image_url:
                    local_path = await self._download_image(image_url, output_path)
                    if local_path:
                        result_payload["local_path"] = local_path
            
            logger.info(
                f"[ImageAgent:{self.id()}] Image generation successful: "
                f"output_path={output_path}, "
                f"size={result_payload['image_size_bytes']} bytes"
            )
        else:
            logger.warning(f"[ImageAgent:{self.id()}] Unexpected response: {image_response}")
        
        if message:
            await LLMAgent.send_agent_response_output(
                self, image_response, message.context, kwargs.get("outputs")
            )
        
        self._finished = True
        # Set params to mark this as tool result
        params = {"is_tool_result": True}
        policy_result = [ActionModel(agent_name=self.id(), policy_info=result_payload, params=params)]
        logger.info(f"[ImageAgent:{self.id()}] Agent result: {result_payload} {policy_result}")
        return policy_result
    
    async def _invoke_image_generation(
        self,
        prompt: str,
        size: str,
        output_format: str,
        response_format: str,
        negative_prompt: Optional[str],
        seed: Optional[int],
        output_compression: Optional[int],
        user: Optional[str],
        output_path: str,
        context: Context = None,
        **extra_kwargs,
    ) -> ModelResponse:
        """Call the underlying image provider.
        
        Runs the image generation call in the default thread-pool executor
        so it does not block the event loop.
        
        Args:
            prompt: Text prompt for image generation
            size: Image size (e.g., "1024x1024")
            output_format: Output format (png, jpeg, webp)
            response_format: Response format (b64_json, url)
            negative_prompt: Negative prompt to exclude from generation
            seed: Random seed for reproducible generation
            output_compression: Compression factor for jpeg/webp
            user: User identifier
            output_path: Path to save the image file
            context: Runtime context (unused by the provider, passed through)
            **extra_kwargs: Additional provider-specific parameters
        
        Returns:
            ModelResponse with image data and metadata
        
        Raises:
            Any exception raised by the provider
        """
        import asyncio
        
        provider = self.llm.provider
        
        # Verify provider type
        if not isinstance(provider, (ImageProvider, KlingImageProvider)):
            raise TypeError(
                f"ImageAgent requires ImageProvider or KlingImageProvider, "
                f"but got {type(provider).__name__}. "
                f"Set conf.llm_provider to 'image' or 'kling_image'."
            )
        
        # Check if provider has the required methods
        if not hasattr(provider, "generate_image") and not hasattr(provider, "agenerate_image"):
            raise AttributeError(
                f"Provider {type(provider).__name__} does not have "
                f"generate_image or agenerate_image methods."
            )
        
        loop = asyncio.get_event_loop()
        runtime_config = self._resolve_provider_runtime_config(
            provider,
            has_input_images=bool(extra_kwargs.get("image_urls")),
        )
        self._apply_provider_runtime_config(provider, runtime_config)
        logger.info(
            f"[ImageAgent:{self.id()}] Using {'image_to_image' if extra_kwargs.get('image_urls') else 'text_to_image'} "
            f"model={provider.model_name} base_url={provider.base_url}"
        )
        
        # Check if provider has async method
        if hasattr(provider, "agenerate_image"):
            return await provider.agenerate_image(
                prompt=prompt,
                size=size,
                output_format=output_format,
                response_format=response_format,
                negative_prompt=negative_prompt,
                seed=seed,
                output_compression=output_compression,
                user=user,
                output_path=output_path,
                **extra_kwargs,
            )
        else:
            # Fall back to sync method in executor
            return await loop.run_in_executor(
                None,
                lambda: provider.generate_image(
                    prompt=prompt,
                    size=size,
                    output_format=output_format,
                    response_format=response_format,
                    negative_prompt=negative_prompt,
                    seed=seed,
                    output_compression=output_compression,
                    user=user,
                    output_path=output_path,
                    **extra_kwargs,
                ),
            )
    
    async def _download_image(
        self,
        image_url: str,
        output_path: str,
    ) -> Optional[str]:
        """Download an image from *image_url* and save to *output_path*.
        
        Tries ``aiohttp`` first; falls back to ``urllib`` so the method works
        without optional dependencies.
        
        Args:
            image_url: Remote URL of the image file.
            output_path: Local path to save the file.
        
        Returns:
            Absolute path of the saved file, or ``None`` on failure.
        """
        import asyncio
        
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        logger.info(f"[ImageAgent:{self.id()}] Downloading image from {image_url} to {output_path} ...")
        
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._download_image_sync(image_url, output_path),
            )
            logger.info(f"[ImageAgent:{self.id()}] Image saved to {output_path}")
            return output_path
        except Exception as exc:
            logger.warning(
                f"[ImageAgent:{self.id()}] Failed to download image from {image_url}: {exc}\n"
                f"{traceback.format_exc()}"
            )
            return None
    
    @staticmethod
    def _download_image_sync(image_url: str, local_path: str) -> None:
        """Blocking download implementation used inside a thread-pool executor.
        
        Tries ``requests`` first, then falls back to ``urllib`` so it works
        without optional dependencies.
        
        Args:
            image_url: Remote URL of the image file.
            local_path: Absolute local path to write the file.
        """
        try:
            import requests  # type: ignore
            resp = requests.get(image_url, stream=True, timeout=300)
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        except ImportError:
            import urllib.request
            urllib.request.urlretrieve(image_url, local_path)
    
    # ------------------------------------------------------------------
    # Override is_agent_finished — always finish after one round
    # ------------------------------------------------------------------
    
    def is_agent_finished(self, llm_response: ModelResponse, agent_result: AgentResult) -> bool:
        """ImageAgent always finishes after a single round."""
        self._finished = True
        return True
