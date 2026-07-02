from __future__ import annotations

def register_builtin_top_level_commands(registry) -> None:
    from .resume_cmd import ResumeTopLevelCommand

    registry.register(ResumeTopLevelCommand(), source="builtin")
