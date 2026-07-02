import asyncio
import ast
import json
from types import SimpleNamespace

import pytest

from aworld.config import ConfigDict
from aworld.core.context.base import Context
from aworld.core.task import Task, TaskResponse
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.core.llm_provider import LLMProviderBase
from aworld.runners.event_runner import TaskEventRunner


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

    async def fake_get_task_trajectory(task_id):
        assert task_id == task.id
        return [FakeTrajectoryStep()]

    monkeypatch.setattr(context, "get_task_trajectory", fake_get_task_trajectory)
    monkeypatch.setattr("aworld.runners.event_runner.trajectory_logger.info", logged_payloads.append)

    await runner._save_trajectories()

    assert len(logged_payloads) == 1
    payload = ast.literal_eval(logged_payloads[0])
    assert json.loads(payload["llm_calls"]) == llm_calls
