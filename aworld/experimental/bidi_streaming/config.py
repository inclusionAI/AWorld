from typing import Dict, Any
from aworld.config import BaseConfig


class ServingConfig(BaseConfig):
    host: str = "0.0.0.0"
    port: int = 8000
    uvicorn_config: Dict[str, Any] = {}