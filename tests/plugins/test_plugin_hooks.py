import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.hooks import PluginHookResult, load_plugin_hooks


def test_load_plugin_hook_entrypoints():
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    hooks = load_plugin_hooks([plugin])

    assert "stop" in hooks
    assert hooks["stop"][0].entrypoint_id == "loop-stop"


@pytest.mark.asyncio
async def test_stop_hook_can_block_and_continue_session(tmp_path):
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["stop"][0].run(
        event={"transcript_path": str(tmp_path / "transcript.jsonl")},
        state={"iteration": 1, "prompt": "keep going"},
    )

    assert result.action == "block_and_continue"
    assert result.follow_up_prompt == "keep going"


def test_plugin_hook_result_accepts_claude_style_block_decision():
    result = PluginHookResult.from_payload(
        {
            "decision": "block",
            "reason": "keep going",
            "systemMessage": "Loop continues",
        }
    )

    assert result.action == "block_and_continue"
    assert result.follow_up_prompt == "keep going"
    assert result.system_message == "Loop continues"
