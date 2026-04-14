from pathlib import Path
import tempfile

from aworld.core.tool.func_to_tool import _get_generated_tool_dir, _get_generated_tool_dir_name


def test_generated_tool_dir_defaults_to_system_temp_dir(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_TMP_DIR", raising=False)
    monkeypatch.setattr("aworld.core.tool.func_to_tool.getpass.getuser", lambda: "alice")
    monkeypatch.setattr("aworld.core.tool.func_to_tool.os.getuid", lambda: 1234)

    output_dir = _get_generated_tool_dir()

    assert output_dir.exists()
    assert output_dir.is_dir()
    assert output_dir == Path(tempfile.gettempdir()) / "aworld_local_tools_alice_1234"


def test_generated_tool_dir_name_sanitizes_username(monkeypatch):
    monkeypatch.setattr("aworld.core.tool.func_to_tool.getpass.getuser", lambda: "alice@example.com")
    monkeypatch.setattr("aworld.core.tool.func_to_tool.os.getuid", lambda: 1234)

    assert _get_generated_tool_dir_name() == "aworld_local_tools_alice_example.com_1234"


def test_generated_tool_dir_respects_env_override(monkeypatch, tmp_path):
    custom_dir = tmp_path / "local-tools"
    monkeypatch.setenv("AWORLD_TOOL_TMP_DIR", str(custom_dir))

    output_dir = _get_generated_tool_dir()

    assert output_dir == custom_dir
    assert output_dir.exists()
    assert output_dir.is_dir()
