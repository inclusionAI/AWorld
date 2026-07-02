from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfigLoader
from aworld_gateway.config import WecomChannelConfig


def test_wecom_config_defaults_are_text_phase_one_safe() -> None:
    cfg = WecomChannelConfig()

    assert cfg.enabled is False
    assert cfg.bot_id_env == "AWORLD_WECOM_BOT_ID"
    assert cfg.secret_env == "AWORLD_WECOM_SECRET"
    assert cfg.websocket_url_env == "AWORLD_WECOM_WEBSOCKET_URL"
    assert cfg.dm_policy == "open"
    assert cfg.group_policy == "open"


def test_loader_persists_wecom_defaults(tmp_path: Path) -> None:
    base_dir = tmp_path / "project"
    base_dir.mkdir()

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    assert config.channels.wecom.enabled is False
    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert raw["channels"]["wecom"]["enabled"] is False
