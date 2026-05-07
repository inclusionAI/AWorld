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
