

from importlib.metadata import PackageNotFoundError, version


try:
    # Resolve the version from the artifact that is actually installed.  The
    # repository publishes both the synchronized v1 distribution and a
    # separately versioned Runtime-compatibility CLI wheel.
    __version__ = version("aworld-cli")
except PackageNotFoundError:
    # A source checkout follows the synchronized v1 package version.
    from aworld._version import __version__


def __getattr__(name):
    if name in {"AWorldCLI", "CliRuntime", "BaseCliRuntime", "AgentInfo", "TeamInfo", "AgentExecutor", "CLIHumanHandler"}:
        from . import _legacy_init
        return getattr(_legacy_init, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
