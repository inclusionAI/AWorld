import json
import os
from typing import Any, Dict, List, Generator, AsyncGenerator

from aworld.utils import import_package
from aworld.logs.util import logger
from aworld.core.llm_provider import LLMProviderBase
from aworld.core.context.compiler import (
    CandidateRequestNotEnforceable,
    ProviderLoweringCapability,
    ProviderToolsLowering,
)
from aworld.models.model_response import ModelResponse, LLMResponseError
from aworld.models.prompt_cache import AnthropicPromptAssemblyLowerer
from aworld.models.provider_context_request import (
    PreparedProviderContextRequest,
    ProviderWireProjection,
    mark_prepared_provider_attempt,
    prepare_provider_context_request,
)


ANTHROPIC_CONTEXT_LOWERING = ProviderLoweringCapability(
    provider_name="anthropic",
    adapter_identity="aworld.provider.anthropic.messages",
    adapter_version="v1",
    request_projection="anthropic.messages.params.v1",
)


class AnthropicProvider(LLMProviderBase):
    """Anthropic provider implementation."""

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model_name: str = None,
        sync_enabled: bool = None,
        async_enabled: bool = None,
        **kwargs,
    ):
        super().__init__(
            api_key, base_url, model_name, sync_enabled, async_enabled, **kwargs
        )
        import_package("anthropic")

    def _init_provider(self):
        """Initialize Anthropic provider.

        Returns:
            Anthropic provider instance.
        """
        from anthropic import Anthropic

        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "ANTHROPIC_API_KEY"
            api_key = os.getenv(env_var, "")
            if not api_key:
                raise ValueError(
                    f"Anthropic API key not found, please set {env_var} environment variable or provide it in the parameters"
                )

        return Anthropic(api_key=api_key, base_url=self.base_url)

    def _init_async_provider(self):
        """Initialize async Anthropic provider.

        Returns:
            Async Anthropic provider instance.
        """
        from anthropic import Anthropic, AsyncAnthropic

        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "ANTHROPIC_API_KEY"
            api_key = os.getenv(env_var, "")
            if not api_key:
                raise ValueError(
                    f"Anthropic API key not found, please set {env_var} environment variable or provide it in the parameters"
                )

        return AsyncAnthropic(api_key=api_key, base_url=self.base_url)

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"claude-3-.*"]

    def context_candidate_lowering_capability(
        self,
    ) -> ProviderLoweringCapability | None:
        return ANTHROPIC_CONTEXT_LOWERING

    @staticmethod
    def _messages_api(client: Any) -> Any:
        """Resolve the official Messages API with legacy-client compatibility."""
        api = getattr(client, "messages", None)
        if api is None:
            api = getattr(client, "visited_messages", None)
        if api is None or not callable(getattr(api, "create", None)):
            raise RuntimeError("Anthropic client does not expose messages.create")
        return api

    @staticmethod
    def _anthropic_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
        function = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(function, dict):
            raise TypeError("Anthropic Tool must be a function object")
        parameters = function.get("parameters") or function.get("input_schema")
        if not isinstance(parameters, dict):
            raise TypeError("Anthropic Tool requires an input schema")
        return {
            "name": function.get("name"),
            "description": function.get("description") or "",
            "input_schema": parameters,
        }

    @staticmethod
    def _anthropic_message(message: Dict[str, Any]) -> tuple[str, Any]:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            if not isinstance(content, str):
                raise TypeError("Anthropic system messages must contain text")
            return "system", content
        if role == "tool":
            return "message", {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.get("tool_call_id"),
                        "content": "" if content is None else content,
                    }
                ],
            }
        if role not in {"user", "assistant"}:
            raise TypeError(f"Unsupported Anthropic message role: {role!r}")
        if role == "assistant" and message.get("tool_calls"):
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tool_call in message["tool_calls"]:
                function = (
                    tool_call.get("function")
                    if isinstance(tool_call, dict)
                    else getattr(tool_call, "function", None)
                )
                if not isinstance(function, dict):
                    function = {
                        "name": getattr(function, "name", None),
                        "arguments": getattr(function, "arguments", None),
                    }
                arguments = function.get("arguments") or "{}"
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": (
                            tool_call.get("id")
                            if isinstance(tool_call, dict)
                            else getattr(tool_call, "id", None)
                        ),
                        "name": function.get("name"),
                        "input": (
                            json.loads(arguments)
                            if isinstance(arguments, str)
                            else arguments
                        ),
                    }
                )
            content = blocks
        return "message", {"role": role, "content": content}

    def _lower_context_request(
        self,
        standard: Dict[str, Any],
        request_kwargs: Dict[str, Any],
        stream: bool,
    ) -> ProviderWireProjection:
        anthropic_messages = []
        system_parts = []
        message_occurrences = []
        for message in standard["messages"]:
            destination, projected = self._anthropic_message(message)
            message_occurrences.append(projected)
            if destination == "system":
                system_parts.append(projected)
            else:
                anthropic_messages.append(projected)
        tools = standard["tools"]
        tool_occurrences = (
            None if tools is None else [self._anthropic_tool(tool) for tool in tools]
        )
        params = standard["params"]
        provider_kwargs = dict(request_kwargs)
        provider_kwargs.pop("context", None)
        provider_kwargs.pop("llm_request_id", None)
        if tools is None:
            provider_kwargs.pop("tools", None)
        else:
            provider_kwargs["tools"] = tools
        if stream:
            provider_kwargs["stream"] = True
        payload = self.get_anthropic_params(
            anthropic_messages,
            "\n\n".join(system_parts) if system_parts else None,
            params["temperature"],
            params["max_tokens"],
            params["stop"],
            **provider_kwargs,
        )
        if tools is not None:
            payload["tools"] = tool_occurrences
        return ProviderWireProjection(
            payload=payload,
            message_occurrences=tuple(message_occurrences),
            tool_occurrences=(
                None if tool_occurrences is None else tuple(tool_occurrences)
            ),
            tools_lowering=(
                ProviderToolsLowering.NULL_TO_ABSENT
                if tools is None
                else ProviderToolsLowering.PRESERVE
            ),
        )

    def _prepare_context_request(
        self,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        stop: List[str] | None,
        kwargs: Dict[str, Any],
        *,
        stream: bool,
    ) -> PreparedProviderContextRequest:
        return prepare_provider_context_request(
            provider=self,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            kwargs=kwargs,
            stream=stream,
            lower=self._lower_context_request,
        )

    def preprocess_messages(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Preprocess messages, convert OpenAI format to Anthropic format.

        Args:
            messages: OpenAI format message list.

        Returns:
            Converted message dictionary, containing messages and system fields.
        """
        anthropic_messages = []
        system_content = None

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_content = content
            elif role == "user":
                anthropic_messages.append({"role": "user", "content": content})
            elif role == "assistant":
                anthropic_messages.append({"role": "assistant", "content": content})

        return {"messages": anthropic_messages, "system": system_content}

    def postprocess_response(self, response: Any) -> ModelResponse:
        """Process Anthropic response to unified ModelResponse.

        Args:
            response: Anthropic response object.

        Returns:
            ModelResponse object.

        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        # Check if response is empty or contains error
        if not response or (isinstance(response, dict) and response.get("error")):
            error_msg = (
                response.get("error", "Unknown error")
                if isinstance(response, dict)
                else "Empty response"
            )
            raise LLMResponseError(error_msg, self.model_name or "claude", response)

        return ModelResponse.from_anthropic_response(response)

    def postprocess_stream_response(self, chunk: Any) -> ModelResponse:
        """Process Anthropic streaming response chunk.

        Args:
            chunk: Anthropic response chunk.

        Returns:
            ModelResponse object.

        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        # Check if chunk is empty or contains error
        if not chunk or (isinstance(chunk, dict) and chunk.get("error")):
            error_msg = (
                chunk.get("error", "Unknown error")
                if isinstance(chunk, dict)
                else "Empty response"
            )
            raise LLMResponseError(error_msg, self.model_name or "claude", chunk)

        return ModelResponse.from_anthropic_stream_chunk(chunk)

    def completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        **kwargs,
    ) -> ModelResponse:
        """Synchronously call Anthropic to generate response.

        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse object.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Make sure 'sync_enabled' parameter is set to True in initialization."
            )

        try:
            prepared = self._prepare_context_request(
                messages, temperature, max_tokens, stop, kwargs, stream=False
            )
            mark_prepared_provider_attempt(self, prepared)
            response = self._messages_api(self.provider).create(**prepared.payload)

            return self.postprocess_response(response)
        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            logger.warn(f"Error in Anthropic completion: {e}")
            raise LLMResponseError(
                str(e), kwargs.get("model_name", self.model_name or "claude")
            )

    def stream_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        **kwargs,
    ) -> Generator[ModelResponse, None, None]:
        """Synchronously call Anthropic to generate streaming response.

        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            Generator yielding ModelResponse chunks.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Make sure 'sync_enabled' parameter is set to True in initialization."
            )

        try:
            prepared = self._prepare_context_request(
                messages, temperature, max_tokens, stop, kwargs, stream=True
            )
            mark_prepared_provider_attempt(self, prepared)
            response_stream = self._messages_api(self.provider).create(
                **prepared.payload
            )

            for chunk in response_stream:
                if not chunk:
                    continue

                yield self.postprocess_stream_response(chunk)

        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            logger.warn(f"Error in Anthropic stream_completion: {e}")
            raise LLMResponseError(
                str(e), kwargs.get("model_name", self.model_name or "claude")
            )

    async def astream_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        **kwargs,
    ) -> AsyncGenerator[ModelResponse, None]:
        """Asynchronously call Anthropic to generate streaming response.

        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            AsyncGenerator yielding ModelResponse chunks.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Make sure 'async_enabled' parameter is set to True in initialization."
            )

        try:
            prepared = self._prepare_context_request(
                messages, temperature, max_tokens, stop, kwargs, stream=True
            )
            mark_prepared_provider_attempt(self, prepared)
            response_stream = await self._messages_api(self.async_provider).create(
                **prepared.payload
            )

            async for chunk in response_stream:
                if not chunk:
                    continue

                yield self.postprocess_stream_response(chunk)

        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            logger.warn(f"Error in Anthropic astream_completion: {e}")
            raise LLMResponseError(
                str(e), kwargs.get("model_name", self.model_name or "claude")
            )

    async def acompletion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        **kwargs,
    ) -> ModelResponse:
        """Asynchronously call Anthropic to generate response.

        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse object.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Make sure 'async_enabled' parameter is set to True in initialization."
            )

        try:
            prepared = self._prepare_context_request(
                messages, temperature, max_tokens, stop, kwargs, stream=False
            )
            mark_prepared_provider_attempt(self, prepared)
            response = await self._messages_api(self.async_provider).create(
                **prepared.payload
            )

            return self.postprocess_response(response)
        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            logger.warn(f"Error in Anthropic acompletion: {e}")
            raise LLMResponseError(
                str(e), kwargs.get("model_name", self.model_name or "claude")
            )

    def get_anthropic_params(
        self,
        messages: List[Dict[str, str]],
        system: str = None,
        temperature: float = 0.0,
        max_tokens: int = None,
        stop: List[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        prompt_assembly_plan = kwargs.pop("prompt_assembly_plan", None)
        provider_native_prompt_cache = bool(
            kwargs.pop("provider_native_prompt_cache", False)
        )
        lowered_request_kwargs = {}
        if prompt_assembly_plan is not None:
            lowered = AnthropicPromptAssemblyLowerer().lower(
                plan=prompt_assembly_plan,
                request_kwargs={},
                enable_native_cache=provider_native_prompt_cache,
            )
            lowered_messages = self.preprocess_messages(lowered.messages)
            messages = lowered_messages["messages"]
            system = lowered_messages["system"]
            lowered_request_kwargs = lowered.request_kwargs

        if "tools" in kwargs:
            openai_tools = kwargs["tools"]
            claude_tools = []

            for tool in openai_tools:
                claude_tools.append(self._anthropic_tool(tool))

            kwargs["tools"] = claude_tools

        anthropic_params = {
            "model": kwargs.get("model_name", self.model_name or ""),
            "messages": messages,
            "system": system,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
            "stop_sequences": stop,
        }

        if "tools" in kwargs and kwargs["tools"]:
            anthropic_params["tools"] = kwargs["tools"]
            anthropic_params["tool_choice"] = kwargs.get("tool_choice", "auto")

        anthropic_params.update(lowered_request_kwargs)
        for param in ["top_p", "top_k", "metadata", "stream"]:
            if param in kwargs:
                anthropic_params[param] = kwargs[param]

        return anthropic_params
