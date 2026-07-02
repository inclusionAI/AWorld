from __future__ import annotations

import os
from typing import Any

AWORLD_GATEWAY_QUIET_BOOT_ENV = "AWORLD_GATEWAY_QUIET_BOOT"
_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def enable_quiet_gateway_boot() -> None:
    os.environ[AWORLD_GATEWAY_QUIET_BOOT_ENV] = "true"


def is_quiet_gateway_boot_enabled() -> bool:
    return (
        str(os.getenv(AWORLD_GATEWAY_QUIET_BOOT_ENV, "")).strip().lower()
        in _TRUTHY_VALUES
    )


def log_verbose_boot(logger_obj: Any, message: str, *, level: str = "info") -> None:
    effective_level = "debug" if is_quiet_gateway_boot_enabled() else level
    getattr(logger_obj, effective_level)(message)
