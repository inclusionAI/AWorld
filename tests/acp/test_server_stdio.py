from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ENV = {
    "PYTHONPATH": str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT),
}
SELF_TEST_SLOW_PROMPT = "__acp_self_test_slow__"


def _spawn_acp_server() -> subprocess.Popen[str]:
    env = dict(os.environ)
    env.update(ENV)
    env["AWORLD_ACP_SELF_TEST_BRIDGE"] = "1"
    return subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )


async def _spawn_async_acp_server(extra_env: dict[str, str] | None = None) -> asyncio.subprocess.Process:
    env = dict(os.environ)
    env.update(ENV)
    if extra_env:
        env.update(extra_env)
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "aworld_cli.main",
        "--no-banner",
        "acp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_ROOT),
        env=env,
    )


def _read_json_line(proc: subprocess.Popen[str]) -> dict:
    line = proc.stdout.readline()
    assert line, proc.stderr.read()
    return json.loads(line)


def test_acp_server_initialize_round_trip() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 1
        assert payload["result"]["serverInfo"]["name"] == "aworld-cli"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_acp_server_bootstrap_warnings_stay_on_stderr(tmp_path: Path) -> None:
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            import aworld_cli.acp.server as server_module

            def _patched_bootstrap(_base_dir):
                return {
                    "plugin_roots": [],
                    "warnings": ["bootstrap degraded for test"],
                    "command_sync_enabled": False,
                    "interactive_refresh_enabled": False,
                }

            server_module.bootstrap_acp_plugins = _patched_bootstrap
            """
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update(ENV)
    env["PYTHONPATH"] = str(patch_dir) + ":" + env["PYTHONPATH"]

    proc = subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.flush()

        payload = _read_json_line(proc)
        if proc.poll() is None:
            proc.kill()
        stderr_text = proc.stderr.read()
        proc.wait(timeout=5)

        assert payload["id"] == 1
        assert payload["result"]["serverInfo"]["name"] == "aworld-cli"
        assert "bootstrap degraded for test" in stderr_text
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_new_session_and_prompt_emit_session_update() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "text", "text": "hello"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        notification = _read_json_line(proc)
        response = _read_json_line(proc)

        assert notification["method"] == "sessionUpdate"
        assert notification["params"]["sessionId"] == session_id
        assert notification["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
        assert notification["params"]["update"]["content"]["text"] == "hello"
        assert response["id"] == 3
        assert response["result"]["status"] == "completed"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_accepts_current_acp_session_methods() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
                        "clientInfo": {"name": "happy-cli", "version": "test"},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "text", "text": "hello"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        notification = _read_json_line(proc)
        response = _read_json_line(proc)

        assert notification["method"] == "session/update"
        assert notification["params"]["sessionId"] == session_id
        assert notification["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
        assert notification["params"]["update"]["content"] == {"type": "text", "text": "hello"}
        assert response["id"] == 3
        assert response["result"]["status"] == "completed"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_cancel_missing_session_returns_error() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"cancel","params":{"sessionId":"missing"}}\n')
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 1
        assert payload["error"]["message"] == "AWORLD_ACP_SESSION_NOT_FOUND"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_acp_server_invalid_cwd_returns_structured_error_data(tmp_path: Path) -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        missing_dir = tmp_path / "missing"
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "newSession",
                    "params": {"cwd": str(missing_dir), "mcpServers": []},
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 1
        assert payload["error"]["message"] == "AWORLD_ACP_INVALID_CWD"
        assert payload["error"]["data"]["code"] == "AWORLD_ACP_INVALID_CWD"
        assert payload["error"]["data"]["message"] == "AWORLD_ACP_INVALID_CWD"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_acp_server_invalid_mcp_servers_returns_structured_error_data() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "newSession",
                    "params": {"cwd": ".", "mcpServers": "bad-shape"},
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 1
        assert payload["error"]["message"] == "AWORLD_ACP_UNSUPPORTED_MCP_SERVERS"
        assert payload["error"]["data"]["code"] == "AWORLD_ACP_UNSUPPORTED_MCP_SERVERS"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_acp_server_invalid_prompt_content_returns_structured_error_data() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "image", "url": "file:///tmp/demo.png"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 3
        assert payload["error"]["message"] == "AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT"
        assert payload["error"]["data"]["code"] == "AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_closes_tool_lifecycle_before_terminal_requires_human_failure(tmp_path: Path) -> None:
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            from aworld.models.model_response import Function, ModelResponse, ToolCall
            from aworld.output.base import MessageOutput
            import aworld_cli.acp.server as server_module
            from aworld_cli.acp.human_intercept import AcpRequiresHumanError

            class HumanToolBridge:
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

            _orig_init = server_module.AcpStdioServer.__init__

            def _patched_init(self, *, output_bridge=None):
                return _orig_init(self, output_bridge=HumanToolBridge())

            server_module.AcpStdioServer.__init__ = _patched_init
            """
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update(ENV)
    env["PYTHONPATH"] = str(patch_dir) + ":" + env["PYTHONPATH"]

    proc = subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        first = _read_json_line(proc)
        second = _read_json_line(proc)
        third = _read_json_line(proc)

        assert first["method"] == "sessionUpdate"
        assert first["params"]["update"]["sessionUpdate"] == "tool_call"
        assert second["method"] == "sessionUpdate"
        assert second["params"]["update"]["sessionUpdate"] == "tool_call_update"
        assert second["params"]["update"]["status"] == "failed"
        assert third["id"] == 3
        assert third["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
        assert third["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_treats_runtime_turn_error_as_terminal_structured_failure(tmp_path: Path) -> None:
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            import aworld_cli.acp.server as server_module

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

            _orig_init = server_module.AcpStdioServer.__init__

            def _patched_init(self, *, output_bridge=None):
                return _orig_init(self, output_bridge=TurnErrorBridge())

            server_module.AcpStdioServer.__init__ = _patched_init
            """
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update(ENV)
    env["PYTHONPATH"] = str(patch_dir) + ":" + env["PYTHONPATH"]

    proc = subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 3
        assert payload["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
        assert payload["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."
        assert payload["error"]["data"]["code"] == "AWORLD_ACP_REQUIRES_HUMAN"
        assert payload["error"]["data"]["retryable"] is True
        assert payload["error"]["data"]["data"]["origin"] == "runtime"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_closes_all_same_name_tool_lifecycles_before_terminal_failure(tmp_path: Path) -> None:
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            from aworld.models.model_response import Function, ModelResponse, ToolCall
            from aworld.output.base import MessageOutput
            import aworld_cli.acp.server as server_module

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

            _orig_init = server_module.AcpStdioServer.__init__

            def _patched_init(self, *, output_bridge=None):
                return _orig_init(self, output_bridge=TurnErrorBridge())

            server_module.AcpStdioServer.__init__ = _patched_init
            """
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update(ENV)
    env["PYTHONPATH"] = str(patch_dir) + ":" + env["PYTHONPATH"]

    proc = subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        first = _read_json_line(proc)
        second = _read_json_line(proc)
        third = _read_json_line(proc)
        fourth = _read_json_line(proc)
        fifth = _read_json_line(proc)

        assert first["params"]["update"]["sessionUpdate"] == "tool_call"
        assert second["params"]["update"]["sessionUpdate"] == "tool_call"
        assert third["params"]["update"]["sessionUpdate"] == "tool_call_update"
        assert fourth["params"]["update"]["sessionUpdate"] == "tool_call_update"
        assert [third["params"]["update"]["toolCallId"], fourth["params"]["update"]["toolCallId"]] == [
            "call-1",
            "call-2",
        ]
        assert fifth["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
        assert fifth["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_suppresses_events_emitted_after_runtime_turn_error(tmp_path: Path) -> None:
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            from aworld.models.model_response import Function, ModelResponse, ToolCall
            from aworld.output.base import MessageOutput
            import aworld_cli.acp.server as server_module

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

            _orig_init = server_module.AcpStdioServer.__init__

            def _patched_init(self, *, output_bridge=None):
                return _orig_init(self, output_bridge=TurnErrorBridge())

            server_module.AcpStdioServer.__init__ = _patched_init
            """
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update(ENV)
    env["PYTHONPATH"] = str(patch_dir) + ":" + env["PYTHONPATH"]

    proc = subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "text", "text": "needs approval"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        first = _read_json_line(proc)
        second = _read_json_line(proc)
        third = _read_json_line(proc)

        assert first["params"]["update"]["sessionUpdate"] == "tool_call"
        assert second["params"]["update"]["sessionUpdate"] == "tool_call_update"
        assert third["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"

        proc.kill()
        proc.wait(timeout=5)
        remaining_stdout = proc.stdout.read() if proc.stdout is not None else ""
        assert remaining_stdout == ""
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_acp_server_can_cancel_active_prompt_with_self_test_bridge() -> None:
    proc = await _spawn_async_acp_server({"AWORLD_ACP_SELF_TEST_BRIDGE": "1"})
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        proc.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        await proc.stdin.drain()
        _ = json.loads((await asyncio.wait_for(proc.stdout.readline(), timeout=10)).decode("utf-8"))

        proc.stdin.write(
            b'{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n'
        )
        await proc.stdin.drain()
        new_session = json.loads((await asyncio.wait_for(proc.stdout.readline(), timeout=10)).decode("utf-8"))
        session_id = new_session["result"]["sessionId"]

        slow_prompt = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "prompt",
            "params": {
                "sessionId": session_id,
                "prompt": {"content": [{"type": "text", "text": SELF_TEST_SLOW_PROMPT}]},
            },
        }
        cancel = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "cancel",
            "params": {"sessionId": session_id},
        }
        proc.stdin.write((json.dumps(slow_prompt) + "\n").encode("utf-8"))
        proc.stdin.write((json.dumps(cancel) + "\n").encode("utf-8"))
        await proc.stdin.drain()

        seen_by_id: dict[int, dict] = {}
        deadline = asyncio.get_running_loop().time() + 10
        while {3, 4} - seen_by_id.keys():
            timeout = max(0.1, deadline - asyncio.get_running_loop().time())
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            assert line, (await proc.stderr.read()).decode("utf-8", errors="replace")
            message = json.loads(line.decode("utf-8"))
            if "id" in message:
                seen_by_id[int(message["id"])] = message

        assert seen_by_id[4]["result"]["status"] == "cancelled"
        assert seen_by_id[3]["result"]["status"] == "cancelled"
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_acp_server_queues_busy_prompt_with_self_test_bridge() -> None:
    proc = await _spawn_async_acp_server({"AWORLD_ACP_SELF_TEST_BRIDGE": "1"})
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        proc.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        await proc.stdin.drain()
        _ = json.loads((await asyncio.wait_for(proc.stdout.readline(), timeout=10)).decode("utf-8"))

        proc.stdin.write(
            b'{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n'
        )
        await proc.stdin.drain()
        new_session = json.loads((await asyncio.wait_for(proc.stdout.readline(), timeout=10)).decode("utf-8"))
        session_id = new_session["result"]["sessionId"]

        slow_prompt = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "prompt",
            "params": {
                "sessionId": session_id,
                "prompt": {"content": [{"type": "text", "text": SELF_TEST_SLOW_PROMPT}]},
            },
        }
        competing_prompt = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "prompt",
            "params": {
                "sessionId": session_id,
                "prompt": {"content": [{"type": "text", "text": "competing-prompt"}]},
            },
        }
        cancel = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "cancel",
            "params": {"sessionId": session_id},
        }
        proc.stdin.write((json.dumps(slow_prompt) + "\n").encode("utf-8"))
        proc.stdin.write((json.dumps(competing_prompt) + "\n").encode("utf-8"))
        proc.stdin.write((json.dumps(cancel) + "\n").encode("utf-8"))
        await proc.stdin.drain()

        seen_by_id: dict[int, dict] = {}
        session_updates: list[dict] = []
        deadline = asyncio.get_running_loop().time() + 10
        while {3, 4, 5} - seen_by_id.keys():
            timeout = max(0.1, deadline - asyncio.get_running_loop().time())
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            assert line, (await proc.stderr.read()).decode("utf-8", errors="replace")
            message = json.loads(line.decode("utf-8"))
            if "id" in message:
                seen_by_id[int(message["id"])] = message
            elif message.get("method") == "sessionUpdate":
                session_updates.append(message)

        assert seen_by_id[4]["result"]["status"] == "queued"
        assert seen_by_id[5]["result"]["status"] == "cancelled"
        assert seen_by_id[3]["result"]["status"] == "cancelled"
        assert any(
            item["params"]["update"]["content"]["text"] == "Steering captured. Applying at next checkpoint."
            for item in session_updates
            if item.get("params", {}).get("update", {}).get("sessionUpdate") == "agent_message_chunk"
        )
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
