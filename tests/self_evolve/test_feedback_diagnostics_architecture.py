from __future__ import annotations

import ast
import inspect

from aworld.self_evolve import feedback_diagnostics, runner


def test_feedback_diagnostics_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(feedback_diagnostics))
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


def test_runner_exposes_feedback_diagnostic_compatibility_alias() -> None:
    assert (
        runner._typed_gate_feedback_metrics
        is feedback_diagnostics._typed_gate_feedback_metrics
    )
