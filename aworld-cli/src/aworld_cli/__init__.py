

def __getattr__(name):
    if name in {"AWorldCLI", "CliRuntime", "BaseCliRuntime", "AgentInfo", "TeamInfo", "AgentExecutor", "CLIHumanHandler"}:
        from . import _legacy_init
        return getattr(_legacy_init, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
