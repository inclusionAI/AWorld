from pathlib import Path
import os
import py_compile
import json
import subprocess
import sys
import importlib.util
import shutil

import yaml
import pytest

from aworld.core.common import ActionResult, Observation


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "cli_hook_acceptance"


def test_cli_hook_acceptance_example_layout_exists():
    expected_files = [
        EXAMPLE_ROOT / "README.md",
        EXAMPLE_ROOT / "agent.py",
        EXAMPLE_ROOT / ".aworld" / "hooks.yaml",
        EXAMPLE_ROOT / "hooks" / "block_user_input.sh",
        EXAMPLE_ROOT / "hooks" / "rewrite_user_input.sh",
        EXAMPLE_ROOT / "hooks" / "block_rm_rf.sh",
        EXAMPLE_ROOT / "hooks" / "audit_tool_output.sh",
    ]

    missing = [str(path.relative_to(REPO_ROOT)) for path in expected_files if not path.exists()]
    assert not missing, f"missing example files: {missing}"

    shell_scripts = [path for path in expected_files if path.suffix == ".sh"]
    non_executable = [
        str(path.relative_to(REPO_ROOT))
        for path in shell_scripts
        if not os.access(path, os.X_OK)
    ]
    assert not non_executable, f"non-executable hook scripts: {non_executable}"


def test_cli_hook_acceptance_hook_config_and_agent_compile():
    agent_path = EXAMPLE_ROOT / "agent.py"
    config_path = EXAMPLE_ROOT / ".aworld" / "hooks.yaml"

    py_compile.compile(str(agent_path), doraise=True)

    data = yaml.safe_load(config_path.read_text())
    assert data["version"] == "2"
    assert list(data["hooks"].keys()) == [
        "user_input_received",
        "before_tool_call",
        "after_tool_call",
    ]

    user_input_hooks = data["hooks"]["user_input_received"]
    assert [hook["name"] for hook in user_input_hooks] == [
        "block-user-input",
        "rewrite-user-input",
    ]
    assert user_input_hooks[0]["command"] == "./hooks/block_user_input.sh"
    assert user_input_hooks[1]["command"] == "./hooks/rewrite_user_input.sh"

    before_tool_hooks = data["hooks"]["before_tool_call"]
    assert len(before_tool_hooks) == 1
    assert before_tool_hooks[0]["command"] == "./hooks/block_rm_rf.sh"

    after_tool_hooks = data["hooks"]["after_tool_call"]
    assert len(after_tool_hooks) == 1
    assert after_tool_hooks[0]["command"] == "./hooks/audit_tool_output.sh"


def test_cli_hook_acceptance_readme_documents_manual_walkthrough():
    readme = (EXAMPLE_ROOT / "README.md").read_text()

    expected_sections = [
        "# CLI Hook Acceptance Demo",
        "## Scenario 1: Block A Dangerous Prompt Before Agent Execution",
        "## Scenario 2: Rewrite A Prompt Before It Reaches The Agent",
        "## Scenario 3: Block A Dangerous Tool Command",
        "## Scenario 4: Observe Audit Context After A Safe Tool Call",
    ]
    for section in expected_sections:
        assert section in readme

    expected_prompts = [
        "please run rm -rf /tmp/build immediately",
        "clean up build artifacts",
        "Use a shell command to remove ./tmp/build and do it with rm -rf.",
        "Use a shell command to list files in ./tmp/build, then remove only ./tmp/build/demo.txt safely.",
    ]
    for prompt in expected_prompts:
        assert prompt in readme


def _load_example_agent_module():
    module_path = EXAMPLE_ROOT / "agent.py"
    module_name = "test_cli_hook_acceptance_example_agent"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _run_hook_script(script_path: Path, prompt: str) -> dict:
    env = os.environ.copy()
    env["AWORLD_MESSAGE_JSON"] = json.dumps(
        {
            "payload": prompt,
            "content": prompt,
        }
    )
    result = subprocess.run(
        [str(script_path)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout)


def test_cli_hook_acceptance_user_input_blocking_is_narrow_enough_for_tool_gate_demo():
    block_script = EXAMPLE_ROOT / "hooks" / "block_user_input.sh"

    scenario_1 = _run_hook_script(
        block_script,
        "please run rm -rf /tmp/build immediately",
    )
    assert scenario_1["permission_decision"] == "deny"

    scenario_3 = _run_hook_script(
        block_script,
        "Use a shell command to remove ./tmp/build and do it with rm -rf.",
    )
    assert "permission_decision" not in scenario_3


@pytest.mark.asyncio
async def test_cli_hook_acceptance_agent_is_deterministic_and_scoped():
    module = _load_example_agent_module()
    swarm = module.build_cli_hook_acceptance_swarm()
    agent = swarm.topology[0]

    assert "bash" in agent.tool_names

    rewrite_actions = await agent.async_policy(
        Observation(
            content=(
                "List the files under ./tmp/build first, then remove only build artifacts "
                "under ./tmp/build using the safest shell command you can."
            )
        )
    )
    assert len(rewrite_actions) == 1
    assert rewrite_actions[0].tool_name is None
    assert "./tmp/build" in rewrite_actions[0].policy_info
    assert "scoped cleanup request" in rewrite_actions[0].policy_info.lower()

    destructive_actions = await agent.async_policy(
        Observation(content="Use a shell command to remove ./tmp/build and do it with rm -rf.")
    )
    assert len(destructive_actions) == 1
    assert destructive_actions[0].tool_name == "bash"
    assert destructive_actions[0].action_name == "run_command"
    assert destructive_actions[0].params["command"] == "rm -rf ./tmp/build"

    safe_actions = await agent.async_policy(
        Observation(
            content="Use a shell command to list files in ./tmp/build, then remove only ./tmp/build/demo.txt safely."
        )
    )
    assert len(safe_actions) == 1
    assert safe_actions[0].tool_name == "bash"
    assert safe_actions[0].action_name == "run_command"
    assert safe_actions[0].params["command"] == "ls -la ./tmp/build && rm -f ./tmp/build/demo.txt"

    post_tool_actions = await agent.async_policy(
        Observation(
            content="demo.txt removed safely",
            info={"audit_logged": True, "audit_source": "cli_hook_acceptance"},
            action_result=[
                ActionResult(
                    tool_name="bash",
                    action_name="run_command",
                    content="demo.txt removed safely",
                )
            ],
        )
    )
    assert len(post_tool_actions) == 1
    assert post_tool_actions[0].tool_name is None
    assert "audit_logged=true" in post_tool_actions[0].policy_info
    assert "cli_hook_acceptance" in post_tool_actions[0].policy_info


@pytest.mark.asyncio
async def test_cli_hook_acceptance_local_tool_runs_under_hook_runtime(tmp_path, monkeypatch, capsys, caplog):
    workspace = tmp_path / "cli_hook_acceptance"
    shutil.copytree(EXAMPLE_ROOT, workspace)
    monkeypatch.chdir(workspace)

    (workspace / ".aworld" / "trusted").touch()
    (workspace / "tmp" / "build").mkdir(parents=True, exist_ok=True)
    (workspace / "tmp" / "build" / "demo.txt").write_text("artifact\n")

    module = _load_example_agent_module()
    from aworld_cli.executors.local import LocalAgentExecutor

    swarm = module.build_cli_hook_acceptance_swarm()
    executor = LocalAgentExecutor(swarm)

    caplog.clear()
    answer = await executor.chat(
        "Use a shell command to list files in ./tmp/build, then remove only ./tmp/build/demo.txt safely."
    )
    captured = capsys.readouterr()

    assert not (workspace / "tmp" / "build" / "demo.txt").exists()
    assert "Audit hook observed a safe tool call." in answer
    assert "audit_source=cli_hook_acceptance" in answer
    assert "Failed to serialize message to JSON" not in captured.out
    assert "Failed to serialize message to JSON" not in captured.err
    assert all("Failed to serialize message to JSON" not in record.message for record in caplog.records)
