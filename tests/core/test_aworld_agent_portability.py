from datetime import timedelta
from zoneinfo import ZoneInfoNotFoundError

from aworld_cli.builtin_agents.smllc.agents import aworld_agent


def test_cast_native_import_failure_is_optional() -> None:
    def incompatible_native_module(_name: str):
        raise OSError("GLIBC_2.33 not found")

    available, reason = aworld_agent._register_optional_cast_tools(
        incompatible_native_module
    )

    assert available is False
    assert reason == "OSError: GLIBC_2.33 not found"


def test_beijing_timezone_has_portable_fixed_offset_fallback(monkeypatch) -> None:
    def missing_timezone_data(_name: str):
        raise ZoneInfoNotFoundError("Asia/Shanghai")

    monkeypatch.setattr(aworld_agent, "ZoneInfo", missing_timezone_data)

    resolved = aworld_agent._resolve_beijing_timezone()

    assert resolved.utcoffset(None) == timedelta(hours=8)
    assert str(resolved) == "UTC+08:00"
