from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfigLoader


def test_load_or_init_creates_default_config_when_missing(tmp_path):
    base_dir = tmp_path / "project"
    base_dir.mkdir()

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    assert config_path.exists()
    assert config.default_agent_id == "aworld"
    assert config.channels.telegram.enabled is False
    assert config.channels.web.enabled is False

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    assert raw["default_agent_id"] == "aworld"
    assert raw["gateway"]["port"] == 18888
    assert raw["gateway"]["public_base_url"] is None
    assert raw["channels"]["telegram"]["enabled"] is False
    assert raw["channels"]["web"]["enabled"] is False
    assert raw["channels"]["wechat"]["enabled"] is False
    assert raw["channels"]["wecom"]["enabled"] is False


def test_load_or_init_persists_public_base_url_default(tmp_path):
    base_dir = tmp_path / "project"
    base_dir.mkdir()

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    assert config.gateway.public_base_url is None

    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert raw["gateway"]["public_base_url"] is None


def test_load_or_init_preserves_existing_config(tmp_path):
    base_dir = tmp_path / "project"
    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        (
            "default_agent_id: custom-agent\n"
            "gateway:\n"
            "  host: 0.0.0.0\n"
            "  port: 19999\n"
            "channels:\n"
            "  telegram:\n"
            "    enabled: true\n"
            "    default_agent_id: telegram-agent\n"
            "  web:\n"
            "    enabled: false\n"
            "  dingding:\n"
            "    enabled: false\n"
            "  wechat:\n"
            "    enabled: false\n"
            "  feishu:\n"
            "    enabled: false\n"
            "  wecom:\n"
            "    enabled: false\n"
        ),
        encoding="utf-8",
    )

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    assert config.default_agent_id == "custom-agent"
    assert config.channels.telegram.enabled is True
    assert config.channels.telegram.default_agent_id == "telegram-agent"


def test_load_or_init_rejects_unknown_config_keys(tmp_path):
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
            "    typo_field: should-fail\n"
            "  web:\n"
            "    enabled: false\n"
            "  dingding:\n"
            "    enabled: false\n"
            "  wechat:\n"
            "    enabled: false\n"
            "  feishu:\n"
            "    enabled: false\n"
            "  wecom:\n"
            "    enabled: false\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        GatewayConfigLoader(base_dir=base_dir).load_or_init()


def test_load_or_init_ignores_legacy_empty_telegram_bot_token_field(tmp_path):
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
            "    bot_token: null\n"
            "    bot_token_env: AWORLD_TELEGRAM_BOT_TOKEN\n"
            "  web:\n"
            "    enabled: false\n"
            "  dingding:\n"
            "    enabled: false\n"
            "  wechat:\n"
            "    enabled: false\n"
            "  feishu:\n"
            "    enabled: false\n"
            "  wecom:\n"
            "    enabled: false\n"
        ),
        encoding="utf-8",
    )

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    assert config.channels.telegram.bot_token_env == "AWORLD_TELEGRAM_BOT_TOKEN"


def test_load_or_init_ignores_legacy_wecom_implemented_field(tmp_path):
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
            "  wechat:\n"
            "    enabled: false\n"
            "  feishu:\n"
            "    enabled: false\n"
            "  wecom:\n"
            "    enabled: false\n"
            "    implemented: false\n"
        ),
        encoding="utf-8",
    )

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    assert config.channels.wecom.enabled is False
