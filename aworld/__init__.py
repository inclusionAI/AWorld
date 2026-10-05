"""AWorld v1: independent Agent + Context + Tool kernel."""

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path


def _is_source_checkout() -> bool:
    return (Path(__file__).resolve().parent.parent / "pyproject.toml").is_file()


def _resolve_version() -> str:
    """Return metadata only when it belongs to this imported package tree."""

    if _is_source_checkout():
        from ._version import __version__ as source_version

        return source_version

    try:
        installed = distribution("aworld")
        installed_init = Path(installed.locate_file("aworld/__init__.py")).resolve()
        if installed_init == Path(__file__).resolve():
            return installed.version
    except (OSError, PackageNotFoundError):
        pass

    # Source and editable checkouts use the synchronized v1 source version.
    # This prevents an unrelated older site-packages installation from
    # contaminating a checkout imported through PYTHONPATH.
    from ._version import __version__ as source_version

    return source_version


__version__ = _resolve_version()


def __getattr__(name):
    # Historical APIs are loaded only when callers explicitly request them.
    if name in {"PROJECT_CONFIG", "configure", "cleanup", "debug_mode", "log_level"}:
        from . import _legacy_init
        return getattr(_legacy_init, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
