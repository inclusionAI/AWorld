import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module
from aworld_cli.core.installed_skill_manager import InstalledSkillManager


def _write_skill(root: Path, skill_name: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\n\n# Demo\n",
        encoding="utf-8",
    )


def test_skill_install_and_list_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()

    install_output = capsys.readouterr().out
    assert "installed successfully" in install_output

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()

    list_output = capsys.readouterr().out
    assert "source-skills" in list_output
    assert "global" in list_output
    assert "skill_count=1" in list_output


def test_skill_import_and_remove_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    manual_entry = tmp_path / ".aworld" / "skills" / "installed" / "manual-demo"
    _write_skill(manual_entry, "agent-browser")

    monkeypatch.setattr(
        sys, "argv", ["aworld-cli", "skill", "import", str(manual_entry)]
    )
    main_module.main()

    import_output = capsys.readouterr().out
    assert "imported successfully" in import_output

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "remove", "manual-demo"])
    main_module.main()

    remove_output = capsys.readouterr().out
    assert "removed successfully" in remove_output


def test_skill_update_cli_rejects_non_git_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()

    install_id = InstalledSkillManager().list_installs()[0]["install_id"]

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "update", install_id])
    with pytest.raises(
        ValueError, match="Only git-backed installed skill entries can be updated"
    ):
        main_module.main()


def test_skill_install_accepts_agent_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "developer-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(
        sys,
        "argv",
        ["aworld-cli", "skill", "install", str(source), "--scope", "agent:developer"],
    )
    main_module.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()
    output = capsys.readouterr().out

    assert "agent:developer" in output
