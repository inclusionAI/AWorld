from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from aworld_cli.cloud_client import CloudApiError, CloudClientConfig, CloudHttpClient
from aworld_cli.top_level_commands.cloud_cmd import CloudTopLevelCommand


def _parse_cloud_args(argv: list[str]):
    parser = argparse.ArgumentParser(prog="aworld-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    CloudTopLevelCommand().register_parser(subparsers)
    return parser.parse_args(["cloud", *argv])


def test_cloud_client_submits_versioned_benchmark_with_authentication() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "run-1", "mode": "benchmark"})

    async def scenario() -> dict[str, object]:
        client = CloudHttpClient(
            CloudClientConfig(
                endpoint="https://cloud.example.test/",
                token="secret-token",
                timeout_seconds=5,
            ),
            transport=httpx.MockTransport(handler),
        )
        async with client:
            return await client.submit_run(
                "workspace-1",
                task="fix it",
                mode="benchmark",
                model=None,
                benchmark={"dataset": "suite", "task_id": "case-1"},
                idempotency_key="request-1",
            )

    result = asyncio.run(scenario())

    assert result == {"id": "run-1", "mode": "benchmark"}
    assert captured == {
        "url": "https://cloud.example.test/api/v1/cloud/workspaces/workspace-1/runs",
        "authorization": "Bearer secret-token",
        "payload": {
            "benchmark": {"dataset": "suite", "task_id": "case-1"},
            "idempotency_key": "request-1",
            "mode": "benchmark",
            "model": None,
            "request_schema_version": "aworld.cloud.run-request.v1",
            "task": "fix it",
        },
    }


def test_cloud_client_decodes_stable_api_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "workspace_busy",
                    "message": "workspace has an active run",
                    "details": {"workspace_id": "workspace-1"},
                }
            },
        )

    async def scenario() -> None:
        client = CloudHttpClient(
            CloudClientConfig(endpoint="https://cloud.example.test/api/v1/cloud"),
            transport=httpx.MockTransport(handler),
        )
        async with client:
            await client.get_workspace("workspace-1")

    with pytest.raises(CloudApiError) as error:
        asyncio.run(scenario())

    assert error.value.status_code == 409
    assert error.value.code == "workspace_busy"
    assert error.value.details == {"workspace_id": "workspace-1"}


def test_cloud_client_uses_versioned_batch_paths() -> None:
    captured: list[tuple[str, str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        return httpx.Response(200, json={"id": "batch-1"})

    async def scenario() -> None:
        async with CloudHttpClient(
            CloudClientConfig(endpoint="https://cloud.example.test"),
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.create_batch(
                "workspace-1",
                name="nightly",
                runs=[{"task": "one"}],
                idempotency_key="batch-create",
            )
            await client.list_batches(workspace_id="workspace-1")
            await client.get_batch("batch-1")
            await client.cancel_batch("batch-1", idempotency_key="batch-cancel")

    asyncio.run(scenario())

    assert captured == [
        (
            "POST",
            "/api/v1/cloud/workspaces/workspace-1/batches",
            {
                "idempotency_key": "batch-create",
                "name": "nightly",
                "runs": [{"task": "one"}],
            },
        ),
        ("GET", "/api/v1/cloud/batches", None),
        ("GET", "/api/v1/cloud/batches/batch-1", None),
        (
            "POST",
            "/api/v1/cloud/batches/batch-1/cancel",
            {"idempotency_key": "batch-cancel"},
        ),
    ]


def test_cloud_command_is_registered_via_builtin_plugin() -> None:
    plugin_root = (
        Path(__file__).parents[1]
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "cloud_cli"
    )
    manifest = json.loads(
        (plugin_root / ".aworld-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert manifest["id"] == "aworld-cloud-cli"
    assert manifest["entrypoints"]["cli_commands"][0]["name"] == "cloud"
    assert CloudTopLevelCommand().name == "cloud"


def test_cloud_cli_rejects_benchmark_fields_in_query_mode(capsys) -> None:
    args = _parse_cloud_args(
        [
            "run",
            "submit",
            "--workspace-id",
            "workspace-1",
            "--task",
            "inspect",
            "--dataset",
            "suite",
        ]
    )
    exit_code = CloudTopLevelCommand().run(args, SimpleNamespace())

    assert exit_code == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "invalid_cli_request"
    assert "--mode benchmark" in error["error"]["message"]


def test_cloud_cli_creates_workspace_with_machine_readable_output(
    monkeypatch, capsys
) -> None:
    from aworld_cli.top_level_commands import cloud_cmd

    class FakeClient:
        def __init__(self, config):
            assert config.endpoint == "https://cloud.example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create_workspace(self, *, name, profile_name, idempotency_key):
            assert (name, profile_name, idempotency_key) == (
                "review",
                "standard",
                "workspace-request-1",
            )
            return {"id": "workspace-1", "state": "ready"}

    monkeypatch.setattr(cloud_cmd, "CloudHttpClient", FakeClient)
    args = _parse_cloud_args(
        [
            "--endpoint",
            "https://cloud.example.test",
            "workspace",
            "create",
            "--name",
            "review",
            "--profile",
            "standard",
            "--idempotency-key",
            "workspace-request-1",
        ]
    )

    exit_code = CloudTopLevelCommand().run(args, SimpleNamespace())

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "id": "workspace-1",
        "state": "ready",
    }


def test_cloud_cli_downloads_canonical_trajectory(
    monkeypatch, tmp_path, capsys
) -> None:
    from aworld_cli.top_level_commands import cloud_cmd

    class FakeClient:
        def __init__(self, config):
            assert config.endpoint == "https://cloud.example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_run(self, run_id):
            assert run_id == "run-1"
            return {"canonical_trajectory_file_id": "file-1"}

        async def download_file(self, run_id, file_id):
            assert (run_id, file_id) == ("run-1", "file-1")
            return b'{"schema_version":"ATIF-v1.7"}\n'

    monkeypatch.setattr(cloud_cmd, "CloudHttpClient", FakeClient)
    output = tmp_path / "trajectory.json"

    args = _parse_cloud_args(
        [
            "--endpoint",
            "https://cloud.example.test",
            "run",
            "trajectory",
            "run-1",
            "--output",
            str(output),
        ]
    )
    exit_code = CloudTopLevelCommand().run(args, SimpleNamespace())

    assert exit_code == 0
    assert json.loads(output.read_text()) == {"schema_version": "ATIF-v1.7"}
    result = json.loads(capsys.readouterr().out)
    assert result["file_id"] == "file-1"
    assert result["format"] == "atif"


def test_cloud_cli_waits_for_terminal_result(monkeypatch, capsys) -> None:
    from aworld_cli.top_level_commands import cloud_cmd

    class FakeClient:
        calls = 0

        def __init__(self, config):
            del config

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_run(self, run_id):
            assert run_id == "run-1"
            self.calls += 1
            return {
                "id": run_id,
                "state": "succeeded" if self.calls == 2 else "running",
                "benchmark_outcome": {"reward": 1.0, "result": {}},
            }

    monkeypatch.setattr(cloud_cmd, "CloudHttpClient", FakeClient)
    args = _parse_cloud_args(
        [
            "run",
            "wait",
            "run-1",
            "--poll-interval",
            "0.001",
            "--wait-timeout",
            "1",
        ]
    )

    exit_code = CloudTopLevelCommand().run(args, SimpleNamespace())

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "succeeded"
    assert result["benchmark_outcome"]["reward"] == 1.0


def test_cloud_cli_downloads_run_logs(monkeypatch, tmp_path, capsys) -> None:
    from aworld_cli.top_level_commands import cloud_cmd

    class FakeClient:
        def __init__(self, config):
            del config

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def list_files(self, run_id):
            assert run_id == "run-1"
            return {
                "items": [
                    {
                        "id": "file-stdout",
                        "kind": "stdout",
                        "relative_path": "stdout.log",
                    },
                    {
                        "id": "file-trajectory",
                        "kind": "trajectory",
                        "relative_path": "trajectory.atif.json",
                    },
                ]
            }

        async def download_file(self, run_id, file_id):
            assert (run_id, file_id) == ("run-1", "file-stdout")
            return b"harbor output\n"

    monkeypatch.setattr(cloud_cmd, "CloudHttpClient", FakeClient)
    output_directory = tmp_path / "logs"
    args = _parse_cloud_args(
        ["run", "logs", "run-1", "--output-dir", str(output_directory)]
    )

    exit_code = CloudTopLevelCommand().run(args, SimpleNamespace())

    assert exit_code == 0
    assert (output_directory / "stdout.log").read_text() == "harbor output\n"
    result = json.loads(capsys.readouterr().out)
    assert [item["kind"] for item in result["files"]] == ["stdout"]


def test_cloud_cli_creates_batch_from_json_file(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from aworld_cli.top_level_commands import cloud_cmd

    runs_file = tmp_path / "runs.json"
    runs_file.write_text(
        json.dumps([{"task": "one"}, {"task": "two", "mode": "query"}]),
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, config):
            del config

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create_batch(self, workspace_id, *, name, runs, idempotency_key):
            assert workspace_id == "workspace-1"
            assert name == "nightly"
            assert runs == [{"task": "one"}, {"task": "two", "mode": "query"}]
            assert idempotency_key == "batch-key"
            return {"id": "batch-1", "counts": {"total": 2}}

    monkeypatch.setattr(cloud_cmd, "CloudHttpClient", FakeClient)
    args = _parse_cloud_args(
        [
            "batch",
            "create",
            "--workspace-id",
            "workspace-1",
            "--name",
            "nightly",
            "--runs-file",
            str(runs_file),
            "--idempotency-key",
            "batch-key",
        ]
    )

    assert CloudTopLevelCommand().run(args, SimpleNamespace()) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "batch-1"
