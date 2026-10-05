

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path


def _is_source_checkout() -> bool:
    return (Path(__file__).resolve().parents[2] / "pyproject.toml").is_file()


def _resolve_version() -> str:
    """Return metadata only when it belongs to this imported package tree."""

    if _is_source_checkout():
        from aworld._version import __version__ as source_version

        return source_version

    try:
        installed = distribution("aworld-cli")
        installed_init = Path(
            installed.locate_file("aworld_cli/__init__.py")
        ).resolve()
        if installed_init == Path(__file__).resolve():
            return installed.version
    except (OSError, PackageNotFoundError):
        pass

    # A source or editable checkout follows the synchronized v1 source
    # version.  In particular, do not trust an unrelated legacy CLI that is
    # installed elsewhere on sys.path.
    from aworld._version import __version__ as source_version

    return source_version


__version__ = _resolve_version()


def __getattr__(name):
    if name in {"AWorldCLI", "CliRuntime", "BaseCliRuntime", "AgentInfo", "TeamInfo", "AgentExecutor", "CLIHumanHandler"}:
        from . import _legacy_init
        return getattr(_legacy_init, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
