from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml

from aworld_gateway.config.models import GatewayConfig


class GatewayConfigLoader:
    def __init__(self, base_dir: Union[str, Path]):
        self.base_dir = Path(base_dir)
        self.config_path = self.base_dir / ".aworld" / "gateway" / "config.yaml"

    def load_or_init(self) -> GatewayConfig:
        if self.config_path.exists():
            with self.config_path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            return GatewayConfig.model_validate(raw)

        config = GatewayConfig()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                config.model_dump(mode="python"),
                fh,
                sort_keys=False,
                allow_unicode=True,
            )
        return config
