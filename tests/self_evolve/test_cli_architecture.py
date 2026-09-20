from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from aworld.self_evolve import cli_ingestion, cli_orchestration, cli_rerun
from aworld.self_evolve.runner import optimize_from_cli_request


@pytest.mark.parametrize(
    "module",
    (cli_ingestion, cli_rerun, cli_orchestration),
)
def test_cli_modules_do_not_import_runner(module) -> None:
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


def test_runner_cli_entry_point_is_a_typed_orchestration_adapter() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(optimize_from_cli_request)))
    function = tree.body[0]

    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    assert len(function.body) == 2
    returned = function.body[-1]
    assert isinstance(returned, ast.Return)
    assert isinstance(returned.value, ast.Call)
    assert isinstance(returned.value.func, ast.Name)
    assert returned.value.func.id == "execute_cli_optimization"
