# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Audio Agent - Agent for text-to-speech audio generation.

This agent provides a simple interface for converting text to speech using
the Doubao TTS provider. It handles the entire workflow from text input to
audio file generation.

Example usage:
    from aworld.agents.audio_agent import AudioAgent
    from aworld.config.conf import AgentConfig
    
    agent = AudioAgent(
        name="audio_gen",
        conf=AgentConfig(
            llm_provider="doubao_tts",
            llm_api_key="YOUR_API_KEY",
            llm_base_url="https://your-api-endpoint.com"
        ),
        default_voice_type="zh_male_M392_conversation_wvae_bigtts",
        default_encoding="mp3",
        output_dir="./audio_output"
    )
    
    # Use the agent
    from aworld.core.common import Observation
    
    obs = Observation(
        content="ByteDance text-to-speech",
        info={
            "voice_type": "zh_female_F001_conversation_wvae_bigtts",
            "speed_ratio": 1.2
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
from aworld.models.doubao_tts_provider import DoubaoTTSProvider
from aworld.models.model_response import ModelResponse
from aworld.output.base import Output


class AudioAgent(LLMAgent):
    """An agent dedicated to text-to-speech audio generation.
    
    Each invocation is a single-round call: the agent takes text input,
    generates speech audio, saves it to a file, and terminates.
    No tool-calling loop is entered.
    
    The text to synthesize is taken from ``Observation.content``.
    Additional audio parameters can be supplied via ``Observation.info``
    (keys: ``voice_type``, ``encoding``, ``speed_ratio``, ``output_path``).
    Instance-level defaults are used as fallbacks.
    
    Attributes:
        default_voice_type: Default voice type for speech synthesis
        default_encoding: Default audio encoding format
        default_speed_ratio: Default speech speed ratio
        output_dir: Default directory for saving audio files
        auto_filename: Whether to auto-generate filenames
    """
    
    @staticmethod
    def _ensure_doubao_tts_config(conf):
        """Ensure the config uses doubao_tts provider.
        
        This method forcibly sets the llm_provider to 'doubao_tts' because
        AudioAgent only works with DoubaoTTSProvider. If the user provided
        a different provider, it will be overridden with a warning.
        
        Args:
            conf: Input configuration (AgentConfig, dict, or ConfigDict)
            
        Returns:
            A new config object with llm_provider set to 'doubao_tts'
            
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
        
        # Log warning if overriding
        if original_provider and original_provider != "doubao_tts":
            logger.warning(
                f"AudioAgent: Overriding llm_provider from '{original_provider}' "
                f"to 'doubao_tts'. AudioAgent only works with DoubaoTTSProvider."
            )
        
        # Create a new AgentConfig with doubao_tts provider
        if isinstance(conf, AgentConfig):
            # For AgentConfig, we need to modify llm_config
            # Get the llm_config dict
            llm_config_dict = conf.llm_config.model_dump(exclude_none=True)
            llm_config_dict['llm_provider'] = "doubao_tts"
            
            # Create new ModelConfig
            new_llm_config = ModelConfig(**llm_config_dict)
            
            # Create new AgentConfig with the modified llm_config
            conf_dict = conf.model_dump(exclude_none=True)
            conf_dict['llm_config'] = new_llm_config
            
            return AgentConfig(**conf_dict)
        elif isinstance(conf, dict):
            # Modify dict directly
            conf['llm_provider'] = "doubao_tts"
            return conf
        else:
            # For other types (ConfigDict, etc.), try to handle gracefully
            logger.warning(
                f"AudioAgent: Unexpected config type {type(conf).__name__}. "
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
        # Audio generation defaults (overridable per-call via Observation.info)
        default_voice_type: Optional[str] = None,
        default_encoding: str = "mp3",
        default_speed_ratio: float = 1.0,
        output_dir: Optional[str] = None,
        auto_filename: bool = True,
        **kwargs,
    ):
        """Initialize AudioAgent.
        
        Args:
            name: Agent name
            conf: AgentConfig specifying the TTS provider, API key, and base URL.
                Must not be None. The llm_provider will be forcibly set to
                'doubao_tts' regardless of the input value.
            desc: Agent description exposed as tool description
            agent_id: Explicit agent ID; auto-generated if None
            default_voice_type: Default voice type identifier
                (e.g., "zh_male_M392_conversation_wvae_bigtts")
            default_encoding: Default audio encoding format (mp3, wav, pcm, ogg_opus)
            default_speed_ratio: Default speech speed ratio (0.5 to 2.0)
            output_dir: Directory to save generated audio files.
                Defaults to current working directory.
            auto_filename: Whether to auto-generate filenames based on timestamp
                and UUID. If False, output_path must be provided per call.
            **kwargs: Forwarded to ``LLMAgent.__init__``
            
        Raises:
            ValueError: If conf is None or invalid
            TypeError: If the provider is not DoubaoTTSProvider after initialization
        """
        # Validate and ensure doubao_tts config
        conf = self._ensure_doubao_tts_config(conf)
        
        super().__init__(
            name=name,
            conf=conf,
            desc=desc or "Text-to-speech audio generation agent",
            agent_id=agent_id,
            **kwargs,
        )
        
        # Verify that the provider is DoubaoTTSProvider
        if self.llm and self.llm.provider:
            if not isinstance(self.llm.provider, DoubaoTTSProvider):
                error_msg = (
                    f"[AudioAgent:{self.id()}] Expected DoubaoTTSProvider, "
                    f"but got {type(self.llm.provider).__name__}. "
                    f"AudioAgent only works with DoubaoTTSProvider. "
                    f"Config llm_provider was set to 'doubao_tts', but provider "
                    f"initialization failed. Please check your provider registry and "
                    f"ensure DoubaoTTSProvider is properly registered."
                )
                logger.error(error_msg)
                raise TypeError(error_msg)
        else:
            error_msg = (
                f"[AudioAgent:{self.id()}] Provider initialization failed. "
                f"self.llm or self.llm.provider is None. "
                f"Please check your configuration (api_key, base_url)."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        self.default_voice_type = default_voice_type or "zh_male_M392_conversation_wvae_bigtts"
        self.default_encoding = default_encoding
        self.default_speed_ratio = default_speed_ratio
        self.output_dir = output_dir or os.getcwd()
        self.auto_filename = auto_filename
    
    # ------------------------------------------------------------------
    # Core policy — single-round audio generation
    # ------------------------------------------------------------------
    
    async def async_policy(
        self,
        observation: Observation,
        info: Dict[str, Any] = {},
        message: Message = None,
        **kwargs,
    ) -> List[ActionModel]:
        """Single-round audio generation policy.
        
        Extracts the text and audio parameters from the observation, calls
        the TTS provider, saves the audio file, and returns the result as an
        ActionModel. The agent is marked as finished immediately so no
        further loop iterations occur.
        
        Args:
            observation: Contains the text to synthesize in ``content`` and
                optional overrides in ``info`` (voice_type, encoding,
                speed_ratio, output_path, uid).
            info: Supplementary information dict (merged with
                ``observation.info`` if both are non-empty).
            message: Incoming event message carrying context.
            **kwargs: Additional parameters (unused by this agent).
        
        Returns:
            A single-element list with an ActionModel whose ``policy_info``
            contains the audio generation result dict, or an error description
            string on failure.
        """
        self.context = message.context if message else None
        self._finished = False
        
        # Merge observation.info and caller-supplied info
        obs_info: Dict[str, Any] = dict(observation.info or {})
        obs_info.update(info or {})
        
        text = observation.content or ""
        if not text:
            error_msg = "Empty text provided; audio generation requires non-empty text."
            logger.warning(f"[AudioAgent:{self.id()}] {error_msg}")
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info=error_msg)]
        
        # Resolve audio parameters (observation.info overrides instance defaults)
        voice_type: str = obs_info.pop("voice_type", self.default_voice_type)
        encoding: str = obs_info.pop("encoding", self.default_encoding)
        speed_ratio: float = obs_info.pop("speed_ratio", self.default_speed_ratio)
        uid: Optional[str] = obs_info.pop("uid", None)
        
        # Determine output path
        output_path: Optional[str] = obs_info.pop("output_path", None)
        if not output_path and self.auto_filename:
            # Auto-generate filename
            timestamp = uuid.uuid4().hex[:8]
            filename = f"audio_{timestamp}.{encoding}"
            output_path = os.path.join(self.output_dir, filename)
        
        if not output_path:
            error_msg = (
                "No output_path provided and auto_filename is disabled. "
                "Please provide output_path in observation.info or enable auto_filename."
            )
            logger.error(f"[AudioAgent:{self.id()}] {error_msg}")
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info=error_msg)]
        
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path) or self.output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        logger.info(
            f"[AudioAgent:{self.id()}] Generating audio: "
            f"text_length={len(text)}, voice_type={voice_type}, "
            f"encoding={encoding}, speed_ratio={speed_ratio}, "
            f"output_path={output_path}"
        )
        
        audio_response: Optional[ModelResponse] = None
        try:
            audio_response = await self._invoke_audio_generation(
                text=text,
                voice_type=voice_type,
                encoding=encoding,
                speed_ratio=speed_ratio,
                uid=uid,
                output_path=output_path,
                context=message.context if message else None,
                **obs_info,  # Forward any remaining parameters
            )
            logger.info(f"[AudioAgent:{self.id()}] Audio generation response: {audio_response}")
        except Exception as exc:
            error_msg = f"Audio generation failed: {exc}"
            logger.error(
                f"[AudioAgent:{self.id()}] {error_msg}\n{traceback.format_exc()}"
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
            "text": text,
            "voice_type": voice_type,
            "encoding": encoding,
            "speed_ratio": speed_ratio,
        }

        if audio_response:
            # 不要把audio_bytes放入payload，因为bytes不能JSON序列化
            # 音频已经保存到文件了，返回文件路径即可
            result_payload.update({
                "audio_size": len(getattr(audio_response, "audio_data", b"")),
                "duration_ms": getattr(audio_response, "audio_duration_ms", 0),
                "usage": audio_response.usage,
            })

            logger.info(
                f"[AudioAgent:{self.id()}] Audio generation successful: "
                f"output_path={output_path}, "
                f"size={result_payload['audio_size']} bytes, "
                f"duration={result_payload['duration_ms']}ms"
            )
        else:
            logger.warning(f"[AudioAgent:{self.id()}] Unexpected response: {audio_response}")
        
        if message:
            await LLMAgent.send_agent_response_output(
                self, audio_response, message.context, kwargs.get("outputs")
            )
        
        self._finished = True
        # 设置params标记这是tool result，确保能正确反馈给调用方触发ReAct循环
        params = {"is_tool_result": True}
        policy_result = [ActionModel(agent_name=self.id(), policy_info=result_payload, params=params)]
        logger.info(f"[AudioAgent:{self.id()}] Agent result: {result_payload} {policy_result}")
        return policy_result
    
    async def _invoke_audio_generation(
        self,
        text: str,
        voice_type: str,
        encoding: str,
        speed_ratio: float,
        uid: Optional[str],
        output_path: str,
        context: Context = None,
        **extra_kwargs,
    ) -> ModelResponse:
        """Call the underlying TTS provider.
        
        Runs the TTS generation call in the default thread-pool executor
        so it does not block the event loop.
        
        Args:
            text: Text to synthesize
            voice_type: Voice type identifier
            encoding: Audio encoding format
            speed_ratio: Speech speed ratio
            uid: User ID for the request
            output_path: Path to save the audio file
            context: Runtime context (unused by the provider, passed through)
            **extra_kwargs: Additional provider-specific parameters
        
        Returns:
            ModelResponse with audio data and metadata
        
        Raises:
            Any exception raised by the provider
        """
        import asyncio
        
        provider = self.llm.provider
        
        # Verify provider type
        if not isinstance(provider, DoubaoTTSProvider):
            raise TypeError(
                f"AudioAgent requires DoubaoTTSProvider, "
                f"but got {type(provider).__name__}. "
                f"Please ensure conf.llm_provider is set to 'doubao_tts'."
            )
        
        # Check if provider has the required methods
        if not hasattr(provider, "text_to_speech") and not hasattr(provider, "atext_to_speech"):
            raise AttributeError(
                f"Provider {type(provider).__name__} does not have "
                f"text_to_speech or atext_to_speech methods."
            )
        
        loop = asyncio.get_event_loop()
        
        # Check if provider has async method
        if hasattr(provider, "atext_to_speech"):
            return await provider.atext_to_speech(
                text=text,
                voice_type=voice_type,
                encoding=encoding,
                speed_ratio=speed_ratio,
                uid=uid,
                output_path=output_path,
                **extra_kwargs,
            )
        else:
            # Fall back to sync method in executor
            return await loop.run_in_executor(
                None,
                lambda: provider.text_to_speech(
                    text=text,
                    voice_type=voice_type,
                    encoding=encoding,
                    speed_ratio=speed_ratio,
                    uid=uid,
                    output_path=output_path,
                    **extra_kwargs,
                ),
            )
    
    # ------------------------------------------------------------------
    # Override is_agent_finished — always finish after one round
    # ------------------------------------------------------------------
    
    def is_agent_finished(self, llm_response: ModelResponse, agent_result: AgentResult) -> bool:
        """AudioAgent always finishes after a single round."""
        self._finished = True
        return True
