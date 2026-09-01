import json
import hashlib
import os
import socket
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Generator, AsyncGenerator, Tuple, Optional

import httpx
from openai import OpenAI, AsyncOpenAI
from openai import (
    APIError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
    ConflictError,
    APIStatusError,
    OpenAIError,
)

from aworld.config.conf import ClientType
from aworld.core.llm_provider import LLMProviderBase
from aworld.core.context.compiler import (
    AWORLD_PROVIDER_CANDIDATE_KWARG,
    CandidateRequestNotEnforceable,
    ProviderCandidateEnvelope,
    ProviderLoweringCapability,
    ProviderLoweringReceipt,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    SerializedPrefixEvidence,
    build_cache_identity,
    canonical_json_bytes,
)
from aworld.logs.util import logger, log_llm_record
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.openai_message_sanitizer import sanitize_openai_messages
from aworld.models.model_response import ModelResponse, LLMResponseError
from aworld.models.prompt_cache import OpenAIPromptAssemblyLowerer


@dataclass(frozen=True, slots=True)
class _PreparedOpenAIRequest:
    params: Dict[str, Any]
    serialized_body: bytes | None = None


OPENAI_CONTEXT_LOWERING = ProviderLoweringCapability(
    provider_name="openai",
    adapter_identity="aworld.provider.openai.chat_completions",
    adapter_version="v2",
    request_projection="openai.chat.completions.params.v1",
)


class OpenAIProvider(LLMProviderBase):
    """OpenAI provider implementation.
    """

    def _build_tcp_keepalive_socket_options(self) -> Optional[List[Tuple[int, int, int]]]:
        """Build TCP keepalive socket options for httpx transports."""
        if not self.kwargs.get("tcp_keepalive", True):
            return None

        options: List[Tuple[int, int, int]] = [
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        ]

        keepidle = int(self.kwargs.get("tcp_keepalive_idle", 30))
        keepintvl = int(self.kwargs.get("tcp_keepalive_interval", 10))
        keepcnt = int(self.kwargs.get("tcp_keepalive_count", 8))

        # Linux
        if hasattr(socket, "TCP_KEEPIDLE"):
            options.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, keepidle))
        # macOS / BSD
        elif hasattr(socket, "TCP_KEEPALIVE"):
            options.append((socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, keepidle))

        if hasattr(socket, "TCP_KEEPINTVL"):
            options.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, keepintvl))
        if hasattr(socket, "TCP_KEEPCNT"):
            options.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, keepcnt))

        return options

    def _build_httpx_client(self, timeout: float) -> httpx.Client:
        socket_options = self._build_tcp_keepalive_socket_options()
        transport = httpx.HTTPTransport(socket_options=socket_options)
        return httpx.Client(timeout=timeout, transport=transport)

    def _build_async_httpx_client(self, timeout: float) -> httpx.AsyncClient:
        socket_options = self._build_tcp_keepalive_socket_options()
        transport = httpx.AsyncHTTPTransport(socket_options=socket_options)
        return httpx.AsyncClient(timeout=timeout, transport=transport)

    def _init_provider(self):
        """Initialize OpenAI provider.
        
        Returns:
            OpenAI provider instance.
        """
        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "OPENAI_API_KEY"
            api_key = os.getenv(env_var, "")
            if not api_key:
                raise ValueError(
                    f"OpenAI API key not found, please set {env_var} environment variable or provide it in the parameters")
        base_url = self.base_url
        if not base_url:
            base_url = os.getenv("OPENAI_ENDPOINT", "https://api.openai.com/v1")

        self.is_http_provider = False
        if self.kwargs.get("client_type", ClientType.SDK) == ClientType.HTTP:
            logger.info(f"Using HTTP provider for OpenAI")
            self.http_provider = LLMHTTPHandler(
                base_url=base_url,
                api_key=api_key,
                model_name=self.model_name,
                max_retries=self.kwargs.get("max_retries", 3)
            )
            self.is_http_provider = True
            return self.http_provider
        else:
            timeout = self.kwargs.get("timeout", 600)
            http_client = self.kwargs.get("http_client") or self._build_httpx_client(timeout=timeout)
            return OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=self.kwargs.get("max_retries", 3),
                http_client=http_client,
            )

    def _init_async_provider(self):
        """Initialize async OpenAI provider.

        Returns:
            Async OpenAI provider instance.
        """
        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "OPENAI_API_KEY"
            api_key = os.getenv(env_var, "")
            if not api_key:
                raise ValueError(
                    f"OpenAI API key not found, please set {env_var} environment variable or provide it in the parameters")
        base_url = self.base_url
        if not base_url:
            base_url = os.getenv("OPENAI_ENDPOINT", "https://api.openai.com/v1")

        timeout = self.kwargs.get("timeout", 7200)
        http_client = self.kwargs.get("http_client") or self._build_async_httpx_client(timeout=timeout)
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=self.kwargs.get("max_retries", 3),
            http_client=http_client,
        )

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["gpt-4o", "gpt-4", "gpt-3.5-turbo", "o3-mini", "gpt-4o-mini", "deepseek-chat", "deepseek-reasoner",
                r"qwq-.*", r"qwen-.*"]

    def context_candidate_lowering_capability(
        self,
    ) -> ProviderLoweringCapability | None:
        return OPENAI_CONTEXT_LOWERING

    def context_model_boundary_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Run the reviewed structural normalizer before final compilation."""
        return sanitize_openai_messages(messages)

    def _prepare_chat_completion_request(
        self,
        *,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        stop: List[str] | None,
        kwargs: Dict[str, Any],
        stream: bool,
    ) -> _PreparedOpenAIRequest:
        """Lower one immutable candidate and commit its receipt before send."""
        request_kwargs = dict(kwargs) if stream else kwargs
        if stream:
            request_kwargs["stream"] = True
        envelope = request_kwargs.pop(AWORLD_PROVIDER_CANDIDATE_KWARG, None)
        if envelope is not None:
            if not isinstance(envelope, ProviderCandidateEnvelope):
                raise CandidateRequestNotEnforceable(
                    "provider_lowering_contract_invalid"
                )
            capability = self.context_candidate_lowering_capability()
            if capability != envelope.expected_lowering:
                raise CandidateRequestNotEnforceable(
                    "provider_lowering_contract_invalid"
                )
            if request_kwargs.get("prompt_assembly_plan") is not None:
                raise CandidateRequestNotEnforceable(
                    "provider_transform_after_candidate"
                )
            try:
                payload = envelope.candidate_request.thaw()
                if set(payload) != {"messages", "tools", "params"}:
                    raise ValueError("unsupported model-boundary projection")
                params = payload["params"]
                if not isinstance(params, dict) or set(params) != {
                    "temperature", "max_tokens", "stop"
                }:
                    raise ValueError("unsupported candidate parameter projection")
                if not isinstance(payload["messages"], list):
                    raise TypeError("candidate messages must be a list")
                if payload["tools"] is not None and not isinstance(
                    payload["tools"], list
                ):
                    raise TypeError("candidate tools must be a list or null")
                messages = payload["messages"]
                request_kwargs["tools"] = payload["tools"]
                temperature = params["temperature"]
                max_tokens = params["max_tokens"]
                stop = params["stop"]
            except Exception:
                raise CandidateRequestNotEnforceable(
                    "provider_candidate_schema_unsupported"
                ) from None

        try:
            processed_messages = self.preprocess_messages(messages, **request_kwargs)
            if envelope is not None and processed_messages != messages:
                raise CandidateRequestNotEnforceable(
                    "provider_transform_after_candidate"
                )
            openai_params = self.get_openai_params(
                processed_messages,
                temperature,
                max_tokens,
                stop,
                **request_kwargs,
            )
        except CandidateRequestNotEnforceable:
            raise
        except Exception:
            if envelope is not None:
                raise CandidateRequestNotEnforceable(
                    "provider_request_lowering_failed"
                ) from None
            raise
        if stream:
            openai_params["stream"] = True

        serialized_body = None
        serialized_evidence = None
        cache_identity = None
        if self.is_http_provider:
            try:
                serialized_body = canonical_json_bytes(openai_params)
                if envelope is not None and envelope.cache_material is not None:
                    material = envelope.cache_material
                    messages_bytes = canonical_json_bytes(openai_params["messages"])
                    message_anchor = b'"messages":' + messages_bytes
                    anchor_index = serialized_body.find(message_anchor)
                    if anchor_index < 0:
                        raise ValueError("serialized messages not found in request")
                    stable_messages = openai_params["messages"][
                        : material.stable_message_count
                    ]
                    stable_array = canonical_json_bytes(stable_messages)
                    stable_fragment = stable_array[:-1]
                    message_value_start = anchor_index + len(b'"messages":')
                    if not serialized_body.startswith(
                        stable_fragment, message_value_start
                    ):
                        raise ValueError("stable message prefix mismatch")
                    serialized_prefix = serialized_body[
                        : message_value_start + len(stable_fragment)
                    ]
                    serialized_evidence = SerializedPrefixEvidence.provider_wire(
                        serialized_prefix=serialized_prefix,
                        serialized_request=serialized_body,
                        provider_name="openai",
                        adapter_identity=capability.adapter_identity,
                        serialization_version="openai-canonical-json-v1",
                        request_id=envelope.candidate_request.request_id,
                    )
                    cache_identity = build_cache_identity(
                        inference_profile=material.inference_profile,
                        policy_version=material.policy_version,
                        tool_catalog_hash=material.tool_catalog_hash,
                        skill_set_hash=material.skill_set_hash,
                        serialized_prefix_evidence=serialized_evidence,
                        provider_cache_namespace=material.provider_cache_namespace,
                    )
            except Exception:
                if envelope is not None:
                    raise CandidateRequestNotEnforceable(
                        "provider_serialization_evidence_failed"
                    ) from None
                raise

        try:
            provider_request = ProviderRequestSnapshot(
                request_id=request_kwargs.get("llm_request_id"),
                provider_name="openai",
                payload=openai_params,
                capture_stage=RequestCaptureStage.PROVIDER_PREPARED,
                fidelity=ProviderRequestFidelity.PROVIDER_PREPARED,
                serialized_checksum=(
                    "sha256:" + hashlib.sha256(serialized_body).hexdigest()
                    if serialized_body is not None
                    else None
                ),
            )
        except Exception:
            if envelope is not None:
                raise CandidateRequestNotEnforceable(
                    "provider_request_not_snapshotable"
                ) from None
            provider_request = None

        if envelope is not None:
            capability = self.context_candidate_lowering_capability()
            try:
                if provider_request is None:
                    raise ValueError("provider request snapshot unavailable")
                receipt = ProviderLoweringReceipt.from_envelope(
                    envelope=envelope,
                    provider_request=provider_request,
                    lowering=capability,
                    serialized_prefix_evidence=serialized_evidence,
                    cache_identity=cache_identity,
                )
            except Exception:
                raise CandidateRequestNotEnforceable(
                    "provider_request_not_snapshotable"
                ) from None
            self.commit_context_candidate_lowering(
                context=request_kwargs.get("context"),
                envelope=envelope,
                receipt=receipt,
            )
        if provider_request is not None:
            try:
                self.commit_provider_request_capture(
                    context=request_kwargs.get("context"),
                    request_id=provider_request.request_id,
                    snapshot=provider_request,
                )
            except Exception:
                if envelope is not None:
                    raise CandidateRequestNotEnforceable(
                        "provider_request_capture_failed"
                    ) from None
                logger.warning(
                    "OpenAI provider request capture failed before send; "
                    "continuing because Context enforcement is not active"
                )
        return _PreparedOpenAIRequest(
            params=openai_params,
            serialized_body=serialized_body,
        )

    def preprocess_messages(self, messages: List[Dict[str, str]], **kwargs) -> List[Dict[str, str]]:
        """Preprocess messages, use OpenAI format directly.

        Args:
            messages: OpenAI format message list.

        Returns:
            Processed message list.
        """
        return sanitize_openai_messages(messages)

    def postprocess_response(self, response: Any) -> ModelResponse:
        """Process OpenAI response.

        Args:
            response: OpenAI response object.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if ((not isinstance(response, dict) and (not hasattr(response, 'choices') or not response.choices))
                or (isinstance(response, dict) and not response.get("choices"))):
            error_msg = ""
            if hasattr(response, 'error') and response.error and isinstance(response.error, dict):
                error_msg = response.error.get('message', '')
            elif hasattr(response, 'msg'):
                error_msg = response.msg

            logger.warning(f"API Error: {error_msg}, response is: {response}")

            raise LLMResponseError(
                error_msg if error_msg else "Unknown error",
                self.model_name or "unknown",
                response
            )

        try:
            resp = ModelResponse.from_openai_response(response)
            return resp
        except Exception as e:
            logger.error(f"postprocess_response error: {e}, traceback is {traceback.format_exc()}")
            raise LLMResponseError(f"postprocess_response error: {e}", self.model_name or "unknown", response)


    def postprocess_stream_response(self, chunk: Any) -> Tuple[ModelResponse, str]:
        """Process OpenAI streaming response chunk.

        Args:
            chunk: OpenAI response chunk.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        # Check if chunk contains error
        if hasattr(chunk, 'error') or (isinstance(chunk, dict) and chunk.get('error')):
            error_msg = chunk.error if hasattr(chunk, 'error') else chunk.get('error', 'Unknown error')
            raise LLMResponseError(
                error_msg,
                self.model_name or "unknown",
                chunk
            )

        chunk_choice = None
        if hasattr(chunk, 'choices') and chunk.choices:
            chunk_choice = chunk.choices[0]
        elif isinstance(chunk, dict) and chunk.get("choices") and chunk["choices"]:
            chunk_choice = chunk["choices"][0]
        if not chunk_choice:
            resp = ModelResponse.from_openai_stream_chunk(chunk)
            has_usage = bool(resp and any(int(resp.usage.get(key, 0) or 0) > 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")))
            if has_usage or (resp and resp.raw_usage) or (resp and resp.provider_request_id):
                logger.debug("[stream] usage-only or metadata-only chunk received")
                return resp, None
            logger.debug("[stream] skip chunk: choices is empty")
            return None, None

        try:
            finish_reason = ModelResponse._get_item_from_openai_message(chunk_choice, "finish_reason")

            # process tool calls
            if (hasattr(chunk_choice, 'delta') and chunk_choice.delta and chunk_choice.delta.tool_calls) or (
                    isinstance(chunk_choice, dict) and chunk_choice.get("delta", {}).get("tool_calls")):
                tool_calls = chunk_choice.delta.tool_calls if hasattr(chunk_choice, 'delta') else chunk_choice.get("delta", {}).get("tool_calls")

                for tool_call in tool_calls:
                    index = tool_call.index if hasattr(tool_call, 'index') else tool_call["index"]
                    func = tool_call.function if (hasattr(tool_call, 'function') and tool_call.function is not None) else None
                    if isinstance(tool_call, dict):
                        func_name = tool_call.get("function", {}).get("name")
                        func_args = tool_call.get("function", {}).get("arguments")
                    else:
                        func_name = func.name if func and hasattr(func, 'name') else None
                        func_args = func.arguments if func and hasattr(func, 'arguments') else None
                    func_args = func_args or ""  # API may send None in early chunks
                    if index >= len(self.stream_tool_buffer):
                        self.stream_tool_buffer.append({
                            "id": tool_call.id if hasattr(tool_call, 'id') else tool_call.get("id"),
                            "type": "function",
                            "function": {
                                "name": func_name,
                                "arguments": func_args
                            },
                            "extra_content": tool_call.get("extra_content") if isinstance(tool_call, dict) else getattr(tool_call, "extra_content", None)
                        })
                    else:
                        existing = self.stream_tool_buffer[index]["function"]["arguments"]
                        self.stream_tool_buffer[index]["function"]["arguments"] = (existing or "") + func_args
                processed_chunk = chunk
                if hasattr(processed_chunk, 'choices'):
                    processed_chunk.choices[0].delta.tool_calls = None
                else:
                    processed_chunk["choices"][0]["delta"]["tool_calls"] = None
                resp = ModelResponse.from_openai_stream_chunk(processed_chunk)
                # Skip this chunk only when there is no finish_reason; otherwise continue to return buffered tool_calls below
                if (not resp.content and not resp.usage.get("total_tokens", 0)) and not finish_reason:
                    logger.debug("[stream] skip chunk: no content and no usage")
                    return None, None
            if finish_reason:
                if self.stream_tool_buffer:
                    raw_usage = ModelResponse._extract_usage_payload(
                        chunk.usage if hasattr(chunk, "usage") else chunk.get("usage") if isinstance(chunk, dict) else None
                    )
                    # Extract content based on chunk type (dict vs object)
                    if isinstance(chunk, dict):
                        content = chunk['choices'][0].get('delta', {}).get('content')
                    else:
                        delta = chunk.choices[0].delta
                        content = delta.content if hasattr(delta, 'content') else None

                    tool_call_chunk = {
                        "id": chunk.id if hasattr(chunk, 'id') else chunk.get("id"),
                        "model": chunk.model if hasattr(chunk, 'model') else chunk.get("model"),
                        "object": chunk.object if hasattr(chunk, 'object') else chunk.get("object"),
                        "usage": chunk.usage if hasattr(chunk, 'usage') else chunk.get("usage"),
                        "request_id": getattr(chunk, "request_id", None) if not isinstance(chunk, dict) else chunk.get("request_id"),
                        "_request_id": getattr(chunk, "_request_id", None) if not isinstance(chunk, dict) else chunk.get("_request_id"),
                        "choices": [
                            {
                                "delta": {
                                    "role": "assistant",
                                    "content": content,
                                    "tool_calls": self.stream_tool_buffer
                                }
                            }
                        ],
                        "usage": raw_usage,
                    }
                    self.stream_tool_buffer = []
                    chunk_resp = ModelResponse.from_openai_stream_chunk(tool_call_chunk)
                    logger.debug(f"[stream] finished chunk: {chunk} \n chunk_resp: {chunk_resp}, finish_reason={finish_reason}")
                    return chunk_resp, finish_reason
            resp = ModelResponse.from_openai_stream_chunk(chunk)
            logger.debug(f"[stream] chunk: {chunk} \n resp: {resp}\nfinish_reason:{finish_reason}")
            # Skip chunks with empty content and no tool_calls (unless finish_reason signals stream end)
            if (not resp.content and not resp.tool_calls) and not finish_reason:
                logger.debug("[stream] skip chunk: empty content and no tool_calls")
                return None, None
            return resp, finish_reason
        except Exception as e:
            logger.error(f"postprocess_stream_response error: {e}, traceback is {traceback.format_exc()}")
            raise LLMResponseError(f"postprocess_stream_response error: {e}", self.model_name or "unknown", chunk)

    def completion(self,
                   messages: List[Dict[str, str]],
                   temperature: float = 0.0,
                   max_tokens: int = None,
                   stop: List[str] = None,
                   **kwargs) -> ModelResponse:
        """Synchronously call OpenAI to generate response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Make sure 'sync_enabled' parameter is set to True in initialization.")

        try:
            prepared_request = self._prepare_chat_completion_request(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                kwargs=kwargs,
                stream=False,
            )
            openai_params = prepared_request.params
            if self.is_http_provider:
                response = self.http_provider.sync_call(
                    openai_params,
                    serialized_body=prepared_request.serialized_body,
                )
            else:
                response = self.provider.chat.completions.create(**openai_params)
            logger.debug(f"LLM raw response: {response}")

            if (hasattr(response, 'code') and response.code != 0) or (
                    isinstance(response, dict) and response.get("code", 0) != 0):
                error_msg = getattr(response, 'msg', 'Unknown error')
                logger.warn(f"API Error: {error_msg}")
                raise LLMResponseError(error_msg, kwargs.get("model_name", self.model_name or "unknown"), response)

            if not response:
                raise LLMResponseError("Empty response", kwargs.get("model_name", self.model_name or "unknown"))

            resp = self.postprocess_response(response)
            return resp
        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in OpenAI completion: {e}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    def stream_completion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> Generator[ModelResponse, None, None]:
        """Synchronously call OpenAI to generate streaming response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            Generator yielding ModelResponse chunks.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Make sure 'sync_enabled' parameter is set to True in initialization.")

        usage={
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0
        }

        try:
            prepared_request = self._prepare_chat_completion_request(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                kwargs=kwargs,
                stream=True,
            )
            openai_params = prepared_request.params
            if self.is_http_provider:
                response_stream = self.http_provider.sync_stream_call(
                    openai_params,
                    serialized_body=prepared_request.serialized_body,
                )
            else:
                response_stream = self.provider.chat.completions.create(**openai_params)

            for chunk in response_stream:
                logger.debug(f"LLM raw stream chunk: {chunk}")
                if not chunk:
                    continue
                resp, finish_reason = self.postprocess_stream_response(chunk)
                if resp:
                    self._accumulate_chunk_usage(usage, resp.usage)
                    yield resp
                    if finish_reason:
                        yield ModelResponse(
                            id = resp.id,
                            model = resp.model,
                            finish_reason=finish_reason,
                            usage=usage,
                            raw_usage=resp.raw_usage,
                            provider_request_id=resp.provider_request_id)

        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in stream_completion: {e}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    async def astream_completion(self,
                                 messages: List[Dict[str, str]],
                                 temperature: float = 0.0,
                                 max_tokens: int = None,
                                 stop: List[str] = None,
                                 **kwargs) -> AsyncGenerator[ModelResponse, None]:
        """Asynchronously call OpenAI to generate streaming response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            AsyncGenerator yielding ModelResponse chunks.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Make sure 'async_enabled' parameter is set to True in initialization.")

        usage = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0
        }

        try:
            prepared_request = self._prepare_chat_completion_request(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                kwargs=kwargs,
                stream=True,
            )
            openai_params = prepared_request.params
            logger.debug(f"openai_params: {openai_params}")

            if self.is_http_provider:
                async for chunk in self.http_provider.async_stream_call(
                    openai_params,
                    serialized_body=prepared_request.serialized_body,
                ):
                    logger.debug(f"LLM raw stream chunk: {chunk}")
                    if not chunk:
                        continue
                    resp, finish_reason = self.postprocess_stream_response(chunk)
                    if resp:
                        self._accumulate_chunk_usage(usage, resp.usage)
                        yield resp
                        if finish_reason:
                            yield ModelResponse(
                                id=resp.id,
                                model=resp.model,
                                finish_reason=finish_reason,
                                usage=usage,
                                raw_usage=resp.raw_usage,
                                provider_request_id=resp.provider_request_id)
            else:
                response_stream = await self.async_provider.chat.completions.create(**openai_params)
                async for chunk in response_stream:
                    if not chunk:
                        continue
                    logger.debug(f"origin chunk: {chunk}")
                    resp, finish_reason = self.postprocess_stream_response(chunk)
                    if resp:
                        self._accumulate_chunk_usage(usage, resp.usage)
                        yield resp
                        if finish_reason:
                            yield ModelResponse(
                                id=resp.id,
                                model=resp.model,
                                content="",
                                finish_reason=finish_reason,
                                usage=usage,
                                raw_usage=resp.raw_usage,
                                provider_request_id=resp.provider_request_id)

        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in astream_completion: {e} {traceback.format_exc()}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    async def acompletion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> ModelResponse:
        """Asynchronously call OpenAI to generate response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Make sure 'async_enabled' parameter is set to True in initialization.")

        try:
            prepared_request = self._prepare_chat_completion_request(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                kwargs=kwargs,
                stream=False,
            )
            openai_params = prepared_request.params
            logger.debug(f"openai_params: {json.dumps(openai_params)}")
            if self.is_http_provider:
                response = await self.http_provider.async_call(
                    openai_params,
                    serialized_body=prepared_request.serialized_body,
                )
            else:
                response = await self.async_provider.chat.completions.create(**openai_params)
            logger.debug(f"LLM raw response: {response}")

            if (hasattr(response, 'code') and response.code != 0) or (
                    isinstance(response, dict) and response.get("code", 0) != 0):
                error_msg = getattr(response, 'msg', 'Unknown error')
                logger.warn(f"API Error: {error_msg}")
                raise LLMResponseError(error_msg, kwargs.get("model_name", self.model_name or "unknown"), response)

            if not response:
                raise LLMResponseError("Empty response", kwargs.get("model_name", self.model_name or "unknown"))

            resp = self.postprocess_response(response)
            return resp
        except Exception as e:
            if isinstance(e, CandidateRequestNotEnforceable):
                raise
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in acompletion: {e}\n")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    def get_openai_params(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> Dict[str, Any]:
        prompt_assembly_plan = kwargs.pop("prompt_assembly_plan", None)
        provider_native_prompt_cache = bool(kwargs.pop("provider_native_prompt_cache", False))
        lowered_request_kwargs = {}
        if prompt_assembly_plan is not None:
            lowered = OpenAIPromptAssemblyLowerer().lower(
                plan=prompt_assembly_plan,
                request_kwargs={},
                enable_native_cache=provider_native_prompt_cache,
            )
            messages = sanitize_openai_messages(lowered.messages)
            lowered_request_kwargs = lowered.request_kwargs

        model_name = kwargs.get("model_name", self.model_name or "")
        openai_params = {
            "model": model_name,
            "messages": messages
        }

        supported_params = [
            "temperature", "max_tokens", "stop",
            "max_completion_tokens", "meta_data", "modalities", "n", "parallel_tool_calls",
            "prediction", "reasoning_effort", "service_tier", "stream_options", "web_search_options",
            "frequency_penalty", "logit_bias", "logprobs", "top_logprobs",
            "presence_penalty", "response_format", "seed", "stream", "top_p",
            "user", "function_call", "functions", "tools", "tool_choice", "metadata",
            "prompt_cache_key", "safety_identifier", "store", "verbosity", "extra_body", "model"
        ]

        llm_params = dict(self.kwargs.get("params", {}))
        llm_params.update(kwargs)
        llm_params.update(lowered_request_kwargs)
        llm_params.pop("response_parse_args", None)
        llm_params.pop("context", None)
        llm_params.update({
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stop": stop
        })
        if llm_params.get("stream"):
            stream_options = llm_params.get("stream_options")
            if stream_options is None:
                llm_params["stream_options"] = {"include_usage": True}
            elif isinstance(stream_options, dict):
                merged_stream_options = dict(stream_options)
                merged_stream_options.setdefault("include_usage", True)
                llm_params["stream_options"] = merged_stream_options
        else:
            llm_params.pop("stream_options", None)
        log_llm_record("OPENAI_PARAMS", model_name, llm_params, {"request_id": llm_params.pop("llm_request_id", None)})

        for param in llm_params:
            if param not in supported_params:
                logger.warning(f"Using unsupported openai parameter may cause exception: {param}")
            if llm_params[param] is not None:
                openai_params[param] = llm_params[param]
        return openai_params

    def speech_to_text(self,
                       audio_file: str,
                       language: str = None,
                       prompt: str = None,
                       **kwargs) -> ModelResponse:
        """Convert speech to text.

        Uses OpenAI's speech-to-text API to convert audio files to text.

        Args:
            audio_file: Path to audio file or file object.
            language: Audio language, optional.
            prompt: Transcription prompt, optional.
            **kwargs: Other parameters, may include:
                - model: Transcription model name, defaults to "whisper-1".
                - response_format: Response format, defaults to "text".
                - temperature: Sampling temperature, defaults to 0.

        Returns:
            ModelResponse: Unified model response object, with content field containing the transcription result.

        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Make sure 'sync_enabled' parameter is set to True in initialization.")

        try:
            # Prepare parameters
            transcription_params = {
                "model": kwargs.get("model", "whisper-1"),
                "response_format": kwargs.get("response_format", "text"),
                "temperature": kwargs.get("temperature", 0)
            }

            # Add optional parameters
            if language:
                transcription_params["language"] = language
            if prompt:
                transcription_params["prompt"] = prompt

            # Open file (if path is provided)
            if isinstance(audio_file, str):
                with open(audio_file, "rb") as file:
                    transcription_response = self.provider.audio.transcriptions.create(
                        file=file,
                        **transcription_params
                    )
            else:
                # If already a file object
                transcription_response = self.provider.audio.transcriptions.create(
                    file=audio_file,
                    **transcription_params
                )

            # Create ModelResponse
            return ModelResponse(
                id=f"stt-{hash(str(transcription_response)) & 0xffffffff:08x}",
                model=transcription_params["model"],
                content=transcription_response.text if hasattr(transcription_response, 'text') else str(
                    transcription_response),
                raw_response=transcription_response,
                message={
                    "role": "assistant",
                    "content": transcription_response.text if hasattr(transcription_response, 'text') else str(
                        transcription_response)
                }
            )
        except Exception as e:
            logger.warn(f"Speech-to-text error: {e}")
            raise LLMResponseError(str(e), kwargs.get("model", "whisper-1"))

    async def aspeech_to_text(self,
                              audio_file: str,
                              language: str = None,
                              prompt: str = None,
                              **kwargs) -> ModelResponse:
        """Asynchronously convert speech to text.

        Uses OpenAI's speech-to-text API to convert audio files to text.

        Args:
            audio_file: Path to audio file or file object.
            language: Audio language, optional.
            prompt: Transcription prompt, optional.
            **kwargs: Other parameters, may include:
                - model: Transcription model name, defaults to "whisper-1".
                - response_format: Response format, defaults to "text".
                - temperature: Sampling temperature, defaults to 0.

        Returns:
            ModelResponse: Unified model response object, with content field containing the transcription result.

        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Make sure 'async_enabled' parameter is set to True in initialization.")

        try:
            # Prepare parameters
            transcription_params = {
                "model": kwargs.get("model", "whisper-1"),
                "response_format": kwargs.get("response_format", "text"),
                "temperature": kwargs.get("temperature", 0)
            }

            # Add optional parameters
            if language:
                transcription_params["language"] = language
            if prompt:
                transcription_params["prompt"] = prompt

            # Open file (if path is provided)
            if isinstance(audio_file, str):
                with open(audio_file, "rb") as file:
                    transcription_response = await self.async_provider.audio.transcriptions.create(
                        file=file,
                        **transcription_params
                    )
            else:
                # If already a file object
                transcription_response = await self.async_provider.audio.transcriptions.create(
                    file=audio_file,
                    **transcription_params
                )

            # Create ModelResponse
            return ModelResponse(
                id=f"stt-{hash(str(transcription_response)) & 0xffffffff:08x}",
                model=transcription_params["model"],
                content=transcription_response.text if hasattr(transcription_response, 'text') else str(
                    transcription_response),
                raw_response=transcription_response,
                message={
                    "role": "assistant",
                    "content": transcription_response.text if hasattr(transcription_response, 'text') else str(
                        transcription_response)
                }
            )
        except Exception as e:
            logger.warn(f"Async speech-to-text error: {e}")
            raise LLMResponseError(str(e), kwargs.get("model", "whisper-1"))


class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI provider implementation.
    """

    def context_candidate_lowering_capability(
        self,
    ) -> ProviderLoweringCapability | None:
        # Azure currently uses a LangChain client with a different send
        # boundary, so inheriting the OpenAI SDK receipt would be a false claim.
        return None

    def _init_provider(self):
        """Initialize Azure OpenAI provider.

        Returns:
            Azure OpenAI provider instance.
        """
        from langchain_openai import AzureChatOpenAI

        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "AZURE_OPENAI_API_KEY"
            api_key = os.getenv(env_var, "")
            if not api_key:
                raise ValueError(
                    f"Azure OpenAI API key not found, please set {env_var} environment variable or provide it in the parameters")

        # Get API version
        api_version = self.kwargs.get("api_version", "") or os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

        # Get endpoint
        azure_endpoint = self.base_url
        if not azure_endpoint:
            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
            if not azure_endpoint:
                raise ValueError(
                    "Azure OpenAI endpoint not found, please set AZURE_OPENAI_ENDPOINT environment variable or provide it in the parameters")

        return AzureChatOpenAI(
            model=self.model_name or "gpt-4o",
            temperature=self.kwargs.get("temperature", 0.0),
            api_version=api_version,
            azure_endpoint=azure_endpoint,
            api_key=api_key
        )
