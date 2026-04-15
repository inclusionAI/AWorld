from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.registry import ChannelRegistry


def test_list_channels_reports_phase_one_builtins_with_implementation_flags():
    summary = ChannelRegistry().list_channels()

    assert summary["telegram"]["implemented"] is True
    assert summary["web"]["implemented"] is False
    assert summary["dingding"]["implemented"] is False
    assert summary["feishu"]["implemented"] is False
    assert summary["wecom"]["implemented"] is False
