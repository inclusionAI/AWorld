from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.registry import ChannelRegistry


def test_list_channels_reports_phase_one_builtins_with_implementation_flags():
    channels = ChannelRegistry().list_channels()

    assert channels == [
        {"name": "telegram", "implemented": True},
        {"name": "web", "implemented": False},
        {"name": "dingding", "implemented": False},
        {"name": "feishu", "implemented": False},
        {"name": "wecom", "implemented": False},
    ]
