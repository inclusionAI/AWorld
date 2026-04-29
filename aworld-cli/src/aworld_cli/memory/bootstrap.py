from __future__ import annotations

import os

from aworld.core.context.amni.config import build_memory_config
from aworld.core.memory import MemoryConfig


def resolve_cli_memory_mode() -> str:
    raw = os.getenv("AWORLD_CLI_MEMORY_MODE", "hybrid").strip().lower()
    if raw == "legacy":
        return "legacy"
    return "hybrid"


def build_cli_memory_config() -> MemoryConfig:
    provider = "aworld" if resolve_cli_memory_mode() == "legacy" else "hybrid"
    return build_memory_config().model_copy(update={"provider": provider})


def register_cli_memory_provider() -> None:
    from aworld.memory.main import register_memory_provider
    from aworld_cli.memory.hybrid import build_hybrid_memory_provider

    if resolve_cli_memory_mode() == "hybrid":
        register_memory_provider("hybrid", build_hybrid_memory_provider)
