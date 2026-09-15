from __future__ import annotations

import ast
from pathlib import Path


def _production_python_files(root: Path) -> list[Path]:
    return sorted(
        (
            *root.joinpath("aworld").rglob("*.py"),
            *root.joinpath("aworld-cli", "src", "aworld_cli").rglob("*.py"),
        )
    )


def _depends_on_examples(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("examples"):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name.startswith("examples") for alias in node.names
        ):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("examples."):
                return True
    return False


def test_production_code_has_no_example_package_dependency() -> None:
    root = Path(__file__).resolve().parents[2]
    dependencies = {
        str(path.relative_to(root))
        for path in _production_python_files(root)
        if _depends_on_examples(path)
    }

    assert dependencies == set()


def test_runtime_wheel_configuration_excludes_example_packages() -> None:
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "setup.py").read_text(encoding="utf-8"))
    setup_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "setup"
    )
    packages_keyword = next(
        keyword for keyword in setup_call.keywords if keyword.arg == "packages"
    )
    assert isinstance(packages_keyword.value, ast.Call)
    exclude_keyword = next(
        keyword
        for keyword in packages_keyword.value.keywords
        if keyword.arg == "exclude"
    )
    assert isinstance(exclude_keyword.value, ast.List)
    exclusions = {
        item.value
        for item in exclude_keyword.value.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }

    assert {"examples", "examples.*", "train.examples", "train.examples.*"} <= (
        exclusions
    )
