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
    ContextEntrypointParityReceipt,
    ProviderCandidateEnvelope,
    ProviderLoweringCapability,
    ProviderLoweringReceipt,
    ProviderObservedAttributionEnvelope,
    ProviderObservedAttributionReceipt,
    ProviderRequestSnapshot,
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

    def commit_provider_prepared_attempt(
        self,
        *,
        context: Context | None,
        request_id: str,
        snapshot: ProviderRequestSnapshot,
        envelope: ProviderCandidateEnvelope | None = None,
        receipt: ProviderLoweringReceipt | None = None,
    ) -> None:
        """Atomically persist one complete prepared attempt before provider I/O.

        Enforce is fail-closed: missing or ambiguous request correlation must
        never degrade into an unobserved provider request.
        """
        try:
            if not isinstance(snapshot, ProviderRequestSnapshot) or snapshot.request_id != request_id:
                raise TypeError("invalid provider prepared snapshot")
            if (envelope is None) != (receipt is None):
                raise ValueError("envelope and receipt must be atomic")
            if envelope is not None:
                if not isinstance(envelope, ProviderCandidateEnvelope) or not isinstance(receipt, ProviderLoweringReceipt):
                    raise TypeError("invalid candidate lowering evidence")
                if envelope.attribution_plan is None or receipt.attribution is None:
                    raise ValueError("enforce lowering requires attribution")
                if not receipt.attribution.binds_plan(envelope.attribution_plan):
                    raise ValueError("provider attribution is not bound to candidate plan")
                capability = self.context_candidate_lowering_capability()
                if capability != envelope.expected_lowering or capability != receipt.lowering:
                    raise ValueError("provider lowering capability mismatch")
                if receipt.candidate_content_hash != envelope.candidate_request.content_hash:
                    raise ValueError("provider receipt is not bound to the candidate")
            if context is None:
                raise ValueError("provider prepared attempt requires Context")
            llm_calls = context.get_llm_calls()
            matches = [
                (index, record)
                for index, record in enumerate(llm_calls)
                if isinstance(record, dict)
                and record.get("request_id") == request_id
            ]
            if len(matches) != 1:
                raise ValueError("provider lowering receipt correlation is ambiguous")
            index, record = matches[0]
            updated = dict(record)
            updated.update({"provider_request": snapshot.to_dict(), "provider_invoked": False, "provider_attempt_status": "prepared"})
            if envelope is not None:
                rollout = record.get("context_rollout")
                if not isinstance(rollout, dict) or rollout.get("mode") != "enforce":
                    raise ValueError("provider lowering receipt requires an enforce record")
                candidate_evidence = rollout.get("candidate_snapshot")
                if not isinstance(candidate_evidence, dict) or candidate_evidence.get("content_hash") != envelope.candidate_request.content_hash:
                    raise ValueError("persisted candidate evidence does not match envelope")
                lowering_evidence = receipt.to_redacted_dict()
                lowering_evidence["cache_continuity"] = (
                    context.preview_provider_cache_identity(receipt.cache_identity)
                    if receipt.cache_identity is not None
                    else {"status": "unavailable", "previous_present": False, "break_reasons": [], "reason_code": "provider_wire_prefix_evidence_unavailable"}
                )
                updated_rollout = dict(rollout)
                updated_rollout.update({"candidate_status": "provider_prepared", "candidate_applied": True, "provider_lowering_ready": True, "provider_lowering": lowering_evidence})
                updated_rollout["provider_attribution"] = {
                    "subject": "candidate_selected",
                    "subject_content_hash": envelope.candidate_request.content_hash,
                    "plan_fingerprint": envelope.attribution_plan.fingerprint,
                    "status": "available",
                    "adapter_identity": receipt.lowering.adapter_identity,
                    "adapter_version": receipt.lowering.adapter_version,
                    "request_projection": receipt.lowering.request_projection,
                    "provider_request": lowering_evidence["provider_request"],
                    "attribution": lowering_evidence["attribution"],
                }
                parity_payload = rollout.get("entrypoint_parity")
                try:
                    if not isinstance(parity_payload, dict):
                        raise ValueError("entrypoint parity receipt missing")
                    updated_rollout["entrypoint_parity"] = (
                        ContextEntrypointParityReceipt.from_dict(parity_payload)
                        .bind_provider_lowering(receipt)
                        .to_dict()
                    )
                except Exception:
                    # Parity evidence is a rollout/default-on gate, not a
                    # provider request transform. Keep the selected request
                    # untouched and make the missing proof explicit.
                    updated_rollout["entrypoint_parity"] = {
                        "schema_version": ContextEntrypointParityReceipt.SCHEMA_VERSION,
                        "status": "unavailable",
                        "reason_code": "entrypoint_provider_binding_failed",
                    }
                updated_rollout.pop("error", None)
                updated.update({"request": envelope.candidate_request.thaw(), "request_selection": "candidate", "context_observe_scope": "legacy_request_before_rollout", "provider_prepared_request_match": None, "context_rollout": updated_rollout})
            context.replace_llm_call(
                index, updated, event_type="provider_request_prepared"
            )
        except CandidateRequestNotEnforceable:
            raise
        except Exception:
            raise CandidateRequestNotEnforceable(
                "provider_lowering_receipt_failed"
            ) from None

    def mark_provider_attempted(
        self,
        *,
        context: Context | None,
        request_id: str,
        cache_identity: Any = None,
    ) -> None:
        """Commit attempted state at the immediate SDK/HTTP call boundary."""
        try:
            if context is None:
                raise ValueError("provider attempt requires Context")
            llm_calls = context.get_llm_calls()
            matches = [(index, record) for index, record in enumerate(llm_calls) if isinstance(record, dict) and record.get("request_id") == request_id]
            if len(matches) != 1:
                raise ValueError("provider attempt correlation is ambiguous")
            index, record = matches[0]
            if record.get("provider_attempt_status") != "prepared" or record.get("provider_invoked") is not False:
                raise ValueError("provider attempt was not prepared")
            updated = dict(record)
            updated.update({"provider_invoked": True, "provider_attempt_status": "attempted"})
            rollout = updated.get("context_rollout")
            if isinstance(rollout, dict) and rollout.get("mode") == "enforce":
                next_rollout = dict(rollout)
                next_rollout["candidate_status"] = "provider_attempted"
                updated["context_rollout"] = next_rollout
            previous_cache = getattr(context, "_provider_cache_identity", None)
            previous_breaks = set(getattr(context, "_pending_cache_break_reasons", ()))
            try:
                if cache_identity is not None:
                    context.commit_provider_cache_identity(cache_identity)
                context.replace_llm_call(
                    index, updated, event_type="provider_request_attempted"
                )
            except Exception:
                context._provider_cache_identity = previous_cache
                context._pending_cache_break_reasons = previous_breaks
                raise
        except Exception:
            raise CandidateRequestNotEnforceable("provider_prepared_attempt_failed") from None

    def commit_provider_observed_attribution(
        self,
        *,
        context: Context | None,
        request_id: str,
        snapshot: ProviderRequestSnapshot,
        envelope: ProviderObservedAttributionEnvelope,
        receipt: ProviderObservedAttributionReceipt | None,
        reason_code: str | None = None,
    ) -> None:
        """Persist observe-only provider attribution without authorizing mutation."""
        if context is None:
            raise ValueError("observed attribution requires Context")
        if not isinstance(snapshot, ProviderRequestSnapshot) or snapshot.request_id != request_id:
            raise TypeError("invalid observed provider snapshot")
        if not isinstance(envelope, ProviderObservedAttributionEnvelope):
            raise TypeError("invalid observed attribution envelope")
        if (receipt is None) == (reason_code is None):
            raise ValueError("observed attribution requires receipt xor reason")
        if receipt is not None and (
            not isinstance(receipt, ProviderObservedAttributionReceipt)
            or receipt.envelope != envelope
            or receipt.provider_request != snapshot
        ):
            raise ValueError("observed attribution receipt mismatch")
        llm_calls = context.get_llm_calls()
        matches = [
            (index, record)
            for index, record in enumerate(llm_calls)
            if isinstance(record, dict) and record.get("request_id") == request_id
        ]
        if len(matches) != 1:
            raise ValueError("observed attribution correlation is ambiguous")
        index, record = matches[0]
        rollout = record.get("context_rollout")
        if not isinstance(rollout, dict) or rollout.get("mode") != "observe":
            raise ValueError("observed attribution requires an observe record")
        observed = rollout.get("observed_snapshot")
        if (
            not isinstance(observed, dict)
            or observed.get("content_hash") != envelope.observed_request.content_hash
        ):
            raise ValueError("persisted observed request does not match envelope")
        evidence = (
            {**receipt.to_redacted_dict(), "status": "available"}
            if receipt is not None
            else {
                "subject": envelope.subject.value,
                "subject_content_hash": envelope.observed_request.content_hash,
                "plan_fingerprint": envelope.attribution_plan.fingerprint,
                "status": "unavailable",
                "reason_code": reason_code,
            }
        )
        updated_rollout = dict(rollout)
        updated_rollout["provider_attribution"] = evidence
        updated = dict(record)
        updated.update({
            "provider_request": snapshot.to_dict(),
            "provider_invoked": False,
            "provider_attempt_status": "prepared",
            "context_rollout": updated_rollout,
        })
        context.replace_llm_call(
            index, updated, event_type="provider_observed_attribution_committed"
        )

    def commit_provider_observation_unavailable(
        self,
        *,
        context: Context | None,
        request_id: str,
        envelope: ProviderObservedAttributionEnvelope,
        reason_code: str,
        snapshot: ProviderRequestSnapshot | None = None,
    ) -> bool:
        """Best-effort observe evidence; never authorizes or blocks provider I/O."""
        try:
            if context is None or not isinstance(envelope, ProviderObservedAttributionEnvelope):
                return False
            llm_calls = context.get_llm_calls()
            matches = [
                (index, record)
                for index, record in enumerate(llm_calls)
                if isinstance(record, dict) and record.get("request_id") == request_id
            ]
            if len(matches) != 1:
                return False
            index, record = matches[0]
            rollout = record.get("context_rollout")
            if not isinstance(rollout, dict) or rollout.get("mode") != "observe":
                return False
            updated_rollout = dict(rollout)
            updated_rollout["provider_attribution"] = {
                "subject": envelope.subject.value,
                "subject_content_hash": envelope.observed_request.content_hash,
                "plan_fingerprint": envelope.attribution_plan.fingerprint,
                "status": "unavailable",
                "reason_code": reason_code,
            }
            updated = dict(record)
            updated.update({
                "provider_invoked": False,
                "provider_attempt_status": "prepared",
                "context_rollout": updated_rollout,
            })
            if snapshot is not None:
                updated["provider_request"] = snapshot.to_dict()
            context.replace_llm_call(
                index, updated, event_type="provider_observation_unavailable"
            )
            return True
        except Exception:
            return False

    def mark_provider_attempted_fail_open(
        self, *, context: Context | None, request_id: str | None
    ) -> None:
        """Record attempt truth where possible without making observe correctness-critical."""
        try:
            if context is None or request_id is None:
                return
            llm_calls = context.get_llm_calls()
            matches = [
                (index, record)
                for index, record in enumerate(llm_calls)
                if isinstance(record, dict) and record.get("request_id") == request_id
            ]
            if len(matches) != 1:
                return
            index, record = matches[0]
            updated = dict(record)
            updated.update({"provider_invoked": True, "provider_attempt_status": "attempted"})
            context.replace_llm_call(
                index, updated, event_type="provider_request_attempted_fail_open"
            )
        except Exception:
            return

    def commit_provider_request_capture(
        self,
        *,
        context: Context | None,
        request_id: str,
        snapshot: ProviderRequestSnapshot,
    ) -> None:
        """Bind one provider-owned immutable request snapshot to its LLM call."""
        self.commit_provider_prepared_attempt(
            context=context, request_id=request_id, snapshot=snapshot
        )

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
