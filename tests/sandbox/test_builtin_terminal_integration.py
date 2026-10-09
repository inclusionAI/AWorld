import asyncio
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

from aworld.mcp_client.utils import process_mcp_tools
from aworld.sandbox import Sandbox
from aworld.sandbox.terminal_receipt import plan_terminal_execution


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
        assert (
            "declared_write_paths"
            in schemas["terminal__run_code"]["parameters"]["properties"]
        )
        assert "terminal__read_output_artifact" in schemas
        processed_tools, tool_mapping = await process_mcp_tools(tools)
        assert "run_code" in {item["function"]["name"] for item in processed_tools}
        assert tool_mapping["run_code"] == "terminal__run_code"

        result = await asyncio.wait_for(
            sandbox.terminal.run_code(
                'python -I -c "from pathlib import Path; print(Path.cwd())"'
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
        assert terminal_receipt["language_contract_version"] == 2
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
async def test_builtin_terminal_module_monkeypatch_never_claims_write_authority(
    tmp_path: Path,
) -> None:
    target = tmp_path / "purity-bypass.txt"
    source = f"""
import math
from pathlib import Path
math.sin = Path({str(target)!r}).write_text
math.sin('written')
"""
    sandbox = Sandbox(
        builtin_tools=["terminal"],
        workspaces=[str(tmp_path)],
        reuse=False,
    )
    try:
        result = await asyncio.wait_for(
            sandbox.terminal.run_code(source, language="python"),
            timeout=30,
        )
        receipt = result["data"]["metadata"]["terminal_execution_receipt"]

        assert result["success"] is True
        assert target.read_text(encoding="utf-8") == "written"
        assert receipt["effect"] == "unknown"
        assert receipt["cacheable"] is False
        assert receipt["write_paths"] == []
        assert receipt["write_set_complete"] is False
        assert receipt["workspace_generation_delta"] == 1

        callback_target = tmp_path / "callback-bypass.txt"
        callback_target.write_text("delete-me", encoding="utf-8")
        callback_source = f"""
from pathlib import Path
list(map(Path({str(callback_target)!r}).unlink, [True]))
"""
        callback_result = await asyncio.wait_for(
            sandbox.terminal.run_code(callback_source, language="python"),
            timeout=30,
        )
        callback_receipt = callback_result["data"]["metadata"][
            "terminal_execution_receipt"
        ]

        assert callback_result["success"] is True
        assert callback_target.exists() is False
        assert callback_receipt["effect"] == "unknown"
        assert callback_receipt["cacheable"] is False
        assert callback_receipt["write_paths"] == []
        assert callback_receipt["write_set_complete"] is False
        assert callback_receipt["workspace_generation_delta"] == 1

        iter_target = tmp_path / "iter-callback-bypass.txt"
        iter_target.write_text("delete-me", encoding="utf-8")
        iter_source = f"""
from pathlib import Path
list(iter(Path({str(iter_target)!r}).unlink, None))
"""
        iter_result = await asyncio.wait_for(
            sandbox.terminal.run_code(iter_source, language="python"),
            timeout=30,
        )
        iter_receipt = iter_result["data"]["metadata"]["terminal_execution_receipt"]

        assert iter_result["success"] is True
        assert iter_target.exists() is False
        assert iter_receipt["effect"] == "unknown"
        assert iter_receipt["cacheable"] is False
        assert iter_receipt["write_paths"] == []
        assert iter_receipt["write_set_complete"] is False
        assert iter_receipt["workspace_generation_delta"] == 1

        builtin_target = tmp_path / "builtin-rebind-bypass.txt"
        builtin_source = f"""
len = open
len({str(builtin_target)!r}, 'w')
"""
        builtin_result = await asyncio.wait_for(
            sandbox.terminal.run_code(builtin_source, language="python"),
            timeout=30,
        )
        builtin_receipt = builtin_result["data"]["metadata"][
            "terminal_execution_receipt"
        ]

        assert builtin_result["success"] is True
        assert builtin_target.exists() is True
        assert builtin_receipt["effect"] == "unknown"
        assert builtin_receipt["cacheable"] is False
        assert builtin_receipt["write_paths"] == []
        assert builtin_receipt["write_set_complete"] is False
        assert builtin_receipt["workspace_generation_delta"] == 1
    finally:
        await sandbox.cleanup()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_builtin_terminal_python_import_authority_requires_isolation(
    tmp_path: Path,
) -> None:
    subdirectory = tmp_path / "sub"
    subdirectory.mkdir()
    input_path = subdirectory / "input.txt"
    input_path.write_text("G1 X1 Y1\n", encoding="utf-8")
    shadow_marker = subdirectory / "shadow-import.txt"
    (subdirectory / "statistics.py").write_text(
        f"open({str(shadow_marker)!r}, 'w').write('shadowed')\n",
        encoding="utf-8",
    )
    raw_shadow_marker = subdirectory / "raw-shadow-import.txt"
    (subdirectory / "re.py").write_text(
        f"open({str(raw_shadow_marker)!r}, 'w').write('shadowed')\n"
        "def escape(value):\n"
        "    return value\n",
        encoding="utf-8",
    )
    sandbox = Sandbox(
        builtin_tools=["terminal"],
        workspaces=[str(tmp_path)],
        reuse=False,
    )
    try:
        nonisolated_code = """cd sub && python3 - <<'PY'
import statistics
print('nonisolated')
PY
"""
        nonisolated_plan = plan_terminal_execution(nonisolated_code)
        nonisolated = await asyncio.wait_for(
            sandbox.terminal.run_code(nonisolated_code),
            timeout=30,
        )
        nonisolated_receipt = nonisolated["data"]["metadata"][
            "terminal_execution_receipt"
        ]

        assert nonisolated["success"] is True
        assert shadow_marker.read_text(encoding="utf-8") == "shadowed"
        assert nonisolated_plan.command_cwd == "sub"
        assert nonisolated_receipt["effect"] == "unknown"
        assert nonisolated_receipt["effect_source"] == "parser_contract"
        assert nonisolated_receipt["read_path_epochs"] == []
        assert nonisolated_receipt["write_paths"] == []
        assert nonisolated_receipt["read_set_complete"] is False
        assert nonisolated_receipt["write_set_complete"] is False

        raw_python = await asyncio.wait_for(
            sandbox.terminal.run_code(
                "import re\nprint(re.escape('raw-python'))",
                cwd="sub",
                language="python",
            ),
            timeout=30,
        )
        raw_receipt = raw_python["data"]["metadata"]["terminal_execution_receipt"]

        assert raw_python["success"] is True
        assert raw_shadow_marker.exists() is False
        assert raw_python["data"]["message"]["stdout"] == "raw\\-python\n"
        assert raw_receipt["effect"] == "read_only"
        assert raw_receipt["effect_source"] == "trusted_command_contract"
        assert raw_receipt["read_set_complete"] is True
        assert raw_receipt["write_set_complete"] is True

        isolated_read_code = """cd sub && python3 -I - <<'PY'
import re
print(re.escape(open('input.txt').read()))
PY
"""
        isolated_read_plan = plan_terminal_execution(isolated_read_code)
        isolated_read = await asyncio.wait_for(
            sandbox.terminal.run_code(isolated_read_code),
            timeout=30,
        )
        read_receipt = isolated_read["data"]["metadata"]["terminal_execution_receipt"]

        assert isolated_read["success"] is True
        assert isolated_read_plan.command_cwd == "sub"
        assert read_receipt["effect"] == "read_only"
        assert read_receipt["effect_source"] == "trusted_command_contract"
        assert read_receipt["read_paths"] == ["input.txt"]
        assert len(read_receipt["read_path_epochs"]) == 1
        assert Path(read_receipt["read_path_epochs"][0]["resolved_path"]) == (
            input_path.resolve()
        )
        assert read_receipt["write_paths"] == []
        assert read_receipt["read_set_complete"] is True
        assert read_receipt["write_set_complete"] is True

        output_path = subdirectory / "out.txt"
        isolated_write_code = """cd sub && python3 -I - <<'PY'
import re
rows = open('input.txt').read().splitlines()
open('out.txt', 'w').write(str(sum(bool(re.match(r'G1', row)) for row in rows)))
PY
"""
        isolated_write_plan = plan_terminal_execution(isolated_write_code)
        isolated_write = await asyncio.wait_for(
            sandbox.terminal.run_code(isolated_write_code),
            timeout=30,
        )
        write_receipt = isolated_write["data"]["metadata"]["terminal_execution_receipt"]

        assert isolated_write["success"] is True
        assert output_path.read_text(encoding="utf-8") == "1"
        assert isolated_write_plan.command_cwd == "sub"
        assert write_receipt["effect"] == "mutating"
        assert write_receipt["effect_source"] == "parser_contract"
        assert write_receipt["read_paths"] == ["input.txt"]
        assert write_receipt["read_path_epochs"] == []
        assert write_receipt["write_paths"] == ["out.txt"]
        assert write_receipt["read_set_complete"] is True
        assert write_receipt["write_set_complete"] is True
        assert write_receipt["mutation_observed"] is True
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
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=SimpleNamespace(
            session_id="receipt-session",
            session_epoch=0,
            task_epoch=1,
            branch_id="main",
            checkpoint_revision=0,
        ),
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
