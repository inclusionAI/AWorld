import dotenv
import os
from typing import (
    Any,
    Optional,
    List,
    Dict,
    Union,
    Generator,
    AsyncGenerator,
)

from aworld.models.model_response import ModelResponse


class LLMProviderBase:
    """Base class for large language model providers, defines unified interface."""

    def __init__(self, api_key: str = None, base_url: str = None, model_name: str = None, sync_able: bool = None, async_able: bool = None, **kwargs):
        """Initialize provider.

        Args:
            api_key: API key.
            base_url: Service URL.
            model_name: Model name.
            **kwargs: Other parameters.
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.kwargs = kwargs
        # Determine whether to initialize sync and async providers
        self.need_sync = sync_able if sync_able is not None else async_able is not True
        self.need_async = async_able if async_able is not None else sync_able is not True

        # Initialize providers based on flags
        self.provider = self._init_provider() if self.need_sync else None
        self.async_provider = self._init_async_provider() if self.need_async else None


    def _init_provider(self):
        """Initialize specific provider instance, to be implemented by subclasses.
        Returns:
            Provider instance.
        """
        raise NotImplementedError("Subclasses must implement _init_provider method")

    def _init_async_provider(self):
        """Initialize async provider instance. Optional for subclasses that don't need async support.
        Only called if async provider is needed.

        Returns:
            Async provider instance.
        """
        return None

    def preprocess_messages(self, messages: List[Dict[str, str]]) -> Any:
        """Preprocess messages, convert OpenAI format messages to specific provider required format.

        Args:
            messages: OpenAI format message list [{"role": "system", "content": "..."}, ...].

        Returns:
            Converted messages, format determined by specific provider.
        """
        raise NotImplementedError("Subclasses must implement preprocess_messages method")

    def postprocess_response(self, response: Any) -> ModelResponse:
        """Post-process response, convert provider response to unified ModelResponse.

        Args:
            response: Original response from provider.

        Returns:
            ModelResponse: Unified format response object.
        """
        raise NotImplementedError("Subclasses must implement postprocess_response method")

    def postprocess_stream_response(self, chunk: Any) -> ModelResponse:
        """Post-process streaming response chunk, convert provider chunk to unified ModelResponse.

        Args:
            chunk: Original response chunk from provider.
            
        Returns:
            ModelResponse: Unified format response object for the chunk.
        """
        raise NotImplementedError("Subclasses must implement postprocess_stream_response method")

    
    async def acompletion(self, 
                    messages: List[Dict[str, str]], 
                    temperature: float = 0.0, 
                    max_tokens: int = None, 
                    stop: List[str] = None, 
                    **kwargs) -> ModelResponse:
        """Asynchronously call model to generate response.
        
        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse: Unified model response object.
        """
        if not self.async_provider:
            raise RuntimeError("Async provider not initialized. Make sure 'async_able' parameter is set to True in initialization.")

        raise NotImplementedError("Subclasses must implement acompletion method")

    def completion(self, 
                messages: List[Dict[str, str]], 
                temperature: float = 0.0, 
                max_tokens: int = None, 
                stop: List[str] = None, 
                **kwargs) -> ModelResponse:
        """Synchronously call model to generate response.

        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse: Unified model response object.
        """
        if not self.provider:
            raise RuntimeError("Sync provider not initialized. Make sure 'sync_able' parameter is set to True in initialization.")

        raise NotImplementedError("Subclasses must implement completion method")

    def stream_completion(self, 
                     messages: List[Dict[str, str]], 
                     temperature: float = 0.0, 
                     max_tokens: int = None, 
                     stop: List[str] = None, 
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
        raise NotImplementedError("Subclasses must implement stream_completion method")

    async def astream_completion(self,
                                 messages: List[Dict[str, str]],
                                 temperature: float = 0.0,
                                 max_tokens: int = None,
                                 stop: List[str] = None,
                                 **kwargs) -> AsyncGenerator[ModelResponse, None]:
        """Asynchronously call model to generate streaming response.

        Args:
            messages: Message list, format is [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}].
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            AsyncGenerator yielding ModelResponse chunks.
        """
        raise NotImplementedError("Subclasses must implement astream_completion method") 