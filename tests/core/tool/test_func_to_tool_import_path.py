import sys
import uuid
from pathlib import Path

from aworld.core.tool.func_to_tool import function_to_tool
from aworld.tools import parse_local_tool_entries


def sample_tool_for_import_path_test(message: str) -> str:
    return message


def test_function_to_tool_loads_generated_modules_without_cwd_on_sys_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if path not in ("", str(tmp_path))],
    )

    suffix = uuid.uuid4().hex[:8]
    tool_name = f"test_tool_{suffix}"
    action_name = f"test_action_{suffix}"

    function_to_tool(
        sample_tool_for_import_path_test,
        tool_name=tool_name,
        tool_desc="test tool",
        name=action_name,
        desc="test action",
    )

    generated = list(tmp_path.glob(f"*{suffix}*.py"))
    assert not generated, "dynamic tool modules should not pollute the current working directory"


def test_parse_local_tool_entries_supports_legacy_and_explicit_tool_paths():
    legacy_action = "/tmp/cron_tool123abc__tmp_action.py"
    explicit_action = "/tmp/other_action456def__tmp_action.py"
    explicit_tool = "/tmp/cron456def__tmp.py"

    parsed = parse_local_tool_entries(
        f"{legacy_action};{explicit_action}|{explicit_tool}"
    )

    assert parsed[0] == (legacy_action, "/tmp/cron_tool123abc__tmp.py")
    assert parsed[1] == (explicit_action, explicit_tool)
