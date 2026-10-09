"""Provider serialization and a real local HTTP model/tool/model roundtrip."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

import pytest

from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ModelRequest, ToolCall, ToolResultMessage, UserMessage
from aworld.core.session import RunStatus, create_session
from aworld.core.tool import Tool
from aworld.models.chat_completions import ChatCompletionsModel, parse_response, request_payload


@pytest.fixture(autouse=True)
def sdk_retry_without_test_delay(monkeypatch):
    from openai import AsyncOpenAI
    monkeypatch.setattr(AsyncOpenAI, "_calculate_retry_timeout", lambda *args: 0)


async def mock_provider_client(model, handler):
    import httpx
    from aworld.models.openai_provider import OpenAIProvider
    assert isinstance(model._provider, OpenAIProvider)
    client = model._provider.async_provider
    await client.close()
    model._provider.async_provider = client.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def payload(message, reason="stop"):
    return {"choices": [{"message": {"role": "assistant", **message}, "finish_reason": reason}]}


def test_message_roles_tool_ids_json_and_empty_capability_set():
    request = ModelRequest("instructions", (UserMessage("hello"), AssistantMessage("", (ToolCall("id", "sum", {"a": 2}),)), ToolResultMessage("id", "sum", {"value": 5}, True)), ())
    value = request_payload(request, "configured-model", "none")
    assert [item["role"] for item in value["messages"]] == ["system", "user", "assistant", "tool"]
    assert value["messages"][-1]["tool_call_id"] == "id"
    assert json.loads(value["messages"][-1]["content"])["is_error"] is True
    assert "tools" not in value
    assert value["reasoning_effort"] == "none"


def test_real_cli_profile_compaction_and_summary_usage_reach_atif(tmp_path):
    requests, main_calls, summary_calls = [], [], []
    (tmp_path / "note").write_text("verified file content\n" + "x" * 1900)
    config = tmp_path / ".aworld"
    config.mkdir()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if "tools" not in body:
                summary_calls.append(body)
                result = payload({"content": "Goal: inspect note. File content verified; continue inspection."})
            else:
                main_calls.append(body)
                n = len(main_calls)
                result = payload({"content": "verified"}) if n > 10 else payload({"tool_calls": [{
                    "id": f"read-{n}", "type": "function", "function": {"name": "read", "arguments": '{"path":"note"}'}}]}, "tool_calls")
            result["usage"] = {"prompt_tokens": 100, "completion_tokens": 20,
                               "prompt_tokens_details": {"cached_tokens": 50}}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (config / "aworld.json").write_text(json.dumps({"models": {"fixture": {
        "model": "local-fixture", "provider": "openai", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "max_model_len": 8000, "max_tokens": 1000, "api_key_env": "FIXTURE_KEY"}}}))
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("AWORLD_", "OPENAI_", "LLM_"))}
    environment["FIXTURE_KEY"] = "local-test-key"
    try:
        result = subprocess.run([sys.executable, "-m", "aworld", "run", "--model-profile", "fixture", "--cwd", str(tmp_path),
            "--tools", "read", "--no-skills", "--system-prompt", "Inspect files and retain verified facts.",
            "--task", "Inspect note ten times", "--max-turns", "15", "--keep-recent-tokens", "1200",
            "--summary-max-tokens", "200", "--compaction-trigger-ratio", ".6", "--json",
            "--trajectory-output", str(tmp_path / "trajectory.json")], cwd=Path(__file__).resolve().parents[1],
            env=environment, capture_output=True, text=True, timeout=20)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["output"] == "verified"
    assert summary_calls
    assert all(body["max_tokens"] == 1000 for body in main_calls)
    assert all(body["max_tokens"] == 200 for body in summary_calls)
    assert "<context-summary>" in str(main_calls[-1]["messages"])
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert trajectory["extra"]["context_budget"]["context_window"] == 8000
    assert trajectory["extra"]["context_budget"]["window_source"] == "profile"
    assert trajectory["extra"]["run_metrics"]["input_tokens"] == 100 * len(requests)
    assert trajectory["extra"]["run_metrics"]["output_tokens"] == 20 * len(requests)
    assert trajectory["extra"]["compaction"]["completed"] == len(summary_calls)


@pytest.mark.parametrize("response", [
    payload({"content": "partial"}, "length"), payload({"content": "filtered"}, "content_filter"),
    payload({"content": "", "refusal": "refused"}), payload({"content": ""}),
    payload({"tool_calls": [{"id": "id", "type": "function", "function": {"name": "read", "arguments": "[]"}}]}, "tool_calls"),
    payload({"content": "answer"}, "tool_calls"), {"choices": []},
])
def test_invalid_or_incomplete_responses_cannot_trigger_success_or_tools(response):
    with pytest.raises((ValueError, TypeError)):
        parse_response(response)


def test_actual_http_tool_roundtrip_uses_original_provider():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if body["messages"][-1]["role"] == "tool":
                result = payload({"content": "5"})
            else:
                result = payload({"content": None, "tool_calls": [{"id": "sum-1", "type": "function", "function": {"name": "sum", "arguments": '{"a":2,"b":3}'}}]}, "tool_calls")
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        model = ChatCompletionsModel(model="local-test", base_url=f"http://127.0.0.1:{server.server_port}/v1")
        from aworld.models.openai_provider import OpenAIProvider
        assert isinstance(model._provider, OpenAIProvider)
        assert model._provider.provider is None
        async def sum_values(arguments, context):
            return arguments["a"] + arguments["b"]
        session = await create_session(agent=Agent(model=model, tools=[Tool("sum", "Add", {"type": "object"}, sum_values)]))
        try:
            result = await (await session.submit("add")).result()
            assert result.status == RunStatus.COMPLETED
            assert result.output == "5"
        finally:
            await session.close()
            await model.aclose()
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert len(requests) == 2
    assert requests[0]["tools"][0]["function"]["name"] == "sum"
    assert requests[1]["messages"][-1]["tool_call_id"] == "sum-1"


def test_real_cli_http_model_reads_a_local_file(tmp_path):
    (tmp_path / "note").write_text("provider tool output")
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if body["messages"][-1]["role"] == "tool":
                content = body["messages"][-1]["content"]
                text = content if isinstance(content, str) else "".join(part["text"] for part in content)
                tool = json.loads(text)
                assert "provider tool output" in tool["result"]["content"]
                result = payload({"content": "verified"})
            else:
                result = payload({"tool_calls": [{"id": "read-note", "type": "function", "function": {
                    "name": "read", "arguments": '{"path":"note"}'}}]}, "tool_calls")
            result["usage"] = {"prompt_tokens": 100 * len(requests),
                               "completion_tokens": 10 * len(requests),
                               "prompt_tokens_details": {"cached_tokens": 80 if len(requests) == 1 else 150}}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = dict(os.environ)
    environment["AWORLD_API_KEY"] = "local-fixture-key"
    try:
        result = subprocess.run([sys.executable, "-m", "aworld", "run", "--model", "local-fixture", "--base-url",
            f"http://127.0.0.1:{server.server_port}/v1", "--cwd", str(tmp_path), "--task", "read note", "--json",
            "--trajectory-output", str(tmp_path / "trajectory.json")],
            cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["output"] == "verified"
    assert len(requests) == 2
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert trajectory["extra"]["token_usage"] == "reported"
    metrics = trajectory["extra"]["run_metrics"]
    assert (metrics["input_tokens"], metrics["output_tokens"], metrics["cache_read_tokens"], metrics["total_tokens"]) == (300, 30, 230, 330)
    assert metrics["reported_calls"] == metrics["model_calls"] == 2


def test_cli_ctrl_c_during_http_request_exits_and_cleans_up():
    started, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            started.set()
            release.wait(10)
            self.send_response(200)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = subprocess.Popen([sys.executable, "-m", "aworld", "run", "--model", "local-fixture", "--base-url",
        f"http://127.0.0.1:{server.server_port}/v1", "--task", "wait"],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert started.wait(5), "CLI did not reach the model endpoint"
        process.send_signal(signal.SIGINT)
        output, error = process.communicate(timeout=3)
        assert process.returncode == 130, error
        assert "Traceback" not in error
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_disconnected_followup_retries_same_request_without_replaying_tool(monkeypatch):
    requests, effects = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if len(requests) in (2, 3):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            result = payload({"content": "done"}) if len(requests) == 4 else payload({
                "tool_calls": [{"id": "effect-1", "type": "function", "function": {
                    "name": "effect", "arguments": "{}"}}]}, "tool_calls")
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        model = ChatCompletionsModel(model="fixture", base_url=f"http://127.0.0.1:{server.server_port}/v1", max_retries=2)
        async def effect(arguments, context):
            effects.append("executed")
            return "written"
        session = await create_session(agent=Agent(model=model, tools=[Tool("effect", "Side effect", {"type": "object"}, effect)]))
        try:
            result = await (await session.submit("do it")).result()
            assert result.status == RunStatus.COMPLETED
            assert result.output == "done"
        finally:
            await session.close()
            await model.aclose()
    try:
        asyncio.run(run())
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert effects == ["executed"]
    assert len(requests) == 4
    assert requests[1] == requests[2] == requests[3]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_failure_recovers(monkeypatch, status):
    import httpx
    calls = []
    async def run():
        model = ChatCompletionsModel(model="fixture", base_url="http://fixture/v1")
        def handle(request):
            calls.append(request.content)
            return httpx.Response(status if len(calls) == 1 else 200,
                                  json=payload({"content": "ok"}), headers={"Retry-After": "0"})
        await mock_provider_client(model, handle)
        try:
            assert (await model.complete(ModelRequest("", (UserMessage("hello"),), ()))).content == "ok"
        finally:
            await model.aclose()
    asyncio.run(run())
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_errors_do_not_retry(status):
    import httpx
    calls = []
    async def run():
        model = ChatCompletionsModel(model="fixture", base_url="http://fixture/v1", api_key="private-fixture")
        def handle(request):
            calls.append(request)
            return httpx.Response(status, text="private server response")
        await mock_provider_client(model, handle)
        try:
            with pytest.raises(RuntimeError, match=f"HTTP {status}") as error:
                await model.complete(ModelRequest("", (UserMessage("hello"),), ()))
            assert "private" not in str(error.value)
        finally:
            await model.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_retry_limit_and_cancellation_during_backoff(monkeypatch):
    import httpx
    async def run():
        calls = []
        model = ChatCompletionsModel(model="fixture", base_url="http://fixture/v1", max_retries=2)
        def disconnect(request):
            calls.append(request)
            raise httpx.RemoteProtocolError("private transport details")
        await mock_provider_client(model, disconnect)
        request = ModelRequest("", (UserMessage("hello"),), ())
        try:
            with pytest.raises(RuntimeError, match="RemoteProtocolError") as error:
                await model.complete(request)
            assert "private" not in str(error.value)
            assert len(calls) == 3
            calls.clear()
            monkeypatch.setattr(type(model._provider.async_provider), "_calculate_retry_timeout", lambda *args: 30)
            pending = asyncio.create_task(model.complete(request))
            await asyncio.sleep(0.01)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, timeout=1)
            assert len(calls) == 1
        finally:
            await model.aclose()
    asyncio.run(run())


def test_malformed_responses_still_fail_without_retry():
    import httpx
    calls = []
    async def run():
        model = ChatCompletionsModel(model="fixture", base_url="http://fixture/v1")
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json=payload({"content": "partial"}, "length"))
        await mock_provider_client(model, handle)
        try:
            with pytest.raises(ValueError, match="incomplete"):
                await model.complete(ModelRequest("", (UserMessage("hello"),), ()))
        finally:
            await model.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_injected_provider_receives_only_model_input_and_is_not_owned():
    from types import SimpleNamespace
    from aworld.models.chat_completions import ProviderModel
    calls = []
    class Provider:
        async def acompletion(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(raw_response=payload({"content": "adapted"}))
    async def run():
        model = ProviderModel(Provider(), model="existing", reasoning_effort="none")
        result = await model.complete(ModelRequest("system", (UserMessage("hello"),), ()))
        assert result.content == "adapted"
        await model.aclose()
    asyncio.run(run())
    assert calls == [{"messages": [{"role": "system", "content": "system"},
                                  {"role": "user", "content": "hello"}],
                      "reasoning_effort": "none"}]


def test_original_retry_disable_flag_is_respected(monkeypatch):
    monkeypatch.setenv("AWORLD_SELF_EVOLVE_DISABLE_PROVIDER_RETRIES", "1")
    async def run():
        model = ChatCompletionsModel(model="fixture", base_url="http://fixture/v1", max_retries=3)
        try:
            assert model._provider.async_provider.max_retries == 0
        finally:
            await model.aclose()
    asyncio.run(run())


def test_provider_import_does_not_spawn_an_installer():
    code = """
import subprocess, asyncio
from unittest.mock import patch
with patch.object(subprocess, 'Popen', side_effect=AssertionError('unexpected installation')):
 from aworld.models.chat_completions import ChatCompletionsModel
 model = ChatCompletionsModel(model='fixture', base_url='http://127.0.0.1:9999/v1')
 asyncio.run(model.aclose())
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_sdk_reasoning_replay_survives_empty_response_without_repeating_tool():
    import httpx
    from aworld.cli.trajectory import build_trajectory
    requests, effects = [], []
    reasoning = "private fixture reasoning before the tool"
    rejected_reasoning = "private fixture reasoning with no delivered action"

    async def run():
        model = ChatCompletionsModel(model="matrixllm.aisearch_dsv41flash", base_url="http://fixture/v1",
                                    default_parameters={"reasoning_effort": "high"})
        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                result = payload({"content": None, "reasoning_content": reasoning,
                    "tool_calls": [{"id": "effect-once", "type": "function", "function": {
                        "name": "effect", "arguments": "{}"}}]}, "tool_calls")
            else:
                # A strict compatible server requires the prior reasoning.
                prior = next(m for m in body["messages"] if m["role"] == "assistant")
                assert prior["reasoning_content"] == reasoning
                assert body["messages"][-1]["role"] == "tool"
                result = payload({"content": None, "reasoning_content": rejected_reasoning}) if len(requests) == 2 else payload({"content": "done", "reasoning_content": "finished"})
            result["usage"] = {"prompt_tokens": 10, "completion_tokens": 5}
            return httpx.Response(200, json=result)
        await mock_provider_client(model, handle)
        async def effect(arguments, context):
            effects.append("executed")
            return "written"
        agent = Agent(model=model, tools=[Tool("effect", "Side effect", {"type": "object"}, effect)],
                      system_prompt="Complete the requested tool action.", response_retry_delay=0)
        session = await create_session(agent=agent)
        try:
            handle = await session.submit("do it")
            result = await handle.result()
            assert result.status == RunStatus.COMPLETED and result.output == "done", result.error
            events = [e async for e in handle.events()]
            assert sum(e.type == "model.retry.scheduled" for e in events) == 1
            trajectory = build_trajectory(await session.history(), result=result, agent=agent, events=events)
            assert trajectory["extra"]["run_metrics"]["model_calls"] == 3
            assert trajectory["extra"]["run_metrics"]["total_tokens"] == 45
            steps = [s for s in trajectory['steps'] if s['source'] == 'agent']
            assert steps[0]['extra']['reasoning_chars'] == len(reasoning)
            assert steps[1]['extra']['diagnostics']['replayed_reasoning_chars'] == len(reasoning)
            assert steps[2]['extra']['replayed_reasoning_chars'] == len(reasoning)
            assert reasoning not in str(trajectory) and rejected_reasoning not in str(trajectory)
        finally:
            await session.close()
            await model.aclose()
    asyncio.run(run())
    assert len(requests) == 3 and effects == ["executed"]
    assert requests[1]["messages"][1:] == requests[2]["messages"][1:]
    assert requests[1]["messages"][0] != requests[2]["messages"][0]
