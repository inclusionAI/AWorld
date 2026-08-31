from __future__ import annotations

import importlib


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
