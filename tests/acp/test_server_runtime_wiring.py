from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.output.base import ChunkOutput, MessageOutput, ToolResultOutput

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.server import AcpExecutorOutputBridge, AcpStdioServer
from aworld_cli.acp.human_intercept import AcpRequiresHumanError
from aworld_cli.acp.errors import AWORLD_ACP_APPROVAL_UNSUPPORTED
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
async def test_prompt_resets_turn_local_runtime_state_between_prompts() -> None:
    class FakeOutputBridge:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_outputs(self, *, record, prompt_text):
            self.calls += 1
            if self.calls == 1:
                yield ChunkOutput(
                    data=ModelResponse(id="resp-1", model="demo", content="first-turn"),
                    metadata={},
                )
                return

            yield MessageOutput(
                source=ModelResponse(id="resp-2", model="demo", content="second-turn-final"),
            )

    server = AcpStdioServer(output_bridge=FakeOutputBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    first_response = await server._handle_prompt(
        3,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "first"}]},
        },
    )
    second_response = await server._handle_prompt(
        4,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "second"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert first_response["result"]["status"] == "completed"
    assert second_response["result"]["status"] == "completed"
    assert [item["params"]["update"]["content"]["text"] for item in notifications] == [
        "first-turn",
        "second-turn-final",
    ]


@pytest.mark.asyncio
async def test_current_acp_protocol_suppresses_shell_tool_notifications() -> None:
    class FakeOutputBridge:
        async def stream_outputs(self, *, record, prompt_text):
            tool_call = ToolCall(
                id="call-1",
                function=Function(name="shell", arguments='{"command":"pwd"}'),
            )
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-2",
                    model="demo",
                    content="final",
                    reasoning_content="searching sources",
                    tool_calls=[tool_call],
                ),
                metadata={"sender": "Aworld", "is_finished": True},
            )
            yield ToolResultOutput(
                tool_name="shell",
                data={"cwd": "/tmp"},
                origin_tool_call=tool_call,
            )

    server = AcpStdioServer(output_bridge=FakeOutputBridge())
    server._session_update_method = "session/update"
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

    notifications = [message for message in writes if message.get("method") == "session/update"]

    assert response["result"]["status"] == "completed"
    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "agent_thought_chunk",
        "agent_message_chunk",
    ]
    assert notifications[0]["params"]["update"]["content"] == {"type": "text", "text": "searching sources"}
    assert notifications[1]["params"]["update"]["content"] == {"type": "text", "text": "final"}


@pytest.mark.asyncio
async def test_current_acp_protocol_suppresses_other_tool_notifications() -> None:
    class FakeOutputBridge:
        async def stream_outputs(self, *, record, prompt_text):
            tool_call = ToolCall(
                id="call-1",
                function=Function(
                    name="async_spawn_subagent__spawn",
                    arguments='{"items":[{"type":"content","content":{"type":"text","text":"search deepseek v4"}},{"type":"text","text":"web_searcher"}]}',
                ),
            )
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-2",
                    model="demo",
                    content="final",
                    reasoning_content="我先尝试搜索一下最新信息",
                    tool_calls=[tool_call],
                ),
                metadata={"sender": "Aworld", "is_finished": True},
            )
            yield ToolResultOutput(
                tool_name="async_spawn_subagent__spawn",
                data={"error": "subagent unavailable"},
                origin_tool_call=tool_call,
            )

    server = AcpStdioServer(output_bridge=FakeOutputBridge())
    server._session_update_method = "session/update"
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        4,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "hello"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "session/update"]

    assert response["result"]["status"] == "completed"
    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "agent_thought_chunk",
        "agent_message_chunk",
    ]
    assert notifications[0]["params"]["update"]["content"] == {"type": "text", "text": "我先尝试搜索一下最新信息"}
    assert notifications[1]["params"]["update"]["content"] == {"type": "text", "text": "final"}


@pytest.mark.asyncio
async def test_current_acp_protocol_suppresses_execute_tool_notifications() -> None:
    class FakeOutputBridge:
        async def stream_outputs(self, *, record, prompt_text):
            tool_call = ToolCall(
                id="call-1",
                function=Function(name="execute", arguments='{"command":"curl https://x.com"}'),
            )
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-2",
                    model="demo",
                    content="final",
                    reasoning_content="我先检查一下页面",
                    tool_calls=[tool_call],
                ),
                metadata={"sender": "Aworld", "is_finished": True},
            )
            yield ToolResultOutput(
                tool_name="execute",
                data={"stdout": "ok"},
                origin_tool_call=tool_call,
            )

    server = AcpStdioServer(output_bridge=FakeOutputBridge())
    server._session_update_method = "session/update"
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        5,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "hello"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "session/update"]

    assert response["result"]["status"] == "completed"
    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "agent_thought_chunk",
        "agent_message_chunk",
    ]
    assert notifications[0]["params"]["update"]["content"] == {"type": "text", "text": "我先检查一下页面"}
    assert notifications[1]["params"]["update"]["content"] == {"type": "text", "text": "final"}


@pytest.mark.asyncio
async def test_prompt_rejects_unsupported_prompt_content_with_structured_error() -> None:
    server = AcpStdioServer(output_bridge=object())
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        7,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "image", "url": "file:///tmp/demo.png"}]},
        },
    )

    assert response["error"]["message"] == "AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT"
    assert response["error"]["data"]["code"] == "AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT"


def test_new_session_rejects_unsupported_mcp_servers_with_structured_error() -> None:
    server = AcpStdioServer(output_bridge=object())

    detail = server._known_error_detail("AWORLD_ACP_UNSUPPORTED_MCP_SERVERS")

    with pytest.raises(ValueError, match="AWORLD_ACP_UNSUPPORTED_MCP_SERVERS"):
        server._handle_new_session({"cwd": ".", "mcpServers": "bad-shape"})

    assert detail is not None
    assert detail.code == "AWORLD_ACP_UNSUPPORTED_MCP_SERVERS"


@pytest.mark.asyncio
async def test_prompt_translates_human_intercept_error_to_retryable_structured_error() -> None:
    class HumanBridge:
        async def stream_outputs(self, *, record, prompt_text):
            raise AcpRequiresHumanError("Human approval/input flow is not bridged in ACP mode.")
            yield  # pragma: no cover

    server = AcpStdioServer(output_bridge=HumanBridge())
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        8,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    assert response["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."
    assert response["error"]["data"]["code"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["data"]["retryable"] is True


@pytest.mark.asyncio
async def test_prompt_closes_open_tool_lifecycle_before_requires_human_failure() -> None:
    class HumanBridge:
        async def stream_outputs(self, *, record, prompt_text):
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-tool-start",
                    model="demo",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            function=Function(name="shell", arguments='{"command":"pwd"}'),
                        )
                    ],
                )
            )
            raise AcpRequiresHumanError("Human approval/input flow is not bridged in ACP mode.")

    server = AcpStdioServer(output_bridge=HumanBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        9,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "tool_call",
        "tool_call_update",
    ]
    assert notifications[1]["params"]["update"]["toolCallId"] == "call-1"
    assert notifications[1]["params"]["update"]["status"] == "failed"
    assert notifications[1]["params"]["update"]["content"]["code"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."


@pytest.mark.asyncio
async def test_prompt_translates_approval_unsupported_to_retryable_structured_error() -> None:
    class ApprovalBridge:
        async def stream_outputs(self, *, record, prompt_text):
            raise ValueError(AWORLD_ACP_APPROVAL_UNSUPPORTED)
            yield  # pragma: no cover

    server = AcpStdioServer(output_bridge=ApprovalBridge())
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        10,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "approval path"}]},
        },
    )

    assert response["error"]["message"] == AWORLD_ACP_APPROVAL_UNSUPPORTED
    assert response["error"]["data"]["message"] == "Approval flow is not bridged in phase 1."
    assert response["error"]["data"]["code"] == AWORLD_ACP_APPROVAL_UNSUPPORTED
    assert response["error"]["data"]["retryable"] is True


@pytest.mark.asyncio
async def test_prompt_treats_runtime_turn_error_as_terminal_structured_failure() -> None:
    class TurnErrorBridge:
        async def stream_outputs(self, *, record, prompt_text):
            yield {
                "event_type": "turn_error",
                "seq": 1,
                "code": "AWORLD_ACP_REQUIRES_HUMAN",
                "message": "Human approval/input flow is not bridged in phase 1.",
                "retryable": True,
                "origin": "runtime",
            }

    server = AcpStdioServer(output_bridge=TurnErrorBridge())
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        11,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    assert response["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."
    assert response["error"]["data"]["code"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["data"]["retryable"] is True


@pytest.mark.asyncio
async def test_prompt_closes_open_tool_lifecycle_before_runtime_turn_error_failure() -> None:
    class TurnErrorBridge:
        async def stream_outputs(self, *, record, prompt_text):
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-tool-start",
                    model="demo",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            function=Function(name="shell", arguments='{"command":"pwd"}'),
                        )
                    ],
                )
            )
            yield {
                "event_type": "turn_error",
                "seq": 2,
                "code": "AWORLD_ACP_REQUIRES_HUMAN",
                "message": "Human approval/input flow is not bridged in phase 1.",
                "retryable": True,
                "origin": "runtime",
            }

    server = AcpStdioServer(output_bridge=TurnErrorBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        12,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "tool_call",
        "tool_call_update",
    ]
    assert notifications[1]["params"]["update"]["status"] == "failed"
    assert notifications[1]["params"]["update"]["content"]["code"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."


@pytest.mark.asyncio
async def test_prompt_closes_all_open_same_name_tool_lifecycles_before_runtime_turn_error_failure() -> None:
    class TurnErrorBridge:
        async def stream_outputs(self, *, record, prompt_text):
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-tool-start-1",
                    model="demo",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            function=Function(name="shell", arguments='{"command":"pwd"}'),
                        )
                    ],
                )
            )
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-tool-start-2",
                    model="demo",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-2",
                            function=Function(name="shell", arguments='{"command":"ls"}'),
                        )
                    ],
                )
            )
            yield {
                "event_type": "turn_error",
                "seq": 3,
                "code": "AWORLD_ACP_REQUIRES_HUMAN",
                "message": "Human approval/input flow is not bridged in phase 1.",
                "retryable": True,
                "origin": "runtime",
            }

    server = AcpStdioServer(output_bridge=TurnErrorBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        15,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "tool_call",
        "tool_call",
        "tool_call_update",
        "tool_call_update",
    ]
    assert [item["params"]["update"]["toolCallId"] for item in notifications[2:]] == ["call-1", "call-2"]
    assert all(item["params"]["update"]["status"] == "failed" for item in notifications[2:])
    assert response["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert response["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."


@pytest.mark.asyncio
async def test_session_continues_after_runtime_turn_error_failure() -> None:
    class TurnErrorThenSuccessBridge:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_outputs(self, *, record, prompt_text):
            self.calls += 1
            if self.calls == 1:
                yield {
                    "event_type": "turn_error",
                    "seq": 1,
                    "code": "AWORLD_ACP_REQUIRES_HUMAN",
                    "message": "Human approval/input flow is not bridged in phase 1.",
                    "retryable": True,
                    "origin": "runtime",
                }
                return

            yield MessageOutput(
                source=ModelResponse(id="resp-2", model="demo", content="recovered"),
            )

    server = AcpStdioServer(output_bridge=TurnErrorThenSuccessBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    first = await server._handle_prompt(
        13,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )
    second = await server._handle_prompt(
        14,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "follow-up"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert first["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert first["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."
    assert second["result"]["status"] == "completed"
    assert notifications[-1]["params"]["update"]["content"]["text"] == "recovered"


@pytest.mark.asyncio
async def test_prompt_suppresses_events_emitted_after_runtime_turn_error() -> None:
    class TurnErrorBridge:
        async def stream_outputs(self, *, record, prompt_text):
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-tool-start",
                    model="demo",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            function=Function(name="shell", arguments='{"command":"pwd"}'),
                        )
                    ],
                )
            )
            yield {
                "event_type": "turn_error",
                "seq": 2,
                "code": "AWORLD_ACP_REQUIRES_HUMAN",
                "message": "Human approval/input flow is not bridged in phase 1.",
                "retryable": True,
                "origin": "runtime",
            }
            yield MessageOutput(
                source=ModelResponse(id="resp-late-text", model="demo", content="late-text"),
            )
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-late-tool",
                    model="demo",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-2",
                            function=Function(name="shell", arguments='{"command":"ls"}'),
                        )
                    ],
                )
            )

    server = AcpStdioServer(output_bridge=TurnErrorBridge())
    writes: list[dict] = []

    async def capture(message: dict) -> None:
        writes.append(message)

    server._write_message = capture  # type: ignore[method-assign]
    session = server._handle_new_session({"cwd": ".", "mcpServers": []})

    response = await server._handle_prompt(
        16,
        {
            "sessionId": session["sessionId"],
            "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
        },
    )

    notifications = [message for message in writes if message.get("method") == "sessionUpdate"]

    assert [item["params"]["update"]["sessionUpdate"] for item in notifications] == [
        "tool_call",
        "tool_call_update",
    ]
    assert response["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"


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
async def test_executor_output_bridge_passes_session_cwd_without_global_chdir(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeAgent:
        context_config = None
        hooks = None

        async def get_swarm(self, _context):
            return "fake-swarm"

    class FakeRegistry:
        @staticmethod
        def get_agent(agent_id: str):
            return FakeAgent() if agent_id == "Aworld" else None

        @staticmethod
        def list_agents():
            return [FakeAgent()]

    class FakeExecutor:
        instances: list["FakeExecutor"] = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.working_directory = kwargs.get("working_directory")
            self.build_cwds: list[str] = []
            self.cleaned = False
            type(self).instances.append(self)

        async def _build_task(self, prompt_text: str, *, session_id: str):
            self.build_cwds.append(os.getcwd())
            return "TASK"

        async def cleanup_resources(self) -> None:
            self.cleaned = True

    stream_cwds: list[str] = []

    class FakeStreamingOutputs:
        async def stream_events(self):
            stream_cwds.append(os.getcwd())
            yield ChunkOutput(
                data=ModelResponse(id="resp-1", model="demo", content="hi"),
                metadata={},
            )

    from aworld_cli.acp import server as server_module

    monkeypatch.setattr(
        server_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )
    monkeypatch.setattr(
        server_module.os,
        "chdir",
        lambda _path: (_ for _ in ()).throw(AssertionError("os.chdir should not be used")),
    )

    record = AcpSessionRecord(
        acp_session_id="acp-1",
        aworld_session_id="aworld-1",
        cwd=str(tmp_path),
        requested_mcp_servers=[],
    )
    original_cwd = os.getcwd()
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

    assert len(outputs) == 1
    assert FakeExecutor.instances[0].working_directory == str(tmp_path)
    assert FakeExecutor.instances[0].build_cwds == [original_cwd]
    assert stream_cwds == [original_cwd]
    assert os.getcwd() == original_cwd
    assert FakeExecutor.instances[0].cleaned is True


@pytest.mark.asyncio
async def test_executor_output_bridge_does_not_serialize_independent_sessions(
    monkeypatch,
) -> None:
    class FakeAgent:
        context_config = None
        hooks = None

        async def get_swarm(self, _context):
            return "fake-swarm"

    class FakeRegistry:
        @staticmethod
        def get_agent(agent_id: str):
            return FakeAgent() if agent_id == "Aworld" else None

        @staticmethod
        def list_agents():
            return [FakeAgent()]

    class FakeExecutor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def _build_task(self, prompt_text: str, *, session_id: str):
            return {"prompt_text": prompt_text, "session_id": session_id}

        async def cleanup_resources(self) -> None:
            return None

    max_parallel = 0
    in_flight = 0

    class FakeStreamingOutputs:
        async def stream_events(self):
            nonlocal in_flight, max_parallel
            in_flight += 1
            max_parallel = max(max_parallel, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            yield ChunkOutput(
                data=ModelResponse(id="resp-1", model="demo", content="hi"),
                metadata={},
            )

    from aworld_cli.acp import server as server_module

    monkeypatch.setattr(
        server_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )

    bridge = AcpExecutorOutputBridge(
        registry_cls=FakeRegistry,
        executor_cls=FakeExecutor,
        init_agents_func=lambda *_args, **_kwargs: None,
    )

    async def collect(session_suffix: str) -> list[ChunkOutput]:
        record = AcpSessionRecord(
            acp_session_id=f"acp-{session_suffix}",
            aworld_session_id=f"aworld-{session_suffix}",
            cwd=".",
            requested_mcp_servers=[],
        )
        return [
            output
            async for output in bridge.stream_outputs(
                record=record,
                prompt_text=f"hello-{session_suffix}",
            )
        ]

    first, second = await asyncio.gather(collect("1"), collect("2"))

    assert len(first) == 1
    assert len(second) == 1
    assert max_parallel == 2


@pytest.mark.asyncio
async def test_executor_output_bridge_loads_bootstrap_agent_dirs_once(
    monkeypatch, tmp_path: Path
) -> None:
    plugin_root = tmp_path / "plugin-alpha"
    agent_dir = plugin_root / "agents"
    agent_dir.mkdir(parents=True)

    init_calls: list[str] = []

    class FakeAgent:
        context_config = None
        hooks = None

        async def get_swarm(self, _context):
            return "fake-swarm"

    class FakeRegistry:
        @staticmethod
        def get_agent(agent_id: str):
            return FakeAgent() if agent_id == "Aworld" else None

        @staticmethod
        def list_agents():
            return [FakeAgent()]

    class FakeExecutor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def _build_task(self, prompt_text: str, *, session_id: str):
            return "TASK"

        async def cleanup_resources(self) -> None:
            return None

    class FakeStreamingOutputs:
        async def stream_events(self):
            yield ChunkOutput(
                data=ModelResponse(id="resp-1", model="demo", content="hi"),
                metadata={},
            )

    from aworld_cli.acp import server as server_module

    monkeypatch.setattr(
        server_module,
        "bootstrap_acp_plugins",
        lambda _base_dir: {
            "plugin_roots": [],
            "agent_dirs": [agent_dir],
            "warnings": [],
            "command_sync_enabled": False,
            "interactive_refresh_enabled": False,
        },
        raising=False,
    )
    monkeypatch.setattr(
        server_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )

    bridge = AcpExecutorOutputBridge(
        registry_cls=FakeRegistry,
        executor_cls=FakeExecutor,
        init_agents_func=lambda path: init_calls.append(str(path)),
    )

    record = AcpSessionRecord(
        acp_session_id="acp-1",
        aworld_session_id="aworld-1",
        cwd=str(tmp_path),
        requested_mcp_servers=[],
    )

    _ = [
        output
        async for output in bridge.stream_outputs(
            record=record,
            prompt_text="hello",
        )
    ]
    _ = [
        output
        async for output in bridge.stream_outputs(
            record=record,
            prompt_text="hello-again",
        )
    ]

    assert init_calls == [str(agent_dir)]


@pytest.mark.asyncio
async def test_executor_output_bridge_attaches_bootstrap_runtime_to_executor(
    monkeypatch, tmp_path: Path
) -> None:
    plugin_root = tmp_path / "plugin-alpha"
    plugin_root.mkdir()

    class FakeAgent:
        context_config = None
        hooks = None

        async def get_swarm(self, _context):
            return "fake-swarm"

    class FakeRegistry:
        @staticmethod
        def get_agent(agent_id: str):
            return FakeAgent() if agent_id == "Aworld" else None

        @staticmethod
        def list_agents():
            return [FakeAgent()]

    class FakeExecutor:
        instances: list["FakeExecutor"] = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self._base_runtime = None
            type(self).instances.append(self)

        async def _build_task(self, prompt_text: str, *, session_id: str):
            return "TASK"

        async def cleanup_resources(self) -> None:
            return None

    class FakeRuntime:
        instances: list["FakeRuntime"] = []

        def __init__(self, *, workspace_path: str, plugin_roots, bootstrap):
            self.workspace_path = workspace_path
            self.plugin_roots = list(plugin_roots)
            self.bootstrap = bootstrap
            type(self).instances.append(self)

        def active_plugin_capabilities(self) -> tuple[str, ...]:
            return ("hooks",)

        def update_hud_snapshot(self, **sections):
            return sections

        def settle_hud_snapshot(self, task_status: str = "idle"):
            return {"task_status": task_status}

        async def run_plugin_hooks(self, hook_point: str, event: dict, executor_instance=None):
            return []

    class FakeStreamingOutputs:
        async def stream_events(self):
            yield ChunkOutput(
                data=ModelResponse(id="resp-1", model="demo", content="hi"),
                metadata={},
            )

    from aworld_cli.acp import server as server_module

    monkeypatch.setattr(
        server_module,
        "bootstrap_acp_plugins",
        lambda _base_dir: {
            "plugin_roots": [plugin_root],
            "warnings": [],
            "command_sync_enabled": False,
            "interactive_refresh_enabled": False,
        },
        raising=False,
    )
    monkeypatch.setattr(server_module, "AcpPluginRuntime", FakeRuntime, raising=False)
    monkeypatch.setattr(
        server_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )

    bridge = AcpExecutorOutputBridge(
        registry_cls=FakeRegistry,
        executor_cls=FakeExecutor,
        init_agents_func=lambda *_args, **_kwargs: None,
    )

    record = AcpSessionRecord(
        acp_session_id="acp-1",
        aworld_session_id="aworld-1",
        cwd=str(tmp_path),
        requested_mcp_servers=[],
    )

    _ = [
        output
        async for output in bridge.stream_outputs(
            record=record,
            prompt_text="hello",
        )
    ]

    assert isinstance(FakeExecutor.instances[0]._base_runtime, FakeRuntime)
    assert FakeRuntime.instances[0].workspace_path == str(tmp_path)
    assert FakeRuntime.instances[0].plugin_roots == [plugin_root]


def test_executor_output_bridge_emits_bootstrap_warnings_to_stderr(capsys) -> None:
    _ = AcpExecutorOutputBridge(
        registry_cls=object,
        executor_cls=object,
        init_agents_func=lambda *_args, **_kwargs: None,
        bootstrap_func=lambda _base_dir: {
            "plugin_roots": [],
            "warnings": ["plugin runtime degraded"],
            "command_sync_enabled": False,
            "interactive_refresh_enabled": False,
        },
        plugin_runtime_cls=None,
    )

    captured = capsys.readouterr()

    assert captured.out == ""
    assert "plugin runtime degraded" in captured.err


@pytest.mark.asyncio
async def test_executor_output_bridge_applies_and_restores_requested_mcp_servers(monkeypatch) -> None:
    class FakeSandbox:
        def __init__(self) -> None:
            self._mcp_config = {"mcpServers": {"base": {"command": "base"}}}
            self._mcp_servers = ["base"]

        @property
        def mcp_config(self):
            return self._mcp_config

        @mcp_config.setter
        def mcp_config(self, value):
            self._mcp_config = value

        @property
        def mcp_servers(self):
            return self._mcp_servers

        @mcp_servers.setter
        def mcp_servers(self, value):
            self._mcp_servers = value

    class RuntimeAgent:
        def __init__(self) -> None:
            self.sandbox = FakeSandbox()

        def id(self):
            return "runtime-agent"

    runtime_agent = RuntimeAgent()

    class FakeSwarm:
        def __init__(self, agent) -> None:
            self.topology = [agent]

    class FakeAgent:
        context_config = None
        hooks = None

        async def get_swarm(self, _context):
            return FakeSwarm(runtime_agent)

    class FakeRegistry:
        @staticmethod
        def get_agent(agent_id: str):
            return FakeAgent() if agent_id == "Aworld" else None

        @staticmethod
        def list_agents():
            return [FakeAgent()]

    class FakeExecutor:
        seen_mcp_servers: list[list[str]] = []
        seen_mcp_config: list[dict] = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def _build_task(self, prompt_text: str, *, session_id: str):
            FakeExecutor.seen_mcp_servers.append(list(runtime_agent.sandbox.mcp_servers))
            FakeExecutor.seen_mcp_config.append(runtime_agent.sandbox.mcp_config)
            return "TASK"

        async def cleanup_resources(self) -> None:
            return None

    class FakeStreamingOutputs:
        async def stream_events(self):
            yield ChunkOutput(
                data=ModelResponse(id="resp-1", model="demo", content="hi"),
                metadata={},
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
        requested_mcp_servers=[
            {"name": "demo", "command": "demo-server", "args": ["--help"]},
        ],
    )
    bridge = AcpExecutorOutputBridge(
        registry_cls=FakeRegistry,
        executor_cls=FakeExecutor,
        init_agents_func=lambda *_args, **_kwargs: None,
    )

    _ = [
        output
        async for output in bridge.stream_outputs(
            record=record,
            prompt_text="hello",
        )
    ]

    assert FakeExecutor.seen_mcp_servers == [["base", "demo"]]
    assert FakeExecutor.seen_mcp_config == [
        {
            "mcpServers": {
                "base": {"command": "base"},
                "demo": {"command": "demo-server", "args": ["--help"]},
            }
        }
    ]
    assert runtime_agent.sandbox.mcp_servers == ["base"]
    assert runtime_agent.sandbox.mcp_config == {"mcpServers": {"base": {"command": "base"}}}


@pytest.mark.asyncio
async def test_executor_output_bridge_raises_error_when_no_agent_loaded() -> None:
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

    with pytest.raises(ValueError, match="No ACP-capable agent found"):
        _outputs = [
            output
            async for output in bridge.stream_outputs(
                record=record,
                prompt_text="hello",
            )
        ]
