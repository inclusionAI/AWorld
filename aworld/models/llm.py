import asyncio
import re
import time
import uuid
import traceback
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import (
    List,
    Dict,
    Union,
    Generator,
    AsyncGenerator,
    Any, Optional,
)
from aworld.config import ConfigDict, ModelConfig
from aworld.config.conf import AgentConfig, ClientType
from aworld.core.model_output_parser.default_parsers import ToolParser, ReasoningParser, CodeParser, JsonParser
from aworld.logs.util import logger, log_llm_record

from aworld.core.llm_provider import LLMProviderBase
from aworld.core.video_gen_provider import VideoGenProviderBase
from aworld.models.openai_provider import (
    OPENAI_CONTEXT_LOWERING,
    OpenAIProvider,
    AzureOpenAIProvider,
)
from aworld.models.anthropic_provider import AnthropicProvider
from aworld.models.ant_provider import AntProvider
from aworld.models.together_video_provider import TogetherVideoProvider
from aworld.models.ant_video_provider import AntVideoProvider
from aworld.models.kling_provider import KlingProvider
from aworld.models.kling_avatar_provider import KlingAvatarProvider
from aworld.models.volcano_seedance_provider import VolcanoSeedanceProvider
from aworld.models.model_response import ModelResponse
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    AWORLD_PROVIDER_CANDIDATE_KWARG,
    CandidateCompileInput,
    CandidateCompilePolicy,
    CandidateCompilation,
    CandidateRequestNotEnforceable,
    ContextCompilerMode,
    ContextObservationSidecar,
    ContextInputBudget,
    ContextResolutionTarget,
    FinalCompilePolicy,
    FRAMEWORK_COMPILER_IDENTITY,
    InferenceProfile,
    ProviderRequestSnapshot,
    ProviderCandidateEnvelope,
    ProviderCacheMaterial,
    ProviderLoweringCapability,
    ProviderRequestFidelity,
    RequestCaptureStage,
    RolloutContractError,
    canonical_json_hash,
    compile_context_candidate,
    inspect_final_context,
    observe_legacy_provider_request,
    request_trace_match,
    reviewed_provider_lowerings,
    select_rollout_request,
)
from aworld.core.model_output_parser import ModelOutputParser, BaseContentParser
from aworld.utils.common import sync_exec


AWORLD_CONTEXT_CALL_ID_KWARG = "_aworld_context_call_id"
_AWORLD_CONTEXT_CALL_ID: ContextVar[str | None] = ContextVar(
    "aworld_context_call_id", default=None
)

reviewed_provider_lowerings.register(
    OpenAIProvider, OPENAI_CONTEXT_LOWERING
)


@contextmanager
def bind_llm_context_call_id(call_id: str):
    """Correlate an Agent call without exposing an internal provider kwarg."""
    token = _AWORLD_CONTEXT_CALL_ID.set(call_id)
    try:
        yield
    finally:
        _AWORLD_CONTEXT_CALL_ID.reset(token)


def _resolve_context_call_id(kwargs: dict[str, Any]) -> str | None:
    # Continue accepting the historical private kwarg for direct callers while
    # ensuring it is removed before any provider invocation.
    return kwargs.pop(AWORLD_CONTEXT_CALL_ID_KWARG, None) or _AWORLD_CONTEXT_CALL_ID.get()


def _optional_runtime_identity(value: Any) -> str | None:
    """Normalize unset runtime ids before entering sealed trace contracts."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None

# Predefined model names for common providers
MODEL_NAMES = {
    "anthropic": ["claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620", "claude-3-opus-20240229"],
    "openai": ["gpt-4o", "gpt-4", "gpt-3.5-turbo", "o3-mini", "gpt-4o-mini"],
    "azure_openai": ["gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-35-turbo"],
}

# Endpoint patterns for identifying providers
ENDPOINT_PATTERNS = {
    "openai": ["api.openai.com"],
    "anthropic": ["api.anthropic.com", "claude-api"],
    "azure_openai": ["openai.azure.com"],
    "ant": ["zdfmng.alipay.com"],
    "together_video": ["api.together.ai", "api.together.xyz"],
    "video": ["matrixcube.alipay.com", "matrixcube-pool.global.alipay.com"],
    "ant_video": ["matrixcube.alipay.com", "matrixcube-pool.global.alipay.com"],
    # Kling official HTTP API (direct; distinct from MatrixCube gateway routing)
    "kling_video": ["api-beijing.klingai.com"],
    # Volcano Ark Seedance official API (direct)
    "volcano_seedance": ["ark.cn-beijing.volces.com"],
}

# Provider class mapping (LLM providers)
PROVIDER_CLASSES = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "azure_openai": AzureOpenAIProvider,
    "ant": AntProvider,
    "together_video": TogetherVideoProvider,
    "speech": None,  # Lazy loaded to avoid circular import
    "doubao_tts": None,  # Lazy loaded to avoid circular import
    "volcano_openspeech_tts": None,  # Lazy loaded; direct ByteDance OpenSpeech HTTP TTS
    "image": None,  # Lazy loaded to avoid circular import
    "kling_image": None,  # Lazy loaded to avoid circular import
}

# ---------------------------------------------------------------------------
# Video provider registry
#
# VIDEO_PROVIDER_CLASSES: provider_name -> VideoGenProviderBase subclass
#
# VIDEO_MODEL_REGISTRY: list of (pattern, provider_name) pairs.
#   Each pattern is matched against the model_name string.
#   Patterns are tried in order; the first match wins.
#   Patterns starting with '^' are treated as regex; otherwise plain prefix/exact match.
#
# To register a new video provider at runtime, call register_video_provider().
# ---------------------------------------------------------------------------

VIDEO_PROVIDER_CLASSES: Dict[str, type] = {
    # MatrixCube: alias "video" matches endpoint detection; implementation is AntVideoProvider only
    "video":          AntVideoProvider,
    "ant_video":      AntVideoProvider,
    "kling_video":    KlingProvider,
    "kling_avatar":   KlingAvatarProvider,
    "volcano_seedance": VolcanoSeedanceProvider,
    "together_video": TogetherVideoProvider,
}

VIDEO_MODEL_REGISTRY: List[tuple] = [
    # (pattern, provider_name) — first match wins.
    # Direct Kling official API (kling_provider.KlingProvider), not MatrixCube
    (r"^kling-",        "kling_video"),
    # Ant gateway (Doubao/Seedance, Veo via matrixcube) — AntVideoProvider
    (r"^doubao-video-", "ant_video"),
    (r"^seedance-",     "ant_video"),
    (r"^veo-",          "ant_video"),
    # Together.ai video models (use regex; matched with re.match from model_name start)
    (r".*minimax/.*",           "together_video"),
    (r".*google/veo-.*",        "together_video"),
    (r".*ByteDance/Seedance.*", "together_video"),
    (r".*pixverse/.*",          "together_video"),
    (r".*kwaivgI/kling-.*",     "together_video"),
    (r".*Wan-AI/.*",            "together_video"),
    (r".*vidu/.*",              "together_video"),
    (r".*openai/sora-.*",       "together_video"),
]


class ModelResponseParser(ModelOutputParser[ModelResponse, ModelResponse]):
    def __init__(self, parsers: List[BaseContentParser] = None, enable_default_parsers: bool = False) -> None:
        """Initialize the ModelOutputParser with default parsers and optional user-defined parsers.

        Args:
            parsers (List[BaseContentParser], optional): A list of custom parsers to register.
                These parsers will override default parsers if they share the same parser_type.
        Note:
            - If enable_default_parsers is True, the default parsers will be registered.
            - If parsers is provided, the user provided parsers will be registered.
            - If both are provided, the user provided parsers will be registered and the default parsers will be ignored.
            - default parsers: tool, thinking, code, json
        """
        self._parsers: Dict[str, BaseContentParser] = {}

        # Initialize default parsers
        default_parsers = [
            ToolParser(),
            ReasoningParser(),
            CodeParser(),
            JsonParser()
        ]

        if enable_default_parsers:
            for parser in default_parsers:
                self.register_parser(parser)

        # Register user provided parsers
        if parsers:
            for parser in parsers:
                self.register_parser(parser)

    def register_parser(self, parser: BaseContentParser) -> None:
        """Register a new content parser.

        If a parser with the same type already exists, it will be overwritten.

        Args:
            parser (BaseContentParser): The parser instance to register.
        """
        self._parsers[parser.parser_type] = parser

    def get_parser(self, parser_type: str) -> Optional[BaseContentParser]:
        """Retrieve a registered parser by its type.

        Args:
            parser_type (str): The type of the parser to retrieve (e.g., 'tool', 'thinking').

        Returns:
            Optional[BaseContentParser]: The parser instance if found, otherwise None.
        """
        return self._parsers.get(parser_type)

    def get_parsers(self) -> Dict[str, BaseContentParser]:
        """Get all registered parsers.

        Returns:
            Dict[str, BaseContentParser]: A dictionary mapping parser types to parser instances.
        """
        return self._parsers

    def list_supported_parser_types(self) -> List[str]:
        """List all supported parser types currently registered.

        Returns:
            List[str]: A list of parser type strings (e.g., ['tool', 'thinking', 'code', 'json']).
        """
        return list(self._parsers.keys())

    async def parse(self, resp: ModelResponse, **kwargs) -> ModelResponse:
        """Standard parse based Openai API."""

        if not resp:
            logger.warning("no valid content to parse!")
            return resp
        if kwargs.get("use_tools_in_prompt", False) and 'tool' not in self.list_supported_parser_types():
            self.register_parser(ToolParser())

        for content_parser in self.get_parsers().values():
            resp = await content_parser.parse(resp, **kwargs)

        return resp

    async def parse_chunk(self, chunk: ModelResponse, **kwargs) -> ModelResponse:
        """Standard parse based Openai API."""
        return chunk


class LLMModel:
    """Unified large model interface, encapsulates different model implementations, provides a unified completion method.
    """

    def __init__(self, conf: Union[ConfigDict, ModelConfig] = None, custom_provider: LLMProviderBase = None, **kwargs):
        """Initialize unified model interface.

        Args:
            conf: Agent configuration, if provided, create model based on configuration.
            custom_provider: Custom LLMProviderBase instance, if provided, use it directly.
            **kwargs: Other parameters, may include:
                - base_url: Specify model endpoint.
                - api_key: API key.
                - model_name: Model name.
                - temperature: Temperature parameter.
        """
        candidate_policy = kwargs.pop("context_candidate_policy", None)
        runtime_config = kwargs.pop("context_compiler", None)
        if runtime_config is None and conf_contains_key(conf, "context_compiler"):
            runtime_config = conf.context_compiler
        runtime_mode = (
            runtime_config.get("mode", "off")
            if isinstance(runtime_config, dict)
            else getattr(runtime_config, "mode", "off")
        )
        compiler_version = (
            runtime_config.get("compiler_version", "v1")
            if isinstance(runtime_config, dict)
            else getattr(runtime_config, "compiler_version", "v1")
        )
        def context_config_value(name: str, default: Any) -> Any:
            if isinstance(runtime_config, dict):
                return runtime_config.get(name, default)
            return getattr(runtime_config, name, default)

        resolved_compiler_mode = ContextCompilerMode(runtime_mode)
        self._context_scoped_instructions = context_config_value(
            "scoped_instructions", "workspace_only"
        )
        self._context_default_tool_output_inline_tokens = context_config_value(
            "default_tool_output_inline_tokens", 4096
        )
        self._context_artifact_offload = context_config_value(
            "artifact_offload", True
        )
        self._context_progressive_skills = context_config_value(
            "progressive_skills", True
        )
        self._context_progressive_tools = context_config_value(
            "progressive_tools", True
        )
        self._context_task_catalog_policy = context_config_value(
            "task_catalog_policy", "sticky"
        )
        if candidate_policy is None:
            final_policy = None
            if context_config_value("universal_final", True):
                configured_context_limit = context_config_value(
                    "context_limit", None
                )
                if configured_context_limit is None:
                    configured_context_limit = (
                        getattr(conf, "max_model_len", None) or 128000
                    )
                final_policy = FinalCompilePolicy(
                    compiler_version=compiler_version,
                    policy_version=context_config_value("policy_version", "v1"),
                    input_budget=ContextInputBudget(
                        context_limit=configured_context_limit,
                        reserved_output_tokens=context_config_value(
                            "reserved_output_tokens", 4096
                        ),
                        provider_protocol_reserve=context_config_value(
                            "provider_protocol_reserve", 256
                        ),
                        safety_margin_tokens=context_config_value(
                            "safety_margin_tokens", 512
                        ),
                        max_item_tokens=context_config_value(
                            "max_item_tokens", 10000
                        ),
                    ),
                    require_proven_semantics_for_enforce=context_config_value(
                        "require_proven_semantics_for_enforce", True
                    ),
                )
            candidate_policy = CandidateCompilePolicy(
                compiler_version=compiler_version,
                final_policy=final_policy,
            )
        self.configure_context_compiler(
            mode=resolved_compiler_mode,
            candidate_policy=candidate_policy,
        )

        self.llm_response_parser: ModelResponseParser = conf.llm_response_parser \
            if conf and hasattr(conf, 'llm_response_parser') else None

        # If custom_provider instance is provided, use it directly
        if custom_provider is not None:
            if not isinstance(custom_provider, (LLMProviderBase, VideoGenProviderBase)):
                raise TypeError(
                    "custom_provider must be an instance of LLMProviderBase or VideoGenProviderBase"
                )
            self.provider_name = "custom"
            self.provider = custom_provider
            return
        # Get basic parameters
        base_url = kwargs.get("base_url") or (
            conf.llm_base_url if conf else None)
        model_name = kwargs.get("model_name") or (
            conf.llm_model_name if conf else None)
        llm_provider = conf.llm_provider if conf_contains_key(
            conf, "llm_provider") else None

        # Get API key from configuration (if any)
        if conf and conf.llm_api_key:
            kwargs["api_key"] = conf.llm_api_key

        # Identify provider
        self.provider_name = self._identify_provider(
            llm_provider, base_url, model_name)

        # Fill basic parameters
        kwargs['base_url'] = base_url
        kwargs['model_name'] = model_name

        # Fill parameters for llm provider
        kwargs['sync_enabled'] = conf.llm_sync_enabled if conf_contains_key(
            conf, "llm_sync_enabled") else True
        kwargs['async_enabled'] = conf.llm_async_enabled if conf_contains_key(
            conf, "llm_async_enabled") else True
        kwargs['client_type'] = conf.llm_client_type if conf_contains_key(
            conf, "llm_client_type") else ClientType.SDK

        kwargs.update(self._transfer_conf_to_args(conf))

        # Create model provider based on provider_name
        self._create_provider(**kwargs)

    def _transfer_conf_to_args(self, conf: Union[ConfigDict, AgentConfig] = None) -> dict:
        """
        Transfer parameters from conf to args

        Args:
            conf: config object
        """
        if not conf:
            return {}

        # Get all parameters from conf
        if type(conf).__name__ == 'ModelConfig':
            conf_dict = conf.model_dump()
        else:  # ConfigDict
            conf_dict = conf

        ignored_keys = ["llm_provider", "llm_base_url", "llm_model_name", "llm_api_key", "llm_sync_enabled",
                        "llm_async_enabled", "llm_client_type", "llm_response_parser", "context_compiler"]
        args = {}
        # Filter out used parameters and add remaining parameters to args
        for key, value in conf_dict.items():
            if key == "ext_config" and value is not None:
                args.update(value)
            elif key not in ignored_keys and value is not None:
                args[key] = value

        return args

    def configure_context_compiler(
        self,
        *,
        mode: ContextCompilerMode | str,
        candidate_policy: CandidateCompilePolicy | None = None,
    ) -> None:
        """Configure request rollout without changing provider construction."""
        resolved_mode = ContextCompilerMode(mode)
        if candidate_policy is None:
            candidate_policy = CandidateCompilePolicy()
        if type(candidate_policy) is not CandidateCompilePolicy:
            raise TypeError(
                "candidate_policy must be the sealed CandidateCompilePolicy type"
            )
        self._context_compiler_mode = resolved_mode
        self._context_candidate_policy = candidate_policy

    @property
    def context_compiler_mode(self) -> ContextCompilerMode:
        """Expose the validated rollout mode without allowing direct replacement."""
        return self._context_compiler_mode

    @property
    def context_candidate_policy(self) -> CandidateCompilePolicy:
        """Expose the frozen policy without allowing ambient replacement."""
        return self._context_candidate_policy

    def enforced_tool_output_policy(self):
        """Return the runtime policy only when candidate execution is authoritative."""
        if self.context_compiler_mode is not ContextCompilerMode.ENFORCE:
            return None
        from aworld.core.context.compiler import ToolOutputMode, ToolOutputPolicy

        limit = self._context_default_tool_output_inline_tokens
        return ToolOutputPolicy(
            max_inline_tokens=limit,
            mode=ToolOutputMode.HEAD_TAIL,
            preserve_fields=("head", "tail", "artifact_ref", "raw_checksum"),
            tail_tokens=max(0, limit // 4),
            artifact_retention="task",
            policy_version="aworld-tool-output-v1",
        )

    def _identify_provider(self, provider: str = None, base_url: str = None, model_name: str = None) -> str:
        """Identify the provider for the given configuration.

        Identification logic (in priority order):
        1. Explicit ``provider`` argument — used as-is when it exists in either
           PROVIDER_CLASSES or VIDEO_PROVIDER_CLASSES.
        2. ``base_url`` — matched against ENDPOINT_PATTERNS.
        3. ``model_name`` — first checked against VIDEO_MODEL_REGISTRY (video
           providers), then against MODEL_NAMES (LLM providers).
        4. Falls back to ``"openai"``.

        Args:
            provider: Explicitly specified provider name.
            base_url: Service endpoint URL.
            model_name: Model name string.

        Returns:
            str: Resolved provider name.
        """
        identified_provider = "openai"

        # 1. FIRST: Check explicit provider (highest priority)
        all_providers = {**PROVIDER_CLASSES, **VIDEO_PROVIDER_CLASSES}
        if provider:
            if provider in all_providers:
                logger.info(
                    f"Using explicit provider: {provider}"
                )
                return provider
            else:
                logger.warning(
                    f"Explicit provider '{provider}' not found in registry. "
                    f"Available providers: {list(all_providers.keys())}. "
                    f"Falling back to auto-detection."
                )

        # 2. SECOND: Match base_url against endpoint patterns (covers both LLM and video providers)
        if base_url:
            for p, patterns in ENDPOINT_PATTERNS.items():
                if any(pattern in base_url for pattern in patterns):
                    identified_provider = p
                    logger.info(
                        f"Identified provider: {identified_provider} based on base_url: {base_url}"
                    )
                    return identified_provider

        # 3. THIRD: Match model_name — video registry takes priority over LLM model names
        if model_name:
            # Check video model registry first
            video_provider = _match_video_registry(model_name)
            if video_provider:
                logger.info(
                    f"Identified video provider: {video_provider} based on model_name: {model_name}"
                )
                identified_provider = video_provider
            else:
                # Fall back to LLM model name matching
                for p, models in MODEL_NAMES.items():
                    if model_name in models or any(model_name.startswith(m) for m in models):
                        identified_provider = p
                        logger.info(
                            f"Identified provider: {identified_provider} based on model_name: {model_name}"
                        )
                        break

        # 4. FOURTH: Default fallback
        if identified_provider == "openai" and not provider and not base_url and not model_name:
            logger.debug("No provider information provided, using default: openai")
        return identified_provider

    def _create_provider(self, **kwargs):
        """Instantiate the provider class resolved by ``_identify_provider``.

        Looks up the provider name first in VIDEO_PROVIDER_CLASSES (video
        generation providers), then in PROVIDER_CLASSES (LLM providers).

        Args:
            **kwargs: Parameters forwarded to the provider constructor, e.g.
                base_url, api_key, model_name, timeout, max_retries.

        Raises:
            ValueError: When the resolved provider name is not registered in
                either provider table.
        """
        if self.provider_name in VIDEO_PROVIDER_CLASSES:
            self.provider = VIDEO_PROVIDER_CLASSES[self.provider_name](**kwargs)
        elif self.provider_name in PROVIDER_CLASSES:
            provider_class = PROVIDER_CLASSES[self.provider_name]
            # Lazy load providers to avoid circular import
            if provider_class is None and self.provider_name in ("speech", "doubao_tts"):
                from aworld.models.doubao_tts_provider import DoubaoTTSProvider
                provider_class = DoubaoTTSProvider
                PROVIDER_CLASSES[self.provider_name] = provider_class
            elif provider_class is None and self.provider_name == "volcano_openspeech_tts":
                from aworld.models.volcano_openspeech_tts_provider import (
                    VolcanoOpenSpeechTTSProvider,
                )

                provider_class = VolcanoOpenSpeechTTSProvider
                PROVIDER_CLASSES[self.provider_name] = provider_class
            elif provider_class is None and self.provider_name == "image":
                from aworld.models.image_provider import ImageProvider
                provider_class = ImageProvider
                PROVIDER_CLASSES[self.provider_name] = provider_class
            elif provider_class is None and self.provider_name == "kling_image":
                from aworld.models.kling_image_provider import KlingImageProvider
                provider_class = KlingImageProvider
                PROVIDER_CLASSES[self.provider_name] = provider_class
            self.provider = provider_class(**kwargs)
        else:
            raise ValueError(
                f"Unknown provider '{self.provider_name}'. "
                f"Register it via register_llm_provider() or register_video_provider()."
            )

    @staticmethod
    def _generate_llm_request_id() -> str:
        """Generate a unique LLM request ID based on timestamp and UUID."""
        ts = int(time.time() * 1000)
        rand = uuid.uuid4().hex[:8]
        return f"llm_req_{ts}_{rand}"

    @classmethod
    def supported_providers(cls) -> list[str]:
        return list(PROVIDER_CLASSES.keys())

    def supported_models(self) -> list[str]:
        """Get supported models for the current provider.
        Returns:
            list: Supported models.
        """
        return self.provider.supported_models() if self.provider else []

    @staticmethod
    def _safe_copy(value: Any) -> Any:
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    @staticmethod
    def _has_meaningful_value(value: Any) -> bool:
        if value is None or value == "" or value is False:
            return False
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        if isinstance(value, (int, float)):
            return value != 0
        return True

    @classmethod
    def _usage_has_meaningful_value(cls, usage: Any) -> bool:
        if not isinstance(usage, dict) or not usage:
            return False
        for value in usage.values():
            if isinstance(value, dict):
                if cls._usage_has_meaningful_value(value):
                    return True
                continue
            if isinstance(value, (list, tuple, set)):
                if any(cls._has_meaningful_value(item) for item in value):
                    return True
                continue
            if cls._has_meaningful_value(value):
                return True
        return False

    @classmethod
    def _message_has_meaningful_value(cls, message: Any) -> bool:
        if not isinstance(message, dict) or not message:
            return False
        return any(
            cls._has_meaningful_value(value) and key != "role"
            for key, value in message.items()
        )

    @classmethod
    def _is_meaningful_stream_response(cls, response: Optional[ModelResponse]) -> bool:
        if response is None:
            return False
        return any([
            cls._has_meaningful_value(getattr(response, "provider_request_id", None)),
            cls._has_meaningful_value(getattr(response, "content", None)),
            cls._has_meaningful_value(getattr(response, "tool_calls", None)),
            cls._has_meaningful_value(getattr(response, "finish_reason", None)),
            cls._usage_has_meaningful_value(getattr(response, "usage", None)),
            cls._usage_has_meaningful_value(getattr(response, "raw_usage", None)),
            cls._message_has_meaningful_value(getattr(response, "message", None)),
        ])

    def _merge_stream_response_record(
        self,
        base_response: Optional[ModelResponse],
        next_response: Optional[ModelResponse],
    ) -> Optional[ModelResponse]:
        if base_response is None:
            return self._safe_copy(next_response)
        if next_response is None:
            return self._safe_copy(base_response)

        merged = self._safe_copy(base_response)
        message = self._safe_copy(getattr(base_response, "message", None))
        next_message = getattr(next_response, "message", None)
        if isinstance(message, dict) and isinstance(next_message, dict):
            for key, value in next_message.items():
                if key == "role":
                    continue
                if self._has_meaningful_value(value):
                    message[key] = self._safe_copy(value)
        elif self._message_has_meaningful_value(next_message):
            message = self._safe_copy(next_message)

        for attr in ("id", "model", "provider_request_id", "finish_reason", "content", "tool_calls"):
            value = getattr(next_response, attr, None)
            if self._has_meaningful_value(value):
                setattr(merged, attr, self._safe_copy(value))

        if self._usage_has_meaningful_value(getattr(next_response, "usage", None)):
            merged.usage = self._safe_copy(next_response.usage)
        if self._usage_has_meaningful_value(getattr(next_response, "raw_usage", None)):
            merged.raw_usage = self._safe_copy(next_response.raw_usage)
        if message is not None:
            merged.message = message
        return merged

    def _capture_stream_response_record(
        self,
        base_response: Optional[ModelResponse],
        next_response: Optional[ModelResponse],
    ) -> Optional[ModelResponse]:
        """Fold a stream record without changing the provider-facing stream."""
        try:
            if base_response is None or self._is_meaningful_stream_response(
                next_response
            ):
                return self._merge_stream_response_record(
                    base_response, next_response
                )
            return base_response
        except Exception as exc:
            logger.warning(
                f"LLM stream capture merge failed; error_type={type(exc).__name__}"
            )
            return base_response

    def _resolve_request_model_name(self, **kwargs) -> Optional[str]:
        return kwargs.get("model_name") or getattr(self.provider, "model_name", None)

    def _model_boundary_request(
        self,
        *,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        stop: List[str],
        tools: Any,
    ) -> dict[str, Any]:
        return {
            "messages": self._safe_copy(messages),
            "tools": self._safe_copy(tools),
            "params": {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stop": self._safe_copy(stop),
            },
        }

    async def _apply_before_llm_hooks(
        self,
        *,
        context: Context | None,
        request_id: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> List[Dict[str, str]]:
        """Apply the shared final pre-compile hook chain for every call shape."""
        if context is None:
            return messages
        from aworld.runners.hook.hooks import HookPoint
        from aworld.runners.hook.utils import run_hooks

        current_messages = messages
        payload = {
            "event": "before_llm_call",
            "model_name": self.provider.model_name,
            "provider_name": self.provider_name,
            "messages": current_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "request_id": request_id,
            "timestamp": time.time(),
        }
        async for hook_event in run_hooks(
            context=context,
            hook_point=HookPoint.BEFORE_LLM_CALL,
            hook_from="llm_model",
            payload=payload,
            workspace_path=getattr(context, "workspace_path", None),
        ):
            updated_input = (
                hook_event.headers.get("updated_input")
                if hook_event is not None and hasattr(hook_event, "headers")
                else None
            )
            if isinstance(updated_input, list):
                current_messages = updated_input
            elif isinstance(updated_input, dict) and isinstance(
                updated_input.get("messages"), list
            ):
                current_messages = updated_input["messages"]
        return current_messages

    @staticmethod
    def _context_agent_identity(context: Context | None) -> str | None:
        if context is None:
            return None
        agent_info = getattr(context, "agent_info", None)
        value = (
            agent_info.get("current_agent_id")
            if isinstance(agent_info, dict)
            else getattr(agent_info, "current_agent_id", None)
        )
        if not value:
            value = context.get_state("current_agent_id")
        return str(value) if value else f"task-agent-{context.task_id}"

    def _finalize_context_messages(
        self,
        *,
        context: Context | None,
        request_id: str,
        messages: List[Dict[str, Any]],
        tools: Any,
    ) -> List[Dict[str, Any]]:
        """Apply reviewed provider normalization and publish the exact boundary."""
        values = messages
        if self.context_compiler_mode is not ContextCompilerMode.OFF:
            normalizer = getattr(
                self.provider, "context_model_boundary_messages", None
            )
            if callable(normalizer):
                values = normalizer(messages)
                if not isinstance(values, list):
                    raise TypeError(
                        "provider model-boundary normalizer must return a list"
                    )
        if context is None or self.context_compiler_mode is ContextCompilerMode.OFF:
            return values
        from aworld.agents.final_context_adapter import adapt_agent_final_request

        agent_id = self._context_agent_identity(context)
        source_identity = (
            f"model-final://{agent_id}/task-{context.task_id}/"
            f"epoch-{context.task_epoch}/request-{request_id}"
        )
        try:
            from aworld.core.context.amni import AmniContext

            amni_folded_system = isinstance(context, AmniContext)
        except ImportError:
            amni_folded_system = False
        message_result, tool_result = adapt_agent_final_request(
            messages=values,
            tools=tools or (),
            source_identity=source_identity,
            task_id=context.task_id,
            task_epoch=context.task_epoch,
            agent_id=agent_id,
            amni_folded_system=amni_folded_system,
        )
        context.publish_context_observation(
            ContextObservationSidecar.from_adapter_result(
                owner="model.final_messages",
                namespace=agent_id,
                source_identity=source_identity,
                result=message_result,
            )
        )
        context.publish_context_observation(
            ContextObservationSidecar.from_adapter_result(
                owner="model.final_tool_catalog",
                namespace=agent_id,
                source_identity=source_identity,
                result=tool_result,
            )
        )
        return values

    def _finalize_context_messages_for_rollout(
        self,
        *,
        context: Context | None,
        request_id: str,
        agent_call_id: str | None,
        started_at: float,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        stop: List[str],
        tools: Any,
        model_name: str | None,
    ) -> List[Dict[str, Any]]:
        """Finalize the model boundary with a correlated fail-closed receipt."""
        try:
            return self._finalize_context_messages(
                context=context,
                request_id=request_id,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            if self.context_compiler_mode is not ContextCompilerMode.ENFORCE:
                logger.warning(
                    "Context model-boundary finalization failed; continuing "
                    f"without enforcement; error_type={type(exc).__name__}"
                )
                return messages
            rollout = {
                "mode": ContextCompilerMode.ENFORCE.value,
                "candidate_status": "blocked",
                "candidate_applied": False,
                "provider_lowering_ready": False,
                "error": {"code": "model_boundary_finalize_failed"},
            }
            self._begin_llm_call_record(
                context=context,
                request_id=request_id,
                agent_call_id=agent_call_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                started_at=started_at,
                tools=tools,
                model_name=model_name,
                context_rollout=rollout,
                provider_invoked=False,
            )
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="blocked_before_provider",
                finished_at=time.time(),
                error_code="model_boundary_finalize_failed",
            )
            raise CandidateRequestNotEnforceable(
                "model_boundary_finalize_failed"
            ) from None

    def _prepare_context_rollout(
        self,
        *,
        context: Context | None,
        request_id: str,
        agent_call_id: str | None,
        started_at: float,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        stop: List[str],
        tools: Any,
        model_name: str | None,
    ) -> tuple[dict[str, Any] | None, ProviderCandidateEnvelope | None]:
        """Prepare redacted rollout metadata without invoking external actions.

        The current model boundary is structurally faithful but not a
        provider-serialized execution boundary. Therefore shadow is supported,
        while runtime enforce remains fail-closed until provider lowering owns
        both the immutable candidate and its execution.
        """
        mode = self.context_compiler_mode
        if mode is ContextCompilerMode.OFF:
            # Keep the default path byte-for-byte compatible at the model
            # boundary: no candidate snapshot, comparison, or record field.
            return None, None
        if mode is ContextCompilerMode.OBSERVE:
            # Existing context_observe capture below remains authoritative.
            # Observe never asks the candidate compiler to do work.
            return {
                "mode": mode.value,
                "candidate_status": "not_requested",
                "candidate_applied": False,
                "external_actions_authorized": False,
                "external_action_count_observed": None,
                "comparison": None,
            }, None

        compile_started = time.perf_counter()
        policy = getattr(self, "_context_candidate_policy", None)
        policy_valid = type(policy) is CandidateCompilePolicy
        base_evidence = {
            "mode": mode.value,
            "compiler_identity": FRAMEWORK_COMPILER_IDENTITY,
            "compiler_version": policy.compiler_version if policy_valid else None,
            "comparison_projection": "aworld.standard.model_boundary.v1",
            "comparison_direction": "candidate_against_legacy",
            "external_actions_authorized": False,
            "external_action_count_observed": None,
            "provider_lowering_ready": False,
        }

        def elapsed_ms() -> float:
            return round((time.perf_counter() - compile_started) * 1000, 3)

        def fail_metadata(*, status: str, error_code: str) -> dict[str, Any]:
            return {
                **base_evidence,
                "candidate_status": status,
                "candidate_applied": False,
                "compiler_elapsed_ms": elapsed_ms(),
                "comparison": None,
                "error": {"code": error_code},
            }

        def block(error: RolloutContractError, metadata: dict[str, Any]):
            self._begin_llm_call_record(
                context=context,
                request_id=request_id,
                agent_call_id=agent_call_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                started_at=started_at,
                tools=tools,
                model_name=model_name,
                context_rollout=metadata,
                provider_invoked=False,
            )
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="blocked_before_provider",
                finished_at=time.time(),
                error_code=error.code,
            )
            raise error from None

        if not policy_valid:
            if mode is ContextCompilerMode.ENFORCE:
                block(
                    CandidateRequestNotEnforceable("invalid_compiler_policy"),
                    fail_metadata(
                        status="blocked", error_code="invalid_compiler_policy"
                    ),
                )
            return (
                fail_metadata(
                    status="failed", error_code="invalid_compiler_policy"
                ),
                None,
            )

        try:
            resolved_active_path = (
                context.get_state("active_path", context.workspace_path)
                if context is not None
                else None
            )
            if resolved_active_path is not None:
                resolved_active_path = str(resolved_active_path)
            if (
                context is not None
                and self._context_scoped_instructions == "nested"
            ):
                context.refresh_nested_instruction_observation(
                    active_path=resolved_active_path
                )
            legacy_request = ProviderRequestSnapshot(
                request_id=request_id,
                provider_name=self.provider_name,
                payload=self._model_boundary_request(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stop=stop,
                    tools=tools,
                ),
                capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
                fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
            )
            observations = (
                context.get_context_observations() if context is not None else ()
            )
            task_epoch = context.task_epoch if context is not None else None
            agent_id = self._context_agent_identity(context)
            workspace_path = (
                context.workspace_path if context is not None else None
            )
            context_limit = (
                policy.final_policy.input_budget.context_limit
                if policy.final_policy is not None
                else None
            )
            compiler_input = CandidateCompileInput(
                legacy_request=legacy_request,
                observations=observations,
                inference_profile=InferenceProfile(
                    provider=self.provider_name,
                    model=model_name or "unknown-model",
                    reasoning_effort=None,
                    execution_mode="chat_completions",
                    context_limit=context_limit,
                ),
                created_at=datetime.now(timezone.utc),
                task_id=context.task_id if context is not None else None,
                session_id=context.session_id if context is not None else None,
                trace_id=(context.trace_id or None) if context is not None else None,
                task_epoch=task_epoch,
                resolution_target=(
                    ContextResolutionTarget(
                        workspace_id=workspace_path,
                        directory=(resolved_active_path or workspace_path),
                        active_paths=(
                            (resolved_active_path,)
                            if resolved_active_path
                            else ((workspace_path,) if workspace_path else ())
                        ),
                        session_id=context.session_id,
                        task_id=context.task_id,
                        turn_id=(
                            context.current_step_id()
                            or f"turn-{context.context_lifecycle_state.turn_epoch}"
                        ),
                        agent_id=agent_id,
                        child_task_id=context.task_id,
                        task_epoch=task_epoch,
                    )
                    if context is not None
                    else None
                ),
                reducer_replacements=(
                    tuple(
                        receipt.to_replacement()
                        for receipt in context.get_context_reduction_receipts()
                    )
                    if context is not None
                    else ()
                ),
            )
        except Exception:
            if mode is ContextCompilerMode.ENFORCE:
                block(
                    CandidateRequestNotEnforceable("candidate_input_failed"),
                    fail_metadata(
                        status="blocked", error_code="candidate_input_failed"
                    ),
                )
            return (
                fail_metadata(
                    status="failed", error_code="candidate_input_failed"
                ),
                None,
            )

        try:
            candidate = compile_context_candidate(
                compiler_input=compiler_input,
                policy=policy,
            )
            if not isinstance(candidate, CandidateCompilation):
                raise TypeError("framework compiler returned an invalid result")
            snapshot = candidate.request_snapshot
            if (
                snapshot.request_id != request_id
                or snapshot.provider_name != self.provider_name
                or snapshot.capture_stage is not RequestCaptureStage.MODEL_BOUNDARY
                or snapshot.fidelity is not ProviderRequestFidelity.MODEL_BOUNDARY
            ):
                raise ValueError("candidate boundary mismatch")
            selection = select_rollout_request(
                mode=ContextCompilerMode.SHADOW,
                legacy_request=legacy_request,
                candidate_request=snapshot,
            )
            metadata = {
                **base_evidence,
                "compiler_identity": candidate.compiler_identity,
                "compiler_version": candidate.compiler_version,
                "candidate_status": (
                    "compiled" if candidate.enforce_ready else "shadow_only"
                ),
                "candidate_applied": False,
                "compiler_elapsed_ms": elapsed_ms(),
                "legacy_snapshot": {
                    "content_hash": legacy_request.content_hash,
                    "capture_stage": legacy_request.capture_stage.value,
                    "fidelity": legacy_request.fidelity.value,
                },
                "candidate_snapshot": {
                    "content_hash": snapshot.content_hash,
                    "capture_stage": snapshot.capture_stage.value,
                    "fidelity": snapshot.fidelity.value,
                },
                "comparison": (
                    selection.comparison.to_dict()
                    if selection.comparison else None
                ),
                "diagnostic_code_hashes": [
                    canonical_json_hash({"code": code})
                    for code in candidate.diagnostic_codes
                ],
            }
            if candidate.final_result is not None:
                metadata["final_compile"] = inspect_final_context(
                    candidate.final_result
                )
        except Exception as exc:
            logger.error(
                "Context candidate compilation failed before provider lowering; "
                f"error_type={type(exc).__name__}; "
                f"error_fingerprint={canonical_json_hash({'type': type(exc).__name__, 'detail': str(exc)})}"
            )
            if mode is ContextCompilerMode.ENFORCE:
                block(
                    CandidateRequestNotEnforceable("compiler_failed"),
                    fail_metadata(
                        status="blocked", error_code="candidate_compilation_failed"
                    ),
                )
            return (
                fail_metadata(
                    status="failed", error_code="candidate_compilation_failed"
                ),
                None,
            )

        if mode is ContextCompilerMode.ENFORCE:
            if not candidate.enforce_ready:
                blocked = dict(metadata)
                blocked["candidate_status"] = "blocked"
                blocked["error"] = {"code": "candidate_not_enforce_ready"}
                block(
                    CandidateRequestNotEnforceable(
                        "candidate_not_enforce_ready"
                    ),
                    blocked,
                )
            capability = reviewed_provider_lowerings.resolve(
                self.provider, self.provider_name
            )
            if type(capability) is not ProviderLoweringCapability:
                blocked = dict(metadata)
                blocked["candidate_status"] = "blocked"
                blocked["error"] = {"code": "provider_lowering_required"}
                block(
                    CandidateRequestNotEnforceable(
                        "provider_lowering_required"
                    ),
                    blocked,
                )
            try:
                cache_material = None
                if candidate.final_result is not None:
                    request_messages = snapshot.payload["messages"]
                    stable_items = candidate.final_result.stable_partition.stable_items
                    stable_message_count = len(stable_items)
                    if (
                        isinstance(request_messages, tuple)
                        and stable_message_count <= len(request_messages)
                        and all(
                            item.payload == request_messages[index]
                            for index, item in enumerate(stable_items)
                        )
                    ):
                        cache_material = ProviderCacheMaterial(
                            inference_profile=compiler_input.inference_profile,
                            policy_version=candidate.final_result.policy_version,
                            tool_catalog_hash=candidate.final_result.tool_catalog_hash,
                            skill_set_hash=candidate.final_result.skill_set_hash,
                            logical_stable_prefix_hash=(
                                candidate.final_result.stable_partition.stable_prefix_hash
                            ),
                            stable_message_count=stable_message_count,
                        )
                envelope = ProviderCandidateEnvelope(
                    candidate_request=snapshot,
                    compiler_identity=candidate.compiler_identity,
                    compiler_version=candidate.compiler_version,
                    expected_lowering=capability,
                    cache_material=cache_material,
                )
            except Exception:
                blocked = dict(metadata)
                blocked["candidate_status"] = "blocked"
                blocked["error"] = {"code": "provider_lowering_contract_invalid"}
                block(
                    CandidateRequestNotEnforceable(
                        "provider_lowering_contract_invalid"
                    ),
                    blocked,
                )
            prepared = dict(metadata)
            prepared.update({
                "candidate_status": "awaiting_provider_lowering",
                "provider_lowering_ready": True,
                "provider_lowering": {
                    "adapter_identity": capability.adapter_identity,
                    "adapter_version": capability.adapter_version,
                    "request_projection": capability.request_projection,
                    "status": "awaiting_receipt",
                },
            })
            return prepared, envelope
        return metadata, None

    def _mark_context_rollout_blocked(
        self,
        *,
        context: Context | None,
        request_id: str,
        reason_code: str,
    ) -> None:
        """Best-effort redacted receipt for a provider-lowering rejection."""
        try:
            if context is None:
                return
            llm_calls = context.get_llm_calls()
            for index, record in enumerate(llm_calls):
                if not isinstance(record, dict) or record.get("request_id") != request_id:
                    continue
                updated = dict(record)
                rollout = dict(updated.get("context_rollout") or {})
                rollout.update({
                    "candidate_status": "blocked",
                    "candidate_applied": False,
                    "error": {"code": reason_code},
                })
                updated.update({
                    "context_rollout": rollout,
                    "provider_invoked": False,
                })
                llm_calls[index] = updated
                return
        except Exception as exc:
            logger.warning(
                "LLM provider lowering rejection capture failed; "
                f"error_type={type(exc).__name__}"
            )

    def _unsafe_begin_llm_call_record(
        self,
        *,
        context: Context | None,
        request_id: str,
        agent_call_id: str | None,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        stop: List[str],
        started_at: float,
        tools: Any,
        model_name: str | None,
        context_rollout: dict[str, Any] | None = None,
        provider_invoked: bool = True,
    ) -> None:
        if context is None:
            return

        agent_info = context.agent_info
        agent_id = (
            agent_info.get("current_agent_id")
            if isinstance(agent_info, dict)
            else getattr(agent_info, "current_agent_id", None)
        ) if agent_info else None
        request = self._model_boundary_request(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=tools,
        )
        observe_payload: dict[str, Any]
        observation = None
        try:
            observation = observe_legacy_provider_request(
                messages=messages,
                tools=tools,
                params=request["params"],
                provider_name=self.provider_name,
                request_id=request_id,
                capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
                fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
                source_identity=f"llm-model-request:{request_id}",
                task_id=_optional_runtime_identity(context.task_id),
                session_id=_optional_runtime_identity(
                    getattr(context, "session_id", None)
                ),
                trace_id=_optional_runtime_identity(
                    getattr(context, "trace_id", None)
                ),
            )
            observe_payload = observation.to_redacted_dict()
        except Exception as exc:
            observe_payload = {
                "status": "error",
                "error": {"code": "context_observe_failed"},
            }
            logger.warning(
                "Context request observation failed; "
                f"error_type={type(exc).__name__}"
            )

        llm_call = {
            "capture_stage": RequestCaptureStage.MODEL_BOUNDARY.value,
            "capture_fidelity": ProviderRequestFidelity.MODEL_BOUNDARY.value,
            "request_projection": "aworld.standard.model_boundary.v1",
            "provider_prepared_request_match": None,
            "request_id": request_id,
            "provider_request_id": None,
            "task_id": context.task_id,
            "agent_id": agent_id,
            "model": model_name or getattr(self.provider, "model_name", None),
            "provider_name": self.provider_name,
            "status": "in_progress",
            "started_at": started_at,
            "request": request,
            "context_observe": observe_payload,
            "attempt": 1,
        }
        if context_rollout is not None:
            llm_call["context_rollout"] = self._safe_copy(context_rollout)
        if not provider_invoked:
            llm_call["provider_invoked"] = False
        llm_call["request_trace_match_scope"] = (
            "aworld.standard.model_boundary.v1"
        )
        if observation is not None:
            try:
                direct_match = request_trace_match(
                    observation.request_snapshot,
                    request,
                )
                llm_call["request_trace_match"] = direct_match.exact
                llm_call["request_trace_mismatch_paths"] = list(
                    direct_match.mismatch_paths
                )
                llm_call["request_trace_mismatch_count"] = (
                    direct_match.mismatch_count
                )
            except Exception:
                llm_call["request_trace_match"] = None
                llm_call["request_trace_mismatch_paths"] = None
                llm_call["request_trace_mismatch_count"] = None
                llm_call["request_trace_match_error"] = {
                    "code": "request_trace_match_failed"
                }
        else:
            llm_call["request_trace_match"] = None
            llm_call["request_trace_mismatch_paths"] = None
            llm_call["request_trace_mismatch_count"] = None
        llm_calls = context.get_llm_calls()
        if agent_call_id:
            matched = [
                (index, record)
                for index, record in enumerate(llm_calls)
                if isinstance(record, dict)
                and record.get("call_id") == agent_call_id
            ]
            unbound = next(
                (
                    (index, record)
                    for index, record in matched
                    if not record.get("request_id")
                ),
                None,
            )
            if matched:
                source = unbound[1] if unbound is not None else matched[0][1]
                compiler_request = self._safe_copy(
                    source.get("compiler_request") or source.get("request") or {}
                )
                bound_attempts = sum(
                    1 for _, record in matched if record.get("request_id")
                )
                if unbound is not None:
                    merged = dict(source)
                else:
                    merged = {
                        key: self._safe_copy(source[key])
                        for key in (
                            "call_id",
                            "step_id",
                            "agent_id",
                            "assembly_observability",
                            "request_metrics",
                        )
                        if key in source
                    }
                merged.update(llm_call)
                merged["attempt"] = bound_attempts + 1
                merged["compiler_request"] = compiler_request
                merged["request_trace_match_scope"] = (
                    "aworld.standard.model_boundary.v1"
                )
                if observation is not None:
                    try:
                        match = request_trace_match(
                            observation.request_snapshot,
                            compiler_request,
                        )
                        merged["request_trace_match"] = match.exact
                        merged["request_trace_mismatch_paths"] = list(
                            match.mismatch_paths
                        )
                        merged["request_trace_mismatch_count"] = match.mismatch_count
                    except Exception:
                        merged["request_trace_match"] = None
                        merged["request_trace_mismatch_paths"] = None
                        merged["request_trace_mismatch_count"] = None
                        merged["request_trace_match_error"] = {
                            "code": "request_trace_match_failed"
                        }
                else:
                    merged["request_trace_match"] = None
                    merged["request_trace_mismatch_paths"] = None
                    merged["request_trace_mismatch_count"] = None
                if unbound is not None:
                    llm_calls[unbound[0]] = merged
                else:
                    llm_calls.append(merged)
                return
            llm_call["call_id"] = agent_call_id
            llm_call["correlation"] = {"status": "compiler_call_not_found"}
        context.append_llm_call(llm_call)

    def _begin_llm_call_record(self, **kwargs) -> None:
        """Observe request start without changing provider-call semantics."""
        try:
            self._unsafe_begin_llm_call_record(**kwargs)
        except Exception as exc:
            logger.warning(
                f"LLM request capture failed before provider call; error_type={type(exc).__name__}"
            )

    def _unsafe_finish_llm_call_record(
        self,
        *,
        context: Context | None,
        request_id: str,
        status: str,
        finished_at: float,
        response: ModelResponse | None = None,
        error_code: str | None = None,
    ) -> None:
        if context is None:
            return
        llm_calls = context.get_llm_calls()
        for index, record in enumerate(llm_calls):
            if not isinstance(record, dict) or record.get("request_id") != request_id:
                continue
            updated = dict(record)
            updated["status"] = status
            updated["finished_at"] = finished_at
            if error_code is not None:
                updated["error"] = {"code": error_code}
            else:
                updated.pop("error", None)
            if response is not None:
                usage_normalized = self._safe_copy(
                    getattr(response, "usage", None) or {}
                )
                updated["provider_request_id"] = getattr(
                    response, "provider_request_id", None
                )
                updated["response"] = {
                    "id": getattr(response, "id", None),
                    "message": self._safe_copy(getattr(response, "message", None)),
                    "finish_reason": getattr(response, "finish_reason", None),
                }
                updated["usage_normalized"] = usage_normalized
                updated["usage_raw"] = self._safe_copy(
                    getattr(response, "raw_usage", None) or usage_normalized
                )
            llm_calls[index] = updated
            return

    def _finish_llm_call_record(self, **kwargs) -> None:
        """Observe request completion without replacing success or primary failure."""
        try:
            self._unsafe_finish_llm_call_record(**kwargs)
        except Exception as exc:
            logger.warning(
                f"LLM request capture failed at completion; error_type={type(exc).__name__}"
            )

    def _apply_updated_output(self, response: ModelResponse, updated_output: Any, *, sync_mode: bool = False) -> ModelResponse:
        if updated_output is None:
            return response

        if hasattr(updated_output, "content") and not isinstance(updated_output, dict):
            logger.info(f"AFTER_LLM_CALL hook replaced response object{' (sync)' if sync_mode else ''}")
            return updated_output

        if not isinstance(updated_output, dict):
            return response

        if 'content' in updated_output:
            response.content = updated_output['content']
            if isinstance(getattr(response, "message", None), dict):
                response.message["content"] = updated_output["content"]

        if 'token_usage' in updated_output:
            response.usage = updated_output['token_usage']
        if 'usage' in updated_output:
            response.usage = updated_output['usage']
        if 'raw_usage' in updated_output:
            response.raw_usage = updated_output['raw_usage']

        for key, value in updated_output.items():
            if key == 'token_usage':
                continue
            if hasattr(response, key):
                setattr(response, key, value)

        logger.info(f"AFTER_LLM_CALL hook modified response fields{' (sync)' if sync_mode else ''}")
        return response

    async def acompletion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          context: Context = None,
                          **kwargs) -> ModelResponse:
        """Asynchronously call model to generate response.

        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            context: runtime context.
            **kwargs: Other parameters.

        Returns:
            ModelResponse: Unified model response object.
        """
        # Call provider's acompletion method directly
        agent_call_id = _resolve_context_call_id(kwargs)
        start_ms = time.time()
        request_id = LLMModel._generate_llm_request_id()
        # `context` is optional in some call sites (e.g. background summary). We should
        # still be able to call the model and rely on trace/log auto-resolution.
        context_task_id = context.task_id if context else None
        context_trace_id = context.trace_id if context else None
        log_params = {
            "task_id": context_task_id,
            "request_id": request_id,
        }
        kwargs["llm_request_id"] = request_id
        log_llm_record("INPUT", self.provider.model_name, messages, log_params, context_trace_id)

        if context:
            try:
                messages = await self._apply_before_llm_hooks(
                    context=context,
                    request_id=request_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(f"BEFORE_LLM_CALL hook execution failed: {exc}")

        messages = self._finalize_context_messages_for_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=start_ms,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        context_rollout, provider_candidate = self._prepare_context_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=start_ms,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        self._begin_llm_call_record(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            started_at=start_ms,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
            context_rollout=context_rollout,
            provider_invoked=provider_candidate is None,
        )
        if provider_candidate is not None:
            kwargs[AWORLD_PROVIDER_CANDIDATE_KWARG] = provider_candidate
        try:
            resp = await self.provider.acompletion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                context=context,
                **kwargs
            )
            if self.llm_response_parser:
                response_parse_args = kwargs.get("response_parse_args") or {}
                response_parse_args["tools"] = kwargs.get("tools")
                resp = await self.llm_response_parser.parse(resp, **response_parse_args)

            log_params["time_cost"] = round(time.time() - start_ms, 3)
            log_llm_record("OUTPUT", self.provider.model_name, resp, log_params, context_trace_id)

            # Hooks V2: 触发 AFTER_LLM_CALL hook 并消费 updated_output
            if context:
                try:
                    from aworld.runners.hook.hooks import HookPoint
                    from aworld.runners.hook.utils import run_hooks

                    after_llm_call_payload = {
                        'event': 'after_llm_call',
                        'model_name': self.provider.model_name,
                        'provider_name': self.provider_name,
                        'request_id': request_id,
                        'time_cost': log_params["time_cost"],
                        'response_content': resp.content if resp else None,
                        'token_usage': getattr(resp, 'token_usage', None),
                        'status': 'success',
                        'timestamp': time.time()
                    }

                    after_hook_events = []
                    async for hook_event in run_hooks(
                        context=context,
                        hook_point=HookPoint.AFTER_LLM_CALL,
                        hook_from='llm_model',
                        payload=after_llm_call_payload,
                        workspace_path=getattr(context, 'workspace_path', None)
                    ):
                        after_hook_events.append(hook_event)

                    # Apply updated_output from hooks if present (chain all modifications)
                    for hook_event in after_hook_events:
                        if hook_event and hasattr(hook_event, 'headers'):
                            updated_output = hook_event.headers.get('updated_output')
                            if updated_output:
                                # Update resp with modified output
                                # Accept either complete response object or dict with specific fields
                                resp = self._apply_updated_output(resp, updated_output)
                                # Continue to next hook to allow chaining
                except Exception as e:
                    logger.warning(f"AFTER_LLM_CALL hook execution failed: {e}")

            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="success",
                response=resp,
                finished_at=time.time(),
            )
            return resp
        except CandidateRequestNotEnforceable as exc:
            self._mark_context_rollout_blocked(
                context=context,
                request_id=request_id,
                reason_code=exc.reason_code,
            )
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="blocked_before_provider",
                finished_at=time.time(),
                error_code=exc.code,
            )
            raise
        except asyncio.CancelledError:
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="cancelled",
                finished_at=time.time(),
                error_code="provider_call_cancelled",
            )
            raise
        except AttributeError as e:
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="failed",
                finished_at=time.time(),
                error_code="provider_call_failed",
            )
            logger.error(f"Provider {self.provider_name} does not support acompletion: {e}")
            raise NotImplementedError(f"Provider {self.provider_name} does not support async completion") from e
        except (ConnectionError, TimeoutError) as e:
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="failed",
                finished_at=time.time(),
                error_code="provider_call_failed",
            )
            logger.error(f"Network error calling {self.provider_name}: {e}")
            raise ConnectionError(f"Failed to connect to {self.provider_name} API") from e
        except Exception as e:
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="failed",
                finished_at=time.time(),
                error_code="provider_call_failed",
            )
            logger.error(f"Unexpected error calling model {self.provider_name}: {traceback.format_exc()}")
            logger.debug(
                "Failed request details redacted: "
                f"message_count={len(messages) if isinstance(messages, list) else None}, "
                f"kwarg_keys={sorted(str(key) for key in kwargs)}"
            )
            raise RuntimeError(f"Model call failed: {str(e)}") from e
        except BaseException as exc:
            cancelled = isinstance(
                exc, (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt)
            )
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="cancelled" if cancelled else "failed",
                finished_at=time.time(),
                error_code=(
                    "provider_call_cancelled" if cancelled else "provider_call_failed"
                ),
            )
            raise

    def completion(self,
                   messages: List[Dict[str, str]],
                   temperature: float = 0.0,
                   max_tokens: int = None,
                   stop: List[str] = None,
                   context: Context = None,
                   **kwargs) -> ModelResponse:
        """Synchronously call model to generate response.

        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            context: runtime context.
            **kwargs: Other parameters.

        Returns:
            ModelResponse: Unified model response object.
        """
        # Call provider's completion method directly
        agent_call_id = _resolve_context_call_id(kwargs)
        start_ms = time.time()
        request_id = LLMModel._generate_llm_request_id()
        context_task_id = context.task_id if context else None
        context_trace_id = context.trace_id if context else None
        log_params = {
            "task_id": context_task_id,
            "request_id": request_id,
        }
        kwargs["llm_request_id"] = request_id
        log_llm_record("INPUT", self.provider.model_name, messages, log_params, context_trace_id)

        if context:
            try:
                messages = sync_exec(
                    self._apply_before_llm_hooks,
                    context=context,
                    request_id=request_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(f"BEFORE_LLM_CALL hook execution failed: {exc}")

        messages = self._finalize_context_messages_for_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=start_ms,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        context_rollout, provider_candidate = self._prepare_context_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=start_ms,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        self._begin_llm_call_record(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            started_at=start_ms,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
            context_rollout=context_rollout,
            provider_invoked=provider_candidate is None,
        )
        if provider_candidate is not None:
            kwargs[AWORLD_PROVIDER_CANDIDATE_KWARG] = provider_candidate
        try:
            resp = self.provider.completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                context=context,
                **kwargs
            )
            if self.llm_response_parser:
                response_parse_args = kwargs.get("response_parse_args") or {}
                resp = sync_exec(self.llm_response_parser.parse, resp, **response_parse_args)
        except BaseException as exc:
            if isinstance(exc, CandidateRequestNotEnforceable):
                self._mark_context_rollout_blocked(
                    context=context,
                    request_id=request_id,
                    reason_code=exc.reason_code,
                )
                self._finish_llm_call_record(
                    context=context,
                    request_id=request_id,
                    status="blocked_before_provider",
                    finished_at=time.time(),
                    error_code=exc.code,
                )
                raise
            cancelled = isinstance(
                exc, (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt)
            )
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status="cancelled" if cancelled else "failed",
                finished_at=time.time(),
                error_code=(
                    "provider_call_cancelled" if cancelled else "provider_call_failed"
                ),
            )
            raise

        log_params["time_cost"] = round(time.time() - start_ms, 3)
        log_llm_record("OUTPUT", self.provider.model_name, resp, log_params, context_trace_id)

        # Hooks V2: 触发 AFTER_LLM_CALL hook (同步版本)
        if context:
            try:
                from aworld.runners.hook.hooks import HookPoint
                from aworld.runners.hook.utils import run_hooks

                after_llm_call_payload = {
                    'event': 'after_llm_call',
                    'model_name': self.provider.model_name,
                    'provider_name': self.provider_name,
                    'request_id': request_id,
                    'time_cost': log_params["time_cost"],
                    'response_content': resp.content if resp else None,
                    'token_usage': getattr(resp, 'token_usage', None),
                    'status': 'success',
                    'timestamp': time.time()
                }

                # 同步执行 async hooks 并消费 updated_output
                async def _run_after_hooks():
                    nonlocal resp
                    after_hook_events = []
                    async for hook_event in run_hooks(
                        context=context,
                        hook_point=HookPoint.AFTER_LLM_CALL,
                        hook_from='llm_model',
                        payload=after_llm_call_payload,
                        workspace_path=getattr(context, 'workspace_path', None)
                    ):
                        after_hook_events.append(hook_event)

                    # Apply updated_output from hooks if present (chain all modifications)
                    for hook_event in after_hook_events:
                        if hook_event and hasattr(hook_event, 'headers'):
                            updated_output = hook_event.headers.get('updated_output')
                            if updated_output:
                                resp = self._apply_updated_output(resp, updated_output, sync_mode=True)

                sync_exec(_run_after_hooks)
            except Exception as e:
                logger.warning(f"AFTER_LLM_CALL hook execution failed: {e}")

        self._finish_llm_call_record(
            context=context,
            request_id=request_id,
            status="success",
            response=resp,
            finished_at=time.time(),
        )
        return resp

    def stream_completion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          context: Context = None,
                          **kwargs) -> Generator[ModelResponse, None, None]:
        """Synchronously call model to generate streaming response.

        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            Generator yielding ModelResponse chunks.
        """
        agent_call_id = _resolve_context_call_id(kwargs)
        start_ms = time.time()
        request_id = LLMModel._generate_llm_request_id()
        context_task_id = context.task_id if context else None
        context_trace_id = context.trace_id if context else None
        log_params = {
            "task_id": context_task_id,
            "request_id": request_id,
        }
        kwargs["llm_request_id"] = request_id
        log_llm_record("INPUT", self.provider.model_name, messages, log_params, context_trace_id)
        stream_started_at = start_ms

        if context:
            try:
                messages = sync_exec(
                    self._apply_before_llm_hooks,
                    context=context,
                    request_id=request_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(f"BEFORE_LLM_CALL hook execution failed: {exc}")

        final_chunk = None
        record_chunk = None
        terminal_status = "success"
        terminal_error = None
        messages = self._finalize_context_messages_for_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=stream_started_at,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        context_rollout, provider_candidate = self._prepare_context_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=stream_started_at,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        self._begin_llm_call_record(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            started_at=stream_started_at,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
            context_rollout=context_rollout,
            provider_invoked=provider_candidate is None,
        )
        if provider_candidate is not None:
            kwargs[AWORLD_PROVIDER_CANDIDATE_KWARG] = provider_candidate
        try:
            for chunk in self.provider.stream_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                context=context,
                **kwargs
            ):
                if self.llm_response_parser:
                    response_parse_args = kwargs.get("response_parse_args") or {}
                    chunk = sync_exec(
                        self.llm_response_parser.parse_chunk,
                        chunk,
                        **response_parse_args,
                    )
                log_params["time_cost"] = round(time.time() - start_ms, 3)
                log_llm_record(
                    "CHUNK", self.provider.model_name, chunk, log_params, context_trace_id
                )
                start_ms = time.time()
                final_chunk = chunk
                record_chunk = self._capture_stream_response_record(
                    record_chunk, chunk
                )
                yield chunk
        except GeneratorExit:
            terminal_status = "cancelled"
            terminal_error = "stream_closed_early"
            raise
        except BaseException as exc:
            if isinstance(exc, CandidateRequestNotEnforceable):
                terminal_status = "blocked_before_provider"
                terminal_error = exc.code
                self._mark_context_rollout_blocked(
                    context=context,
                    request_id=request_id,
                    reason_code=exc.reason_code,
                )
            elif isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                terminal_status = "cancelled"
                terminal_error = "provider_stream_cancelled"
            else:
                terminal_status = "failed"
                terminal_error = "provider_stream_failed"
            raise
        finally:
            persisted_chunk = self._capture_stream_response_record(
                record_chunk, final_chunk
            )
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status=terminal_status,
                response=persisted_chunk,
                finished_at=time.time(),
                error_code=terminal_error,
            )

    async def astream_completion(self,
                                 messages: List[Dict[str, str]],
                                 temperature: float = 0.0,
                                 max_tokens: int = None,
                                 stop: List[str] = None,
                                 context: Context = None,
                                 **kwargs) -> AsyncGenerator[ModelResponse, None]:
        """Asynchronously call model to generate streaming response.

        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters, may include:
                - base_url: Specify model endpoint.
                - api_key: API key.
                - model_name: Model name.

        Returns:
            AsyncGenerator yielding ModelResponse chunks.
        """
        # Call provider's astream_completion method directly
        agent_call_id = _resolve_context_call_id(kwargs)
        start_ms = time.time()
        request_id = LLMModel._generate_llm_request_id()
        context_task_id = context.task_id if context else None
        context_trace_id = context.trace_id if context else None
        log_params = {
            "task_id": context_task_id,
            "request_id": request_id,
        }
        kwargs["llm_request_id"] = request_id
        log_llm_record("INPUT", self.provider.model_name, messages, log_params, context_trace_id)
        stream_started_at = start_ms

        if context:
            try:
                messages = await self._apply_before_llm_hooks(
                    context=context,
                    request_id=request_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(f"BEFORE_LLM_CALL hook execution failed: {exc}")
        final_chunk = None
        record_chunk = None
        terminal_status = "success"
        terminal_error = None
        messages = self._finalize_context_messages_for_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=stream_started_at,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        context_rollout, provider_candidate = self._prepare_context_rollout(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            started_at=stream_started_at,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
        )
        self._begin_llm_call_record(
            context=context,
            request_id=request_id,
            agent_call_id=agent_call_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            started_at=stream_started_at,
            tools=kwargs.get("tools"),
            model_name=kwargs.get("model_name") or kwargs.get("model"),
            context_rollout=context_rollout,
            provider_invoked=provider_candidate is None,
        )
        if provider_candidate is not None:
            kwargs[AWORLD_PROVIDER_CANDIDATE_KWARG] = provider_candidate
        try:
            async for chunk in self.provider.astream_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stop=stop,
                    context=context,
                    **kwargs
            ):
                if self.llm_response_parser:
                    response_parse_args = kwargs.get("response_parse_args") or {}
                    chunk = await self.llm_response_parser.parse_chunk(
                        chunk, **response_parse_args
                    )
                log_params["time_cost"] = round(time.time() - start_ms, 3)
                log_llm_record(
                    "CHUNK", self.provider.model_name, chunk, log_params, context_trace_id
                )
                start_ms = time.time()
                final_chunk = chunk
                record_chunk = self._capture_stream_response_record(
                    record_chunk, chunk
                )
                yield chunk
        except GeneratorExit:
            terminal_status = "cancelled"
            terminal_error = "stream_closed_early"
            raise
        except BaseException as exc:
            if isinstance(exc, CandidateRequestNotEnforceable):
                terminal_status = "blocked_before_provider"
                terminal_error = exc.code
                self._mark_context_rollout_blocked(
                    context=context,
                    request_id=request_id,
                    reason_code=exc.reason_code,
                )
            elif isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                terminal_status = "cancelled"
                terminal_error = "provider_stream_cancelled"
            else:
                terminal_status = "failed"
                terminal_error = "provider_stream_failed"
            raise
        finally:
            persisted_chunk = self._capture_stream_response_record(
                record_chunk, final_chunk
            )
            self._finish_llm_call_record(
                context=context,
                request_id=request_id,
                status=terminal_status,
                response=persisted_chunk,
                finished_at=time.time(),
                error_code=terminal_error,
            )

    def speech_to_text(self,
                       audio_file: str,
                       language: str = None,
                       prompt: str = None,
                       **kwargs) -> ModelResponse:
        """Convert speech to text.

        Args:
            audio_file: Path to audio file or file object.
            language: Audio language, optional.
            prompt: Transcription prompt, optional.
            **kwargs: Other parameters.

        Returns:
            ModelResponse: Unified model response object, with content field containing the transcription result.

        Raises:
            LLMResponseError: When LLM response error occurs.
            NotImplementedError: When provider does not support speech to text conversion.
        """
        return self.provider.speech_to_text(
            audio_file=audio_file,
            language=language,
            prompt=prompt,
            **kwargs
        )

    async def aspeech_to_text(self,
                              audio_file: str,
                              language: str = None,
                              prompt: str = None,
                              **kwargs) -> ModelResponse:
        """Asynchronously convert speech to text.

        Args:
            audio_file: Path to audio file or file object.
            language: Audio language, optional.
            prompt: Transcription prompt, optional.
            **kwargs: Other parameters.

        Returns:
            ModelResponse: Unified model response object, with content field containing the transcription result.

        Raises:
            LLMResponseError: When LLM response error occurs.
            NotImplementedError: When provider does not support speech to text conversion.
        """
        return await self.provider.aspeech_to_text(
            audio_file=audio_file,
            language=language,
            prompt=prompt,
            **kwargs
        )

    def apply_chat_template(self, messages: List[Dict[str, str]]) -> List[int]:
        """Apply the chat template to the messages.

        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].

        Returns:
            List[int]: Tokenized message list.
        """
        return self.provider.apply_chat_template(messages)


def _match_video_registry(model_name: str) -> Optional[str]:
    """Return the video provider name for *model_name* using VIDEO_MODEL_REGISTRY.

    Each entry in VIDEO_MODEL_REGISTRY is a ``(pattern, provider_name)`` tuple.
    Patterns are matched with :func:`re.match` against *model_name* (regex).
    Entries are evaluated in order and the first match wins.

    Args:
        model_name: The model identifier to look up.

    Returns:
        Matched provider name, or ``None`` if no entry matches.
    """
    for pattern, provider_name in VIDEO_MODEL_REGISTRY:
        if re.match(pattern, model_name):
            return provider_name
    return None


def register_llm_provider(provider: str, provider_class: type):
    """Register a custom LLM provider.

    Args:
        provider: Provider name.
        provider_class: Provider class, must be a subclass of LLMProviderBase.
    """
    if not issubclass(provider_class, LLMProviderBase):
        raise TypeError("provider_class must be a subclass of LLMProviderBase")
    PROVIDER_CLASSES[provider] = provider_class


def register_video_provider(
    provider: str,
    provider_class: type,
    model_patterns: Optional[List[str]] = None,
    endpoint_patterns: Optional[List[str]] = None,
):
    """Register a video generation provider and optionally bind model/endpoint patterns.

    This is the extension point for adding new video providers (e.g. Doubao,
    Google Veo direct, etc.) without touching the core routing tables.

    Args:
        provider: Unique provider name, e.g. ``"doubao_video"``.
        provider_class: Class that inherits from
            :class:`~aworld.core.video_gen_provider.VideoGenProviderBase`.
        model_patterns: List of model-name patterns to map to this provider.
            Patterns starting with ``^`` are treated as regular expressions;
            others are used as prefix strings.  New patterns are prepended to
            VIDEO_MODEL_REGISTRY so they take priority over existing entries.
        endpoint_patterns: List of base-URL substrings that identify this
            provider (e.g. ``["ark.cn-beijing.volces.com"]``).  Added to
            ENDPOINT_PATTERNS under *provider*.

    Raises:
        TypeError: When *provider_class* is not a subclass of
            VideoGenProviderBase.

    Example::

        from aworld.models.llm import register_video_provider
        from aworld.models.doubao_video_provider import DoubaoVideoProvider

        register_video_provider(
            provider="doubao_video",
            provider_class=DoubaoVideoProvider,
            model_patterns=[r"^doubao-video-", r"^seedance-"],
            endpoint_patterns=["ark.cn-beijing.volces.com"],
        )
    """
    if not issubclass(provider_class, VideoGenProviderBase):
        raise TypeError("provider_class must be a subclass of VideoGenProviderBase")

    VIDEO_PROVIDER_CLASSES[provider] = provider_class

    if model_patterns:
        # Prepend so newly registered patterns take priority
        for pattern in reversed(model_patterns):
            VIDEO_MODEL_REGISTRY.insert(0, (pattern, provider))

    if endpoint_patterns:
        ENDPOINT_PATTERNS[provider] = endpoint_patterns


def conf_contains_key(conf: Union[ConfigDict, AgentConfig, ModelConfig], key: str) -> bool:
    """Check if configuration contains a specific key.

    Args:
        conf: Configuration object (ConfigDict or AgentConfig).
        key: Key to check for existence.

    Returns:
        bool: True if the key exists in the configuration, False otherwise.

    Examples:
        >>> conf = AgentConfig(llm_provider="openai")
        >>> conf_contains_key(conf, "llm_provider")
        True
        >>> conf_contains_key(conf, "nonexistent_key")
        False
    """
    if not conf:
        return False
    if type(conf).__name__ == 'ModelConfig':
        return hasattr(conf, key)
    else:
        return key in conf


def get_llm_model(conf: Union[ConfigDict, ModelConfig] = None,
                  custom_provider: LLMProviderBase = None,
                  **kwargs) -> Union[LLMModel, 'ChatOpenAI']:
    """Get a unified LLM model instance.

    Args:
        conf: Agent configuration, if provided, create model based on configuration.
        custom_provider: Custom LLMProviderBase instance, if provided, use it directly.
        **kwargs: Other parameters, may include:
            - base_url: Specify model endpoint.
            - api_key: API key.
            - model_name: Model name.
            - temperature: Temperature parameter.

    Returns:
        Unified model interface.
    """
    # Create and return LLMModel instance directly
    llm_provider = conf.llm_provider if conf_contains_key(
        conf, "llm_provider") else None

    if (llm_provider == "chatopenai"):
        from langchain_openai import ChatOpenAI
        conf = conf.llm_config if type(conf).__name__ == 'AgentConfig' else conf
        base_url = kwargs.get("base_url") or (
            conf.llm_base_url if conf_contains_key(conf, "llm_base_url") else None)
        model_name = kwargs.get("model_name") or (
            conf.llm_model_name if conf_contains_key(conf, "llm_model_name") else None)
        api_key = kwargs.get("api_key") or (
            conf.llm_api_key if conf_contains_key(conf, "llm_api_key") else None)

        return ChatOpenAI(
            model=model_name,
            temperature=kwargs.get("temperature", conf.llm_temperature if conf_contains_key(
                conf, "llm_temperature") else 0.0),
            base_url=base_url,
            api_key=api_key,
        )

    return LLMModel(conf=conf, custom_provider=custom_provider, **kwargs)


def call_llm_model(
        llm_model: LLMModel,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        stream: bool = False,
        **kwargs
) -> Union[ModelResponse, Generator[ModelResponse, None, None]]:
    """Convenience function to call LLM model.

    Args:
        llm_model: LLM model instance.
        messages: Message list.
        temperature: Temperature parameter.
        max_tokens: Maximum number of tokens to generate.
        stop: List of stop sequences.
        stream: Whether to return a streaming response.
        **kwargs: Other parameters.

    Returns:
        Model response or response generator.
    """
    if stream:
        return llm_model.stream_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            **kwargs
        )
    else:
        return llm_model.completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            **kwargs
        )


async def acall_llm_model(
        llm_model: LLMModel,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        stream: bool = False,
        context: Context = None,
        **kwargs
) -> ModelResponse:
    """Convenience function to asynchronously call LLM model.

    Args:
        llm_model: LLM model instance.
        messages: Message list.
        temperature: Temperature parameter.
        max_tokens: Maximum number of tokens to generate.
        stop: List of stop sequences.
        stream: Whether to return a streaming response.
        **kwargs: Other parameters.

    Returns:
        Model response or response generator.
    """
    return await llm_model.acompletion(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop,
        stream=stream,
        context=context,
        **kwargs
    )


async def acall_llm_model_stream(
        llm_model: LLMModel,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        **kwargs
) -> AsyncGenerator[ModelResponse, None]:
    # Fix: Cannot await an async generator, directly iterate over it
    stream = llm_model.astream_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            **kwargs
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await stream.aclose()


def speech_to_text(
        llm_model: LLMModel,
        audio_file: str,
        language: str = None,
        prompt: str = None,
        **kwargs
) -> ModelResponse:
    """Convenience function to convert speech to text.

    Args:
        llm_model: LLM model instance.
        audio_file: Path to audio file or file object.
        language: Audio language, optional.
        prompt: Transcription prompt, optional.
        **kwargs: Other parameters.

    Returns:
        ModelResponse: Unified model response object, with content field containing the transcription result.
    """
    if llm_model.provider_name != "openai":
        raise NotImplementedError(
            f"Speech-to-text functionality is currently only supported for OpenAI compatible provider, current provider: {llm_model.provider_name}")

    return llm_model.speech_to_text(
        audio_file=audio_file,
        language=language,
        prompt=prompt,
        **kwargs
    )


async def aspeech_to_text(
        llm_model: LLMModel,
        audio_file: str,
        language: str = None,
        prompt: str = None,
        **kwargs
) -> ModelResponse:
    """Convenience function to asynchronously convert speech to text.

    Args:
        llm_model: LLM model instance.
        audio_file: Path to audio file or file object.
        language: Audio language, optional.
        prompt: Transcription prompt, optional.
        **kwargs: Other parameters.

    Returns:
        ModelResponse: Unified model response object, with content field containing the transcription result.
    """
    if llm_model.provider_name != "openai":
        raise NotImplementedError(
            f"Speech-to-text functionality is currently only supported for OpenAI compatible provider, current provider: {llm_model.provider_name}")

    return await llm_model.aspeech_to_text(
        audio_file=audio_file,
        language=language,
        prompt=prompt,
        **kwargs
    )


def apply_chat_template(
        llm_model: LLMModel,
        messages: List[Dict[str, str]]) -> List[int]:
    """Apply the chat template to the messages.

    Args:
        llm_model: LLM model instance.
        messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].

    Returns:
        List[int]: Tokenized message list.
    """
    return llm_model.apply_chat_template(messages)
