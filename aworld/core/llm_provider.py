import abc
from typing import (
    Any,
    List,
    Dict,
    Generator,
    AsyncGenerator,
)

from aworld.models.model_response import ModelResponse
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CandidateRequestNotEnforceable,
    ProviderCandidateEnvelope,
    ProviderLoweringCapability,
    ProviderLoweringReceipt,
)
from aworld.utils.common import nest_dict_counter


class LLMProviderBase(abc.ABC):
    """Base class for large language model providers, defines unified interface."""

    def __init__(self,
                 api_key: str = None,
                 base_url: str = None,
                 model_name: str = None,
                 sync_enabled: bool = None,
                 async_enabled: bool = None,
                 **kwargs):
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
        self.need_sync = sync_enabled if sync_enabled is not None else async_enabled is not True
        self.need_async = async_enabled if async_enabled is not None else sync_enabled is not True

        # Initialize providers based on flags
        self.provider = self._init_provider() if self.need_sync else None
        self.async_provider = self._init_async_provider() if self.need_async else None
        self.stream_tool_buffer = []

    @abc.abstractmethod
    def _init_provider(self):
        """Initialize specific provider instance, to be implemented by subclasses.
        Returns:
            Provider instance.
        """

    def _init_async_provider(self):
        """Initialize async provider instance. Optional for subclasses that don't need async support.
        Only called if async provider is needed.

        Returns:
            Async provider instance.
        """
        return None

    @classmethod
    def supported_models(cls) -> list[str]:
        return []

    def context_candidate_lowering_capability(
        self,
    ) -> ProviderLoweringCapability | None:
        """Return a versioned provider-owned lowering contract, if supported."""
        return None

    def commit_context_candidate_lowering(
        self,
        *,
        context: Context | None,
        envelope: ProviderCandidateEnvelope,
        receipt: ProviderLoweringReceipt,
    ) -> None:
        """Persist the lowering receipt before the provider action is attempted.

        Enforce is fail-closed: missing or ambiguous request correlation must
        never degrade into an unobserved provider request.
        """
        try:
            if not isinstance(envelope, ProviderCandidateEnvelope):
                raise TypeError("invalid candidate envelope")
            if not isinstance(receipt, ProviderLoweringReceipt):
                raise TypeError("invalid provider lowering receipt")
            capability = self.context_candidate_lowering_capability()
            if capability != envelope.expected_lowering or capability != receipt.lowering:
                raise ValueError("provider lowering capability mismatch")
            if receipt.candidate_content_hash != envelope.candidate_request.content_hash:
                raise ValueError("provider receipt is not bound to the candidate")
            if context is None:
                raise ValueError("provider lowering receipt requires Context")
            llm_calls = context.get_llm_calls()
            matches = [
                (index, record)
                for index, record in enumerate(llm_calls)
                if isinstance(record, dict)
                and record.get("request_id") == envelope.candidate_request.request_id
            ]
            if len(matches) != 1:
                raise ValueError("provider lowering receipt correlation is ambiguous")
            index, record = matches[0]
            rollout = record.get("context_rollout")
            if not isinstance(rollout, dict) or rollout.get("mode") != "enforce":
                raise ValueError("provider lowering receipt requires an enforce record")
            candidate_evidence = rollout.get("candidate_snapshot")
            if (
                not isinstance(candidate_evidence, dict)
                or candidate_evidence.get("content_hash")
                != envelope.candidate_request.content_hash
            ):
                raise ValueError("persisted candidate evidence does not match envelope")

            lowering_evidence = receipt.to_redacted_dict()
            if receipt.cache_identity is None:
                lowering_evidence["cache_continuity"] = {
                    "status": "unavailable",
                    "previous_present": False,
                    "break_reasons": [],
                    "reason_code": "provider_wire_prefix_evidence_unavailable",
                }
            else:
                lowering_evidence["cache_continuity"] = (
                    context.commit_provider_cache_identity(
                        receipt.cache_identity
                    )
                )

            updated_rollout = dict(rollout)
            updated_rollout.update({
                "candidate_status": "provider_lowered",
                "candidate_applied": True,
                "provider_lowering_ready": True,
                "provider_lowering": lowering_evidence,
            })
            updated_rollout.pop("error", None)
            updated = dict(record)
            updated.update({
                # The persisted raw request remains the selected model-boundary
                # candidate.  Provider-prepared truth is represented by the
                # redacted receipt hash below, so the top-level field must not
                # falsely claim that raw provider parameters were persisted.
                "request": envelope.candidate_request.thaw(),
                "request_selection": "candidate",
                "context_observe_scope": "legacy_request_before_rollout",
                "provider_invoked": True,
                "provider_prepared_request_match": None,
                "context_rollout": updated_rollout,
            })
            llm_calls[index] = updated
        except CandidateRequestNotEnforceable:
            raise
        except Exception:
            raise CandidateRequestNotEnforceable(
                "provider_lowering_receipt_failed"
            ) from None

    def preprocess_messages(self, messages: List[Dict[str, str]]) -> Any:
        """Preprocess messages, convert OpenAI format messages to specific provider required format.

        Args:
            messages: OpenAI format message list [{"role": "system", "content": "..."}, ...].

        Returns:
            Converted messages, format determined by specific provider.
        """
        return messages

    @abc.abstractmethod
    def postprocess_response(self, response: Any) -> ModelResponse:
        """Post-process response, convert provider response to unified ModelResponse.

        Args:
            response: Original response from provider.

        Returns:
            ModelResponse: Unified format response object.

        Raises:
            LLMResponseError: When LLM response error occurs.
        """

    def postprocess_stream_response(self, chunk: Any) -> ModelResponse:
        """Post-process streaming response chunk, convert provider chunk to unified ModelResponse.

        Args:
            chunk: Original response chunk from provider.

        Returns:
            ModelResponse: Unified format response object for the chunk.

        Raises:
            LLMResponseError: When LLM response error occurs.
        """

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

        Raises:
            LLMResponseError: When LLM response error occurs.
            RuntimeError: When async provider is not initialized.
        """
        if not self.async_provider:
            raise RuntimeError(
                "Async provider not initialized. Make sure 'async_enabled' parameter is set to True in initialization.")

    @abc.abstractmethod
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

        Raises:
            LLMResponseError: When LLM response error occurs.
            RuntimeError: When sync provider is not initialized.
        """

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
            context: runtime context.
            **kwargs: Other parameters.

        Returns:
            Generator yielding ModelResponse chunks.

        Raises:
            LLMResponseError: When LLM response error occurs.
            RuntimeError: When sync provider is not initialized.
        """

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
            context: runtime context.
            **kwargs: Other parameters.

        Returns:
            AsyncGenerator yielding ModelResponse chunks.

        Raises:
            LLMResponseError: When LLM response error occurs.
            RuntimeError: When async provider is not initialized.
        """

    def _accumulate_chunk_usage(self, usage: Dict[str, int], chunk_usage: Dict[str, int]):
        """Accumulate usage statistics from chunk into the main usage dictionary.

        Args:
            usage: Dictionary to accumulate usage into (will be modified)
            chunk_usage: Usage statistics from the current chunk
        """
        if not chunk_usage:
            return

        usage.update(nest_dict_counter(usage, chunk_usage))

    def speech_to_text(self, audio_file, language, prompt, **kwargs) -> ModelResponse:
        pass

    async def aspeech_to_text(self, audio_file, language, prompt, **kwargs) -> ModelResponse:
        pass

    def apply_chat_template(self, messages: List[Dict[str, str]]) -> List[int]:
        pass
