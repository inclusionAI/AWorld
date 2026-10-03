"""AWorld v1: independent Agent + Context + Tool kernel."""
from ._version import __version__


def __getattr__(name):
    # Historical APIs are loaded only when callers explicitly request them.
    if name in {"PROJECT_CONFIG", "configure", "cleanup", "debug_mode", "log_level"}:
        from . import _legacy_init
        return getattr(_legacy_init, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
