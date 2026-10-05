from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CLI_METADATA = ROOT / "packaging/runtime/aworld-cli.pyproject.toml"


def _literal_assignment(path: Path, name: str) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            assert isinstance(value, str)
            return value
    raise AssertionError(f"{name} is not assigned in {path}")


def test_runtime_compatibility_versions_and_dependency_are_explicit() -> None:
    core_version = _literal_assignment(ROOT / "aworld/version_gen.py", "__version__")
    metadata = tomllib.loads(RUNTIME_CLI_METADATA.read_text(encoding="utf-8"))

    assert core_version == "0.2.9"
    assert metadata["project"]["version"] == "0.1.1"
    assert f"aworld=={core_version}" in metadata["project"]["dependencies"]


def test_runtime_compatibility_manifest_keeps_full_cli_entrypoint() -> None:
    metadata = tomllib.loads(RUNTIME_CLI_METADATA.read_text(encoding="utf-8"))

    assert metadata["project"]["scripts"] == {"aworld-cli": "aworld_cli.main:main"}
    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/aworld_cli"
    ]
