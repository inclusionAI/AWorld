import asyncio
import ast
import json
from types import SimpleNamespace

import pytest

import aworld.models.llm as llm_module
from aworld.config import ConfigDict
from aworld.core.context.base import Context
from aworld.core.context.compiler import canonical_json_hash
from aworld.core.task import Task, TaskResponse
from aworld.models.llm import AWORLD_CONTEXT_CALL_ID_KWARG, LLMModel
from aworld.models.reasoning_policy import (
    AWORLD_REASONING_SELECTION_KWARG,
    ReasoningPhasePolicy,
    resolve_reasoning_request,
)
from aworld.models.model_response import ModelResponse
from aworld.models.openai_provider import OpenAIProvider
from aworld.core.llm_provider import LLMProviderBase
from aworld.runners.event_runner import TaskEventRunner


def test_openai_provider_disables_hidden_retries_for_authoritative_usage(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_SELF_EVOLVE_DISABLE_PROVIDER_RETRIES", "1")
    provider = OpenAIProvider(
        model_name="gpt-4.1",
        sync_enabled=False,
        async_enabled=False,
    )

    assert provider._authoritative_max_retries(http_handler=False) == 0
    assert provider._authoritative_max_retries(http_handler=True) == 1
    assert provider.authoritative_usage_single_attempt is True


class RecordingLLMProvider(LLMProviderBase):
    def __init__(self, model_name="mock-model", **kwargs):
        super().__init__(model_name=model_name, **kwargs)
        self.seen_requests = []
        self._response_index = 0

    def _init_provider(self):
        pass

    def postprocess_response(self, response, **kwargs):
        return response

    def _build_response(self):
        self._response_index += 1
        return ModelResponse(
            id=f"resp-{self._response_index}",
            model=self.model_name,
            content=f"response-{self._response_index}",
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
            raw_usage={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "prompt_tokens_details": {"cached_tokens": 5},
                "cache_hit_tokens": 5,
            },
            provider_request_id=f"provider-req-{self._response_index}",
        )

    async def acompletion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        await asyncio.sleep(0)
        return self._build_response()

    def completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        return self._build_response()

    def stream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        yield ModelResponse(
            id="stream-resp-1",
            model=self.model_name,
            content="partial",
            message={"role": "assistant", "content": "partial"},
        )
        yield ModelResponse(
            id="stream-resp-1",
            model=self.model_name,
            content="final",
            message={"role": "assistant", "content": "final"},
            usage={
                "prompt_tokens": 13,
                "completion_tokens": 8,
                "total_tokens": 21,
            },
            raw_usage={
                "prompt_tokens": 13,
                "completion_tokens": 8,
                "total_tokens": 21,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
            provider_request_id="provider-stream-sync",
            finish_reason="stop",
        )

    async def astream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        yield ModelResponse(
            id="astream-resp-1",
            model=self.model_name,
            content="partial",
            message={"role": "assistant", "content": "partial"},
        )
        await asyncio.sleep(0)
        yield ModelResponse(
            id="astream-resp-1",
            model=self.model_name,
            content="final",
            message={"role": "assistant", "content": "final"},
            usage={
                "prompt_tokens": 17,
                "completion_tokens": 9,
                "total_tokens": 26,
            },
            raw_usage={
                "prompt_tokens": 17,
                "completion_tokens": 9,
                "total_tokens": 26,
                "cache_hit_tokens": 4,
            },
            provider_request_id="provider-stream-async",
            finish_reason="stop",
        )


class ReasoningRecordingProvider(RecordingLLMProvider):
    def __init__(self):
        super().__init__(model_name="aisearch_dsv4flash_cron_job")
        self.kwargs_by_method = {}

    async def acompletion(self, messages, **kwargs):
        self.kwargs_by_method["acompletion"] = dict(kwargs)
        return await super().acompletion(messages, **kwargs)

    def completion(self, messages, **kwargs):
        self.kwargs_by_method["completion"] = dict(kwargs)
        return super().completion(messages, **kwargs)

    def stream_completion(self, messages, **kwargs):
        self.kwargs_by_method["stream_completion"] = dict(kwargs)
        yield from super().stream_completion(messages, **kwargs)

    async def astream_completion(self, messages, **kwargs):
        self.kwargs_by_method["astream_completion"] = dict(kwargs)
        async for chunk in super().astream_completion(messages, **kwargs):
            yield chunk


class ManyChunkRecordingProvider(RecordingLLMProvider):
    def stream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        for index in range(1_000):
            yield ModelResponse(
                id="many-chunk-sync",
                model=self.model_name,
                content="x",
                finish_reason="stop" if index == 999 else None,
            )

    async def astream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        for index in range(1_000):
            yield ModelResponse(
                id="many-chunk-async",
                model=self.model_name,
                content="x",
                finish_reason="stop" if index == 999 else None,
            )


class ClosableSyncStreamProvider(RecordingLLMProvider):
    class Iterator:
        def __init__(self, model_name):
            self.model_name = model_name
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            if self.closed:
                raise StopIteration
            return ModelResponse(
                id="closable-sync-stream",
                model=self.model_name,
                content="partial",
            )

        def close(self):
            self.closed = True

    def __init__(self):
        super().__init__()
        self.iterator = None

    def stream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        self.iterator = self.Iterator(self.model_name)
        return self.iterator


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["high", "medium", "xhigh"])
async def test_reasoning_selection_is_recorded_and_not_forwarded_as_provider_kwarg(
    effort,
):
    provider = ReasoningRecordingProvider()
    model = LLMModel(custom_provider=provider)
    selection = {
        "phase": "execute",
        "source": "phase_policy",
        "reasoning_effort": effort,
        "thinking": True,
        "policy_id": "balanced/v1",
        "transport": "openai/v1",
        "applied": True,
        "reason_code": "phase_policy_selected",
    }

    sync_context = Context(task_id="reasoning-selection-sync")
    model.completion(
        [{"role": "user", "content": "continue sync"}],
        context=sync_context,
        reasoning_effort=effort,
        **{AWORLD_REASONING_SELECTION_KWARG: selection},
    )
    assert AWORLD_REASONING_SELECTION_KWARG not in provider.kwargs_by_method[
        "completion"
    ]
    sync_record = sync_context.get_llm_calls()[0]
    assert sync_record["request"]["params"]["reasoning_effort"] == effort
    assert sync_record["reasoning_selection"] == selection

    sync_stream_context = Context(task_id="reasoning-selection-sync-stream")
    sync_chunks = list(
        model.stream_completion(
            [{"role": "user", "content": "stream sync"}],
            context=sync_stream_context,
            reasoning_effort=effort,
            **{AWORLD_REASONING_SELECTION_KWARG: selection},
        )
    )
    assert sync_chunks
    assert AWORLD_REASONING_SELECTION_KWARG not in provider.kwargs_by_method[
        "stream_completion"
    ]
    sync_stream_record = sync_stream_context.get_llm_calls()[0]
    assert sync_stream_record["request"]["params"]["reasoning_effort"] == effort
    assert sync_stream_record["reasoning_selection"] == selection

    context = Context(task_id="reasoning-selection-record")
    await model.acompletion(
        [{"role": "user", "content": "continue"}],
        context=context,
        reasoning_effort=effort,
        extra_body={
            "chat_template_kwargs": {
                "reasoning_effort": effort,
                "thinking": True,
            }
        },
        **{AWORLD_REASONING_SELECTION_KWARG: selection},
    )

    assert AWORLD_REASONING_SELECTION_KWARG not in provider.kwargs_by_method[
        "acompletion"
    ]
    record = context.get_llm_calls()[0]
    assert record["request"]["params"]["reasoning_effort"] == effort
    assert record["reasoning_selection"] == selection

    stream_context = Context(task_id="reasoning-selection-stream")
    chunks = [
        chunk
        async for chunk in model.astream_completion(
            [{"role": "user", "content": "stream"}],
            context=stream_context,
            reasoning_effort=effort,
            **{AWORLD_REASONING_SELECTION_KWARG: selection},
        )
    ]
    assert chunks
    assert AWORLD_REASONING_SELECTION_KWARG not in provider.kwargs_by_method[
        "astream_completion"
    ]
    stream_record = stream_context.get_llm_calls()[0]
    assert stream_record["request"]["params"]["reasoning_effort"] == effort
    assert stream_record["reasoning_selection"] == selection


@pytest.mark.asyncio
async def test_reasoning_selection_rejects_untrusted_payloads_in_all_call_shapes():
    provider = ReasoningRecordingProvider()
    model = LLMModel(custom_provider=provider)
    valid = {
        "phase": "execute",
        "source": "phase_policy",
        "reasoning_effort": "high",
        "thinking": True,
        "policy_id": "balanced/v1",
        "transport": "openai/v1",
        "applied": True,
        "reason_code": "phase_policy_selected",
    }
    secret = "caller-secret-must-not-be-recorded"

    sync_context = Context(task_id="reasoning-invalid-sync")
    model.completion(
        [{"role": "user", "content": "sync"}],
        context=sync_context,
        reasoning_effort="high",
        **{AWORLD_REASONING_SELECTION_KWARG: {**valid, "secret": secret}},
    )

    async_context = Context(task_id="reasoning-invalid-async")
    await model.acompletion(
        [{"role": "user", "content": "async"}],
        context=async_context,
        reasoning_effort="high",
        **{
            AWORLD_REASONING_SELECTION_KWARG: {
                **valid,
                "reasoning_effort": "max",
            }
        },
    )

    sync_stream_context = Context(task_id="reasoning-invalid-sync-stream")
    assert list(
        model.stream_completion(
            [{"role": "user", "content": "sync stream"}],
            context=sync_stream_context,
            reasoning_effort="high",
            **{
                AWORLD_REASONING_SELECTION_KWARG: {
                    **valid,
                    "applied": False,
                }
            },
        )
    )

    async_stream_context = Context(task_id="reasoning-invalid-async-stream")
    assert [
        chunk
        async for chunk in model.astream_completion(
            [{"role": "user", "content": "async stream"}],
            context=async_stream_context,
            reasoning_effort="high",
            **{
                AWORLD_REASONING_SELECTION_KWARG: {
                    **valid,
                    "policy_id": "x" * 129,
                }
            },
        )
    ]

    contexts = (
        sync_context,
        async_context,
        sync_stream_context,
        async_stream_context,
    )
    for context in contexts:
        record = context.get_llm_calls()[0]
        assert record["reasoning_selection"] == {
            "status": "selection_receipt_invalid"
        }
        assert secret not in str(record)
    for provider_kwargs in provider.kwargs_by_method.values():
        assert AWORLD_REASONING_SELECTION_KWARG not in provider_kwargs


def test_reasoning_receipt_rejects_conflicting_canonical_locations():
    receipt = {
        "phase": "execute",
        "source": "caller",
        "reasoning_effort": "high",
        "thinking": True,
        "policy_id": None,
        "transport": "openai/v1",
        "applied": True,
        "reason_code": "explicit_caller_pin",
    }

    assert LLMModel._project_reasoning_selection_receipt(
        receipt,
        request_kwargs={
            "reasoning_effort": "high",
            "extra_body": {
                "chat_template_kwargs": {"reasoning_effort": "low"}
            },
        },
    ) == {"status": "selection_receipt_invalid"}


@pytest.mark.parametrize(
    ("provider_name", "model_name", "request_kwargs", "policy", "reason_code"),
    [
        (
            "anthropic",
            "gpt-4.1",
            {"reasoning_effort": "high"},
            None,
            "unsupported_reasoning_transport",
        ),
        (
            "anthropic",
            "gpt-4.1",
            {},
            ReasoningPhasePolicy.balanced(),
            "unsupported_reasoning_transport",
        ),
        (
            "openai",
            "aisearch_dsv4flash_cron_job",
            {"extra_body": {"chat_template_kwargs": "invalid"}},
            ReasoningPhasePolicy.balanced(),
            "incompatible_request_shape",
        ),
    ],
)
def test_reasoning_selection_preserves_bounded_fail_open_receipts(
    provider_name,
    model_name,
    request_kwargs,
    policy,
    reason_code,
):
    resolved, receipt = resolve_reasoning_request(
        phase="execute",
        model_name=model_name,
        provider=provider_name,
        request_kwargs=request_kwargs,
        policy=policy,
    )
    assert receipt.applied is False
    assert receipt.reason_code == reason_code

    provider = ReasoningRecordingProvider()
    provider.model_name = model_name
    model = LLMModel(custom_provider=provider)
    context = Context(task_id=f"reasoning-{reason_code}")
    model.completion(
        [{"role": "user", "content": "continue"}],
        context=context,
        **resolved,
        **{AWORLD_REASONING_SELECTION_KWARG: receipt.to_dict()},
    )

    assert context.get_llm_calls()[0]["reasoning_selection"] == receipt.to_dict()


def test_turn_economics_storage_failure_does_not_block_provider(monkeypatch):
    provider = RecordingLLMProvider()
    model = LLMModel(custom_provider=provider)
    context = Context(task_id="turn-economics-fail-open")
    monkeypatch.setattr(
        context,
        "record_model_turn",
        lambda request_id, messages: (_ for _ in ()).throw(RuntimeError("storage")),
    )

    model.completion([{"role": "user", "content": "go"}], context=context)

    assert len(provider.seen_requests) == 1
    call = context.get_llm_calls()[0]
    assert call["status"] == "success"
    assert call["turn_economics"] == {
        "status": "unavailable",
        "reason_code": "turn_economics_record_failed",
    }


class TerminalMarkerStreamProvider(RecordingLLMProvider):
    def stream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        yield ModelResponse(
            id="stream-resp-marker",
            model=self.model_name,
            content="final",
            message={"role": "assistant", "content": "final"},
            usage={
                "prompt_tokens": 13,
                "completion_tokens": 8,
                "total_tokens": 21,
            },
            raw_usage={
                "prompt_tokens": 13,
                "completion_tokens": 8,
                "total_tokens": 21,
                "cache_hit_tokens": 3,
            },
            provider_request_id="provider-stream-sync",
        )
        yield ModelResponse(
            id="stream-resp-marker",
            model=self.model_name,
            content=None,
            message={"role": "assistant", "content": ""},
            finish_reason="stop",
        )

    async def astream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        yield ModelResponse(
            id="astream-resp-marker",
            model=self.model_name,
            content="final",
            message={"role": "assistant", "content": "final"},
            usage={
                "prompt_tokens": 17,
                "completion_tokens": 9,
                "total_tokens": 26,
            },
            raw_usage={
                "prompt_tokens": 17,
                "completion_tokens": 9,
                "total_tokens": 26,
                "cache_hit_tokens": 4,
            },
            provider_request_id="provider-stream-async",
        )
        await asyncio.sleep(0)
        yield ModelResponse(
            id="astream-resp-marker",
            model=self.model_name,
            content=None,
            message={"role": "assistant", "content": ""},
            finish_reason="stop",
        )


class SplitUsageStreamProvider(RecordingLLMProvider):
    def __init__(self, *, cumulative: bool):
        super().__init__()
        self.cumulative = cumulative

    def _chunks(self):
        yield ModelResponse(
            id="split-usage",
            model=self.model_name,
            content="partial",
            usage={"prompt_tokens": 10, "total_tokens": 10},
            raw_usage={"input": {"tokens": 10}},
            usage_reported=True,
            usage_is_cumulative=self.cumulative,
        )
        yield ModelResponse(
            id="split-usage",
            model=self.model_name,
            finish_reason="stop",
            usage=(
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
                if self.cumulative
                else {"completion_tokens": 5, "total_tokens": 5}
            ),
            raw_usage=(
                {"input": {"tokens": 10}, "output": {"tokens": 5}}
                if self.cumulative
                else {"output": {"tokens": 5}}
            ),
            usage_reported=True,
            usage_is_cumulative=self.cumulative,
        )

    def stream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        yield from self._chunks()

    async def astream_completion(self, messages, **kwargs):
        self.seen_requests.append(messages)
        for chunk in self._chunks():
            yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize("cumulative", [False, True])
async def test_stream_usage_capture_merges_deltas_and_replaces_cumulative_snapshots(
    cumulative,
):
    provider = SplitUsageStreamProvider(cumulative=cumulative)
    model = LLMModel(custom_provider=provider)

    sync_context = Context(task_id=f"split-usage-sync-{cumulative}")
    assert list(
        model.stream_completion(
            [{"role": "user", "content": "sync"}],
            context=sync_context,
        )
    )
    async_context = Context(task_id=f"split-usage-async-{cumulative}")
    assert [
        chunk
        async for chunk in model.astream_completion(
            [{"role": "user", "content": "async"}],
            context=async_context,
        )
    ]

    for context in (sync_context, async_context):
        record = context.get_llm_calls()[0]
        assert record["usage_normalized"] == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
        assert record["usage_raw"] == {
            "input": {"tokens": 10},
            "output": {"tokens": 5},
        }
        assert record["usage_reported"] is True


class ToolChoiceProvider(RecordingLLMProvider):
    def _build_response(self):
        response = super()._build_response()
        response.message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "chosen-tool-call",
                "type": "function",
                "function": {"name": "generic_tool", "arguments": "{}"},
            }],
        }
        return response


@pytest.mark.asyncio
async def test_acompletion_appends_llm_call_with_final_messages_and_usage(monkeypatch):
    provider = RecordingLLMProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-async")
    context.trace_id = "trace-async"
    original_messages = [{"role": "user", "content": "original"}]
    final_messages = [
        {"role": "system", "content": "hook-added"},
        {"role": "user", "content": "original"},
    ]

    async def fake_run_hooks(*, hook_point, **kwargs):
        if hook_point == "before_llm_call":
            yield SimpleNamespace(headers={"updated_input": {"messages": final_messages}})
            return
        if False:
            yield None

    monkeypatch.setattr("aworld.runners.hook.utils.run_hooks", fake_run_hooks)

    await llm_model.acompletion(original_messages, context=context)

    llm_calls = context.context_info.get("llm_calls")
    assert isinstance(llm_calls, list)
    assert len(llm_calls) == 1
    assert provider.seen_requests == [final_messages]

    llm_call = llm_calls[0]
    assert llm_call["request_id"].startswith("llm_req_")
    assert llm_call["provider_request_id"] == "provider-req-1"
    assert llm_call["provider_name"] == "custom"
    assert llm_call["model"] == "mock-model"
    assert llm_call["request"]["messages"] == final_messages
    assert llm_call["context_observe"]["request"]["content_hash"] == (
        canonical_json_hash(
            {
                "messages": final_messages,
                "tools": None,
                "params": {
                    "temperature": 0.0,
                    "max_tokens": None,
                    "stop": None,
                },
            }
        )
    )
    assert llm_call["usage_normalized"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert llm_call["usage_raw"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "prompt_tokens_details": {"cached_tokens": 5},
        "cache_hit_tokens": 5,
    }
    assert llm_call["cache_usage_receipt"] == {
        "schema_version": "aworld.cache-usage-receipt.v1",
        "fidelity": "exact",
        "reason_code": None,
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_tokens": 5,
        "cache_write_tokens": None,
        "cache_read_lower_bound": 5,
        "cache_read_upper_bound": 5,
        "reported_input_tokens": 11,
        "input_token_accounting": "inclusive",
        "uncached_input_tokens": 6,
        "cache_read_ratio": 5 / 11,
        "raw_cache_sources": ["cache_hit_tokens", "prompt_tokens_details.cached_tokens"],
        "normalized_cache_sources": [],
    }
    assert llm_call["turn_economics"]["turn_kind"] == "model"
    assert llm_call["turn_economics"]["cause"] == "initial_input"


@pytest.mark.asyncio
async def test_llm_response_tool_choice_is_bound_to_the_following_tool_turn():
    provider = ToolChoiceProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="typed-tool-choice")

    await llm_model.acompletion(
        [{"role": "user", "content": "choose a tool"}], context=context
    )
    tool_turn = context.record_tool_turn("chosen-tool-call")

    assert tool_turn.cause.value == "model_choice"
    assert tool_turn.parent_turn_id_hash is not None


@pytest.mark.asyncio
async def test_acompletion_captures_after_hook_mutated_response_payload(monkeypatch):
    provider = RecordingLLMProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-after-hook")
    context.trace_id = "trace-after-hook"
    updated_message = {"role": "assistant", "content": "hook-mutated"}

    async def fake_run_hooks(*, hook_point, **kwargs):
        if hook_point == "after_llm_call":
            yield SimpleNamespace(
                headers={
                    "updated_output": {
                        "content": "hook-mutated",
                        "message": updated_message,
                        "finish_reason": "tool_calls",
                    }
                }
            )
            return
        if False:
            yield None

    monkeypatch.setattr("aworld.runners.hook.utils.run_hooks", fake_run_hooks)

    response = await llm_model.acompletion([{"role": "user", "content": "hi"}], context=context)

    assert response.content == "hook-mutated"
    llm_call = context.context_info.get("llm_calls")[0]
    assert llm_call["response"]["message"] == updated_message
    assert llm_call["response"]["finish_reason"] == "tool_calls"


def test_completion_appends_llm_calls_without_overwriting_prior_records():
    provider = RecordingLLMProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-sync")
    context.trace_id = "trace-sync"

    first_messages = [{"role": "user", "content": "first"}]
    second_messages = [{"role": "user", "content": "second"}]

    llm_model.completion(first_messages, context=context)
    llm_model.completion(second_messages, context=context)

    llm_calls = context.context_info.get("llm_calls")
    assert len(llm_calls) == 2
    assert [record["request"]["messages"] for record in llm_calls] == [first_messages, second_messages]
    assert llm_calls[0]["provider_request_id"] == "provider-req-1"
    assert llm_calls[1]["provider_request_id"] == "provider-req-2"
    assert llm_calls[0]["request_id"] != llm_calls[1]["request_id"]


def test_completion_records_effective_request_model_when_overridden():
    provider = RecordingLLMProvider(model_name="provider-default")
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-sync-override")

    llm_model.completion(
        [{"role": "user", "content": "first"}],
        context=context,
        model_name="request-override",
    )

    llm_call = context.context_info.get("llm_calls")[0]
    assert llm_call["model"] == "request-override"


def test_model_boundary_capture_merges_agent_compiler_snapshot():
    provider = RecordingLLMProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-merged-capture")
    context.agent_info.current_agent_id = "solver"
    compiled_messages = [{"role": "user", "content": "compiled"}]
    provider_messages = [{"role": "user", "content": "provider-bound"}]
    context.context_info["llm_calls"] = [
        {
            "call_id": "compiler-call",
            "agent_id": "solver",
            "request": {"messages": compiled_messages},
            "assembly_observability": {"stable_prefix_hash": "prefix-1"},
        }
    ]

    llm_model.completion(
        provider_messages,
        context=context,
        **{AWORLD_CONTEXT_CALL_ID_KWARG: "compiler-call"},
    )

    llm_calls = context.context_info["llm_calls"]
    assert len(llm_calls) == 1
    assert llm_calls[0]["call_id"] == "compiler-call"
    assert llm_calls[0]["capture_stage"] == "model_boundary"
    assert llm_calls[0]["capture_fidelity"] == "model_boundary"
    assert llm_calls[0]["request_projection"] == "aworld.standard.model_boundary.v1"
    assert llm_calls[0]["provider_prepared_request_match"] is None
    assert llm_calls[0]["compiler_request"] == {"messages": compiled_messages}
    assert llm_calls[0]["request"]["messages"] == provider_messages
    assert llm_calls[0]["request_trace_match"] is False
    assert (
        llm_calls[0]["request_trace_match_scope"]
        == "aworld.standard.model_boundary.v1"
    )
    assert llm_calls[0]["assembly_observability"]["stable_prefix_hash"] == "prefix-1"


@pytest.mark.asyncio
async def test_merge_context_appends_only_child_local_llm_calls():
    parent = Context(task_id="parent-task")
    parent.context_info["llm_calls"] = [{"request_id": "parent-call"}]

    child = await parent.build_sub_context("child-input", sub_task_id="child-task")
    child.append_llm_call({"request_id": "child-call"})

    parent.merge_context(child)

    assert parent.context_info.get("llm_calls") == [
        {"request_id": "parent-call"},
        {"request_id": "child-call"},
    ]


def test_merge_context_from_deep_copy_appends_only_new_llm_calls():
    parent = Context(task_id="parent-task")
    parent.context_info["llm_calls"] = [{"request_id": "parent-call"}]

    child = parent.deep_copy()
    child.append_llm_call({"request_id": "child-call"})

    parent.merge_context(child)

    assert parent.context_info.get("llm_calls") == [
        {"request_id": "parent-call"},
        {"request_id": "child-call"},
    ]


def test_preserved_llm_call_merge_baseline_survives_transport_copy():
    parent = Context(task_id="parent-task")
    parent.context_info["llm_calls"] = [{"request_id": "parent-call"}]

    child = parent.deep_copy()
    child.append_llm_call({"request_id": "child-call"})

    transported = child.deep_copy(preserve_merge_baseline=True)
    parent.merge_context(transported)

    assert parent.context_info.get("llm_calls") == [
        {"request_id": "parent-call"},
        {"request_id": "child-call"},
    ]


def test_merge_context_consumes_llm_call_delta_once():
    parent = Context(task_id="parent-task")
    child = parent.deep_copy()
    child.append_llm_call({"request_id": "child-call"})

    parent.merge_context(child)
    parent.merge_context(child)

    assert parent.context_info.get("llm_calls") == [
        {"request_id": "child-call"},
    ]


def test_merge_context_reconciles_duplicate_call_with_latest_snapshot():
    parent = Context(task_id="parent-task")
    parent.context_info["llm_calls"] = [
        {
            "call_id": "stable-call",
            "request_id": "request-1",
            "status": "started",
        }
    ]
    child = Context(task_id="parent-task")
    child.context_info["llm_calls"] = [
        {
            "call_id": "stable-call",
            "request_id": "request-1",
            "status": "success",
            "provider_invoked": True,
        }
    ]

    parent.merge_context(child)

    assert parent.context_info.get("llm_calls") == [
        {
            "call_id": "stable-call",
            "request_id": "request-1",
            "status": "success",
            "provider_invoked": True,
        }
    ]


def test_merge_context_preserves_distinct_provider_retry_attempts():
    parent = Context(task_id="parent-task")
    parent.context_info["llm_calls"] = [
        {"call_id": "stable-call", "request_id": "request-1", "status": "failed"}
    ]
    child = Context(task_id="parent-task")
    child.context_info["llm_calls"] = [
        {"call_id": "stable-call", "request_id": "request-2", "status": "success"}
    ]

    parent.merge_context(child)

    assert [call["request_id"] for call in parent.get_llm_calls()] == [
        "request-1",
        "request-2",
    ]


def test_merge_context_reconciles_first_bound_attempt_with_unbound_placeholder():
    parent = Context(task_id="parent-task")
    parent.context_info["llm_calls"] = [
        {"call_id": "stable-call", "status": "started"}
    ]
    child = Context(task_id="parent-task")
    child.context_info["llm_calls"] = [
        {"call_id": "stable-call", "request_id": "request-1", "status": "success"}
    ]

    parent.merge_context(child)

    assert parent.get_llm_calls() == [
        {"call_id": "stable-call", "request_id": "request-1", "status": "success"}
    ]


def test_stream_completion_appends_one_final_llm_call_record():
    provider = RecordingLLMProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-stream-sync")
    context.trace_id = "trace-stream-sync"
    messages = [{"role": "user", "content": "sync stream"}]

    chunks = list(llm_model.stream_completion(messages, context=context))

    assert [chunk.content for chunk in chunks] == ["partial", "final"]
    llm_calls = context.context_info.get("llm_calls")
    assert len(llm_calls) == 1
    assert llm_calls[0]["request"]["messages"] == messages
    assert llm_calls[0]["provider_request_id"] == "provider-stream-sync"
    assert llm_calls[0]["usage_normalized"] == {
        "prompt_tokens": 13,
        "completion_tokens": 8,
        "total_tokens": 21,
    }
    assert llm_calls[0]["response"]["finish_reason"] == "stop"
    diagnostics = llm_calls[0]["diagnostics"]
    assert diagnostics["schema_version"] == "aworld.llm_call_diagnostics.v1"
    assert diagnostics["usage"] == {
        "reported": True,
        "input_tokens": 13,
        "output_tokens": 8,
        "total_tokens": 21,
    }
    assert diagnostics["timing"]["reported"] is True
    assert diagnostics["timing"]["source"] == "framework"
    assert diagnostics["timing"]["duration_ms"] >= 0
    assert diagnostics["stream"]["reported"] is True
    assert diagnostics["stream"]["chunk_count"] == 2
    assert diagnostics["stream"]["content_chars_observed"] == len("partialfinal")
    assert diagnostics["stream"]["first_chunk_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_stream_logging_is_constant_per_call_for_many_chunks(monkeypatch):
    directions = []

    def record_log(direction, *_args, **_kwargs):
        directions.append(direction)

    monkeypatch.setattr(llm_module, "log_llm_record", record_log)
    provider = ManyChunkRecordingProvider()
    llm_model = LLMModel(custom_provider=provider)

    assert len(list(llm_model.stream_completion([{"role": "user", "content": "sync"}]))) == 1_000
    assert len(
        [
            chunk
            async for chunk in llm_model.astream_completion(
                [{"role": "user", "content": "async"}]
            )
        ]
    ) == 1_000

    assert directions.count("CHUNK") == 0
    assert directions.count("STREAM_SUMMARY") == 2
    assert directions == [
        "INPUT",
        "STREAM_SUMMARY",
        "INPUT",
        "STREAM_SUMMARY",
    ]


def test_sync_stream_early_close_closes_provider_and_logs_one_cancelled_summary(
    monkeypatch,
):
    records = []

    def record_log(direction, _model, data, *_args, **_kwargs):
        records.append((direction, data))

    monkeypatch.setattr(llm_module, "log_llm_record", record_log)
    provider = ClosableSyncStreamProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="closable-sync-stream")
    stream = llm_model.stream_completion(
        [{"role": "user", "content": "sync"}], context=context
    )

    assert next(stream).content == "partial"
    stream.close()

    assert provider.iterator is not None
    assert provider.iterator.closed is True
    summaries = [data for direction, data in records if direction == "STREAM_SUMMARY"]
    assert len(summaries) == 1
    assert summaries[0]["terminal_status"] == "cancelled"
    assert summaries[0]["terminal_error"] == "stream_closed_early"
    assert summaries[0]["chunk_count"] == 1
    assert context.get_llm_calls()[0]["status"] == "cancelled"


def test_stream_completion_uses_last_meaningful_chunk_for_llm_call_record():
    provider = TerminalMarkerStreamProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-stream-marker-sync")
    messages = [{"role": "user", "content": "sync stream"}]

    chunks = list(llm_model.stream_completion(messages, context=context))

    assert [chunk.content for chunk in chunks] == ["final", None]
    llm_call = context.context_info.get("llm_calls")[0]
    assert llm_call["provider_request_id"] == "provider-stream-sync"
    assert llm_call["usage_normalized"] == {
        "prompt_tokens": 13,
        "completion_tokens": 8,
        "total_tokens": 21,
    }
    assert llm_call["usage_raw"]["cache_hit_tokens"] == 3
    assert llm_call["response"]["message"] == {"role": "assistant", "content": "final"}
    assert llm_call["response"]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_astream_completion_appends_one_final_llm_call_record():
    provider = RecordingLLMProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-stream-async")
    context.trace_id = "trace-stream-async"
    messages = [{"role": "user", "content": "async stream"}]

    chunks = [chunk async for chunk in llm_model.astream_completion(messages, context=context)]

    assert [chunk.content for chunk in chunks] == ["partial", "final"]
    llm_calls = context.context_info.get("llm_calls")
    assert len(llm_calls) == 1
    assert llm_calls[0]["request"]["messages"] == messages
    assert llm_calls[0]["provider_request_id"] == "provider-stream-async"
    assert llm_calls[0]["usage_raw"] == {
        "prompt_tokens": 17,
        "completion_tokens": 9,
        "total_tokens": 26,
        "cache_hit_tokens": 4,
    }
    assert llm_calls[0]["response"]["finish_reason"] == "stop"
    diagnostics = llm_calls[0]["diagnostics"]
    assert diagnostics["usage"]["reported"] is True
    assert diagnostics["stream"]["reported"] is True
    assert diagnostics["stream"]["chunk_count"] == 2
    assert diagnostics["stream"]["content_chars_observed"] == len("partialfinal")


@pytest.mark.asyncio
async def test_interrupted_stream_records_unreported_usage_and_observed_timing():
    class UnreportedStreamProvider(RecordingLLMProvider):
        async def astream_completion(self, messages, **kwargs):
            self.seen_requests.append(messages)
            yield ModelResponse(
                id="unreported-stream",
                model=self.model_name,
                reasoning_content="reasoning stays in memory",
                content="partial",
            )
            while True:
                await asyncio.sleep(1)

    llm_model = LLMModel(custom_provider=UnreportedStreamProvider())
    context = Context(task_id="task-stream-unreported")
    stream = llm_model.astream_completion(
        [{"role": "user", "content": "async stream"}], context=context
    )

    chunk = await stream.__anext__()
    assert chunk.content == "partial"
    await stream.aclose()

    diagnostics = context.get_llm_calls()[0]["diagnostics"]
    assert diagnostics["usage"] == {
        "reported": False,
        "reason_code": "provider_usage_unreported",
    }
    assert diagnostics["timing"]["reported"] is True
    assert diagnostics["stream"]["reported"] is True
    assert diagnostics["stream"]["chunk_count"] == 1
    assert diagnostics["stream"]["reasoning_chars_observed"] == len(
        "reasoning stays in memory"
    )


@pytest.mark.asyncio
async def test_astream_completion_uses_last_meaningful_chunk_for_llm_call_record():
    provider = TerminalMarkerStreamProvider()
    llm_model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-stream-marker-async")
    messages = [{"role": "user", "content": "async stream"}]

    chunks = [chunk async for chunk in llm_model.astream_completion(messages, context=context)]

    assert [chunk.content for chunk in chunks] == ["final", None]
    llm_call = context.context_info.get("llm_calls")[0]
    assert llm_call["provider_request_id"] == "provider-stream-async"
    assert llm_call["usage_normalized"] == {
        "prompt_tokens": 17,
        "completion_tokens": 9,
        "total_tokens": 26,
    }
    assert llm_call["usage_raw"]["cache_hit_tokens"] == 4
    assert llm_call["response"]["message"] == {"role": "assistant", "content": "final"}
    assert llm_call["response"]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_task_response_and_trajectory_payload_include_llm_calls(monkeypatch):
    llm_calls = [
        {
            "request_id": "llm_req_123",
            "provider_request_id": "provider-req-123",
            "request": {"messages": [{"role": "user", "content": "hi"}]},
            "usage_normalized": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            "usage_raw": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "cache_hit_tokens": 1},
        }
    ]
    context = Context(task_id="task-runner")
    context.context_info["llm_calls"] = llm_calls
    task = Task(id="task-runner", name="task-runner", context=context, conf=ConfigDict())
    context.set_task(task)

    runner = TaskEventRunner(task, agent_oriented=False)
    runner.context = context
    runner._task_response = TaskResponse(id=task.id, context=context, success=True)

    response = runner._response()
    assert response.llm_calls == llm_calls
    assert response.to_dict()["llm_calls"] == llm_calls

    logged_payloads = []

    class FakeTrajectoryStep:
        def to_dict(self):
            return {"step": 1}

    async def fake_get_task_trajectory(task_id, **kwargs):
        assert task_id == task.id
        assert kwargs == {"strict": True}
        return [FakeTrajectoryStep()]

    monkeypatch.setattr(context, "get_task_trajectory", fake_get_task_trajectory)
    monkeypatch.setattr("aworld.runners.event_runner.trajectory_logger.info", logged_payloads.append)

    await runner._save_trajectories()

    assert len(logged_payloads) == 1
    payload = ast.literal_eval(logged_payloads[0])
    assert json.loads(payload["llm_calls"]) == llm_calls
