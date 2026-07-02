from pathlib import Path

import pytest

from aworld_cli.memory.discovery import (
    discover_workspace_instruction_layers,
    load_instruction_text,
)


def test_discovery_prefers_dot_aworld_workspace_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"

    monkeypatch.setattr(Path, "home", lambda: home)

    (home / ".aworld").mkdir(parents=True)
    (home / ".aworld" / "AWORLD.md").write_text("global file", encoding="utf-8")
    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text("workspace file", encoding="utf-8")
    (workspace / "AWORLD.md").write_text("compat root file", encoding="utf-8")

    layers = discover_workspace_instruction_layers(workspace_path=str(workspace))

    assert layers.global_file == home / ".aworld" / "AWORLD.md"
    assert layers.workspace_file == workspace / ".aworld" / "AWORLD.md"
    assert layers.compatibility_file == workspace / "AWORLD.md"
    assert layers.canonical_write_file == workspace / ".aworld" / "AWORLD.md"
    assert layers.effective_read_files == (
        home / ".aworld" / "AWORLD.md",
        workspace / ".aworld" / "AWORLD.md",
    )
    assert layers.warning is None


def test_discovery_warns_when_only_root_file_exists(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"

    monkeypatch.setattr(Path, "home", lambda: home)

    workspace.mkdir(parents=True)
    (workspace / "AWORLD.md").write_text("compat root file", encoding="utf-8")

    layers = discover_workspace_instruction_layers(workspace_path=str(workspace))

    assert layers.global_file is None
    assert layers.workspace_file is None
    assert layers.compatibility_file == workspace / "AWORLD.md"
    assert layers.canonical_write_file == workspace / ".aworld" / "AWORLD.md"
    assert layers.effective_read_files == (workspace / "AWORLD.md",)
    assert layers.warning is not None
    assert "compatibility" in layers.warning.lower()
    assert "move edits" in layers.warning.lower()


def test_discovery_effective_read_order_uses_global_then_compatibility_when_needed(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"

    monkeypatch.setattr(Path, "home", lambda: home)

    (home / ".aworld").mkdir(parents=True)
    (home / ".aworld" / "AWORLD.md").write_text("global file", encoding="utf-8")
    workspace.mkdir(parents=True)
    (workspace / "AWORLD.md").write_text("compat root file", encoding="utf-8")

    layers = discover_workspace_instruction_layers(workspace_path=str(workspace))

    assert layers.effective_read_files == (
        home / ".aworld" / "AWORLD.md",
        workspace / "AWORLD.md",
    )


def test_load_instruction_text_expands_imports_from_canonical_file(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".aworld").mkdir(parents=True)
    main_file = workspace / ".aworld" / "AWORLD.md"
    main_file.write_text("# Root\n@import guides.md\n", encoding="utf-8")
    (workspace / ".aworld" / "guides.md").write_text("## Guides\n- use tests\n", encoding="utf-8")

    content = load_instruction_text(main_file)

    assert "Root" in content
    assert "Guides" in content
    assert "use tests" in content


def test_load_instruction_text_expands_imports_from_compatibility_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    main_file = workspace / "AWORLD.md"
    main_file.write_text("# Root\n@shared.md\n", encoding="utf-8")
    (workspace / "shared.md").write_text("## Shared\n- compatibility read\n", encoding="utf-8")

    content = load_instruction_text(main_file)

    assert "Root" in content
    assert "Shared" in content
    assert "compatibility read" in content


def test_load_instruction_text_supports_both_import_forms_in_one_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    main_file = workspace / "AWORLD.md"
    main_file.write_text("# Root\n@import guides.md\n@shared.md\n", encoding="utf-8")
    (workspace / "guides.md").write_text("## Guides\n- import keyword\n", encoding="utf-8")
    (workspace / "shared.md").write_text("## Shared\n- bare form\n", encoding="utf-8")

    content = load_instruction_text(main_file)

    assert "Guides" in content
    assert "import keyword" in content
    assert "Shared" in content
    assert "bare form" in content


def test_load_instruction_text_marks_circular_imports(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    main_file = workspace / "AWORLD.md"
    first = workspace / "first.md"
    second = workspace / "second.md"

    main_file.write_text("@first.md\n", encoding="utf-8")
    first.write_text("# First\n@second.md\n", encoding="utf-8")
    second.write_text("# Second\n@first.md\n", encoding="utf-8")

    content = load_instruction_text(main_file)

    assert "Circular import" in content
    assert "first.md" in content


def test_load_instruction_text_marks_missing_imports(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    main_file = workspace / "AWORLD.md"
    main_file.write_text("# Root\n@missing.md\n", encoding="utf-8")

    content = load_instruction_text(main_file)

    assert "Import not found" in content
    assert "missing.md" in content


def test_load_instruction_text_returns_inline_error_for_root_read_failure(
    tmp_path, monkeypatch
):
    main_file = tmp_path / "AWORLD.md"
    main_file.write_text("# Root\n", encoding="utf-8")

    original_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == main_file.resolve():
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad data")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    content = load_instruction_text(main_file)

    assert "Error reading file" in content
    assert str(main_file.resolve()) in content


def test_load_instruction_text_returns_inline_error_for_import_read_failure(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    main_file = workspace / "AWORLD.md"
    imported = workspace / "broken.md"

    main_file.write_text("# Root\n@broken.md\n", encoding="utf-8")
    imported.write_text("broken content\n", encoding="utf-8")

    original_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == imported.resolve():
            raise OSError("cannot read imported file")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    content = load_instruction_text(main_file)

    assert "# Root" in content
    assert "Error reading file" in content
    assert str(imported.resolve()) in content
