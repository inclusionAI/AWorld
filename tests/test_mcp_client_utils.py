from pathlib import Path

from aworld.mcp_client import utils


def test_iter_obsidian_vault_candidates_returns_parent_directories(tmp_path: Path):
    docs_root = tmp_path / "Documents"
    vault_a = docs_root / "VaultA"
    vault_b = docs_root / "Notes" / "ResearchVault"
    (vault_a / ".obsidian").mkdir(parents=True)
    (vault_b / ".obsidian").mkdir(parents=True)

    candidates = utils._iter_obsidian_vault_candidates((docs_root,))

    assert str(vault_a) in candidates
    assert str(vault_b) in candidates


def test_augment_tool_description_adds_obsidian_vault_hints(monkeypatch):
    monkeypatch.setattr(
        utils,
        "get_obsidian_vault_candidates",
        lambda: ["/Users/test/Documents/wuman_knowledge"],
    )

    augmented = utils._augment_tool_description(
        "terminal",
        "mcp_execute_command",
        "Execute terminal commands safely.",
    )

    assert "Detected Obsidian vaults" in augmented
    assert "/Users/test/Documents/wuman_knowledge" in augmented
    assert "save notes to Obsidian" in augmented


def test_augment_tool_description_leaves_non_terminal_tools_unchanged(monkeypatch):
    monkeypatch.setattr(
        utils,
        "get_obsidian_vault_candidates",
        lambda: ["/Users/test/Documents/wuman_knowledge"],
    )

    original = "Read file content."
    assert utils._augment_tool_description("filesystem", "read_file", original) == original


def test_stdio_server_environment_inherits_only_opted_in_prefixes(monkeypatch):
    monkeypatch.setenv(
        "AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES",
        "AWORLD_REPLAY_,TRACE_CONTEXT_",
    )
    monkeypatch.setenv("AWORLD_REPLAY_ENDPOINT_BROWSER", "http://127.0.0.1:54321")
    monkeypatch.setenv("TRACE_CONTEXT_RUN_ID", "run-123")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")

    environment = utils._stdio_server_environment(
        {"env": {"EXPLICIT_SETTING": "enabled"}}
    )

    assert environment == {
        "AWORLD_REPLAY_ENDPOINT_BROWSER": "http://127.0.0.1:54321",
        "EXPLICIT_SETTING": "enabled",
        "TRACE_CONTEXT_RUN_ID": "run-123",
    }


def test_stdio_server_environment_preserves_legacy_explicit_only_behavior(monkeypatch):
    monkeypatch.delenv("AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES", raising=False)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")

    assert utils._stdio_server_environment({"env": {"ONLY": "this"}}) == {
        "ONLY": "this"
    }
