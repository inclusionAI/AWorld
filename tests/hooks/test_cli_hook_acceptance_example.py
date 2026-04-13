from pathlib import Path
import os
import py_compile

import yaml


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
