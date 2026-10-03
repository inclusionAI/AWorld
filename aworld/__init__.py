"""AWorld v1: independent Agent + Context + Tool kernel."""

from importlib.metadata import PackageNotFoundError, version

try:
    # The repository can also produce the full legacy Runtime distribution.
    # Report the version of the artifact that was actually installed instead
    # of leaking the minimal v1 source version into that compatibility wheel.
    __version__ = version("aworld")
except PackageNotFoundError:
    from ._version import __version__


def __getattr__(name):
    # Historical APIs are loaded only when callers explicitly request them.
    if name in {"PROJECT_CONFIG", "configure", "cleanup", "debug_mode", "log_level"}:
        from . import _legacy_init
        return getattr(_legacy_init, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
