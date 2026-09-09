import os
import sys
import uuid
from pathlib import Path

from aworld.core.tool.func_to_tool import function_to_tool
from aworld.tools import (
    LOCAL_TOOLS_ENV_VAR,
    encode_local_tool_entry,
    parse_local_tool_entries,
    prune_missing_local_tool_entries,
)


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


def test_prune_missing_local_tool_entries_keeps_only_existing_pairs(tmp_path):
    missing_action = tmp_path / "missing_action.py"
    missing_tool = tmp_path / "missing_tool.py"
    existing_action = tmp_path / "existing_action.py"
    existing_tool = tmp_path / "existing_tool.py"
    existing_action.write_text("", encoding="utf-8")
    existing_tool.write_text("", encoding="utf-8")

    value = ";".join(
        [
            encode_local_tool_entry(str(missing_action), str(missing_tool)),
            encode_local_tool_entry(str(existing_action), str(existing_tool)),
        ]
    )

    kept_entries, missing_entries = prune_missing_local_tool_entries(value)

    assert kept_entries == [(str(existing_action), str(existing_tool))]
    assert missing_entries == [(str(missing_action), str(missing_tool))]


def test_function_to_tool_drops_stale_local_tool_entries(monkeypatch, tmp_path):
    stale_action = tmp_path / "stale_action.py"
    stale_tool = tmp_path / "stale_tool.py"
    monkeypatch.setenv(
        LOCAL_TOOLS_ENV_VAR,
        encode_local_tool_entry(str(stale_action), str(stale_tool)),
    )

    suffix = uuid.uuid4().hex[:8]
    tool_name = f"fresh_tool_{suffix}"
    action_name = f"fresh_action_{suffix}"

    function_to_tool(
        sample_tool_for_import_path_test,
        tool_name=tool_name,
        tool_desc="fresh tool",
        name=action_name,
        desc="fresh action",
    )

    entries = parse_local_tool_entries(os.environ.get(LOCAL_TOOLS_ENV_VAR, ""))
    assert len(entries) == 1
    assert entries[0] != (str(stale_action), str(stale_tool))
    assert Path(entries[0][0]).exists()
    assert Path(entries[0][1]).exists()
