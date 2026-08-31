from __future__ import annotations

import asyncio
import json

import pytest

from aworld.config import ConfigDict  # establishes the supported model/config import order
from aworld.core.context.base import Context
from aworld.core.llm_provider import LLMProviderBase
from aworld.core.task import Task  # completes context/model initialization before LLMModel
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse


INTERNAL_CALL_ID = "_aworld_context_call_id"


class BoundaryProvider(LLMProviderBase):
    def __init__(self) -> None:
        super().__init__(model_name="boundary-model")
        self.seen: list[tuple[str, list[dict], dict]] = []

    def _init_provider(self):
        pass

    def postprocess_response(self, response, **kwargs):
        return response

    @staticmethod
    def _response(label: str) -> ModelResponse:
        return ModelResponse(
            id=f"response-{label}",
            model="boundary-model",
            content=f"answer-{label}",
            message={"role": "assistant", "content": f"answer-{label}"},
            finish_reason="stop",
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            provider_request_id=f"provider-{label}",
        )

    def completion(self, messages, **kwargs):
        self.seen.append(("sync", messages, dict(kwargs)))
        return self._response(messages[-1]["content"])

    async def acompletion(self, messages, **kwargs):
        self.seen.append(("async", messages, dict(kwargs)))
        label = messages[-1]["content"]
        await asyncio.sleep(0.01 if label == "slow" else 0)
        return self._response(label)

    def stream_completion(self, messages, **kwargs):
        self.seen.append(("stream", messages, dict(kwargs)))
        label = messages[-1]["content"]
        yield ModelResponse(
            id=f"stream-{label}",
            model="boundary-model",
            content="first",
            message={"role": "assistant", "content": "first"},
        )
        yield self._response(label)

    async def astream_completion(self, messages, **kwargs):
        self.seen.append(("astream", messages, dict(kwargs)))
        label = messages[-1]["content"]
        yield ModelResponse(
            id=f"astream-{label}",
            model="boundary-model",
            content="first",
            message={"role": "assistant", "content": "first"},
        )
        await asyncio.sleep(0)
        yield self._response(label)


class FailureProvider(BoundaryProvider):
    def completion(self, messages, **kwargs):
        self.seen.append(("sync", messages, dict(kwargs)))
        raise ValueError("provider-secret-sync")

    async def acompletion(self, messages, **kwargs):
        self.seen.append(("async", messages, dict(kwargs)))
        raise ValueError("provider-secret-async")

    def stream_completion(self, messages, **kwargs):
        self.seen.append(("stream", messages, dict(kwargs)))
        yield ModelResponse(id="failed-stream", model="boundary-model", content="partial")
        raise ValueError("provider-secret-stream")

    async def astream_completion(self, messages, **kwargs):
        self.seen.append(("astream", messages, dict(kwargs)))
        yield ModelResponse(id="failed-astream", model="boundary-model", content="partial")
        raise ValueError("provider-secret-astream")


class BlockingProvider(BoundaryProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def acompletion(self, messages, **kwargs):
        self.seen.append(("async", messages, dict(kwargs)))
        self.started.set()
        await asyncio.Event().wait()


def _compiled_record(call_id: str, content: str) -> dict:
    return {
        "capture_stage": "compiled",
        "call_id": call_id,
        "agent_id": "same-agent",
        "request": {
            "messages": [{"role": "user", "content": content}],
            "tools": None,
            "params": {"temperature": 0.0, "max_tokens": None, "stop": None},
        },
    }


def _assert_internal_kwarg_not_forwarded(provider: BoundaryProvider) -> None:
    assert provider.seen
    assert all(INTERNAL_CALL_ID not in kwargs for _, _, kwargs in provider.seen)


def test_sync_observe_correlates_exact_call_id_and_records_redacted_match() -> None:
    provider = BoundaryProvider()
    model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-sync-boundary")
    context.agent_info.current_agent_id = "same-agent"
    context.context_info["llm_calls"] = [
        _compiled_record("call-target", "compiled-secret"),
        _compiled_record("call-other", "leave-untouched"),
    ]
    messages = [{"role": "user", "content": "model-secret"}]
    tools = [
        {
            "type": "function",
            "function": {"name": "search", "parameters": {"type": "object"}},
        }
    ]
    context.context_info["llm_calls"][0]["request"]["tools"] = tools

    model.completion(
        messages,
        context=context,
        tools=tools,
        api_key="extra-kwarg-secret",
        **{INTERNAL_CALL_ID: "call-target"},
    )

    calls = context.get_llm_calls()
    assert len(calls) == 2
    target, other = calls
    assert target["call_id"] == "call-target"
    assert target["request_id"].startswith("llm_req_")
    assert target["status"] == "success"
    assert target["request"]["messages"] == messages
    assert target["compiler_request"]["messages"][0]["content"] == "compiled-secret"
    assert target["request_trace_match"] is False
    assert target["request_trace_mismatch_paths"] == ["/messages/0/content"]
    assert target["request_trace_mismatch_count"] == 1
    assert target["context_observe"]["request"]["capture_stage"] == "model_boundary"
    assert target["context_observe"]["request"]["fidelity"] == "model_boundary"
    rendered_observe = json.dumps(target["context_observe"], ensure_ascii=False)
    assert "model-secret" not in rendered_observe
    assert "compiled-secret" not in rendered_observe
    assert "extra-kwarg-secret" not in json.dumps(target, ensure_ascii=False)
    assert other == _compiled_record("call-other", "leave-untouched")
    _assert_internal_kwarg_not_forwarded(provider)
    assert provider.seen[0][1] is messages
    assert provider.seen[0][2]["tools"] is tools
    assert provider.seen[0][2]["api_key"] == "extra-kwarg-secret"


@pytest.mark.asyncio
async def test_async_concurrent_same_agent_calls_do_not_cross_correlate() -> None:
    provider = BoundaryProvider()
    model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-concurrent")
    context.agent_info.current_agent_id = "same-agent"
    context.context_info["llm_calls"] = [
        _compiled_record("call-slow", "slow"),
        _compiled_record("call-fast", "fast"),
    ]

    await asyncio.gather(
        model.acompletion(
            [{"role": "user", "content": "slow"}],
            context=context,
            **{INTERNAL_CALL_ID: "call-slow"},
        ),
        model.acompletion(
            [{"role": "user", "content": "fast"}],
            context=context,
            **{INTERNAL_CALL_ID: "call-fast"},
        ),
    )

    records = {item["call_id"]: item for item in context.get_llm_calls()}
    assert records["call-slow"]["request"]["messages"][-1]["content"] == "slow"
    assert records["call-slow"]["provider_request_id"] == "provider-slow"
    assert records["call-fast"]["request"]["messages"][-1]["content"] == "fast"
    assert records["call-fast"]["provider_request_id"] == "provider-fast"
    assert all(item["status"] == "success" for item in records.values())
    assert all(item["request_trace_match"] is True for item in records.values())
    assert all(item["request_trace_mismatch_paths"] == [] for item in records.values())
    _assert_internal_kwarg_not_forwarded(provider)


@pytest.mark.asyncio
async def test_provider_failure_and_cancellation_keep_one_terminal_snapshot() -> None:
    failure_provider = FailureProvider()
    failure_model = LLMModel(custom_provider=failure_provider)
    sync_failed_context = Context(task_id="task-sync-failed")

    with pytest.raises(ValueError, match="provider-secret-sync"):
        failure_model.completion(
            [{"role": "user", "content": "sync-failure-input"}],
            context=sync_failed_context,
            **{INTERNAL_CALL_ID: "direct-sync-failure"},
        )

    sync_failed = sync_failed_context.get_llm_calls()
    assert len(sync_failed) == 1
    assert sync_failed[0]["status"] == "failed"
    assert sync_failed[0]["error"] == {"code": "provider_call_failed"}
    assert "provider-secret-sync" not in json.dumps(sync_failed[0])

    failed_context = Context(task_id="task-failed")

    with pytest.raises(RuntimeError):
        await failure_model.acompletion(
            [{"role": "user", "content": "failure-input"}],
            context=failed_context,
            **{INTERNAL_CALL_ID: "direct-failure"},
        )

    failed = failed_context.get_llm_calls()
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["error"] == {"code": "provider_call_failed"}
    assert failed[0]["request"]["messages"][0]["content"] == "failure-input"
    assert "provider-secret-async" not in json.dumps(failed[0])
    _assert_internal_kwarg_not_forwarded(failure_provider)

    blocking_provider = BlockingProvider()
    blocking_model = LLMModel(custom_provider=blocking_provider)
    cancelled_context = Context(task_id="task-cancelled")
    task = asyncio.create_task(
        blocking_model.acompletion(
            [{"role": "user", "content": "cancel-input"}],
            context=cancelled_context,
        )
    )
    await blocking_provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    cancelled = cancelled_context.get_llm_calls()
    assert len(cancelled) == 1
    assert cancelled[0]["status"] == "cancelled"
    assert cancelled[0]["error"] == {"code": "provider_call_cancelled"}
    assert cancelled[0]["request"]["messages"][0]["content"] == "cancel-input"


def test_sync_stream_close_and_exception_finalize_once() -> None:
    provider = BoundaryProvider()
    model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-stream-close")
    stream = model.stream_completion(
        [{"role": "user", "content": "close"}],
        context=context,
        **{INTERNAL_CALL_ID: "stream-close"},
    )
    assert next(stream).content == "first"
    stream.close()

    calls = context.get_llm_calls()
    assert len(calls) == 1
    assert calls[0]["status"] == "cancelled"
    assert calls[0]["error"] == {"code": "stream_closed_early"}
    _assert_internal_kwarg_not_forwarded(provider)

    failed_provider = FailureProvider()
    failed_model = LLMModel(custom_provider=failed_provider)
    failed_context = Context(task_id="task-stream-failure")
    with pytest.raises(ValueError, match="provider-secret-stream"):
        list(
            failed_model.stream_completion(
                [{"role": "user", "content": "stream-failure"}],
                context=failed_context,
            )
        )
    failed = failed_context.get_llm_calls()
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["error"] == {"code": "provider_stream_failed"}
    assert "provider-secret-stream" not in json.dumps(failed[0])


@pytest.mark.asyncio
async def test_async_stream_close_and_exception_finalize_once() -> None:
    provider = BoundaryProvider()
    model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-astream-close")
    stream = model.astream_completion(
        [{"role": "user", "content": "close"}],
        context=context,
        **{INTERNAL_CALL_ID: "astream-close"},
    )
    assert (await anext(stream)).content == "first"
    await stream.aclose()

    calls = context.get_llm_calls()
    assert len(calls) == 1
    assert calls[0]["status"] == "cancelled"
    assert calls[0]["error"] == {"code": "stream_closed_early"}
    _assert_internal_kwarg_not_forwarded(provider)

    failed_provider = FailureProvider()
    failed_model = LLMModel(custom_provider=failed_provider)
    failed_context = Context(task_id="task-astream-failure")
    with pytest.raises(ValueError, match="provider-secret-astream"):
        _ = [
            chunk
            async for chunk in failed_model.astream_completion(
                [{"role": "user", "content": "astream-failure"}],
                context=failed_context,
            )
        ]
    failed = failed_context.get_llm_calls()
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["error"] == {"code": "provider_stream_failed"}
    assert "provider-secret-astream" not in json.dumps(failed[0])


def test_observe_failure_is_typed_redacted_and_fail_open(monkeypatch) -> None:
    provider = BoundaryProvider()
    model = LLMModel(custom_provider=provider)
    context = Context(task_id="task-observe-fail-open")

    def fail_observe(**kwargs):
        raise ValueError("observe-secret")

    monkeypatch.setattr(
        "aworld.models.llm.observe_legacy_provider_request",
        fail_observe,
        raising=False,
    )
    response = model.completion(
        [{"role": "user", "content": "still-call-provider"}],
        context=context,
    )

    assert response.content == "answer-still-call-provider"
    assert len(provider.seen) == 1
    record = context.get_llm_calls()[0]
    assert record["status"] == "success"
    assert record["context_observe"] == {
        "status": "error",
        "error": {"code": "context_observe_failed"},
    }
    assert "observe-secret" not in json.dumps(record)
