import re
import time
import uuid
import traceback
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
from aworld.models.openai_provider import OpenAIProvider, AzureOpenAIProvider
from aworld.models.anthropic_provider import AnthropicProvider
from aworld.models.ant_provider import AntProvider
from aworld.models.together_video_provider import TogetherVideoProvider
from aworld.models.ant_video_provider import AntVideoProvider
from aworld.models.kling_provider import KlingProvider
from aworld.models.kling_avatar_provider import KlingAvatarProvider
from aworld.models.model_response import ModelResponse
from aworld.core.context.base import Context
from aworld.core.model_output_parser import ModelOutputParser, BaseContentParser
from aworld.utils.common import sync_exec

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
    "image": None,  # Lazy loaded to avoid circular import
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
                        "llm_async_enabled", "llm_client_type", "llm_response_parser"]
        args = {}
        # Filter out used parameters and add remaining parameters to args
        for key, value in conf_dict.items():
            if key == "ext_config" and value is not None:
                args.update(value)
            elif key not in ignored_keys and value is not None:
                args[key] = value

        return args

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
            elif provider_class is None and self.provider_name == "image":
                from aworld.models.image_provider import ImageProvider
                provider_class = ImageProvider
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

        # Hooks V2: 触发 BEFORE_LLM_CALL hook 并消费 updated_input
        if context:
            try:
                from aworld.runners.hook.hooks import HookPoint
                from aworld.runners.hook.utils import run_hooks

                before_llm_call_payload = {
                    'event': 'before_llm_call',
                    'model_name': self.provider.model_name,
                    'provider_name': self.provider_name,
                    'messages': messages,
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                    'request_id': request_id,
                    'timestamp': time.time()
                }

                before_hook_events = []
                async for hook_event in run_hooks(
                    context=context,
                    hook_point=HookPoint.BEFORE_LLM_CALL,
                    hook_from='llm_model',
                    payload=before_llm_call_payload,
                    workspace_path=getattr(context, 'workspace_path', None)
                ):
                    before_hook_events.append(hook_event)

                # Apply updated_input from hooks if present (chain all modifications)
                for hook_event in before_hook_events:
                    if hook_event and hasattr(hook_event, 'headers'):
                        updated_input = hook_event.headers.get('updated_input')
                        if updated_input:
                            # Update messages with modified input
                            if isinstance(updated_input, list):
                                messages = updated_input
                                logger.info(f"BEFORE_LLM_CALL hook modified messages")
                            elif isinstance(updated_input, dict) and 'messages' in updated_input:
                                messages = updated_input['messages']
                                logger.info(f"BEFORE_LLM_CALL hook modified messages")
                            # Continue to next hook to allow chaining
            except Exception as e:
                logger.warning(f"BEFORE_LLM_CALL hook execution failed: {e}")

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
                                if hasattr(updated_output, 'content'):
                                    # Direct response object replacement
                                    resp = updated_output
                                    logger.info(f"AFTER_LLM_CALL hook replaced response object")
                                elif isinstance(updated_output, dict):
                                    # Partial update of response fields
                                    if 'content' in updated_output:
                                        resp.content = updated_output['content']
                                    if 'token_usage' in updated_output:
                                        resp.token_usage = updated_output['token_usage']
                                    logger.info(f"AFTER_LLM_CALL hook modified response fields")
                                # Continue to next hook to allow chaining
                except Exception as e:
                    logger.warning(f"AFTER_LLM_CALL hook execution failed: {e}")

            return resp
        except AttributeError as e:
            logger.error(f"Provider {self.provider_name} does not support acompletion: {e}")
            raise NotImplementedError(f"Provider {self.provider_name} does not support async completion") from e
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Network error calling {self.provider_name}: {e}")
            raise ConnectionError(f"Failed to connect to {self.provider_name} API") from e
        except Exception as e:
            logger.error(f"Unexpected error calling model {self.provider_name}: {traceback.format_exc()}")
            logger.debug(f"Failed request details - messages: {messages}, kwargs: {kwargs}")
            raise RuntimeError(f"Model call failed: {str(e)}") from e

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

        # Hooks V2: 触发 BEFORE_LLM_CALL hook (同步版本)
        if context:
            try:
                from aworld.runners.hook.hooks import HookPoint
                from aworld.runners.hook.utils import run_hooks

                before_llm_call_payload = {
                    'event': 'before_llm_call',
                    'model_name': self.provider.model_name,
                    'provider_name': self.provider_name,
                    'messages': messages,
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                    'request_id': request_id,
                    'timestamp': time.time()
                }

                # 同步执行 async hooks 并消费 updated_input
                async def _run_before_hooks():
                    nonlocal messages
                    before_hook_events = []
                    async for hook_event in run_hooks(
                        context=context,
                        hook_point=HookPoint.BEFORE_LLM_CALL,
                        hook_from='llm_model',
                        payload=before_llm_call_payload,
                        workspace_path=getattr(context, 'workspace_path', None)
                    ):
                        before_hook_events.append(hook_event)

                    # Apply updated_input from hooks if present (chain all modifications)
                    for hook_event in before_hook_events:
                        if hook_event and hasattr(hook_event, 'headers'):
                            updated_input = hook_event.headers.get('updated_input')
                            if updated_input:
                                # Update messages with modified input
                                if isinstance(updated_input, list):
                                    messages = updated_input
                                    logger.info(f"BEFORE_LLM_CALL hook modified messages (sync)")
                                elif isinstance(updated_input, dict) and 'messages' in updated_input:
                                    messages = updated_input['messages']
                                    logger.info(f"BEFORE_LLM_CALL hook modified messages (sync)")

                sync_exec(_run_before_hooks)
            except Exception as e:
                logger.warning(f"BEFORE_LLM_CALL hook execution failed: {e}")

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
                                # Update resp with modified output
                                if isinstance(updated_output, dict):
                                    if 'content' in updated_output:
                                        resp.content = updated_output['content']
                                        logger.info(f"AFTER_LLM_CALL hook modified response content (sync)")
                                    # Allow other fields to be updated as well
                                    for key, value in updated_output.items():
                                        if hasattr(resp, key):
                                            setattr(resp, key, value)
                                elif hasattr(updated_output, '__dict__'):
                                    # If it's an object, replace resp entirely
                                    resp = updated_output
                                    logger.info(f"AFTER_LLM_CALL hook replaced response object (sync)")

                sync_exec(_run_after_hooks)
            except Exception as e:
                logger.warning(f"AFTER_LLM_CALL hook execution failed: {e}")

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
        # Call provider's stream_completion method directly
        return self.provider.stream_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            context=context,
            **kwargs
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
                chunk = await self.llm_response_parser.parse_chunk(chunk, **response_parse_args)
            log_params["time_cost"] = round(time.time() - start_ms, 3)
            log_llm_record("CHUNK", self.provider.model_name, chunk, log_params, context_trace_id)
            start_ms = time.time()
            yield chunk

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
    async for chunk in llm_model.astream_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            **kwargs
    ):
        yield chunk


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
