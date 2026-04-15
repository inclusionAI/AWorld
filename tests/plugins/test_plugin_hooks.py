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


def test_plugin_hooks_are_sorted_by_priority_then_identity(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"

    for plugin_root, plugin_id, priority in (
        (alpha, "alpha", 50),
        (beta, "beta", 10),
    ):
        (plugin_root / ".aworld-plugin").mkdir(parents=True)
        (plugin_root / "hooks").mkdir()
        (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                f"\"id\": \"{plugin_id}\", "
                f"\"name\": \"{plugin_id}\", "
                "\"version\": \"1.0.0\", "
                "\"entrypoints\": {"
                "\"hooks\": ["
                "{"
                "\"id\": \"stop-loop\", "
                "\"target\": \"hooks/stop.py\", "
                "\"metadata\": {"
                "\"hook_point\": \"stop\", "
                f"\"priority\": {priority}"
                "}"
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )
        (plugin_root / "hooks" / "stop.py").write_text(
            "def handle_event(event, state):\n    return {'action': 'allow'}\n",
            encoding="utf-8",
        )

    plugins = discover_plugins([alpha, beta])
    hooks = load_plugin_hooks(plugins)

    assert [hook.plugin_id for hook in hooks["stop"]] == ["beta", "alpha"]
