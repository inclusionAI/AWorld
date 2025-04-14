import os
import dotenv
import json
import asyncio
from typing import Any, Dict, List, Generator, AsyncGenerator
from anthropic import Anthropic, AsyncAnthropic

from aworld.logs.util import logger
from aworld.models.llm_provider_base import LLMProviderBase
from aworld.models.model_response import ModelResponse
from aworld.env_secrets import secrets


class AnthropicProvider(LLMProviderBase):
    """Anthropic provider implementation.
    """

    def _init_provider(self):
        """Initialize Anthropic provider.

        Returns:
            Anthropic provider instance.
        """
        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "ANTHROPIC_API_KEY"
            api_key = dotenv.get_key(".env", env_var) or os.getenv(env_var, "") or secrets.claude_api_key
            if not api_key:
                raise ValueError(
                    f"Anthropic API key not found, please set {env_var} environment variable or provide it in the parameters")

        return Anthropic(
            api_key=api_key,
            base_url=self.base_url
        )

    def _init_async_provider(self):
        """Initialize async Anthropic provider.

        Returns:
            Async Anthropic provider instance.
        """
        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "ANTHROPIC_API_KEY"
            api_key = dotenv.get_key(".env", env_var) or os.getenv(env_var, "") or secrets.claude_api_key
            if not api_key:
                raise ValueError(
                    f"Anthropic API key not found, please set {env_var} environment variable or provide it in the parameters")

        return AsyncAnthropic(
            api_key=api_key,
            base_url=self.base_url
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

        return {
            "messages": anthropic_messages,
            "system": system_content
        }

    def postprocess_response(self, response: Any) -> ModelResponse:
        """Process Anthropic response to unified ModelResponse.

        Args:
            response: Anthropic response object.

        Returns:
            ModelResponse object.
        """
        return ModelResponse.from_anthropic_response(response)

    def postprocess_stream_response(self, chunk: Any) -> ModelResponse:
        """Process Anthropic streaming response chunk.

        Args:
            chunk: Anthropic response chunk.

        Returns:
            ModelResponse object.
        """
        return ModelResponse.from_anthropic_stream_chunk(chunk)

    def completion(self, 
                messages: List[Dict[str, str]], 
                temperature: float = 0.0, 
                max_tokens: int = None, 
                stop: List[str] = None, 
                **kwargs) -> ModelResponse:
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
            raise RuntimeError("Sync provider not initialized. Make sure 'sync_able' parameter is set to True in initialization.")

        try:
            processed_data = self.preprocess_messages(messages)

            if "tools" in kwargs:
                openai_tools = kwargs["tools"]
                claude_tools = []

                for tool in openai_tools:
                    if tool["type"] == "function":
                        claude_tool = {
                            "name": tool["name"],
                            "description": tool["description"],
                            "input_schema": {
                                "type": "object",
                                "properties": tool["parameters"]["properties"],
                                "required": tool["parameters"].get("required", [])
                            }
                        }
                        claude_tools.append(claude_tool)

                kwargs["tools"] = claude_tools

            anthropic_params = {
                "model": kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022"),
                "messages": processed_data["messages"],
                "system": processed_data["system"],
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
                "stop_sequences": stop,
            }

            if "tools" in kwargs and kwargs["tools"]:
                anthropic_params["tools"] = kwargs["tools"]
                anthropic_params["tool_choice"] = kwargs.get("tool_choice", "auto")

            for param in ["top_p", "top_k", "metadata", "stream"]:
                if param in kwargs:
                    anthropic_params[param] = kwargs[param]

            response = self.provider.messages.create(**anthropic_params)

            return self.postprocess_response(response)
        except Exception as e:
            logger.warn(f"Error in Anthropic completion: {e}")
            return ModelResponse.from_error(
                str(e),
                kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022")
            ) 
    
    def stream_completion(self, 
                     messages: List[Dict[str, str]], 
                     temperature: float = 0.0, 
                     max_tokens: int = None, 
                     stop: List[str] = None, 
                     **kwargs) -> Generator[ModelResponse, None, None]:
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
            raise RuntimeError("Sync provider not initialized. Make sure 'sync_able' parameter is set to True in initialization.")

        try:
            processed_data = self.preprocess_messages(messages)

            if "tools" in kwargs:
                openai_tools = kwargs["tools"]
                claude_tools = []

                for tool in openai_tools:
                    if tool["type"] == "function":
                        claude_tool = {
                            "name": tool["name"],
                            "description": tool["description"],
                            "input_schema": {
                                "type": "object",
                                "properties": tool["parameters"]["properties"],
                                "required": tool["parameters"].get("required", [])
                            }
                        }
                        claude_tools.append(claude_tool)

                kwargs["tools"] = claude_tools

            anthropic_params = {
                "model": kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022"),
                "messages": processed_data["messages"],
                "system": processed_data["system"],
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
                "stop_sequences": stop,
                "stream": True  # Enable streaming
            }

            if "tools" in kwargs and kwargs["tools"]:
                anthropic_params["tools"] = kwargs["tools"]
                anthropic_params["tool_choice"] = kwargs.get("tool_choice", "auto")

            for param in ["top_p", "top_k", "metadata"]:
                if param in kwargs:
                    anthropic_params[param] = kwargs[param]

            response_stream = self.provider.messages.create(**anthropic_params)

            for chunk in response_stream:
                if not chunk:
                    continue

                yield self.postprocess_stream_response(chunk)

        except Exception as e:
            logger.warn(f"Error in Anthropic stream_completion: {e}")
            yield ModelResponse.from_error(
                str(e),
                kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022")
            )

    async def astream_completion(self, 
                           messages: List[Dict[str, str]], 
                           temperature: float = 0.0, 
                           max_tokens: int = None, 
                           stop: List[str] = None, 
                           **kwargs) -> AsyncGenerator[ModelResponse, None]:
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
            raise RuntimeError("Async provider not initialized. Make sure 'async_able' parameter is set to True in initialization.")

        try:
            processed_data = self.preprocess_messages(messages)

            if "tools" in kwargs:
                openai_tools = kwargs["tools"]
                claude_tools = []

                for tool in openai_tools:
                    if tool["type"] == "function":
                        claude_tool = {
                            "name": tool["name"],
                            "description": tool["description"],
                            "input_schema": {
                                "type": "object",
                                "properties": tool["parameters"]["properties"],
                                "required": tool["parameters"].get("required", [])
                            }
                        }
                        claude_tools.append(claude_tool)

                kwargs["tools"] = claude_tools

            anthropic_params = {
                "model": kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022"),
                "messages": processed_data["messages"],
                "system": processed_data["system"],
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
                "stop_sequences": stop,
                "stream": True  # Enable streaming
            }

            if "tools" in kwargs and kwargs["tools"]:
                anthropic_params["tools"] = kwargs["tools"]
                anthropic_params["tool_choice"] = kwargs.get("tool_choice", "auto")

            for param in ["top_p", "top_k", "metadata"]:
                if param in kwargs:
                    anthropic_params[param] = kwargs[param]

            response_stream = await self.async_provider.messages.create(**anthropic_params)

            async for chunk in response_stream:
                if not chunk:
                    continue

                yield self.postprocess_stream_response(chunk)

        except Exception as e:
            logger.warn(f"Error in Anthropic astream_completion: {e}")
            yield ModelResponse.from_error(
                str(e),
                kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022")
            )

    async def acompletion(self, 
                    messages: List[Dict[str, str]], 
                    temperature: float = 0.0, 
                    max_tokens: int = None, 
                    stop: List[str] = None, 
                    **kwargs) -> ModelResponse:
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
            raise RuntimeError("Async provider not initialized. Make sure 'async_able' parameter is set to True in initialization.")

        try:
            processed_data = self.preprocess_messages(messages)

            if "tools" in kwargs:
                openai_tools = kwargs["tools"]
                claude_tools = []

                for tool in openai_tools:
                    if tool["type"] == "function":
                        claude_tool = {
                            "name": tool["name"],
                            "description": tool["description"],
                            "input_schema": {
                                "type": "object",
                                "properties": tool["parameters"]["properties"],
                                "required": tool["parameters"].get("required", [])
                            }
                        }
                        claude_tools.append(claude_tool)

                kwargs["tools"] = claude_tools

            anthropic_params = {
                "model": kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022"),
                "messages": processed_data["messages"],
                "system": processed_data["system"],
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
                "stop_sequences": stop,
            }

            if "tools" in kwargs and kwargs["tools"]:
                anthropic_params["tools"] = kwargs["tools"]
                anthropic_params["tool_choice"] = kwargs.get("tool_choice", "auto")

            for param in ["top_p", "top_k", "metadata", "stream"]:
                if param in kwargs:
                    anthropic_params[param] = kwargs[param]

            response = await self.async_provider.messages.create(**anthropic_params)

            return self.postprocess_response(response)
        except Exception as e:
            logger.warn(f"Error in Anthropic acompletion: {e}")
            return ModelResponse.from_error(
                str(e),
                kwargs.get("model_name", self.model_name or "claude-3-5-sonnet-20241022")
            ) 