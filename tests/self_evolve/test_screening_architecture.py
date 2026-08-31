from __future__ import annotations

import ast
import inspect

from aworld.self_evolve.controllers import (
    screening,
    screening_execution,
    screening_helpers,
)
from aworld.self_evolve.controllers.screening_execution import (
    execute_screen_candidate_population,
)
from aworld.self_evolve.runner import SelfEvolveRunner


def test_runner_screening_executor_is_a_compatibility_alias() -> None:
    assert (
        SelfEvolveRunner._execute_screen_candidate_population
        is execute_screen_candidate_population
    )


def test_screening_modules_do_not_import_runner() -> None:
    for module in (screening, screening_execution, screening_helpers):
        tree = ast.parse(inspect.getsource(module))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert "aworld.self_evolve.runner" not in imported_modules
