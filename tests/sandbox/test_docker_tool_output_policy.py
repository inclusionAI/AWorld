from __future__ import annotations

import importlib
import json

import pytest


def test_head_tail_policy_preserves_full_output_as_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    monkeypatch.setenv("AWORLD_DOCKER_MAX_OUTPUT_BYTES", "10")
    monkeypatch.setenv("AWORLD_DOCKER_OUTPUT_HEAD_BYTES", "4")
    monkeypatch.setenv("AWORLD_DOCKER_ARTIFACT_DIRECTORY", str(tmp_path))

    server = importlib.import_module("aworld.sandbox.tool_servers.docker.src.server")
    test_bridge = server.DockerBridge()
    raw = b"0123456789abcdefghij"

    inline, metadata = test_bridge.bound_output(raw, label="stdout")

    assert inline == b"0123efghij"
    assert metadata["raw_bytes"] == 20
    assert metadata["inline_bytes"] == 10
    assert metadata["offloaded_bytes"] == 10
    assert metadata["truncation_strategy"] == "head_tail_artifact"
    assert metadata["output_truncated"] is True
    assert open(metadata["artifact_ref"], "rb").read() == raw


@pytest.mark.asyncio
async def test_docker_run_code_uses_explicit_python_execution_contract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    monkeypatch.setenv("AWORLD_DOCKER_PYTHON", "/opt/python")
    server = importlib.import_module("aworld.sandbox.tool_servers.docker.src.server")
    captured: dict[str, object] = {}

    class _Bridge:
        container = "context-eval"
        workdir = "/workspace"
        shell = "/bin/sh"

        @staticmethod
        def validate_path(value):
            return value

        @staticmethod
        async def execute(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return 0, b"3\n", b"", False

        @staticmethod
        def bound_output(value, *, label):
            return value, {"output_truncated": False, "label": label}

        @staticmethod
        def decode_inline_text(value, _policy):
            return value.decode()

    monkeypatch.setattr(server, "bridge", _Bridge())

    result = await server.run_code(
        None,
        "print(1 + 2)",
        timeout=30,
        language="python",
    )
    payload = json.loads(result.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert captured["command"] == ["/opt/python", "-c", "print(1 + 2)"]
    assert captured["kwargs"] == {"timeout": 30, "workdir": "/workspace"}
    assert payload["success"] is True
    assert receipt["requested_language"] == "python"
    assert receipt["effective_language"] == "python"
