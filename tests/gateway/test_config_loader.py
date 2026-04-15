from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfigLoader


def test_load_or_init_creates_default_config_when_missing(tmp_path):
    base_dir = tmp_path / "project"
    base_dir.mkdir()

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    assert config_path.exists()
    assert config.default_agent_id == "aworld"
    assert config.telegram.enabled is False
    assert config.web.enabled is False

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    assert raw["default_agent_id"] == "aworld"
    assert raw["telegram"]["enabled"] is False
    assert raw["web"]["enabled"] is False


def test_load_or_init_preserves_existing_config(tmp_path):
    base_dir = tmp_path / "project"
    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        (
            "default_agent_id: custom-agent\n"
            "telegram:\n"
            "  enabled: true\n"
            "  default_agent_id: telegram-agent\n"
            "web:\n"
            "  enabled: false\n"
        ),
        encoding="utf-8",
    )

    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()

    assert config.default_agent_id == "custom-agent"
    assert config.telegram.enabled is True
    assert config.telegram.default_agent_id == "telegram-agent"
