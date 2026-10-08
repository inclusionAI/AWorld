import asyncio
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

from aworld.mcp_client.utils import process_mcp_tools
from aworld.sandbox import Sandbox


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("reuse", [False, True])
async def test_builtin_terminal_discovers_and_executes_in_workspace(
    tmp_path: Path,
    reuse: bool,
) -> None:
    sandbox = Sandbox(
        builtin_tools=["terminal"],
        workspaces=[str(tmp_path)],
        reuse=reuse,
    )
    try:
        tools = await asyncio.wait_for(
            sandbox.mcpservers.list_tools(),
            timeout=30,
        )
        schemas = {item["function"]["name"]: item["function"] for item in tools}

        assert "terminal__run_code" in schemas
        assert "code" in schemas["terminal__run_code"]["parameters"]["properties"]
        assert "language" in schemas["terminal__run_code"]["parameters"]["properties"]
        assert "cwd" in schemas["terminal__run_code"]["parameters"]["properties"]
        assert "env" in schemas["terminal__run_code"]["parameters"]["properties"]
        assert "terminal__read_output_artifact" in schemas
        processed_tools, tool_mapping = await process_mcp_tools(tools)
        assert "run_code" in {item["function"]["name"] for item in processed_tools}
        assert tool_mapping["run_code"] == "terminal__run_code"

        result = await asyncio.wait_for(
            sandbox.terminal.run_code(
                'python -c "from pathlib import Path; print(Path.cwd())"'
            ),
            timeout=30,
        )

        payload = result["data"]
        assert result["success"] is True
        assert payload["success"] is True
        assert Path(payload["metadata"]["working_directory"]).resolve() == (
            tmp_path.resolve()
        )
        assert payload["metadata"]["output_data"] is None
        assert str(tmp_path.resolve()) in payload["message"]["stdout"]
        assert payload["metadata"]["timeout_seconds"] == 300
        terminal_receipt = payload["metadata"]["terminal_execution_receipt"]
        assert terminal_receipt["schema_version"] == (
            "aworld.terminal-execution-receipt/v2"
        )
        assert terminal_receipt["language_contract_version"] == 1
        assert terminal_receipt["requested_language"] == "shell"
        assert terminal_receipt["effective_language"] == "shell"
        assert terminal_receipt["language"] == "shell"
        assert terminal_receipt["effect"] == "read_only"
        assert terminal_receipt["workspace_generation_delta"] == 0
        assert terminal_receipt["executed"] is True
        assert terminal_receipt["exit_code"] == 0

        python_result = await asyncio.wait_for(
            sandbox.terminal.run_code(
                "print('raw-python-ok')",
                language="python",
            ),
            timeout=30,
        )
        python_payload = python_result["data"]
        python_receipt = python_payload["metadata"]["terminal_execution_receipt"]
        assert python_payload["message"]["stdout"] == "raw-python-ok\n"
        assert python_receipt["requested_language"] == "python"
        assert python_receipt["effective_language"] == "python"
    finally:
        await sandbox.cleanup()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_builtin_terminal_artifact_survives_non_reuse_stdio_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "2048")
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_MAX_BYTES", "200000")
    original = "BEGIN" + ("x" * 100_000) + "END"
    sandbox = Sandbox(
        builtin_tools=["terminal"],
        workspaces=[str(tmp_path)],
        reuse=False,
    )
    try:
        result = await asyncio.wait_for(
            sandbox.terminal.run_code(
                f"{shlex.quote(sys.executable)} -c "
                + shlex.quote("print('BEGIN' + ('x' * 100000) + 'END', end='')"),
            ),
            timeout=30,
        )
        policy = result["data"]["metadata"]["output_policy"]["stdout"]
        artifact = await asyncio.wait_for(
            sandbox.terminal.read_output_artifact(
                policy["artifact_ref"],
                offset=0,
                limit=len(original),
            ),
            timeout=30,
        )

        assert artifact["data"]["content"] == original
        assert artifact["data"]["complete"] is True
        assert artifact["data"]["content_sha256"] == policy["content_sha256"]
    finally:
        await sandbox.cleanup()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_builtin_terminal_receipt_drives_sandbox_observation_cache(
    tmp_path: Path,
) -> None:
    source = tmp_path / "evidence.txt"
    source.write_text("alpha\nbeta\n", encoding="utf-8")
    sandbox = Sandbox(
        builtin_tools=["terminal"],
        workspaces=[str(tmp_path)],
        reuse=False,
    )
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {
            "code": "cat evidence.txt | head -1; wc -l evidence.txt",
        },
    }
    context = SimpleNamespace(
        task_id="receipt-task",
        task_epoch=1,
        session_id="receipt-session",
    )
    try:
        first = await asyncio.wait_for(
            sandbox.call_tool(action_list=[action], context=context),
            timeout=30,
        )
        repeated = await asyncio.wait_for(
            sandbox.call_tool(action_list=[action], context=context),
            timeout=30,
        )
        opaque = {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {
                "code": "python -c 'import sys; sys.stdout.write(\"opaque\")'",
            },
        }
        await asyncio.wait_for(
            sandbox.call_tool(action_list=[opaque], context=context),
            timeout=30,
        )
        retained = await asyncio.wait_for(
            sandbox.call_tool(action_list=[action], context=context),
            timeout=30,
        )
        source.write_text("gamma\n", encoding="utf-8")
        changed = await asyncio.wait_for(
            sandbox.call_tool(action_list=[action], context=context),
            timeout=30,
        )

        assert first[0].metadata["terminal_execution_receipt"]["effect"] == (
            "read_only"
        )
        assert first[0].metadata["sandbox_observation"]["workspace_generation"] == 0
        assert repeated[0].metadata["sandbox_observation"]["cache_hit"] is True
        assert repeated[0].metadata["sandbox_observation"]["changed"] is False
        retained_receipt = retained[0].metadata["sandbox_observation"]
        assert retained_receipt["cache_hit"] is True
        assert retained_receipt["cache_validation"] == "epoch_revalidated"
        assert retained_receipt["source_workspace_generation"] == 0
        assert retained_receipt["workspace_generation"] == 1
        assert changed[0].metadata["sandbox_observation"]["cache_hit"] is False
        assert "gamma" in changed[0].content
        assert "env_content" not in action["params"]
    finally:
        await sandbox.cleanup()
