from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.output.base import ChunkOutput, MessageOutput, ToolResultOutput

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.server import AcpExecutorOutputBridge, AcpStdioServer
from aworld_cli.acp.session_store import AcpSessionRecord


@pytest.mark.asyncio
async def test_prompt_streams_executor_outputs_through_adapter_and_mapper() -> None:
    class FakeOutputBridge:
        async def stream_outputs(self, *, record, prompt_text):
            assert prompt_text == "hello"
            assert record.aworld_session_id.startswith("aworld_")
            yield ChunkOutput(
                data=ModelResponse(id="resp-1", model="demo", content="hel"),
                metadata={},
            )
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-2",
                    model="demo",
                    content="hello",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            function=Function(
                                name="shell",
                                arguments='{"command":"pwd"}',
                            ),
                        )
                    ],
                )
            )
            yield ToolResultOutput(
                tool_name="shell",
                data={"cwd": "/tmp"},
                origin_tool_call=ToolCall(
                    id="call-1",
                    function=Function(name="shell", arguments='{"command":"pwd"}'),
                ),
            )

    server = AcpStdioServer(output_bridge=FakeOutputBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        3,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "hello"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert response["result"]["status"] == "completed"
    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "agent_message_chunk",
        "tool_call",
        "tool_call_update",
    ]
    assert notifications[0]["params"]["update"]["content"]["text"] == "hel"
    assert notifications[1]["params"]["update"]["toolCallId"] == "call-1"
    assert notifications[1]["params"]["update"]["content"] == {"command": "pwd"}
    assert notifications[2]["params"]["update"]["status"] == "completed"
    assert notifications[2]["params"]["update"]["content"] == {"cwd": "/tmp"}


@pytest.mark.asyncio
async def test_executor_output_bridge_streams_existing_executor_outputs(monkeypatch) -> None:
    class FakeAgent:
        context_config = None
        hooks = None

        async def get_swarm(self, _context):
            return "fake-swarm"

    class FakeRegistry:
        agent = FakeAgent()

        @classmethod
        def get_agent(cls, agent_id: str):
            if agent_id == "Aworld":
                return cls.agent
            return None

        @classmethod
        def list_agents(cls):
            return [cls.agent]

    class FakeExecutor:
        instances: list["FakeExecutor"] = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.calls: list[tuple[str, str]] = []
            self.cleaned = False
            type(self).instances.append(self)

        async def _build_task(self, prompt_text: str, *, session_id: str):
            self.calls.append((prompt_text, session_id))
            return "TASK"

        async def cleanup_resources(self) -> None:
            self.cleaned = True

    class FakeStreamingOutputs:
        async def stream_events(self):
            yield ChunkOutput(
                data=ModelResponse(id="resp-1", model="demo", content="hi"),
                metadata={},
            )
            yield MessageOutput(
                source=ModelResponse(id="resp-2", model="demo", content="hi")
            )

    from aworld_cli.acp import server as server_module

    monkeypatch.setattr(
        server_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )

    record = AcpSessionRecord(
        acp_session_id="acp-1",
        aworld_session_id="aworld-1",
        cwd=".",
        requested_mcp_servers=[],
    )
    bridge = AcpExecutorOutputBridge(
        registry_cls=FakeRegistry,
        executor_cls=FakeExecutor,
        init_agents_func=lambda *_args, **_kwargs: None,
    )

    outputs = [
        output
        async for output in bridge.stream_outputs(
            record=record,
            prompt_text="hello",
        )
    ]

    assert len(outputs) == 2
    assert FakeExecutor.instances[0].calls == [("hello", "aworld-1")]
    assert FakeExecutor.instances[0].kwargs["swarm"] == "fake-swarm"
    assert FakeExecutor.instances[0].cleaned is True


@pytest.mark.asyncio
async def test_executor_output_bridge_falls_back_to_message_output_when_no_agent_loaded() -> None:
    class EmptyRegistry:
        @staticmethod
        def get_agent(_agent_id: str):
            return None

        @staticmethod
        def list_agents():
            return []

    record = AcpSessionRecord(
        acp_session_id="acp-1",
        aworld_session_id="aworld-1",
        cwd=".",
        requested_mcp_servers=[],
    )
    bridge = AcpExecutorOutputBridge(
        registry_cls=EmptyRegistry,
        executor_cls=object,
        init_agents_func=lambda *_args, **_kwargs: None,
    )

    outputs = [
        output
        async for output in bridge.stream_outputs(
            record=record,
            prompt_text="hello",
        )
    ]

    assert len(outputs) == 1
    assert isinstance(outputs[0], MessageOutput)
    assert outputs[0].source.content == "hello"
