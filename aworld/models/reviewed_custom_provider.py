"""Framework-owned boundary for custom standard-JSON chat transports."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Generator

from aworld.core.context.compiler import (
    ProviderLoweringCapability,
    ProviderToolsLowering,
)
from aworld.core.llm_provider import LLMProviderBase
from aworld.models.model_response import ModelResponse
from aworld.models.provider_context_request import (
    ProviderWireProjection,
    mark_prepared_provider_attempt,
    prepare_provider_context_request,
)


REVIEWED_CUSTOM_CONTEXT_LOWERING = ProviderLoweringCapability(
    provider_name="custom",
    adapter_identity="aworld.provider.custom.standard_chat",
    adapter_version="v1",
    request_projection="aworld.standard-chat.params.v1",
)


class ReviewedCustomChatProvider(LLMProviderBase):
    """Exact framework adapter around an already provider-bound transport.

    The injected transport receives the immutable standard chat parameter map
    and must send that map directly. Arbitrary ``LLMProviderBase`` subclasses
    remain fail-closed; this wrapper is the supported custom-provider path.
    """

    def __init__(self, *, transport: Any, model_name: str, **kwargs):
        if transport is None:
            raise ValueError("Reviewed custom provider requires a transport")
        kwargs["transport"] = transport
        super().__init__(model_name=model_name, **kwargs)

    def _init_provider(self):
        return self.kwargs["transport"]

    def _init_async_provider(self):
        return self.kwargs["transport"]

    def context_candidate_lowering_capability(
        self,
    ) -> ProviderLoweringCapability | None:
        return REVIEWED_CUSTOM_CONTEXT_LOWERING

    def postprocess_response(self, response: Any) -> ModelResponse:
        if not isinstance(response, ModelResponse):
            raise TypeError("custom transport must return ModelResponse")
        return response

    @staticmethod
    def _lower(
        model_name: str,
        standard: dict[str, Any],
        request_kwargs: dict[str, Any],
        stream: bool,
    ) -> ProviderWireProjection:
        params = standard["params"]
        payload = {
            "model": model_name,
            "messages": standard["messages"],
            "temperature": params["temperature"],
            "max_tokens": params["max_tokens"],
            "stop": params["stop"],
        }
        tools = standard["tools"]
        if tools is not None:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
        for key in (
            "frequency_penalty",
            "logit_bias",
            "logprobs",
            "presence_penalty",
            "response_format",
            "seed",
            "top_logprobs",
            "top_p",
            "tool_choice",
            "user",
        ):
            if key in request_kwargs:
                payload[key] = request_kwargs[key]
        return ProviderWireProjection(
            payload=payload,
            message_occurrences=tuple(standard["messages"]),
            tool_occurrences=None if tools is None else tuple(tools),
            tools_lowering=(
                ProviderToolsLowering.NULL_TO_ABSENT
                if tools is None
                else ProviderToolsLowering.PRESERVE
            ),
        )

    def _prepare(self, messages, temperature, max_tokens, stop, kwargs, stream):
        return prepare_provider_context_request(
            provider=self,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            kwargs=kwargs,
            stream=stream,
            lower=lambda standard, request_kwargs, is_stream: self._lower(
                self.model_name, standard, request_kwargs, is_stream
            ),
        )

    def completion(
        self,
        messages,
        temperature=0.0,
        max_tokens=None,
        stop=None,
        **kwargs,
    ) -> ModelResponse:
        prepared = self._prepare(messages, temperature, max_tokens, stop, kwargs, False)
        mark_prepared_provider_attempt(self, prepared)
        return self.postprocess_response(self.provider.completion(prepared.payload))

    async def acompletion(
        self,
        messages,
        temperature=0.0,
        max_tokens=None,
        stop=None,
        **kwargs,
    ) -> ModelResponse:
        prepared = self._prepare(messages, temperature, max_tokens, stop, kwargs, False)
        mark_prepared_provider_attempt(self, prepared)
        return self.postprocess_response(
            await self.async_provider.acompletion(prepared.payload)
        )

    def stream_completion(
        self,
        messages,
        temperature=0.0,
        max_tokens=None,
        stop=None,
        **kwargs,
    ) -> Generator[ModelResponse, None, None]:
        prepared = self._prepare(messages, temperature, max_tokens, stop, kwargs, True)
        mark_prepared_provider_attempt(self, prepared)
        for response in self.provider.stream_completion(prepared.payload):
            yield self.postprocess_response(response)

    async def astream_completion(
        self,
        messages,
        temperature=0.0,
        max_tokens=None,
        stop=None,
        **kwargs,
    ) -> AsyncGenerator[ModelResponse, None]:
        prepared = self._prepare(messages, temperature, max_tokens, stop, kwargs, True)
        mark_prepared_provider_attempt(self, prepared)
        async for response in self.async_provider.astream_completion(prepared.payload):
            yield self.postprocess_response(response)


__all__ = [
    "REVIEWED_CUSTOM_CONTEXT_LOWERING",
    "ReviewedCustomChatProvider",
]
