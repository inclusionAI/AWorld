from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.preflight import (
    detect_backend_availability,
    gate_enabled,
    is_macos_host,
)


def test_gate_enabled_only_accepts_truthy_flag(monkeypatch):
    monkeypatch.delenv("AWORLD_ENABLE_MAC_UI_AUTOMATION", raising=False)
    assert gate_enabled() is False
    monkeypatch.setenv("AWORLD_ENABLE_MAC_UI_AUTOMATION", "1")
    assert gate_enabled() is True


def test_detect_backend_availability_checks_peekaboo(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.preflight.shutil.which",
        lambda name: "/usr/local/bin/peekaboo" if name == "peekaboo" else None,
    )
    assert detect_backend_availability() is True


def test_is_macos_host_rejects_non_darwin(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.preflight.platform.system",
        lambda: "Linux",
    )
    assert is_macos_host() is False
