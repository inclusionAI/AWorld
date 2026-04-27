from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfigLoader, WechatChannelConfig


def test_wechat_config_defaults_are_text_phase_one_safe() -> None:
    cfg = WechatChannelConfig()

    assert cfg.enabled is False
    assert cfg.account_id_env == "AWORLD_WECHAT_ACCOUNT_ID"
    assert cfg.token_env == "AWORLD_WECHAT_TOKEN"
    assert cfg.base_url_env == "AWORLD_WECHAT_BASE_URL"
    assert cfg.cdn_base_url_env == "AWORLD_WECHAT_CDN_BASE_URL"
    assert cfg.dm_policy == "open"
    assert cfg.group_policy == "disabled"
    assert cfg.allow_from == []
    assert cfg.group_allow_from == []
    assert cfg.split_multiline_messages is False


def test_loader_persists_wechat_defaults(tmp_path: Path) -> None:
    base_dir = tmp_path / "project"
    base_dir.mkdir()

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()
    assert config.channels.wechat.enabled is False

    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    assert raw["channels"]["wechat"]["enabled"] is False
