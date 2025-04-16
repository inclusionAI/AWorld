import os
from typing import Any, Dict, List, Generator, AsyncGenerator
from openai import OpenAI, AsyncOpenAI
from langchain_openai import AzureChatOpenAI

from aworld.config.conf import ClientType
from aworld.models.llm_provider_base import LLMProviderBase
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import ModelResponse, LLMResponseError
from aworld.env_secrets import secrets
from aworld.logs.util import logger


class OpenAIProvider(LLMProviderBase):
    """OpenAI provider implementation.
    """

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
            )
            self.is_http_provider = True
            return self.http_provider
        else:
            return OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self.kwargs.get("timeout", 180),
                max_retries=self.kwargs.get("max_retries", 3)
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

        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.kwargs.get("timeout", 180),
            max_retries=self.kwargs.get("max_retries", 3)
        )

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["gpt-4o", "gpt-4", "gpt-3.5-turbo", "o3-mini", "gpt-4o-mini", "deepseek-chat", "deepseek-reasoner",
                r"qwq-.*", r"qwen-.*"]

    def preprocess_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Preprocess messages, use OpenAI format directly.

        Args:
            messages: OpenAI format message list.

        Returns:
            Processed message list.
        """
        for message in messages:
            if message["role"] == "assistant" and "tool_calls" in message and message["tool_calls"]:
                for tool_call in message["tool_calls"]:
                    if "function" not in tool_call and "name" in tool_call and "arguments" in tool_call:
                        tool_call["function"] = {"name": tool_call["name"], "arguments": tool_call["arguments"]}

        return messages

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

            raise LLMResponseError(
                error_msg if error_msg else "Unknown error",
                self.model_name or "unknown",
                response
            )

        return ModelResponse.from_openai_response(response)

    def postprocess_stream_response(self, chunk: Any) -> ModelResponse:
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

        return ModelResponse.from_openai_stream_chunk(chunk)

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

        processed_messages = self.preprocess_messages(messages)

        try:
            openai_params = self.get_openai_params(processed_messages, temperature, max_tokens, stop, **kwargs)
            if self.is_http_provider:
                response = self.http_provider.sync_call(openai_params)
            else:
                response = self.provider.chat.completions.create(**openai_params)

            if (hasattr(response, 'code') and response.code != 0) or (
                    isinstance(response, dict) and response.get("code", 0) != 0):
                error_msg = getattr(response, 'msg', 'Unknown error')
                logger.warn(f"API Error: {error_msg}")
                raise LLMResponseError(error_msg, kwargs.get("model_name", self.model_name or "unknown"), response)

            if not response:
                raise LLMResponseError("Empty response", kwargs.get("model_name", self.model_name or "unknown"))

            return self.postprocess_response(response)
        except Exception as e:
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

        processed_messages = self.preprocess_messages(messages)

        try:
            openai_params = self.get_openai_params(processed_messages, temperature, max_tokens, stop, **kwargs)
            openai_params["stream"] = True
            if self.is_http_provider:
                response_stream = self.http_provider.sync_stream_call(openai_params)
            else:
                response_stream = self.provider.chat.completions.create(**openai_params)

            for chunk in response_stream:
                if not chunk:
                    continue
                yield self.postprocess_stream_response(chunk)

        except Exception as e:
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

        processed_messages = self.preprocess_messages(messages)

        try:
            openai_params = self.get_openai_params(processed_messages, temperature, max_tokens, stop, **kwargs)
            openai_params["stream"] = True

            if self.is_http_provider:
                async for chunk in self.http_provider.async_stream_call(openai_params):
                    if not chunk:
                        continue
                    yield self.postprocess_stream_response(chunk)
            else:
                response_stream = await self.async_provider.chat.completions.create(**openai_params)
                async for chunk in response_stream:
                    if not chunk:
                        continue
                    yield self.postprocess_stream_response(chunk)

        except Exception as e:
            logger.warn(f"Error in astream_completion: {e}")
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

        processed_messages = self.preprocess_messages(messages)

        try:
            openai_params = self.get_openai_params(processed_messages, temperature, max_tokens, stop, **kwargs)
            if self.is_http_provider:
                response = await self.http_provider.async_call(openai_params)
            else:
                response = await self.async_provider.chat.completions.create(**openai_params)

            if (hasattr(response, 'code') and response.code != 0) or (
                    isinstance(response, dict) and response.get("code", 0) != 0):
                error_msg = getattr(response, 'msg', 'Unknown error')
                logger.warn(f"API Error: {error_msg}")
                raise LLMResponseError(error_msg, kwargs.get("model_name", self.model_name or "unknown"), response)

            if not response:
                raise LLMResponseError("Empty response", kwargs.get("model_name", self.model_name or "unknown"))

            return self.postprocess_response(response)
        except Exception as e:
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in acompletion: {e}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    def get_openai_params(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> Dict[str, Any]:
        openai_params = {
            "model": kwargs.get("model_name", self.model_name or ""),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stop": stop
        }

        supported_params = [
            "frequency_penalty", "logit_bias", "logprobs", "top_logprobs",
            "presence_penalty", "response_format", "seed", "stream", "top_p",
            "user", "function_call", "functions", "tools", "tool_choice"
        ]

        for param in supported_params:
            if param in kwargs:
                openai_params[param] = kwargs[param]

        return openai_params


class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI provider implementation.
    """

    def _init_provider(self):
        """Initialize Azure OpenAI provider.

        Returns:
            Azure OpenAI provider instance.
        """
        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "AZURE_OPENAI_API_KEY"
            api_key = os.getenv(env_var, "") or secrets.azure_openai_api_key
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
