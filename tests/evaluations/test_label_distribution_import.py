from __future__ import annotations

import ast
from pathlib import Path


def test_label_distribution_keeps_scipy_as_a_lazy_runtime_dependency() -> None:
    source_path = (
        Path(__file__).resolve().parents[2]
        / "aworld"
        / "evaluations"
        / "scorers"
        / "label_distribution.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))

    top_level_scipy_imports = [
        node
        for node in module.body
        if (
            isinstance(node, ast.Import)
            and any(alias.name == "scipy" for alias in node.names)
        )
        or (
            isinstance(node, ast.ImportFrom)
            and node.module == "scipy"
        )
    ]
    top_level_dynamic_installs = [
        node
        for node in module.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "import_package"
    ]

    assert top_level_scipy_imports == []
    assert top_level_dynamic_installs == []

    scorer = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "LabelDistributionScorer"
    )
    summarize = next(
        node
        for node in scorer.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and node.name == "summarize"
    )
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "scipy"
        for node in summarize.body
    )
