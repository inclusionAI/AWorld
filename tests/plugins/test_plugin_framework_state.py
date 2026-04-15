from pathlib import Path

from aworld_cli.plugin_framework.state import PluginStateStore


def test_session_state_is_shared_within_same_plugin_scope(tmp_path):
    store = PluginStateStore(base_dir=tmp_path)

    first = store.session_state("ralph-like", "session-1")
    first.write_text('{"iteration": 1}', encoding="utf-8")

    second = store.session_state("ralph-like", "session-1")
    assert second.read_text(encoding="utf-8") == '{"iteration": 1}'


def test_workspace_state_isolated_by_workspace_slug(tmp_path):
    store = PluginStateStore(base_dir=tmp_path)

    alpha = store.workspace_state("plugin-a", "/tmp/workspace-alpha")
    beta = store.workspace_state("plugin-a", "/tmp/workspace-beta")

    assert alpha != beta


def test_state_store_sanitizes_plugin_id_to_prevent_escape(tmp_path):
    store = PluginStateStore(base_dir=tmp_path)

    escaped = store.session_state("../../evil", "session-escape")

    assert tmp_path.resolve() in escaped.resolve().parents
