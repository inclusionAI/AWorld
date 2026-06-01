from __future__ import annotations

def register_builtin_top_level_commands(registry) -> None:
    from .evaluator_cmd import EvaluatorTopLevelCommand

    registry.register(EvaluatorTopLevelCommand())
