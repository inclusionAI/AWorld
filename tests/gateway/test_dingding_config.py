from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import (
    ChannelConfigMap,
    DingdingChannelConfig,
    GatewayConfigLoader,
)


def test_dingding_channel_config_has_expected_defaults():
    config = DingdingChannelConfig()

    assert config.enabled is False
    assert config.default_agent_id is None
    assert config.client_id_env == "AWORLD_DINGTALK_CLIENT_ID"
    assert config.client_secret_env == "AWORLD_DINGTALK_CLIENT_SECRET"
    assert config.card_template_id_env == "AWORLD_DINGTALK_CARD_TEMPLATE_ID"
    assert config.enable_ai_card is True
    assert config.enable_attachments is True
    assert config.workspace_dir is None


def test_channel_config_map_wires_dingding_channel_config():
    channels = ChannelConfigMap()
    assert isinstance(channels.dingding, DingdingChannelConfig)


def test_loader_ignores_legacy_dingding_implemented_field(tmp_path: Path):
    base_dir = tmp_path / "project"
    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        (
            "default_agent_id: aworld\n"
            "gateway:\n"
            "  host: 127.0.0.1\n"
            "  port: 18888\n"
            "channels:\n"
            "  telegram:\n"
            "    enabled: false\n"
            "  web:\n"
            "    enabled: false\n"
            "  dingding:\n"
            "    enabled: false\n"
            "    implemented: true\n"
            "    client_id_env: CUSTOM_DING_CLIENT_ID\n"
            "    client_secret_env: CUSTOM_DING_CLIENT_SECRET\n"
            "  feishu:\n"
            "    enabled: false\n"
            "  wecom:\n"
            "    enabled: false\n"
        ),
        encoding="utf-8",
    )

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    assert config.channels.dingding.client_id_env == "CUSTOM_DING_CLIENT_ID"
    assert config.channels.dingding.client_secret_env == "CUSTOM_DING_CLIENT_SECRET"
