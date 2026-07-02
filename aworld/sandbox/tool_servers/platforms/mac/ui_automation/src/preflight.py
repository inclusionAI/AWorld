import os
import platform
import shutil


def gate_enabled() -> bool:
    value = os.getenv("AWORLD_ENABLE_MAC_UI_AUTOMATION", "")
    return value.strip().lower() in {"1", "true", "yes"}


def is_macos_host() -> bool:
    return platform.system() == "Darwin"


def detect_backend_availability() -> bool:
    return shutil.which("peekaboo") is not None
