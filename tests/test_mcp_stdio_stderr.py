import os

import pytest

from aworld.mcp_client.server import MCPServerStdio


class _DummyContextManager:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_stdio_client_redirects_stderr_to_devnull_by_default(monkeypatch):
    captured = {}

    def fake_stdio_client(params, errlog=None):
        captured["params"] = params
        captured["errlog"] = errlog
        return _DummyContextManager()

    monkeypatch.setattr("aworld.mcp_client.server.stdio_client", fake_stdio_client)
    monkeypatch.delenv("AWORLD_PRESERVE_MCP_LOGS", raising=False)

    server = MCPServerStdio(
        name="test-server",
        params={
            "command": "python",
            "args": ["-c", "print('ok')"],
        },
    )

    _ = server.create_streams()

    assert captured["errlog"] is not None
    assert captured["errlog"].name == os.devnull
    captured["errlog"].close()


def test_stdio_client_can_preserve_stderr_to_log_file(monkeypatch, tmp_path):
    captured = {}

    def fake_stdio_client(params, errlog=None):
        captured["params"] = params
        captured["errlog"] = errlog
        return _DummyContextManager()

    monkeypatch.setattr("aworld.mcp_client.server.stdio_client", fake_stdio_client)
    monkeypatch.setenv("AWORLD_PRESERVE_MCP_LOGS", "true")
    monkeypatch.setenv("AWORLD_LOG_PATH", str(tmp_path))

    server = MCPServerStdio(
        name="test-server",
        params={
            "command": "python",
            "args": ["-c", "print('ok')"],
        },
    )

    _ = server.create_streams()

    assert captured["errlog"] is not None
    assert captured["errlog"].name.endswith("mcp_stderr.log")
    assert os.path.dirname(captured["errlog"].name) == str(tmp_path)
    captured["errlog"].close()
